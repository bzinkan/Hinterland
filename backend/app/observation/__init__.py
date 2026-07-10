"""Observation submission services kept outside the route module."""

from app.observation.photo_finalize import CanonicalPhoto, finalize_uploaded_photo

__all__ = ["CanonicalPhoto", "finalize_uploaded_photo"]
