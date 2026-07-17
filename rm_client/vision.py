from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import socket
import struct
import threading
import time
from typing import Any, Callable

from .model import RobotState
from .tongji_bridge import TongjiPacket


PROTOCOL_MAGIC = b"PLVR"
PROTOCOL_VERSION = 1
PACKET_HEADER = struct.Struct("!4sHHIQI")
PACKET_FRAME = 1
PACKET_RESULT = 2


@dataclass
class VisionTarget:
    x: float
    y: float
    w: float
    h: float
    confidence: float
    label: str
    kind: str = "armor"


@dataclass(frozen=True)
class ArmorObservation:
    armor_id: int
    label: str
    color: str
    confidence: float
    points: tuple[tuple[float, float], ...]
    distance_m: float = 0.0
    predicted: bool = False
    aim: bool = False
    depth_m: float = 0.0
    back_points: tuple[tuple[float, float], ...] = ()
    yaw_rad: float = 0.0
    plate_id: int = -1
    light_strength: float = 0.0
    light_state: str = "unknown"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ArmorObservation":
        raw_points = payload.get("points", [])
        points: list[tuple[float, float]] = []
        if isinstance(raw_points, list):
            for point in raw_points[:4]:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    try:
                        points.append((float(point[0]), float(point[1])))
                    except (TypeError, ValueError):
                        continue
        raw_back = payload.get("back_points", [])
        back_points: list[tuple[float, float]] = []
        if isinstance(raw_back, list):
            for point in raw_back[:4]:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    try:
                        back_points.append((float(point[0]), float(point[1])))
                    except (TypeError, ValueError):
                        continue
        return cls(
            armor_id=int(payload.get("id", -1)),
            label=str(payload.get("label", "armor")),
            color=str(payload.get("color", "unknown")),
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            points=tuple(points),
            distance_m=max(0.0, float(payload.get("distance_m", 0.0))),
            predicted=bool(payload.get("predicted", False)),
            aim=bool(payload.get("aim", False)),
            depth_m=max(0.0, float(payload.get("depth_m", 0.0))),
            back_points=tuple(back_points),
            yaw_rad=float(payload.get("yaw_rad", 0.0)),
            plate_id=int(payload.get("plate_id", payload.get("id", -1))),
            light_strength=max(0.0, min(1.0, float(payload.get("light_strength", 0.0)))),
            light_state=str(payload.get("light_state", "unknown")),
        )


@dataclass(frozen=True)
class VisionResult:
    sequence: int
    timestamp_ns: int
    source_width: int
    source_height: int
    detections: tuple[ArmorObservation, ...] = ()
    predicted_armors: tuple[ArmorObservation, ...] = ()
    aim_armor: ArmorObservation | None = None
    tracking_state: str = "disconnected"
    calibrated: bool = False
    inference_device: str = "unknown"
    inference_fps: float = 0.0
    latency_ms: float = 0.0
    aim_yaw_deg: float = 0.0
    aim_pitch_deg: float = 0.0

    @classmethod
    def from_payload(cls, sequence: int, timestamp_ns: int, payload: dict[str, Any]) -> "VisionResult":
        def armor_list(key: str) -> tuple[ArmorObservation, ...]:
            values = payload.get(key, [])
            if not isinstance(values, list):
                return ()
            return tuple(ArmorObservation.from_payload(item) for item in values if isinstance(item, dict))

        detected = armor_list("detections")
        predicted = armor_list("predicted_armors")
        raw_aim = payload.get("aim_armor")
        aim = ArmorObservation.from_payload(raw_aim) if isinstance(raw_aim, dict) else next((item for item in predicted if item.aim), None)
        return cls(
            sequence=sequence,
            timestamp_ns=int(payload.get("timestamp_ns", timestamp_ns)),
            source_width=max(1, int(payload.get("source_width", 1))),
            source_height=max(1, int(payload.get("source_height", 1))),
            detections=detected,
            predicted_armors=predicted,
            aim_armor=aim,
            tracking_state=str(payload.get("tracking_state", "lost")),
            calibrated=bool(payload.get("calibrated", False)),
            inference_device=str(payload.get("inference_device", "unknown")),
            inference_fps=max(0.0, float(payload.get("inference_fps", 0.0))),
            latency_ms=max(0.0, float(payload.get("latency_ms", 0.0))),
            aim_yaw_deg=float(payload.get("aim_yaw_deg", 0.0)),
            aim_pitch_deg=float(payload.get("aim_pitch_deg", 0.0)),
        )

    @property
    def is_tracking(self) -> bool:
        return self.tracking_state.lower() in ("tracking", "locked", "detected") and bool(self.detections or self.predicted_armors)


