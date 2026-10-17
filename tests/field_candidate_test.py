from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_client.backend.field_log import ReadOnlyFieldReplay, RotatingJsonlLogger
from rm_client.backend.qualification import CORE_TESTS, QualificationEvidenceStore, QualificationManager
from rm_client.preflight import run_preflight
from rm_client.backend.safety import OfficialSafety
from rm_client.backend.video_backend import AnnexBRecoveryGate, ContinuousMjpegDecoder, resolve_ffmpeg_executable
from rm_client.backend.codec_selftest import run_all_codec_selftests
from rm_client.runtime_paths import RuntimePaths
from rm_client.replay import summarize_field_logs, write_replay_summary
from rm_client.scenarios import SCENARIOS
from rm_client.tongji_yolo import LocalAlignedVision
from rm_client.vision import CameraCaptureWorker
from main import _prepare_runtime_paths


def require(value: bool, name: str) -> None:
    if not value:
        raise AssertionError(name)
    print("  ok", name)


def encoded_stream(codec: str, duration: float = 3.0) -> bytes:
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("ffmpeg required")
    encoder, output = ("libx265", "hevc") if codec == "h265" else ("libx264", "h264")
    result = subprocess.run(
        [
            ffmpeg, "-v", "error", "-f", "lavfi", "-i",
            f"testsrc2=size=160x120:rate=30:duration={duration}",
            "-c:v", encoder, "-preset", "ultrafast", "-g", "30",
            "-pix_fmt", "yuv420p", "-f", output, "pipe:1",
        ],
        capture_output=True,
        timeout=90,
    )
    require(result.returncode == 0 and len(result.stdout) > 1000, f"generate {codec} GOP")
    return result.stdout


def test_continuous_codecs() -> None:
    print("[field] persistent codecs with stdin open")
    for codec in ("h264", "h265"):
        stream = encoded_stream(codec)
        decoder = ContinuousMjpegDecoder(codec)
        require(decoder.start(), f"start {codec}")
        started = time.perf_counter()
        for offset in range(0, len(stream), 1200):
            while not decoder.write(stream[offset:offset + 1200]):
                time.sleep(0.002)
            time.sleep(0.002)
        deadline = started + 3.0
        while time.perf_counter() < deadline and decoder.frames_decoded == 0:
            time.sleep(0.02)
        require(decoder.frames_decoded > 0, f"{codec} first frame while stdin open")
        require(time.perf_counter() - started < 3.0, f"{codec} first frame under 3 seconds")
        require(decoder.available, f"{codec} stdin remains open")
        decoder.stop()
    results = run_all_codec_selftests()
    require(all(result.passed for result in results), "startup codec self-test")


def test_recovery_gate() -> None:
    print("[field] Annex-B recovery")
    h264 = AnnexBRecoveryGate("h264")
    require(h264.feed(b"\x00\x00\x01\x65broken") is None, "H264 IDR without SPS/PPS blocked")
    recovered = h264.feed(
        b"\x00\x00\x01\x67sps\x00\x00\x01\x68pps\x00\x00\x01\x65idr"
    )
    require(bool(recovered and recovered.endswith(b"idr")), "H264 complete recovery accepted")
    h265 = AnnexBRecoveryGate("h265")
    recovered = h265.feed(
        b"\x00\x00\x01\x40\x01vps\x00\x00\x01\x42\x01sps"
        b"\x00\x00\x01\x44\x01pps\x00\x00\x01\x26\x01idr"
    )
    require(bool(recovered and recovered.endswith(b"idr")), "HEVC VPS/SPS/PPS/IDR recovery")
    h265.reset()
    require(h265.feed(b"\x00\x00\x01\x26\x01idr") is None, "reset forgets stale parameter sets")

    # Cross-chunk start-code and parameter-set accumulation.
    split = AnnexBRecoveryGate("h264")
    require(split.feed(b"noise\x00\x00") is None, "split start-code prefix retained")
    require(split.feed(b"\x01\x67sps\x00\x00\x01\x68pps") is None, "split parameters wait for IDR")
    require(bool(split.feed(b"\x00\x00\x01\x65idr")), "split recovery completes on next IDR")

    # Parameter payloads may span arbitrary transport blocks; caching only
    # three start-code bytes would otherwise truncate real SPS/PPS/VPS NALs.
    fragmented = AnnexBRecoveryGate("h264")
    require(fragmented.feed(b"\x00\x00\x01\x67abc") is None, "fragmented SPS begins")
    require(fragmented.feed(b"defgh") is None, "fragmented SPS payload retained")
    require(fragmented.feed(b"\x00\x00\x01\x68pps") is None, "fragmented SPS committed at PPS")
    recovered = fragmented.feed(b"\x00\x00\x01\x65idr")
    require(bool(recovered and b"abcdefgh" in recovered), "full fragmented SPS survives recovery")


