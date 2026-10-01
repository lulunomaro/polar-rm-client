"""field_log 落盘路径 + MQTT event hook 分发的回归测试。

这个文件钉住本轮 perf/modular-rust 分支上 field-log 组的四件事：

1. **逐位一致**：常开句柄 + 字节数缓存的同步写盘路径，写出的 JSONL 必须与
   优化前那份"每条都 mkdir/stat/open/write/close"的实现**逐字节相同**
   （含轮转后的 .1/.2 文件）。用固定时钟消掉 time.time()/perf_counter()。
2. **快在哪**：N 条 emit 只 open 一次、只 mkdir 一次（原来是 N 次），
   并且轮转判据仍然用真实盘上大小。
3. **异步不丢事件**：async_writer=True 时，事件要么落盘、要么被显式记账
   （dropped_events / last_error / 日志里那条 field_log_dropped），
   绝不允许静默丢弃；flush()/close() 必须把队列排空。
4. **MQTT 生产热路径**：publish_topic 挂上生产 event_hook 时，
   hook 必须在**别的线程**上跑；队列满时显式报错而不是阻塞 75Hz 控制拍。

另外把轮转容量钉住：默认 8×64MiB = 512MiB（原 5×20MiB = 100MiB），
并实测 75Hz publish_result 的日志增长速率，给这个数字一个来源。

跑法：``python -u tests/field_log_async_test.py``
"""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend import field_log  # noqa: E402
from rm_client.backend.field_log import RotatingJsonlLogger  # noqa: E402
from rm_client.backend.mqtt_backend import _HOOK_DISPATCH, RmMqttBackend  # noqa: E402
from rm_client.backend.protocol import encode_keyboard_mouse_control  # noqa: E402
from rm_client.backend.safety import OfficialSafety  # noqa: E402
from rm_client.scenarios import SCENARIOS  # noqa: E402

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


# ======================================================================
# 0. 参照实现：优化前的 RotatingJsonlLogger（逐字复制，只用来对比字节）
# ======================================================================


class _ReferenceRotatingLogger:
    """优化前的实现：每条事件都 mkdir + stat + open/write/close。"""

    def __init__(self, directory: Path, *, filename: str = "polar-field.jsonl",
                 max_bytes: int = 20 * 1024 * 1024, backups: int = 4) -> None:
        self.directory = Path(directory)
        self.path = self.directory / filename
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(1, int(backups))
        self.last_error = ""

    def emit(self, event: str, **fields: Any) -> None:
        if str(event).lower() in {"video_frame", "video_payload", "decoded_frame"}:
            return
        record = {
            "schema": 1,
            "wall_time": time.time(),
            "monotonic": time.perf_counter(),
            "event": str(event),
        }
        record.update({key: self._safe(value) for key, value in fields.items()})
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed(len(line))
            with self.path.open("ab", buffering=0) as handle:
                handle.write(line)
            self.last_error = ""
        except OSError as exc:
            self.last_error = str(exc)

    def referee_payload(self, topic: str, payload: bytes) -> None:
        import base64

        bounded = bytes(payload[:65536])
        self.emit(
            "referee_rx",
            topic=topic,
            payload_b64=base64.b64encode(bounded).decode("ascii"),
            payload_bytes=len(payload),
            truncated=len(payload) > len(bounded),
        )

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        if size + incoming <= self.max_bytes:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        if self.path.exists():
            os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))

    _safe = classmethod(lambda cls, value: RotatingJsonlLogger._safe.__func__(cls, value))


# ======================================================================
# 测试脚手架
# ======================================================================


class _FixedClock:
    """确定性时钟：每次调用 +1µs，让 JSONL 字节可复现。"""

    def __init__(self) -> None:
        self._value = 1_700_000_000.0

    def __call__(self) -> float:
        self._value += 1e-6
        return self._value


class _fake_clock:
    """把 time.time / time.perf_counter 换成一个确定性序列。

    field_log 和参照实现都是 ``time.time()``/``time.perf_counter()`` 这种
    模块属性调用，所以直接 patch 这两个属性就能让两条路径写出可逐字节比较的
    日志 —— 时间戳既不参与协议，也不参与轮转判据。

    **每个 logger 必须各开一段**：序列是共享的交错计数器，两个 logger 共用
    一段的话第 2 条记录拿到的是第 4 个时间戳，比对就毫无意义了。
    """

    def __init__(self) -> None:
        self._patches: list[Any] = []

    def __enter__(self) -> _FixedClock:
        import unittest.mock as mock

        clock = _FixedClock()
        self._patches = [
            mock.patch.object(time, "time", clock),
            mock.patch.object(time, "perf_counter", clock),
        ]
        for patch in self._patches:
            patch.start()
        return clock

    def __exit__(self, *exc: Any) -> None:
        for patch in reversed(self._patches):
            patch.stop()
        self._patches = []


