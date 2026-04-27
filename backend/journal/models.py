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
     # ── Free-text ──────────────────────────────────────────────
    raw_text = models.TextField()
    embedding = VectorField(dimensions=384, null=True, blank=True)
 
    # ── Survey answers (all nullable — answered step by step) ──
    # Step 1: overall sentiment
    is_positive = models.BooleanField(null=True, blank=True)
    # Step 2: genre affinity → boosts genre-similar recommendations
    liked_genre = models.BooleanField(null=True, blank=True)
    # Step 3: story/writing → boosts writer/director signal
    liked_story = models.BooleanField(null=True, blank=True)
    # Step 4: performances → boosts cast signal
    liked_performances = models.BooleanField(null=True, blank=True)
    # Step 5: rewatchability → strong positive/negative weight
    would_rewatch = models.BooleanField(null=True, blank=True)
 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        unique_together = ("user", "movie")
        ordering = ["-created_at"]
 
    def __str__(self):
        return f"{self.user} — {self.movie.title if self.movie else 'No Movie'}"
 
    @property
    def survey_score(self):
        """0–5 integer score based on survey answers."""
        fields = [
            self.is_positive,
            self.liked_genre,
            self.liked_story,
            self.liked_performances,
            self.would_rewatch,
        ]
        answered = [f for f in fields if f is not None]
        if not answered:
            return None
        return sum(1 for f in answered if f)
 
    @property
    def survey_answers(self):
        """Ordered list of (label, value) for display."""
        return [
            ("Overall", self.is_positive),
            ("Genre", self.liked_genre),
            ("Story", self.liked_story),
            ("Performances", self.liked_performances),
            ("Rewatch", self.would_rewatch),
        ]