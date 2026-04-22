from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Movie, Genre, StreamingPlatform, Wishlist


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['title', 'release_date', 'runtime', 'vote_average']
    search_fields = ['title']
    list_filter = ['genres', 'streaming_platforms']


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name', 'tmdb_id']


@admin.register(StreamingPlatform)
class StreamingPlatformAdmin(admin.ModelAdmin):
    list_display = ['name']

@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ['user', 'movie', 'added_at']