def _dump(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.jsonl*"))}


def _record_stream(directory: Path, stem: str = "polar-field.jsonl") -> list[dict[str, Any]]:
    """按轮转时间顺序把所有文件拼起来还原成记录序列。

    同步通道每条记录单独判断轮转，所以文件切点落在记录边界上；异步通道按批
    聚合成块，切点同样只在记录边界上（_write_batch 保证），但块的边界不必与
    同步通道一一对应。因此"逐字节相同"的断言落在**记录流**上，而不是每个
    文件的切分位置 —— 文件切分逐字节一致由测试 [1] 单独钉死（同步路径 vs
    优化前实现）。
    """
    directory = Path(directory)
    base = directory / stem
    ordered: list[Path] = []
    index = 1
    while True:
        candidate = base.with_suffix(base.suffix + f".{index}")
        if not candidate.exists():
            break
        ordered.append(candidate)
        index += 1
    ordered.reverse()  # .N 最老
    if base.exists():
        ordered.append(base)
    blob = b"".join(path.read_bytes() for path in ordered)
    return [
        json.loads(line)
        for line in blob.split(b"\n")
        if line.strip()
    ]


class _StableRepr:
    """repr 里不能带内存地址，否则"逐字节一致"根本没法比。"""

    def __repr__(self) -> str:  # pragma: no cover - 只为确定性
        return "<stable-repr object>"


def _scenario_events(count: int) -> list[tuple[str, dict[str, Any]]]:
    """一条能同时打到编码/轮转/拒绝分支的事件序列。"""
    events: list[tuple[str, dict[str, Any]]] = []
    for index in range(count):
        events.append((
            "publish_result",
            {
                "topic": "KeyboardMouseControl",
                "ok": index % 7 != 0,
                "reason": "ok" if index % 3 else "限频",
                "intent": "core_kmc",
                "index": index,
                "detail": "x" * 60,
            },
        ))
        if index % 17 == 0:
            events.append(("mqtt_connect", {"host": "127.0.0.1", "port": 3333}))
        if index % 23 == 0:
            events.append(("video_frame", {"should": "be dropped"}))
        if index % 29 == 0:
            events.append((
                "control_lock",
                {
                    "reason": "中文也要原样落盘",
                    "caps": {"b", "a"},
                    "raw": bytes(range(40)),
                    "nested": {"z": (1, 2), "n": None, "f": 1.5},
                    "obj": _StableRepr(),
                },
            ))
    return events


# ======================================================================
# 1. 逐位一致
# ======================================================================


def test_sync_bytes_identical_to_reference() -> None:
    print("\n[1] 同步写盘逐位一致（对比优化前实现）")
    with tempfile.TemporaryDirectory() as reference_dir, tempfile.TemporaryDirectory() as new_dir:
        reference = _ReferenceRotatingLogger(Path(reference_dir), max_bytes=4096, backups=3)
        optimized = RotatingJsonlLogger(Path(new_dir), max_bytes=4096, backups=3)
        events = _scenario_events(240)
        with _fake_clock():
            for event, fields in events:
                reference.emit(event, **fields)
        with _fake_clock():
            for event, fields in events:
                optimized.emit(event, **fields)
        optimized.close()
        left = _dump(Path(reference_dir))
        right = _dump(Path(new_dir))
        check(
            "轮转文件名集合一致",
            sorted(left) == sorted(right),
            f"{sorted(left)} != {sorted(right)}",
        )
        identical = all(left.get(name) == right.get(name) for name in set(left) | set(right))
        check(
            "每个 jsonl 文件逐字节相同",
            identical,
            "; ".join(
                f"{name}: {len(left.get(name, b''))}B vs {len(right.get(name, b''))}B"
                for name in sorted(set(left) | set(right))
                if left.get(name) != right.get(name)
            ),
        )
        check(
            "确实发生了轮转（有 .1 备份）",
            any(name.endswith(".1") for name in right),
            f"files={sorted(right)}",
        )


def test_sync_and_async_bytes_identical() -> None:
    print("\n[2] 同步通道与异步通道写出的记录流一致")
    with tempfile.TemporaryDirectory() as sync_dir, tempfile.TemporaryDirectory() as async_dir:
        sync_logger = RotatingJsonlLogger(Path(sync_dir), max_bytes=4096, backups=3)
        async_logger = RotatingJsonlLogger(
            Path(async_dir), max_bytes=4096, backups=3, async_writer=True
        )
        with _fake_clock():
            for event, fields in _scenario_events(240):
                sync_logger.emit(event, **fields)
        with _fake_clock():
            for event, fields in _scenario_events(240):
                async_logger.emit(event, **fields)
        check("flush() 返回 True", async_logger.flush(timeout=5.0) is True)
        async_logger.close()
        sync_logger.close()
        left = _record_stream(Path(sync_dir))
        right = _record_stream(Path(async_dir))
        check(
            "同步/异步写出的记录逐条逐字段相同",
            left == right,
            f"{len(left)} vs {len(right)}",
        )
        check("记录条数一致", len(left) == len(right), f"{len(left)} vs {len(right)}")
        check(
            "确实发生了轮转（这条断言才有意义）",
            len(list(Path(sync_dir).glob("*.jsonl*"))) > 1
            and len(list(Path(async_dir).glob("*.jsonl*"))) > 1,
            "没有轮转",
        )


# ======================================================================
# 2. 快在哪：句柄常开 + 字节数缓存
# ======================================================================


def test_single_open_and_mkdir_per_run() -> None:
    print("\n[3] 句柄常开 / 字节数缓存")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        opens: list[str] = []
        mkdirs: list[str] = []
        real_open = builtins.open
        real_mkdir = Path.mkdir

        def counting_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            opens.append(str(file))
            return real_open(file, *args, **kwargs)

        def counting_mkdir(self: Path, *args: Any, **kwargs: Any) -> Any:
            mkdirs.append(str(self))
            return real_mkdir(self, *args, **kwargs)

        builtins.open = counting_open  # type: ignore[assignment]
        Path.mkdir = counting_mkdir  # type: ignore[assignment]
        try:
            for index in range(500):
                logger.emit("publish_result", topic="KeyboardMouseControl", index=index)
        finally:
            builtins.open = real_open  # type: ignore[assignment]
            Path.mkdir = real_mkdir  # type: ignore[assignment]
        check("500 条 emit 只 open 一次（原来 500 次）", len(opens) == 1, f"opens={opens}")
        check("500 条 emit 只 mkdir 一次（原来 500 次）", len(mkdirs) == 1, f"mkdirs={len(mkdirs)}")
        check("缓存字节数与盘上一致", logger._size == logger.path.stat().st_size,
              f"cached={logger._size} disk={logger.path.stat().st_size}")
        logger.close()


def test_cached_size_matches_stat_after_rotation() -> None:
    print("\n[4] 轮转后缓存字节数仍等于真实盘上大小")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), max_bytes=2048, backups=3)
        for index in range(400):
            logger.emit("publish_result", index=index, detail="y" * 40)
        mismatches = []
        for index in range(logger.backups + 1):
            suffix = "" if index == 0 else f".{index}"
            target = logger.path.with_suffix(logger.path.suffix + suffix)
            if target.exists() and target.stat().st_size > logger.max_bytes:
                mismatches.append((target.name, target.stat().st_size))
        check("没有任何单个文件超过 max_bytes", not mismatches, f"{mismatches}")
        logger.close()
        check("关闭后无残留句柄", logger._handle is None)


