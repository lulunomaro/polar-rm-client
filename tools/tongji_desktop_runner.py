"""Lab 同济外挂入口 — 默认 Gestalt 共享内存 VisionBridge（非 OBS）。

默认：
  sp_vision.apps.gestalt_shm_runner
  读游戏 SHM（-visionbridge）→ 完整 sp_vision → UDP :9871

兼容回退：
  POLAR_TONGJI_PIPELINE=obs|legacy  → 旧 OBS 摄像头管线
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SP_VISION_PY = WORKSPACE / "sp_vision_py"


def _ensure_sp_vision_path() -> Path:
    if not SP_VISION_PY.is_dir():
        raise FileNotFoundError(f"找不到 sp_vision_py: {SP_VISION_PY}")
    path = str(SP_VISION_PY.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
    return SP_VISION_PY


def _run_shm(argv: list[str]) -> int:
    _ensure_sp_vision_path()
    from sp_vision.apps.gestalt_shm_runner import main as shm_main

    return int(shm_main(argv))


def _run_obs_full(argv: list[str]) -> int:
    _ensure_sp_vision_path()
    from sp_vision.apps.polar_lab_runner import main as polar_main

    return int(polar_main(argv))


def _run_legacy(argv: list[str]) -> int:
    """旧简化管线（OBS + TongjiLabPipeline）。"""
    sys.path.insert(0, str(ROOT))
    from rm_client.gestalt_lightbar import detect_lightbar_armors, merge_detections
    from rm_client.tongji_bridge import (
        AA_STATE_PORT,
        DEFAULT_PORT,
        ArmorAutoAimStateReceiver,
        PolarPublisher,
        build_tongji_packet,
    )
    from rm_client.tongji_pipeline import TongjiLabPipeline
    from rm_client.tongji_yolo import TongjiYoloV5, resolve_tongji_yolo_xml
    import argparse
    from pathlib import Path as P

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--aa-port", type=int, default=AA_STATE_PORT)
    parser.add_argument("--device", default="AUTO")
    parser.add_argument("--model", type=P, default=None)
    parser.add_argument("--score", type=float, default=0.08)
    parser.add_argument("--conf", type=float, default=0.08)
    parser.add_argument("--bullet-speed", type=float, default=23.0)
    parser.add_argument("--enemy-color", default="any")
    parser.add_argument("--no-lightbar", action="store_true")
    args, _ = parser.parse_known_args(argv)
    import cv2
    import time

    model_path = args.model if args.model is not None else resolve_tongji_yolo_xml(WORKSPACE)
    if not P(model_path).is_file():
        print(f"找不到模型: {model_path}")
        return 3
    source: int | str = int(args.source) if str(args.source).isdigit() else args.source
    if isinstance(source, int):
        capture = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(source)
    else:
        capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        print(f"无法打开图传源: {args.source}")
        return 2
    detector = TongjiYoloV5(P(model_path), args.device)
    pipeline = TongjiLabPipeline(bullet_speed=float(args.bullet_speed), enemy_color=str(args.enemy_color))
    publisher = PolarPublisher(port=args.port)
    aa_rx = ArmorAutoAimStateReceiver(port=args.aa_port)
    aa_rx.start()
    print(f"[tongji-legacy-obs] -> :{args.port}")
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            dets = detector.detect(frame, score_threshold=float(args.score), min_confidence=float(args.conf))
            yolo_armors = [d.payload(i, locked=False) for i, d in enumerate(dets)]
            if args.no_lightbar:
                armors = yolo_armors
            else:
                lb = detect_lightbar_armors(frame)
                armors = merge_detections(yolo_armors, lb, frame, yolo_min_conf=float(args.conf))
            aa_on = bool(aa_rx.armor_auto_aim) and aa_rx.fresh(1.5)
            primary, command, armors_out = pipeline.process(
                armors, frame.shape[1], frame.shape[0], aa_on=aa_on
            )
            publisher.publish(
                build_tongji_packet(frame.shape[1], frame.shape[0], armors_out, primary=primary, command=command)
            )
            print(f"\r[legacy-obs] det={len(armors)} AA={'on' if aa_on else 'off'}   ", end="", flush=True)
    finally:
        aa_rx.stop()
        capture.release()
        publisher.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    mode = os.environ.get("POLAR_TONGJI_PIPELINE", "shm").strip().lower()

    if mode in ("legacy", "v1", "simple"):
        print("[tongji] pipeline=legacy-obs")
        return _run_legacy(argv)
    if mode in ("obs", "full", "webcam", "polar"):
        # OBS / webcam full sp_vision (old Lab)
        print("[tongji] pipeline=obs-full (polar_lab_runner)")
        mapped: list[str] = []
        i = 0
        while i < len(argv):
            tok = argv[i]
            if tok == "--source" and i + 1 < len(argv):
                mapped.extend(["--source", argv[i + 1]])
                i += 2
                continue
            mapped.append(tok)
            i += 1
        if not any(a in ("--config", "-c") or a.startswith("--config=") for a in mapped):
            cfg = SP_VISION_PY / "configs" / "gestalt_lab.yaml"
            if cfg.is_file():
                mapped = ["--config", str(cfg), *mapped]
        if "--show" not in mapped and "--headless" not in mapped:
            mapped.append("--headless")
        try:
            return _run_obs_full(mapped)
        except Exception as exc:
            print(f"obs-full failed: {exc}; fallback legacy")
            return _run_legacy(argv)

    # default: SHM
    print(f"[tongji] pipeline=gestalt_shm root={SP_VISION_PY}")
    if not any(a == "--config" or a.startswith("--config=") for a in argv):
        cfg = SP_VISION_PY / "configs" / "gestalt_shm.yaml"
        if cfg.is_file():
            argv = ["--config", str(cfg), *argv]
    # map --port to polar if present
    try:
        return _run_shm(argv)
    except FileNotFoundError as exc:
        print(exc)
        print("fallback obs-full…")
        return _run_obs_full(argv)
    except ModuleNotFoundError as exc:
        print(f"sp_vision missing: {exc}")
        return _run_legacy(argv)
    except Exception as exc:
        print(f"gestalt_shm failed: {exc}")
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
