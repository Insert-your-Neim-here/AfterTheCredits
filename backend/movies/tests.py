from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from journal.models import JournalEntry

from .models import Movie


class MovieDetailJournalCtaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="password123",
        )
        self.movie_data = {
            "id": 101,
            "title": "Existing Entry",
            "overview": "A test movie.",
            "release_date": "2024-01-01",
            "runtime": 100,
            "poster_path": "",
            "backdrop_path": "",
            "vote_average": 7.5,
            "vote_count": 10,
            "popularity": 1,
            "genres": [],
        }

    def get_details(self):
        self.client.force_login(self.user)
        with (
            patch("movies.views._tmdb_get", return_value=self.movie_data),
            patch("movies.views.fetch_movie_watch_providers", return_value=[]),
        ):
            return self.client.get(reverse("movies:details", args=[self.movie_data["id"]]))

    def test_shows_journal_cta_without_existing_entry(self):
        response = self.get_details()

        self.assertContains(response, "Write Journal Entry")

    def test_hides_journal_cta_with_existing_entry(self):
        movie = Movie.objects.create(tmdb_id=101, title="Existing Entry")
        JournalEntry.objects.create(
            user=self.user,
            movie=movie,
            raw_text="Already wrote this one up.",
        )

        response = self.get_details()

        self.assertNotContains(response, "Write Journal Entry")
        self.assertContains(response, "You already have a journal entry for this movie.")
