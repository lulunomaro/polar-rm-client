"""Apply a validated change with exact value rollback and fail-closed cleanup.

The caller owns runtime effects. It supplies separate quiesce, update,
restore-runtime, and fail-closed operations; this module cannot open a device,
resume an old command, or infer authorization from a saved preference.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .registry import RegistryPort, RegistrySnapshot


class ConfigurationParticipant(Protocol):
    """A prepared local preference change, independent of the window host.

    Rollback must be safe when commit has not run or has only partly run.
    """
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class ConfigurationRestorePoint:
    document: dict[str, Any]
    control_binds: dict[str, str]
    home_visible: bool
    startup_step: str
    registry_snapshot: RegistrySnapshot | None = None


@dataclass(frozen=True)
class RuntimeRestorePending:
    generation: int


class ConfigurationApplyError(ValueError):
    def __init__(self, cause: Exception, restore_errors: tuple[Exception, ...], safety_error: Exception | None,
                 *, restore_pending: bool = False) -> None:
        self.cause = cause
        self.restore_errors = restore_errors
        self.safety_error = safety_error
        self.restore_pending = restore_pending
        detail = "; ".join(str(error) for error in (cause, *restore_errors))
        if safety_error is not None:
            detail += f"; fail-closed cleanup failed: {safety_error}"
        if restore_pending:
            detail += "; stopped runtime restoration pending"
        super().__init__(detail)

    @property
    def restored(self) -> bool:
        return not self.restore_errors and not self.restore_pending


def apply_configuration_transaction(
    registry: RegistryPort,
    *,
    quiesce: Callable[[], None],
    update: Callable[[], None],
    restore_runtime: Callable[[], RuntimeRestorePending | None],
    fail_closed: Callable[[], None],
    participants: Sequence[ConfigurationParticipant] = (),
) -> None:
    """Run after preflight; never consider a failed runtime update committed.

    Runtime restoration must configure stopped/locked resources only. Each
    rollback step is attempted even when another fails, and fail-closed cleanup
    is unconditional after a failed change. No old held input is restored.
    """
    snapshot = RegistrySnapshot.capture(registry)
    participants = tuple(participants)
    try:
        quiesce()
        update()
        for participant in participants:
            participant.commit()
    except Exception as cause:
        failure = rollback_configuration_transaction(
            snapshot, cause=cause, restore_runtime=restore_runtime, fail_closed=fail_closed,
            participants=participants,
        )
        raise failure from cause


def rollback_configuration_transaction(
    snapshot: RegistrySnapshot,
    *,
    cause: Exception,
    restore_runtime: Callable[[], RuntimeRestorePending | None],
    fail_closed: Callable[[], None],
    participants: Sequence[ConfigurationParticipant] = (),
) -> ConfigurationApplyError:
    """Restore exact values and safety even when runtime recovery is pending."""
    restore_errors = []
    restore_pending = False
    # Runtime rebuilds may normalize options or companion preferences.
    # Restore exact operator values again even if that rebuild fails.
    # Resolving an adapter's rollback callback is part of that participant's
    # fallible work. Do it inside the same guard as invocation, not while
    # constructing the plan, so a broken descriptor cannot bypass safety.
    restorations = (snapshot.restore,
                    *(lambda item=item: item.rollback() for item in reversed(participants)),
                    restore_runtime, snapshot.restore)
    for restore in restorations:
        try:
            result = restore()
            restore_pending |= isinstance(result, RuntimeRestorePending)
        except Exception as error:
            restore_errors.append(error)
    safety_error = None
    try:
        fail_closed()
    except Exception as error:
        safety_error = error
    return ConfigurationApplyError(cause, tuple(restore_errors), safety_error,
                                   restore_pending=restore_pending)
