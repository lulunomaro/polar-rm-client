"""第 12 轮审计补网：rm_client/backend/field_log.py 里变异存活的 31 条分支。

背景（三个数字先摆出来）
------------------------
* ``field_log.py`` 484 行，AST 全量枚举出 **74 个决策点**（含嵌套：if / elif /
  三元 / except / ``and``·``or`` 的每个子句 / 推导式 if / while）。
* 条件变异 **60 个，存活 31 个（51.7%）**。判定方式是"改坏那一行 -> 跑相关套件
  -> 比 ``(退出码, [PASS] 数, [FAIL] 数)`` 三元组"，不是看行有没有被执行过。
* 存活率高的根因一句话说得清：**全仓只有一个 suite 打开过这个模块**。
  LINE 级追踪扫过全部 60 个 ``tests/*.py``，命中 212 行，全部来自
  ``tests/field_log_async_test.py``；别的套件只碰到构造 ``BackendManager`` 时
  执行的那 77 行（模块 + 类 + ``__init__``），也就是说轮转、异步队列、失败记账、
  回放这四块**只有那一个 suite 在守**。

这一批断言全部**先探针、后断言**
--------------------------------
每个期望值都是探针脚本在**当前实现**上跑出来的真实值（见下面每段的「探针事实」），
不是照抄源码算出来的。凡是改坏被测行之后会变红的，才算行为覆盖；
三处**等价变异**（任何行为断言都抓不住）在文末单列，不计入覆盖数。

跑法：``python -u tests/field_log_branches_audit_test.py``
"""

from __future__ import annotations

import builtins
import contextlib
import ctypes
import json
import msvcrt
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any
import unittest.mock as mock

ROOT = Path(r"C:\Users\lulunomaro\Desktop\claude\openzenuicodexver\rm_custom_client")
sys.path.insert(0, str(ROOT))

from rm_client.backend import field_log as fl  # noqa: E402
from rm_client.backend.field_log import (  # noqa: E402
    ReadOnlyFieldReplay,
    RotatingJsonlLogger,
)

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


def attempt(label: str, fn):
    """跑一次调用并把异常变成一条 [FAIL]。

    守卫被摘掉时正确的反应本来就是"抛异常"。让异常冲出整份文件的话，后面那些
    断言一条都跑不到 —— 那就不是"断言变红"而是"进程死了"。这里把它记成一条和
    原断言同名的失败，既红得明确，也保住后面的断言。
    """
    try:
        return fn()
    except BaseException as exc:  # noqa: BLE001
        check(label, False, f"抛出 {type(exc).__name__}: {exc}")
        return None


def finished_worker() -> threading.Thread:
    """一个已经结束的线程。

    把它塞进 ``logger._worker``，``_enqueue`` 就不会再起真正的写盘线程
    （``if self._worker is None`` 为假），于是队列里的东西没人消费。
    这是把"队列积压 / 丢弃计数 / close() 补写"这几条路径拉到**确定态**的唯一
    干净办法：靠真实线程 + sleep 去撞，测出来的是调度器不是代码。
    """
    thread = threading.Thread(target=lambda: None, name="already-dead")
    thread.start()
    thread.join()
    return thread


