"""队内 0x5A 链路的串口传输层（下位机 <-> 本客户端）。

设计约束全部来自固件实测（StandardRobotpp @2722e0d）：

1. **固件没有断流归零**。`usb_task.c:282` 每 1ms 无条件把最后一帧拷进控制量，
   `USB_OFFLINE=true` 分支（`usb_task.c:285-287`）不清数据 —— 上位机崩溃/拔线后
   机器人会**保持最后一次指令永久执行**。因此归零责任完全在客户端侧：
   任何停发、失焦、急停、退出都必须先把控制量归零再停止。

2. **`time_stamp` 是活性门控而不仅是记录**。固件只在帧内 `time_stamp` 严格大于
   历史最大值时才刷新 `RECEIVE_TIME`（`usb_task.c:699-702`），而
   `LATEST_RX_TIMESTAMP` 仅在下位机 `UsbInit` 清零。若客户端用进程相对时间，
   **客户端一重启时间戳归零，固件会永久判离线，重新插拔也恢复不了**。
   所以这里用 epoch 毫秒 + 回绕延续，见 `PolarTimestamp`。

3. mock 默认开启，与项目其它后端一致的 fail-closed 纪律。
"""

from __future__ import annotations

import threading
import math
import struct
import time
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable

from ..serial_ports import describe_ports, resolve_port as resolve_polar_port
from .resource_close import close_resource
from .polar_connection import PolarConnectionConfig, acquire_polar_connection
from .polar_link import (
    MAX_RETAINED_BYTES,
    SPIN_CMD_NONE,
    GIMBAL_HOST_REQUEST_ON,
    SPIN_CMD_OFF,
    SPIN_CMD_ON,
    PolarFrameCodec,
    PolarSegment,
    deg_to_rad,
    drive_key_mask,
)

# 客户端发送频率上限。底盘内部是 2ms 环，50Hz 已足够；留出余量给下行段。
DEFAULT_SEND_HZ = 50.0
MAX_SEND_HZ = 100.0
# 连续这么久没有新的驾驶意图，控制量自动归零（本地看门狗）。
DEADMAN_TIMEOUT_S = 0.20
# 断流后归零帧的持续时长，确保固件侧完成一次归零覆盖。
ZERO_BURST_S = 0.30


class PolarTimestamp:
    """严格递增的 u32 毫秒时间戳。

    固件用「帧时间戳是否变大」判断上位机在线（`usb_task.c:699-702`），而基线
    `LATEST_RX_TIMESTAMP` 只在下位机自己上电时清零，所以客户端必须保证：

    - 同一进程内严格递增 —— 用 64 位虚拟毫秒计数器保证，不受系统时钟粒度影响；
    - 跨进程重启仍大于上一次会话 —— epoch 毫秒天然满足；若怀疑系统时钟被
      往回调，调用方可把上次发出的末值作为 ``floor`` 传回来续接；
    - **低 32 位回绕是真实危险**：每 49.7 天发生一次，一旦跨过，固件的 ``>``
      判据会立刻反向失败并把上位机永久判为离线。这里不假装能凭空解决，
      而是置 ``roll_detected`` 让链路 fail-closed 停止驾驶，并提示重启机器人。
    """

    _MASK = 0xFFFFFFFF
    _ROLL = 0x1_0000_0000

    def __init__(self, floor: int | None = None) -> None:
        self._lock = threading.Lock()
        virtual = int(time.time() * 1000)
        if floor is not None:
            floor = int(floor) & self._MASK
            if (virtual & self._MASK) <= floor:
                # 抬到「低位刚好大于 floor」的最小虚拟值
                virtual += floor + 1 - (virtual & self._MASK)
        self._virtual = virtual
        self._last_emitted = virtual & self._MASK
        self.roll_detected = floor == self._MASK

    @property
    def current(self) -> int:
        """最后一次发出的 u32 值，可持久化后作为下次重启的 ``floor``。"""

        with self._lock:
            return self._last_emitted

    def next(self) -> int:
        with self._lock:
            previous_virtual = self._virtual
            self._virtual = max(int(time.time() * 1000), self._virtual + 1)
            emitted = self._virtual & self._MASK
            if (self._virtual >> 32) != (previous_virtual >> 32):
                self.roll_detected = True
            self._last_emitted = emitted
            return emitted


