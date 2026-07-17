"""Gestalt 键鼠分流（注入模型）。

设计（默认 **game-first**，不是「只放行 WASD」）：

进游戏（Inject Enabled 时）：
- **所有非 client 保留键** → 游戏（含 P 开赛/菜单、Space、E、Q、WASD…）
- WASD + L-Shift 底盘（也走注入）
- SpinMode 联动的 R（小陀螺；client 的 R=自瞄 时由保留表拦住）
- **Alt+1/2/3 部署**（装 HUD 前原生键，不可吞）
- 可选 Inject Mouse：原生鼠标视角（**与自瞄 RBExtAim 无关**）

只走 client：
- RightShift / F7–F9 / Escape(关 GUI) / Tab(地图)
- 模块键绑定（默认 R 自瞄、V 小陀螺…）——可动态更新

自瞄：外挂 RBExtAim，**禁止**用鼠标注入跟枪。
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from ctypes import wintypes
from typing import Deque, Iterable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200

WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
HC_ACTION = 0

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

# 底盘键（始终视为游戏键；client 模块不得占用 WASD/LShift）
CHASSIS_KEYS = frozenset({"W", "A", "S", "D", "SHIFT", "LSHIFT"})
CHASSIS_VKS = frozenset({0x57, 0x41, 0x53, 0x44, 0xA0})  # W A S D LShift
# 兼容旧名
INJECT_KEY_WHITELIST = frozenset({"W", "A", "S", "D", "SHIFT", "LSHIFT", "R"})
INJECT_VK_WHITELIST = CHASSIS_VKS
GAME_SPIN_VK = 0x52

# 游戏部署：长按 Alt + 1/2/3（主键盘与小键盘）
GAME_ALT_VKS = frozenset({0x12, 0xA4, 0xA5})  # VK_MENU / LMENU / RMENU
GAME_DEPLOY_DIGIT_VKS = frozenset({0x31, 0x32, 0x33, 0x61, 0x62, 0x63})  # 1-3, numpad 1-3

# 系统键：永不吞、不注入、不进 client 队列
OS_PASSTHROUGH_VKS = frozenset({
    0x5B,  # VK_LWIN
    0x5C,  # VK_RWIN
    0x5D,  # VK_APPS
    0x2C,  # VK_SNAPSHOT PrintScreen
})

# 固定 client 保留键（模块绑定之外）
BASE_CLIENT_RESERVED_VKS = frozenset({
    0xA1,  # RightShift → ClickGUI
    0x76,  # F7 inject toggle
    0x77,  # F8 GUI
    0x78,  # F9 mode
    0x1B,  # Escape
    0x09,  # Tab map hold
})

VK_MAP = {
    "W": 0x57,
    "A": 0x41,
    "S": 0x53,
    "D": 0x44,
    "SHIFT": 0xA0,
    "LSHIFT": 0xA0,
    "R": 0x52,
    "P": 0x50,
    "E": 0x45,
    "Q": 0x51,
    "F": 0x46,
    "C": 0x43,
    "V": 0x56,
    "SPACE": 0x20,
    "ESCAPE": 0x1B,
    "TAB": 0x09,
}


def key_name_to_vk(name: str) -> int | None:
    n = (name or "").strip().lower().replace(" ", "")
    if not n or n == "none":
        return None
    aliases = {
        "rshift": 0xA1,
        "rightshift": 0xA1,
        "lshift": 0xA0,
        "shift": 0xA0,
        "leftshift": 0xA0,
        "esc": 0x1B,
        "escape": 0x1B,
        "space": 0x20,
        "spacebar": 0x20,
        "tab": 0x09,
        "ctrl": 0x11,
        "control": 0x11,
        "lctrl": 0xA2,
        "rctrl": 0xA3,
        "alt": 0x12,
        "enter": 0x0D,
        "return": 0x0D,
        "backspace": 0x08,
    }
    if n in aliases:
        return aliases[n]
    if len(n) == 1:
        ch = n.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    if n.startswith("f") and n[1:].isdigit():
        fi = int(n[1:])
        if 1 <= fi <= 24:
            return 0x70 + fi - 1
    if n in VK_MAP:
        return VK_MAP[n]
    up = n.upper()
    if up in VK_MAP:
        return VK_MAP[up]
    return None

VK_NAME = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0x1B: "Escape",
    0x20: "Space",
    0xA0: "Shift",
    0xA1: "RightShift",
    0xA2: "Ctrl",
    0xA3: "Ctrl",
    0xA4: "Alt",
    0xA5: "Alt",
    **{0x70 + i: f"F{i + 1}" for i in range(12)},
}

MAPVK_VK_TO_VSC = 0
LLKHF_INJECTED = 0x00000010
LLKHF_UP = 0x00000080


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD))


class INPUT_UNION(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))


class POINT(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageW.restype = wintypes.LPARAM
user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(POINT))
user32.ScreenToClient.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetWindowPos.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
)
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_long
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _key_lparam(scan: int, pressed: bool, *, was_down: bool = False) -> int:
    lp = 1 | ((scan & 0xFF) << 16)
    if was_down or not pressed:
        lp |= 1 << 30
    if not pressed:
        lp |= 1 << 31
    return lp


def vk_to_key_name(vk: int) -> str:
    if vk in VK_NAME:
        return VK_NAME[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    return f"VK_{vk:02X}"


class GestaltInputInjector:
    """Game-first 键盘注入：默认键进游戏；仅 client 保留键留给 POLAR。"""

    DEFAULT_TITLE_HINTS = ("RobotBridgeDemo", "Gestalt", "Robot Bridge")

    def __init__(self) -> None:
        self.enabled = False
        self.inject_mouse = False
        self.client_hwnd: int = 0
        self.mouse_sensitivity = 1.0
        self.title_hints: tuple[str, ...] = self.DEFAULT_TITLE_HINTS
        self.hwnd: int = 0
        self.last_error = ""
        self.last_ok_at = 0.0
        self._held: set[str] = set()
        self._held_vks: set[int] = set()  # 非底盘游戏键按住表
        self._deploy_held: set[int] = set()  # 部署组合中按下的 vk（Alt/1/2/3）
        self.status = "idle"
        self._client_topmost = False
        self._mouse_mode_armed = False
        self._client_reserved_vks: set[int] = set(BASE_CLIENT_RESERVED_VKS)

        self._hook = None
        self._hook_proc = None
        self._hook_thread: threading.Thread | None = None
        self._hook_thread_id = 0
        self._hook_ready = threading.Event()
        self._hook_stop = threading.Event()
        self._hook_lock = threading.Lock()

        self._client_key_queue: Deque[tuple[str, bool, int]] = deque(maxlen=512)
        self._queue_lock = threading.Lock()

        # Input Bridge DLL (in-process inject via named pipe)
        self.use_input_bridge = True
        self.bridge_exclusive = False
        self._bridge = None  # InputBridgeClient | None

        # 兼容旧字段
        self.focus_game = False
        self.keep_client_focus = True
        self.mouse_look = False
        self.suppress_next_mouse_delta = False

    def set_client_hwnd(self, hwnd: int) -> None:
        self.client_hwnd = int(hwnd) if hwnd else 0

    def set_input_bridge_options(
        self,
        *,
        use_bridge: bool = True,
        exclusive: bool = False,
        game_pid: int = 0,
    ) -> None:
        """Prefer polar_input_bridge.dll pipe when injected."""
        self.use_input_bridge = bool(use_bridge)
        self.bridge_exclusive = bool(exclusive)
        if not self.use_input_bridge:
            self._bridge_disconnect()
            return
        if not self.enabled:
            return
        self._bridge_ensure(game_pid=game_pid)

    def _bridge_disconnect(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.disconnect()
            except Exception:
                pass
        self._bridge = None

    def _bridge_ensure(self, game_pid: int = 0) -> bool:
        if not self.use_input_bridge:
            return False
        try:
            from .input_bridge import get_input_bridge
        except Exception as exc:
            self.last_error = f"input_bridge import: {exc}"
            return False
        br = get_input_bridge()
        self._bridge = br
        if br.connected:
            if self.bridge_exclusive:
                br.set_exclusive(True)
            return True
        if br.connect(game_pid=game_pid, timeout_s=1.2):
            if self.bridge_exclusive:
                br.set_exclusive(True)
            else:
                br.set_exclusive(False)
            return True
        self.last_error = br.last_error or "input bridge not connected (inject DLL first)"
        return False

    def _bridge_ok(self) -> bool:
        return bool(self.use_input_bridge and self._bridge is not None and self._bridge.connected)

    def set_client_reserved_keys(self, names: Iterable[str] | None = None, vks: Iterable[int] | None = None) -> None:
        """更新 client 保留键（模块绑定 R/V…）。底盘 WASD/LShift 永远不会被保留。"""
        reserved = set(BASE_CLIENT_RESERVED_VKS)
        if vks:
            for vk in vks:
                try:
                    reserved.add(int(vk))
                except (TypeError, ValueError):
                    continue
        if names:
            for name in names:
                vk = key_name_to_vk(str(name))
                if vk is not None:
                    reserved.add(int(vk))
        # 底盘键强制进游戏
        reserved -= CHASSIS_VKS
        # Alt / 部署数字不进 client 保留
        reserved -= GAME_ALT_VKS
        reserved -= GAME_DEPLOY_DIGIT_VKS
        self._client_reserved_vks = reserved

    def is_client_reserved_vk(self, vk: int) -> bool:
        return int(vk) in self._client_reserved_vks

    def configure(
        self,
        *,
        enabled: bool,
        inject_mouse: bool = False,
        mouse_sensitivity: float = 1.0,
        title_hints: Iterable[str] | None = None,
        focus_game: bool = False,
        keep_client_focus: bool = True,
        mouse_look: bool = False,
        use_input_bridge: bool | None = None,
        bridge_exclusive: bool | None = None,
        game_pid: int = 0,
    ) -> None:
        want_enabled = bool(enabled)
        want_mouse = bool(inject_mouse or mouse_look) and want_enabled
        self.mouse_sensitivity = max(0.05, min(8.0, float(mouse_sensitivity)))
        if title_hints:
            self.title_hints = tuple(str(item) for item in title_hints if str(item).strip())
        if use_input_bridge is not None:
            self.use_input_bridge = bool(use_input_bridge)
        if bridge_exclusive is not None:
            self.bridge_exclusive = bool(bridge_exclusive)

        self.keep_client_focus = True
        self.focus_game = False
        self.mouse_look = want_mouse

        was_enabled = self.enabled
        was_mouse = self.inject_mouse

        if not want_enabled:
            if was_enabled:
                self._teardown_all()
            self.enabled = False
            self.inject_mouse = False
            self.status = "disabled"
            self.last_error = ""
            return

        self.enabled = True
        self.inject_mouse = want_mouse
        ok_win = self.ensure_ready()
        bridge_ok = False
        if self.use_input_bridge:
            bridge_ok = self._bridge_ensure(game_pid=game_pid)
        else:
            self._bridge_disconnect()

        if bridge_ok:
            self.status = "bridge+mouse" if want_mouse else "bridge"
            self.last_error = ""
        elif not ok_win:
            self.status = "error"
        else:
            self.status = "game+mouse" if want_mouse else "game-keys"

        self._ensure_hook(True)

        if want_mouse and not was_mouse:
            self._arm_mouse_mode()
        elif not want_mouse and was_mouse:
            self._disarm_mouse_mode()
        elif want_mouse and not bridge_ok:
            self.status = "game+mouse"

    def _teardown_all(self) -> None:
        self._disarm_mouse_mode()
        self._ensure_hook(False)
        self.release_all()
        try:
            if self._bridge is not None:
                self._bridge.set_exclusive(False)
                self._bridge.release_all()
        except Exception:
            pass
        self._bridge_disconnect()

    def _arm_mouse_mode(self) -> None:
        """鼠标视角标记。不抢游戏前台（避免 client 失焦、光标乱跳）。"""
        # Bridge 路径下保持 client 前台；不再 SetForeground 到游戏。
        self._mouse_mode_armed = True
        if self._bridge_ok():
            self.status = "bridge+mouse"
        else:
            self.status = "game+mouse"

    def _disarm_mouse_mode(self) -> None:
        if self._mouse_mode_armed or self._client_topmost:
            self._set_client_topmost(False)
            if self.client_hwnd:
                self._force_foreground(self.client_hwnd)
        self._mouse_mode_armed = False

    def refresh_window(self) -> bool:
        hwnd = self._find_game_window()
        if not hwnd:
            self.hwnd = 0
            self.last_error = "未找到游戏窗口（RobotBridgeDemo/Gestalt）"
            self.status = "error"
            return False
        self.hwnd = int(hwnd)
        self.last_error = ""
        if self.enabled:
            self.status = "game+mouse" if self.inject_mouse else "game-keys"
        return True

    def ensure_ready(self) -> bool:
        if not self.enabled:
            self.status = "disabled"
            return False
        if self.hwnd and user32.IsWindow(wintypes.HWND(self.hwnd)):
            return True
        return self.refresh_window()

    def drain_client_keys(self) -> list[tuple[str, bool, int]]:
        with self._queue_lock:
            items = list(self._client_key_queue)
            self._client_key_queue.clear()
        return items

    def _enqueue_client_key(self, vk: int, pressed: bool) -> None:
        name = vk_to_key_name(int(vk))
        with self._queue_lock:
            self._client_key_queue.append((name, pressed, int(vk)))

    def _ensure_hook(self, want: bool) -> None:
        with self._hook_lock:
            if want and self._hook_thread and self._hook_thread.is_alive():
                return
            if not want:
                self._stop_hook_thread()
                return
            self._start_hook_thread()

    def _start_hook_thread(self) -> None:
        self._stop_hook_thread()
        self._hook_stop.clear()
        self._hook_ready.clear()
        self._hook_proc = LowLevelKeyboardProc(self._ll_keyboard_proc)
        t = threading.Thread(target=self._hook_thread_main, name="gestalt-kb-hook", daemon=True)
        self._hook_thread = t
        t.start()
        self._hook_ready.wait(timeout=2.0)

    def _stop_hook_thread(self) -> None:
        self._hook_stop.set()
        if self._hook_thread_id:
            try:
                user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._hook_thread and self._hook_thread.is_alive():
            self._hook_thread.join(timeout=1.5)
        self._hook_thread = None
        self._hook_thread_id = 0
        self._hook = None

    def _hook_thread_main(self) -> None:
        self._hook_thread_id = kernel32.GetCurrentThreadId()
        mod = kernel32.GetModuleHandleW(None)
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, mod, 0)
        self._hook = hook
        self._hook_ready.set()
        if not hook:
            self.last_error = "键盘钩子安装失败（快捷键分流不可用）"
            return
        msg = wintypes.MSG()
        while not self._hook_stop.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if hook:
            user32.UnhookWindowsHookEx(hook)
        self._hook = None

    def _ll_keyboard_proc(self, nCode, wParam, lParam):  # type: ignore[no-untyped-def]
        try:
            if nCode == HC_ACTION and self.enabled:
                info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.flags & LLKHF_INJECTED:
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

                vk = int(info.vkCode)
                msg = int(wParam)
                pressed = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = msg in (WM_KEYUP, WM_SYSKEYUP) or bool(info.flags & LLKHF_UP)
                if is_up:
                    pressed = False
                if not (pressed or is_up or msg in (WM_KEYUP, WM_SYSKEYUP, WM_KEYDOWN, WM_SYSKEYDOWN)):
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

                # —— 系统键（Win / Apps / PrtSc）：永久放行 ——
                if vk in OS_PASSTHROUGH_VKS:
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

                # —— 部署：Alt + 1/2/3 ——
                if self._is_deploy_combo_vk(vk, pressed):
                    self._forward_deploy_key(vk, pressed)
                    self.last_ok_at = time.perf_counter()
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

                # —— client 保留键（RShift/F7-F9/模块绑定…）——
                if self.is_client_reserved_vk(vk):
                    if self._is_client_foreground():
                        # Qt 直接收，避免重复入队
                        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
                    # 游戏前台：吞掉并转 client（自瞄/GUI 热键）
                    self._enqueue_client_key(vk, pressed)
                    return 1

                # —— 其余全部 = 游戏键（含 P / Space / WASD / E / Q…）——
                self._route_game_key(vk, pressed)
                self.last_ok_at = time.perf_counter()
                if self._is_game_foreground():
                    # 游戏已前台：原生即可，不必再 SendInput（防双击）
                    return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)
                # client 前台：已注入游戏，吞掉避免 Qt 误吃 P/WASD
                return 1
        except Exception:
            pass
        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _route_game_key(self, vk: int, pressed: bool) -> bool:
        """把游戏键同步到游戏窗口；维护 held 表。"""
        # 底盘键用字符串 held（兼容 apply_chassis_keys）
        if vk in CHASSIS_VKS:
            if vk == 0xA0:
                key = "SHIFT"
            else:
                key = chr(vk) if 0x41 <= vk <= 0x5A else f"VK_{vk:02X}"
            was = key in self._held
            if pressed and not was:
                ok = self._inject_key_to_game(vk, True, was_down=False)
                if ok:
                    self._held.add(key)
                return ok
            if not pressed and was:
                ok = self._inject_key_to_game(vk, False, was_down=True)
                self._held.discard(key)
                return ok
            return True

        was = vk in self._held_vks
        if pressed and not was:
            ok = self._inject_key_to_game(vk, True, was_down=False)
            if ok:
                self._held_vks.add(vk)
            return ok
        if not pressed and was:
            ok = self._inject_key_to_game(vk, False, was_down=True)
            self._held_vks.discard(vk)
            return ok
        if pressed and was:
            # 自动重复：仍注入 KEYDOWN was_down
            return self._inject_key_to_game(vk, True, was_down=True)
        return True

    def inject_vk(self, vk: int, pressed: bool) -> bool:
        """供 Qt 前台路径直接注入任意游戏键（P 等）。"""
        if not self.enabled:
            return False
        if self.is_client_reserved_vk(int(vk)):
            return False
        return self._route_game_key(int(vk), bool(pressed))

    def _is_alt_physically_down(self) -> bool:
        for vk in GAME_ALT_VKS:
            try:
                if user32.GetAsyncKeyState(vk) & 0x8000:
                    return True
            except Exception:
                pass
        return False

    def _is_deploy_combo_vk(self, vk: int, pressed: bool) -> bool:
        """Alt 本体，或 Alt 按下时的 1/2/3（含小键盘），以及组合释放过程中的残留。"""
        if vk in GAME_ALT_VKS:
            return True
        if vk in GAME_DEPLOY_DIGIT_VKS:
            if self._is_alt_physically_down() or vk in self._deploy_held:
                return True
            # 按下瞬间 Alt 状态偶发滞后：若任意 Alt 在 _deploy_held 也放行
            if any(a in self._deploy_held for a in GAME_ALT_VKS):
                return True
        return False

    def _forward_deploy_key(self, vk: int, pressed: bool) -> None:
        """部署键：游戏前台原生放行；client 前台则 Post/Send 到游戏窗口。"""
        if pressed:
            self._deploy_held.add(vk)
        else:
            self._deploy_held.discard(vk)
        # 游戏已前台：CallNextHookEx 足够，不必再 SendInput 防双击
        if self._is_game_foreground():
            return
        if not self.ensure_ready():
            return
        was = not pressed  # 释放时 was_down=True
        self._inject_key_to_game(vk, pressed, was_down=was)

    def _inject_key_to_game(self, vk: int, pressed: bool, *, was_down: bool = False) -> bool:
        """把键打进游戏：优先 Input Bridge DLL，失败再外置 PostMessage/SendInput。"""
        if self.use_input_bridge:
            if not self._bridge_ok():
                self._bridge_ensure()
            if self._bridge_ok() and self._bridge is not None:
                scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFF
                ok = self._bridge.key(int(vk), bool(pressed), scancode=scan)
                if ok:
                    self.last_ok_at = time.perf_counter()
                    self.status = "bridge+mouse" if self.inject_mouse else "bridge"
                    self.last_error = ""
                    return True
                # fall through to legacy

        if not self.hwnd:
            if not self.ensure_ready():
                return False
        # Alt / 系统键用 SYSKEY 消息更贴近真实部署组合
        is_alt = vk in GAME_ALT_VKS
        scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFF
        if is_alt:
            msg = WM_SYSKEYDOWN if pressed else WM_SYSKEYUP
        elif self._is_alt_physically_down() or any(a in self._deploy_held for a in GAME_ALT_VKS):
            msg = WM_SYSKEYDOWN if pressed else WM_SYSKEYUP
        else:
            msg = WM_KEYDOWN if pressed else WM_KEYUP
        lp = _key_lparam(scan, pressed, was_down=was_down)
        if is_alt or self._is_alt_physically_down():
            lp |= 1 << 29  # context code: Alt down
        hwnd = wintypes.HWND(self.hwnd)
        ok = False
        try:
            ok = bool(user32.PostMessageW(hwnd, msg, vk, lp)) or ok
        except Exception:
            pass
        try:
            user32.SendMessageW(hwnd, msg, vk, lp)
            ok = True
        except Exception:
            pass
        if self._is_game_foreground() or self.inject_mouse:
            if not self._is_game_foreground() and self.inject_mouse:
                self._force_foreground(self.hwnd)
            if self._send_key_input(scan, pressed):
                ok = True
        elif not self._is_game_foreground():
            prev_client = self.client_hwnd
            self._force_foreground(self.hwnd)
            if self._send_key_input(scan, pressed):
                ok = True
            if prev_client and not self.inject_mouse:
                self._force_foreground(prev_client)
        if ok:
            self.last_ok_at = time.perf_counter()
            self.status = "legacy+mouse" if self.inject_mouse else "legacy-keys"
        else:
            self.last_error = "键注入游戏失败"
            self.status = "error"
        return ok

    def _send_key_input(self, scan: int, pressed: bool) -> bool:
        flags = KEYEVENTF_SCANCODE
        if not pressed:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)),
        )
        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1

    def set_key(self, key: str, pressed: bool, *, force_game: bool = False) -> bool:
        """注入游戏键。force_game=True 时忽略 client 保留表（用于映射：V→游戏R 等）。"""
        key = key.upper()
        if key == "LSHIFT":
            key = "SHIFT"
        vk = VK_MAP.get(key) or key_name_to_vk(key)
        if vk is None:
            return False
        if (
            not force_game
            and self.is_client_reserved_vk(int(vk))
            and key not in CHASSIS_KEYS
        ):
            return False
        # bridge 可无 hwnd；legacy 需要窗口
        if not self._bridge_ok() and not self.ensure_ready():
            return False
        if key in CHASSIS_KEYS or force_game or key in ("R",):
            if pressed and key in self._held:
                return True
            if not pressed and key not in self._held:
                return True
            was_down = key in self._held
            ok = self._inject_key_to_game(int(vk), pressed, was_down=was_down)
            if ok:
                if pressed:
                    self._held.add(key)
                else:
                    self._held.discard(key)
            return ok
        return self.inject_vk(int(vk), pressed)

    def set_game_spin(self, enabled: bool) -> bool:
        """SpinMode 开/关 → 游戏按住/松开 R（小陀螺）。

        注意：client 的 R 是自瞄保留键，不能走 set_key 默认路径（会被拦）。
        """
        if not self.enabled:
            # 仍尝试松键，避免卡在游戏陀螺
            if not enabled:
                vk = VK_MAP.get("R", 0x52)
                self._inject_key_to_game(int(vk), False, was_down=True)
                self._held.discard("R")
            return False
        # force_game：把 R 注入游戏，即使 R 在 client 保留表里
        return self.set_key("R", bool(enabled), force_game=True)

    def apply_chassis_keys(self, key_state: dict[str, bool]) -> bool:
        if not self.enabled:
            return False
        if not self.ensure_ready():
            return False
        ok_all = True
        any_action = False
        for key in ("W", "A", "S", "D", "SHIFT"):
            want = bool(key_state.get(key, False) or key_state.get("LSHIFT", False) if key == "SHIFT" else key_state.get(key, False))
            if key == "SHIFT":
                want = bool(key_state.get("SHIFT", False) or key_state.get("LSHIFT", False))
            held = key in self._held
            if want == held:
                continue
            any_action = True
            if not self.set_key(key, want):
                ok_all = False
        if not any_action:
            self.status = "game+mouse" if self.inject_mouse else "game-keys"
            return True
        return ok_all

    def mouse_move(
        self,
        dx: float,
        dy: float,
        *,
        force: bool = False,
        restore_cursor: bool = True,
    ) -> bool:
        """相对鼠标视角。仅 Inject Mouse；自瞄勿 force（请用 RBExtAim）。

        SendInput 会动系统光标；默认注入后 SetCursorPos 还原。
        中心锁定视角可传 restore_cursor=False，由上层 QCursor.setPos 回中。
        """
        if not self.enabled:
            return False
        if not self.inject_mouse and not force:
            return False
        if force and not self.inject_mouse:
            return False
        sx = int(round(dx * self.mouse_sensitivity))
        sy = int(round(dy * self.mouse_sensitivity))
        if sx == 0 and sy == 0:
            return True
        if abs(sx) > 200 or abs(sy) > 200:
            return True

        saved = POINT()
        have_pos = bool(user32.GetCursorPos(ctypes.byref(saved))) if restore_cursor else False

        ok = False
        if self.use_input_bridge:
            if not self._bridge_ok():
                self._bridge_ensure()
            if self._bridge_ok() and self._bridge is not None:
                ok = self._bridge.mouse_move(sx, sy)
                if ok:
                    self.last_ok_at = time.perf_counter()
                    self.status = "bridge+mouse"

        if not ok:
            if not self.ensure_ready():
                if have_pos:
                    user32.SetCursorPos(int(saved.x), int(saved.y))
                return False
            # Legacy: nudge game foreground so UE receives deltas
            if self.hwnd and not self._is_game_foreground():
                self._force_foreground(self.hwnd)
            ok = self._send_mouse_move(sx, sy)
            if not ok:
                ok = self._post_mouse_move_delta(sx, sy)
            if ok:
                self.last_ok_at = time.perf_counter()
                self.status = "legacy+mouse"
            if self.client_hwnd and not self.inject_mouse:
                pass
            elif self.client_hwnd and restore_cursor:
                # Prefer client stays interactive after legacy inject
                try:
                    user32.SetForegroundWindow(wintypes.HWND(self.client_hwnd))
                except Exception:
                    pass

        if have_pos and restore_cursor:
            user32.SetCursorPos(int(saved.x), int(saved.y))
        return ok

    def mouse_button(self, button: str, pressed: bool) -> bool:
        return True

    def release_all(self) -> None:
        if self._bridge_ok() and self._bridge is not None:
            try:
                self._bridge.release_all()
            except Exception:
                pass
        for key in list(self._held):
            vk = VK_MAP.get(key)
            if vk is not None:
                self._inject_key_to_game(vk, False, was_down=True)
        self._held.clear()
        for vk in list(self._held_vks):
            self._inject_key_to_game(int(vk), False, was_down=True)
        self._held_vks.clear()
        for vk in list(self._deploy_held):
            self._inject_key_to_game(int(vk), False, was_down=True)
        self._deploy_held.clear()

    def _is_game_foreground(self) -> bool:
        if not self.hwnd:
            return False
        return int(user32.GetForegroundWindow() or 0) == int(self.hwnd)

    def _is_client_foreground(self) -> bool:
        if not self.client_hwnd:
            return False
        return int(user32.GetForegroundWindow() or 0) == int(self.client_hwnd)

    def _set_client_topmost(self, topmost: bool) -> None:
        if not self.client_hwnd or not user32.IsWindow(wintypes.HWND(self.client_hwnd)):
            return
        insert_after = HWND_TOPMOST if topmost else HWND_NOTOPMOST
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        try:
            user32.SetWindowPos(wintypes.HWND(self.client_hwnd), wintypes.HWND(insert_after), 0, 0, 0, 0, flags)
            self._client_topmost = topmost
        except Exception:
            pass

    def _force_foreground(self, hwnd: int) -> bool:
        target = wintypes.HWND(hwnd)
        if not user32.IsWindow(target):
            return False
        try:
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            target_tid = user32.GetWindowThreadProcessId(target, None)
            cur_tid = kernel32.GetCurrentThreadId()
            attached_fg = attached_cur = False
            if fg_tid and fg_tid != target_tid:
                attached_fg = bool(user32.AttachThreadInput(fg_tid, target_tid, True))
            if cur_tid != target_tid:
                attached_cur = bool(user32.AttachThreadInput(cur_tid, target_tid, True))
            user32.BringWindowToTop(target)
            ok = bool(user32.SetForegroundWindow(target))
            if attached_cur:
                user32.AttachThreadInput(cur_tid, target_tid, False)
            if attached_fg:
                user32.AttachThreadInput(fg_tid, target_tid, False)
            return ok or int(user32.GetForegroundWindow() or 0) == int(hwnd)
        except Exception:
            return False

    def _send_mouse_move(self, dx: int, dy: int) -> bool:
        inp = INPUT(
            type=INPUT_MOUSE,
            union=INPUT_UNION(
                mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=None)
            ),
        )
        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1

    def _post_mouse_move_delta(self, dx: int, dy: int) -> bool:
        if not self.hwnd:
            return False
        pt = POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return False
        client = POINT(pt.x + dx, pt.y + dy)
        user32.ScreenToClient(wintypes.HWND(self.hwnd), ctypes.byref(client))
        x, y = max(0, int(client.x)), max(0, int(client.y))
        lp = (y << 16) | (x & 0xFFFF)
        try:
            return bool(user32.PostMessageW(wintypes.HWND(self.hwnd), WM_MOUSEMOVE, 0, lp))
        except Exception:
            return False

    def _find_game_window(self) -> int:
        matches: list[tuple[int, str]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):  # type: ignore[no-untyped-def]
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            lower = title.lower()
            if "polar" in lower and "client" in lower:
                return True
            for hint in self.title_hints:
                if hint.lower() in lower:
                    matches.append((int(hwnd), title))
                    break
            return True

        user32.EnumWindows(enum_proc, 0)
        if not matches:
            return 0
        for hwnd, title in matches:
            if "robotbridge" in title.lower() or "robot bridge" in title.lower():
                return hwnd
        return matches[0][0]

    def enter_mouse_look(self) -> bool:
        self.inject_mouse = True
        self._arm_mouse_mode()
        return True

    def exit_mouse_look(self) -> None:
        self._disarm_mouse_mode()

    def refresh_mouse_look_focus(self) -> None:
        return
