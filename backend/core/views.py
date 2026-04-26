from django.shortcuts import render

# Create your views here.
# core/views.py
from django.core.paginator import Paginator

from movies.models import Movie, Genre


def home_view(request):
    """
    Homepage — shows the hero, filmstrip, and the browseable movie grid.
    Before TMDb data is loaded, renders 20 placeholder cards.
    """
    movies_qs = Movie.objects.all().order_by('-popularity', 'title')
    genres    = Genre.objects.all().order_by('name')

    # Search
    q = request.GET.get('q', '').strip()
    if q:
        movies_qs = movies_qs.filter(title__icontains=q)

    # Filters
    genre_id = request.GET.get('genre')
    if genre_id:
        movies_qs = movies_qs.filter(genres__id=genre_id)

    runtime = request.GET.get('runtime')
    if runtime == 'short':
        movies_qs = movies_qs.filter(runtime__lt=90)
    elif runtime == 'medium':
        movies_qs = movies_qs.filter(runtime__gte=90, runtime__lte=120)
    elif runtime == 'long':
        movies_qs = movies_qs.filter(runtime__gt=120)

    paginator = Paginator(movies_qs, 20)
    page_obj  = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'core/home.html', {
        'movies':           page_obj,
        'page_obj':         page_obj,
        'genres':           genres,
        # Shown when there are no movies yet (range of 20)
        'placeholder_range': range(20),
    })


