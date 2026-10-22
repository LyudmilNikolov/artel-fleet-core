"""Download links use the public address and its own scheme; the internal MinIO connection stays plain http."""

import pytest

from app.config import settings
from app.utils.s3 import S3Client


@pytest.mark.parametrize(
    ("internal_ssl", "external_ssl", "expected_internal", "expected_external"),
    [
        (False, None, "http://minio:9000", "http://files.example.com"),
        (True, None, "https://minio:9000", "https://files.example.com"),
        (False, True, "http://minio:9000", "https://files.example.com"),
    ],
)
def test_internal_and_public_schemes(monkeypatch, internal_ssl, external_ssl, expected_internal, expected_external):
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "MINIO_EXTERNAL_ENDPOINT", "files.example.com")
    monkeypatch.setattr(settings, "MINIO_USE_SSL", internal_ssl)
    monkeypatch.setattr(settings, "MINIO_EXTERNAL_USE_SSL", external_ssl)
    client = S3Client()
    assert client.client.meta.endpoint_url == expected_internal
    assert client._presign_client.meta.endpoint_url == expected_external


@pytest.mark.asyncio
async def test_health_reports_the_release_version(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_VERSION", "v9.9.9")
    response = await client.get("/api/v1/health")
    assert response.status_code == 200 and response.json() == {"status": "ok", "version": "v9.9.9"}
