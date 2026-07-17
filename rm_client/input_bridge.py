"""Named-pipe client for polar_input_bridge.dll (in-game key/mouse inject).

Pipe: \\\\.\\pipe\\POLARInputBridge.<game_pid>
Protocol: see polar_input_bridge/src/ipc/protocol.h
"""

from __future__ import annotations

import ctypes
import struct
import subprocess
import threading
import time
from ctypes import wintypes
from typing import Optional

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PIPE_READMODE_BYTE = 0x00000000

MAGIC = b"PLIN"
VERSION = 1

MSG_HELLO = 1
MSG_KEY = 2
MSG_MOUSE_MOVE = 3
MSG_MOUSE_BUTTON = 4
MSG_SET_EXCLUSIVE = 5
MSG_HEARTBEAT = 6
MSG_RELEASE_ALL = 7
MSG_STATUS = 8
MSG_STATUS_REPLY = 9

HEADER_FMT = "<4sHHI"  # magic, version, type, size
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class InputBridgeClient:
    """Process-local IPC to injected DLL."""

    def __init__(self) -> None:
        self._handle: Optional[int] = None
        self._lock = threading.Lock()
        self._game_pid = 0
        self._exclusive = False
        self._last_ok = 0.0
        self._hb_thread: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()
        self.status = "disconnected"
        self.last_error = ""

    @property
    def connected(self) -> bool:
        return self._handle is not None

    @property
    def game_pid(self) -> int:
        return self._game_pid

    def connect(self, game_pid: int = 0, timeout_s: float = 2.0) -> bool:
        self.disconnect()
        pids = [game_pid] if game_pid > 0 else self.discover_game_pids()
        if not pids:
            # also try scanning common pipe suffix by probing recent PIDs from tasklist
            pids = self.discover_game_pids()
        deadline = time.perf_counter() + max(0.2, timeout_s)
        last_err = "no pipe"
        for pid in pids:
            if time.perf_counter() > deadline:
                break
            name = rf"\\.\pipe\POLARInputBridge.{int(pid)}"
            if self._open_pipe(name):
                self._game_pid = int(pid)
                if self.hello():
                    self.status = "connected"
                    self.last_error = ""
                    self._start_heartbeat()
                    return True
                self._close_handle()
                last_err = "hello failed"
            else:
                last_err = f"open failed pid={pid}"
        # probe any open POLARInputBridge.* via retry list expanded
        for pid in self._enumerate_candidate_pids():
            if time.perf_counter() > deadline:
                break
            name = rf"\\.\pipe\POLARInputBridge.{int(pid)}"
            if self._open_pipe(name) and self.hello():
                self._game_pid = int(pid)
                self.status = "connected"
                self.last_error = ""
                self._start_heartbeat()
                return True
            self._close_handle()
        self.status = "disconnected"
        self.last_error = last_err
        return False

    def disconnect(self) -> None:
        self._hb_stop.set()
        if self._hb_thread and self._hb_thread.is_alive():
            self._hb_thread.join(timeout=0.5)
        self._hb_thread = None
        try:
            self.release_all()
        except Exception:
            pass
        try:
            self.set_exclusive(False)
        except Exception:
            pass
        self._close_handle()
        self.status = "disconnected"
        self._game_pid = 0

    def hello(self) -> bool:
        payload = struct.pack("<I", ctypes.windll.kernel32.GetCurrentProcessId())
        rep = self._transact(MSG_HELLO, payload, expect_reply=True)
        return rep is not None

    def key(self, vk: int, down: bool, scancode: int = 0) -> bool:
        # KeyPayload: vk u16, down u8, pad u8, scancode u16, pad2 u16
        payload = struct.pack(
            "<HBBHH",
            int(vk) & 0xFFFF,
            1 if down else 0,
            0,
            int(scancode) & 0xFFFF,
            0,
        )
        return self._send(MSG_KEY, payload)

    def mouse_move(self, dx: int, dy: int) -> bool:
        dx = max(-32768, min(32767, int(dx)))
        dy = max(-32768, min(32767, int(dy)))
        return self._send(MSG_MOUSE_MOVE, struct.pack("<hh", dx, dy))

    def mouse_button(self, button: int, down: bool) -> bool:
        return self._send(MSG_MOUSE_BUTTON, struct.pack("<BB2x", int(button) & 0xFF, 1 if down else 0))

    def set_exclusive(self, on: bool) -> bool:
        ok = self._send(MSG_SET_EXCLUSIVE, struct.pack("<B3x", 1 if on else 0))
        if ok:
            self._exclusive = bool(on)
        return ok

    def release_all(self) -> bool:
        return self._send(MSG_RELEASE_ALL, b"")

    def heartbeat(self) -> bool:
        return self._send(MSG_HEARTBEAT, b"")

    def status_query(self) -> dict | None:
        rep = self._transact(MSG_STATUS, b"", expect_reply=True)
        if not rep or len(rep) < 12:
            return None
        ok, exclusive, _p0, _p1, hwnd, dll_pid = struct.unpack_from("<BBBBII", rep)
        return {
            "ok": bool(ok),
            "exclusive": bool(exclusive),
            "game_hwnd": int(hwnd),
            "dll_pid": int(dll_pid),
        }

    # ── internals ──────────────────────────────────────────────────

    def _start_heartbeat(self) -> None:
        self._hb_stop.clear()

        def loop() -> None:
            while not self._hb_stop.is_set():
                try:
                    self.heartbeat()
                except Exception:
                    break
                self._hb_stop.wait(0.75)

        self._hb_thread = threading.Thread(target=loop, name="polar-input-bridge-hb", daemon=True)
        self._hb_thread.start()

    def _open_pipe(self, name: str) -> bool:
        # Wait briefly for pipe
        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.WaitNamedPipeW.restype = wintypes.BOOL
        kernel32.WaitNamedPipeW(name, 500)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        h = kernel32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if h == INVALID_HANDLE_VALUE or h is None:
            return False
        self._handle = int(h)
        return True

    def _close_handle(self) -> None:
        if self._handle:
            try:
                kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            except Exception:
                pass
        self._handle = None

    def _send(self, msg_type: int, payload: bytes) -> bool:
        with self._lock:
            if not self._handle:
                return False
            header = struct.pack(HEADER_FMT, MAGIC, VERSION, int(msg_type), len(payload))
            try:
                self._write(header + payload)
                self._last_ok = time.perf_counter()
                return True
            except OSError as exc:
                self.last_error = str(exc)
                self._close_handle()
                self.status = "disconnected"
                return False

    def _transact(self, msg_type: int, payload: bytes, *, expect_reply: bool) -> bytes | None:
        with self._lock:
            if not self._handle:
                return None
            header = struct.pack(HEADER_FMT, MAGIC, VERSION, int(msg_type), len(payload))
            try:
                self._write(header + payload)
                if not expect_reply:
                    return b""
                rh = self._read_exact(HEADER_SIZE)
                magic, ver, rtype, size = struct.unpack(HEADER_FMT, rh)
                if magic != MAGIC or ver != VERSION:
                    return None
                body = self._read_exact(size) if size else b""
                self._last_ok = time.perf_counter()
                if rtype in (MSG_STATUS_REPLY, MSG_STATUS):
                    return body
                return body
            except OSError as exc:
                self.last_error = str(exc)
                self._close_handle()
                self.status = "disconnected"
                return None

    def _write(self, data: bytes) -> None:
        written = wintypes.DWORD(0)
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        ok = kernel32.WriteFile(
            wintypes.HANDLE(self._handle),
            data,
            len(data),
            ctypes.byref(written),
            None,
        )
        if not ok or written.value != len(data):
            raise OSError(f"WriteFile failed err={ctypes.get_last_error()}")

    def _read_exact(self, n: int) -> bytes:
        buf = ctypes.create_string_buffer(n)
        got = 0
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        while got < n:
            rd = wintypes.DWORD(0)
            ok = kernel32.ReadFile(
                wintypes.HANDLE(self._handle),
                ctypes.byref(buf, got),
                n - got,
                ctypes.byref(rd),
                None,
            )
            if not ok or rd.value == 0:
                raise OSError(f"ReadFile failed err={ctypes.get_last_error()}")
            got += rd.value
        return buf.raw[:n]

    @staticmethod
    def discover_game_pids() -> list[int]:
        pids: list[int] = []
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-Process | Where-Object { $_.ProcessName -match "
                    "'RobotBridge|Gestalt' } | Select-Object -ExpandProperty Id",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        except Exception:
            pass
        return pids

    def _enumerate_candidate_pids(self) -> list[int]:
        # Prefer game PIDs; also include any that might host the pipe after inject.
        return self.discover_game_pids()


_global_bridge: Optional[InputBridgeClient] = None


def get_input_bridge() -> InputBridgeClient:
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = InputBridgeClient()
    return _global_bridge
