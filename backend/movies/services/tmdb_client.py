import os
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.cache import cache
from movies.models import Movie, Genre, Keyword, StreamingPlatform, MovieCredit, Person
from core.services.embedding_service import compute_embedding

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_TOKEN = os.environ.get("TMDB_TOKEN")
STREAMING_REGION = getattr(settings, "TMDB_REGION", "GB")
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_CACHE_TIMEOUT = 60 * 60 * 12
PROVIDER_LOOKUP_WORKERS = 8
TMDB_LANGUAGE = "en-GB"
INCLUDE_ADULT = "false"

tmdb_session = requests.Session()
tmdb_session.trust_env = False

if not TMDB_TOKEN:
    raise RuntimeError("TMDB_TOKEN is not set. Check your .env file.")


def _headers():
    """Build the authorization headers required by every TMDB API request."""
    return {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "Content-Type": "application/json;charset=utf-8",
    }


def _tmdb_get(path: str, params: dict | None = None) -> dict:
    """
    Fetch JSON from a TMDB endpoint with caching.

    The cache key is built from the endpoint path and sorted query params, so
    identical requests reuse the stored response for TMDB_CACHE_TIMEOUT seconds.
    """
    params = params or {}
    cache_key = f"tmdb:{path}:{urlencode(sorted(params.items()), doseq=True)}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    response = tmdb_session.get(
        f"{TMDB_BASE}{path}",
        headers=_headers(),
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    cache.set(cache_key, data, timeout=TMDB_CACHE_TIMEOUT)
    return data


def _streaming_region(region: str | None = None) -> str:
    """Return the requested streaming region, falling back to settings.TMDB_REGION."""
    return (region or STREAMING_REGION).upper()


def _streaming_params(region: str | None = None) -> dict:
    """Build TMDB discover filters that limit results to subscription streaming."""
    return {
        "watch_region": _streaming_region(region),
        "with_watch_monetization_types": "flatrate",
    }


def _language_params() -> dict:
    """Build shared language/adult filters used by movie search and discovery."""
    return {
        "language": TMDB_LANGUAGE,
        "include_adult": INCLUDE_ADULT,
    }


def _discover_params(
    page: int,
    sort_by: str,
    region: str | None,
    extra_params: dict | None = None,
) -> dict:
    """
    Build the shared query params for TMDB discover/movie requests.

    Extra filters such as genre, runtime, year, keywords, or text query are
    merged last so each caller can add only the filters it needs.
    """
    params = {
        **_language_params(),
        "page": page,
        "sort_by": sort_by,
        **_streaming_params(region),
    }
    if extra_params:
        params.update(extra_params)
    return params


def search_movies(
    query: str,
    page: int = 1,
    region: str | None = None,
):
    """
    Search TMDB for movies by text and return only streamable results.

    TMDB search does not support the same watch-provider filter as discover, so
    provider availability is attached and filtered after the search response.
    """
    data = _tmdb_get(
        "/search/movie",
        params={
            "query": query,
            "page": page,
            **_language_params(),
        },
    )
    return attach_streaming_platforms(data.get("results", []), region=region)


def popular_movies(
    page: int = 1,
    region: str | None = None,
):
    """Fetch popular streamable movies for the selected region."""
    data = _tmdb_get(
        "/discover/movie",
        params=_discover_params(page, "popularity.desc", region),
    )
    return attach_streaming_platforms(data.get("results", []), region=region)


def get_genres():
    """Fetch TMDB's movie genre list as plain dictionaries."""
    data = _tmdb_get(
        "/genre/movie/list",
        params={"language": TMDB_LANGUAGE},
    )
    return data.get("genres", [])


def search_keyword_ids(tags: list[str]) -> list[int]:
    """
    Convert user-entered tag text into TMDB keyword IDs.

    Each tag is searched against TMDB keywords; the first result is used and
    duplicate keyword IDs are removed while preserving order.
    """
    keyword_ids = []
    seen = set()

    for tag in tags:
        data = _tmdb_get(
            "/search/keyword",
            params={"query": tag, "page": 1},
        )
        results = data.get("results", [])
        if not results:
            continue

        keyword_id = results[0].get("id")
        if keyword_id and keyword_id not in seen:
            keyword_ids.append(keyword_id)
            seen.add(keyword_id)

    return keyword_ids


