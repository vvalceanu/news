import base64
import binascii
import secrets

from django.conf import settings
from django.http import HttpResponse


class StagingBasicAuthMiddleware:
    """Gate the whole site behind HTTP Basic Auth when BASIC_AUTH_USER and BASIC_AUTH_PASSWORD are set.

    A no-op when either is empty (production).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.user = settings.BASIC_AUTH_USER
        self.password = settings.BASIC_AUTH_PASSWORD
        self.enabled = bool(self.user and self.password)

    def __call__(self, request):
        if self.enabled and not self._authorized(request):
            response = HttpResponse("Authentication required.", status=401)
            response["WWW-Authenticate"] = 'Basic realm="staging"'
            return response
        return self.get_response(request)

    def _authorized(self, request) -> bool:
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return False
        try:
            user, _, password = base64.b64decode(encoded).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        try:
            user_ok = secrets.compare_digest(user, self.user)
            password_ok = secrets.compare_digest(password, self.password)
        except TypeError:  # non-ASCII input
            return False
        return user_ok and password_ok
