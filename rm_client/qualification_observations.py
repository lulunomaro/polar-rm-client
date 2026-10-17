"""Pure qualification observations, never a source of runtime authority.

Each evaluator receives explicit measured values and session baselines. The
qualification store still owns identity binding, signing and durable evidence;
these functions neither record a result nor change an output gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualificationEvidence:
    key: str
    passed: bool
    measured: dict[str, Any]
    threshold: dict[str, Any] | str


def video_evidence(*, source: str, session_exists: bool, session_age: float,
                   decoded_frames: int, baseline_frames: int, fresh: bool,
                   continuous_seconds: float) -> QualificationEvidence:
    passed = (session_exists and source in {"udp", "native_0310"} and fresh
              and decoded_frames > baseline_frames and continuous_seconds >= 60.0 and session_age >= 60.0)
    return QualificationEvidence("codec_field_test", passed, {
        "source": source, "frames": decoded_frames,
        "frames_since_baseline": max(0, decoded_frames - baseline_frames),
        "seconds": continuous_seconds, "session_age_seconds": session_age,
    }, {"seconds": 60, "fresh": True, "new_frames": True})


def _runtime_evidence(key: str, passed: bool) -> QualificationEvidence:
    return QualificationEvidence(key, passed, {"runtime_observed": passed}, "live lifecycle observed")


def estop_evidence(*, estop_at: float, baseline_estop_at: float, zero_published: bool) -> QualificationEvidence:
    return _runtime_evidence("estop_robot_test", estop_at > baseline_estop_at and zero_published)


def firmware_zero_evidence(*, estop_at: float, baseline_estop_at: float,
                           observed_ms: float) -> QualificationEvidence:
    passed = estop_at > baseline_estop_at and bool(observed_ms) and observed_ms <= 500.0
    return QualificationEvidence("firmware_loss_zero_test", passed,
        {"zero_observed_ms": observed_ms}, {"maximum_ms": 500, "physical_observer_required": True})


def recovery_evidence(*, reconnect_count: int, baseline_reconnect_count: int,
                      disconnect_lock_at: float, reconnected_locked_at: float,
                      baseline_at: float, control_locked: bool) -> QualificationEvidence:
    passed = (reconnect_count > baseline_reconnect_count and disconnect_lock_at > baseline_at
              and reconnected_locked_at > baseline_at and control_locked)
    return _runtime_evidence("loss_recovery_test", passed)


def hero_evidence(*, revision: int, baseline_revision: int, observed_at: float,
                  baseline_at: float, deploy_mode: int) -> QualificationEvidence:
    passed = revision >= baseline_revision + 2 and observed_at > baseline_at and deploy_mode == 0
    return _runtime_evidence("hero_deploy_status_test", passed)


def economy_evidence(*, succeeded: bool, request_is_mock: bool, request_at: float,
                     baseline_at: float, revision: int, baseline_revision: int,
                     transport_is_mock: bool) -> QualificationEvidence:
    passed = (succeeded and not request_is_mock and request_at >= baseline_at
              and revision > baseline_revision and not transport_is_mock)
    return _runtime_evidence("economy_live_test", passed)


def soak_evidence(*, started_at: float, now: float, match_duration: float) -> QualificationEvidence:
    elapsed = now - started_at if started_at > 0.0 else 0.0
    required = max(420.0, match_duration)
    return QualificationEvidence("latency_soak_test", elapsed >= required,
                                 {"seconds": elapsed}, {"minimum_seconds": required})
