import logging
import uuid
from io import BytesIO
from urllib.parse import quote

import boto3
from botocore.client import Config

from app.config import settings

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}


# Newer botocore adds checksum headers that Cloudflare R2 and other S3-compatible stores reject: only when required
_BOTO_CONFIG = Config(
    signature_version="s3v4",
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)
_bucket_ready = False


class S3Client:
    def __init__(self):
        scheme = "https" if settings.MINIO_USE_SSL else "http"
        self.client = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=_BOTO_CONFIG,
            region_name=settings.S3_REGION,
        )
        self.bucket = settings.MINIO_BUCKET

        # Separate client for browser-facing presigned URLs.
        # Uses external endpoint (e.g. localhost:9000) so URLs are
        # accessible from outside Docker.
        ext = settings.MINIO_EXTERNAL_ENDPOINT or settings.MINIO_ENDPOINT
        external_ssl = (
            settings.MINIO_USE_SSL if settings.MINIO_EXTERNAL_USE_SSL is None else settings.MINIO_EXTERNAL_USE_SSL
        )
        external_scheme = "https" if external_ssl else "http"
        self._presign_client = boto3.client(
            "s3",
            endpoint_url=f"{external_scheme}://{ext}",
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=_BOTO_CONFIG,
            region_name=settings.S3_REGION,
        )

    def ensure_bucket(self):
        """Create the bucket when it is missing. Never fails the caller: a key that may not create buckets
        (managed storage) is fine as long as the bucket already exists."""
        global _bucket_ready
        if _bucket_ready:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception:
                logger.warning("S3 bucket %s is not reachable or cannot be created with this key", self.bucket)
                return
        _bucket_ready = True

    def upload_file(self, file_data: bytes, filename: str, mime_type: str, folder: str = "uploads") -> str:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        s3_key = f"{folder}/{uuid.uuid4().hex}.{ext}" if ext else f"{folder}/{uuid.uuid4().hex}"
        self.client.upload_fileobj(
            BytesIO(file_data),
            self.bucket,
            s3_key,
            ExtraArgs={"ContentType": mime_type},
        )
        return s3_key

    def get_presigned_url(
        self, s3_key: str, expires: int = 3600, filename: str | None = None, mime_type: str | None = None
    ) -> str:
        params = {"Bucket": self.bucket, "Key": s3_key}
        if mime_type is not None and mime_type not in INLINE_TYPES:
            # Office files are downloaded, never rendered in the browser
            name = quote(filename or "download")
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{name}"
        return self._presign_client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)

    def delete_file(self, s3_key: str):
        self.client.delete_object(Bucket=self.bucket, Key=s3_key)


def get_s3_client() -> S3Client:
    client = S3Client()
    client.ensure_bucket()
    return client
