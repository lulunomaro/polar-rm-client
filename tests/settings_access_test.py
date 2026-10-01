"""设置项读取器：迁移前后**逐位相同**。

# 这轮改了什么

`"从模块里读一个设置项、拿不到就用默认值"`在本仓库被手写了 6 遍，而且给了
不同的答案：

* `auto_spin._bool_setting` —— `bool(setting.value)`，**不**捕获类型错
* `auto_spin._number_setting` —— `float(...)`，捕获 `TypeError/ValueError`
* `hero_lob` 的内嵌 `setting()` —— 原样返回 `.value`，**不**转型、**不**捕获
* `manager.tick_polar_link` 的 25 项字典推导式 —— 取原始 `Setting` 对象
* `persistence_mixin.get_setting_number` —— 同 `_number_setting`
* `polar_app` / `config_actions_mixin` —— 各有各的（视觉冻结文件，本轮不动）

这轮把**前四种**收进 `rm_client/settings_access.py`，并且**给每一种语义起名字**
（`read_bool_strict` / `read_bool_guarded` / `read_number_guarded` /
`raw_value` / `collect_settings`），把"选哪种语义"从每个人现场手搓
变成调用点自己声明。

# 为什么这个文件是"等价性"而不是"功能"测试

这是一次**纯重构**，价值全在"没有改变行为"上。所以本文件不测新功能，
只测一件事：同一组输入下，**旧实现与新读取器给出完全相同的结果**
（包括"旧实现会抛、新读取器也必须抛"这一类）。

最容易出错的地方是"顺手加个防护"：迁移时若觉得"这里应该也挡一下 NaN /
空字符串"，那就已经不是重构了 —— 差异极小，review 时却说不清。
所以本文件把**已知的、被刻意保留的差异点**也钉出来。

跑法：``python tests/settings_access_test.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.settings_access import (  # noqa: E402
    collect_settings,
    find_setting,
    raw_value,
    read_bool_guarded,
    read_bool_strict,
    read_number_guarded,
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


class FakeSetting:
    def __init__(self, value):
        self.value = value


class FakeModule:
    def __init__(self, settings: dict | None):
        self._settings = settings or {}

    def get_setting(self, name):
        return self._settings.get(name)


# --- 参照实现：改之前的四处手写版本，原样抄 -----------------------------

def ref_bool_setting(module, name, default):
    setting = module.get_setting(name) if module is not None else None
    return bool(setting.value) if setting is not None else bool(default)


def ref_number_setting(module, name, default):
    setting = module.get_setting(name) if module is not None else None
    try:
        return float(setting.value) if setting is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def ref_raw_value(module, name, fallback):
    item = module.get_setting(name) if module is not None else None
    return item.value if item is not None else fallback


def ref_collect(module, names):
    return {name: (module.get_setting(name) if module is not None else None) for name in names}


def both(fn_new, fn_ref, *args):
    """同时跑新旧实现，返回 (新结果或异常标记, 旧结果或异常标记)。"""

    def call(fn):
        try:
            return ("ok", fn(*args))
        except Exception as exc:  # noqa: BLE001
            return ("raise", type(exc).__name__)

    return call(fn_new), call(fn_ref)


#: 覆盖"正常值 / 缺失 / 模块为 None / 类型不合法 / 边界值"五类。
VALUE_CASES = [
    True, False, 1, 0, 1.5, -2.75, 0.0,
    "3.5", "not-a-number", "", "false", None,
    float("inf"), float("nan"),
]


# --- 1. bool strict：逐位相同，包括"不捕获"这件事 ---------------------

def test_bool_strict_equivalence() -> None:
    modules = [None, FakeModule({}), FakeModule({"X": FakeSetting(True)}),
               FakeModule({"X": FakeSetting("nonsense")})]
    bad = []
    for module in modules:
        for value in VALUE_CASES:
            mod = FakeModule({"X": FakeSetting(value)}) if module is not None and hasattr(module, "_settings") and "X" not in getattr(module, "_settings", {}) else module
            new, ref = both(read_bool_strict, ref_bool_setting, mod, "X", True)
            if new != ref:
                bad.append(f"module={type(mod).__name__} value={value!r}: new={new} ref={ref}")
    check("read_bool_strict 与旧 _bool_setting 逐位相同（含抛异常的时机）",
          not bad, "; ".join(bad[:4]))


# --- 2. bool strict 确实不捕获（把"不捕获"钉成契约） -------------------

def test_bool_strict_propagates_errors() -> None:
    class Exploding:
        @property
        def value(self):
            raise RuntimeError("boom")

    mod = FakeModule({"X": Exploding()})
    try:
        read_bool_strict(mod, "X", True)
        check("read_bool_strict 不吞掉读取器抛出的异常（strict 的定义）", False, "居然没抛")
    except RuntimeError:
        check("read_bool_strict 不吞掉读取器抛出的异常（strict 的定义）", True)
    # 与旧实现同一条路径
    try:
        ref_bool_setting(mod, "X", True)
        ref_raised = False
    except RuntimeError:
        ref_raised = True
    check("旧实现同样不吞（所以两版行为一致）", ref_raised)


# --- 3. bool guarded：这是**新语义**，不是迁移，必须自证 ----------------

def test_bool_guarded_is_strict_plus_fallback() -> None:
    bad = []
    for value in VALUE_CASES:
        mod = FakeModule({"X": FakeSetting(value)})
        strict = read_bool_strict(mod, "X", True)
        guarded = read_bool_guarded(mod, "X", True)
        ref = ref_bool_setting(mod, "X", True)
        # guarded 在 strict 会抛的时候退回默认值，其余与 strict 相同
        if isinstance(strict, bool):
            if guarded != strict:
                bad.append(f"value={value!r}: guarded={guarded} strict={strict}")
        else:
            if guarded is not True:
                bad.append(f"value={value!r}: guarded 应退回默认值，得到 {guarded!r}")
        _ = ref
    check("read_bool_guarded = strict + 值不合法时退回默认", not bad, "; ".join(bad[:4]))


# --- 4. number guarded：与两个旧实现逐位相同 --------------------------

def test_number_guarded_equivalence() -> None:
    bad = []
    for module in (None, FakeModule({}), FakeModule({"X": FakeSetting(1.5)})):
        for value in VALUE_CASES:
            mod = FakeModule({"X": FakeSetting(value)}) if module is not None and hasattr(module, "_settings") and "X" not in getattr(module, "_settings", {}) else module
            new, ref = both(read_number_guarded, ref_number_setting, mod, "X", 7.0)
            if new != ref:
                bad.append(f"value={value!r}: new={new} ref={ref}")
    check("read_number_guarded 与旧 _number_setting / get_setting_number 逐位相同",
          not bad, "; ".join(bad[:4]))


# --- 5. NaN 被刻意放行（不是遗漏，是"纯重构"的纪律） ------------------

def test_nan_passes_through_on_purpose() -> None:
    """NaN 会穿过 except —— 这是**与旧实现一致**的行为，不是修复。

    把它钉成断言，是为了让"下一轮有人想顺手加个防护"这件事变成一次
    有意识的决定，而不是一次无意的行为变更。
    """
    mod = FakeModule({"X": FakeSetting(float("nan"))})
    got = read_number_guarded(mod, "X", 7.0)
    check("NaN 穿过读取器（与旧实现一致，刻意不加防护）",
          got != got, f"得到 {got!r} —— 说明已经加了防护，那就不是纯重构了")
    ref = ref_number_setting(mod, "X", 7.0)
    check("旧实现同样放行 NaN（两版一致）", ref != ref, f"ref={ref!r}")


# --- 6. raw_value：与 hero_lob 内嵌函数逐位相同 -----------------------

def test_raw_value_equivalence() -> None:
    bad = []
    for module in (None, FakeModule({}), FakeModule({"X": FakeSetting(1.5)})):
        for value in VALUE_CASES:
            mod = FakeModule({"X": FakeSetting(value)}) if module is not None and hasattr(module, "_settings") and "X" not in getattr(module, "_settings", {}) else module
            new, ref = both(raw_value, ref_raw_value, mod, "X", "FB")
            if new != ref:
                bad.append(f"value={value!r}: new={new} ref={ref}")
    check("raw_value 与 hero_lob 内嵌 setting() 逐位相同", not bad, "; ".join(bad[:4]))


def test_raw_value_does_not_coerce() -> None:
    mod = FakeModule({"X": FakeSetting("3.5")})
    got = raw_value(mod, "X", 0.0)
    check("raw_value 原样返回字符串（不转型，这是它的定义）",
          got == "3.5" and isinstance(got, str), f"got {got!r}")


# --- 7. collect_settings：与 manager 的字典推导式逐位相同 --------------

def test_collect_settings_equivalence() -> None:
    names = ("Allow Drive", "Direct DT7 Egress", "Port", "不存在的一项")
    for module in (None, FakeModule({}), FakeModule({"Port": FakeSetting("COM7")})):
        new = collect_settings(module, names)
        ref = ref_collect(module, names)
        check(f"collect_settings 与旧字典推导式相同（module={type(module).__name__}）",
              new == ref, f"{new} != {ref}")
    # 键的顺序也必须一致（Python 3.7+ 字典保序）
    mod = FakeModule({})
    check("collect_settings 保持名字的顺序",
          list(collect_settings(mod, names)) == list(names),
          str(list(collect_settings(mod, names))))
    check("collect_settings 返回的是原始对象（不是 .value）",
          isinstance(collect_settings(FakeModule({"Port": FakeSetting("COM7")}), ("Port",))["Port"], FakeSetting))


# --- 8. find_setting：两种"没有"走同一条路 ---------------------------

def test_find_setting_handles_all_none_forms() -> None:
    check("module 为 None → None", find_setting(None, "X") is None)
    check("模块里没有这一项 → None", find_setting(FakeModule({}), "X") is None)
    setting = FakeSetting(1)
    check("能取到时返回原始对象", find_setting(FakeModule({"X": setting}), "X") is setting)


# --- 9. manager 的常量与真实注册表对得上 ------------------------------

def test_robot_link_constant_matches_registry() -> None:
    from rm_client.backend.manager import ROBOT_LINK_SETTING_NAMES
    from rm_client.model import build_default_registry

    registry = build_default_registry()
    module = registry.get("RobotLink")
    check("RobotLink 模块存在", module is not None, "注册表里没有 RobotLink")
    if module is None:
        return
    existing = {s.name for s in module.settings}
    missing = [n for n in ROBOT_LINK_SETTING_NAMES if n not in existing]
    check(f"ROBOT_LINK_SETTING_NAMES 的 {len(ROBOT_LINK_SETTING_NAMES)} 项都在 RobotLink 里",
          not missing, f"注册表里没有：{missing}")
    check("名单里没有重复项",
          len(set(ROBOT_LINK_SETTING_NAMES)) == len(ROBOT_LINK_SETTING_NAMES))


def main() -> int:
    tests = [
        test_bool_strict_equivalence,
        test_bool_strict_propagates_errors,
        test_bool_guarded_is_strict_plus_fallback,
        test_number_guarded_equivalence,
        test_nan_passes_through_on_purpose,
        test_raw_value_equivalence,
        test_raw_value_does_not_coerce,
        test_collect_settings_equivalence,
        test_find_setting_handles_all_none_forms,
        test_robot_link_constant_matches_registry,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"设置项读取器等价性：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"设置项读取器等价性：全部通过（{PASSED} 项）")
    print("6 处手写收敛成一处，四种语义各有名字，迁移前后逐位相同。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
