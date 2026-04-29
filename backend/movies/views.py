from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Genre, Movie, Wishlist

from .services.tmdb_client import (
    _tmdb_get,
    fetch_movie_watch_providers,
    poster_url,
)

def release_year_options():
    return range(date.today().year, 1949, -1)


def _database_genre_options():
    return [
        {"id": genre.tmdb_id, "name": genre.name}
        for genre in Genre.objects.order_by("name")
    ]


def _browse_sort(sort_by):
    sort_options = {
        "popularity.desc": "-popularity",
        "popularity.asc": "popularity",
        "vote_average.desc": "-vote_average",
        "vote_average.asc": "vote_average",
        "release_date.desc": "-release_date",
        "release_date.asc": "release_date",
        "title.asc": "title",
        "title.desc": "-title",
    }
    return sort_options.get(sort_by, "-popularity")


def _browse_queryset(q, genre, runtime, year, active_tags, sort_by):
    movies = Movie.objects.prefetch_related("genres", "keywords", "streaming_platforms")

    if q:
        movies = movies.filter(Q(title__icontains=q) | Q(overview__icontains=q))

    if genre:
        movies = movies.filter(genres__tmdb_id=genre)

    if runtime == "short":
        movies = movies.filter(runtime__lte=89)
    elif runtime == "medium":
        movies = movies.filter(runtime__gte=90, runtime__lte=120)
    elif runtime == "long":
        movies = movies.filter(runtime__gte=121)

    if year.isdigit():
        movies = movies.filter(release_date__year=int(year))

    if active_tags:
        tag_filter = Q()
        for tag in active_tags:
            tag_filter |= Q(keywords__name__icontains=tag)
        movies = movies.filter(tag_filter)

    return movies.distinct().order_by(_browse_sort(sort_by), "title")


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

    genres = _database_genre_options()
    movies_queryset = _browse_queryset(q, genre, runtime, year, active_tags, sort_by)
    paginator = Paginator(movies_queryset, 20)
    page_obj = paginator.get_page(page)
    page = page_obj.number
    movies = [_movie_from_database(movie) for movie in page_obj.object_list]

    previous_page_url = None
    if page_obj.has_previous():
        previous_params = request.GET.copy()
        previous_params["page"] = page_obj.previous_page_number()
        previous_page_url = f"?{previous_params.urlencode()}"

    next_page_url = None
    if page_obj.has_next():
        next_params = request.GET.copy()
        next_params["page"] = page_obj.next_page_number()
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

def _movie_from_database(movie):
    return {
        "id": movie.tmdb_id,
        "title": movie.title,
        "overview": movie.overview,
        "release_date": movie.release_date.isoformat() if movie.release_date else "",
        "runtime": movie.runtime,
        "poster_url": movie.poster_url,
        "backdrop_url": poster_url(movie.backdrop_path),
        "vote_average": movie.vote_average,
        "vote_count": movie.vote_count,
        "popularity": movie.popularity,
        "genres": [
            {"id": genre.tmdb_id, "name": genre.name}
            for genre in movie.genres.all()
        ],
        "streaming_platforms": [
            platform.name for platform in movie.streaming_platforms.all()
        ],
    }


def movie_detail_view(request, tmdb_id):
    db_movie = (
        Movie.objects.filter(tmdb_id=tmdb_id)
        .prefetch_related("genres", "streaming_platforms")
        .first()
    )

    if db_movie:
        movie = _movie_from_database(db_movie)
    else:
        movie = _tmdb_get(f"/movie/{tmdb_id}", params={"language": "en-GB"})
        if not movie:
            return render(request, "404.html", status=404)

        movie["poster_url"] = poster_url(movie.get("poster_path"))
        movie["backdrop_url"] = poster_url(movie.get("backdrop_path"))
        movie["streaming_platforms"] = fetch_movie_watch_providers(tmdb_id)

    if request.user.is_authenticated and db_movie:
        on_wishlist = Wishlist.objects.filter(user=request.user, movie=db_movie).exists()
        has_journal_entry = db_movie.journal_entries.filter(user=request.user).exists()
    else:
        on_wishlist = False
        has_journal_entry = False

    context = {
        "movie": movie,
        "on_wishlist": on_wishlist,
        "has_journal_entry": has_journal_entry,
        "active_page": "browse",
    }
    return render(request, "movies/movie_details.html", context)


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

    return redirect("movies:details", tmdb_id=tmdb_id)


@login_required
def wishlist_view(request):
    entries = (
        Wishlist.objects.filter(user=request.user)
        .select_related("movie")
        .order_by("-added_at")
    )
    return render(request, "movies/wishlist.html", {"entries": entries})