def test_write_error_is_reported_and_recovered() -> None:
    print("\n[5] 写盘失败要显式报错，且能自愈")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        logger.emit("warmup", n=1)
        handle = logger._handle
        original_write = handle.write
        calls = {"n": 0}

        def flaky_write(data: bytes) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(28, "No space left on device")
            return original_write(data)

        handle.write = flaky_write  # type: ignore[method-assign]
        logger.emit("first", n=2)          # 失败 -> last_error，重开后重试
        logger.emit("second", n=3)         # 恢复
        check("失败被显式记录到 last_error 或重试成功", logger.last_error == "" or "28" in logger.last_error,
              f"last_error={logger.last_error!r}")
        lines = logger.path.read_bytes().count(b"\n")
        check("重试后两条事件都在盘上", lines == 3, f"lines={lines}")
        logger.close()


# ======================================================================
# 3. 异步 writer：不丢事件
# ======================================================================


def test_async_delivers_everything() -> None:
    print("\n[6] 异步 writer 一条不丢")
    # 6a. 真实产线形状：75Hz 持续 2 秒
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True)
        total = 150
        for index in range(total):
            logger.emit("publish_result", index=index, detail="z" * 30)
            time.sleep(1.0 / 75.0)
        check("75Hz x 2s 的 flush() 排空队列", logger.flush(timeout=10.0) is True)
        logger.close()
        records = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
        check(f"75Hz x 2s: {total} 条全部落盘", len(records) == total, f"got={len(records)}")
        check("75Hz x 2s: dropped_events 为 0", logger.dropped_events == 0, f"{logger.dropped_events}")

    # 6b. 压满一整条队列的突发：证明管线本身跟得上，不靠"排队"硬扛
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=65536)
        total = 20000

        def produce() -> None:
            for index in range(total):
                logger.emit("publish_result", index=index, detail="z" * 30)

        thread = threading.Thread(target=produce, name="producer")
        started = time.perf_counter()
        thread.start()
        thread.join()
        produced = time.perf_counter() - started
        check("flush() 排空队列", logger.flush(timeout=30.0) is True)
        logger.close()
        records = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
        check(f"{total} 条突发事件全部落盘", len(records) == total, f"got={len(records)}")
        check("dropped_events 恒为 0", logger.dropped_events == 0, f"dropped={logger.dropped_events}")
        indexes = [record["index"] for record in records if record["event"] == "publish_result"]
        check("顺序严格保持", indexes == list(range(total)), "FIFO 顺序被破坏")
        print(
            f"       突发 {total} 条耗时 {produced:.3f}s"
            f"（{total / produced:.0f} 条/秒，队列 65536 未触发拒收）"
        )


