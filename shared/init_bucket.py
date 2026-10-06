"""Wait for S3, idempotently create the private bucket, remove anonymous access policy."""
import logging
import time

from botocore.exceptions import BotoCoreError, ClientError

from shared.storage import Storage


def initialize():
    storage = Storage()
    for attempt in range(60):
        try:
            storage.client.list_buckets()
            break
        except (BotoCoreError, ClientError):
            if attempt == 59:
                raise
            time.sleep(2)
    try:
        storage.check()
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
            raise
        storage.client.create_bucket(Bucket=storage.bucket)
    # S3 buckets default to private. Removing any prior public policy also makes
    # repeat starts restore the local deployment's intended access model.
    storage.client.delete_bucket_policy(Bucket=storage.bucket)
    logging.warning("Private recordings bucket is ready")


if __name__ == "__main__":
    initialize()