def records_of(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@contextlib.contextmanager
def fixed_clock():
    """把 time.time / perf_counter 钉成常量，让一条记录的**字节数**可复算。

    记录里带 wall_time/monotonic 两个浮点，不钉住时钟的话同一条事件每次长度
    会差 1~3 字节 —— 轮转边界这种"差一个字节就翻面"的判据根本没法测
    （探针第一版就栽在这：算出来的 93 和真正落盘的 92 对不上）。
    """
    value = lambda: 1_700_000_000.0  # noqa: E731
    with mock.patch.object(time, "time", value), \
            mock.patch.object(time, "perf_counter", value):
        yield


# ===========================================================================
# A. 写盘失败必须显式记账：emit / 写盘线程 / close() 三条 except 出口
# ===========================================================================


def test_sync_emit_swallows_and_records_the_oserror() -> None:
    """field_log.py:221 —— ``emit`` 自己的 ``except OSError``。

    探针事实（同步 logger，把 ``_write_line`` 换成抛 OSError）：
        不抛异常；``last_error == "[Errno 28] No space left on device"``；
        **``dropped_events`` 仍然是 0**；那条事件确实没落盘。

    ``dropped_events`` 不动是刻意的（同步模式下没落盘的东西没有编号可数），
    所以唯一能看到事故的字段就是 ``last_error``。它一旦被吞掉，
    manager.py:2411 那个 ``field_log.last_error`` 诊断块就会一直显示"没事"，
    而现场日志里那条记录是真的没有。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        logger.emit("warmup", n=0)

        def boom(line: bytes) -> None:
            raise OSError(28, "No space left on device")

        logger._write_line = boom  # type: ignore[method-assign]
        attempt("同步 emit 不把 OSError 抛给调用方", lambda: logger.emit("doomed", n=1))
        check(
            "OSError 落进 last_error（诊断块靠它报警）",
            "No space left on device" in logger.last_error,
            f"last_error={logger.last_error!r}",
        )
        check(
            "同步模式不把没落盘的计入 dropped_events（没编号可数，只有 last_error 能报警）",
            logger.dropped_events == 0,
            f"dropped={logger.dropped_events}",
        )
        check(
            "没落盘的那条确实不在盘上（所以 last_error 是唯一线索）",
            "doomed" not in logger.path.read_text(encoding="utf-8"),
        )
        logger._drop_handle()


def test_async_writer_accounts_a_failed_batch() -> None:
    """field_log.py:388 —— ``_run_writer`` 的 ``except Exception``。

    探针事实（50 条突发，第一批 ``_write_batch`` 抛 OSError，之后恢复）：
        ``dropped_events == 50``（整批记账）；``last_error`` 写着
        ``field_log 异步落盘失败，本批 50 条未落盘：[Errno 28] ...``；
        **写盘线程仍然活着**；随后 10 条正常事件全部落盘。

    这条分支一旦不接，整批事件就在**没有任何痕迹**的情况下消失：证据链上
    有一段空白，而日志里既没有 ``field_log_dropped`` 记录，也没有 last_error。
    现场表现是"日志中间凭空少掉一段时间"，顺着查什么都查不到。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        state = {"fail": True}
        real = logger._write_batch

        def flaky(records: list[bytes]) -> None:
            if state["fail"]:
                state["fail"] = False
                raise OSError(28, "disk full during batch write")
            real(records)

        logger._write_batch = flaky  # type: ignore[method-assign]
        for index in range(50):
            logger.emit("boom", n=index)
        logger.flush(timeout=20.0)
        check(
            "整批未落盘被如实计入 dropped_events",
            logger.dropped_events == 50,
            f"dropped={logger.dropped_events}",
        )
        check(
            "last_error 写明是异步落盘失败且带批大小",
            "异步落盘失败" in logger.last_error and "50 条" in logger.last_error,
            f"last_error={logger.last_error!r}",
        )
        check(
            "写盘线程没有死（失败被记账，不是不再消费）",
            bool(logger._worker and logger._worker.is_alive()),
        )
        for index in range(50, 60):
            logger.emit("after", n=index)
        logger.flush(timeout=20.0)
        logger.close()
        lines = logger.path.read_bytes().count(b"\n")
        check("失败恢复后新事件照常落盘", lines == 10, f"lines={lines}")


def test_close_backfill_failure_is_accounted() -> None:
    """field_log.py:441 —— ``close()`` 补写失败。

    探针事实（7 条积压 + ``_write_batch`` 抛 OSError + close）：
        不抛异常；``dropped_events == 7``；``last_error`` 写着
        ``field_log 关闭时补写失败，7 条未落盘：...``；盘上 0 行。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        logger._worker = finished_worker()
        for index in range(7):
            logger.emit("will_fail", n=index)

        def boom(records: list[bytes]) -> None:
            raise OSError(28, "No space left on device")

        logger._write_batch = boom  # type: ignore[method-assign]
        attempt("close() 补写失败也不把异常抛出去", lambda: logger.close(timeout=1.0))
        check(
            "补写失败整批计入 dropped_events",
            logger.dropped_events == 7,
            f"dropped={logger.dropped_events}",
        )
        check(
            "last_error 指明是关闭时补写失败",
            "关闭时补写失败" in logger.last_error,
            f"last_error={logger.last_error!r}",
        )


# ===========================================================================
# B. close() 自己也要把证据链补上：leftovers 与未报告丢弃
# ===========================================================================


def test_close_writes_queued_events_even_without_drops() -> None:
    """field_log.py:428 —— ``if leftovers or report:``。

    探针事实（队列里积压 300 条、``_unreported == 0``、close）：
        300 条**全部**落到盘上，``dropped_events == 0``，``last_error == ""``。

    ``or`` 的第一个操作数在这里就是全部意义：队列里还剩东西时，哪怕一条都没丢，
    也必须在本线程补写掉。写成 ``and`` 的话这 300 条会被**静默跳过** ——
    现场表现是"关停瞬间那几秒的事件整段不见了"，而且 ``dropped_events`` 是 0，
    没有任何告警。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        logger._worker = finished_worker()
        total = 300
        for index in range(total):
            logger.emit("leftover", n=index)
        check("前提：close() 之前确实有积压", logger.pending_events() == total,
              f"pending={logger.pending_events()}")
        check("前提：一条都没丢", logger.dropped_events == 0 and logger._unreported == 0)
        logger.close(timeout=1.0)
        lines = logger.path.read_bytes().count(b"\n")
        check(f"close() 把队列里剩下的 {total} 条全部补写（没有丢弃就不补 = 静默丢）",
              lines == total, f"lines={lines}")
        check("补写成功不产生任何错误标记", logger.dropped_events == 0 and logger.last_error == "",
              f"dropped={logger.dropped_events} last_error={logger.last_error!r}")


def test_close_reports_unreported_drops_in_the_log() -> None:
    """field_log.py:430 —— ``close()`` 里的 ``if report:``。

    探针事实（queue_size=64、提交 400 条、无人消费、close）：
        盘上 65 行 = 64 条 ``overflow`` + **1 条 ``field_log_dropped``**，
        那条记录是 ``{"dropped": 336, "reason": "queue_overflow"}``，
        且 64 + 336 == 400（落地 + 丢弃 == 提交，仍然没有黑洞）。

    写盘线程还活着的时候它自己会写这条自曝记录；可一旦 ``close()`` 超时放弃
    join，剩下的自曝就得由 ``close()`` 替它写。否则收工那一刻的空洞就成了
    证据链上**唯一一段没有任何标记的空白**。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=64)
        logger._worker = finished_worker()
        total = 400
        for index in range(total):
            logger.emit("overflow", n=index)
        dropped = logger.dropped_events
        check(f"前提：确实丢了 {dropped} 条（{total} - 队列 64）",
              dropped == total - 64, f"dropped={dropped}")
        logger.close(timeout=1.0)
        records = records_of(logger.path)
        markers = [r for r in records if r.get("event") == "field_log_dropped"]
        check("close() 自己把未报告的丢弃写进日志", len(markers) == 1,
              f"markers={markers}")
        check("自曝记录写明丢了多少、为什么",
              bool(markers) and markers[0].get("dropped") == dropped
              and markers[0].get("reason") == "queue_overflow",
              f"markers={markers} dropped={dropped}")
        landed = len([r for r in records if r.get("event") == "overflow"])
        check("落地 + 丢弃 == 提交（自曝记录本身也在账上）",
              landed + dropped == total, f"landed={landed} dropped={dropped} total={total}")


# ===========================================================================
# C. 退出与 GC 兜底：atexit / __del__
# ===========================================================================


def test_atexit_drain_survives_one_broken_logger() -> None:
    """field_log.py:58 —— ``_drain_async_loggers`` 里的 ``except Exception``。

    探针事实（4 个 async logger，其中 1 个 ``close()`` 抛异常）：
        整个调用不抛；**4 个全被 close 过**（含抛异常那个）。

    atexit 是在解释器退出时跑的，这里抛出去会直接盖掉别的退出清理。
    一个坏 logger 如果能把循环打断，后面几个 logger 的队列就没人排空了 ——
    现场表现是"每次关机都少最后一段日志"，而且只在关停那一下复现。
    """
    with tempfile.TemporaryDirectory() as directory:
        loggers = [RotatingJsonlLogger(Path(directory) / f"l{i}", async_writer=True)
                   for i in range(4)]
        closed: list[int] = []

        def make(index: int, boom: bool):
            def close(timeout: float = 1.0) -> None:
                closed.append(index)
                if boom:
                    raise RuntimeError(f"close #{index} exploded")
            return close

        for index, logger in enumerate(loggers):
            logger.close = make(index, index == 2)  # type: ignore[method-assign]
        attempt("atexit 排空队列不因单个 logger 抛异常而中断", fl._drain_async_loggers)
        check("4 个 logger 全部被 close（坏的那个不挡住后面的）",
              sorted(closed) == [0, 1, 2, 3], f"closed={sorted(closed)}")


def test_async_logger_is_registered_for_exit_drain() -> None:
    """field_log.py:177 —— 只有 async logger 进 ``_ASYNC_LOGGERS``。

    探针事实：``RotatingJsonlLogger(dir, async_writer=True) in _ASYNC_LOGGERS``
    为真；同步 logger 为假；跑一次 ``_drain_async_loggers()`` 之后该 logger 的
    ``_closed`` 变 True。

    这条注册是"进程被强杀时队列也会排空"的全部依据。注册反了的话，
    async 模式下客户端被任务管理器结束的那一下，队列里那几百条事件
    一条也不会落盘 —— 而且**没有任何痕迹**，因为它们压根没进过 ``dropped_events``。
    """
    with tempfile.TemporaryDirectory() as directory:
        async_logger = RotatingJsonlLogger(Path(directory) / "a", async_writer=True)
        sync_logger = RotatingJsonlLogger(Path(directory) / "s")
        check("async logger 注册进退出排空集合", async_logger in fl._ASYNC_LOGGERS)
        check("同步 logger 不注册（它不需要后台线程）", sync_logger not in fl._ASYNC_LOGGERS)
        fl._drain_async_loggers()
        check("排空后 async logger 被 close", async_logger._closed is True)
        async_logger.close()
        sync_logger.close()


def test_del_never_propagates() -> None:
    """field_log.py:451 —— ``__del__`` 里的 ``except Exception``。

    探针事实：``close()`` 抛 RuntimeError 时直接调 ``__del__`` 不抛异常。

    ``__del__`` 里抛出来的异常只会变成一条"Exception ignored in __del__"，
    看着像噪声，实际会把解释器退出时的其它清理挤掉。这里断言它必须吞。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True)
        logger.emit("a", n=1)
        logger.flush(timeout=5.0)

        def boom(timeout: float = 1.0) -> None:
            raise RuntimeError("close exploded inside __del__")

        logger.close = boom  # type: ignore[method-assign]
        raised: BaseException | None = None
        try:
            RotatingJsonlLogger.__del__(logger)
        except BaseException as exc:  # noqa: BLE001
            raised = exc
        check("__del__ 把 close() 的异常吞掉（不吐成 Exception ignored in __del__）",
              raised is None, f"raised={type(raised).__name__ if raised else None}: {raised}")
        del logger


