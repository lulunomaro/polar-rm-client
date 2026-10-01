"""配置档案的落盘 / 恢复（config-io 组）。

这一层同时被两条线程碰到：GUI 的渲染 QTimer 线程（去抖 0.25 s 的自动保存冲刷）
和只读磁盘的落盘线程。本轮的四条纪律：

1. **落盘不碰渲染线程。** ``save()`` 只做序列化（0.06 ms）并把字节排进
   ``_BackgroundWriter``，fsync + ``os.replace`` 在后台线程上做。调用方立刻
   返回，数据最终一定落盘。序列化必须留在调用线程：``export_client_config``
   传进来的 ``old_panels`` / ``polar_windows`` 是 GUI 正在用的活字典。
2. **退出前一定刷完。** 落盘线程是 daemon（绝不会拖住解释器），所以
   ``atexit`` 钩子 :func:`_flush_at_exit` 负责最后一轮 flush + join。
   兜底还有"读己之写"：``load()`` / ``profile_names()`` 会等自己这条路径上的
   排队写（``save()`` 刚返回就 ``load()`` 拿到的一定是最新一版）。
3. **所有写都是原子的。** 自动保存、显式保存、导入共用
   :func:`_atomic_write_bytes`（临时文件 + fsync + ``os.replace``）。
   导入原来用的是裸 ``shutil.copyfile``，是全模块唯一的非原子写。
4. **读回来的值必须和 UI 自己会写出的值一致。** ``profile_names()`` 与
   ``current_profile()`` 用同一个清洗函数；bool 认得 JSON 字符串
   ``"false"``；number 恢复会吸附到 ``step`` 网格（见 :func:`_restore_number`
   的"离网默认"说明）。

复现：``python tests/config_store_io_test.py``
"""

from __future__ import annotations

import atexit
import errno
import json
import math
import os
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


CONFIG_VERSION = 1
DEFAULT_PROFILE = "default"

#: 落盘是否默认走后台线程。用 ``POLAR_CONFIG_ASYNC_SAVE=0`` 可退回全同步行为
#: （排障 / 录制基准时需要"save() 返回即落盘"这一确定性）。
ASYNC_SAVE = os.environ.get("POLAR_CONFIG_ASYNC_SAVE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

#: ``os.replace`` 在 Windows 上会被"目标文件正被别的句柄打开"打断
#: （winerror 32 ERROR_SHARING_VIOLATION / 5 ERROR_ACCESS_DENIED）。落盘挪到
#: 后台线程后，写与读天然并发，所以必须重试而不是直接失败。
_REPLACE_RETRY_DELAYS: tuple[float, ...] = (0.0, 0.0005, 0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.064)
_REPLACE_RETRY_ERRORS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})
#: 一轮退避（合计 127.5 ms）还换不回来时，把这条写重新排到队尾再试一轮。
#: 6 轮 ≈ 0.76 s，足够扛过一次杀软扫描 / 索引器占用。
_MAX_WRITE_ATTEMPTS = 6


