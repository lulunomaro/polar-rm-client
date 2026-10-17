"""Owner-thread backend transition and final-audit state machine.

The controller owns teardown admission/completion, not settings or live UI
state. Its three explicit owner actions invalidate runtime caches, finish
stopped-runtime bookkeeping, and activate one captured scenario request.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from ..lifecycle import (LifecycleCompletion, LifecycleCoordinator, LifecyclePhase,
                         LifecycleSnapshot, TeardownFailure, TeardownTask)
from .lifecycle import (AuditCloseStatus, ScenarioRequest, ShutdownCompletion,
                        ShutdownRequest, close_audit, emit_lifecycle_event)
from .teardown_plan import BACKEND_RESOURCE_NAMES, TeardownResources


class BackendLifecycleController:
    def __init__(self, *, invalidate_runtime: Callable[[], None],
                 stopped_runtime: Callable[[], None],
                 activate_scenario: Callable[[ScenarioRequest], None]) -> None:
        self._resources: LifecycleCoordinator[ScenarioRequest | ShutdownRequest] = LifecycleCoordinator()
        self._audit: LifecycleCoordinator[None] = LifecycleCoordinator(max_resources=1)
        self._invalidate_runtime = invalidate_runtime
        self._stopped_runtime = stopped_runtime
        self._activate_scenario = activate_scenario
        self._owners: TeardownResources | None = None
        self._prepare_failures: tuple[TeardownFailure, ...] = ()
        self._observer_failure: TeardownFailure | None = None
        self._failures: tuple[TeardownFailure, ...] = ()
        self._final_resource_completion: LifecycleCompletion | None = None
        self._final_resource_failures: tuple[TeardownFailure, ...] = ()
        self.initial_shutdown_completion: ShutdownCompletion | None = None
        self.shutdown_completion: ShutdownCompletion | None = None
        self._shutdown_attempt = 0
        self._retrying_shutdown = False
        self.closed = False
        self.applying = False
        self.pending_scenario: ScenarioRequest | None = None
        self.transition_estop_at = 0.0

    @property
    def pending(self) -> bool:
        return self._resources.pending or self._audit.pending

    @property
    def final(self) -> bool:
        return self._resources.final

    @property
    def blocks_work(self) -> bool:
        return (self.pending or self.final or bool(self._failures)
                or self._resources.snapshot().phase == LifecyclePhase.FAILED)

    def snapshot(self) -> LifecycleSnapshot:
        if self._audit.pending:
            snapshot = self._audit.snapshot()
            resources = self._resources.snapshot()
            return replace(snapshot, generation=resources.generation,
                           failures=self._final_resource_completion.failures + snapshot.failures,
                           warnings=resources.warnings + snapshot.warnings)
        snapshot = self._resources.snapshot()
        if self._failures:
            return replace(snapshot, phase=LifecyclePhase.FAILED, failures=self._failures)
        return snapshot

    def _prepare(self, owners: TeardownResources, timeout: float | None) -> tuple[TeardownTask, ...]:
        self._owners = owners
        self._observer_failure = None
        prepared = owners.prepare(self._invalidate_runtime, device_wait_timeout=timeout)
        self._prepare_failures = prepared.failures
        self.transition_estop_at = prepared.estop_at
        return prepared.tasks

    def record_input_failure(self, exc: Exception) -> bool:
        """Retain observer evidence without changing native ownership results."""
        self.snapshot()
        if not self.pending and not self.final:
            return False
        if self._observer_failure is None:
            self._observer_failure = TeardownFailure("input clear", f"{type(exc).__name__}: {exc}")
        return True

    def request_scenario(self, request: ScenarioRequest, owners: TeardownResources) -> int:
        self.snapshot()  # Assert affinity before callbacks or captured-owner mutation.
        if self.final:
            raise RuntimeError("Backend is closing; scenario replacement is forbidden")
        tasks = () if self._resources.pending else self._prepare(
            owners, 2.5 if request.synchronous_start else None)
        self.pending_scenario = request
        return self._resources.request(request, tasks)

    def update_pending_identity(self, client_id: str) -> None:
        if self.pending_scenario is not None and not self.applying:
            self.pending_scenario = replace(self.pending_scenario, robot_id=client_id)
            self._resources.request(self.pending_scenario)

    def request_shutdown(self, owners: TeardownResources, *, restart: bool = False,
                         teardown_tasks: tuple[TeardownTask, ...] = (),
                         device_wait_timeout: float | None = None) -> int:
        self.snapshot()
        if any(task.name in BACKEND_RESOURCE_NAMES for task in teardown_tasks):
            raise ValueError("Additional teardown names must not shadow backend resources")
        fresh = not self._resources.pending and not self.final
        preview = tuple(TeardownTask(name, lambda: None) for name in owners.names) if fresh else ()
        self._resources.validate_tasks((*preview, *teardown_tasks))
        tasks = self._prepare(owners, device_wait_timeout) if fresh else ()
        self.pending_scenario = None
        return self._resources.request(ShutdownRequest(restart), (*tasks, *teardown_tasks), final=not restart)

    def retry_shutdown(self) -> int:
        self.snapshot()
        if not self.final or not self.closed or self.pending:
            raise RuntimeError("Only a completed final shutdown may be retried")
        resource_failures = self._resources.snapshot().failures
        audit_failures = self._audit.snapshot().failures
        if not resource_failures and not audit_failures:
            raise ValueError("No failed shutdown tasks to retry")
        self._shutdown_attempt += 1
        self._retrying_shutdown = True
        self.closed = False
        self._failures = ()
        if resource_failures:
            self._resources.retry_failed()
        else:
            self._audit.retry_failed()
        return self._shutdown_attempt

    def _finish_shutdown(self, audit_failures: tuple[TeardownFailure, ...]) -> ShutdownCompletion:
        resource = self._final_resource_completion
        if self._observer_failure is not None and self._observer_failure not in resource.failures:
            resource = replace(resource, failures=resource.failures + (self._observer_failure,))
        audit = AuditCloseStatus.capture(self._owners.logger)
        warnings = resource.warnings + self._audit.snapshot().warnings
        # Warnings are visible final diagnostics, not evidence of unclosed
        # native owners and never an excuse to replay successful close jobs.
        completion = ShutdownCompletion(
            resource.generation, resource.target, True,
            resource.failures + audit_failures + warnings,
            not self._final_resource_failures, audit, self._shutdown_attempt,
            warnings=warnings)
        self._failures = completion.failures
        self.closed = True
        self._retrying_shutdown = False
        self.shutdown_completion = completion
        if self.initial_shutdown_completion is None:
            self.initial_shutdown_completion = completion
        return completion

    def _complete_resources(self, completion: LifecycleCompletion) -> LifecycleCompletion | None:
        if completion.final:
            self._final_resource_failures = completion.failures
        completion = replace(completion, failures=self._prepare_failures + completion.failures
                             + ((self._observer_failure,) if self._observer_failure else ()))
        self._failures = completion.failures
        self.pending_scenario = None
        try:
            self._stopped_runtime()
        except Exception as exc:
            failure = TeardownFailure("stopped runtime", f"{type(exc).__name__}: {exc}")
            completion = replace(completion, failures=completion.failures + (failure,))
            self._failures = completion.failures
        logger = self._owners.logger
        for failure in completion.failures:
            emit_lifecycle_event(logger, "backend_lifecycle_failure", resource=failure.resource,
                                 error=failure.error, generation=completion.generation)
        for warning in completion.warnings:
            emit_lifecycle_event(logger, "backend_lifecycle_warning", resource=warning.resource,
                                 error=warning.error, generation=completion.generation)
        if completion.final:
            self._final_resource_completion = completion
            if self._retrying_shutdown:
                if self._audit.snapshot().failures:
                    self._audit.retry_failed()
                    return None
                return self._finish_shutdown(())
            self._shutdown_attempt = 1
            emit_lifecycle_event(logger, "backend_shutdown", restart=False)
            self._audit.request(None, [TeardownTask("audit-log", lambda: close_audit(logger))], final=True)
            return None
        emit_lifecycle_event(logger, "backend_shutdown", restart=True)
        if completion.succeeded and isinstance(completion.target, ScenarioRequest):
            self.applying = True
            try:
                self._activate_scenario(completion.target)
            except Exception as exc:
                self._owners.safety.trigger_estop()
                failure = TeardownFailure("scenario apply", f"{type(exc).__name__}: {exc}")
                self._failures = completion.failures + (failure,)
                completion = replace(completion, failures=self._failures)
                emit_lifecycle_event(logger, "backend_lifecycle_failure", resource=failure.resource,
                                     error=failure.error, generation=completion.generation)
            finally:
                self.applying = False
        return completion

    def poll(self) -> LifecycleCompletion | None:
        if self._audit.pending:
            audit = self._audit.poll()
            return self._finish_shutdown(audit.failures) if audit is not None else None
        completion = self._resources.poll()
        return self._complete_resources(completion) if completion is not None else None

    def wait(self) -> LifecycleCompletion | None:
        completion = self._resources.wait()
        if completion is not None:
            completion = self._complete_resources(completion)
        if self._audit.pending:
            audit = self._audit.wait()
            if audit is not None:
                completion = self._finish_shutdown(audit.failures)
        return completion
