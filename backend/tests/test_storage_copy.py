from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.storage import BlobSignedUrlGenerator, StorageCopyVerificationError


def _generator(
    *,
    source: bytes = b"jpeg",
    destination: bytes = b"jpeg",
) -> tuple[BlobSignedUrlGenerator, MagicMock]:
    src = MagicMock()
    src.get_blob_properties.return_value = SimpleNamespace(size=len(source))
    dst = MagicMock()
    dst.get_blob_properties.return_value = SimpleNamespace(size=len(destination))
    dst.download_blob.return_value.readall.return_value = destination
    service = MagicMock()
    service.get_blob_client.side_effect = [src, dst]

    generator = BlobSignedUrlGenerator.__new__(BlobSignedUrlGenerator)
    generator._account_endpoint = "https://account.blob.core.windows.net"  # type: ignore[attr-defined]
    generator._service = service  # type: ignore[attr-defined]
    generator._user_delegation_key = MagicMock(return_value=object())  # type: ignore[method-assign]
    return generator, dst


def test_copy_is_synchronous_and_verifies_destination_sha256() -> None:
    generator, destination = _generator()
    with patch("azure.storage.blob.generate_blob_sas", return_value="read-sas"):
        generator.copy_object(
            src_bucket="photos",
            src_object="pending/photo.jpg",
            dst_bucket="photos",
            dst_object="observations/photo.jpg",
            expected_size=4,
            expected_sha256=hashlib.sha256(b"jpeg").hexdigest(),
        )

    destination.upload_blob_from_url.assert_called_once()
    assert (
        not hasattr(destination, "start_copy_from_url")
        or not destination.start_copy_from_url.called
    )


def test_copy_sha_mismatch_fails_before_caller_can_delete_source() -> None:
    generator, _ = _generator(destination=b"evil")
    with (
        patch("azure.storage.blob.generate_blob_sas", return_value="read-sas"),
        pytest.raises(StorageCopyVerificationError, match="SHA-256"),
    ):
        generator.copy_object(
            src_bucket="photos",
            src_object="pending/photo.jpg",
            dst_bucket="photos",
            dst_object="observations/photo.jpg",
            expected_size=4,
            expected_sha256=hashlib.sha256(b"jpeg").hexdigest(),
        )
