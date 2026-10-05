"""加载并校验 `polar_protocol.json` —— 队内 0x5A 链路的协议规格。

设计约束：
1. **规格是数据，不是代码**。电控改固件协议时只改 JSON，不需要读 Python。
2. **坏规格不能把链路搞砖**。文件缺失/JSON 语法错/字段非法时，记录原因并回退到
   `polar_link.py` 里的内置表；客户端照常启动，只是继续用旧规格。这比"启动即崩"
   或"静默按错误格式解包"都安全。
3. **校验要严**：struct 格式串要能算出字节数、位域的 shift/width 不能越界、
   outputs 引用的输入名必须存在。宁可拒绝整份规格，也不要半套生效。
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SPEC_FILENAME = "polar_protocol.json"
SPEC_PATH = Path(__file__).resolve().parent / SPEC_FILENAME

#: 规格里允许出现的顶层键（缺了不报错，多了也不报错，只做提示）
_REQUIRED_TOP_LEVEL = ("spec_version", "downlink_segments", "uplink", "roles")


@dataclass(slots=True)
class SpecIssue:
    kind: str  # "error" | "warning"
    where: str
    message: str


@dataclass(slots=True)
class PolarSpec:
    """解析并校验后的协议规格。"""

    raw: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    source: str = ""
    errors: list[SpecIssue] = field(default_factory=list)
    warnings: list[SpecIssue] = field(default_factory=list)

    # ---- 派生表（校验通过时才填充）----
    #: 段号 int -> struct 格式串
    segment_formats: dict[int, str] = field(default_factory=dict)
    #: 段号 int -> 段名
    segment_names: dict[int, str] = field(default_factory=dict)
    #: 段号 int -> 输出声明列表
    segment_outputs: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    #: 段号 int -> {解码字段名: RobotState/overlay 字段名}，供无专用处理器时通用落库
    segment_state_fields: dict[int, dict[str, str]] = field(default_factory=dict)
    #: 机器人编号 -> 兵种限幅字典
    roles: dict[int, dict[str, Any]] = field(default_factory=dict)
    fallback_role: dict[str, Any] = field(default_factory=dict)
    #: 上行键位
    key_bits: dict[str, int] = field(default_factory=dict)
    drive_key_mask: int = 0
    never_set_uplink_fields: tuple[str, ...] = ()
    spec_version: int = 0

    def error_text(self) -> str:
        parts = [f"{i.where}: {i.message}" for i in self.errors]
        return "; ".join(parts)

    def warning_text(self) -> str:
        parts = [f"{i.where}: {i.message}" for i in self.warnings]
        return "; ".join(parts)


def _as_int(value: Any, default: int = 0) -> int:
    """规格里段号/掩码既可以写 10 也可以写 "0x0A"，统一转成 int。"""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        try:
            return int(text, 16) if text.startswith("0x") else int(text, 0)
        except ValueError:
            return default
    return default


def _validate_segment(
    entry: Any, index: int, spec: PolarSpec
) -> tuple[int, str, str, list[dict[str, Any]]] | None:
    if not isinstance(entry, dict):
        spec.errors.append(SpecIssue("error", f"downlink_segments[{index}]", "不是对象"))
        return None
    where = f"downlink_segments[{index}]"
    name = str(entry.get("name") or f"segment_{index}")
    where = f"{where}({name})"

    seg_id = _as_int(entry.get("id"), -1)
    if seg_id < 0 or seg_id > 0xFF:
        spec.errors.append(SpecIssue("error", where, f"段号非法：{entry.get('id')!r}"))
        return None
    if entry.get("handled_special"):
        # 像 0x01 Debug 这种需要专门代码处理的段：登记名字但不提供格式串
        spec.segment_names[seg_id] = name
        return None

    fmt = str(entry.get("struct") or "")
    if not fmt:
        spec.errors.append(SpecIssue("error", where, "缺少 struct 格式串"))
        return None
    try:
        size = struct.calcsize(fmt)
    except struct.error as exc:
        spec.errors.append(SpecIssue("error", where, f"struct 格式串非法：{fmt!r} ({exc})"))
        return None

    inputs = [str(n) for n in (entry.get("inputs") or [])]
    # 数一下 struct 实际解出几个值：直接解一次零缓冲最可靠
    # （iter_unpack 返回的是迭代器，没有 len()）。
    try:
        field_count = len(struct.Struct(fmt).unpack(b"\x00" * size))
    except struct.error:
        field_count = len(inputs)
    if inputs and len(inputs) != field_count:
        spec.errors.append(
            SpecIssue(
                "error",
                where,
                f"inputs 有 {len(inputs)} 项但 struct 解出 {field_count} 个值（{fmt} = {size} 字节）",
            )
        )
        return None

    known = set(inputs)
    outputs = entry.get("outputs") or []
    if not isinstance(outputs, list):
        spec.errors.append(SpecIssue("error", where, "outputs 不是数组"))
        return None
    for out in outputs:
        if not isinstance(out, dict) or not out.get("name"):
            spec.errors.append(SpecIssue("error", where, f"输出声明非法：{out!r}"))
            return None
        src = out.get("from")
        if src is not None and inputs and str(src) not in known:
            spec.errors.append(
                SpecIssue("error", where, f"输出 {out['name']} 引用了不存在的输入 {src!r}")
            )
            return None
        if "width" in out:
            width = int(out["width"])
            if width < 1 or width > 32:
                spec.errors.append(
                    SpecIssue("error", where, f"{out['name']} 的 width={width} 越界")
                )
                return None
    spec.segment_formats[seg_id] = fmt
    spec.segment_names[seg_id] = name
    spec.segment_outputs[seg_id] = outputs
    # state_fields：让新段不改 Python 也能落库。
    # 它的键是**解码后的输出字段名**（通用落库时查的是 segment.fields），
    # 不是 struct 的原始 inputs；没声明 outputs 时才回退到按 inputs 校。
    raw_map = entry.get("state_fields") or {}
    if isinstance(raw_map, dict):
        clean = {str(k): str(v) for k, v in raw_map.items() if str(k) and str(v)}
        reference = {str(o.get("name")) for o in outputs if isinstance(o, dict) and o.get("name")}
        if not reference:
            reference = known
        unknown = [k for k in clean if reference and k not in reference]
        if unknown:
            spec.errors.append(
                SpecIssue("error", where,
                          f"state_fields 引用了未声明的输出字段：{unknown}（应在 outputs 里先声明）")
            )
            return None
        if clean:
            spec.segment_state_fields[seg_id] = clean
    return seg_id, name, fmt, list(outputs)


def _validate_uplink(spec: PolarSpec) -> None:
    uplink = spec.raw.get("uplink") or {}
    if not isinstance(uplink, dict):
        spec.errors.append(SpecIssue("error", "uplink", "不是对象"))
        return

    robot_cmd = uplink.get("robot_cmd") or {}
    never = [str(n) for n in (robot_cmd.get("never_set") or [])]
    spec.never_set_uplink_fields = tuple(never)
    if "fire" not in never:
        spec.warnings.append(
            SpecIssue(
                "warning",
                "uplink.robot_cmd.never_set",
                "没有把 fire 列入 never_set；开火权必须始终留在驾驶员手上",
            )
        )

    vrc = uplink.get("virtual_rc") or {}
    bits = vrc.get("key_bits") or {}
    if isinstance(bits, dict):
        spec.key_bits = {str(k).upper(): int(v) for k, v in bits.items()}
    mask = _as_int(vrc.get("drive_key_mask"), -1)
    if mask < 0 or mask > 0xFFFF:
        spec.errors.append(
            SpecIssue("error", "uplink.virtual_rc.drive_key_mask", f"掩码非法：{vrc.get('drive_key_mask')!r}")
        )
        return
    # 只对**真正危险的位**报警。
    #
    # 早期版本刻意把 SHIFT 排除在外（当时它是 Tier 2 opt-in 上限），
    # 理由是“合法规格不应每次都报警”。但 SHIFT 现已废除：固件对它是
    # 边沿翻转（只能开不能关，再开反而关掉），自转改走上行 0x04。
    # 两条通路共存会互相打架，所以现在掩码里出现 SHIFT 就是真实信号，
    # 应该报——这不是误报，而是“有人把已废除的通路又打开了”。
    never_bits = 0
    for name in (
        "SHIFT", "Q", "E", "F", "CTRL", "R", "G", "Z", "X", "C", "V", "B",
    ):
        if name in spec.key_bits:
            never_bits |= 1 << int(spec.key_bits[name])
    if mask & never_bits:
        offending = sorted(
            str(n) for n, b in spec.key_bits.items()
            if (1 << int(b)) & mask & never_bits
        )
        spec.warnings.append(
            SpecIssue(
                "warning",
                "uplink.virtual_rc.drive_key_mask",
                f"掩码 0x{mask:04X} 包含了不得从上位机注入的键位：{offending}；"
                "Q/E 会启停摩擦轮（开火前置执行器），F 会翻转 sc_flag，"
                "SHIFT 是边沿翻转的小陀螺开关（只能开不能关，已改走上行 0x04）",
            )
        )
    spec.drive_key_mask = mask


def _validate_roles(spec: PolarSpec) -> None:
    roles = spec.raw.get("roles")
    if not isinstance(roles, list) or not roles:
        spec.errors.append(SpecIssue("error", "roles", "缺失或为空"))
        return
    for index, entry in enumerate(roles):
        if not isinstance(entry, dict):
            spec.errors.append(SpecIssue("error", f"roles[{index}]", "不是对象"))
            return
        rid = _as_int(entry.get("robot_id"), -1)
        if rid <= 0:
            spec.errors.append(SpecIssue("error", f"roles[{index}]", f"robot_id 非法：{entry.get('robot_id')!r}"))
            return
        try:
            linear = float(entry.get("max_linear_mps", -1))
            yaw = float(entry.get("max_yaw_rps", -1))
        except (TypeError, ValueError):
            spec.errors.append(SpecIssue("error", f"roles[{index}]", "限速不是数值"))
            return
        if linear < 0 or yaw < 0:
            spec.errors.append(SpecIssue("error", f"roles[{index}]", "限速为负"))
            return
        # 第 134 轮：**两个权限位必须显式给**，缺一即整份规格无效。
        #
        # 为什么这条比限速那几条还重要（**实测出来的，不是推的**）：
        # `robot_roles.py` 读它们时用的是 `entry.get("drivable", True)` ——
        # **缺键就当成 True**。而 `polar_protocol.json` 不是冻结文件，
        # `robot_roles.py` 开头明写着「电控改 JSON 就生效」——
        # **所以「少写一个键」是一次正常的、预期内的事件**。
        #
        # 实测：把哨兵（robot_id=7）那一条里的 `drivable` 与
        # `gimbal_controllable` 删掉，本函数**不报任何错**，`spec.ok` 仍是 True，
        # 而 `drive_block_reason(7)` 返回空串 —— 那正是 `manager.py`
        # 放行驾驶指令的条件。**哨兵会被下发底盘指令**，
        # 而 `robot_roles.py` 明写「客户端不得如此」。
        #
        # 删掉 `max_linear_mps` 是会报错的（落到内置表，`drivable=False`）；
        # **唯独这两个权限位没有守卫。**
        # 第 134 轮：**两个权限位必须显式给**，缺一即整份规格无效。
        #
        # 为什么这条比限速那几条还重要（**实测出来的，不是推的**）：
        # `robot_roles.py` 读它们时用的是 `entry.get("drivable", True)` ——
        # **缺键就当成 True**。而 `polar_protocol.json` 不是冻结文件，
        # `robot_roles.py` 开头明写着「电控改 JSON 就生效」——
        # **所以「少写一个键」是一次正常的、预期内的事件**。
        #
        # 实测：把哨兵（robot_id=7）那一条里的 `drivable` 与
        # `gimbal_controllable` 删掉，本函数**不报任何错**，`spec.ok` 仍是 True，
        # 而 `drive_block_reason(7)` 返回空串 —— 那正是 `manager.py`
        # 放行驾驶指令的条件。**哨兵会被下发底盘指令**，
        # 而 `robot_roles.py` 明写「客户端不得如此」。
        #
        # 删掉 `max_linear_mps` 是会报错的（落到内置表，`drivable=False`）；
        # **唯独这两个权限位没有守卫。**
        for _flag in ("drivable", "gimbal_controllable"):
            if not isinstance(entry.get(_flag), bool):
                spec.errors.append(
                    SpecIssue(
                        "error",
                        f"roles[{index}]",
                        "{} 必须是显式的 true/false：缺失会按 True 处理，"
                        "哨兵会被下发驾驶指令".format(_flag),
                    )
                )
                return
        # 限速放大属于安全相关变更：允许，但必须在规格里标 known=true
        if linear > 2.5 or yaw > 4.0:
            spec.warnings.append(
                SpecIssue(
                    "warning",
                    f"roles[{index}]",
                    f"{entry.get('label')} 限速 {linear}/{yaw} 高于常见上场值，请核对固件 CHASSIS_RC_MAX_*",
                )
            )
        spec.roles[rid] = dict(entry)

    fallback = spec.raw.get("fallback_role")
    if not isinstance(fallback, dict):
        spec.errors.append(SpecIssue("error", "fallback_role", "缺失：未知编号无保守兜底"))
        return
    spec.fallback_role = dict(fallback)


def load_spec(path: Path | str | None = None) -> PolarSpec:
    """读取并校验规格文件。任何失败都返回 ok=False 且带原因，不抛异常。"""

    target = Path(path) if path else SPEC_PATH
    spec = PolarSpec(source=str(target))
    if not target.exists():
        spec.errors.append(SpecIssue("error", str(target.name), "文件不存在"))
        return spec
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        spec.errors.append(SpecIssue("error", str(target.name), f"JSON 语法错误：{exc}"))
        return spec
    except OSError as exc:
        spec.errors.append(SpecIssue("error", str(target.name), f"读取失败：{exc}"))
        return spec

    if not isinstance(raw, dict):
        spec.errors.append(SpecIssue("error", str(target.name), "顶层不是对象"))
        return spec
    spec.raw = raw
    spec.spec_version = _as_int(raw.get("spec_version"), 0)

    missing = [k for k in _REQUIRED_TOP_LEVEL if k not in raw]
    if missing:
        spec.errors.append(SpecIssue("error", "顶层", f"缺少必需键：{missing}"))
        return spec

    segments = raw.get("downlink_segments")
    if not isinstance(segments, list) or not segments:
        spec.errors.append(SpecIssue("error", "downlink_segments", "缺失或为空"))
        return spec
    for index, entry in enumerate(segments):
        _validate_segment(entry, index, spec)

    _validate_uplink(spec)
    _validate_roles(spec)

    if spec.errors:
        spec.ok = False
        return spec
    spec.ok = True
    return spec


_cached: PolarSpec | None = None
_cache_key: str = ""


def get_spec(path: Path | str | None = None) -> PolarSpec:
    """取规格（带缓存）。传 path 时绕过缓存，便于测试注入替代规格。"""

    global _cached, _cache_key
    if path is not None:
        return load_spec(path)
    key = str(SPEC_PATH)
    if _cached is None or _cache_key != key:
        _cached = load_spec(SPEC_PATH)
        _cache_key = key
    return _cached
