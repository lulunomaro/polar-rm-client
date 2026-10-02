"""把一条**够不着但很脆**的闸门钉住：mock-output 的 fail-open 方向。

# 这条不是"发现了一个 bug"

第 12 轮 `rm-readiness` 那批搬迁时发现，就绪度里有两处闸门长这样：

    bool(mqtt.get("connected", False)) and not bool(mqtt.get("mock_output", True))

方向是 **fail-closed 的那一侧是对的、fail-open 的那一侧是错的**：

| ``mock_output`` 的值 | 闸门 |
|---|---|
| 键**不存在** | 拒绝（默认 True → 取反为 False） |
| ``True`` | 拒绝 |
| ``False`` | 放行 |
| ``None`` | **放行** |
| ``0`` | **放行** |
| ``""`` | **放行** |

也就是说：**只要有人往那个 dict 里塞一个不是布尔的东西，闸门就从"拒绝"翻成"放行"。**
而这是两道**决定能不能真发出去**的闸门（`mqtt_real_publish` 与
`KeyboardMouseControl` / `CustomControl` 的上行判定）。

**但它现在够不着** —— 见下面"为什么现在够不着"。

# 为什么现在够不着

写 `mock_output` 的地方有两条，都在 `mqtt_backend.py` / `manager.py`：

    mqtt_backend.py:190   self.mock_output = bool(mock_output)
    manager.py:211        self.mqtt.mock_output = not bool(config.safety.allow_mqtt_publish)
    mqtt_backend.py:621   "mock_output": self.mock_output      # 进 stats dict

三处都产出**真正的 `bool`**，所以 `readiness.py:122` 那句
`mqtt = dict(stats.get("mqtt", {}))` 读回来的一定是布尔。

**所以这不是一个现场故障，是一个"下一次有人改了写入端就会中招"的陷阱。**

# 那为什么不直接修

因为**现在没有 bug 可修**。改 `bool(...)` 的写法会让行为在**当前**路径上完全不变
（值本来就是 bool），却让人以为这里曾经出过事、以后要小心 ——
那是给不存在的问题制造历史包袱。

正确的做法是：**把"写入端必须是 bool"这条钉成断言**。
哪天有人图省事写成 `self.mock_output = mock_output`（去掉那个 `bool()`），
或者把它改成从 JSON 配置里直接读，这条会立刻红。

# 为什么值得单独一个文件

因为它防的是**一种改写**，不是一条分支：

* 它不测闸门本身的逻辑（那是 `rm-readiness` 的向量在管）；
* 它测的是**"写入端做了类型收敛"这个事实**。

这两件事在别的套件里都没有对应的断言 ——
现有测试都是"喂一个 bool 进去，看闸门对不对"，
而 bug 恰恰会发生在"喂进去的不是 bool"的那一侧。

跑法：``python tests/mock_output_gate_fragility_test.py``
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend import readiness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
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


def _gate(mqtt: dict) -> bool:
    """与 `readiness.py` 里那两处**逐字相同**的判据。

    刻意直接抄表达式而不是调 `audit_backend`：
    我们要验的是**这个表达式本身**的语义，而 `audit_backend` 还要一整个 manager。
    """
    return bool(mqtt.get("connected", False)) and not bool(mqtt.get("mock_output", True))


# ===========================================================================
# 1. 表达式本身：哪些取值会放行
# ===========================================================================

def test_the_gate_refuses_when_the_key_is_absent_or_true() -> None:
    """键不存在 / 值为 True ⇒ **拒绝**。这两条是 fail-closed 的正确一侧。"""
    check("键不存在 ⇒ 拒绝（这是默认值 0 要保住的那一侧）", not _gate({"connected": True}))
    check("键为 True ⇒ 拒绝", not _gate({"connected": True, "mock_output": True}))
    check("未连接时无论 mock_output 是什么都拒绝",
          not _gate({"connected": False, "mock_output": False}))


def test_the_gate_allows_only_when_explicitly_not_mocking() -> None:
    check("键为 False ⇒ 放行", _gate({"connected": True, "mock_output": False}))


def test_non_boolean_values_would_flip_the_gate_to_open() -> None:
    """**这是本文件的核心断言。**

    `None` / `0` / `""` 都会被 `bool()` 取成假，于是闸门**放行**。
    现在够不着（见文件头），但它说明了这个表达式**朝哪个方向坏**。

    把它写成断言而不是注释，是因为将来有人真的改了写入端、
    或者把这个 dict 的来源从"代码里的 bool"换成"配置文件里的 JSON"，
    这里会先红 —— 那时候才知道这条的严重性，而不是等现场事故。
    """
    for label, value in (("None", None), ("0", 0), ("空串", ""), ("空列表", [])):
        check(
            f"mock_output = {label} 会让闸门**放行**（fail-open 方向）",
            _gate({"connected": True, "mock_output": value}),
            f"{label!r} 竟然没放行 —— 表达式变了？",
        )


# ===========================================================================
# 2. 为什么现在够不着：写入端做了类型收敛
# ===========================================================================

def _assignments(path: Path, attr: str) -> list[ast.Assign]:
    """找出所有**以 `.attr` 结尾**的赋值节点。

    刻意接受 `self.mock_output` 与 `self.mqtt.mock_output` 两种形态 ——
    第一版只认 `self.<attr>`（要求 `target.value` 是 `Name('self')`），
    于是 `manager.py:211` 的 `self.mqtt.mock_output = not bool(...)` 被漏掉，
    只找到 1 处赋值。**断言数对不上的时候我第一个反应应该是"匹配器坏了"**，
    而不是"代码只有那一处"。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: list[ast.Assign] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            chain: list[str] = []
            cursor: ast.expr = target
            while isinstance(cursor, ast.Attribute):
                chain.append(cursor.attr)
                cursor = cursor.value
            if isinstance(cursor, ast.Name) and chain and chain[0] == attr:
                found.append(node)
    return found