# ===========================================================================
# D. Win32 追加句柄的两条降级路径
# ===========================================================================


def test_open_osfhandle_failure_falls_back() -> None:
    """field_log.py:114 —— ``open_osfhandle`` 抛 OSError。

    探针事实：把它换成抛 OSError 之后，``_win_open_share_delete`` 返回 ``None``
    （不抛），且 ``_open_append`` 退到内置 ``open(path, "ab")``，
    写进去的字节是 ``b'x\\n'``。

    这条降级保证"日志句柄打不开"时只是丢掉允许外部删除这个特性，而不是把整个
    落盘通道弄炸 —— 炸了的话 emit 的调用方（75Hz 控制拍）会看到异常。
    """
    if os.name != "nt":
        check("非 Windows 平台跳过（这条降级只存在于 nt 分支）", True)
        return
    real = msvcrt.open_osfhandle
    calls = {"n": 0}

    def boom(fd: int, flags: int) -> int:
        calls["n"] += 1
        raise OSError(22, "invalid argument")

    msvcrt.open_osfhandle = boom  # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "probe.jsonl"
        try:
            result = attempt("open_osfhandle 失败时 _win_open_share_delete 返回 None",
                             lambda: fl._win_open_share_delete(str(target)))
        finally:
            msvcrt.open_osfhandle = real  # type: ignore[assignment]
        check("确实走了 Win32 通道再降级", calls["n"] == 1, f"calls={calls['n']}")
        check("降级返回 None 而不是抛异常", result is None, f"result={result!r}")
        handle = fl._open_append(target)
        handle.write(b"x\n")
        handle.close()
        check("退回内置 open 之后仍然能追加写", target.read_bytes() == b"x\n",
              f"bytes={target.read_bytes()!r}")


