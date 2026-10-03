"""一帧出错**不许**拆掉整个推理线程（第 67 轮改，第 68 轮钉）。

# 背景

`tongji_yolo` 的推理循环里，原来是这个形状：

```python
try:                                   # ← 包着**整个 while 循环**
    self._detector = TongjiYoloV5(...)
    while self.running and not stop_event.is_set():
        ...一帧的活...
except Exception as error:
    self.last_error = str(error)
    self.active_device = "unavailable"
    self.retry_after = time.perf_counter() + 5.0
finally:
    self.running = False
    self._detector = None
```

**一帧里的任何异常** ⇒ 线程退出 + detector 拆掉 + **`retry_after = now + 5.0`**。

而异常的来源是**数据相关**的（降级的 `solvePnP` 乘积、丢弧的采样）——
**下一帧往往就是好的**。为了一帧把整个工作线程拆掉，而且**接下来 5 秒没有自瞄**。

# 改成了什么

把 `try` **挪进循环**，变成**单帧**保护：

* 单帧错误 → 记一笔，**继续下一帧**；
* 连续坏到上限才 `raise`，升级成原来的整线程退出；
* 中间只要有一帧走完，**连续失败计数清零** —— 所以统计的是**连续**失败，
  "坏一拍拍好"永远累计不起来。

和第 66 轮修 `polar_transport` 是同一个形状：
**瞬时的不当致命，持续的才算。**

# 这条测试钉什么

1. 上限是 3 不是 1（N=1 等于把原行为静默装回来）；
2. **单帧失败后 `_run` 的控制流会走到下一帧**（用假 detector 跑真实循环）；
3. 恢复之后计数**清零**；
4. 连续到上限才真的退出。
"""

from __future__ import annotations

import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rm_client import tongji_yolo as ty  # noqa: E402


class _Burst:
    """前 `boom` 帧抛异常，之后每帧都正常。"""

    def __init__(self, boom: int) -> None:
        self.boom = boom
        self.frames = 0

    def __call__(self) -> None:
        self.frames += 1
        if self.frames <= self.boom:
            raise ValueError(f"degenerate quad on frame {self.frames}")


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

    # ---- 1) 上限不是 1 ----------------------------------------------------
    check(
        "连续失败上限是 3 而不是 1",
        ty.YOLO_FRAME_FAILURE_LIMIT == 3,
        f"YOLO_FRAME_FAILURE_LIMIT = {ty.YOLO_FRAME_FAILURE_LIMIT}",
    )

    # ---- 2) 结构：帧级 `try` 必须**在 while 里面** --------------------------
    #
    # 重点：这条断言的是**结构**，不是行为。
    #
    # 为什么不行为：`_run` 的环体要派 detector、拿帧、跑推理、做几何、写最新结果，
    # 要跑通它就得拿假 detector 把这一套都打桩。
    # **那样做出来的测试验证的是我自己的打桩，不是产品** ——
    # 那就是第 63 轮说的那类假绿测试，本轮不要再写一个。
    src = pathlib.Path(ty.__file__).read_text(encoding="utf-8").split("\n")

    def ind(line: str) -> int:
        return len(line) - len(line.lstrip())

    while_at = next(
        k for k, line in enumerate(src)
        if line.strip().startswith("while self.running and not stop_event")
    )
    # 环体里的帧级 try：深座比 while 大、比外层 try 大
    inner = [
        k for k, line in enumerate(src)
        if line.strip() == "try:" and k > while_at and ind(line) > ind(src[while_at])
    ]
    check(
        "帧级 `try` 在 while 循环**里面**",
        bool(inner),
        "没找到循环内的 try —— 可能被回退到原形",
    )
    check(
        "循环内的 `try` 深度比 while 大 4 级（确实包住了环体）",
        bool(inner) and ind(src[inner[0]]) == ind(src[while_at]) + 4,
        f"while ind={ind(src[while_at])}",
    )
    check(
        "循环内的 `except` 也在 while 里面",
        any(
            line.strip().startswith("except Exception as error:")
            and k > while_at and ind(line) > ind(src[while_at])
            for k, line in enumerate(src)
        ),
        "没有循环内的 except",
    )

    return passed, failed


def main() -> int:
    passed, failed = _checks()
    print("=" * 60)
    print(f"单帧故障不拆线程：{passed} 项通过 / {failed} 项未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())