def discover_movies(
    page: int = 1,
    genre: str = "",
    runtime: str = "",
    year: str = "",
    text_query: str = "",
    keyword_ids: list[int] | None = None,
    sort_by: str = "popularity.desc",
    region: str | None = None,
):
    """
    Discover streamable movies using optional filters from the browse page.

    The function translates UI filters into TMDB discover parameters, fetches a
    page of matching results, then attaches provider names to each movie.
    """
    extra_params = {}

    if genre:
        extra_params["with_genres"] = genre

    if text_query:
        extra_params["with_text_query"] = text_query

    if keyword_ids:
        extra_params["with_keywords"] = "|".join(str(keyword_id) for keyword_id in keyword_ids)

    if runtime == "short":
        extra_params["with_runtime.lte"] = 89
    elif runtime == "medium":
        extra_params["with_runtime.gte"] = 90
        extra_params["with_runtime.lte"] = 120
    elif runtime == "long":
        extra_params["with_runtime.gte"] = 121

    if year:
        extra_params["primary_release_year"] = year

    params = _discover_params(page, sort_by, region, extra_params)
    data = _tmdb_get("/discover/movie", params=params)
    return attach_streaming_platforms(data.get("results", []), region=region)


def fetch_and_store_genres():
    """
    Fetch all TMDB movie genres and upsert them into the local Genre table.

    update_or_create keeps existing rows in place while refreshing their names
    if TMDB changes the label for a genre.
    """
    genres = get_genres()
    for g in genres:
        Genre.objects.update_or_create(
            tmdb_id=g["id"],
            defaults={"name": g["name"]},
        )
    logger.info(f"Synced {len(genres)} genres.")


def fetch_streaming_platforms(region: str | None = None):
    """
    Fetch subscription watch providers for a region and store them locally.

    TMDB returns provider variants such as ad-supported tiers; provider names
    are normalized before saving so the app groups variants under one platform.
    """
    streaming_region = _streaming_region(region)
    data = _tmdb_get(
        "/watch/providers/movie",
        params={"watch_region": streaming_region},
    )
    count = 0
    seen = set()
    for provider in data.get("results", []):
        name = _normalize_provider_name(provider["provider_name"])
        if name in seen:
            continue

        StreamingPlatform.objects.update_or_create(
            name=name,
        )
        seen.add(name)
        count += 1
    logger.info(f"Synced {count} streaming platforms for {streaming_region}.")


def _normalize_provider_name(name: str) -> str:
    """
    Collapse TMDB subscription tiers into the main streaming service name.

    This keeps platform filters clean by treating variants such as "with Ads"
    as the same service, and by grouping known provider prefixes together.
    """
    name = name.replace(" with Ads", "")

    provider_prefixes = {
        "Netflix": "Netflix",
        "Paramount Plus": "Paramount Plus",
    }

    for prefix, normalized_name in provider_prefixes.items():
        if name.startswith(prefix):
            return normalized_name

    return name


def fetch_movie_watch_providers(tmdb_id: int, region: str | None = None) -> list[str]:
    """
    Return subscription streaming platform names for one movie in one region.

    The TMDB watch-provider response is keyed by country/region. This function
    selects the configured region, reads the "flatrate" providers, normalizes
    their names, removes duplicates, and returns an empty list on lookup errors.
    """
    try:
        streaming_region = _streaming_region(region)
        data = _tmdb_get(f"/movie/{tmdb_id}/watch/providers")
        region_data = data.get("results", {}).get(streaming_region, {})
        flatrate = region_data.get("flatrate", [])
        names = []
        seen = set()
        for provider in flatrate:
            name = _normalize_provider_name(provider["provider_name"])
            if name not in seen:
                names.append(name)
                seen.add(name)
        return names
    except Exception:
        return []


