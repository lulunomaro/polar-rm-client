"""视觉流水线（vision-pipeline 组）四条问题的钉死测试。

覆盖的问题
----------
1. **丢帧保持窗口**：tongji_yolo 原注释写"超过 300ms 仍会安全清空"，真实类
   驱动实测是 **310 帧 / 8.03 秒**。本文件用真实 ``LocalAlignedVision`` 驱动，
   把"至少 3 秒仍在保持、约 8 秒才清空"钉死，并钉住源码里不再出现那个错注释。
2. **TongjiVisionReceiver 没有退避**：``refresh_vision_runtime`` 以 60 Hz 调
   ``start()``，bind 失败时实测 1.04 秒生成 **60 个线程**。本文件钉死退避后
   同一场景只生成 ≤3 个，并把退避曲线（0.5/1/2/4/5s）与"换端口立刻重试"
   一并钉死。
3. **延迟遥测既错又无人读**：``submit()`` 曾丢掉 ``CapturedFrame.timestamp_ns``，
   ``latency_ms`` 只覆盖 ``detect()`` 而漏掉 8 次 solvePnP 的几何段。本文件钉死
   四段遥测自洽（detect+geometry == latency_ms）、排队/帧龄可测、以及
   ``latency_ms`` 口径修正后 **payload 逐位不变**（见 GOLDEN_PAYLOAD）。
4. **TongjiVisionBridge 是生产死代码**：钉死"构造它不产生任何线程/socket"，
   并核验 ``submit_bgr``（JPEG 编码链）全仓库零调用方。

跑法::

    python tests/vision_pipeline_hardening_test.py

不依赖摄像头、不依赖 OpenVINO 模型：推理器被桩掉，其余全部走真实类。
"""

from __future__ import annotations

import socket as _socket
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import rm_client.tongji_bridge as tb  # noqa: E402
import rm_client.tongji_yolo as ty  # noqa: E402
from rm_client.tongji_yolo import (  # noqa: E402
    HOLD_MAX_SECONDS,
    LocalAlignedVision,
    LocalDetection,
    TongjiYoloV5,
)

REPO = Path(__file__).resolve().parent.parent
FRAME_W, FRAME_H = 640, 360

FAILURES: list[str] = []
PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


# --- 推理器桩：只桩掉 OpenVINO 模型，其余（select/PnP/保持逻辑）全是真的 -----

DETECTION = LocalDetection(
    "three", "blue", 0.93, (300.0, 150.0, 40.0, 60.0),
    ((300.0, 150.0), (340.0, 150.0), (340.0, 210.0), (300.0, 210.0)),
    light_strength=0.8,
)


class _StubDetector:
    """前 ``visible_frames`` 帧给一个亮灯蓝装甲，之后一直漏检。"""

    active_device = "STUB"
    _sample_light_strength = staticmethod(TongjiYoloV5._sample_light_strength)

    def __init__(self, *args, **kwargs) -> None:
        pass

    def detect(self, bgr, score_threshold: float = 0.70, min_confidence: float = 0.80):
        type(self).calls += 1
        if type(self).calls <= self.visible_frames:
            return [DETECTION]
        return []


_StubDetector.calls = 0
_StubDetector.visible_frames = 1


def _bright_frame() -> "np.ndarray":
    return np.full((FRAME_H, FRAME_W, 3), 200, dtype=np.uint8)


class _VisionHarness:
    """构造真实 LocalAlignedVision，桩掉模型层。"""

    def __init__(self, visible_frames: int = 1, submit_dt: float = 0.025):
        self.visible_frames = visible_frames
        self.submit_dt = submit_dt
        self._saved = ty.TongjiYoloV5

    def __enter__(self) -> LocalAlignedVision:
        _StubDetector.calls = 0
        _StubDetector.visible_frames = self.visible_frames
        ty.TongjiYoloV5 = _StubDetector
        self.vision = LocalAlignedVision(Path("nonexistent-model.xml"), "CPU")
        self.vision.start()
        time.sleep(0.30)
        return self.vision

    def __exit__(self, *exc) -> None:
        self.vision.stop()
        ty.TongjiYoloV5 = self._saved