@dataclass(frozen=True)
class VisionSnapshot:
    """所有视觉来源统一交给 HUD 的快照；不携带摄像头句柄或推理线程状态。"""

    source: str
    result: VisionResult
    received_ns: int
    gimbal_command: dict[str, Any] = field(default_factory=dict)

    @property
    def primary(self) -> ArmorObservation | None:
        return self.result.aim_armor or next(iter(self.result.detections), None)

    @property
    def armors(self) -> tuple[ArmorObservation, ...]:
        return self.result.detections


def _observation_from_tongji(payload: dict[str, Any], *, force_aim: bool = False) -> ArmorObservation:
    return ArmorObservation.from_payload({
        "id": payload.get("id", -1),
        "label": payload.get("name", "armor"),
        "color": payload.get("color", "unknown"),
        "confidence": payload.get("confidence", 0.0),
        "points": payload.get("points", []),
        "back_points": payload.get("back_points", []),
        "distance_m": payload.get("distance_m", 0.0),
        "depth_m": payload.get("depth_m", 0.0),
        "predicted": payload.get("predicted", False),
        "aim": force_aim or payload.get("aim", False) or payload.get("locked", False),
        "yaw_rad": payload.get("yaw_rad", 0.0),
        "plate_id": payload.get("plate_id", payload.get("id", -1)),
        "light_strength": payload.get("light_strength", 0.0),
        "light_state": payload.get("light_state", "unknown"),
    })


def expand_multi_plate_esp(
    result: VisionResult,
    *,
    armor_count: int = 4,
    radius_m: float = 0.23,
    horizon_s: float = 0.06,
) -> VisionResult:
    """单板 → 3D 厚度 + 整车四装甲板预测（对齐同济整车几何 / MultiArmorPrediction）。

    外挂已给出多板 predicted 时原样返回；否则对主目标做 PnP 扩板。
    """
    if len(result.predicted_armors) >= max(1, armor_count - 1):
        return result
    seed = result.aim_armor
    if seed is None or len(seed.points) != 4:
        seed = next((a for a in result.detections if len(a.points) == 4), None)
    if seed is None:
        return result

    from .armor_vehicle import MultiArmorPrediction

    is_big = str(seed.label).lower() in ("one", "base")
    plates = MultiArmorPrediction().build(
        seed.points,
        result.source_width,
        result.source_height,
        is_big=is_big,
        armor_count=max(2, min(4, int(armor_count))),
        radius_m=float(radius_m),
        horizon_s=float(horizon_s),
        force_multi=True,
    )
    if not plates:
        return result

    observed: list[ArmorObservation] = []
    predicted: list[ArmorObservation] = []
    aim = result.aim_armor
    for plate in plates:
        obs = ArmorObservation(
            armor_id=int(plate.plate_id),
            label=seed.label,
            color=seed.color if not plate.predicted else "predicted",
            confidence=seed.confidence if not plate.predicted else max(0.35, seed.confidence * 0.68),
            points=tuple(plate.points),
            distance_m=float(plate.distance_m),
            predicted=bool(plate.predicted),
            aim=bool(aim is not None and not plate.predicted and aim.aim),
            depth_m=float(plate.depth_m),
            back_points=tuple(plate.back_points),
            yaw_rad=float(plate.yaw_rad),
            plate_id=int(plate.plate_id),
            light_strength=seed.light_strength,
            light_state=seed.light_state,
        )
        if plate.predicted:
            predicted.append(obs)
        else:
            observed.append(obs)
            if aim is None or not aim.aim:
                aim = obs

    # 保留其它非种子检测（多目标时）
    for det in result.detections:
        if det is seed:
            continue
        if len(det.points) == 4 and all(
            math.dist(det.points[0], o.points[0]) > 8.0 for o in observed if len(o.points) == 4
        ):
            observed.append(det)

    return VisionResult(
        sequence=result.sequence,
        timestamp_ns=result.timestamp_ns,
        source_width=result.source_width,
        source_height=result.source_height,
        detections=tuple(observed),
        predicted_armors=tuple(predicted),
        aim_armor=aim,
        tracking_state=result.tracking_state,
        calibrated=result.calibrated,
        inference_device=result.inference_device,
        inference_fps=result.inference_fps,
        latency_ms=result.latency_ms,
        aim_yaw_deg=result.aim_yaw_deg,
        aim_pitch_deg=result.aim_pitch_deg,
    )


