from __future__ import annotations

import math
from typing import TYPE_CHECKING

from journal.models import JournalEntry

if TYPE_CHECKING:
    from users.models import User


def build_profile_embedding(entries: list[JournalEntry]) -> list[float] | None:
    """
    Build a normalized, survey-weighted average of journal-entry embeddings.
    """
    weighted_sum: list[float] | None = None
    total_weight = 0

    for entry in entries:
        if entry.embedding is None:
            continue

        vector = list(entry.embedding)
        weight = max(1, entry.survey_score or 0)

        if weighted_sum is None:
            weighted_sum = [0.0] * len(vector)

        for index, value in enumerate(vector):
            weighted_sum[index] += float(value) * weight
        total_weight += weight

    if weighted_sum is None or total_weight == 0:
        return None

    averaged = [value / total_weight for value in weighted_sum]
    magnitude = math.sqrt(sum(value * value for value in averaged))
    if magnitude == 0:
        return None

    return [value / magnitude for value in averaged]


def update_user_profile_embedding(user: "User") -> list[float] | None:
    """
    Recompute and persist the user's current profile embedding.
    """
    entries = list(JournalEntry.objects.filter(user=user, movie__isnull=False))
    profile_embedding = build_profile_embedding(entries)

    user.profile_embedding = profile_embedding
    user.save(update_fields=["profile_embedding"])

    return profile_embedding
