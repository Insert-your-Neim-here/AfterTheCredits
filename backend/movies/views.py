from django.shortcuts import render

from .services.tmdb_client import (
    _tmdb_get,
    discover_movies,
    get_genres,
    popular_movies,
    poster_url,
    search_movies,
)



# Create your views here.
def browse_view(request):
    q = request.GET.get("q", "").strip()
    genre = request.GET.get("genre", "").strip()
    runtime = request.GET.get("runtime", "").strip()
    year = request.GET.get("year", "").strip()
    sort_by = request.GET.get("sort_by", "popularity.desc").strip()
    page = request.GET.get("page", "1").strip()

    try:
        page = int(page)
    except ValueError:
        page = 1

    genres = get_genres()

    if q:
        movies = search_movies(q, page=page)
    elif genre or runtime or year or sort_by != "popularity.desc":
        movies = discover_movies(
            page=page,
            genre=genre,
            runtime=runtime,
            year=year,
            sort_by=sort_by,
            uk_only=True,
        )
    else:
        movies = popular_movies(page=page, uk_only=True)

    for movie in movies:
        movie["poster_url"] = poster_url(movie.get("poster_path"))

    context = {
        "movies": movies,
        "genres": genres,
        "q": q,
        "genre": genre,
        "runtime": runtime,
        "year": year,
        "sort_by": sort_by,
        "page": page,
        "active_page": "browse",
    }

    return render(request, "movies/browse.html", context)

def movie_detail_view(request, tmdb_id):
    movie = _tmdb_get(f"/movie/{tmdb_id}", params={"language": "en-GB"})
    if not movie:
        return render(request, "404.html", status=404)

    movie["poster_url"] = poster_url(movie.get("poster_path"))
    movie["backdrop_url"] = poster_url(movie.get("backdrop_path"))

    context = {
        "movie": movie,
        "active_page": "browse",
    }
    return render(request, "movies/movie_details.html", context)
