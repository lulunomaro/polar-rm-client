"""输入松开钩子抛异常时：**状态照落，但失败必须看得见**（第 53 轮）。

# 背景

`rm_client/backend/safety.py` 的 `_notify_input_clear` 原本是：

```python
try:
    hook(str(reason))
except Exception:
    pass
```

# 为什么这个 `pass` 有问题

那个 hook 是**唯一**能在 `LOCKED` / `ESTOP` 时松开卡住的按键的机制。
下游 `input_dispatch_mixin._clear_control_inputs_for_lock` 的文档写着：
DT7 与官方客户端**没有**陈旧帧超时 —— 松不开就等于**那个键一直被按住**。

而状态是在**通知之前**就写进去的。于是 hook 一抛：
状态机报着 `ESTOP`，而车还按着最后一个键，**而日志里什么都没有**。

# 这条测试钉的是两件事，而且**两件都要**

1. **控制流不变**：状态照落（这是安全方向，不该因为通知失败就不落）；
2. **失败可���**：`input_clear_failures` 涨、`last_input_clear_error` 非空。

只钉第一件会漏掉"安静的 ESTOP"；
只钉第二件会有人为了"别污染状态"而把转移吞掉 —— **那更糟**。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rm_client.backend.safety import ControlState, OfficialSafety  # noqa: E402


def _checks() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name} :: {detail}")

    # ---- 一个每次都抛的 hook -------------------------------------------
    def exploding(reason: str) -> None:
        raise RuntimeError("松键失败")

    safety = OfficialSafety()
    safety.input_clear_hook = exploding

    safety.trigger_estop()
    # ① 状态**必须**照落 —— 通知失败不该阻止状态转移。
    check(
        "通知失败时 ESTOP 状态照落",
        safety.control_state is ControlState.ESTOP,
        f"control_state = {safety.control_state!r}",
    )
    # ② 失败必须**看得见** —— 这条是第 53 轮修的那句 `pass`。
    check(
        "通知失败被记进 input_clear_failures",
        safety.input_clear_failures >= 1,
        f"input_clear_failures = {safety.input_clear_failures}",
    )
    check(
        "通知失败留下了原因文本",
        bool(safety.last_input_clear_error),
        f"last_input_clear_error = {safety.last_input_clear_error!r}",
    )

    # ---- 正常 hook 不该被误记 ------------------------------------------
    ok_safety = OfficialSafety()
    seen: list[str] = []
    ok_safety.input_clear_hook = seen.append
    ok_safety.trigger_estop()
    check(
        "正常 hook 不会被误记成失败",
        ok_safety.input_clear_failures == 0,
        f"input_clear_failures = {ok_safety.input_clear_failures}",
    )
    check(
        "正常 hook 确实被调用过",
        bool(seen),
        f"seen = {seen!r}",
    )

    # ---- 连续失败会累加，而不是被覆盖 ----------------------------------
    many = OfficialSafety()
    many.input_clear_hook = exploding
    many.trigger_estop()
    first = many.input_clear_failures
    many.trigger_estop()
    check(
        "连续失败会累加",
        many.input_clear_failures == first + 1,
        f"{first} -> {many.input_clear_failures}",
    )

    return passed, failed


def main() -> int:
    passed, failed = _checks()
    print("=" * 60)
    print(f"钩子失败记账：{passed} 项通过 / {failed} 项未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())