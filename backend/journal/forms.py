from django import forms

from .models import JournalEntry


class JournalTextForm(forms.ModelForm):
    """Step 6 free-text journal entry."""

    class Meta:
        model = JournalEntry
        fields = ["raw_text"]
        widgets = {
            "raw_text": forms.Textarea(
                attrs={
                    "rows": 7,
                    "placeholder": "What do you think about the movie?",
                }
            ),
        }
        labels = {"raw_text": ""}


class JournalEditForm(forms.ModelForm):
    """Used on the edit page for all survey fields and text."""

    BOOL_CHOICES = [("true", "Yes"), ("false", "No")]

    is_positive = forms.ChoiceField(
        choices=BOOL_CHOICES,
        widget=forms.RadioSelect,
        label="Did you enjoy the movie overall?",
    )
    liked_genre = forms.ChoiceField(
        choices=BOOL_CHOICES,
        widget=forms.RadioSelect,
        label="Did you like the genre?",
    )
    liked_story = forms.ChoiceField(
        choices=BOOL_CHOICES,
        widget=forms.RadioSelect,
        label="Did you like the story / writing?",
    )
    liked_performances = forms.ChoiceField(
        choices=BOOL_CHOICES,
        widget=forms.RadioSelect,
        label="Did you enjoy the performances?",
    )
    would_rewatch = forms.ChoiceField(
        choices=BOOL_CHOICES,
        widget=forms.RadioSelect,
        label="Would you watch it again?",
    )

    class Meta:
        model = JournalEntry
        fields = [
            "is_positive",
            "liked_genre",
            "liked_story",
            "liked_performances",
            "would_rewatch",
            "raw_text",
        ]
        widgets = {
            "raw_text": forms.Textarea(attrs={"rows": 6}),
        }
        labels = {"raw_text": "Your thoughts"}

    def _clean_bool(self, field):
        value = self.cleaned_data.get(field)
        if value is None:
            return None
        return value == "true"

    def clean_is_positive(self):
        return self._clean_bool("is_positive")

    def clean_liked_genre(self):
        return self._clean_bool("liked_genre")

    def clean_liked_story(self):
        return self._clean_bool("liked_story")

    def clean_liked_performances(self):
        return self._clean_bool("liked_performances")

    def clean_would_rewatch(self):
        return self._clean_bool("would_rewatch")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in [
            "is_positive",
            "liked_genre",
            "liked_story",
            "liked_performances",
            "would_rewatch",
        ]:
            value = getattr(self.instance, field, None)
            if value is not None:
                self.initial[field] = "true" if value else "false"
