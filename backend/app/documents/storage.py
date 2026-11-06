import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings


class Storage(Protocol):
    """Object-storage interface. LocalStorage is the dev/default backend; an S3-compatible
    backend implements the same three methods."""

    def put(self, fileobj: BinaryIO) -> str: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("Invalid storage key")
        return p

    def new_key(self) -> str:
        now = datetime.now(timezone.utc)
        return f"{now:%Y/%m}/{uuid.uuid4().hex}"

    def put(self, fileobj: BinaryIO) -> str:
        key = self.new_key()
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as out:
            while chunk := fileobj.read(1024 * 1024):
                out.write(chunk)
        return key

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete(self, key: str) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass


def get_storage() -> Storage:
    return LocalStorage(get_settings().storage_root)
