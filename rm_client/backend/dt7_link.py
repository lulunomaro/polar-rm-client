#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""DT7 直连出口（C 路）：我们自己把键鼠写进遥控器那条 DJI USB Virtual COM。

链路（官方明文，不是推测）
--------------------------
《2026 竞赛规程》表 2-2「DT7遥控器 —— **键鼠指令**、遥控器杆量信息」（rm2026_spec.txt:380），
《遥控器手册》第 6 页链路图：键盘鼠标 → PC主机 —USB→ DT7发射机 —(虚拟控制链路)→ DR16接收机
—DBUS→ 战车。DT7 的 USB 口在 Windows 上就是一个普通虚拟串口
（官方 INF：``USB\VID_FFF0&PID_0008`` → 微软 ``usbser.sys``，见 tools/dt7_link/dt7_vcp_probe.py 顶部），
所以任何程序都能打开它写字节。
车端 **零改动**：接收机那 18 字节 DBUS 帧里的 mouse/key 字段，我们固件一直在解
（``application/other/remote_control.c:611-634``，``__RC_TYPE == RC_DT7`` 分支）。

PC→DT7 那一帧长什么样（从大疆 12.x 选手端二进制里逐条反汇编复原）
------------------------------------------------------------------
唯一的 0x5A 头构造点在 ``SerialPortThread.OnSendInputStatus``
（GameAssembly.dll ``0x18027B2A0``~``0x18027BAE0``，全库扫描只有这一处 ``mov byte [mem],0x5a``）：

    偏移  字节  含义
    0     0x5A  hdr_flag
    1     0x10  length = 之后"前缀+载荷"的字节数（= 4 + 12；**含** cmd 那 2 字节，不含尾 CRC16）
    2     0x01  protocol_version
    3     xx    crc8，覆盖 out[0..2]
    4-5   0000  sequence_number（厂商这条**不递增**，恒 0）
    6-7   0020  cmd_id（小端 u16 线值 = 0x2000）
    8-19  ...   载荷 12 字节 = ``Input_Device_Status_RC``
    20-21 xx    crc16 覆盖 out[0..19]，小端

    ⇒ 期望帧：``5A 10 01 <crc8> 00 00 00 20 <12B 载荷> <crc16 LE>``，总长 22。
  CRC 用的是 ``CRCCheck`` 那族（``CRC8_INIT=0x77 / CRC16_INIT=0x1862``，
  ``..cctor`` 里 ``0x1802FAB75`` / ``0x1802FAB86``），**不是**裁判系统那族 0xFF/0xFFFF。
  载荷 12 字节 ``<hhhBBHH``：mouse_x/y/z int16、左键、右键、keyboard_value u16、reserved u16
  —— **没有中键**（厂商日志字面量 ``Input_Device_RC Sent x,y,z,LB,RB,KeyValue`` 也只有这些）。

  ``0x2000`` 的语义也结案了：厂商自己给它起了名字 ——
  ``public class Proto_Prefix { public const int CONTROL_PC_ROBOT = 8192; }``
  （``dump.cs:136032-136035``；8192 = 0x2000，是这条前缀里**唯一**的常量）
  ⇒ 这个 cmd_id 的含义就是「PC 控机器人」，正是我们在做的事。
  帧头/前缀/载荷三个厂商类型（``CommonHeader_V1`` / ``Data_Body_Prefix`` /
  ``Input_Device_Status_RC``）的字段顺序与偏移也和我们的字节一一对上
  （``tools/dt7_link/test_dt7_link.py`` 第 11 节拿 dump.cs 当外部锚点逐字段断言）。
  ⇒ 剩下**唯一**没证明的只有一件事：这台 DT7 的现行固件认不认我们写的这 22 字节 —— 只能上机。

与另两条链路的区别（别混）
--------------------------
- 图传/裁判那族是 ``0xA5`` 头 + ``datalen 不含 cmd`` + ``CRCCheck_StudentSerialPort``(0xFF/0xFFFF)，
  键鼠在 2025 图传上的命令号是 ``CMD_ID_Keyboard=17``、在 ``VideoSerialPortThread`` 上又是 ``0x0304``
  —— 那些**不是** DT7 的号，早期把这 17 借过来当默认值是错的。
- ``cc_link.py``（0x0306）是"借大疆选手端的手"，必须有官方选手端在跑；本模块不依赖它。

安全约定
--------
- 默认 ``mock_output=True``（只产帧不碰硬件）；真发要 manager 显式传 ``mock_output=False``
  且界面勾 "DT7 Real TX"。正常真发还必须 MATCH_ARMED + 独立 DT7 能力；
  自检使用短期资格令牌，仅允许全零/X。探针 PASS 不能代替车辆安全证据。