def test_decoder_recovery_after_loss() -> None:
    print("[field] decoder crash/loss recovery under five seconds")
    fixtures = RuntimePaths.discover().asset("selftest")
    for codec, filename in (("h264", "h264_one_gop.h264"), ("h265", "hevc_one_gop.hevc")):
        stream = (fixtures / filename).read_bytes()
        decoder = ContinuousMjpegDecoder(codec)
        require(decoder.start(), f"start {codec} recovery decoder")
        require(decoder.write(stream), f"queue initial {codec} GOP")
        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline and decoder.frames_decoded == 0:
            time.sleep(0.01)
        require(decoder.frames_decoded > 0, f"initial {codec} GOP decoded")

        # A process failure/loss epoch clears the image and every old parameter
        # set.  P/B or IDR-only data must stay blocked until a complete fresh
        # parameter set plus keyframe arrives.
        decoder.stop(clear_latest=True)
        gate = AnnexBRecoveryGate(codec)
        invalid_key = (
            b"\x00\x00\x01\x26\x01broken"
            if codec == "h265"
            else b"\x00\x00\x01\x65broken"
        )
        require(gate.feed(invalid_key) is None, f"{codec} damaged epoch blocked")
        require(decoder.restart(clear_latest=True), f"restart {codec} process")
        recovered_at = time.perf_counter()
        for offset in range(0, len(stream), 257):
            payload = gate.feed(stream[offset:offset + 257])
            if payload is not None:
                while not decoder.write(payload):
                    time.sleep(0.002)
        deadline = recovered_at + 5.0
        while time.perf_counter() < deadline and decoder.first_frame_at <= 0.0:
            time.sleep(0.01)
        require(decoder.first_frame_at > 0.0, f"{codec} recovery outputs a frame")
        require(time.perf_counter() - recovered_at < 5.0, f"{codec} recovery under five seconds")
        decoder.stop()


