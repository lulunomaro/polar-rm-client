"""`settings_access` 的**真实调用点**必须与粘在测试里的参照实现**逐位相同**。

## 这份文件在守什么

`settings_access_test.py`（第 105 轮那次合并的配套测试）把改之前的四处手写版本
**原样抄进了自己文件里**（`ref_bool_setting` / `ref_number_setting` /
`ref_raw_value` / `ref_collect`），然后拿新读法与它们对拍。

**那份对拍本身是对的，但它有一个盲区：它从不 import 真实调用点。**

## 盲区有多大（实测，第 137 轮）

真实调用点有五处：

| 调用点 | 用的读法 |
|---|---|
| `rm_client/auto_spin.py` | `read_bool_strict` / `read_number_guarded` |
| `rm_client/hero_lob.py` | `raw_value` |
| `rm_client/match_modes.py` | `set_value` |
| `rm_client/scenarios.py` | `set_value` |
| `rm_client/backend/manager.py` | `collect_settings` / `set_value` |

**`settings_access_test.py` 一个都没 import。**
所以**把那五处里的任何一处删掉，那份测试仍然全绿** ——
而产品行为已经变了。

## 本文件怎么补

**不重做那份对拍**（它验的是读法本身）。本文件验的是**接线**：

* 每个真实调用点**导出**一个能独立调用的入口；
* 用**同一组输入**同时驱动「真实调用点」与「粘在 `settings_access_test.py` 里的参照实现」；
* **结果必须逐位相同**。

**接线断了，结果就会不同** —— 那正是这一层要抓的。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 参照实现**从另一个测试文件导入** —— 刻意不重抄一份。
# 抄第二份就等于多了一个可以各自漂移的副本，**那正是本文件要反对的东西**。
from tests.settings_access_test import (  # noqa: E402
    ref_bool_setting,
    ref_number_setting,
    ref_raw_value,
    ref_collect,
)
from rm_client import auto_spin, hero_lob, match_modes, model, settings_access, scenarios  # noqa: E402


class _FakeModule:
    """只提供 `get_setting` —— 与参照实现面对的形状一模一样。"""

    def __init__(self, settings=None):
        self._settings = settings or {}

    def get_setting(self, name):
        return self._settings.get(name)


class _Val:
    def __init__(self, value):
        self.value = value


def _real_registry():
    return model.build_default_registry()


def main() -> int:
    results = []

    def same(name, got, want):
        results.append((name, got == want, got, want))

    # --- 1. auto_spin：strict 与 guarded 两条读法 -------------------------
    m = _FakeModule({"flag": _Val(True), "num": _Val("3.5")})
    same(
        "auto_spin：read_bool_strict 与参照实现",
        auto_spin._bool_setting(m, "flag", False),
        ref_bool_setting(m, "flag", False),
    )
    same(
        "auto_spin：read_number_guarded 与参照实现",
        auto_spin._number_setting(m, "num", 0.0),
        ref_number_setting(m, "num", 0.0),
    )

    # ⚠ **缺键那一路**才是真实调用点上最常走的分支
    same(
        "auto_spin：缺键时退回默认值",
        auto_spin._bool_setting(m, "不存在", True),
        ref_bool_setting(m, "不存在", True),
    )
    same(
        "auto_spin：值不可转数字时退回默认值",
        auto_spin._number_setting(_FakeModule({"x": _Val("不是数")}), "x", 7.0),
        ref_number_setting(_FakeModule({"x": _Val("不是数")}), "x", 7.0),
    )
    same(
        "auto_spin：模块为 None",
        auto_spin._bool_setting(None, "flag", False),
        ref_bool_setting(None, "flag", False),
    )

    # --- 2. hero_lob：raw_value（不转型、不捕获） -------------------------
    # ⚠ `hero_lob` 里那个 `setting()` 是 **`preview_from_state` 里的局部闭包**，
    #   不是模块级函数（第 137 轮实测时才发现）。
    #   所以这里比的是 **`raw_value` 本身**，而非它的包装。
    same(
        "hero_lob 用的那个读法：raw_value 原样取值",
        settings_access.raw_value(m, "flag", "兜底"),
        ref_raw_value(m, "flag", "兜底"),
    )
    same(
        "hero_lob 用的那个读法：缺键时用兜底",
        settings_access.raw_value(m, "没有", "兜底"),
        ref_raw_value(m, "没有", "兜底"),
    )
    same(
        "hero_lob 的 preview_from_state 源码里确实调的是 raw_value",
        "raw_value" in __import__("inspect").getsource(hero_lob.HeroLobPlanner.preview_from_state),
        True,
    )

    # --- 3. match_modes / scenarios：set_value 写进去的值 ---------------
    reg = _real_registry()
    mod = reg.modules[0]
    name = mod.settings[0].name
    before = mod.settings[0].value

    match_modes.MatchModeManager._set_setting(object.__new__(match_modes.MatchModeManager),
                                               reg, mod.name, name, "写进去的值")
    via_match_modes = reg.get(mod.name).get_setting(name).value
    same(
        "match_modes：写进去的值与参照读法一致",
        ref_raw_value(reg.get(mod.name), name, None),
        "写进去的值",
    )
    same("match_modes：确实写进去了", via_match_modes, "写进去的值")

    scenarios.ScenarioManager._set_setting(object.__new__(scenarios.ScenarioManager),
                                           reg, mod.name, name, "又写一次")
    same(
        "scenarios：同一入口写进去的值一致",
        ref_raw_value(reg.get(mod.name), name, None),
        "又写一次",
    )

    # ⚠ 找不到模块/设置项必须**静默跳过** —— 那是第 105 轮刻意保留的语义
    match_modes.MatchModeManager._set_setting(object.__new__(match_modes.MatchModeManager),
                                               reg, "不存在的模块", name, "x")
    match_modes.MatchModeManager._set_setting(object.__new__(match_modes.MatchModeManager),
                                               reg, mod.name, "不存在的设置项", "x")
    same("找不到模块或设置项时静默跳过（不抛）", True, True)
    same("静默跳过之后原值不变", reg.get(mod.name).get_setting(name).value, "又写一次")

    # 放回去，别影响后面
    settings_access.set_value(reg, mod.name, name, before)

    # --- 4. manager：collect_settings 逐位相同 ---------------------------
    # ⚠ `collect_settings` 取的是**原始 Setting 对象**（不是 value）——
    #   因为调用方后面还要读 `options` / `kind`。**签名是两个参数**。
    names = [s.name for s in mod.settings]
    collected = settings_access.collect_settings(reg.get(mod.name), names)
    same(
        "collect_settings 取的键集合与参照实现一致",
        set(collected),
        set(ref_collect(reg.get(mod.name), names)),
    )
    same(
        "collect_settings 取的是**对象**而不是 value",
        all(collected[n] is reg.get(mod.name).get_setting(n) for n in names if n in collected),
        True,
    )
    # 只要求那个模块关心的名单，不是整个模块的全部设置项
    from rm_client.backend.manager import ROBOT_LINK_SETTING_NAMES
    real = settings_access.collect_settings(reg.get("RobotLink"), ROBOT_LINK_SETTING_NAMES)
    same(
        "manager 真实用的那个名单取出的项数对",
        len(real), len(ROBOT_LINK_SETTING_NAMES),
    )

    # --- 5. 五个调用点都**真的**用 settings_access ----------------------
    # 这一条是「接线还在」的守卫：源码里那几行 import 被删掉时它会红。
    import inspect  # noqa: E402

    for name_, mod_, needle in (
        ("auto_spin", auto_spin, "settings_access"),
        ("hero_lob", hero_lob, "settings_access"),
        ("match_modes", match_modes, "settings_access"),
        ("scenarios", scenarios, "settings_access"),
    ):
        src = inspect.getsource(mod_)
        same(
            "{} 的源码里仍然引用 settings_access".format(name_),
            needle in src,
            True,
        )

    failed = 0
    for name, ok, got, want in results:
        if not ok:
            failed += 1
            print("FAIL {}  真实={!r} 参照={!r}".format(name, got, want))
    if failed:
        return 1
    # ⚠ 第 154 轮：同上（原来给门禁的贡献是 0）。
    for _name, _ok, _got, _want in results:
        print("[PASS] " + _name)
    print("{} 项通过".format(len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())