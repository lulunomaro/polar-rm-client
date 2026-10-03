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


# 第 54 轮新增：回退到默认时的原因与次数。
# 它们**不参与判定**（仍然返回内置表），只是让现场知道用的是哪一张表。
_KEY_USAGE_NOTE = None
_KEY_USAGE_ERRORS = 0


def load_key_usage(path: str | None = None) -> dict[str, int]:
    """读键值表；文件不存在或坏值就退回内置 HID 默认，**绝不因为配置坏而发不出键**。"""
    # 第 54 轮：两个 `global` 必须在**函数开头**声明——
    # Python 要求 `global` 在任何使用之**前**，否则报
    # `name '...' is assigned to before global declaration` —— 第一版把它写在 except 里，直接拉埋整个包。
    global _KEY_USAGE_NOTE, _KEY_USAGE_ERRORS
    table = dict(HID_USAGE)
    fp = Path(path) if path else KEY_USAGE_FILE
    try:
        raw = json.loads(fp.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        # 第 54 轮：原来这里还有一行 `raw = {}    # unreachable, kept for clarity`
        # —— 它在 `return` **之后**，是**真的死代码**，而不是"保留清晰"。
        # 一并删掉：留着一行永不执行的代码，会让人以为它在兜什么。
        _KEY_USAGE_NOTE = "键值表文件不存在，用内置 HID 默认"
        return table
    except Exception as exc:  # noqa: BLE001
        # 第 54 轮：原来这里也是光秃秃的 `return table`。
        #
        # 退回默认**本身是对的**（配置坏了也要能发键），但原来是**完全静默**的：
        # 这张表直接走 **0x0306 控制出口**（`control.py` 的 `_send_custom_controller`），
        # 错的键码会**当作权威值发出去**，而现场看到的是"正常发送" ——
        # 没有日志、没有计数，连"用了默认"这个信息都没有。
        #
        # 宽泛的 `except Exception` 还会把真的 `PermissionError`（文件被锁）
        # 一起藏起来：那和"文件不存在"是**两件不同的事**。
        #
        # 控制流**不变**（仍然退回内置表），只是失败**看得见**了。
        _KEY_USAGE_ERRORS += 1
        _KEY_USAGE_NOTE = f"键值表读失败（{type(exc).__name__}），已用内置 HID 默认"
        return table
    for k, v in (raw or {}).items():
        key = str(k).upper()
        if key.startswith("_") or not isinstance(v, int) or not 0 <= v <= 0xFF:
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
    if not 0 <= cmd_id <= 0xFFFF:
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
      * 只有 2 键无冲，按下第 3 个键必须丢掉一个（这里按"后到的赢"处理，
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
        if code not in codes:
            codes.append(code)
    if len(codes) > 2:
        # 报**键名**：现场看日志的人要想知道"我按的哪个键没生效"，
        # 给一个 HID 编码数字没用。
        dropped.extend(str(n) for n in names[2:])
        codes = codes[:2]
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
    cursor: VirtualCursor = field(default_factory=VirtualCursor)

    # ---- 频率控制 -------------------------------------------------
    def _rate_gate(self, now: float) -> bool:
        interval = 1.0 / max(1.0, float(self.max_hz))
        if now + 1e-9 < self._next_due:
            self.dropped_rate += 1
            return False
        self._next_due = now + interval
        return True

    def _send(self, cmd_id: int, payload: bytes, now: float) -> tuple[bool, str]:
        if not self._rate_gate(now):
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
                    self.write_bytes(frame)
                else:
                    import serial
                    from .. import serial_ports as ports
                    target = ports.resolve_cc_port(self.port)
                    if target == ports.STUB_PORT:
                        raise RuntimeError(
                            "没找到自定义控制器该用的 FTDI 口（选手端只认它）："
                            "插好线或在 CC Port 里显式选口")
                    self.resolved_port = target
                    with serial.Serial(target, self.baudrate, timeout=0.2) as sp:
                        sp.write(frame)
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
        ok, why = self._send(CC_CMD_KEYMOUSE, payload, now)
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
        self.cursor.reset()
        return self._send(CC_CMD_KEYMOUSE,
                          encode_keymouse_0306(key1=0, key2=0,
                                               x=self.cursor.x, y=self.cursor.y),
                          now)


def decode_cc_frame(frame: bytes) -> dict | None:
    """解一帧 0xA5 包（自检/回放抓包用；与 encode_cc_frame 严格互逆）。"""
    if len(frame) < HEADER_SIZE + CMD_ID_SIZE + CRC16_SIZE:
        return None
    if frame[0] != SOF:
        return None
    (data_len,) = struct.unpack_from("<H", frame, 1)
    if crc8(frame[:4]) != frame[4]:
        return None
    total = HEADER_SIZE + CMD_ID_SIZE + data_len + CRC16_SIZE
    if len(frame) != total:
        return None
    if crc16(frame[:total - CRC16_SIZE]) != struct.unpack_from("<H", frame, total - 2)[0]:
        return None
    (cmd_id,) = struct.unpack_from("<H", frame, HEADER_SIZE)
    return {"seq": frame[3], "cmd_id": cmd_id,
            "data": frame[HEADER_SIZE + CMD_ID_SIZE: HEADER_SIZE + CMD_ID_SIZE + data_len]}