def test_file_object_failure_releases_the_descriptor() -> None:
    """field_log.py:123 —— ``open(descriptor, "ab")`` 抛 OSError。

    探针事实：把它换成对 int 参数抛 OSError 之后，函数返回 ``None``，
    而且刚才 ``open_osfhandle`` 交出来的 fd **已经被关掉**
    （之后 ``os.fstat`` 报 ``OSError``）。

    不关 fd 就是每失败一次泄漏一个句柄：现场表现是跑久了之后句柄数一路涨，
    最后"日志写不进去了"，而报错说的是磁盘。
    """
    if os.name != "nt":
        check("非 Windows 平台跳过（这条降级只存在于 nt 分支）", True)
        return
    real_open = builtins.open
    real_osfhandle = msvcrt.open_osfhandle
    seen: dict[str, Any] = {}

    def capture(fd: int, flags: int) -> int:
        seen["fd"] = fd
        return real_osfhandle(fd, flags)

    def patched_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, int):
            raise OSError(9, "Bad file descriptor")
        return real_open(file, *args, **kwargs)

    msvcrt.open_osfhandle = capture  # type: ignore[assignment]
    builtins.open = patched_open  # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "probe.jsonl"
        try:
            result = attempt("文件对象构造失败时返回 None",
                             lambda: fl._win_open_share_delete(str(target)))
        finally:
            builtins.open = real_open  # type: ignore[assignment]
            msvcrt.open_osfhandle = real_osfhandle  # type: ignore[assignment]
        check("降级返回 None", result is None, f"result={result!r}")
        released = False
        try:
            os.fstat(seen["fd"])
        except OSError:
            released = True
        check("交出来的 fd 已经被 os.close 释放（否则每次降级泄漏一个句柄）",
              released, f"fd={seen.get('fd')}")


# ===========================================================================
# E. 回放与诊断读数
# ===========================================================================


