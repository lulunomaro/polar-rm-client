"""Physical StandardRobot++ engineer controller -> official player-end relay.

Only A5 / 0x0302 / 30-byte packets are angles: six little-endian binary32
motor-joint positions in radians. This is NOT MQTT CustomControl / PRM1.
Hardware is off by default. Reception never grants forwarding permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import struct
import threading
import time
from typing import Any, Callable

from .resource_close import close_resource

from ..protocol import RefereeFrameCodec, crc8, crc16

ANGLE_CMD = 0x0302
ANGLE_TIMEOUT_S = 0.300  # Provisional bench default; matches the firmware patch.
PARTIAL_TIMEOUT_S = 0.100
MAX_CHUNK = 512
MAX_BUFFER = 1024
FORWARD_INTERVAL_S = 1.0 / 30.0
GUARD_TIMEOUT_S = 0.250
PROFILE_ID = "standardrobotpp-engineer-2722e0d"
# Proven source clamps in robot_param_engineer_controller.h + its observer.
# J4/J5 are ALREADY coupled source positions; never invert them again here.
ANGLE_BOUNDS = ((-2 * math.pi, 2 * math.pi), (-1.10, 1.20), (-2.50, 0.0),
                (-4 * math.pi, 4 * math.pi), (-4.5 * math.pi, 4.5 * math.pi),
                (-4.5 * math.pi, 4.5 * math.pi))
ANGLE_TOLERANCE = 1e-5  # Binary32 rounding at source clamp boundaries, radians.


def port_key(port: str) -> str:
    """Compare explicit OS names, including POSIX symlink aliases; no probing."""
    value = str(port).strip()
    return os.path.realpath(value) if value.startswith("/") else value.casefold()


def explicit_port(port: str) -> bool:
    return bool(str(port).strip()) and str(port).strip().lower() not in {"auto", "stub", "none"}


def angle_payload(data: bytes) -> tuple[tuple[float, ...], bytes]:
    """Validate the known six-axis contract; never forward source padding.

    Old firmware sends 30 bytes from a 28-byte object. The last six bytes
    are not part of the six-angle contract and can contain out-of-bounds data.
    Units/axis signs remain exactly those chosen by the source firmware.
    """
    data = bytes(data)
    if len(data) != 30:
        raise ValueError("controller angles require exactly 30 bytes")
    angles = struct.unpack("<6f", data[:24])
    if not all(math.isfinite(value) for value in angles):
        raise ValueError("controller angles must be finite")
    if any(not low - ANGLE_TOLERANCE <= value <= high + ANGLE_TOLERANCE
           for value, (low, high) in zip(angles, ANGLE_BOUNDS)):
        raise ValueError("angles outside the pinned engineer controller source bounds")
    return angles, data[:24] + bytes(6)


@dataclass(frozen=True)
class AngleSample:
    angles: tuple[float, ...]
    payload: bytes
    received_at: float
    seq: int
    revision: int


class AngleStreamDecoder:
    """Bounded, CRC-checked stream parser with per-angle freshness.

    CRC-valid unrelated frames are skipped whole, including their contents.
    An incomplete candidate has a total-age deadline, not a sliding timeout.
    Duplicate/late sequence values never renew angle freshness.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.partial_at: float | None = None
        self.latest: AngleSample | None = None
        self.revision = 0
        self.rejected = 0
        self.ignored = 0
        self._last_seq: int | None = None

    def reset(self) -> None:
        self.buffer.clear()
        self.partial_at = None
        self.latest = None
        self._last_seq = None

    def feed(self, data: bytes, now: float) -> list[AngleSample]:
        data = bytes(data)
        if not math.isfinite(now) or len(data) > MAX_CHUNK:
            self.rejected += 1
            self.reset()
            return []
        if self.latest is not None and now - self.latest.received_at >= ANGLE_TIMEOUT_S:
            self.latest = None
            self._last_seq = None
        if self.partial_at is not None and now - self.partial_at > PARTIAL_TIMEOUT_S:
            # Preserve suffixes so a good frame behind a corrupt header recovers.
            del self.buffer[:1]
            self.partial_at = None
            self.rejected += 1
        self.buffer.extend(data)
        if len(self.buffer) > MAX_BUFFER:
            self.rejected += 1
            self.reset()
            return []
        samples = []
        while self.buffer:
            if self.buffer[0] != 0xA5:
                del self.buffer[0]
                self.partial_at = None
                continue
            if len(self.buffer) < 5:
                break
            header = self.buffer[:5]
            size = int.from_bytes(header[1:3], "little")
            if crc8(header[:4]) != header[4] or size > 300:
                del self.buffer[0]
                self.partial_at = None
                self.rejected += 1
                continue
            total = size + 9
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            if crc16(raw[:-2]) != int.from_bytes(raw[-2:], "little"):
                del self.buffer[0]
                self.partial_at = None
                self.rejected += 1
                continue
            del self.buffer[:total]
            self.partial_at = None
            if int.from_bytes(raw[5:7], "little") != ANGLE_CMD:
                self.ignored += 1
                continue
            try:
                angles, canonical = angle_payload(raw[7:-2])
            except ValueError:
                self.rejected += 1
                continue
            seq = raw[3]
            if self._last_seq is not None and not 0 < ((seq - self._last_seq) & 255) < 128:
                self.rejected += 1
                continue
            self._last_seq = seq
            self.revision += 1
            sample = AngleSample(angles, canonical, now, seq, self.revision)
            self.latest = sample
            samples.append(sample)
        if self.buffer and self.partial_at is None:
            self.partial_at = now
        return samples


