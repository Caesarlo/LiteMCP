"""Contract tests for the filesystem/S3-compatible object storage boundary."""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest


@runtime_checkable
class StorageBackend(Protocol):
    """Boundary shared by filesystem and S3-compatible implementations."""

    def put(self, object_key: str, payload: bytes) -> str: ...

    def get(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> None: ...

    def digest(self, payload: bytes) -> str: ...


class InMemoryS3CompatibleBackend:
    """Tiny S3-shaped fake proving the contract is store-independent."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    @staticmethod
    def _key(object_key: str) -> str:
        if not isinstance(object_key, str) or not object_key:
            raise ValueError("object key must be a non-empty string")
        key = object_key.replace("\\", "/")
        if key.startswith("/") or re.match(r"^[A-Za-z]:/", key):
            raise ValueError("absolute object keys are not portable")
        normalized = posixpath.normpath(key)
        if normalized in (".", "..") or normalized.startswith("../"):
            raise ValueError("object key escapes its namespace")
        return normalized

    def digest(self, payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def put(self, object_key: str, payload: bytes) -> str:
        self._objects[self._key(object_key)] = bytes(payload)
        return self.digest(payload)

    def get(self, object_key: str) -> bytes:
        key = self._key(object_key)
        try:
            return self._objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    def delete(self, object_key: str) -> None:
        key = self._key(object_key)
        try:
            del self._objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc


def _make_backend(factory_id: str, root: Path) -> StorageBackend:
    if factory_id == "filesystem":
        from litemcp.storage import FileSystemStorageBackend

        backend = FileSystemStorageBackend(root)
    else:
        backend = InMemoryS3CompatibleBackend()
    assert isinstance(backend, StorageBackend)
    return backend


@pytest.mark.parametrize("factory_id", ["filesystem", "s3-compatible-fake"])
def test_put_get_delete_contract(factory_id: str, tmp_path: Path) -> None:
    backend = _make_backend(factory_id, tmp_path / factory_id)
    payload = b"artifact bytes\x00\xff"

    digest = backend.put("artifacts/demo.bin", payload)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert backend.get("artifacts/demo.bin") == payload
    backend.delete("artifacts/demo.bin")
    with pytest.raises(FileNotFoundError):
        backend.get("artifacts/demo.bin")


@pytest.mark.parametrize("factory_id", ["filesystem", "s3-compatible-fake"])
def test_digest_is_stable_and_independent_of_backend(factory_id: str, tmp_path: Path) -> None:
    backend = _make_backend(factory_id, tmp_path / factory_id)

    assert backend.digest(b"") == hashlib.sha256(b"").hexdigest()
    assert backend.digest(b"same content") == backend.digest(b"same content")
    assert backend.digest(b"same content") != backend.digest(b"different content")


@pytest.mark.parametrize("factory_id", ["filesystem", "s3-compatible-fake"])
def test_object_keys_are_portable_and_cannot_escape_namespace(
    factory_id: str, tmp_path: Path
) -> None:
    backend = _make_backend(factory_id, tmp_path / factory_id)

    backend.put("nested/path/asset.json", b"{}")
    assert backend.get("nested/path/asset.json") == b"{}"

    for invalid_key in ("/absolute/path", "C:\\absolute\\path", "../escape", "a/../../escape"):
        with pytest.raises(ValueError):
            backend.put(invalid_key, b"must-not-write")


def test_filesystem_and_s3_fake_have_identical_boundary_results(tmp_path: Path) -> None:
    stores = (
        _make_backend("filesystem", tmp_path / "filesystem"),
        _make_backend("s3-compatible-fake", tmp_path / "s3"),
    )
    payload = b"portable payload"
    expected_digest = hashlib.sha256(payload).hexdigest()

    outcomes = []
    for store in stores:
        digest = store.put("releases/v1/bundle.zip", payload)
        outcomes.append((digest, store.get("releases/v1/bundle.zip")))
        store.delete("releases/v1/bundle.zip")
        with pytest.raises(FileNotFoundError):
            store.get("releases/v1/bundle.zip")

    assert outcomes == [(expected_digest, payload), (expected_digest, payload)]
