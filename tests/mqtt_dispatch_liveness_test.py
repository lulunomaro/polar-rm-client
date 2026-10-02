"""`_HookDispatch` 的**活性**：把第 14 轮那 6 条"挂死型检出"变成带标签的断言。

跑法：``python tests/mqtt_dispatch_liveness_test.py``

这一轮要补的东西
----------------
第 14 轮对 `rm_client/backend/mqtt_backend.py` 做变异验证时，有 6 个决策点
**只被"挂死"检出，没有一条断言接住**：

    DP1  _start_locked    L75   if    self._thread is not None or self._closed
    DP2  flush            L101  while (sink._event_queue or self._inflight) and time.monotonic() < deadline
    DP3  _run             L129  while True
    DP4  _run             L140  if    not batch and self._closed
    DP5  _dispatch_alive  L240  and   thread is not None
    DP6  _dispatch_alive  L240  and   thread.is_alive()

当时的理由是"依赖真实并发时序，写死容易 flaky"。这个理由只对了一半。

    这些实现**没有一个会真的永久阻塞**。`flush()` 自带 `timeout`，
    `submit()` 只做一次 `deque.append`，`_run()` 的等待是 `cv.wait(0.2)`
    的轮询而不是无界 `join()`。变异的现场表现是**事件再也送不到 hook**，
    于是每一个 `flush_events(timeout=10.0)` 都白白耗满那 10 秒 ——
    一个套件里攒着几十次调用，整套件就撞穿墙钟预算、被记成"挂死"。

    所以**挂死是症状，不是不可观测的行为**。把观察方式换掉就能钉住：
    给每一次调用套一个有界外壳（`observe()`），让它把结局分成
    **正常返回 / 超时 / 抛异常** 三类分别记账。这样"送不到"就是一条
    普通的 `[FAIL]`，而不是整套件从报告里消失。

    本文件里的**每一条断言都带 `[DPn]` 标签**，所以任何一个决策点被改坏，
    都能一眼看出是哪一条红了 —— 不再是"某个套件整体变红 20 条"。

超时值怎么定的（不许拍脑袋）
---------------------------
预算不是猜的。先在**正确实现**上实测每个场景的耗时（9 遍取最大值）：

    首条事件送达 flush          0.33 ms
    注册突发 x4                 0.14 ms
    三轮投递                    0.14 ms
    shutdown 排空 600 条 + join 11.72 ms   ← 最慢的一个
    shutdown 空队列 join         0.48 ms
    闸住的 flush（含那 0.25s 反向检查）    ~250 ms
    到点放弃的 flush（deadline=0.5s）       ~530 ms

最坏的正常耗时是 **11.72 ms**。定阈值的规则是"实测最大值 x 8，再加一个给
CI 抖动留余量的地板"，落成三个常量：

    BUDGET          = 2.0 s  = 170 x 最坏正常耗时（11.72 ms）
    BUDGET_GATED    = 2.0 s  =    8 x 闸住场景的正常耗时（~250 ms）
    BUDGET_DEADLINE = 5.0 s  = 9.4 x 到点场景的正常耗时（~530 ms）

注意 `flush(timeout=…)` 的**参数**是另一回事：它是被测输入，本文件故意传
**小**的值（1.0 s / 0.5 s）。这样即便实现被改坏、事件永远送不到，
单次 `flush` 也只烧 1 秒 —— 一个"被改坏时也会自己结束"的测试套件，
才敢拿来钉活性。`observe()` 的工作线程是 daemon 的，即使万一真卡住，
解释器退出时也会把它收掉。

唯一一处本文件**主动摘掉**的东西：atexit 上的 `_flush_hook_dispatch`
--------------------------------------------------
`mqtt_backend` 在 import 时 `atexit.register(_flush_hook_dispatch)`，退出前
会对每个已登记的 sink 调一次 `sink.flush_events(timeout=0.5)`。

DP2 那个变异（`while` 守卫取反）会让 `flush` 进入一种**真的永不返回**的状态：

    while (not ((queue or inflight) and time.monotonic() < deadline)):

deadline 一过、队列还非空时，`queue and t < deadline` 恒为 False，取反恒为
True，两项都不再变化 —— 循环永不退出。而 atexit 是**无条件**调用 `flush`
的，调用方给不给出路，于是**任何**跑这个模块的进程都会在退出时永久挂住，
测试运行器只能记 TIMEOUT。

说清楚：这**不是生产缺陷**。正确实现的 `while (queue or inflight) and t < d`
在 deadline 到达时一定会退出，`flush` 永远在 `timeout` 之内返回；上面那个
死循环只有把守卫取反才会出现。但它对本轮的意义很直接 ——
它证明"挂死"在这 6 条上的来源是**两种不同的东西**：

  * DP1/DP3/DP4/DP5/DP6：事件送不到，每次 `flush` 白白烧满 timeout，
    几十次累加起来撞穿套件的墙钟预算。表现是"慢"，本质是可断言的。
  * DP2：真的进死循环。表现是"不返回"，只能在进程层面躲开。

既然本轮的目标是"把挂死变成可断言的观察"，本文件就必须做到：
**被测实现坏掉时，这个套件自己会结束。** 所以：

  * 入口处 `atexit.unregister(_flush_hook_dispatch)`，**且不再装回去**
    （装回去等于把挂死又请回来 —— 试过，进程照样永不退出）；
  * 收尾前把全局分发器上所有 sink 的积压倒空，不给 atexit 留活；
  * 其余一切观察都走 `observe()` 的有界外壳。

不装回去是安全的：这个进程是一次性测试运行，不是生产进程，
退出收尾钩子对它没有价值。

代价：本文件**不覆盖**进程退出路径本身。那条路径本来就标着
`# pragma: no cover - 解释器退出路径`，也不在本轮这 6 个决策点里。

实测值会在运行本文件时以 `[INFO]` 打印出来，可以逐条核对预算。
"""