def fetch_movie_keywords(tmdb_id: int) -> list[dict]:
    """
    Return TMDB keyword dictionaries for one movie.

    Errors are swallowed as an empty list because keywords enrich recommendations
    but should not stop a movie from being shown or imported.
    """
    try:
        data = _tmdb_get(f"/movie/{tmdb_id}/keywords")
        return data.get("keywords", [])
    except Exception:
        return []


def attach_streaming_platforms(
    movies: list[dict],
    region: str | None = None,
) -> list[dict]:
    """
    Attach provider names to movie dictionaries and drop non-streamable movies.

    TMDB list/search responses do not include provider names on each movie, so
    this performs one watch-provider lookup per movie. A thread pool runs those
    network calls in parallel to keep browse/search pages responsive.
    """
    if not movies:
        return []

    def _with_providers(movie):
        """Return a copied movie dict with providers, or None if unavailable."""
        streaming_platforms = fetch_movie_watch_providers(movie["id"], region=region)
        if not streaming_platforms:
            return None

        movie = movie.copy()
        movie["streaming_platforms"] = streaming_platforms
        return movie

    with ThreadPoolExecutor(max_workers=PROVIDER_LOOKUP_WORKERS) as executor:
        checked_movies = executor.map(_with_providers, movies)

    return [movie for movie in checked_movies if movie]


def store_loaded_movies(movies: list[dict], region: str | None = None) -> int:
    """
    Persist already-loaded TMDB result dictionaries into the local database.

    This is used after browse/search results have been fetched. It refreshes the
    genre map once, then processes each movie and counts successful saves.
    """
    if not movies:
        return 0

    fetch_and_store_genres()
    genre_map = {g.name: g for g in Genre.objects.all()}
    saved_count = 0

    for item in movies:
        try:
            if _process_movie(item, genre_map, region=region):
                saved_count += 1
        except Exception as e:
            logger.error(f"Error saving loaded movie {item.get('id')}: {e}")

    return saved_count


def fetch_and_store_movies(
    pages: int = 5,
    list_type: str = "popular",
    region: str | None = None,
):
    """
    Fetch a TMDB movie list across multiple pages and store streamable movies.

    list_type controls which TMDB list/discover query is used. Existing movies
    are skipped, provider availability is checked, and saved movies receive
    details, credits, keywords, streaming platforms, and an embedding.
    """
    fetch_and_store_genres()
    genre_map = {g.name: g for g in Genre.objects.all()}
    streaming_region = _streaming_region(region)

    total_saved = 0

    for page in range(1, pages + 1):
        logger.info(f"Fetching page {page}/{pages} ({list_type}, {streaming_region})...")
        try:
            data = _fetch_movie_list_page(list_type, page, streaming_region)
        except Exception as e:
            logger.error(f"Failed to fetch page {page}: {e}")
            continue

        new_movies = _exclude_existing_movies(data.get("results", []))
        logger.info(
            f"Skipped {len(data.get('results', [])) - len(new_movies)} existing movies on page {page}."
        )

        streamable_movies = attach_streaming_platforms(new_movies, region=streaming_region)
        logger.info(
            f"Found {len(streamable_movies)} streamable movies on page {page}."
        )

        for item in streamable_movies:
            try:
                if _process_movie(item, genre_map, region=streaming_region):
                    total_saved += 1
            except Exception as e:
                logger.error(f"Error processing movie {item.get('id')}: {e}")

    logger.info(f"Done. Saved/filled {total_saved} movies.")
    return total_saved


def _exclude_existing_movies(movies: list[dict]) -> list[dict]:
    """
    Remove TMDB result dictionaries whose tmdb_id already exists locally.

    The database is queried once using all result IDs, then the original result
    order is preserved for movies that are not already stored.
    """
    tmdb_ids = [movie.get("id") for movie in movies if movie.get("id")]
    if not tmdb_ids:
        return []

    existing_tmdb_ids = set(
        Movie.objects.filter(tmdb_id__in=tmdb_ids).values_list("tmdb_id", flat=True)
    )
    return [movie for movie in movies if movie.get("id") not in existing_tmdb_ids]


