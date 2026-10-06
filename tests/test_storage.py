import pytest
from botocore.exceptions import ClientError


def test_failed_video_upload_does_not_publish_metadata(storage, tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    def fail(*args, **kwargs):
        raise RuntimeError("simulated connection failure")
    monkeypatch.setattr(storage.client, "upload_file", fail)
    with pytest.raises(RuntimeError):
        storage.publish(path, {"id": "c" * 32})
    assert storage.list() == []
    with pytest.raises(ClientError):
        storage.get("c" * 32)


def test_private_metadata_round_trip_and_pagination(storage, published, monkeypatch, tmp_path):
    path = tmp_path / "second.mp4"
    path.write_bytes(b"second")
    storage.publish(path, {**published, "id": "b" * 32, "started_at": "2026-10-07T00:00:00+00:00"})
    paginator = storage.client.get_paginator("list_objects_v2")
    original = paginator.paginate
    monkeypatch.setattr(paginator, "paginate", lambda **kwargs: original(**kwargs, PaginationConfig={"PageSize": 1}))
    monkeypatch.setattr(storage.client, "get_paginator", lambda name: paginator)
    assert [row["id"] for row in storage.list()] == ["b" * 32, "a" * 32]
    assert storage.get(published["id"])["video_key"] == f"recordings/{published['id']}/video.mp4"
    acl = storage.client.get_object_acl(Bucket=storage.bucket, Key=published["video_key"])
    assert all("URI" not in grant["Grantee"] for grant in acl["Grants"])


def test_bucket_initialization_creates_and_removes_public_policy(storage):
    import json
    from shared.init_bucket import initialize
    storage.client.delete_bucket(Bucket=storage.bucket)
    initialize()
    storage.check()
    storage.client.put_bucket_policy(Bucket=storage.bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::recordings/*"}]}))
    initialize()
    with pytest.raises(ClientError):
        storage.client.get_bucket_policy(Bucket=storage.bucket)
