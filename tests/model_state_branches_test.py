"""`RobotState` 六个函数的分支契约：第 11 轮可达性审计补的网。

## 这份文件为什么存在

第 10 轮发现 `tick_polar_link` 有 56/97 个变异存活，根因是"测试选的输入全落在
同一侧"。本轮把同一招用在 `rm_client/model.py` 的 `RobotState` 上：
`effective_capacitor_percent` / `buff_indicator_state` /
`robot_module_indicator_state` / `apply_polar_overlay` / `tick` / `push_event`。

做法不是"再多写几条"，而是先把每个决策点的**可达性量出来**，再对量不出来的
那些做变异验证，最后只给真正零覆盖的点补断言：

1. **列清单**：AST 抽出 45 个决策点（if / elif / 三元 / except / `and`-`or` 子句，
   含嵌套），带行号与守卫条件。
2. **量可达性**：用 `sys.monitoring` 的 LINE 事件跑全量 58 个套件，记下每个
   决策点所在行被哪几个套件执行过多少次。不用 `sys.settrace` —— 它会漏事件、
   而且 INSTRUCTION 级跟踪会拖慢被测代码本身，把 `tick()` 里那些**依赖墙钟
   `elapsed`** 的分支"测"出来（见下面 E 组）。
3. **变异验证**：45 个变异逐个跑 32 个真会执行 `model.py` 的套件，逐套件比
   `(退出码, [PASS] 数, [FAIL] 数)` 三元组。存活的才算零覆盖。

## 这一轮真正的系统性根因

`RobotState.tick()` 里的分支**全部是 `int(elapsed) % N` 的函数**，而
`elapsed = time.perf_counter() - self.start_time`。全仓没有一个测试设置过
`start_time`，所以 `elapsed` 在每个测试里都 ≈ 0：

    int(elapsed) % 11  -> 恒 0    gimbal_power 恒 24
    int(elapsed) % 34  -> 恒 0    energy_rune_phase 恒 "SMALL"
    int(elapsed / 4) % 2 -> 恒 0  energy_rune_rotation 恒 "CW"
    int(elapsed) % 19  -> 恒 0    每拍都推 "Radar highlight refreshed"

于是这些三元表达式**只被执行过一次取值**，另一侧从来没被走到。
`start_time` 是普通 dataclass 字段，谁都能改 —— 本文件全部 E 组断言就是
先把 `start_time` 钉住再 tick，把另一侧真的走一遍。

另外两条根因：

* **RMUL3v3 分支只被 `smoke_test` 走到，而本机 `smoke_test` 因缺 openvino
  根本跑不起来**（`ModuleNotFoundError`）。也就是说 3v3 赛制下控制点归属
  （ALLY/ENEMY/NEUTRAL）在本机是彻底没人看的。
* **`buff_indicator_state` 的 `elif`（BUFF_DEF_TYPES）整条语句在本机一次都没
  执行过**：所有喂进去的 buff 行要么 `buff_type=1`（走 if），要么
  `active=False`（在更早一行就被跳过）。于是"防御增益灯"从未被点亮过。

## 诚实声明（三类分开，不把后两类算成覆盖）

* **行为覆盖**：下面每条 `check()` 都是探针在**正确实现**上读出来的数值，
  改坏对应那一行之后会变红。
* **等价变异**：`L618 self.max_buffer_energy or 0`。去掉 `or 0` 之后
  `int(None)` 抛 TypeError，被同一函数的 `except` 接住，**落回同一个
  `capacitor_percent` 兜底** —— 两条路殊途同归，数值完全相同。
  任何行为断言都抓不住它，本文件**不**为它硬凑一条断言。
* **文本钉子**：本文件**没有**任何源码字符串匹配。全部是行为断言。

    python tests/model_state_branches_test.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.model import RobotState  # noqa: E402

FAILED: list[str] = []
PASSED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"[FAIL] {name}  -> {detail}")


def pinned(seconds: float, **kw) -> RobotState:
    """造一个 state，并把 tick() 里的 elapsed 钉死成 `seconds`。

    这是本文件的核心工具：`elapsed = time.perf_counter() - start_time`，
    不钉住它，下面一半的分支根本走不到第二侧。
    """
    state = RobotState(**kw)
    state.start_time = time.perf_counter() - seconds
    return state


# ===========================================================================
# A. effective_capacitor_percent —— 四个 `or 0` 兜底 + except
#
# 探针实测（正确实现）：
#   current=41, max=100, capacitor=62            -> 41
#   current=0,  max=100                          -> 0     (真值，不是 mock)
#   current=None, max=100, capacitor=55          -> 0     (走 `or 0`；去掉则回落 55)
#   max=0, capacitor=77                          -> 77    (max 不可信 -> 回落)
#   max=0, capacitor=None                        -> 0
#   current="abc", max=0, capacitor=55           -> 55    (except 路径)
# ===========================================================================

def test_capacitor_prefers_real_fields_over_mock() -> None:
    s = RobotState()
    s.max_buffer_energy, s.current_buffer_energy, s.capacitor_percent = 100, 20, 99
    check("有可信 max 时按 current/max 算（忽略只在 mock 里写的 capacitor_percent）",
          s.effective_capacitor_percent == 20, str(s.effective_capacitor_percent))

    s.current_buffer_energy = 0
    check("真值读到 0 时显示 0%，不是回落到 mock 的 99%",
          s.effective_capacitor_percent == 0, str(s.effective_capacitor_percent))


def test_capacitor_none_reading_does_not_fall_back_to_mock() -> None:
    """L619 `current_buffer_energy or 0`。

    裁判系统这一帧没带出缓冲能量（解码成 None）时，**必须**按"真值 0%"显示。
    去掉 `or 0` 之后 `int(None)` 抛 TypeError，被 except 接住，于是电容条会
    改显示 mock 字段的 55% —— 现场看到的是"车上一发没打，电容条却说有 55%"。
    """
    s = RobotState()
    s.max_buffer_energy, s.current_buffer_energy = 100, None
    s.capacitor_percent = 55
    check("current=None 时按真值 0% 显示，不回落到 mock 的 55%",
          s.effective_capacitor_percent == 0, str(s.effective_capacitor_percent))


def test_capacitor_zero_max_falls_back_without_dividing_by_zero() -> None:
    """L622 `maximum > 0`（以及 L624 的回落）。"""
    s = RobotState()
    s.current_buffer_energy, s.max_buffer_energy = 40, 0
    s.capacitor_percent = 77
    try:
        got = s.effective_capacitor_percent
    except ZeroDivisionError as exc:                                   # noqa: PERF203
        got = f"ZeroDivisionError: {exc}"
    check("max=0（未收到报文）时不除零，回落到 capacitor_percent",
          got == 77, str(got))

    s.capacitor_percent = 0
    check("max=0 且 capacitor_percent=0 时显示 0%（不是 None/崩）",
          s.effective_capacitor_percent == 0, str(s.effective_capacitor_percent))

    s.capacitor_percent = None
    try:
        got = s.effective_capacitor_percent
        ok = got == 0
    except Exception as exc:                                           # noqa: BLE001
        got, ok = f"{type(exc).__name__}: {exc}", False
    check("capacitor_percent=None 时 `or 0` 兜住，显示 0% 而不是抛异常",
          ok, str(got))


def test_capacitor_garbage_type_takes_the_except_path() -> None:
    """L620 `except (TypeError, ValueError)`。"""
    s = RobotState()
    s.max_buffer_energy, s.current_buffer_energy = 0, "abc"
    s.capacitor_percent = 55
    try:
        got = s.effective_capacitor_percent
        ok = got == 55
    except Exception as exc:                                           # noqa: BLE001
        got, ok = f"{type(exc).__name__}: {exc}", False
    check("真值是垃圾类型时不抛异常，回落到 capacitor_percent", ok, str(got))

    s.current_buffer_energy = None
    s.capacitor_percent = 56
    got = s.effective_capacitor_percent
    check("None 同样走 except 路径（max=0 时），仍落到 capacitor_percent",
          got == 56, str(got))

    # L621 那个 `or 0` 在 **except 处理器里面**：平时测不到，只有"真值是垃圾
    # 类型"且"mock 字段也是 None"时才会被走到。去掉它，int(None) 的 TypeError
    # 会从 except 里直接冒出去 —— 而 except 里再抛是没人接得住的。
    s2 = RobotState()
    s2.max_buffer_energy, s2.current_buffer_energy = 0, "abc"
    s2.capacitor_percent = None
    try:
        got = s2.effective_capacitor_percent
        ok = got == 0
    except Exception as exc:                                           # noqa: BLE001
        got, ok = f"{type(exc).__name__}: {exc}", False
    check("except 路径里 capacitor_percent 也为 None 时 `or 0` 兜住（不再抛出去）",
          ok, str(got))


# ===========================================================================
# B. buff_indicator_state
#
# 探针实测（正确实现，state.robot_id=3）：
#   buff_type=1, active=True, remaining=7, robot_id=3 -> (True, False, 'Atk 7s')
#   buff_type=2 -> (False, True, 'Def 4s')      buff_type=4 -> (False, True, ...)
#   buff_type=5 -> (False, True, ...)            buff_type=3 -> (False, False, '')
#   buff_type=9 -> (False, False, '')
#   robot_id='不是数' -> (False, False, '')      robot_id=None -> (False, False, '')
#   缺 robot_id     -> (False, False, '')        缺 remaining -> (True, False, 'Z 0s')
#   5 条自己的行 -> 详情只留前 4 条
# ===========================================================================

def _own(buff_type, **kw):
    row = {"name": f"B{buff_type}", "buff_type": buff_type, "active": True,
           "remaining": 7, "robot_id": 3}
    row.update(kw)
    return row


def test_defence_buff_types_light_the_defence_lamp() -> None:
    """L656 `elif b in self.BUFF_DEF_TYPES` —— 本机整条语句一次都没执行过。

    防御/回血增益（2 防御、4 底盘功率、5 回血）点亮的是 HUD 上的 DEFBUF 灯。
    这条 elif 从没被走到，意味着把 `BUFF_DEF_TYPES` 改成一个空集合、把
    `2, 4, 5` 写错、或者把 elif 改成别的，**没有任何一个套件会发现** ——
    现场表现是吃到回血/防御增益时灯不亮，选手以为自己没吃到。
    """
    for buff_type, label in ((2, "防御"), (4, "底盘功率"), (5, "回血")):
        s = RobotState()
        s.buff_rows = [_own(buff_type)]
        atk, dfn, detail = s.buff_indicator_state()
        check(f"buff_type={buff_type}（{label}）点亮防御增益灯且不点亮攻击灯",
              dfn is True and atk is False and detail == f"B{buff_type} 7s",
              f"{(atk, dfn, detail)}")


def test_attack_buff_type_lights_only_the_attack_lamp() -> None:
    s = RobotState()
    s.buff_rows = [_own(1)]
    atk, dfn, detail = s.buff_indicator_state()
    check("buff_type=1 只点亮攻击增益灯",
          atk is True and dfn is False and detail == "B1 7s",
          f"{(atk, dfn, detail)}")


def test_buff_type_outside_every_set_lights_nothing() -> None:
    """3（冷却）与 9（越界）都不在 ATK/DEF 任何一个集合里。"""
    for buff_type in (3, 9):
        s = RobotState()
        s.buff_rows = [_own(buff_type)]
        check(f"buff_type={buff_type} 不属于任何集合时两盏灯都不亮",
              s.buff_indicator_state() == (False, False, ""),
              str(s.buff_indicator_state()))


def test_mixed_own_buffs_light_both_and_cap_the_detail() -> None:
    s = RobotState()
    s.buff_rows = [
        {"name": "A", "buff_type": 1, "active": True, "remaining": 9, "robot_id": 3},
        {"name": "B", "buff_type": 2, "active": True, "remaining": 8, "robot_id": 3},
        {"name": "C", "buff_type": 4, "active": True, "remaining": 7, "robot_id": 3},
        {"name": "D", "buff_type": 1, "active": True, "remaining": 6, "robot_id": 3},
        {"name": "E", "buff_type": 2, "active": True, "remaining": 5, "robot_id": 3},
    ]
    atk, dfn, detail = s.buff_indicator_state()
    check("攻击+防御两类同时在场时两盏灯都亮", atk is True and dfn is True,
          f"{(atk, dfn, detail)}")
    check(f"详情文本只保留前 4 条（实测 {detail!r}）",
          detail == "A 9s; B 8s; C 7s; D 6s", detail)


def test_unreadable_robot_id_is_skipped_not_raised() -> None:
    """L643 `row.get('robot_id', 0) or 0` + L644 `except (TypeError, ValueError)`。

    队友那一行的 robot_id 若是脏数据，**必须**安静跳过这一行；
    抛异常会让整个 HUD 指示灯这一帧全灭（而不是只少一条增益）。
    """
    for bad in ("不是数", None, [], {}):
        s = RobotState()
        s.buff_rows = [_own(1, robot_id=bad)]
        try:
            got = s.buff_indicator_state()
            ok = got == (False, False, "")
        except Exception as exc:                                       # noqa: BLE001
            got, ok = f"{type(exc).__name__}: {exc}", False
        check(f"robot_id={bad!r} 这一行被安静跳过，不抛异常", ok, str(got))


def test_row_without_robot_id_is_never_local() -> None:
    """L643 的默认值 0：`robot_id` 缺失时不能被当成本机（state.robot_id=3）。"""
    s = RobotState()
    s.buff_rows = [{"name": "NoId", "buff_type": 1, "active": True, "remaining": 3}]
    check("缺 robot_id 的行不会被当成本机增益", s.buff_indicator_state() == (False, False, ""),
          str(s.buff_indicator_state()))


def test_row_without_remaining_shows_zero_seconds() -> None:
    """L652 `row.get('remaining', 0) or 0`（L652 **没有** try 兜底）。"""
    s = RobotState()
    s.buff_rows = [{"name": "Z", "buff_type": 1, "active": True, "robot_id": 3}]
    check("缺 remaining 时显示 0s 而不是崩掉",
          s.buff_indicator_state() == (True, False, "Z 0s"),
          str(s.buff_indicator_state()))

    s.buff_rows = [{"name": "Z", "buff_type": 1, "active": True, "robot_id": 3,
                    "remaining": None}]
    try:
        got = s.buff_indicator_state()
        ok = got == (True, False, "Z 0s")
    except Exception as exc:                                           # noqa: BLE001
        got, ok = f"{type(exc).__name__}: {exc}", False
    check("remaining=None 时 `or 0` 兜住（这一行没有 try，None 会直接冒出去）",
          ok, str(got))


def test_inactive_and_foreign_rows_are_ignored() -> None:
    s = RobotState()
    s.buff_rows = [_own(1, active=False)]
    check("active=False 的本机行不点灯", s.buff_indicator_state() == (False, False, ""),
          str(s.buff_indicator_state()))
    s.buff_rows = [_own(1, robot_id=103)]
    check("敌方（robot_id=103）的增益不点灯", s.buff_indicator_state() == (False, False, ""),
          str(s.buff_indicator_state()))


def test_untyped_rows_keep_demo_lamp_only_off_the_official_feed() -> None:
    """L637 `getattr(self, 'buff_rows', ()) or ()` + L659 的四个组合。"""
    s = RobotState()
    s.buff_rows = [{"name": "Cooldown", "active": True, "remaining": 26}]
    s.small_buff, s.large_buff = True, False
    check("无 buff_type 的 mock 行：非官方源仍回落到演示灯",
          s.buff_indicator_state() == (True, False, ""), str(s.buff_indicator_state()))

    s.official_state_active = True
    check("同样一行但官方源已接管：绝不借用 mock 灯",
          s.buff_indicator_state() == (False, False, ""), str(s.buff_indicator_state()))

    s.buff_rows = None
    try:
        got = s.buff_indicator_state()
        ok = got == (False, False, "")
    except Exception as exc:                                           # noqa: BLE001
        got, ok = f"{type(exc).__name__}: {exc}", False
    check("buff_rows=None 时 `or ()` 兜住（去掉会在 for 上抛 TypeError）", ok, str(got))


# ===========================================================================
# C. robot_module_indicator_state
# 探针实测：{"big_shooter": 2} -> (('PWR',False),('SHT',True),('CV',False),('ARM',False),('MAIN',False))
#            {"power_manager": None} -> PWR 假，且不抛异常
# ===========================================================================

def test_module_chip_treats_none_as_offline() -> None:
    """L678 `modules.get(field_name, 0) or 0`（在 genexpr 里，本机从未被跟踪到）。

    裁判系统用 0 表示离线；某一版解码器把缺失的模块报成 None 时，
    这一行必须显示"离线"，而不是把整帧指示灯带崩。
    """
    s = RobotState()
    s.robot_module_status = {"power_manager": None}
    try:
        got = s.robot_module_indicator_state()
        ok = got == (("PWR", False), ("SHT", False), ("CV", False),
                     ("ARM", False), ("MAIN", False))
    except Exception as exc:                                           # noqa: BLE001
        got, ok = f"{type(exc).__name__}: {exc}", False
    check("模块值为 None 时按离线处理（不抛异常）", ok, str(got))

    s.robot_module_status = {"big_shooter": 2}
    check("只有大弹模块上线时：发射芯片亮，其余全灭（射孔模块任一上线即亮）",
          s.robot_module_indicator_state()
          == (("PWR", False), ("SHT", True), ("CV", False), ("ARM", False), ("MAIN", False)),
          str(s.robot_module_indicator_state()))

    s.robot_module_status = None
    check("robot_module_status=None 时不产生任何芯片行",
          s.robot_module_indicator_state() == (), str(s.robot_module_indicator_state()))


# ===========================================================================
# D. apply_polar_overlay —— official_state_at 必须钉住**第一次**到达
# 探针实测：连续 apply 两次，official_state_at 保持第一次那个值不变。
# ===========================================================================

def test_first_dropped_overlay_key_is_kept_not_overwritten() -> None:
    """L715 `if self.polar_dropped_overlay_keys == 1`。

    丢键计数器**只增不减**，所以 `== 1` 才是"第一次"。本机 5 个套件一共让
    这行执行了 12 次，却没有任何一条断言检查"记下来的是**第一个**丢掉的键"：
    把 `== 1` 改成恒真，最后一个丢掉的键就会覆盖掉现场真正要找的那一个。
    排查丢段时表现是：日志里写的键名和真正的第一条对不上，顺着查半天查不到。
    """
    s = RobotState()
    s.polar_link_active = True
    s.polar_overlay = {
        "polar_ally_hp": 100,        # 真字段，计入 0 次丢弃
        "aaa_no_such_field": 1,      # 第一个丢掉的键
        "zzz_no_such_field": 2,      # 第二个丢掉的键
        "mmm_no_such_field": 3,      # 第三个
    }
    s.apply_polar_overlay()
    check(f"丢弃计数为 3（实测 {s.polar_dropped_overlay_keys}）",
          s.polar_dropped_overlay_keys == 3, str(s.polar_dropped_overlay_keys))
    check("记下来的是**第一个**丢掉的键，不是最后一个",
          s.polar_first_dropped_overlay_key == "aaa_no_such_field",
          s.polar_first_dropped_overlay_key)
    check("真字段仍然照常写入（没有一起被丢掉）",
          s.polar_ally_hp == 100, str(s.polar_ally_hp))


def test_official_state_at_keeps_the_first_arrival() -> None:
    """L717 `self.official_state_at or time.perf_counter()`。

    这个字段回答的是"队内链路的真值是什么时候到的"。如果每帧都被刷新，
    现场看到的时间永远是"上一帧"，排查链路抖动时会被彻底带偏。
    """
    s = RobotState()
    s.polar_link_active = True
    s.polar_overlay = {"polar_ally_hp": 11}
    s.official_state_at = 0.0
    s.apply_polar_overlay()
    first = s.official_state_at
    check(f"首次到达打上时间戳（实测 {first:.3f}）", first > 0.0, str(first))

    time.sleep(0.02)
    s.apply_polar_overlay()
    check("第二次 apply 不会把首次到达时间刷成当前时间",
          s.official_state_at == first, f"{s.official_state_at} != {first}")


# ===========================================================================
# E. tick() —— 全组都靠钉住 start_time 才走得到第二侧
# 探针实测（start_time = now - seconds）：
#   elapsed=0  -> gimbal_power=24 rune=SMALL rot=CW  shop=READY
#   elapsed=5  -> gimbal_power=24 rune=SMALL rot=CCW shop=READY
#   elapsed=10 -> gimbal_power=0  rune=SMALL rot=CW  shop=READY
#   elapsed=18 -> rune=LARGE
#   RMUL3v3 elapsed=0 -> ALLY / 12 -> ENEMY / 24 -> NEUTRAL / 38 -> ALLY
#   duration=600, elapsed=600 -> stage=600 outpost=0 enemy_outpost=0
#        base_status=EXPOSED enemy_base_status=EXPOSED
#        outpost_status=DOWN enemy_outpost_status=DOWN shield=0/0
#   economy_managed=True, economy=10, price=50 -> shop=WAIT, 金币不被改写
#   max_heat=0 -> tick 不崩，current_heat=0.0
#   current_heat=1.0, max_heat=240 -> start_pct=0.42<=5 -> 起始 48% -> current_heat=115.2
#   elapsed=19 -> 推 "Radar highlight refreshed"；elapsed=20 -> 不推
# ===========================================================================

def test_zero_max_heat_does_not_divide_by_zero() -> None:
    """L735 `self.max_heat > 0`。"""
    s = pinned(0.0)
    s.max_heat = 0.0
    try:
        s.tick(1 / 60.0)
        ok, got = True, s.current_heat
    except Exception as exc:                                           # noqa: BLE001
        ok, got = False, f"{type(exc).__name__}: {exc}"
    check("max_heat=0 时 tick() 不除零、不抛异常", ok, str(got))
    check("max_heat=0 时 current_heat 为 0", ok and got == 0.0, str(got))


def test_cold_robot_starts_the_mock_heat_ring_at_48_percent() -> None:
    """L740 `start_pct if start_pct > 5.0 else 48.0`。

    起始热量低于 5% 时模拟器被重置到 48%（而不是被 36% 的下限夹住），
    这样外圈蓝环在开机那几秒不会是一条几乎空的弧。
    探针实测：current_heat=1.0 / max_heat=240 -> start_pct=0.42 -> 48% -> 115.2
    对照：current_heat=42.0 -> start_pct=17.5 -> 被 36% 夹住 -> 86.4
    """
    cold = pinned(0.0)
    cold.current_heat, cold.max_heat = 1.0, 240.0
    cold.tick(1 / 60.0)
    check(f"起始热量 0.42% 走 48% 那一侧（实测 current_heat={cold.current_heat}）",
          abs(cold.current_heat - 115.2) < 1e-6, str(cold.current_heat))

    warm = pinned(0.0)
    warm.current_heat, warm.max_heat = 42.0, 240.0
    warm.tick(1 / 60.0)
    check(f"起始热量 17.5% 被 36% 下限夹住（实测 current_heat={warm.current_heat}）",
          abs(warm.current_heat - 86.4) < 1e-6, str(warm.current_heat))


def test_gimbal_power_follows_the_elapsed_phase() -> None:
    """L753 `int(elapsed) % 11 < 9`。"""
    s = pinned(0.0)
    s.tick(1 / 60.0)
    check("elapsed=0（0%11=0<9）时云台功率 24W", s.gimbal_power == 24, str(s.gimbal_power))
    s = pinned(10.0)
    s.tick(1 / 60.0)
    check("elapsed=10（10%11=10>=9）时云台断电 0W", s.gimbal_power == 0, str(s.gimbal_power))
    s = pinned(12.0)
    s.tick(1 / 60.0)
    check("elapsed=12（12%11=1<9）时恢复到 24W", s.gimbal_power == 24, str(s.gimbal_power))


def test_energy_rune_phase_and_rotation_follow_elapsed() -> None:
    """L770 `int(elapsed) % 34 > 16` 与 L772 `int(elapsed/4) % 2`。"""
    s = pinned(0.0)
    s.tick(1 / 60.0)
    check("elapsed=0：符文 SMALL + CW",
          (s.energy_rune_phase, s.energy_rune_rotation) == ("SMALL", "CW"),
          f"{(s.energy_rune_phase, s.energy_rune_rotation)}")
    s = pinned(18.0)
    s.tick(1 / 60.0)
    check("elapsed=18（18%34=18>16）：符文切 LARGE",
          s.energy_rune_phase == "LARGE", s.energy_rune_phase)
    s = pinned(5.0)
    s.tick(1 / 60.0)
    check("elapsed=5（int(5/4)%2=1）：符文 CCW",
          s.energy_rune_rotation == "CCW", s.energy_rune_rotation)
    s = pinned(16.0)
    s.tick(1 / 60.0)
    check("elapsed=16（int(16/4)%2=0）：符文回 CW",
          s.energy_rune_rotation == "CW", s.energy_rune_rotation)


def test_projectile_shop_waits_when_gold_is_below_price() -> None:
    """L784 `self.remaining_economy >= self.projectile_shop_price`。"""
    s = pinned(0.0)
    s.tick(1 / 60.0)
    check(f"默认 mock 经济（{s.remaining_economy} >= {s.projectile_shop_price}）时商店 READY",
          s.projectile_shop_state == "READY", s.projectile_shop_state)

    poor = pinned(0.0)
    poor.economy_managed = True           # mock 不得改写真实金币
    poor.remaining_economy = 10
    poor.tick(1 / 60.0)
    check("金币 10 < 价 50 时商店 WAIT（不买就是等）",
          poor.projectile_shop_state == "WAIT", poor.projectile_shop_state)
    check("economy_managed=True 时 mock 不得改写真实金币",
          poor.remaining_economy == 10, str(poor.remaining_economy))


def test_economy_latch_protects_ammo_and_gold() -> None:
    """L775 / L779 `if not self.economy_managed`。"""
    s = pinned(0.0)
    s.economy_managed = True
    s.remaining_economy, s.remaining_ammo = 777, 42
    s.total_economy_obtained = 999
    s.tick(1 / 60.0)
    check("economy_managed=True 时剩余金币不被 mock 覆盖",
          s.remaining_economy == 777, str(s.remaining_economy))
    check("economy_managed=True 时总收入不被 mock 覆盖",
          s.total_economy_obtained == 999, str(s.total_economy_obtained))
    check("economy_managed=True 时弹匣余量不被 mock 覆盖",
          s.remaining_ammo == 42, str(s.remaining_ammo))

    free = pinned(0.0)
    free.economy_managed = False
    free.remaining_economy = 777
    free.tick(1 / 60.0)
    check("economy_managed=False 时 mock 照常接管金币",
          free.remaining_economy != 777, str(free.remaining_economy))


def test_rmul3v3_scene_is_reachable_and_switches_control_owner() -> None:
    """L785 `match_mode == 'RMUL3v3'` 与 L794 那两个嵌套三元。

    本机唯一会走进 3v3 分支的套件是 `smoke_test`，而它因缺 openvino 跑不起来
    —— 所以"控制点归属"这三个取值（ALLY/ENEMY/NEUTRAL）在本机是无人看守的。
    现场表现：3v3 地图上的控制点高亮永远是 ALLY，敌方什么时候抢到点、
    什么时候回到中立，选手在界面上完全看不出来。
    """
    owners = {}
    for sec in (0, 12, 24, 38):
        s = pinned(float(sec), match_mode="RMUL3v3")
        s.tick(1 / 60.0)
        owners[sec] = s.control_owner
    check(f"3v3 控制点归属按 elapsed 轮转（实测 {owners}）",
          owners == {0: "ALLY", 12: "ENEMY", 24: "NEUTRAL", 38: "ALLY"}, str(owners))

    s = pinned(0.0, match_mode="RMUL3v3")
    s.tick(1 / 60.0)
    check("3v3 分支把赛制标题/时长换成 3v3 的",
          (s.match_title, s.match_duration_sec) == ("RMUL 3v3", 300),
          f"{(s.match_title, s.match_duration_sec)}")
    check("3v3 没有基地/前哨战（血量归零）",
          (s.base_health, s.enemy_base_health, s.outpost_health,
           s.enemy_outpost_health) == (0, 0, 0, 0),
          str((s.base_health, s.enemy_base_health, s.outpost_health,
               s.enemy_outpost_health)))
    check("3v3 小地图固定 5 个机器人（7v7 是 7 个）",
          len(s.map_robots) == 5, str(len(s.map_robots)))

    s7 = pinned(0.0, match_mode="RMUC7v7")
    s7.tick(1 / 60.0)
    check("7v7 分支仍然是 7 个机器人 + 2 个标记（反向对照）",
          len(s7.map_robots) == 7 and len(s7.map_markers) == 2,
          f"{len(s7.map_robots)}/{len(s7.map_markers)}")


def test_late_match_exposes_bases_and_downs_outposts() -> None:
    """L818--L823：基地护盾/前哨战上下线那六个三元。

    `stage_elapsed_sec` 最大等于 `match_duration_sec`，而前哨战掉到 0 需要
    `stage_elapsed_sec >= 549`，所以默认 420 秒的赛制里这六个分支**一次都
    走不到**。现场表现：只要打到后期，基地仍然显示"有护盾"、己方前哨战
    仍然显示"在线"，选手会按着已经没了的掩体和已经倒掉的前哨战打。
    """
    s = pinned(600.0)
    s.match_duration_sec = 600
    s.tick(1 / 60.0)
    check(f"elapsed=600/duration=600 时 stage_elapsed_sec={s.stage_elapsed_sec}",
          s.stage_elapsed_sec == 600, str(s.stage_elapsed_sec))
    check("双方前哨战归零", (s.outpost_health, s.enemy_outpost_health) == (0, 0),
          f"{s.outpost_health}/{s.enemy_outpost_health}")
    check("双方基地转为 EXPOSED",
          (s.base_status, s.enemy_base_status) == ("EXPOSED", "EXPOSED"),
          f"{s.base_status}/{s.enemy_base_status}")
    check("双方前哨战转为 DOWN",
          (s.outpost_status, s.enemy_outpost_status) == ("DOWN", "DOWN"),
          f"{s.outpost_status}/{s.enemy_outpost_status}")
    check("护盾随之清零",
          (s.base_shield, s.enemy_base_shield) == (0, 0),
          f"{s.base_shield}/{s.enemy_base_shield}")

    early = pinned(0.0)
    early.tick(1 / 60.0)
    check("开局一侧：SHIELDED / ONLINE / 护盾 100（反向对照）",
          (early.base_status, early.outpost_status, early.base_shield)
          == ("SHIELDED", "ONLINE", 100),
          f"{(early.base_status, early.outpost_status, early.base_shield)}")
    # 敌方那一侧同样要有反向对照：只断言"晚期变 EXPOSED"的话，
    # 把 `enemy_base_status == "SHIELDED"` 改成永假也照样成立（晚期本来就是 0）。
    check("开局敌方一侧：SHIELDED / ONLINE / 护盾 100（反向对照）",
          (early.enemy_base_status, early.enemy_outpost_status, early.enemy_base_shield)
          == ("SHIELDED", "ONLINE", 100),
          f"{(early.enemy_base_status, early.enemy_outpost_status, early.enemy_base_shield)}")
    check("开局时双方前哨战都还有血（反向对照）",
          (early.outpost_health, early.enemy_outpost_health) == (1500, 1500),
          f"{early.outpost_health}/{early.enemy_outpost_health}")


def test_radar_refresh_event_only_on_its_own_phase() -> None:
    """L848 `int(elapsed) % 19 == 0`。"""
    hit = pinned(19.0)
    hit.events.clear()
    hit.tick(1 / 60.0)
    check("elapsed=19（19%19=0）时推一条雷达刷新事件",
          list(hit.events) == ["Radar highlight refreshed"], str(list(hit.events)))
    miss = pinned(20.0)
    miss.events.clear()
    before = miss.last_event
    miss.tick(1 / 60.0)
    check("elapsed=20（20%19=1）时不推任何事件",
          list(miss.events) == [] and miss.last_event == before,
          f"{list(miss.events)} / {miss.last_event!r}")


def test_push_event_dedupes_consecutive_identical_events() -> None:
    """L852 `self.events and self.events[-1] == event`。"""
    s = RobotState()
    s.events.clear()
    s.push_event("X")
    s.push_event("X")
    check("同一条事件连推两次只留一条", list(s.events) == ["X"], str(list(s.events)))
    s.push_event("Y")
    check("换成不同事件就照常追加", list(s.events) == ["X", "Y"], str(list(s.events)))
    s.push_event("Y")
    check("尾部重复同样被吞掉", list(s.events) == ["X", "Y"], str(list(s.events)))
    check("last_event 停在最后一条", s.last_event == "Y", s.last_event)


def test_official_feed_is_never_clobbered_by_the_desktop_mock() -> None:
    """L722 `not self.mock_simulation_enabled or self.official_state_active`。"""
    s = pinned(0.0)
    s.official_state_active = True
    s.gimbal_power = 7
    s.buff_rows = [{"name": "official", "active": True, "remaining": 3}]
    s.map_robots = [{"id": 3, "official": True}]
    s.tick(1 / 60.0)
    check("官方源已接管时 tick() 整拍早退：云台功率不被 mock 改写",
          s.gimbal_power == 7, str(s.gimbal_power))
    check("官方源已接管时官方 buff 行不被 mock 覆盖", s.buff_rows == [
        {"name": "official", "active": True, "remaining": 3}], str(s.buff_rows))
    check("官方源已接管时官方地图行不被 mock 覆盖",
          s.map_robots == [{"id": 3, "official": True}], str(s.map_robots))

    m = pinned(0.0)
    m.mock_simulation_enabled = False
    m.gimbal_power = 7
    m.tick(1 / 60.0)
    check("关掉桌面模拟（mock_simulation_enabled=False）时同样整拍早退",
          m.gimbal_power == 7, str(m.gimbal_power))

    live = pinned(0.0)
    live.gimbal_power = 7
    live.tick(1 / 60.0)
    check("反向对照：默认桌面模拟照常驱动（云台功率变成 24W）",
          live.gimbal_power == 24, str(live.gimbal_power))


def test_heat_simulator_is_built_once_and_reused() -> None:
    """L730 `getattr(self, '_mock_heat_sim', None) is None`。"""
    s = pinned(0.0)
    s.tick(1 / 60.0)
    first = s._mock_heat_sim
    s.tick(1 / 60.0)
    check("热度模拟器只在第一拍构造，之后复用同一个（不每拍重置）",
          s._mock_heat_sim is first, "模拟器被重建了")


def main() -> int:
    tests = [
        test_capacitor_prefers_real_fields_over_mock,
        test_capacitor_none_reading_does_not_fall_back_to_mock,
        test_capacitor_zero_max_falls_back_without_dividing_by_zero,
        test_capacitor_garbage_type_takes_the_except_path,
        test_defence_buff_types_light_the_defence_lamp,
        test_attack_buff_type_lights_only_the_attack_lamp,
        test_buff_type_outside_every_set_lights_nothing,
        test_mixed_own_buffs_light_both_and_cap_the_detail,
        test_unreadable_robot_id_is_skipped_not_raised,
        test_row_without_robot_id_is_never_local,
        test_row_without_remaining_shows_zero_seconds,
        test_inactive_and_foreign_rows_are_ignored,
        test_untyped_rows_keep_demo_lamp_only_off_the_official_feed,
        test_module_chip_treats_none_as_offline,
        test_first_dropped_overlay_key_is_kept_not_overwritten,
        test_official_state_at_keeps_the_first_arrival,
        test_zero_max_heat_does_not_divide_by_zero,
        test_cold_robot_starts_the_mock_heat_ring_at_48_percent,
        test_gimbal_power_follows_the_elapsed_phase,
        test_energy_rune_phase_and_rotation_follow_elapsed,
        test_projectile_shop_waits_when_gold_is_below_price,
        test_economy_latch_protects_ammo_and_gold,
        test_rmul3v3_scene_is_reachable_and_switches_control_owner,
        test_late_match_exposes_bases_and_downs_outposts,
        test_radar_refresh_event_only_on_its_own_phase,
        test_push_event_dedupes_consecutive_identical_events,
        test_official_feed_is_never_clobbered_by_the_desktop_mock,
        test_heat_simulator_is_built_once_and_reused,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 70)
    if FAILED:
        print(f"RobotState 分支契约：{len(FAILED)} 项未通过 / 共 {PASSED + len(FAILED)} 项")
        for line in FAILED:
            print(f"  - {line}")
        return 1
    print(f"RobotState 分支契约：全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
