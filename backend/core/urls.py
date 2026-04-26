# core/urls.py  (stub — we'll fill this in Step 2)
from django.urls import path

from . import views
app_name = 'core'
urlpatterns = [
    path('', views.home_view, name='home'),
]
