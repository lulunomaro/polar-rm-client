"""Exact local preference-file snapshots and atomic byte replacement."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile


class FileWriteError(OSError):
    """A failed write plus its separate failed temporary-file cleanup."""
    def __init__(self, cause: Exception, cleanup_error: Exception) -> None:
        self.cause = cause
        self.cleanup_error = cleanup_error
        super().__init__(f"{type(cause).__name__}: {cause}; temporary cleanup failed "
                         f"({type(cleanup_error).__name__}): {cleanup_error}")


def _adopt_temporary_descriptor(descriptor: int):
    """The raw descriptor remains ours until fdopen returns its stream."""
    try:
        return os.fdopen(descriptor, "wb")
    except Exception as cause:
        try:
            os.close(descriptor)
        except Exception as cleanup_error:
            raise FileWriteError(cause, cleanup_error) from cause
        raise


def atomic_write_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    failure = None
    try:
        with _adopt_temporary_descriptor(descriptor) as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception as error:
        failure = error
        raise
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except Exception as cleanup_error:
            if failure is not None:
                raise FileWriteError(failure, cleanup_error) from failure
            raise


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    contents: bytes | None

    @classmethod
    def capture(cls, path: Path) -> FileSnapshot:
        return cls(path, path.read_bytes() if path.exists() else None)

    def restore(self) -> None:
        if self.contents is None:
            self.path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(self.path, self.contents)
