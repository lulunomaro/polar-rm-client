"""UDP 图传后端（对齐 SharkClient udp_bridge + video_decoder 能力）。

- 8 字节 Shark 包头分片重组（复用 FrameAssembler）
- 帧 codec 自动检测：MJPEG(FFD8) / H.265(HEVC NAL)
- MJPEG 帧直接用 OpenCV 解码为 BGR ndarray（可转 QImage）
- H.265 帧保留原始 bytes，若本机有 ffmpeg 则提供按需解码工具
- 统计：帧率/码率/丢帧/编解码后端/输出分辨率（对齐 Shark UDP stats）
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading
import time
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable

from ..transports import UDPVideoReceiver
from ..runtime_paths import RuntimePaths
from .resource_close import close_resource
from .video_source import VideoSourceAdmission, VideoStopStatus


def resolve_ffmpeg_executable() -> str | None:
    """Resolve bundled/system/wheel ffmpeg without downloading at runtime."""
    paths = RuntimePaths.discover()
    candidates = [
        str(paths.ffmpeg_path),
        os.environ.get("POLAR_FFMPEG", "") if paths.source_mode else "",
        (shutil.which("ffmpeg") or "") if paths.source_mode else "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


def _decode_jpeg_bgr(frame: bytes) -> Any:
    """Decode one raw encoded image; callers own envelopes and publication.

    OpenCV requires a uint8 array, not Python bytes. Keep this conversion shared
    by UDP MJPEG and the continuous decoder without sharing their lifecycle,
    timestamp, counter or error-reporting policies.
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    encoded = np.frombuffer(frame, dtype=np.uint8)
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)  # type: ignore[attr-defined]


def detect_frame_codec(frame: bytes) -> str:
    """返回 'mjpeg' / 'h264' / 'h265' / 'unknown'。

    注意：UDP 链路预期为 MJPEG 或 H.265（Shark 编码选项），H.264 识别
    仅用于避免把普通 H.264 误判成 H.265。启发式识别不做严格字节流解析。
    """
    if len(frame) < 4:
        return "unknown"
    # MJPEG：裸 SOI 或 4 字节长度前缀 + SOI
    if frame[0] == 0xFF and frame[1] == 0xD8:
        return "mjpeg"
    if len(frame) >= 6 and frame[4] == 0xFF and frame[5] == 0xD8:
        return "mjpeg"
    for start in (b"\x00\x00\x00\x01", b"\x00\x00\x01"):
        idx = frame.find(start)
        if idx < 0:
            continue
        if idx + len(start) >= len(frame):
            continue  # 起始码在帧尾，无 NAL 头字节，跳过避免越界
        nal = frame[idx + len(start)]
        h264_type = nal & 0x1F
        hevc_type = (nal >> 1) & 0x3F
        second = frame[idx + len(start) + 1] if idx + len(start) + 1 < len(frame) else 0
        temporal_ok = (second & 0x07) in range(1, 8) and ((second >> 3) & 0x1F) == 0
        # 先排除明确的 H.264 参数集/分隔符
        if h264_type in (7, 8, 9):
            return "h264"
        # HEVC 参数集/前缀/IDR/尾随帧：要求 HEVC 双字节头格式成立
        if hevc_type in (32, 33, 34, 35, 39, 40) and temporal_ok:
            return "h265"
        if hevc_type in (19, 20, 21) and temporal_ok:
            return "h265"
        # 兜底：H.264 IDR/非参考帧明确标识
        if h264_type in (1, 5, 6) and not temporal_ok:
            return "h264"
    return "unknown"


def iter_annexb_nal_units(data: bytes) -> list[tuple[int, bytes]]:
    """Return ``(start_offset, nal_payload)`` for complete Annex-B NAL units."""

    starts: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(data) - 2:
        if data[cursor:cursor + 4] == b"\x00\x00\x00\x01":
            starts.append((cursor, 4))
            cursor += 4
        elif data[cursor:cursor + 3] == b"\x00\x00\x01":
            starts.append((cursor, 3))
            cursor += 3
        else:
            cursor += 1
    units: list[tuple[int, bytes]] = []
    for index, (offset, prefix) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        payload = data[offset + prefix:end]
        if payload:
            units.append((offset, payload))
    return units


def has_complete_annexb_recovery(data: bytes, codec: str) -> bool:
    """True when a buffer has full parameter sets followed by a keyframe."""

    normal = "h265" if str(codec).lower() in {"h265", "hevc"} else "h264"
    required = {32, 33, 34} if normal == "h265" else {7, 8}
    present: set[int] = set()
    for _offset, payload in iter_annexb_nal_units(data):
        nal_type = ((payload[0] >> 1) & 0x3F) if normal == "h265" else (payload[0] & 0x1F)
        if nal_type in required:
            present.add(nal_type)
        if nal_type in ({19, 20, 21} if normal == "h265" else {5}):
            return required.issubset(present)
    return False