def test_replay_reports_half_written_line_as_invalid_jsonl() -> None:
    """field_log.py:481 —— ``except json.JSONDecodeError``。

    探针事实：文件末尾多写半条 ``{"event":"half"``（无换行）时，
    ``records()`` 抛 ``ValueError: <path>:2: invalid JSONL``；
    合法记录（第一行）在它之前已经被 yield 出去了。

    轮转/补写中途断电留下的就是这种半行 JSON。报成 ``JSONDecodeError`` 的话，
    现场拿到的是一条看不出位置的解码栈；报成 ``invalid JSONL`` 才知道要看哪个文件
    第几行。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        logger.emit("whole", n=1)
        logger.close()
        torn = Path(directory) / "torn.jsonl"
        torn.write_bytes(logger.path.read_bytes() + b'{"event":"half"')
        raised = None
        stream = ReadOnlyFieldReplay([torn]).records()
        first = attempt("半行之前的合法记录先被 yield 出来", lambda: next(stream))
        check("半行之前的合法记录仍然读得到",
              isinstance(first, dict) and first.get("event") == "whole",
              f"first={first}")
        try:
            next(stream)
        except BaseException as exc:  # noqa: BLE001
            raised = exc
        check("半行 JSON 抛 ValueError 而不是 JSONDecodeError",
              isinstance(raised, ValueError) and type(raised) is ValueError,
              f"raised={type(raised).__name__ if raised else None}: {raised}")
        check("报错里带文件名与行号（现场才知道该看哪个文件第几行）",
              raised is not None and "invalid JSONL" in str(raised) and ":2" in str(raised),
              f"raised={raised}")


def test_disk_usage_counts_each_file_once() -> None:
    """field_log.py:193 —— ``suffix = "" if index == 0 else f".{index}"``。

    探针事实：手工摆出 live=558 / .1=1956 / .2=1954 / .3=1958 四个文件时，
    ``disk_usage_bytes()`` 恰好等于四者之和 6426。

    写反的话 live 会被数 ``backups`` 次（index 0 去查不存在的 ``.0``，
    其余每一档都查 live）。``disk_usage_bytes`` 是容量体检读的那个数：
    读大了就等于"磁盘快满了"，于是每次现场都要先去删日志。
    """
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory) / "polar-field.jsonl"
        sizes = {"": 558, ".1": 1956, ".2": 1954, ".3": 1958}
        for suffix, size in sizes.items():
            Path(base.as_posix() + suffix).write_bytes(b"x" * size)
        logger = RotatingJsonlLogger(Path(directory), backups=3)
        reported = logger.disk_usage_bytes()
        logger.close()
        check("disk_usage_bytes 每个文件只数一次", reported == sum(sizes.values()),
              f"reported={reported} expected={sum(sizes.values())}")
        check("盘上多一个文件也不许被重复计数",
              reported != sum(sizes.values()) + sizes[""],
              f"reported={reported}")


# ===========================================================================
# F. 字节数缓存 / 轮转的边界判据
# ===========================================================================


def test_rotate_decision_uses_real_on_disk_size() -> None:
    """field_log.py:313 —— 判轮转之前先 ``_ensure_open()`` 拿真实大小。

    探针事实：目录里**已经有**一个 4090 字节的 polar-field.jsonl、
    ``max_bytes=4096``，再 ``emit`` 一条 93 字节的记录：
        立刻轮转 —— 盘上是 ``polar-field.jsonl``(93) + ``.1``(4090)。

    不先开一次拿真实大小的话，新进程/重启后缓存的 ``_size`` 是 0，
    判据就变成"0 + 93 <= 4096"，于是**先把老文件顶穿再轮转**。
    现场表现：客户端重启一次，日志单文件最大可以到 2×max_bytes，
    标称 512MiB 的容量实际变成 1GiB，一次洪泛就把该留的记录冲掉。
    """
    with tempfile.TemporaryDirectory() as directory:
        prefill = 4090
        (Path(directory) / "polar-field.jsonl").write_bytes(b"p" * prefill)
        logger = RotatingJsonlLogger(Path(directory), max_bytes=4096, backups=2)
        logger.emit("fresh", n=1)
        sizes = {p.name: p.stat().st_size for p in Path(directory).glob("*.jsonl*")}
        logger.close()
        check("老文件已经接近阈值时第一条记录就触发轮转",
              "polar-field.jsonl.1" in sizes, f"sizes={sizes}")
        check("轮转后 live 只剩新记录，老内容进 .1",
              sizes.get("polar-field.jsonl.1") == prefill
              and sizes.get("polar-field.jsonl", 0) < 100,
              f"sizes={sizes}")


def test_rotate_boundary_is_inclusive() -> None:
    """field_log.py:316 —— ``_size + incoming <= self.max_bytes`` 里的 ``<=``。

    探针事实（钉死时钟后记录长 93 字节，``max_bytes=4096``）：
        预填 4003 → 4003 + 93 == 4096，**不轮转**，live 恰好 4096；
        预填 4004 → 超 1 字节，轮转成 live(93) + .1(4004)。

    时钟必须钉死：记录里带两个浮点时间戳，不钉住的话同一条事件每次长度会
    差 1~3 字节，"差一个字节就翻面"的判据根本没法测（探针第一版就栽在这）。
    """
    with fixed_clock():
        probe = RotatingJsonlLogger(Path(tempfile.mkdtemp(prefix="fieldlog_")), max_bytes=4096)
        record_len = len(probe._encode("fresh", {"n": 1}))
        probe.close()
    check("钉住时钟后记录长度可复算", record_len > 0, f"record_len={record_len}")
    for label, prefill, expect_rotate in (("正好等于阈值", 4096 - record_len, False),
                                          ("超出 1 字节", 4097 - record_len, True)):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "polar-field.jsonl").write_bytes(b"p" * prefill)
            with fixed_clock():
                logger = RotatingJsonlLogger(Path(directory), max_bytes=4096, backups=2)
                logger.emit("fresh", n=1)
                names = sorted(p.name for p in Path(directory).glob("*.jsonl*"))
                sizes = {p.name: p.stat().st_size for p in Path(directory).glob("*.jsonl*")}
                logger.close()
            rotated = "polar-field.jsonl.1" in names
            check(f"{label}：轮转判据 {'转' if expect_rotate else '不转'}", rotated == expect_rotate,
                  f"prefill={prefill} record_len={record_len} sizes={sizes}")


def test_write_batch_splits_only_at_record_boundaries() -> None:
    """field_log.py:272 / 277 —— 批聚合与切分点。

    探针事实：
        * 100 条小记录（合计 5090 B）、``max_bytes=65536`` → **1 次**
          ``_write_line``，块大小 5090；
        * 两条各 2048 B、合计**正好 4096 == max_bytes** → **1 次**
          ``_write_line``，单文件 4096 B（没被切成两个文件）；
        * 空批 → **0 次** ``_write_line``。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), max_bytes=64 * 1024, backups=2)
        calls: list[int] = []
        real = logger._write_line
        logger._write_line = lambda line: (calls.append(len(line)), real(line))[1]  # type: ignore[method-assign]
        logger._write_batch([json.dumps({"i": i, "pad": "y" * 30}).encode() + b"\n"
                             for i in range(100)])
        logger.close()
        check("100 条小记录聚合成 1 次写盘（不是 100 次）",
              calls == [5090], f"calls={calls}")

    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), max_bytes=4096, backups=2)
        calls = []
        real = logger._write_line
        logger._write_line = lambda line: (calls.append(len(line)), real(line))[1]  # type: ignore[method-assign]
        logger._write_batch([b"x" * 2047 + b"\n", b"y" * 2047 + b"\n"])
        files = sorted(p.name for p in Path(directory).glob("*.jsonl*"))
        logger.close()
        check("批次正好等于 max_bytes 时不切块（切成两块就多一个文件）",
              len(calls) == 1 and files == ["polar-field.jsonl"],
              f"calls={calls} files={files}")

    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), max_bytes=4096, backups=2)
        calls = []
        real = logger._write_line
        logger._write_line = lambda line: (calls.append(len(line)), real(line))[1]  # type: ignore[method-assign]
        logger._write_batch([])
        logger.close()
        check("空批不写任何东西", calls == [], f"calls={calls}")


