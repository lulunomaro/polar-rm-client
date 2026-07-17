"""生成 POLAR 中性 UI 开关音效（16-bit mono WAV，无第三方依赖）。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "ui_sounds" / "polar"
SAMPLE_RATE = 44100


def _envelope(i: int, n: int, attack: float = 0.008, release: float = 0.035) -> float:
    t = i / SAMPLE_RATE
    dur = n / SAMPLE_RATE
    a = min(1.0, t / max(1e-6, attack))
    r = 1.0
    if t > dur - release:
        r = max(0.0, (dur - t) / max(1e-6, release))
    return a * r


def _tone(freq: float, duration: float, volume: float = 0.28, *, slide: float = 0.0) -> list[int]:
    n = max(1, int(SAMPLE_RATE * duration))
    samples: list[int] = []
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        f = freq + slide * t
        phase += 2.0 * math.pi * f / SAMPLE_RATE
        # 轻微二次谐波，比纯正弦更像「滴」而不是测试音。
        wave_v = math.sin(phase) * 0.86 + math.sin(phase * 2.0) * 0.14
        v = wave_v * volume * _envelope(i, n)
        samples.append(int(max(-1.0, min(1.0, v)) * 32767))
    return samples


def _silence(duration: float) -> list[int]:
    return [0] * max(1, int(SAMPLE_RATE * duration))


def _write_wav(path: Path, samples: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def build_enable() -> list[int]:
    # 短上扬：开启
    return _tone(920.0, 0.078, volume=0.30, slide=280.0)


def build_disable() -> list[int]:
    # 略低下沉：关闭
    return _tone(640.0, 0.070, volume=0.26, slide=-220.0)


def build_invalid() -> list[int]:
    # 双短音：无效
    a = _tone(420.0, 0.045, volume=0.20, slide=0.0)
    b = _silence(0.028)
    c = _tone(360.0, 0.055, volume=0.18, slide=-80.0)
    return a + b + c


def main() -> None:
    mapping = {
        "enable.wav": build_enable(),
        "disable.wav": build_disable(),
        "invalid.wav": build_invalid(),
    }
    for name, samples in mapping.items():
        path = OUT / name
        _write_wav(path, samples)
        print(f"wrote {path} ({len(samples) / SAMPLE_RATE * 1000:.0f} ms)")


if __name__ == "__main__":
    main()
