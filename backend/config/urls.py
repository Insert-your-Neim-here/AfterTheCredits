from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls", namespace="core")),
    path("auth/", include("users.urls", namespace="users")),
    path("movies/", include("movies.urls", namespace="movies")),
    path("journal/", include("journal.urls", namespace="journal")),
    path("recommendations/", include("recommendations.urls", namespace="recommendations")),
]
