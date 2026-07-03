"""Azure Blob Storage signed-URL generation for photo upload.

Container Apps runs under the User-Assigned Managed Identity granted
Storage Blob Data Contributor on `dragonflyphotosdev` (provisioned in
Phase 5). User-delegation SAS URLs are the AAD-credentialed equivalent
of the old GCS V4 signed URLs.

The Protocol's `bucket` parameter name is preserved for back-compat
with the call sites that haven't been renamed yet; it maps to a Blob
container on the configured storage account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol, cast

from fastapi import Depends, Request


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

        The URL is bound to `content_type` -- the client MUST PUT with a
        matching `Content-Type` header or the upload will be rejected.
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

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
    ) -> None:
        """Server-side copy. Used by the moderation worker to move
        `pending/<id>.jpg` to `observations/<id>.jpg` or
        `quarantine/<id>.jpg` per ADR 0009 + `docs/moderation.md`."""
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

    def _user_delegation_key(self, lifetime: timedelta) -> Any:
        # Issue a fresh user-delegation key for each SAS mint. Cheap to
        # mint (one AAD call) and pinning per-request avoids cross-request
        # lifetime leaks. Later we can cache for 50min if SAS minting
        # becomes hot.
        start = datetime.now(UTC)
        return self._service.get_user_delegation_key(
            key_start_time=start,
            key_expiry_time=start + lifetime + timedelta(minutes=5),
        )

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

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
    ) -> None:
        # In-account copy: pass the source blob URL with a short-lived read
        # SAS so the dst container can pull it server-side.
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
        dst = self._service.get_blob_client(container=dst_bucket, blob=dst_object)
        dst.start_copy_from_url(src_url)

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
