from django.contrib import admin
from .models import JournalEntry


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ['user', 'movie', 'is_positive', 'created_at']
    list_filter = ['is_positive']
    search_fields = ['user__email', 'raw_text']
