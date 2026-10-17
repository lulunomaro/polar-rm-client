"""Compatibility entry point for the Qt client.

The historical Tk prototype is loaded only when its symbols are explicitly
requested. Normal startup must not depend on Tk or the prototype transports.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_LEGACY_EXPORTS = frozenset({
    "RMClientApp",
    "BG", "PANEL_BG", "PANEL_BG_2", "TEXT", "MUTED", "HOVER", "ACCENT",
    "PINK", "GREEN", "RED", "lerp", "clamp",
})
__all__ = ["main", *sorted(_LEGACY_EXPORTS)]


def __getattr__(name: str) -> Any:
    if name not in _LEGACY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    legacy = import_module(".legacy_tk_app", package=__package__)
    value = getattr(legacy, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LEGACY_EXPORTS)


def main() -> None:
    """Launch the supported Qt client without constructing legacy objects."""
    from .ui.polar_app import main as run_gpu_client

    run_gpu_client()


if __name__ == "__main__":
    main()
