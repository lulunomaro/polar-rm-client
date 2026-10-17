"""Window compatibility contracts, with an opt-in historic full-snapshot audit.

Default mode checks all established public members and explicitly documented
private caller seams in window_api_contract.json. Python methods are compared by
accepted parameters/defaults, not their implementing class or Mixin qualname.
New implementation storage does not silently become an API contract.

The original api_freeze_baseline.json remains unchanged. --legacy-snapshot uses
its original full dir(window) comparison, including private fields and method
owners. It reports actual differences and may fail after a compatible extraction.
Neither mode is a behavioral, GPU, firmware, or hardware qualification.

  python tests/api_freeze_test.py
  python tests/api_freeze_test.py --mutation-selftest
  python tests/api_freeze_test.py --legacy-snapshot

--update only updates the historic snapshot; it requires --legacy-snapshot and
an explicit --baseline destination. It never updates the compatibility manifest.
Use a fresh POLAR_USER_DATA_ROOT for deterministic constructor defaults.
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

from tools.quality.window_contract import (
    callable_signature,
    compatibility_snapshots,
    load_contract,
    protected_names,
)
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle

DEFAULT_BASELINE = Path(__file__).resolve().parent / "api_freeze_baseline.json"
DEFAULT_CONTRACT = Path(__file__).resolve().parent / "window_api_contract.json"

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
    owner = None
    for klass in type(window).__mro__:
        if name in vars(klass):
            cls_attr = vars(klass)[name]
            owner = klass
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
        entry = {"kind": kind, "type": type(value).__name__, "sig": sig}
        call_signature = callable_signature(value)
        if call_signature is not None:
            entry["call_signature"] = call_signature
        if name in QT_OPTIONAL_MEMBERS:
            # Legacy baseline predates provider metadata. New captures identify
            # the defining class so an application override is never exempted.
            entry["provider"] = getattr(owner, "__module__", "")
        return entry
    if cls_attr is not None and hasattr(cls_attr, "__get__") and not hasattr(cls_attr, "__set__"):
        return _maybe_mark_volatile(
            name, value,
            {"kind": "descriptor", "type": type(value).__name__, "sig": _value_sig(value)},
        )
    return _maybe_mark_volatile(
        name, value,
        {"kind": "plain-attribute", "type": type(value).__name__, "sig": _value_sig(value)},
    )


def collect_window_snapshot(window: object, names: set[str] | None = None) -> dict[str, dict]:
    """Capture only requested members, or the full historic surface for audits.

    Unprotected private properties are not evaluated by compatibility checks.
    Absence from dir(window) remains a missing member rather than an error value.
    """
    snapshot: dict[str, dict] = {}
    available = set(dir(window))
    selected = available if names is None else available & names
    for name in sorted(selected):
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
    try:
        settle_window_lifecycle(window, app)
        window.resize(640, 360)
        _quiesce(window)
        app.processEvents()
    except Exception:
        close_window_safely(window, app)
        raise
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
        close_window_safely(window, app)

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

# These exact built-in members vary across the Qt versions used to capture
# the Windows baseline and run Linux CI. All other inherited APIs stay frozen.
QT_OPTIONAL_MEMBERS = frozenset({
    "accessibleIdentifier", "isQmlExposed", "setAccessibleIdentifier",
})


def _known_optional_qt_member(name: str, entry: dict) -> bool:
    return (
        name in QT_OPTIONAL_MEMBERS
        and entry.get("kind") == "callable"
        and entry.get("type") == "builtin_function_or_method"
        and entry.get("sig") == f"PolarRMWindow.{name}"
        and entry.get("provider", "PySide6.QtWidgets") in {
            "PySide6.QtWidgets", "PySide6.QtCore",
        }
        and not entry.get("volatile")
    )


def _portable_member(name: str, entry: dict) -> dict:
    # Only the workspace's concrete pathlib implementation is OS-dependent.
    # Strings, other attributes, mismatched signatures, and kind changes fail.
    if (name == "workspace" and entry.get("kind") == "plain-attribute"
            and entry.get("type") in {"WindowsPath", "PosixPath"}
            and entry.get("sig") == entry.get("type")):
        return {**entry, "type": "Path", "sig": "Path"}
    return entry


def diff_snapshots(baseline: dict, current: dict) -> dict:
    """逐一报告 新增 / 缺失 / 类型种类变化 / 签名变化。"""
    base_members: dict = baseline.get("members", {})
    curr_members: dict = current.get("members", {})
    added = sorted(name for name in set(curr_members) - set(base_members)
                   if not _known_optional_qt_member(name, curr_members[name]))
    missing = sorted(name for name in set(base_members) - set(curr_members)
                     if not _known_optional_qt_member(name, base_members[name]))
    kind_changed, type_changed, sig_changed = [], [], []
    for name in sorted(set(base_members) & set(curr_members)):
        b = _portable_member(name, base_members[name])
        c = _portable_member(name, curr_members[name])
        volatile = b.get("volatile") or c.get("volatile")
        if not volatile and b.get("kind") != c.get("kind"):
            kind_changed.append((name, b.get("kind"), c.get("kind")))
        if not volatile and b.get("type") != c.get("type"):
            type_changed.append((name, b.get("type"), c.get("type")))
        if not volatile and b.get("kind") == c.get("kind") and b.get("sig") != c.get("sig"):
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
        "type_changed": type_changed,
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
    for name, b, c in diffs["type_changed"]:
        lines.append(f"[类型变化] {name}: {b} -> {c}")
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
            diffs["type_changed"],
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


def compare_contract(baseline: dict, current: dict, contract: dict | None) -> dict:
    if contract is not None:
        baseline, current = compatibility_snapshots(baseline, current, contract)
    return diff_snapshots(baseline, current)


def collect_comparison_snapshot(window: object, baseline: dict, contract: dict | None) -> dict:
    names = protected_names(baseline, contract) if contract is not None else None
    return {"meta": {}, "module_symbols": collect_module_symbols(),
            "members": collect_window_snapshot(window, names)}


def run_compare(baseline_path: Path, contract_path: Path | None = DEFAULT_CONTRACT) -> int:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    contract = load_contract(contract_path, baseline) if contract_path else None
    app, window = _build_window()
    try:
        current = collect_comparison_snapshot(window, baseline, contract)
        lazy_failures = probe_lazy_assignable(window)
    finally:
        close_window_safely(window, app)
    diffs = compare_contract(baseline, current, contract)
    print(format_diff(diffs))
    if contract is not None:
        print(f"Compatibility scope: {len(protected_names(baseline, contract))} established members; "
              f"{len(contract['python_signatures'])} Python call signatures; "
              f"{len(contract['private_members'])} explicit private caller seams. "
              "Behavior still requires focused regression tests.")
    else:
        print("Legacy full-snapshot audit: every historic private field and method owner participates.")
    if lazy_failures:
        print(f"[惰性成员不可赋值] {lazy_failures}")
    print(_summary(current))
    if is_clean(diffs) and not lazy_failures:
        print(f"PASS: 与基线 {baseline_path.name} 一致")
        return 0
    print(f"FAIL: 与基线 {baseline_path.name} 不一致（exit 2）")
    return 2


def run_update(baseline_path: Path) -> int:
    snapshot = collect_baseline_snapshot()
    app, window = _build_window()
    try:
        lazy_failures = probe_lazy_assignable(window)
    finally:
        close_window_safely(window, app)
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


def run_mutation_selftest(baseline_path: Path, contract_path: Path | None = DEFAULT_CONTRACT) -> int:
    """微型变异自检：进程内删除一个非关键成员，验证测试能抓到，恢复后 PASS。"""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    contract = load_contract(contract_path, baseline) if contract_path else None
    target = "draw_watermark"  # 非关键：纯绘制方法，删除不影响构造/其余成员
    if target not in baseline["members"]:
        print(f"FAIL: 基线中不存在目标成员 {target}")
        return 2

    # 定位定义该名字的类（重构后可能在 mixin 基类上）
    app, window = _build_window()
    owner = None
    original = None
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
        mutated = collect_comparison_snapshot(window, baseline, contract)
        diffs = compare_contract(baseline, mutated, contract)
        caught = target in diffs["missing"] and not is_clean(diffs)

        # ② 恢复：必须重新 PASS
        setattr(owner, target, original)
        restored = collect_comparison_snapshot(window, baseline, contract)
        diffs_after = compare_contract(baseline, restored, contract)
        clean_after = is_clean(diffs_after)
    finally:
        if owner is not None and original is not None:
            setattr(owner, target, original)
        close_window_safely(window, app)

    if caught and clean_after:
        print(f"变异自检 PASS: 删除 {target} 被 [成员缺失] 抓到；恢复后对比重新一致。")
        return 0
    print(f"变异自检 FAIL: caught={caught} clean_after={clean_after}")
    if not clean_after:
        print(format_diff(diffs_after))
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PolarRMWindow API 冻结面快照测试（§5.6）")
    parser.add_argument("--update", action="store_true", help="Explicit historic-snapshot capture, never a contract refresh")
    parser.add_argument("--legacy-snapshot", action="store_true", help="Audit the unchanged full historic snapshot")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT, help="Reviewed compatibility manifest")
    parser.add_argument("--mutation-selftest", action="store_true",
                        help="进程内变异自检（删除成员→抓到→恢复→PASS）")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"基线 JSON 路径（默认 {DEFAULT_BASELINE.name}）")
    args = parser.parse_args(argv)

    if args.update:
        if not args.legacy_snapshot or args.baseline.resolve() == DEFAULT_BASELINE.resolve():
            parser.error("--update requires --legacy-snapshot and a separate --baseline destination; review changes before replacing evidence")
        return run_update(args.baseline)
    contract_path = None if args.legacy_snapshot else args.contract
    if args.mutation_selftest:
        return run_mutation_selftest(args.baseline, contract_path)
    if not args.baseline.exists():
        print(f"FAIL: 基线不存在 {args.baseline}；请恢复已评审的历史快照，不要用当前输出覆盖缺失证据")
        return 2
    return run_compare(args.baseline, contract_path)


if __name__ == "__main__":
    raise SystemExit(main())