# --- 1. 丢帧保持窗口 ------------------------------------------------------

def test_hold_window_claim_is_now_true() -> None:
    source = (REPO / "rm_client" / "tongji_yolo.py").read_text(encoding="utf-8")
    # 钉的是"这句注释不能再以断言形式出现在代码里"；模块常量处为了讲清楚
    # 来龙去脉，会**引用**这句旧注释，所以只查它的原断言形态（含分号前缀）。
    check(
        "源码里已没有“短暂漏检…；超过 300ms 仍会安全清空”这句错注释",
        "瞬间消失；超过 300ms" not in source,
        "注释又漂回错误口径",
    )
    check(
        "保持窗口的清空条件写成模块常量 HOLD_MAX_SECONDS(8.0s)",
        HOLD_MAX_SECONDS == 8.0 and "HOLD_MAX_SECONDS" in source,
        f"HOLD_MAX_SECONDS={HOLD_MAX_SECONDS}",
    )


def test_hold_window_measured() -> None:
    """真实类驱动：310 帧 / ~8 秒，而不是注释声称的 300ms。"""
    with _VisionHarness(visible_frames=1) as vision:
        frame = _bright_frame()
        held_frames = 0
        cleared_at: float | None = None
        held_at_3s = False
        t0 = time.perf_counter()
        sequence = 0
        while time.perf_counter() - t0 < 9.0:
            sequence += 1
            vision.submit(sequence, FRAME_W, FRAME_H, frame)
            time.sleep(0.025)
            latest = vision.latest(2000.0)
            if latest is None:
                continue
            payload = latest[4]
            elapsed = time.perf_counter() - t0
            if payload and payload[0].get("held"):
                held_frames += 1
                if elapsed >= 3.0:
                    held_at_3s = True
            elif cleared_at is None and sequence > 5:
                cleared_at = elapsed

    check(
        f"3.0 秒时仍在保持（远超注释声称的 300ms），实测保持帧数={held_frames}",
        held_at_3s and held_frames > 100,
        f"held_frames={held_frames} held_at_3s={held_at_3s}",
    )
    check(
        f"清空发生在 ~8 秒（HOLD_MAX_SECONDS={HOLD_MAX_SECONDS}），实测 {cleared_at:.2f}s",
        cleared_at is not None and 7.5 <= cleared_at <= 8.8,
        f"cleared_at={cleared_at}",
    )
    check(
        f"保持帧数与基线 310 帧一致（±15%），实测 {held_frames}",
        260 <= held_frames <= 360,
        f"held_frames={held_frames}",
    )


def test_payload_bit_identical_to_prechange_golden() -> None:
    """改动前后 payload 必须逐位相同（本轮只动计时与注释，不动几何）。"""
    golden = {
        "id": 1, "name": "three", "color": "blue", "confidence": 0.93,
        "box": [298.7667194408527, 149.94254776640858, 42.4665270454754, 60.10927413957583],
        "points": [[301.8911281876815, 149.94254776640858], [298.7667194408527, 210.05180314993922],
                   [341.2332464863281, 210.0518219059844], [338.10892023234993, 149.94256267952994]],
        "locked": True, "light_strength": 0.8, "light_state": "lit",
        "track_id": 1, "target_id": 1,
        "back_points": [[302.4456892067105, 167.76339937764112], [299.5251427695841, 228.69046891366258],
                        [340.474814821079, 228.69048737947801], [337.5543495432182, 167.76341414533186]],
        "predicted": False, "plate_id": 0,
        "distance_m": 0.6546461933392178, "depth_m": 0.6546372329446097,
        "yaw_rad": 2.879278359425135e-07,
    }
    with _VisionHarness(visible_frames=1) as vision:
        vision.submit(1, FRAME_W, FRAME_H, _bright_frame())
        time.sleep(0.40)
        latest = vision.latest(3000.0)
    check("改动后能取到 payload", latest is not None and bool(latest[4]), "latest() 为空")
    if latest is None or not latest[4]:
        return
    payload = latest[4][0]
    same = payload == golden
    detail = ""
    if not same:
        for key in sorted(set(payload) | set(golden)):
            if payload.get(key) != golden.get(key):
                detail = f"{key}: {payload.get(key)!r} != {golden.get(key)!r}; "
    check("payload 与改动前逐位相同（含 8 次 solvePnP 的输出角点）", same, detail)
    check(
        f"_latest 元组仍是 8 元（冻结 UI 按位解包），实际 {len(latest)} 元",
        len(latest) == 8,
        f"arity={len(latest)}",
    )


