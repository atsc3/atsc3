"""Bundle delivery: store a fulfilled order's artifacts and return a location.

The default (:class:`LocalDeliverer`) writes to a directory for dev/tests; the
AWS path (:class:`S3Deliverer`) uploads to S3 and returns a presigned GET URL.
``boto3`` is imported lazily, so the package stays dependency-light.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Protocol, runtime_checkable

#: The artifact a presigned URL points at (always present in a bundle).
PRIMARY_ARTIFACT = "certs.pem"


@runtime_checkable
class Deliverer(Protocol):
    """Stores a named set of files and returns a fetchable location."""

    def deliver(self, name: str, files: Mapping[str, bytes], *,
                expires_in: int) -> str:
        ...


class LocalDeliverer:
    """Write the bundle to ``<root>/<name>/`` and return its path (dev/tests)."""

    def __init__(self, root) -> None:
        self.root = Path(root)

    def deliver(self, name: str, files: Mapping[str, bytes], *,
                expires_in: int) -> str:
        out = self.root / name
        out.mkdir(parents=True, exist_ok=True)
        for filename, data in files.items():
            (out / filename).write_bytes(data)
        return str(out)


class S3Deliverer:
    """Upload the bundle to S3 and return a presigned GET URL for the bundle.

    ``client`` is any object exposing boto3's ``put_object`` and
    ``generate_presigned_url``; injected so the backend is testable without AWS.
    """

    def __init__(self, bucket: str, *, region: Optional[str] = None, client=None,
                 prefix: str = "") -> None:
        self.bucket = bucket
        self.prefix = prefix
        if client is None:
            import boto3
            client = boto3.client("s3", region_name=region)
        self.client = client

    def deliver(self, name: str, files: Mapping[str, bytes], *,
                expires_in: int) -> str:
        base = f"{self.prefix}{name}"
        for filename, data in files.items():
            self.client.put_object(Bucket=self.bucket, Key=f"{base}/{filename}",
                                   Body=data)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": f"{base}/{PRIMARY_ARTIFACT}"},
            ExpiresIn=expires_in)


def default_deliverer() -> Deliverer:
    """The deliverer selected by settings: S3 when a bucket is configured."""
    from django.conf import settings

    bucket = getattr(settings, "OPENATSC3_CA_S3_BUCKET", "")
    if bucket:
        return S3Deliverer(bucket, region=getattr(settings, "OPENATSC3_CA_S3_REGION", "") or None)
    return LocalDeliverer(getattr(settings, "OPENATSC3_CA_DELIVERY_DIR", "deliveries"))
