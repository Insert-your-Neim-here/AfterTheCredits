from django.shortcuts import render

# Create your views here.
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.decorators.http import require_POST

from movies.models import Movie
from users.services.profile_embedding import update_user_profile_embedding
from .forms import JournalTextForm, JournalEditForm
from .models import JournalEntry
from .services import (
    SURVEY_STEPS, TOTAL_STEPS, TEXT_STEP, SESSION_KEY,
    get_step_data, save_answer_to_session, get_survey_from_session,
    clear_survey_session, save_entry_with_embedding,
    create_entry_from_session, get_user_journal_entries, get_entry_for_user,
)


# ── Journal list ───────────────────────────────────────────────────────────────

@login_required
def journal_list_view(request):
    entries = get_user_journal_entries(request.user)
    return render(request, "journal/journal_list.html", {"entries": entries})


# ── Multi-step survey ──────────────────────────────────────────────────────────

@login_required
def survey_start_view(request, tmdb_id):
    """Clear any stale session data and redirect to step 1."""
    movie = get_object_or_404(Movie, tmdb_id=tmdb_id)

    # If entry already exists, redirect to edit
    existing = JournalEntry.objects.filter(user=request.user, movie=movie).first()
    if existing:
        messages.info(request, "You already have a journal entry for this film. Edit it below.")
        return redirect("journal:edit", entry_id=existing.pk)

    clear_survey_session(request)
    # Store which movie we're journalling about
    request.session["journal_tmdb_id"] = tmdb_id
    request.session.modified = True
    return redirect("journal:survey_step", step=1)


@login_required
def survey_step_view(request, step):
    """
    Steps 1-5: yes/no survey questions.
    Step 6: free-text entry + save.
    """
    tmdb_id = request.session.get("journal_tmdb_id")
    if not tmdb_id:
        messages.error(request, "Please start your journal entry from a film page.")
        return redirect("journal:list")

    movie = get_object_or_404(Movie, tmdb_id=tmdb_id)

    # ── Steps 1–5: survey questions ────────────────────────────
    if 1 <= step <= TOTAL_STEPS:
        step_data = get_step_data(step)
        if not step_data:
            return redirect("journal:survey_step", step=1)

        if request.method == "POST":
            answer = request.POST.get("answer")
            if answer not in ("yes", "no"):
                return redirect("journal:survey_step", step=step)

            save_answer_to_session(request, step_data["key"], answer == "yes")
            next_step = step + 1
            return redirect("journal:survey_step", step=next_step)

        return render(request, "journal/survey_question.html", {
            "movie": movie,
            "step": step,
            "total_steps": TOTAL_STEPS,
            "question": step_data["question"],
            "progress": int((step / (TOTAL_STEPS + 1)) * 100),
        })

    # ── Step 6: free-text ──────────────────────────────────────
    if step == TEXT_STEP:
        # Guard: must have answered all survey steps
        survey = get_survey_from_session(request)
        for s in SURVEY_STEPS:
            if s["key"] not in survey:
                return redirect("journal:survey_step", step=1)

        if request.method == "POST":
            form = JournalTextForm(request.POST)
            if form.is_valid():
                entry = create_entry_from_session(request, movie)
                entry.raw_text = form.cleaned_data["raw_text"]
                save_entry_with_embedding(entry)
                clear_survey_session(request)
                request.session.pop("journal_tmdb_id", None)
                messages.success(request, f'Journal entry for "{movie.title}" saved.')
                return redirect("journal:list")
        else:
            form = JournalTextForm()

        return render(request, "journal/survey_text.html", {
            "movie": movie,
            "form": form,
            "step": step,
            "total_steps": TOTAL_STEPS,
            "progress": int((step / (TOTAL_STEPS + 1)) * 100),
        })

    # Out of range
    return redirect("journal:survey_step", step=1)


# ── Edit & Delete ──────────────────────────────────────────────────────────────

@login_required
def edit_entry_view(request, entry_id):
    entry = get_entry_for_user(entry_id, request.user)
    movie = entry.movie

    if request.method == "POST":
        form = JournalEditForm(request.POST, instance=entry)
        if form.is_valid():
            updated = form.save(commit=False)
            save_entry_with_embedding(updated)
            messages.success(request, f'Journal entry for "{movie.title if movie else "Unknown Movie"}" updated.')
            return redirect("journal:list")
    else:
        form = JournalEditForm(instance=entry)

    return render(request, "journal/entry_edit.html", {"form": form, "movie": movie, "entry": entry})


@login_required
def delete_entry_view(request, entry_id):
    entry = get_entry_for_user(entry_id, request.user)
    movie = entry.movie

    if request.method == "POST":
        entry.delete()
        update_user_profile_embedding(request.user)
        messages.success(request, f'Journal entry for "{movie.title if movie else "Unknown Movie"}" deleted.')
        return redirect("journal:list")

    return render(request, "journal/entry_confirm_delete.html", {"entry": entry, "movie": movie})
