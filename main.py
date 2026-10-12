import argparse
import json
from pathlib import Path
import sys
import time

from rm_client.runtime_paths import RuntimePaths


def _preflight_options(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--codec-selftest", action="store_true")
    parser.add_argument("--preflight-output", type=Path)
    options, _ = parser.parse_known_args(argv)
    return options


def _run_replay(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only POLAR field-log replay; no uplink objects are constructed"
    )
    parser.add_argument("--replay-field-log", nargs="+", required=True, type=Path)
    parser.add_argument(
        "--replay-output",
        type=Path,
        default=Path.cwd() / "POLAR-field-replay-summary.json",
    )
    options = parser.parse_args(argv)
    from rm_client.replay import summarize_field_logs, write_replay_summary

    summary = summarize_field_logs(options.replay_field_log)
    output = write_replay_summary(options.replay_output, summary)
    print(json.dumps({**summary, "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def _run_polar_selftest(argv: list[str]) -> int:
    """Headless team-link self test.

    Handled before any GUI import so it also works on a machine whose OpenGL
    path is broken, and so opening the robot serial port never depends on Qt.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Read-only by default. Team StandardRobot++ 0x5A link bench/field "
            "self test that writes a JSON evidence draft."
        )
    )
    parser.add_argument("--polar-port", default="Auto")
    parser.add_argument("--polar-baud", type=int, default=115200)
    parser.add_argument("--polar-seconds", type=float, default=3.0)
    parser.add_argument("--polar-send-hz", type=float, default=50.0)
    parser.add_argument(
        "--polar-enable-drive",
        action="store_true",
        help="Allow the motion loopback phase (still requires --polar-confirm-motion).",
    )
    parser.add_argument(
        "--polar-confirm-motion",
        action="store_true",
        help="Second explicit acknowledgement that the robot is on stands or in a safe area.",
    )
    parser.add_argument("--polar-output", type=Path)
    options = parser.parse_args(argv)

    from rm_client.polar_selftest import run_polar_selftest, write_selftest_report

    output = options.polar_output or (
        Path.cwd() / "field_evidence" / f"polar-selftest-{int(time.time())}.json"
    )
    result = run_polar_selftest(
        port=options.polar_port,
        seconds=options.polar_seconds,
        enable_drive=options.polar_enable_drive,
        confirm_motion=options.polar_confirm_motion,
        baud=options.polar_baud,
        send_hz=options.polar_send_hz,
    )
    written = write_selftest_report(output, result)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    print(f"[POLAR-RM] selftest report: {written}")
    if result.exit_code == 0:
        print("[POLAR-RM] team link self test PASSED")
    return result.exit_code


def _prepare_runtime_paths(
    argv: list[str],
    *,
    paths: RuntimePaths | None = None,
) -> RuntimePaths:
    """Create writable runtime directories without mutating preflight media.

    A packaged preflight runs before the application is launched and is also
    executed while assembling a release.  Seeding ``user_data/configs`` during
    that check made a newly built archive contain generated runtime state.
    Normal application startup still receives its first-launch config copy;
    preflight only verifies that the destination directories are writable.
    """

    runtime_paths = paths or RuntimePaths.discover()
    runtime_paths.ensure_writable_dirs()
    if "--preflight" not in argv:
        runtime_paths.seed_default_config()
    return runtime_paths


if __name__ == "__main__":
    # Fail closed even for malformed simulation switches; never fall through
    # to a physical backend because a simulator flag was misspelled.
    if any(arg.split("=", 1)[0].startswith(("--mujoco", "--sim-")) for arg in sys.argv[1:]):
        from rm_client.ui.sim_operator_window import main as operator_main

        raise SystemExit(operator_main(sys.argv[1:]))
    if "--replay-field-log" in sys.argv:
        raise SystemExit(_run_replay(sys.argv[1:]))
    if "--polar-selftest" in sys.argv:
        argv = [arg for arg in sys.argv[1:] if arg != "--polar-selftest"]
        paths = _prepare_runtime_paths(argv)
        raise SystemExit(_run_polar_selftest(argv))
    paths = _prepare_runtime_paths(sys.argv[1:])
    if "--preflight" in sys.argv:
        from rm_client.preflight import run_preflight

        options = _preflight_options(sys.argv[1:])
        report = run_preflight(
            paths,
            official=options.official,
            codec_selftest=options.codec_selftest,
        )
        rendered = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
        if options.preflight_output is not None:
            options.preflight_output.parent.mkdir(parents=True, exist_ok=True)
            options.preflight_output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        raise SystemExit(0 if report.passed else 2)
    from rm_client.ui.polar_app import main

    main()
