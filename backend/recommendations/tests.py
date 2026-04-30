from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from journal.models import JournalEntry
from movies.models import Genre, Movie, MovieCredit, Person, StreamingPlatform
from recommendations.models import Recommendation
from recommendations.services import (
    MAIN_ACTOR_LIMIT,
    MIN_JOURNAL_ENTRIES,
    _build_explanation,
    _credit_person_ids,
    _excluded_genre_ids,
    get_recommendations,
)


TEST_EMBEDDING = [0.1] * 384
POSITIVE_AXIS_EMBEDDING = [1.0] + [0.0] * 383
NEGATIVE_AXIS_EMBEDDING = [-1.0] + [0.0] * 383


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
            embedding=TEST_EMBEDDING,
            is_positive=True,
        )

    def test_cached_recommendations_hidden_until_three_entries(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 1):
            self._add_entry(tmdb_id)

        self.assertEqual(get_recommendations(self.user), [])
        self.assertEqual(Recommendation.objects.count(), 0)

    def test_cached_recommendations_are_not_reused_after_three_entries(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        recs = get_recommendations(self.user)

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].explanation, "Similar feel to films you've enjoyed.")
        self.assertEqual(Recommendation.objects.count(), 0)

    def test_recommendation_links_use_tmdb_id_not_database_id(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        self.client.force_login(self.user)
        rec = Recommendation(
            user=self.user,
            movie=self.movie,
            score=0.9,
            explanation="Fresh pick",
        )
        with patch("recommendations.views.get_recommendations", return_value=[rec]):
            response = self.client.get(reverse("recommendations:list"))

        self.assertContains(response, reverse("movies:details", args=[self.movie.tmdb_id]))
        self.assertContains(response, reverse("journal:create", args=[self.movie.tmdb_id]))
        self.assertContains(
            response,
            reverse("movies:toggle_wishlist", args=[self.movie.tmdb_id]),
        )
        self.assertNotContains(response, reverse("movies:details", args=[self.movie.id]))

    def test_runtime_selection_prioritizes_matches_without_removing_options(self):
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
        recs = [
            Recommendation(
                user=self.user,
                movie=short_movie,
                score=0.8,
                explanation="Short enough",
            ),
            Recommendation(
                user=self.user,
                movie=long_movie,
                score=0.7,
                explanation="Too long",
            ),
            Recommendation(
                user=self.user,
                movie=self.movie,
                score=0.6,
                explanation="No runtime",
            ),
        ]
        with patch("recommendations.views.get_recommendations", return_value=recs) as mock_get:
            response = self.client.get(reverse("recommendations:list"), {"runtime": "90"})

        self.assertContains(response, "Short Pick")
        self.assertContains(response, "Long Pick")
        self.assertContains(response, "Candidate")
        mock_get.assert_called_once_with(self.user, runtime_minutes=90)

        titles = [rec.movie.title for rec in response.context["recs"]]
        self.assertEqual(titles, ["Short Pick", "Long Pick", "Candidate"])

    def test_runtime_selection_generates_three_matching_movies_when_available(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        for index in range(3):
            movie = Movie.objects.create(
                tmdb_id=9200 + index,
                title=f"Long Candidate {index}",
                runtime=130 + index,
            )
            movie.embedding = [0.1] * 384
            movie.save(update_fields=["embedding"])

        short_movies = []
        for index in range(3):
            movie = Movie.objects.create(
                tmdb_id=9300 + index,
                title=f"Short Candidate {index}",
                runtime=85 + index,
            )
            movie.embedding = [0.1] * 384
            movie.save(update_fields=["embedding"])
            short_movies.append(movie)

        recs = get_recommendations(self.user, runtime_minutes=90)

        self.assertEqual(len(recs), 3)
        self.assertEqual(
            {rec.movie.title for rec in recs},
            {movie.title for movie in short_movies},
        )
        self.assertTrue(all(rec.movie.runtime <= 90 for rec in recs))

    def test_recommendations_page_does_not_show_refresh_action(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            self._add_entry(tmdb_id)

        self.client.force_login(self.user)
        rec = Recommendation(
            user=self.user,
            movie=self.movie,
            score=0.9,
            explanation="Fresh pick",
        )
        with patch("recommendations.views.get_recommendations", return_value=[rec]):
            response = self.client.get(reverse("recommendations:list"))

        self.assertNotContains(response, "REFRESH")
        self.assertContains(response, "WATCH SESSION")

    def test_recommendations_recompute_stale_profile_embedding(self):
        self.user.profile_embedding = NEGATIVE_AXIS_EMBEDDING
        self.user.save(update_fields=["profile_embedding"])
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"Seen {tmdb_id}")
            JournalEntry.objects.create(
                user=self.user,
                movie=movie,
                raw_text="A logged film.",
                embedding=POSITIVE_AXIS_EMBEDDING,
                is_positive=True,
            )

        positive_candidate = Movie.objects.create(
            tmdb_id=9401,
            title="Fresh Profile Pick",
        )
        positive_candidate.embedding = POSITIVE_AXIS_EMBEDDING
        positive_candidate.save(update_fields=["embedding"])
        stale_candidate = Movie.objects.create(
            tmdb_id=9402,
            title="Stale Profile Pick",
        )
        stale_candidate.embedding = NEGATIVE_AXIS_EMBEDDING
        stale_candidate.save(update_fields=["embedding"])

        recs = get_recommendations(self.user)

        self.assertEqual(recs[0].movie, positive_candidate)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_embedding, POSITIVE_AXIS_EMBEDDING)

    def test_platform_fallback_happens_when_platform_candidates_do_not_score(self):
        platform = StreamingPlatform.objects.create(name="Chosen Streamer")
        self.user.streaming_platforms.add(platform)
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"Seen {tmdb_id}")
            JournalEntry.objects.create(
                user=self.user,
                movie=movie,
                raw_text="A logged film.",
                embedding=POSITIVE_AXIS_EMBEDDING,
                is_positive=True,
            )

        weak_platform_movie = Movie.objects.create(
            tmdb_id=9501,
            title="Weak Platform Pick",
        )
        weak_platform_movie.embedding = NEGATIVE_AXIS_EMBEDDING
        weak_platform_movie.save(update_fields=["embedding"])
        weak_platform_movie.streaming_platforms.add(platform)

        fallback_movie = Movie.objects.create(tmdb_id=9502, title="Fallback Pick")
        fallback_movie.embedding = POSITIVE_AXIS_EMBEDDING
        fallback_movie.save(update_fields=["embedding"])

        recs = get_recommendations(self.user)

        self.assertIn(fallback_movie, [rec.movie for rec in recs])
        self.assertNotIn(weak_platform_movie, [rec.movie for rec in recs])

    def test_runtime_candidate_fetch_backfills_when_runtime_pool_scores_poorly(self):
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"Seen {tmdb_id}")
            JournalEntry.objects.create(
                user=self.user,
                movie=movie,
                raw_text="A logged film.",
                embedding=POSITIVE_AXIS_EMBEDDING,
                is_positive=True,
            )

        for index in range(60):
            movie = Movie.objects.create(
                tmdb_id=9600 + index,
                title=f"Weak Short Candidate {index}",
                runtime=80,
            )
            movie.embedding = NEGATIVE_AXIS_EMBEDDING
            movie.save(update_fields=["embedding"])

        strong_long_movie = Movie.objects.create(
            tmdb_id=9701,
            title="Strong Long Candidate",
            runtime=130,
        )
        strong_long_movie.embedding = POSITIVE_AXIS_EMBEDDING
        strong_long_movie.save(update_fields=["embedding"])

        recs = get_recommendations(self.user, runtime_minutes=90)

        self.assertIn(strong_long_movie, [rec.movie for rec in recs])

    def test_recommendation_scores_are_capped_for_display(self):
        genre = Genre.objects.create(tmdb_id=18, name="Drama")
        for tmdb_id in range(2, MIN_JOURNAL_ENTRIES + 2):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"Seen {tmdb_id}")
            movie.genres.add(genre)
            JournalEntry.objects.create(
                user=self.user,
                movie=movie,
                raw_text="A logged film.",
                embedding=POSITIVE_AXIS_EMBEDDING,
                is_positive=True,
                liked_genre=True,
                would_rewatch=True,
            )

        candidate = Movie.objects.create(tmdb_id=9801, title="Boosted Candidate")
        candidate.embedding = POSITIVE_AXIS_EMBEDDING
        candidate.save(update_fields=["embedding"])
        candidate.genres.add(genre)

        recs = get_recommendations(self.user)

        self.assertLessEqual(recs[0].score, 1.0)

    def test_explanation_snippet_falls_back_when_genres_do_not_overlap(self):
        seen_genre = Genre.objects.create(tmdb_id=99, name="Seen Genre")
        candidate_genre = Genre.objects.create(tmdb_id=100, name="Candidate Genre")
        seen_movie = Movie.objects.create(tmdb_id=9901, title="Seen")
        seen_movie.genres.add(seen_genre)
        candidate = Movie.objects.create(tmdb_id=9902, title="Candidate")
        candidate.genres.add(candidate_genre)
        entry = JournalEntry.objects.create(
            user=self.user,
            movie=seen_movie,
            raw_text="This journal text should still be available.",
            embedding=TEST_EMBEDDING,
            is_positive=True,
        )

        _, snippet = _build_explanation(candidate, set(), {entry.movie_id: entry})

        self.assertEqual(snippet, entry.raw_text)


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
            embedding=TEST_EMBEDDING,
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
            embedding=TEST_EMBEDDING,
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
            embedding=TEST_EMBEDDING,
            is_positive=True,
            liked_genre=False,
            liked_story=True,
            liked_performances=True,
            would_rewatch=True,
        )

        self.assertEqual(_excluded_genre_ids([first_entry, second_entry]), {comedy.id})

    def test_three_no_answers_count_as_negative_genre_signal_when_genre_was_not_liked(self):
        thriller = Genre.objects.create(tmdb_id=53, name="Thriller")
        first_movie = Movie.objects.create(tmdb_id=201, title="Mostly Disliked Thriller")
        second_movie = Movie.objects.create(tmdb_id=202, title="Another Bad Thriller")
        first_movie.genres.add(thriller)
        second_movie.genres.add(thriller)

        first_entry = JournalEntry.objects.create(
            user=self.user,
            movie=first_movie,
            raw_text="A couple of things worked, but mostly no.",
            embedding=TEST_EMBEDDING,
            is_positive=True,
            liked_genre=False,
            liked_story=False,
            liked_performances=True,
            would_rewatch=False,
        )
        second_entry = JournalEntry.objects.create(
            user=self.user,
            movie=second_movie,
            raw_text="Same problem again.",
            embedding=TEST_EMBEDDING,
            is_positive=True,
            liked_genre=False,
            liked_story=False,
            liked_performances=True,
            would_rewatch=False,
        )

        self.assertEqual(first_entry.survey_score, 2)
        self.assertEqual(second_entry.survey_score, 2)
        self.assertEqual(_excluded_genre_ids([first_entry, second_entry]), {thriller.id})

    def test_overall_dislike_does_not_exclude_genre_when_genre_was_liked(self):
        thriller = Genre.objects.create(tmdb_id=53, name="Thriller")
        first_movie = Movie.objects.create(tmdb_id=201, title="Mixed Thriller")
        second_movie = Movie.objects.create(tmdb_id=202, title="Another Mixed Thriller")
        first_movie.genres.add(thriller)
        second_movie.genres.add(thriller)

        first_entry = JournalEntry.objects.create(
            user=self.user,
            movie=first_movie,
            raw_text="Bad film, good genre.",
            embedding=TEST_EMBEDDING,
            is_positive=False,
            liked_genre=True,
            liked_story=False,
            liked_performances=False,
            would_rewatch=False,
        )
        second_entry = JournalEntry.objects.create(
            user=self.user,
            movie=second_movie,
            raw_text="Still like the genre, not this movie.",
            embedding=TEST_EMBEDDING,
            is_positive=False,
            liked_genre=True,
            liked_story=False,
            liked_performances=False,
            would_rewatch=False,
        )

        self.assertEqual(_excluded_genre_ids([first_entry, second_entry]), set())
