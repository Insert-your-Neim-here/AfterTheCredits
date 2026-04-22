
# Create your views here.
# users/views.py
from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model, login, logout, authenticate
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods

from .forms import SignupForm, LoginForm, VerificationCodeForm
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

        # Store credentials in session until email is verified
        request.session['pending_signup'] = {'email': email, 'password': password}

        send_verification_email(email)
        messages.success(request, f'A verification code has been sent to {email}.')
        return redirect('users:verify_email')

    return render(request, 'users/signup.html', {'form': form})


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