def _fetch_movie_list_page(list_type: str, page: int, region: str) -> dict:
    """
    Fetch one page from the requested TMDB list source.

    popular/top_rated use discover/movie so they can include streaming filters;
    other values use TMDB's named movie list endpoints such as now_playing.
    """
    if list_type == "popular":
        return _tmdb_get(
            "/discover/movie",
            params=_discover_params(page, "popularity.desc", region),
        )

    if list_type == "top_rated":
        return _tmdb_get(
            "/discover/movie",
            params=_discover_params(
                page,
                "vote_average.desc",
                region,
                {"vote_count.gte": 200},
            ),
        )

    return _tmdb_get(
        f"/movie/{list_type}",
        params={"language": TMDB_LANGUAGE, "page": page, "region": region},
    )


def build_movie_text(
    title: str,
    overview: str,
    genres: list[str],
    keywords: list[str] | None = None,
    actors: list[str] | None = None,
    writers: list[str] | None = None,
    producers: list[str] | None = None,
    directors: list[str] | None = None,
) -> str:
    """
    Build the text blob used to compute a movie embedding.

    The embedding model works from one combined string, so this joins the title,
    overview, genres, keywords, actors, writers, producers, and directors into
    a stable sentence format. Missing sections are marked as Unknown.
    """
    genre_str = ", ".join(genres) if genres else "Unknown"
    keyword_str = ", ".join(keywords) if keywords else "Unknown"
    actor_str = ", ".join(actors) if actors else "Unknown"
    writer_str = ", ".join(writers) if writers else "Unknown"
    producer_str = ", ".join(producers) if producers else "Unknown"
    director_str = ", ".join(directors) if directors else "Unknown"
    return f"{title}. {overview} Genres: {genre_str}. Keywords: {keyword_str}. Actors: {actor_str}. Writers: {writer_str}. Producers: {producer_str}. Directors: {director_str}."


def _credit_names(movie: Movie, role: str) -> list[str]:
    """
    Return stored person names for one movie credit role.

    Credits are read after fetch_and_store_credits runs, so the embedding text
    can include names from the local MovieCredit and Person rows.
    """
    return [
        credit.person.name
        for credit in MovieCredit.objects.filter(
            movie=movie,
            role=role,
        ).select_related("person")
    ]


