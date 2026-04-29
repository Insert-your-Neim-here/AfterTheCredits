from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class StreamingPlatform(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Genre(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Keyword(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Movie(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    title = models.CharField(max_length=255)
    overview = models.TextField(blank=True)
    release_date = models.DateField(null=True, blank=True)
    runtime = models.IntegerField(null=True, blank=True)
    poster_path = models.CharField(max_length=255, blank=True)
    backdrop_path = models.CharField(max_length=255, blank=True)
    vote_average = models.FloatField(default=0)
    vote_count = models.IntegerField(default=0)
    popularity = models.FloatField(default=0)
    genres = models.ManyToManyField(Genre, blank=True)
    keywords = models.ManyToManyField(Keyword, blank=True)
    streaming_platforms = models.ManyToManyField(
        StreamingPlatform,
        blank=True,
        related_name="movies",
    )
    embedding = VectorField(dimensions=384, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-popularity"]

    def __str__(self):
        return self.title

    @property
    def poster_url(self):
        if self.poster_path:
            return f"https://image.tmdb.org/t/p/w500{self.poster_path}"
        return None


class Wishlist(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wishlist",
    )
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="wishlisted_by",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "movie"]

    def __str__(self):
        return f"{self.user.email} -> {self.movie.title}"


class Person(models.Model):
    tmdb_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    profile_path = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name_plural = "people"

    def __str__(self):
        return self.name


class MovieCredit(models.Model):
    ROLE_DIRECTOR = "director"
    ROLE_WRITER = "writer"
    ROLE_ACTOR = "actor"
    ROLE_PRODUCER = "producer"

    ROLE_CHOICES = [
        (ROLE_DIRECTOR, "Director"),
        (ROLE_WRITER, "Writer"),
        (ROLE_ACTOR, "Actor"),
        (ROLE_PRODUCER, "Producer"),
    ]

    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="credits")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="credits")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ("movie", "person", "role")
        ordering = ["role", "order"]

    def __str__(self):
        return f"{self.person.name} - {self.role} in {self.movie.title}"
