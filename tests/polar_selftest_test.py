"""队内链路自检 CLI（polar_selftest）的行为验证。

重点不是「能不能跑出 JSON」，而是几条安全契约：

1. 默认只读：不给两个显式开关就绝不发运动指令。
2. 运动前置门：没有 0x1E 回读 / 有部件错误 / 不知道是哪台车时，必须拒绝发运动。
3. 无论成功、失败还是异常，退出前都必须先归零再关端口。
4. 报告永远是 `authoritative: False` —— 自检不得绕过 QualificationManager
   的 HMAC + 双人署名去铸造现场证据。

    python tests/polar_selftest_test.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from polar_integration_test import Pipe, PipeSerial, VirtualRobot  # noqa: E402

from rm_client import polar_selftest as ps  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAILED.append(name)


class RobotSession:
    """把虚拟机器人接到被 fake 的 serial 模块上，跑一次自检。

    “无 0x1E 回读”的情况由 `test_motion_gate_refusals` 直接对纯函数取证，
    不在这里靠改虚拟机器人内部实现去拼凑。
    """

    def __init__(self, *, consumes_chassis: bool, emits_motion: bool = True) -> None:
        self.to_robot = Pipe()
        self.to_host = Pipe()
        self.robot = VirtualRobot(
            self.to_robot, self.to_host,
            consumes_chassis=consumes_chassis, emits_motion=emits_motion,
        )
        self.saved = sys.modules.get("serial")

    def __enter__(self) -> "RobotSession":
        endpoint = PipeSerial(self.to_robot, self.to_host)
        fake = types.ModuleType("serial")
        fake.Serial = lambda *a, **k: endpoint  # type: ignore[attr-defined]
        sys.modules["serial"] = fake
        self.robot.start()
        return self

    def __exit__(self, *exc) -> None:
        self.robot.stop()
        if self.saved is not None:
            sys.modules["serial"] = self.saved
        else:
            sys.modules.pop("serial", None)

    def run(self, **kwargs) -> ps.SelftestResult:
        kwargs.setdefault("port", "COM99")
        kwargs.setdefault("seconds", 1.0)
        kwargs.setdefault("log", lambda *a, **k: None)
        return ps.run_polar_selftest(**kwargs)


def test_readonly_by_default() -> None:
    print("\n--- 默认只读 ---")
    with RobotSession(consumes_chassis=True) as session:
        result = session.run()
        loop = result.phases.get("control_loopback", {})
        check("只读模式跳过控制回环", loop.get("skipped") is True, str(loop)[:120])
        check("只读模式退出码为 0", result.exit_code == ps.EXIT_OK, str(result.exit_code))
        check("连接成功", result.connected is True)
        check("下行解码正常", not result.phases["downlink"]["required_missing"])
        check("报告标记为非权威证据", result.as_dict()["authoritative"] is False)
        check("急停阶段已写归零帧", result.phases["estop"]["zero_frames_written"] > 0, str(result.phases["estop"]))
        check("机器人只收到零速帧", all(abs(c[0]) < 1e-6 for c in session.robot.received_cmds), str(session.robot.received_cmds[:2]))


def test_unfinished_selftest_duration_is_not_negative_epoch() -> None:
    """自检没跑完就导出时，`duration_s` 不能是 `0.0 - epoch`（约 −1.79e9）。

    第 116 轮。一次 `or_drop` 普查（AST，92 个生产文件）把它列为第一顺位，本轮实测确认。

    ```python
    finished_at: float = 0.0                       # <- 「还没结束」的缺省值
    "finished_at": round(self.finished_at or time.time(), 3),
    "duration_s": round((self.finished_at or time.time()) - self.started_at, 3),
    ```

    ## 那个 `or` 挡掉的是什么

    `started_at` 是 epoch 秒（`default_factory=time.time`）。所以一旦
    `finished_at` 保持缺省的 `0.0` 而没人去 `or` 一下，
    `duration_s` 就是 **`0.0 − 1.79e9`**。

    **这是一份会被现场当证据读的文件**（本文件的注释自己写着
    「正式现场证据必须由 QualificationManager 以 HMAC 签名记录」），
    **让证据里出现一个负二十亿秒的时长，是在污染它**。

    ## 判据

    只断「`duration_s` 落在 `[0, 60)` 里」——
    上界取 60 秒是因为本用例把 `started_at` 设成 5 秒前，
    而 5 秒后 `finished_at` 仍是 `0.0`，走 `or` 拿到的是**当下**，
    于是 `duration_s` 约等于 5。给它一个宽松但明确的窗口，
    **既容得下真值，又容不下 −1.79e9**。
    """
    import time

    result = ps.SelftestResult()
    result.started_at = time.time() - 5.0
    result.finished_at = 0.0                       # 从未设置 —— 就是缺省那个「没结束」

    payload = result.as_dict()
    duration = float(payload["duration_s"])

    check(
        "未结束时 duration_s 落在 [0, 60) 而不是 0.0 - epoch",
        0.0 <= duration < 60.0,
        f"拿到 {duration} —— `finished_at or time.time()` 的 `or` 被删了？"
        f"started_at={result.started_at!r} finished_at={result.finished_at!r}",
    )
    check(
        "同一次导出里 finished_at 与 duration_s 自洽"
        "（差值应落在 [0, 60) 内，不是各算各的）",
        abs(float(payload["finished_at"]) - float(payload["started_at"]) - duration) < 1.0,
        f"finished_at={payload['finished_at']} started_at={payload['started_at']} "
        f"duration_s={duration}",
    )


def test_finished_selftest_duration_matches_the_finish_time() -> None:
    """已结束时 `duration_s` 必须等于**它自己记录的**那个结束时刻之差。

    上面那条只能证明「没结束时不是负数」，证明不了「结束时是对的」——
    **一个恒等于 0 的实现也能过上面那条**。这条把另一侧钉住。
    """
    import time

    result = ps.SelftestResult()
    result.started_at = 1000.0
    result.finished_at = 1007.25                    # 一个不会漂移的固定值

    payload = result.as_dict()
    check("已结束时 duration_s 就是结束时刻之差", abs(float(payload["duration_s"]) - 7.25) < 1e-3,
          str(payload["duration_s"]))
    check("已结束时 finished_at 原样透传（不被 `or` 改写）",
          abs(float(payload["finished_at"]) - 1007.25) < 1e-3, str(payload["finished_at"]))


def test_single_switch_is_not_enough() -> None:
    print("\n--- 只给一个开关不允许运动 ---")
    with RobotSession(consumes_chassis=True) as session:
        result = session.run(enable_drive=True, confirm_motion=False)
        check("缺 --polar-confirm-motion 时仍跳过", result.phases["control_loopback"].get("skipped") is True)
        check("未运动则机器人只收到零速", all(abs(c[0]) < 1e-6 for c in session.robot.received_cmds))

    with RobotSession(consumes_chassis=True) as session:
        result = session.run(enable_drive=False, confirm_motion=True)
        check("缺 --polar-enable-drive 时仍跳过", result.phases["control_loopback"].get("skipped") is True)


def test_motion_loopback_patched_robot() -> None:
    print("\n--- 两个开关齐给 + 机器人已补消费者 ---")
    with RobotSession(consumes_chassis=True) as session:
        result = session.run(enable_drive=True, confirm_motion=True)
        loop = result.phases.get("control_loopback", {})
        check("控制回环已执行", loop.get("skipped") is False, str(loop)[:160])
        check("底盘响应被确认", loop.get("chassis_responded") is True, str(loop.get("observations")))
        check("云台响应被确认", loop.get("gimbal_responded") is True, str(loop.get("gimbal_delta_deg")))
        check("自检退出码 0", result.exit_code == ps.EXIT_OK, str(result.exit_code))
        check("机器人确实收到过非零速度", any(abs(c[0]) > 0.1 for c in session.robot.received_cmds))
        check("fire 全程为 0", all(c[5] == 0 for c in session.robot.received_cmds))
        last = session.robot.received_cmds[-1]
        check("最后一帧已归零", abs(last[0]) < 1e-6, str(last))


def test_motion_loopback_unpatched_robot() -> None:
    print("\n--- 机器人未补底盘消费者（当前 master 真实行为）---")
    with RobotSession(consumes_chassis=False) as session:
        result = session.run(enable_drive=True, confirm_motion=True)
        loop = result.phases.get("control_loopback", {})
        check("自检判定车未响应", loop.get("verdict") == "NO_MOTION_RESPONSE", str(loop.get("verdict")))
        check("底盘响应被判 False", loop.get("chassis_responded") is False)
        check("退出码指出无运动响应", result.exit_code == ps.EXIT_NO_MOTION_RESPONSE, str(result.exit_code))
        check(
            "问题清单指向固件消费者",
            any("GetScCmdChassisSpeed" in p for p in result.problems),
            str(result.problems)[:160],
        )
        check("仍然发完归零帧", abs(session.robot.received_cmds[-1][0]) < 1e-6)


def test_motion_gate_refusals() -> None:
    print("\n--- 运动前置门的各项拒绝 ---")
    both = {"robot_id": 3, "has_motion_feedback": True, "has_position_feedback": True}
    check("机器人编号未知时拒绝运动", bool(ps._motion_gate(_Bridge(active=True), {"robot_id": 0, "has_motion_feedback": True, "has_position_feedback": True})))
    check("链路不新鲜时拒绝运动", bool(ps._motion_gate(_Bridge(active=False), both)))
    check("有部件错误时拒绝运动", bool(ps._motion_gate(_Bridge(active=True, errors={"gimbal": True}), both)))
    check(
        "无 0x1E 也无测速坐标时拒绝运动（避免盲开）",
        bool(ps._motion_gate(_Bridge(active=True), {"robot_id": 3, "has_motion_feedback": False, "has_position_feedback": False})),
    )
    check(
        "缺 0x1E 但有测速坐标时放行（改用位移证据）",
        ps._motion_gate(_Bridge(active=True), {"robot_id": 3, "has_motion_feedback": False, "has_position_feedback": True}) == "",
    )
    check("条件齐备时放行", ps._motion_gate(_Bridge(active=True), both) == "")


def test_position_fallback_verifies_motion() -> None:
    """omni/mecanum 实车形态：没有 0x1E 速度回读，但有 0x0A 测速坐标。

    这是他们实际那两台车在打完补丁后的样子：如果没这个兜底，
    自检会永远只能报 NO_FEEDBACK，无法真正验收“车会动”。
    """
    print("\n--- 无 0x1E 时用 0x0A 位移完成验收 ---")
    with RobotSession(consumes_chassis=True, emits_motion=False) as session:
        result = session.run(enable_drive=True, confirm_motion=True)
        loop = result.phases.get("control_loopback", {})
        check("无 0x1E 但有坐标时仍允许运动", loop.get("skipped") is False, str(loop)[:140])
        check("靠位移判定为已响应", loop.get("responded_by_position") is True, str(loop.get("observations")))
        check("证据源标注为 0x0A 位移", loop.get("evidence") == "0x0A-position", str(loop.get("evidence")))
        check("位移量已记录", float(loop.get("displacement_m", 0.0)) > 0.01, str(loop.get("displacement_m")))
        check("自检退出码 0", result.exit_code == ps.EXIT_OK, str(result.exit_code))

    # 同样的机器人但未补消费者：位移不会增长，必须报 STUCK 而不是“无法验证”
    with RobotSession(consumes_chassis=False, emits_motion=False) as session:
        result = session.run(enable_drive=True, confirm_motion=True)
        loop = result.phases.get("control_loopback", {})
        check(
            "无回读且位移不增长时判定为未响应",
            loop.get("verdict") == "NO_MOTION_RESPONSE",
            str(loop.get("verdict")),
        )
        check("位移证据量级入报告", "displacement_m" in loop, str(list(loop)))
        check("退出码指出无运动响应", result.exit_code == ps.EXIT_NO_MOTION_RESPONSE, str(result.exit_code))


class _Bridge:
    def __init__(self, *, active: bool, errors: dict | None = None) -> None:
        self.active = active
        self.module_errors = errors or {}


def test_connect_failure_is_reported() -> None:
    print("\n--- 连不上时的报告 ---")
    saved = sys.modules.get("serial")
    fake = types.ModuleType("serial")

    def boom(*a, **k):
        raise OSError("Access is denied")

    fake.Serial = boom  # type: ignore[attr-defined]
    sys.modules["serial"] = fake
    try:
        result = ps.run_polar_selftest(port="COM7", seconds=0.5, log=lambda *a, **k: None)
    finally:
        if saved is not None:
            sys.modules["serial"] = saved
        else:
            sys.modules.pop("serial", None)
    check("端口被占用时退出码为连接失败", result.exit_code == ps.EXIT_CONNECT_FAILED, str(result.exit_code))
    check("未连接", result.connected is False)
    check("报告里有可行动原因", bool(result.problems), str(result.problems)[:120])


def test_report_is_written_and_parseable() -> None:
    print("\n--- 报告落盘 ---")
    out = os.path.join(os.environ.get("TEMP", "/tmp"), f"polar_selftest_report_{int(time.time()*1e6)}.json")
    with RobotSession(consumes_chassis=True) as session:
        result = session.run()
        written = ps.write_selftest_report(__import__("pathlib").Path(out), result)
        check("报告文件已生成", os.path.exists(written))
        data = json.loads(open(written, encoding="utf-8").read())
        check("报告含五个阶段", len(data["phases"]) == 5, str(list(data["phases"])))
        check("报告声明非权威", data["authoritative"] is False)
        check("报告含已探测端口", "detected_ports" in data["phases"]["connect"])
        os.remove(written)


def main() -> int:
    tests = [
        test_readonly_by_default,
        test_unfinished_selftest_duration_is_not_negative_epoch,
    test_finished_selftest_duration_matches_the_finish_time,
    test_single_switch_is_not_enough,
        test_motion_loopback_patched_robot,
        test_motion_loopback_unpatched_robot,
        test_motion_gate_refusals,
        test_position_fallback_verifies_motion,
        test_connect_failure_is_reported,
        test_report_is_written_and_parseable,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"FAILED {len(FAILED)}: {', '.join(FAILED)}")
        return 1
    print("ALL POLAR_SELFTEST BEHAVIOUR TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
