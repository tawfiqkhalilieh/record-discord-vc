import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(storage):
    from dashboard.app import app, attempts
    attempts.clear()
    with TestClient(app) as client:
        yield client


def login(client):
    response = client.get("/login")
    token = re.search(r'name="token" value="([^"]+)"', response.text).group(1)
    response = client.post("/login", data={"token": token, "password": "testing-password-123456"}, follow_redirects=False)
    assert response.status_code == 303
    return response


def test_all_recording_routes_require_admin(client, published):
    for path in ["/", f"/recordings/{published['id']}", f"/recordings/{published['id']}/video"]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    assert client.get("/api/recordings").status_code == 401


def test_login_listing_escape_and_logout(client, published):
    response = login(client)
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=strict' in response.headers['set-cookie'].lower()
    response = client.get("/")
    assert "&lt;script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert client.get("/api/recordings").json()[0]["id"] == published["id"]
    assert client.post("/logout", data={"token": "bad"}).status_code == 403
    token = re.search(r'name="token" value="([^"]+)"', response.text).group(1)
    assert client.post("/logout", data={"token": token}, follow_redirects=False).status_code == 303
    assert client.get("/api/recordings").status_code == 401


def test_login_csrf_and_rate_limit(client):
    assert client.post("/login", data={"password": "testing-password-123456", "token": "bad"}).status_code == 403
    page = client.get("/login")
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    for _ in range(5):
        assert client.post("/login", data={"password": "incorrect", "token": token}).status_code == 401
    assert client.post("/login", data={"password": "incorrect", "token": token}).status_code == 429


def test_playback_full_head_ranges_and_download(client, published):
    login(client)
    path = f"/recordings/{published['id']}/video"
    response = client.get(path)
    assert response.status_code == 200 and response.content == b"0123456789abcdef"
    assert response.headers["accept-ranges"] == "bytes"
    assert client.head(path).content == b""
    assert client.head(path).headers["content-length"] == "16"
    for header, expected in [("bytes=2-5", b"2345"), ("bytes=12-", b"cdef"), ("bytes=-3", b"def"), ("bytes=14-99", b"ef")]:
        response = client.get(path, headers={"Range": header})
        assert response.status_code == 206 and response.content == expected
        assert int(response.headers["content-length"]) == len(expected)
    for header in ["bytes=100-200", "bytes=-0", "bytes=4-2", "bytes=0-1,4-5", "bytes=-"]:
        response = client.get(path, headers={"Range": header})
        assert response.status_code == 416 and response.headers["content-range"] == "bytes */16"
    assert "attachment" in client.get(path + "?download=true").headers["content-disposition"]
    assert client.get("/recordings/invalid/video").status_code == 404
    assert client.get("/recordings/" + "b" * 32).status_code == 404


def test_tampered_cookie_rejected(client, published):
    login(client)
    cookie = client.cookies.get("stash_session")
    client.cookies.clear()
    client.cookies.set("stash_session", cookie + "tampered")
    assert client.get("/api/recordings").status_code == 401
