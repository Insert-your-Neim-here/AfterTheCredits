
# Create your views here.
# users/views.py
from django.shortcuts import render, redirect
from django.contrib.auth import (
    get_user_model,
    login,
    logout,
    authenticate,
    update_session_auth_hash,
)
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.views.decorators.http import require_http_methods

from journal.services import get_user_journal_entries
from movies.models import Genre, StreamingPlatform
from recommendations.services import get_negative_genre_signal_ids

from .forms import LoginForm, ProfileForm, SignupForm, VerificationCodeForm
from .services.email_service import send_verification_email, verify_code

User = get_user_model()


@require_http_methods(['GET', 'POST'])
def signup_view(request):
    if request.user.is_authenticated:
        return redirect('core:home')

    form = SignupForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        password = form.cleaned_data['password']
        streaming_platform_ids = [
            platform.id for platform in form.cleaned_data['streaming_platforms']
        ]

        # Store credentials in session until email is verified
        request.session['pending_signup'] = {
            'email': email,
            'password': password,
            'streaming_platform_ids': streaming_platform_ids,
        }

        send_verification_email(email)
        messages.success(request, f'A verification code has been sent to {email}.')
        return redirect('users:verify_email')

    selected_streaming_platform_ids = []
    if request.method == 'POST':
        selected_streaming_platform_ids = request.POST.getlist('streaming_platforms')

    return render(request, 'users/signup.html', {
        'form': form,
        'selected_streaming_platform_ids': selected_streaming_platform_ids,
    })


@require_http_methods(['GET', 'POST'])
def verify_email_view(request):
    pending = request.session.get('pending_signup')
    if not pending:
        return redirect('users:signup')

    form = VerificationCodeForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        code = form.cleaned_data['code']
        email = pending['email']

        if verify_code(email, code):
            # Create and immediately log in the user
            user = User.objects.create_user(
                username=email,
                email=email,
                password=pending['password'],
                is_email_verified=True,
            )
            user.streaming_platforms.set(pending.get('streaming_platform_ids', []))
            del request.session['pending_signup']
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, 'Email verified! Welcome to After The Credits.')
            return redirect('core:home')
        else:
            form.add_error('code', 'Invalid or expired code. Please try again.')

    return render(request, 'users/verify_email.html', {'form': form, 'email': pending['email']})


@require_http_methods(['POST'])
def resend_code_view(request):
    pending = request.session.get('pending_signup')
    if not pending:
        return redirect('users:signup')

    send_verification_email(pending['email'])
    messages.success(request, 'A new code has been sent.')
    return redirect('users:verify_email')


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('core:home')

    form = LoginForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email'].lower()
        password = form.cleaned_data['password']
        user = authenticate(request, username=email, password=password)

        if user is not None:
            if not user.is_email_verified: # type: ignore
                # Re-send code and push them through verification
                request.session['pending_signup'] = {'email': email, 'password': password}
                send_verification_email(email)
                messages.info(request, 'Please verify your email first. We sent you a new code.')
                return redirect('users:verify_email')

            login(request, user)
            next_url = request.GET.get('next', 'core:home')
            return redirect(next_url)
        else:
            form.add_error(None, 'Invalid email or password.')

    return render(request, 'users/login.html', {'form': form})


@login_required
def logout_view(request):
    logout(request)
    return redirect('users:login')

@login_required
def profile_view(request):
    selected_streaming_platform_ids = [
        str(platform_id)
        for platform_id in request.user.streaming_platforms.values_list("id", flat=True)
    ]

    if request.method == "POST":
        if request.POST.get("account_action") == "change_password":
            form = ProfileForm(instance=request.user)
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password updated.")
                return redirect("users:profile")
        else:
            form = ProfileForm(request.POST, instance=request.user)
            password_form = PasswordChangeForm(request.user)
            selected_streaming_platform_ids = request.POST.getlist("streaming_platforms")
            if form.is_valid():
                form.save()
                # Regenerate recommendations now that platforms may have changed
                try:
                    from recommendations.services import generate_recommendations
                    generate_recommendations(request.user)
                except Exception:
                    pass
                messages.success(request, "Streaming services updated.")
                return redirect("users:profile")
    else:
        form = ProfileForm(instance=request.user)
        password_form = PasswordChangeForm(request.user)

    journal_entries = get_user_journal_entries(request.user)
    all_platforms   = StreamingPlatform.objects.all()

    # Stats
    total_entries  = journal_entries.count()
    positive_count = journal_entries.filter(is_positive=True).count()
    rewatch_count  = journal_entries.filter(would_rewatch=True).count()

    taste_entries = list(journal_entries.prefetch_related("movie__genres"))
    negative_genre_ids = get_negative_genre_signal_ids(taste_entries)

    # Favourite genres across journalled films, excluding genres with any
    # negative taste signal so they don't appear as favourites.
    top_genres = list(
        journal_entries
        .values("movie__genres__id", "movie__genres__name")
        .annotate(c=Count("movie__genres__id"))
        .exclude(movie__genres__id=None)
        .exclude(movie__genres__id__in=negative_genre_ids)
        .order_by("-c")[:5]
    )

    negative_genres = Genre.objects.filter(id__in=negative_genre_ids).order_by("name")

    max_count = max([row["c"] for row in top_genres] + [1])
    taste_genres = [
        {
            "name": row["movie__genres__name"],
            "count": row["c"],
            "width": round((row["c"] / max_count) * 100),
            "is_negative": False,
        }
        for row in top_genres
    ]
    taste_genres.extend(
        {
            "name": genre.name,
            "count": 0,
            "width": 100,
            "is_negative": True,
        }
        for genre in negative_genres
    )

    context = {
        "form":           form,
        "password_form":  password_form,
        "selected_streaming_platform_ids": selected_streaming_platform_ids,
        "all_platforms":  all_platforms,
        "total_entries":  total_entries,
        "positive_count": positive_count,
        "rewatch_count":  rewatch_count,
        "top_genres":     top_genres,
        "taste_genres":   taste_genres,
    }
    return render(request, "users/profile.html", context)