def test_runtime_paths() -> None:
    print("[field] frozen paths")
    with tempfile.TemporaryDirectory() as directory:
        # RuntimePaths uses the host filesystem. A Windows drive literal is a
        # relative filename on POSIX, not a portable way to test frozen layout.
        # Native absolute paths exercise the same contract on Windows and POSIX.
        root = Path(directory).resolve()
        portable_root = root / "Portable Client"
        paths = RuntimePaths.discover(
            executable=str(portable_root / "POLAR-RM.exe"), frozen=True, environ={}
        )
        require(paths.app_root == portable_root, "portable executable determines app root")
        require(not paths.source_mode, "portable mode detected")
        require(paths.resource_root == portable_root / "resources", "portable resources")
        require(paths.defaults_root == portable_root / "defaults", "portable immutable defaults")
        require(paths.configs_root == portable_root / "user_data" / "configs", "portable configs")
        require(paths.evidence_root == portable_root / "field_evidence", "portable evidence")
        require(paths.bin_root == portable_root / "bin", "portable binaries")
        require(paths.manifest_path == portable_root / "BUILD_MANIFEST.json", "portable manifest")

        source_root = root / "Source Client"
        module_file = source_root / "rm_client" / "runtime_paths.py"
        source = RuntimePaths.discover(module_file=module_file, frozen=False, environ={})
        require(source.source_mode, "source mode detected")
        require(source.app_root == source_root, "source module determines app root")
        require(source.resource_root == source_root, "source resources")
        require(source.defaults_root == source_root / "configs", "source immutable defaults")
        require(
            source.configs_root == source_root / "user_data" / "configs",
            "source config isolated from defaults",
        )
        require(
            source.evidence_root == source_root / "user_data" / "field_evidence",
            "source evidence isolated from defaults",
        )

        data_root = root / "Isolated Runtime"
        isolated = RuntimePaths.discover(
            module_file=module_file,
            frozen=False,
            environ={"POLAR_USER_DATA_ROOT": str(data_root)},
        )
        require(isolated.configs_root == data_root / "configs", "source runtime override")
        require(isolated.defaults_root == source.defaults_root, "runtime override preserves defaults")
        overridden = RuntimePaths.discover(
            module_file=module_file,
            frozen=False,
            environ={"POLAR_APP_ROOT": str(portable_root), "POLAR_USER_DATA_ROOT": str(data_root)},
        )
        require(overridden == paths, "app override selects complete portable layout")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "b").mkdir()
        (root / "a.txt").write_bytes(b"one")
        (root / "b" / "c.txt").write_bytes(b"two")
        first = RuntimePaths.hash_tree(root)
        second = RuntimePaths.hash_tree(root)
        require(bool(first) and first == second, "resource tree hash deterministic")
        (root / "b" / "c.txt").write_bytes(b"changed")
        require(RuntimePaths.hash_tree(root) != first, "resource tree hash detects mutation")


def test_preflight_does_not_seed_runtime_config() -> None:
    print("[field] preflight runtime-state isolation")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        defaults = root / "defaults"
        defaults.mkdir(parents=True)
        (defaults / "default.json").write_text('{"source":"immutable"}\n', encoding="utf-8")
        paths = RuntimePaths(
            app_root=root,
            resource_root=root / "resources",
            defaults_root=defaults,
            user_data_root=root / "user_data",
            configs_root=root / "user_data" / "configs",
            calibrations_root=root / "user_data" / "calibrations",
            evidence_root=root / "field_evidence",
            logs_root=root / "user_data" / "logs",
            bin_root=root / "bin",
            manifest_path=root / "BUILD_MANIFEST.json",
            source_mode=False,
        )
        _prepare_runtime_paths(["--preflight", "--official"], paths=paths)
        require(paths.configs_root.is_dir(), "preflight creates writable config directory")
        require(not (paths.configs_root / "default.json").exists(), "preflight leaves user config empty")
        _prepare_runtime_paths([], paths=paths)
        require(
            (paths.configs_root / "default.json").read_text(encoding="utf-8")
            == '{"source":"immutable"}\n',
            "normal launch seeds immutable default",
        )


def test_capture_workers_are_bounded() -> None:
    print("[field] capture worker lifecycle")

    class ClosedCapture:
        def isOpened(self) -> bool:
            return False

        def release(self) -> None:
            pass

    class FakeCv2:
        CAP_DSHOW = 700

        def __init__(self) -> None:
            self.opens = 0

        def VideoCapture(self, *_args):
            self.opens += 1
            return ClosedCapture()

    previous_cv2 = sys.modules.get("cv2")
    fake_cv2 = FakeCv2()
    sys.modules["cv2"] = fake_cv2
    try:
        camera = CameraCaptureWorker()
        require(camera.start(), "camera first probe starts")
        deadline = time.perf_counter() + 1.0
        while camera.running and time.perf_counter() < deadline:
            time.sleep(0.005)
        require(not camera.running, "camera failed probe exits")
        require(fake_cv2.opens == 1, "camera does not fall through to MSMF")
        require(camera.retry_after > time.perf_counter(), "camera failure enters backoff")
        require(not camera.start(), "camera backoff blocks hot retry")
        camera.stop()
    finally:
        if previous_cv2 is None:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = previous_cv2

    # The local inference worker used to poll every 3 ms even with no frame.
    # Verify its event wait wakes immediately for shutdown without OpenVINO.
    module = sys.modules["rm_client.tongji_yolo"]
    original_detector = module.TongjiYoloV5

    class EmptyDetector:
        active_device = "TEST"

        def detect(self, _frame):
            return []

    module.TongjiYoloV5 = lambda *_args, **_kwargs: EmptyDetector()
    try:
        local = LocalAlignedVision(Path("missing.xml"))
        stop_event = threading.Event()
        pending_event = threading.Event()
        local.running = True
        thread = threading.Thread(target=local._run, args=(stop_event, pending_event), daemon=True)
        thread.start()
        time.sleep(0.04)
        stop_event.set()
        pending_event.set()
        thread.join(timeout=0.5)
        require(not thread.is_alive(), "local vision idle wait is interruptible")
    finally:
        module.TongjiYoloV5 = original_detector


