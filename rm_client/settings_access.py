"""设置项读取的**唯一**落点。

# 为什么要有这一个文件

"从模块里读一个设置项、拿不到就用默认值"这件事，在本仓库里被手写了至少 6 遍：

| 位置 | 写法 | 特点 |
|---|---|---|
| `auto_spin._bool_setting` | `bool(setting.value) if setting is not None else bool(default)` | **不**捕获类型错 |
| `auto_spin._number_setting` | `float(setting.value)`，外面包 `try/except (TypeError, ValueError)` | 捕获 |
| `hero_lob` 的内嵌 `setting()` | `item.value if item is not None else fallback` | **不**捕获、**不**转型 |
| `manager.tick_polar_link` | `{name: module.get_setting(name) ... for name in (...15 项...)}` | 取原始 `Setting` 对象 |
| `persistence_mixin.get_setting_number` | 同 `_number_setting` | 捕获 |
| `polar_app` / `config_actions_mixin` | 各有各的 | 视觉冻结文件，本轮不动 |

问题不是"重复了几行"，而是**它们对同一件事给了不同的答案**：

* 值是垃圾时，`_number_setting` 静默退回默认值，而 `_bool_setting` 会抛
  `TypeError`/`ValueError`。同一个控制回路里两种设置项走两种错误语义，
  现场表现是"某个模块一开就崩"，而崩的栈指向读取器而不是配置本身。
* `float(default)` 与 `bool(default)` 都不做，调用方得自己记得转型。

现在把**每一种实际在用的语义**都显式命名，调用方按名字选，而不是每个人
现场手搓一份。**迁移不改变任何一处现有行为** —— `tests/settings_access_test.py`
逐个变体做等价性对拍（同一组输入下迁移前后逐项相同）。

# 新增读取语义时

先想清楚"值不合法时该怎么办"，然后**新增一个命名清楚的函数**，
不要去改已有函数的语义 —— 已有函数的语义都被人依赖着。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SettingLike",
    "find_setting",
    "raw_value",
    "collect_settings",
    "read_bool_strict",
    "read_bool_guarded",
    "read_number_guarded",
    "read_value",
]


class SettingLike:
    """只需要一个 ``value`` 属性的最小协议。

    写成 Protocol 而不是 `Setting` 具体类型，是为了让这些读取器
    在没有注册表的地方（回放、测试、纯函数）也能用。
    """

    value: Any


def find_setting(module: Any, name: str) -> Any:
    """按名字取设置项；模块不存在或没有这一项时返回 ``None``。

    注意 `module is None` 与 `module.get_setting(name) is None`` 两种情况
    走的是同一条路 —— 调用方绝大多数只是想"拿不到就用默认值"，
    没必要区分"没有这个模块"和"模块里没这一项"。
    """
    if module is None:
        return None
    return module.get_setting(name)


def raw_value(module: Any, name: str, fallback: Any) -> Any:
    """取 ``setting.value``，取不到（或模块为空）返回 ``fallback``。

    对应 `hero_lob` 内嵌 `setting()` 的语义：**不转型、不捕获异常**。
    值本身不合法时会原样抛出去。
    """
    setting = find_setting(module, name)
    return setting.value if setting is not None else fallback


def collect_settings(module: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """一次性取一组设置项的**原始 Setting 对象**（不是 value）。

    对应 `manager.tick_polar_link` 里那个 15 项的字典推导式。取对象而不是值，
    是因为调用方后面要读的不止 ``value``（还有 ``options``、``kind`` 等），
    各自再转型。
    """
    return {name: find_setting(module, name) for name in names}


def read_value(module: Any, name: str, fallback: Any) -> Any:
    """`raw_value` 的别名，语义完全相同。

    单独给一个名字是为了让调用点的意图写在脸上：
    读出来是"配置里的原值"还是"解释过的值"，看名字就知道。
    """
    return raw_value(module, name, fallback)


def read_bool_strict(module: Any, name: str, default: bool) -> bool:
    """bool 设置项，**不捕获**类型错 —— 复现 `auto_spin._bool_setting` 的语义。

    什么时候该用：调用方在**控制/安全**路径上，宁可崩也不要静默用一个
    和配置不符的默认值（比如把"允许驾驶"当成 False 就等于车不动了，
    崩掉反而能立刻看见）。迁移期保留它，是为了让行为**逐位不变**。
    """
    setting = find_setting(module, name)
    return bool(setting.value) if setting is not None else bool(default)


def read_bool_guarded(module: Any, name: str, default: bool) -> bool:
    """bool 设置项，值不合法时静默退回默认值。

    与 :func:`read_bool_strict` 的区别就是这一条。
    新代码请**优先用这个** —— 配置文件是人手写的、可能来自旧版本，
    一个不合法的值不应该让整个模块炸掉。
    """
    setting = find_setting(module, name)
    if setting is None:
        return bool(default)
    try:
        return bool(setting.value)
    except (TypeError, ValueError):
        return bool(default)


def read_number_guarded(module: Any, name: str, default: float) -> float:
    """数值设置项，值不合法时退回默认值。

    对应 `auto_spin._number_setting` 与 `persistence_mixin.get_setting_number`
    的共同语义。它们两个实现一模一样，所以合并成一处没有行为变化。

    **刻意没有挡 NaN。** ``float("nan")`` 不会抛，会静默穿过 `except`，
    而 NaN 参与比较时 ``x < 0`` 一律为 False —— 这确实是个隐患。
    但旧的两个实现也没挡，所以在"纯重构"这一轮加挡就是**偷偷改行为**：
    差异极小，却会让"迁移前后逐位相同"这条保证失效，review 时也说不清。
    这里保持等价，NaN 的事单独立项（它属于 config_store 的输入校验层，
    在那里挡比在每个读取器里挡更合适）。
    """
    setting = find_setting(module, name)
    if setting is None:
        return float(default)
    try:
        return float(setting.value)
    except (TypeError, ValueError):
        return float(default)


def set_value(registry: Any, module_name: str, setting_name: str, value: object) -> None:
    """按「模块名 → 设置项名」写入设置项；找不到就什么都不做。

    第 105 轮合并。原先有**两份逐字节相同**的实现：
    `MatchModesManager._set_setting`（`match_modes.py`）与
    `ScenarioManager._set_setting`（`scenarios.py`），两者方法体完全一致。

    保留原来那个「找不到就静默跳过」的语义**没有改**：
    模块不存在、设置项不存在、`registry.get` 返回 None —— 都直接返回。

    为什么两个类的私有方法还在（只把方法体换成调用这里）：
    调用点写的是 `self._set_setting(...)`，**那是它们内部的写法**；
    去掉方法会牵动调用点，而那对本次合并没有收益。
    合并掉的是**逻辑**，不是调用形状。
    """
    module = registry.get(module_name)
    setting = module.get_setting(setting_name) if module else None
    if setting is not None:
        setting.value = value
