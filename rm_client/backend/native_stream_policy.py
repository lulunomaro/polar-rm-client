"""Pure continuity decisions for native telemetry and 0x0310 video.

Wire validation belongs to ``native_viewer_telemetry``. These policies consume
already-decoded values; they neither publish robot state nor touch a decoder.
Telemetry restarts revoke localization trust. Video discontinuities require a
fresh recovery epoch, while duplicate/late chunks must not enter that epoch.
"""
from __future__ import annotations

from dataclasses import dataclass


_LOCALIZATION_RESET_MARKERS = (
    "LOCALIZATION LOST",
    "LOCALISATION LOST",
    "RELOCALIZING",
    "RELOCALISING",
    "POSE RESET",
    "MAP RESET",
)


def localization_invalidated(previous_sequence: int, sequence: int, status: str) -> bool:
    """A short backward jump is a restart; natural uint32 wrap is not."""
    status_upper = status.upper()
    sequence_restarted = bool(
        previous_sequence > 0
        and sequence < previous_sequence
        and (previous_sequence - sequence) < 0x80000000
    )
    return bool(
        sequence_restarted
        or any(marker in status_upper for marker in _LOCALIZATION_RESET_MARKERS)
    )


@dataclass(frozen=True, slots=True)
class NativeVideoSequenceDecision:
    sequence: int
    reset: bool
    dropped_chunks: int = 0
    accept_payload: bool = True


def assess_native_video_sequence(
    *, sequence: int, modulus: int, reset: bool, previous_sequence: int | None,
) -> NativeVideoSequenceDecision:
    """Classify one bounded sequence without changing stream or codec state.

    A reset starts a new epoch regardless of the previous sequence. Within an
    epoch, the forward half of the ring counts missing chunks; the other half
    (including exactly half a ring) is late/duplicate and must be discarded.
    """
    modulus = max(1, int(modulus))
    sequence = int(sequence) % modulus
    if reset or previous_sequence is None:
        return NativeVideoSequenceDecision(sequence, reset)
    expected = (previous_sequence + 1) % modulus
    distance = (sequence - expected) % modulus
    if not distance:
        return NativeVideoSequenceDecision(sequence, False)
    if distance < modulus // 2:
        return NativeVideoSequenceDecision(sequence, False, dropped_chunks=distance)
    return NativeVideoSequenceDecision(sequence, False, accept_payload=False)
