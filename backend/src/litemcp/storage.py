"""Object storage backends used by LiteMCP.

The filesystem implementation intentionally follows the same small boundary as
the future S3-backed implementation: object keys are portable POSIX-style
names, while values are opaque bytes.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path


class FileSystemStorageBackend:
    """Store objects beneath ``root`` using portable object keys."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(object_key: str) -> str:
        """Validate and normalize a key without allowing namespace escapes."""

        if not isinstance(object_key, str) or not object_key:
            raise ValueError("object key must be a non-empty string")

        # S3 keys are POSIX-like; accepting backslashes here keeps callers
        # portable across Windows and POSIX hosts.
        key = object_key.replace("\\", "/")
        if key.startswith("/") or re.match(r"^[A-Za-z]:/", key):
            raise ValueError("absolute object keys are not portable")

        normalized = posixpath.normpath(key)
        if normalized in (".", "..") or normalized.startswith("../"):
            raise ValueError("object key escapes its namespace")
        return normalized

    def _path(self, object_key: str) -> Path:
        return self.root / self._key(object_key)

    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def put(self, object_key: str, payload: bytes) -> str:
        key = self._key(object_key)
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(payload))
        return self.digest(payload)

    def get(self, object_key: str) -> bytes:
        key = self._key(object_key)
        path = self.root / key
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise FileNotFoundError(key) from exc

    def delete(self, object_key: str) -> None:
        key = self._key(object_key)
        path = self.root / key
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise FileNotFoundError(key) from exc

