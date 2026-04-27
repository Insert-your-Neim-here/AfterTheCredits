from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services.tmdb_client import (
    _tmdb_get,
    attach_streaming_platforms,
    discover_movies,
    fetch_movie_watch_providers,
    get_genres,
    popular_movies,
    poster_url,
    search_keyword_ids,
    search_movies,
    store_loaded_movies,
)



def release_year_options():
    return range(date.today().year, 1949, -1)


@login_required
def browse_view(request):
    q = request.GET.get("q", "").strip()
    genre = request.GET.get("genre", "").strip()
    runtime = request.GET.get("runtime", "").strip()
    year = request.GET.get("year", "").strip()
    sort_by = request.GET.get("sort_by", "popularity.desc").strip()
    active_tags = [
        tag.strip()
        for tag in request.GET.getlist("tags")
        if tag.strip()
    ]
    page = request.GET.get("page", "1").strip()

    try:
        page = int(page)
    except ValueError:
        page = 1

    genres = get_genres()
    keyword_ids = search_keyword_ids(active_tags) if active_tags else []
    has_discovery_filters = bool(
        genre or runtime or year or keyword_ids or sort_by != "popularity.desc"
    )
    used_search = q and not has_discovery_filters

    if used_search:
        movies = search_movies(q, page=page, uk_only=True)
    elif q or has_discovery_filters:
        movies = discover_movies(
            page=page,
            genre=genre,
            runtime=runtime,
            year=year,
            text_query=q,
            keyword_ids=keyword_ids,
            sort_by=sort_by,
            uk_only=True,
        )
    else:
        movies = popular_movies(page=page, uk_only=True)

    if not used_search:
        movies = attach_streaming_platforms(movies)

    for movie in movies:
        movie["poster_url"] = poster_url(movie.get("poster_path"))

    store_loaded_movies(movies)

    previous_page_url = None
    if page > 1:
        previous_params = request.GET.copy()
        previous_params["page"] = page - 1
        previous_page_url = f"?{previous_params.urlencode()}"

    next_page_url = None
    if len(movies) == 20:
        next_params = request.GET.copy()
        next_params["page"] = page + 1
        next_page_url = f"?{next_params.urlencode()}"

    context = {
        "movies": movies,
        "genres": genres,
        "q": q,
        "genre": genre,
        "runtime": runtime,
        "year": year,
        "year_options": release_year_options(),
        "sort_by": sort_by,
        "active_tags": active_tags,
        "page": page,
        "previous_page_url": previous_page_url,
        "next_page_url": next_page_url,
        "active_page": "browse",
    }

    return render(request, "movies/browse.html", context)

def movie_detail_view(request, tmdb_id):
    movie = _tmdb_get(f"/movie/{tmdb_id}", params={"language": "en-GB"})
    if not movie:
        return render(request, "404.html", status=404)

    movie["poster_url"] = poster_url(movie.get("poster_path"))
    movie["backdrop_url"] = poster_url(movie.get("backdrop_path"))
    movie["streaming_platforms"] = fetch_movie_watch_providers(tmdb_id)

    context = {
        "movie": movie,
        "active_page": "browse",
    }
    return render(request, "movies/movie_details.html", context)
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .models import Movie, Wishlist




@login_required
@require_POST
def toggle_wishlist_view(request, tmdb_id):
    movie = get_object_or_404(Movie, tmdb_id=tmdb_id)
    wishlist_item, created = Wishlist.objects.get_or_create(
        user=request.user, movie=movie
    )
    if not created:
        wishlist_item.delete()
        on_wishlist = False
    else:
        on_wishlist = True

    # Support both AJAX (fetch) and plain form POST
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"on_wishlist": on_wishlist})

    from django.shortcuts import redirect
    return redirect("movies:detail", tmdb_id=tmdb_id)


@login_required
def wishlist_view(request):
    entries = (
        Wishlist.objects.filter(user=request.user)
        .select_related("movie")
        .order_by("-added_at")
    )
    return render(request, "movies/wishlist.html", {"entries": entries})
