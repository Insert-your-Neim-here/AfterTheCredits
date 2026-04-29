from django.contrib import admin
from .models import Recommendation


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ['user', 'movie', 'score', 'created_at']
    search_fields = ['user__email', 'movie__title']
