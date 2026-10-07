from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from app.core.config import settings


def store_snapshot(content: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    root = Path(settings.SNAPSHOT_DIR).resolve()
    path = root / digest[:2] / (digest + ".bin")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as pending:
            temporary = Path(pending.name)
            pending.write(content)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return digest, path.relative_to(root).as_posix()


def read_snapshot(path: str, expected_hash: str) -> bytes:
    root = Path(settings.SNAPSHOT_DIR).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Snapshot fuera del directorio configurado.")
    data = resolved.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("El snapshot no coincide con su hash.")
    return data