def test_every_writer_coerces_mock_output_to_bool() -> None:
    """`self.mock_output` 的**每一处**赋值都必须经过 `bool(...)`。

    这是本文件真正在守的东西：不是闸门的逻辑，而是
    **"写入端做了类型收敛、所以那个 fail-open 的表达式够不着"** 这个事实。

    现有测试全都是"喂一个 bool 进去看闸门对不对" ——
    而 bug 恰恰会落在"喂进去的不是 bool"那一侧，没有任何现有断言覆盖它。

    所以这里走 AST 而不是正则：正则会漏（多行赋值、属性链更深、
    `setattr` 形式），AST 认的是"这是一次对 self.<attr> 的赋值"这件事本身。
    """
    offenders: list[str] = []
    total = 0
    for relative, attr in (("backend/mqtt_backend.py", "mock_output"),
                            ("backend/manager.py", "mock_output")):
        path = ROOT / "rm_client" / relative
        for node in _assignments(path, attr):
            total += 1
            # 允许 `= bool(x)` 与 `= not bool(x)`；其他形式一律算"没收敛"。
            src = ast.unparse(node.value)
            if "bool(" not in src:
                offenders.append(f"rm_client/{relative}:{node.lineno}  self.{attr} = {src}")
    check(f"self.mock_output 的 {total} 处赋值都做了 bool() 收敛", not offenders,
          "；".join(offenders))
    # 第一版这里写的是 `>= 3`，报"只找到 1 处"——那是**匹配器**漏了
    # `self.mqtt.mock_output`（嵌套属性链）那一处。修好匹配器后只剩 2 处，
    # 而 2 处才是真实的数量：全仓确实只有 `mqtt_backend.py:190` 与 `manager.py:211`
    # 两处写它，我原先数成 3 是凭印象。
    #
    # 所以门槛是 `>= 2`，并且**注释写明它为什么是 2** ——
    # 免得下一个人看到"只有 2 处"时以为 AST 又漏了，再花一轮去修匹配器。
    check("确实找到了赋值点（不是 0 处才'通过'）", total >= 2,
          f"只找到 {total} 处 —— AST 匹配本身可能坏了，或写入端少了")


