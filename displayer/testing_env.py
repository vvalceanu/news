"""Test-only env values.

Loaded as a pytest plugin (`-p testing_env` in pytest.ini) so the values are set before
pytest-django imports Django settings, which happens before conftest.py is loaded.
"""

import os

os.environ["DJANGO_SECRET_KEY"] = "test-only-secret-key-not-for-production"
os.environ["DJANGO_DEBUG"] = ""
os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver"
os.environ["DJANGO_ADMIN_URL"] = "test-admin"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["BASIC_AUTH_USER"] = ""
os.environ["BASIC_AUTH_PASSWORD"] = ""
