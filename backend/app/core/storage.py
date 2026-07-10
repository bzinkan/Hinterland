"""Azure Blob Storage signed-URL generation for photo upload.

Container Apps runs under the User-Assigned Managed Identity granted
Storage Blob Data Contributor on `hinterlandphotosdev` (provisioned in
Phase 5). User-delegation SAS URLs are the AAD-credentialed equivalent
of the old GCS V4 signed URLs.

The Protocol's `bucket` parameter name is preserved for back-compat
with the call sites that haven't been renamed yet; it maps to a Blob
container on the configured storage account.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol, cast

from fastapi import Depends, Request


class StorageCopyVerificationError(RuntimeError):
    """A destination blob was not proven identical to its source."""


@dataclass(frozen=True)
class StorageObjectProperties:
    byte_count: int
    content_type: str | None
    etag: str | None


class SignedUrlGenerator(Protocol):
    """Photo bucket facade: signed-PUT URL generation + server-side reads."""

    def generate_put_url(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        expires_in: timedelta,
    ) -> tuple[str, datetime]:
        """Return `(signed_url, expires_at)` for a single PUT.

        ``content_type`` is returned to the client as a required upload header;
        Azure SAS does not itself enforce that request header. Server-side
        canonicalization is the trust boundary.
        """
        ...

    def put_required_headers(self, *, content_type: str) -> dict[str, str]:
        """Headers the client MUST send on the PUT to the signed URL.

        Backend-specific: Azure Put Blob rejects requests without
        `x-ms-blob-type`. Returned to clients in the presign response so
        the upload contract is explicit instead of implied by whichever
        storage backend happens to be configured.
        """
        ...

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        """Read a photo's raw bytes server-side.

        Used by the iNat identify endpoint to feed the kid's photo into
        the CV call. Server-side download keeps the iNat token off the
        mobile binary and lets us cache responses by photo_id later.
        """
        ...

    def get_object_properties(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> StorageObjectProperties:
        """Return bounded-validation metadata or raise ``FileNotFoundError``."""
        ...

    def put_object_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> None:
        """Write canonical bytes and verify retry-safe destination integrity."""
        ...

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        """Synchronously copy and verify before the caller deletes source."""
        ...

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        """Delete an object. Paired with `copy_object` to implement a
        move (copy then delete) on the moderation hot path."""
        ...

    def generate_get_url(
        self,
        *,
        bucket: str,
        object_name: str,
        expires_in: timedelta,
    ) -> tuple[str, datetime]:
        """Return `(signed_url, expires_at)` for a single GET.

        Used by the review-queue UI to render quarantined photos for
        teachers, and by the My Observations list to render clean photos
        for kids.
        """
        ...


class BlobSignedUrlGenerator:
    """Azure Blob Storage impl using user-delegation SAS URLs.

    Container Apps managed identity must have Storage Blob Data
    Contributor on the storage account (granted in Phase 5).

    The `bucket` arg on every protocol method maps to a Blob container
    name on the configured account (single account per env, set via
    settings.blob_account_endpoint).
    """

    def __init__(self, account_endpoint: str) -> None:
        # Lazy imports so test collection doesn't require azure-storage-blob
        # when the storage_provider isn't "blob".
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self._account_endpoint = account_endpoint
        self._credential = DefaultAzureCredential()
        self._service = BlobServiceClient(account_url=account_endpoint, credential=self._credential)
        self._udk_cache: tuple[Any, datetime] | None = None

    def _user_delegation_key(self, lifetime: timedelta) -> Any:
        # Cached ~1h: SAS minting became hot once the gallery started
        # requesting a signed GET per photo per page (each key mint is an
        # AAD round-trip). The key is reused only while it still outlives
        # the requested SAS lifetime plus a clock-skew margin, so every
        # minted SAS expires before its signing key does.
        now = datetime.now(UTC)
        if self._udk_cache is not None:
            key, key_expiry = self._udk_cache
            if key_expiry - now > lifetime + timedelta(minutes=5):
                return key
        key_expiry = now + timedelta(hours=1)
        key = self._service.get_user_delegation_key(
            key_start_time=now,
            key_expiry_time=key_expiry,
        )
        self._udk_cache = (key, key_expiry)
        return key

    def generate_put_url(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        expires_in: timedelta,
    ) -> tuple[str, datetime]:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        udk = self._user_delegation_key(expires_in)
        account_name = self._account_endpoint.split("//")[1].split(".")[0]
        expires_at = datetime.now(UTC) + expires_in
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=bucket,
            blob_name=object_name,
            user_delegation_key=udk,
            permission=BlobSasPermissions(write=True, create=True),
            expiry=expires_at,
            content_type=content_type,
        )
        url = f"{self._account_endpoint.rstrip('/')}/{bucket}/{object_name}?{sas}"
        return url, expires_at

    def put_required_headers(self, *, content_type: str) -> dict[str, str]:
        # Azure Put Blob fails with 400 MissingRequiredHeader without
        # x-ms-blob-type. The SAS's content_type binding only shapes GET
        # responses (rsct), so Content-Type here is convention, not
        # enforcement.
        return {
            "Content-Type": content_type,
            "x-ms-blob-type": "BlockBlob",
        }

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        blob = self._service.get_blob_client(container=bucket, blob=object_name)
        return blob.download_blob().readall()

    def get_object_properties(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> StorageObjectProperties:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._service.get_blob_client(container=bucket, blob=object_name)
        try:
            properties = blob.get_blob_properties()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(object_name) from exc
        content_settings = getattr(properties, "content_settings", None)
        content_type = getattr(content_settings, "content_type", None)
        etag = getattr(properties, "etag", None)
        return StorageObjectProperties(
            byte_count=self._blob_size(properties),
            content_type=content_type if isinstance(content_type, str) else None,
            etag=str(etag) if etag is not None else None,
        )

    def put_object_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        blob = self._service.get_blob_client(container=bucket, blob=object_name)
        try:
            blob.upload_blob(
                data,
                overwrite=overwrite,
                content_settings=ContentSettings(content_type=content_type),
                metadata=metadata,
            )
        except ResourceExistsError as exc:
            if overwrite:
                raise
            existing = blob.download_blob().readall()
            digest = hashlib.sha256(existing).hexdigest()
            if expected_sha256 is None or digest != expected_sha256:
                raise StorageCopyVerificationError(
                    "existing canonical blob did not match the expected SHA-256"
                ) from exc
            return

        properties = blob.get_blob_properties()
        if self._blob_size(properties) != len(data):
            raise StorageCopyVerificationError(
                "canonical destination size did not match uploaded bytes"
            )
        if expected_sha256 is not None:
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected_sha256:
                raise StorageCopyVerificationError(
                    "canonical source bytes did not match expected SHA-256"
                )

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        # Put Blob From URL is synchronous. Do not use start_copy_from_url
        # here: it may return while the copy is still pending, and deleting
        # the source at that point can corrupt the moderation move.
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        udk = self._user_delegation_key(timedelta(minutes=5))
        account_name = self._account_endpoint.split("//")[1].split(".")[0]
        src_sas = generate_blob_sas(
            account_name=account_name,
            container_name=src_bucket,
            blob_name=src_object,
            user_delegation_key=udk,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(minutes=5),
        )
        src_url = f"{self._account_endpoint.rstrip('/')}/{src_bucket}/{src_object}?{src_sas}"
        src = self._service.get_blob_client(container=src_bucket, blob=src_object)
        dst = self._service.get_blob_client(container=dst_bucket, blob=dst_object)
        source_properties = src.get_blob_properties()
        source_size = self._blob_size(source_properties)
        if expected_size is not None and source_size != expected_size:
            raise StorageCopyVerificationError(
                f"source size {source_size} did not match expected size {expected_size}"
            )

        with suppress(ResourceExistsError):
            dst.upload_blob_from_url(src_url, overwrite=False)
        # A prior attempt may have copied successfully and crashed before the
        # database commit. The verification below is required in both cases.

        destination_properties = dst.get_blob_properties()
        destination_size = self._blob_size(destination_properties)
        required_size = expected_size if expected_size is not None else source_size
        if destination_size != required_size:
            raise StorageCopyVerificationError(
                f"destination size {destination_size} did not match expected size {required_size}"
            )

        required_sha256 = expected_sha256
        if required_sha256 is None:
            source_bytes = src.download_blob().readall()
            required_sha256 = hashlib.sha256(source_bytes).hexdigest()
        destination_bytes = dst.download_blob().readall()
        destination_sha256 = hashlib.sha256(destination_bytes).hexdigest()
        if destination_sha256 != required_sha256:
            raise StorageCopyVerificationError("destination SHA-256 verification failed")

    @staticmethod
    def _blob_size(properties: Any) -> int:
        size = getattr(properties, "size", None)
        if not isinstance(size, int):
            size = getattr(properties, "content_length", None)
        if not isinstance(size, int):
            raise StorageCopyVerificationError("Azure Blob properties omitted object size")
        return size

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        blob = self._service.get_blob_client(container=bucket, blob=object_name)
        blob.delete_blob()

    def generate_get_url(
        self,
        *,
        bucket: str,
        object_name: str,
        expires_in: timedelta,
    ) -> tuple[str, datetime]:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        udk = self._user_delegation_key(expires_in)
        account_name = self._account_endpoint.split("//")[1].split(".")[0]
        expires_at = datetime.now(UTC) + expires_in
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=bucket,
            blob_name=object_name,
            user_delegation_key=udk,
            permission=BlobSasPermissions(read=True),
            expiry=expires_at,
        )
        url = f"{self._account_endpoint.rstrip('/')}/{bucket}/{object_name}?{sas}"
        return url, expires_at


def _build_generator_for(settings: Any) -> SignedUrlGenerator:
    """Construct the Blob backend from settings.

    Phase 11b dropped the GCS fallback. If the call site doesn't supply
    a fake via app.state.signed_url_generator (which is what tests do),
    a working blob_account_endpoint is required.
    """
    endpoint = getattr(settings, "blob_account_endpoint", "")
    if not endpoint:
        raise RuntimeError(
            "BlobSignedUrlGenerator requires settings.blob_account_endpoint to be set "
            "(usually sourced from Key Vault secret `blob-account-endpoint` "
            "via the Container App UAMI)."
        )
    return BlobSignedUrlGenerator(endpoint)


def get_signed_url_generator(request: Request) -> SignedUrlGenerator:
    """Pull the generator off `app.state` so tests can inject a fake."""
    generator = getattr(request.app.state, "signed_url_generator", None)
    if generator is None:
        settings = getattr(request.app.state, "settings", None)
        if settings is None:
            raise RuntimeError(
                "get_signed_url_generator: app.state.settings is unset; "
                "cannot build the storage backend."
            )
        generator = _build_generator_for(settings)
        request.app.state.signed_url_generator = generator
    return cast(SignedUrlGenerator, generator)


SignedUrlGeneratorDep = Annotated[SignedUrlGenerator, Depends(get_signed_url_generator)]