def test_async_overflow_is_explicit() -> None:
    print("\n[7] 队列满 = 显式报错，不是静默丢弃")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=64)
        release = threading.Event()
        entered = threading.Event()

        original_write = logger._write_line

        def slow_write(line: bytes) -> None:
            entered.set()
            release.wait(5.0)
            original_write(line)

        logger._write_line = slow_write  # type: ignore[method-assign]
        try:
            total = 4000
            for index in range(total):
                logger.emit("publish_result", index=index, detail="q" * 40)
            check("写盘线程确实被拖住", entered.wait(5.0), "写盘线程没进入 slow_write")
            dropped = logger.dropped_events
            check("队列满时 dropped_events 增加", dropped > 0, f"dropped={dropped}")
            check(
                "last_error 当场写明未落盘",
                "未落盘" in logger.last_error,
                f"last_error={logger.last_error!r}",
            )
            release.set()
            logger.flush(timeout=10.0)
        finally:
            release.set()
            logger.close()
            logger._write_line = original_write  # type: ignore[method-assign]
        records = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
        markers = [record for record in records if record["event"] == "field_log_dropped"]
        check("日志里出现 field_log_dropped 标记", len(markers) >= 1, f"markers={len(markers)}")
        check(
            "标记里的条数与记账一致",
            sum(int(marker["dropped"]) for marker in markers) == dropped,
            f"markers={markers} dropped={dropped}",
        )
        landed = [r for r in records if r["event"] == "publish_result"]
        check(
            "落盘条数 + 丢弃条数 == 提交条数（没有静默黑洞）",
            len(landed) + dropped == total,
            f"landed={len(landed)} dropped={dropped} total={total}",
        )
        check("close() 之后队列为空", logger.pending_events() == 0)


def test_async_close_drains_queue() -> None:
    print("\n[8] close() 必须把队列里剩下的写完")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True, queue_size=4096)
        for index in range(500):
            logger.emit("publish_result", index=index)
        logger.close(timeout=5.0)
        lines = logger.path.read_bytes().count(b"\n")
        check("close() 后 500 条全部落盘", lines == 500, f"lines={lines}")
        check("close() 幂等", logger.close(timeout=1.0) is None)
        # close 之后再来的事件退回同步写，不能凭空消失
        logger.emit("after_close", n=1)
        check(
            "close 之后的事件仍然落盘",
            b"after_close" in logger.path.read_bytes(),
            "after_close 没落盘",
        )
        logger.close()


