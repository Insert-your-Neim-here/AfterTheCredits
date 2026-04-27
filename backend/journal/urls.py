# journal/urls.py
from django.urls import path
from . import views
app_name = 'journal'
urlpatterns = [   
   path("", views.journal_list_view, name="list"),
 
    # Multi-step survey
    path("new/<int:tmdb_id>/", views.survey_start_view, name="create"),
    path("survey/step/<int:step>/", views.survey_step_view, name="survey_step"),
 
    # Edit / Delete
    path("<int:entry_id>/edit/", views.edit_entry_view, name="edit"),
    path("<int:entry_id>/delete/", views.delete_entry_view, name="delete"),]