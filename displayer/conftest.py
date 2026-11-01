import pytest


@pytest.fixture(autouse=True)
def _force_flags_off(settings):
    settings.DEBUG = False
    settings.SECURE_SSL_REDIRECT = False
