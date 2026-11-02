import base64

from django.test import Client



def test_robots_disallows_admin(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /test-admin/" in response.content.decode()


def test_default_admin_path_is_not_served(client):
    assert client.get("/admin/").status_code == 404


def _staging_client(settings):
    settings.BASIC_AUTH_USER = "user"
    settings.BASIC_AUTH_PASSWORD = "pass"
    return Client()


def _auth(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


def test_basic_auth_blocks_without_credentials(settings):
    client = _staging_client(settings)
    response = client.get("/robots.txt")
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Basic")


def test_basic_auth_allows_correct_credentials(settings):
    client = _staging_client(settings)
    assert client.get("/robots.txt", **_auth("user", "pass")).status_code == 200


def test_basic_auth_rejects_wrong_and_non_ascii_credentials(settings):
    client = _staging_client(settings)
    assert client.get("/robots.txt", **_auth("user", "nope")).status_code == 401
    assert client.get("/robots.txt", **_auth("üser", "päss")).status_code == 401