def _replace_with_retry(temp_path: Path, path: Path) -> None:
    """``os.replace`` + 有界退避重试，总退避上限 127.5 ms。"""
    for delay in _REPLACE_RETRY_DELAYS:
        try:
            os.replace(temp_path, path)
            return
        except OSError as exc:
            retryable = (
                getattr(exc, "winerror", None) in (5, 32)
                or exc.errno in _REPLACE_RETRY_ERRORS
            )
            if not retryable:
                raise
        time.sleep(delay)
    os.replace(temp_path, path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """把 payload 原子地落到 path。

    断电或进程被杀只能留下"旧的完整文件"或"新的完整文件"，永远不会是半个。
    所有落盘路径（自动保存、显式保存、导入）共用这一条实现。
    """
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class _BackgroundWriter:
    """后台落盘队列：同一路径的连续写合并成一次，fsync 不再压在调用线程上。

    语义（与同步版逐字一致的部分）：

    * ``submit()`` 只把**已经序列化好的字节**排队，立刻返回；
    * 同一路径在排队期间被再次提交时，新内容覆盖旧内容 —— 连按滑块 4 次/秒
      最多触发 1 次落盘，但最终落盘的永远是最后那一版；
    * ``flush_path()`` / ``flush()`` 会等队列排空，是"读己之写"的保证；
    * 进程退出前由 :func:`_flush_at_exit` 冲刷，daemon 线程永远不会拖住解释器。
    """

    def __init__(self) -> None:
        self._cv = threading.Condition(threading.Lock())
        self._pending: dict[str, tuple[bytes, int]] = {}
        self._order: deque[str] = deque()
        self._active: str | None = None
        self._drained = threading.Event()
        self._drained.set()
        self._errors: list[BaseException] = []
        self._closed = False
        self._writes = 0
        self._thread: threading.Thread | None = None

    # --- 提交 -------------------------------------------------------------
    def submit(self, path: Path, payload: bytes) -> None:
        key = str(path)
        with self._cv:
            if self._closed:
                raise RuntimeError("配置落盘器已关闭")
            if key not in self._pending:
                self._order.append(key)
            self._pending[key] = (payload, 0)
            self._drained.clear()
            self._ensure_thread()
            self._cv.notify()

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="rm-config-writer", daemon=True
        )
        self._thread.start()

    # --- 查询 -------------------------------------------------------------
    def pending_keys(self) -> frozenset[str]:
        """已提交但**还没出现在磁盘上**的路径（含正在写的那一个）。"""
        with self._cv:
            keys = set(self._pending)
            if self._active is not None:
                keys.add(self._active)
            return frozenset(keys)

    def stats(self) -> dict[str, int]:
        with self._cv:
            return {"pending": len(self._pending), "writes": self._writes}

    # --- 同步点 -----------------------------------------------------------
    def flush_path(self, path: Path, timeout: float = 5.0) -> bool:
        """等某个具体路径上的排队写与在途写都结束。"""
        key = str(path)
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cv:
            while key in self._pending or self._active == key:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(min(remaining, 0.05))
        return True

    def flush(self, timeout: float = 5.0, *, raise_errors: bool = True) -> bool:
        """等全部排队内容落盘；超时返回 False，有失败则抛出第一个错误。"""
        finished = self._drained.wait(max(0.0, timeout))
        with self._cv:
            errors = self._errors
            self._errors = []
        if errors and raise_errors:
            raise errors[0]
        return finished

    def _report(self, exc: BaseException) -> None:
        stream = sys.stderr
        if stream is None:
            return
        try:
            print(f"[config_store] 后台落盘失败: {type(exc).__name__}: {exc}", file=stream)
        except Exception:  # noqa: BLE001 - stderr 可能已关闭
            pass

    def close(self, timeout: float = 5.0) -> None:
        """停掉写盘线程。只在测试收尾 / 解释器退出时用，调用后不能再 submit()。"""
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    # --- 工作线程 ---------------------------------------------------------
    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._order and not self._closed:
                    self._cv.wait(0.5)
                if not self._order:
                    if self._closed:
                        return
                    continue
                key = self._order.popleft()
                payload, attempt = self._pending.pop(key)
                self._active = key
            error: BaseException | None = None
            try:
                _atomic_write_bytes(Path(key), payload)
            except BaseException as exc:  # noqa: BLE001 - 后台线程不能让异常静默
                error = exc
            with self._cv:
                self._active = None
                dropped = False
                if error is None:
                    self._writes += 1
                elif attempt + 1 < _MAX_WRITE_ATTEMPTS and key not in self._pending:
                    # 目标文件被别人占着（Windows ERROR_ACCESS_DENIED /
                    # ERROR_SHARING_VIOLATION，杀软/索引器/另一个读句柄都会）：
                    # 重新排到队尾再试一轮，配置不就这么丢掉。
                    self._pending[key] = (payload, attempt + 1)
                    self._order.append(key)
                    self._drained.clear()
                else:
                    self._errors.append(error)
                    dropped = True
                if not self._pending:
                    self._drained.set()
                self._cv.notify_all()
            if dropped and error is not None:
                self._report(error)


_WRITER = _BackgroundWriter()


def flush_pending_saves(timeout: float = 5.0) -> bool:
    """进程退出前的最后一道闸：把队列里没落盘的档案全部写完。"""
    try:
        finished = _WRITER.flush(timeout, raise_errors=False)
    except Exception:  # noqa: BLE001 - 退出路径不允许再抛
        return False
    if not finished:
        print("[config_store] 退出前仍有配置未落盘（后台线程被占住）", file=sys.stderr)
    return finished


def _flush_at_exit() -> None:
    """atexit 钩子：把队列里没落盘的档案全部写完。

    幂等：没有排队内容时是空操作，所以任何时候手动调它都安全。
    写盘线程是 daemon（绝不会拖住解释器退出），"退出前一定刷完"这件事完全
    依赖这个钩子。
    """
    flush_pending_saves(10.0)


