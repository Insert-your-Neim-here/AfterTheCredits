from django.shortcuts import render

# Create your views here.
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.http import HttpResponseRedirect
from django.urls import reverse

from .services import generate_recommendations, get_recommendations


@login_required
def recommendations_view(request):
    recs = get_recommendations(request.user)

    context = {
        "recs": recs,
        "has_recs": recs.exists(),
    }
    return render(request, "recommendations/recommendations.html", context)


@login_required
@require_POST
def refresh_recommendations_view(request):
    generate_recommendations(request.user)
    return HttpResponseRedirect(reverse("recommendations:list"))