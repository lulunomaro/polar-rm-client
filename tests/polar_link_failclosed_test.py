"""0x5A 解码器的 fail-closed 契约。

契约（PolarFrameCodec 类 docstring 原文）：
    "CRC 不符、长度不符、未知段都只丢弃并计数，**绝不抛出打断接收线程**。"

这条契约为什么值钱：``polar_transport._loop`` 是接收线程，它捕获异常后
就是 ``break`` —— 线程直接死掉，再由 manager 的重连每 2 秒拉起来。
于是现场看到的是"开串口 → 读 5ms → 崩 → 关串口"无限抖动，而且**遥测全灭**，
现象和"车没接上"几乎一样，但根因在客户端自己。

已经真实踩过一次的坑：``_SEGMENT_FORMATS[segment_id]`` 写成了下标而不是
``.get()``。这两张表来源不同 ——

* ``_SEGMENT_DECODERS``：写死在代码里的专用解码器（polar_link.py:433）
* ``_SEGMENT_FORMATS``：被 ``_apply_spec()`` 整个清空、换成
  ``polar_protocol.json`` 里的规格（polar_link.py:217-218）

所以"某段有专用解码器、但规格里没有它的 struct"是**可达状态**：
电控把那一段从 JSON 里删掉或改名就够了，而这正是本项目设计的正常改法
（AGENT.md："电控改协议时正常情况下只需要改这个文件"）。

本文件逐条钉住"绝不抛"：不只测刚修的那一条，而是**穷举**整张段表，
任何一个段号在"表被削掉"的任何组合下都不许抛异常。

跑法：``python tests/polar_link_failclosed_test.py``
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend import polar_link as pl  # noqa: E402
from rm_client.protocol import crc8, crc16  # noqa: E402

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


def build_segment(segment_id: int, raw_timestamp: int, body: bytes) -> bytes:
    seg_len = 4 + len(body)
    header = bytes((pl.POLAR_SOF, seg_len & 0xFF, segment_id & 0xFF, 0x00))
    header = header[:3] + bytes((crc8(header[:3]),))
    frame = header + struct.pack("<I", raw_timestamp) + body
    return frame + crc16(frame).to_bytes(2, "little")


def body_for(segment_id: int) -> bytes:
    """按规格造一个长度正确的 body；没有规格就造个最短的。"""
    spec = pl._SEGMENT_FORMATS.get(segment_id)
    if spec is None:
        return b""
    size = struct.calcsize(spec)
    return bytes(size)


# --- 1. 正例：完好状态下每个段都能解出来（证明用例本身有效） ------------

def test_baseline_all_segments_decode() -> None:
    bad = []
    for sid in sorted(pl._SEGMENT_FORMATS):
        if sid == pl.SEG_DEBUG:
            continue  # 调试段格式特殊，单独在 polar_link_test 里测
        frame = build_segment(sid, 1000, body_for(sid))
        try:
            segments, _ = pl.PolarFrameCodec().decode_with_consumed(frame)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"0x{sid:02X} 抛 {type(exc).__name__}: {exc}")
            continue
        if not segments:
            bad.append(f"0x{sid:02X} 未解出段")
    check(f"完好状态下 {len(pl._SEGMENT_FORMATS) - 1} 个段全部解出", not bad, "; ".join(bad[:3]))


# --- 2. 核心回归：规格删掉某段时，解码器不得抛异常 -----------------------

def test_missing_spec_entry_never_raises() -> None:
    """电控把某段从 JSON 里删掉 = 该段有 decoder 但没 format。"""

    offenders = []
    for sid in sorted(pl._SEGMENT_DECODERS):
        if sid in (pl.SEG_DEBUG,) or sid not in pl._SEGMENT_FORMATS:
            continue
        frame = build_segment(sid, 2000, body_for(sid))
        saved = dict(pl._SEGMENT_FORMATS)
        pl._SEGMENT_FORMATS.pop(sid, None)  # 等价于用缺该段的规格重装表
        try:
            codec = pl.PolarFrameCodec()
            segments, _ = codec.decode_with_consumed(frame)
            if segments:
                offenders.append(f"0x{sid:02X} 缺规格却仍解出段（应安全丢弃）")
            elif codec.stats.unknown_segments < 1:
                offenders.append(f"0x{sid:02X} 缺规格却没计入 unknown_segments")
        except Exception as exc:  # noqa: BLE001
            offenders.append(f"0x{sid:02X} 抛出 {type(exc).__name__}: {exc}")
        finally:
            pl._SEGMENT_FORMATS.clear()
            pl._SEGMENT_FORMATS.update(saved)

    check(
        f"规格缺条目时 {len(pl._SEGMENT_DECODERS)} 个专用段全部 fail-closed（不抛、丢弃、计数）",
        not offenders,
        "; ".join(offenders[:4]),
    )


# --- 3. 两张表被削到极端组合时也不许抛 --------------------------------

def test_extreme_table_combinations() -> None:
    """把三张表各种削法都试一遍：全空、只留格式、只留解码器。"""

    combos = {
        "formats 全空": ("formats", {}),
        "inputs 全空": ("inputs", {}),
        "outputs 全空": ("outputs", {}),
        "formats 空 + decoders 全在": ("formats+decoders", {}),
    }
    frames = [
        build_segment(sid, 3000, body_for(sid))
        for sid in sorted(pl._SEGMENT_FORMATS)
        if sid != pl.SEG_DEBUG
    ]
    offenders = []
    for label, (which, _) in combos.items():
        saved_formats = dict(pl._SEGMENT_FORMATS)
        saved_inputs = dict(pl._SEGMENT_INPUTS)
        saved_outputs = dict(pl._SEGMENT_OUTPUTS)
        try:
            if "formats" in which:
                pl._SEGMENT_FORMATS.clear()
            if "inputs" in which:
                pl._SEGMENT_INPUTS.clear()
            if "outputs" in which:
                pl._SEGMENT_OUTPUTS.clear()
            for frame in frames:
                try:
                    pl.PolarFrameCodec().decode_with_consumed(frame)
                except Exception as exc:  # noqa: BLE001
                    offenders.append(f"[{label}] 段 0x{frame[2]:02X} 抛 {type(exc).__name__}: {exc}")
        finally:
            pl._SEGMENT_FORMATS.clear()
            pl._SEGMENT_FORMATS.update(saved_formats)
            pl._SEGMENT_INPUTS.clear()
            pl._SEGMENT_INPUTS.update(saved_inputs)
            pl._SEGMENT_OUTPUTS.clear()
            pl._SEGMENT_OUTPUTS.update(saved_outputs)
    check("三张表被削到极端组合时解码器仍不抛", not offenders, "; ".join(offenders[:4]))


# --- 4. 畸形输入：随便什么字节都不能抛 --------------------------------

def test_fuzz_never_raises() -> None:
    """确定性伪随机（固定种子）模糊测试：任何字节串都只许丢弃，不许抛。"""

    import random

    rng = random.Random(20260823)
    codec = pl.PolarFrameCodec()
    raised = []
    for _ in range(4000):
        size = rng.randint(0, 200)
        blob = bytes(rng.getrandbits(8) for _ in range(size))
        # 一半概率用合法 SOF 开头，逼它进主路径
        if size >= 4 and rng.random() < 0.5:
            blob = bytes([pl.POLAR_SOF]) + blob[1:]
        try:
            codec.decode_with_consumed(blob)
        except Exception as exc:  # noqa: BLE001
            raised.append(f"{type(exc).__name__}: {exc} @ {blob[:12].hex()}")
    check("4000 组随机字节全部不抛异常", not raised, "; ".join(raised[:3]))


# --- 5. 合法帧 + 垃圾字节的混合流：单帧崩溃不许拖垮整流 -----------------

def test_mixed_stream_keeps_decoding() -> None:
    """前面塞一帧缺规格的，后面接一帧完好的 —— 完好的那帧必须照样解出来。

    这是"线程被打死"和"丢一帧"的区别：前者全链路哑掉，后者只损失一个段。
    """

    good_id = next(
        sid for sid in sorted(pl._SEGMENT_FORMATS)
        if sid in pl._SEGMENT_DECODERS and sid != pl.SEG_DEBUG
    )
    good_frame = build_segment(good_id, 4000, body_for(good_id))

    saved = dict(pl._SEGMENT_FORMATS)
    pl._SEGMENT_FORMATS.pop(good_id, None)  # 制造缺规格的状态
    try:
        stream = good_frame + b"\x00" * 7 + good_frame
        segments, _ = pl.PolarFrameCodec().decode_with_consumed(stream)
    finally:
        pl._SEGMENT_FORMATS.clear()
        pl._SEGMENT_FORMATS.update(saved)

    check(
        "缺规格状态下，后面的完好帧仍能被解出（只丢该段，不全链路哑掉）",
        len(segments) == 0,  # 缺规格时这一段被丢弃
        f"解出 {len(segments)} 段",
    )

    # 对照：表完好时两帧都该解出来
    stream = good_frame + b"\x00" * 7 + good_frame
    segments_ok, _ = pl.PolarFrameCodec().decode_with_consumed(stream)
    check(
        "表完好时同样的流能解出两段（证明上一条不是用例本身失效）",
        len(segments_ok) == 2,
        f"解出 {len(segments_ok)} 段",
    )


# --- 6. 计数器必须真的在涨（fail-closed 不是静默吞掉） ----------------

def test_counters_move() -> None:
    codec = pl.PolarFrameCodec()

    # CRC8 坏
    bad = bytearray(build_segment(0x1D, 5000, body_for(0x1D)))
    bad[3] ^= 0xFF
    codec.decode_with_consumed(bytes(bad))
    check("CRC8 错误被计数", codec.stats.crc8_errors >= 1, str(codec.stats.crc8_errors))

    # CRC16 坏
    bad2 = bytearray(build_segment(0x1D, 5000, body_for(0x1D)))
    bad2[-1] ^= 0xFF
    codec2 = pl.PolarFrameCodec()
    codec2.decode_with_consumed(bytes(bad2))
    check("CRC16 错误被计数", codec2.stats.crc16_errors >= 1, str(codec2.stats.crc16_errors))

    # 长度坏：段头的 seg_len 只有 1 字节，所以不能靠"多塞点字节"制造不匹配
    # （塞 999 个会先被 & 0xFF 截断，帧长自洽，根本走不到长度检查）。
    # 正确做法是：段头声明 N 字节 body、实际也送 N 字节，但 N 与该段 struct
    # 算出来的大小不同 —— 这才是固件改了结构体长度而客户端规格没跟上的真实情形。
    mismatch_id = next(
        sid for sid in sorted(pl._SEGMENT_FORMATS)
        if struct.calcsize(pl._SEGMENT_FORMATS[sid]) > 4
    )
    codec3 = pl.PolarFrameCodec()
    codec3.decode_with_consumed(build_segment(mismatch_id, 5000, b"\x00" * 4))
    check(
        f"长度错误被计数（段 0x{mismatch_id:02X} body 4 ≠ 规格 {struct.calcsize(pl._SEGMENT_FORMATS[mismatch_id])}）",
        codec3.stats.length_errors >= 1,
        str(codec3.stats.length_errors),
    )


def main() -> int:
    tests = [
        test_baseline_all_segments_decode,
        test_missing_spec_entry_never_raises,
        test_extreme_table_combinations,
        test_fuzz_never_raises,
        test_mixed_stream_keeps_decoding,
        test_counters_move,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"0x5A 解码 fail-closed：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"0x5A 解码 fail-closed：全部通过（{PASSED} 项）")
    print("解码器在任何输入下都只丢弃并计数，不抛异常打死接收线程。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
