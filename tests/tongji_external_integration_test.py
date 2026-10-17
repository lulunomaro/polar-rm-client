"""Opt-in real OpenVINO inference using explicit, locally supplied fixtures.

Set POLAR_TONGJI_MODEL to yolov5.xml (with its adjacent .bin) and
POLAR_TONGJI_DEMO to demo.avi. This does not qualify robot/field operation.
Use verify_all.py --category external to run it; release does not claim it.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    fixtures = {name: os.environ.get(name, "") for name in ("POLAR_TONGJI_MODEL", "POLAR_TONGJI_DEMO")}
    missing = [name for name, value in fixtures.items() if not value or not Path(value).is_file()]
    if missing:
        print("[SKIP] Supply explicit local inference fixtures: " + ", ".join(missing))
        return 77
    model, demo = (Path(fixtures[name]) for name in ("POLAR_TONGJI_MODEL", "POLAR_TONGJI_DEMO"))
    if not model.with_suffix(".bin").is_file():
        print("[SKIP] Model weights are absent: " + str(model.with_suffix(".bin")))
        return 77
    import cv2
    from rm_client.tongji_yolo import TongjiYoloV5

    capture = cv2.VideoCapture(str(demo))
    try:
        ok, frame = capture.read()
    finally:
        capture.release()
    assert ok and frame is not None, "demo did not decode a frame"
    detector = TongjiYoloV5(model, "CPU")
    result = detector.detect(frame)
    assert isinstance(result, list)
    print("[PASS] Explicit Tongji model loaded and ran one decoded demo frame; no hardware qualification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
