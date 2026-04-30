# users/urls.py
from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('signup/', views.signup_view, name='signup'),
    path('verify/', views.verify_email_view, name='verify_email'),
    path('verify/resend/', views.resend_code_view, name='resend_code'),
    path('login/', views.login_view, name='login'),
    path('password/forgot/', views.forgot_password_view, name='forgot_password'),
    path('password/verify/', views.password_reset_verify_view, name='password_reset_verify'),
    path('password/resend/', views.resend_password_reset_code_view, name='resend_password_reset_code'),
    path('password/reset/', views.password_reset_confirm_view, name='password_reset_confirm'),
    path('profile/', views.profile_view, name='profile'),
    path('logout/', views.logout_view, name='logout'),
]
