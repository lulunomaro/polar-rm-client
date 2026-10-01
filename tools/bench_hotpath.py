"""非视觉热路径基准：把 60Hz 逻辑拍里最贵的几段单独量出来。

为什么要有这个文件
------------------
优化"感觉更快"是没用的，要有能复现的数字。这个脚本在**不改任何被冻结的
视觉文件**的前提下，把后端/模型层的高频路径单独拉出来计时，输出
"每壁钟秒的 CPU 成本"，这样任何一次改动都能立刻回答两个问题：

1. 这次改动到底省了多少？
2. 有没有把省下来的又花回去？

量的是这些（都不碰画面）：
- ``manager.tick_polar_link``  —— 60Hz 逻辑拍的编排主体
- ``robot_roles.limits_for``  —— 兵种限幅表
- ``dt7_link.load_profile``   —— 读 profile 文件
- ``RobotState.tick``         —— 60Hz 状态推进
- ``protocol.crc16``          —— 0x5A / 0xA5 帧校验

跑法::

    python tools/bench_hotpath.py             # 全部
    python tools/bench_hotpath.py --json      # 便于 diff
    python tools/bench_hotpath.py --reps 2000
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 逻辑层真实频率（polar_app.py 的 on_tick 门控）
LOGIC_HZ = 60.0
CONTROL_HZ = 75.0


def _timeit(fn, reps: int, warmup: int | None = None) -> dict:
    """返回 fn 的中位/平均/最小单次耗时（微秒）与每秒成本。"""
    if warmup is None:
        warmup = max(3, reps // 10)
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e6)
    samples.sort()
    return {
        "reps": reps,
        "median_us": round(statistics.median(samples), 4),
        "mean_us": round(statistics.fmean(samples), 4),
        "min_us": round(samples[0], 4),
        "p95_us": round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 4),
    }


def bench_robot_roles(reps: int) -> dict:
    from rm_client import robot_roles

    r = _timeit(lambda: robot_roles.limits_for(1), reps)
    r["per_second_ms"] = round(r["median_us"] * LOGIC_HZ / 1000.0, 4)
    r["note"] = "60Hz 逻辑拍；若每拍调用多次则按倍数放大"
    return r


def bench_dt7_profile(reps: int) -> dict:
    from rm_client.backend import dt7_link

    r = _timeit(lambda: dt7_link.load_profile(), reps)
    r["per_second_ms"] = round(r["median_us"] * LOGIC_HZ / 1000.0, 4)
    r["note"] = "profile 来自磁盘；缓存后应降到近 0"
    return r


def bench_model_tick(reps: int) -> dict:
    from rm_client.model import RobotState

    state = RobotState()
    state.mock_simulation_enabled = True
    state.official_state_active = False
    r = _timeit(lambda: state.tick(1.0 / LOGIC_HZ), reps)
    r["per_second_ms"] = round(r["median_us"] * LOGIC_HZ / 1000.0, 4)
    r["note"] = "仅 LabDebug 场景走主体；官方场景第一行早退"
    return r


def bench_crc16(reps: int) -> dict:
    from rm_client.protocol import crc16

    payload = bytes(range(160))
    r = _timeit(lambda: crc16(payload), reps)
    r["per_second_ms"] = round(r["median_us"] * 1360.0 / 1000.0, 4)
    r["note"] = "按 0x5A 实况下行 1360 帧/秒折算"
    return r


def bench_polar_decode(reps: int) -> dict:
    """真实 14 段规格下的 decode_with_consumed 单帧成本。"""
    from rm_client.backend import polar_link

    codec = polar_link.PolarFrameCodec()
    builder = getattr(polar_link, "build_test_frame", None)
    if builder is None:
        return {"skipped": "polar_link 没有可用的构帧助手，跳过"}
    frame = builder()
    r = _timeit(lambda: codec.decode_with_consumed(frame), reps)
    r["per_second_ms"] = round(r["median_us"] * 1360.0 / 1000.0, 4)
    r["note"] = "0x5A 单帧解码；CRC 占其中约 90%"
    return r


BENCHES = {
    "robot_roles.limits_for": bench_robot_roles,
    "dt7_link.load_profile": bench_dt7_profile,
    "RobotState.tick": bench_model_tick,
    "protocol.crc16(160B)": bench_crc16,
    "polar_link.decode_with_consumed": bench_polar_decode,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="非视觉热路径基准")
    parser.add_argument("--reps", type=int, default=1500, help="每个基准的重复次数")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于 diff）")
    parser.add_argument("--only", default="", help="只跑名字里含该子串的基准")
    args = parser.parse_args(argv)

    results: dict[str, dict] = {}
    for name, fn in BENCHES.items():
        if args.only and args.only not in name:
            continue
        try:
            results[name] = fn(args.reps)
        except Exception as exc:  # noqa: BLE001
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    width = max(len(n) for n in results) if results else 20
    print("=" * 78)
    print(f"非视觉热路径基准（逻辑层 {LOGIC_HZ:.0f}Hz，每项 {args.reps} 次）")
    print("=" * 78)
    print(f"{'基准':<{width}}  {'中位(µs)':>10}  {'P95(µs)':>9}  {'每秒(ms)':>10}")
    print("-" * 78)
    for name, data in results.items():
        if "skipped" in data or "error" in data:
            print(f"{name:<{width}}  {data.get('skipped') or data.get('error')}")
            continue
        print(
            f"{name:<{width}}  {data['median_us']:>10.3f}  {data['p95_us']:>9.3f}  "
            f"{data['per_second_ms']:>10.4f}"
        )
    print("-" * 78)
    for name, data in results.items():
        if "note" in data:
            print(f"  {name}: {data['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
