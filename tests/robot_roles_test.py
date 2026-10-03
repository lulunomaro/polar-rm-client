"""兵种对齐验证：客户端的限幅与驾驶权限必须跟固件参数一致。

数值出处全部来自 `StandardRobotpp/application/robot_param_*.h`：
  - 英雄  robot_param_mecannum_hero.h   麦轮  2.0 m/s / 2.0 rad/s
  - 步兵  robot_param_omni_infantry.h   全向轮 1.5 m/s / 2.0 rad/s
客户端此前把 1.5 写死，对英雄是错的；哨兵则根本不该被客户端驾驶。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client import robot_roles as roles  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILED.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


def test_table_matches_firmware() -> None:
    hero = roles.limits_for(1)
    check("英雄=麦轮 2.0 m/s（robot_param_mecannum_hero.h）",
          hero.chassis == "麦轮" and abs(hero.max_linear_mps - 2.0) < 1e-9,
          f"{hero.chassis} {hero.max_linear_mps}")
    check("英雄角速度 2.0 rad/s", abs(hero.max_yaw_rps - 2.0) < 1e-9, str(hero.max_yaw_rps))

    inf = roles.limits_for(3)
    check("步兵=全向轮 1.5 m/s（robot_param_omni_infantry.h）",
          inf.chassis == "全向轮" and abs(inf.max_linear_mps - 1.5) < 1e-9,
          f"{inf.chassis} {inf.max_linear_mps}")

    check("英雄限速高于步兵（不是同一个写死值）",
          hero.max_linear_mps > inf.max_linear_mps,
          f"{hero.max_linear_mps} vs {inf.max_linear_mps}")


def test_non_drivable_roles_are_blocked() -> None:
    check("哨兵禁止客户端驾驶", roles.drive_block_reason(7) != "",
          roles.drive_block_reason(7))
    check("空中机器人禁止客户端驾驶", roles.drive_block_reason(6) != "")
    check("雷达禁止客户端驾驶", roles.drive_block_reason(9) != "")
    check("哨兵禁止客户端控云台", roles.gimbal_block_reason(7) != "")
    check("英雄/步兵允许驾驶",
          roles.drive_block_reason(1) == "" and roles.drive_block_reason(3) == "")


def test_unknown_id_fails_conservatively() -> None:
    unknown = roles.limits_for(99)
    check("未知编号不放大限速（回退到步兵 1.5）",
          unknown.max_linear_mps <= 1.5 + 1e-9, str(unknown.max_linear_mps))
    check("未知编号标记为不可查证", unknown.known is False)
    check("None/0 也走保守回退",
          roles.limits_for(None).max_linear_mps <= 1.5 + 1e-9
          and roles.limits_for(0).max_linear_mps <= 1.5 + 1e-9)


def test_limits_reach_the_wire() -> None:
    """端到端：换兵种后，上线的底盘速度必须跟着变。"""

    from rm_client.backend.polar_bridge import scale_drive

    hero_vx, _, _ = scale_drive(1.0, 0.0, 0.0,
                                max_linear_mps=roles.limits_for(1).max_linear_mps,
                                max_yaw_rps=roles.limits_for(1).max_yaw_rps)
    inf_vx, _, _ = scale_drive(1.0, 0.0, 0.0,
                               max_linear_mps=roles.limits_for(3).max_linear_mps,
                               max_yaw_rps=roles.limits_for(3).max_yaw_rps)
    check("满推杆时英雄上线 2.0 m/s", abs(hero_vx - 2.0) < 1e-9, str(hero_vx))
    check("满推杆时步兵上线 1.5 m/s", abs(inf_vx - 1.5) < 1e-9, str(inf_vx))


def test_manager_uses_role_limits() -> None:
    """真实 manager 链路：选英雄应发 2.0，选哨兵应拒绝驾驶。"""

    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState, build_default_registry

    class FakeCommand:
        def __init__(self, vx, vy, wz):
            self.vx, self.vy, self.wz = vx, vy, wz

    workspace = str(ROOT / "user_data" / "_role_test")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = manager.state = RobotState()
    manager._apply_config(manager.config)

    link = registry.get("RobotLink")
    link.enabled = True
    link.get_setting("Allow Drive").value = True
    link.get_setting("Mock Writes").value = False
    link.get_setting("Client Chassis Drive").value = True
    link.get_setting("Vision NUC Occupied").value = False
    link.get_setting("Client Gimbal Control").value = False

    # 把机器人编号设成英雄（1 号），验证限幅跟的是实际那台车
    from rm_client.backend.polar_link import SEG_ROBOT_STATUS

    state.robot_id = 1
    manager.polar_bridge.last_segment_at = __import__("time").perf_counter()
    # 段号必须是 int：产品代码按 `0x%02X` 格式化这些键
    manager.polar_bridge.segment_counts[SEG_ROBOT_STATUS] = 1

    class Ctrl:
        last_command = FakeCommand(1.0, 0.0, 0.0)

    # 桥接层活跃判定依赖 last_segment_at，这里直接确认兵种限幅已生效
    stats = manager.tick_polar_link(state, registry, Ctrl())
    check("统计里带出兵种标签", stats.get("role_label") == "英雄", str(stats.get("role_label")))
    check("统计里英雄限速为 2.0",
          abs(float(stats.get("role_max_linear_mps", 0)) - 2.0) < 1e-9,
          str(stats.get("role_max_linear_mps")))

    # 切到哨兵：即使其它闸门全开，也必须拒绝驾驶
    state.robot_id = 7
    stats2 = manager.tick_polar_link(state, registry, Ctrl())
    check("哨兵下 drive_armed 为假", stats2["drive_armed"] is False, str(stats2["drive_armed"]))
    check("哨兵拒绝原因可读", "哨兵" in str(stats2.get("role_drive_block", "")),
          str(stats2.get("role_drive_block")))

    try:
        manager.shutdown()
    except Exception:
        pass


def test_role_mismatch_is_reported() -> None:
    """界面选的车与实际连的车不是同一台时，必须告警而不是默默错配。

    选错车是真实事故源：限速不同（英雄 2.0 / 步兵 1.5），而且给哨兵
    下驾驶指令会破坏它的自动巡线。
    """

    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState, build_default_registry

    class Cmd:
        vx, vy, wz = 1.0, 0.0, 0.0

    class Ctrl:
        last_command = Cmd()

    workspace = str(ROOT / "user_data" / "_mismatch_test")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    manager = BackendManager(workspace=workspace, scenario_key="OfficialEvent", client_id="3")
    registry = build_default_registry()
    state = manager.state = RobotState()
    manager._apply_config(manager.config)

    link = registry.get("RobotLink")
    link.enabled = True
    link.get_setting("Allow Drive").value = True
    link.get_setting("Mock Writes").value = False
    link.get_setting("Client Chassis Drive").value = True
    link.get_setting("Vision NUC Occupied").value = False
    link.get_setting("Client Gimbal Control").value = False

    from rm_client.backend.polar_link import SEG_ROBOT_STATUS

    manager.polar_bridge.last_segment_at = __import__("time").perf_counter()
    manager.polar_bridge.segment_counts[SEG_ROBOT_STATUS] = 1

    # 操作员选步兵（3），机器人自报英雄（1）
    state.robot_id = 3
    state.polar_reported_robot_id = 1
    stats = manager.tick_polar_link(state, registry, Ctrl())
    check("不一致会被报出来", bool(stats.get("role_mismatch")), str(stats.get("role_mismatch")))
    check("限幅按实际车辆（英雄 2.0）而不是界面选择（步兵 1.5）执行",
          abs(float(stats.get("role_max_linear_mps", 0)) - 2.0) < 1e-9,
          str(stats.get("role_max_linear_mps")))
    check("上报值不再覆盖操作员的选择",
          int(state.robot_id) == 3, str(state.robot_id))

    # 两边一致时不应误报
    state.polar_reported_robot_id = 3
    stats2 = manager.tick_polar_link(state, registry, Ctrl())
    check("兵种一致时不误报", not stats2.get("role_mismatch"), str(stats2.get("role_mismatch")))

    # 机器人还没上报时也不应误报（不能拿 0 当“另一台车”）
    state.polar_reported_robot_id = 0
    stats3 = manager.tick_polar_link(state, registry, Ctrl())
    check("未上报时不报不一致", not stats3.get("role_mismatch"), str(stats3.get("role_mismatch")))

    # ---- 第 114 轮：上面那条只查了「不误报」，**没查限幅跟谁走** ----------
    #
    # 上面 `selected_id = 3`（步兵，1.5 m/s），而 `limits_for(0)` **也是 1.5** ——
    # 也就是说对步兵来说，删掉 `role_id = reported_id or selected_id`
    # 那个 `or` 之后**结果一模一样**，所以任何以步兵为题的用例都抓不住它。
    # 这就是「or_drop 变异杀伤率 0%」的直接成因：**唯一的用例恰好落在
    # 两条分支碰巧相等的那一侧。**
    #
    # 换英雄（`limits_for(1) = 2.0`）就分开了。
    state.robot_id = 1
    state.polar_reported_robot_id = 0
    stats4 = manager.tick_polar_link(state, registry, Ctrl())
    check(
        "未上报时限幅跟**操作员选的英雄**走（2.0），而不是落回 role 0（1.5）",
        abs(float(stats4.get("role_max_linear_mps", 0)) - 2.0) < 1e-9,
        "拿到 {} —— `or` 那一半被删了？limits_for(0)={}，limits_for(1)={}".format(
            stats4.get("role_max_linear_mps"),
            roles.limits_for(0),
            roles.limits_for(1),
        ),
    )
    check("未上报时仍不误报（英雄这一侧同样）",
          not stats4.get("role_mismatch"), str(stats4.get("role_mismatch")))

    try:
        manager.shutdown()
    except Exception:
        pass


def test_hud_codes_do_not_drift_between_spec_and_code() -> None:
    """ASCII 短码同时存在于 JSON 与 Python 内置表，不一致就会显示错标签。

    规格可用时取 JSON，不可用时取内置表 —— 两条路径必须给出同一个短码，
    否则“回退”本身会改变界面显示，操作员看到的兵种标签就不确定了。
    """

    from rm_client.backend import polar_spec

    spec = polar_spec.get_spec()
    check("规格可用", spec.ok, spec.error_text())

    bad = []
    for rid, entry in spec.roles.items():
        spec_code = str(entry.get("hud_code", ""))
        builtin = roles.ROLE_LIMITS_BY_ID.get(int(rid))
        builtin_code = roles._with_hud_code(builtin).hud_code if builtin else ""
        if not spec_code or spec_code != builtin_code:
            bad.append(f"{rid}: spec={spec_code!r} builtin={builtin_code!r}")
    check("JSON 与内置表的 hud_code 逐项一致", not bad, "; ".join(bad[:4]))

    # 每个短码必须是纯 ASCII 大写（HUD 字体无 CJK 字形）
    non_ascii = [f"{rid}:{entry.get('hud_code')}" for rid, entry in spec.roles.items()
                 if not str(entry.get("hud_code", "")).isascii() or not str(entry.get("hud_code", "")).isupper()]
    check("短码全为 ASCII 大写（HUD 能渲染）", not non_ascii, str(non_ascii))

    # 内置表自己也要能独立给出短码（不依赖规格）
    check("不读规格也能得到英雄短码", roles.HUD_CODES.get(1) == "HERO", str(roles.HUD_CODES.get(1)))


def test_limits_for_always_returns_a_usable_object() -> None:
    """渲染器无条件读这些字段，所以任何输入都不能返回 None 或抛异常。"""

    ok = True
    for value in (None, 0, -1, 999, "abc", 1.5, True):
        try:
            limits = roles.limits_for(value)
            if not limits.hud_code or limits.max_linear_mps < 0:
                ok = False
        except Exception as exc:
            ok = False
            print(f"  输入 {value!r} 抛异常：{exc}")
    check("任意输入都返回可用限幅（含垃圾输入）", ok)


def test_spin_key_tier_is_opt_in() -> None:
    """SHIFT 已废除：小陀螺改走上行 0x04 的显式“设定”指令。

    函数名保留（不弄坏 main() 的调用列表），但语义已反转。
    原来这里验的是“SHIFT 可以 opt-in 放行”，那是错的：
    固件对 SHIFT 是边沿翻转（chassis_mecanum.c:177 置1 / :223 置0 /
    chassis_omni.c:96 取反，松手 :187 / :107 不改变 spin_flag），
    客户端因此只能开、关不掉，再开一次反而关掉。
    现在验的是：无论怎么开，SHIFT 都不得上线。
    """

    from rm_client.backend import polar_link as pl

    check("掩码只含 W/S/A/D", pl.drive_key_mask(False) == 0x000F,
          hex(pl.drive_key_mask(False)))
    # allow_spin 参数已失效：保留只为不弄坏调用方，但不得再放行 SHIFT
    check("allow_spin=True 也不放行 SHIFT（参数已无效）",
          pl.drive_key_mask(True) == 0x000F, hex(pl.drive_key_mask(True)))
    check("上限不含 Q/E/F 也不含 SHIFT",
          pl.RC_MAX_ALLOWED_KEY_MASK == 0x000F
          and not (pl.RC_MAX_ALLOWED_KEY_MASK & (1 << pl.RC_KEY_BITS["Q"]))
          and not (pl.RC_MAX_ALLOWED_KEY_MASK & (1 << pl.RC_KEY_BITS["E"]))
          and not (pl.RC_MAX_ALLOWED_KEY_MASK & (1 << pl.RC_KEY_BITS["F"]))
          and not (pl.RC_MAX_ALLOWED_KEY_MASK & pl.RC_SPIN_KEY_MASK),
          hex(pl.RC_MAX_ALLOWED_KEY_MASK))

    codec = pl.PolarFrameCodec()
    full = 0xFFFF

    # 默认：只留驾驶键
    frame = codec.encode_virtual_rc(raw_timestamp=1, key_value=full)
    on_wire = __import__("struct").unpack_from("<H", frame, 28)[0]
    check("默认上线只有 W/S/A/D", on_wire == 0x000F, hex(on_wire))

    # 显式传宽掩码：编码器必须兜住，SHIFT 依旧不得上线
    frame2 = codec.encode_virtual_rc(raw_timestamp=2, key_value=full,
                                     key_mask=pl.drive_key_mask(True))
    on_wire2 = __import__("struct").unpack_from("<H", frame2, 28)[0]
    check("即使显式要 SHIFT，上线也只有 W/S/A/D", on_wire2 == 0x000F, hex(on_wire2))

    # 即使调用方传一个荒谬的宽掩码，编码器也要兜住不超出上限
    frame3 = codec.encode_virtual_rc(raw_timestamp=3, key_value=full, key_mask=0xFFFF)
    on_wire3 = __import__("struct").unpack_from("<H", frame3, 28)[0]
    check("传宽掩码时编码器兜底（不超出 0x000F）", on_wire3 == 0x000F, hex(on_wire3))

    # 自转的真正通路：上行 0x04，语义是“设定”而不是“翻转”
    frame_on = codec.encode_chassis_cmd(raw_timestamp=4, spin_cmd=pl.SPIN_CMD_ON)
    frame_off = codec.encode_chassis_cmd(raw_timestamp=5, spin_cmd=pl.SPIN_CMD_OFF)
    check("0x04 帧长 14（帧头4 + 时间戳4 + 数据4 + CRC16 2）",
          len(frame_on) == 14 and len(frame_off) == 14, f"{len(frame_on)}/{len(frame_off)}")
    check("0x04 段 id 正确", frame_on[0] == 0x5A and frame_on[2] == pl.CMD_CHASSIS_CMD)
    check("ON 与 OFF 载荷不同（真的是设定而不是翻转）",
          frame_on[8] == pl.SPIN_CMD_ON and frame_off[8] == pl.SPIN_CMD_OFF,
          f"{frame_on[8]}/{frame_off[8]}")
    bogus = codec.encode_chassis_cmd(raw_timestamp=6, spin_cmd=0x7F)
    check("0x04 未知取值归为 NONE", bogus[8] == pl.SPIN_CMD_NONE, str(bogus[8]))
    check("0x04 不带任何键位字段（与 0x03 彻底分开）",
          len(frame_on) != 32 and pl.CHASSIS_CMD_BODY_SIZE == 4)


def main() -> int:
    test_table_matches_firmware()
    test_non_drivable_roles_are_blocked()
    test_unknown_id_fails_conservatively()
    test_limits_reach_the_wire()
    test_manager_uses_role_limits()
    test_role_mismatch_is_reported()
    test_hud_codes_do_not_drift_between_spec_and_code()
    test_limits_for_always_returns_a_usable_object()
    test_spin_key_tier_is_opt_in()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"FAILED {len(FAILED)}: {', '.join(FAILED)}")
        return 1
    print("ALL ROLE ALIGNMENT TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
