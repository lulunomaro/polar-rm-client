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


def _build_polar_frame(segment_id: int, body: bytes, timestamp: int = 0x1234ABCD) -> bytes:
    """按 0x5A 帧格式造一帧真帧。

    之前这一项是**跳过的**，理由写着"polar_link 没有可用的构帧助手"。
    结果就是：**全链路里唯一一个每秒跑 1360 次的东西从来没被测过**，
    而旁边每秒跑 60 次的 `RobotState.tick` 反而有数字。基准表最该有的那行是空的。

    不去 `polar_link` 里加 `build_test_frame`：那是运行时代码，为了基准往里塞
    造帧助手属于把测试设施混进产品。这里就地按 `crc8` / `crc16` 与
    `_SEGMENT_FORMATS` 造，构造与真实编帧器共用同一套公式 ——
    造错了会表现为 CRC 不符而被丢弃，所以下面还有一道"造出来的帧必须解得出来"
    的自检 —— 否则基准测的是"全丢弃"那条便宜路径。
    """
    from rm_client.backend import polar_link
    from rm_client.protocol import crc8, crc16

    seg_len = 4 + len(body)
    head = bytes((polar_link.POLAR_SOF, seg_len & 0xFF, segment_id & 0xFF, 0x00))
    head = head[:3] + bytes((crc8(head[:3]),))
    frame = head + (timestamp & 0xFFFFFFFF).to_bytes(4, "little") + body
    return frame + crc16(frame).to_bytes(2, "little")


def _segment_body(segment_id: int):
    """按规格格式串算出该段 body 应有的字节数，全零填充。"""
    import struct

    from rm_client.backend import polar_link

    fmt = polar_link._SEGMENT_FORMATS.get(segment_id)
    if fmt is None:
        return None
    return bytes(struct.calcsize(fmt))


def bench_polar_decode(reps: int) -> dict:
    """真实规格下的 ``decode_with_consumed`` 单帧成本。"""
    from rm_client.backend import polar_link

    segment_id = polar_link.SEG_ROBOT_MOTION
    body = _segment_body(segment_id)
    if body is None:
        return {"skipped": f"规格里没有 {segment_id:#04x}，跳过"}
    frame = _build_polar_frame(segment_id, body)

    codec = polar_link.PolarFrameCodec()

    # 先确认这一帧真的能被解出来：解不出的话下面测的是"全丢弃"那条便宜路径，
    # 数字会偏小而且不反映真实成本。
    segments, consumed = codec.decode_with_consumed(frame)
    if not segments:
        return {"skipped": "造的帧解不出来（CRC 或长度对不上），基准无意义"}

    codec = polar_link.PolarFrameCodec()      # 重置统计，避免上面那次计入
    r = _timeit(lambda: codec.decode_with_consumed(frame), reps)
    r["per_second_ms"] = round(r["median_us"] * 1360.0 / 1000.0, 4)
    r["segment_id"] = f"{segment_id:#04x}"
    r["body_bytes"] = len(body)
    r["frame_bytes"] = len(frame)
    r["consumed"] = consumed
    r["note"] = "0x5A 单帧解码（实测每秒 1360 帧）"
    return r


def bench_polar_tick(reps: int) -> dict:
    """``BackendManager.tick_polar_link`` 每逻辑拍的**链路活跃**成本。

    这是 60Hz 那一拍里最重的一段：排空段 -> 落 overlay -> 读 25 项设置 ->
    门控 -> 编上行帧。它比 ``RobotState.tick`` 重得多，而此前完全没有数字。

    **每拍都要重新喂段**，不能只在开头喂一次：`tick_polar_link` 第一行就把
    ``_rx`` 排空了，只喂一次的话后面几千次量到的全是"这拍没段"的空路径 ——
    数字偏小，而报告里却写成"链路活跃时的成本"。
    这是基准自己骗自己的典型：测的不是它声称在测的东西。
    第一版就是这么写的，30.5us 那个数字其实大半是空路径。

    现在每次调用前把预解好的段塞回去；塞的动作在计时之内（诚实），
    段本身提前解好（不重复计入 CRC）。
    """
    import tempfile

    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState, build_default_registry

    manager = BackendManager(workspace=tempfile.mkdtemp(prefix="bench_polar_"),
                             scenario_key="OfficialEvent", client_id="3")
    try:
        state = RobotState()
        registry = build_default_registry()

        ready: list = []
        for segment_id in (0x0A, 0x1F, 0x02, 0x1E, 0x02):
            body = _segment_body(segment_id)
            if body is None:
                continue
            segments, _consumed = manager.polar.codec.decode_with_consumed(
                _build_polar_frame(segment_id, body))
            ready.extend(segments)
        if not ready:
            return {"skipped": "规格里找不到可解的段，跳过"}

        def one_tick():
            manager.polar._rx.extend(ready)
            return manager.tick_polar_link(state, registry)

        r = _timeit(one_tick, reps)
        r["per_second_ms"] = round(r["median_us"] * 60.0 / 1000.0, 4)
        r["segments_per_tick"] = len(ready)
        r["note"] = f"60Hz 逻辑拍，每拍喂 {len(ready)} 段（链路活跃）"
        return r
    finally:
        manager.shutdown()


def bench_polar_tick_idle(reps: int) -> dict:
    """同一拍但**这拍没有段** —— 差值就是处理那几段的成本。

    两条一起看才说得清"排空段"占多少。只报活跃那一条会把打开链路的
    成本全算进"每拍的固定开销"里。
    """
    import tempfile

    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState, build_default_registry

    manager = BackendManager(workspace=tempfile.mkdtemp(prefix="bench_polar_idle_"),
                             scenario_key="OfficialEvent", client_id="3")
    try:
        state = RobotState()
        registry = build_default_registry()
        r = _timeit(lambda: manager.tick_polar_link(state, registry), reps)
        r["per_second_ms"] = round(r["median_us"] * 60.0 / 1000.0, 4)
        r["note"] = "同一拍但这拍没有段；与上一项的差值 = 处理那些段的成本"
        return r
    finally:
        manager.shutdown()


BENCHES = {
    "robot_roles.limits_for": bench_robot_roles,
    "dt7_link.load_profile": bench_dt7_profile,
    "RobotState.tick": bench_model_tick,
    "protocol.crc16(160B)": bench_crc16,
    "polar_link.decode_with_consumed": bench_polar_decode,
    "manager.tick_polar_link": bench_polar_tick,
    "manager.tick_polar_link(idle)": bench_polar_tick_idle,
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
