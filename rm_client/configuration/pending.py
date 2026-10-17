"""Generation-bound state for an owner-thread configuration transition.

This record holds values, not a window or a backend. The sole UI lifecycle
poller routes completions to the adapter; stale completions cannot commit a
profile, and an unexpected newer generation requires fail-closed restoration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .registry import RegistrySnapshot
from .transactions import ConfigurationRestorePoint


class ConfigurationPhase(Enum):
    APPLYING = auto()
    RESTORING = auto()


class CompletionAction(Enum):
    IGNORE = auto()
    CANCEL = auto()
    COMMIT = auto()
    ROLLBACK = auto()
    RESTORED = auto()
    RESTORE_FAILED = auto()


@dataclass(frozen=True)
class ProfileIdentity:
    """A successfully loaded/saved profile, distinct from UI selection."""
    name: str
    generation: int = 0


@dataclass(frozen=True)
class PendingScenarioConfiguration:
    """Accepted registry projection for an ordinary, coalescible scenario request."""
    generation: int
    staged: RegistrySnapshot


@dataclass(frozen=True)
class PendingConfigurationChange:
    generation: int
    phase: ConfigurationPhase
    previous: ConfigurationRestorePoint
    original: RegistrySnapshot
    staged: RegistrySnapshot
    announce: bool = False
    restore_errors: tuple[str, ...] = ()
    safety_error: str | None = None
    target_profile: str | None = None

    def completion_action(self, generation: int, *, succeeded: bool, final: bool) -> CompletionAction:
        if final:
            return CompletionAction.CANCEL
        if generation < self.generation:
            return CompletionAction.IGNORE
        if generation > self.generation:
            return CompletionAction.ROLLBACK
        if self.phase is ConfigurationPhase.APPLYING:
            return CompletionAction.COMMIT if succeeded else CompletionAction.ROLLBACK
        return CompletionAction.RESTORED if succeeded else CompletionAction.RESTORE_FAILED