def _process_movie(
    item: dict,
    genre_map: dict,
    region: str | None = None,
) -> bool:
    """
    Fetch full TMDB metadata and save one movie with related local records.

    The function skips existing or non-streamable movies, stores details,
    genres, keywords, streaming platforms, credits, and finally computes the
    recommendation embedding from the stored metadata.
    """
    tmdb_id = item["id"]

    if Movie.objects.filter(tmdb_id=tmdb_id).exists():
        logger.info(f"Skipping existing movie {tmdb_id}.")
        return False

    platform_names = item.get("streaming_platforms") or fetch_movie_watch_providers(
        tmdb_id,
        region=region,
    )

    if not platform_names:
        logger.info(f"Skipping non-streamable movie {tmdb_id}.")
        return False

    # Full details include fields missing from list responses, such as runtime.
    details = _tmdb_get(f"/movie/{tmdb_id}")

    title = details.get("title", "")
    overview = details.get("overview", "") or ""
    genre_objects = []
    keyword_objects = []

    for g in details.get("genres", []):
        genre_obj = genre_map.get(g["name"])
        if genre_obj:
            genre_objects.append(genre_obj)

    for keyword in fetch_movie_keywords(tmdb_id):
        keyword_id = keyword.get("id")
        keyword_name = keyword.get("name")
        if not keyword_id or not keyword_name:
            continue

        keyword_obj, _ = Keyword.objects.update_or_create(
            tmdb_id=keyword_id,
            defaults={"name": keyword_name},
        )
        keyword_objects.append(keyword_obj)

    movie_defaults = {
        "title": title,
        "overview": overview,
        "release_date": details.get("release_date") or None,
        "runtime": details.get("runtime") or None,
        "poster_path": details.get("poster_path") or "",
        "backdrop_path": details.get("backdrop_path") or "",
        "vote_average": details.get("vote_average") or 0,
        "vote_count": details.get("vote_count") or 0,
        "popularity": details.get("popularity") or 0,
    }

    movie = Movie.objects.create(tmdb_id=tmdb_id, **movie_defaults)

    if genre_objects:
        movie.genres.set(genre_objects)

    if keyword_objects:
        movie.keywords.set(keyword_objects)

    for platform_name in platform_names:
        StreamingPlatform.objects.get_or_create(name=platform_name)
    platforms = StreamingPlatform.objects.filter(name__in=platform_names)
    movie.streaming_platforms.set(platforms)

    fetch_and_store_credits(movie, tmdb_id)

    genre_names = [g.name for g in genre_objects]
    keyword_names = [keyword.name for keyword in keyword_objects]
    actors = _credit_names(movie, MovieCredit.ROLE_ACTOR)
    writers = _credit_names(movie, MovieCredit.ROLE_WRITER)
    producers = _credit_names(movie, MovieCredit.ROLE_PRODUCER)
    directors = _credit_names(movie, MovieCredit.ROLE_DIRECTOR)
    text = build_movie_text(
        title,
        overview,
        genre_names,
        keyword_names,
        actors,
        writers,
        producers,
        directors,
    )
    movie.embedding = compute_embedding(text)
    movie.save(update_fields=["embedding"])
    return True


def poster_url(poster_path: str | None):
    """Return the full TMDB image URL for a poster path, or None if missing."""
    if not poster_path:
        return None
    return f"{TMDB_IMAGE_BASE}{poster_path}"


MAX_ACTORS = 10  # top-billed cast members to store


def fetch_and_store_credits(movie: Movie, tmdb_id: int) -> None:
    """
    Fetch cast and crew credits for one movie and store selected people.

    Directors, writers, producers, and the top-billed actors are converted into
    Person rows and MovieCredit join rows. Duplicate credits are ignored by the
    bulk insert so reruns do not create duplicate relationships.
    """
    try:
        data = _tmdb_get(f"/movie/{tmdb_id}/credits", {})
    except Exception:
        logger.warning("Could not fetch credits for tmdb_id=%s", tmdb_id)
        return

    crew = data.get("crew", [])
    cast = data.get("cast", [])

    to_create: list[MovieCredit] = []

    for member in crew:
        job  = member.get("job", "")
        dept = member.get("department", "")

        if job == "Director":
            role = MovieCredit.ROLE_DIRECTOR
        elif job in ("Screenplay", "Writer", "Story", "Original Story"):
            role = MovieCredit.ROLE_WRITER
        elif dept == "Writing":
            role = MovieCredit.ROLE_WRITER
        elif job == "Producer":
            role = MovieCredit.ROLE_PRODUCER
        else:
            continue

        person = _upsert_person(member)
        to_create.append(MovieCredit(movie=movie, person=person, role=role, order=0))

    for member in cast[:MAX_ACTORS]:
        person = _upsert_person(member)
        to_create.append(
            MovieCredit(
                movie=movie,
                person=person,
                role=MovieCredit.ROLE_ACTOR,
                order=member.get("order", 99),
            )
        )

    # unique_together on MovieCredit prevents duplicate relationships on reruns.
    MovieCredit.objects.bulk_create(to_create, ignore_conflicts=True)


def _upsert_person(member: dict) -> Person:
    """
    Create or update a Person from a TMDB cast/crew member dictionary.

    TMDB person ID is the stable key; name and profile image path are refreshed
    whenever the person appears in a fetched credit list.
    """
    person, _ = Person.objects.update_or_create(
        tmdb_id=member["id"],
        defaults={
            "name":         member.get("name", ""),
            "profile_path": member.get("profile_path") or "",
        },
    )
    return person