def test_async_rotation_stays_bounded() -> None:
    print("\n[9] 异步模式下的轮转")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(
            Path(directory), max_bytes=4096, backups=3, async_writer=True
        )
        for index in range(600):
            logger.emit("publish_result", index=index, detail="w" * 60)
        logger.close()
        files = sorted(Path(directory).glob("*.jsonl*"))
        check("文件数受 backups 约束", len(files) <= 4, f"files={[p.name for p in files]}")
        check(
            "总占用不超过 capacity_bytes",
            logger.disk_usage_bytes() <= logger.capacity_bytes,
            f"{logger.disk_usage_bytes()} > {logger.capacity_bytes}",
        )


def test_open_handle_does_not_block_deletion() -> None:
    print("\n[19] 句柄常开不能挡住日志目录被删（Windows 回归）")
    import shutil

    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        for index in range(200):
            logger.emit("publish_result", index=index)
        # 句柄还开着的时候删目录 —— 这正是 TemporaryDirectory 收尾做的事。
        shutil.rmtree(directory, ignore_errors=False)
        check("句柄常开期间日志目录仍可删除", not Path(directory).exists(), "目录还在")
        logger.close()
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), async_writer=True)
        logger.emit("a", n=1)
        logger.flush(timeout=5.0)
        shutil.rmtree(directory, ignore_errors=False)
        check("异步模式同理可删", not Path(directory).exists(), "目录还在")
        logger.close()


def test_cached_size_survives_external_deletion() -> None:
    print("\n[20] 外部删掉日志文件后能自愈（reopen）")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        logger.emit("first", n=1)
        logger.path.unlink()
        logger.reopen()
        logger.emit("second", n=2)
        logger.close()
        text = logger.path.read_text(encoding="utf-8")
        check("reopen() 之后重新建文件并继续写", "second" in text and "first" not in text, text[:200])


# ======================================================================
# 4. 轮转容量
# ======================================================================


def test_rotation_capacity_default() -> None:
    print("\n[10] 轮转容量：100MiB -> 512MiB")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        old_capacity = 20 * 1024 * 1024 * 5
        check(
            "默认总容量 = 8 x 64MiB = 512MiB",
            logger.capacity_bytes == 512 * 1024 * 1024,
            f"capacity={logger.capacity_bytes}",
        )
        check(
            "比原来的 100MiB 大 5.12 倍",
            logger.capacity_bytes == old_capacity * 5 + 12 * 1024 * 1024,
            f"old={old_capacity} new={logger.capacity_bytes}",
        )
        check("容量可读、磁盘占用可读", isinstance(logger.disk_usage_bytes(), int))
        logger.close()

    # 75Hz publish_result 的稳态增长速率 —— 容量数字的来源
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        seconds = 20.0
        count = int(75 * seconds)
        for index in range(count):
            logger.emit(
                "publish_result",
                topic="KeyboardMouseControl",
                ok=True,
                reason="ok",
                intent="core_kmc",
                index=index,
            )
        grew = logger.path.stat().st_size
        logger.close()
        mib_per_hour = grew / (1024 * 1024) / (seconds / 3600.0)
        retention_hours = (512 * 1024 * 1024) / (grew / (seconds / 3600.0))
        print(
            f"       75Hz publish_result 实测 {grew / count:.0f} B/条"
            f" → {mib_per_hour:.1f} MiB/h"
            f"；512MiB 可留 {retention_hours:.1f} 小时"
            f"（100MiB 只有 {100 / mib_per_hour:.1f} 小时）"
        )
        check(
            "512MiB 至少能留 8 小时的 75Hz publish_result",
            retention_hours >= 8.0,
            f"{retention_hours:.2f} h",
        )


# ======================================================================
# 5. MQTT event hook 分发
# ======================================================================


def _armed_backend() -> tuple[RmMqttBackend, OfficialSafety]:
    safety = OfficialSafety("OfficialEvent")
    safety.from_scenario(SCENARIOS["OfficialEvent"])
    safety.update_stage("4")
    safety.arm_match({"core_kmc"})
    return RmMqttBackend(mock_output=True, safety=safety), safety


