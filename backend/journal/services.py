from core.services.embedding_service import compute_embedding, get_embedding_model
from .models import JournalEntry

# ── Survey step definitions ────────────────────────────────────────────────────
SURVEY_STEPS = [
    {"key": "is_positive",        "question": "Did you enjoy the movie overall?",      "step": 1},
    {"key": "liked_genre",        "question": "Did you like the genre?",               "step": 2},
    {"key": "liked_story",        "question": "Did you enjoy the story and writing?",  "step": 3},
    {"key": "liked_performances", "question": "Did you enjoy the performances?",       "step": 4},
    {"key": "would_rewatch",      "question": "Would you watch it again?",             "step": 5},
]

TOTAL_STEPS = len(SURVEY_STEPS)   # 5 survey questions
TEXT_STEP   = TOTAL_STEPS + 1     # step 6 = free-text
SESSION_KEY = "journal_survey"


def get_step_data(step: int) -> dict | None:
    if 1 <= step <= TOTAL_STEPS:
        return SURVEY_STEPS[step - 1]
    return None


def save_answer_to_session(request, key: str, value: bool):
    survey = request.session.get(SESSION_KEY, {})
    survey[key] = value
    request.session[SESSION_KEY] = survey
    request.session.modified = True


def get_survey_from_session(request) -> dict:
    return request.session.get(SESSION_KEY, {})


def clear_survey_session(request):
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True


def save_entry_with_embedding(entry: JournalEntry) -> JournalEntry:
    get_embedding_model()
    embedding = compute_embedding(entry.raw_text)
    entry.embedding = embedding
    entry.save()
    return entry


def create_entry_from_session(request, movie) -> JournalEntry:
    survey = get_survey_from_session(request)
    entry, _ = JournalEntry.objects.get_or_create(user=request.user, movie=movie)
    entry.is_positive        = survey.get("is_positive")
    entry.liked_genre        = survey.get("liked_genre")
    entry.liked_story        = survey.get("liked_story")
    entry.liked_performances = survey.get("liked_performances")
    entry.would_rewatch      = survey.get("would_rewatch")
    return entry


def get_user_journal_entries(user):
    return (
        JournalEntry.objects.filter(user=user)
        .select_related("movie")
        .order_by("-created_at")
    )


def get_entry_for_user(entry_id: int, user):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(JournalEntry, id=entry_id, user=user)