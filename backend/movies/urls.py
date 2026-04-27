# movies/urls.py
from django.urls import path
from . import views
app_name = 'movies'
urlpatterns = [
  path('browse/', views.browse_view, name='browse'),
  path('wishlist/', views.wishlist_view, name='wishlist'),
  path('wishlist/toggle/<int:tmdb_id>/', views.toggle_wishlist_view, name='toggle_wishlist'),
  path('details/<int:tmdb_id>/', views.movie_detail_view, name='details'),
]