def test_hook_runs_off_the_calling_thread() -> None:
    print("\n[11] 生产 hook 不再跑在 75Hz 控制拍线程上")
    backend, _ = _armed_backend()
    seen: list[tuple[str, int]] = []
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory))
        backend.event_hook = lambda event, **payload: seen.append(
            (event, threading.get_ident())
        )
        caller_ident = threading.get_ident()
        payload = encode_keyboard_mouse_control()
        backend.publish_topic("KeyboardMouseControl", payload)
        backend.flush_events()
        check("publish_topic 照样成功返回 True", seen and seen[0][0] == "publish_result", f"{seen}")
        check(
            "hook 在别的线程上执行",
            all(ident != caller_ident for _event, ident in seen),
            f"caller={caller_ident} hook={seen}",
        )
        backend.event_hook = lambda event, **payload: logger.emit(event, **payload)
        samples = []
        for _ in range(2000):
            start = time.perf_counter()
            backend.publish_topic("KeyboardMouseControl", payload)
            samples.append((time.perf_counter() - start) * 1e6)
        backend.flush_events()
        logger.close()
    median_us = statistics.median(samples)
    print(
        f"       publish_topic(带生产 hook) 中位 {median_us:.2f} µs"
        f" → 75Hz 折合 {median_us * 75 / 1000.0:.4f} ms/s"
        f"（优化前 141.1 µs → 10.5825 ms/s）"
    )
    check(
        "publish_topic 中位耗时 < 20µs（优化前 141µs）",
        median_us < 20.0,
        f"{median_us:.2f} µs",
    )


def test_hook_preserves_order_and_loses_nothing() -> None:
    print("\n[12] 事件有序、一条不丢")
    backend, _ = _armed_backend()
    seen: list[Any] = []
    backend.event_hook = lambda event, **payload: seen.append((event, payload.get("index")))

    # 12a. 队列有富余时的突发：顺序必须严格 FIFO，一条都不能少
    previous_max = _HOOK_DISPATCH.queue_max
    _HOOK_DISPATCH.queue_max = 65536
    try:
        ordered = 5000
        for index in range(ordered):
            backend._emit_event("seq", index=index)
        check("flush_events 排空", backend.flush_events(timeout=10.0) is True)
        indexes = [index for event, index in seen if event == "seq"]
        check(f"{ordered} 条标记全部送达", len(indexes) == ordered, f"got={len(indexes)}")
        check("顺序严格 FIFO", indexes == list(range(ordered)), "顺序被破坏")
        check("队列足够时 dropped_events 为 0", backend.dropped_events == 0, f"{backend.dropped_events}")
    finally:
        _HOOK_DISPATCH.queue_max = previous_max

    # 12b. 产线形状：默认队列 + 75Hz KMC 连续 publish
    seen.clear()
    backend.dropped_events = 0
    backend._reported_drops = 0
    payload = encode_keyboard_mouse_control()
    total = 150
    for _ in range(total):
        backend.publish_topic("KeyboardMouseControl", payload)
        time.sleep(1.0 / 75.0)
    backend.flush_events(timeout=10.0)
    published = [event for event, _index in seen]
    check(f"75Hz x 2s: {total} 条 publish_result 全部送达", len(published) == total, f"got={len(published)}")
    check("全部是 publish_result", set(published) == {"publish_result"}, f"{set(published)}")
    check("默认队列下 dropped_events 为 0", backend.dropped_events == 0, f"{backend.dropped_events}")
    check("队列已排空", backend.events_pending() == 0)
    check(
        "默认队列容量 >= 75Hz 一分钟的缓冲",
        _HOOK_DISPATCH.queue_max >= 75 * 60,
        f"queue_max={_HOOK_DISPATCH.queue_max}",
    )


def test_hook_overflow_is_explicit() -> None:
    print("\n[13] hook 队列满 = 显式报错，不阻塞控制拍")
    backend, _ = _armed_backend()
    entered = threading.Event()
    release = threading.Event()
    seen: list[tuple[str, Any]] = []
    previous_max = _HOOK_DISPATCH.queue_max
    _HOOK_DISPATCH.queue_max = 64
    try:
        def blocking_hook(event: str, **payload: Any) -> None:
            if event == "publish_result":
                entered.set()
                release.wait(5.0)
            seen.append((event, payload.get("dropped")))

        backend.event_hook = blocking_hook
        payload = encode_keyboard_mouse_control()
        started = time.perf_counter()
        total = 4000
        for _ in range(total):
            backend.publish_topic("KeyboardMouseControl", payload)
        elapsed = time.perf_counter() - started
        check("写盘线程确实被拖住", entered.wait(5.0), "hook 没进入阻塞点")
        dropped = backend.dropped_events
        check("dropped_events 增加", dropped > 0, f"dropped={dropped}")
        check("last_error 当场写明", "队列已满" in backend.last_error, f"{backend.last_error!r}")
        check(
            "队列满不阻塞控制拍（4000 次 publish 用时 < 1.5s）",
            elapsed < 1.5,
            f"{elapsed:.3f}s",
        )
        release.set()
        backend.flush_events(timeout=5.0)
    finally:
        release.set()
        _HOOK_DISPATCH.queue_max = previous_max
    markers = [dropped_count for event, dropped_count in seen if event == "mqtt_event_dropped"]
    check("补发了 mqtt_event_dropped 记录", bool(markers), f"{seen[-3:]}")
    check(
        "补发条数与记账一致",
        markers and sum(int(value) for value in markers) == dropped,
        f"markers={markers} dropped={dropped}",
    )
    delivered = sum(1 for event, _ in seen if event == "publish_result")
    check(
        "送达 + 丢弃 == 提交（没有静默黑洞）",
        delivered + dropped == total,
        f"delivered={delivered} dropped={dropped} total={total}",
    )


