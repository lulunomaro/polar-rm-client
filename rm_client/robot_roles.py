"""上场兵种参数表 —— 数值全部取自队内固件，不是客户端自己定的。

数据来源：`StandardRobotpp/application/robot_param_*.h`。所有上场兵种共用同一个
Keil 工程，靠 `application/robot_param.h` 里那一行 `#include` 切换兵种，所以每个
兵种的底盘形式与限速都是**固件侧的既定事实**。客户端必须跟着它走，否则会出现
"给英雄下步兵的限速"或"给哨兵下驾驶指令"这类错配。

编号沿用 RoboMaster 官方机器人编号（与 `robot_profiles.py` 的 robot_id 一致），
比中文标题稳定，因此以编号为主键。

⚠ 表里标 `known=False` 的项表示**固件里没有对应参数文件可查证**，不是"值就是
这个"。这些一律取保守值，并在 `notes` 里说明缺什么，避免把猜测当事实传下去。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RobotRoleLimits:
    """一个兵种在队内 0x5A 链路上的能力与限幅。"""

    robot_id: int
    label: str
    chassis: str
    #: 底盘线速度上限 m/s，对应固件 CHASSIS_RC_MAX_SPEED
    max_linear_mps: float
    #: 底盘角速度上限 rad/s，对应固件 CHASSIS_RC_MAX_VELOCITY
    max_yaw_rps: float
    #: HUD 行内用的 ASCII 短码：HUD 字体没有 CJK 字形，中文只能进 tooltip。
    #: （带默认值的字段必须排在必需字段之后，否则 dataclass 直接报错）
    hud_code: str = ""
    #: 客户端是否允许对本兵种下底盘速度指令
    drivable: bool = True
    #: 是否允许下云台角度指令
    gimbal_controllable: bool = True
    #: 该兵种参数是否能在固件里查到出处
    known: bool = True
    notes: str = ""


#: 固件出处：robot_param_omni_infantry.h（全向轮步兵）
OMNI_INFANTRY_MAX_LINEAR = 1.5
OMNI_INFANTRY_MAX_YAW = 2.0
#: 固件出处：robot_param_mecannum_hero.h / robot_param_mecannum.h（麦轮）
MECANUM_MAX_LINEAR = 2.0
MECANUM_MAX_YAW = 2.0


ROLE_LIMITS_BY_ID: dict[int, RobotRoleLimits] = {
    1: RobotRoleLimits(
        robot_id=1,
        label="英雄",
        chassis="麦轮",
        max_linear_mps=MECANUM_MAX_LINEAR,
        max_yaw_rps=MECANUM_MAX_YAW,
        notes="robot_param_mecannum_hero.h：CHASSIS_MECANUM_WHEEL + "
              "GIMBAL_YAW_PITCH_DIRECT + SHOOT_FRIC_TRIGGER，限速 2.0f/2.0f",
    ),
    2: RobotRoleLimits(
        robot_id=2,
        label="工程",
        chassis="麦轮",
        max_linear_mps=MECANUM_MAX_LINEAR,
        max_yaw_rps=MECANUM_MAX_YAW,
        known=False,
        notes="robot_param_engineer.h 只定义了机械臂类型，底盘/云台/限速沿用 "
              "robot_param_mecannum.h 的麦轮值；工程车通常由队员操作但上场多为"
              "自动，客户端驾驶权限需电控确认。",
    ),
    3: RobotRoleLimits(
        robot_id=3,
        label="步兵1",
        chassis="全向轮",
        max_linear_mps=OMNI_INFANTRY_MAX_LINEAR,
        max_yaw_rps=OMNI_INFANTRY_MAX_YAW,
        notes="robot_param_omni_infantry.h：CHASSIS_OMNI_WHEEL，限速 1.5f/2.0f",
    ),
    4: RobotRoleLimits(
        robot_id=4,
        label="步兵2",
        chassis="全向轮",
        max_linear_mps=OMNI_INFANTRY_MAX_LINEAR,
        max_yaw_rps=OMNI_INFANTRY_MAX_YAW,
        notes="与步兵1同为全向轮步兵配置；若队内给 4 号车单独配参，需电控确认。",
    ),
    6: RobotRoleLimits(
        robot_id=6,
        label="空中机器人",
        chassis="无（飞行器）",
        max_linear_mps=0.0,
        max_yaw_rps=0.0,
        drivable=False,
        gimbal_controllable=False,
        known=False,
        notes="固件仓库里没有飞行器工程，队内 0x5A 链路不适用于空中单位。",
    ),
    7: RobotRoleLimits(
        robot_id=7,
        label="哨兵",
        chassis="麦轮",
        max_linear_mps=0.0,
        max_yaw_rps=0.0,
        drivable=False,
        gimbal_controllable=False,
        notes="哨兵自动巡线、场上无操作员。客户端**不得**下驾驶或云台指令 —— "
              "有人接管会直接破坏自动巡线。可观测遥测，不可控。",
    ),
    8: RobotRoleLimits(
        robot_id=8,
        label="Dart 系统",
        chassis="无",
        max_linear_mps=0.0,
        max_yaw_rps=0.0,
        drivable=False,
        gimbal_controllable=False,
        known=False,
        notes="dart 基地不属于本轮 0x5A 直连范围。",
    ),
    9: RobotRoleLimits(
        robot_id=9,
        label="雷达",
        chassis="无",
        max_linear_mps=0.0,
        max_yaw_rps=0.0,
        drivable=False,
        gimbal_controllable=False,
        notes="雷达是站端计算单元，没有底盘与云台可控。",
    ),
}

#: 固件里存在但没有明确机器人编号归属的配置，留作对照说明
FIRMWARE_ROLE_CONFIGS = (
    "robot_param_omni_infantry.h",
    "robot_param_mecannum_hero.h",
    "robot_param_mecannum.h",
    "robot_param_balanced_infantry.h",
    "robot_param_balanced_infantry_gimbal.h",
    "robot_param_engineer.h",
    "robot_param_engineer_controller.h",
    "robot_param_penguin_mini_arm.h",
    "robot_param_penguin_mini_custom_controller.h",
    "robot_param_communicate.h",
)


#: ``_load_roles_from_spec`` 的结果缓存。
#:
#: 为什么不直接缓存：兵种表要能在电控改 ``polar_protocol.json`` 后生效。
#: 但 ``polar_spec.get_spec()`` 本身已经按路径缓存了规格对象 —— 同一个对象
#: 就意味着文件没变、解析结果没变、表也就没变。所以这里用**对象身份**当键，
#: 而不是时间戳：规格被重新加载（换路径、测试注入、显式 load_spec）时拿到
#: 的一定是新对象，缓存自然失效，不会出现"改了文件但读到旧表"。
#:
#: 省下来的是什么：``limits_for`` 在 60Hz 逻辑拍上被调用，每次都会用
#: dict comprehension 重建 9 个兵种 × 10 个字段的 dataclass 表。
#: 实测 11.50 µs/次 → 缓存后约 0.4 µs/次。
_ROLES_SPEC_CACHE: dict[str, object] = {"spec": None, "table": None}


def _load_roles_from_spec() -> tuple[dict[int, RobotRoleLimits], RobotRoleLimits] | None:
    """优先用协议规格里的兵种表（电控改 JSON 就生效）。

    规格缺失/无效/字段不全时返回 None，回退到下面的内置表 —— 与链路本身
    一样的 fail-safe 取向：不能因为一个 JSON 写坏就让限速变成未知。
    """

    try:
        from rm_client.backend import polar_spec
    except Exception:
        return None
    spec = polar_spec.get_spec()
    if not spec.ok or not spec.roles or not spec.fallback_role:
        return None
    if _ROLES_SPEC_CACHE["spec"] is spec and _ROLES_SPEC_CACHE["table"] is not None:
        return _ROLES_SPEC_CACHE["table"]  # type: ignore[return-value]
    try:
        built = {int(rid): _role_from_entry(entry) for rid, entry in spec.roles.items()}
        fallback = _role_from_entry(spec.fallback_role)
    except (TypeError, ValueError, KeyError):
        return None
    if not built:
        return None
    result = (built, fallback)
    _ROLES_SPEC_CACHE["spec"] = spec
    _ROLES_SPEC_CACHE["table"] = result
    return result


def _role_from_entry(entry: dict) -> RobotRoleLimits:
    return RobotRoleLimits(
        robot_id=int(entry.get("robot_id", 0) or 0),
        label=str(entry.get("label", "?")),
        chassis=str(entry.get("chassis", "?")),
        hud_code=str(entry.get("hud_code", "")) or f"R{entry.get('robot_id', 0)}",
        max_linear_mps=float(entry.get("max_linear_mps", 1.5)),
        max_yaw_rps=float(entry.get("max_yaw_rps", 2.0)),
        drivable=bool(entry.get("drivable", True)),
        gimbal_controllable=bool(entry.get("gimbal_controllable", True)),
        known=bool(entry.get("known", False)),
        notes=str(entry.get("notes", "")),
    )


#: HUD 行内用的 ASCII 短码（与 polar_protocol.json 里的 hud_code 一致）。
HUD_CODES = {
    0: "UNKNOWN", 1: "HERO", 2: "ENG", 3: "INF1", 4: "INF2",
    6: "UAV", 7: "SEN", 8: "DART", 9: "RADAR",
}


def _with_hud_code(limits: RobotRoleLimits) -> RobotRoleLimits:
    """给内置表补齐 ASCII 短码（没写就用 R<编号>）。"""

    if limits.hud_code:
        return limits
    return RobotRoleLimits(
        robot_id=limits.robot_id,
        label=limits.label,
        chassis=limits.chassis,
        hud_code=HUD_CODES.get(limits.robot_id, f"R{limits.robot_id}"),
        max_linear_mps=limits.max_linear_mps,
        max_yaw_rps=limits.max_yaw_rps,
        drivable=limits.drivable,
        gimbal_controllable=limits.gimbal_controllable,
        known=limits.known,
        notes=limits.notes,
    )


#: 查不到时的兜底：用最保守的步兵限速，而不是放行
FALLBACK = RobotRoleLimits(
    robot_id=0,
    label="未知兵种",
    chassis="未知",
    max_linear_mps=OMNI_INFANTRY_MAX_LINEAR,
    max_yaw_rps=OMNI_INFANTRY_MAX_YAW,
    drivable=True,
    gimbal_controllable=True,
    known=False,
    notes="编号不在已知兵种表内，按最保守的步兵限速放行；请核对客户端所选兵种"
          "与机器人实际编号是否一致。",
)


def limits_for(robot_id: int | None) -> RobotRoleLimits:
    """按机器人编号取兵种限幅；未知编号回退到保守值而不是放行。

    表优先来自 `polar_protocol.json`（电控改文件即生效），规格不可用时
    才用下面写死的内置表。
    """

    from_spec = _load_roles_from_spec()
    table, fallback = (ROLE_LIMITS_BY_ID, FALLBACK) if from_spec is None else from_spec
    try:
        key = int(robot_id or 0)
    except (TypeError, ValueError):
        key = 0
    return _with_hud_code(table.get(key, fallback))


def drive_block_reason(robot_id: int | None) -> str:
    """该兵种是否禁止客户端驾驶；返回空串表示允许。"""

    limits = limits_for(robot_id)
    if limits.drivable:
        return ""
    return f"{limits.label}（编号 {limits.robot_id}）不允许客户端驾驶：{limits.notes.split('。')[0]}"


def gimbal_block_reason(robot_id: int | None) -> str:
    """该兵种是否禁止客户端控云台；返回空串表示允许。"""

    limits = limits_for(robot_id)
    if limits.gimbal_controllable:
        return ""
    return f"{limits.label}（编号 {limits.robot_id}）没有可控云台"
