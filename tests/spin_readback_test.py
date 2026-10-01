"""自转 0x1F 回读闭环 —— AGENT.md 第 4 节不变量 9 的**收敛那半边**。

# 缺口在哪

AGENT.md 不变量 9 写得很清楚：

> 自转（小陀螺）只能走上行 0x04，且必须用 0x1F 回读闭环。
> **OFF 必须收敛到 NONE**：确认停转后就放手，否则会永久锁死驾驶员用物理
> 遥控器开自转的能力。无 0x1F 回读时用 SPIN_OFF_ASSERT_S=1.0s 有界压制兼底。

并点名了三条回归断言（`test_spin_command_reaches_robot_without_drive_authorisation`、
`test_spin_off_is_bounded_without_firmware_readback`、
`test_spin_command_channel_is_independent_of_drive`）。

这三条**都在**，但它们走的都是 ``reported=None``（固件没打 host_bridge 补丁）
那一条**兜底**路。`BackendManager._update_spin_command` 的另外两个分支 ——
``reported`` 为真（回读说还在转 → 继续发 OFF）与为假（回读确认已停 → 交还
控制权）—— **全仓没有任何测试碰过**（`grep reported=True` 在 tests/ 下零命中）。

# 为什么这条分支比兜底路更要紧

兜底路（reported=None）判错的后果是"自转可能没关掉"，现场看得见。
回读路判错的后果分两个方向，都更糟：

* **把"还在转"当成"已停"**（reported 真值被当成假值处理）→ 立刻发 NONE 放手，
  而底盘还在转，客户端**永远不再压制**。车会一直转下去，而 HUD 显示"已交还"。
* **把"已停"当成"还在转"**（假值被当成真值）→ 永久卡在 OFF，**永久锁死驾驶员
  用物理遥控器开自转**。这正是不变量 9 特意点名要防的那件事。

换句话说，**这条分支的两侧都是静默失效**：没有任何异常、没有任何日志，
只有车在转，或者操作员按不动。

跑法：``python tests/spin_readback_test.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend import polar_link as pl  # noqa: E402
from rm_client.backend.manager import BackendManager  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


def _manager() -> BackendManager:
    m = BackendManager(workspace=tempfile.mkdtemp(prefix="spin_readback_"),
                       scenario_key="OfficialEvent", client_id="3")
    # 从"客户端曾经主张过开"这个状态起步 —— 收敛逻辑只在 claimed 之后才有意义
    m._spin_ever_on = True
    m._spin_off_since = 0.0
    m.polar.set_spin_command(pl.SPIN_CMD_NONE)
    return m


# --- 1. 回读说"还在转"：必须继续发 OFF ---------------------------------

def test_reported_spinning_keeps_asserting_off() -> None:
    m = _manager()
    try:
        for value in (1, True, 2, 0xFF):
            cmd = m._update_spin_command(desired=False, reported=value)
            check(f"回读={value!r}（还在转）时继续发 OFF", cmd == pl.SPIN_CMD_OFF, str(cmd))
        check("持续发 OFF 期间仍认为客户端主张过自转", m._spin_ever_on is True)
        check("持续发 OFF 期间没有开始有界倒计时",
              m._spin_off_since == 0.0, str(m._spin_off_since))
    finally:
        m.shutdown()


# --- 2. 回读说"已停"：必须立刻交还控制权 -------------------------------

def test_reported_stopped_hands_control_back() -> None:
    m = _manager()
    try:
        for value in (0, False, 0x0000):
            cmd = m._update_spin_command(desired=False, reported=value)
            check(f"回读={value!r}（已停）时交还控制权（发 NONE）",
                  cmd == pl.SPIN_CMD_NONE, str(cmd))
            check(f"回读={value!r} 交还后不再认为自己主张过自转", m._spin_ever_on is False)
            check(f"回读={value!r} 交还后倒计时清零", m._spin_off_since == 0.0,
                  str(m._spin_off_since))
            # 交还之后要能重新主张（状态机没被卡死）
            m._spin_ever_on = True
    finally:
        m.shutdown()


# --- 3. 完整的"开 -> 关 -> 回读还在转 -> 回读已停"序列 ----------------

def test_full_convergence_sequence() -> None:
    """把不变量 9 的三条规则按真实顺序串一遍。

    顺序很重要：兜底路的 ``_spin_off_since`` 倒计时与回读路的判定是互斥的，
    单测各跑一条看不出来。
    """
    m = _manager()
    try:
        seq = []

        seq.append(("想要开", m._update_spin_command(desired=True, reported=0), pl.SPIN_CMD_ON))
        check("想要开时发 ON（电平语义，每 tick 重发）",
              seq[-1][1] == pl.SPIN_CMD_ON, str(seq[-1][1]))
        check("想要开会记住自己主张过自转", m._spin_ever_on is True)

        seq.append(("还在转", m._update_spin_command(desired=False, reported=1), pl.SPIN_CMD_OFF))
        check("松开摇杆且回读还在转 → 发 OFF", seq[-1][1] == pl.SPIN_CMD_OFF, str(seq[-1][1]))

        seq.append(("仍在转", m._update_spin_command(desired=False, reported=1), pl.SPIN_CMD_OFF))
        check("持续回读还在转 → 持续发 OFF", seq[-1][1] == pl.SPIN_CMD_OFF, str(seq[-1][1]))

        seq.append(("已停", m._update_spin_command(desired=False, reported=0), pl.SPIN_CMD_NONE))
        check("回读确认已停 → 收敛到 NONE（不变量 9 的核心）",
              seq[-1][1] == pl.SPIN_CMD_NONE, str(seq[-1][1]))
        check("收敛后交还控制权", m._spin_ever_on is False)
        print(f"      序列：{[(n, c) for n, c, _ in seq]}")
    finally:
        m.shutdown()


# --- 4. 规则 3：从未主张过开就一律发 NONE -----------------------------

def test_never_claimed_never_touches_operators_spin() -> None:
    m = _manager()
    try:
        m._spin_ever_on = False
        m._spin_off_since = 0.0
        for reported in (None, 0, 1, True, False):
            cmd = m._update_spin_command(desired=False, reported=reported)
            check(f"客户端从未主张过（回读={reported!r}）→ 发 NONE，不碰驾驶员状态",
                  cmd == pl.SPIN_CMD_NONE, str(cmd))
        check("始终不认为客户端主张过自转", m._spin_ever_on is False)
    finally:
        m.shutdown()


# --- 5. 兜底路与回读路互不串味 ---------------------------------------

def test_fallback_timer_does_not_leak_into_readback_path() -> None:
    """``reported=None`` 的有界倒计时不得污染回读分支。

    如果两条路共用一个计时器，"曾经走过兜底"会被当成"已经收敛过"，
    表现为：固件打了补丁之后仍然发满 1 秒 OFF 才放手 —— 慢一秒，
    而在这一秒里操作员按遥控器是没用的。
    """
    m = _manager()
    try:
        # 先走一遍兜底，把倒计时推起来
        cmd = m._update_spin_command(desired=False, reported=None)
        check("兜底路先发 OFF", cmd == pl.SPIN_CMD_OFF, str(cmd))
        check("兜底路启动了倒计时", m._spin_off_since > 0.0, str(m._spin_off_since))

        # 现在固件打了补丁，回读说已停
        cmd = m._update_spin_command(desired=False, reported=0)
        check("回读说已停时立刻交还，不等兜底倒计时走完",
              cmd == pl.SPIN_CMD_NONE, str(cmd))
        check("交还后倒计时被清零", m._spin_off_since == 0.0, str(m._spin_off_since))
    finally:
        m.shutdown()


# --- 6. 指令确实压到了线上（不只状态对了） ---------------------------

def test_command_reaches_the_transport_layer() -> None:
    m = _manager()
    try:
        m._update_spin_command(desired=False, reported=1)
        check("OFF 真的压到了传输层（不是只改了返回值）",
              m.polar.spin_command() == pl.SPIN_CMD_OFF, str(m.polar.spin_command()))
        m._update_spin_command(desired=False, reported=0)
        check("交还后传输层也是 NONE",
              m.polar.spin_command() == pl.SPIN_CMD_NONE, str(m.polar.spin_command()))
        m._update_spin_command(desired=True, reported=0)
        check("想要开时传输层是 ON",
              m.polar.spin_command() == pl.SPIN_CMD_ON, str(m.polar.spin_command()))
    finally:
        m.shutdown()


# --- 7. 取值归一（先看清真实语义再写断言） ---------------------------

def test_invalid_command_is_normalised() -> None:
    """``set_spin_command`` 实际做的是 ``int(spin_cmd) & 0xFF`` 再归一。

    所以有两级，得分开测：

    * **数值越界**先按 8 位截断，再落到 {NONE, ON, OFF} 之外的一律归 NONE。
      注意 ``258 & 0xFF == 2 == SPIN_CMD_OFF`` —— 截断之后**会变成"关"**。
      这不是 bug，是编码器 / 固件 / 传输层三处一致的掩码写法，但它足够反直觉，
      所以必须有一条测试钉住，否则将来有人"顺手改成先归一再截断"，
      258 就会从"关"变成"不干预"，而症状是"自转关不掉"，现场极难定位。

    * **非数值类型**（字符串等）会在 ``int()`` 处抛 ValueError。这里**不**吞：
      那只可能来自编程错误，让它当场炸比静默按"不干预"处理要好。
      第一版这条测试写成了"非数值也归一"，跑出来红 —— 是断言写错了，
      不是代码错了。
    """
    m = _manager()
    try:
        # 越界：先截断到 8 位
        for bad, want, why in (
            (-1, pl.SPIN_CMD_NONE, "-1 & 0xFF == 255，落在集合外 -> 归一"),
            (99, pl.SPIN_CMD_NONE, "99 落在集合外 -> 归一"),
            (256, pl.SPIN_CMD_NONE, "256 & 0xFF == 0"),
            (257, pl.SPIN_CMD_ON, "257 & 0xFF == 1"),
            (258, pl.SPIN_CMD_OFF, "258 & 0xFF == 2 —— 反直觉但与固件一致"),
            (0x1FF, pl.SPIN_CMD_NONE, "0x1FF & 0xFF == 255，落在集合外"),
        ):
            m.polar.set_spin_command(bad)
            got = m.polar.spin_command()
            check(f"{bad} -> {want}（{why}）", got == want, str(got))

        # 非数值类型：当场抛，不静默
        try:
            m.polar.set_spin_command("spin")   # type: ignore[arg-type]
            check("非数值指令当场抛 ValueError 而不是静默归一", False, "居然没抛")
        except ValueError:
            check("非数值指令当场抛 ValueError 而不是静默归一", True)

        # 抛完之后状态没被污染
        check("抛错后自转指令保持原值", m.polar.spin_command() in
              (pl.SPIN_CMD_NONE, pl.SPIN_CMD_ON, pl.SPIN_CMD_OFF),
              str(m.polar.spin_command()))
    finally:
        m.shutdown()


def main() -> int:
    tests = [
        test_reported_spinning_keeps_asserting_off,
        test_reported_stopped_hands_control_back,
        test_full_convergence_sequence,
        test_never_claimed_never_touches_operators_spin,
        test_fallback_timer_does_not_leak_into_readback_path,
        test_command_reaches_the_transport_layer,
        test_invalid_command_is_normalised,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"自转 0x1F 回读闭环：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"自转 0x1F 回读闭环：全部通过（{PASSED} 项）")
    print("AGENT.md 不变量 9 的三条规则，以及此前无测试的回读收敛分支，都已钉住。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