@dataclass(slots=True)
class PolarDriveIntent:
    """一帧驾驶/云台意图。单位：线速度 m/s、角速度与云台角度 rad。

    `key_value` 是固件 `rc_ctrl.key.v` 位图（W=0 S=1 A=2 D=3 …），用于 0x03
    虚拟遥控器通道：固件已有读该位图直接产生斜坡速度的驾驶逻辑。
    """

    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    gimbal_pitch_rad: float = 0.0
    gimbal_yaw_rad: float = 0.0
    friction_on: bool = False
    tracking: bool = False
    key_value: int = 0
    #: 是否允许把 SHIFT（小陀螺）这类**模式切换**键位发到固件。
    #: 默认 False：只发驾驶键。开启后仍不允许 Q/E/F（开火相关）。
    allow_spin_key: bool = False
    produced_at: float = field(default_factory=time.perf_counter)


@dataclass(slots=True)
class PolarLinkStats:
    connected: bool = False
    mock_writes: bool = True
    resolved_port: str = ""
    last_error_code: str = ""
    detected_ports: list[str] = field(default_factory=list)
    port: str = "Stub"
    baud: int = 115200
    frames_sent: int = 0
    zero_frames_sent: int = 0
    spin_frames_sent: int = 0
    segments_received: int = 0
    send_hz: float = DEFAULT_SEND_HZ
    last_send_at: float = 0.0
    last_segment_at: float = 0.0
    last_error: str = ""
    link_age_s: float = 0.0


