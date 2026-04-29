# recommendations/urls.py
from django.urls import path
from .views import recommendations_view, refresh_recommendations_view
app_name = 'recommendations'
urlpatterns = [
    path("recommendations/", recommendations_view, name="list"),
    path("recommendations/refresh/", refresh_recommendations_view, name="refresh"), 
]