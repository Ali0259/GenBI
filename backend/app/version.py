"""
app.version
------------
Single source of truth for the running application's version number,
read from the VERSION file baked into the Docker image at build time
(see backend/Dockerfile: `COPY VERSION .`). Falls back to "0.0.0-dev" if
the file is missing, which only happens when running app.main directly
outside a container without that file present (e.g. certain local dev
setups) -- it should never happen in a built image.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_VERSION_FILE_PATH = Path(__file__).resolve().parent.parent / "VERSION"
_FALLBACK_VERSION = "0.0.0-dev"


@lru_cache
def get_application_version() -> str:
    try:
        return _VERSION_FILE_PATH.read_text().strip()
    except OSError:
        return _FALLBACK_VERSION
