"""`_idle_zero_authorized` 的**不确定性往哪边倒**（第 135 轮）。

## 这条在守什么

`control.py` 的 `KeyboardControlBridge._idle_zero_authorized` 里有两句「读不到就怎么办」：

```python
control_state = getattr(safety, "control_state", None)
value = getattr(control_state, "value", control_state)
if str(value) != "MATCH_ARMED":
    return False                                     # 读不到阶段 -> fail-closed

is_fighting = getattr(safety, "is_fighting", None)
return not callable(is_fighting) or bool(is_fighting())   # 读不到 -> fail-OPEN
```

**同一个函数、同一类不确定性，两个相反的答案。**
第 135 轮把第二句改成 fail-closed，本文件钉住**两个方向**。

## 为什么值得钉

实际影响是有界的：这一帧是**全零**指令，而真发布仍由 `safety.can_publish()` 把关。
所以它不是「车动了」，而是「闸门锁着时还在尝试发」——

**但方向是反的，而这类方向错误一旦扩散就很难查。**
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.control import KeyboardControlBridge  # noqa: E402


class _State:
    def __init__(self, value):
        self.value = value


class _SafetyBare:
    """只给 `control_state`，**故意不给 `is_fighting`**。"""

    def __init__(self, value):
        self.control_state = _State(value)


class _SafetyFight(_SafetyBare):
    def __init__(self, value, fighting):
        super().__init__(value)
        self._fighting = fighting

    def is_fighting(self):
        return self._fighting


class _Publisher:
    def __init__(self, safety):
        self.safety = safety


def _auth(safety):
    bridge = KeyboardControlBridge(publisher=_Publisher(safety), mock_output=False)
    return bridge._idle_zero_authorized()


def main() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))

    mock = KeyboardControlBridge(publisher=None, mock_output=True)
    check("mock 恒为真（捷径不受影响）", mock._idle_zero_authorized() is True)

    check("非 MATCH_ARMED -> 不授权", _auth(_SafetyBare("NOT_STARTED")) is False)

    check(
        "MATCH_ARMED 且在交战 -> 授权",
        _auth(_SafetyFight("MATCH_ARMED", True)) is True,
    )

    check(
        "MATCH_ARMED 但不在交战 -> 不授权",
        _auth(_SafetyFight("MATCH_ARMED", False)) is False,
    )

    # ⭐ 这一条就是第 135 轮修的那一句
    got = _auth(_SafetyBare("MATCH_ARMED"))
    check(
        "**读不到 is_fighting -> 不授权**（原来恰好是反面）",
        got is False,
        "拿到 {} —— `not callable(...) or ...` 又回来了".format(got),
    )

    for name, ok, detail in checks:
        if not ok:
            print("FAIL {} {}".format(name, detail))
            return 1
    # ⚠ **第 154 轮：改成打 `[PASS]` 行。**
    # 原来只打中文数字，而 `verify_all.run()` 只数以 `[PASS]` 开头的行
    # —— **这个套件给门禁的贡献是 0。**
    for name, _ok, _detail in checks:
        print("[PASS] " + name)
    print("{} 项通过".format(len(checks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())