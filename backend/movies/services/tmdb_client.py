import requests
import logging
from django.conf import settings
from sentence_transformers import SentenceTransformer
from movies.models import Movie, Genre, StreamingPlatform

logger = logging.getLogger(__name__)

_model = None

def get_embedding_model():
    global _model
    if _model is None:
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model

def compute_embedding(text: str) -> list[float]:
    model = get_embedding_model()
    return model.encode(text).tolist()

def build_movie_text(title: str, overview: str, genres: list[str]) -> str:
    genre_str = ", ".join(genres) if genres else "Unknown"
    return f"{title}. {overview} Genres: {genre_str}."

TMDB_BASE = "https://api.themoviedb.org/3"

def _tmdb_get(path: str, params: dict = None) -> dict:
    params = params or {}
    params["api_key"] = settings.TMDB_API_KEY
    response = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()

def fetch_and_store_genres():
    """Fetch all movie genres from TMDb and store them."""
    data = _tmdb_get("/genre/movie/list")
    for g in data.get("genres", []):
        Genre.objects.update_or_create(
            tmdb_id=g["id"],
            defaults={"name": g["name"]},
        )
    logger.info(f"Synced {len(data.get('genres', []))} genres.")

def fetch_streaming_platforms():
    """
    Fetch watch providers available in the US.
    Stores them as StreamingPlatform objects.
    """
    data = _tmdb_get("/watch/providers/movie", params={"watch_region": "US"})
    count = 0
    for provider in data.get("results", []):
        StreamingPlatform.objects.update_or_create(
            name=provider["provider_name"],
            defaults={
                "logo_url": f"https://image.tmdb.org/t/p/original{provider['logo_path']}"
                if provider.get("logo_path") else "",
            },
        )
        count += 1
    logger.info(f"Synced {count} streaming platforms.")

def fetch_movie_watch_providers(tmdb_id: int) -> list[str]:
    """Return list of US streaming platform names for a movie."""
    try:
        data = _tmdb_get(f"/movie/{tmdb_id}/watch/providers")
        us = data.get("results", {}).get("US", {})
        flatrate = us.get("flatrate", [])
        return [p["provider_name"] for p in flatrate]
    except Exception:
        return []

def fetch_and_store_movies(pages: int = 5, list_type: str = "popular"):
    """
    Fetch movies from TMDb (popular/top_rated/now_playing) and store with embeddings.
    list_type: 'popular' | 'top_rated' | 'now_playing'
    """
    fetch_and_store_genres()
    genre_map = {g.name: g for g in Genre.objects.all()}

    total_saved = 0

    for page in range(1, pages + 1):
        logger.info(f"Fetching page {page}/{pages} ({list_type})...")
        try:
            data = _tmdb_get(f"/movie/{list_type}", params={"page": page})
        except Exception as e:
            logger.error(f"Failed to fetch page {page}: {e}")
            continue

        for item in data.get("results", []):
            try:
                _process_movie(item, genre_map)
                total_saved += 1
            except Exception as e:
                logger.error(f"Error processing movie {item.get('id')}: {e}")

    logger.info(f"Done. Saved/updated {total_saved} movies.")

def _process_movie(item: dict, genre_map: dict):
    """Fetch full movie details, compute embedding, and upsert into DB."""
    tmdb_id = item["id"]

    # Fetch full details (includes runtime)
    details = _tmdb_get(f"/movie/{tmdb_id}")

    title = details.get("title", "")
    overview = details.get("overview", "") or ""
    genre_objects = []

    for g in details.get("genres", []):
        genre_obj = genre_map.get(g["name"])
        if genre_obj:
            genre_objects.append(genre_obj)

    # Compute embedding
    genre_names = [g.name for g in genre_objects]
    text = build_movie_text(title, overview, genre_names)
    embedding = compute_embedding(text)

    movie, _ = Movie.objects.update_or_create(
        tmdb_id=tmdb_id,
        defaults={
            "title": title,
            "overview": overview,
            "release_date": details.get("release_date") or None,
            "runtime": details.get("runtime") or None,
            "poster_path": details.get("poster_path") or "",
            "backdrop_path": details.get("backdrop_path") or "",
            "vote_average": details.get("vote_average") or 0,
            "vote_count": details.get("vote_count") or 0,
            "popularity": details.get("popularity") or 0,
            "embedding": embedding,
        },
    )

    if genre_objects:
        movie.genres.set(genre_objects)

    # Streaming platforms
    platform_names = fetch_movie_watch_providers(tmdb_id)
    platforms = StreamingPlatform.objects.filter(name__in=platform_names)
    movie.streaming_platforms.set(platforms)

    movie.save()