from __future__ import annotations

import atexit
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rm_client.backend.mqtt_backend import (  # noqa: E402
    _HOOK_DISPATCH,
    _HookDispatch,
    RmMqttBackend,
)
from rm_client.backend import mqtt_backend as _mb  # noqa: E402

# 退出路径的收尾钩子见文件头：它无条件调用被测的 flush，DP2 变异会让进程
# 在 atexit 永久挂住。**只摘不装**：这是一个一次性测试进程，退出收尾钩子对它
# 没有价值，而装回去等于把挂死原样请回来。
_ATEXIT_HOOK = getattr(_mb, "_flush_hook_dispatch", None)
if _ATEXIT_HOOK is not None:
    atexit.unregister(_ATEXIT_HOOK)

FAILURES: list[str] = []
PASSED = 0

#: 最坏的正常场景实测 11.72ms；x8 后不足 0.1s，这里给到 2.0s 地板（约 170 倍）。
BUDGET = 2.0
#: 闸住场景的正常耗时约 250ms（其中 250ms 是本文件**故意**等的反向检查）。
BUDGET_GATED = 2.0
#: 到点场景的正常耗时约 530ms（其中 500ms 是被测的 deadline）。
BUDGET_DEADLINE = 5.0
#: 被测的 `flush(timeout=…)` 参数：故意小，保证实现坏掉时也不烧墙钟。
FLUSH_TIMEOUT = 1.0
FLUSH_DEADLINE = 0.5
#: `_run` 单批上限是 512（`len(batch) < 512`），用它逼出"分批 + 关停排空"。
BATCH_CAP = 512

# --------------------------------------------------------------------------
# 三分类观察外壳
# --------------------------------------------------------------------------
RETURNED = "正常返回"
TIMEOUT = "超时"
RAISED = "抛异常"


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


