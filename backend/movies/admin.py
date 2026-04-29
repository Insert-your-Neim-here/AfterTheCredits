from django.contrib import admin
from .models import (
    Genre,
    Keyword,
    Movie,
    MovieCredit,
    Person,
    StreamingPlatform,
    Wishlist,
)


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['title', 'release_date', 'runtime', 'vote_average']
    search_fields = ['title', 'keywords__name']
    list_filter = ['genres', 'keywords', 'streaming_platforms']


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name', 'tmdb_id']


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ['name', 'tmdb_id']
    search_fields = ['name']


@admin.register(StreamingPlatform)
class StreamingPlatformAdmin(admin.ModelAdmin):
    list_display = ['name']


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ['name', 'tmdb_id']
    search_fields = ['name']


@admin.register(MovieCredit)
class MovieCreditAdmin(admin.ModelAdmin):
    list_display = ['movie', 'person', 'role', 'order']
    list_filter = ['role', 'movie']
    search_fields = ['movie__title', 'person__name']

@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ['user', 'movie', 'added_at']
