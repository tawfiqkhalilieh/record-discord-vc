import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def recorder_client(storage, tmp_path, monkeypatch):
    from capture.app import Recorder, app
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    recorder = Recorder()
    @asynccontextmanager
    async def lifespan(app):
        app.state.recorder = recorder
        yield
        if recorder.tasks:
            await asyncio.gather(*list(recorder.tasks))
    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    with TestClient(app) as client:
        yield client, recorder


AUTH = {"Authorization": "Bearer test-recorder-key"}


def test_internal_api_auth_and_target_guards(recorder_client):
    client, recorder = recorder_client
    assert client.get("/state").status_code == 401
    assert client.get("/state", headers=AUTH).json()["status"] == "idle"
    request = {"guild_id": "123", "channel_id": "456", "channel_name": "General", "requested_by": "789", "participants": []}
    assert client.post("/sessions", json=request, headers=AUTH).status_code == 409
    assert client.post("/end", json={"guild_id": "123", "channel_id": "456"}, headers=AUTH).status_code == 409
    recorder.active = {"id": "a" * 32, "status": "recording", "guild_id": "123", "channel_id": "456"}
    assert client.post("/end", json={"guild_id": "123", "channel_id": "999"}, headers=AUTH).status_code == 409
    assert client.get("/sessions/invalid", headers=AUTH).status_code == 404


def test_arm_checks_operator_browser_location(recorder_client):
    client, recorder = recorder_client
    async def front():
        pass
    page = SimpleNamespace(url="https://discord.com/channels/123/456", bring_to_front=front)
    recorder.context = SimpleNamespace(pages=[page])
    assert client.post("/arm", json={"guild_id": "123", "channel_id": "999"}, headers=AUTH).status_code == 409
    response = client.post("/arm", json={"guild_id": "123", "channel_id": "456"}, headers=AUTH)
    assert response.status_code == 200 and response.json()["status"] == "armed"


def test_demo_encodes_uploads_and_is_queryable(recorder_client, storage):
    client, recorder = recorder_client
    response = client.post("/demo", headers=AUTH)
    assert response.status_code == 202
    recording_id = response.json()["id"]
    # Synchronize with background task through the same application event loop.
    client.portal.call(asyncio.gather, *list(recorder.tasks))
    session = client.get(f"/sessions/{recording_id}", headers=AUTH).json()
    assert session["status"] == "complete"
    assert session["duration_seconds"] > 7
    assert storage.get(recording_id)["channel_name"] == "Grid demo"
    assert recorder.active is None
    assert client.post(f"/sessions/{recording_id}/retry", headers=AUTH).status_code == 409


def test_failed_job_retains_media_and_retry_releases_worker(recorder_client, monkeypatch):
    client, recorder = recorder_client
    session = {"id": "d" * 32, "status": "processing", "channel_name": "Retry test",
               "started_at": "2026-10-06T12:00:00+00:00", "participants": []}
    recorder.save(session)
    raw = recorder.folder(session["id"]) / "raw.mkv"
    raw.write_bytes(b"retained media")
    recorder.active = session
    def fail(_session):
        raise RuntimeError("simulated S3 outage")
    monkeypatch.setattr(recorder, "finish_sync", fail)
    client.portal.call(recorder.process_job, session)
    assert recorder.load(session["id"])["status"] == "failed"
    assert raw.read_bytes() == b"retained media" and recorder.active is None
    def recover(retry_session):
        return {**retry_session, "status": "complete"}
    monkeypatch.setattr(recorder, "finish_sync", recover)
    assert client.post(f"/sessions/{session['id']}/retry", headers=AUTH).status_code == 202
    client.portal.call(asyncio.gather, *list(recorder.tasks))
    assert recorder.load(session["id"])["status"] == "complete"
    assert "error" not in recorder.load(session["id"])
    assert recorder.active is None
