"""Bounded field telemetry logs and a publish-free replay reader.

落盘路径有两条，写出的字节完全一致
------------------------------------

``RotatingJsonlLogger`` 是诊断证据链，所以"快"绝不能以"可能丢事件"换。
这里保留两条写盘通道，它们共用同一个记录编码（``_encode``），因此同样的输入
序列在两条通道下写出的 JSONL 逐字节相同：

``async_writer=False``（默认）
    ``emit()`` 返回前一定已经把这一行交给操作系统。代价被压到最低的方式是
    **句柄常开 + 字节数缓存**：原来每条事件都要
    ``mkdir(35µs) + stat(12µs) + open/write/close(53µs) = 100µs``，
    现在只剩一次 ``write()``（实测 2.1µs）。首次写入时才 mkdir/stat。

``async_writer=True``
    序列化仍在调用线程完成（所以诊断快照语义不变），随后把已经编码好的字节
    交给**一个有界队列**和一条常驻 daemon 写盘线程。队列满时事件被拒收，
    并显式记账：:attr:`dropped_events` 加一、:attr:`last_error` 立刻写明、
    并且下一次成功落盘会附带一条 ``field_log_dropped`` 记录，让证据链自己
    标出那段空洞。写盘失败时整批计入 ``dropped_events``，同样不静默。

轮转容量
--------
默认从 5×20MiB=100MiB 提到 8×64MiB=**512MiB**（见 ``capacity_bytes``）。
裁判回环洪泛修复之后仍然有 75Hz 的 ``publish_result`` 在写，实测约
32 MiB/h；100MiB 只够约 3 小时，512MiB 约 16 小时，见 ``docs`` 里的实测。
"""

from __future__ import annotations

import atexit
import base64
from collections import deque
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable, Iterator, Mapping
import weakref

#: 一次落盘最多合并多少条，避免极端情况下持锁写盘。
_WRITE_BATCH_MAX = 1024


#: 进程内所有 async writer 的弱引用集合，退出时统一 close()。
_ASYNC_LOGGERS: "weakref.WeakSet[RotatingJsonlLogger]" = weakref.WeakSet()
_ASYNC_LOCK = threading.Lock()


def _drain_async_loggers() -> None:  # pragma: no cover - 解释器退出路径
    with _ASYNC_LOCK:
        loggers = list(_ASYNC_LOGGERS)
    for logger in loggers:
        try:
            logger.close()
        except Exception:
            pass


atexit.register(_drain_async_loggers)


def _open_append(path: Path) -> Any:
    """打开一个**允许外部删除/替换**的追加写句柄。

    为什么不用内置 ``open(path, "ab")``
    ----------------------------------
    Windows 上 CRT 的 ``open()`` 默认是 ``_SH_DENYNO``：允许别人读写，但
    **不允许删除**。句柄常开之后（这正是本轮性能改动的前提），日志目录在
    客户端运行期间就删不掉了 —— 实测 ``TemporaryDirectory`` 收尾直接
    ``PermissionError: [WinError 32]``。现场表现是"日志文件删不掉/换不掉"，
    而这不是日志系统该有的行为。

    所以这里显式用 ``CreateFileW`` 带上
    ``FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE``，再用
    ``msvcrt.open_osfhandle`` 变回普通文件对象。任何一步失败都退回内置
    ``open()``（退化但不报错）。
    """
    if os.name == "nt":
        handle = _win_open_share_delete(str(path))
        if handle is not None:
            return handle
    return open(path, "ab", buffering=0)


def _win_open_share_delete(path: str) -> Any:  # pragma: no cover - 仅 Windows
    import ctypes
    from ctypes import wintypes

    import msvcrt

    generic_write = 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004  # READ|WRITE|DELETE
    open_always = 4
    attribute_normal = 0x00000080
    invalid_handle = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = wintypes.HANDLE
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    handle = create_file(
        path, generic_write, share_all, None, open_always, attribute_normal, None
    )
    if handle is None or handle == invalid_handle:
        return None
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND | os.O_BINARY)
    except OSError:
        # 默认的 c_int 返回值会在 x64 上把句柄截断，这里必须显式声明。
        close_handle = kernel32.CloseHandle
        close_handle.restype = wintypes.BOOL
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle(handle)
        return None
    try:
        return open(descriptor, "ab", buffering=0)
    except OSError:
        os.close(descriptor)
        return None


