from django.db import models
# Create your models here.

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    email = models.EmailField(unique=True)
    is_email_verified = models.BooleanField(default=False)
    profile_embedding = models.JSONField(null=True, blank=True)
    
    # Streaming platforms the user has (UK)
    streaming_platforms = models.ManyToManyField(
        'movies.StreamingPlatform',
        blank=True,
        related_name='users'
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email

