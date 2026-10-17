"""Lazy compatibility exports for the decomposed window package.

Pure policy/controller modules do not require Qt just because they share this
package. Explicit historical exports still resolve to the original objects;
loading a renderer or Qt helper is deferred until that export is requested.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "EconomyScenePhase": "primitives",
    "FrameScheduler": "primitives",
    "Hotspot": "primitives",
    "InertialScroll": "primitives",
    "ModuleListLayout": "primitives",
    "StartupLayerCache": "primitives",
    "TextMetricsMixin": "text_metrics",
}
__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", package=__name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _EXPORT_MODULES.keys())