def snapshot_from_tongji_packet(packet: TongjiPacket, *, source: str = "Tongji", inference_device: str = "external") -> VisionSnapshot:
    """将 UDP JSON 几何数据转换成 UI 使用的同一套 VisionResult。"""
    detections = tuple(_observation_from_tongji(armor) for armor in packet.armors)
    primary = _observation_from_tongji(packet.primary, force_aim=True) if packet.primary else None
    predicted = tuple(armor for armor in detections if armor.predicted)
    observed = tuple(armor for armor in detections if not armor.predicted)
    # 外挂几何（同济/sp_vision）已带标定或四板；不依赖 client 棋盘格
    external_ok = bool(packet.command.get("calibrated", True)) or bool(
        packet.command.get("multi_plate", False)
    ) or len(predicted) > 0
    result = VisionResult(
        sequence=int(packet.timestamp_ns & 0xFFFFFFFF),
        timestamp_ns=packet.timestamp_ns,
        source_width=packet.frame_width,
        source_height=packet.frame_height,
        detections=observed,
        predicted_armors=predicted,
        aim_armor=primary,
        tracking_state="tracking" if primary else "detected" if observed else "lost",
        calibrated=external_ok,
        inference_device=inference_device,
        aim_yaw_deg=math.degrees(float(packet.command.get("yaw_rad", packet.gimbal.get("yaw_rad", 0.0)))),
        aim_pitch_deg=math.degrees(float(packet.command.get("pitch_rad", packet.gimbal.get("pitch_rad", 0.0)))),
    )
    # 仅有单板时在 client 补齐四板 3D ESP（同济 MultiArmor / EKF 未到时的回退）
    if len(predicted) < 1 and any(len(a.points) == 4 for a in observed + ((primary,) if primary else ())):
        result = expand_multi_plate_esp(result)
    return VisionSnapshot(source, result, time.time_ns(), packet.command)


def snapshot_from_local_result(
    timestamp_ns: int, sequence: int, width: int, height: int, armors: list[dict[str, Any]], inference_fps: float, latency_ms: float, device: str,
) -> VisionSnapshot:
    packet = TongjiPacket(timestamp_ns, width, height, "auto_aim", armors[0] if armors else None, tuple(armors), None, {}, {"control": False, "fire": False})
    snapshot = snapshot_from_tongji_packet(packet, source="Local", inference_device=device)
    # Local：LocalAlignedVision 可能已带多板；否则 expand 会补齐
    expanded = expand_multi_plate_esp(snapshot.result) if len(snapshot.result.predicted_armors) < 1 else snapshot.result
    result = VisionResult(
        sequence=sequence,
        timestamp_ns=timestamp_ns,
        source_width=width,
        source_height=height,
        detections=expanded.detections,
        predicted_armors=expanded.predicted_armors,
        aim_armor=expanded.aim_armor,
        tracking_state=expanded.tracking_state,
        calibrated=False,
        inference_device=device,
        inference_fps=inference_fps,
        latency_ms=latency_ms,
    )
    return VisionSnapshot("Local", result, timestamp_ns, {"control": False, "fire": False})