def test_writer_batch_is_capped_at_write_batch_max() -> None:
    """field_log.py:366 / 368 —— 一批最多 ``_WRITE_BATCH_MAX`` 条。

    探针事实：把写盘线程卡在第一批上、队列里堆 3000 条、放行之后
    第二批正好是 **1024** 条（= ``_WRITE_BATCH_MAX``），
    而不是"有多少拿多少"。

    这一批上限就是 75Hz 突发时"一次持锁写盘不写太久"的全部依据。
    拿掉它（每次只拿 1 条，或者不设上限地一次拿 8192），
    写盘线程要么退化成每条一次 write，要么一次持锁几 MB ——
    两者都会让控制拍看到比原来长一个数量级的停顿。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True,
                                      queue_size=1 << 20)
        batches: list[int] = []
        gate = threading.Event()
        entered = threading.Event()
        real = logger._write_batch

        def slow(records: list[bytes]) -> None:
            batches.append(len(records))
            if len(batches) == 1:
                entered.set()
                gate.wait(10.0)
            real(records)

        logger._write_batch = slow  # type: ignore[method-assign]
        logger.emit("seed", n=0)
        entered.wait(5.0)
        for index in range(3000):
            logger.emit("bulk", n=index)
        gate.set()
        logger.flush(timeout=30.0)
        logger.close()
        check("积压 3000 条时第二批就是上限 1024 条",
              len(batches) >= 2 and batches[1] == fl._WRITE_BATCH_MAX,
              f"batches={batches[:5]} cap={fl._WRITE_BATCH_MAX}")


def test_writer_takes_a_big_batch_when_closing() -> None:
    """field_log.py:369 —— 关闭时 ``_WRITE_BATCH_MAX * 8`` 的加量预算。

    探针事实：同样卡住写盘线程、堆 3000 条，然后把 ``_closed`` 置真并唤醒 ——
    写盘线程把 3000 条**一次全拿走**（批大小序列是 ``[1, 3000]``）。

    这是关机路径的优化：正常工作时一批 1024，收到关闭信号后允许一次多拿 8 倍，
    这样收尾只写一次而不是 8 次。拿掉它每次 shutdown 都要多等几轮批处理。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True,
                                      queue_size=1 << 20)
        batches: list[int] = []
        gate = threading.Event()
        entered = threading.Event()
        real = logger._write_batch

        def slow(records: list[bytes]) -> None:
            batches.append(len(records))
            if len(batches) == 1:
                entered.set()
                gate.wait(10.0)
            real(records)

        logger._write_batch = slow  # type: ignore[method-assign]
        logger.emit("seed", n=0)
        entered.wait(5.0)
        for index in range(3000):
            logger.emit("bulk", n=index)
        logger._closed = True
        with logger._cv:
            logger._cv.notify_all()
        gate.set()
        logger.flush(timeout=30.0)
        logger.close()
        check("关闭时把积压一次全拿走（写盘批大小序列 [1, 3000]）",
              batches[1:] == [3000], f"batches={batches}")


def test_queue_accepts_exactly_queue_size_before_refusing() -> None:
    """field_log.py:339 —— ``len(self._queue) >= self.queue_size``。

    探针事实（queue_size=64，无人消费）：前 64 条一条不丢
    （``dropped_events == 0``），第 65 条开始记账（``dropped_events == 1``）。

    写成 ``>`` 的话队列会多缓冲一条。这条本身无害，但它同时是"队列容量"这个
    数字的语义边界：现场按 queue_size 推算"最多可能积压多少条"时，
    实际容量和标称差一条，排障时对不上数。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=64)
        logger._worker = finished_worker()
        for index in range(64):
            logger.emit("fill", n=index)
        check(f"正好 {64} 条一条都不拒收",
              logger.dropped_events == 0 and logger.pending_events() == 64,
              f"dropped={logger.dropped_events} pending={logger.pending_events()}")
        logger.emit("one_more", n=64)
        check("第 65 条才被拒收并记账",
              logger.dropped_events == 1 and logger.pending_events() == 64,
              f"dropped={logger.dropped_events} pending={logger.pending_events()}")
        check("拒收当场写明队列已满",
              "队列已满" in logger.last_error, f"last_error={logger.last_error!r}")
        logger._closed = True


def test_writer_is_woken_only_on_the_empty_to_nonempty_edge() -> None:
    """field_log.py:347 —— ``if not self._queue: self._cv.notify()``。

    探针事实：连续 ``emit`` 5 条，``_cv.notify()`` 只被调用 **1 次**。

    通知的语义是"从空变非空的那一下"。反过来（队列非空才通知）就等于把
    真正的唤醒边沿漏掉：写盘线程睡在 0.2 s 的轮询上，最近的一条事件要等到
    下一轮轮询才落盘 —— 现场表现是"日志慢半拍"，按时间戳对现场时对不上。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        cv = logger._cv
        notifies = {"n": 0}

        class CountingCV:
            def __getattr__(self, name: str) -> Any:
                return getattr(cv, name)

            def __enter__(self) -> "CountingCV":
                cv.__enter__()
                return self

            def __exit__(self, *exc: Any) -> Any:
                return cv.__exit__(*exc)

            def notify(self, *args: Any) -> Any:
                notifies["n"] += 1
                return cv.notify(*args)

            def notify_all(self, *args: Any) -> Any:
                return cv.notify_all(*args)

        logger._cv = CountingCV()  # type: ignore[assignment]
        for index in range(5):
            logger.emit("edge", n=index)
        time.sleep(0.1)
        logger._cv = cv  # type: ignore[assignment]
        logger.close()
        check("5 条事件只触发 1 次唤醒（只通知空->非空那个沿）",
              notifies["n"] == 1, f"notify={notifies['n']}")


