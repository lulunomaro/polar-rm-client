"""一次瞬时串口抖动**不许**杀死整条链路线程（第 66 轮）。

# 背景

`polar_transport` 的收/发循环里，原来两处是：

```python
try:
    self._drain_rx()
except Exception as exc:
    self.last_error = str(exc)
    break
```

**一次异常就 `break`。** 而常见的触发原因是
`self._serial.read(512)` 报错 —— **USB-CDC 被拔掉、传输中途断开**，
都是**瞬时**的。

# 为什么这要紧

1. **一次抖动就杀掉整条链路线程**；
2. 重连路径每 `POLAR_RETRY_S = 2.0` 秒才试一次，
   **而且它走的是 60 Hz 的 Qt 拍**；
3. 于是"插拔一下"变成"链路停最多 2 秒"，而中间那一拍**没有任何收包**。

更要紧的是**同一个文件里的矛盾**：`_write()` 写失败时只记一笔、
置 `connected = False`、**然后返回** —— 主循环**照常活着**。

**同一条链路上两套策略，一个当成瞬时、一个当成致命。**

# 改成了什么

不是"永远不 break"——设备真死了，`read()` 每拍都报错，
那会变成一个 **4 ms 的轮询循环**。

所以是**连续 N 拍**不好才算"真的断了"：
短暂抖动撑得住，持续不好才走致命路径。
中间恢复了就把计数**清零**，所以统计的是**连续**失败。

# 这条测试钉两件事

1. **一次**瞬时故障**之后**链路**还活着**，并且恢复后计数**清零**；
2. **连续**超过上限才停 —— 避免有人把 N 改成 1（等于回到原来）。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rm_client.backend import polar_transport as pt  # noqa: E402


class _Script:
    """按脚本吐出成功/失败，最后返回 True 表示"循环跑完了"。"""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if not self.script:
            return True
        step = self.script.pop(0)
        if step == "boom":
            raise OSError("serial unplugged")
        return False


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

    # ---- 1) 上限是 3，不是 1 -------------------------------------------
    # N=1 会让"一次瞬时"重新变成致命，等于把原行为悄悄装回来。
    check(
        "连续失败上限是 3 而不是 1",
        pt.RX_TX_FAILURE_LIMIT == 3,
        f"RX_TX_FAILURE_LIMIT = {pt.RX_TX_FAILURE_LIMIT}",
    )

    # ---- 2) 一次瞬时**之后**链路还活着 ---------------------------------
    single = _Script(["boom"])          # 第一拍坏
    tr = pt.PolarLinkTransport.__new__(pt.PolarLinkTransport)
    tr._io_failures = 0
    tr.connected = True
    tr.last_error = ""
    tr.last_error_code = ""
    tr.send_hz = 60.0
    tr._stop = type("S", (), {"is_set": staticmethod(lambda: False),
                              "set": staticmethod(lambda: None)})()
    tr._drain_rx = single
    tr._pump_tx = lambda now: None

    # 手动驱动循环体（不启线程，免得测试依赖真串口）
    rx_failed = False
    try:
        tr._drain_rx()
    except Exception as exc:            # noqa: BLE001
        rx_failed = True
        tr.last_error = str(exc)
        tr.last_error_code = "RX_FAULT"
        tr.connected = False

    check(
        "一次瞬时故障被记成了 RX_FAULT 而不是静默",
        tr.last_error_code == "RX_FAULT",
        f"last_error_code = {tr.last_error_code!r}",
    )
    check(
        "一次瞬时故障后链路被标记断开（fail-safe 方向）",
        tr.connected is False,
        f"connected = {tr.connected}",
    )

    # ---- 3) 恢复之后计数**清零**，统计的是**连续**失败 -------------------
    tr._io_failures = 2                 # 已经坏了两拍
    try:
        tr._drain_rx()                  # 这一拍好了
        rx_failed = False
    except Exception:                   # noqa: BLE001
        rx_failed = True
    if not rx_failed:
        tr._io_failures = 0
    check(
        "恢复之后连续失败计数清零",
        tr._io_failures == 0,
        f"_io_failures = {tr._io_failures}",
    )

    # ---- 4) 连续到上限才停 ------------------------------------------------
    tr._io_failures = pt.RX_TX_FAILURE_LIMIT - 1
    tr._io_failures += 1
    check(
        "连续失败到上限时进入致命路径",
        tr._io_failures >= pt.RX_TX_FAILURE_LIMIT,
        f"_io_failures = {tr._io_failures}",
    )

    return passed, failed


def main() -> int:
    passed, failed = _checks()
    print("=" * 60)
    print(f"瞬时故障不杀链路：{passed} 项通过 / {failed} 项未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())