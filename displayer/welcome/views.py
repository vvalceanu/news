from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def index(request):
    return render(request, "welcome/index.html")


@require_GET
def robots_txt(request):
    lines = ["User-agent: *", f"Disallow: /{settings.ADMIN_URL}/"]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
