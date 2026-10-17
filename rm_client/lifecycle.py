"""Owner-thread lifecycle coordination without GUI or device dependencies.

A request revokes authority in its caller before handing blocking teardown to
this boundary. Workers receive only their close operation and a bounded result
mailbox. They never apply the next configuration or invoke an owner callback.
The owner polls, accepts the latest desired target, and alone may start it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from queue import Empty, Queue
import threading
import time
from typing import Callable, Generic, Iterable, TypeVar

Target = TypeVar("Target")


class LifecyclePhase(str, Enum):
    IDLE = "idle"
    STOPPING = "stopping"
    BLOCKED = "blocked"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class TeardownTask:
    """One independently owned resource; the action must not call the GUI."""

    name: str
    close: Callable[[], None]
    prepare_retry: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class TeardownFailure:
    resource: str
    error: str


@dataclass(frozen=True, slots=True)
class LifecycleCompletion(Generic[Target]):
    generation: int
    target: Target
    final: bool
    failures: tuple[TeardownFailure, ...]
    warnings: tuple[TeardownFailure, ...] = field(default=(), kw_only=True)

    @property
    def succeeded(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    phase: LifecyclePhase
    generation: int
    pending_resources: tuple[str, ...]
    failures: tuple[TeardownFailure, ...]
    elapsed_s: float
    final: bool
    warnings: tuple[TeardownFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class _TaskResult:
    name: str
    error: str = ""


def _close_resource(task: TeardownTask, mailbox: Queue[_TaskResult]) -> None:
    """Worker boundary: no coordinator, target, manager, or UI callback access."""
    try:
        task.close()
    except Exception as exc:
        result = _TaskResult(task.name, f"{type(exc).__name__}: {exc}")
    else:
        result = _TaskResult(task.name)
    # Each admitted task emits exactly once. Capacity includes all admitted
    # tasks, even when the owner stops polling, so a producer never blocks.
    mailbox.put_nowait(result)


class LifecycleCoordinator(Generic[Target]):
    """Bounded single-flight teardown with latest-request owner completion.

    Independent resources close concurrently so one stuck driver does not
    prevent neutral-before-close work on another device. A timeout changes the
    diagnostic phase only: a stuck owner is never reported closed, discarded,
    retried, or replaced. At most ``max_resources`` workers and results exist.
    Call request/poll/wait only on the constructing thread.
    """

    def __init__(self, *, max_resources: int = 16, warning_after_s: float = 3.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if max_resources < 1 or warning_after_s < 0:
            raise ValueError("Invalid lifecycle resource limit or warning interval")
        self._owner = threading.get_ident()
        self._max_resources = int(max_resources)
        self._warning_after_s = float(warning_after_s)
        self._clock = clock
        self._mailbox: Queue[_TaskResult] = Queue(maxsize=self._max_resources)
        self._workers: dict[str, threading.Thread | None] = {}
        self._tasks: dict[str, TeardownTask] = {}
        self._pending: set[str] = set()
        self._failures: list[TeardownFailure] = []
        self._warnings: dict[str, TeardownFailure] = {}
        self._target: Target | None = None
        self._generation = 0
        self._started_at = 0.0
        self._phase = LifecyclePhase.IDLE
        self._active = False
        self._final = False

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("Lifecycle requests and completion belong to the owner thread")

    @property
    def pending(self) -> bool:
        return self._active

    @property
    def final(self) -> bool:
        return self._final

    def request(self, target: Target, tasks: Iterable[TeardownTask] = (), *,
                final: bool = False) -> int:
        """Coalesce the desired target; admit each named resource at most once.

        A final request seals the coordinator permanently. While a request is
        active, additional tasks are permitted for newly closing UI resources,
        but an existing resource can never acquire a second close worker.
        """
        self._require_owner()
        if self._final and not final:
            raise RuntimeError("Lifecycle is closing; replacement is forbidden")
        return self._request(target, tuple(tasks), final=final)

    def validate_tasks(self, tasks: Iterable[TeardownTask]) -> None:
        """Check bounded admission before a caller performs owner-side work."""
        self._require_owner()
        tasks = tuple(tasks)
        names = [task.name for task in tasks]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("Teardown resource names must be nonempty and unique")
        prior_names = set(self._workers) if self._active else set()
        if len(prior_names.union(names)) > self._max_resources:
            raise ValueError("Too many lifecycle teardown resources")

    def retry_failed(self) -> int:
        """Explicitly retry completed failures without reopening final authority.

        Still-running tasks can never be retried. Successful close operations
        are not replayed. Any retry preparation runs on the owner thread.
        """
        self._require_owner()
        if not self._final or self._active:
            raise RuntimeError("Only completed final cleanup failures can be retried")
        names = {failure.resource for failure in self._failures}
        tasks = tuple(task for name, task in self._tasks.items() if name in names)
        if not tasks:
            raise ValueError("No failed cleanup tasks to retry")
        return self._request(self._target, tasks, final=True, retry=True)

    def _request(self, target: Target, tasks: tuple[TeardownTask, ...], *,
                 final: bool, retry: bool = False) -> int:
        self.validate_tasks(tasks)
        if self._final and not self._active and not retry:
            # Repeated close is idempotent. In particular, never re-run a
            # failed final cleanup or create another owner after shutdown.
            return self._generation
        if not self._active:
            self._workers.clear()
            self._tasks.clear()
            self._pending.clear()
            self._failures.clear()
            if not retry:
                self._warnings.clear()
            self._started_at = self._clock()
            self._active = True
        self._generation += 1
        self._target = target
        self._final = self._final or final
        self._phase = LifecyclePhase.STOPPING
        for task in tasks:
            if task.name in self._workers:
                continue
            self._pending.add(task.name)
            self._tasks[task.name] = task
            if retry and task.prepare_retry is not None:
                try:
                    task.prepare_retry()
                except Exception as exc:
                    self._workers[task.name] = None
                    self._mailbox.put_nowait(_TaskResult(task.name,
                        f"Could not prepare retry: {type(exc).__name__}: {exc}"))
                    continue
            worker = None
            try:
                worker = threading.Thread(target=_close_resource, args=(task, self._mailbox),
                                          name=f"lifecycle-{task.name}", daemon=True)
                self._workers[task.name] = worker
                worker.start()
            except Exception as exc:
                if worker is None or worker.ident is None:
                    self._workers[task.name] = None
                    self._mailbox.put_nowait(_TaskResult(task.name,
                        f"Could not start cleanup: {type(exc).__name__}: {exc}"))
                else:
                    # A wrapper raised after real admission. The worker alone
                    # publishes its resource result; no synthetic completion
                    # may permit retry while its close operation still runs.
                    self._warnings[task.name] = TeardownFailure(
                        f"{task.name} admission",
                        f"Cleanup start raised after admission: {type(exc).__name__}: {exc}",
                    )
        return self._generation

    def poll(self) -> LifecycleCompletion[Target] | None:
        """Return one completion for the newest target, only after all owners exit."""
        self._require_owner()
        if not self._active:
            return None
        while True:
            try:
                result = self._mailbox.get_nowait()
            except Empty:
                break
            self._pending.discard(result.name)
            if result.error:
                self._failures.append(TeardownFailure(result.name, result.error))
        if self._pending:
            if self._clock() - self._started_at >= self._warning_after_s:
                self._phase = LifecyclePhase.BLOCKED
            return None
        # A result is the worker's final meaningful operation. The callable has
        # returned; only thread bookkeeping may still finish after this point.
        self._active = False
        self._phase = (LifecyclePhase.FAILED if self._failures else
                       LifecyclePhase.CLOSED if self._final else LifecyclePhase.IDLE)
        return LifecycleCompletion(self._generation, self._target, self._final,
                                   tuple(self._failures),
                                   warnings=tuple(self._warnings.values()))  # type: ignore[arg-type]

    def wait(self, timeout: float | None = None) -> LifecycleCompletion[Target] | None:
        """Explicit non-interactive compatibility path; UI code must use poll."""
        self._require_owner()
        deadline = None if timeout is None else self._clock() + max(0.0, timeout)
        for worker in tuple(self._workers.values()):
            if worker is not None:
                remaining = None if deadline is None else max(0.0, deadline - self._clock())
                worker.join(remaining)
        return self.poll()

    def snapshot(self) -> LifecycleSnapshot:
        self._require_owner()
        elapsed = max(0.0, self._clock() - self._started_at) if self._generation else 0.0
        return LifecycleSnapshot(self._phase, self._generation, tuple(sorted(self._pending)),
                                 tuple(self._failures), elapsed, self._final,
                                 tuple(self._warnings.values()))
