from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from journal.models import JournalEntry
from movies.models import Genre, Movie
from users.services.profile_embedding import (
    build_profile_embedding,
    update_user_profile_embedding,
)


class ProfileEmbeddingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="test@example.com",
            username="testuser",
            password="password",
        )
        self.movie = Movie.objects.create(
            tmdb_id=1,
            title="Test Film",
            overview="A test film.",
        )

    def test_build_profile_embedding_weights_and_normalizes_entries(self):
        entries = [
            JournalEntry(
                user=self.user,
                movie=self.movie,
                raw_text="Loved it.",
                embedding=[1.0, 0.0],
                is_positive=True,
                liked_genre=True,
            ),
            JournalEntry(
                user=self.user,
                movie=self.movie,
                raw_text="Not perfect.",
                embedding=[0.0, 1.0],
                is_positive=True,
            ),
        ]

        profile = build_profile_embedding(entries)

        self.assertAlmostEqual(profile[0], 0.8944271909999159)
        self.assertAlmostEqual(profile[1], 0.4472135954999579)

    def test_update_user_profile_embedding_persists_and_clears_profile(self):
        embedding = [1.0] + [0.0] * 383
        entry = JournalEntry.objects.create(
            user=self.user,
            movie=self.movie,
            raw_text="Loved it.",
            embedding=embedding,
            is_positive=True,
        )

        profile = update_user_profile_embedding(self.user)
        self.user.refresh_from_db()

        self.assertEqual(profile, embedding)
        self.assertEqual(self.user.profile_embedding, embedding)

        entry.delete()
        profile = update_user_profile_embedding(self.user)
        self.user.refresh_from_db()

        self.assertIsNone(profile)
        self.assertIsNone(self.user.profile_embedding)


class ProfileTasteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="taste@example.com",
            username="tasteuser",
            password="password",
        )

    def test_negative_genre_is_not_top_genre(self):
        comedy = Genre.objects.create(tmdb_id=35, name="Comedy")
        drama = Genre.objects.create(tmdb_id=18, name="Drama")
        comedy_movie = Movie.objects.create(tmdb_id=101, title="Bad Comedy")
        drama_movie = Movie.objects.create(tmdb_id=102, title="Good Drama")
        comedy_movie.genres.add(comedy)
        drama_movie.genres.add(drama)

        JournalEntry.objects.create(
            user=self.user,
            movie=comedy_movie,
            raw_text="I did not like this genre.",
            is_positive=True,
            liked_genre=False,
            liked_story=True,
            liked_performances=True,
            would_rewatch=True,
        )
        JournalEntry.objects.create(
            user=self.user,
            movie=drama_movie,
            raw_text="This worked for me.",
            is_positive=True,
            liked_genre=True,
            liked_story=True,
            liked_performances=True,
            would_rewatch=True,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("users:profile"))

        top_genre_names = {
            row["movie__genres__name"] for row in response.context["top_genres"]
        }

        self.assertNotIn("Comedy", top_genre_names)
        self.assertIn("Drama", top_genre_names)

    def test_top_genres_are_limited_to_five(self):
        genres = [
            Genre.objects.create(tmdb_id=1000 + i, name=f"Genre {i}")
            for i in range(6)
        ]

        for i, genre in enumerate(genres):
            movie = Movie.objects.create(tmdb_id=2000 + i, title=f"Movie {i}")
            movie.genres.add(genre)
            JournalEntry.objects.create(
                user=self.user,
                movie=movie,
                raw_text=f"Entry {i}",
                is_positive=True,
                liked_genre=True,
                liked_story=True,
                liked_performances=True,
                would_rewatch=True,
            )

        self.client.force_login(self.user)
        response = self.client.get(reverse("users:profile"))

        top_genre_names = [
            row["movie__genres__name"] for row in response.context["top_genres"]
        ]

        self.assertEqual(len(top_genre_names), 5)
