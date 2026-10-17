#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""自定义控制器出口：经**官方选手端**把键鼠灌进 DT7，再走空口到车。

为什么是这条路
--------------
用户（电控）的记忆是"电脑连 DT7 就能开车"。把官方选手端扒开看之后，
真实机制是这样的（全部有出处）：

1. 《RoboMaster 2026 机甲大师高校系列赛通信协议》表 1-1（:90-94）：
   `电源管理模块←→机器人 115200` / **`自定义控制器←→选手端 115200`** /
   `图传发送端←→机器人 921600`
   —— 也就是说**自定义控制器对选手端就是一个 115200 串口设备**。
2. 同文档 1.5 节（:1372）：**"操作手可使用自定义控制器模拟键鼠操作选手端。"**
3. 同文档 表 1-43 命令码 0x0306（:1373-1398）给出这 8 个字节的**完整布局**
   （键盘两个"通用键值" + 鼠标绝对像素坐标 + 左右键，见 `encode_keymouse_0306`）。
4. 选手端内部实现印证：元数据里有 `CCSPortThread` / `GetCCSSerialPort` /
   `Keybd_event` / `Mouse_event` / `SetCursorPos` / `virtualKey`
   —— 它把 0x0306 **翻译成系统键鼠事件**，再由它自己塞进 DT7 的键鼠字段
   （日志原话 `[Game] 收到0306数据包`、`0306数据为KeyValue1:{1},KeyValue2:{2},
   x:{3},y:{4},LB:{5},RB:{6},reserved:{7}`、
   `Input_Device_RC Sent x:{1}, y:{2}, z:{3}, LB:{4}, RB:{5}, KeyValue:{6}`）。

⇒ 所以**不需要逆向 DT7 的 USB**：官方给了一个文档化的串口命令 0x0306，
选手端负责最后那一跳。这条路完全不走车上的 921600，
因此也不受"图传发送端有没有接到 C 板"影响。

帧格式（与队内固件逐字节核对，不是照抄文档措辞）
------------------------------------------------
`referee.c:128-133` + `referee_usart_task.c:124-169` + `protocol.h:8-14`：

    SOF(1)=0xA5 | data_length(2,LE) | seq(1) | CRC8(1) | cmd_id(2,LE) | data(n) | CRC16(2,LE)

* `data_length` **只算 cmd_id 之后的 data**，不含 cmd_id：
  `REF_HEADER_CRC_CMDID_LEN = 5 + 2 + 2 = 9`，收包结束条件是
  `index == 9 + data_len`，整帧 = 5 + 2 + n + 2 = 9 + n ⇒ `data_len == n`。
* CRC8 覆盖帧头前 4 字节（`verify_CRC8_check_sum(packet, 5)` 内部算前 5-1 字节）。
* CRC16 覆盖整帧前 `9 + n` 去掉尾部 2 字节，即 header+cmd_id+data。
* 串口 8 数据位 / 1 停止位 / 无校验（表 1-1 上方的 :88）。

现场怎么接
----------
选手端那台电脑需要一个"自定义控制器"串口。两种做法：
* 真硬件：USB 转 TTL 插选手端机（或自制板上跑我们这段帧），本模块直接写那个 COM 口；
* 同一台机器：com0com 建虚拟串口对，本模块写 A 端、选手端读 B 端
  （前提是选手端按端口名/自动枚举挑口，见 `DeviceInfo.GetCCSSerialPort`，
   这条**必须在现场验一次**，本模块不假装已经验过）。
