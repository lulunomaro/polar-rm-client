"""队内 StandardRobot++ 上位机链路（SOF=0x5A）编解码。

与官方裁判串口帧（`rm_client.protocol.RefereeFrameCodec`，SOF=0xA5）是两套
**结构不兼容**的协议族：本链路的命令字是帧头内的 u8 段 id，帧头只有 4 字节，
数据段前置 u32 时间戳。CRC8/CRC16 算法与官方逐位相同，因此直接复用
`rm_client.protocol` 里的实现，不另写一份查表。

线缆格式（对齐 StandardRobotpp/application/typedef/usb_typdef.h）::

    sof(u8=0x5A) | len(u8) | id(u8) | crc8(u8) | time_stamp(u32) | body | crc16(u16 LE)

其中 ``len = 4 + len(body)``（即 time_stamp 与 body 之和，不含帧头与尾部 CRC16），
整帧长度 ``= 4 + len + 2``。CRC8 覆盖帧头前 3 字节，CRC16 覆盖帧头到 body 结束。

下行只实现下位机 `UsbSendData()` 中**当前真的未被注释**的 14 个段；其余段 id
在协议里已定义但固件不发送，解码器遇到时按未知段安全跳过，不报错也不崩。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from ..protocol import crc8, crc16

POLAR_SOF = 0x5A
HEADER_SIZE = 4
TIMESTAMP_SIZE = 4
CRC16_SIZE = 2
MAX_SEGMENT_LEN = 0xFF  # len 字段是 u8，理论上限 255
MAX_RETAINED_BYTES = 4096  # 半帧缓冲上限，杜绝异常输入撑爆内存

# ---------------- 下行段 id（下位机 -> 上位机） ----------------
SEG_DEBUG = 0x01
SEG_IMU = 0x02
SEG_ROBOT_STATE_INFO = 0x03
SEG_GAME_STATUS = 0x04
SEG_ALL_ROBOT_HP = 0x06
SEG_EVENT = 0x07
SEG_REFEREE_WARNING = 0x08
SEG_ROBOT_STATUS = 0x0A
SEG_BUFF = 0x0D
SEG_SHOOT_DATA = 0x0F
SEG_RFID_STATUS = 0x11
SEG_GROUND_ROBOT_POSITION = 0x13
SEG_JOINT_STATE = 0x1D
SEG_ROBOT_MOTION = 0x1E
# 底盘状态回报（自转开/关、底盘模式、自检位）。由固件补丁
# tools/firmware/host_bridge/ 新增；未打补丁的固件不会发这一段，
# 客户端因此可以据此判断"对面有没有这个能力"。
SEG_CHASSIS_STATE = 0x1F

# ---------------- 上行段 id（上位机 -> 下位机） ----------------
CMD_ROBOT = 0x01
CMD_PID_DEBUG = 0x02
CMD_VIRTUAL_RC = 0x03
# 底盘指令（显式自转开/关）。同样由 host_bridge 补丁新增。
CMD_CHASSIS_CMD = 0x04

# ---------------------------------------------------------------------------
# 显式自转指令（上行 0x04）
#
# 为什么不能继续用 0x03 的 SHIFT 键位：固件对 SHIFT 是**边沿翻转**，
#   chassis_mecanum.c:177  FOLLOW 下按一次 -> spin_flag = 1
#   chassis_mecanum.c:223  SPIN   下按一次 -> spin_flag = 0
#   chassis_omni.c:96      按一次 -> spin_flag 取反
#   松手(:187 / :107)      不改变 spin_flag
# 后果是客户端只能"开"、无法"关"，再开一次反而把自转关掉——
# "受击后自动小陀螺"需要明确的开/关语义，借模拟按键做不到。
# 0x04 就是为修这个而加的：语义是"设定"，且有 0x1F 回读可闭环验证。
#
# 刻意不提供"翻转"取值：客户端在收到 0x1F 之前并不知道当前状态，
# 发翻转就会在第二次把它关掉，正是本次要修掉的缺陷本身。
# ---------------------------------------------------------------------------
SPIN_CMD_NONE = 0x00
SPIN_CMD_ON = 0x01
SPIN_CMD_OFF = 0x02

#: 0x04 param0 bit0：上位机请求云台直控（电平语义，与 spin_cmd 同一条 150ms
#: 新鲜度门控）。只有打了 tools/firmware/gimbal_host/ 桥的固件才认识这个位；
#: 未打桥的固件该字节本来就被忽略，所以发它不改变老固件的任何行为。
GIMBAL_HOST_REQUEST_ON = 0x01
#: 与固件 HOST_SPIN_CMD_VALID_MS 一致：超过该时长没刷新，固件就停止强制、
#: 把控制权交还物理遥控器。客户端按"电平"方式每 tick 重发当前指令。
SPIN_CMD_VALID_MS = 150
CHASSIS_CMD_BODY_FORMAT = "<4B"  # spin_cmd, param0, param1, reserved
CHASSIS_CMD_BODY_SIZE = struct.calcsize(CHASSIS_CMD_BODY_FORMAT)  # 4

# 上行 0x03 的载荷就是整份 `RC_ctrl_t`（remote_control.h:141-161）：
#   int16 ch[5] | int8 s[2] | int16 mouse.x/y/z | u8 press_l | u8 press_r | u16 key.v
# = 10 + 2 + 6 + 2 + 2 = 22 字节；len 字段 = 4(时间戳) + 22 = 26；整帧 = 4 + 26 + 2 = 32。
# 与固件结构体、以及队内上位机 data_procecss.py:470-497 的实发字节长度逐字节对齐。
RC_CTRL_BODY_FORMAT = "<5h2b3h2BH"
RC_CTRL_BODY_SIZE = struct.calcsize(RC_CTRL_BODY_FORMAT)  # 22

# 固件 `remote_control.h:103-138` 的键位位序，与 `rm_client.control.KEYBOARD_BITS`
# 完全一致（W=0 S=1 A=2 D=3 SHIFT=4 CTRL=5 Q=6 E=7 R=8 F=9 G=10 Z=11 X=12 C=13 V=14 B=15）。
# 这意味着客户端现有的 keyboard_value 位图可以原封不动塑进 key.v。
RC_KEY_BITS = {
    "W": 0, "S": 1, "A": 2, "D": 3, "SHIFT": 4, "CTRL": 5, "Q": 6, "E": 7,
    "R": 8, "F": 9, "G": 10, "Z": 11, "X": 12, "C": 13, "V": 14, "B": 15,
}


# 允许经 0x03 虚拟遥控器下发的**驾驶键子集**：只放行 W/S/A/D。
#
# 这不是过度保守，而是固件对其它键位有真实动作，全量透传会绕过安全纪律：
#   shoot_fric_trigger.c:100  Q -> 启动摩擦轮（开火前置执行器！）
#   shoot_fric_trigger.c:104  E -> 关闭摩擦轮
#   chassis_omni.c:96         SHIFT -> 切换小陀螺
#   chassis_omni.c:334        F     -> 翻转 sc_flag
# 客户端的 keyboard_value 是全 16 位位图，直接灌进 key.v 就等于把“开火相关
#  actuation”从遥控器手里搬到了客户端，与“fire 恒为 0”的约定相矛盾。
# 需要放行更多键位时，必须显式拓宽本掩码，而不是去掉掩码。
# 键位分三层，而不是一刀切只留 W/S/A/D：
#
#  Tier 1 驾驶（永远放行）W/S/A/D —— 底盘平移，纯运动意图。
#  Tier 2 已废除：SHIFT（小陀螺）**不再从客户端注入**。
#         它曾经是“受击后小陀螺”的唯一通路，但固件对 SHIFT 是边沿翻转
#         （chassis_mecanum.c:177 置1 / :223 置0 / chassis_omni.c:96 取反，
#         而松手 :187 / :107 不改变 spin_flag），客户端因此只能开、关不掉，
#         再开一次反而把自转关掉。自转现已改走上行 0x04 的显式“设定”指令，
#         并用下行 0x1F 回读闭环；见 tools/firmware/host_bridge/。
#         两条通路共存会互相打架，所以这里把 SHIFT 做成**结构上不可达**，
#         而不是仅靠调用方传 False——后者只要有一个遗漏的调用点就会漏。
#  Tier 3 永禁：Q/E（启动/关闭摩擦轮 = 开火前置执行器）、F（翻转 sc_flag）、
#         以及其余无对应动作的位。
#
# 注意跨层耦合：固件补丁里的 VRC_DRIVE_KEY_MASK 必须至少包含 Tier 1，
# 否则客户端发了驾驶键也会被固件剔掉。两层默认值不一致时以“更严”的一侧为准。
RC_DRIVE_KEY_MASK = (
    (1 << RC_KEY_BITS["W"]) | (1 << RC_KEY_BITS["S"])
    | (1 << RC_KEY_BITS["A"]) | (1 << RC_KEY_BITS["D"])
)

#: 小陀螺键位。保留常量仅用于测试断言“它确实被拦下”，
#: **不参与任何允许集合**。自转请走 encode_chassis_cmd()。
RC_SPIN_KEY_MASK = (1 << RC_KEY_BITS["SHIFT"])

#: 客户端允许到达固件的最大键位集合：只有驾驶键。
#: 刻意不包含 RC_SPIN_KEY_MASK，理由见上方 Tier 2 说明。
RC_MAX_ALLOWED_KEY_MASK = RC_DRIVE_KEY_MASK


def drive_key_mask(allow_spin: bool = False) -> int:
    """算出本次可用的键位掩码。

    `allow_spin` 参数保留只是为了不弄坏已有调用方，**它已不再有任何作用**：
    SHIFT 无论怎么开都进不来。自转请改用 `encode_chassis_cmd()`（上行 0x04），
    那是“设定”语义且可回读；而 SHIFT 是“翻转”，客户端在没回读时根本
    不知道当前状态，用它表达“开”/“关”在原理上就做不到。
    """

    return RC_DRIVE_KEY_MASK


def rc_key_value(key_state: dict[str, bool], allow_spin: bool = False) -> int:
    """把按键状态表打包成固件的 `rc_ctrl.key.v` 位图。

    位序与 `rm_client.control.KEYBOARD_BITS` 相同，所以官方键鼠链路已经算好的
    keyboard_value 可以直接复用；但输出会被 `drive_key_mask(allow_spin)` 裁剪。
    """

    value = 0
    for name, bit in RC_KEY_BITS.items():
        if key_state.get(name):
            value |= 1 << bit
    return value & drive_key_mask(allow_spin) & 0xFFFF

# 固件 `GetScCmd*` 中实测**有消费者**的字段只有 gimbal 角度与 fire；
# 底盘 speed/velocity/height、fric_on、tracking 的 getter 在 application/ 下
# 零调用者，填了也不会驱动电机。为保持线缆一致仍按完整结构体编码。
_ROBOT_CMD_BODY = struct.Struct("<fff ffff ff BBB".replace(" ", ""))

# 内置默认表：作为**规格文件不可用时的兜底**，而不是首选真相。
# 正常情况下面会用 polar_protocol.json 派生的表覆盖它（见 _apply_spec）。
_BUILTIN_SEGMENT_FORMATS: dict[int, str] = {
    SEG_IMU: "<ffffff",
    SEG_ROBOT_STATE_INFO: "<HB",
    SEG_GAME_STATUS: "<BHQ",
    SEG_ALL_ROBOT_HP: "<8H",
    SEG_EVENT: "<I",
    SEG_REFEREE_WARNING: "<BBB",
    SEG_ROBOT_STATUS: "<BBHHHHHfffBHHHH",
    SEG_BUFF: "<BHBBHB",
    SEG_SHOOT_DATA: "<BBBf",
    SEG_RFID_STATUS: "<IB",
    SEG_GROUND_ROBOT_POSITION: "<10f",
    SEG_JOINT_STATE: "<ff",
    SEG_ROBOT_MOTION: "<fff",
}

_SEGMENT_FORMATS: dict[int, str] = dict(_BUILTIN_SEGMENT_FORMATS)

#: 规格加载结果，供自检/HUD 说明“当前用的是规格还是内置兜底”
SPEC_STATUS: dict[str, Any] = {"ok": False, "source": "", "errors": "", "warnings": ""}


def _apply_spec() -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[str]]]:
    """用规格文件覆盖内置表。规格无效时保留内置表并记录原因（fail-safe）。"""

    from . import polar_spec as _spec

    loaded = _spec.get_spec()
    SPEC_STATUS["ok"] = bool(loaded.ok)
    SPEC_STATUS["source"] = loaded.source
    SPEC_STATUS["errors"] = loaded.error_text()
    SPEC_STATUS["warnings"] = loaded.warning_text()
    if not loaded.ok:
        # 不能因为一个手滑的 JSON 就把链路弄砖：回退到内置表，
        # 但要把原因带出来，否则人会误以为自己的修改已生效。
        SPEC_STATUS["fell_back"] = True
        return {}, {}
    SPEC_STATUS["fell_back"] = False
    _SEGMENT_FORMATS.clear()
    _SEGMENT_FORMATS.update(loaded.segment_formats)
    inputs: dict[int, list[str]] = {}
    for entry in loaded.raw.get("downlink_segments") or []:
        try:
            seg_id = int(str(entry.get("id")), 16)
        except (TypeError, ValueError):
            continue
        inputs[seg_id] = [str(n) for n in (entry.get("inputs") or [])]
    return loaded.segment_outputs, inputs


# 注意：元组目标不能加类型标注（Python 语法限制），所以这里不写 : tuple[...]
_SEGMENT_OUTPUTS, _SEGMENT_INPUTS = _apply_spec()

# 0x01 Debug 段是 10 个具名浮点位，单独处理。
_DEBUG_PACKAGE_NUM = 10
_DEBUG_PACKAGE_SIZE = 15  # name[10] + type(u8) + float


def _generic_outputs(segment_id: int) -> list[dict[str, Any]]:
    """从规格里取该段的输出声明（电控新增段时只需写 JSON）。"""

    return list(_SEGMENT_OUTPUTS.get(segment_id) or [])


def apply_spec_outputs(
    inputs: list[str], outputs: list[dict[str, Any]], values: tuple[Any, ...]
) -> dict[str, Any]:
    """按规格声明把 struct 解出的值映射成具名字段。

    支持三种声明：直接改名、`{from_index}` 按下标、`{from, shift, width}` / `{from, bit}`
    位域。这让“新增一个下行段”不再需要写 Python 解码函数。
    """

    named = dict(zip(inputs, values))
    result: dict[str, Any] = {}
    for out in outputs:
        name = str(out.get("name") or "")
        if not name:
            continue
        if "from_index" in out:
            index = int(out["from_index"])
            if 0 <= index < len(values):
                result[name] = values[index]
            continue
        src = out.get("from")
        if src is None:
            continue
        base = named.get(str(src))
        if base is None:
            continue
        if "bit" in out:
            result[name] = bool(int(base) & (1 << int(out["bit"])))
        elif "shift" in out:
            width = int(out.get("width", 1))
            result[name] = (int(base) >> int(out["shift"])) & ((1 << width) - 1)
        else:
            result[name] = base
    return result


@dataclass(slots=True)
class PolarSegment:
    """一条校验通过的下行数据段。"""

    segment_id: int
    raw_timestamp: int
    fields: dict[str, Any] = field(default_factory=dict)


class PolarDecodeStats:
    """可观测的解码健康度，供 readiness 与 HUD 展示。"""

    def __init__(self) -> None:
        self.frames_ok = 0
        self.crc8_errors = 0
        self.crc16_errors = 0
        self.unknown_segments = 0
        self.length_errors = 0
        self.buffer_overflows = 0
        self.last_error = ""


def _decode_robot_state_info(values: tuple[Any, ...]) -> dict[str, Any]:
    type_bits, state_bits = values
    return {
        "chassis_type": type_bits & 0x07,
        "gimbal_type": (type_bits >> 3) & 0x07,
        "shoot_type": (type_bits >> 6) & 0x07,
        "arm_type": (type_bits >> 9) & 0x07,
        "custom_controller_type": (type_bits >> 12) & 0x07,
        # state 位：0 正常，1 错误
        "chassis_error": bool(state_bits & 0x01),
        "gimbal_error": bool(state_bits & 0x02),
        "shoot_error": bool(state_bits & 0x04),
        "arm_error": bool(state_bits & 0x08),
        "custom_controller_error": bool(state_bits & 0x10),
    }


def _decode_game_status(values: tuple[Any, ...]) -> dict[str, Any]:
    packed, remain, sync_stamp = values
    return {
        "game_type": packed & 0x0F,
        "game_progress": (packed >> 4) & 0x0F,
        "stage_remain_time": remain,
        "sync_time_stamp": sync_stamp,
    }


def _decode_robot_status(values: tuple[Any, ...]) -> dict[str, Any]:
    (
        robot_id,
        robot_level,
        current_hp,
        maximum_hp,
        cooling,
        heat_limit,
        power_limit,
        x,
        y,
        angle,
        armor_packed,
        allowance_17,
        allowance_42,
        gold_coin,
        fortress,
    ) = values
    return {
        "robot_id": robot_id,
        "robot_level": robot_level,
        "current_hp": current_hp,
        "maximum_hp": maximum_hp,
        "barrel_cooling_value": cooling,
        "barrel_heat_limit": heat_limit,
        "chassis_power_limit": power_limit,
        "x": x,
        "y": y,
        "angle_deg": angle,
        "armor_id": armor_packed & 0x0F,
        "hp_deduction_reason": (armor_packed >> 4) & 0x0F,
        "projectile_allowance_17mm": allowance_17,
        "projectile_allowance_42mm": allowance_42,
        "remaining_gold_coin": gold_coin,
        "projectile_allowance_fortress": fortress,
    }


def _decode_all_robot_hp(values: tuple[Any, ...]) -> dict[str, Any]:
    # 固件字段序为 ally 1/2/3/4 / (reserved) / 7 / outpost / base；
    # 敌方 HP 在 usb_typdef.h 中已被整段注释，队内链路不提供敌方血量。
    names = (
        "ally_1_hp",
        "ally_2_hp",
        "ally_3_hp",
        "ally_4_hp",
        "reserved",
        "ally_7_hp",
        "ally_outpost_hp",
        "ally_base_hp",
    )
    return dict(zip(names, values))


def _decode_buff(values: tuple[Any, ...]) -> dict[str, Any]:
    recovery, cooling, defence, vulnerability, attack, energy = values
    return {
        "recovery_buff": recovery,
        # 固件 UsbSendBuffData() 当前漏填 cooling_buff，保持原名但标注不可信
        "cooling_buff": cooling,
        "defence_buff": defence,
        "vulnerability_buff": vulnerability,
        "attack_buff": attack,
        "remaining_energy": energy,
    }


def _decode_shoot_data(values: tuple[Any, ...]) -> dict[str, Any]:
    bullet_type, shooter_number, frequency, initial_speed = values
    return {
        "bullet_type": bullet_type,
        "shooter_number": shooter_number,
        "launching_frequency_hz": frequency,
        "initial_speed_mps": initial_speed,
    }


def _decode_rfid(values: tuple[Any, ...]) -> dict[str, Any]:
    status, status_2 = values
    return {"rfid_status": status, "rfid_status_2": status_2}


def _decode_ground_positions(values: tuple[Any, ...]) -> dict[str, Any]:
    keys = (
        "hero_x",
        "hero_y",
        "engineer_x",
        "engineer_y",
        "standard_3_x",
        "standard_3_y",
        "standard_4_x",
        "standard_4_y",
        "reserved_1_x",
        "reserved_2_y",
    )
    return dict(zip(keys, values))


def _decode_simple(names: tuple[str, ...]) -> Callable[[tuple[Any, ...]], dict[str, Any]]:
    def _decode(values: tuple[Any, ...]) -> dict[str, Any]:
        return dict(zip(names, values))

    return _decode


_SEGMENT_DECODERS: dict[int, Callable[[tuple[Any, ...]], dict[str, Any]]] = {
    SEG_IMU: _decode_simple(
        ("yaw_rad", "pitch_rad", "roll_rad", "yaw_vel_rps", "pitch_vel_rps", "roll_vel_rps")
    ),
    SEG_ROBOT_STATE_INFO: _decode_robot_state_info,
    SEG_GAME_STATUS: _decode_game_status,
    SEG_ALL_ROBOT_HP: _decode_all_robot_hp,
    SEG_EVENT: _decode_simple(("event_data",)),
    SEG_REFEREE_WARNING: _decode_simple(
        ("level", "offending_robot_id", "count")
    ),
    SEG_ROBOT_STATUS: _decode_robot_status,
    SEG_BUFF: _decode_buff,
    SEG_SHOOT_DATA: _decode_shoot_data,
    SEG_RFID_STATUS: _decode_rfid,
    SEG_GROUND_ROBOT_POSITION: _decode_ground_positions,
    SEG_JOINT_STATE: _decode_simple(("pitch_rad", "yaw_rad")),
    SEG_ROBOT_MOTION: _decode_simple(("vx_mps", "vy_mps", "wz_rps")),
}


def _decode_debug(body: bytes) -> dict[str, Any]:
    """解出 Debug 段里的具名浮点位，坏条目逐条隔离不影响其余。"""

    packages: dict[str, float] = {}
    for index in range(_DEBUG_PACKAGE_NUM):
        offset = index * _DEBUG_PACKAGE_SIZE
        chunk = body[offset : offset + _DEBUG_PACKAGE_SIZE]
        if len(chunk) < _DEBUG_PACKAGE_SIZE:
            break
        name = chunk[0:10].rstrip(b"\x00")
        kind = chunk[10]
        (value,) = struct.unpack_from("<f", chunk, 11)
        if not name or not math.isfinite(value):
            continue
        try:
            key = name.decode("ascii")
        except UnicodeDecodeError:
            continue
        # type 0 表示整型视图，1 表示浮点；其余按不可信丢弃。
        if kind not in (0, 1):
            continue
        packages[key] = value
    return {"packages": packages}


class PolarFrameCodec:
    """0x5A 链路的流式编解码器。

    与官方 codec 一样遵循 fail-closed：CRC 不符、长度不符、未知段都只丢弃并
    计数，绝不抛出打断接收线程；半帧保留在缓冲里等待后续字节。
    """

    def __init__(self) -> None:
        self.stats = PolarDecodeStats()

    # ------------------------------------------------------------------
    def decode_with_consumed(self, stream: bytes) -> tuple[list[PolarSegment], int]:
        """解析出所有完整段，返回（段列表, 已消费字节数）。"""

        segments: list[PolarSegment] = []
        index = 0
        total = len(stream)

        while index + HEADER_SIZE + TIMESTAMP_SIZE + CRC16_SIZE <= total:
            if stream[index] != POLAR_SOF:
                index += 1
                continue

            header = stream[index : index + HEADER_SIZE]
            if crc8(header[:3]) != header[3]:
                self.stats.crc8_errors += 1
                index += 1
                continue

            segment_len = header[1]
            segment_id = header[2]
            frame_len = HEADER_SIZE + segment_len + CRC16_SIZE
            if segment_len < TIMESTAMP_SIZE:
                self.stats.length_errors += 1
                self.stats.last_error = f"段 0x{segment_id:02X} 长度 {segment_len} 小于时间戳"
                index += 1
                continue
            if index + frame_len > total:
                break  # 半帧，保留缓冲等下一批字节

            frame = stream[index : index + frame_len]
            expected_crc = int.from_bytes(frame[-CRC16_SIZE:], "little")
            if crc16(frame[:-CRC16_SIZE]) != expected_crc:
                self.stats.crc16_errors += 1
                index += 1
                continue

            (raw_timestamp,) = struct.unpack_from("<I", frame, HEADER_SIZE)
            body = frame[HEADER_SIZE + TIMESTAMP_SIZE : -CRC16_SIZE]
            index += frame_len

            decoder = _SEGMENT_DECODERS.get(segment_id)
            if segment_id == SEG_DEBUG:
                if len(body) != _DEBUG_PACKAGE_SIZE * _DEBUG_PACKAGE_NUM:
                    self.stats.length_errors += 1
                    continue
                segments.append(
                    PolarSegment(SEG_DEBUG, raw_timestamp, _decode_debug(body))
                )
                self.stats.frames_ok += 1
                continue
            if decoder is None:
                # 规格里声明了但没写专用解码函数的段：走通用映射。
                # 这样“电控新增一个下行段”只需改 JSON，不必改 Python。
                outputs = _generic_outputs(segment_id)
                if not outputs:
                    # 固件定义了但未发送的段：安全跳过，不计错误（可能是旧/新版固件）
                    self.stats.unknown_segments += 1
                    continue
                spec0 = _SEGMENT_FORMATS.get(segment_id)
                if spec0 is None or len(body) != struct.calcsize(spec0):
                    self.stats.length_errors += 1
                    continue
                try:
                    values0 = struct.unpack(spec0, body)
                except struct.error:
                    self.stats.length_errors += 1
                    continue
                segments.append(
                    PolarSegment(
                        segment_id,
                        raw_timestamp,
                        apply_spec_outputs(_SEGMENT_INPUTS.get(segment_id, []), outputs, values0),
                    )
                )
                self.stats.frames_ok += 1
                continue

            # 必须用 .get()，不能用 []。
            #
            # 这两张表的来源不同：_SEGMENT_DECODERS 是**写死在代码里**的专用
            # 解码器（:433），_SEGMENT_FORMATS 则会被 _apply_spec() 整个清空
            # 换成 polar_protocol.json 里的规格（:217-218）。所以完全可能出现
            # "某段有专用解码器、但规格里没有它的 struct" —— 只要电控把那一段
            # 从 JSON 里删掉或改名（而这正是本项目设计的正常改法）就会命中。
            #
            # 之前这里写的是 _SEGMENT_FORMATS[segment_id]，KeyError 直接逃出
            # decode_with_consumed，被 polar_transport._loop 捕获后 break 打死
            # 接收线程，再被 manager 的重连每 2 秒拉起来 —— 现场表现是
            # "开串口 → 读 5ms → 崩 → 关串口"无限抖动且遥测全灭，
            # 违背本类 docstring 第 482-484 行"绝不抛出打断接收线程"的承诺。
            spec = _SEGMENT_FORMATS.get(segment_id)
            if spec is None:
                # 有专用解码器却没规格：算"规格不认识它"，按未知段安全跳过。
                self.stats.unknown_segments += 1
                self.stats.last_error = (
                    f"段 0x{segment_id:02X} 有解码器但规格里没有它的格式串，已跳过"
                )
                continue
            expected_body = struct.calcsize(spec)
            if len(body) != expected_body:
                self.stats.length_errors += 1
                self.stats.last_error = (
                    f"段 0x{segment_id:02X} 长度 {len(body)} != 期望 {expected_body}"
                )
                continue
            try:
                values = struct.unpack(spec, body)
                fields = decoder(values)
            except Exception as exc:  # pragma: no cover - 防御性
                self.stats.last_error = f"段 0x{segment_id:02X} 解码异常: {exc}"
                continue
            segments.append(PolarSegment(segment_id, raw_timestamp, fields))
            self.stats.frames_ok += 1

        return segments, index

    # ------------------------------------------------------------------
    @staticmethod
    def _wrap(segment_id: int, raw_timestamp: int, body: bytes) -> bytes:
        segment_len = TIMESTAMP_SIZE + len(body)
        if segment_len > MAX_SEGMENT_LEN:
            raise ValueError(f"数据段过长: {segment_len} > {MAX_SEGMENT_LEN}")
        header = bytes((POLAR_SOF, segment_len & 0xFF, segment_id & 0xFF, 0x00))
        header = header[:3] + bytes((crc8(header[:3]),))
        frame = header + struct.pack("<I", raw_timestamp & 0xFFFFFFFF) + body
        return frame + crc16(frame).to_bytes(CRC16_SIZE, "little")

    def encode_robot_cmd(
        self,
        *,
        raw_timestamp: int,
        gimbal_pitch_rad: float,
        gimbal_yaw_rad: float,
        chassis_vx: float = 0.0,
        chassis_vy: float = 0.0,
        chassis_wz: float = 0.0,
        chassis_roll: float = 0.0,
        chassis_pitch: float = 0.0,
        chassis_yaw: float = 0.0,
        chassis_leg_length: float = 0.0,
        friction_on: bool = False,
        tracking: bool = False,
    ) -> bytes:
        """编码上行 0x01 RobotCmd。

        **fire 位在本客户端恒为 0**：开火权始终留在驾驶员手上，任何调用点都
        无法通过参数置 1（签名不暴露 fire）。`polar_link_test` 有不变量断言。
        """

        body = _ROBOT_CMD_BODY.pack(
            float(chassis_vx),
            float(chassis_vy),
            float(chassis_wz),
            float(chassis_roll),
            float(chassis_pitch),
            float(chassis_yaw),
            float(chassis_leg_length),
            float(gimbal_pitch_rad),
            float(gimbal_yaw_rad),
            0,  # fire —— 恒 0，见 docstring
            1 if friction_on else 0,
            1 if tracking else 0,
        )
        return self._wrap(CMD_ROBOT, raw_timestamp, body)


    def encode_virtual_rc(
        self,
        *,
        raw_timestamp: int,
        key_value: int = 0,
        key_mask: int = RC_DRIVE_KEY_MASK,
        channels: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
        switches: tuple[int, int] = (0, 0),
        mouse: tuple[int, int, int] = (0, 0, 0),
        left_button: bool = False,
        right_button: bool = False,
    ) -> bytes:
        """编码上行 0x03 虚拟遥控器帧（整份 `RC_ctrl_t`）。

        为什么需要它：固件里**已经有一套生效的 WASD 驾驶**（
        `chassis_omni.c` FOLLOW/SPIN 分支读 `rc->key.v` 直接产生斜坡速度，
        自带限幅与低通），但它读的是物理遥控器的键位位图。把位图塑进本帧，
        固件只需补“把 `VIRTUAL_RC_CTRL` 灌进 `rc_ctrl`”一处，就能复用全部
        既有驾驶逻辑 —— 比新写一个 speed_vector 消费者安全得多。

        注意：本函数不发送任何 fire/开火语义，且 `key_value` 会被 `key_mask`
        裁剪（默认只留 W/S/A/D）。Q（启动摩擦轮）、E、F（翻 sc_flag）属于
        开火相关或模式旁路，**任何情况下都不从客户端注入**；SHIFT（小陀螺）
        是防御性底盘模式，只有在调用方显式传 `drive_key_mask(allow_spin=True)`
        时才放行。
        """

        if len(channels) != 5:
            raise ValueError("RC_ctrl_t.ch 固定 5 通道")
        # 无论调用方传什么掩码，都不能超出客户端允许的上限（兵底）
        effective_mask = int(key_mask) & RC_MAX_ALLOWED_KEY_MASK & 0xFFFF
        body = struct.pack(
            RC_CTRL_BODY_FORMAT,
            *(int(c) for c in channels),
            *(int(s) for s in switches),
            *(int(m) for m in mouse),
            1 if left_button else 0,
            1 if right_button else 0,
            int(key_value) & effective_mask,
        )
        return self._wrap(CMD_VIRTUAL_RC, raw_timestamp, body)

    def encode_chassis_cmd(
        self,
        *,
        raw_timestamp: int,
        spin_cmd: int = SPIN_CMD_NONE,
        param0: int = 0,
        param1: int = 0,
    ) -> bytes:
        """编码上行 0x04 底盘指令（显式自转开/关 + param0 云台直控请求）。

        与 0x03 的关键差别：本帧**不覆盖驾驶员的物理遥控器**，
        它只维护固件里的 `chassis.spin_flag` 一个量，模式仲裁仍然
        完全由固件原有逻辑（含 s[0] 拨杆）决定。因此本帧在驾驶未授权时
        也可以安全发送——"我只想开自转、不想用客户端开车"是合理诉求。

        未知取值一律归为 NONE（不干预），避免半包/串位导致误动作。
        """

        cmd = int(spin_cmd) & 0xFF
        if cmd not in (SPIN_CMD_NONE, SPIN_CMD_ON, SPIN_CMD_OFF):
            cmd = SPIN_CMD_NONE
        body = struct.pack(
            CHASSIS_CMD_BODY_FORMAT,
            cmd,
            int(param0) & 0xFF,
            int(param1) & 0xFF,
            0,
        )
        return self._wrap(CMD_CHASSIS_CMD, raw_timestamp, body)


def deg_to_rad(value_deg: float) -> float:
    """客户端内部用度，固件 `GetScCmdGimbalAngle` 要弧度 —— 直接对接会差 57.3 倍。"""

    return math.radians(float(value_deg))


def rad_to_deg(value_rad: float) -> float:
    return math.degrees(float(value_rad))
