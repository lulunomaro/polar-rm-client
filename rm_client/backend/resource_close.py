"""Turn ambiguous driver close results into explicit retained-ownership errors."""
from __future__ import annotations
from typing import Any


def close_resource(handle: Any) -> None:
    """Close one captured handle or raise; callers retain it until success.

    Most Python drivers return None on success. An explicit False or a still
    open status is not confirmation. Exceptions are intentionally propagated,
    including a driver that closes partially before reporting an error.
    """
    result = handle.close()
    if result is False:
        raise OSError("Resource close returned False")
    if getattr(handle, "is_open", None) is True:
        raise OSError("Resource remained open after close")


def release_capture(handle: Any) -> None:
    """Release a captured OpenCV owner or raise without discarding ownership."""
    if handle.release() is False:
        raise OSError("Capture release returned False")
    if handle.isOpened():
        raise OSError("Capture remained open after release")
