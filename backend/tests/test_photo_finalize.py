from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from app.observation.photo_finalize import (
    MAX_UPLOAD_BYTES,
    PhotoUploadMissing,
    PhotoValidationError,
    finalize_uploaded_photo,
)


@dataclass(frozen=True)
class _Properties:
    byte_count: int
    content_type: str | None = "image/jpeg"
    etag: str | None = "etag"


class _Storage:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.writes: list[dict[str, object]] = []
        self.missing = False

    def get_object_properties(self, *, bucket: str, object_name: str) -> _Properties:
        if self.missing:
            raise FileNotFoundError(object_name)
        return _Properties(len(self.raw))

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return self.raw

    def put_object_bytes(self, **kwargs: object) -> None:
        self.writes.append(kwargs)

    # Protocol methods unused by this service.
    def generate_put_url(self, **_: object) -> tuple[str, datetime]:
        return "https://upload", datetime.now(UTC) + timedelta(minutes=1)

    def copy_object(self, **_: object) -> None:  # pragma: no cover
        raise NotImplementedError

    def delete_object(self, **_: object) -> None:  # pragma: no cover
        raise NotImplementedError

    def generate_get_url(self, **_: object) -> tuple[str, datetime]:  # pragma: no cover
        raise NotImplementedError


class _ChangingStorage(_Storage):
    def __init__(self, raw: bytes) -> None:
        super().__init__(raw)
        self.property_reads = 0

    def get_object_properties(self, *, bucket: str, object_name: str) -> _Properties:
        self.property_reads += 1
        return _Properties(len(self.raw), etag=f"etag-{self.property_reads}")


def _jpeg(width: int = 320, height: int = 240) -> bytes:
    output = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "test-metadata"
    Image.new("RGB", (width, height), (20, 120, 70)).save(
        output,
        format="JPEG",
        quality=95,
        exif=exif,
    )
    return output.getvalue()


async def test_finalizes_canonical_metadata_free_jpeg() -> None:
    storage = _Storage(_jpeg())
    result = await finalize_uploaded_photo(
        storage,
        bucket="photos",
        raw_object_name="pending/uploads/photo.jpg",
        photo_id="01J0PHOTOID00000000000ULID",
    )

    assert result.object_name == "pending/finalized/01J0PHOTOID00000000000ULID.jpg"
    assert (result.width_px, result.height_px) == (320, 240)
    assert result.byte_count == len(result.image_bytes)
    assert len(result.sha256) == 64
    assert b"test-metadata" not in result.image_bytes
    assert storage.writes[0]["expected_sha256"] == result.sha256


@pytest.mark.parametrize("raw", [b"", b"not-a-jpeg", b"\xff\xd8broken\xff\xd9"])
async def test_rejects_invalid_jpeg(raw: bytes) -> None:
    with pytest.raises(PhotoValidationError):
        await finalize_uploaded_photo(
            _Storage(raw),
            bucket="photos",
            raw_object_name="pending/uploads/photo.jpg",
            photo_id="01J0PHOTOID00000000000ULID",
        )


async def test_rejects_oversized_blob_before_download() -> None:
    storage = _Storage(b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(PhotoValidationError, match="larger than 4 MB"):
        await finalize_uploaded_photo(
            storage,
            bucket="photos",
            raw_object_name="pending/uploads/photo.jpg",
            photo_id="01J0PHOTOID00000000000ULID",
        )


async def test_rejects_dimensions_outside_w1_bounds() -> None:
    with pytest.raises(PhotoValidationError, match="dimensions"):
        await finalize_uploaded_photo(
            _Storage(_jpeg(width=49, height=100)),
            bucket="photos",
            raw_object_name="pending/uploads/photo.jpg",
            photo_id="01J0PHOTOID00000000000ULID",
        )


async def test_missing_upload_has_distinct_error() -> None:
    storage = _Storage(_jpeg())
    storage.missing = True
    with pytest.raises(PhotoUploadMissing):
        await finalize_uploaded_photo(
            storage,
            bucket="photos",
            raw_object_name="pending/uploads/photo.jpg",
            photo_id="01J0PHOTOID00000000000ULID",
        )


async def test_rejects_blob_replaced_during_verification() -> None:
    with pytest.raises(PhotoValidationError, match="changed during verification"):
        await finalize_uploaded_photo(
            _ChangingStorage(_jpeg()),
            bucket="photos",
            raw_object_name="pending/uploads/photo.jpg",
            photo_id="01J0PHOTOID00000000000ULID",
        )
