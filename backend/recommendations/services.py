"""
Recommendation engine for After The Credits.

Strategy
--------
1. Build a profile embedding from the user's journal-entry embeddings, weighting
   each entry by its survey score so highly rated films pull harder.
2. Find candidate movies via cosine similarity against that profile vector.
3. Apply a multi-signal re-ranking pass using the journal survey:
   - overall positivity (is_positive) boosts matching directors/producers
   - genre affinity (liked_genre) boosts movies sharing genres
   - story/writing (liked_story) boosts matching writers/directors
   - performances (liked_performances) boosts matching top-billed actors
   - rewatchability (would_rewatch) strengthens matches to that whole movie
   - negative signal (is_positive=False) penalizes genre-similar movies
4. Filter by user streaming platforms, if the user has set any.
5. Exclude films the user has already journaled.
6. Return top-N Recommendation objects with a plain-English explanation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import QuerySet
from pgvector.django import CosineDistance

from journal.models import JournalEntry
from movies.models import Movie, MovieCredit
from users.services.profile_embedding import update_user_profile_embedding

from .models import Recommendation

if TYPE_CHECKING:
    from users.models import User

logger = logging.getLogger(__name__)

# Tuneable knobs
CANDIDATE_POOL = 60  # how many movies to pull from pgvector before re-rank
MIN_JOURNAL_ENTRIES = 3
TOP_N = 3  # final recommendations returned
GENRE_BOOST = 0.12  # added to score when liked_genre=True and genres overlap
OVERALL_CREW_BOOST = 0.10
STORY_CREW_BOOST = 0.10
PERFORMANCE_BOOST = 0.10
REWATCH_MULTIPLIER = 1.20  # strengthens matches to rewatchable movies
NEGATIVE_PENALTY = 0.60  # score multiplied for negative genre clusters
MIN_SCORE = 0.10  # discard anything below this threshold
MAIN_ACTOR_LIMIT = 5
EXPLANATION_NAME_LIMIT = 2


def _movie_genre_ids(movie: Movie | None) -> set[int]:
    if movie is None:
        return set()
    return {genre.id for genre in movie.genres.all()}


def _credit_person_ids(
    movie: Movie | None,
    roles: set[str],
    *,
    actor_limit: int | None = None,
) -> set[int]:
    if movie is None:
        return set()

    person_ids: set[int] = set()
    actors_seen = 0
    for credit in movie.credits.all():
        if credit.role not in roles:
            continue
        if credit.role == MovieCredit.ROLE_ACTOR and actor_limit is not None:
            if actors_seen >= actor_limit:
                continue
            actors_seen += 1
        person_ids.add(credit.person_id)
    return person_ids


def _matching_credit_names(
    movie: Movie,
    role: str,
    person_ids: set[int],
    *,
    actor_limit: int | None = None,
) -> list[str]:
    names: list[str] = []
    seen: set[int] = set()
    actors_seen = 0

    for credit in movie.credits.all():
        if credit.role != role:
            continue
        if role == MovieCredit.ROLE_ACTOR and actor_limit is not None:
            if actors_seen >= actor_limit:
                continue
            actors_seen += 1
        if credit.person_id not in person_ids or credit.person_id in seen:
            continue
        names.append(credit.person.name)
        seen.add(credit.person_id)

    return names


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


def _person_ids_from_entries(
    entries: list[JournalEntry],
    roles: set[str],
    *,
    actor_limit: int | None = None,
) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        ids.update(_credit_person_ids(entry.movie, roles, actor_limit=actor_limit))
    return ids


def _journalled_movie_ids(entries: list[JournalEntry]) -> set[int]:
    return {entry.movie_id for entry in entries if entry.movie_id is not None}


def _genre_ids_from_entries(entries) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        ids.update(_movie_genre_ids(entry.movie))
    return ids


def get_journal_entry_count(user: "User") -> int:
    return JournalEntry.objects.filter(user=user, movie__isnull=False).count()


def has_enough_journal_entries(user: "User") -> bool:
    return get_journal_entry_count(user) >= MIN_JOURNAL_ENTRIES


def _build_explanation(
    movie: Movie,
    liked_genres: set[int],
    entry_map: dict[int, JournalEntry],
    *,
    director_ids: set[int] | None = None,
    producer_ids: set[int] | None = None,
    writer_ids: set[int] | None = None,
    actor_ids: set[int] | None = None,
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

    people_matches = []
    role_matches = [
        ("director", MovieCredit.ROLE_DIRECTOR, director_ids or set(), None),
        ("producer", MovieCredit.ROLE_PRODUCER, producer_ids or set(), None),
        ("writer", MovieCredit.ROLE_WRITER, writer_ids or set(), None),
        (
            "actor",
            MovieCredit.ROLE_ACTOR,
            actor_ids or set(),
            MAIN_ACTOR_LIMIT,
        ),
    ]
    for label, role, ids, actor_limit in role_matches:
        names = _matching_credit_names(
            movie,
            role,
            ids,
            actor_limit=actor_limit,
        )[:EXPLANATION_NAME_LIMIT]
        if names:
            people_matches.append(f"{label} {', '.join(names)}")

    if people_matches:
        explanation = f"{explanation} Also connects through {'; '.join(people_matches[:3])}."

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
        .prefetch_related("movie__genres", "movie__credits__person")
    )

    if len(entries) < MIN_JOURNAL_ENTRIES:
        Recommendation.objects.filter(user=user).delete()
        return Recommendation.objects.none()

    # 1. Read the persisted profile embedding, refreshing it if needed.
    profile_vector = user.profile_embedding or update_user_profile_embedding(user)
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
        .prefetch_related("genres", "streaming_platforms", "credits__person")[:CANDIDATE_POOL]
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
            .prefetch_related("genres", "streaming_platforms", "credits__person")[:CANDIDATE_POOL]
        )

    # 3. Re-rank with survey signals.
    liked_genres = _liked_genre_ids(entries)
    negative_genres = _negative_genre_ids(entries)
    entry_map = {entry.movie_id: entry for entry in entries if entry.movie_id is not None}

    rewatch_yes_entries = {entry for entry in entries if entry.would_rewatch is True}
    positive_entries = {entry for entry in entries if entry.is_positive is True}
    story_liked_entries = {entry for entry in entries if entry.liked_story is True}
    perf_liked_entries = {
        entry for entry in entries if entry.liked_performances is True
    }

    rewatch_genre_ids = _genre_ids_from_entries(rewatch_yes_entries)
    rewatch_crew_ids = _person_ids_from_entries(
        rewatch_yes_entries,
        {
            MovieCredit.ROLE_DIRECTOR,
            MovieCredit.ROLE_PRODUCER,
            MovieCredit.ROLE_WRITER,
        },
    )
    rewatch_actor_ids = _person_ids_from_entries(
        rewatch_yes_entries,
        {MovieCredit.ROLE_ACTOR},
        actor_limit=MAIN_ACTOR_LIMIT,
    )
    positive_crew_ids = _person_ids_from_entries(
        positive_entries,
        {MovieCredit.ROLE_DIRECTOR, MovieCredit.ROLE_PRODUCER},
    )
    positive_director_ids = _person_ids_from_entries(
        positive_entries,
        {MovieCredit.ROLE_DIRECTOR},
    )
    positive_producer_ids = _person_ids_from_entries(
        positive_entries,
        {MovieCredit.ROLE_PRODUCER},
    )
    story_crew_ids = _person_ids_from_entries(
        story_liked_entries,
        {MovieCredit.ROLE_WRITER, MovieCredit.ROLE_DIRECTOR},
    )
    story_director_ids = _person_ids_from_entries(
        story_liked_entries,
        {MovieCredit.ROLE_DIRECTOR},
    )
    story_writer_ids = _person_ids_from_entries(
        story_liked_entries,
        {MovieCredit.ROLE_WRITER},
    )
    performance_actor_ids = _person_ids_from_entries(
        perf_liked_entries,
        {MovieCredit.ROLE_ACTOR},
        actor_limit=MAIN_ACTOR_LIMIT,
    )
    rewatch_director_ids = _person_ids_from_entries(
        rewatch_yes_entries,
        {MovieCredit.ROLE_DIRECTOR},
    )
    rewatch_producer_ids = _person_ids_from_entries(
        rewatch_yes_entries,
        {MovieCredit.ROLE_PRODUCER},
    )
    rewatch_writer_ids = _person_ids_from_entries(
        rewatch_yes_entries,
        {MovieCredit.ROLE_WRITER},
    )

    explanation_director_ids = positive_director_ids | story_director_ids | rewatch_director_ids
    explanation_producer_ids = positive_producer_ids | rewatch_producer_ids
    explanation_writer_ids = story_writer_ids | rewatch_writer_ids
    explanation_actor_ids = performance_actor_ids | rewatch_actor_ids

    scored: list[tuple[float, Movie]] = []

    for movie in candidates:
        # Base: convert cosine distance to similarity, clamped to [0, 1].
        raw_distance = float(getattr(movie, "vector_distance", 1.0))
        score = max(0.0, 1.0 - raw_distance)

        movie_genre_ids = _movie_genre_ids(movie)
        movie_story_crew_ids = _credit_person_ids(
            movie,
            {MovieCredit.ROLE_WRITER, MovieCredit.ROLE_DIRECTOR},
        )
        movie_overall_crew_ids = _credit_person_ids(
            movie,
            {MovieCredit.ROLE_DIRECTOR, MovieCredit.ROLE_PRODUCER},
        )
        movie_actor_ids = _credit_person_ids(
            movie,
            {MovieCredit.ROLE_ACTOR},
            actor_limit=MAIN_ACTOR_LIMIT,
        )

        if movie_genre_ids & liked_genres:
            score += GENRE_BOOST

        if movie_overall_crew_ids & positive_crew_ids:
            score += OVERALL_CREW_BOOST

        if movie_story_crew_ids & story_crew_ids:
            score += STORY_CREW_BOOST

        if movie_actor_ids & performance_actor_ids:
            score += PERFORMANCE_BOOST

        if (
            movie_genre_ids & rewatch_genre_ids
            or movie_story_crew_ids & rewatch_crew_ids
            or movie_actor_ids & rewatch_actor_ids
        ):
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
            explanation, snippet = _build_explanation(
                movie,
                liked_genres,
                entry_map,
                director_ids=explanation_director_ids,
                producer_ids=explanation_producer_ids,
                writer_ids=explanation_writer_ids,
                actor_ids=explanation_actor_ids,
            )
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
    if not has_enough_journal_entries(user):
        return Recommendation.objects.none()

    return (
        Recommendation.objects.filter(user=user, movie__embedding__isnull=False)
        .select_related("movie")
        .prefetch_related("movie__genres", "movie__streaming_platforms")
        .order_by("-score")
    )
