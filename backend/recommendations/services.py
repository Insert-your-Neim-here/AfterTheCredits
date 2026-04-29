"""
Recommendation engine for After The Credits.

Strategy
--------
1. Build a profile embedding from the user's journal-entry embeddings, weighting
   each entry by its survey score so highly rated films pull harder.
2. Find candidate movies via cosine similarity against that profile vector.
3. Apply a multi-signal re-ranking pass:
   - genre affinity (liked_genre) boosts movies sharing genres
   - story/writing (liked_story) mildly boosts genre-similar movies
   - performances (liked_performances) mildly boosts genre-similar movies
   - rewatchability (would_rewatch) applies a stronger multiplier
   - negative signal (is_positive=False) penalizes genre-similar movies
4. Filter by user streaming platforms, if the user has set any.
5. Exclude films the user has already journaled.
6. Return top-N Recommendation objects with a plain-English explanation.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import QuerySet
from pgvector.django import CosineDistance

from journal.models import JournalEntry
from movies.models import Movie

from .models import Recommendation

if TYPE_CHECKING:
    from users.models import User

logger = logging.getLogger(__name__)

# Tuneable knobs
CANDIDATE_POOL = 60  # how many movies to pull from pgvector before re-rank
TOP_N = 12  # final recommendations returned
GENRE_BOOST = 0.12  # added to score when liked_genre=True and genres overlap
STORY_BOOST = 0.05
PERFORMANCE_BOOST = 0.05
REWATCH_MULTIPLIER = 1.20  # score multiplied when would_rewatch=True
NEGATIVE_PENALTY = 0.60  # score multiplied for negative genre clusters
MIN_SCORE = 0.10  # discard anything below this threshold


def _movie_genre_ids(movie: Movie | None) -> set[int]:
    if movie is None:
        return set()
    return {genre.id for genre in movie.genres.all()}


def _build_profile_vector(entries: list[JournalEntry]) -> list[float] | None:
    """
    Build a weighted average of journal-entry embeddings.

    This avoids creating one large profile string that the embedding model may
    truncate once the user has enough journal history.
    """
    weighted_sum: list[float] | None = None
    total_weight = 0

    for entry in entries:
        if entry.embedding is None:
            continue

        vector = list(entry.embedding)
        weight = max(1, entry.survey_score or 0)

        if weighted_sum is None:
            weighted_sum = [0.0] * len(vector)

        for index, value in enumerate(vector):
            weighted_sum[index] += float(value) * weight
        total_weight += weight

    if weighted_sum is None or total_weight == 0:
        return None

    averaged = [value / total_weight for value in weighted_sum]
    magnitude = math.sqrt(sum(value * value for value in averaged))
    if magnitude == 0:
        return None

    return [value / magnitude for value in averaged]


def _get_platform_ids(user: "User") -> list[int]:
    return list(user.streaming_platforms.values_list("id", flat=True))


def _liked_genre_ids(entries: list[JournalEntry]) -> set[int]:
    """Genre IDs from entries where the user liked the genre."""
    ids: set[int] = set()
    for entry in entries:
        if entry.liked_genre is True:
            ids.update(_movie_genre_ids(entry.movie))
    return ids


def _negative_genre_ids(entries: list[JournalEntry]) -> set[int]:
    """Genre IDs from entries the user marked as negative overall."""
    ids: set[int] = set()
    for entry in entries:
        if entry.is_positive is False:
            ids.update(_movie_genre_ids(entry.movie))
    return ids


def _journalled_movie_ids(entries: list[JournalEntry]) -> set[int]:
    return {entry.movie_id for entry in entries if entry.movie_id is not None}


def _genre_ids_from_entries(entries) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        ids.update(_movie_genre_ids(entry.movie))
    return ids


def _build_explanation(
    movie: Movie,
    liked_genres: set[int],
    entry_map: dict[int, JournalEntry],
) -> tuple[str, str]:
    """
    Return (explanation, journal_snippet) for a recommended movie.
    """
    movie_genres = list(movie.genres.all())
    movie_genre_ids = {genre.id for genre in movie_genres}
    matching_genres = [genre.name for genre in movie_genres if genre.id in liked_genres]

    if matching_genres:
        explanation = f"Matches your taste for {', '.join(matching_genres[:2])}."
    else:
        explanation = "Similar feel to films you've enjoyed."

    # Find a journal entry whose genres overlap most with this movie.
    snippet = ""
    best_overlap = 0
    for entry in entry_map.values():
        overlap = len(_movie_genre_ids(entry.movie) & movie_genre_ids)
        if overlap > best_overlap and entry.raw_text:
            best_overlap = overlap
            snippet = entry.raw_text[:200]

    return explanation, snippet


def generate_recommendations(user: "User") -> QuerySet[Recommendation]:
    """
    Full pipeline: profile -> candidates -> re-rank -> save -> return.
    Returns an empty queryset if the user has no journal entries with embeddings.
    """
    entries = list(
        JournalEntry.objects.filter(user=user, movie__isnull=False)
        .select_related("movie")
        .prefetch_related("movie__genres")
    )

    if not entries:
        return Recommendation.objects.none()

    # 1. Build profile embedding.
    profile_vector = _build_profile_vector(entries)
    if not profile_vector:
        return Recommendation.objects.none()

    # 2. Fetch candidate movies via pgvector cosine similarity.
    excluded_ids = _journalled_movie_ids(entries)
    platform_ids = _get_platform_ids(user)

    qs = Movie.objects.exclude(id__in=excluded_ids).filter(embedding__isnull=False)

    if platform_ids:
        qs = qs.filter(streaming_platforms__id__in=platform_ids).distinct()

    candidates = list(
        qs.annotate(vector_distance=CosineDistance("embedding", profile_vector))
        .order_by("vector_distance")
        .prefetch_related("genres", "streaming_platforms")[:CANDIDATE_POOL]
    )

    if not candidates and platform_ids:
        logger.info(
            "No platform-matching recommendation candidates for user %s; "
            "falling back to all platforms.",
            user.pk,
        )
        candidates = list(
            Movie.objects.exclude(id__in=excluded_ids)
            .filter(embedding__isnull=False)
            .annotate(vector_distance=CosineDistance("embedding", profile_vector))
            .order_by("vector_distance")
            .prefetch_related("genres", "streaming_platforms")[:CANDIDATE_POOL]
        )

    # 3. Re-rank with survey signals.
    liked_genres = _liked_genre_ids(entries)
    negative_genres = _negative_genre_ids(entries)
    entry_map = {entry.movie_id: entry for entry in entries if entry.movie_id is not None}

    rewatch_yes_entries = {entry for entry in entries if entry.would_rewatch is True}
    story_liked_entries = {entry for entry in entries if entry.liked_story is True}
    perf_liked_entries = {
        entry for entry in entries if entry.liked_performances is True
    }

    rewatch_genre_ids = _genre_ids_from_entries(rewatch_yes_entries)
    story_genre_ids = _genre_ids_from_entries(story_liked_entries)
    perf_genre_ids = _genre_ids_from_entries(perf_liked_entries)

    scored: list[tuple[float, Movie]] = []

    for movie in candidates:
        # Base: convert cosine distance to similarity, clamped to [0, 1].
        raw_distance = float(getattr(movie, "vector_distance", 1.0))
        score = max(0.0, 1.0 - raw_distance)

        movie_genre_ids = _movie_genre_ids(movie)

        if movie_genre_ids & liked_genres:
            score += GENRE_BOOST

        if movie_genre_ids & story_genre_ids:
            score += STORY_BOOST

        if movie_genre_ids & perf_genre_ids:
            score += PERFORMANCE_BOOST

        if movie_genre_ids & rewatch_genre_ids:
            score *= REWATCH_MULTIPLIER

        if movie_genre_ids & negative_genres:
            score *= NEGATIVE_PENALTY

        if score >= MIN_SCORE:
            scored.append((score, movie))

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:TOP_N]

    # 4. Persist recommendations, replacing the previous batch.
    with transaction.atomic():
        Recommendation.objects.filter(user=user).delete()
        recs = []
        for score, movie in top:
            explanation, snippet = _build_explanation(movie, liked_genres, entry_map)
            recs.append(
                Recommendation(
                    user=user,
                    movie=movie,
                    score=round(score, 4),
                    explanation=explanation,
                    journal_snippet=snippet,
                )
            )
        Recommendation.objects.bulk_create(recs)

    return get_recommendations(user)


def get_recommendations(user: "User") -> QuerySet[Recommendation]:
    """Return existing recommendations without regenerating."""
    return (
        Recommendation.objects.filter(user=user, movie__embedding__isnull=False)
        .select_related("movie")
        .prefetch_related("movie__genres", "movie__streaming_platforms")
        .order_by("-score")
    )
