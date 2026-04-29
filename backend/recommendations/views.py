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


@login_required
def recommendations_view(request):
    recs = get_recommendations(request.user)
    journal_count = get_journal_entry_count(request.user)

    context = {
        "recs": recs,
        "has_recs": recs.exists(),
        "journal_count": journal_count,
        "min_journal_entries": MIN_JOURNAL_ENTRIES,
        "entries_needed": max(0, MIN_JOURNAL_ENTRIES - journal_count),
        "can_generate": journal_count >= MIN_JOURNAL_ENTRIES,
    }
    return render(request, "recommendations/recommendations.html", context)


@login_required
@require_POST
def refresh_recommendations_view(request):
    generate_recommendations(request.user)
    return HttpResponseRedirect(reverse("recommendations:list"))
