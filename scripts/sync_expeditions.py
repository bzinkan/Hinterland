#!/usr/bin/env python
"""Local-dev shim around `admin.sync_expeditions`.

All sync logic lives in backend/admin/sync_expeditions.py, which runs in
Azure as the manual Container Apps Job `hinterland-sync-expeditions`
against the content baked into the image at /app/content/expeditions.
This shim exists so local runs keep working against the repo checkout:
it points DRAGONFLY_CONTENT_ROOT at content/expeditions/ here (an
explicitly set env var still wins) and delegates argv untouched.

Run from a machine with the same DRAGONFLY_DATABASE_* env as the target
database (local `make dev-db` Postgres by default).

Usage:
    python scripts/sync_expeditions.py
    python scripts/sync_expeditions.py --dry-run
    python scripts/sync_expeditions.py --unarchive <expedition_id>
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DRAGONFLY_CONTENT_ROOT", str(_REPO_ROOT / "content" / "expeditions"))

from admin.sync_expeditions import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
