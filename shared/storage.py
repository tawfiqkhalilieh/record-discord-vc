"""Private S3 objects; JSON metadata is published only after the MP4 upload succeeds."""
import json
import os
import re
from pathlib import Path

import boto3
from botocore.config import Config

ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def validate_id(recording_id: str) -> str:
    if not ID_PATTERN.fullmatch(recording_id):
        raise ValueError("Invalid recording ID")
    return recording_id


class Storage:
    def __init__(self):
        self.bucket = os.environ.get("S3_BUCKET", "recordings")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
        )

    def check(self):
        self.client.head_bucket(Bucket=self.bucket)

    def publish(self, path: Path, metadata: dict):
        recording_id = validate_id(metadata["id"])
        key = f"recordings/{recording_id}/video.mp4"
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": "video/mp4"})
        data = {**metadata, "video_key": key, "size_bytes": path.stat().st_size}
        self.client.put_object(Bucket=self.bucket, Key=f"recordings/{recording_id}/metadata.json",
                               Body=json.dumps(data).encode(), ContentType="application/json")
        return data

    def get(self, recording_id: str):
        key = f"recordings/{validate_id(recording_id)}/metadata.json"
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        try:
            return json.loads(obj["Body"].read())
        finally:
            obj["Body"].close()

    def list(self):
        rows = []
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix="recordings/")
        for page in pages:
            for entry in page.get("Contents", []):
                match = re.fullmatch(r"recordings/([a-f0-9]{32})/metadata\.json", entry["Key"])
                if match:
                    rows.append(self.get(match[1]))
        return sorted(rows, key=lambda row: row["started_at"], reverse=True)
