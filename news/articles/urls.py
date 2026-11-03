from django.urls import path

from . import views

app_name = "articles"

urlpatterns = [
    path("", views.index, name="index"),
    path("articles/<int:article_id>/", views.detail, name="detail"),
    path("articles/<int:article_id>/deep-dive/", views.request_deep_dive, name="deep_dive"),
    path("pending/", views.pending, name="pending"),
]
