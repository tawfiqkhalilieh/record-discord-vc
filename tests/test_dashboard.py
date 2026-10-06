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


@pytest.fixture
def live_worker(client):
    import httpx
    from dashboard.app import app
    state = {'id': 'e' * 32, 'status': 'recording', 'channel_name': 'Live call',
             'participants': ['Alex'], 'started_at': '2026-10-06T12:00:00+00:00', 'requested_by': 'private'}
    requests = []
    def handle(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer test-recorder-key'
        if request.url.path == '/state':
            return httpx.Response(200, json=state)
        if state['status'] != 'recording':
            return httpx.Response(404)
        if request.url.path.endswith('/index.m3u8'):
            return httpx.Response(200, text='#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\nindex0.m4s\n')
        return httpx.Response(200, content=b'live media')
    original = app.state.recorder_http
    app.state.recorder_http = httpx.AsyncClient(base_url='http://capture:8000',
        headers={'Authorization': 'Bearer test-recorder-key'}, transport=httpx.MockTransport(handle))
    yield state, requests
    client.portal.call(app.state.recorder_http.aclose)
    app.state.recorder_http = original


def test_live_dashboard_and_obs_session_access(client, live_worker):
    from urllib.parse import urlsplit, parse_qs
    state, requests = live_worker
    recording_id = state['id']
    assert client.get('/api/live').status_code == 401
    assert client.get(f'/live/{recording_id}/index.m3u8').status_code == 401
    login(client)
    assert 'OBS Browser Source URL' in client.get('/').text
    result = client.get('/api/live').json()
    assert result['channel_name'] == 'Live call' and 'requested_by' not in result
    obs = urlsplit(result['obs_url'])
    token = parse_qs(obs.query)['token'][0]
    assert client.get(result['playlist_url']).status_code == 200
    client.cookies.clear()
    assert client.get(obs.path + '?' + obs.query).status_code == 200
    playlist = client.get(f'/live/{recording_id}/index.m3u8?token={token}')
    assert playlist.status_code == 200
    assert f'URI="init.mp4?token={token}"' in playlist.text
    assert f'index0.m4s?token={token}' in playlist.text
    assert playlist.headers['cache-control'] == 'private, no-store'
    assert client.get(f'/live/{recording_id}/index0.m4s?token={token}').content == b'live media'
    assert client.get(f'/live/{recording_id}/state?token={token}').json()['status'] == 'recording'
    assert client.get(f'/live/{recording_id}/raw.mkv?token={token}').status_code == 404
    assert client.get(f'/live/{recording_id}/index.m3u8?token={token}bad').status_code == 401
    assert client.get('/live/' + 'f' * 32 + f'/index.m3u8?token={token}').status_code == 401
    assert client.get('/api/recordings').status_code == 401
    state['status'] = 'processing'
    assert client.get(obs.path + '?' + obs.query).status_code == 404
    assert client.get(f'/live/{recording_id}/index.m3u8?token={token}').status_code == 404
    state['id'] = 'f' * 32
    state['status'] = 'recording'
    assert client.get(f'/live/{recording_id}/state?token={token}').json()['status'] == 'idle'


def test_live_worker_outage_is_reported(client, live_worker):
    import httpx
    from dashboard.app import app
    login(client)
    def offline(request):
        raise httpx.ConnectError('offline', request=request)
    app.state.recorder_http._transport = httpx.MockTransport(offline)
    response = client.get('/api/live')
    assert response.status_code == 503 and response.json()['detail'] == 'Capture worker unavailable'