class PolarLinkTransport:
    """0x5A 链路收发线程。

    控制面只开放**云台角度 + 底盘速度 + 摩擦轮**；`fire` 由 codec 恒置 0，
    本类也没有任何能置 1 的入口。
    """

    def __init__(
        self,
        port: str = "Stub",
        baud: int = 115200,
        mock_writes: bool = True,
        safety: Any = None,
        send_hz: float = DEFAULT_SEND_HZ,
    ) -> None:
        self.port = str(port or "Stub")
        self.baud = int(baud)
        self.mock_writes = bool(mock_writes)
        self.safety = safety
        self.send_hz = max(5.0, min(float(send_hz), MAX_SEND_HZ))
        self.codec = PolarFrameCodec()
        self.timestamp = PolarTimestamp()

        self.connected = False
        self.last_error = ""
        # HUD 字体（Poppins）没有 CJK 字形，中文错误串会渲染成方块。
        # 因此额外维护一个稳定的 ASCII 状态码供 HUD 行使用，
        # 中文详情只进日志与 tooltip。
        self.last_error_code = ""
        self.frames_sent = 0
        self.zero_frames_sent = 0
        self.last_send_at = 0.0
        self.last_segment_at = 0.0

        self._serial: Any = None
        self._close_error = ""
        # ---- D 路（车端 USB<->WiFi 桥）：只有"字节从哪儿来"这一层受影响 ----
        #: "serial" 直插 C 板；"tcp" 走车上小电脑上的 bridge_server 裸管道
        self.transport = "serial"
        self.host = ""
        self.tcp_port = 50570
        #: 只给测试注入假串口用；生产为 None
        self.stream_opener = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._stop = threading.Event()

        self._intent: PolarDriveIntent | None = None
        self._intent_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._stream_close_lock = threading.Lock()
        self._connect_generation = 0
        self._connecting = False
        self._stopping = False
        self._request_lock = threading.Lock()
        self._stopper_thread: threading.Thread | None = None
        self._connector_thread: threading.Thread | None = None

        self._rx: deque[PolarSegment] = deque(maxlen=256)
        self._rx_lock = threading.Lock()
        self._rx_buffer = bytearray()

        self._zeros_until = 0.0
        self._estop_active = False
        #: 驾驶意图中的“底盘速度”走哪条上行通道（0x01 云台角度始终发）：
        #:   "robot_cmd" —— 0x01 speed_vector（需固件新增消费者，当前 master 没人读）
        #:   "virtual_rc" —— 0x03 key.v 位图（复用固件已生效的 WASD 驾驶，
        #:                    仅需固件补一处 VIRTUAL_RC_CTRL → rc_ctrl 接线）；
        #:                    此时 0x01 的 speed_vector 写零，避免两套斜坡叠加
        #:   "both"      —— 两路都带速度，便于固件升级前后行为一致
        #: 默认 robot_cmd：因为一旦固件把虚拟遥控器灌进 rc_ctrl，任何 0x03 帧
        #: （包括全零帧）都会冻结驾驶员的真实摇杆，所以 0x03 只能在驾驶已授权时发。
        self.drive_channel = "robot_cmd"
        #: 上一帧是否处于“已授权驾驶”；用于只在下降沿发一次全零 0x03
        self._drive_was_armed = False
        #: 当前显式自转指令（SPIN_CMD_*）。走独立的 0x04 通道，
        #: **不挂在 PolarDriveIntent 上**，理由有二：
        #:   1) 意图受 DEADMAN_TIMEOUT_S 看门狗约束，没人推杆时 intent 为 None，
        #:      自转指令就发不出去——而“只开自转、不用客户端开车”是合理诉求；
        #:   2) 0x04 不覆盖驾驶员物理遥控器（只维护 spin_flag），
        #:      不像 0x03 那样有“冻结真实摇杆”的风险，因此无需驾驶授权闸门。
        self._spin_cmd = SPIN_CMD_NONE
        self.spin_frames_sent = 0
        #: 云台直控请求位（0x04 param0 bit0）。与自转同理走电平语义、随 0x04
        #: 每 tick 刷新；急停/归零突发强制为 0（控制权还给驾驶员）。
        self._gimbal_host_request = 0
        #: 实际打开的设备名（"Auto" 解析后的结果）
        self.resolved_port = ""
        #: 收到段后的回调，交给 manager 做状态落库
        self.on_segment: Callable[[PolarSegment], None] | None = None

    # ------------------------------------------------------------------
    @property
    def link_age_s(self) -> float:
        if self.last_segment_at <= 0.0:
            return float("inf")
        return max(0.0, time.perf_counter() - self.last_segment_at)

    @property
    def link_alive(self) -> bool:
        """收到过下行段、且仍在 2 秒新鲜期内。"""

        return self.connected and self.last_segment_at > 0.0 and self.link_age_s <= 2.0

    # ------------------------------------------------------------------
    def _connection_config(self) -> PolarConnectionConfig:
        return PolarConnectionConfig(str(self.transport or "serial").strip().lower(), self.port,
                self.baud, self.host, self.tcp_port, self.stream_opener,
                self.mock_writes)

    def _claim_start_locked(self) -> tuple[int, PolarConnectionConfig] | None:
        if (self._stopping or self._connecting or self._running or self._serial is not None
                or (self._thread is not None and self._thread.is_alive())):
            return None
        self._connecting = True
        self._connect_generation += 1
        return self._connect_generation, self._connection_config()

    def start(self) -> None:
        """Synchronous compatibility API; UI callers should use request_start()."""
        with self._lifecycle_lock:
            attempt = self._claim_start_locked()
        if attempt is not None:
            self._connect_attempt(*attempt)

    def request_start(self) -> None:
        """Request connection without doing enumeration or I/O on the caller.

        At most one connector owns an open attempt, including after cancellation.
        Callers may retry once it finishes; requests never queue additional opens.
        """
        if not self._lifecycle_lock.acquire(blocking=False):
            return
        try:
            attempt = self._claim_start_locked()
            if attempt is None:
                return
            worker = None
            try:
                worker = threading.Thread(target=self._connect_attempt, args=attempt,
                                          name="polar-connect", daemon=True)
                self._connector_thread = worker
                worker.start()
            except Exception as exc:
                # A start wrapper can raise after admitting native open work.
                # Revoke publication but retain its reservation until cleanup.
                self._connect_generation += 1
                if worker is None or worker.ident is None:
                    self._connecting = False
                    self._connector_thread = None
                self.last_error = str(exc)
                self.last_error_code = "THREAD_START_FAILED"
        finally:
            self._lifecycle_lock.release()

    def _connect_attempt(self, generation: int, config: PolarConnectionConfig) -> None:
        # Enumeration and native open may block; the connector reservation,
        # rather than the lifecycle lock, protects this unpublished owner.
        result = acquire_polar_connection(config, resolve_port=resolve_polar_port)
        opened = result.resource
        try:
            with self._lifecycle_lock:
                # A stop or configuration change revokes ownership. The local
                # handle has never been visible to TX and must simply close.
                if (self._stopping or generation != self._connect_generation
                        or config != self._connection_config()):
                    return
                self.resolved_port = result.resolved_port
                self.last_error = result.error
                self.last_error_code = result.error_code
                if result.error_code:
                    self.connected = False
                    return
                self._serial, opened = opened, None
                self.connected = True
                self._rx_buffer.clear()
                with self._rx_lock:
                    self._rx.clear()
                self.last_segment_at = 0.0
                with self._intent_lock:
                    self._intent = None
                    self._spin_cmd = SPIN_CMD_NONE
                    self._gimbal_host_request = 0
                    self._drive_was_armed = False
                self._running = True
                self._stop.clear()
                worker = None
                try:
                    worker = threading.Thread(target=self._loop, name="polar-link", daemon=True)
                    self._thread = worker
                    worker.start()
                except Exception as exc:
                    self._running = False
                    self.connected = False
                    self._stop.set()
                    self.last_error = str(exc)
                    self.last_error_code = "THREAD_START_FAILED"
                    if worker is None or worker.ident is None:
                        self._thread = None
                        self._close_failed_stream()
                    # An admitted writer may still be inside native read/TX.
                    # Its existing finally owns close; retaining the thread and
                    # stream keeps replacement blocked until that cleanup exits.
        finally:
            # Keep ownership until the rejected/failed handle has been closed.
            if opened is not None:
                try:
                    close_resource(opened)
                except Exception as exc:
                    with self._lifecycle_lock:
                        # No replacement is admitted while this connector is
                        # active. Preserve even a rejected/stale open owner.
                        self._serial = opened
                        self.connected = False
                        self._close_error = f"Rejected robot stream close unconfirmed: {exc}"
                        self.last_error = self._close_error
                        self.last_error_code = "STREAM_CLOSE_FAILED"
            with self._lifecycle_lock:
                self._connecting = False
                self._connector_thread = None

    def request_estop(self) -> None:
        """Latch neutral output without waiting for a stream write or flushing.

        A write already inside the driver cannot be recalled. The TX worker
        observes this latch before subsequent writes and sends the zero burst.
        Do not take _intent_lock here: that lock deliberately spans driver I/O.
        """
        self._estop_active = True
        self._intent = None
        self._spin_cmd = SPIN_CMD_OFF
        self._gimbal_host_request = 0
        self._zeros_until = time.perf_counter() + ZERO_BURST_S

    def request_stop(self) -> None:
        """Latch now and close on one bounded cleanup worker, never on the UI."""
        self.request_estop()
        with self._request_lock:
            if self._stopping:
                return
            self._stopping = True
            worker = None
            try:
                worker = threading.Thread(target=self._finish_requested_stop,
                                          name="polar-close", daemon=True)
                self._stopper_thread = worker
                worker.start()
            except Exception as exc:
                # The admitted stopper alone releases its reservation, even
                # if Thread.start reports failure after launching it.
                if worker is None or worker.ident is None:
                    self._stopping = False
                    self._stopper_thread = None
                self.last_error = str(exc)
                self.last_error_code = "THREAD_START_FAILED"

    def _finish_requested_stop(self) -> None:
        try:
            self.stop()
        finally:
            with self._request_lock:
                self._stopping = False
                self._stopper_thread = None

    def stop(self) -> None:
        """先归零再断开；旧工作线程完全退出前禁止开启新连接。"""
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._connect_generation += 1
        # Also discard controls submitted while an open was in flight.
        with self._intent_lock:
            self._intent = None
            self._spin_cmd = SPIN_CMD_NONE
            self._gimbal_host_request = 0

        if self._running or self._serial is not None:
            # Latch before flushing, so the background writer cannot put an
            # old non-neutral intent back on the wire between the zero burst
            # and close(). The latch is released explicitly by the manager.
            self.trigger_estop()
            self._flush_zero_burst(blocking=True)
        self._stop.set()
        self._running = False
        worker = self._thread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        if worker is None or not worker.is_alive():
            self._thread = None
        self._close_failed_stream()
        self.connected = False

    # ------------------------------------------------------------------
    def submit(self, intent: PolarDriveIntent) -> None:
        """UI/tick 侧投递一帧意图；非阻塞，最后一帧覆盖前一行。"""

        try:
            values = {name: float(getattr(intent, name)) for name in (
                "vx", "vy", "wz", "gimbal_pitch_rad", "gimbal_yaw_rad", "produced_at")}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("驾驶意图含非有限数值")
            for name, value in values.items():
                if name != "produced_at":
                    struct.pack("<f", value)  # finite doubles may overflow the wire float32
            if values["produced_at"] > time.perf_counter():
                raise ValueError("驾驶意图时间不能在未来")
            key_value = int(intent.key_value)
            if not 0 <= key_value <= 0xFFFF:
                raise ValueError("驾驶键位超出 uint16 范围")
            snapshot = replace(intent, **values, key_value=key_value)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            # Rejecting a bad replacement must also neutralize the previous
            # valid intent: firmware may retain the last accepted command.
            self.trigger_estop()
            self.last_error = str(exc)
            self.last_error_code = "INVALID_INTENT"
            return
        with self._intent_lock:
            self._intent = snapshot

    def clear_intent(self) -> None:
        with self._intent_lock:
            self._intent = None

    def set_spin_command(self, spin_cmd: int) -> None:
        """设定显式自转指令（SPIN_CMD_NONE / ON / OFF）。

        语义是**设定**而不是翻转：调用方直接说“我要开”或“我要关”，
        不需要知道机器人当前在不在转。这是本次修正的核心——
        原来借 0x03 的 SHIFT 键位，固件是边沿翻转，只能开、关不掉。

        未知取值归为 NONE（不干预），与编码器、固件三处一致。
        """

        try:
            cmd = int(spin_cmd)
        except (TypeError, ValueError, OverflowError):
            cmd = SPIN_CMD_NONE
        if cmd not in (SPIN_CMD_NONE, SPIN_CMD_ON, SPIN_CMD_OFF):
            cmd = SPIN_CMD_NONE
        with self._intent_lock:
            self._spin_cmd = cmd

    def spin_command(self) -> int:
        with self._intent_lock:
            return int(self._spin_cmd)

    def set_gimbal_host_request(self, active: bool) -> None:
        """0x04 param0 bit0：请求云台直控（电平语义，随 0x04 每 tick 刷新）。

        只有打了 gimbal_host 桥的固件才认识这个位（中间档 + 请求 ->
        GIMBAL_AUTO_AIM）；未打桥的固件忽略该字节，发它不改变老固件行为。
        新鲜度沿用 spin_cmd 的 150ms 门控：客户端崩溃/拔线后请求自动失效，
        云台控制权回到驾驶员手里——与自转指令同一条安全逻辑。
        """
        with self._intent_lock:
            self._gimbal_host_request = 1 if active else 0

    def gimbal_host_request(self) -> int:
        with self._intent_lock:
            return int(self._gimbal_host_request)

    def trigger_estop(self) -> None:
        """急停：立刻归零并停止后续控制输出。

        置标志与清意图必须在同一把锁里：否则发送线程可能已经取了非零意图、
        卡在写串口那一步，导致急停后仍漏出一帧带速度的指令。
        """

        with self._intent_lock:
            self._estop_active = True
            self._intent = None
            # 急停必须主动关自转：固件在指令过期时是“保持现状”（防御姿态
            # 不宜因上位机断线而突停），所以光停发不够，得显式发 OFF。
            self._spin_cmd = SPIN_CMD_OFF
            self._gimbal_host_request = 0  # 急停：云台直控请求一并撤回
        self._zeros_until = time.perf_counter() + ZERO_BURST_S
        self._flush_zero_burst(blocking=False)

    def release_estop(self) -> None:
        with self._intent_lock:
            if not self.timestamp.roll_detected:
                self._estop_active = False

    def set_send_hz(self, hz: float) -> bool:
        """运行中改发送频率。返回是否真的变了。

        为什么需要这个方法而不是直接赋值：`send_hz` 必须限幅
        （5..MAX_SEND_HZ）。直接赋值会让调用方绕过限幅，而频率过高
        会淹没下行段解析、过低会让固件的 150ms 新鲜度门控误判掉线。
        """

        clamped = max(5.0, min(float(hz), MAX_SEND_HZ))
        if abs(clamped - self.send_hz) < 1e-6:
            return False
        self.send_hz = clamped
        return True

    # ------------------------------------------------------------------
    def _loop(self) -> None:  # pragma: no cover - 需要真实串口
        # 周期在循环内重算，不是在循环外算一次。
        # 原来 `interval = 1.0 / self.send_hz` 写在 while 之前，意味着
        # 运行中改 send_hz 完全不生效——必须重启链路。而界面上的
        # “Send Hz” 滑块是一个运行期设置，算死了就等于那个滑块是装饰品。
        try:
            next_send = time.perf_counter()
            while not self._stop.is_set():
                interval = 1.0 / self.send_hz
                try:
                    self._drain_rx()
                except Exception as exc:
                    self.last_error = str(exc)
                    self.last_error_code = "STREAM_DISCONNECTED"
                    break
                now = time.perf_counter()
                if now >= next_send:
                    next_send = now + interval
                    try:
                        self._pump_tx(now)
                    except Exception as exc:
                        self.last_error = str(exc)
                        break
                    if not self.connected:
                        break
                time.sleep(max(0.0, min(interval * 0.25, next_send - time.perf_counter())))
        finally:
            # Discard all receive/control state from this connection. Reconnect
            # must not reuse a buffered partial frame or an old driving intent.
            self.connected = False
            self.last_segment_at = 0.0
            self._rx_buffer.clear()
            with self._rx_lock:
                self._rx.clear()
            with self._intent_lock:
                self._intent = None
                self._spin_cmd = SPIN_CMD_NONE
                self._gimbal_host_request = 0
                self._drive_was_armed = False
            self._close_failed_stream()
            self._running = False

    def _drain_rx(self) -> None:
        stream = self._serial
        if stream is None:
            return
        chunk = stream.read(512)
        if self._serial is not stream or self._stop.is_set():
            return
        if not getattr(stream, "is_open", True):
            raise ConnectionError("机器人字节流已断开")
        if not chunk:
            return
        self._rx_buffer.extend(chunk)
        if len(self._rx_buffer) > MAX_RETAINED_BYTES:
            # 异常输入：丢掉最老的一部分，避免无限增长
            del self._rx_buffer[: len(self._rx_buffer) - MAX_RETAINED_BYTES]
            self.codec.stats.buffer_overflows += 1
        segments, consumed = self.codec.decode_with_consumed(bytes(self._rx_buffer))
        if consumed:
            del self._rx_buffer[:consumed]
        if segments:
            now = time.perf_counter()
            with self._rx_lock:
                for segment in segments:
                    self._rx.append(segment)
                    self.last_segment_at = now
                    self.codec.stats.frames_ok += 0  # codec 已计数
            if self.on_segment is not None:
                for segment in segments:
                    try:
                        self.on_segment(segment)
                    except Exception as exc:  # 单个坏段不打断接收
                        self.last_error = f"on_segment: {exc}"

    def _pump_tx(self, now: float) -> None:
        if self._estop_active or now < self._zeros_until:
            # 急停/归零突发窗口：有界（ZERO_BURST_S），故意把两路都按零
            self._flush_zero_burst(blocking=False)
            return
        # 持锁完成“取意图之后的全部写出”：与 trigger_estop 互斥，
        # 消除“已取到非零意图、写入前被急停插入”的窗口。
        # 注意：intent 为 None 不能在这里早退去无条件补发 0x03 归零帧——
        # 持续发全零 0x03 会冻结驾驶员真实摇杆。必须走下面同一套
        # “仅下降沿发一次”的逻辑。
        with self._intent_lock:
            if self._estop_active:
                return
            # Snapshot and freshness check belong to the same critical section
            # as writes; clear_intent must invalidate an unsent snapshot.
            intent = self._take_intent_locked(max(now, time.perf_counter()))
            self._write_after_intent_held(now, intent)
            # 0x04 与驾驶意图无关，即使 intent 为 None（没人推杆）也要发：
            # 否则“只开自转、不用客户端开车”这个诉求根本发不出去。
            self._pump_spin_locked()

    def _write_after_intent_held(self, now: float, intent: PolarDriveIntent | None) -> None:
        armed = bool(
            intent is not None
            and (intent.vx or intent.vy or intent.wz or intent.key_value
                 or intent.gimbal_yaw_rad or intent.gimbal_pitch_rad)
        )
        wants_rc = self.drive_channel in ("virtual_rc", "both")

        # 0x01 **始终发**：云台角度只存在于这一帧（0x03 的 RC_ctrl_t 没有云台字段），
        # 不发 0x01 会让云台控制静默失效。但 virtual_rc 纯通道下把 speed_vector
        # 写零，避免与 key.v 通路同时驱动底盘造成两套斜坡叠加。
        if intent is not None:
            if self.drive_channel == "virtual_rc":
                self._write(self._frame_for(
                    PolarDriveIntent(
                        gimbal_pitch_rad=intent.gimbal_pitch_rad,
                        gimbal_yaw_rad=intent.gimbal_yaw_rad,
                        tracking=intent.tracking,
                    )
                ))
            else:
                self._write(self._frame_for(intent))
        else:
            self._write(self._zero_frame())

        if intent is not None and time.perf_counter() - intent.produced_at > DEADMAN_TIMEOUT_S:
            # Even a successful write may have consumed the remaining intent
            # lifetime. Never append stale RC keys after backpressure.
            intent = None
            armed = False
            self._write(self._zero_frame(), zero_frame=True)

        # 0x03：会覆盖驾驶员真实摇杆，所以只在已授权期间发；
        # 松杆后仅在下降沿补一次全零，之后彻底不再占用该通道。
        if wants_rc and armed and intent is not None:
            self._write(self._virtual_rc_frame_for(intent))
        was_armed = self._drive_was_armed
        if was_armed and not (wants_rc and armed):
            before_rc = self.frames_sent
            self._write(self._zero_virtual_rc_frame(), zero_frame=True)
            # An attempted/short release does not establish a neutral wire.
            self._drive_was_armed = not (self.mock_writes or self.frames_sent > before_rc)
        else:
            self._drive_was_armed = bool(wants_rc and armed)

    def _take_intent_locked(self, now: float) -> PolarDriveIntent | None:
        with self._intent_lock:
            intent = self._intent
        if intent is None:
            return None
        if now - intent.produced_at > DEADMAN_TIMEOUT_S:
            return None  # 本地看门狗：意图过期即归零
        return intent

    def _frame_for(self, intent: PolarDriveIntent) -> bytes:
        return self.codec.encode_robot_cmd(
            raw_timestamp=self.timestamp.next(),
            gimbal_pitch_rad=float(intent.gimbal_pitch_rad),
            gimbal_yaw_rad=float(intent.gimbal_yaw_rad),
            chassis_vx=float(intent.vx),
            chassis_vy=float(intent.vy),
            chassis_wz=float(intent.wz),
            friction_on=bool(intent.friction_on),
            tracking=bool(intent.tracking),
        )

    def _zero_frame(self) -> bytes:
        return self.codec.encode_robot_cmd(
            raw_timestamp=self.timestamp.next(),
            gimbal_pitch_rad=0.0,
            gimbal_yaw_rad=0.0,
        )

    def _virtual_rc_frame_for(self, intent: PolarDriveIntent) -> bytes:
        return self.codec.encode_virtual_rc(
            raw_timestamp=self.timestamp.next(),
            key_value=int(intent.key_value) & 0xFFFF,
            key_mask=drive_key_mask(bool(getattr(intent, "allow_spin_key", False))),
        )

    def _zero_virtual_rc_frame(self) -> bytes:
        return self.codec.encode_virtual_rc(raw_timestamp=self.timestamp.next(), key_value=0)

    def _chassis_cmd_frame(self, spin_cmd: int, *, gimbal_request: bool | None = None) -> bytes:
        if gimbal_request is None:
            gimbal_request = bool(self._gimbal_host_request)
        return self.codec.encode_chassis_cmd(
            raw_timestamp=self.timestamp.next(), spin_cmd=int(spin_cmd),
            param0=GIMBAL_HOST_REQUEST_ON if gimbal_request else 0)

    def _pump_spin_locked(self) -> None:
        """每 tick 重发当前自转指令（**电平**语义）。调用方需已持 _intent_lock。

        为什么不能只在状态变化时发一次：固件侧 HOST_SPIN_CMD_VALID_MS=150ms
        内没刷新就把指令收回 NONE、停止强制。只发一次的话，自转会在
        150ms 后“失控”（虽然 spin_flag 保持，但驾驶员遥控器一碰就变，
        客户端再想关也发不出去）。
        """

        before = self.frames_sent
        self._write(self._chassis_cmd_frame(self._spin_cmd))
        if self.frames_sent > before:
            self.spin_frames_sent += 1

    def _write(self, payload: bytes, *, zero_frame: bool = False) -> None:
        # 急停竞态兜底：`_pump_tx` 开头的检查与本行之间仍有时间窗口，
        # 如果急停刚好发生在这里，非零帧必须被拒。归零帧不受限。
        # （与下面持锁写配合：锁把“取意图→写”串行化，本行是第 2 道防线）
        if self._estop_active and not zero_frame:
            self.dropped_by_estop = getattr(self, "dropped_by_estop", 0) + 1
            return
        if self.timestamp.roll_detected:
            self._estop_active = True
            self._intent = None
            self.connected = False
            self.last_error = "时间戳回绕，停止输出；需重启机器人后重新连接"
            self.last_error_code = "TIMESTAMP_ROLLOVER"
            return
        if self.mock_writes:
            return
        if self._serial is None or not self.connected:
            if not self.last_error_code:
                self.last_error = "机器人串口未连接"
                self.last_error_code = "NOT_CONNECTED"
            return
        try:
            written = self._serial.write(payload)
            if written != len(payload):
                self.last_error = f"串口短写 {written}/{len(payload)}"
                self.last_error_code = "STREAM_SHORT_WRITE"
                self._close_failed_stream()
                return
            self.frames_sent += 1
            self.last_send_at = time.perf_counter()
        except Exception as exc:  # pragma: no cover
            self.last_error = str(exc)
            self.last_error_code = "STREAM_WRITE_FAILED"
            self._close_failed_stream()

    @property
    def cleanup_complete(self) -> bool:
        return (self._serial is None and not self._running and not self._connecting
                and (self._thread is None or not self._thread.is_alive())
                and (self._stopper_thread is None or not self._stopper_thread.is_alive()))

    def _close_failed_stream(self) -> None:
        with self._stream_close_lock:
            stream = self._serial
            self.connected = False
            if stream is not None:
                try:
                    close_resource(stream)
                except Exception as exc:
                    self._close_error = f"Robot stream close unconfirmed: {type(exc).__name__}: {exc}"
                    self.last_error = self._close_error
                    self.last_error_code = "STREAM_CLOSE_FAILED"
                    return
                self._serial = None
            self._close_error = ""

    def _flush_zero_burst(self, *, blocking: bool) -> None:
        """尽力连续发送零帧；断链/短写时不能保证固件收到或执行停车。

        硬件部署必须有独立的固件过期归零看门狗；时间戳回绕时停止发送
        也不能证明最后一条执行器指令已清除。
        """

        count = 3 if blocking else 1
        for _ in range(count):
            with self._intent_lock:
                before = self.frames_sent
                self._write(self._zero_frame(), zero_frame=True)
                if self.frames_sent > before:
                    self.zero_frames_sent += 1
                # Only release a previously active RC writer. Repeated zero
                # 0x03 frames would otherwise overwrite the physical joystick.
                if self._drive_was_armed:
                    before_rc = self.frames_sent
                    self._write(self._zero_virtual_rc_frame(), zero_frame=True)
                    if self.mock_writes or self.frames_sent > before_rc:
                        self._drive_was_armed = False
                self._write(self._chassis_cmd_frame(SPIN_CMD_OFF, gimbal_request=False), zero_frame=True)
            if blocking and count > 1:
                time.sleep(0.005)

    # ------------------------------------------------------------------
    def drain_segments(self) -> list[PolarSegment]:
        with self._rx_lock:
            segments = list(self._rx)
            self._rx.clear()
        return segments

    def stats(self) -> dict[str, Any]:
        # 注意：本 dataclass 带 slots=True，没 __dict__，必须走 asdict()
        data = asdict(
            PolarLinkStats(
                connected=self.connected,
                mock_writes=self.mock_writes,
                port=self.port,
                resolved_port=self.resolved_port,
                last_error_code=self.last_error_code,
                baud=self.baud,
                frames_sent=self.frames_sent,
                zero_frames_sent=self.zero_frames_sent,
                spin_frames_sent=self.spin_frames_sent,
                segments_received=self.codec.stats.frames_ok,
                send_hz=self.send_hz,
                last_send_at=self.last_send_at,
                last_segment_at=self.last_segment_at,
                last_error=self.last_error,
                link_age_s=self.link_age_s,
            )
        )
        # 把现场探测到的口一并上报，HUD/预检能直接告诉操作员该选哪个
        data["detected_ports"] = describe_ports()
        return data


__all__ = [
    "DEADMAN_TIMEOUT_S",
    "DEFAULT_SEND_HZ",
    "PolarDriveIntent",
    "PolarLinkTransport",
    "PolarTimestamp",
    "deg_to_rad",
]