class RotatingJsonlLogger:
    """A tiny dependency-free rotating JSONL logger.

    Video payloads are deliberately rejected. Referee payloads may be recorded
    as bounded base64 strings so protocol regressions are reproducible.

    参数
    ----
    max_bytes / backups
        轮转阈值与备份份数，总容量 = ``max_bytes * (backups + 1)``。
        默认 64MiB×8 = 512MiB（原 20MiB×5 = 100MiB 太小，见模块 docstring）。
    async_writer
        是否把写盘搬到后台线程。默认关闭，保持"emit 返回即已落盘"的旧语义。
    queue_size
        async 模式下的有界队列长度（条）。满了就显式拒收并记账，绝不静默丢。
    """

    def __init__(
        self,
        directory: Path,
        *,
        filename: str = "polar-field.jsonl",
        max_bytes: int = 64 * 1024 * 1024,
        backups: int = 7,
        async_writer: bool = False,
        queue_size: int = 8192,
    ) -> None:
        self.directory = Path(directory)
        self.path = self.directory / filename
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(1, int(backups))
        self.async_writer = bool(async_writer)
        self.queue_size = max(64, int(queue_size))
        #: "没有落盘"的事件总数（队列拒收 + 写盘失败）。正常情况恒为 0。
        self.dropped_events = 0
        self._lock = threading.Lock()
        self.last_error = ""

        # --- 常开句柄 / 缓存字节数 -------------------------------------------
        self._handle: Any = None
        self._size = 0

        # --- async writer 状态 ----------------------------------------------
        self._queue: deque[bytes] = deque()
        self._cv = threading.Condition(threading.Lock())
        self._inflight = False
        self._unreported = 0
        self._worker: threading.Thread | None = None
        self._closed = False
        if self.async_writer:
            with _ASYNC_LOCK:
                _ASYNC_LOGGERS.add(self)

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------
    @property
    def capacity_bytes(self) -> int:
        """轮转日志的总容量上限（当前文件 + 全部备份）。"""
        return self.max_bytes * (self.backups + 1)

    def disk_usage_bytes(self) -> int:
        """当前已经在盘上的总字节数（含备份）。"""
        total = 0
        for index in range(self.backups + 1):
            suffix = "" if index == 0 else f".{index}"
            try:
                total += self.path.with_suffix(self.path.suffix + suffix).stat().st_size
            except OSError:
                continue
        return total

    def pending_events(self) -> int:
        """async 模式下还没落盘的条数（同步模式恒为 0）。"""
        with self._cv:
            return len(self._queue)

    # ------------------------------------------------------------------
    # 写入口
    # ------------------------------------------------------------------
    def emit(self, event: str, **fields: Any) -> None:
        if str(event).lower() in {"video_frame", "video_payload", "decoded_frame"}:
            return
        line = self._encode(event, fields)
        try:
            if self.async_writer:
                self._enqueue(line)
            else:
                self._write_line(line)
            # 有事件没落盘时不清空 last_error：那是一条还挂着的显式报错，
            # 写盘恢复并不代表证据链上的空洞消失了。
            if not self.dropped_events:
                self.last_error = ""
        except OSError as exc:
            # Logging must never break the 60/75 Hz control loops.
            self.last_error = str(exc)

    def referee_payload(self, topic: str, payload: bytes) -> None:
        bounded = bytes(payload[:65536])
        self.emit(
            "referee_rx",
            topic=topic,
            payload_b64=base64.b64encode(bounded).decode("ascii"),
            payload_bytes=len(payload),
            truncated=len(payload) > len(bounded),
        )

    # ------------------------------------------------------------------
    # 编码（同步/异步共用，保证两条通道写出的字节完全一致）
    # ------------------------------------------------------------------
    def _encode(self, event: str, fields: Mapping[str, Any]) -> bytes:
        record = {
            "schema": 1,
            "wall_time": time.time(),
            "monotonic": time.perf_counter(),
            "event": str(event),
        }
        record.update({key: self._safe(value) for key, value in fields.items()})
        return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    # ------------------------------------------------------------------
    # 同步写盘：句柄常开 + 字节数缓存
    # ------------------------------------------------------------------
    def _write_line(self, line: bytes) -> None:
        # 注意：**不在这里切块**。一条记录就是一条记录，切开会让轮转后的文件
        # 里出现半行 JSON，ReadOnlyFieldReplay 会直接抛 invalid JSONL。
        # 批写请走 _write_batch（它只在记录边界上切）。
        try:
            self._append(line)
        except OSError:
            # 常开句柄失效（文件被外部删掉/换掉）时重开一次再试。
            self._drop_handle()
            self._append(line)

    def _write_batch(self, records: list[bytes]) -> None:
        """把一批记录写下去，切分点**只落在记录边界**上。

        批可能远大于 max_bytes；如果整批一次 write，一个文件就能被顶穿到几倍
        于 max_bytes，磁盘占用失控。而按字节硬切又会在文件里留下半行 JSON。
        所以这里按记录聚合成 <=max_bytes 的块，逐块走 _write_line。
        """
        block: list[bytes] = []
        size = 0
        for line in records:
            if block and size + len(line) > self.max_bytes:
                self._write_line(b"".join(block))
                block, size = [], 0
            block.append(line)
            size += len(line)
        if block:
            self._write_line(b"".join(block))

    def _append(self, line: bytes) -> None:
        with self._lock:
            self._rotate_if_needed(len(line))
            handle = self._ensure_open()
            handle.write(line)
            self._size += len(line)

    def _ensure_open(self) -> Any:
        if self._handle is not None:
            return self._handle
        self.directory.mkdir(parents=True, exist_ok=True)
        self._handle = _open_append(self.path)
        try:
            self._size = self.path.stat().st_size
        except OSError:
            self._size = 0
        return self._handle

    def _drop_handle(self) -> None:
        handle, self._handle = self._handle, None
        self._size = 0
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def reopen(self) -> None:
        """丢弃缓存的句柄/字节数，下一条记录重新 mkdir + stat + open。"""
        with self._lock:
            self._drop_handle()

    def _rotate_if_needed(self, incoming: int) -> None:
        if self._handle is None:
            # 先开一次拿到真实盘上大小，判据和原来的 stat() 完全一致。
            self._ensure_open()
        if self._size + incoming <= self.max_bytes:
            return
        # Windows 不允许 rename 一个仍打开的文件，所以先放手柄。
        self._drop_handle()
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        if self.path.exists():
            os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
        self._size = 0

    # ------------------------------------------------------------------
    # 异步写盘
    # ------------------------------------------------------------------
    def _enqueue(self, line: bytes) -> None:
        if self._closed:
            # 已经 close() 之后还来事件：直接同步写，绝不丢。
            self._write_line(line)
            return
        with self._cv:
            if len(self._queue) >= self.queue_size:
                self.dropped_events += 1
                self._unreported += 1
                self.last_error = (
                    f"field_log 队列已满（{self.queue_size} 条），本次事件未落盘；"
                    f"累计未落盘 {self.dropped_events} 条"
                )
                return
            if not self._queue:
                self._cv.notify()
            self._queue.append(line)
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._run_writer, name="field-log-writer", daemon=True
                )
                self._worker.start()

    def _run_writer(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait(0.2)
                if not self._queue:
                    if self._closed:
                        return
                    continue
                batch: list[bytes] = []
                while self._queue and len(batch) < _WRITE_BATCH_MAX:
                    batch.append(self._queue.popleft())
                if self._closed:
                    while self._queue and len(batch) < _WRITE_BATCH_MAX * 8:
                        batch.append(self._queue.popleft())
                self._inflight = True
                report, self._unreported = self._unreported, 0
            try:
                if report:
                    # 证据链自曝：把"丢了多少"直接写进日志本身。
                    batch.insert(
                        0,
                        self._encode(
                            "field_log_dropped",
                            {"dropped": report, "reason": "queue_overflow"},
                        ),
                    )
                self._write_batch(batch)
                # 只要历史上丢过事件，last_error 就一直留着，直到有人重建 logger。
                # 写盘恢复本身不构成"问题已消除"，证据链上那段空洞还在。
                if not self.dropped_events:
                    self.last_error = ""
            except Exception as exc:  # noqa: BLE001 - 记账优先于传播
                self.dropped_events += len(batch)
                with self._cv:
                    self._unreported += report
                self.last_error = (
                    f"field_log 异步落盘失败，本批 {len(batch)} 条未落盘：{exc}"
                )
            finally:
                with self._cv:
                    self._inflight = False
                    self._cv.notify_all()

    # ------------------------------------------------------------------
    # 冲刷 / 关闭
    # ------------------------------------------------------------------
    def flush(self, timeout: float = 2.0) -> bool:
        """等到队列真正落盘。返回 True 表示已排空。"""
        if not self.async_writer:
            return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cv:
            while (self._queue or self._inflight) and time.monotonic() < deadline:
                self._cv.wait(0.02)
            return not (self._queue or self._inflight)

    def close(self, timeout: float = 1.0) -> None:
        """停止写盘线程并释放句柄。队列里剩下的会在本线程里直接写掉。"""
        self._closed = True
        with self._cv:
            self._cv.notify_all()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(max(0.0, float(timeout)))
        leftovers: list[bytes] = []
        with self._cv:
            report, self._unreported = self._unreported, 0
            while self._queue:
                leftovers.append(self._queue.popleft())
            self._inflight = False
            self._cv.notify_all()
        if leftovers or report:
            batch = leftovers
            if report:
                batch.insert(
                    0,
                    self._encode(
                        "field_log_dropped",
                        {"dropped": report, "reason": "queue_overflow"},
                    ),
                )
            try:
                self._write_batch(batch)
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001
                self.dropped_events += len(batch)
                self.last_error = (
                    f"field_log 关闭时补写失败，{len(batch)} 条未落盘：{exc}"
                )
        self.reopen()

    def __del__(self) -> None:  # pragma: no cover - GC 路径
        try:
            self.close(timeout=0.2)
        except Exception:
            pass

    @classmethod
    def _safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, bytes):
            return base64.b64encode(value[:65536]).decode("ascii")
        if isinstance(value, Mapping):
            return {str(k): cls._safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._safe(item) for item in value]
        return repr(value)


class ReadOnlyFieldReplay:
    """Iterate field records. This class intentionally has no publisher."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.paths = tuple(Path(path) for path in paths)

    def records(self) -> Iterator[dict[str, Any]]:
        for path in self.paths:
            # Accept a BOM on imported/operator-created logs while the native
            # logger itself continues to write canonical BOM-free UTF-8.
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{path}:{line_number}: invalid JSONL") from exc
                    if isinstance(record, dict):
                        yield record