def test_stats_dict_carries_the_coerced_bool() -> None:
    """`stats()` 里那个 dict 必须直接给出 `self.mock_output`，不能重新算一遍。

    如果它写成 `"mock_output": not self.connected` 之类，
    那 readiness 读到的就与写入端无关，类型收敛这条链就断了。
    """
    path = ROOT / "rm_client" / "backend" / "mqtt_backend.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []
    seen = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "mock_output"
                        for k in node.keys)):
            seen += 1
            value = node.values[
                [i for i, k in enumerate(node.keys)
                 if isinstance(k, ast.Constant) and k.value == "mock_output"][0]]
            src = ast.unparse(value)
            if "self.mock_output" not in src:
                bad.append(f"L{node.lineno}: {src}")
    check(f"stats dict 里的 mock_output 直接取 self.mock_output（{seen} 处）", not bad,
          "；".join(bad))
    check("确实找到了那处 dict（不是 0 处才'通过'）", seen >= 1, f"只找到 {seen} 处")


# ===========================================================================
# 3. 那两处闸门用的是什么判据
# ===========================================================================

def test_both_gates_use_the_same_expression() -> None:
    """两处闸门（`mqtt_real_publish` 与上行判定）必须是**同一个**判据。

    如果将来有人把其中一处改成 `mqtt.get("mock_output") is False`，
    语义会**变**（`None` 从"放行"变成"拒绝"）而另一个不变 ——
    于是两个出口的严格程度不一样，而没有任何测试会红。

    所以这里钉住"两处写的是同一个表达式"，不是"每处各自对"。
    """
    path = ROOT / "rm_client" / "backend" / "readiness.py"
    source = path.read_text(encoding="utf-8")
    expected = ('bool(mqtt.get("connected", False)) '
                'and not bool(mqtt.get("mock_output", True))')
    count = source.count(expected)
    check(f"两处闸门用的是同一个判据（找到 {count} 处）", count >= 2,
          f"只找到 {count} 处 —— 有一处被改成了别的写法")
    # 反向：不能有第三种写法混进来
    for stray in ('mqtt.get("mock_output") is False',
                  'mqtt.get("mock_output", False)',
                  '"mock_output" in mqtt'):
        check(f"没有混进 {stray!r} 这种别的写法", stray not in source,
              f"readiness.py 里出现了 {stray!r}")


def test_readiness_module_is_where_we_think_it_is() -> None:
    """守住"我审计的是哪个文件"这个前提。

    上面的断言全是文本/AST 级的 —— 万一 `readiness` 被搬走或改名，
    它们照样会绿，只是绿在一个没人看的文件上。所以显式钉一下。
    """
    check("readiness 模块在 backend/ 下且能被 import", readiness.__file__.endswith(
        "backend\\readiness.py") or readiness.__file__.endswith("backend/readiness.py"),
        readiness.__file__)


def main() -> int:
    tests = [
        test_the_gate_refuses_when_the_key_is_absent_or_true,
        test_the_gate_allows_only_when_explicitly_not_mocking,
        test_non_boolean_values_would_flip_the_gate_to_open,
        test_every_writer_coerces_mock_output_to_bool,
        test_stats_dict_carries_the_coerced_bool,
        test_both_gates_use_the_same_expression,
        test_readiness_module_is_where_we_think_it_is,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"mock-output 闸门脆性：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"mock-output 闸门脆性：全部通过（{PASSED} 项）")
    print("这条闸门现在够不着（写入端收敛成了真正的 bool），但朝 fail-open 方向坏。")
    print("本文件守的是那个'够不着'的前提本身 —— 前提没了它会先红。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())