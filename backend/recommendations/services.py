"""
Recommendation engine for After The Credits.

Strategy
--------
1. Build a profile embedding from the user's journal-entry embeddings,
   weighting each entry by its survey score so highly rated films pull harder.
2. Find candidate movies via cosine similarity against that profile vector.
3. Apply a multi-signal re-ranking pass using the journal survey:
   - overall positivity (is_positive) boosts matching directors/producers
   - genre affinity (liked_genre) boosts movies sharing genres
   - story/writing (liked_story) boosts matching writers/directors
   - performances (liked_performances) boosts matching top-billed actors
   - rewatchability (would_rewatch) strengthens matches to that whole movie
   - negative signals exclude genre-similar movies
4. Filter by user streaming platforms, if the user has set any.
5. Exclude films the user has already journaled.
6. Return top-N Recommendation objects with a plain-English explanation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pgvector.django import CosineDistance  # pylint: disable=import-error

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
MIN_SCORE = 0.10  # discard anything below this threshold
MAX_DISPLAY_SCORE = 1.0
MAIN_ACTOR_LIMIT = 5
EXPLANATION_NAME_LIMIT = 2
NEGATIVE_GENRE_SIGNAL_THRESHOLD = 2


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
    for credit in movie.credits.all(): # type: ignore
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

    for credit in movie.credits.all(): # type: ignore
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


def _is_negative_taste_signal(entry: JournalEntry) -> bool:
    """
    Treat a film as disliked when the user answered No to at least 3 of 5
    survey questions, or when the overall enjoyment answer is explicitly No.
    """
    return entry.is_positive is False or (
        entry.survey_score is not None and entry.survey_score <= 2
    )


def _has_negative_genre_signal(entry: JournalEntry) -> bool:
    return entry.liked_genre is False or (
        entry.liked_genre is not True and _is_negative_taste_signal(entry)
    )


def _excluded_genre_ids(entries: list[JournalEntry]) -> set[int]:
    """
    Genres that should not be recommended again after repeated negative
    signals.

    A single disappointing film should not block a genre. Once the same
    genre is disliked across multiple journal entries, stop recommending
    that genre.
    """
    negative_counts: dict[int, int] = {}
    for entry in entries:
        if not _has_negative_genre_signal(entry):
            continue
        for genre_id in _movie_genre_ids(entry.movie):
            negative_counts[genre_id] = negative_counts.get(genre_id, 0) + 1

    return {
        genre_id
        for genre_id, count in negative_counts.items()
        if count >= NEGATIVE_GENRE_SIGNAL_THRESHOLD
    }


def get_negative_genre_signal_ids(entries: list[JournalEntry]) -> set[int]:
    """Return genres touched by any negative journal signal."""
    ids: set[int] = set()
    for entry in entries:
        if _has_negative_genre_signal(entry):
            ids.update(_movie_genre_ids(entry.movie))
    return ids


def get_excluded_genre_ids(entries: list[JournalEntry]) -> set[int]:
    """Return genres that have crossed the repeated-negative threshold."""
    return _excluded_genre_ids(entries)


def _person_ids_from_entries(
    entries: list[JournalEntry],
    roles: set[str],
    *,
    actor_limit: int | None = None,
) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        ids.update(
            _credit_person_ids(
                entry.movie,
                roles,
                actor_limit=actor_limit,
            )
        )
    return ids


def _journalled_movie_ids(entries: list[JournalEntry]) -> set[int]:
    return {entry.movie_id for entry in entries if entry.movie_id is not None} # type: ignore


def _genre_ids_from_entries(entries) -> set[int]:
    ids: set[int] = set()
    for entry in entries:
        ids.update(_movie_genre_ids(entry.movie))
    return ids


def get_journal_entry_count(user: "User") -> int:
    """Return the count of journal entries that are linked to movies."""
    return JournalEntry.objects.filter(user=user, movie__isnull=False).count()


def has_enough_journal_entries(user: "User") -> bool:
    """Return whether the user has enough entries for recommendations."""
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
    # pylint: disable=too-many-arguments,too-many-locals
    """
    Return (explanation, journal_snippet) for a recommended movie.
    """
    movie_genres = list(movie.genres.all())
    movie_genre_ids = {genre.id for genre in movie_genres}
    matching_genres = [
        genre.name for genre in movie_genres if genre.id in liked_genres
    ]

    if matching_genres:
        genre_names = ", ".join(matching_genres[:2])
        explanation = f"Matches your taste for {genre_names}."
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
        people_names = "; ".join(people_matches[:3])
        explanation = f"{explanation} Also connects through {people_names}."

    # Find a journal entry whose genres overlap most with this movie.
    snippet = ""
    best_overlap = 0
    for entry in entry_map.values():
        overlap = len(_movie_genre_ids(entry.movie) & movie_genre_ids)
        if overlap > best_overlap and entry.raw_text:
            best_overlap = overlap
            snippet = entry.raw_text[:200]

    if not snippet:
        for entry in entry_map.values():
            if entry.raw_text:
                snippet = entry.raw_text[:200]
                break

    return explanation, snippet


def _fetch_candidate_movies(
    qs,
    profile_vector,
    *,
    runtime_minutes: int | None = None,
):
    if not runtime_minutes:
        distance = CosineDistance("embedding", profile_vector)
        return list(
            qs.annotate(vector_distance=distance)
            .order_by("vector_distance")
            .prefetch_related(
                "genres",
                "streaming_platforms",
                "credits__person",
            )[:CANDIDATE_POOL]
        )

    distance = CosineDistance("embedding", profile_vector)
    runtime_matches = list(
        qs.filter(runtime__isnull=False, runtime__lte=runtime_minutes)
        .annotate(vector_distance=distance)
        .order_by("vector_distance")
        .prefetch_related(
            "genres",
            "streaming_platforms",
            "credits__person",
        )[:CANDIDATE_POOL]
    )

    runtime_match_ids = [movie.id for movie in runtime_matches]
    fallback_candidates = list(
        qs.exclude(id__in=runtime_match_ids)
        .annotate(vector_distance=distance)
        .order_by("vector_distance")
        .prefetch_related(
            "genres",
            "streaming_platforms",
            "credits__person",
        )[:CANDIDATE_POOL]
    )
    return runtime_matches + fallback_candidates


def _score_candidates(
    candidates: list[Movie],
    *,
    liked_genres: set[int],
    excluded_genres: set[int],
    positive_crew_ids: set[int],
    story_crew_ids: set[int],
    performance_actor_ids: set[int],
    rewatch_genre_ids: set[int],
    rewatch_crew_ids: set[int],
    rewatch_actor_ids: set[int],
) -> list[tuple[float, Movie]]:
    # pylint: disable=too-many-arguments,too-many-locals
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

        if movie_genre_ids & excluded_genres:
            continue

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

        if score >= MIN_SCORE:
            scored.append((score, movie))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _pick_top_recommendations(
    scored: list[tuple[float, Movie]],
    *,
    runtime_minutes: int | None = None,
) -> list[tuple[float, Movie]]:
    if runtime_minutes:
        runtime_matches = [
            item
            for item in scored
            if (
                item[1].runtime is not None
                and item[1].runtime <= runtime_minutes
            )
        ]
        fallback_matches = [
            item for item in scored if item not in runtime_matches
        ]
        return (runtime_matches + fallback_matches)[:TOP_N]

    return scored[:TOP_N]


def generate_recommendations(
    user: "User",
    *,
    runtime_minutes: int | None = None,
) -> list[Recommendation]:
    # pylint: disable=too-many-locals,too-many-statements
    """
    Full pipeline: profile -> candidates -> re-rank -> return.
    Returns an empty list if the user has no journal entries with embeddings.
    """
    Recommendation.objects.filter(user=user).delete()

    entries = list(
        JournalEntry.objects.filter(user=user, movie__isnull=False)
        .select_related("movie")
        .prefetch_related("movie__genres", "movie__credits__person")
    )

    if len(entries) < MIN_JOURNAL_ENTRIES:
        return []

    # 1. Recompute the profile embedding so recommendations stay fresh.
    profile_vector = update_user_profile_embedding(user)
    if not profile_vector:
        return []

    # 2. Fetch candidate movies via pgvector cosine similarity.
    excluded_ids = _journalled_movie_ids(entries)
    platform_ids = _get_platform_ids(user)
    excluded_genres = _excluded_genre_ids(entries)

    qs = Movie.objects.exclude(id__in=excluded_ids).filter(
        embedding__isnull=False,
    )
    if excluded_genres:
        qs = qs.exclude(genres__id__in=excluded_genres)

    if platform_ids:
        qs = qs.filter(streaming_platforms__id__in=platform_ids).distinct()

    candidates = _fetch_candidate_movies(
        qs,
        profile_vector,
        runtime_minutes=runtime_minutes,
    )

    # 3. Re-rank with survey signals.
    liked_genres = _liked_genre_ids(entries)
    entry_map = {
        entry.movie_id: entry # type: ignore
        for entry in entries
        if entry.movie_id is not None # type: ignore
    }

    rewatch_yes_entries = {
        entry for entry in entries if entry.would_rewatch is True
    }
    positive_entries = {
        entry for entry in entries if entry.is_positive is True
    }
    story_liked_entries = {
        entry for entry in entries if entry.liked_story is True
    }
    perf_liked_entries = {
        entry for entry in entries if entry.liked_performances is True
    }

    rewatch_genre_ids = _genre_ids_from_entries(rewatch_yes_entries)
    rewatch_crew_ids = _person_ids_from_entries(
        rewatch_yes_entries, # type: ignore
        {
            MovieCredit.ROLE_DIRECTOR,
            MovieCredit.ROLE_PRODUCER,
            MovieCredit.ROLE_WRITER,
        },
    )
    rewatch_actor_ids = _person_ids_from_entries(
        rewatch_yes_entries, # type: ignore
        {MovieCredit.ROLE_ACTOR},
        actor_limit=MAIN_ACTOR_LIMIT,
    )
    positive_crew_ids = _person_ids_from_entries(
        positive_entries, # type: ignore
        {MovieCredit.ROLE_DIRECTOR, MovieCredit.ROLE_PRODUCER},
    )
    positive_director_ids = _person_ids_from_entries(
        positive_entries, # type: ignore
        {MovieCredit.ROLE_DIRECTOR},
    )
    positive_producer_ids = _person_ids_from_entries(
        positive_entries, # type: ignore
        {MovieCredit.ROLE_PRODUCER},
    )
    story_crew_ids = _person_ids_from_entries(
        story_liked_entries, # type: ignore
        {MovieCredit.ROLE_WRITER, MovieCredit.ROLE_DIRECTOR},
    )
    story_director_ids = _person_ids_from_entries(
        story_liked_entries, # type: ignore
        {MovieCredit.ROLE_DIRECTOR},
    )
    story_writer_ids = _person_ids_from_entries(
        story_liked_entries, # type: ignore
        {MovieCredit.ROLE_WRITER},
    )
    performance_actor_ids = _person_ids_from_entries(
        perf_liked_entries, # type: ignore
        {MovieCredit.ROLE_ACTOR},
        actor_limit=MAIN_ACTOR_LIMIT,
    )
    rewatch_director_ids = _person_ids_from_entries(
        rewatch_yes_entries, # type: ignore
        {MovieCredit.ROLE_DIRECTOR},
    )
    rewatch_producer_ids = _person_ids_from_entries(
        rewatch_yes_entries, # type: ignore
        {MovieCredit.ROLE_PRODUCER},
    )
    rewatch_writer_ids = _person_ids_from_entries(
        rewatch_yes_entries, # type: ignore
        {MovieCredit.ROLE_WRITER},
    )

    explanation_director_ids = (
        positive_director_ids
        | story_director_ids
        | rewatch_director_ids
    )
    explanation_producer_ids = positive_producer_ids | rewatch_producer_ids
    explanation_writer_ids = story_writer_ids | rewatch_writer_ids
    explanation_actor_ids = performance_actor_ids | rewatch_actor_ids

    scored = _score_candidates(
        candidates,
        liked_genres=liked_genres,
        excluded_genres=excluded_genres,
        positive_crew_ids=positive_crew_ids,
        story_crew_ids=story_crew_ids,
        performance_actor_ids=performance_actor_ids,
        rewatch_genre_ids=rewatch_genre_ids,
        rewatch_crew_ids=rewatch_crew_ids,
        rewatch_actor_ids=rewatch_actor_ids,
    )

    if platform_ids and len(scored) < TOP_N:
        logger.info(
            "Only %s platform-matching recommendation candidates scored "
            "for user %s; "
            "falling back to all platforms.",
            len(scored),
            user.pk,
        )
        fallback_qs = Movie.objects.exclude(id__in=excluded_ids).filter(
            embedding__isnull=False
        )
        if excluded_genres:
            fallback_qs = fallback_qs.exclude(genres__id__in=excluded_genres)

        fallback_candidates = _fetch_candidate_movies(
            fallback_qs,
            profile_vector,
            runtime_minutes=runtime_minutes,
        )
        seen_ids = {movie.id for _, movie in scored} # type: ignore
        fallback_scored = _score_candidates(
            [
                movie
                for movie in fallback_candidates
                if movie.id not in seen_ids # type: ignore
            ],
            liked_genres=liked_genres,
            excluded_genres=excluded_genres,
            positive_crew_ids=positive_crew_ids,
            story_crew_ids=story_crew_ids,
            performance_actor_ids=performance_actor_ids,
            rewatch_genre_ids=rewatch_genre_ids,
            rewatch_crew_ids=rewatch_crew_ids,
            rewatch_actor_ids=rewatch_actor_ids,
        )
        scored = sorted(
            scored + fallback_scored,
            key=lambda item: item[0],
            reverse=True,
        )

    top = _pick_top_recommendations(scored, runtime_minutes=runtime_minutes)

    # 4. Build unsaved recommendation objects for display.
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
                score=round(min(score, MAX_DISPLAY_SCORE), 4),
                explanation=explanation,
                journal_snippet=snippet,
            )
        )

    return recs


def get_recommendations(
    user: "User",
    *,
    runtime_minutes: int | None = None,
) -> list[Recommendation]:
    """Return freshly generated recommendations."""
    if not has_enough_journal_entries(user):
        Recommendation.objects.filter(user=user).delete()
        return []

    return generate_recommendations(user, runtime_minutes=runtime_minutes)
