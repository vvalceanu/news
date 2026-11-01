from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
]
