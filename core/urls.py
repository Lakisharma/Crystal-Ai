from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('dashboard/', views.dashboard_router_view, name='dashboard'),
    path('about/', views.about_view, name='about'),
]
