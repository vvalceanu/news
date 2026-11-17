from django.urls import path

from . import views

app_name = "articles"

urlpatterns = [
    path("", views.index, name="index"),
    path("articles/<int:article_id>/", views.detail, name="detail"),
    path("articles/<int:article_id>/deep-dive/", views.request_deep_dive, name="deep_dive"),
    path("articles/<int:article_id>/words/<slug:level>/<int:index>/ask/", views.ask_word, name="ask_word"),
    path("pending/", views.pending, name="pending"),
]