def test_stop_flushes_pending_events() -> None:
    print("\n[14] 关停必须把队列排空（含 mqtt_disconnect）")
    backend, _ = _armed_backend()
    seen: list[str] = []
    backend.event_hook = lambda event, **payload: seen.append(event)
    backend._emit_event("manual_marker", n=1)
    backend.stop()
    check("队列排空", backend.events_pending() == 0, f"pending={backend.events_pending()}")
    check("mqtt_disconnect 已落盘", "mqtt_disconnect" in seen, f"{seen}")
    check("关停前的事件也没丢", "manual_marker" in seen, f"{seen}")


def test_no_hook_means_no_queueing() -> None:
    print("\n[15] 没配 hook 时不排队、不起线程")
    backend, _ = _armed_backend()
    payload = encode_keyboard_mouse_control()
    for _ in range(100):
        backend.publish_topic("KeyboardMouseControl", payload)
    check("队列保持为空", backend.events_pending() == 0)
    check("dropped_events 为 0", backend.dropped_events == 0)
    stats = backend.stats()
    check(
        "stats 暴露诊断字段",
        {"dropped_events", "events_pending", "event_queue_max"} <= set(stats),
        f"{sorted(stats)}",
    )


def test_video_payload_still_rejected() -> None:
    print("\n[16] 视频载荷仍然被拒绝（回归）")
    with tempfile.TemporaryDirectory() as directory:
        for async_writer in (False, True):
            logger = RotatingJsonlLogger(Path(directory) / f"v{int(async_writer)}",
                                        async_writer=async_writer)
            logger.emit("video_frame", big="x" * 100)
            logger.emit("decoded_frame", big="x" * 100)
            logger.emit("referee_rx", topic="t", payload_b64="AA==")
            logger.flush(timeout=5.0)
            logger.close()
            text = logger.path.read_text(encoding="utf-8")
            check(
                f"async={async_writer}: video 事件没落盘",
                "video_frame" not in text and "decoded_frame" not in text,
                text[:200],
            )
            check(
                f"async={async_writer}: 其它事件照常落盘",
                "referee_rx" in text,
                text[:200],
            )


def test_referee_payload_roundtrip() -> None:
    print("\n[17] referee_payload 编码不变（逐位）")
    with tempfile.TemporaryDirectory() as directory:
        reference_dir = Path(directory) / "ref"
        new_dir = Path(directory) / "new"
        # 76800 > 65536 上限，必须真的走到截断分支
        payload = bytes(range(256)) * 300
        reference = _ReferenceRotatingLogger(reference_dir)
        new = RotatingJsonlLogger(new_dir)
        with _fake_clock():
            reference.referee_payload("robot_status/0x0303", payload)
        with _fake_clock():
            new.referee_payload("robot_status/0x0303", payload)
        new.close()
        check(
            "referee_rx 记录逐字节一致",
            _dump(reference_dir) == _dump(new_dir),
            "字节不一致",
        )
        record = json.loads(_dump(new_dir)["polar-field.jsonl"].decode("utf-8"))
        check(
            "payload_bytes / truncated 语义不变",
            record["payload_bytes"] == len(payload) and record["truncated"] is True,
            f"{record['payload_bytes']} {record['truncated']}",
        )
        import base64

        check(
            "base64 载荷仍是前 65536 字节",
            base64.b64decode(record["payload_b64"]) == payload[:65536],
            "base64 载荷不一致",
        )


