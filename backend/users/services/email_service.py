# users/services.py
import random
import string
from django.core.mail import send_mail
from django.conf import settings
from django.core.cache import cache


VERIFICATION_CODE_LENGTH = 6
VERIFICATION_CODE_TTL = 600  # 10 minutes


def generate_verification_code() -> str:
    return ''.join(random.choices(string.digits, k=VERIFICATION_CODE_LENGTH))


def send_verification_email(email: str) -> str:
    """Generate a code, cache it, send it, and return it."""
    code = generate_verification_code()
    cache_key = _cache_key(email)
    cache.set(cache_key, code, timeout=VERIFICATION_CODE_TTL)

    send_mail(
        subject='Your After The Credits verification code',
        message=f'Your verification code is: {code}\n\nIt expires in 10 minutes.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
    return code


def verify_code(email: str, code: str) -> bool:
    """Return True and delete the code if it matches; False otherwise."""
    cache_key = _cache_key(email)
    stored = cache.get(cache_key)
    if stored and stored == code:
        cache.delete(cache_key)
        return True
    return False


def _cache_key(email: str) -> str:
    return f'email_verify:{email}'