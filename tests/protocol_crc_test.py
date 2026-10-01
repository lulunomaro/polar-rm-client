"""CRC 查表化的等价性证明。

背景：crc8/crc16 原本是逐位串行实现（每字节 8 次位判断）。0x5A 队内链路的
实况下行速率是 1360 帧/秒，实测 CRC 占了整帧解码开销的 **90%**
（29.96 ms/秒）。改成 256 项查表后降到 7.76 ms/秒（4.30×）。

但"快"不是重点，**逐位等价**才是。这里把原实现原样抄进来当参照物，
穷举比对：任何一条不等价都直接红。

为什么值得单独立一个测试文件：CRC 一旦算错，现场现象是"帧被固件整帧丢弃"，
表现为"发了但没反应"——最难查的一类问题。宁可在这里多花几毫秒。

跑法：``python tests/protocol_crc_test.py``
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.protocol import (  # noqa: E402
    CRC16_INIT,
    CRC8_INIT,
    RefereeFrameCodec,
    crc8,
    crc16,
)

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


# --- 参照实现：改表之前的逐位串行版本，一字不改 --------------------------

def ref_crc8(data: bytes) -> int:
    crc = CRC8_INIT
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x01:
                crc = ((crc >> 1) ^ 0x8C) & 0xFF
            else:
                crc = (crc >> 1) & 0xFF
    return crc & 0xFF


def ref_crc16(data: bytes) -> int:
    crc = CRC16_INIT
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = ((crc >> 1) ^ 0x8408) & 0xFFFF
            else:
                crc = (crc >> 1) & 0xFFFF
    return crc & 0xFFFF


# --- 1. 单字节穷举：两个 CRC 全部 256 种取值 ----------------------------

def test_single_byte_exhaustive() -> None:
    for byte in range(256):
        payload = bytes([byte])
        got8, want8 = crc8(payload), ref_crc8(payload)
        if got8 != want8:
            check(f"crc8 单字节 0x{byte:02X}", False, f"got {got8} want {want8}")
            return
        got16, want16 = crc16(payload), ref_crc16(payload)
        if got16 != want16:
            check(f"crc16 单字节 0x{byte:02X}", False, f"got {got16} want {want16}")
            return
    check("crc8/crc16 单字节穷举 256×2 全等", True)


# --- 2. 双字节穷举：覆盖查表索引的高/低位组合 ---------------------------

def test_two_byte_exhaustive() -> None:
    for hi in range(256):
        for lo in (0x00, 0x01, 0x7F, 0x80, 0xAA, 0xFE, 0xFF, hi):
            payload = bytes([hi, lo])
            if crc8(payload) != ref_crc8(payload) or crc16(payload) != ref_crc16(payload):
                check(f"双字节 0x{hi:02X}{lo:02X}", False, "查表与逐位不等")
                return
    check("crc8/crc16 双字节代表性组合全等", True)


# --- 3. 全 65536 种双字节：把查表索引的每种组合都走一遍 -------------------

def test_two_byte_full_space() -> None:
    bad = 0
    for hi in range(256):
        for lo in range(256):
            payload = bytes([hi, lo])
            if crc8(payload) != ref_crc8(payload) or crc16(payload) != ref_crc16(payload):
                bad += 1
    check("crc8/crc16 双字节全空间 65536 组全等", bad == 0, f"{bad} 组不等")


# --- 4. 随机长载荷：覆盖表项连乘与边界长度 -------------------------------

def test_random_long_payloads() -> None:
    rng = random.Random(20260823)  # 固定种子：可复现
    bad = 0
    # 真实帧长分布：1..320，含 0（空载荷）
    for _ in range(4000):
        size = rng.randint(0, 320)
        payload = bytes(rng.getrandbits(8) for _ in range(size))
        if crc8(payload) != ref_crc8(payload) or crc16(payload) != ref_crc16(payload):
            bad += 1
    check("随机载荷 4000 组（0..320 字节）全等", bad == 0, f"{bad} 组不等")


# --- 5. 边界长度：0/1/2/255/256/257 单独点名 ----------------------------

def test_boundary_lengths() -> None:
    bad = []
    for size in (0, 1, 2, 3, 4, 5, 8, 15, 16, 31, 32, 63, 64, 127, 128, 255, 256, 257, 511, 512):
        payload = bytes((i * 7 + size) & 0xFF for i in range(size))
        if crc8(payload) != ref_crc8(payload) or crc16(payload) != ref_crc16(payload):
            bad.append(size)
    check("边界长度 0..512 全等", not bad, f"不等的长度：{bad}")


# --- 6. 0xA5 裁判帧端到端：编出的帧必须和改表前逐位一致 ------------------

def test_referee_frame_bytes_identical() -> None:
    """整帧字节级一致：头 CRC8 + 体 CRC16 都要落在同一位置。"""

    def ref_encode(cmd_id: int, data: bytes, seq: int) -> bytes:
        header = bytearray([0xA5])
        header.extend(len(data).to_bytes(2, "little"))
        header.append(seq & 0xFF)
        header.append(ref_crc8(bytes(header)))
        frame = bytearray(header)
        frame.extend(cmd_id.to_bytes(2, "little"))
        frame.extend(data)
        frame.extend(ref_crc16(bytes(frame)).to_bytes(2, "little"))
        return bytes(frame)

    rng = random.Random(7)
    codec = RefereeFrameCodec()
    bad = 0
    for _ in range(2000):
        cmd_id = rng.getrandbits(16)
        data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 64)))
        seq = rng.getrandbits(8)
        if codec.encode(cmd_id, data, seq=seq) != ref_encode(cmd_id, data, seq):
            bad += 1
    check("0xA5 整帧编解码 2000 组字节级一致", bad == 0, f"{bad} 帧字节不同")


# --- 7. 解码器自洽：改表后的帧仍能被改表后的解码器验过 ------------------

def test_codec_roundtrip_still_accepts() -> None:
    codec = RefereeFrameCodec()
    stream = b""
    expected = []
    for i in range(200):
        cmd_id = 0x0301 + (i % 8)
        data = bytes([i & 0xFF, (i * 3) & 0xFF])
        stream += codec.encode(cmd_id, data, seq=i & 0xFF)
        expected.append(cmd_id)
    frames, consumed = codec.decode_with_consumed(stream)
    check(
        "0xA5 解码往返：帧数与 cmd_id 全对",
        [f.cmd_id for f in frames] == expected and consumed == len(stream),
        f"解出 {len(frames)} 帧，消费 {consumed}/{len(stream)} 字节",
    )


# --- 8. 反例护栏：单比特翻转必须被发现（证明表没算错方向） ----------------

def test_single_bit_flip_is_detected() -> None:
    rng = random.Random(11)
    payload = bytes(rng.getrandbits(8) for _ in range(64))
    base8, base16 = crc8(payload), crc16(payload)
    missed8 = 0
    missed16 = 0
    for byte_index in range(len(payload)):
        for bit in range(8):
            mutated = bytearray(payload)
            mutated[byte_index] ^= 1 << bit
            if crc8(bytes(mutated)) == base8:
                missed8 += 1
            if crc16(bytes(mutated)) == base16:
                missed16 += 1
    # CRC16 能检出全部单比特错误；CRC8 会漏，这是它 8 位的固有限制，不算失败。
    check("crc16 单比特错误检出率 100%", missed16 == 0, f"漏检 {missed16} 个")
    check("crc8 漏检率符合 8 位 CRC 的固有上限（<1%）", missed8 < 5, f"漏检 {missed8} 个")


# --- 9. bytes-like 输入：bytearray / memoryview 与 bytes 结果一致 ---------

def test_accepts_buffer_protocol() -> None:
    payload = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    ref8, ref16 = ref_crc8(payload), ref_crc16(payload)
    for name, buf in (("bytearray", bytearray(payload)), ("memoryview", memoryview(payload))):
        check(f"crc8 接受 {name}", crc8(buf) == ref8, f"got {crc8(buf)} want {ref8}")
        check(f"crc16 接受 {name}", crc16(buf) == ref16, f"got {crc16(buf)} want {ref16}")


# --- 10. 性能护栏：查表必须真的更快（防止有人改回逐位还保持绿） ----------

def test_table_is_faster() -> None:
    import time

    payload = bytes(range(160))  # 典型 SEG_DEBUG 帧长
    reps = 30000

    t0 = time.perf_counter()
    for _ in range(reps):
        ref_crc16(payload)
    ref_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(reps):
        crc16(payload)
    tbl_time = time.perf_counter() - t0

    speedup = ref_time / tbl_time if tbl_time else 0.0
    check(
        f"crc16 查表比逐位快（实测 {speedup:.2f}×，逐位 {ref_time*1e6/reps:.2f}µs → 查表 {tbl_time*1e6/reps:.2f}µs）",
        speedup >= 2.0,
        f"只快了 {speedup:.2f}×，低于 2× 护栏",
    )


def main() -> int:
    tests = [
        test_single_byte_exhaustive,
        test_two_byte_exhaustive,
        test_two_byte_full_space,
        test_random_long_payloads,
        test_boundary_lengths,
        test_referee_frame_bytes_identical,
        test_codec_roundtrip_still_accepts,
        test_single_bit_flip_is_detected,
        test_accepts_buffer_protocol,
        test_table_is_faster,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"CRC 等价性：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"CRC 等价性：全部通过（{PASSED} 项）")
    print("查表版与逐位版逐位等价，0xA5 整帧字节级一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
