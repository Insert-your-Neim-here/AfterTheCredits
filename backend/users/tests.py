from django.contrib.auth import get_user_model
from django.test import TestCase

from journal.models import JournalEntry
from movies.models import Movie
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