- 串口句柄长持：每帧 open/close 会被 DTR/RTS 抖动与端口独占拖死。
- DT7 那侧没有"过期自动清零"的兜底 ⇒ 任何降级路径都必须补一帧全零（``release``）。
- profile 自相矛盾（布局/字段/CRC 族对不上）一律**拒发并写明原因**，不静默改语义。
"""
from __future__ import annotations

import json
import hashlib
import math
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import serial_ports as ports
from ..runtime_paths import RuntimePaths
from .resource_close import close_resource

ROOT = Path(__file__).resolve().parents[2]
PROFILE_FILE = RuntimePaths.discover().defaults_root / "dt7_profile.json"

SOF_RC = 0x5A             # DT7 那条 VCOM 上的帧头（厂商 OnSendInputStatus 现场）
SOF_A5 = 0xA5             # 图传/裁判那族（本模块只作对照用）
RC_HEADER_SIZE = 4        # flag + length + version + crc8
A5_HEADER_SIZE = 5        # sof + datalen(2) + seq + crc8
CRC16_SIZE = 2
CMD_SIZE = 2

# 载荷字段名 -> 允许的 struct 字符（宽度必须一致，否则配置就是写错了）
FIELD_FMTS: dict[str, tuple[str, ...]] = {
    "mouse_x": ("h",), "mouse_y": ("h",), "mouse_z": ("h",),
    "left": ("B",), "right": ("B",), "mid": ("B",),
    "keyboard_value": ("H", "h"), "reserved": ("B", "H"),
    "version": ("B",), "sdr": ("B",), "seq": ("B",),
}

CRC_VARIANTS = {                          # 名字 -> (crc8 初值, crc16 初值)
    "rc_check": (0x77, 0x1862),           # CRCCheck（DT7/RC 口）——本路的默认
    "student": (0xFF, 0xFFFF),            # CRCCheck_StudentSerialPort（图传/裁判那族）
    "video": (0x77, 0x3692),              # CRCCheck_VideoSerialPort（2025 图传）
}
FRAME_VARIANTS = ("dji_rc_v1", "a5_tail", "a5_no_tail")

DEFAULT_PROFILE = {
    "cmd_wire": 0x2000,               # 厂商现场读出的 16 位线值（out[6..7] 小端 = 00 20）
    "cmd_set": 0,
    "cmd_id_keyboard": None,
    "cmd_confirmed": True,            # 字节层面确凿；归属仍是候选（见模块文档）
    "allow_experimental_cmd": False,
    # 默认拒绝把 DT7 帧写进"机器人本体的 CDC 口"（认错口会产出假的 NO-CHANGE）。
    # 真有非标准 VID/PID 的遥控器时，把它设 true 并自己在 DT7 Port 里指定。
    "allow_robot_port": False,
    "payload_layout": "<hhhBBHH",
    "payload_fields": ["mouse_x", "mouse_y", "mouse_z", "left", "right",
                       "keyboard_value", "reserved"],
    "frame_variant": "dji_rc_v1",
    "datalen_includes_cmd": True,
    "crc_variant": "rc_check",
    "protocol_version": 1,
    "seq_increments": False,          # 厂商这条恒 0；改 True 需要现场证据
    "baudrate": 961200,               # 厂商对 RC 口的设置（SerialPortThread.cs:92-95）
    "max_hz": 30.0,
    # 每帧鼠标增量的上限（像素）。客户端按 tick 累加位移：一旦界面卡到 5Hz 或桌面被拖窗，
    # 一帧攒出几千像素是完全可能的，而云台增量 = mouse / SENSITIVITY，会一帧甩到限位。
    # 截断是安全侧，越界值同时记在 clipped_fields 里，不静默。
    "mouse_limit": 200,
    "reserved_value": 1024,          # DBUS channel 5 neutral; part of qualified wire identity
    "open_retry_s": 0.5,
}


def profile_digest(profile: dict) -> str:
    semantic = {key: profile.get(key, value) for key, value in DEFAULT_PROFILE.items()}
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest().upper()


def status_token(*, mock_output: bool, resolved_port: str = "", last_error: str = "",
                 wire_neutral: bool = True, release_pending: bool = False,
                 enabled: bool = True) -> str:
    """C 路状态的**唯一**成文处：开着但没发过 = 必须看得出来。

    单独成函数（而不是留在 paint 里）只有一个理由：这里最容易犯的错是
    "开关都勾了 => 显示 DT7:real"，而真发闸开了、口却从没成功打开过，是完全
    不同的两件事。实测过这个假象（mock_output=False、resolved_port=''、
    last_error=''），所以它现在是一条断言而不是一句口头保证。
    """
    if not enabled:
        return ""
    real = not bool(mock_output)
    port = str(resolved_port or "")
    if real and not port:
        text = "DT7:real(口未开)"
    else:
        text = "DT7:" + ("real" if real else "mock") + (("@" + port) if (real and port) else "")
    if not wire_neutral:
        # 线上还挂着我们上一帧的按键：这比任何计数器都重要，必须上脸
        text += "(线上仍带按键)"
    if release_pending:
        text += "(松手帧未送达!)"
    if last_error:
        text += "(!" + str(last_error)[:40] + ")"
    return text


class Dt7ConfigError(RuntimeError):
    """profile 自相矛盾 —— 宁可拒发也不静默改语义。"""


def _crc8_init(data: bytes, init: int) -> int:
    crc = init & 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc & 0xFF


def _crc16_init(data: bytes, init: int) -> int:
    crc = init & 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def clamp_field(name: str, fmt: str, value: int) -> tuple[int, bool]:
    """把值截进该字段真正的位宽，返回 (值, 是否被截)。

    不截的后果很难看：``struct.pack("<h", 40000)`` 直接抛错，于是**整帧**（包括 W/S/A/D）
    一个都发不出去，现场表现为"动一下鼠标车就不认键子了"。越界截断 + 记一笔，
    比整帧作废安全得多。
    """
    lo, hi = {"h": (-32768, 32767), "H": (0, 65535), "B": (0, 255)}.get(fmt, (-32768, 32767))
    v = int(value)
    if v < lo or v > hi:
        return (max(lo, min(hi, v)) if fmt == "h" else v & (0xFFFF if fmt == "H" else 0xFF)), True
    return v, False


def layout_letters(layout: str) -> list:
    return [c for c in str(layout)[1:] if not c.isdigit()]


def validate_layout(layout: str, fields: list) -> int:
    """校验 payload_layout 与 payload_fields 一一对应；返回字节数。不一致就抛。"""
    if not isinstance(layout, str) or not layout.startswith("<"):
        raise Dt7ConfigError(f"payload_layout 必须是小端 '<...'：{layout!r}")
    if not isinstance(fields, (list, tuple)) or not fields:
        raise Dt7ConfigError("payload_fields 必须是非空列表")
    # Profiles map one explicitly named field to one scalar format character.
    # Repeat counts (including zero counts) otherwise pass this check but change
    # the number of values required by struct.pack.
    if any(c not in "hHB" for c in layout[1:]):
        raise Dt7ConfigError("payload_layout 只支持逐字段 h/H/B，不支持重复计数或填充")
    letters = list(layout[1:])
    if len(letters) != len(fields):
        raise Dt7ConfigError(f"字段数不匹配：layout {len(letters)} 个 vs fields {len(fields)} 个")
    for name, ch in zip(fields, letters):
        if not isinstance(name, str):
            raise Dt7ConfigError(f"载荷字段名必须是字符串：{name!r}")
        allowed = FIELD_FMTS.get(name)
        if allowed is None:
            raise Dt7ConfigError(f"未知载荷字段 {name!r}（可用：{sorted(FIELD_FMTS)}）")
        if ch not in allowed:
            raise Dt7ConfigError(f"字段 {name!r} 要 {allowed}，layout 给的是 {ch!r}")
    try:
        size = struct.calcsize(layout)
    except Exception as exc:                       # noqa: BLE001
        raise Dt7ConfigError(f"payload_layout 无法 calcsize：{exc}") from exc
    if not 0 < size <= 24:
        raise Dt7ConfigError(f"载荷尺寸离谱：{size}")
    return size


def _clamp_mouse(values: dict, prof: dict) -> tuple[dict, list]:
    """把三轴鼠标增量截进 ±mouse_limit（0 = 不限制），返回 (新值, 被截字段)。"""
    try:
        limit = abs(int(prof.get("mouse_limit", 200)))
    except (TypeError, ValueError):
        return values, []
    if limit <= 0:
        return values, []
    out = dict(values)
    clipped = []
    for key in ("mouse_x", "mouse_y", "mouse_z"):
        if key not in out:
            continue
        v = int(out[key])
        if v > limit or v < -limit:
            out[key] = max(-limit, min(limit, v))
            clipped.append(key)
    return out, clipped


def load_profile(path: str | None = None) -> dict:
    """读 profile。**配置有问题不再静默退回默认**：错误带在 ``_profile_error`` 里，
    让上层能显示到人眼看得见的地方（现场最怕"开着、没反应、没原因"）。"""
    prof = dict(DEFAULT_PROFILE)
    prof["payload_fields"] = list(DEFAULT_PROFILE["payload_fields"])
    prof["_profile_error"] = ""
    prof["_profile_path"] = str(path or PROFILE_FILE)
    fp = Path(path) if path else PROFILE_FILE
    try:
        raw = json.loads(fp.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        prof["_profile_error"] = f"DT7 profile missing: {fp}; real TX is locked"
        prof["cmd_confirmed"] = False
        return prof
    except Exception as exc:                       # noqa: BLE001
        prof["_profile_error"] = f"profile 读失败，按默认（不发）：{exc}"
        prof["cmd_wire"] = None
        prof["cmd_confirmed"] = False
        return prof
    if not isinstance(raw, dict):
        prof["_profile_error"] = "profile 不是对象，按默认（不发）"
        return prof
    for key in ("cmd_wire", "cmd_set", "cmd_id_keyboard"):
        v = raw.get(key)
        if v is None:
            if key in raw:
                prof[key] = None
            continue
        if isinstance(v, bool) or not isinstance(v, int):
            prof["_profile_error"] = f"{key} 不是整数：{v!r}"
            continue
        hi = 0xFFFF if key == "cmd_wire" else 0xFF
        if not 0 <= v <= hi:
            prof["_profile_error"] = f"{key} 超 {'16' if hi == 0xFFFF else '8'} 位：{v!r}"
            continue
        prof[key] = v
    for key in ("cmd_confirmed", "allow_experimental_cmd", "datalen_includes_cmd",
                "seq_increments", "allow_robot_port"):
        if isinstance(raw.get(key), bool):
            prof[key] = raw[key]
    for key in ("frame_variant", "crc_variant"):
        v = raw.get(key)
        if isinstance(v, str) and v:
            prof[key] = v
    if str(prof["crc_variant"]) not in CRC_VARIANTS:
        prof["_profile_error"] = f"crc_variant 未知：{prof['crc_variant']!r}"
    if str(prof["frame_variant"]) not in FRAME_VARIANTS:
        prof["_profile_error"] = f"frame_variant 未知：{prof['frame_variant']!r}"
    ver = raw.get("protocol_version")
    if "protocol_version" in raw:
        if isinstance(ver, int) and not isinstance(ver, bool) and 0 <= ver <= 255:
            prof["protocol_version"] = ver
        else:
            prof["_profile_error"] = prof["_profile_error"] or f"protocol_version 要取 0..255：{ver!r}"
    layout, fields = raw.get("payload_layout"), raw.get("payload_fields")
    if isinstance(layout, str):
        prof["payload_layout"] = layout
    if isinstance(fields, list) and all(isinstance(x, str) for x in fields):
        prof["payload_fields"] = fields
    try:
        prof["_payload_size"] = validate_layout(prof["payload_layout"], prof["payload_fields"])
    except Dt7ConfigError as exc:
        prof["_profile_error"] = str(exc)
        prof["_payload_size"] = -1
    baud = raw.get("baudrate")
    if isinstance(baud, int) and 9600 <= baud <= 2_000_000:
        prof["baudrate"] = baud
    hz = raw.get("max_hz")
    if isinstance(hz, (int, float)) and not isinstance(hz, bool) and 1 <= float(hz) <= 200:
        prof["max_hz"] = float(hz)
    limit = raw.get("mouse_limit")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 32767:
            prof["_profile_error"] = f"mouse_limit 要取 0..32767：{limit!r}"
        else:
            prof["mouse_limit"] = limit
    for key, low, high in (("reserved_value", 0, 65535), ("open_retry_s", 0.1, 5.0)):
        if key in raw:
            value = raw[key]
            valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                     and math.isfinite(value) and low <= value <= high)
            if key == "reserved_value":
                valid = valid and isinstance(value, int)
            if valid:
                prof[key] = value
            else:
                prof["_profile_error"] = f"invalid {key}: {value!r}"
    return prof


def cmd_wire_of(prof: dict) -> int | None:
    """命令号折成 16 位线值。``cmd_wire`` 优先（直接给线值，绕开位域方向争议）。"""
    wire = prof.get("cmd_wire")
    if wire is not None:
        return wire if (isinstance(wire, int) and not isinstance(wire, bool)
                        and 0 <= wire <= 0xFFFF) else None
    cid, cset = prof.get("cmd_id_keyboard"), prof.get("cmd_set", 0)
    if (isinstance(cid, int) and not isinstance(cid, bool)
            and isinstance(cset, int) and not isinstance(cset, bool) and 0 <= cid <= 0xFF and 0 <= cset <= 0xFF):
        return (cset | (cid << 8)) & 0xFFFF
    return None


def pack_cmd(cmd_set: int, cmd_id: int) -> bytes:
    """A5 族的 DJI_PRO_CMD_T：Cmd_Set 占低字节、Cmd_Id 占高字节。"""
    if not (0 <= cmd_set <= 0xFF and 0 <= cmd_id <= 0xFF):
        raise ValueError(f"cmd_set/cmd_id 都要是 1 字节：{cmd_set!r} {cmd_id!r}")
    return struct.pack("<H", cmd_set | (cmd_id << 8))


def build_frame(cmd_wire: int, payload: bytes, seq: int, *,
                frame_variant: str = "dji_rc_v1",
                datalen_includes_cmd: bool = True,
                crc_variant: str = "rc_check",
                protocol_version: int = 1) -> bytes:
    """按厂商那一族的现场字节编一帧。三种帧型都能出，现场定案不用改代码。"""
    payload = bytes(payload)
    if crc_variant not in CRC_VARIANTS:
        raise Dt7ConfigError(f"crc_variant 未知：{crc_variant!r}")
    if frame_variant not in FRAME_VARIANTS:
        raise Dt7ConfigError(f"frame_variant 未知：{frame_variant!r}")
    if isinstance(cmd_wire, bool) or not isinstance(cmd_wire, int) or not 0 <= cmd_wire <= 0xFFFF:
        raise Dt7ConfigError(f"cmd_wire 超出 16 位：{cmd_wire!r}")
    if (isinstance(protocol_version, bool) or not isinstance(protocol_version, int)
            or not 0 <= protocol_version <= 0xFF):
        raise Dt7ConfigError(f"protocol_version 超出 8 位：{protocol_version!r}")
    crc8_init, crc16_init = CRC_VARIANTS[crc_variant]
    if frame_variant == "dji_rc_v1":
        # 厂商现场：hdr = 5A | length | version | crc8(hdr[0..2])
        #             body = seq(u16) | cmd(u16) | payload
        if len(payload) > 0xFF - 2 * CMD_SIZE:
            raise Dt7ConfigError("dji_rc_v1 载荷超出 1 字节长度字段")
        body = struct.pack("<HH", seq & 0xFFFF, cmd_wire) + payload
        header = bytearray([SOF_RC, len(body), protocol_version])
        header.append(_crc8_init(bytes(header), crc8_init))
        frame = bytes(header) + body
        return frame + struct.pack("<H", _crc16_init(frame, crc16_init))
    datalen = len(payload) + (CMD_SIZE if datalen_includes_cmd else 0)
    if datalen > 0xFFFF:
        raise Dt7ConfigError("A5 载荷超出 2 字节长度字段")
    header = bytearray([SOF_A5])
    header += struct.pack("<H", datalen)
    header.append(seq & 0xFF)
    header.append(_crc8_init(bytes(header), crc8_init))
    frame = bytes(header) + struct.pack("<H", cmd_wire & 0xFFFF) + payload
    if frame_variant == "a5_tail":
        frame += struct.pack("<H", _crc16_init(frame, crc16_init))
    return frame


def decode_dt7_frame(frame: bytes, *, crc_variant: str = "rc_check",
                     datalen_includes_cmd: bool = True) -> dict | None:
    """解一帧（抓包核对用）：按帧头自动分族；任何校验不过都返回 None，不猜。"""
    if crc_variant not in CRC_VARIANTS:
        return None
    crc8_init, crc16_init = CRC_VARIANTS[crc_variant]
    if len(frame) < 4:
        return None
    if frame[0] == SOF_RC:
        length = frame[1]
        total = RC_HEADER_SIZE + length + CRC16_SIZE
        if length < 2 * CMD_SIZE or len(frame) != total:
            return None
        if _crc8_init(frame[:3], crc8_init) != frame[3]:
            return None
        if _crc16_init(frame[:total - CRC16_SIZE], crc16_init) != struct.unpack_from(
                "<H", frame, total - 2)[0]:
            return None
        (seq, cmd) = struct.unpack_from("<HH", frame, RC_HEADER_SIZE)
        return {"variant": "dji_rc_v1", "seq": seq, "cmd_wire": cmd,
                "cmd_set": cmd & 0xFF, "cmd_id": cmd >> 8,
                "protocol_version": frame[2],
                "payload": frame[RC_HEADER_SIZE + 2 * CMD_SIZE: total - CRC16_SIZE]}
    if frame[0] != SOF_A5 or len(frame) < A5_HEADER_SIZE:
        return None
    (datalen,) = struct.unpack_from("<H", frame, 1)
    if _crc8_init(frame[:4], crc8_init) != frame[4]:
        return None
    body = datalen - (CMD_SIZE if datalen_includes_cmd else 0)
    if body < 0:
        return None
    total = A5_HEADER_SIZE + CMD_SIZE + body + CRC16_SIZE
    if len(frame) != total:
        return None
    (cmd,) = struct.unpack_from("<H", frame, A5_HEADER_SIZE)
    if _crc16_init(frame[:total - CRC16_SIZE], crc16_init) != struct.unpack_from(
            "<H", frame, total - 2)[0]:
        return None
    return {"variant": "a5_tail", "seq": frame[3], "cmd_wire": cmd,
            "cmd_set": cmd & 0xFF, "cmd_id": cmd >> 8,
            "payload": frame[A5_HEADER_SIZE + CMD_SIZE: A5_HEADER_SIZE + CMD_SIZE + body]}


@dataclass
class Dt7KeyMouseLink:
    """把键鼠按 profile 编成帧、写进 DT7 那个 DJI VCOM（长持句柄）。"""

    port: str = "COM3"
    profile: dict = field(default_factory=load_profile)
    mock_output: bool = True
    write_bytes: object | None = None
    seq: int = 0
    sent: int = 0
    dropped_rate: int = 0
    refused: int = 0
    last_error: str = ""
    last_note: str = ""
    last_frame: bytes = b""
    last_payload: bytes = b""
    resolved_port: str = ""      # 真在写的设备名（Auto 解析后的结果）
    _open_backoff_until: float = 0.0
    _open_last_error: str = ""
    mid_dropped: bool = False
    clipped_fields: list = field(default_factory=list)
    _next_due: float = 0.0
    #: 我们最后**真正写上线**的那一帧是不是全中立。DT7/DR16 那侧没有过期兜底，
    #: 只要这里还是 False，线上就还挂着我们按下的键。
    wire_neutral: bool = True
    #: 为了松手而破例提前发的帧数（限流统计之外，自检/现场要看这个）
    forced_releases: int = 0
    #: 上一帧"内容合法但没落地"（被限流挡下 / 写口失败）——调用方要补发。
    #: 与 last_send_rate_limited 的区别：写口失败也置位，而闸门拒绝**不**置位。
    last_send_dropped: bool = False
    _handle: object | None = None
    time_fn: object = time.perf_counter
    safety: object | None = None
    authorization_guard: object | None = None
    pre_write_guard: object | None = None
    _handle_requested_port: str = ""
    _handle_close_failed: bool = False

    def _gate(self) -> str:
        """能不能发。任何一条不满足就返回原因，调用方据此拒发。"""
        prof = self.profile
        err = str(prof.get("_profile_error") or "")
        if err:
            return f"profile 有问题：{err}"
        try:
            validate_layout(prof.get("payload_layout"), prof.get("payload_fields"))
            reserved = prof.get("reserved_value", 1024)
            if isinstance(reserved, bool) or not isinstance(reserved, int):
                raise Dt7ConfigError("reserved_value 必须是整数")
            for name, fmt in zip(prof["payload_fields"], layout_letters(prof["payload_layout"])):
                if name == "reserved" and not 0 <= reserved <= (255 if fmt == "B" else 65535):
                    raise Dt7ConfigError("reserved_value 超出载荷字段位宽")
        except Dt7ConfigError as exc:
            return str(exc)
        if cmd_wire_of(prof) is None:
            return "DT7 命令号未定案（profile 里 cmd_wire / cmd_id_keyboard 都是空），拒绝发包"
        if not bool(prof.get("cmd_confirmed")):
            if not bool(prof.get("allow_experimental_cmd")):
                return ("命令号未确认（cmd_confirmed=false）：先定案再真发；"
                        "工具试号请显式置 allow_experimental_cmd")
        if str(prof.get("frame_variant")) not in FRAME_VARIANTS:
            return f"frame_variant 未知：{prof.get('frame_variant')!r}"
        if str(prof.get("crc_variant")) not in CRC_VARIANTS:
            # 未知族若静默退回默认初值，CRC 就会一路算错而"看起来在发"
            return f"crc_variant 未知：{prof.get('crc_variant')!r}"
        return ""

    def encode(self, values: dict) -> bytes:
        """按 payload_fields 取值打包（字段名驱动，profile 换布局不用改代码）。"""
        prof = self.profile
        validate_layout(prof.get("payload_layout"), prof.get("payload_fields"))
        layout = prof["payload_layout"]
        fields = list(prof["payload_fields"])
        args = []
        clipped: list[str] = []
        letters = layout_letters(layout)
        for idx, name in enumerate(fields):
            if name == "reserved":
                # 这个 2 字节不是"惰性"的：DBUS 里它就落在 bytes 16-17，而固件把
                # 16-17 当第 5 通道解（remote_control.c:711 ch[4] = buf[16]|buf[17]<<8，
                # :717 再减中值 1024）。写 0 ⇒ ch[4] = -1024 = 左上角滚轮满偏。
                # 英雄机没有机械臂消费者，但它还会经 CAN 转发给另一块板
                # （CAN_communication.c:81/87），工程/企鹅一开就翻车。
                # 所以默认填**通道中值**：DT7 若原样搬走 ⇒ 读到 0（中立）；
                # 若不搬 ⇒ 我们这一位本来就没意义。两种情况都不比 0 坏。
                args.append(int(prof.get("reserved_value", 1024)))
                continue
            if name not in values:
                raise Dt7ConfigError(f"载荷缺字段值：{name}")
            v = values[name]
            v = int(bool(v)) if isinstance(v, bool) else int(v)
            v, was_clipped = clamp_field(str(name), letters[idx] if idx < len(letters) else "h", v)
            if was_clipped:
                clipped.append(str(name))
            args.append(v)
        self.clipped_fields = clipped
        payload = struct.pack(layout, *args)
        frame = build_frame(int(cmd_wire_of(prof) or 0), payload, self.seq,
                            frame_variant=str(prof.get("frame_variant", "dji_rc_v1")),
                            datalen_includes_cmd=bool(prof.get("datalen_includes_cmd", True)),
                            crc_variant=str(prof.get("crc_variant", "rc_check")),
                            protocol_version=prof.get("protocol_version", 1))
        self.last_payload = payload
        if bool(prof.get("seq_increments")):
            self.seq = (self.seq + 1) & 0xFFFF
        return frame

    def send(self, *, pressed_bits: int = 0, mouse_dx: int = 0, mouse_y: int = 0,
             mouse_z: int = 0, left: bool = False, right: bool = False,
             mid: bool | None = None, now: float | None = None,
             qualification_token: str | None = None,
             _neutral_release: bool = False) -> tuple[bool, str]:
        now = float(self.time_fn()) if now is None else float(now)
        self.last_send_rate_limited = False
        self.last_send_dropped = False
        gate = self._gate()
        if gate:
            self.refused += 1
            self.last_error = gate
            return False, gate
        prof = self.profile
        interval = 1.0 / max(1.0, float(prof.get("max_hz", 30)))
        # 松手帧必须落地：DT7/DR16 那侧**没有**"过期自动清零"，接收机会把最后
        # 一帧的键位一直重复到下一帧为止。所以只要线上还挂着我们发出去的按键，
        # 这一帧全中立的松手帧就**不许**被 max_hz 挡下。
        # 实测（心跳 75Hz、出口闸 30Hz，按 24 个相位松手）：旧代码松手帧 24/24
        # 全被挡；最好情况也要等 4Hz 空闲零帧补，也就是 **253ms** 之后车才真的
        # 松手——3.5m/s 下将近一米过冲。离线复现见 tools/dt7_link/test_dt7_release_flush.py。
        neutral = (int(pressed_bits) == 0 and not left and not right and not mid
                   and int(mouse_dx) == 0 and int(mouse_y) == 0 and int(mouse_z) == 0)
        if not self.mock_output:
            if _neutral_release and not neutral:
                self.last_error = "neutral release cannot contain control input"
                return False, self.last_error
            if not _neutral_release:
                if self.safety is None:
                    self.last_error = "DT7 has no bound safety policy; real TX is LOCKED"
                    return False, self.last_error
                allowed, reason = self.safety.can_transmit_dt7(
                    qualification_token=qualification_token, now=now)
                if not allowed:
                    self.last_error = str(reason)
                    return False, self.last_error
                if not qualification_token and self.authorization_guard is not None:
                    try:
                        allowed, reason = self.authorization_guard(self.profile)
                    except Exception as exc:
                        allowed, reason = False, f"DT7 evidence verification failed: {exc}"
                    if not allowed:
                        self.safety.lock_control(str(reason))
                        self.last_error = str(reason)
                        return False, self.last_error
                if qualification_token and (int(pressed_bits) not in (0, 1 << 12)
                        or mouse_dx or mouse_y or mouse_z or left or right or mid):
                    self.last_error = "DT7 qualification permits only neutral/X probe, never driving or firing"
                    return False, self.last_error
        must_land = bool(neutral and not self.wire_neutral)
        # 破例的额度是**结构上**有界的，不需要再加时间下限：连续两帧都走 must_land
        # 是不可能的（must_land 成功后 wire_neutral 立刻变 True），所以"破例"最多
        # 和"正常发送"一样频繁 —— 线上速率上限 2*max_hz = 60Hz，22 字节 @961200
        # 的线容量是 ~4.3kHz，差两个数量级。
        if not must_land and now + 1e-9 < self._next_due:
            self.dropped_rate += 1
            # 告诉调用方"这帧没走，鼠标增量请滚存到下一帧"（见 control.py）
            self.last_send_rate_limited = True
            self.last_send_dropped = True
            self.last_error = f"超过 {float(prof.get('max_hz', 30)):g}Hz 上限，本帧未发"
            return False, self.last_error          # 被限流不消耗时隙（旧版白吃一帧）
        fields = list(prof["payload_fields"])
        self.mid_dropped = bool(mid and "mid" not in fields)
        if self.mid_dropped:
            # 这套载荷（Input_Device_Status_RC）根本没有中键位。丢掉中键是安全的
            # （固件只有 press_l/press_r 两个消费者），但**不能因此把 WASD 一起拒发**，
            # 所以这里只记账并在 last_note 里说清楚，不静默假装一切正常。
            self.last_note = "载荷无 mid 位：本帧中键未表达"
        values = {"mouse_x": mouse_dx, "mouse_y": mouse_y, "mouse_z": mouse_z,
                  "left": left, "right": right, "keyboard_value": pressed_bits}
        if mid is not None and "mid" in fields:
            values["mid"] = bool(mid)
        values, mouse_clipped = _clamp_mouse(values, prof)
        try:
            frame = self.encode(values)
        except (struct.error, Dt7ConfigError) as exc:
            self.refused += 1
            self.last_error = f"打包失败：{exc}"
            return False, self.last_error
        self._next_due = now + interval
        self.last_frame = frame
        notes = [] if bool(prof.get("cmd_confirmed")) else ["实验命令号，别当已定案"]
        if getattr(self, "mid_dropped", False):
            notes.append("中键未表达（载荷无 mid 位）")
        clipped = list(getattr(self, "clipped_fields", None) or []) + mouse_clipped
        if clipped:
            notes.append("越界截断：" + "/".join(sorted(set(clipped))))
        self.last_note = "；".join(notes)
        if not self.mock_output:
            # A failed/partial write cannot prove the receiver stayed neutral.
            if not neutral:
                self.wire_neutral = False
            try:
                self._emit(frame)
            except Exception as exc:               # noqa: BLE001
                reason = f"写 DT7 口失败：{exc}"
                self.last_send_dropped = True      # 线**没**变，调用方必须补发
                if not _neutral_release and self.safety is not None:
                    self.safety.lock_control("DT7 disconnected/write failed; explicit re-arm required")
                self.last_error = reason
                return False, self.last_error
        self.last_error = ""
        self.sent += 1
        self.last_send_dropped = False
        # 只有真写出去（或 mock 里等价于写出去）才允许改变"线上状态"的记账。
        # 写失败时上面直接 return 了，wire_neutral 会一直停在 False，
        # 于是下一次松手帧仍然是 must_land —— 端口恢复的那一刻它就插队出去。
        self.wire_neutral = neutral
        if not self.mock_output and not _neutral_release and self.safety is not None:
            self.safety.mark_published("DT7KeyboardMouseControl", now=now)
        if must_land:
            self.forced_releases += 1
        return True, "mock" if self.mock_output else "ok"

    def _open_target(self) -> tuple[str, str]:
        """该往哪个口写。返回 (设备名, 错误说明)，二者只有一个非空。

        `DT7 Port` 默认是 "Auto"：拿字符串 Auto 去 open 必然失败，第一次上车就卡在这。
        但也不能借用给机器人本体写的那套 Auto 打分——那会挑中 C 板的 CDC(0483:5720)，
        于是我们把遥控器帧写进车、又拿同一块板的 km_key 回读判"DT7 不认这一帧"，
        得到一个彻底的假结论。所以这里只认得出的 DJI VCOM，认不出就明说。
        """
        want = str(self.port or "").strip()
        resolved = ports.resolve_rc_port(want or ports.AUTO_PORT)
        if resolved == ports.STUB_PORT:
            return "", ("没找到 DT7 的 DJI USB Virtual COM（VID_FFF0&PID_0008）。"
                        "请把遥控器 USB 插好，或在 DT7 Port 里显式选口")
        entry = ports.port_identity(resolved)
        vid = int(entry.get("vid", 0) or 0)
        pid = int(entry.get("pid", 0) or 0)
        looks_robot = bool(entry) and ports.is_robot_port(entry)
        if looks_robot and not bool(self.profile.get("allow_robot_port")):
            return "", (f"{resolved} 是机器人本体的 CDC 串口（USB {vid:04X}:{pid:04X}），"
                        "不是遥控器；往那儿写只会得到假的 NO-CHANGE 结论，已拒发")
        return resolved, ""

    def _drop_handle(self) -> None:
        """Release ownership only after the driver confirms close."""
        handle = self._handle
        try:
            if handle is not None:
                close_resource(handle)
        except Exception as exc:
            self._handle_close_failed = True
            self._open_last_error = f"DT7 close unconfirmed; explicit close retry required: {exc}"
            raise
        self._handle = None
        self.resolved_port = ""
        self._handle_requested_port = ""
        self._handle_close_failed = False

    def _emit(self, frame: bytes) -> None:
        if self._handle_close_failed:
            raise RuntimeError(self._open_last_error)
        if self.write_bytes is not None:
            if self.pre_write_guard is not None and not self.pre_write_guard():
                raise RuntimeError("stale DT7 intent cancelled before write")
            written = self.write_bytes(frame)
            if written is not None and written != len(frame):
                raise RuntimeError(f"short write {written}/{len(frame)}")
            return
        # Backoff precedes all discovery. A missing Auto target must back off
        # too; enumeration can take tens of milliseconds on Windows.
        now = float(self.time_fn())
        if now < self._open_backoff_until:
            raise RuntimeError(f"DT7 端口暂时不重试: {self._open_last_error}")
        handle = self._handle
        target = self.resolved_port
        if handle is None or self._handle_requested_port != str(self.port):
            ports.invalidate_port_cache()  # validate current identity on every new handle
            target, why = self._open_target()
            if not target:
                self._open_last_error = why
                self._open_backoff_until = now + float(self.profile.get("open_retry_s", 0.5))
                raise RuntimeError(why)
            self._drop_handle()
            import serial
            try:
                handle = serial.Serial(target, int(self.profile.get("baudrate", 961200)),
                                       timeout=0.2, write_timeout=0.2)
            except Exception as exc:
                self._open_backoff_until = now + float(self.profile.get("open_retry_s", 0.5))
                self._open_last_error = str(exc)
                raise
            self._handle = handle
            self._handle_requested_port = str(self.port)
        self.resolved_port = target
        self._open_last_error = ""
        try:
            if self.pre_write_guard is not None and not self.pre_write_guard():
                raise RuntimeError("stale DT7 intent cancelled before write")
            written = handle.write(frame)
            if written != len(frame):
                raise RuntimeError(f"short write {written}/{len(frame)}")
        except Exception as exc:
            self._open_last_error = str(exc)
            ports.invalidate_port_cache()
            self._open_backoff_until = now + float(self.profile.get("open_retry_s", 0.5))
            self._drop_handle()
            raise RuntimeError(f"{target} 写入失败（句柄已丢弃，下一帧重开）：{exc}")

    def release(self, now: float | None = None) -> tuple[bool, str]:
        """全零一帧：DT7 没有"过期自动清零"的兜底，松手必须我们主动发。"""
        now = float(self.time_fn()) if now is None else now
        self._next_due = 0.0
        return self.send(pressed_bits=0, mouse_dx=0, mouse_y=0, mouse_z=0,
                         left=False, right=False, mid=None, now=now, _neutral_release=True)

    def watermark_token(self) -> str:
        """本出口能自证的那半句水印（真函数在模块级，那里才拿得到 control 的 pending）。

        方法刻意不叫 status_token：同名不同参会让人以为它和模块函数是一回事。
        """

        return status_token(mock_output=self.mock_output,
                            resolved_port=self.resolved_port,
                            wire_neutral=self.wire_neutral)

    def close(self) -> None:
        """Close or retain the handle and raise; a later call explicitly retries."""
        self._drop_handle()