class AnnexBRecoveryGate:
    """Bounded parameter-set/keyframe recovery for H.264 and HEVC streams."""

    # Limit applies to the *total* retained parameter-set cache, not to each
    # individual NAL. A malicious/corrupt stream therefore cannot retain
    # three independent 64 KiB HEVC headers indefinitely.
    PARAMETER_LIMIT = 64 * 1024

    def __init__(self, codec: str) -> None:
        self.codec = "h265" if str(codec).lower() in {"h265", "hevc"} else "h264"
        self.parameters: dict[int, bytes] = {}
        self.blocked = True
        self._tail = b""
        self._pending_parameter_type: int | None = None
        self._pending_parameter = bytearray()

    @property
    def required(self) -> frozenset[int]:
        return frozenset({32, 33, 34}) if self.codec == "h265" else frozenset({7, 8})

    def reset(self) -> None:
        self.blocked = True
        self._tail = b""
        self._pending_parameter_type = None
        self._pending_parameter.clear()
        self.parameters.clear()

    def feed(self, chunk: bytes) -> bytes | None:
        if not self.blocked:
            return bytes(chunk)

        data = self._tail + bytes(chunk)
        self._tail = b""
        while data:
            if self._pending_parameter_type is not None:
                # A following start code may begin in the previous parameter's
                # last three bytes. Search across that boundary, not only in
                # the newly received transport block.
                tail_len = min(3, len(self._pending_parameter))
                boundary = bytes(self._pending_parameter[-tail_len:]) + data
                next_start = self._find_start(boundary)
                if next_start is None:
                    self._append_pending_parameter(data)
                    return None
                offset, _prefix = next_start
                if offset < tail_len:
                    del self._pending_parameter[-(tail_len - offset):]
                else:
                    self._append_pending_parameter(data[:offset - tail_len])
                self._commit_pending_parameter()
                data = boundary[offset:]
                continue

            start = self._find_start(data)
            if start is None:
                # Only retain enough arbitrary stream bytes to bridge a split
                # four-byte Annex-B start code. Parameter NALs have their own
                # bounded accumulator below.
                self._tail = data[-3:]
                return None
            offset, prefix_len = start
            if offset:
                data = data[offset:]
                offset = 0
            header_len = 2 if self.codec == "h265" else 1
            if len(data) < prefix_len + header_len:
                self._tail = data[-6:]
                return None
            first = data[prefix_len]
            nal_type = ((first >> 1) & 0x3F) if self.codec == "h265" else (first & 0x1F)
            next_start = self._find_start(data, prefix_len + header_len)
            if nal_type in self.required:
                if next_start is None:
                    self._pending_parameter_type = nal_type
                    self._pending_parameter = bytearray(data)
                    self._enforce_pending_limit()
                    return None
                next_offset, _next_prefix = next_start
                self._store_parameter(nal_type, bytes(data[:next_offset]))
                data = data[next_offset:]
                continue

            is_key = nal_type in ({19, 20, 21} if self.codec == "h265" else {5})
            if is_key and self.required.issubset(self.parameters):
                self.blocked = False
                self._tail = b""
                prefix = b"".join(self.parameters[key] for key in sorted(self.required))
                return prefix + data
            if next_start is None:
                self._tail = data[-3:]
                return None
            data = data[next_start[0]:]
        return None

    @staticmethod
    def _find_start(data: bytes, start: int = 0) -> tuple[int, int] | None:
        three = data.find(b"\x00\x00\x01", max(0, int(start)))
        four = data.find(b"\x00\x00\x00\x01", max(0, int(start)))
        candidates = [(offset, prefix) for offset, prefix in ((three, 3), (four, 4)) if offset >= 0]
        if not candidates:
            return None
        # At a four-byte start, the three-byte pattern also starts one byte
        # later. Prefer the earliest offset and longest prefix.
        return min(candidates, key=lambda item: (item[0], -item[1]))

    def _append_pending_parameter(self, data: bytes) -> None:
        self._pending_parameter.extend(data)
        self._enforce_pending_limit()

    def _enforce_pending_limit(self) -> None:
        retained = sum(len(value) for value in self.parameters.values())
        if retained + len(self._pending_parameter) > self.PARAMETER_LIMIT:
            self._pending_parameter_type = None
            self._pending_parameter.clear()

    def _commit_pending_parameter(self) -> None:
        nal_type = self._pending_parameter_type
        parameter = bytes(self._pending_parameter)
        self._pending_parameter_type = None
        self._pending_parameter.clear()
        if nal_type is not None:
            self._store_parameter(nal_type, parameter)

    def _store_parameter(self, nal_type: int, parameter: bytes) -> None:
        other_bytes = sum(
            len(value) for key, value in self.parameters.items() if key != nal_type
        )
        if parameter and len(parameter) + other_bytes <= self.PARAMETER_LIMIT:
            self.parameters[nal_type] = parameter