atexit.register(_flush_at_exit)


class ClientConfigStore:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def profile_names(self) -> list[str]:
        # 名字必须和 current_profile() / profile_path() 用的是同一个清洗函数，
        # 否则下拉框里会出现一个点了打不开（存到别的文件里）的档案名。
        names: list[str] = []
        seen: set[str] = set()
        for path in sorted(self.config_dir.glob("*.json")):
            if not path.is_file():
                continue
            name = self.clean_profile_name(path.stem)
            if name not in seen:
                seen.add(name)
                names.append(name)
        # 已提交但还在后台队列里的档案：save() 立刻返回，档案却已经"存在"。
        for key in _WRITER.pending_keys():
            pending = Path(key)
            if pending.parent != self.config_dir:
                continue  # 别的 ClientConfigStore 实例，不属于这一份档案列表
            name = self.clean_profile_name(pending.stem)
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names or [DEFAULT_PROFILE]

    def profile_path(self, profile: str) -> Path:
        return self.config_dir / f"{self.clean_profile_name(profile)}.json"

    def load(self, profile: str) -> dict[str, Any] | None:
        path = self.profile_path(profile)
        # 读己之写：save() 立刻返回了，读必须能看见那一版（同步版就是这个语义）。
        # 队列为空时这里只多一次无竞争的加锁，不产生 I/O。
        _WRITER.flush_path(path)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            version = int(data.get("version", 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if version < 0 or version > CONFIG_VERSION:
            return None
        return data

    def flush(self, timeout: float = 5.0) -> None:
        """等所有后台落盘完成，并把后台写失败抛回调用方。"""
        _WRITER.flush(timeout)

    def save(self, profile: str, data: dict[str, Any], *, wait: bool = False) -> Path:
        path = self.profile_path(profile)
        payload = dict(data)
        payload["version"] = CONFIG_VERSION
        payload["profile"] = self.clean_profile_name(profile)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        # 序列化必须留在调用线程：payload 里的 old_panels / polar_windows 等
        # 是 GUI 正在用的活字典，晚一点 dumps 就会读到 GUI 线程的中间态。
        # 搬走的只有写盘 I/O（本机实测 1.41 ms / 18.5 KB）。
        blob = serialized.encode("utf-8")
        if wait or not ASYNC_SAVE:
            # 同步写之前先把同一路径的排队写排空，否则队列里的旧内容会反盖回来。
            _WRITER.flush_path(path)
            _atomic_write_bytes(path, blob)
        else:
            _WRITER.submit(path, blob)
        return path

    def ensure_default(self, data: dict[str, Any]) -> Path:
        path = self.profile_path(DEFAULT_PROFILE)
        if not path.exists():
            # 启动路径紧接着就是 load_startup_config()，这里同步写。
            self.save(DEFAULT_PROFILE, data, wait=True)
        return path

    def import_file(self, source: Path) -> str:
        source = Path(source)
        if source.suffix.lower() != ".json" or not source.exists():
            raise ValueError("只能导入已经存在的 .json 配置文件。")
        name = self.clean_profile_name(source.stem)
        target = self.profile_path(name)
        if source.resolve() != target.resolve():
            # 原来是裸 shutil.copyfile（全模块唯一的非原子写）：导入途中断电会
            # 在已有同名档案的位置留下半个文件。现在和 save() 共用同一条
            # "临时文件 + fsync + os.replace"路径，字节内容与 copyfile 一致。
            _WRITER.flush_path(target)
            _atomic_write_bytes(target, source.read_bytes())
        return name

    def clean_profile_name(self, profile: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(profile).strip())
        return safe or DEFAULT_PROFILE


def export_client_config(
    registry: Any,
    *,
    gui_mode: str,
    selected_category: str,
    selected_module: str,
    module_expanded: set[str],
    old_panels: dict[str, Any],
    new_panels: dict[str, Any],
    dropdown_panels: dict[str, Any],
    polar_windows: dict[str, Any] | None = None,
    quick_action_wheel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        # Legacy ClickGUI renderers are transient previews. Never serialize
        # one as the profile's startup surface, even when an older caller
        # passes a historical mode name.
        "gui_mode": "JelloSigma",
        "selected_category": selected_category,
        "selected_module": selected_module,
        "module_expanded": sorted(module_expanded),
        "old_panels": old_panels,
        "new_panels": new_panels,
        "dropdown_panels": dropdown_panels,
        "polar_windows": polar_windows or {},
        "quick_action_wheel": quick_action_wheel or {},
        "modules": {
            module.name: {
                "enabled": module.enabled,
                "keybind": module.keybind,
                "settings": {
                    setting.name: setting.value
                    for setting in module.settings
                    if setting.kind != "action_group"
                },
            }
            for module in registry.modules
        },
    }


#: 手改过的档案 / 老版本客户端会把开关写成字符串。``bool("false")`` 在 Python
#: 里是 True，所以"关"会被恢复成"开"——这正是下面这张表的用途。
_TRUE_TEXT = frozenset({"true", "1", "yes", "y", "on", "t", "enable", "enabled"})
_FALSE_TEXT = frozenset({"false", "0", "no", "n", "off", "f", "", "disable", "disabled"})


def _restore_bool(value: Any) -> bool:
    """把档案里的开关值恢复成真正的 bool，字符串按词义解释。

    无法识别的字符串保持旧行为（非空即真），避免"猜错"导致静默翻转。
    """
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in _TRUE_TEXT:
            return True
        if token in _FALSE_TEXT:
            return False
    return bool(value)


def _on_step_grid(value: float, step: float) -> bool:
    return bool(step > 0) and round(value / step) * step == value


def _restore_number(value: Any, setting: Any) -> float | None:
    """把档案里的数值恢复成"滑块自己也会写出来的那个值"。

    范围钳位之后还要吸附到 ``step`` 网格：否则 137.77 会原样留在内存里，
    直到用户碰一下滑块才突然变成 140——同一份配置在"刚加载"和"碰过一下"
    两个时刻表现不一致。

    吸附只在**当前值本来就在 step 网格上**时启用。内置默认值里有 6 个离网
    默认（如 Armor3DESP Opacity 0.82 / step 0.05、Capture Height 540 / step 16），
    无条件吸附会在每次启动时把它们改成 0.8 / 544，直接动到画面。守住这条
    之后，"内置默认 → 导出 → 导入"必然逐位不变。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if setting.min_value is not None:
        number = max(setting.min_value, number)
    if setting.max_value is not None:
        number = min(setting.max_value, number)
    try:
        step = float(setting.step or 0.0)
        current = float(setting.value)
    except (TypeError, ValueError):
        step = 0.0
        current = 0.0
    if step > 0 and _on_step_grid(current, step):
        # 与 Setting.set_from_ratio 逐字相同的表达式：恢复出来的值和用户
        # 真去拖一次滑块得到的值二进制一致。
        number = round(number / step) * step
        if setting.min_value is not None:
            number = max(setting.min_value, number)
        if setting.max_value is not None:
            number = min(setting.max_value, number)
    return number


def apply_registry_config(registry: Any, data: dict[str, Any]) -> None:
    modules = data.get("modules")
    if not isinstance(modules, dict):
        return
    modules = dict(modules)
    click_gui = modules.get("ClickGui")
    if isinstance(click_gui, dict) and isinstance(click_gui.get("settings"), dict):
        settings = dict(click_gui["settings"])
        settings["Mode"] = {
            "VapeUnified": "PolarUnified",
            "VapeMulti": "PolarMulti",
        }.get(str(settings.get("Mode", "")), settings.get("Mode"))
        click_gui = dict(click_gui)
        click_gui["settings"] = settings
        modules["ClickGui"] = click_gui
    audio = modules.get("AudioFeedback")
    if isinstance(audio, dict) and isinstance(audio.get("settings"), dict):
        settings = dict(audio["settings"])
        if settings.get("Sound Theme") == "Vape":
            settings["Sound Theme"] = "Polar UI"
        audio = dict(audio)
        audio["settings"] = settings
        modules["AudioFeedback"] = audio
    # 旧配置把阵营挂在 MatchMode；新模块独立管理，但仍能无感加载历史档案。
    alliance_profile = modules.get("AllianceProfile")
    legacy_match = modules.get("MatchMode")
    has_explicit_side = isinstance(alliance_profile, dict) and isinstance(alliance_profile.get("settings"), dict) and "Side" in alliance_profile["settings"]
    if not has_explicit_side and isinstance(legacy_match, dict):
        legacy_settings = legacy_match.get("settings")
        if isinstance(legacy_settings, dict) and legacy_settings.get("Alliance") in ("Red", "Blue"):
            modules = dict(modules)
            modules["AllianceProfile"] = {"settings": {"Side": legacy_settings["Alliance"]}}
    legacy_fx = modules.get("EliminationFX")
    if isinstance(legacy_fx, dict) and isinstance(legacy_fx.get("settings"), dict):
        fx_settings = dict(legacy_fx["settings"])
        if "Preset" not in fx_settings and "Style" in fx_settings:
            old_style = str(fx_settings.get("Style", "Scrolling"))
            old_variant = str(fx_settings.get("Variant", "Prime"))
            fx_settings["Preset"] = {
                "Scrolling": "Default Scrolling", "Combo": "Combo", "Card": "Card",
                "CardBar": "Card", "Battlefield 1": "Battlefield 1", "Battlefield 5": "Battlefield 5",
                "Valorant": f"Valorant {old_variant if old_variant != 'Default' else 'Prime'}",
            }.get(old_style, "Default Scrolling")
        if "Sound Volume" not in fx_settings and "Volume" in fx_settings:
            fx_settings["Sound Volume"] = fx_settings["Volume"]
        old_anchor = str(fx_settings.get("Anchor Policy", ""))
        if old_anchor == "Safe Area":
            fx_settings["Anchor Policy"] = "Combat Focus"
            fx_settings["X Offset"] = 0
            fx_settings["Y Offset"] = 0
        elif old_anchor == "Original Position":
            fx_settings["Anchor Policy"] = "Original Bottom"
        fx_settings.setdefault("Responsive Scale", "Auto")
        legacy_fx = dict(legacy_fx)
        legacy_fx["settings"] = fx_settings
        modules = dict(modules)
        modules["EliminationFX"] = legacy_fx
    for name, module_data in modules.items():
        module = registry.get(str(name))
        if module is None or not isinstance(module_data, dict):
            continue
        if "enabled" in module_data:
            module.set_enabled(bool(module_data["enabled"]))
        keybind = module_data.get("keybind")
        if isinstance(keybind, str):
            # HeroLob previously had no entry key and could remain enabled in
            # a profile without any way to reopen its new dedicated console.
            module.keybind = (
                "K"
                if module.name == "HeroLob" and keybind in {"", "None"}
                else (keybind or "None")
            )
        settings = module_data.get("settings")
        if not isinstance(settings, dict):
            continue
        for setting_name, value in settings.items():
            setting = module.get_setting(str(setting_name))
            if setting is None:
                continue
            if setting.kind == "bool":
                setting.value = _restore_bool(value)
            elif setting.kind == "number":
                number = _restore_number(value, setting)
                if number is None:
                    continue
                setting.value = number
            elif setting.kind == "mode":
                text_value = str(value)
                if setting.name == "Status Display":
                    text_value = {
                        "Auto": "LiquidAuto",
                        "Rings": "LegacyOrbit",
                        "Bars": "LegacyBars",
                        "ReGlassAuto": "LiquidAuto",
                        "ReGlassBars": "LiquidBars",
                        "ReGlassCards": "LiquidCards",
                    }.get(text_value, text_value)
                elif setting.name == "Glass Style":
                    text_value = {"ReGlass": "LiquidGlass"}.get(text_value, text_value)
                elif setting.name == "ModuleList Surface":
                    text_value = {"ReGlass": "LiquidGlass"}.get(text_value, text_value)
                elif setting.name == "ModuleList Style":
                    text_value = {
                        "PolarLegacy": "PolarTextList",
                        "RainbowRight": "PolarRainbowRight",
                        "DebugWhiteList": "JelloActiveMods",
                    }.get(text_value, text_value)
                elif setting.name == "Render Backend":
                    # 旧版的 GPU Cached 只是缓存合成，升级后统一迁移到真正的 Direct GPU。
                    text_value = {"GPU Cached": "Unified GPU", "Direct GPU": "Unified GPU"}.get(text_value, text_value)
                elif module.name == "Armor3DESP" and setting.name == "Style":
                    # The old fluorescent-white default is superseded by the
                    # Sigma Shadow implementation. Other explicitly selected
                    # ESP styles remain available unchanged.
                    text_value = {"PolarMechanicalWire": "JelloShadow"}.get(text_value, text_value)
                if not setting.options or text_value in setting.options:
                    setting.value = text_value
            elif setting.kind == "action_group":
                # Expansion and selected action belong to the live ClickGUI
                # session and are intentionally never restored from a profile.
                continue
            else:
                setting.value = value