class Observation:
    """一次有界调用的完整结局。三个桶互斥，缺一不可。"""

    __slots__ = ("outcome", "elapsed", "value", "exc")

    def __init__(self, outcome: str, elapsed: float,
                 value: Any = None, exc: BaseException | None = None) -> None:
        self.outcome = outcome
        self.elapsed = elapsed
        self.value = value
        self.exc = exc

    def describe(self) -> str:
        extra = ""
        if self.outcome == RAISED:
            extra = f" exc={type(self.exc).__name__}: {self.exc}"
        return (f"outcome={self.outcome} elapsed={self.elapsed * 1000:.1f}ms"
                f" value={self.value!r}{extra}")

    def is_return(self, expected: Any = True) -> bool:
        """正常返回、且返回值就是 ``expected``。"""
        return self.outcome == RETURNED and self.value == expected


def observe(fn: Callable[..., Any], budget: float, *args: Any, **kwargs: Any) -> Observation:
    """在 ``budget`` 秒的硬墙钟内跑 ``fn``，把结局分成三桶。

    工作线程是 daemon 的：万一实现被改坏、这一调用真的不返回，
    解释器退出时也会把它收掉，**不会把测试进程钉住** ——
    这正是本文件要避免的"自己制造挂死"。
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def runner() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
            box["outcome"] = RETURNED
        except BaseException as exc:          # noqa: BLE001 - 三分类里必须含异常
            box["exc"] = exc
            box["outcome"] = RAISED
        finally:
            done.set()

    worker = threading.Thread(target=runner, name="mqtt-liveness-observer", daemon=True)
    started = time.perf_counter()
    worker.start()
    finished = done.wait(budget)
    elapsed = time.perf_counter() - started
    if not finished:
        return Observation(TIMEOUT, elapsed)
    return Observation(box["outcome"], elapsed, box.get("value"), box.get("exc"))


def note(label: str, value: Any) -> None:
    print(f"[INFO] {label}: {value}")


# --------------------------------------------------------------------------
# 测试替身：_HookDispatch 真正用到的那几个成员
# --------------------------------------------------------------------------


class Sink:
    """`_HookDispatch` 需要的最小 sink：`_event_queue` 与 `_deliver_event`。"""

    def __init__(self, hook: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
        self._event_queue: deque = deque()
        self.hook = hook
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def _deliver_event(self, event: str, payload: dict[str, Any]) -> bool:
        self.seen.append((event, payload))
        if self.hook is not None:
            self.hook(event, payload)
        return True


# ======================================================================
# DP1  _start_locked  L75  if self._thread is not None or self._closed
# ======================================================================


def test_dp1_register_starts_one_live_dispatcher() -> None:
    """`register()` 必须把分发线程真的拉起来。

    现场表现：`_emit_event` 每次都"排队成功"，队列只涨不消，
    `flush_events()` 每次都耗满 timeout 并返回 False，field_log 里一条事件都没有，
    而程序看上去一切正常 —— 没有异常、没有日志、只有"日志停了"。
    """
    print("\n[DP1] _start_locked: 首次注册必须把分发线程拉起来并真正送达")
    dispatch = _HookDispatch()
    sink = Sink()
    try:
        dispatch.register(sink)
        check("[DP1] register 之后 _thread 不是 None",
              dispatch._thread is not None, "分发线程从没被创建")
        check("[DP1] 而且那个线程是活的",
              dispatch._thread is not None and dispatch._thread.is_alive(),
              "线程对象在，但没跑起来")

        before = dispatch.delivered
        submitted = dispatch.submit(sink, sink._event_queue, ("referee_rx", {"n": 1}))
        check("[DP1] submit 排队成功", submitted is True, submitted)

        obs = observe(dispatch.flush, BUDGET, sink, FLUSH_TIMEOUT)
        note("DP1 首条事件 flush 耗时", f"{obs.elapsed * 1000:.2f}ms")
        check("[DP1] flush 落进'正常返回'桶（不是超时）",
              obs.outcome == RETURNED, obs.describe())
        check("[DP1] flush 返回 True（队列真的排空了）",
              obs.is_return(True), obs.describe())
        check("[DP1] 事件送到了 hook", sink.seen == [("referee_rx", {"n": 1})], sink.seen)
        check("[DP1] delivered 计数 +1", dispatch.delivered == before + 1,
              (before, dispatch.delivered))
    finally:
        dispatch.shutdown()


def test_dp1_no_second_thread() -> None:
    """`self._thread is not None` 这一支：已经在跑就别再拉第二条。

    现场表现：场景切换 / `set_client_id` 每调一次 `start()` 就多一条线程，
    两条线程同时往同一个 `on_message` 回调里塞包，ClientStatus online 被发两遍。
    """
    print("\n[DP1] _start_locked: 线程已在运行时不许再造第二条")
    dispatch = _HookDispatch()
    sinks = [Sink() for _ in range(4)]
    # 前面几个用例的线程可能还在收尾，所以按 ident 做增量，而不是按名字数全局。
    before = {t.ident for t in threading.enumerate()}
    try:
        dispatch.register(sinks[0])
        first = dispatch._thread
        after_first = {t.ident for t in threading.enumerate()}
        check("[DP1] 首次注册只新起了一条线程",
              len(after_first - before) == 1, sorted(after_first - before))
        check("[DP1] 首次注册起了一条线程",
              first is not None and first.is_alive(), "没起来")
        for sink in sinks:
            dispatch.register(sink)
        after_again = {t.ident for t in threading.enumerate()}
        check("[DP1] 再注册 4 次，一条新线程都没有", after_again == after_first,
              sorted(after_again - after_first))
        check("[DP1] 再注册 4 次，线程对象没换",
              dispatch._thread is first, (id(first), id(dispatch._thread)))
    finally:
        dispatch.shutdown()
        if dispatch._thread is not None:
            dispatch._thread.join(BUDGET)


def test_dp1_respects_closed() -> None:
    """`self._closed` 这一支：已关停的分发器不许被 `register()` 复活。

    `shutdown()` 之后 `_run` 会退出；这时再来一个 backend 并 `register()`，
    如果又被拉起线程，"关停后不再投递"这条语义就没了 —— `atexit` 的收尾
    `flush_events` 反而会把新事件写进已经关掉的日志。
    """
    print("\n[DP1] _start_locked: 已 shutdown 的分发器不会被 register 复活")
    closed = _HookDispatch()
    closed.shutdown()
    check("[DP1] shutdown 之后 _closed 为 True", closed._closed is True, closed._closed)
    closed.register(Sink())
    check("[DP1] register 不会给已关停的分发器重新起线程",
          closed._thread is None, closed._thread)

    fresh = _HookDispatch()
    try:
        fresh.register(Sink())
        check("[DP1] 对照：没关停的同一个分发器仍然起线程",
              fresh._thread is not None and fresh._thread.is_alive(), "没起来")
    finally:
        fresh.shutdown()


# ======================================================================
# DP2  flush  L101  while (queue or inflight) and time.monotonic() < deadline
# ======================================================================


def test_dp2_flush_blocks_until_drained() -> None:
    """`flush()` 必须**等**队列排空，而不是看一眼就返回。

    判据坏掉的后果是"立刻返回 False"：关停时 `flush_events()` 不再等，
    `stop()` 紧接着就清空队列，最后那一刻的 `mqtt_disconnect` 随队列一起被丢掉，
    证据链永远缺最后一环，而 `flush()` 自己没报任何错。
    """
    print("\n[DP2] flush: hook 被闸住时必须继续等，不能提前返回")
    dispatch = _HookDispatch()
    entered = threading.Event()
    release = threading.Event()
    sink = Sink(hook=lambda _e, _p: (entered.set(), release.wait(BUDGET_GATED * 5)))
    dispatch.register(sink)
    try:
        for index in range(3):
            dispatch.submit(sink, sink._event_queue, ("seq", {"index": index}))

        flushed = threading.Event()
        box: dict[str, Any] = {}

        def run_flush() -> None:
            try:
                box["value"] = dispatch.flush(sink, 2.0)
            finally:
                flushed.set()

        worker = threading.Thread(target=run_flush, name="audit-flush", daemon=True)
        worker.start()

        check("[DP2] hook 确实被调用了（分发线程活着）",
              entered.wait(BUDGET_GATED), "2 秒内没进 hook")
        # 到这一步事件还在投递中：队列非空、_inflight 非 0 —— 正确实现
        # **不可能**已经返回。这不是"赌它慢"，是前置条件已成立。
        check("[DP2] 闸住期间 flush 没有提前返回（它在等）",
              not flushed.wait(0.25), "flush 在 hook 还没跑完时就返回了")

        release.set()
        check("[DP2] 放行之后 flush 在预算内返回",
              flushed.wait(BUDGET_GATED), "放行后 flush 还不返回")
        check("[DP2] flush 返回 True", box.get("value") is True, box)
        check("[DP2] 3 条事件按 FIFO 全部送达",
              [p["index"] for _e, p in sink.seen] == [0, 1, 2], sink.seen)
    finally:
        release.set()
        dispatch.shutdown()


def test_dp2_flush_is_bounded_at_its_deadline() -> None:
    """`time.monotonic() < deadline` 这一支：闸门一直不放，flush 到点返回 False。

    这条把"有界"钉成断言：实现坏掉时的现场是**返回 False**，
    而不是永不返回。返回 False 之后由调用方决定重试还是记错，
    这一点必须和"卡死"区分开。
    """
    print("\n[DP2] flush: 闸门不放时到点返回 False（有界，不是卡死）")
    dispatch = _HookDispatch()
    entered = threading.Event()
    release = threading.Event()
    sink = Sink(hook=lambda _e, _p: (entered.set(), release.wait(BUDGET_DEADLINE * 6)))
    dispatch.register(sink)
    try:
        dispatch.submit(sink, sink._event_queue, ("seq", {"index": 0}))
        check("[DP2] hook 进入阻塞点", entered.wait(BUDGET), "没进 hook")
        obs = observe(dispatch.flush, BUDGET_DEADLINE, sink, FLUSH_DEADLINE)
        note("DP2 到点放弃耗时", f"{obs.elapsed * 1000:.1f}ms")
        check("[DP2] 到点正常返回、不是超时桶", obs.outcome == RETURNED, obs.describe())
        check("[DP2] 到点返回值是 False", obs.is_return(False), obs.describe())
        check("[DP2] 确实等满了 deadline（不是提前放弃）",
              obs.elapsed >= FLUSH_DEADLINE - 0.05, f"{obs.elapsed:.3f}s")
    finally:
        release.set()
        dispatch.shutdown()


# ======================================================================
# DP3  _run  L129  while True
# ======================================================================


def test_dp3_dispatcher_survives_many_rounds() -> None:
    """`while True` 这一支：处理完一批之后必须回到循环里，而不是退出。

    现场表现：第一批事件正常送达，之后的事件**永远躺在队列里**。
    队列不报错、计数不涨，只有 `flush_events()` 每次耗满 timeout 返回 False。
    """
    print("\n[DP3] _run while True: 多轮投递之后线程仍在、后续事件照样送达")
    dispatch = _HookDispatch()
    sink = Sink()
    dispatch.register(sink)
    try:
        for round_index in range(3):
            for item in range(4):
                dispatch.submit(sink, sink._event_queue,
                                ("seq", {"round": round_index, "item": item}))
            obs = observe(dispatch.flush, BUDGET, sink, FLUSH_TIMEOUT)
            note(f"DP3 第 {round_index + 1} 轮 flush 耗时", f"{obs.elapsed * 1000:.2f}ms")
            check(f"[DP3] 第 {round_index + 1} 轮 flush 正常返回",
                  obs.outcome == RETURNED, obs.describe())
            check(f"[DP3] 第 {round_index + 1} 轮 flush 返回 True",
                  obs.is_return(True), obs.describe())
        check("[DP3] 12 条全部送达", len(sink.seen) == 12, f"got={len(sink.seen)}")
        check("[DP3] 顺序严格 FIFO",
              [(p["round"], p["item"]) for _e, p in sink.seen]
              == [(r, i) for r in range(3) for i in range(4)], sink.seen)
        check("[DP3] 分发线程三轮之后仍然活着",
              dispatch._thread is not None and dispatch._thread.is_alive(),
              "线程在第一批之后就退出了")
    finally:
        dispatch.shutdown()


# ======================================================================
# DP4  _run  L140  if not batch and self._closed: return
# ======================================================================


def test_dp4_shutdown_still_drains_the_queue() -> None:
    """`not batch and self._closed` 的 `not batch` 那一支：关停时先把剩下的送完。

    `_run` 一批最多取 512 条（`len(batch) < 512`），这里投 `512 + 88` 条再关停，
    正确实现必须送两轮才退出。任何"处理完一批就 return"的改法都会丢掉 88 条，
    而现场表现是 `shutdown()` 正常返回、日志里安安静静少一截，没有任何报错。
    """
    print("\n[DP4] _run: shutdown 时先把队列剩下的送完，再退出")
    dispatch = _HookDispatch()
    sink = Sink()
    dispatch.register(sink)
    try:
        total = BATCH_CAP + 88
        for index in range(total):
            dispatch.submit(sink, sink._event_queue, ("seq", {"index": index}))
        dispatch.shutdown()
        check("[DP4] shutdown 置位 _closed", dispatch._closed is True, dispatch._closed)

        started = time.perf_counter()
        deadline = started + BUDGET_DEADLINE
        while len(sink.seen) < total and time.monotonic() < deadline:
            time.sleep(0.01)
        note("DP4 关停后排空耗时", f"{(time.perf_counter() - started) * 1000:.2f}ms")
        check(f"[DP4] {total} 条在关停后全部送达（超过单批 {BATCH_CAP} 上限）",
              len(sink.seen) == total, f"got={len(sink.seen)} 少了 {total - len(sink.seen)} 条")
        check("[DP4] 顺序严格 FIFO",
              [p["index"] for _e, p in sink.seen] == list(range(total)), "顺序被破坏")

        worker = dispatch._thread
        check("[DP4] 分发线程在排空之后退出",
              worker is not None and worker.join(BUDGET_DEADLINE) is None, "线程还在跑")
        check("[DP4] 线程确实不再存活",
              worker is not None and not worker.is_alive(), "线程还活着")
    finally:
        dispatch.shutdown()


def test_dp4_shutdown_with_empty_queue_exits_the_thread() -> None:
    """`_closed` 那一支：队列空 + 已关停 ⇒ 线程退出（不是永远轮询下去）。

    这条钉的是 `return` 的另一半：只测"排空后退出"的话，
    把它改成 `if self._closed: return`（队列还没排空就退）也能混过去。
    """
    print("\n[DP4] _run: 队列本来就空时 shutdown 也要让线程退出")
    dispatch = _HookDispatch()
    dispatch.register(Sink())
    worker = dispatch._thread
    try:
        check("[DP4] 先确认分发线程在跑",
              worker is not None and worker.is_alive(), "没跑")
        dispatch.shutdown()
        check("[DP4] 空队列 shutdown 后线程退出",
              worker is not None and worker.join(BUDGET) is None, "线程没退出")
    finally:
        dispatch.shutdown()


# ======================================================================
# DP5/DP6  _dispatch_alive  L240  thread is not None and thread.is_alive()
# ======================================================================


def test_dp5_dp6_dispatch_alive_reports_the_truth() -> None:
    """`_dispatch_alive()` 必须如实回答"分发线程在不在跑"。

    现场表现（两个方向各自要命）：
    * 线程活着却报 False → `_emit_event` 队列满时**同步**投递，
      把 75Hz 控制拍堵在一次同步写盘上；
    * 线程死了却报 True → 关停那一刻的 `mqtt_disconnect` 被"顺手记一笔丢弃"，
      现场证据链永远缺最后一环。
    """
    print("\n[DP5/DP6] _dispatch_alive: 活/死/没建 三种状态都要如实回答")
    backend = RmMqttBackend()
    saved = _HOOK_DISPATCH._thread
    stop = threading.Event()
    alive = threading.Thread(target=stop.wait, name="audit-live", daemon=True)
    never_started = threading.Thread(target=lambda: None, name="audit-never")
    finished = threading.Thread(target=lambda: None, name="audit-finished")
    finished.start()
    finished.join()
    try:
        _HOOK_DISPATCH._thread = alive
        alive.start()
        obs = observe(backend._dispatch_alive, BUDGET)
        check("[DP5/DP6] 线程活着 → True", obs.is_return(True), obs.describe())

        _HOOK_DISPATCH._thread = never_started
        obs = observe(backend._dispatch_alive, BUDGET)
        check("[DP6] 线程对象存在但从未 start → False", obs.is_return(False), obs.describe())

        _HOOK_DISPATCH._thread = finished
        obs = observe(backend._dispatch_alive, BUDGET)
        check("[DP6] 线程已经跑完退出 → False", obs.is_return(False), obs.describe())

        _HOOK_DISPATCH._thread = None
        obs = observe(backend._dispatch_alive, BUDGET)
        check("[DP5] 根本没有线程对象 → False（而不是抛 AttributeError）",
              obs.is_return(False), obs.describe())
    finally:
        stop.set()
        alive.join(1.0)
        _HOOK_DISPATCH._thread = saved


def test_dp5_queue_full_while_alive_is_accounted_not_delivered() -> None:
    """分发线程活着 + 队列满 ⇒ 记一笔丢弃，**不**同步投递。

    这是 `_dispatch_alive()` 判错的另一半现场：线程明明在跑，判据坏成 False
    就会退回同步投递，把控制拍堵在写盘上。做法是先用一个阻塞 hook 把全局分发
    线程钉住（队列就不会被搬空），再灌满队列，然后看有没有调用方线程上的
    hook 调用 —— 同步投递必然发生在调用方线程上。
    """
    print("\n[DP5] 分发线程活着时队列满 = 显式记账，不退回同步投递")
    backend = RmMqttBackend()
    blocker = RmMqttBackend()
    caller = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    calls: list[tuple[int, str]] = []

    def recording_hook(event: str, **_payload: Any) -> None:
        calls.append((threading.get_ident(), event))

    def blocking_hook(event: str, **_payload: Any) -> None:
        entered.set()
        release.wait(BUDGET_DEADLINE * 2)

    backend.event_hook = recording_hook
    backend.dropped_events = 0
    backend._reported_drops = 0
    blocker.event_hook = blocking_hook
    saved_max = _HOOK_DISPATCH.queue_max
    try:
        check("[DP5] 前提：分发线程活着",
              backend._dispatch_alive() is True, backend._dispatch_alive())
        _HOOK_DISPATCH.submit(blocker, blocker._event_queue, ("gate", {}))
        check("[DP5] 前提：分发线程已被闸在 hook 上", entered.wait(BUDGET), "没进 hook")

        _HOOK_DISPATCH.queue_max = 64
        for index in range(_HOOK_DISPATCH.queue_max + 120):
            backend._emit_event("referee_rx", index=index)
        check("[DP5] 队列被灌到上限",
              backend.events_pending() == _HOOK_DISPATCH.queue_max,
              backend.events_pending())
        check("[DP5] 被拒收的事件一条都没进 hook（不是同步投递）",
              not [c for c in calls if c[0] == caller], calls[:5])
        check("[DP5] dropped_events 明确记账", backend.dropped_events > 0,
              backend.dropped_events)
        check("[DP5] last_error 当场写明是队列满",
              "队列已满" in backend.last_error, repr(backend.last_error))
    finally:
        _HOOK_DISPATCH.queue_max = saved_max
        release.set()
        # 先把本用例灌进去的积压直接倒掉，再收尾。
        #
        # 为什么不"用 flush 收尾"：DP2 的变异（`while` 守卫取反）会让 flush 在
        # **deadline 已过且队列非空**时永远循环 —— `not (queue and t < deadline)`
        # 两项都不再变化，于是 flush 真的**永不返回**。这时候再拿 flush 收尾，
        # 就等于用一个坏掉的函数去收尾，整个套件被拖死；更糟的是 atexit 里
        # `_flush_hook_dispatch` 也会撞上同一段，整个进程永远不退出。
        # 那正是本轮要消灭的东西，所以这里宁可手动清队列。
        backend._event_queue.clear()
        blocker._event_queue.clear()
        observe(backend.flush_events, BUDGET, timeout=BUDGET)
        observe(blocker.flush_events, BUDGET, timeout=BUDGET)


def test_observer_shell_is_itself_bounded() -> None:
    """兜底：确认本文件用的观察外壳本身**不会**把套件钉住。

    一个故意永不返回的函数，在 `observe()` 里必须落进"超时"桶，
    而且观察线程是 daemon 的。哪天 `observe()` 自己坏了，
    "挂死"就会重新变成"整套件从报告里消失" —— 所以这条本身也是断言。
    """
    print("\n[SHELL] 观察外壳自身有界：永不返回 → 落进超时桶")
    obs = observe(lambda: threading.Event().wait(3600), 0.3)
    check("[SHELL] 永不返回的调用落进超时桶", obs.outcome == TIMEOUT, obs.describe())
    check("[SHELL] 超时桶里没有返回值", obs.value is None, obs.value)

    def boom() -> None:
        raise RuntimeError("probe")

    obs2 = observe(boom, BUDGET)
    check("[SHELL] 抛异常的调用落进抛异常桶", obs2.outcome == RAISED, obs2.describe())
    check("[SHELL] 异常原样带出来", isinstance(obs2.exc, RuntimeError), obs2.exc)

    obs3 = observe(lambda: "done", BUDGET)
    check("[SHELL] 正常返回落进正常返回桶", obs3.outcome == RETURNED, obs3.describe())
    check("[SHELL] 返回值带出来", obs3.value == "done", obs3.value)


def main() -> int:
    note("预算", f"BUDGET={BUDGET}s BUDGET_GATED={BUDGET_GATED}s "
                 f"BUDGET_DEADLINE={BUDGET_DEADLINE}s；被测 flush 参数 "
                 f"{FLUSH_TIMEOUT}s / {FLUSH_DEADLINE}s")
    try:
        test_dp1_register_starts_one_live_dispatcher()
        test_dp1_no_second_thread()
        test_dp1_respects_closed()
        test_dp2_flush_blocks_until_drained()
        test_dp2_flush_is_bounded_at_its_deadline()
        test_dp3_dispatcher_survives_many_rounds()
        test_dp4_shutdown_still_drains_the_queue()
        test_dp4_shutdown_with_empty_queue_exits_the_thread()
        test_dp5_dp6_dispatch_alive_reports_the_truth()
        test_dp5_queue_full_while_alive_is_accounted_not_delivered()
        test_observer_shell_is_itself_bounded()
    finally:
        # 兜底：把全局分发器上所有 sink 的积压倒空。
        #
        # 不这么做的话，某个变异可能让进程在 atexit 的 `_flush_hook_dispatch`
        # 里永不退出（见 DP2 用例的注释）。本文件宁可让实现坏掉，也要保证
        # "实现坏掉时这个套件自己会结束" —— 否则"挂死"就又回来了。
        for sink in _HOOK_DISPATCH.all_sinks():
            try:
                sink._event_queue.clear()
            except Exception:  # noqa: BLE001 - 兜底路径不该再抛
                pass

    print("\n" + "=" * 72)
    print(f"[INFO] 断言 {PASSED + len(FAILURES)} 条，通过 {PASSED}，失败 {len(FAILURES)}")
    if FAILURES:
        for item in FAILURES:
            print(f"[FAIL] {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
