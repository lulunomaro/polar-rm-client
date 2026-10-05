"""四个**零调用者**的函数，以及它们**并不是**彼此的副本（��� 138 轮）。

## 这份文件在守什么

第 134 轮的子代理报出四个零调用者的函数。本轮**独立复算**（AST 全仓扫描），
结论**一致**：

| 函数 | 定义处 | 调用点 |
|---|---|---|
| `clear_intent` | `rm_client/backend/polar_transport.py` | **0** |
| `pack_cmd` | `rm_client/backend/dt7_link.py` | **0** |
| `read_value` | `rm_client/settings_access.py` | **0**（连测试都没有） |
| `read_bool_guarded` | `rm_client/settings_access.py` | **1**，且**只在测试里** |

## ⚠ 但子代理说的「两处是重复实现」**是错的** —— 本文件把差别钉出来

### `clear_intent` 与 `trigger_estop` **不是同一个操作**

```python
def clear_intent(self):
    with self._intent_lock:
        self._intent = None                      # 只清意图

def trigger_estop(self):
    with self._intent_lock:
        self._estop_active = True                # \u7acb\u573a
        self._intent = None                      # \u4e5f\u6e05\u610f\u56fe
    self._spin_cmd = SPIN_CMD_OFF                # \u8fd8\u8981\u5173\u81ea\u8f6c
```

**`clear_intent` 是「不清急停、不关自转」的那个更轻的操作。**
它是**一个未被使用的操作**，**不是一个副本**。

### `pack_cmd` 与 `cmd_wire_of` **签名、返回类型、出错方式全不同**

| | `cmd_wire_of(prof: dict)` | `pack_cmd(cmd_set, cmd_id)` |
|---|---|---|
| 入参 | **一个 dict** | **两个 int** |
| 返回 | `int \\| None` | **`bytes`** |
| 越界时 | **静默返回 `None`** | **抛 `ValueError`** |
| 用途 | profile \u2192 \u7ebf\u503c int | \u7ebf\u503c int \u2192 \u6253\u5305\u540e\u5b57\u8282 |

**它们在不同层**（\u4e00\u4e2a\u662f\u201c\u914d\u7f6e\u5230\u7ebf\u503c\u201d\uff0c\u4e00\u4e2a\u662f\u201c\u7ebf\u503c\u5230\u5b57\u8282\u201d\uff09\u3002

**\u6309\u300c\u5220\u6389\u91cd\u590d\u5b9e\u73b0\u300d\u7684\u7406\u7531\u53bb\u5220\u5b83\u4eec\uff0c\u4f1a\u5220\u6389\u4e00\u4e2a\u53e6\u5916\u7684\u9519\u8bef\u5951\u7ea6\u3002**
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("clear_intent", "pack_cmd", "read_value", "read_bool_guarded")


def _repo_python_files():
    for path in ROOT.rglob("*.py"):
        parts = path.parts
        if ".venv-polar" in parts or ".git" in parts or "__pycache__" in parts:
            continue
        if any(p.startswith("verify_all_") for p in parts):
            continue
        # \u6784\u5efa\u4ea7\u7269\u91cc\u7684\u540c\u540d\u4e0d\u7b97\uff08openvino \u91cc\u4e5f\u6709 read_value\uff09
        if "tmp_pyinstaller_smoke" in parts or "dist" in parts or "build" in parts:
            continue
        # **本文件自己**也调用那几个函数（为了验它们），
        # 不排除就会把自己计成「调用点」，
        # 那语句每次跑都假。**第 138 轮实测时乳这个。**
        if path.name == Path(__file__).name:
            continue
        yield path


def _call_sites():
    hits = {name: [] for name in NAMES}
    for path in _repo_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name in hits:
                hits[name].append("{}:{}".format(path.relative_to(ROOT).as_posix(), node.lineno))
    return hits


def main() -> int:
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))

    hits = _call_sites()
    for name in ("clear_intent", "pack_cmd", "read_value"):
        check(
            "{} 仍然是零调用者".format(name),
            not hits[name],
            "现在有了调用点：{} —— 那本条该重写".format(hits[name]),
        )
    check(
        "read_bool_guarded 只有测试在用，生产侧零调用",
        all("tests/" in loc for loc in hits["read_bool_guarded"]) and hits["read_bool_guarded"],
        "调用点：{}".format(hits["read_bool_guarded"]),
    )

    # --- ⭐ 两处「重复」的说法是错的：把差别钉住 -------------------------
    from rm_client.backend import dt7_link, polar_transport

    # pack_cmd 返回 bytes，cmd_wire_of 返回 int | None
    check(
        "pack_cmd 返回 **bytes**，而 cmd_wire_of 返回 **int | None** —— 不是同一个函数",
        dt7_link.pack_cmd(0x03, 0x2A) == b"\x03\x2a"
        and dt7_link.cmd_wire_of({"cmd_id_keyboard": 0x2A, "cmd_set": 0x03}) == 0x2A03,
        "两者对同一组入参给出的东西不同：pack_cmd 给字节，cmd_wire_of 给线值",
    )
    # \u8d8a\u754c\u65f6\uff1a\u4e00\u4e2a\u9759\u9ed8\u8fd4\u56de None\uff0c\u53e6\u4e00\u4e2a\u629b ValueError
    check(
        "越界时 cmd_wire_of 静默返回 None，而 pack_cmd 抛 ValueError —— **错误契约不同**",
        dt7_link.cmd_wire_of({"cmd_id_keyboard": 999, "cmd_set": 0}) is None,
    )
    try:
        dt7_link.pack_cmd(0x03, 999)
        raised = False
    except ValueError:
        raised = True
    check("pack_cmd 越界确实抛 ValueError", raised, "它没抛 —— 那两条错误契约其实是同一个")

    # clear_intent 只清意图；trigger_estop 还置急停、关自转
    from rm_client.backend.polar_transport import PolarLinkTransport as _T

    check(
        "clear_intent 与 trigger_estop **不是同一个操作**（一个有急停侧效��一个没有）",
        hasattr(_T, "clear_intent") and hasattr(_T, "trigger_estop"),
    )
    src = inspect.getsource(_T.clear_intent)
    check(
        "clear_intent 只碰 `_intent`，**不碰** `_estop_active`",
        "_intent" in src and "_estop_active" not in src,
        "它的方法体里出现了 _estop_active —— 那它就不是那个「更轻的操作」了",
    )
    estop_src = inspect.getsource(_T.trigger_estop)
    check(
        "trigger_estop 才碰 `_estop_active`（所以两者确实不同）",
        "_estop_active" in estop_src,
        "trigger_estop 里没有 _estop_active —— 那两者的差别没了，本条要重写",
    )

    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
            print("FAIL {} {}".format(name, detail))
    if failed:
        return 1
    print("{} 项通过".format(len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())