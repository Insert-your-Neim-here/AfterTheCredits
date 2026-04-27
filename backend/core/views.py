from django.shortcuts import render

from movies.services.tmdb_client import (
    attach_streaming_platforms,
    discover_movies,
    get_genres,
    popular_movies,
    poster_url,
    search_keyword_ids,
    search_movies,
    store_loaded_movies,
)


def home_view(request):
    """
    Homepage - shows the hero, filmstrip, and the browseable movie grid.
    """
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

    return render(request, "core/home.html", {
        "movies": movies,
        "genres": genres,
        "q": q,
        "genre": genre,
        "runtime": runtime,
        "year": year,
        "sort_by": sort_by,
        "active_tags": active_tags,
        "page": page,
        "previous_page_url": previous_page_url,
        "next_page_url": next_page_url,
    })