class CompositeVisionPipeline:
    """按 Mock / Local / Tongji / Auto 选择最新可用视觉快照。"""

    def __init__(self) -> None:
        self.last_snapshot: VisionSnapshot | None = None

    def choose(self, source: str, local: VisionSnapshot | None, tongji: VisionSnapshot | None) -> VisionSnapshot | None:
        chosen: VisionSnapshot | None
        if source == "Local":
            chosen = local
        elif source == "Tongji":
            chosen = tongji
        elif source == "Auto":
            chosen = tongji or local
        else:
            chosen = None
        if chosen is not None:
            self.last_snapshot = chosen
        return chosen


@dataclass(frozen=True)
class CapturedFrame:
    sequence: int
    timestamp_ns: int
    width: int
    height: int
    rgb: Any
    bgr: Any
    source: str = "device"
    fov_degrees: float = 0.0
    frame_id: int = 0


class CameraCaptureWorker:
    """图传后台线程：本机摄像头 / OBS 虚拟摄像头 / Gestalt VisionBridge SHM。

    只保留最新帧供 HUD 显示；自瞄仍走外挂 SHM/UDP，不在此做控制。
    """

    def __init__(self, camera_id: int = 0, size: tuple[int, int] = (960, 540)) -> None:
        self.camera_id = camera_id
        self.size = size
        self.source_kind = "device"  # device | shm
        self.ws_port = 0
        self.game_pid = 0
        self.resize_to_setting = True
        # 显示侧限流：自瞄不走此路径，可大胆丢帧换流畅
        self.max_display_fps = 30.0
        self.display_max_width = 960  # 0 = 不限制；SHM 默认缩到 960 宽
        self.skip_rgb_convert = True  # UI 用 BGR888，省一次 cvtColor
        self.connected = False
        self.running = False
        self.last_error = ""
        self.status_text = "idle"
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: CapturedFrame | None = None
        self._sequence = 0
        self._last_publish_t = 0.0
        self.on_frame: Callable[[CapturedFrame], None] | None = None

    def configure(
        self,
        camera_id: int,
        size: tuple[int, int],
        *,
        source_kind: str = "device",
        ws_port: int = 0,
        game_pid: int = 0,
        resize_to_setting: bool = True,
        max_display_fps: float = 30.0,
        display_max_width: int = 960,
    ) -> None:
        self.camera_id = int(camera_id)
        self.size = (max(160, int(size[0])), max(120, int(size[1])))
        self.source_kind = "shm" if source_kind in ("shm", "gestalt", "Gestalt SHM") else "device"
        self.ws_port = int(ws_port or 0)
        self.game_pid = int(game_pid or 0)
        self.resize_to_setting = bool(resize_to_setting)
        self.max_display_fps = max(0.0, float(max_display_fps))
        self.display_max_width = max(0, int(display_max_width))

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="polar-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.2)
        self._thread = None
        self.connected = False
        self.status_text = "stopped"

    def latest_frame(self) -> CapturedFrame | None:
        with self._lock:
            return self._latest

    def _publish(
        self,
        bgr: Any,
        *,
        source: str,
        fov: float = 0.0,
        frame_id: int = 0,
        already_sized: bool = False,
    ) -> None:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        if bgr is None:
            return
        # 显示限帧：只保留最新、丢中间帧（自瞄不读这里）
        now = time.perf_counter()
        if self.max_display_fps > 1.0:
            min_dt = 1.0 / self.max_display_fps
            if now - self._last_publish_t < min_dt * 0.92:
                return
        self._last_publish_t = now

        h0, w0 = int(bgr.shape[0]), int(bgr.shape[1])
        if not already_sized:
            target_w, target_h = w0, h0
            if self.display_max_width > 0 and w0 > self.display_max_width:
                scale = self.display_max_width / float(w0)
                target_w = int(self.display_max_width)
                target_h = max(1, int(round(h0 * scale)))
            elif self.resize_to_setting and (w0 != self.size[0] or h0 != self.size[1]):
                target_w, target_h = self.size[0], self.size[1]

            if target_w != w0 or target_h != h0:
                interp = cv2.INTER_AREA if (target_w < w0 or target_h < h0) else cv2.INTER_LINEAR
                bgr = cv2.resize(bgr, (target_w, target_h), interpolation=interp)
                # resize 结果需拥有权
                if not getattr(bgr, "flags", None) or not bgr.flags["OWNDATA"]:
                    bgr = np.array(bgr, copy=True, order="C")
        if not getattr(bgr, "flags", None) or not bgr.flags["C_CONTIGUOUS"]:
            bgr = np.ascontiguousarray(bgr)
        # grab() 已用 triple-buffer 时不再二次 copy
        if self.skip_rgb_convert:
            rgb = bgr
        else:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self._sequence += 1
        frame = CapturedFrame(
            self._sequence,
            time.time_ns(),
            int(bgr.shape[1]),
            int(bgr.shape[0]),
            rgb,
            bgr,
            source=source,
            fov_degrees=float(fov),
            frame_id=int(frame_id),
        )
        with self._lock:
            self._latest = frame
        callback = self.on_frame
        if callback is not None:
            callback(frame)

    def _run(self) -> None:
        if self.source_kind == "shm":
            self._run_shm()
        else:
            self._run_device()

    def _run_device(self) -> None:
        try:
            import cv2  # type: ignore
        except Exception as exc:  # pragma: no cover - local camera stack
            self.last_error = str(exc)
            self.status_text = f"cv2-error:{exc}"
            self.running = False
            return
        capture = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(self.camera_id)
        if not capture.isOpened():
            capture.release()
            self.last_error = "摄像头不可用"
            self.status_text = "device-unavailable"
            self.running = False
            return
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        self.connected = True
        self.status_text = f"device:{self.camera_id}"
        try:
            while self.running:
                ok, bgr = capture.read()
                if not ok or bgr is None:
                    time.sleep(0.01)
                    continue
                self._publish(bgr, source="device")
        finally:
            capture.release()
            self.connected = False

    def _run_shm(self) -> None:
        """Gestalt VisionBridge shared-memory feed (same protocol as aim runner)."""
        try:
            import sys
            from pathlib import Path

            # RMaccseeplan/sp_vision_py next to rm_custom_client
            workspace = Path(__file__).resolve().parents[2]
            sp_root = workspace / "sp_vision_py"
            if not sp_root.is_dir():
                sp_root = workspace.parent / "sp_vision_py"
            if sp_root.is_dir() and str(sp_root) not in sys.path:
                sys.path.insert(0, str(sp_root))
            from sp_vision.io.gestalt_shm import SharedFrameCapture  # type: ignore
        except Exception as exc:
            self.last_error = f"SHM import failed: {exc}"
            self.status_text = "shm-import-fail"
            self.running = False
            return

        cap = SharedFrameCapture()
        self.status_text = "shm-connecting"
        opened = False
        # retry loop so game can start after client
        while self.running and not opened:
            if self.game_pid > 0:
                opened = cap.init_pid(self.game_pid, timeout_ms=2500)
            elif self.ws_port > 0:
                opened = cap.init_port(self.ws_port, timeout_ms=2500)
            else:
                opened = cap.init_auto(timeout_ms=2500)
            if not opened:
                self.last_error = "等待 Gestalt VisionBridge SHM（游戏需 -visionbridge）"
                self.status_text = "shm-waiting"
                time.sleep(0.5)
        if not opened:
            self.running = False
            return

        self.connected = True
        self.status_text = f"shm:pid={getattr(cap, '_pid', 0)}"
        self.last_error = ""
        miss = 0
        last_fid = -1
        # 显示解码直接在 grab 内降采样（不解 1928 全图再 resize）
        decode_w = int(self.display_max_width or 0)
        try:
            while self.running:
                # 限帧：到期前只 spin 取最新 commit，避免无意义全量 decode
                if self.max_display_fps > 1.0:
                    min_dt = 1.0 / self.max_display_fps
                    wait = min_dt - (time.perf_counter() - self._last_publish_t)
                    if wait > 0.001:
                        time.sleep(min(wait, 0.008))
                frame = cap.grab(timeout_ms=6, max_width=decode_w)
                if not frame.ok or frame.img is None:
                    miss += 1
                    if miss > 80:
                        self.status_text = "shm-stale"
                        # remount mapping (game restart)
                        cap.close()
                        opened = False
                        while self.running and not opened:
                            if self.game_pid > 0:
                                opened = cap.init_pid(self.game_pid, timeout_ms=2000)
                            elif self.ws_port > 0:
                                opened = cap.init_port(self.ws_port, timeout_ms=2000)
                            else:
                                opened = cap.init_auto(timeout_ms=2000)
                            if not opened:
                                time.sleep(0.4)
                        miss = 0
                        last_fid = -1
                        self.status_text = f"shm:pid={getattr(cap, '_pid', 0)}"
                    else:
                        time.sleep(0.001)
                    continue
                miss = 0
                # 同 engine frame 不重复处理
                fid = int(frame.frame_id or 0)
                if fid and fid == last_fid:
                    continue
                last_fid = fid
                self.connected = True
                # grab 已按 max_width 解出显示尺寸；triple-buffer 持有权在 cap 内轮转
                self._publish(
                    frame.img,
                    source="shm",
                    fov=float(frame.fov_degrees or 0.0),
                    frame_id=fid,
                    already_sized=True,
                )
        finally:
            try:
                cap.close()
            except Exception:
                pass
            self.connected = False
            self.status_text = "shm-closed"