@dataclass(frozen=True)
class AngleRelayConfig:
    enabled: bool = False
    rx_port: str = "Stub"
    tx_port: str = "Stub"
    forward: bool = False
    mock_output: bool = True


class ControllerAngleRelay:
    """One background owner, latest-only forwarding, explicit process-local arm.

    UI methods do not open/read/write/join serial ports. The worker never
    repeats a sample to keep a robot alive. On stale input/failure/lock/config
    change forwarding disarms; fresh reception alone cannot re-arm it.
    """

    def __init__(self, *, time_fn: Callable[[], float] = time.perf_counter,
                 serial_factory=None, start_workers: bool = True) -> None:
        self.time_fn = time_fn
        self.serial_factory = serial_factory
        self.start_workers = start_workers
        self.config = AngleRelayConfig()
        self.decoder = AngleStreamDecoder()
        self._lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._thread = None
        self._owned_config: AngleRelayConfig | None = None
        self._retained_streams: tuple[Any, ...] = ()
        self._close_error = ""
        self._stop = threading.Event()
        self._epoch = 0
        self._authorized = False
        self._guard_at = -math.inf
        self._arm_revision = 0
        self._last_forward_revision = 0
        self._last_forward_at = -math.inf
        self._failed = False
        self._tx_busy = False
        self._arm_epoch = 0
        self.armed = False
        self.connected = False
        self.forwarded = 0
        self.mock_forwarded = 0
        self.last_error = "disabled"
        self.last_frame = b""

    def configure(self, config: AngleRelayConfig, authorized: bool, *, reason: str = "") -> None:
        with self._lock:
            changed = config != self.config
            if changed:
                self._epoch += 1
                self._stop.set()
                self.decoder.reset()
                self.disarm("configuration changed; fresh input and re-arm required")
                self._failed = False
                self.connected = False
                self.config = config
            self._authorized = bool(authorized)
            self._guard_at = self.time_fn()
            if not authorized:
                self.disarm(reason or "RobotLink/Allow Drive/stop gate denied forwarding")
            if not config.enabled:
                self._stop.set()
                self.decoder.reset()
                self.disarm("disabled")
                return
            if not explicit_port(config.rx_port):
                self.disarm("select an explicit controller RX port (Auto is forbidden)")
                return
            if config.forward and (not explicit_port(config.tx_port)
                    or port_key(config.rx_port) == port_key(config.tx_port)):
                self.disarm("select distinct explicit RX and player-end TX ports")
                return
            if self.start_workers and not self._failed and not self._retained_streams and (
                    self._thread is None or not self._thread.is_alive()):
                stop = threading.Event()
                worker = threading.Thread(target=self._run,
                    args=(config, self._epoch, stop), name="controller-angle-relay", daemon=True)
                self._stop = stop
                self._owned_config = config
                self._thread = worker
                try:
                    worker.start()
                except Exception:
                    # An unstarted Thread cannot be joined by strict teardown.
                    # A real admission, even a finished worker, keeps its ident
                    # and remains owned until wait_closed confirms cleanup.
                    if worker.ident is None:
                        # A reentrant start wrapper may already have admitted
                        # a successor; this rejection owns only its old slot.
                        if self._thread is worker:
                            self._thread = None
                            self._owned_config = None
                        stop.set()
                    raise

    @property
    def owned_ports(self) -> tuple[str, ...]:
        """Reserved until the old worker closes, even after reconfiguration."""
        with self._lock:
            config = self._owned_config
            if config is None:
                return ()
            ports = [config.rx_port]
            if config.forward and not config.mock_output:
                ports.append(config.tx_port)
            return tuple(ports)

    def _fresh(self, now: float) -> bool:
        sample = self.decoder.latest
        return sample is not None and 0 <= now - sample.received_at < ANGLE_TIMEOUT_S

    def _expire(self, now: float) -> None:
        if self.decoder.latest is not None and not self._fresh(now):
            self.decoder.reset()
            self.disarm("angle input stale; receive fresh angles, then re-arm")
        if self.armed and (not self._authorized or now - self._guard_at > GUARD_TIMEOUT_S):
            self.disarm("forward permission heartbeat expired; re-arm required")

    def arm(self) -> tuple[bool, str]:
        with self._lock:
            now = self.time_fn()
            self._expire(now)
            c = self.config
            if not c.enabled or not c.forward or not self._authorized:
                return False, "enable receiver and Forward Angles; RobotLink and Allow Drive are required"
            if self._failed or not self._fresh(now) or now - self._guard_at > GUARD_TIMEOUT_S:
                return False, "fresh valid angles and current permission are required"
            if not explicit_port(c.rx_port) or not explicit_port(c.tx_port) or port_key(c.rx_port) == port_key(c.tx_port):
                return False, "select distinct explicit RX and player-end TX ports"
            self.armed = True
            self._arm_epoch += 1
            self._arm_revision = self.decoder.revision
            self.last_error = "armed; waiting for the next fresh source frame"
            return True, self.last_error

    def disarm(self, reason: str = "operator disarmed") -> None:
        with self._lock:
            self.armed = False
            self._arm_epoch += 1
            self._arm_revision = self.decoder.revision
            self.last_error = str(reason)

    def feed(self, data: bytes, now: float | None = None) -> list[AngleSample]:
        with self._lock:
            now = self.time_fn() if now is None else now
            self._expire(now)
            return self.decoder.feed(data, now)

    def _pending(self, now: float) -> AngleSample | None:
        self._expire(now)
        sample = self.decoder.latest
        if (not self.armed or not self._fresh(now) or sample is None
                or sample.revision <= max(self._arm_revision, self._last_forward_revision)
                or now - self._last_forward_at < FORWARD_INTERVAL_S):
            return None
        return sample

    def pump(self, writer=None, now: float | None = None) -> tuple[bool, str]:
        """Forward at most one CURRENT sample. Tests inject an inert writer.

        The actual writer is already opened by the worker; the guard is
        rechecked after slow opens. A short write permanently fails this epoch.
        No all-zero angle packet is sent: zero is a real actuator target.
        An already-started OS write cannot be recalled by disarm. It runs
        without the metadata lock so a broken driver cannot freeze the UI;
        epoch checks prevent its completion from restoring authorization.
        """
        with self._lock:
            now = self.time_fn() if now is None else now
            sample = self._pending(now)
            if sample is None or self._tx_busy:
                return False, self.last_error or "no new eligible sample"
            frame = RefereeFrameCodec().encode(ANGLE_CMD, sample.payload, seq=sample.seq)
            mock = self.config.mock_output
            if not mock and writer is None:
                return False, "output writer unavailable"
            epoch = (self._epoch, self._arm_epoch)
            self._tx_busy = True
            self._last_forward_revision = sample.revision
            self._last_forward_at = now
        try:
            if not mock:
                written = writer(frame)
                if written != len(frame):
                    raise IOError(f"controller angle short write {written}/{len(frame)}")
            with self._lock:
                if not mock:
                    self.forwarded += 1
                else:
                    self.mock_forwarded += 1
                self.last_frame = frame
                if epoch == (self._epoch, self._arm_epoch):
                    self.last_error = "mock" if mock else "written; robot receipt unverified"
                return True, self.last_error
        except Exception as exc:
            with self._lock:
                if epoch[0] == self._epoch:
                    self._failed = True
                    self.disarm(f"output failed; toggle receiver off/on then re-arm: {exc}")
                    self._stop.set()
                return False, self.last_error
        finally:
            with self._lock:
                self._tx_busy = False

    def stats(self, now: float | None = None) -> dict:
        with self._lock:
            now = self.time_fn() if now is None else now
            self._expire(now)
            sample = self.decoder.latest if self._fresh(now) else None
            return {"enabled": self.config.enabled, "connected": self.connected,
                    "valid": sample is not None, "armed": self.armed,
                    "angles_rad": list(sample.angles) if sample else None,
                    "age_s": now - sample.received_at if sample else None,
                    "received": self.decoder.revision, "rejected": self.decoder.rejected,
                    "ignored": self.decoder.ignored, "forwarded": self.forwarded,
                    "write_in_flight": self._tx_busy,
                    "mock_forwarded": self.mock_forwarded, "mock_output": self.config.mock_output,
                    "last_error": self.last_error, "timeout_s": ANGLE_TIMEOUT_S,
                    "profile": PROFILE_ID}

    def close(self) -> None:
        with self._lock:
            self._epoch += 1
            self._stop.set()
            self.decoder.reset()
            self.disarm("closed")

    def _open(self, port):
        factory = self.serial_factory
        if factory is None:
            import serial
            factory = serial.Serial
        return factory(port=port, baudrate=115200, timeout=0, write_timeout=0.020)

    def _run(self, config, epoch, stop) -> None:
        rx = tx = None
        try:
            rx = self._open(config.rx_port)
            # Discard pre-open USB/driver backlog; new samples must be live.
            rx.reset_input_buffer()
            with self._lock:
                if epoch != self._epoch or stop.is_set():
                    return
                self.connected = True
            while not stop.is_set():
                if int(getattr(rx, "in_waiting", 0)) > MAX_CHUNK:
                    rx.reset_input_buffer()
                    with self._lock:
                        self.decoder.reset()
                        self.disarm("RX backlog dropped; fresh input and re-arm required")
                chunk = rx.read(MAX_CHUNK)
                with self._lock:
                    if epoch != self._epoch or stop.is_set():
                        return
                    self.feed(chunk)
                    pending = self._pending(self.time_fn())
                if pending is not None:
                    if not config.mock_output and tx is None:
                        tx = self._open(config.tx_port)
                    with self._lock:
                        if epoch != self._epoch or stop.is_set():
                            return
                    self.pump(None if tx is None else tx.write)
                stop.wait(0.005)
        except Exception as exc:
            with self._lock:
                if epoch == self._epoch:
                    self._failed = True
                    self.decoder.reset()
                    self.disarm(f"serial failure; toggle receiver off/on then re-arm: {exc}")
        finally:
            retained = []
            errors = []
            for stream in (tx, rx):
                if stream is not None:
                    try:
                        close_resource(stream)
                    except Exception as exc:
                        retained.append(stream)
                        errors.append(f"{type(exc).__name__}: {exc}")
            with self._lock:
                self._retained_streams = tuple(retained)
                self._close_error = "; ".join(errors)
                if retained:
                    self._failed = True
                    self.disarm("Controller angle close unconfirmed: " + self._close_error)
                else:
                    self._owned_config = None
                self.connected = False

    def wait_closed(self, timeout: float | None = None) -> bool:
        """Worker/CLI only: finish the existing owner and retry retained handles."""
        with self._close_lock:
            worker = self._thread
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout)
            if worker is not None and worker.is_alive():
                return False
            with self._lock:
                # A standalone caller may reconfigure concurrently. Never
                # release the new worker's reservation on behalf of the old.
                if self._thread is not worker:
                    return False
                retained = self._retained_streams
            still_owned = []
            errors = []
            for stream in retained:
                try:
                    close_resource(stream)
                except Exception as exc:
                    still_owned.append(stream)
                    errors.append(f"{type(exc).__name__}: {exc}")
            with self._lock:
                if self._thread is not worker:
                    return False
                self._retained_streams = tuple(still_owned)
                self._close_error = "; ".join(errors)
                if still_owned:
                    self._failed = True
                    self.last_error = "Controller angle close unconfirmed: " + self._close_error
                else:
                    self._owned_config = None
                return not still_owned
