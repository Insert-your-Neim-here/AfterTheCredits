from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from journal.models import JournalEntry
from movies.models import Genre, Movie, MovieCredit, Person
from recommendations.models import Recommendation
from recommendations.services import (
    MAIN_ACTOR_LIMIT,
    MIN_JOURNAL_ENTRIES,
    _build_explanation,
    _credit_person_ids,
    _excluded_genre_ids,
    get_recommendations,
)


class RecommendationThresholdTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="threshold",
            email="threshold@example.com",
            password="password",
        )
        self.movie = Movie.objects.create(tmdb_id=9001, title="Candidate")
        self.movie.embedding = [0.1] * 384
        self.movie.save(update_fields=["embedding"])
        Recommendation.objects.create(
            user=self.user,
            movie=self.movie,
            score=0.9,
            explanation="Existing pick",
        )

    def _add_entry(self, tmdb_id: int):
        movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"Seen {tmdb_id}")
        JournalEntry.objects.create(
            user=self.user,
            movie=movie,
            raw_text="A logged film.",
            is_positive=True,
        )

    def test_existing_recommendations_hidden_until_three_entries(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 1):
            self._add_entry(tmdb_id)

        self.assertFalse(get_recommendations(self.user).exists())

    def test_existing_recommendations_show_after_three_entries(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        self.assertEqual(list(get_recommendations(self.user)), list(Recommendation.objects.all()))

    def test_recommendation_links_use_tmdb_id_not_database_id(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        self.client.force_login(self.user)
        response = self.client.get(reverse("recommendations:list"))

        self.assertContains(response, reverse("movies:details", args=[self.movie.tmdb_id]))
        self.assertContains(response, reverse("journal:create", args=[self.movie.tmdb_id]))
        self.assertContains(
            response,
            reverse("movies:toggle_wishlist", args=[self.movie.tmdb_id]),
        )
        self.assertNotContains(response, reverse("movies:details", args=[self.movie.id]))

    def test_recommendations_can_be_filtered_by_watch_session_runtime(self):
        short_movie = Movie.objects.create(tmdb_id=9101, title="Short Pick", runtime=88)
        short_movie.embedding = [0.2] * 384
        short_movie.save(update_fields=["embedding"])
        long_movie = Movie.objects.create(tmdb_id=9102, title="Long Pick", runtime=121)
        long_movie.embedding = [0.3] * 384
        long_movie.save(update_fields=["embedding"])
        Recommendation.objects.create(
            user=self.user,
            movie=short_movie,
            score=0.8,
            explanation="Short enough",
        )
        Recommendation.objects.create(
            user=self.user,
            movie=long_movie,
            score=0.7,
            explanation="Too long",
        )
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        self.client.force_login(self.user)
        response = self.client.get(reverse("recommendations:list"), {"runtime": "90"})

        self.assertContains(response, "Short Pick")
        self.assertNotContains(response, "Long Pick")
        self.assertNotContains(response, "Candidate")

    def test_recommendations_page_does_not_show_refresh_action(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        self.client.force_login(self.user)
        response = self.client.get(reverse("recommendations:list"))

        self.assertNotContains(response, "REFRESH")
        self.assertContains(response, "WATCH SESSION")


class CreditSignalTests(TestCase):
    def test_credit_person_ids_uses_top_five_actors(self):
        genre = Genre.objects.create(tmdb_id=1, name="Drama")
        movie = Movie.objects.create(tmdb_id=10, title="Ensemble")
        movie.genres.add(genre)

        actor_ids = []
        for order in range(7):
            person = Person.objects.create(tmdb_id=100 + order, name=f"Actor {order}")
            actor_ids.append(person.id)
            MovieCredit.objects.create(
                movie=movie,
                person=person,
                role=MovieCredit.ROLE_ACTOR,
                order=order,
            )

        self.assertEqual(
            _credit_person_ids(
                movie,
                {MovieCredit.ROLE_ACTOR},
                actor_limit=MAIN_ACTOR_LIMIT,
            ),
            set(actor_ids[:MAIN_ACTOR_LIMIT]),
        )

    def test_explanation_includes_matched_people(self):
        genre = Genre.objects.create(tmdb_id=2, name="Thriller")
        movie = Movie.objects.create(tmdb_id=20, title="Matched People")
        movie.genres.add(genre)

        director = Person.objects.create(tmdb_id=201, name="Dana Director")
        writer = Person.objects.create(tmdb_id=202, name="Will Writer")
        actor = Person.objects.create(tmdb_id=203, name="Ada Actor")
        MovieCredit.objects.create(
            movie=movie,
            person=director,
            role=MovieCredit.ROLE_DIRECTOR,
        )
        MovieCredit.objects.create(
            movie=movie,
            person=writer,
            role=MovieCredit.ROLE_WRITER,
        )
        MovieCredit.objects.create(
            movie=movie,
            person=actor,
            role=MovieCredit.ROLE_ACTOR,
            order=0,
        )

        explanation, _ = _build_explanation(
            movie,
            {genre.id},
            {},
            director_ids={director.id},
            writer_ids={writer.id},
            actor_ids={actor.id},
        )

        self.assertIn("Thriller", explanation)
        self.assertIn("director Dana Director", explanation)
        self.assertIn("writer Will Writer", explanation)
        self.assertIn("actor Ada Actor", explanation)


class SurveyTasteSignalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="taste",
            email="taste@example.com",
            password="password",
        )

    def test_single_disliked_genre_is_not_excluded_from_recommendations(self):
        comedy = Genre.objects.create(tmdb_id=35, name="Comedy")
        movie = Movie.objects.create(tmdb_id=101, title="Bad Comedy")
        movie.genres.add(comedy)

        entry = JournalEntry.objects.create(
            user=self.user,
            movie=movie,
            raw_text="This style did not work for me.",
            is_positive=True,
            liked_genre=False,
            liked_story=True,
            liked_performances=True,
            would_rewatch=True,
        )

        self.assertEqual(_excluded_genre_ids([entry]), set())

    def test_repeated_disliked_genre_is_excluded_from_recommendations(self):
        comedy = Genre.objects.create(tmdb_id=35, name="Comedy")
        first_movie = Movie.objects.create(tmdb_id=101, title="Bad Comedy")
        second_movie = Movie.objects.create(tmdb_id=102, title="Worse Comedy")
        first_movie.genres.add(comedy)
        second_movie.genres.add(comedy)

        first_entry = JournalEntry.objects.create(
            user=self.user,
            movie=first_movie,
            raw_text="This style did not work for me.",
            is_positive=True,
            liked_genre=False,
            liked_story=True,
            liked_performances=True,
            would_rewatch=True,
        )
        second_entry = JournalEntry.objects.create(
            user=self.user,
            movie=second_movie,
            raw_text="I still do not enjoy this genre.",
            is_positive=True,
            liked_genre=False,
            liked_story=True,
            liked_performances=True,
            would_rewatch=True,
        )

        self.assertEqual(_excluded_genre_ids([first_entry, second_entry]), {comedy.id})

    def test_three_no_answers_count_as_negative_genre_signal(self):
        thriller = Genre.objects.create(tmdb_id=53, name="Thriller")
        first_movie = Movie.objects.create(tmdb_id=201, title="Mostly Disliked Thriller")
        second_movie = Movie.objects.create(tmdb_id=202, title="Another Bad Thriller")
        first_movie.genres.add(thriller)
        second_movie.genres.add(thriller)

        first_entry = JournalEntry.objects.create(
            user=self.user,
            movie=first_movie,
            raw_text="A couple of things worked, but mostly no.",
            is_positive=True,
            liked_genre=True,
            liked_story=False,
            liked_performances=False,
            would_rewatch=False,
        )
        second_entry = JournalEntry.objects.create(
            user=self.user,
            movie=second_movie,
            raw_text="Same problem again.",
            is_positive=True,
            liked_genre=True,
            liked_story=False,
            liked_performances=False,
            would_rewatch=False,
        )

        self.assertEqual(first_entry.survey_score, 2)
        self.assertEqual(second_entry.survey_score, 2)
        self.assertEqual(_excluded_genre_ids([first_entry, second_entry]), {thriller.id})