# --- 2. TongjiVisionReceiver 退避 ---------------------------------------

class _BindFailsSocket:
    def __init__(self, *args, **kwargs) -> None:
        _BindFailsSocket.created.append(time.perf_counter())

    def setsockopt(self, *args) -> None:
        pass

    def settimeout(self, *args) -> None:
        pass

    def bind(self, address) -> None:
        raise OSError(10048, "[WinError 10048] 每个地址/端口只允许使用一次")

    def close(self) -> None:
        pass


_BindFailsSocket.created = []


class _BindFailsSocketModule:
    AF_INET = _socket.AF_INET
    SOL_SOCKET = _socket.SOL_SOCKET
    SO_REUSEADDR = _socket.SO_REUSEADDR
    SOCK_DGRAM = _socket.SOCK_DGRAM
    timeout = _socket.timeout

    def socket(self, *args, **kwargs):
        return _BindFailsSocket()


class _FailingBindHarness:
    """把 tongji_bridge 模块内的 socket 换成 bind 必失败的替身。"""

    def __enter__(self):
        _BindFailsSocket.created = []
        self._saved = tb.socket
        tb.socket = _BindFailsSocketModule()
        return _BindFailsSocket.created

    def __exit__(self, *exc) -> None:
        tb.socket = self._saved


def _drive_start_60hz(receiver, seconds: float) -> int:
    """复刻 refresh_vision_runtime 的 60 Hz start() 调用。"""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        receiver.start()
        time.sleep(1.0 / 60.0)
    return int((time.perf_counter() - t0) * 60.0)


def test_receiver_backoff_stops_thread_churn() -> None:
    with _FailingBindHarness() as created:
        receiver = tb.TongjiVisionReceiver(9871)
        calls = _drive_start_60hz(receiver, 1.04)
        receiver.stop()
    check(
        f"bind 失败时 {calls} 次 start() 只生成 {len(created)} 个线程（改动前是 {calls} 个）",
        len(created) <= 3,
        f"threads={len(created)} calls={calls}",
    )


def test_receiver_backoff_curve() -> None:
    with _FailingBindHarness() as created:
        receiver = tb.TongjiVisionReceiver(9871)
        receiver.start()
        deadline = time.perf_counter() + 9.0
        while time.perf_counter() < deadline and len(created) < 5:
            receiver.start()
            time.sleep(0.02)
        receiver.stop()
        delays = [
            round(created[i + 1] - created[i], 1) for i in range(min(4, len(created) - 1))
        ]
    expected = [0.5, 1.0, 2.0, 4.0]
    ok = len(delays) == 4 and all(abs(d - e) <= 0.45 for d, e in zip(delays, expected))
    check(
        f"退避间隔实测 {delays}s ≈ 指数曲线 {expected}s（封顶 5.0s）",
        ok,
        f"delays={delays}",
    )
    check(
        f"失败计数随尝试累加到 {tb.TongjiVisionReceiver.RETRY_BASE_SECONDS}/"
        f"{tb.TongjiVisionReceiver.RETRY_MAX_SECONDS}s 的有界曲线",
        tb.TongjiVisionReceiver.RETRY_BASE_SECONDS == 0.5
        and tb.TongjiVisionReceiver.RETRY_MAX_SECONDS == 5.0,
        "退避参数被改动",
    )