class RmVideoBackend(UDPVideoReceiver):
    """SharkClient 风格 UDP 图传后端（含 codec 检测/MJPEG 解码/统计）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 3334) -> None:
        super().__init__(host=host, port=port)
        self.codec: str = "unknown"
        self.fps: float = 0.0
        self.bitrate: float = 0.0  # bytes/s
        self.dropped_frames: int = 0
        self.decoder_backend: str = "none"
        self.output_width: int = 0
        self.output_height: int = 0
        self.mjpeg_decoded_frames: int = 0
        self.latest_bgr: Any = None  # MJPEG 解码后的 BGR ndarray
        # Timestamp of the *decoded* MJPEG frame.  ``last_frame_at`` tracks
        # encoded UDP traffic and therefore must not be used to refresh an old
        # image when H.265/unknown packets arrive after the last MJPEG frame.
        self.latest_bgr_at = 0.0
        self.latest_h265: bytes = b""
        self.last_frame_at = 0.0
        self.external_on_frame: Callable[[bytes], None] | None = None
        self.decode_h265 = True  # 有 ffmpeg 时自动开持久解码器
        self.ffmpeg_executable = resolve_ffmpeg_executable()
        self.ffmpeg_available: bool = self.ffmpeg_executable is not None
        # Always construct the decoder. Its start() re-resolves ffmpeg so a
        # bundled binary becoming available before runtime is picked up.
        self.h265_decoder: ContinuousMjpegDecoder | None = ContinuousMjpegDecoder("h265")
        self.h265_recovery = AnnexBRecoveryGate("h265")
        self._udp_sequence_discontinuities = 0
        self._started_at = 0.0
        self._frame_times: list[float] = []
        self._bytes_this_second = 0
        self._last_stats_at = 0.0
        self._lock = threading.Lock()
        self._video_generation = 0
        self._h265_generation = 0
        self._h265_restart_lock = threading.Lock()
        self._h265_restart_thread: threading.Thread | None = None
        self._h265_next_restart_at = 0.0
        self._h265_restart_backoff_s = 0.5
        self.h265_decoder_restarts = 0
        self.event_hook: Callable[..., None] | None = None

    def _emit_event(self, event: str, **payload: Any) -> None:
        hook = self.event_hook
        if hook is None:
            return
        try:
            hook(event, source="udp_hevc", **payload)
        except Exception:
            # Diagnostics must never delay or terminate the UDP worker.
            pass

    # ------------------------------------------------------------------
    def start(self) -> None:
        # A bind failure leaves UDPVideoReceiver.running false. Do not reset
        # counters/decoder state on every retry while an existing receiver is
        # already alive.
        if self.running or not self.cleanup_complete:
            return
        self.ffmpeg_executable = resolve_ffmpeg_executable()
        self.ffmpeg_available = self.ffmpeg_executable is not None
        self._started_at = time.perf_counter()
        self._last_stats_at = time.perf_counter()
        with self._lock:
            self._video_generation += 1
            self._h265_generation += 1
            generation = self._video_generation
            self.on_frame = lambda frame: self._on_assembled_frame(frame, generation)
            self._frame_times.clear()
            self._bytes_this_second = 0
            self.fps = 0.0
            self.bitrate = 0.0
            self.codec = "unknown"
            self.mjpeg_decoded_frames = 0
            self.latest_bgr = None
            self.latest_bgr_at = 0.0
            self.latest_h265 = b""
            self.last_frame_at = 0.0
            self.h265_decoder_restarts = 0
            self.h265_recovery.reset()
            self.dropped_frames = 0
            self._h265_next_restart_at = 0.0
            self._h265_restart_backoff_s = 0.5
            self._assembler.clear()
            self._udp_sequence_discontinuities = 0
        super().start()

    def _on_assembled_frame(self, frame: bytes, generation: int | None = None) -> None:
        now = time.perf_counter()
        codec = "unknown"
        with self._lock:
            if generation is not None and generation != self._video_generation:
                return
            discontinuities = self._assembler.sequence_discontinuities
            if discontinuities != self._udp_sequence_discontinuities:
                self._udp_sequence_discontinuities = discontinuities
                self.h265_recovery.reset()
            codec = detect_frame_codec(frame)
            # 官方 UDP 源为连续 HEVC；VPS/SPS 只在关键帧出现。锁定后，普通
            # type 0..31 P/B slices 不能被当成 unknown 丢弃。
            if codec == "unknown" and self.codec == "h265":
                codec = "h265"
            self.codec = codec
            self.last_frame_at = now
            self._frame_times.append(now)
            if len(self._frame_times) > 120:
                self._frame_times.pop(0)
            self._bytes_this_second += len(frame)
            if now - self._last_stats_at >= 1.0:
                span = now - self._last_stats_at
                self.bitrate = self._bytes_this_second / span
                self._bytes_this_second = 0
                self._last_stats_at = now
                if len(self._frame_times) >= 2:
                    window = self._frame_times[-min(60, len(self._frame_times)):]
                    if len(window) >= 2:
                        self.fps = (len(window) - 1) / max(1e-6, window[-1] - window[0])
            if codec == "unknown":
                self.dropped_frames += 1
        # 解码放到锁外，避免 cv2/ffmpeg 阻塞 UDP 接收线程与 stats()
        if codec == "mjpeg":
            self._decode_mjpeg(frame, generation)
        elif codec == "h265":
            with self._lock:
                if generation is not None and generation != self._video_generation:
                    return
                self.latest_h265 = frame
                decoder = self.h265_decoder if self.decode_h265 else None
                decoder_generation = getattr(decoder, "lifecycle_generation", None)
            if decoder is not None:
                # Preserve lazy first-block startup, including a one-GOP source.
                # start() never tears down a failed incarnation; subsequent
                # recovery always belongs to the dedicated worker below.
                if not decoder.available and decoder.restart_count == 0 and not decoder.last_error:
                    decoder.start(expected_generation=decoder_generation)
                # Process teardown/recovery always runs outside the receiver.
                # Gate state is serialized with reset at lifecycle boundaries.
                with self._lock:
                    if generation is not None and generation != self._video_generation:
                        return
                    if not self.decode_h265:
                        return
                    if decoder.available:
                        payload = self.h265_recovery.feed(frame)
                        queued = payload is not None and decoder.write(payload)
                        self.decoder_backend = "ffmpeg-mjpeg"
                        if payload is not None and not queued:
                            self.dropped_frames += 1
                            self.h265_recovery.reset()
                    else:
                        self.h265_recovery.reset()
                if not decoder.available:
                    self._request_h265_restart(generation=generation)
        if self.external_on_frame is not None:
            try:
                self.external_on_frame(frame)
            except Exception:
                pass

    def _decode_mjpeg(self, frame: bytes, generation: int | None = None) -> None:
        try:
            # Match only the exact optional envelope already recognized by
            # detect_frame_codec; raw SOI takes precedence over a prefix.
            payload = frame
            if not frame.startswith(b"\xff\xd8") and frame[4:6] == b"\xff\xd8":
                payload = frame[4:]
            bgr = _decode_jpeg_bgr(payload)
            if bgr is not None:
                h, w = bgr.shape[:2]
                with self._lock:
                    if generation is not None and generation != self._video_generation:
                        return
                    self.latest_bgr = bgr
                    self.latest_bgr_at = time.perf_counter()
                    self.mjpeg_decoded_frames += 1
                    self.decoder_backend = "cv2"
                    self.output_width, self.output_height = w, h
        except Exception:
            with self._lock:
                if generation is None or generation == self._video_generation:
                    self.decoder_backend = "none"

    def _ffmpeg_decode_hevc(self, frame: bytes) -> Any:
        """用系统 ffmpeg 把单帧 HEVC 解成 BGR24 ndarray；失败返回 None。"""
        try:
            proc = subprocess.run(
                [
                    self.ffmpeg_executable or "ffmpeg", "-v", "error", "-f", "hevc", "-i", "pipe:0",
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
                ],
                input=frame, capture_output=True, timeout=5.0,
            )
            if proc.returncode != 0 or not proc.stdout:
                return None
            import cv2  # type: ignore
            # 需要知道宽高才能 reshape；从 SPS 解析宽高成本高，这里先尝试常见 1280x720
            # 失败则放弃，保持 raw bytes 由上层处理。
            data = proc.stdout
            for (w, h) in ((1280, 720), (960, 540), (1920, 1080), (640, 480)):
                if len(data) == w * h * 3:
                    import numpy as np  # type: ignore
                    return np.frombuffer(data, dtype=np.uint8).reshape((h, w, 3))
            return None
        except Exception:
            return None

    def latest_video_frame(self, timeout_s: float = 0.5) -> tuple[Any, float]:
        """Return the freshest decoded UDP frame and its monotonic timestamp."""

        deadline_age = max(0.0, float(timeout_s))
        now = time.perf_counter()
        h265_frame: Any = None
        h265_at = 0.0
        if self.decode_h265 and self.h265_decoder is not None and self.h265_decoder.available:
            h265_frame, h265_at = self.h265_decoder.latest_with_timestamp(deadline_age)

        with self._lock:
            mjpeg_frame = self.latest_bgr
            mjpeg_at = float(self.latest_bgr_at)
        if mjpeg_frame is None or mjpeg_at <= 0.0 or now - mjpeg_at > deadline_age:
            mjpeg_frame, mjpeg_at = None, 0.0

        if h265_frame is not None and h265_at >= mjpeg_at:
            return h265_frame, h265_at
        if mjpeg_frame is not None:
            return mjpeg_frame, mjpeg_at
        return None, 0.0

    def clear_stale_decoded_frame(self, timeout_s: float = 0.5) -> bool:
        """Release stale decoded images so callers cannot render a frozen feed."""

        cutoff = max(0.0, float(timeout_s))
        now = time.perf_counter()
        cleared = False
        with self._lock:
            if self.latest_bgr_at > 0.0 and now - self.latest_bgr_at > cutoff:
                self.latest_bgr = None
                self.latest_bgr_at = 0.0
                cleared = True
        # Keep the decoder timestamp until the 2 s health monitor observes it
        # and performs an SPS/keyframe-gated restart. UI callers already reject
        # it by age, so retaining this one frame cannot cause a visual freeze.
        return cleared

    def decoded_frame_fresh(self, timeout_s: float = 2.0) -> bool:
        """Cheap HUD connectivity probe without building the full stats tree."""

        h265_at = self.h265_decoder.latest_timestamp() if self.h265_decoder else 0.0
        with self._lock:
            decoded_at = max(float(self.latest_bgr_at), float(h265_at))
        return bool(
            decoded_at > 0.0
            and time.perf_counter() - decoded_at <= max(0.0, float(timeout_s))
        )

    def _request_h265_restart(self, *, generation: int | None = None) -> None:
        # This lock only protects scheduling, never process teardown or joins.
        with self._h265_restart_lock:
            if self._h265_restart_thread is not None and self._h265_restart_thread.is_alive():
                return
            with self._lock:
                decoder = self.h265_decoder
                now = time.perf_counter()
                if (
                    decoder is None or not self.running or not self.decode_h265
                    or now < self._h265_next_restart_at
                    or (generation is not None and generation != self._video_generation)
                ):
                    return
                epoch = self._h265_generation
                decoder_generation = getattr(decoder, "lifecycle_generation", None)
                self.h265_recovery.reset()
                self._h265_next_restart_at = now + self._h265_restart_backoff_s

            def restart_worker() -> None:
                try:
                    restarted = decoder.restart(clear_latest=True, expected_generation=decoder_generation)
                except Exception:
                    restarted = False
                with self._lock:
                    if epoch != self._h265_generation or not self.running or not self.decode_h265:
                        return
                    self.h265_recovery.reset()
                    if restarted:
                        self.h265_decoder_restarts += 1
                        self.decoder_backend = "ffmpeg-mjpeg"
                        self._h265_restart_backoff_s = 0.5
                    else:
                        self._h265_restart_backoff_s = min(5.0, self._h265_restart_backoff_s * 2.0)
                    self._h265_next_restart_at = time.perf_counter() + self._h265_restart_backoff_s
                    restart_count = self.h265_decoder_restarts
                    backoff = self._h265_restart_backoff_s
                self._emit_event(
                    "decoder_restart", restarted=restarted, restart_count=restart_count,
                    backoff_s=backoff, health=(decoder.health() if hasattr(decoder, "health") else {}),
                )

            worker = threading.Thread(target=restart_worker, name="h265-decoder-restart", daemon=True)
            self._h265_restart_thread = worker
            try:
                worker.start()
            except Exception as exc:
                # A real thread that already started (even if it finished)
                # remains a joinable owner. Never-admitted objects do not.
                if worker.ident is None:
                    self._h265_restart_thread = None
                with self._lock:
                    self.last_error = f"HEVC recovery admission failed: {type(exc).__name__}: {exc}"
                    self._h265_next_restart_at = time.perf_counter() + self._h265_restart_backoff_s
                    self._h265_restart_backoff_s = min(5.0, self._h265_restart_backoff_s * 2.0)
                raise

    def request_stop(self) -> None:
        """Revoke all packet/decode state before independent close admissions."""
        with self._lock:
            self._video_generation += 1
            self._h265_generation += 1
            self.decode_h265 = False
            self.latest_bgr = None
            self.latest_bgr_at = 0.0
        admission = VideoSourceAdmission()
        admission.attempt("HEVC recovery reset", self.h265_recovery.reset)
        admission.attempt("UDP stop", super().request_stop)
        if self.h265_decoder is not None:
            admission.attempt("HEVC stop", self.h265_decoder.request_stop)
        admission.raise_if_failed()

    @property
    def stop_pending(self) -> bool:
        return (super().stop_pending
                or (self.h265_decoder is not None and self.h265_decoder.stop_pending))

    @property
    def stop_status(self) -> VideoStopStatus:
        udp = super().stop_status
        decoder = self.h265_decoder.stop_status if self.h265_decoder is not None else VideoStopStatus()
        recovery = self._h265_restart_thread
        inflight = udp.inflight or decoder.inflight or (recovery is not None and recovery.is_alive())
        errors = tuple(f"UDP: {error}" for error in udp.errors) + tuple(
            f"HEVC: {error}" for error in decoder.errors)
        return VideoStopStatus(bool(udp.pending or decoder.pending or inflight), inflight, errors)

    def stop(self) -> None:
        with self._lock:
            self._video_generation += 1
            self._h265_generation += 1
        # Stop packet production before detaching decoder pipes. No restart
        # lock is held while waiting; stop invalidates the decoder generation.
        super().stop()
        if self.h265_decoder is not None:
            self.h265_decoder.stop()
        with self._lock:
            self.h265_recovery.reset()

    @property
    def cleanup_complete(self) -> bool:
        return (super().cleanup_complete
                and (self.h265_decoder is None or self.h265_decoder.cleanup_complete))

    def wait_closed(self, timeout: float | None = None) -> bool:
        super().wait_closed(timeout)
        decoder_closed = self.h265_decoder is None or self.h265_decoder.wait_closed()
        if not decoder_closed:
            self.last_error = self.h265_decoder.last_error or "HEVC decoder close unconfirmed"
        return super().cleanup_complete and decoder_closed

    def set_decode_enabled(self, enabled: bool) -> None:
        """Synchronous compatibility: select HEVC and close disabled native state."""
        self._set_decode_enabled(enabled, wait=True)

    def request_decode_enabled(self, enabled: bool) -> None:
        """UI-safe HEVC selection; disabling admits native close off-thread."""
        self._set_decode_enabled(enabled, wait=False)

    def _set_decode_enabled(self, enabled: bool, *, wait: bool) -> None:
        enabled = bool(enabled)
        with self._lock:
            if enabled == self.decode_h265:
                return
            self.decode_h265 = enabled
            self._h265_generation += 1
            self.h265_recovery.reset()
        if not enabled and self.h265_decoder is not None:
            if wait:
                self.h265_decoder.stop()
            else:
                self.h265_decoder.request_stop()

    def stats(self) -> dict[str, Any]:
        h265_at = self.h265_decoder.latest_timestamp() if self.h265_decoder else 0.0
        h265_frames = self.h265_decoder.frames_decoded if self.h265_decoder else 0
        with self._lock:
            decoded_at = max(float(self.latest_bgr_at), float(h265_at))
            decoded_connected = bool(
                decoded_at > 0.0 and time.perf_counter() - decoded_at <= 2.0
            )
            connected = (
                self.frames_received > 0
                and self.last_frame_at > 0
                and time.perf_counter() - self.last_frame_at <= 2.0
            )
            return {
                "connected": connected,
                "host": self.host,
                "port": self.port,
                "packets_received": self.packets_received,
                "frames_received": self.frames_received,
                "dropped_frames": self.dropped_frames + self._assembler.dropped_count,
                "invalid_fragments": self._assembler.invalid_count,
                "stale_fragments": self._assembler.stale_packet_count,
                "sequence_discontinuities": self._assembler.sequence_discontinuities,
                "codec": self.codec,
                "fps": round(self.fps, 1),
                "bitrate": round(self.bitrate, 1),
                "decoder_backend": self.decoder_backend,
                "output_width": self.output_width,
                "output_height": self.output_height,
                "decoded_connected": decoded_connected,
                "decoded_frames": self.mjpeg_decoded_frames + h265_frames,
                "continuous_decoded_seconds": (
                    self.h265_decoder.continuous_decoded_seconds
                    if self.h265_decoder and self.codec == "h265"
                    else max(0.0, decoded_at - self._started_at) if decoded_at > 0.0 else 0.0
                ),
                "decoded_frame_at": decoded_at,
                "h265_decoder_ready": bool(self.h265_decoder and self.h265_decoder.available),
                "h265_decoded_frames": h265_frames,
                "h265_input_queue": self.h265_decoder.input_queue_depth() if self.h265_decoder else 0,
                "h265_input_dropped": self.h265_decoder.input_chunks_dropped if self.h265_decoder else 0,
                "h265_decoder_restarts": self.h265_decoder_restarts,
                "h265_recovery_blocked": self.h265_recovery.blocked,
                "h265_decoder_health": self.h265_decoder.health() if self.h265_decoder else {},
                "last_error": self.last_error,
            }


def consume_mjpeg_stream(buf: bytearray, on_frame: Callable[[bytes], None]) -> None:
    """从字节流中切出完整 MJPEG 帧（SOI/EOI）并回调；保留可能跨块的尾部。"""
    while True:
        start = buf.find(b"\xff\xd8")
        if start < 0:
            # 保留末尾 3 字节，防止 SOI 标志跨块
            if len(buf) > 3:
                del buf[:-3]
            return
        end = buf.find(b"\xff\xd9", start + 2)
        if end < 0:
            del buf[:start]
            return
        frame = bytes(buf[start:end + 2])
        del buf[:end + 2]
        on_frame(frame)


@dataclass
class _DecoderSession:
    """Process, queue and cancellation belong to one decoder incarnation."""

    proc: Any
    input_queue: Queue[bytes]
    stopped: threading.Event = field(default_factory=threading.Event)
    threads: list[threading.Thread] = field(default_factory=list)
    close_lock: threading.Lock = field(default_factory=threading.Lock)
    close_error: str = ""
    closed: bool = False


class ContinuousMjpegDecoder:
    """常驻 ffmpeg → MJPEG 管道的持续 H.264/H.265 解码器。

    与 SharkClient "H.265: 后端解码后以 MJPEG 本机流显示" 同思路：
    ffmpeg 把连续码流解码成自包含 MJPEG 帧（带 SOI/EOI，自带宽高），
    前端按 FFD8/FFD9 切帧后用 cv2.imdecode 得到 BGR 帧，避免 rawvideo
    需要预先知道宽高，也避免每帧 spawn 一个 ffmpeg 进程。
    """

    INPUT_QUEUE_CHUNKS = 256
    STDERR_LIMIT = 32 * 1024
    FIRST_FRAME_TIMEOUT_S = 3.0
    STALE_TIMEOUT_S = 2.0

    def __init__(self, codec: str = "h265", quality: int = 3) -> None:
        self.codec = codec  # "h264" | "h265"
        self.quality = int(quality)
        self.available = False
        self.last_error = ""
        self.dependency_error = ""
        self.frames_decoded = 0
        self._proc: Any = None
        self._reader: threading.Thread | None = None
        self._writer: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._generation = 0
        self._session: _DecoderSession | None = None
        self._retired_sessions: list[_DecoderSession] = []
        self._stop_worker: threading.Thread | None = None
        # Decoder stdin is serviced off the UDP/UI thread.  A bounded queue
        # prevents a stalled ffmpeg process from freezing 144 Hz rendering or
        # growing memory without limit.
        self._input_queue: Queue[bytes] = Queue(maxsize=self.INPUT_QUEUE_CHUNKS)
        self.input_chunks_dropped = 0
        self.input_chunks_written = 0
        self.input_bytes_written = 0
        self._latest: Any = None
        self._latest_at = 0.0
        self._frame_w = 0
        self._frame_h = 0
        self.output_bytes_read = 0
        self.started_at = 0.0
        self.last_exit_code: int | None = None
        self.restart_count = 0
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_bytes = 0
        self._next_restart_at = 0.0
        self._restart_backoff_s = 0.5
        self.first_frame_at = 0.0
        self._process_frames_decoded = 0
        self._decoded_window_started_at = 0.0
        self.continuous_decoded_seconds = 0.0

    @property
    def lifecycle_generation(self) -> int:
        with self._lock:
            return self._generation

    def start(self, *, expected_generation: int | None = None) -> bool:
        # Check dependencies before spawning or claiming readiness. Keep this
        # fast failure path usable by headless dependency diagnostics as well.
        try:
            import cv2  # type: ignore  # noqa: F401
            import numpy  # type: ignore  # noqa: F401
        except Exception as exc:
            self.dependency_error = f"video decode dependency unavailable: {type(exc).__name__}: {exc}"
            self.last_error = self.dependency_error
            self.available = False
            return False
        # A failed incarnation needs restart(), which runs on a recovery
        # worker. start() must not reap stale workers on a UI/receive callback.
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return False
            if (self._retired_sessions or self._stop_in_progress_locked()
                    or (self._session is not None and not self._running)):
                return False
            generation = self._generation
        # A concurrent recovery owns process creation. Never make a receive or
        # UI caller wait behind its pipe teardown.
        if not self._lifecycle_lock.acquire(blocking=False):
            return False
        try:
            return self._start_generation(generation)
        finally:
            self._lifecycle_lock.release()

    def _start_generation(self, generation: int) -> bool:
        with self._lock:
            if (generation != self._generation or self._retired_sessions
                    or self._stop_in_progress_locked()):
                return False
            if self._running:
                return True
            old = self._detach_session_locked(clear_latest=False)
        if not self._close_retired_session(old):
            return False
        with self._lock:
            if generation != self._generation:
                return False
        ffmpeg = resolve_ffmpeg_executable()
        if not ffmpeg:
            with self._lock:
                if generation == self._generation:
                    self.last_error = "ffmpeg 不可用"
            return False
        session = None
        try:
            demuxer = "hevc" if self.codec.lower() in {"h265", "hevc"} else "h264"
            proc = subprocess.Popen(
                [
                    ffmpeg, "-v", "error",
                    "-probesize", "32", "-analyzeduration", "0",
                    "-f", demuxer, "-i", "pipe:0",
                    "-flush_packets", "1",
                    "-c:v", "mjpeg", "-q:v", str(self.quality),
                    "-f", "image2pipe", "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            session = _DecoderSession(proc, Queue(maxsize=self.INPUT_QUEUE_CHUNKS))
            session.threads = [
                threading.Thread(
                    target=self._run_session_worker, args=(target, session, pipe),
                    name=f"{name}-{self.codec}", daemon=True,
                )
                for target, name, pipe in (
                    (self._write_loop, "mjpeg-in", "stdin"),
                    (self._read_loop, "mjpeg-dec", "stdout"),
                    (self._stderr_loop, "mjpeg-err", "stderr"),
                )
            ]
            with self._lock:
                if generation != self._generation:
                    session.stopped.set()
                else:
                    self._session = session
                    self._proc = proc
                    self._input_queue = session.input_queue
                    self._writer, self._reader, self._stderr_reader = session.threads
                    self._running = True
                    self.available = True
                    self.started_at = time.perf_counter()
                    self.first_frame_at = 0.0
                    self._process_frames_decoded = 0
                    self.last_exit_code = None
                    self.dependency_error = ""
                    self.last_error = ""
                    self._stderr_chunks.clear()
                    self._stderr_bytes = 0
                    # Publish and start atomically with respect to stop().
                    for worker in session.threads:
                        worker.start()
            if session.stopped.is_set():
                self._close_retired_session(session)
                return False
            return True
        except Exception as exc:  # pragma: no cover - platform process failure
            with self._lock:
                if generation == self._generation:
                    self.last_error = str(exc)
                    if self._session is session:
                        self._detach_session_locked(clear_latest=True)
            self._close_retired_session(session)
            return False

    def write(self, data: bytes, *, expected_generation: int | None = None) -> bool:
        """Queue bytes to the captured process without blocking on its stdin."""

        payload = bytes(data)
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return False
            if self._proc is None or not self._running:
                return False
            if not payload:
                return True
            try:
                self._input_queue.put_nowait(payload)
                return True
            except Full:
                self.input_chunks_dropped += 1
                self.last_error = "decoder input queue overflow; waiting for stream recovery"
                return False

    def _session_current_locked(self, session: _DecoderSession) -> bool:
        return self._session is session and not session.stopped.is_set()

    def _session_failed(self, session: _DecoderSession, message: str = "") -> None:
        with self._lock:
            if not self._session_current_locked(session):
                return
            self.available = False
            self._running = False
            session.stopped.set()
            self.last_exit_code = session.proc.poll()
            if message:
                self.last_error = message
            elif self.last_exit_code not in (None, 0) and not self.last_error:
                self.last_error = f"ffmpeg exited with code {self.last_exit_code}"

    def _run_session_worker(self, target: Callable[[_DecoderSession], None],
                            session: _DecoderSession, pipe_name: str) -> None:
        try:
            target(session)
        finally:
            # If OS I/O outlives the bounded stop deadline, its owner still
            # closes the captured handle when it eventually returns.
            pipe = getattr(session.proc, pipe_name, None)
            if pipe is not None:
                try:
                    close_resource(pipe)
                except Exception as exc:
                    session.close_error = f"Decoder pipe close unconfirmed: {type(exc).__name__}: {exc}"

    def _write_loop(self, session: _DecoderSession) -> None:
        proc = session.proc
        while not session.stopped.is_set():
            try:
                payload = session.input_queue.get(timeout=0.10)
            except Empty:
                continue
            if session.stopped.is_set() or proc.stdin is None:
                break
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
                with self._lock:
                    if self._session_current_locked(session):
                        self.input_chunks_written += 1
                        self.input_bytes_written += len(payload)
            except Exception as exc:
                self._session_failed(session, str(exc))
                break

    def _read_loop(self, session: _DecoderSession) -> None:
        proc = session.proc
        buf = bytearray()
        while not session.stopped.is_set() and proc.stdout is not None:
            try:
                # os.read returns available bytes instead of waiting for a
                # full buffered read, preserving low-latency JPEG delivery.
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk or session.stopped.is_set():
                    break
                with self._lock:
                    if not self._session_current_locked(session):
                        return
                    self.output_bytes_read += len(chunk)
                buf.extend(chunk)
                consume_mjpeg_stream(buf, lambda frame: self._decode_frame(frame, session))
            except Exception as exc:
                self._session_failed(session, f"decoder reader failed: {type(exc).__name__}: {exc}")
                return
        self._session_failed(session)

    def _stderr_loop(self, session: _DecoderSession) -> None:
        proc = session.proc
        if proc.stderr is None:
            return
        while not session.stopped.is_set():
            try:
                chunk = os.read(proc.stderr.fileno(), 4096)
            except OSError:
                break
            if not chunk:
                break
            with self._lock:
                if not self._session_current_locked(session):
                    return
                self._stderr_chunks.append(chunk)
                self._stderr_bytes += len(chunk)
                while self._stderr_bytes > self.STDERR_LIMIT and self._stderr_chunks:
                    removed = self._stderr_chunks.popleft()
                    self._stderr_bytes -= len(removed)
                diagnostic = b"".join(self._stderr_chunks).decode("utf-8", "replace").strip()
                if diagnostic:
                    self.last_error = diagnostic[-self.STDERR_LIMIT:]

    def _decode_frame(self, frame: bytes, session: _DecoderSession | None = None) -> None:
        try:
            bgr = _decode_jpeg_bgr(frame)
            if bgr is None:
                return
            with self._lock:
                if session is not None and not self._session_current_locked(session):
                    return
                self._latest = bgr
                self._latest_at = time.perf_counter()
                if self.first_frame_at <= 0.0:
                    self.first_frame_at = self._latest_at
                    self._decoded_window_started_at = self._latest_at
                if self._decoded_window_started_at > 0.0:
                    self.continuous_decoded_seconds = max(
                        0.0, self._latest_at - self._decoded_window_started_at
                    )
                h, w = bgr.shape[:2]
                self._frame_w, self._frame_h = w, h
                self.frames_decoded += 1
                self._process_frames_decoded += 1
        except Exception as exc:
            with self._lock:
                if session is None or self._session_current_locked(session):
                    self.last_error = f"frame decode failed: {type(exc).__name__}: {exc}"

    def latest(self, timeout_s: float = 0.5) -> Any:
        frame, _timestamp = self.latest_with_timestamp(timeout_s)
        return frame

    def latest_with_timestamp(self, timeout_s: float = 0.5) -> tuple[Any, float]:
        """Return a fresh decoded frame and its monotonic decode timestamp."""

        now = time.perf_counter()
        with self._lock:
            if (
                self._latest is None
                or self._latest_at <= 0.0
                or now - self._latest_at > max(0.0, float(timeout_s))
            ):
                return None, 0.0
            return self._latest, float(self._latest_at)

    def restart(self, *, clear_latest: bool = True,
                expected_generation: int | None = None) -> bool:
        """Restart on a worker; stop() invalidates even an in-flight spawn."""

        if not self._lifecycle_lock.acquire(blocking=False):
            return False
        try:
            now = time.perf_counter()
            with self._lock:
                generation = self._generation
                if self._stop_in_progress_locked():
                    return False
                if expected_generation is not None and expected_generation != generation:
                    return False
                if now < self._next_restart_at:
                    return False
                session = self._detach_session_locked(clear_latest=clear_latest)
                self.restart_count += 1
            self._close_retired_session(session)
            with self._lock:
                retired = tuple(self._retired_sessions)
            for owner in retired:
                self._close_retired_session(owner)
            # The start helper must not reacquire the lifecycle lock. A stop
            # during teardown/spawn advances the generation and wins.
            try:
                import cv2  # type: ignore  # noqa: F401
                import numpy  # type: ignore  # noqa: F401
            except Exception as exc:
                with self._lock:
                    if generation == self._generation:
                        self.dependency_error = f"video decode dependency unavailable: {type(exc).__name__}: {exc}"
                        self.last_error = self.dependency_error
                started = False
            else:
                started = self._start_generation(generation)
            with self._lock:
                if generation != self._generation:
                    return False
                if started:
                    self._restart_backoff_s = 0.5
                    self._next_restart_at = 0.0
                else:
                    self._next_restart_at = time.perf_counter() + self._restart_backoff_s
                    self._restart_backoff_s = min(5.0, self._restart_backoff_s * 2.0)
            return started
        finally:
            self._lifecycle_lock.release()

    def health(self, now: float | None = None) -> dict[str, Any]:
        current = time.perf_counter() if now is None else float(now)
        latest = self.latest_timestamp()
        return {
            "available": self.available,
            "dependency_error": self.dependency_error,
            "first_frame_timeout": bool(
                self._running and self._process_frames_decoded == 0
                and self.started_at > 0.0 and current - self.started_at > self.FIRST_FRAME_TIMEOUT_S
            ),
            "stale": bool(latest > 0.0 and current - latest > self.STALE_TIMEOUT_S),
            "exit_code": self.last_exit_code,
            "restart_count": self.restart_count,
            "first_frame_latency_s": (
                self.first_frame_at - self.started_at
                if self.first_frame_at > 0.0 and self.started_at > 0.0
                else None
            ),
            "stderr": self.stderr_tail(),
        }

    def stderr_tail(self) -> str:
        with self._lock:
            return b"".join(self._stderr_chunks).decode("utf-8", "replace")[-self.STDERR_LIMIT:]

    def frame_size(self) -> tuple[int, int]:
        with self._lock:
            return self._frame_w, self._frame_h

    def latest_timestamp(self) -> float:
        """Return the monotonic timestamp of the last decoded frame."""

        with self._lock:
            return float(self._latest_at)

    def input_queue_depth(self) -> int:
        return self._input_queue.qsize()

    def _drain_input_queue(self) -> None:
        while True:
            try:
                self._input_queue.get_nowait()
            except Empty:
                return

    def _detach_session_locked(self, *, clear_latest: bool) -> _DecoderSession | None:
        session = self._session
        if session is not None:
            session.stopped.set()
            if not any(owner is session for owner in self._retired_sessions):
                self._retired_sessions.append(session)
        self._session = None
        self._proc = None
        self._running = False
        self.available = False
        self._writer = self._reader = self._stderr_reader = None
        # A new queue prevents an old blocked writer consuming new input.
        self._input_queue = Queue(maxsize=self.INPUT_QUEUE_CHUNKS)
        if clear_latest:
            self._latest = None
            self._latest_at = 0.0
            self._frame_w = self._frame_h = 0
            self.first_frame_at = 0.0
            self._process_frames_decoded = 0
            self._decoded_window_started_at = 0.0
            self.continuous_decoded_seconds = 0.0
        return session

    @staticmethod
    def _close_session(session: _DecoderSession | None, *,
                       worker_timeout: float | None = 1.0) -> bool:
        if session is None:
            return True
        with session.close_lock:
            if session.closed:
                return True
            session.stopped.set()
            proc = session.proc
            deadline = time.perf_counter() + 1.0
            try:
                proc.terminate()
            except Exception:
                # Already-exited processes may reject termination. Only the
                # final poll/reap confirmation decides whether ownership ends.
                pass
            try:
                proc.wait(timeout=max(0.0, deadline - time.perf_counter()))
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=0.2)
                except Exception:
                    pass
            for worker in session.threads:
                if worker is not threading.current_thread() and worker.is_alive():
                    wait = None if worker_timeout is None else max(0.0, deadline - time.perf_counter())
                    worker.join(timeout=wait)
            if any(worker.is_alive() for worker in session.threads):
                session.close_error = "Decoder workers still own process pipes"
                return False
            try:
                if proc.poll() is None:
                    session.close_error = "Decoder process exit unconfirmed"
                    return False
            except Exception as exc:
                session.close_error = f"Decoder process exit unconfirmed: {exc}"
                return False
            errors = []
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None and getattr(pipe, "closed", None) is not True:
                    try:
                        close_resource(pipe)
                        if getattr(pipe, "closed", None) is False:
                            raise OSError("Decoder pipe remained open after close")
                    except Exception as exc:
                        errors.append(f"{type(exc).__name__}: {exc}")
            session.close_error = "; ".join(errors)
            session.closed = not errors
            return session.closed

    def _close_retired_session(self, session: _DecoderSession | None, *,
                               worker_timeout: float | None = 1.0) -> bool:
        if session is None:
            return True
        with self._lock:
            if not any(owner is session for owner in self._retired_sessions):
                self._retired_sessions.append(session)
        closed = self._close_session(session, worker_timeout=worker_timeout)
        with self._lock:
            if closed:
                self._retired_sessions = [owner for owner in self._retired_sessions if owner is not session]
            else:
                self.last_error = session.close_error or "Decoder resource close unconfirmed"
        return bool(closed)

    @property
    def stop_pending(self) -> bool:
        """Retained close or process-lifecycle work remains unconfirmed."""
        with self._lock:
            # A failed stop admission may leave an unpublished Popen behind
            # the lifecycle lock, before it can enter the retired inventory.
            return bool(self._retired_sessions or self._stop_in_progress_locked()
                        or self._lifecycle_lock.locked())

    @property
    def stop_status(self) -> VideoStopStatus:
        with self._lock:
            retired = tuple(self._retired_sessions)
            # This also observes a Popen not yet published into a session.
            inflight = self._stop_in_progress_locked() or self._lifecycle_lock.locked()
            # A retired data reader can require terminate() to unblock. Its
            # liveness retains ownership, but must not prevent admitting the
            # missing closer after a stop-worker admission failure.
            inflight = inflight or any(session.close_lock.locked() for session in retired)
            errors = tuple(session.close_error for session in retired if session.close_error)
            return VideoStopStatus(bool(retired or inflight), inflight, errors)

    @property
    def cleanup_complete(self) -> bool:
        with self._lock:
            return (self._session is None and not self._retired_sessions and not self._running
                    and not self._stop_in_progress_locked())

    def wait_closed(self) -> bool:
        """Worker/CLI only: observe in-flight startup and finish retained owners."""
        with self._lock:
            stopper = self._stop_worker
        if stopper is not None and stopper is not threading.current_thread():
            stopper.join()
        with self._lifecycle_lock:
            with self._lock:
                if self._session is not None:
                    return False
                retired = tuple(self._retired_sessions)
            for session in retired:
                self._close_retired_session(session, worker_timeout=None)
            return self.cleanup_complete

    def _stop_in_progress_locked(self) -> bool:
        return self._stop_worker is not None and self._stop_worker.is_alive()

    def request_stop(self, *, clear_latest: bool = True) -> None:
        """Invalidate now; one retained worker closes native owners off caller."""
        with self._lock:
            self._generation += 1
            self._detach_session_locked(clear_latest=clear_latest)
            if self._stop_in_progress_locked():
                return
            if not self._retired_sessions and not self._lifecycle_lock.locked():
                return  # No current, retained, or in-flight spawn owner to wait for.
            worker = threading.Thread(target=self._finish_requested_stop,
                                      name=f"mjpeg-stop-{self.codec}", daemon=True)
            self._stop_worker = worker
            try:
                worker.start()
            except Exception as exc:
                if worker.ident is None:
                    self._stop_worker = None
                self.last_error = f"Decoder stop worker failed: {type(exc).__name__}: {exc}"
                raise

    def _finish_requested_stop(self) -> None:
        # An unpublished Popen belongs to the lifecycle lock. Its generation
        # was invalidated above; wait for it to publish into retained ownership
        # before taking the final owner snapshot. Never join this worker while
        # holding that lock.
        with self._lifecycle_lock:
            with self._lock:
                retired = tuple(self._retired_sessions)
            for session in retired:
                self._close_retired_session(session, worker_timeout=None)

    def stop(self, *, clear_latest: bool = True) -> None:
        # Do not wait for the lifecycle lock: an in-progress restart might be
        # blocked in OS teardown. Its generation check prevents resurrection.
        with self._lock:
            self._generation += 1
            session = self._detach_session_locked(clear_latest=clear_latest)
        self._close_retired_session(session)
