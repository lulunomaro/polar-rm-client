"""``auto_spin`` / ``calibration`` / ``match_modes`` 的分支契约（第 11 轮零覆盖补网）。

## 为什么有这份文件

第 10 轮对 60 Hz 主泵 ``tick_polar_link`` 做变异验证，97 个变异里 56 个改坏了
**全量 58 个套件一条都不红**。根因不是"测试写得不够多"，而是**测试选的输入全落在
同一侧**：全仓没有任何一个测试打开过 ``Radio KM Egress`` / ``Direct DT7 Egress``，
默认值是 False，于是"没开"那条路径成了唯一被走到的。

本轮把同一个问题系统化到这三个模块。做法与第 10 轮一致：

1. 用 AST 列出每个决策点（``if`` / 三元 / ``except`` / ``and`` 的各子句），
   **包含嵌套**（``auto_spin.update`` 的 27 个决策点里有 11 个是嵌套子句）。
2. 对每个决策点问"在现有测试的输入下，守卫条件有没有至少一次为真/为假"。
3. 对"从来没走到"的逐条做变异验证：改坏那一行，跑相关套件，确认**真的没人发现**。
4. 为每一条零覆盖补断言。**断言里的每个数值都是先用探针脚本在正确实现上读出来的**
   （``%TEMP%\\audit_r11_spin_cal_mode\\probe_facts.py``，434 条实测事实），
   不是照抄源码 —— 照抄只会得到一个恒绿的测试。
5. 每条断言都**在把那一行改坏之后验过会红**。

## 三类覆盖，本文件只把第一类算作"有覆盖"

* **行为覆盖** —— 有断言，变异后会红。本文件的条目全部属于这一类（``last_reason``
  的具体字符串也算：它经 ``update_auto_spin_guard`` 的 toast 和模块列表露出给操作员，
  是用户可见行为，不是源码文本）。
* **等价变异** —— 改坏了与原写法**数值完全等价**，任何行为断言都抓不住。
  本轮找到 1 条（``auto_spin.py:100`` 的外层 ``max``），见文件末尾。
* **文本钉子** —— 只匹配源码字符串。本文件**没有**这一类。

## 一条必须写清楚的结论：``auto_spin.py:100`` 的外层 ``max`` 抓不住

``self.cooldown_until = max(self.cooldown_until, now + max(0.0, Cooldown))`` 的外层
``max`` 在本模块的控制流里**恒等于** ``now + max(0.0, Cooldown)``：新窗口只可能由
第 146 行武装，而武装前第 126 行的 ``now < self.cooldown_until`` 守卫必须为假，
也就是 ``now >= cooldown_until``；内层 ``max(0.0, ...)` 又保证加数非负，于是
``now + clamp >= cooldown_until``，外层 ``max`` 永远取右项。

随机对拍（40000 次试验、18797 次武装，只走 ``update`` / ``operator_override`` /
``reset`` 三个公开入口，含时钟回跳、设置项随机改写、功能开关翻转）**0 次反例**。
**任何行为断言都区分不了这两者**，所以这里不写断言，只把实测值打成 ``[INFO]`` ——
报一条凑数的断言比不报更糟。

## 环境

    python tests/spin_calib_matchmode_branches_test.py

需要 ``cv2`` 与 ``numpy``（``calibration`` 本身就是可选的本地视觉栈）。缺 ``cv2``
时 C 组整组标记为 SKIP 并在结尾单独报出来 —— 在没有 ``cv2`` 的机器上，
``capture`` / ``solve`` 的断言**不成立**，这一轮的变异结论只在装了 ``cv2`` 的机器上成立。
本机实测：``cv2 5.0.0``，合成棋盘格能被 ``findChessboardCornersSB`` 稳定锁定
（54 个角点），所以 C 组是真的跑 OpenCV，不是 mock。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("POLAR_USER_DATA_ROOT", tempfile.mkdtemp(prefix="polar_r11_"))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client.auto_spin import (                     # noqa: E402
    ProjectileHitSpinController, SpinDirective,
)
from rm_client.calibration import (                    # noqa: E402
    CalibrationProfile, CameraCalibrationManager,
)
from rm_client.match_modes import (                    # noqa: E402
    MATCH_MODE_KEYS, MATCH_MODES, MatchModeManager,
)
from rm_client.model import RobotState, build_default_registry  # noqa: E402

FAILED: list[str] = []
PASSED = 0
SKIPPED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILED.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


def skip(label: str, why: str) -> None:
    SKIPPED.append(f"{label} :: {why}")
    print(f"[SKIP] {label} ({why})")


# ===========================================================================
# 替身：与 ``rm_client.model.Module.get_setting`` 同契约的最小实现
# ===========================================================================

class _Setting:
    def __init__(self, value):
        self.value = value


class Feature:
    """``AutoSpinOnHit`` 的替身。

    默认值逐位对齐 ``build_default_registry()`` 里 ``AutoSpinOnHit`` 的实测默认
    （探针读出：Minimum Damage=1 / Duration=2.5 / Cooldown=1.0 /
    Only During Match=True / Require Referee Link=True / Power Guard=True /
    Minimum Buffer=10 / Auto Disable=True，模块默认 enabled=False）。
    """

    DEFAULTS = {
        "Minimum Damage": 1,
        "Duration": 2.5,
        "Cooldown": 1.0,
        "Only During Match": True,
        "Require Referee Link": True,
        "Power Guard": True,
        "Minimum Buffer": 10,
        "Auto Disable": True,
    }

    def __init__(self, enabled: bool = True, **overrides) -> None:
        self.enabled = enabled
        self._settings = {k: _Setting(v) for k, v in self.DEFAULTS.items()}
        for key, value in overrides.items():
            self._settings[key] = _Setting(value)

    def get_setting(self, name: str):
        return self._settings.get(name)

    def set_value(self, name: str, value) -> None:
        self._settings[name].value = value


def make_state(**overrides) -> RobotState:
    base = dict(
        current_stage="MATCH", current_health=500, current_buffer_energy=50,
        projectile_hit_revision=0, last_projectile_damage_delta=0,
    )
    base.update(overrides)
    return RobotState(**base)


def hit(delta: int = 16, revision: int = 1, **overrides) -> RobotState:
    return make_state(
        projectile_hit_revision=revision, last_projectile_damage_delta=delta, **overrides
    )


class BareState:
    """只带 ``update()`` 真正会读到的属性。

    用来验证 ``getattr(state, "...", 默认值)`` 那几条路：``RobotState`` 是带类级
    默认值的 dataclass，``del`` 删不掉实例上读到的默认值，测出来是恒绿的。
    """

    def __init__(self, **attrs) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


def controller(seen: int = 0, *, active_until: float = 0.0, cooldown: float = 0.0,
               owns: bool = False) -> ProjectileHitSpinController:
    c = ProjectileHitSpinController()
    c.seen_revision = seen
    c.active_until = active_until
    c.cooldown_until = cooldown
    c.owns_spin = owns
    return c


# ===========================================================================
# A. SpinDirective / active 的契约
# ===========================================================================

def test_spin_directive_contract() -> None:
    # A1 —— auto_spin.py:29  ``self.action in {"enable", "disable"}``
    # 变异 01：改成 ``== "enable"`` -> disable 指令不再算 changed，
    # ``update_auto_spin_guard`` 直接 return，底盘自转永远关不掉。
    check("A1 changed 对 enable 为真", SpinDirective("enable").changed is True)
    check("A1 changed 对 disable 为真", SpinDirective("disable").changed is True)
    check("A1 changed 对 none 为假", SpinDirective().changed is False)
    check("A1 changed 对未知动作为假", SpinDirective("weird").changed is False)

    # A2 —— auto_spin.py:155  第三个位置参数是 damage_delta，
    # UI 侧 ``f"AutoSpin ON: {reason}"`` 之后的读数就来自它。
    check("A2 enable 指令带出 damage_delta", SpinDirective("enable", "r", 3).damage_delta == 3)
    check("A2 disable 指令带出 damage_delta", SpinDirective("disable", "r", 3).damage_delta == 3)
    check("A2 none 指令 damage_delta 恒 0", SpinDirective().damage_delta == 0)
    check("A2 默认 action 是 none", SpinDirective().action == "none")
    check("A2 默认 reason 是空串", SpinDirective().reason == "")

    # A3 —— auto_spin.py:58  ``self.active_until > 0.0``
    # 变异 02：改成 ``>= 0.0`` -> 窗口未开启时 active 也报 True。
    idle = controller()
    check("A3 active_until=0.0 时 active 为假", idle.active is False)
    open_ = controller(active_until=0.001)
    check("A3 active_until=0.001 时 active 为真", open_.active is True)
    # 实测：触发一次后 active_until == now + Duration(2.5) == 12.5
    armed = controller()
    armed.update(hit(), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("A3 触发后 active_until == now + 2.5", armed.active_until == 12.5, repr(armed.active_until))
    check("A3 触发后 active 为真", armed.active is True)

    # A4 —— auto_spin.py:61 ``reset(revision)`` 夹负数
    # 变异 03：去掉 ``max(0, ...)`` -> seen_revision 变成 -1。
    c = ProjectileHitSpinController()
    c.reset(-5)
    check("A4 reset(-5) 后 seen_revision 为 0", c.seen_revision == 0, repr(c.seen_revision))
    c.reset(9)
    check("A4 reset(9) 后 seen_revision 为 9", c.seen_revision == 9, repr(c.seen_revision))
    c.active_until, c.cooldown_until, c.owns_spin = 9.0, 9.0, True
    c.reset(3)
    check("A4 reset 清掉命中窗口", c.active_until == 0.0, repr(c.active_until))
    check("A4 reset 清掉冷却", c.cooldown_until == 0.0, repr(c.cooldown_until))
    check("A4 reset 放弃自转归属", c.owns_spin is False)
    check("A4 reset 把原因复位成 IDLE", c.last_reason == "IDLE", repr(c.last_reason))

    # A5 —— auto_spin.py:83 ``max(0, int(getattr(state, "projectile_hit_revision", 0)))``
    # 实测：属性缺失 -> revision 0；负数 revision -> 被夹成 0，于是 0 <= 0 走"没有新命中"。
    c = controller()
    bare = BareState(current_stage="MATCH", current_health=500, current_buffer_energy=50,
                     last_projectile_damage_delta=16)
    check("A5 替身确实没有 revision 属性", not hasattr(bare, "projectile_hit_revision"))
    d = c.update(bare, Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("A5 缺 revision 属性时 seen 记为 0", c.seen_revision == 0, repr(c.seen_revision))
    check("A5 缺 revision 属性时不触发", d.action == "none", d.action)
    c = controller()
    d = c.update(make_state(projectile_hit_revision=-3, last_projectile_damage_delta=16),
                 Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("A5 负 revision 被夹成 0", c.seen_revision == 0, repr(c.seen_revision))
    check("A5 负 revision 不触发", d.action == "none", d.action)


# ===========================================================================
# B. 守卫矩阵：每一条"拦截"都要能被单独看见
# ===========================================================================

def test_damage_threshold_gate() -> None:
    # B1 —— auto_spin.py:123  ``damage < minimum_damage``
    # 变异 23：改成 ``<=`` -> 伤害正好等于门槛时不开自转。
    # 实测：Minimum Damage=16, damage=16 -> enable。
    c = controller()
    d = c.update(hit(delta=16), Feature(**{"Minimum Damage": 16}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B1 伤害正好等于 Minimum Damage 时放行", d.action == "enable", d.action)
    check("B1 放行时原因带出伤害数", d.reason == "PROJECTILE HIT +16", d.reason)
    check("B1 放行时 damage_delta 带出 16", d.damage_delta == 16, repr(d.damage_delta))

    c = controller()
    d = c.update(hit(delta=15), Feature(**{"Minimum Damage": 16}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B1 伤害比门槛小 1 时拦截", d.action == "none", d.action)
    check("B1 拦截原因", d.reason == "DAMAGE BELOW THRESHOLD", d.reason)
    # 变异 24：原因串被改 -> 这条断言变红。
    check("B1 拦截时 damage_delta 仍带真实伤害", d.damage_delta == 15, repr(d.damage_delta))
    check("B1 拦截时不夺取自转归属", c.owns_spin is False)
    check("B1 拦截时不开窗口", c.active_until == 0.0, repr(c.active_until))

    # B2 —— auto_spin.py:122  ``int(round(...))``
    # 变异 22：去掉 round -> 3.5 截成 3。实测：3.5 经 round 得 4。
    c = controller()
    d = c.update(hit(delta=3), Feature(**{"Minimum Damage": 3.5}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B2 Minimum Damage=3.5 时 3 点伤害被拦（round 到 4）",
          d.reason == "DAMAGE BELOW THRESHOLD", d.reason)
    c = controller()
    d = c.update(hit(delta=4), Feature(**{"Minimum Damage": 3.5}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B2 Minimum Damage=3.5 时 4 点伤害放行", d.action == "enable", d.action)

    # B3 —— auto_spin.py:122  ``max(1, ...)``：门槛被手改成 0 时仍然至少要 1 点。
    c = controller()
    d = c.update(hit(delta=0), Feature(**{"Minimum Damage": 0}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B3 Minimum Damage=0 时 0 点伤害被拦（夹到 1）",
          d.reason == "DAMAGE BELOW THRESHOLD", d.reason)
    c = controller()
    d = c.update(hit(delta=1), Feature(**{"Minimum Damage": 0}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B3 Minimum Damage=0 时 1 点伤害放行", d.action == "enable", d.action)

    # B4 —— auto_spin.py:121  ``max(0, ...)``：负增量被夹成 0，而不是负门槛放行。
    c = controller()
    d = c.update(make_state(projectile_hit_revision=1, last_projectile_damage_delta=-9),
                 Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B4 负伤害增量被夹成 0 并按低于门槛处理",
          d.reason == "DAMAGE BELOW THRESHOLD", d.reason)
    check("B4 负伤害增量上报 0 而不是 -9", d.damage_delta == 0, repr(d.damage_delta))


def test_cooldown_gate() -> None:
    # B5 —— auto_spin.py:126  ``now < self.cooldown_until``
    # 变异 26：改成 ``<=`` -> 冷却刚结束的那一帧被误判成冷却中。
    # 实测：cooldown_until=12.0，now=12.0 -> enable。
    c = controller(cooldown=12.0)
    d = c.update(hit(), Feature(), spin_enabled=False, referee_link=True, now=12.0)
    check("B5 now == cooldown_until 时冷却已结束", d.action == "enable", d.action)
    check("B5 冷却结束的那一帧原因不是 COOLDOWN", d.reason != "COOLDOWN", d.reason)

    c = controller(cooldown=12.0)
    d = c.update(hit(), Feature(), spin_enabled=False, referee_link=True, now=11.9)
    check("B5 now < cooldown_until 时进冷却", d.reason == "COOLDOWN", d.reason)
    check("B5 冷却中不上报 enable", d.action == "none", d.action)
    check("B5 冷却中把伤害数照实上报", d.damage_delta == 16, repr(d.damage_delta))
    check("B5 冷却中不夺取归属", c.owns_spin is False)
    check("B5 冷却中仍然消费掉这条 revision（不会补触发）", c.seen_revision == 1, repr(c.seen_revision))

    c = controller(cooldown=12.0)
    d = c.update(hit(), Feature(), spin_enabled=False, referee_link=True, now=12.1)
    check("B5 now > cooldown_until 时放行", d.action == "enable", d.action)


def test_robot_down_gate() -> None:
    # B6 —— auto_spin.py:129  ``int(getattr(state, "current_health", 0)) <= 0``
    # 变异 29：改成 ``<`` -> 血量恰好 0（阵亡）时反而开自转。
    c = controller()
    d = c.update(hit(current_health=0), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B6 血量恰好 0 时不开自转", d.action == "none", d.action)
    check("B6 血量恰好 0 的原因", d.reason == "ROBOT DOWN", d.reason)
    check("B6 阵亡时 damage_delta 仍照实上报", d.damage_delta == 16, repr(d.damage_delta))
    check("B6 阵亡时不开窗口", c.active_until == 0.0, repr(c.active_until))

    c = controller()
    d = c.update(hit(current_health=-5), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B6 血量为负时不开自转", d.reason == "ROBOT DOWN", d.reason)
    c = controller()
    d = c.update(hit(current_health=1), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B6 血量 1 时放行（<= 0 只拦真的死）", d.action == "enable", d.action)


def test_stage_gate() -> None:
    # B7 —— auto_spin.py:133  ``str(...).upper() not in {"4", "MATCH"}``
    # 变异 33：集合里丢掉数字阶段 "4" -> 只发数字阶段码时比赛中永不触发。
    for stage, want in (("MATCH", "enable"), ("match", "enable"), ("4", "enable"),
                        ("0", "none"), ("5", "none"), ("", "none")):
        c = controller()
        d = c.update(hit(current_stage=stage), Feature(), spin_enabled=False,
                     referee_link=True, now=10.0)
        check(f"B7 阶段 {stage!r} -> {want}", d.action == want, d.action)
    c = controller()
    d = c.update(hit(current_stage="0"), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B7 非比赛阶段的原因", d.reason == "NOT IN MATCH", d.reason)
    check("B7 非比赛阶段仍上报伤害数", d.damage_delta == 16, repr(d.damage_delta))

    # 实测：state 上没有 current_stage 属性时 getattr 默认 ""，同样被拦。
    nostage = BareState(projectile_hit_revision=1, last_projectile_damage_delta=16,
                        current_health=500, current_buffer_energy=50)
    check("B7 替身确实没有 current_stage 属性", not hasattr(nostage, "current_stage"))
    c = controller()
    d = c.update(nostage, Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B7 缺 current_stage 属性时按非比赛处理", d.reason == "NOT IN MATCH", d.reason)

    # B8 —— auto_spin.py:132  ``_bool_setting(feature_module, "Only During Match", True)``
    # 变异 32：把阶段检查变成恒真 -> 关掉开关也仍然被拦。
    c = controller()
    d = c.update(hit(current_stage="0"), Feature(**{"Only During Match": False}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B8 关掉 Only During Match 后非比赛阶段也放行", d.action == "enable", d.action)
    check("B8 放行原因是命中而不是 NOT IN MATCH", d.reason == "PROJECTILE HIT +16", d.reason)


def test_referee_link_gate() -> None:
    # B9 —— auto_spin.py:136  ``_bool_setting(..., "Require Referee Link", True) and not referee_link``
    # 变异 36a：丢掉 ``and not referee_link`` -> 链路正常时反被判成离线，完全不触发。
    c = controller()
    d = c.update(hit(), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B9 链路正常且开关打开时放行", d.action == "enable", d.action)
    c = controller()
    d = c.update(hit(), Feature(), spin_enabled=False, referee_link=False, now=10.0)
    check("B9 链路断开且开关打开时拦截", d.action == "none", d.action)
    check("B9 链路断开的原因", d.reason == "REFEREE LINK OFFLINE", d.reason)
    check("B9 链路断开时 damage_delta 照实上报", d.damage_delta == 16, repr(d.damage_delta))
    check("B9 链路断开时不夺取归属", c.owns_spin is False)
    # 变异 36b：丢掉开关 -> 关掉开关也仍然要求链路。
    c = controller()
    d = c.update(hit(), Feature(**{"Require Referee Link": False}),
                 spin_enabled=False, referee_link=False, now=10.0)
    check("B9 关掉 Require Referee Link 后无链路也放行", d.action == "enable", d.action)
    c = controller()
    d = c.update(hit(), Feature(**{"Require Referee Link": False}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B9 关掉开关且链路正常时同样放行", d.action == "enable", d.action)


def test_power_guard_gate() -> None:
    # B10 —— auto_spin.py:141  ``buffer < minimum_buffer``
    # 变异 41：改成 ``<=`` -> 缓冲正好等于门槛时被拦。
    # 实测：Minimum Buffer=10，buffer=10 -> 放行；buffer=9.9 -> 拦截。
    c = controller()
    d = c.update(hit(current_buffer_energy=10), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B10 缓冲正好等于 Minimum Buffer 时放行", d.action == "enable", d.action)
    c = controller()
    d = c.update(hit(current_buffer_energy=9.9), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B10 缓冲比门槛低 0.1 时拦截", d.action == "none", d.action)
    check("B10 低缓冲的原因", d.reason == "LOW BUFFER ENERGY", d.reason)
    check("B10 低缓冲时 damage_delta 照实上报", d.damage_delta == 16, repr(d.damage_delta))
    check("B10 低缓冲时不夺取归属", c.owns_spin is False)
    c = controller()
    d = c.update(hit(current_buffer_energy=0), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B10 缓冲为 0 时拦截", d.reason == "LOW BUFFER ENERGY", d.reason)
    # 变异 39：把守卫变成恒真 -> 关掉 Power Guard 也被拦。
    c = controller()
    d = c.update(hit(current_buffer_energy=0), Feature(**{"Power Guard": False}),
                 spin_enabled=False, referee_link=True, now=10.0)
    check("B10 关掉 Power Guard 后零缓冲也放行", d.action == "enable", d.action)
    # 实测：state 上没有 current_buffer_energy 属性时 getattr 默认 0.0 -> 拦截。
    nobuf = BareState(projectile_hit_revision=1, last_projectile_damage_delta=16,
                      current_health=500, current_stage="MATCH")
    check("B10 替身确实没有 current_buffer_energy 属性",
          not hasattr(nobuf, "current_buffer_energy"))
    c = controller()
    d = c.update(nobuf, Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("B10 缺 current_buffer_energy 属性时按 0 处理（拦截）",
          d.reason == "LOW BUFFER ENERGY", d.reason)


def test_gate_order_is_damage_cooldown_health_stage_link_power() -> None:
    # 顺序本身就是契约：全部不满足时先报哪一条，决定操作员在日志里看到什么。
    c = controller(cooldown=99.0)
    d = c.update(hit(delta=0, current_health=0, current_stage="0", current_buffer_energy=0),
                 Feature(), spin_enabled=False, referee_link=False, now=10.0)
    check("G1 门槛最优先：先报 DAMAGE BELOW THRESHOLD",
          d.reason == "DAMAGE BELOW THRESHOLD", d.reason)
    c = controller(cooldown=99.0)
    d = c.update(hit(delta=16, current_health=0, current_stage="0", current_buffer_energy=0),
                 Feature(), spin_enabled=False, referee_link=False, now=10.0)
    check("G2 冷却次之：先报 COOLDOWN", d.reason == "COOLDOWN", d.reason)
    c = controller()
    d = c.update(hit(delta=16, current_health=0, current_stage="0", current_buffer_energy=0),
                 Feature(), spin_enabled=False, referee_link=False, now=10.0)
    check("G3 阵亡先于阶段：先报 ROBOT DOWN", d.reason == "ROBOT DOWN", d.reason)
    c = controller()
    d = c.update(hit(delta=16, current_stage="0", current_buffer_energy=0),
                 Feature(), spin_enabled=False, referee_link=False, now=10.0)
    check("G4 阶段先于链路：先报 NOT IN MATCH", d.reason == "NOT IN MATCH", d.reason)
    c = controller()
    d = c.update(hit(delta=16, current_buffer_energy=0),
                 Feature(), spin_enabled=False, referee_link=False, now=10.0)
    check("G5 链路先于电量：先报 REFEREE LINK OFFLINE",
          d.reason == "REFEREE LINK OFFLINE", d.reason)
    # 全部放行时原因与伤害数带出实测值
    c = controller()
    d = c.update(hit(delta=42), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("G6 全部放行时原因带出 42", d.reason == "PROJECTILE HIT +42", d.reason)
    check("G6 全部放行时 damage_delta 为 42", d.damage_delta == 42, repr(d.damage_delta))
    check("G6 全部放行时夺取自转归属", c.owns_spin is True)
    check("G6 全部放行时开窗到 now + 2.5", c.active_until == 12.5, repr(c.active_until))


# ===========================================================================
# C. 生命周期：关闭功能 / 过期 / 选手接管
# ===========================================================================

def test_feature_disabled_releases_ownership() -> None:
    # C1 —— auto_spin.py:85  ``feature_module is None or not bool(getattr(feature_module, "enabled", False))``
    # 变异 5：去掉 ``feature_module is None or`` -> 模块缺失时 AttributeError 崩在 60 Hz 主泵里。
    c = controller()
    d = c.update(hit(), None, spin_enabled=False, referee_link=True, now=10.0)
    check("C1 feature_module 为 None 时优雅退出", d.action == "none", d.action)
    check("C1 feature_module 为 None 时不夺取归属", c.owns_spin is False)

    c = controller(owns=True)
    d = c.update(hit(), None, spin_enabled=True, referee_link=True, now=10.0)
    check("C1 模块缺失但我们拥有自转时发 disable", d.action == "disable", d.action)
    check("C1 模块缺失时 disable 的原因", d.reason == "FEATURE DISABLED", d.reason)
    check("C1 模块缺失时放弃归属", c.owns_spin is False)

    # C2 —— auto_spin.py:89  ``self.owns_spin and spin_enabled``
    # 变异 6/7/8/9：关闭功能时必须同时清 seen_revision、窗口、冷却。
    c = controller(seen=5, active_until=99.0, cooldown=99.0, owns=True)
    d = c.update(hit(revision=7), Feature(enabled=False), spin_enabled=True,
                 referee_link=True, now=10.0)
    check("C2 功能关闭且我们拥有自转时发 disable", d.action == "disable", d.action)
    check("C2 disable 的原因", d.reason == "FEATURE DISABLED", d.reason)
    check("C2 关闭时把 revision 记到当前值（不补触发）", c.seen_revision == 7, repr(c.seen_revision))
    check("C2 关闭时清掉命中窗口", c.active_until == 0.0, repr(c.active_until))
    check("C2 关闭时清掉冷却", c.cooldown_until == 0.0, repr(c.cooldown_until))
    check("C2 关闭时放弃归属", c.owns_spin is False)

    # 变异 09：``and spin_enabled`` 反了 —— 自转已经关了就不该再发 disable。
    c = controller(seen=5, owns=True)
    d = c.update(hit(revision=7), Feature(enabled=False), spin_enabled=False,
                 referee_link=True, now=10.0)
    check("C2 功能关闭但自转本来就没开时不发多余的 disable", d.action == "none", d.action)
    check("C2 仍然放弃归属", c.owns_spin is False)
    # 实测：这条 none 指令的 reason 是空串（不是 last_reason）——
    # 记下来是因为 UI 只在 changed 时才去读 reason，空串不会露出。
    check("C2 多余 none 指令的 reason 是空串", d.reason == "", repr(d.reason))

    # C3 —— 关掉再打开：同一条旧伤害不得补触发（模块刚开就自转）
    c = controller()
    c.update(hit(delta=16, revision=3), Feature(enabled=False), spin_enabled=False,
             referee_link=True, now=10.0)
    check("C3 关闭期间把 revision 记下来", c.seen_revision == 3, repr(c.seen_revision))
    check("C3 关闭期间清掉命中窗口", c.active_until == 0.0, repr(c.active_until))
    check("C3 关闭期间清掉冷却", c.cooldown_until == 0.0, repr(c.cooldown_until))
    d = c.update(hit(delta=16, revision=3), Feature(), spin_enabled=False,
                 referee_link=True, now=10.5)
    check("C3 重新打开后同一条 revision 不补触发", d.action == "none", d.action)
    check("C3 同一条 revision 不开窗口", c.active_until == 0.0, repr(c.active_until))
    d = c.update(hit(delta=16, revision=4), Feature(), spin_enabled=False,
                 referee_link=True, now=10.6)
    check("C3 新的 revision 正常触发", d.action == "enable", d.action)


def test_expiry_releases_ownership() -> None:
    # C4 —— auto_spin.py:98  ``now >= self.active_until`` 的边界
    # 变异 9：改成 ``>`` -> 恰好在窗口边界那一帧不收尾，底盘多转一整帧。
    # seen=1：本次 update 的 revision 没有变，第 111 行会短路，
    # 于是我们测的确实是"到期收尾"这一条，而不是顺带处理了一条新命中。
    c = controller(seen=1, active_until=12.5, owns=True)
    d = c.update(hit(), Feature(), spin_enabled=True, referee_link=True, now=12.5)
    check("C4 now 恰好等于 active_until 时收尾", d.action == "disable", d.action)
    check("C4 收尾原因", d.reason == "HIT WINDOW COMPLETE", d.reason)
    check("C4 收尾时武装冷却 now + 1.0", c.cooldown_until == 13.5, repr(c.cooldown_until))
    check("C4 收尾时清空窗口", c.active_until == 0.0, repr(c.active_until))
    check("C4 收尾时放弃归属", c.owns_spin is False)

    c = controller(seen=1, active_until=12.5, owns=True)
    d = c.update(hit(), Feature(), spin_enabled=True, referee_link=True, now=12.499)
    check("C4 now 还没到 active_until 时不收尾", d.action == "none", d.action)
    check("C4 未到期时窗口原样保留", c.active_until == 12.5, repr(c.active_until))
    check("C4 未到期时不动归属", c.owns_spin is True)
    check("C4 未到期时不武装冷却", c.cooldown_until == 0.0, repr(c.cooldown_until))

    # C5 —— auto_spin.py:104  ``auto_disable and self.owns_spin and spin_enabled``
    # 变异 4：去掉 auto_disable -> 操作员明确要求"保持自转"也会被强制关掉。
    c = controller(seen=1, active_until=5.0, owns=True)
    d = c.update(hit(), Feature(**{"Auto Disable": False}), spin_enabled=True,
                 referee_link=True, now=10.0)
    check("C5 Auto Disable=False 时到期不发 disable", d.action == "none", d.action)
    check("C5 Auto Disable=False 时仍然放弃归属", c.owns_spin is False)
    check("C5 Auto Disable=False 时仍然武装冷却", c.cooldown_until == 11.0, repr(c.cooldown_until))
    check("C5 Auto Disable=False 时 last_reason 记成 HIT WINDOW COMPLETE",
          c.last_reason == "HIT WINDOW COMPLETE", repr(c.last_reason))

    # 变异 104b：去掉 owns_spin -> 我们本来就没开自转也会去关选手的。
    c = controller(seen=1, active_until=5.0, owns=False)
    d = c.update(hit(), Feature(), spin_enabled=True, referee_link=True, now=10.0)
    check("C5 我们不拥有自转时到期不发 disable", d.action == "none", d.action)
    check("C5 不拥有时也不夺取归属", c.owns_spin is False)
    check("C5 不拥有时 last_reason 记成 HIT WINDOW COMPLETE",
          c.last_reason == "HIT WINDOW COMPLETE", repr(c.last_reason))

    # 变异 104c：去掉 spin_enabled -> 自转已经关了还再发一遍 disable。
    c = controller(seen=1, active_until=5.0, owns=True)
    d = c.update(hit(), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    check("C5 自转已经关了时到期不发多余的 disable", d.action == "none", d.action)
    check("C5 自转已关时仍然放弃归属", c.owns_spin is False)

    # C6 —— 变异 109：走到 104 的 else 侧时 last_reason 仍然是 "HIT WINDOW COMPLETE"。
    c = controller(seen=1, active_until=5.0, owns=False)
    c.update(hit(), Feature(), spin_enabled=True, referee_link=True, now=10.0)
    check("C6 走 104 的 else 侧时 last_reason 也是 HIT WINDOW COMPLETE",
          c.last_reason == "HIT WINDOW COMPLETE", repr(c.last_reason))

    # C6b —— 实测出来的、必须钉住的一个副作用：104 不成立时代码**不 return**，
    # 同一次 update 会接着走第 111 行往下，去处理"这一帧新来的那条命中"，
    # 而那条命中正好被本帧刚武装的冷却挡住（now < now + Cooldown）。
    # 现象：一条恰好落在到期帧的受击会被静默吞掉，日志里只剩 COOLDOWN。
    c = controller(seen=0, active_until=5.0, owns=False)
    d = c.update(hit(delta=16, revision=1), Feature(), spin_enabled=True,
                 referee_link=True, now=10.0)
    check("C6b 到期帧不 return：本帧新命中被本帧武装的冷却吞掉",
          c.last_reason == "COOLDOWN", repr(c.last_reason))
    check("C6b 被吞掉时不发任何动作", d.action == "none", d.action)
    check("C6b 被吞掉的伤害数照实上报", d.damage_delta == 16, repr(d.damage_delta))
    check("C6b 被吞掉时不开窗", c.active_until == 0.0, repr(c.active_until))

    # C7 —— auto_spin.py:102  ``max(0.0, Cooldown)``
    # 变异 12：去掉 clamp -> 配置里 Cooldown 被手改成负数时冷却落在过去，等于没有冷却。
    c = controller(seen=1, active_until=5.0)
    c.update(hit(), Feature(**{"Cooldown": -5.0}), spin_enabled=True, referee_link=True, now=10.0)
    check("C7 负 Cooldown 被夹到 0：冷却落在 now 而不是 now 之前",
          c.cooldown_until == 10.0, repr(c.cooldown_until))
    c = controller(seen=1, active_until=5.0)
    c.update(hit(), Feature(**{"Cooldown": 2.5}), spin_enabled=True, referee_link=True, now=10.0)
    check("C7 正 Cooldown 按原值武装：now + 2.5", c.cooldown_until == 12.5, repr(c.cooldown_until))

    # C8 —— **等价变异，本文件抓不住，也不假装抓得住**。
    #
    # auto_spin.py:100  ``self.cooldown_until = max(self.cooldown_until, now + max(0.0, Cooldown))``
    # 的**外层** max 恒等于 ``now + clamp``：
    #   * 新窗口只可能由第 146 行武装，而武装前第 126 行的 ``now < cooldown_until``
    #     守卫必须为假，也就是 ``now >= cooldown_until``；
    #   * 内层 ``max(0.0, ...)`` 保证加数非负，于是 ``now + clamp >= cooldown_until``，
    #     外层 max 永远取右项。
    # 随机对拍（40000 次试验 / 18797 次武装，只走 update / operator_override / reset
    # 三个公开入口，含时钟回跳、设置项随机改写、功能开关翻转）0 次反例。
    #
    # 唯一能区分它的是**从外部直接写** ``controller.cooldown_until``（例如预置成 12.0
    # 再让 now=10.0 到期）—— 全仓没有任何代码这么做过（只有 auto_spin.py 自己写这个字段），
    # 所以那不是"行为覆盖"，是"私有字段白盒"。这里只把实测值打出来，不写成断言。
    for cd, now, active, pre in ((1.0, 10.0, 5.0, 0.0), (0.0, 10.0, 5.0, 12.0),
                                 (10.0, 10.0, 5.0, 0.0), (-5.0, 10.0, 5.0, 0.0),
                                 (0.0, 3.0, 1.0, 0.0), (1.0, 40.0, 5.0, 12.0)):
        c = controller(seen=1, active_until=active, cooldown=pre)
        c.update(hit(), Feature(**{"Cooldown": cd}), spin_enabled=True, referee_link=True, now=now)
        print(f"[INFO] auto_spin.py:100 外层 max（等价变异）：预置 cooldown={pre}, now={now}, "
              f"Cooldown={cd} -> 实测 {c.cooldown_until}，无 max 的理论值 {now + max(0.0, cd)}"
              f"（本仓没有代码从外部写 cooldown_until，所以这一行抓不住）")


def test_operator_latch_and_override() -> None:
    # C9 —— auto_spin.py:114  ``self.owns_spin and not spin_enabled``
    # 变异 14：``not`` 反了 -> 选手手动关自转时不取消闩锁。
    c = controller(seen=4, active_until=30.0, owns=True)
    d = c.update(make_state(projectile_hit_revision=4), Feature(), spin_enabled=False,
                 referee_link=True, now=10.0)
    check("C9 选手手动关自转时不动归属以外的东西", d.action == "none", d.action)
    check("C9 手动关自转时取消归属闩锁", c.owns_spin is False)
    check("C9 手动关自转时清掉命中窗口", c.active_until == 0.0, repr(c.active_until))
    check("C9 手动关自转时 last_reason 记成 OPERATOR OVERRIDE",
          c.last_reason == "OPERATOR OVERRIDE", repr(c.last_reason))
    check("C9 手动关自转时冷却不被武装（下一条命中立刻可用）",
          c.cooldown_until == 0.0, repr(c.cooldown_until))
    d = c.update(hit(delta=16, revision=5), Feature(), spin_enabled=False,
                 referee_link=True, now=10.5)
    check("C9 取消闩锁后的下一条命中正常触发", d.action == "enable", d.action)

    # 自转还开着时不取消闩锁（否则重复命中会接管选手手动状态）。
    c = controller(seen=4, active_until=30.0, owns=True)
    d = c.update(make_state(projectile_hit_revision=4), Feature(), spin_enabled=True,
                 referee_link=True, now=10.0)
    check("C9 自转还开着时保留归属", c.owns_spin is True)
    check("C9 自转还开着时保留窗口", c.active_until == 30.0, repr(c.active_until))
    check("C9 自转还开着时不动 last_reason", c.last_reason == "IDLE", repr(c.last_reason))

    # 不拥有自转时也不动任何东西。
    c = controller(seen=4, active_until=30.0, owns=False)
    d = c.update(make_state(projectile_hit_revision=4), Feature(), spin_enabled=False,
                 referee_link=True, now=10.0)
    check("C9 不拥有时窗口保留", c.active_until == 30.0, repr(c.active_until))
    check("C9 不拥有时 last_reason 不变", c.last_reason == "IDLE", repr(c.last_reason))

    # C10 —— auto_spin.py:71  ``if self.active_until > 0.0``（operator_override）
    # 变异 4：改成 ``>=`` -> 没在窗口里也说"选手接管过"，把真实原因盖掉。
    c = controller(active_until=30.0, owns=True)
    c.operator_override()
    check("C10 窗口活跃时接管放弃归属", c.owns_spin is False)
    check("C10 窗口活跃时接管记 OPERATOR OVERRIDE",
          c.last_reason == "OPERATOR OVERRIDE", repr(c.last_reason))
    check("C10 窗口活跃时接管不碰 active_until（不改选手状态）",
          c.active_until == 30.0, repr(c.active_until))
    c = controller()
    c.operator_override()
    check("C10 窗口未开启时接管把原因留在 IDLE", c.last_reason == "IDLE", repr(c.last_reason))

    # C11 —— auto_spin.py:146  ``max(self.active_until, now + duration)``
    # 变异 46：去掉 max -> 选手把 Duration 从 8s 改到 0.2s 后，底盘仍旧多转 7.8s。
    f = Feature(**{"Duration": 8.0})
    c = controller()
    c.update(hit(delta=16, revision=1), f, spin_enabled=False, referee_link=True, now=10.0)
    check("C11 第一次命中开窗到 now + 8", c.active_until == 18.0, repr(c.active_until))
    f.set_value("Duration", 0.2)
    c.update(hit(delta=16, revision=2), f, spin_enabled=True, referee_link=True, now=11.0)
    check("C11 重复命中不会把窗口改短", c.active_until == 18.0, repr(c.active_until))

    # C12 —— auto_spin.py:145  ``max(0.20, Duration)``
    # 变异 45：下限 0.20 改成 0.0 -> Duration=0 时窗口长度为 0，自转瞬间结束。
    c = controller()
    d = c.update(hit(), Feature(**{"Duration": 0.0}), spin_enabled=False,
                 referee_link=True, now=10.0)
    check("C12 Duration=0 时窗口仍被撑到 0.20 秒", c.active_until == 10.2, repr(c.active_until))
    check("C12 Duration=0 时仍然发出 enable", d.action == "enable", d.action)
    c = controller()
    c.update(hit(), Feature(**{"Duration": 0.2}), spin_enabled=False, referee_link=True, now=10.0)
    check("C12 Duration=0.2 时窗口正好 0.20 秒", c.active_until == 10.2, repr(c.active_until))
    c = controller()
    c.update(hit(), Feature(**{"Duration": 2.5}), spin_enabled=False, referee_link=True, now=10.0)
    check("C12 Duration=2.5 时窗口为 2.5 秒", c.active_until == 12.5, repr(c.active_until))

    # C13 —— auto_spin.py:153  重复命中时 damage_delta 不能被抹成 0
    c = controller()
    c.update(hit(delta=16, revision=1), Feature(), spin_enabled=False, referee_link=True, now=10.0)
    d = c.update(hit(delta=7, revision=2), Feature(), spin_enabled=True, referee_link=True, now=11.0)
    check("C13 窗口内重复命中不发新的 enable", d.action == "none", d.action)
    check("C13 窗口内重复命中的 damage_delta 照实上报", d.damage_delta == 7, repr(d.damage_delta))
    check("C13 窗口内重复命中的原因带出本次伤害", d.reason == "PROJECTILE HIT +7", d.reason)
    check("C13 窗口内重复命中保留归属", c.owns_spin is True)


# ===========================================================================
# D. calibration
# ===========================================================================

@contextlib.contextmanager
def _tmp():
    d = tempfile.mkdtemp(prefix="r11_cal_")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


DEMO_YAML = (
    "camera_matrix: [1, 0, 2, 0, 3, 4, 0, 0, 1]\n"
    "distort_coeffs: [0, 0, 0, 0, 0]\n"
    "R_camera2gimbal: [1, 0, 0, 0, 1, 0, 0, 0, 1]\n"
    "t_camera2gimbal: [0, 0, 0]\n"
)

PROFILE = CalibrationProfile(
    "polar_webcam", 960, 540, 9, 6, 25.0,
    [800.0, 0.0, 480.0, 0.0, 800.0, 270.0, 0.0, 0.0, 1.0], [0.0] * 5, 0.42, 12,
)


def test_calibration_paths() -> None:
    with _tmp() as work:
        # D1 —— calibration.py:29  ``Path(profile_dir) if profile_dir is not None else ...``
        m = CameraCalibrationManager(work)
        check("D1 不给 profile_dir 时落在 workspace/rm_custom_client/calibrations",
              m.profile_dir == work / "rm_custom_client" / "calibrations", str(m.profile_dir))
        m2 = CameraCalibrationManager(work, profile_dir=work / "cal")
        check("D1 给了 profile_dir 时用它", m2.profile_dir == work / "cal", str(m2.profile_dir))
        check("D1 profile_dir 会被建出来", (work / "cal").is_dir())

        # D2 —— calibration.py:28  ``Path(workspace or Path.cwd())``
        # 变异 28：去掉 ``or Path.cwd()`` -> 无参构造直接 TypeError。
        noarg = CameraCalibrationManager()
        check("D2 无参构造回落到 cwd 而不是抛 TypeError",
              noarg.workspace == Path.cwd(), str(noarg.workspace))
        shutil.rmtree(noarg.profile_dir, ignore_errors=True)
        explicit = CameraCalibrationManager(work)
        check("D2 给了 workspace 时用它", explicit.workspace == work, str(explicit.workspace))
        check("D2 给了 workspace 时 profile_dir 落在它下面",
              explicit.profile_dir == work / "rm_custom_client" / "calibrations",
              str(explicit.profile_dir))

        # D3 —— calibration.py:39/40  名字清洗 + 空名回落
        for name, want in (
            ("polar_webcam", "polar_webcam.json"),
            ("9x6 v2", "9x6_v2.json"),
            ("a/b", "a_b.json"),
            ("..", "__.json"),
            ("-_-", "-_-.json"),
            ("", "polar_webcam.json"),
        ):
            got = m.profile_path(name).name
            check(f"D3 profile_path({name!r}) -> {want}", got == want, got)
        check("D3 profile_path 不会逃出 profile_dir",
              m.profile_path("a/b").parent == m.profile_dir)
        # 变异 39：去掉字符清洗 -> "a/b" 会变成子目录。
        check("D3 名字里的斜杠被换掉而不是变成目录分隔符",
              m.profile_path("a/b").name == "a_b.json", m.profile_path("a/b").name)
        # 变异 40：空名回落值被改名 -> 读写的不是同一个文件。
        check("D3 空名回落到 polar_webcam.json（load 和 solve 才会对上）",
              m.profile_path("").name == "polar_webcam.json", m.profile_path("").name)


def test_calibration_load() -> None:
    with _tmp() as work:
        m = CameraCalibrationManager(work, profile_dir=work / "cal")
        # D4 —— calibration.py:44  ``not path.exists()``
        # 变异 44：判断反了 -> 文件不存在时也会去 read_text，last_error 被 OSError 弄脏。
        check("D4 读不存在的文件返回 None", m.load("nope") is None)
        check("D4 读不存在的文件不算错误（last_error 保持空）", m.last_error == "", repr(m.last_error))
        check("D4 读不存在的文件不动 self.profile", m.profile is None)

        good = {"name": "good", "width": 960, "height": 540, "board_columns": 9,
                "board_rows": 6, "square_size_mm": 25.0, "camera_matrix": [1.0] * 9,
                "distort_coeffs": [0.0] * 5, "rms_error_px": 0.3, "sample_count": 10}
        (m.profile_dir / "good.json").write_text(json.dumps(good), encoding="utf-8")
        # D5 —— calibration.py:52  ``self.profile = profile``
        # 变异 52：load 成功后不缓存 -> 之后 is_valid_for 永远判 False，等于没加载。
        p = m.load("good")
        check("D5 读得到 profile", p is not None and p.name == "good")
        check("D5 读到 sample_count", p.sample_count == 10, repr(p.sample_count))
        check("D5 load 成功后缓存进 self.profile", m.profile is not None and m.profile.name == "good")
        check("D5 load 成功时 last_error 为空", m.last_error == "", repr(m.last_error))
        check("D5 加载后 is_valid_for 成立", m.is_valid_for(960, 540) is True)

        # D6 —— calibration.py:49  except 元组
        # 变异 49：去掉 TypeError -> 旧 schema 的 profile 在启动时直接崩。
        (m.profile_dir / "truncated.json").write_text("{not json", encoding="utf-8")
        m.profile = None
        check("D6 截断的 JSON 返回 None", m.load("truncated") is None)
        check("D6 截断的 JSON 写进 last_error", bool(m.last_error), repr(m.last_error))
        old = dict(good)
        old.pop("sample_count")
        (m.profile_dir / "oldschema.json").write_text(json.dumps(old), encoding="utf-8")
        m.profile = None
        try:
            got = m.load("oldschema")
            check("D6 缺字段的旧 profile 返回 None 而不是抛异常", got is None, repr(got))
            check("D6 缺字段的旧 profile 写进 last_error",
                  "sample_count" in m.last_error, repr(m.last_error))
        except Exception as exc:                      # noqa: BLE001
            check("D6 缺字段的旧 profile 返回 None 而不是抛异常", False,
                  f"RAISED {type(exc).__name__}: {exc}")
        check("D6 失败后不留下半截 self.profile", m.profile is None)
        (m.profile_dir / "notadict.json").write_text("[1, 2, 3]", encoding="utf-8")
        m.profile = None
        try:
            check("D6 非对象的 JSON 返回 None", m.load("notadict") is None)
        except Exception as exc:                      # noqa: BLE001
            check("D6 非对象的 JSON 返回 None", False, f"RAISED {type(exc).__name__}: {exc}")
        (m.profile_dir / "binary.json").write_bytes(b"\xff\xfe\x00\x01not utf8")
        m.profile = None
        try:
            check("D6 非 UTF-8 的文件返回 None", m.load("binary") is None)
        except Exception as exc:                      # noqa: BLE001
            check("D6 非 UTF-8 的文件返回 None", False, f"RAISED {type(exc).__name__}: {exc}")


def test_calibration_is_valid_for() -> None:
    with _tmp() as work:
        m = CameraCalibrationManager(work)

        def variant(**kw) -> CalibrationProfile:
            base = dict(name="p", width=960, height=540, board_columns=9, board_rows=6,
                        square_size_mm=25.0, camera_matrix=[1.0] * 9, distort_coeffs=[0.0] * 5,
                        rms_error_px=0.42, sample_count=12)
            base.update(kw)
            return CalibrationProfile(**base)

        # D7 —— calibration.py:57 的五个子句，逐个做成假
        m.profile = variant()
        check("D7 尺寸/样本/残差全对时有效", m.is_valid_for(960, 540) is True)
        m.profile = None
        check("D7 没有 profile 时无效（falsy 短路）", m.is_valid_for(960, 540) is False)
        m.profile = variant(sample_count=7)
        check("D7 样本数 7（不足 8）时无效", m.is_valid_for(960, 540) is False)
        m.profile = variant(sample_count=8)
        check("D7 样本数 8（刚够）时有效", m.is_valid_for(960, 540) is True)
        m.profile = variant(rms_error_px=2.0)
        check("D7 残差恰好 2.0 时有效（边界含等号）", m.is_valid_for(960, 540) is True)
        m.profile = variant(rms_error_px=2.01)
        check("D7 残差 2.01 时无效", m.is_valid_for(960, 540) is False)
        m.profile = variant()
        check("D7 宽度不匹配时无效", m.is_valid_for(1280, 540) is False)
        m.profile = variant()
        check("D7 高度不匹配时无效", m.is_valid_for(960, 720) is False)
        # 变异 57：去掉 width/height 两个子句 -> 640x360 的标定被套到 1280x720 的画面上。
        m.profile = variant(width=640, height=360)
        check("D7 640x360 的 profile 判 1280x720 无效（防止错分辨率套用）",
              m.is_valid_for(1280, 720) is False)
        m.profile = variant(width=640, height=360)
        check("D7 640x360 的 profile 判 640x360 有效", m.is_valid_for(640, 360) is True)


def test_calibration_export() -> None:
    with _tmp() as work:
        cfg = work / "同济自瞄" / "sp_vision_25" / "configs"
        cfg.mkdir(parents=True)
        (cfg / "demo.yaml").write_text(DEMO_YAML, encoding="utf-8")
        m = CameraCalibrationManager(work)

        # D8 —— calibration.py:122  ``profile is None``
        m.profile = None
        check("D8 没有任何 profile 时导出返回 None", m.export_tongji_yaml("polar_webcam") is None)
        check("D8 导出失败写进 last_error", m.last_error == "没有可导出的标定 profile",
              repr(m.last_error))

        # D9 —— calibration.py:121  ``self.profile or self.load(name)``
        # 变异 121：不再回读磁盘 -> 重启后第一次点导出直接失败。
        m.profile = PROFILE
        t = m.export_tongji_yaml("polar_webcam", device="/dev/video2")
        check("D9 内存里有 profile 时能导出", t is not None and t.exists(), repr(t))
        on_disk = {"name": "fromdisk", "width": 640, "height": 360, "board_columns": 9,
                   "board_rows": 6, "square_size_mm": 25.0, "camera_matrix": [500.0] * 9,
                   "distort_coeffs": [0.0] * 5, "rms_error_px": 0.3, "sample_count": 10}
        m.profile_dir.mkdir(parents=True, exist_ok=True)
        (m.profile_dir / "fromdisk.json").write_text(json.dumps(on_disk), encoding="utf-8")
        m.profile = None
        t2 = m.export_tongji_yaml("fromdisk", device="/dev/video2")
        check("D9 profile 为 None 时回读磁盘再导出", t2 is not None and t2.exists(), repr(t2))
        if t2 is not None and t2.exists():
            text = t2.read_text(encoding="utf-8")
            check("D9 回读导出的 camera_matrix 来自磁盘", "500" in text)
            check("D9 回读导出把 device 写成传入值",
                  "device: /dev/video2" in text, [ln for ln in text.split("\n") if "device" in ln])
        if t is not None:
            text = t.read_text(encoding="utf-8")
            check("D9 导出把 demo.yaml 里的 camera_matrix 就地替换掉",
                  "camera_matrix: [800, 0, 480, 0, 800, 270, 0, 0, 1]" in text,
                  [ln for ln in text.split("\n") if ln.startswith("camera_matrix")])
            check("D9 导出保留其它键", "R_camera2gimbal: [1, 0, 0, 0, 1, 0, 0, 0, 1]" in text)
            check("D9 导出在末尾留档注释", "POLAR desktop calibration profile: polar_webcam" in text)
            check("D9 留档注释带出分辨率与残差", "960x540" in text and "0.4200" in text)

        # D10 —— calibration.py:141  缺 key 时补一行
        # 变异 141：不再补 -> demo.yaml 少 R_camera2gimbal 时导出缺外参，预测点整体偏移。
        (cfg / "demo.yaml").write_text(
            "camera_matrix: [1, 0, 2, 0, 3, 4, 0, 0, 1]\ndistort_coeffs: [0, 0, 0, 0, 0]\n",
            encoding="utf-8")
        m.profile = PROFILE
        t3 = m.export_tongji_yaml("polar_webcam", device="/dev/video7")
        text3 = t3.read_text(encoding="utf-8") if t3 else ""
        check("D10 demo.yaml 缺 R_camera2gimbal 时补出该键",
              any(ln.startswith("R_camera2gimbal") for ln in text3.split("\n")),
              text3[-200:])
        check("D10 补出来的 t_camera2gimbal 是单位阵",
              any(ln.startswith("t_camera2gimbal") for ln in text3.split("\n")),
              text3[-200:])
        check("D10 补出来的 camera_matrix 用的是 profile 里的值", "800" in text3)
        check("D10 demo.yaml 缺 device 时补出该键",
              any(ln.strip().startswith("device:") for ln in text3.split("\n")))

        # D11 —— calibration.py:144  已有 device 时就地改写，不追加第二行
        # 变异 144：无条件追加 -> 出现两行 device，YAML 重复键。
        (cfg / "demo.yaml").write_text(DEMO_YAML + "device: /dev/video9\n", encoding="utf-8")
        m.profile = PROFILE
        t4 = m.export_tongji_yaml("polar_webcam", device="/dev/video7")
        text4 = t4.read_text(encoding="utf-8") if t4 else ""
        device_lines = [ln for ln in text4.split("\n") if ln.strip().startswith("device:")]
        check("D11 demo.yaml 已有 device 时只留一行", len(device_lines) == 1, device_lines)
        check("D11 那一行被改写成新设备号", device_lines == ["device: /dev/video7"], device_lines)
        check("D11 旧设备号不再出现在文件里", "/dev/video9" not in text4)

        # 缩进过的 key 也要能就地替换。
        (cfg / "demo.yaml").write_text(
            "nested:\n  camera_matrix: [1, 0, 2, 0, 3, 4, 0, 0, 1]\n"
            "  distort_coeffs: [0, 0, 0, 0, 0]\n", encoding="utf-8")
        m.profile = PROFILE
        t5 = m.export_tongji_yaml("polar_webcam", device="/dev/video7")
        text5 = t5.read_text(encoding="utf-8") if t5 else ""
        check("D11 缩进过的 camera_matrix 也被就地替换（不会再多出一行）",
              "  camera_matrix: [800, 0, 480, 0, 800, 270, 0, 0, 1]" in text5,
              [ln for ln in text5.split("\n") if "camera_matrix" in ln])

    with _tmp() as bare:
        # D12 —— calibration.py:127  ``not base.exists()``
        # 变异 127：判断反了 -> 缺基础配置时直接 read_text 抛 FileNotFoundError。
        m = CameraCalibrationManager(bare)
        m.profile = PROFILE
        try:
            got = m.export_tongji_yaml("polar_webcam")
            check("D12 没有同济基础配置时返回 None 而不是抛异常", got is None, repr(got))
            check("D12 基础配置缺失时 last_error 指出 demo.yaml",
                  "demo.yaml" in m.last_error, repr(m.last_error))
        except Exception as exc:                      # noqa: BLE001
            check("D12 没有同济基础配置时返回 None 而不是抛异常", False,
                  f"RAISED {type(exc).__name__}: {exc}")
        check("D12 失败时不留下半个 yaml", not (bare / "同济自瞄").exists())


# ===========================================================================
# E. match_modes
# ===========================================================================

class _Mod:
    def __init__(self, name, settings, enabled=False):
        self.name = name
        self.enabled = enabled
        self.toggles = []
        self._settings = {k: _Setting(v) for k, v in settings.items()}

    def get_setting(self, name):
        return self._settings.get(name)

    def set_enabled(self, value):
        self.enabled = value
        self.toggles.append(value)


class _Reg:
    def __init__(self, modules):
        self.modules = modules
        self._by_name = {m.name: m for m in modules}

    def get(self, name):
        return self._by_name.get(name)


def test_match_mode_table() -> None:
    # E1 —— 赛制键表与模式表必须一一对应（不是决策点，是这张表自己的不变量）
    check("E1 MATCH_MODE_KEYS 与 MATCH_MODES 一一对应",
          set(MATCH_MODE_KEYS) == set(MATCH_MODES),
          f"{MATCH_MODE_KEYS} vs {list(MATCH_MODES)}")
    check("E1 MATCH_MODE_KEYS 没有重复", len(MATCH_MODE_KEYS) == len(set(MATCH_MODE_KEYS)))
    for key, cfg in MATCH_MODES.items():
        check(f"E1 {key} 的 key 字段与字典键一致", cfg.key == key)
        check(f"E1 {key} 的 field.key 与字典键一致", cfg.field.key == key)
        check(f"E1 {key} 的 enabled 与 disabled 不重叠",
              not (cfg.enabled_modules & cfg.disabled_modules),
              sorted(cfg.enabled_modules & cfg.disabled_modules))
        check(f"E1 {key} 的热点都在场地范围内",
              all(0.0 <= z.x <= cfg.field.width_m and 0.0 <= z.y <= cfg.field.height_m
                  for z in cfg.field.zones),
              [(z.name, z.x, z.y) for z in cfg.field.zones
               if not (0.0 <= z.x <= cfg.field.width_m and 0.0 <= z.y <= cfg.field.height_m)])
        check(f"E1 {key} 的时长为正", cfg.duration_sec > 0, cfg.duration_sec)
    check("E1 7v7 场地 28x15", (MATCH_MODES["RMUC7v7"].field.width_m,
                              MATCH_MODES["RMUC7v7"].field.height_m) == (28.0, 15.0))
    check("E1 3v3 场地 12x8", (MATCH_MODES["RMUL3v3"].field.width_m,
                             MATCH_MODES["RMUL3v3"].field.height_m) == (12.0, 8.0))
    check("E1 7v7 有 7 个热点", len(MATCH_MODES["RMUC7v7"].field.zones) == 7)
    check("E1 3v3 有 5 个热点", len(MATCH_MODES["RMUL3v3"].field.zones) == 5)


def test_match_mode_fallback() -> None:
    # E2 —— match_modes.py:150/153  ``MATCH_MODES.get(key, MATCH_MODES["RMUC7v7"])``
    # 变异 150/153：去掉默认值 -> 未知赛制键直接 KeyError（旧配置里的赛制名就会崩）。
    check("E2 合法赛制名取到对应配置", MatchModeManager("RMUL3v3").current.key == "RMUL3v3")
    check("E2 无参默认 7v7", MatchModeManager().current.key == "RMUC7v7")
    check("E2 未知默认赛制回落 7v7 而不是抛 KeyError",
          MatchModeManager("NOPE").current.key == "RMUC7v7")
    m = MatchModeManager("RMUC7v7")
    got = m.set_mode("BOGUS")
    check("E2 set_mode 未知键回落 7v7", got.key == "RMUC7v7" and m.current.key == "RMUC7v7")
    check("E2 set_mode 未知键返回的就是 current 本身", got is m.current)
    check("E2 set_mode 空串也回落", m.set_mode("").key == "RMUC7v7")
    check("E2 set_mode 回落不会改坏已知赛制", m.set_mode("RMUL3v3").key == "RMUL3v3")


def test_match_mode_apply() -> None:
    reg = build_default_registry()
    m = MatchModeManager("RMUC7v7")
    # E3 —— match_modes.py:164/166 两个集合各自都要走到真和假。
    # 变异 164/166：两个集合写反 -> 7v7 把 EnergyAutoAim 关掉、3v3 把它打开。
    m.apply_to_registry(reg)
    check("E3 7v7 打开 EnergyAutoAim", reg.get("EnergyAutoAim").enabled is True)
    check("E3 7v7 打开 BaseOutpostStatus", reg.get("BaseOutpostStatus").enabled is True)
    check("E3 7v7 关掉 VictoryPointStatus", reg.get("VictoryPointStatus").enabled is False)
    check("E3 7v7 写入 MatchMode.Mode", reg.get("MatchMode").get_setting("Mode").value == "RMUC7v7")
    check("E3 7v7 写入 TacticalMap.Map Size",
          reg.get("TacticalMap").get_setting("Map Size").value == "Compact")
    check("E3 7v7 写入 HUD.Right Display",
          reg.get("HUD").get_setting("Right Display").value == "ModuleListHud")

    m.set_mode("RMUL3v3")
    m.apply_to_registry(reg)
    check("E3 3v3 关掉 EnergyAutoAim", reg.get("EnergyAutoAim").enabled is False)
    check("E3 3v3 关掉 BaseOutpostStatus", reg.get("BaseOutpostStatus").enabled is False)
    check("E3 3v3 打开 VictoryPointStatus", reg.get("VictoryPointStatus").enabled is True)
    check("E3 3v3 写入官方地图名",
          reg.get("TacticalMap").get_setting("Official Map").value == "RMUL 3v3 Official Map")
    check("E3 3v3 写入 MatchMode.Mode",
          reg.get("MatchMode").get_setting("Mode").value == "RMUL3v3")

    # 实测：3v3 把 AirSupport 关掉，而 7v7 两个集合里都没有它 -> 切回去它仍然是关的。
    check("E3 3v3 关掉 AirSupport", reg.get("AirSupport").enabled is False)
    m.set_mode("RMUC7v7")
    m.apply_to_registry(reg)
    check("E3 切回 7v7 重新打开 EnergyAutoAim", reg.get("EnergyAutoAim").enabled is True)
    check("E3 切回 7v7 重新打开 BaseOutpostStatus",
          reg.get("BaseOutpostStatus").enabled is True)
    check("E3 切回 7v7 关掉 VictoryPointStatus", reg.get("VictoryPointStatus").enabled is False)
    check("E3 切回 7v7 后 AirSupport 保持 3v3 关掉的状态（不在 7v7 两个集合里）",
          reg.get("AirSupport").enabled is False)

    # E4 —— match_modes.py:171/172  缺模块 / 缺设置项都要安静跳过
    # 变异 171：模块不存在时不再兜底 -> AttributeError。
    try:
        MatchModeManager("RMUL3v3").apply_to_registry(_Reg([]))
        check("E4 注册表里一个模块都没有时不抛异常", True)
    except Exception as exc:                          # noqa: BLE001
        check("E4 注册表里一个模块都没有时不抛异常", False, f"RAISED {type(exc).__name__}: {exc}")

    # 变异 172：设置项缺失时不再跳过 -> None.value。
    partial_hud = _Mod("HUD", {"Fov": 72}, enabled=True)
    partial_tac = _Mod("TacticalMap", {"Map Size": "Compact"}, enabled=True)
    partial_mode = _Mod("MatchMode", {"Mode": "RMUC7v7"}, enabled=True)
    outsider = _Mod("NotInAnySet", {}, enabled=True)
    partial = _Reg([partial_hud, partial_tac, partial_mode, outsider])
    try:
        MatchModeManager("RMUC7v7").apply_to_registry(partial)
        check("E4 模块存在但缺设置项时不抛异常", True)
    except Exception as exc:                          # noqa: BLE001
        check("E4 模块存在但缺设置项时不抛异常", False, f"RAISED {type(exc).__name__}: {exc}")
    check("E4 缺 Right Display 时不凭空造一个设置项",
          partial_hud.get_setting("Right Display") is None)
    check("E4 存在的设置项照常写入",
          partial_tac.get_setting("Map Size").value == "Compact")
    check("E4 在 enabled 集合里的模块照常被打开", partial_hud.toggles == [True], partial_hud.toggles)
    check("E4 不在任何集合里的模块一个开关都不该被写", outsider.toggles == [], outsider.toggles)

    # 只在 enabled 集合里的模块才被 set_enabled(True)，其余一个都不该被碰。
    solo = _Mod("SpinMode", {}, enabled=False)
    reg_solo = _Reg([solo])
    MatchModeManager("RMUC7v7").apply_to_registry(reg_solo)
    check("E4 只有 SpinMode 的注册表里它被打开", solo.toggles == [True], solo.toggles)
    neither = _Mod("AirSupport", {}, enabled=False)
    reg_neither = _Reg([neither])
    MatchModeManager("RMUC7v7").apply_to_registry(reg_neither)
    check("E4 不在任何集合里的模块一个开关都不该被写", neither.toggles == [], neither.toggles)


# ===========================================================================
# F. capture / solve —— 真的跑 OpenCV，不是 mock
# ===========================================================================

def _synth(cols: int, rows: int, sq: int = 40, pad: int = 80, jitter: int = 0, seed: int = 0):
    """合成一张能被 findChessboardCornersSB 锁住的棋盘格 BGR 图。"""
    import numpy as np
    import cv2
    rng = np.random.default_rng(seed)
    img = np.full(((rows + 1) * sq + 2 * pad, (cols + 1) * sq + 2 * pad), 255, np.uint8)
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                continue
            img[pad + r * sq:pad + (r + 1) * sq, pad + c * sq:pad + (c + 1) * sq] = 0
    if jitter:
        img = np.clip(img.astype(np.int16) + rng.integers(-jitter, jitter + 1, img.shape),
                      0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_calibration_capture_and_solve() -> None:
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
    except Exception as exc:                          # noqa: BLE001
        skip("F 组 capture/solve", f"本机没有 cv2/numpy（{type(exc).__name__}）——"
                                   "这一轮的 capture/solve 变异结论在缺 cv2 的机器上不成立")
        return

    with _tmp() as work:
        m = CameraCalibrationManager(work)

        # F1 —— calibration.py:66  ``bgr is None``
        # 变异 66：变成恒真 -> 采集功能整体失效。
        check("F1 capture(None) 返回 False", m.capture(None, 9, 6, 25.0) is False)
        check("F1 capture(None) 给出中文提示",
              m.last_error == "当前没有可用于标定的摄像头帧", repr(m.last_error))
        check("F1 capture(None) 不留下样本", m.sample_count == 0)

        # F2 —— calibration.py:72  ``not found or corners is None``
        # 变异 72：变成恒真 -> 相机对准棋盘也采不到样。
        blank = np.full((200, 200, 3), 200, np.uint8)
        check("F2 纯色画面找不到棋盘格时返回 False", m.capture(blank, 9, 6, 25.0) is False)
        check("F2 找不到棋盘格时给出中文提示",
              m.last_error == "未找到完整棋盘格，请调整距离、角度或曝光", repr(m.last_error))
        check("F2 找不到棋盘格时不留下样本", m.sample_count == 0)
        check("F2 上一次失败不残留 last_corner_count", m.last_corner_count == 0)

        board = _synth(9, 6, jitter=10, seed=0)
        check("F3 合成棋盘格被真实 OpenCV 锁定（前置事实）", m.capture(board, 9, 6, 25.0) is True)
        check("F3 采集成功后 last_error 被清空", m.last_error == "", repr(m.last_error))
        check("F3 9x6 棋盘格数出 54 个角点", m.last_corner_count == 54, repr(m.last_corner_count))
        check("F3 样本数加到 1", m.sample_count == 1, repr(m.sample_count))

        # F4 —— calibration.py:95  ``self._image_size is None or len(self._image_points) < 8``
        # 变异 95：下限 8 失效 -> 少于 8 组也照样解算。
        check("F4 只有 1 组时 solve 返回 None", m.solve("tooFew", 9, 6, 25.0) is None)
        check("F4 样本不足时给出中文提示",
              m.last_error == "至少采集 8 组不同角度的完整棋盘格后才能标定", repr(m.last_error))
        for i in range(1, 4):
            m.capture(_synth(9, 6, jitter=10, seed=i), 9, 6, 25.0)
        check("F4 采到 4 组时 solve 仍然返回 None", m.solve("tooFew", 9, 6, 25.0) is None)
        check("F4 4 组时 sample_count 是 4", m.sample_count == 4, repr(m.sample_count))

        # F5 —— calibration.py:76  分辨率变了必须清点
        # 变异 76：判断改成只判"采过没有" -> 两个分辨率的角点混在一起解算。
        big = _synth(9, 6, sq=60, pad=120, jitter=10, seed=9)
        check("F5 换分辨率的图也能采到", m.capture(big, 9, 6, 25.0) is True)
        check("F5 换分辨率后样本被清空重数（4 -> 1）", m.sample_count == 1, repr(m.sample_count))
        check("F5 记录的新分辨率是 840x660", m._image_size == (840, 660), repr(m._image_size))
        check("F5 同分辨率继续采是累加不是重数",
              (m.capture(_synth(9, 6, sq=60, pad=120, jitter=10, seed=10), 9, 6, 25.0),
               m.sample_count) == (True, 2), repr(m.sample_count))

        # F6 —— 采满 8 组真解算一次
        m2 = CameraCalibrationManager(work, profile_dir=work / "cal2")
        for i in range(8):
            m2.capture(_synth(9, 6, jitter=12, seed=100 + i), 9, 6, 25.0)
        check("F6 采满 8 组", m2.sample_count == 8, repr(m2.sample_count))
        check("F6 记录到的分辨率是 560x440", m2._image_size == (560, 440), repr(m2._image_size))
        p = m2.solve("good9x6", 9, 6, 25.0)
        check("F6 8 组时 solve 成功", p is not None)
        if p is not None:
            check("F6 解出的分辨率来自图像", (p.width, p.height) == (560, 440), (p.width, p.height))
            check("F6 棋盘规格被记下来", (p.board_columns, p.board_rows) == (9, 6),
                  (p.board_columns, p.board_rows))
            check("F6 方格边长被记下来", p.square_size_mm == 25.0, p.square_size_mm)
            check("F6 样本数被记下来", p.sample_count == 8, p.sample_count)
            check("F6 相机矩阵是 9 个数", len(p.camera_matrix) == 9, len(p.camera_matrix))
            check("F6 相机矩阵里有正的焦距",
                  p.camera_matrix[0] > 0 and p.camera_matrix[4] > 0, p.camera_matrix[::4])
            check("F6 畸变系数被展开成列表", len(p.distort_coeffs) >= 5,
                  len(p.distort_coeffs))
            check("F6 解算成功时 last_error 为空", m2.last_error == "", repr(m2.last_error))
            check("F6 profile 落盘了", m2.profile_path("good9x6").exists())
            on_disk = json.loads(m2.profile_path("good9x6").read_text(encoding="utf-8"))
            check("F6 落盘的 sample_count 与内存一致", on_disk["sample_count"] == 8)
            check("F6 解算后 is_valid_for 成立", m2.is_valid_for(p.width, p.height) is True)

        # F7 —— calibration.py:100  解算异常要被吃掉
        # 变异 100：只接 ZeroDivisionError -> 退化输入直接抛到操作员按钮回调里崩掉。
        m3 = CameraCalibrationManager(work, profile_dir=work / "cal3")
        for i in range(8):
            m3.capture(_synth(9, 6, jitter=12, seed=200 + i), 9, 6, 25.0)
        # 故意把点弄成不可能的形状，让 calibrateCamera 抛 OpenCV 异常
        bad = np.zeros((54, 2), dtype=np.float32)
        m3._object_points = [np.zeros((3, 3), dtype=np.float32) for _ in range(8)]
        m3._image_points = [bad for _ in range(8)]
        try:
            got = m3.solve("degenerate", 9, 6, 25.0)
            check("F7 退化输入时 solve 返回 None 而不是抛异常", got is None, repr(got))
            check("F7 退化输入时 last_error 非空", bool(m3.last_error), repr(m3.last_error))
        except Exception as exc:                      # noqa: BLE001
            check("F7 退化输入时 solve 返回 None 而不是抛异常", False,
                  f"RAISED {type(exc).__name__}: {exc}")


# ===========================================================================

def main() -> int:
    groups = [
        ("A. SpinDirective / active 的契约", test_spin_directive_contract),
        ("B1. 伤害门槛", test_damage_threshold_gate),
        ("B2. 冷却", test_cooldown_gate),
        ("B3. 阵亡", test_robot_down_gate),
        ("B4. 比赛阶段", test_stage_gate),
        ("B5. 裁判链路", test_referee_link_gate),
        ("B6. 电量守卫", test_power_guard_gate),
        ("B7. 守卫顺序", test_gate_order_is_damage_cooldown_health_stage_link_power),
        ("C1. 关闭功能 / 模块缺失", test_feature_disabled_releases_ownership),
        ("C2. 窗口到期", test_expiry_releases_ownership),
        ("C3. 选手接管与闩锁", test_operator_latch_and_override),
        ("D1. 路径与名字清洗", test_calibration_paths),
        ("D2. load", test_calibration_load),
        ("D3. is_valid_for", test_calibration_is_valid_for),
        ("D4. export_tongji_yaml", test_calibration_export),
        ("E1. 赛制表不变量", test_match_mode_table),
        ("E2. 赛制回落", test_match_mode_fallback),
        ("E3. apply_to_registry", test_match_mode_apply),
        ("F1. capture / solve（真 OpenCV）", test_calibration_capture_and_solve),
    ]
    for title, fn in groups:
        print(f"\n--- {title} ---")
        fn()
    print(f"\n{'FAILED' if FAILED else 'PASSED'}: {PASSED} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  [FAIL] {name}")
    for name in SKIPPED:
        print(f"  [SKIP] {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