def test_receiver_out_of_range_port_backs_off() -> None:
    """端口填 70000 时 bind 抛 OverflowError（不是 OSError），同样必须退避。"""
    created_before = len(_BindFailsSocket.created)
    receiver = tb.TongjiVisionReceiver(70000)
    _drive_start_60hz(receiver, 0.6)
    threads = receiver.open_failures
    receiver.stop()
    check(
        f"越界端口也计入退避（open_failures={threads}, retry_after={receiver.retry_after > 0}）",
        threads >= 1 and receiver.retry_after > 0.0,
        f"open_failures={threads} retry_after={receiver.retry_after}",
    )
    _ = created_before


def test_receiver_rebind_resets_backoff() -> None:
    with _FailingBindHarness() as created:
        receiver = tb.TongjiVisionReceiver(9871)
        _drive_start_60hz(receiver, 0.6)
        before = len(created)
        receiver.configure(9872)
        receiver.start()
        time.sleep(0.25)
        after = len(created)
        receiver.stop()
    check(
        f"换端口立刻重试（{before} → {after} 个线程），用户改完端口不用干等退避",
        after == before + 1 and receiver.open_failures >= 1,
        f"before={before} after={after}",
    )


def test_receiver_normal_bind_unaffected() -> None:
    receiver = tb.TongjiVisionReceiver(0)
    receiver.start()
    deadline = time.perf_counter() + 1.0
    while receiver._socket is None and time.perf_counter() < deadline:
        time.sleep(0.01)
    bound = receiver._socket is not None
    reset = receiver.open_failures == 0 and receiver.retry_after == 0.0
    receiver.stop()
    check("正常端口绑定不受退避影响", bound and reset, f"bound={bound} reset={reset}")


# --- 3. 延迟遥测 ---------------------------------------------------------

def test_latency_segments_are_readable_and_self_consistent() -> None:
    """连续有目标的几帧：几何段必须被计进去，且四段自洽。"""
    with _VisionHarness(visible_frames=400) as vision:
        frame = _bright_frame()
        for sequence in range(1, 13):
            vision.submit(sequence, FRAME_W, FRAME_H, frame, captured_at_ns=time.time_ns())
            time.sleep(0.05)
        report = dict(vision.read_latency())
        latest = vision.latest(3000.0)

    keys = {"detect_ms", "geometry_ms", "queue_ms", "frame_age_ms", "latency_ms", "inference_fps"}
    check(f"read_latency() 一次给全 {sorted(keys)}", set(report) == keys, f"got={sorted(report)}")
    check(
        f"latency_ms = detect_ms + geometry_ms（{report['latency_ms']:.3f} = "
        f"{report['detect_ms']:.3f} + {report['geometry_ms']:.3f}）",
        abs(report["latency_ms"] - (report["detect_ms"] + report["geometry_ms"])) < 1e-9,
        f"{report}",
    )
    check(
        f"几何段（8 次 solvePnP）不再被漏掉：geometry_ms={report['geometry_ms']:.3f}ms > 0",
        report["geometry_ms"] > 0.05,
        f"geometry_ms={report['geometry_ms']}",
    )
    check(
        f"排队时延可测：queue_ms={report['queue_ms']:.3f}ms ≥ 0",
        report["queue_ms"] >= 0.0,
        f"queue_ms={report['queue_ms']}",
    )
    check(
        f"摄像头帧龄可测：frame_age_ms={report['frame_age_ms']:.3f}ms ≥ 0",
        report["frame_age_ms"] >= 0.0,
        f"frame_age_ms={report['frame_age_ms']}",
    )
    check(
        f"latency_ms 已从 detect-only（{report['detect_ms']:.3f}ms）扩到整段",
        report["latency_ms"] >= report["detect_ms"],
        f"{report}",
    )
    _ = latest


def test_latency_without_capture_timestamp_is_flagged() -> None:
    with _VisionHarness(visible_frames=1) as vision:
        vision.submit(1, FRAME_W, FRAME_H, _bright_frame())
        time.sleep(0.30)
        report = dict(vision.read_latency())
    check(
        f"调用方未传 captured_at_ns 时 frame_age_ms 显式标 -1（实测 {report['frame_age_ms']}）",
        report["frame_age_ms"] == -1.0,
        f"frame_age_ms={report['frame_age_ms']}",
    )