def test_evidence_and_hard_gate() -> None:
    print("[field] evidence integrity and hard gate")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = QualificationEvidenceStore(root, build_commit="a" * 40)
        manager = QualificationManager(store)
        session = manager.new_session(
            robot_id="3",
            firmware_hash="B" * 64,
            match_mode="RMUC7v7",
            video_source="native_0310",
            operator="operator-a",
        )
        for key in CORE_TESTS:
            if key != "field_signoff":
                manager.record_automatic(key, True, source="test-instrument")
        manager.sign_off("safety-b")
        require(manager.core_ready()[0], "signed core evidence ready")
        require(
            manager.identity_matches(
                robot_id="3",
                firmware_hash="B" * 64,
                match_mode="RMUC7v7",
                video_source="native_0310",
            )[0],
            "runtime identity matches evidence",
        )
        require(
            not manager.identity_matches(
                robot_id="4",
                firmware_hash="B" * 64,
                match_mode="RMUC7v7",
                video_source="native_0310",
            )[0],
            "different robot cannot reuse evidence",
        )
        require("prm1_vision" not in manager.capabilities(), "PRM1 not inherited from core")
        try:
            manager.record_automatic("prm1_vision_schema_review", True, source="test-instrument")
        except ValueError:
            pass
        else:
            raise AssertionError("generic runner minted PRM1 evidence")
        require(True, "generic runner cannot mint PRM1 evidence")
        manager.record_supervised_prm1(
            "prm1_vision_schema_review",
            True,
            reviewer="schema-reviewer",
            measured={"schema": "PRM1 kind=1"},
        )
        require("prm1_vision" not in manager.capabilities(), "schema review alone cannot unlock PRM1")
        manager.record_supervised_prm1(
            "prm1_vision_bench_test",
            True,
            reviewer="bench-safety",
            measured={"watchdog_observed": True},
        )
        require(
            not manager.core_ready()[0],
            "technical rerun invalidates prior human sign-off",
        )
        manager.sign_off("safety-b")
        require("prm1_vision" in manager.capabilities(), "review plus firmware-bound bench and re-sign unlock PRM1")
        require(
            not manager.test_passed("control_heartbeat_test", now=time.time() + 25 * 60 * 60),
            "dynamic evidence expires after 24 hours",
        )
        require(
            not manager.test_passed("control_heartbeat_test", now=time.time()),
            "expired evidence cannot resurrect after clock rollback",
        )
        manager.session.tests["control_heartbeat_test"].status = "PASS"
        evidence_path = root / "sessions" / f"{session.session_id}.json"
        value = json.loads(evidence_path.read_text(encoding="utf-8"))
        value["session"]["robot_id"] = "tampered"
        evidence_path.write_text(json.dumps(value), encoding="utf-8")
        require(store.load(session.session_id) is None and "HMAC" in store.last_error, "tamper detected")
        require(not manager.verified_core_ready()[0], "tampered evidence cannot arm")

        safety = OfficialSafety("OfficialEvent")
        safety.from_scenario(SCENARIOS["OfficialEvent"])
        safety.update_stage("4")
        require(not safety.can_publish("KeyboardMouseControl", intent="core_kmc")[0], "startup locked")
        safety.arm_match({"core_kmc"})
        require(safety.can_publish("KeyboardMouseControl", intent="core_kmc")[0], "core authorization works")
        require(not safety.can_publish("CustomControl", intent="prm1_spin")[0], "PRM1 independent lock")


