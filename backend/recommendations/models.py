from django.db import models

# Create your models here.
from django.conf import settings


class Recommendation(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recommendations'
    )
    movie = models.ForeignKey(
        'movies.Movie',
        on_delete=models.CASCADE,
        related_name='recommendations'
    )
    score = models.FloatField()  # cosine similarity score
    explanation = models.TextField(blank=True)
    journal_snippet = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-score']
        unique_together = ['user', 'movie']

    def __str__(self):
        return f"{self.user.email} → {self.movie.title} ({self.score:.2f})"