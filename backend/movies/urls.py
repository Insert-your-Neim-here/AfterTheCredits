# movies/urls.py
from django.urls import path
from . import views
app_name = 'movies'
urlpatterns = [
  path('browse/', views.browse_view, name='browse'),
  path('details/<int:tmdb_id>/', views.movie_detail_view, name='details'),
]