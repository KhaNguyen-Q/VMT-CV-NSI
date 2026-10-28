"""Path helpers for development runs and packaged executables."""

import os
import sys
from pathlib import Path


def get_app_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resolve_app_path(path):
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = get_app_directory() / resolved
    return resolved.resolve()


def get_token_cache_path():
    if os.name == "nt":
        cache_root = Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
    elif sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches"
    else:
        cache_root = Path(
            os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        )
    return cache_root / "VMT-CV-NSI" / "msal_cache.bin"
