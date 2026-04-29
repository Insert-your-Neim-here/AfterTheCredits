import os
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.cache import cache
from movies.models import Movie, Genre, Keyword, StreamingPlatform, MovieCredit, Person
from core.services.embedding_service import build_movie_text, compute_embedding

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_TOKEN = os.environ.get("TMDB_TOKEN")
STREAMING_REGION = getattr(settings, "TMDB_REGION", "GB")
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_CACHE_TIMEOUT = 60 * 60 * 12
PROVIDER_LOOKUP_WORKERS = 8

if not TMDB_TOKEN:
    raise RuntimeError("TMDB_TOKEN is not set. Check your .env file.")


def _headers():
    return {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "Content-Type": "application/json;charset=utf-8",
    }


def _tmdb_get(path: str, params: dict | None = None) -> dict:
    params = params or {}
    cache_key = f"tmdb:{path}:{urlencode(sorted(params.items()), doseq=True)}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    response = requests.get(
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
    return (region or STREAMING_REGION).upper()


def _streaming_params(region: str | None = None) -> dict:
    return {
        "watch_region": _streaming_region(region),
        "with_watch_monetization_types": "flatrate",
    }


def search_movies(
    query: str,
    page: int = 1,
    region: str | None = None,
):
    data = _tmdb_get(
        "/search/movie",
        params={
            "query": query,
            "include_adult": "false",
            "language": "en-GB",
            "page": page,
        },
    )
    return attach_streaming_platforms(data.get("results", []), region=region)


def popular_movies(
    page: int = 1,
    region: str | None = None,
):
    data = _tmdb_get(
        "/discover/movie",
        params={
            "language": "en-GB",
            "page": page,
            "sort_by": "popularity.desc",
            "include_adult": "false",
            **_streaming_params(region),
        },
    )
    return attach_streaming_platforms(data.get("results", []), region=region)


def get_genres():
    data = _tmdb_get(
        "/genre/movie/list",
        params={"language": "en-GB"},
    )
    return data.get("genres", [])


def search_keyword_ids(tags: list[str]) -> list[int]:
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
    params = {
        "language": "en-GB",
        "page": page,
        "sort_by": sort_by,
        "include_adult": "false",
        **_streaming_params(region),
    }

    if genre:
        params["with_genres"] = genre

    if text_query:
        params["with_text_query"] = text_query

    if keyword_ids:
        params["with_keywords"] = "|".join(str(keyword_id) for keyword_id in keyword_ids)

    if runtime == "short":
        params["with_runtime.lte"] = 89
    elif runtime == "medium":
        params["with_runtime.gte"] = 90
        params["with_runtime.lte"] = 120
    elif runtime == "long":
        params["with_runtime.gte"] = 121

    if year:
        params["primary_release_year"] = year

    data = _tmdb_get("/discover/movie", params=params)
    return attach_streaming_platforms(data.get("results", []), region=region)

def fetch_and_store_genres():
    """Fetch all movie genres from TMDb and store them."""
    data = _tmdb_get("/genre/movie/list")
    for g in data.get("genres", []):
        Genre.objects.update_or_create(
            tmdb_id=g["id"],
            defaults={"name": g["name"]},
        )
    logger.info(f"Synced {len(data.get('genres', []))} genres.")

def fetch_streaming_platforms(region: str | None = None):
    """
    Fetch watch providers available in the configured streaming region.
    Stores them as StreamingPlatform objects.
    """
    streaming_region = _streaming_region(region)
    data = _tmdb_get(
        "/watch/providers/movie",
        params={"watch_region": streaming_region},
    )
    count = 0
    for provider in data.get("results", []):
        StreamingPlatform.objects.update_or_create(
            name=provider["provider_name"],
        )
        count += 1
    logger.info(f"Synced {count} streaming platforms for {streaming_region}.")

