"""API 冻结面快照测试（REFACTOR_BLUEPRINT §5.6 / 阶段 0 安全网）。

用途
----
在把 ``ui/polar_app.py``（PolarRMWindow，502 方法）重构为多模块（mixin 路线）
之前，把 ``window.<成员>`` 的完整属性面采集成基线 JSON；重构前后各跑一次
对比模式，任何 新增 / 缺失 / 类型种类变化 / 值签名变化 都会被逐一报告并
以 exit code 2 失败，从而保证外部触点（tests/smoke_test.py 直访约 190 个
成员、tools/ 下 5 个脚本、main.py / rm_client/app.py 的 import）不破坏。

采集内容
--------
1. 模块级冻结符号（``rm_client.ui.polar_app`` 必须继续可 import）：
   PolarRMWindow / main / EconomyScenePhase / InertialScroll / FrameScheduler
   （必需），Hotspot / ModuleListLayout / StartupLayerCache / VisionViewport /
   PolarHudOverlay（建议保留，一并冻结）。
2. ``PolarRMWindow`` 实例的 ``dir(window)`` 全量成员（含下划线私有字段），
   按成员分类：method(staticmethod/unbound) / bound-method / bound-classmethod /
   property / descriptor / plain attribute / getattr-error。
3. 惰性可赋值探针：§5.6 注明 ``_target_box_regression_failed`` 由
   transition 测试运行时 setattr 写入（不在构造后的 dir() 里），故单独
   校验"实例上可赋值 + 可还原"，不进快照。

值签名摘要纪律（避免基线抖动）
------------------------------
- 方法类：记录 ``__qualname__``（能抓到"同名方法换定义处/换实现"）。
- plain attribute：记录 类型名 + 稳定标量值（None/bool/int/str 直接 repr
  截断；全标量 tuple/frozenset 同样处理）。float 一律只记类型名：构造期
  写入的 ``time.monotonic()`` 时钟值跨进程必然不同、进程内又恒定（双采样
  抓不到），属于典型不稳定值。其余容器 / Qt 对象 / 任意对象只记类型名
  （内容不稳定或含内存地址，一律不进签名）。
- repr 中的 ``0x...`` 内存地址统一剥除。
- 易变成员（时钟、EMA、计数器、后台线程写入的字段）自动识别，两条规则
  （显式常量 ``VOLATILE_NAME_PATTERNS`` / ``VOLATILE_*_THRESHOLD``，便于评审）：
  ① 名称命中 _at / time / started / counter / duration 等模式；
  ② 值为疑似时间戳的浮点（≥1e4）或 ns/ms 级时钟整数（≥1e12）。
  命中成员【保留在快照中但标记 volatile】，对比时只校验"存在 + 不消失"，
  不比对类型种类与值签名——时间类/计数器类属性在两次构造间天然不同。
  另有双采样兜底：更新基线时构造两次窗口、间隔采样，两次签名不一致的
  成员同样标记 volatile。方法类成员不受 volatile 规则影响（qualname 永远比对）。
- 采集前停掉窗口内全部 QTimer，消除 tick 驱动的状态漂移。

环境前提（docstring 记录，见任务约束）
--------------------------------------
- Windows + 本仓 ``.venv``（Python 3.12/3.13，PySide6），QT_QPA_PLATFORM=
  offscreen。offscreen 无 GL，QOpenGLWidget 会打
  ``QOpenGLWidget is not supported on this platform`` 警告，CPU 兼容路径
  可正常构造（与 tests/smoke_test.py 同一手法）；GL 相关成员以实际能构造
  出的状态为准采集。
- 需 ``POLAR_DISABLE_LOCAL_CAPTURE=1``、``POLAR_DISABLE_MEDIA_PRELOAD=1``，
  防止测试进程探测物理摄像头 / 启动 FFmpeg 预加载线程（同 smoke_test）。
- 窗口构造约 14s（--update 需两次构造，约 30s）。

CLI
---
- ``python tests/api_freeze_test.py --update``   重新生成基线快照
- ``python tests/api_freeze_test.py``            对比模式（默认），不一致 exit 2
- ``python tests/api_freeze_test.py --mutation-selftest``
    进程内微型变异自检：临时从类上删除一个非关键成员（不改源码），确认
    对比模式能抓到"缺失"，恢复后确认重新 PASS——证明测试有效性。
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import time
from pathlib import Path

# 与 tests/smoke_test.py 相同的规避手法：必须在 import PySide6 之前设置。
os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BASELINE = Path(__file__).resolve().parent / "api_freeze_baseline.json"

# §5.6 模块级冻结符号：required 缺一不可；recommended 建议保留（tools 演进）。
MODULE_SYMBOLS_REQUIRED = (
    "PolarRMWindow",
    "main",
    "EconomyScenePhase",
    "InertialScroll",
    "FrameScheduler",
)
MODULE_SYMBOLS_RECOMMENDED = (
    "Hotspot",
    "ModuleListLayout",
    "StartupLayerCache",
    "VisionViewport",
    "PolarHudOverlay",
)

REPR_TRUNC = 120
VOLATILE_SIG = "*volatile*"

# ---------------------------------------------------------------------------
# 易变性排除规则（显式清单，便于评审）
# ---------------------------------------------------------------------------
# 命中规则的 plain-attribute/property 成员【保留在快照中但标记 volatile】，
# 对比时只校验"存在性"，不比对 kind/sig——时间类/计数器类属性在两次构造间
# 天然不同（time.monotonic() / 计数器），属于环境噪声而非 API 面。
# 方法类成员不受此规则影响（qualname 永远参与比对）。
#
# 规则一：名称模式（不区分大小写）。评审点：新增误伤/漏报改这里。
VOLATILE_NAME_PATTERNS = (
    r"(^|_)at$",              # *_at 后缀时间戳：_startup_static_ready_at
    r"(^|_)times?($|_)",      # ..._time / ..._times：last_frame_time、fps_times
    r"(^|_)started$",         # dynamic_island_event_started、startup_transition_started
    r"counter",               # 单调递增计数器
    r"duration",
    r"timestamp",
    r"elapsed",
)
# 规则二：值形态。浮点 ≥ 阈值 => 疑似时间戳/单调时钟（秒级）；整数 ≥ 阈值 =>
# 疑似 ns/ms 级时钟或大计数（如 time.time_ns()）。正常配置值（scale≤2.0、
# duration≤60 等）远低于阈值，不会误伤。
VOLATILE_FLOAT_THRESHOLD = 10_000.0
VOLATILE_INT_THRESHOLD = 10**12


def _is_volatile_value(name: str, value: object) -> bool:
    lowered = name.lower()
    for pattern in VOLATILE_NAME_PATTERNS:
        if re.search(pattern, lowered):
            return True
    if type(value) is float and value >= VOLATILE_FLOAT_THRESHOLD:
        return True
    if type(value) is int and abs(value) >= VOLATILE_INT_THRESHOLD:
        return True
    return False

# §5.6 注明的"由测试写入、需保证实例上可赋值"的惰性成员：
# 不出现在构造后的 dir() 里（transition_visual_check.py:125 运行时 setattr），
# 冻结面要求是"可赋值"，故用 setattr/getattr/delattr 探针校验而非快照比对。
LAZY_ASSIGNABLE_MEMBERS = ("_target_box_regression_failed",)


def probe_lazy_assignable(window: object) -> list[str]:
    """校验惰性成员在实例上可赋值，返回失败的成员名列表。"""
    failures: list[str] = []
    for name in LAZY_ASSIGNABLE_MEMBERS:
        try:
            had_before = hasattr(window, name)
            original = getattr(window, name, None)
            setattr(window, name, False)
            ok = getattr(window, name, None) is False
            # 探针不留痕：原本不存在则删除；存在则还原原值。
            if had_before:
                setattr(window, name, original)
            else:
                delattr(window, name)
            if not ok:
                failures.append(name)
        except Exception:
            failures.append(name)
    return failures


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------

def _clean_repr(value: object, limit: int = REPR_TRUNC) -> str:
    """repr 截断 + 剥除内存地址（0x...），保证跨进程可复现。"""
    import re

    text = repr(value)
    text = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", text)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


_STABLE_SCALARS = (type(None), bool, int, str)  # float 见 _value_sig：时钟值不稳定


def _value_sig(value: object) -> str:
    """plain attribute 的值签名摘要：类型名 + 稳定标量值（否则仅类型名）。

    float 特殊处理：构造期写入的 time.monotonic() 时钟值跨进程必不同、
    进程内恒定（双采样抓不到），故 float 一律只记类型名。
    """
    if isinstance(value, _STABLE_SCALARS):
        return _clean_repr(value)
    if isinstance(value, (tuple, frozenset)):
        if all(isinstance(item, _STABLE_SCALARS) for item in value):
            return _clean_repr(value)
    # float / dict / list / Qt 包装 / 任意对象：值或 repr 不跨进程稳定，只记类型名。
    return type(value).__name__


def _maybe_mark_volatile(name: str, value: object, entry: dict) -> dict:
    """易变性排除规则（仅作用于非 callable 成员；方法 qualname 永远比对）。

    命中则保留快照条目但 sig 置 *volatile*，对比时只查存在性。
    """
    if not callable(value) and _is_volatile_value(name, value):
        marked = dict(entry)
        marked["volatile"] = True
        marked["sig"] = VOLATILE_SIG
        return marked
    return entry


def _member_entry(window: object, name: str) -> dict:
    """对单个成员分类并产出 {kind, type, sig}。"""
    try:
        value = getattr(window, name)
    except Exception as exc:  # 属性本身抛错也是一种必须冻结的面貌
        return {"kind": "getattr-error", "type": type(exc).__name__, "sig": str(exc)[:REPR_TRUNC]}

    # 找到定义该名字的最派生类条目，用于区分 property / staticmethod / descriptor
    cls_attr = None
    for klass in type(window).__mro__:
        if name in vars(klass):
            cls_attr = vars(klass)[name]
            break

    if isinstance(cls_attr, property):
        return _maybe_mark_volatile(
            name, value,
            {"kind": "property", "type": type(value).__name__, "sig": _value_sig(value)},
        )
    if callable(value):
        if inspect.ismethod(value):
            if isinstance(cls_attr, classmethod):
                kind = "bound-classmethod"
            else:
                kind = "bound-method"
        elif inspect.isfunction(value):
            kind = "method"  # staticmethod 或经类访问的未绑定函数
        else:
            kind = "callable"  # PySide6 SignalInstance / functools.partial 等
        qualname = getattr(value, "__qualname__", None)
        sig = qualname if qualname else _clean_repr(value)
        return {"kind": kind, "type": type(value).__name__, "sig": sig}
    if cls_attr is not None and hasattr(cls_attr, "__get__") and not hasattr(cls_attr, "__set__"):
        return _maybe_mark_volatile(
            name, value,
            {"kind": "descriptor", "type": type(value).__name__, "sig": _value_sig(value)},
        )
    return _maybe_mark_volatile(
        name, value,
        {"kind": "plain-attribute", "type": type(value).__name__, "sig": _value_sig(value)},
    )


def collect_window_snapshot(window: object) -> dict[str, dict]:
    """收集 dir(window) 全量成员面（名称排序，JSON 可序列化）。"""
    snapshot: dict[str, dict] = {}
    for name in sorted(dir(window)):
        if name.startswith("__") and name.endswith("__"):
            # dunder 由语言/Qt 基类提供，重构不触碰；排除以压缩基线噪声。
            continue
        snapshot[name] = _member_entry(window, name)
    return snapshot


def _quiesce(window: object) -> None:
    """停掉窗口内全部 QTimer，消除 tick 驱动的状态漂移，保证采样稳定。"""
    from PySide6.QtCore import QTimer

    for timer in window.findChildren(QTimer):
        timer.stop()


def _build_window():
    """按 smoke_test.py 手法构造 PolarRMWindow（offscreen / 禁摄像头 / 禁预加载）。"""
    from PySide6.QtWidgets import QApplication

    from rm_client.ui.polar_app import PolarRMWindow

    app = QApplication.instance() or QApplication([])
    window = PolarRMWindow()
    window.resize(640, 360)
    _quiesce(window)
    app.processEvents()
    return app, window


def collect_module_symbols() -> dict[str, dict]:
    """校验 rm_client.ui.polar_app 模块级冻结符号可 import 并记录其种类。"""
    import importlib

    module = importlib.import_module("rm_client.ui.polar_app")
    symbols: dict[str, dict] = {}
    for name in sorted(set(MODULE_SYMBOLS_REQUIRED) | set(MODULE_SYMBOLS_RECOMMENDED)):
        obj = getattr(module, name, None)
        if obj is None:
            symbols[name] = {"kind": "missing", "type": "", "sig": ""}
        elif inspect.isclass(obj):
            symbols[name] = {"kind": "class", "type": type(obj).__name__, "sig": obj.__qualname__}
        elif inspect.isfunction(obj):
            symbols[name] = {"kind": "function", "type": "function", "sig": obj.__qualname__}
        else:
            symbols[name] = {"kind": "object", "type": type(obj).__name__, "sig": _clean_repr(obj)}
    return symbols


def collect_baseline_snapshot() -> dict:
    """完整基线：模块符号 + 窗口成员面（双采样自动标记 volatile 成员）。"""
    app, window = _build_window()
    try:
        first = collect_window_snapshot(window)
        # 间隔采样：期间处理事件并留出后台线程写入窗口，两次不一致 => volatile。
        time.sleep(0.3)
        app.processEvents()
        _quiesce(window)
        app.processEvents()
        second = collect_window_snapshot(window)
    finally:
        window.close()
        app.processEvents()

    merged: dict[str, dict] = {}
    for name in sorted(set(first) | set(second)):
        a, b = first.get(name), second.get(name)
        if a == b:
            merged[name] = dict(a)
        else:
            entry = dict(a or b)
            entry["volatile"] = True
            entry["sig"] = VOLATILE_SIG
            if a is not None and b is not None and a.get("kind") != b.get("kind"):
                entry["kind"] = "*volatile-kind*"
            merged[name] = entry

    return {
        "meta": {
            "python": sys.version.split()[0],
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "collected_by": "tests/api_freeze_test.py --update",
            "volatile_policy": "sig/kind 变化跨两次采样 => volatile，对比时只查存在性",
        },
        "module_symbols": collect_module_symbols(),
        "members": merged,
    }


# ---------------------------------------------------------------------------
# 对比
# ---------------------------------------------------------------------------

def diff_snapshots(baseline: dict, current: dict) -> dict:
    """逐一报告 新增 / 缺失 / 类型种类变化 / 签名变化。"""
    base_members: dict = baseline.get("members", {})
    curr_members: dict = current.get("members", {})
    added = sorted(set(curr_members) - set(base_members))
    missing = sorted(set(base_members) - set(curr_members))
    kind_changed, sig_changed = [], []
    for name in sorted(set(base_members) & set(curr_members)):
        b, c = base_members[name], curr_members[name]
        volatile = b.get("volatile") or c.get("volatile")
        if not volatile and b.get("kind") != c.get("kind"):
            kind_changed.append((name, b.get("kind"), c.get("kind")))
        elif not volatile and b.get("kind") == c.get("kind") and b.get("sig") != c.get("sig"):
            sig_changed.append((name, b.get("sig"), c.get("sig")))

    base_syms: dict = baseline.get("module_symbols", {})
    curr_syms: dict = current.get("module_symbols", {})
    sym_missing = sorted(set(base_syms) - set(curr_syms))
    sym_changed = [
        (name, base_syms[name], curr_syms[name])
        for name in sorted(set(base_syms) & set(curr_syms))
        if base_syms[name] != curr_syms[name]
    ]
    sym_added = sorted(set(curr_syms) - set(base_syms))

    return {
        "added": added,
        "missing": missing,
        "kind_changed": kind_changed,
        "sig_changed": sig_changed,
        "module_missing": sym_missing,
        "module_added": sym_added,
        "module_changed": sym_changed,
    }


def format_diff(diffs: dict) -> str:
    lines: list[str] = []
    if diffs["module_missing"]:
        lines.append(f"[模块符号缺失] {len(diffs['module_missing'])}: {diffs['module_missing']}")
    if diffs["module_added"]:
        lines.append(f"[模块符号新增] {len(diffs['module_added'])}: {diffs['module_added']}")
    for name, b, c in diffs["module_changed"]:
        lines.append(f"[模块符号变化] {name}: {b} -> {c}")
    if diffs["added"]:
        lines.append(f"[成员新增] {len(diffs['added'])}: {diffs['added']}")
    if diffs["missing"]:
        lines.append(f"[成员缺失] {len(diffs['missing'])}: {diffs['missing']}")
    for name, b, c in diffs["kind_changed"]:
        lines.append(f"[类型种类变化] {name}: {b} -> {c}")
    for name, b, c in diffs["sig_changed"]:
        lines.append(f"[签名变化] {name}: {b!r} -> {c!r}")
    if not lines:
        lines.append("API 冻结面一致：无差异。")
    return "\n".join(lines)


def is_clean(diffs: dict) -> bool:
    return not any(
        (
            diffs["added"],
            diffs["missing"],
            diffs["kind_changed"],
            diffs["sig_changed"],
            diffs["module_missing"],
            diffs["module_changed"],
        )
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _summary(snapshot: dict) -> str:
    counts: dict[str, int] = {}
    for entry in snapshot["members"].values():
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    parts = [f"{kind}={count}" for kind, count in sorted(counts.items())]
    total = len(snapshot["members"])
    volatile = sum(1 for e in snapshot["members"].values() if e.get("volatile"))
    return (
        f"成员总数={total}（{', '.join(parts)}）；"
        f"volatile 排除={volatile}，实际比对={total - volatile}"
    )


def run_compare(baseline_path: Path) -> int:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    _, window = _build_window()
    try:
        current = {
            "meta": {},
            "module_symbols": collect_module_symbols(),
            "members": collect_window_snapshot(window),
        }
        lazy_failures = probe_lazy_assignable(window)
    finally:
        window.close()
    diffs = diff_snapshots(baseline, current)
    print(format_diff(diffs))
    if lazy_failures:
        print(f"[惰性成员不可赋值] {lazy_failures}")
    print(_summary(current))
    if is_clean(diffs) and not lazy_failures:
        # ⚠ 第 155 轮：加一行 `[PASS]` —— 原来只打 `PASS: ...`，
        # 而门禁只数**以** `[PASS]` 开头**的行**，`PASS: ` 不算。
        # **这一个套件只做一件事**（基线对拍），**所以就是一行。**
        print("[PASS] API 冻结面与基线一致")
        print(f"PASS: 与基线 {baseline_path.name} 一致")
        return 0
    print(f"FAIL: 与基线 {baseline_path.name} 不一致（exit 2）")
    return 2


def run_update(baseline_path: Path) -> int:
    snapshot = collect_baseline_snapshot()
    _, window = _build_window()
    try:
        lazy_failures = probe_lazy_assignable(window)
    finally:
        window.close()
    baseline_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    volatile = sum(1 for e in snapshot["members"].values() if e.get("volatile"))
    print(f"基线已写入 {baseline_path}")
    print(_summary(snapshot))
    print(f"volatile 成员={volatile}（对比时只校验存在性）")
    print(f"模块级冻结符号={len(snapshot['module_symbols'])}")
    if lazy_failures:
        print(f"[惰性成员不可赋值] {lazy_failures}")
        return 2
    print(f"惰性可赋值探针 PASS：{list(LAZY_ASSIGNABLE_MEMBERS)}")
    return 0


def run_mutation_selftest(baseline_path: Path) -> int:
    """微型变异自检：进程内删除一个非关键成员，验证测试能抓到，恢复后 PASS。"""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    target = "draw_watermark"  # 非关键：纯绘制方法，删除不影响构造/其余成员
    if target not in baseline["members"]:
        print(f"FAIL: 基线中不存在目标成员 {target}")
        return 2

    # 定位定义该名字的类（重构后可能在 mixin 基类上）
    _, window = _build_window()
    try:
        owner = None
        for klass in type(window).__mro__:
            if target in vars(klass):
                owner = klass
                break
        if owner is None:
            print(f"FAIL: {target} 不在任何 MRO 类 __dict__ 中，无法变异")
            return 2
        original = vars(owner)[target]

        # ① 变异：删除后必须被抓到（missing）
        delattr(owner, target)
        mutated = {"meta": {}, "module_symbols": collect_module_symbols(),
                   "members": collect_window_snapshot(window)}
        diffs = diff_snapshots(baseline, mutated)
        caught = target in diffs["missing"] and not is_clean(diffs)

        # ② 恢复：必须重新 PASS
        setattr(owner, target, original)
        restored = {"meta": {}, "module_symbols": collect_module_symbols(),
                    "members": collect_window_snapshot(window)}
        diffs_after = diff_snapshots(baseline, restored)
        clean_after = is_clean(diffs_after)
    finally:
        window.close()

    if caught and clean_after:
        print(f"变异自检 PASS: 删除 {target} 被 [成员缺失] 抓到；恢复后对比重新一致。")
        return 0
    print(f"变异自检 FAIL: caught={caught} clean_after={clean_after}")
    if not clean_after:
        print(format_diff(diffs_after))
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PolarRMWindow API 冻结面快照测试（§5.6）")
    parser.add_argument("--update", action="store_true", help="重新生成基线快照")
    parser.add_argument("--mutation-selftest", action="store_true",
                        help="进程内变异自检（删除成员→抓到→恢复→PASS）")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"基线 JSON 路径（默认 {DEFAULT_BASELINE.name}）")
    args = parser.parse_args(argv)

    if args.update:
        return run_update(args.baseline)
    if args.mutation_selftest:
        return run_mutation_selftest(args.baseline)
    if not args.baseline.exists():
        print(f"FAIL: 基线不存在 {args.baseline}，请先运行 --update")
        return 2
    return run_compare(args.baseline)


if __name__ == "__main__":
    raise SystemExit(main())
