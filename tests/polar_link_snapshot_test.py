"""``polar_link`` 诊断快照：两条写入路径必须给出同一组键。

问题
----
``state.backend_stats["polar_link"]`` 这份诊断快照在 manager.py 里曾被写了
两遍：``tick_polar_link`` 每帧写一次，``stats()`` 又整份重建一次（后者由
``apply_updates`` 在收到官方 MQTT 消息时调用，并**覆盖**前者）。

两份已经漂移：``tick_polar_link`` 那份多一个 ``"motion"`` 键。于是每收到
一条官方 MQTT 消息，刚写进去的 ``motion`` 就被丢掉（实测 True → False）。

为什么这个键重要：``motion`` 是自转/运动回读闭环的判据 ——
AGENT.md 第 4 节不变量 9 要求"自转只能走上行 0x04，且必须用 0x1F 回读闭环"。
把 ``motion`` 丢掉，现场就无法回答"车到底动没动"，而这正是那份快照存在的
理由。它是**诊断数据**，丢它不会崩，但会让现场排查彻底失明。

修法：把两份合并成 ``BackendManager._polar_link_stats`` 单一来源。
本文件钉住"两条路径的键集恒等"，防止将来再加一份。

跑法：``python tests/polar_link_snapshot_test.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend.manager import BackendManager  # noqa: E402
from rm_client.model import RobotState, build_default_registry  # noqa: E402

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


class _FakeControl:
    """最小控制替身：tick_polar_link 只需要它存在。"""

    class last_command:  # noqa: N801
        vx, vy, wz = 0.0, 0.0, 0.0


def _make_manager() -> BackendManager:
    manager = BackendManager()
    manager.polar.port = "Stub"
    manager.polar.start()
    return manager


def test_both_paths_expose_same_keys() -> None:
    manager = _make_manager()
    state = RobotState()
    registry = build_default_registry()

    manager.tick_polar_link(state, registry, _FakeControl())
    from_tick = state.backend_stats.get("polar_link")
    from_stats = manager.stats().get("polar_link")

    check("tick_polar_link 写出了快照", isinstance(from_tick, dict), str(type(from_tick)))
    check("stats() 产出了快照", isinstance(from_stats, dict), str(type(from_stats)))
    if not isinstance(from_tick, dict) or not isinstance(from_stats, dict):
        return

    only_tick = set(from_tick) - set(from_stats)
    only_stats = set(from_stats) - set(from_tick)
    check(
        f"两条路径的键集恒等（各 {len(from_tick)} 个键）",
        not only_tick and not only_stats,
        f"只有 tick 有: {sorted(only_tick)}；只有 stats 有: {sorted(only_stats)}",
    )


def test_required_keys_present_everywhere() -> None:
    required = (
        "link_alive", "segment_counts", "dropped_segments", "decode",
        "authority", "motion", "timestamp_roll_detected",
        "connected", "frames_sent", "zero_frames_sent", "send_hz",
        "last_error_code", "detected_ports",
    )
    manager = _make_manager()
    state = RobotState()
    manager.tick_polar_link(state, build_default_registry(), _FakeControl())
    from_tick = state.backend_stats.get("polar_link") or {}
    from_stats = manager.stats().get("polar_link") or {}

    for key in required:
        check(
            f"必需键 '{key}' 在 tick 快照里",
            key in from_tick,
            f"缺了（现有：{sorted(from_tick)[:8]}...）",
        )
        check(f"必需键 '{key}' 在 stats 快照里", key in from_stats, "缺了")


def test_motion_survives_apply_updates() -> None:
    manager = _make_manager()
    state = RobotState()
    state.backend_stats = {}
    registry = build_default_registry()

    manager.tick_polar_link(state, registry, _FakeControl())
    before = state.backend_stats.get("polar_link", {})
    check("tick 之后 motion 在快照里", "motion" in before, f"keys={sorted(before)[:8]}...")

    # 模拟"收到一条官方 MQTT 消息"——这是 apply_updates 唯一会重写快照的路径。
    runtime_stats = manager.stats()
    for key, value in state.backend_stats.items():
        if key not in runtime_stats:
            runtime_stats[key] = value
    state.backend_stats = runtime_stats

    after = state.backend_stats.get("polar_link", {})
    check(
        "被 stats() 覆盖整份快照后，motion 仍然在",
        "motion" in after,
        f"覆盖后 keys={sorted(after)[:8]}... —— 这就是原来那条静默丢数据",
    )
    check(
        "被覆盖后 segment_counts / decode / authority 也都还在",
        all(k in after for k in ("segment_counts", "decode", "authority", "dropped_segments")),
        f"keys={sorted(after)}",
    )


def test_injected_stats_are_used() -> None:
    manager = _make_manager()
    sentinel_motion = {"detail": "injected", "moving": True}
    sentinel_auth = {"state": "ARMED", "reason": "injected"}
    snapshot = manager._polar_link_stats(
        arbiter_stats=sentinel_auth, motion_stats=sentinel_motion
    )
    check("传入的 motion 被原样采用", snapshot["motion"] is sentinel_motion, str(snapshot["motion"]))
    check("传入的 authority 被原样采用", snapshot["authority"] is sentinel_auth, str(snapshot["authority"]))


def test_default_path_is_self_consistent() -> None:
    manager = _make_manager()
    snap = manager._polar_link_stats()
    check(
        "不传参时 motion 是真统计（不是空）",
        isinstance(snap.get("motion"), dict) and bool(snap["motion"]),
        str(snap.get("motion")),
    )
    check(
        "不传参时 authority 是真统计（不是空）",
        isinstance(snap.get("authority"), dict) and bool(snap["authority"]),
        str(snap.get("authority")),
    )


def main() -> int:
    tests = [
        test_both_paths_expose_same_keys,
        test_required_keys_present_everywhere,
        test_motion_survives_apply_updates,
        test_injected_stats_are_used,
        test_default_path_is_self_consistent,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"polar_link 快照单一来源：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"polar_link 快照单一来源：全部通过（{PASSED} 项）")
    print("两条写入路径键集恒等，motion 不会再被覆盖丢掉。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
