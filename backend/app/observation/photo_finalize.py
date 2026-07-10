"""Bounded JPEG verification and canonicalization for observation attach."""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, datetime

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.storage import SignedUrlGenerator

MAX_UPLOAD_BYTES = 4_000_000
MIN_IMAGE_EDGE = 50
MAX_IMAGE_EDGE = 1_600

# The accepted W1 image is at most 2.56M pixels. A slightly larger decoding
# ceiling leaves room for orientation before the explicit dimension check while
# still rejecting decompression bombs early.
Image.MAX_IMAGE_PIXELS = 4_000_000


class PhotoValidationError(ValueError):
    """The uploaded object cannot become an observation photo."""


class PhotoUploadMissing(PhotoValidationError):
    """The reserved object has not arrived in Blob Storage."""


@dataclass(frozen=True)
class CanonicalPhoto:
    raw_object_name: str
    object_name: str
    image_bytes: bytes
    byte_count: int
    width_px: int
    height_px: int
    sha256: str
    verified_at: datetime


async def finalize_uploaded_photo(
    storage: SignedUrlGenerator,
    *,
    bucket: str,
    raw_object_name: str,
    photo_id: str,
) -> CanonicalPhoto:
    """Verify a reserved Blob and write immutable, metadata-free JPEG bytes."""
    try:
        properties = await asyncio.to_thread(
            storage.get_object_properties,
            bucket=bucket,
            object_name=raw_object_name,
        )
    except FileNotFoundError as exc:
        raise PhotoUploadMissing("The photo upload has not finished") from exc

    if properties.byte_count <= 0:
        raise PhotoValidationError("The uploaded photo is empty")
    if properties.byte_count > MAX_UPLOAD_BYTES:
        raise PhotoValidationError("The uploaded photo is larger than 4 MB")

    raw = await asyncio.to_thread(
        storage.fetch_object_bytes,
        bucket=bucket,
        object_name=raw_object_name,
    )
    try:
        current_properties = await asyncio.to_thread(
            storage.get_object_properties,
            bucket=bucket,
            object_name=raw_object_name,
        )
    except FileNotFoundError as exc:
        raise PhotoValidationError("The uploaded photo changed during verification") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        # Defends against an overwrite between the property read and download.
        raise PhotoValidationError("The uploaded photo is larger than 4 MB")
    if (
        len(raw) != properties.byte_count
        or current_properties.byte_count != properties.byte_count
        or (
            properties.etag is not None
            and current_properties.etag is not None
            and current_properties.etag != properties.etag
        )
    ):
        raise PhotoValidationError("The uploaded photo changed during verification")

    canonical_bytes, width, height = await asyncio.to_thread(_canonicalize_jpeg, raw)
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_name = f"pending/finalized/{photo_id}.jpg"
    await asyncio.to_thread(
        storage.put_object_bytes,
        bucket=bucket,
        object_name=canonical_name,
        data=canonical_bytes,
        content_type="image/jpeg",
        metadata={"sha256": digest, "photo_id": photo_id},
        overwrite=False,
        expected_sha256=digest,
    )
    return CanonicalPhoto(
        raw_object_name=raw_object_name,
        object_name=canonical_name,
        image_bytes=canonical_bytes,
        byte_count=len(canonical_bytes),
        width_px=width,
        height_px=height,
        sha256=digest,
        verified_at=datetime.now(UTC),
    )


def validate_canonical_jpeg(raw: bytes) -> tuple[int, int]:
    """Decode a canonical JPEG under the same bounds used at finalization.

    Moderation calls this again immediately before provider egress. Repeating
    the decode is intentional: Blob contents or database metadata may have
    drifted after the original verification, and that must fail closed.
    """
    if len(raw) < 4 or raw[:2] != b"\xff\xd8" or raw[-2:] != b"\xff\xd9":
        raise PhotoValidationError("The photo is not a complete JPEG image")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format != "JPEG":
                raise PhotoValidationError("Only JPEG photos are accepted")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as opened:
            oriented = ImageOps.exif_transpose(opened)
            oriented.load()
            width, height = oriented.size
            if min(width, height) < MIN_IMAGE_EDGE or max(width, height) > MAX_IMAGE_EDGE:
                raise PhotoValidationError(
                    f"Photo dimensions must be between {MIN_IMAGE_EDGE} and {MAX_IMAGE_EDGE} pixels"
                )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PhotoValidationError("The JPEG could not be decoded safely") from exc
    return width, height


def _canonicalize_jpeg(raw: bytes) -> tuple[bytes, int, int]:
    width, height = validate_canonical_jpeg(raw)
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            rgb = ImageOps.exif_transpose(opened).convert("RGB")
            output = io.BytesIO()
            # Supplying no exif/icc arguments intentionally strips metadata.
            rgb.save(output, format="JPEG", quality=80, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PhotoValidationError("The JPEG could not be decoded safely") from exc

    value = output.getvalue()
    if len(value) > MAX_UPLOAD_BYTES:
        raise PhotoValidationError("The canonical photo is larger than 4 MB")
    return value, width, height