def test_ensure_open_survives_a_failing_stat() -> None:
    """field_log.py:294 —— ``_ensure_open`` 里 stat 失败。

    探针事实：目标文件真实 500 字节，把 ``Path.stat`` 换成对**该文件**抛
    OSError 之后，``_ensure_open()`` 正常返回句柄，且 ``_size == 0``、
    不抛异常。

    这个 0 是保守值（低估而不是高估）：后果是轮转推迟到缓存追上真实大小为止，
    而不是提前轮转把刚写的记录冲掉。但它必须是被显式赋的 0 —— 异常若直接冲出去，
    ``emit`` 就不会走到自己的 ``except OSError``，75Hz 拍上会看到栈。
    """
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "polar-field.jsonl"
        target.write_bytes(b"x" * 500)
        logger = RotatingJsonlLogger(Path(directory))
        real_stat = Path.stat

        def boom(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self == target:
                raise OSError(2, "no such file")
            return real_stat(self, *args, **kwargs)

        Path.stat = boom  # type: ignore[assignment]
        try:
            handle = attempt("stat 失败时 _ensure_open 不抛异常", logger._ensure_open)
        finally:
            Path.stat = real_stat  # type: ignore[assignment]
        check("仍然拿到了可用句柄", handle is not None and handle is logger._handle)
        check("取不到大小时保守地记 0（低估 -> 推迟轮转，不是提前轮转）",
              logger._size == 0, f"_size={logger._size}")
        logger.close()


def test_drop_handle_swallows_a_failing_close() -> None:
    """field_log.py:304 —— ``_drop_handle`` 里 ``handle.close()`` 失败。

    探针事实：把句柄换成 ``close()`` 抛 OSError 的假对象，
    ``_drop_handle()`` 不抛，且 ``_handle`` 变 None、``_size`` 归 0。

    这个置 None 是必须的：Windows 上 rename 一个仍打开的文件会失败，
    轮转会整个失败。close 抛异常时如果不清引用，日志就永远转不动 ——
    现场表现是"单个文件一直涨到把磁盘写满"。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        logger.emit("a", n=1)

        class BadHandle:
            def write(self, _data: bytes) -> int:
                return 0

            def close(self) -> None:
                raise OSError(5, "access denied")

        logger._handle = BadHandle()  # type: ignore[assignment]
        attempt("close() 失败也不从 _drop_handle 抛出去", logger._drop_handle)
        check("句柄引用仍然被清成 None（Windows 轮转才能继续）",
              logger._handle is None, f"_handle={logger._handle!r}")
        check("字节数缓存同时归零", logger._size == 0, f"_size={logger._size}")


# ===========================================================================
# G. flush / close 契约
# ===========================================================================


def test_flush_on_sync_logger_returns_true_immediately() -> None:
    """field_log.py:405 —— ``if not self.async_writer: return True``。

    探针事实：同步 logger 上 ``flush(timeout=0.0)`` 返回 True（不等任何东西）。

    这条分支保护的是"同步模式下没有队列可等"。去掉它之后同步 logger 会去
    等一个永远为空的队列，虽然最终也是 True，但多了一次无谓的锁与等待。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        logger.emit("sync", n=1)
        check("同步 logger 的 flush() 立刻返回 True",
              logger.flush(timeout=0.0) is True)
        logger.close()


def test_flush_on_async_logger_actually_waits() -> None:
    """field_log.py:405 的 async 侧 —— 必须真的等，不能直接返回 True。

    探针事实：把 ``_worker`` 换成一个已经结束的线程、队列里积压 300 条，
    ``flush(timeout=0.2)`` 返回 **False**（等超时了，因为没人消费）。

    反过来写成"async 直接 return True"的话，调用方（关停流程、诊断导出）
    会拿到一个假的"已排空"，然后在队列还没落盘时就去读文件 ——
    读到的记录数比落盘数少，最后一段事件凭空不见。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        logger._worker = finished_worker()
        for index in range(300):
            logger.emit("waiting", n=index)
        check("前提：队列确实没排空", logger.pending_events() == 300,
              f"pending={logger.pending_events()}")
        check("async logger 的 flush() 会真等，等不到就报 False",
              logger.flush(timeout=0.2) is False)
        logger._closed = True


def test_flush_reports_inflight_batches() -> None:
    """field_log.py:411 —— ``return not (self._queue or self._inflight)``。

    探针事实：把 ``_write_batch`` 卡住，队列已经取空（``pending == 0``）
    但 ``_inflight is True`` 时，``flush(timeout=0.05)`` 返回 **False**；
    放行之后返回 True。

    只看队列的话，"最后一批还在写"这一段会被当成已排空。关停流程据此去读日志，
    读到的就是少了最后那几百条 —— 正好是"关掉客户端那一刻的现场"。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        gate = threading.Event()
        entered = threading.Event()
        real = logger._write_batch

        def slow(records: list[bytes]) -> None:
            entered.set()
            gate.wait(5.0)
            real(records)

        logger._write_batch = slow  # type: ignore[method-assign]
        for index in range(20):
            logger.emit("x", n=index)
        entered.wait(5.0)
        check("前提：队列已空但有一批在写",
              logger.pending_events() == 0 and logger._inflight is True,
              f"pending={logger.pending_events()} inflight={logger._inflight}")
        check("最后一批还在写时 flush() 报 False（不假装排空）",
              logger.flush(timeout=0.05) is False)
        gate.set()
        check("写完之后 flush() 才报 True", logger.flush(timeout=20.0) is True)
        logger.close()


def test_close_from_the_writer_thread_does_not_self_join() -> None:
    """field_log.py:419 —— ``worker is not threading.current_thread()``。

    探针事实：让写盘线程在 ``_write_batch`` 里回调 ``close()``，
    不抛异常（``RuntimeError: cannot join current thread`` 没出现）。

    去掉这个守卫的话，任何在写盘线程上下文里走到 ``close()`` 的路径
    （含 ``__del__``/atexit 在该线程上被触发）都会直接炸掉，
    而且是在退出路径上炸 —— 现场表现为"退出时刷一屏栈，回放读到半截日志"。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        fired = threading.Event()
        captured: dict[str, Any] = {}
        real = logger._write_batch

        def closing_batch(records: list[bytes]) -> None:
            if not fired.is_set():
                fired.set()
                try:
                    logger.close(timeout=1.0)
                    captured["raised"] = None
                except BaseException as exc:  # noqa: BLE001
                    captured["raised"] = f"{type(exc).__name__}: {exc}"
            real(records)

        logger._write_batch = closing_batch  # type: ignore[method-assign]
        for index in range(10):
            logger.emit("selfclose", n=index)
        logger.flush(timeout=10.0)
        time.sleep(0.3)
        logger.close()
        check("前提：确实在写盘线程上调用了 close()", fired.is_set())
        check("写盘线程里调 close() 不会自 join 炸掉",
              captured.get("raised") is None, f"raised={captured.get('raised')}")


def test_last_error_stays_until_the_logger_is_rebuilt() -> None:
    """field_log.py:386 —— 写盘线程成功之后**不**清 ``last_error``。

    探针事实：队列满丢过 1 条 -> 放行 -> ``flush`` -> 再成功写 1 条，
    ``last_error`` 仍然是那句 ``field_log 队列已满（64 条）…累计未落盘 1 条``。

    写盘恢复不等于证据链上的空洞消失了。把这条清掉的话，manager 的诊断块会
    在系统已经恢复之后把"曾经丢过事件"这件事悄悄咽回去 ——
    现场看到的日志中间少一截，却再也查不出曾经丢过。
    """
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=64)
        release = threading.Event()
        entered = threading.Event()
        real = logger._write_line

        def slow(line: bytes) -> None:
            entered.set()
            release.wait(5.0)
            real(line)

        logger._write_line = slow  # type: ignore[method-assign]
        while logger.dropped_events == 0:
            logger.emit("overflow", n=1)
        check("丢弃当场写明未落盘", "未落盘" in logger.last_error,
              f"last_error={logger.last_error!r}")
        release.set()
        logger.flush(timeout=20.0)
        logger.emit("after_recovery", n=2)
        logger.flush(timeout=20.0)
        check("写盘恢复之后 last_error 仍然保留那段空洞的说明",
              "未落盘" in logger.last_error,
              f"last_error={logger.last_error!r}")
        logger.close()


# ===========================================================================
# H. 等价变异（**不是**行为覆盖，写在这里是为了别让人重复造轮子）
# ===========================================================================


def test_equivalent_mutations_are_characterised() -> None:
    """三个变异没有任何行为断言抓得住 —— 明说，不硬凑。

    1. ``if handle is None or handle == invalid_handle:`` 里**删掉**
       ``handle is None``（或改成 ``and``）：探针实测 ``CreateFileW`` 失败时
       ``restype=c_void_p`` 返回的是 ``18446744073709551615``（INVALID_HANDLE_VALUE），
       **永远不是 None**，所以第一个操作数是死代码；删掉之后 INVALID 句柄落到
       ``open_osfhandle`` 上，它抛 ``OSError: [Errno 9]``，被下面那个
       ``except OSError`` 接住，结果同样是返回 ``None``。**等价**。
    2. ``if block and size + len(line) > self.max_bytes:`` 里删掉 ``block and``：
       ``block`` 为空时 ``size`` 必为 0，只有单条记录大于 ``max_bytes`` 才会多出
       一次 ``_write_line(b"")`` —— 写 0 字节、且最多触发一次多余的轮转改名，
       落盘字节与文件内容不变。**等价**。
    3. 这两条上面那条断言本身就是它们"看起来能测"的证据：这里断言的是
       真实可观测的降级行为（INVALID 句柄 -> 返回 None），不是断言内部写法。
    """
    if os.name == "nt":
        invalid = ctypes.c_void_p(-1).value
        raised = None
        try:
            msvcrt.open_osfhandle(invalid, os.O_WRONLY | os.O_APPEND | os.O_BINARY)
        except OSError as exc:
            raised = exc
        check("INVALID_HANDLE_VALUE 交回 open_osfhandle 会抛 OSError（所以上面那条等价）",
              isinstance(raised, OSError), f"raised={raised!r}")
        check("INVALID_HANDLE_VALUE 本身不是 None（所以 handle is None 是死代码）",
              invalid is not None)
    else:
        check("非 Windows 平台跳过（等价性只在 nt 分支上讨论）", True)


def main() -> int:
    tests = [
        test_sync_emit_swallows_and_records_the_oserror,
        test_async_writer_accounts_a_failed_batch,
        test_close_backfill_failure_is_accounted,
        test_close_writes_queued_events_even_without_drops,
        test_close_reports_unreported_drops_in_the_log,
        test_atexit_drain_survives_one_broken_logger,
        test_async_logger_is_registered_for_exit_drain,
        test_del_never_propagates,
        test_open_osfhandle_failure_falls_back,
        test_file_object_failure_releases_the_descriptor,
        test_replay_reports_half_written_line_as_invalid_jsonl,
        test_disk_usage_counts_each_file_once,
        test_rotate_decision_uses_real_on_disk_size,
        test_rotate_boundary_is_inclusive,
        test_write_batch_splits_only_at_record_boundaries,
        test_writer_batch_is_capped_at_write_batch_max,
        test_writer_takes_a_big_batch_when_closing,
        test_queue_accepts_exactly_queue_size_before_refusing,
        test_writer_is_woken_only_on_the_empty_to_nonempty_edge,
        test_ensure_open_survives_a_failing_stat,
        test_drop_handle_swallows_a_failing_close,
        test_flush_on_sync_logger_returns_true_immediately,
        test_flush_on_async_logger_actually_waits,
        test_flush_reports_inflight_batches,
        test_close_from_the_writer_thread_does_not_self_join,
        test_last_error_stays_until_the_logger_is_rebuilt,
        test_equivalent_mutations_are_characterised,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 70)
    print(f"field_log 零覆盖分支补网：{PASSED} 通过，{len(FAILURES)} 失败")
    for failure in FAILURES:
        print("  FAIL", failure)
    print("=" * 70)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())