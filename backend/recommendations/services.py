"""
Recommendation engine for After The Credits.

Strategy
--------
1.  Build a *profile embedding* from the user's journal entries, weighting each
    entry by its survey_score so highly-rated films pull harder.
2.  Find candidate movies via cosine similarity against that profile vector
    (pgvector <=> operator).
3.  Apply a multi-signal re-ranking pass:
      • genre affinity  (liked_genre)   → boost movies sharing genres
      • story/writing   (liked_story)   → mild boost (director/writer proxy)
      • performances    (liked_performances) → mild boost (cast proxy)
      • rewatchability  (would_rewatch) → strong multiplier
      • negative signal (is_positive=False) → penalise similar films
4.  Filter by user streaming platforms (if the user has set any).
5.  Exclude films the user has already journalled.
6.  Return top-N Recommendation objects with a plain-English explanation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from pgvector.django import CosineDistance

from journal.models import JournalEntry
from movies.models import Movie
from core.services.embedding_service import compute_embedding

from .models import Recommendation

if TYPE_CHECKING:
    from users.models import User

logger = logging.getLogger(__name__)

# ── Tuneable knobs ────────────────────────────────────────────────────────────
CANDIDATE_POOL      = 60   # how many movies to pull from pgvector before re-rank
TOP_N               = 12   # final recommendations returned
GENRE_BOOST         = 0.12 # added to score when liked_genre=True and genres overlap
STORY_BOOST         = 0.05
PERFORMANCE_BOOST   = 0.05
REWATCH_MULTIPLIER  = 1.20 # score × this when would_rewatch=True
NEGATIVE_PENALTY    = 0.60 # score × this when is_positive=False for that genre cluster
MIN_SCORE           = 0.10 # discard anything below this threshold


def _build_profile_text(entries: list[JournalEntry]) -> str | None:
    """
    Concatenate movie texts from journal entries, repeating each title once
    per survey point so well-loved films pull the embedding harder.
    """
    parts: list[str] = []
    for entry in entries:
        if entry.embedding is None:
            continue
        weight = max(1, entry.survey_score)  # 1–5 repetitions
        movie_text = f"{entry.movie.title}. {entry.movie.overview or ''}"
        parts.extend([movie_text] * weight)

    return " ".join(parts) if parts else None


def _get_platform_ids(user: "User") -> list[int]:
    return list(user.streaming_platforms.values_list("id", flat=True))


def _liked_genre_ids(entries: list[JournalEntry]) -> set[int]:
    """Genre IDs from entries where the user liked the genre."""
    ids: set[int] = set()
    for entry in entries:
        if entry.liked_genre is True:
            ids.update(entry.movie.genres.values_list("id", flat=True))
    return ids


def _disliked_genre_ids(entries: list[JournalEntry]) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        if entry.liked_genre is False:
            ids.update(entry.movie.genres.values_list("id", flat=True))
    return ids


def _journalled_movie_ids(entries: list[JournalEntry]) -> set[int]:
    return {e.movie_id for e in entries}


def _build_explanation(
    movie: Movie,
    liked_genres: set[int],
    entry_map: dict[int, JournalEntry],
) -> tuple[str, str]:
    """
    Return (explanation, journal_snippet) for a recommended movie.
    """
    movie_genre_ids = set(movie.genres.values_list("id", flat=True))
    matching_genres = [
        g.name for g in movie.genres.all() if g.id in liked_genres
    ]

    if matching_genres:
        explanation = f"Matches your taste for {', '.join(matching_genres[:2])}."
    else:
        explanation = "Similar feel to films you've enjoyed."

    # Find a journal entry whose genres overlap most with this movie
    snippet = ""
    best_overlap = 0
    for entry in entry_map.values():
        overlap = len(
            set(entry.movie.genres.values_list("id", flat=True)) & movie_genre_ids
        )
        if overlap > best_overlap and entry.raw_text:
            best_overlap = overlap
            snippet = entry.raw_text[:200]

    return explanation, snippet


def generate_recommendations(user: "User") -> list[Recommendation]:
    """
    Full pipeline: profile → candidates → re-rank → save → return.
    Returns an empty list if the user has no journal entries with embeddings.
    """
    entries = list(
        JournalEntry.objects.filter(user=user)
        .select_related("movie")
        .prefetch_related("movie__genres")
    )

    if not entries:
        return []

    # 1. Build profile embedding
    profile_text = _build_profile_text(entries)
    if not profile_text:
        return []

    try:
        profile_vector = compute_embedding(profile_text)
    except Exception:
        logger.exception("Failed to compute profile embedding for user %s", user.pk)
        return []

    # 2. Fetch candidate movies via pgvector cosine similarity
    excluded_ids = _journalled_movie_ids(entries)
    platform_ids  = _get_platform_ids(user)

    qs = Movie.objects.exclude(id__in=excluded_ids).filter(
        embedding__isnull=False
    )

    if platform_ids:
        qs = qs.filter(streaming_platforms__id__in=platform_ids).distinct()

    candidates = list(
        qs.annotate(
            vector_distance=CosineDistance("embedding", profile_vector)
        )
        .order_by("vector_distance")
        .prefetch_related("genres", "streaming_platforms")
        [:CANDIDATE_POOL]
    )

    if not candidates:
        # No platform filter fallback
        candidates = list(
            Movie.objects.exclude(id__in=excluded_ids)
            .filter(embedding__isnull=False)
            .annotate(vector_distance=CosineDistance("embedding", profile_vector))
            .order_by("vector_distance")
            .prefetch_related("genres", "streaming_platforms")
            [:CANDIDATE_POOL]
        )

    # 3. Re-rank with survey signals
    liked_genres    = _liked_genre_ids(entries)
    disliked_genres = _disliked_genre_ids(entries)
    entry_map       = {e.movie_id: e for e in entries}

    # Aggregate per-genre survey signals across all entries
    rewatch_yes_entries   = {e for e in entries if e.would_rewatch is True}
    story_liked_entries   = {e for e in entries if e.liked_story is True}
    perf_liked_entries    = {e for e in entries if e.liked_performances is True}

    # Genre IDs where user liked rewatching / story / performances
    rewatch_genre_ids    = _genre_ids_from_entries(rewatch_yes_entries)
    story_genre_ids      = _genre_ids_from_entries(story_liked_entries)
    perf_genre_ids       = _genre_ids_from_entries(perf_liked_entries)

    scored: list[tuple[float, Movie]] = []

    for movie in candidates:
        # Base: convert distance [0,2] → similarity [1,−1]; clamp to [0,1]
        raw_distance = float(getattr(movie, "vector_distance", 1.0))
        score = max(0.0, 1.0 - raw_distance)

        movie_genre_ids = set(movie.genres.values_list("id", flat=True))

        # Genre affinity boost
        if movie_genre_ids & liked_genres:
            score += GENRE_BOOST

        # Story/writing boost
        if movie_genre_ids & story_genre_ids:
            score += STORY_BOOST

        # Performances boost
        if movie_genre_ids & perf_genre_ids:
            score += PERFORMANCE_BOOST

        # Rewatch multiplier
        if movie_genre_ids & rewatch_genre_ids:
            score *= REWATCH_MULTIPLIER

        # Negative penalty
        if movie_genre_ids & disliked_genres:
            score *= NEGATIVE_PENALTY

        if score >= MIN_SCORE:
            scored.append((score, movie))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:TOP_N]

    # 4. Persist recommendations (replace previous batch)
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

    return Recommendation.objects.filter(user=user).select_related("movie").prefetch_related("movie__genres").order_by("-score")


def _genre_ids_from_entries(entries) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        ids.update(entry.movie.genres.values_list("id", flat=True))
    return ids


def get_recommendations(user: "User"):
    """Return existing recommendations without regenerating."""
    return (
        Recommendation.objects.filter(user=user)
        .select_related("movie")
        .prefetch_related("movie__genres", "movie__streaming_platforms")
        .order_by("-score")
    )
