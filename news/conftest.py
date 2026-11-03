import pytest


@pytest.fixture(autouse=True)
def _force_flags_off(settings):
    settings.DEBUG = False
    settings.RATELIMIT_ENABLE = True  # off when DEBUG is on, but always tested
    settings.SECURE_SSL_REDIRECT = False
    # The manifest storage needs `collectstatic` output, which tests don't have.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