class TongjiVisionBridge:
    """与 WSL/Ubuntu 同济桥接进程通信的最新帧优先 TCP 客户端。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 18301) -> None:
        self.host = host
        self.port = port
        self.running = False
        self.connected = False
        self.last_error = ""
        self.last_result: VisionResult | None = None
        self.results_received = 0
        self.frames_sent = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: tuple[int, int, int, int, bytes] | None = None
        self._receive_buffer = bytearray()
        self.on_result: Callable[[VisionResult], None] | None = None

    def configure(self, host: str, port: int) -> None:
        self.host = host
        self.port = max(1, min(65535, int(port)))

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name="tongji-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self.connected = False

    def submit_bgr(self, frame: CapturedFrame, quality: int = 82) -> None:
        try:
            import cv2  # type: ignore

            ok, encoded = cv2.imencode(".jpg", frame.bgr, [int(cv2.IMWRITE_JPEG_QUALITY), max(35, min(95, int(quality)))])
            if ok:
                self.submit_jpeg(frame.sequence, frame.timestamp_ns, frame.width, frame.height, bytes(encoded))
        except Exception as exc:  # pragma: no cover - depends on optional cv2
            self.last_error = str(exc)

    def submit_jpeg(self, sequence: int, timestamp_ns: int, width: int, height: int, jpeg: bytes) -> None:
        if not jpeg:
            return
        with self._lock:
            self._pending = (int(sequence), int(timestamp_ns), int(width), int(height), bytes(jpeg))

    def latest_result(self, max_age_ms: float = 420.0) -> VisionResult | None:
        result = self.last_result
        if result is None:
            return None
        if (time.time_ns() - result.timestamp_ns) / 1_000_000.0 > max_age_ms:
            return None
        return result

    def _take_pending(self) -> tuple[int, int, int, int, bytes] | None:
        with self._lock:
            frame = self._pending
            self._pending = None
            return frame

    def _run(self) -> None:
        while self.running:
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection((self.host, self.port), timeout=0.6)
                sock.settimeout(0.025)
                self.connected = True
                self.last_error = ""
                self._receive_buffer.clear()
                while self.running:
                    frame = self._take_pending()
                    if frame is not None:
                        sequence, timestamp_ns, width, height, jpeg = frame
                        metadata = json.dumps({"source_width": width, "source_height": height}, separators=(",", ":")).encode("utf-8")
                        self._send_packet(sock, PACKET_FRAME, sequence, timestamp_ns, metadata + b"\n" + jpeg)
                        self.frames_sent += 1
                    try:
                        data = sock.recv(262144)
                        if not data:
                            raise ConnectionError("同济桥接器已关闭连接")
                        self._receive_buffer.extend(data)
                        self._parse_packets()
                    except socket.timeout:
                        pass
            except (OSError, ConnectionError) as exc:
                self.last_error = str(exc)
                self.connected = False
                time.sleep(0.20)
            finally:
                self.connected = False
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    @staticmethod
    def _send_packet(sock: socket.socket, packet_type: int, sequence: int, timestamp_ns: int, payload: bytes) -> None:
        header = PACKET_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, packet_type, sequence, timestamp_ns, len(payload))
        sock.sendall(header + payload)

    def _parse_packets(self) -> None:
        while len(self._receive_buffer) >= PACKET_HEADER.size:
            magic, version, packet_type, sequence, timestamp_ns, payload_size = PACKET_HEADER.unpack(self._receive_buffer[:PACKET_HEADER.size])
            if magic != PROTOCOL_MAGIC or version != PROTOCOL_VERSION or payload_size > 4_000_000:
                self._receive_buffer.clear()
                self.last_error = "同济桥接协议头无效"
                return
            total = PACKET_HEADER.size + payload_size
            if len(self._receive_buffer) < total:
                return
            payload = bytes(self._receive_buffer[PACKET_HEADER.size:total])
            del self._receive_buffer[:total]
            if packet_type != PACKET_RESULT:
                continue
            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.last_error = "同济桥接结果不是有效 JSON"
                continue
            if not isinstance(data, dict):
                continue
            result = VisionResult.from_payload(sequence, timestamp_ns, data)
            self.last_result = result
            self.results_received += 1
            callback = self.on_result
            if callback is not None:
                callback(result)


class MockVisionPipeline:
    """真实自瞄链路接入前使用的移动目标生成器。"""

    def process(self, width: int, height: int, state: RobotState, target_kind: str = "armor") -> VisionTarget:
        if target_kind == "energy":
            return self.process_energy(width, height, state)
        return self.process_armor(width, height, state)

    def process_armor(self, width: int, height: int, state: RobotState) -> VisionTarget:
        now = time.perf_counter() - state.start_time
        target_w = max(76.0, width * 0.08)
        target_h = max(52.0, height * 0.09)
        cx = width * 0.5 + math.sin(now * 0.9) * width * 0.18
        cy = height * 0.45 + math.cos(now * 0.6) * height * 0.08
        return VisionTarget(cx - target_w / 2.0, cy - target_h / 2.0, target_w, target_h, 0.86 + 0.1 * math.sin(now * 1.7), f"Robot {state.target_id}", "armor")

    def process_energy(self, width: int, height: int, state: RobotState) -> VisionTarget:
        now = time.perf_counter() - state.start_time
        target_w = max(92.0, width * 0.075)
        cx = width * 0.5 + math.sin(now * 0.45) * width * 0.12
        cy = height * 0.36 + math.cos(now * 0.72) * height * 0.06
        return VisionTarget(cx - target_w / 2.0, cy - target_w / 2.0, target_w, target_w, 0.78 + 0.14 * math.sin(now * 1.1), f"Energy {state.energy_rune_phase}", "energy")
