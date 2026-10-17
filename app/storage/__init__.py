"""Synchronous blob backends; database bytes remain the default."""

import logging
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.config import settings

logger = logging.getLogger(__name__)


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def signed_url(self, key: str, ttl: int) -> str | None: ...


class LocalBlobStorage:
    def __init__(self, directory: str):
        self.root = Path(directory).resolve()

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not key or path == self.root or not path.is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex)
        temporary.write_bytes(data)
        temporary.replace(path)

    def get_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def signed_url(self, key: str, ttl: int) -> str | None:
        return None


class S3BlobStorage:
    def __init__(self):
        import boto3

        self.client = boto3.client("s3", region_name=settings.AWS_REGION or None)
        self.bucket = settings.AWS_S3_BUCKET
        if not self.bucket:
            raise ValueError("S3 bucket is not configured")

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as error:
            if str(error.response["Error"]["Code"]) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def signed_url(self, key: str, ttl: int) -> str | None:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl
        )


def backend(name: str | None = None) -> BlobStorage | None:
    name = name or settings.STORAGE_BACKEND
    if name == "database":
        return None
    if name == "local":
        return LocalBlobStorage(settings.STORAGE_LOCAL_DIR)
    if name == "s3":
        return S3BlobStorage()
    raise ValueError("Unknown storage backend")


def read_blob(key: str | None, legacy: bytes | None) -> bytes | None:
    if not key:
        return legacy
    try:
        name, relative = key.split(":", 1)
        store = backend(name)
        return store.get_bytes(relative) if store else legacy
    except Exception:
        if legacy is not None:
            return legacy
        raise


def store_blob(data: bytes, content_type: str) -> str | None:
    try:
        store = backend()
        if store is None:
            return None
        key = uuid4().hex
        store.put(key, data, content_type)
        return f"{settings.STORAGE_BACKEND}:{key}"
    except Exception:
        logger.warning("storage.write_unavailable_using_database")
        return None