def _normalize_provider_name(name: str) -> str:
    """Collapse TMDb subscription tiers into the main streaming service name."""
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
    """Return streaming platform names for a movie in the configured region."""
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
    """Return TMDb keywords for a movie."""
    try:
        data = _tmdb_get(f"/movie/{tmdb_id}/keywords")
        return data.get("keywords", [])
    except Exception:
        return []


def attach_streaming_platforms(
    movies: list[dict],
    region: str | None = None,
) -> list[dict]:
    """Attach streaming providers and return only streamable movies."""
    if not movies:
        return []

    def _with_providers(movie):
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
    """Persist TMDb result dictionaries that were loaded for a page."""
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
    Fetch movies from TMDb (popular/top_rated/now_playing) and store with embeddings.
    list_type: 'popular' | 'top_rated' | 'now_playing'
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
    tmdb_ids = [movie.get("id") for movie in movies if movie.get("id")]
    if not tmdb_ids:
        return []

    existing_tmdb_ids = set(
        Movie.objects.filter(tmdb_id__in=tmdb_ids).values_list("tmdb_id", flat=True)
    )
    return [movie for movie in movies if movie.get("id") not in existing_tmdb_ids]


def _fetch_movie_list_page(list_type: str, page: int, region: str) -> dict:
    if list_type == "popular":
        return _tmdb_get(
            "/discover/movie",
            params={
                "language": "en-GB",
                "page": page,
                "sort_by": "popularity.desc",
                "include_adult": "false",
                **_streaming_params(region),
            },
        )

    if list_type == "top_rated":
        return _tmdb_get(
            "/discover/movie",
            params={
                "language": "en-GB",
                "page": page,
                "sort_by": "vote_average.desc",
                "vote_count.gte": 200,
                "include_adult": "false",
                **_streaming_params(region),
            },
        )

    return _tmdb_get(
        f"/movie/{list_type}",
        params={"language": "en-GB", "page": page, "region": region},
    )


def _process_movie(
    item: dict,
    genre_map: dict,
    region: str | None = None,
) -> bool:
    """Fetch details and store a streamable movie with metadata and embeddings."""
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

    # Fetch full details (includes runtime)
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

    genre_names = [g.name for g in genre_objects]
    keyword_names = [keyword.name for keyword in keyword_objects]
    text = build_movie_text(title, overview, genre_names, keyword_names)
    embedding = compute_embedding(text)

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

    movie_defaults["embedding"] = embedding

    movie = Movie.objects.create(tmdb_id=tmdb_id, **movie_defaults)

    if genre_objects:
        movie.genres.set(genre_objects)
    else:
        movie.genres.clear()

    if keyword_objects:
        movie.keywords.set(keyword_objects)
    else:
        movie.keywords.clear()

    for platform_name in platform_names:
        StreamingPlatform.objects.get_or_create(name=platform_name)
    platforms = StreamingPlatform.objects.filter(name__in=platform_names)
    movie.streaming_platforms.set(platforms)

    movie.save()
    return True


def poster_url(poster_path: str | None):
    if not poster_path:
        return None
    return f"{TMDB_IMAGE_BASE}{poster_path}"


MAX_ACTORS = 10  # top-billed cast members to store

def fetch_and_store_credits(movie: Movie, tmdb_id: int) -> None:
    """
    Fetch cast + crew from TMDb credits endpoint and upsert into
    Person / MovieCredit tables.
    Stores: director(s), writers (screenplay/story/writer credit),
    and top MAX_ACTORS cast members.
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

    # Bulk upsert (ignore duplicates from unique_together)
    MovieCredit.objects.bulk_create(to_create, ignore_conflicts=True)


def _upsert_person(member: dict) -> Person:
    """Get or create a Person from a TMDb cast/crew dict."""
    person, _ = Person.objects.update_or_create(
        tmdb_id=member["id"],
        defaults={
            "name":         member.get("name", ""),
            "profile_path": member.get("profile_path") or "",
        },
    )
    return person
