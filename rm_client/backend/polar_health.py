"""Shared POLAR diagnostic prefix, independent of control and state publication.

Suppliers expose only the two diagnostic sources. They are deliberately read
at each original observation point: this is a fresh ordered capture, not an
atomic/cached telemetry snapshot. Authority, motion and clock-roll diagnostics
have different caller timing and remain outside this prefix.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol


class DecodeHealth(Protocol):
    frames_ok: int
    crc8_errors: int
    crc16_errors: int
    unknown_segments: int
    length_errors: int
    buffer_overflows: int


class CodecHealth(Protocol):
    stats: DecodeHealth


class TransportHealth(Protocol):
    codec: CodecHealth

    def stats(self) -> Mapping[str, object]: ...


class BridgeHealth(Protocol):
    active: bool
    segment_counts: Mapping[int, int]
    dropped: int


def capture_polar_link_health(
    read_transport: Callable[[], TransportHealth],
    read_bridge: Callable[[], BridgeHealth],
) -> dict[str, object]:
    """Capture only shared health fields, preserving observation/failure order."""
    return {
        **read_transport().stats(),
        "link_alive": bool(read_bridge().active),
        "segment_counts": {
            f"0x{key:02X}": int(value)
            for key, value in sorted(read_bridge().segment_counts.items())
        },
        "dropped_segments": read_bridge().dropped,
        "decode": {
            "frames_ok": read_transport().codec.stats.frames_ok,
            "crc8_errors": read_transport().codec.stats.crc8_errors,
            "crc16_errors": read_transport().codec.stats.crc16_errors,
            "unknown_segments": read_transport().codec.stats.unknown_segments,
            "length_errors": read_transport().codec.stats.length_errors,
            "buffer_overflows": read_transport().codec.stats.buffer_overflows,
        },
    }
