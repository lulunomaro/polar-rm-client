from __future__ import annotations

from dataclasses import dataclass
import struct


SOF = 0xA5
CRC8_INIT = 0xFF
CRC16_INIT = 0xFFFF


@dataclass
class RefereeFrame:
    cmd_id: int
    data: bytes
    seq: int


class RefereeFrameCodec:
    """大疆 RoboMaster 裁判系统串口帧编解码器。

    线缆传输格式：
    SOF(0xA5) + data_length(uint16 LE) + seq(uint8) + CRC8 +
    cmd_id(uint16 LE) + data + CRC16(uint16 LE).
    """

    def __init__(self) -> None:
        self.seq = 0

    def encode(self, cmd_id: int, data: bytes = b"", seq: int | None = None) -> bytes:
        seq_value = self._next_seq() if seq is None else seq & 0xFF
        header = bytearray([SOF])
        header.extend(len(data).to_bytes(2, "little"))
        header.append(seq_value)
        header.append(crc8(bytes(header)))
        frame = bytearray(header)
        frame.extend(cmd_id.to_bytes(2, "little"))
        frame.extend(data)
        frame.extend(crc16(bytes(frame)).to_bytes(2, "little"))
        return bytes(frame)

    def decode(self, stream: bytes) -> list[RefereeFrame]:
        """解码全部完整帧（保持旧接口，返回帧列表）。"""
        frames, _consumed = self.decode_with_consumed(stream)
        return frames

    def decode_with_consumed(self, stream: bytes) -> tuple[list[RefereeFrame], int]:
        """解码完整帧，并返回已消费字节偏移（供流式读取保留半帧）。"""
        frames: list[RefereeFrame] = []
        index = 0
        while index + 9 <= len(stream):
            if stream[index] != SOF:
                index += 1
                continue
            header = stream[index : index + 5]
            if crc8(header[:4]) != header[4]:
                index += 1
                continue
            data_len = int.from_bytes(header[1:3], "little")
            total_len = 5 + 2 + data_len + 2
            if index + total_len > len(stream):
                break
            frame_bytes = stream[index : index + total_len]
            expected = int.from_bytes(frame_bytes[-2:], "little")
            if crc16(frame_bytes[:-2]) != expected:
                index += 1
                continue
            cmd_id = int.from_bytes(frame_bytes[5:7], "little")
            data = frame_bytes[7:-2]
            frames.append(RefereeFrame(cmd_id=cmd_id, data=data, seq=header[3]))
            index += total_len
        return frames, index

    def _next_seq(self) -> int:
        value = self.seq & 0xFF
        self.seq = (self.seq + 1) & 0xFF
        return value


def _build_crc8_table() -> tuple[int, ...]:
    """CRC-8/MAXIM：poly 0x8C（反射式），init 0xFF。

    表项 = 把字节 i 送进 8 轮逐位反射算法(init=0) 的结果。
    这里刻意用与 :func:`ref_crc8` 同形的 8 轮写法，而不是
    `table[i] = table[i-1] >> 1 ^ poly` 的递推捷径 —— 两者在反射式下
    并不等价，递推捷径会算出一张全错的表（实测 crc8(b"\\x00") 直接返回 0）。
    一次性 256×8 轮的开销在 import 时付清，之后每字节只查一次表。
    """
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8C) & 0xFF if crc & 0x01 else (crc >> 1) & 0xFF
        table.append(crc)
    return tuple(table)


def _build_crc16_table() -> tuple[int, ...]:
    """CRC-16/X-25：poly 0x8408（反射式），init 0xFFFF。同 _build_crc8_table 的理由。"""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8408) & 0xFFFF if crc & 0x0001 else (crc >> 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


_CRC8_TABLE = _build_crc8_table()
_CRC16_TABLE = _build_crc16_table()


def crc8(data: bytes) -> int:
    """CRC-8/MAXIM 校验和。

    查表实现：逐字节一次查表 + 一次移位，替代原先每字节 8 次位判断。
    与逐位实现逐位等价（tests/protocol_crc_test.py 有穷举回归断言）。
    接受任意可索引的 bytes-like（bytes / bytearray / memoryview）。
    """
    crc = CRC8_INIT
    table = _CRC8_TABLE
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc & 0xFF


def crc16(data: bytes) -> int:
    """CRC-16/X-25 校验和。

    查表实现，与逐位实现逐位等价。接受任意可索引的 bytes-like。
    """
    crc = CRC16_INIT
    table = _CRC16_TABLE
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFF


def encode_custom_control_30(
    *,
    seq: int,
    vx: float,
    vy: float,
    wz: float,
    speed_limit: int,
    power_limit: int,
    spin_mode: bool,
) -> bytes:
    """官方 CustomControl / cmd_id 0x0311 使用的紧凑载荷。

    官方容器最多允许 30 字节。真实机器人固件协议定稿前，
    这里先保留一份已记录的队内 13 字节本地格式。
    """

    flags = 0x01 if spin_mode else 0x00
    payload = struct.pack(
        "<BhhhHHB",
        seq & 0xFF,
        clamp_i16(round(vx * 1000)),
        clamp_i16(round(vy * 1000)),
        clamp_i16(round(wz * 1000)),
        max(0, min(0xFFFF, int(speed_limit))),
        max(0, min(0xFFFF, int(power_limit))),
        flags,
    )
    return payload.ljust(30, b"\x00")


def clamp_i16(value: int) -> int:
    return max(-32768, min(32767, value))
