import uuid
from io import BytesIO

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
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


class S3Client:
    def __init__(self):
        scheme = "https" if settings.MINIO_USE_SSL else "http"
        self.client = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
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
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

    def ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

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

    def get_presigned_url(self, s3_key: str, expires: int = 3600) -> str:
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=expires,
        )

    def delete_file(self, s3_key: str):
        self.client.delete_object(Bucket=self.bucket, Key=s3_key)


def get_s3_client() -> S3Client:
    client = S3Client()
    client.ensure_bucket()
    return client
