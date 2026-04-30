from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.http import HttpResponseRedirect
from django.urls import reverse

from .services import (
    MIN_JOURNAL_ENTRIES,
    generate_recommendations,
    get_journal_entry_count,
    get_recommendations,
)


RUNTIME_CHOICES = [
    (90, "Up to 90 min"),
    (120, "Up to 2 hours"),
    (150, "Up to 2.5 hours"),
    (180, "Up to 3 hours"),
]


def _runtime_minutes(request):
    runtime = request.GET.get("runtime", "").strip()
    try:
        minutes = int(runtime)
    except ValueError:
        return None

    valid_minutes = {choice[0] for choice in RUNTIME_CHOICES}
    return minutes if minutes in valid_minutes else None


@login_required
def recommendations_view(request):
    runtime_minutes = _runtime_minutes(request)
    recs = get_recommendations(request.user, runtime_minutes=runtime_minutes)
    journal_count = get_journal_entry_count(request.user)
    can_generate = journal_count >= MIN_JOURNAL_ENTRIES

    unfiltered_has_recs = bool(recs)

    context = {
        "recs": recs,
        "has_recs": bool(recs),
        "unfiltered_has_recs": unfiltered_has_recs,
        "journal_count": journal_count,
        "min_journal_entries": MIN_JOURNAL_ENTRIES,
        "entries_needed": max(0, MIN_JOURNAL_ENTRIES - journal_count),
        "can_generate": can_generate,
        "runtime_choices": RUNTIME_CHOICES,
        "runtime_minutes": runtime_minutes,
    }
    return render(request, "recommendations/recommendations.html", context)


@login_required
@require_POST
def refresh_recommendations_view(request):
    generate_recommendations(request.user)
    return HttpResponseRedirect(reverse("recommendations:list"))