def test_submit_keeps_four_positional_arg_call() -> None:
    """冻结的 on_captured_camera_frame 只传 4 个位置参数，不能破。"""
    with _VisionHarness(visible_frames=1) as vision:
        try:
            vision.submit(1, FRAME_W, FRAME_H, _bright_frame())
            ok = True
        except TypeError as exc:  # pragma: no cover - 破签名才会进这里
            ok = False
            detail = str(exc)
    check("submit(sequence,w,h,bgr) 四位置参数调用仍然可用", ok, "" if ok else detail)


def test_geometry_segment_cost_is_real() -> None:
    """几何段本身是实打实的成本，不是凑数的计时点。"""
    from rm_client.armor_vehicle import MultiArmorPrediction

    points = DETECTION.points
    prediction = MultiArmorPrediction()
    prediction.build(points, FRAME_W, FRAME_H, is_big=False, armor_count=4,
                     radius_m=0.23, horizon_s=0.06, target_key=1)
    samples = []
    for _ in range(40):
        t0 = time.perf_counter()
        prediction.build(points, FRAME_W, FRAME_H, is_big=False, armor_count=4,
                         radius_m=0.23, horizon_s=0.06, target_key=1)
        samples.append((time.perf_counter() - t0) * 1000.0)
    median = statistics.median(samples)
    check(
        f"8 次 solvePnP 的几何段中位 {median:.3f} ms/帧（现在被 latency_ms 计入）",
        median > 0.2,
        f"median={median:.4f}ms",
    )


# --- 4. TongjiVisionBridge 生产死代码 ------------------------------------

def test_bridge_is_inert_on_the_production_path() -> None:
    from rm_client.vision import TongjiVisionBridge

    before = set(threading.enumerate())
    bridge = TongjiVisionBridge()
    after = set(threading.enumerate())
    check(
        f"构造 TongjiVisionBridge 新增线程 {len(after - before)} 个（生产路径从不 start()）",
        len(after - before) == 0 and bridge.running is False and bridge.connected is False,
        f"threads={len(after - before)} running={bridge.running}",
    )

    callers = []
    me = Path(__file__).resolve()
    for path in sorted(REPO.glob("rm_client/**/*.py")) + sorted(REPO.glob("tests/*.py")) + sorted(REPO.glob("tools/**/*.py")):
        if "__pycache__" in path.parts or path.resolve() == me:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.name == "vision.py":
            continue
        if "submit_bgr" in text:
            callers.append(path.relative_to(REPO).as_posix())
    check(
        f"JPEG 编码入口 submit_bgr 的调用方 = {callers or '无'}（含测试在内）",
        not callers,
        f"callers={callers}",
    )
    doc = TongjiVisionBridge.__doc__ or ""
    check(
        "类文档已写明它是生产死代码及其证据",
        "死代码" in doc and "polar_app.py" in doc,
        "文档未标注",
    )


# --- 主流程 --------------------------------------------------------------

def main() -> int:
    tests = [
        test_hold_window_claim_is_now_true,
        test_hold_window_measured,
        test_payload_bit_identical_to_prechange_golden,
        test_receiver_backoff_stops_thread_churn,
        test_receiver_backoff_curve,
        test_receiver_out_of_range_port_backs_off,
        test_receiver_rebind_resets_backoff,
        test_receiver_normal_bind_unaffected,
        test_latency_segments_are_readable_and_self_consistent,
        test_latency_without_capture_timestamp_is_flagged,
        test_submit_keeps_four_positional_arg_call,
        test_geometry_segment_cost_is_real,
        test_bridge_is_inert_on_the_production_path,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"视觉流水线加固：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"视觉流水线加固：全部通过（{PASSED} 项）")
    print("保持窗口口径已与实测对齐；接收器有界退避；四段时延可读且自洽；"
          "payload 逐位不变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
