"""Bounded receive-only evidence for the existing framed UDP video ingress.

This tool does not configure receivers, launch the official client, transmit
packets, capture screens or substitute a bundled sample. It deliberately cannot
certify a legacy receiver merely because arbitrary network video decodes.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rm_client.backend.video_backend import RmVideoBackend


def positive_duration(value: str) -> float:
    duration = float(value)
    if not math.isfinite(duration) or not 0.1 <= duration <= 300.0:
        raise argparse.ArgumentTypeError("duration must be between 0.1 and 300 seconds")
    return duration


def local_ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError("bind host must be a numeric local IPv4 address") from exc


def udp_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def summarize(stats: dict, *, observed: dict, cleanup_complete: bool) -> dict:
    """Pure report projection; no stage can imply a genuine receiver identity."""
    ingress = stats.get("ingress", {})
    fresh = bool(stats.get("decoded_connected", False))
    if fresh:
        stage = "fresh_decoded_video"
    elif observed["decoded"]:
        stage = "decoded_video_became_stale"
    elif observed["assembled"]:
        stage = "assembled_but_not_decoded"
    elif observed["packets"]:
        stage = "packets_but_no_complete_frame"
    elif observed["listening"]:
        stage = "listening_without_packets"
    else:
        stage = "listener_not_started"
    return {
        "schema": "polar-video-ingress-probe-v1",
        "transport": "shark-framed-udp",
        "stage": stage,
        "hardware_verified": False,
        "receiver_identity": "unverified",
        "official_client_required": (
            "For the documented official 2026 forwarding path, the official client "
            "must already show video and both clients must select the same robot ID. "
            "This does not establish legacy receiver compatibility."
        ),
        "observed": dict(observed),
        "fresh_at_end": fresh,
        "cleanup_complete": cleanup_complete,
        "ingress": ingress,
        "video": {key: stats.get(key) for key in (
            "packets_received", "frames_received", "decoded_frames", "codec",
            "invalid_fragments", "stale_fragments", "sequence_discontinuities",
            "output_width", "output_height", "decoder_error", "last_error",
        )},
    }


def probe(host: str, port: int, duration: float) -> tuple[dict, int]:
    backend = RmVideoBackend(host=host, port=port)
    observed = dict(listening=False, packets=False, assembled=False, decoded=False)
    stats: dict = {}
    try:
        backend.start()
        deadline = time.monotonic() + duration
        while True:
            stats = backend.stats()
            observed["listening"] |= bool(stats["ingress"]["listening"])
            observed["packets"] |= stats["packets_received"] > 0
            observed["assembled"] |= stats["frames_received"] > 0
            observed["decoded"] |= bool(stats["decoded_connected"])
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (not backend.running and backend.last_error):
                break
            time.sleep(min(0.05, remaining))
    finally:
        cleanup_error = ""
        try:
            backend.request_stop()
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"
        # Do not call wait_closed(timeout): the decoder compatibility method
        # has an unbounded join. Observe the already-admitted owner instead.
        cleanup_deadline = time.monotonic() + 5.0
        while not backend.cleanup_complete and time.monotonic() < cleanup_deadline:
            time.sleep(0.01)
        cleanup_complete = backend.cleanup_complete
    report = summarize(stats, observed=observed, cleanup_complete=cleanup_complete)
    report["cleanup_error"] = cleanup_error
    # Exit zero means only that a fresh decoded frame was observed at the end
    # and owners closed. It never upgrades hardware_verified.
    return report, 0 if report["fresh_at_end"] and cleanup_complete else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=local_ipv4, default="127.0.0.1",
                        help="local bind IPv4, NOT the official server address")
    parser.add_argument("--port", type=udp_port, default=3334)
    parser.add_argument("--duration", type=positive_duration, default=10.0)
    args = parser.parse_args(argv)
    report, result = probe(args.host, args.port, args.duration)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
