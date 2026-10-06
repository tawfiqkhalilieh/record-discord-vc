import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("SESSION_SECRET", "test-session-secret-that-is-at-least-thirty-two-characters")


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_ENDPOINT", "https://s3.amazonaws.com")
    monkeypatch.setenv("S3_BUCKET", "recordings")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("ADMIN_PASSWORD", "testing-password-123456")
    monkeypatch.setenv("RECORDER_API_KEY", "test-recorder-key")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="recordings")
        from shared.storage import Storage
        yield Storage()


@pytest.fixture
def published(storage, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"0123456789abcdef")
    return storage.publish(path, {"id": "a" * 32, "channel_name": "Call <script>alert(1)</script>",
        "guild_id": "123", "channel_id": "456", "started_at": "2026-10-06T12:00:00+00:00",
        "participants": ["Alex", "Sam"], "duration_seconds": 8, "status": "complete"})