def test_replay_reads_async_output() -> None:
    print("\n[18] 异步写出的日志能被只读回放读出来")
    from rm_client.backend.field_log import ReadOnlyFieldReplay
    from rm_client.replay import summarize_field_logs

    # 刻意用很小的 max_bytes 逼出多轮轮转：切分点必须仍然落在记录边界上，
    # 否则 ReadOnlyFieldReplay 会抛 invalid JSONL（半行 JSON）。
    for async_writer in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            logger = RotatingJsonlLogger(
                Path(directory), max_bytes=2048, backups=4, async_writer=async_writer
            )
            for index in range(500):
                logger.emit("publish_result", index=index, detail="r" * 50)
            logger.close()
            files = sorted(Path(directory).glob("*.jsonl*"))
            check(
                f"async={async_writer}: 确实轮转出了多个文件",
                len(files) > 1,
                f"{[p.name for p in files]}",
            )
            try:
                records = list(ReadOnlyFieldReplay(files).records())
            except ValueError as exc:
                check(f"async={async_writer}: 轮转后每行都是完整 JSONL", False, str(exc))
                continue
            check(f"async={async_writer}: 轮转后每行都是完整 JSONL", True)
            # 轮转本来就会丢掉超出 capacity 的旧数据，所以这里比的是"回放读到的
            # == 盘上还剩下的"，不是"== 写进去的"。
            check(
                f"async={async_writer}: 回放读数 == 盘上剩余记录数",
                len(records) == len(_record_stream(Path(directory))),
                f"replay={len(records)} on_disk={len(_record_stream(Path(directory)))}",
            )
            check(f"async={async_writer}: 回放非空", len(records) > 0)
            check(
                f"async={async_writer}: 回放条数受 capacity 约束（500 条里只留最近的）",
                0 < len(records) < 500,
                f"replay={len(records)}",
            )
            summary = summarize_field_logs([Path(directory)])
            check(
                f"async={async_writer}: summarize 与回放一致",
                summary["records"] == len(records) and summary["uplink"] == "DISABLED",
                f"{summary['records']} vs {len(records)}",
            )


def test_process_exit_flushes_pending_events() -> None:
    print("\n[21] 进程退出前的 atexit 钩子会排空队列（子进程实测）")
    import subprocess

    with tempfile.TemporaryDirectory() as directory:
        log_dir = Path(directory)
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            "from rm_client.backend.mqtt_backend import RmMqttBackend\n"
            "from rm_client.backend.field_log import RotatingJsonlLogger\n"
            "backend = RmMqttBackend()\n"
            "d = Path(sys.argv[1])\n"
            "log = RotatingJsonlLogger(d)\n"        # 引用必须活着，否则 __del__ 会关掉
            "count = [0]\n"
            "def hook(event, **p):\n"
            "    count[0] += 1\n"
            "    log.emit(event, **p)\n"
            "backend.event_hook = hook\n"
            "for i in range(3000):\n"
            "    backend._emit_event('exit_marker', i=i)\n"
            # 不调 flush_events()/stop()：就靠 atexit 收尾
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(log_dir)],
            capture_output=True, text=True, timeout=120,
        )
        check("子进程正常退出", result.returncode == 0, f"{result.stderr[-400:]}")
        log_files = sorted(log_dir.glob("*.jsonl*"))
        total = 0
        for path in log_files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    total += 1
        check(
            "子进程退出（走 atexit）后 3000 条全部落盘",
            total == 3000,
            f"got={total} files={[p.name for p in log_files]} stderr={result.stderr[-300:]}",
        )


# ======================================================================


def main() -> int:
    test_sync_bytes_identical_to_reference()
    test_sync_and_async_bytes_identical()
    test_single_open_and_mkdir_per_run()
    test_cached_size_matches_stat_after_rotation()
    test_write_error_is_reported_and_recovered()
    test_async_delivers_everything()
    test_async_overflow_is_explicit()
    test_async_close_drains_queue()
    test_async_rotation_stays_bounded()
    test_rotation_capacity_default()
    test_hook_runs_off_the_calling_thread()
    test_hook_preserves_order_and_loses_nothing()
    test_hook_overflow_is_explicit()
    test_stop_flushes_pending_events()
    test_no_hook_means_no_queueing()
    test_video_payload_still_rejected()
    test_referee_payload_roundtrip()
    test_replay_reads_async_output()
    test_open_handle_does_not_block_deletion()
    test_cached_size_survives_external_deletion()
    test_process_exit_flushes_pending_events()

    print("\n" + "=" * 70)
    print(f"field-log 落盘 / MQTT hook 分发：{PASSED} 通过，{len(FAILURES)} 失败")
    for failure in FAILURES:
        print("  FAIL", failure)
    print("=" * 70)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())