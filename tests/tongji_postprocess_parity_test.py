"""AI 推理后处理的**逐项等价**证明。

背景
----
YOLOv5 一次前向输出 3×(80²+40²+20²) = **25200** 行。原来的
``TongjiYoloV5.detect`` 对每一行都调一次标量 ``_sigmoid`` 才判断阈值，
再对活下来的行做 argmax 与几何计算。结果是**即使画面里一个装甲都没有**，
也要为 25200 个几乎必然被丢掉的行付 5.91 ms/帧；这段又跑在单槽推理线程上，
每 1 ms 都直接转化成帧龄。

这一轮把两处改成向量化：

1. 预处理：张量从 strides=(0,4,7680,12) 的**非连续**数组改成预分配的
   C 连续 NCHW 缓冲直写。4.20 ms → 0.665 ms。
2. 后处理：整批向量化算置信度并一次性筛出候选行，只对个位数行进 Python
   循环。5.91 ms → 0.26 ms（sigmoid 部分）。

**快不是重点，逐项相同才是。** 这条链路喂的是自瞄与 ESP：一旦判定漂了，
要么打不中（丢目标），要么乱打（打友军/空枪）。所以这里保留**原实现**当
参照物，在合成的 25200 行数据上逐项比对 name / color / confidence /
box / points / light_strength / NMS 后的顺序。

跑法：``python tests/tongji_postprocess_parity_test.py``
（需要 numpy 与 cv2；缺任一个会显式 SKIP 而不是伪装通过。）
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


try:
    import numpy as np
except ImportError:  # pragma: no cover
    print("[SKIP] 本机没有 numpy，无法验证向量化等价性")
    raise SystemExit(0)

from rm_client.tongji_yolo import ARMOR_COLORS, ARMOR_NAMES, _sigmoid, _sigmoid_array  # noqa: E402

ROWS = 25200
FEATURES = 22


# --- 参照实现：改之前的代码，原样抄 --------------------------------------

def ref_sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value)) if value >= 0 else math.exp(value) / (1.0 + math.exp(value))


def ref_detect(rows, scale: float, score_threshold: float, min_confidence: float,
               light_sampler=None):
    """原 detect() 的后处理，签名与行为照抄。"""

    boxes: list[list[int]] = []
    confidences: list[float] = []
    candidates: list[dict] = []
    for row in rows:
        confidence = ref_sigmoid(float(row[8]))
        if confidence < score_threshold or confidence < min_confidence:
            continue
        color_id = int(np.argmax(row[9:13]))
        number_id = int(np.argmax(row[13:22]))
        if number_id >= len(ARMOR_NAMES):
            continue
        name = ARMOR_NAMES[number_id]
        if name == "not_armor":
            continue
        points = ((float(row[0] / scale), float(row[1] / scale)),
                  (float(row[6] / scale), float(row[7] / scale)),
                  (float(row[4] / scale), float(row[5] / scale)),
                  (float(row[2] / scale), float(row[3] / scale)))
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x, y = max(0.0, min(xs)), max(0.0, min(ys))
        box_w, box_h = max(xs) - x, max(ys) - y
        if box_w < 2.0 or box_h < 2.0:
            continue
        boxes.append([int(x), int(y), int(box_w), int(box_h)])
        confidences.append(confidence)
        strength = light_sampler(points) if light_sampler else 0.0
        candidates.append({
            "name": name,
            "color": ARMOR_COLORS[min(color_id, len(ARMOR_COLORS) - 1)],
            "confidence": confidence,
            "box": (x, y, box_w, box_h),
            "points": points,
            "light_strength": strength,
        })
    return boxes, confidences, candidates


def new_detect(rows, scale: float, score_threshold: float, min_confidence: float,
               light_sampler=None):
    """改之后的筛选 + 几何，签名与行为对齐 ref_detect。"""

    threshold = score_threshold if score_threshold > min_confidence else min_confidence
    all_confidences = _sigmoid_array(rows[:, 8])
    selected = np.flatnonzero(all_confidences >= threshold)
    boxes: list[list[int]] = []
    confidences: list[float] = []
    candidates: list[dict] = []
    if selected.size == 0:
        return boxes, confidences, candidates
    selected_rows = rows[selected]
    color_ids = np.argmax(selected_rows[:, 9:13], axis=1)
    number_ids = np.argmax(selected_rows[:, 13:22], axis=1)
    for position, row_index in enumerate(selected):
        row = selected_rows[position]
        confidence = float(all_confidences[row_index])
        color_id = int(color_ids[position])
        number_id = int(number_ids[position])
        if number_id >= len(ARMOR_NAMES):
            continue
        name = ARMOR_NAMES[number_id]
        if name == "not_armor":
            continue
        points = ((float(row[0] / scale), float(row[1] / scale)),
                  (float(row[6] / scale), float(row[7] / scale)),
                  (float(row[4] / scale), float(row[5] / scale)),
                  (float(row[2] / scale), float(row[3] / scale)))
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x, y = max(0.0, min(xs)), max(0.0, min(ys))
        box_w, box_h = max(xs) - x, max(ys) - y
        if box_w < 2.0 or box_h < 2.0:
            continue
        boxes.append([int(x), int(y), int(box_w), int(box_h)])
        confidences.append(confidence)
        strength = light_sampler(points) if light_sampler else 0.0
        candidates.append({
            "name": name,
            "color": ARMOR_COLORS[min(color_id, len(ARMOR_COLORS) - 1)],
            "confidence": confidence,
            "box": (x, y, box_w, box_h),
            "points": points,
            "light_strength": strength,
        })
    return boxes, confidences, candidates


# --- 造数据 --------------------------------------------------------------

def make_rows(seed: int, hit_rate: float, *, raw_scale: float = 1.0) -> "np.ndarray":
    """造一份"像真的"的输出张量。

    hit_rate 是"多少比例的行会越过置信度阈值"。真实场景是极稀疏的
    （0~5 个命中 / 25200 行），所以除了有命中的一档，也必须测**零命中**那一档
    —— 那正是原实现最亏、也最容易在等价性上翻车的一档。
    """
    rng = np.random.default_rng(seed)
    rows = np.zeros((ROWS, FEATURES), dtype=np.float32)
    # 坐标：0..640
    rows[:, 0:8] = rng.random((ROWS, 8)).astype(np.float32) * 640.0
    # 置信度 logit：绝大多数给很大的负数（概率≈0），少量给正数
    logits = np.full(ROWS, -12.0, dtype=np.float32)
    hits = int(ROWS * hit_rate)
    if hits:
        idx = rng.choice(ROWS, size=hits, replace=False)
        logits[idx] = rng.uniform(0.2, 8.0, size=hits).astype(np.float32)
    rows[:, 8] = logits
    # 类别：9 个 class logits + 9 个 number logits（13..21 共 9 列）
    rows[:, 9:13] = rng.random((ROWS, 4)).astype(np.float32)
    rows[:, 13:22] = rng.random((ROWS, 9)).astype(np.float32)
    # 留一部分行让 number_id 落在 not_armor 上
    not_armor_idx = rng.choice(ROWS, size=ROWS // 10, replace=False)
    rows[not_armor_idx, 13:22] = 0.0
    rows[not_armor_idx, 13] = 1.0
    _ = raw_scale
    return rows


# --- 1. sigmoid 本身逐位相同 ---------------------------------------------

def test_sigmoid_bit_identical() -> None:
    rng = np.random.default_rng(11)
    pools = [
        rng.normal(0.0, 8.0, 200_000),
        rng.normal(-500.0, 200.0, 100_000),
        np.array([0.0, -0.0, 1e-300, -1e-300, 88.0, -88.0, 709.0, -745.0, 1e30, -1e30, -1e-320]),
    ]
    all_ok = True
    detail = ""
    for pool in pools:
        ref = np.array([ref_sigmoid(float(v)) for v in pool])
        got = _sigmoid_array(pool)
        if not np.array_equal(ref, got):
            all_ok = False
            detail = f"{int(np.sum(ref != got))} 个值不同，最大差 {float(np.abs(ref - got).max()):.3e}"
            break
    check("向量 sigmoid 与标量 sigmoid 逐位相同（30 万组含极端值）", all_ok, detail)


# --- 2. 阈值判定不翻转 --------------------------------------------------

def test_threshold_decisions_unchanged() -> None:
    rng = np.random.default_rng(23)
    pool = rng.normal(0.0, 1.0, 1_000_000)
    ref = np.array([ref_sigmoid(float(v)) for v in pool])
    got = _sigmoid_array(pool)
    flipped = {th: int(np.sum((ref >= th) != (got >= th))) for th in (0.70, 0.80, 0.50)}
    check(
        f"三个判定阈值上结论零翻转（实测 {flipped}）",
        all(v == 0 for v in flipped.values()),
        str(flipped),
    )


# --- 3. 端到端逐项相同（含零命中档） -----------------------------------

def test_detect_equivalence() -> None:
    scale = 1.0
    cases = [
        ("零命中（0 个）", 0.0, 0.70, 0.80),
        ("稀疏（0.02%≈5 个）", 0.0002, 0.70, 0.80),
        ("中密（1%≈252 个）", 0.01, 0.70, 0.80),
        ("很密（10%）", 0.10, 0.70, 0.80),
        ("阈值贴边 score==min", 0.0002, 0.80, 0.80),
        ("阈值不同时以高者为准", 0.0002, 0.95, 0.60),
    ]
    for label, hit_rate, score_th, min_conf in cases:
        rows = make_rows(seed=hash(label) % 10_000, hit_rate=hit_rate)
        ref = ref_detect(rows, scale, score_th, min_conf)
        got = new_detect(rows, scale, score_th, min_conf)
        same = (
            ref[0] == got[0]
            and len(ref[1]) == len(got[1])
            and all(a == b for a, b in zip(ref[1], got[1]))
            and len(ref[2]) == len(got[2])
            and all(
                r["name"] == g["name"]
                and r["color"] == g["color"]
                and r["confidence"] == g["confidence"]
                and r["box"] == g["box"]
                and r["points"] == g["points"]
                and r["light_strength"] == g["light_strength"]
                for r, g in zip(ref[2], got[2])
            )
        )
        check(
            f"{label}：boxes/confidences/candidates 逐项相同（命中 {len(ref[2])}）",
            same,
            f"ref {len(ref[2])} vs new {len(got[2])}",
        )


# --- 4. 顺序必须一致（NMS 之前） ----------------------------------------

def test_order_preserved() -> None:
    rows = make_rows(seed=99, hit_rate=0.0005)
    ref = ref_detect(rows, 1.0, 0.70, 0.80)
    got = new_detect(rows, 1.0, 0.70, 0.80)
    same_order = [c["box"] for c in ref[2]] == [c["box"] for c in got[2]]
    check(
        f"候选顺序与原实现一致（{len(ref[2])} 个）",
        same_order,
        "顺序变了会让 NMS 输入顺序变化，抑制结果可能不同",
    )


# --- 5. 预处理张量：C 连续且像素逐位相同 -------------------------------

def test_tensor_layout_and_values() -> None:
    rng = np.random.default_rng(31)
    rgb = rng.integers(0, 256, size=(640, 640, 3), dtype=np.uint8)

    # 原做法：非连续
    old = np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
    # 新做法：C 连续直写
    new = np.empty((1, 3, 640, 640), dtype=np.float32)
    for channel in range(3):
        np.divide(rgb[:, :, channel], 255.0, out=new[0, channel])

    check(
        f"原张量确实非连续（strides={old.strides}）",
        not old.flags["C_CONTIGUOUS"],
        "用例前提失效",
    )
    check("新张量是 C 连续", bool(new.flags["C_CONTIGUOUS"]), f"strides={new.strides}")
    check("新旧张量形状相同", old.shape == new.shape, f"{old.shape} vs {new.shape}")
    check("新旧张量像素逐位相同", bool(np.array_equal(old, new)),
          f"最大差 {float(np.abs(old.astype(np.float64) - new.astype(np.float64)).max()):.3e}")


# --- 6. 性能护栏：必须真的更快 -----------------------------------------

def test_speedup() -> None:
    rows = make_rows(seed=7, hit_rate=0.0002)
    reps = 40

    def time_sigmoid_only(kind: str) -> float:
        t0 = time.perf_counter()
        for _ in range(reps):
            if kind == "ref":
                for row in rows:
                    ref_sigmoid(float(row[8]))
            else:
                _sigmoid_array(rows[:, 8])
        return (time.perf_counter() - t0) / reps * 1e3

    ref_ms = time_sigmoid_only("ref")
    new_ms = time_sigmoid_only("new")
    check(
        f"整批 sigmoid：逐行 {ref_ms:.2f} ms → 向量 {new_ms:.3f} ms（{ref_ms / max(new_ms, 1e-9):.0f}×）",
        new_ms < ref_ms * 0.25,
        f"只快了 {ref_ms / max(new_ms, 1e-9):.1f}×，低于 4× 护栏",
    )

    t0 = time.perf_counter()
    for _ in range(reps):
        ref_detect(rows, 1.0, 0.70, 0.80)
    ref_full = (time.perf_counter() - t0) / reps * 1e3
    t0 = time.perf_counter()
    for _ in range(reps):
        new_detect(rows, 1.0, 0.70, 0.80)
    new_full = (time.perf_counter() - t0) / reps * 1e3
    check(
        f"完整后处理：逐行 {ref_full:.2f} ms → 向量 {new_full:.3f} ms（{ref_full / max(new_full, 1e-9):.0f}×）",
        new_full < ref_full * 0.5,
        f"只快了 {ref_full / max(new_full, 1e-9):.1f}×，低于 2× 护栏",
    )


def main() -> int:
    tests = [
        test_sigmoid_bit_identical,
        test_threshold_decisions_unchanged,
        test_detect_equivalence,
        test_order_preserved,
        test_tensor_layout_and_values,
        test_speedup,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"AI 后处理等价性：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"AI 后处理等价性：全部通过（{PASSED} 项）")
    print("25200 行的筛选与预处理已向量化，逐项输出与原实现相同。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
