from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from ..config import Settings
from .common import IntegrationConfigurationError, IntegrationResponseError


MAX_MEMBERSHIP_DOCUMENT_BYTES = 8 * 1024 * 1024
ALLOWED_MEMBERSHIP_DOCUMENT_TYPES = (
    "image/jpeg",
    "image/png",
    "application/pdf",
)
DOCUMENT_KEY_PREFIX = "membership-documents/"
PENDING_DOCUMENT_KEY_PREFIX = f"{DOCUMENT_KEY_PREFIX}pending/"
VERIFIED_DOCUMENT_KEY_PREFIX = f"{DOCUMENT_KEY_PREFIX}verified/"
SHA256_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}


class S3CompatibleClient(Protocol):
    def generate_presigned_url(
        self,
        client_method: str,
        Params: Mapping[str, Any],
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str: ...

    def head_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def copy_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def delete_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def delete_objects(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class R2StorageSettings:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    put_expiry_seconds: int = 300
    get_expiry_seconds: int = 120
    max_document_bytes: int = MAX_MEMBERSHIP_DOCUMENT_BYTES

    def validate(self) -> None:
        if not self.endpoint_url.startswith("https://"):
            raise IntegrationConfigurationError(
                "Cloudflare R2 endpoint must use HTTPS"
            )
        if not self.access_key_id or not self.secret_access_key:
            raise IntegrationConfigurationError(
                "Cloudflare R2 access credentials are required"
            )
        if not self.bucket.strip():
            raise IntegrationConfigurationError(
                "Cloudflare R2 bucket is required"
            )
        if not 60 <= self.put_expiry_seconds <= 300:
            raise IntegrationConfigurationError(
                "R2 PUT presigned URL expiry is invalid"
            )
        if not 30 <= self.get_expiry_seconds <= 120:
            raise IntegrationConfigurationError(
                "R2 GET presigned URL expiry is invalid"
            )
        if not 1 <= self.max_document_bytes <= MAX_MEMBERSHIP_DOCUMENT_BYTES:
            raise IntegrationConfigurationError(
                "Membership document limit cannot exceed 8 MB"
            )


@dataclass(frozen=True)
class DocumentUploadTicket:
    object_key: str
    upload_url: str
    expires_in_seconds: int
    required_headers: Mapping[str, str]
    max_bytes: int


@dataclass(frozen=True)
class DocumentHead:
    object_key: str
    content_type: str
    content_length: int
    sha256: Optional[str]
    etag: Optional[str]


class R2DocumentStorage:
    """Private R2 storage with short-lived direct-upload URLs."""

    def __init__(
        self,
        settings: R2StorageSettings,
        client: Optional[S3CompatibleClient] = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.client = client or self._build_client(settings)

    def create_upload_ticket(
        self,
        *,
        content_type: str,
        content_length: int,
        sha256: Optional[str] = None,
    ) -> DocumentUploadTicket:
        normalized_type = content_type.strip().lower()
        self._validate_document(
            normalized_type,
            content_length,
            sha256,
        )
        random_id = uuid.uuid4().hex
        extension = CONTENT_TYPE_EXTENSIONS[normalized_type]
        object_key = (
            f"{PENDING_DOCUMENT_KEY_PREFIX}"
            f"{random_id[:2]}/{random_id}{extension}"
        )
        params: Dict[str, Any] = {
            "Bucket": self.settings.bucket,
            "Key": object_key,
            "ContentType": normalized_type,
            "ContentLength": content_length,
        }
        required_headers: Dict[str, str] = {
            "Content-Type": normalized_type,
        }
        if sha256:
            normalized_sha256 = sha256.lower()
            params["Metadata"] = {"sha256": normalized_sha256}
            required_headers["x-amz-meta-sha256"] = normalized_sha256
        upload_url = self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=self.settings.put_expiry_seconds,
            HttpMethod="PUT",
        )
        return DocumentUploadTicket(
            object_key=object_key,
            upload_url=upload_url,
            expires_in_seconds=self.settings.put_expiry_seconds,
            required_headers=required_headers,
            max_bytes=self.settings.max_document_bytes,
        )

    def create_download_url(self, object_key: str) -> str:
        self._validate_object_key(object_key)
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.settings.bucket,
                "Key": object_key,
                "ResponseContentDisposition": "attachment",
            },
            ExpiresIn=self.settings.get_expiry_seconds,
            HttpMethod="GET",
        )

    async def confirm_upload(
        self,
        *,
        object_key: str,
        expected_content_type: str,
        expected_content_length: int,
        expected_sha256: Optional[str] = None,
    ) -> DocumentHead:
        self._validate_object_key(object_key)
        normalized_type = expected_content_type.strip().lower()
        self._validate_document(
            normalized_type,
            expected_content_length,
            expected_sha256,
        )
        try:
            response = await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.settings.bucket,
                Key=object_key,
            )
        except Exception as exc:
            raise IntegrationResponseError(
                "Membership document upload could not be confirmed"
            ) from exc
        actual_type = str(response.get("ContentType", "")).lower()
        actual_length = int(response.get("ContentLength", -1))
        metadata = response.get("Metadata") or {}
        actual_sha256 = (
            str(metadata.get("sha256", "")).lower()
            if isinstance(metadata, Mapping)
            else ""
        )
        if actual_type != normalized_type:
            raise IntegrationResponseError(
                "Uploaded membership document Content-Type did not match"
            )
        if actual_length != expected_content_length:
            raise IntegrationResponseError(
                "Uploaded membership document size did not match"
            )
        normalized_sha256 = (
            expected_sha256.lower() if expected_sha256 else None
        )
        if normalized_sha256 and actual_sha256 != normalized_sha256:
            raise IntegrationResponseError(
                "Uploaded membership document checksum did not match"
            )
        try:
            content = await asyncio.to_thread(
                self._read_object,
                object_key,
            )
        except IntegrationResponseError:
            raise
        except Exception as exc:
            raise IntegrationResponseError(
                "Membership document content could not be verified"
            ) from exc
        if len(content) != expected_content_length:
            raise IntegrationResponseError(
                "Uploaded membership document body size did not match"
            )
        if self._detected_content_type(content) != normalized_type:
            raise IntegrationResponseError(
                "Uploaded membership document file signature did not match"
            )
        computed_sha256 = hashlib.sha256(content).hexdigest()
        if normalized_sha256 and not hmac.compare_digest(
            computed_sha256,
            normalized_sha256,
        ):
            raise IntegrationResponseError(
                "Uploaded membership document body checksum did not match"
            )
        raw_etag = str(response.get("ETag", "")).strip()
        etag = raw_etag.strip('"') or None
        if etag is None:
            raise IntegrationResponseError(
                "Uploaded membership document did not include an ETag"
            )
        verified_object_key = self._verified_object_key(object_key)
        try:
            await asyncio.to_thread(
                self._promote_verified_object,
                object_key,
                verified_object_key,
                raw_etag,
            )
        except IntegrationResponseError:
            raise
        except Exception as exc:
            raise IntegrationResponseError(
                "Membership document could not be finalized"
            ) from exc
        return DocumentHead(
            object_key=verified_object_key,
            content_type=actual_type,
            content_length=actual_length,
            sha256=computed_sha256,
            etag=etag,
        )

    async def delete_document(self, object_key: str) -> None:
        self._validate_object_key(object_key)
        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.settings.bucket,
                Key=object_key,
            )
        except Exception as exc:
            raise IntegrationResponseError(
                "Membership document could not be deleted"
            ) from exc

    async def delete_documents(self, object_keys: Sequence[str]) -> int:
        keys = list(dict.fromkeys(object_keys))
        for key in keys:
            self._validate_object_key(key)
        if not keys:
            return 0
        deleted = 0
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            try:
                response = await asyncio.to_thread(
                    self.client.delete_objects,
                    Bucket=self.settings.bucket,
                    Delete={
                        "Objects": [{"Key": key} for key in batch],
                        "Quiet": True,
                    },
                )
            except Exception as exc:
                raise IntegrationResponseError(
                    "Membership documents could not be deleted"
                ) from exc
            errors = response.get("Errors", [])
            if errors:
                raise IntegrationResponseError(
                    "Some membership documents could not be deleted"
                )
            deleted += len(batch)
        return deleted

    def _validate_document(
        self,
        content_type: str,
        content_length: int,
        sha256: Optional[str],
    ) -> None:
        if content_type not in ALLOWED_MEMBERSHIP_DOCUMENT_TYPES:
            raise ValueError(
                "Membership documents must be JPEG, PNG, or PDF"
            )
        if not 1 <= content_length <= self.settings.max_document_bytes:
            raise ValueError(
                "Membership document must be between 1 byte and 8 MB"
            )
        if sha256 and not SHA256_HEX_PATTERN.fullmatch(sha256):
            raise ValueError(
                "Membership document SHA-256 must be 64 hexadecimal characters"
            )

    def _read_object(self, object_key: str) -> bytes:
        response = self.client.get_object(
            Bucket=self.settings.bucket,
            Key=object_key,
        )
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise IntegrationResponseError(
                "Membership document response did not contain a body"
            )
        try:
            content = body.read(self.settings.max_document_bytes + 1)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(content, bytes):
            raise IntegrationResponseError(
                "Membership document body was not binary"
            )
        if len(content) > self.settings.max_document_bytes:
            raise IntegrationResponseError(
                "Membership document exceeded the configured size limit"
            )
        return content

    def _promote_verified_object(
        self,
        source_key: str,
        verified_key: str,
        etag: str,
    ) -> None:
        self.client.copy_object(
            Bucket=self.settings.bucket,
            Key=verified_key,
            CopySource={"Bucket": self.settings.bucket, "Key": source_key},
            CopySourceIfMatch=etag,
            MetadataDirective="COPY",
        )
        try:
            self.client.delete_object(
                Bucket=self.settings.bucket,
                Key=source_key,
            )
        except Exception:
            try:
                self.client.delete_object(
                    Bucket=self.settings.bucket,
                    Key=verified_key,
                )
            except Exception:
                pass
            raise

    @staticmethod
    def _verified_object_key(object_key: str) -> str:
        suffix = object_key.removeprefix(DOCUMENT_KEY_PREFIX)
        if suffix.startswith("verified/"):
            raise IntegrationResponseError(
                "Membership document was already finalized"
            )
        if suffix.startswith("pending/"):
            suffix = suffix.removeprefix("pending/")
        return f"{VERIFIED_DOCUMENT_KEY_PREFIX}{suffix}"

    @staticmethod
    def _detected_content_type(content: bytes) -> Optional[str]:
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"%PDF-"):
            return "application/pdf"
        return None

    @staticmethod
    def _validate_object_key(object_key: str) -> None:
        if (
            not object_key.startswith(DOCUMENT_KEY_PREFIX)
            or ".." in object_key
            or "\\" in object_key
            or object_key.endswith("/")
        ):
            raise ValueError("Invalid membership document object key")

    @staticmethod
    def _build_client(
        settings: R2StorageSettings,
    ) -> S3CompatibleClient:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise IntegrationConfigurationError(
                "Install boto3 to use Cloudflare R2 document storage"
            ) from exc
        return boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )


def r2_document_storage_from_settings(
    settings: Settings,
    client: Optional[S3CompatibleClient] = None,
) -> R2DocumentStorage:
    return R2DocumentStorage(
        R2StorageSettings(
            endpoint_url=settings.cloudflare_r2_resolved_endpoint_url,
            access_key_id=settings.cloudflare_r2_access_key_id,
            secret_access_key=settings.cloudflare_r2_secret_access_key,
            bucket=settings.cloudflare_r2_bucket,
            put_expiry_seconds=settings.r2_presigned_put_seconds,
            get_expiry_seconds=settings.r2_presigned_get_seconds,
            max_document_bytes=settings.membership_document_max_bytes,
        ),
        client=client,
    )