def test_qualification_intent_scope() -> None:
    print("[field] qualification intent scope")
    safety = OfficialSafety("OfficialEvent")
    safety.from_scenario(SCENARIOS["OfficialEvent"])
    safety.update_stage("4")
    economy = safety.begin_qualification({"economy"}, ttl_s=30.0, now=10.0)
    require(
        safety.can_publish(
            "CommonCommand",
            now=10.0,
            intent="economy",
            qualification_token=economy.value,
        )[0],
        "economy token permits economy only",
    )
    require(
        not safety.can_publish(
            "KeyboardMouseControl",
            now=10.01,
            intent="core_kmc",
            qualification_token=economy.value,
        )[0],
        "economy token cannot move robot",
    )
    # A failed widening attempt consumes the whole epoch and returns LOCKED.
    require(safety.control_state.value == "LOCKED", "invalid token use destroys qualification epoch")


def test_rotating_log_and_replay() -> None:
    print("[field] bounded log replay")
    with tempfile.TemporaryDirectory() as directory:
        logger = RotatingJsonlLogger(Path(directory), max_bytes=1024, backups=2)
        for index in range(80):
            logger.emit("publish_result", index=index, detail="x" * 100)
        require(logger.flush(timeout=2.0), "all accepted field records written without loss")
        require(logger.close(timeout=2.0), "field writer closed before replay")
        files = sorted(Path(directory).glob("*.jsonl*"))
        require(1 <= len(files) <= 3, "bounded rotation")
        records = list(ReadOnlyFieldReplay(files).records())
        require(bool(records), "read-only replay parses records")
        summary = summarize_field_logs([Path(directory)])
        require(summary["uplink"] == "DISABLED" and summary["records"] == len(records), "portable replay summary")
        output = write_replay_summary(Path(directory) / "summary.json", summary)
        require(json.loads(output.read_text(encoding="utf-8"))["replay_mode"], "atomic replay export")



def test_source_preflight() -> None:
    print("[field] source preflight")
    ffmpeg = resolve_ffmpeg_executable()
    require(bool(ffmpeg), "source preflight requires a real FFmpeg executable")
    # Source checkouts need not contain the Windows release binary. Preflight
    # deliberately requires an explicit source FFmpeg path; use the same real
    # executable exercised by the codec tests without weakening bundle checks.
    with tempfile.TemporaryDirectory() as directory, patch.dict(
        os.environ,
        {"POLAR_FFMPEG": str(ffmpeg), "POLAR_USER_DATA_ROOT": directory},
    ):
        report = run_preflight(official=False, codec_selftest=True)
    checks = {item.key: item for item in report.checks}
    non_gl = [item for item in report.checks if item.key != "qt_opengl"]
    failures = "; ".join(f"{item.key}: {item.detail}" for item in non_gl if not item.passed)
    require(
        all(item.passed for item in non_gl),
        f"manifest-independent source preflight{': ' + failures if failures else ''}",
    )
    # Some Qt offscreen plugins expose a valid surface but no GL
    # context. The release script repeats this check on the real desktop/WGL
    # platform before both runtime GPU smokes.
    require(
        checks["qt_opengl"].passed or "offscreen=True" in checks["qt_opengl"].detail,
        "offscreen Qt surface or OpenGL context",
    )
    require(checks["h264_continuous_decode"].passed, "bundled H264 GOP")
    require(checks["h265_continuous_decode"].passed, "bundled HEVC GOP")


def main() -> None:
    test_continuous_codecs()
    test_recovery_gate()
    test_decoder_recovery_after_loss()
    test_runtime_paths()
    test_preflight_does_not_seed_runtime_config()
    test_capture_workers_are_bounded()
    test_evidence_and_hard_gate()
    test_qualification_intent_scope()
    test_rotating_log_and_replay()
    test_source_preflight()
    print("FIELD CANDIDATE TESTS PASSED")


if __name__ == "__main__":
    main()
