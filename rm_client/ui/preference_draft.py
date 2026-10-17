"""Pending preference edits, independent of widgets and live configuration.

The adapter owns widget normalization, live reads and transaction effects. This
owner records only the accepted snapshot and explicit reset intent. A failed
transaction must never call ``accept``; a successful transaction accepts the
snapshot taken *before* callbacks, so edits made by callbacks remain pending.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType

DraftKey = tuple[str, ...]


class PreferenceDraft:
    def __init__(self) -> None:
        self._baseline: dict[DraftKey, object] = {}
        self._forced: set[DraftKey] = set()

    @property
    def baseline(self) -> Mapping[DraftKey, object]:
        return MappingProxyType(self._baseline)

    def accept(self, values: Mapping[DraftKey, object]) -> None:
        """Record completed initialization or a successful apply, never an attempt."""
        self._baseline = dict(values)
        self._forced.clear()

    def force_edits(self, keys: Iterable[DraftKey]) -> None:
        """Reset is an edit even when a default equals the accepted snapshot."""
        self._forced.update(keys)

    def is_pending(self, key: DraftKey, read_value: Callable[[], object]) -> bool:
        """Forced fields do not observe widgets, matching reset refresh ordering."""
        return key in self._forced or read_value() != self._baseline[key]

    def edits(self, values: Mapping[DraftKey, object]) -> dict[DraftKey, object]:
        return {key: value for key, value in values.items()
                if key in self._forced or value != self._baseline[key]}

    def follow_live(self, key: DraftKey, normalized_value: object) -> None:
        """Record a clean field only after the adapter has normalized its value."""
        self._baseline[key] = normalized_value