"""
from __future__ import annotations

import json
import struct
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

from ..protocol import crc8, crc16
from .resource_close import close_resource
from .writer_thread import WriterThread

# --- 命令码（《2026 通信协议》表 1-5 / 表 1-43） ---
CC_CMD_ROBOT_INTERACT = 0x0302     # 自定义控制器 <-> 机器人，30 字节，<=30Hz，图传链路
CC_CMD_KEYMOUSE = 0x0306           # 自定义控制器 -> 选手端，8 字节，模拟键鼠
SOF = 0xA5
HEADER_SIZE = 5
CRC16_SIZE = 2
CMD_ID_SIZE = 2
KEYMOUSE_PAYLOAD_SIZE = 8
MAX_DATA_LENGTH = 128 - HEADER_SIZE - CRC16_SIZE - CMD_ID_SIZE   # protocol.h:8

# --- "通用键值"：文档只写"使用通用键值"，没给表；这里按 USB HID Usage Table
#     (Usage Page 0x07) 填，并**必须在现场用 0x0306 一个键一个键验一遍**再用。
#     选手端还有一句"仅响应选手端开放的按键"，所以能生效的键是白名单。 ---
HID_USAGE: dict[str, int] = {
    "NONE": 0x00,
    "A": 0x04, "B": 0x05, "C": 0x06, "D": 0x07, "E": 0x08, "F": 0x09, "G": 0x0A,
    "H": 0x0B, "I": 0x0C, "J": 0x0D, "K": 0x0E, "L": 0x0F, "M": 0x10, "N": 0x11,
    "O": 0x12, "P": 0x13, "Q": 0x14, "R": 0x15, "S": 0x16, "T": 0x17, "U": 0x18,
    "V": 0x19, "W": 0x1A, "X": 0x1B, "Y": 0x1C, "Z": 0x1D,
    "CTRL": 0xE0, "SHIFT": 0xE1, "ALT": 0xE2, "GUI": 0xE3,
    "SPACE": 0x2C, "ENTER": 0x28, "TAB": 0x2B, "ESC": 0x29,
}

#: 现场可改的键值表（**不用改代码**）：文档只写"使用通用键值"没附表，
#: 真值要靠 `cc_probe.py --sweep` 逐键试出来，改 configs/cc_key_usage.json 即可。
KEY_USAGE_FILE = Path(__file__).resolve().parents[2] / "configs" / "cc_key_usage.json"


def load_key_usage(path: str | None = None) -> dict[str, int]:
    """读键值表；文件不存在或坏值就退回内置 HID 默认，**绝不因为配置坏而发不出键**。"""
    table = dict(HID_USAGE)
    fp = Path(path) if path else KEY_USAGE_FILE
    try:
        raw = json.loads(fp.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return table
    except Exception:
        return table
    if not isinstance(raw, dict):
        return table
    for k, v in raw.items():
        key = str(k).upper()
        if key.startswith("_") or isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 0xFF:
            continue
        table[key] = v
    return table


#: 选手端使用的屏幕分辨率（表 1-43 备注：赛事客户端 1920x1080，左上角 (0,0)）
SCREEN_W = 1920
SCREEN_H = 1080
#: 0x0302 官方频率上限 30Hz；0x0306 文档未给上限，选手端会丢超频包
#  （日志原话"自定义控制器0306收包超频，丢弃多余包！"），所以这里保守同 30Hz。
DEFAULT_MAX_HZ = 30.0


def encode_cc_frame(cmd_id: int, payload: bytes, seq: int) -> bytes:
    """拼一帧"自定义控制器 -> 选手端"的 0xA5 包。

    `data_length` 只算 cmd_id 之后的载荷（见模块文档里那步算术）。
    """
    payload = bytes(payload)
    if isinstance(cmd_id, bool) or not isinstance(cmd_id, int) or not 0 <= cmd_id <= 0xFFFF:
        raise ValueError(f"cmd_id 超出 2 字节：{cmd_id!r}")
    if len(payload) > MAX_DATA_LENGTH:
        raise ValueError(f"载荷 {len(payload)} 字节，超过 {MAX_DATA_LENGTH}")
    header = bytearray()
    header.append(SOF)
    header += struct.pack("<H", len(payload))
    header.append(seq & 0xFF)
    header.append(crc8(bytes(header)))            # CRC8 只覆盖前 4 字节
    frame = bytes(header) + struct.pack("<H", cmd_id) + payload
    frame += struct.pack("<H", crc16(frame))      # CRC16 覆盖 header+cmd_id+data
    return frame


def encode_keymouse_0306(*, key1: int = 0, key2: int = 0,
                         x: int = 0, y: int = 0,
                         left: bool = False, right: bool = False) -> bytes:
    """表 1-43：8 字节 = 键值1/键值2 + 鼠标X+左键 + 鼠标Y+右键 + 保留 2 字节。

    注意两个坑，都是这份文档自己写明的：
      * 鼠标是**绝对像素坐标**（不是增量），所以调用方必须自己把
        相对位移攒成绝对位置，见 `VirtualCursor`；
      * 只有 2 键无冲，按下第 3 个键必须丢掉一个（这里保留先到的两个不同键值，
        并在 `keys_to_two_usages` 里把溢出报出来，绝不静默丢驾驶指令）。
    """
    if not (0 <= key1 <= 0xFF and 0 <= key2 <= 0xFF):
        raise ValueError(f"键值必须是 1 字节通用键值：{key1!r}, {key2!r}")
    if not (0 <= x < (1 << 12) and 0 <= y < (1 << 12)):
        raise ValueError(f"鼠标坐标必须落在 12 bit 内：{x!r}, {y!r}")
    b0 = struct.pack("<H", (key1 & 0xFF) | ((key2 & 0xFF) << 8))
    b1 = struct.pack("<H", (x & 0x0FFF) | (0x1000 if left else 0))
    b2 = struct.pack("<H", (y & 0x0FFF) | (0x1000 if right else 0))
    b3 = b"\x00\x00"
    out = b0 + b1 + b2 + b3
    assert len(out) == KEYMOUSE_PAYLOAD_SIZE, len(out)
    return out


def keys_to_two_usages(names, table=None) -> tuple[int, int, list[str]]:
    """按键名集合 -> (键值1, 键值2, 被挤掉的键)。

    2 键无冲是**官方限制**，不是我们的选择；把被挤掉的键报出来，
    是因为"三键组合"在驾驶里很常见（比如 W + A + SHIFT），
    静默丢掉第三个键 = 驾驶员想小陀螺却没进，现场极难查。
    """
    codes: list[int] = []
    seen: set[int] = set()
    dropped: list[str] = []
    if table is None:
        table = load_key_usage()
    for name in names:
        key = str(name).upper()
        if not key or key == "NONE":
            continue
        code = table.get(key)
        if code is None or code == 0:
            dropped.append(key)
            continue
        if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 0xFF:
            dropped.append(key)
            continue
        if code not in seen:
            seen.add(code)
            if len(codes) < 2:
                codes.append(code)
            else:
                dropped.append(key)
    while len(codes) < 2:
        codes.append(0)
    return codes[0], codes[1], dropped


class VirtualCursor:
    """相对位移 -> 选手端要的绝对像素坐标（1920x1080，左上角 0,0）。"""

    def __init__(self, x: int = SCREEN_W // 2, y: int = SCREEN_H // 2,
                 width: int = SCREEN_W, height: int = SCREEN_H) -> None:
        self.x = int(x)
        self.y = int(y)
        self.width = int(width)
        self.height = int(height)

    def move(self, dx: int, dy: int) -> tuple[int, int]:
        self.x = max(0, min(self.width - 1, self.x + int(dx)))
        self.y = max(0, min(self.height - 1, self.y + int(dy)))
        return self.x, self.y

    def reset(self) -> None:
        self.x = self.width // 2
        self.y = self.height // 2


@dataclass
class CustomControllerLink:
    """把键鼠/控制数据写进"自定义控制器"串口的发送器。

    `write_bytes=None` 时用 pyserial 打开 `port`；测试里注入假函数即可全程离线。
    `mock_output=True` 时一字节都不写硬件，只记最后帧（现场默认先跑 mock 看帧）。
    """

    port: str = "COM3"
    baudrate: int = 115200
    max_hz: float = DEFAULT_MAX_HZ
    mock_output: bool = True
    write_bytes: Callable[[bytes], Any] | None = None
    time_fn: Callable[[], float] = time.perf_counter

    seq: int = 0
    sent: int = 0
    resolved_port: str = ""
    dropped_rate: int = 0
    last_error: str = ""
    last_frame: bytes = b""
    last_keys: tuple[int, int] = (0, 0)
    dropped_keys: list[str] = field(default_factory=list)
    _next_due: float = 0.0
    wire_neutral: bool = True
    cursor: VirtualCursor = field(default_factory=VirtualCursor)

    # ---- 频率控制 -------------------------------------------------
    def _rate_gate(self, now: float) -> bool:
        interval = 1.0 / max(1.0, float(self.max_hz))
        if now + 1e-9 < self._next_due:
            self.dropped_rate += 1
            return False
        self._next_due = now + interval
        return True

    def _send(self, cmd_id: int, payload: bytes, now: float, *,
              neutral_release: bool = False) -> tuple[bool, str]:
        if neutral_release:
            # The bypass is available only for an internally encoded key and
            # button release; it cannot carry a non-neutral command.
            if cmd_id != CC_CMD_KEYMOUSE or len(payload) != KEYMOUSE_PAYLOAD_SIZE \
                    or payload[:2] != b"\x00\x00" \
                    or payload[3] & 0xF0 or payload[5] & 0xF0 or payload[6:] != b"\x00\x00":
                raise ValueError("CC rate bypass only accepts neutral key/button release")
            self._next_due = max(self._next_due, now + 1.0 / max(1.0, float(self.max_hz)))
        elif not self._rate_gate(now):
            return False, f"超过 {self.max_hz:g}Hz 上限，本帧已丢弃"
        self.seq = (self.seq + 1) & 0xFF
        try:
            frame = encode_cc_frame(cmd_id, payload, self.seq)
        except ValueError as exc:
            self.last_error = str(exc)
            return False, str(exc)
        self.last_frame = frame
        if not self.mock_output:
            try:
                if self.write_bytes is not None:
                    written = self.write_bytes(frame)
                    if written is not None and written != len(frame):
                        raise IOError(f"CC 串口短写 {written}/{len(frame)}")
                else:
                    import serial
                    from .. import serial_ports as ports
                    target = ports.resolve_cc_port(self.port)
                    if target == ports.STUB_PORT:
                        raise RuntimeError(
                            "没找到自定义控制器该用的 FTDI 口（选手端只认它）："
                            "插好线或在 CC Port 里显式选口")
                    self.resolved_port = target
                    with serial.Serial(target, self.baudrate, timeout=0.2, write_timeout=0.2) as sp:
                        written = sp.write(frame)
                        if written != len(frame):
                            raise IOError(f"CC 串口短写 {written}/{len(frame)}")
            except Exception as exc:
                self.last_error = str(exc)
                return False, str(exc)
        self.last_error = ""
        self.sent += 1
        return True, "mock" if self.mock_output else "ok"

    # ---- 对外两条出口 ---------------------------------------------
    def send_keymouse(self, *, pressed, dx: int = 0, dy: int = 0,
                      left: bool = False, right: bool = False,
                      now: float | None = None) -> tuple[bool, str]:
        """0x0306：键鼠。按下没松开时要**持续重发同一份键值**——
        表 1-43 原文"若无新的按键信息，将保持上一帧数据的按下状态"，
        但这是选手端的保持行为，不保证跨断连；所以我们自己按节拍刷。
        """
        now = self.time_fn() if now is None else float(now)
        key1, key2, dropped = keys_to_two_usages(pressed)
        x, y = self.cursor.move(dx, dy) if (dx or dy) else (self.cursor.x, self.cursor.y)
        self.last_keys = (key1, key2)
        self.dropped_keys = dropped
        payload = encode_keymouse_0306(key1=key1, key2=key2, x=x, y=y,
                                       left=left, right=right)
        neutral = not (key1 or key2 or left or right)
        if neutral and not self.wire_neutral:
            return self.release_all(now)
        if not neutral:
            # Failed/partial writes have an unknown outcome. Only a successful
            # release may certify neutral again.
            self.wire_neutral = False
        ok, why = self._send(CC_CMD_KEYMOUSE, payload, now)
        if ok:
            self.wire_neutral = neutral
        return ok, why

    def send_robot_interact(self, data: bytes, now: float | None = None
                            ) -> tuple[bool, str]:
        """0x0302：自定义控制器与机器人交互数据（30 字节，走图传链路）。

        这条**仍然依赖车上图传发送端接到 C 板**（表 1-1 的 921600），
        所以它的到达与否要用 0x0311 那套回读一起判，别当成 DT7 路。
        """
        now = self.time_fn() if now is None else float(now)
        data = bytes(data)
        if len(data) > 30:
            return False, f"0x0302 载荷最多 30 字节，实际 {len(data)}"
        return self._send(CC_CMD_ROBOT_INTERACT, data, now)

    def release_all(self, now: float | None = None) -> tuple[bool, str]:
        """一键松手：全零键值 + 保持当前光标位置（0 表示"无按键按下"）。"""
        now = self.time_fn() if now is None else float(now)
        ok, why = self._send(CC_CMD_KEYMOUSE,
                          encode_keymouse_0306(key1=0, key2=0,
                                               x=self.cursor.x, y=self.cursor.y),
                          now, neutral_release=True)
        self.wire_neutral = bool(ok)
        if ok:
            self.last_keys = (0, 0)
            self.dropped_keys = []
        return ok, why


def decode_cc_frame(frame: bytes) -> dict | None:
    """解一帧 0xA5 包（自检/回放抓包用；与 encode_cc_frame 严格互逆）。"""
    if len(frame) < HEADER_SIZE + CMD_ID_SIZE + CRC16_SIZE:
        return None
    if frame[0] != SOF:
        return None
    (data_len,) = struct.unpack_from("<H", frame, 1)
    if data_len > MAX_DATA_LENGTH or crc8(frame[:4]) != frame[4]:
        return None
    total = HEADER_SIZE + CMD_ID_SIZE + data_len + CRC16_SIZE
    if len(frame) != total:
        return None
    if crc16(frame[:total - CRC16_SIZE]) != struct.unpack_from("<H", frame, total - 2)[0]:
        return None
    (cmd_id,) = struct.unpack_from("<H", frame, HEADER_SIZE)
    return {"seq": frame[3], "cmd_id": cmd_id,
            "data": frame[HEADER_SIZE + CMD_ID_SIZE: HEADER_SIZE + CMD_ID_SIZE + data_len]}


class _StaleCCIntent(Exception):
    """An intent was invalidated while the driver was opening a port."""


class AsyncCustomControllerLink:
    """Single-owner CC writer with a one-slot, expiring intent mailbox.

    Enqueue methods never enumerate, open, write or join a serial driver. Their
    success means *accepted*, not delivered. ``release_all`` reports success
    only once neutral is confirmed. Neutral supersedes motion; failed writes
    discard every pre-failure intent and require a successful neutral barrier.
    A stuck OS driver can occupy this daemon, but never creates more workers.

    ``retain_owner`` may publish the initialized facade to its caller before
    worker admission. Keep that owner through a constructor failure until
    ``closed`` is confirmed. The original exception also receives a best-effort
    ``failed_writer`` attribute for callers using the original constructor API.
    The callback runs outside the worker lock. If it calls ``close()``, the
    constructor returns that sealed owner without admitting another writer.
    """

    asynchronous = True

    def __init__(self, port: str = "COM3", baudrate: int = 115200,
                 max_hz: float = DEFAULT_MAX_HZ, mock_output: bool = True,
                 write_bytes: Callable[[bytes], Any] | None = None,
                 time_fn: Callable[[], float] = time.perf_counter, *,
                 max_intent_age: float = 0.25, release_attempts: int = 3,
                 retry_interval: float = 0.05,
                 retain_owner: Callable[["AsyncCustomControllerLink"], None] | None = None) -> None:
        import threading
        self.queued = 0
        self.coalesced = 0
        self.failed = 0
        self.faulted = False
        self.fault_reason = ""
        self._fault_at = 0.0
        self._motion_deadline = 0.0
        self.port, self.baudrate = port, baudrate
        self.max_hz, self.mock_output = max_hz, mock_output
        self.time_fn = time_fn
        self.max_intent_age = max(0.001, float(max_intent_age))
        self.release_attempts = max(1, int(release_attempts))
        self.retry_interval = max(0.001, float(retry_interval))
        self.seq = self.sent = self.dropped_rate = 0
        self.resolved_port = self.last_error = ""
        self.last_frame = b""
        self.last_keys = (0, 0)
        self.dropped_keys = []
        self.cursor = VirtualCursor()
        self.wire_neutral = True
        self.release_pending = False
        self._condition = threading.Condition()
        self._pending = None
        self._epoch = 0
        self._release_left = 0
        self._release_due = 0.0
        self._closing = False
        self._stopped = False
        self._close_failed = False
        self._serial = None
        self._write_bytes = write_bytes
        self._active = None
        self._table = load_key_usage()
        self._link = CustomControllerLink(port=port, baudrate=baudrate,
            max_hz=max_hz, mock_output=mock_output,
            write_bytes=self._write_frame, time_fn=time_fn)
        self._worker = WriterThread(self._condition)
        self.startup_failed = False
        admission_reserved = False
        try:
            if retain_owner is not None:
                retain_owner(self)
            with self._condition:
                if self._closing:
                    return  # The retention callback already cancelled this owner.
                self._worker.reserve()
                admission_reserved = True
            self._worker.start_reserved(self._run, "cc-writer")
        except BaseException as exc:
            self.startup_failed = True
            self.faulted = True
            # Retention precedes admission; cancellation and best-effort
            # metadata cannot replace the original construction failure.
            try:
                with self._condition:
                    # Keep an already-finished failed finalizer for explicit
                    # retry, preserving its native-close diagnostic.
                    if not self._closing:
                        self.last_error = self.fault_reason = "CC writer admission failed; close required"
                    never_admitted = self._worker.never_admitted
                    if not self._closing or (admission_reserved and never_admitted):
                        self.close()
            except BaseException:
                pass
            try:
                exc.failed_writer = self
            except BaseException:
                pass
            raise

    @property
    def _thread(self):
        """Compatibility view of the sole retained worker (never a second owner)."""
        return self._worker.thread

    @property
    def worker_alive(self) -> bool:
        return self._worker.alive

    @property
    def closed(self) -> bool:
        return self._stopped

    @property
    def port_handoff_ready(self) -> bool:
        """Proof for another owner: neutral, no retained handle, worker gone."""
        return bool(
            self.closed and not self.worker_alive
            and self._serial is None and not self._close_failed
            and self.wire_neutral and not self.release_pending
        )

    def _queue(self, cmd_id, payload, *, neutral=False):
        with self._condition:
            if self._closing:
                return False, "CC writer closed"
            if neutral:
                return self.release_all()
            if self.faulted:
                return False, self.fault_reason or "CC failure latched; explicit re-arm required"
            if self.release_pending:
                return False, self.last_error or "CC neutral release pending"
            self.queued += 1
            self.coalesced += int(self._pending is not None)
            self._pending = (self._epoch, cmd_id, payload, self.time_fn())
            # An accepted motion may already be in the worker when a caller
            # decides to release. Never advertise neutral until that barrier.
            self.wire_neutral = False
            self._condition.notify()
            return True, "queued"

    def send_keymouse(self, *, pressed, dx=0, dy=0, left=False, right=False,
                      now=None):
        key1, key2, dropped = keys_to_two_usages(pressed, self._table)
        with self._condition:
            x, y = self.cursor.move(dx, dy)
            self.last_keys, self.dropped_keys = (key1, key2), dropped
            payload = encode_keymouse_0306(key1=key1, key2=key2, x=x, y=y,
                                           left=left, right=right)
            return self._queue(CC_CMD_KEYMOUSE, payload,
                               neutral=not (key1 or key2 or left or right))

    def send_robot_interact(self, data, now=None):
        data = bytes(data)
        if len(data) > 30:
            return False, f"0x0302 载荷最多 30 字节，实际 {len(data)}"
        return self._queue(CC_CMD_ROBOT_INTERACT, data)

    def _request_release(self):
        self._epoch += 1
        self._pending = None
        self.release_pending = True
        self.wire_neutral = False
        self._release_left = self.release_attempts
        self._release_due = 0.0
        self._condition.notify()

    def release_all(self, now=None):
        with self._condition:
            if self.wire_neutral and not self.release_pending:
                return True, "neutral"
            if not self.release_pending and not self._closing:
                self._request_release()
            return False, self.last_error or "CC neutral release pending"

    def retry_release(self):
        """Explicit operator retry after the bounded neutral budget is spent."""
        with self._condition:
            if self._closing:
                return False, "CC writer closed"
            if self.release_pending and not self._release_left:
                self._request_release()
            return self.release_all()

    def rearm(self, authorization_epoch):
        """Called only after a newer explicit manager arm and confirmed neutral."""
        with self._condition:
            if (self._closing or self.release_pending or not self.wire_neutral
                    or float(authorization_epoch) <= self._fault_at):
                return False
            self.faulted = False
            self.fault_reason = ""
            self.last_error = ""
            self._epoch += 1
            self._pending = None
            return True

    def close(self):
        """Invalidate motion and request best-effort neutral; never wait on IO."""
        with self._condition:
            if not self._closing:
                self._closing = True
                self._pending = None
                if not self.release_pending:
                    self._epoch += 1
                if not self.wire_neutral and not self.release_pending:
                    self._request_release()
                self._condition.notify()
            if not self._worker.starting and not self.closed and not self.worker_alive:
                # A rejected writer can still own a native handle; only a
                # background worker may drain its barrier and release it.
                never_admitted = self._worker.never_admitted
                # A rejected close-retry thread does not erase an earlier
                # native-close failure. Retry that finalizer, not the drain.
                needs_drain = never_admitted and not self._close_failed
                target = self._run if needs_drain else self._finalize_close
                self._worker.start(target, "cc-close-retry")

    def wait_closed(self, timeout=2.5):
        """Background/test cleanup only; None explicitly waits without a deadline."""
        self._worker.join(timeout)
        return self.closed

    def _valid_active(self):
        epoch, _, _, created, neutral = self._active
        with self._condition:
            return epoch == self._epoch and (neutral or (
                not self._closing and not self.release_pending
                and self.time_fn() - created <= self.max_intent_age))

    def _write_frame(self, frame):
        if not self._valid_active():
            raise _StaleCCIntent("CC stale intent dropped")
        if self._write_bytes is not None:
            return self._write_bytes(frame)
        if self._serial is None:
            import serial
            from .. import serial_ports as ports
            target = ports.resolve_cc_port(self.port)
            if target == ports.STUB_PORT:
                raise RuntimeError("没找到自定义控制器串口，请插好线或显式选口")
            with self._condition:
                self.resolved_port = target
            self._serial = serial.Serial(target, self.baudrate,
                                          timeout=0.2, write_timeout=0.2)
        # Opening/enumeration can take arbitrarily long on a broken driver.
        # Recheck AFTER that operation, before letting its old intent escape.
        if not self._valid_active():
            raise _StaleCCIntent("CC stale intent dropped")
        return self._serial.write(frame)

    def _close_serial(self):
        handle = self._serial
        if handle is not None:
            close_resource(handle)
            self._serial = None

    def _record_close_failure(self, exc):
        with self._condition:
            self._close_failed = True
            self._closing = True
            self._pending = None
            self.faulted = True
            self.last_error = f"CC close unconfirmed; explicit close retry required: {exc}"
            self.fault_reason = self.last_error
            self._condition.notify_all()

    def _finalize_close(self):
        """Runs only on the sole writer or an explicit background close retry."""
        with self._condition:
            self._closing = True
            self._pending = None
        try:
            self._close_serial()
        except Exception as exc:
            self._record_close_failure(exc)
        else:
            with self._condition:
                self._close_failed = False
                self._stopped = True
                self._condition.notify_all()

    def _run(self):
        try:
            while True:
                with self._condition:
                    while True:
                        now = self.time_fn()
                        if self.release_pending and self._release_left:
                            delay = self._release_due - now
                            if delay <= 0:
                                payload = encode_keymouse_0306(x=self.cursor.x,
                                                              y=self.cursor.y)
                                intent = (self._epoch, CC_CMD_KEYMOUSE, payload, now, True)
                                self._release_left -= 1
                                break
                            self._condition.wait(delay)
                        elif self._closing:
                            return
                        elif self._pending is not None:
                            # Wait without removing the mailbox so newer intent
                            # can replace it while the wire-rate gate is closed.
                            delay = self._link._next_due - now
                            if delay > 0:
                                self._condition.wait(delay)
                                continue
                            intent = (*self._pending, False)
                            self._pending = None
                            break
                        else:
                            if self._motion_deadline and not self.wire_neutral:
                                remaining = self._motion_deadline - now
                                if remaining <= 0:
                                    self.faulted = True
                                    self._fault_at = now
                                    self.last_error = "CC input watchdog expired; explicit re-arm required"
                                    self.fault_reason = self.last_error
                                    self._motion_deadline = 0.0
                                    self._request_release()
                                    continue
                                self._condition.wait(remaining)
                            else:
                                self._condition.wait()
                    self._active = intent
                if not self._valid_active():
                    with self._condition:
                        if not self.release_pending and not self.wire_neutral:
                            self._request_release()
                    continue
                epoch, cmd_id, payload, _, neutral = intent
                ok, why = self._link._send(cmd_id, payload, self.time_fn(),
                                          neutral_release=neutral)
                with self._condition:
                    self.seq, self.sent = self._link.seq, self._link.sent
                    self.last_frame = self._link.last_frame
                    self.dropped_rate = self._link.dropped_rate
                    if ok and epoch == self._epoch:
                        self.last_error = self.fault_reason if self.faulted else ""
                        self._motion_deadline = (0.0 if neutral else
                            intent[3] + self.max_intent_age)
                        if neutral:
                            self.wire_neutral = True
                            self.release_pending = False
                            self._release_left = 0
                            self.last_keys = (0, 0)
                            self.dropped_keys = []
                    elif not ok:
                        self.failed += 1
                        self.faulted = True
                        self._fault_at = self.time_fn()
                        self.last_error = why
                        self.fault_reason = self.fault_reason or f"CC output failed; explicit re-arm required: {why}"
                        if neutral and epoch == self._epoch:
                            self._release_due = self.time_fn() + self.retry_interval
                        elif not self.release_pending:
                            self._request_release()
                        # No pre-failure motion survives, even if submitted
                        # while the failing write was in flight.
                        self._pending = None
                if not ok:
                    try:
                        self._close_serial()
                    except Exception as exc:
                        self._record_close_failure(exc)
                        return
        finally:
            if not self._close_failed:
                self._finalize_close()
