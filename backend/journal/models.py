from django.db import models

# Create your models here.
from pgvector.django import VectorField
from django.conf import settings


class JournalEntry(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='journal_entries'
    )
    movie = models.ForeignKey(
        'movies.Movie',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='journal_entries'
    )
    raw_text = models.TextField()
    embedding = VectorField(dimensions=384, null=True, blank=True)
    is_positive = models.BooleanField(default=True)  # based on yes/no questions

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.created_at.strftime('%Y-%m-%d')}"