"""Run the shared test registry with truthful, scoped evidence.

Examples:
    python tools/verify_all.py --list --profile all
    python tools/verify_all.py --test test_test_registry --json report.json
    python tools/verify_all.py --profile offline --firmware /prepared/worktree
    python tools/verify_all.py --profile release --desktop --log-dir test-logs

PASS requires executed checks. SKIP, partial skips, zero tests, missing
prerequisites and timeouts cannot produce a successful aggregate exit code.
An offline PASS is never evidence that a real robot or desktop GPU worked.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import faulthandler
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.test_registry import CATEGORIES, PROFILES, TestEntry, discover, select
from tools.verification_provenance import collect_provenance
from tools.verification_sources import SOURCE_SCOPE, capture_sources, compare_sources


@dataclass(frozen=True)
class CheckResult:
    status: str
    detail: str
    tests_run: int | None = None
    checks_passed: int = 0
    skipped: int = 0
    returncode: int | None = None
    duration_s: float = 0.0
    expected_failures: int = 0


# Anchored status lines only: prose mentioning a mock's "SKIP" or "PASS"
# does not constitute test evidence. unittest's summary is counted separately.
SKIP_LINE = re.compile(r"(?im)^\s*(?:\\n)?(?:\[\s*SKIP\s*\]|SKIP(?:\s|:)|\[INCOMPLETE\]|INCOMPLETE(?:\s|:|\())[^\n]*")
PASS_LINE = re.compile(r"(?im)^\s*(?:\[\s*(?:PASS|OK)\s*\]|PASS:)\s*[^\n]+")
UNITTEST_RUN = re.compile(r"(?m)^Ran (\d+) tests? in [^\n]+$")
UNITTEST_OK = re.compile(r"(?m)^OK(?: \([^\n]*\))?$")
UNITTEST_SKIP = re.compile(r"(?m)^OK \([^\n]*\bskipped=(\d+)[^\n]*\)$")
UNITTEST_EXPECTED_FAILURE = re.compile(r"(?m)^OK \([^\n]*\bexpected failures=(\d+)[^\n]*\)$")


def classify_output(returncode: int, output: str, *, completion_pattern: str | None = None,
                    check_count_pattern: str | None = None) -> CheckResult:
    """One classifier for verify, release, and the compatibility run() API."""
    ran = [int(value) for value in UNITTEST_RUN.findall(output)]
    tests_run = sum(ran) if ran else None
    passed = len(PASS_LINE.findall(output))
    if check_count_pattern:
        reported_counts = [int(value) for value in re.findall(check_count_pattern, output)]
        passed = max([passed, *reported_counts])
    skipped_lines = [line.strip() for line in SKIP_LINE.findall(output)]
    skipped_tests = sum(int(value) for value in UNITTEST_SKIP.findall(output))
    skipped = max(len(skipped_lines), skipped_tests)
    detail = " | ".join(skipped_lines)[:500]
    failure_lines = re.findall(r"(?m)^\s*(?:\[FAIL\]|FAIL:|ERROR:)[^\n]*", output)
    if failure_lines or re.search(r"(?m)^FAILED(?:\s|\()", output):
        return CheckResult("FAIL", " | ".join(line.strip() for line in failure_lines[:3])[:500] or "unittest reported failure",
                           tests_run, passed, skipped, returncode)
    if returncode == 5 and tests_run == 0 and "NO TESTS RAN" in output:
        return CheckResult("INCOMPLETE", "unittest executed zero tests", 0, passed, skipped, returncode)
    if returncode not in (0, 77):
        missing = re.findall(r"(?m)^ModuleNotFoundError: No module named .+$", output)
        if missing:
            return CheckResult("INCOMPLETE", " | ".join(missing), tests_run, passed, skipped, returncode)
        errors = [line.strip() for line in output.splitlines()
                  if line.lstrip().startswith(("[FAIL]", "FAIL:", "ERROR:", "FAILED", "AssertionError"))]
        return CheckResult("FAIL", " | ".join(errors[:3])[:500] or f"exit={returncode}",
                           tests_run, passed, skipped, returncode)
    expected_failures = sum(int(value) for value in UNITTEST_EXPECTED_FAILURE.findall(output))
    if expected_failures:
        detail = f"{expected_failures} unittest expected failures remain unresolved" + (f" | {detail}" if detail else "")
        return CheckResult("INCOMPLETE", detail, tests_run, passed, skipped, returncode,
                           expected_failures=expected_failures)
    if returncode == 77 or skipped:
        any_passed = passed > 0 or (tests_run is not None and tests_run > skipped_tests)
        declared_incomplete = any(re.match(r"(?:\\n)?(?:\[INCOMPLETE\]|INCOMPLETE(?:\s|:|\())", line, re.I)
                                  for line in skipped_lines)
        return CheckResult("INCOMPLETE" if any_passed or declared_incomplete else "SKIP",
                           detail or (f"{skipped_tests} unittest tests skipped" if skipped_tests else "check not run (exit 77)"),
                           tests_run, passed, skipped or 1, returncode)
    if tests_run is not None:
        if any(count == 0 for count in ran):
            return CheckResult("INCOMPLETE", "unittest executed zero tests", tests_run, passed, 0, returncode)
        if len(UNITTEST_OK.findall(output)) < len(ran):
            return CheckResult("INCOMPLETE", "unittest completion summary missing", tests_run, passed, 0, returncode)
        return CheckResult("PASS", f"{tests_run} unittest tests passed", tests_run, passed, 0, returncode)
    if completion_pattern:
        if re.search(completion_pattern, output, re.M):
            return CheckResult("PASS", (f"declared script completion verified ({passed} counted assertions)" if passed else
                                        "declared script completion verified; assertion count unavailable"), None, passed, 0, returncode)
        return CheckResult("INCOMPLETE", "declared script completion missing", None, passed, 0, returncode)
    if passed:
        return CheckResult("PASS", f"{passed} explicit assertions passed", None, passed, 0, returncode)
    return CheckResult("INCOMPLETE", "exit zero without executed-test evidence", None, 0, 0, returncode)


def child_environment(user_data: str, firmware: Path | None, *, desktop: bool = False,
                      release: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    # Child imports must resolve to this exact staged source tree, never an
    # installed package or another checkout inherited through PYTHONPATH.
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["POLAR_USER_DATA_ROOT"] = user_data
    # This runner verifies the selected source checkout. A portable-app root
    # would bypass POLAR_USER_DATA_ROOT and redirect writes to another install.
    env.pop("POLAR_APP_ROOT", None)
    # Standalone startup debugging may opt into a persistent root or activation.
    # The canonical runner instead owns fresh storage and inert startup; a test
    # that exercises activation must explicitly arrange that behavior itself.
    env.pop("POLAR_STARTUP_USER_DATA_ROOT", None)
    env["POLAR_AUTOSTART"] = "0"
    env["XDG_CACHE_HOME"] = str(Path(user_data) / "cache")
    env["XDG_CONFIG_HOME"] = str(Path(user_data) / "config")
    env["XDG_DATA_HOME"] = str(Path(user_data) / "data")
    env["POLAR_FIRMWARE_ROOT"] = str(firmware or ROOT / ".firmware-not-selected")
    env["POLAR_FIRMWARE_READ_ONLY"] = "1"
    if firmware:
        env["POLAR_REFEREE_REFERENCE"] = str(firmware)
    else:
        env.pop("POLAR_REFEREE_REFERENCE", None)
    env["POLAR_DISABLE_LOCAL_CAPTURE"] = "1"
    env["POLAR_DISABLE_MEDIA_PRELOAD"] = "1"
    # OpenVINO's official telemetry wrapper uses disable_in_ci=True and its
    # consent checker recognizes exactly CI=true (case-insensitive). Set this
    # in every owned test child before optional dependencies can be imported.
    # This is a local subprocess opt-out, not a request to run hosted/paid CI.
    env["CI"] = "true"
    if desktop:
        env["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else ("xcb" if env.get("DISPLAY") else "wayland")
    else:
        env["QT_QPA_PLATFORM"] = "offscreen"
    if release:
        env["POLAR_REQUIRE_VIDEO_FIXTURE"] = "1"
    return env


def run(label: str, argv: list[str], *, firmware: Path | None = None,
        timeout: float = 180.0) -> tuple[bool, str]:
    """Compatibility API; skipped checks remain false/INCOMPLETE for callers."""
    with tempfile.TemporaryDirectory(prefix="verify_all_") as user_data:
        try:
            proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  env=child_environment(user_data, firmware), timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"INCOMPLETE: timeout after {timeout:g}s"
        except OSError as exc:
            return False, f"INCOMPLETE: {exc}"
    result = classify_output(proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or ""))
    if result.status in ("SKIP", "INCOMPLETE"):
        return False, "INCOMPLETE: " + result.detail
    return result.status == "PASS", result.detail


def execute_script(path: str, args: list[str]) -> None:
    """Internal subprocess bootstrap: direct-script semantics plus hang logs."""
    target = os.path.normcase(os.path.abspath(path))
    sys.argv = [path, *args]
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    faulthandler.enable(all_threads=True)
    faulthandler.dump_traceback_later(120, repeat=True)
    done = threading.Event()

    def sample_active_test():
        previous = None
        while not done.wait(1.0):
            names = set()
            for frame in sys._current_frames().values():
                while frame is not None:
                    if (frame.f_code.co_name.startswith("test_") and
                            os.path.normcase(os.path.abspath(frame.f_code.co_filename)) == target):
                        names.add(frame.f_code.co_name)
                    frame = frame.f_back
            active = ", ".join(sorted(names))
            if active and active != previous:
                print("ACTIVE " + active, flush=True)
            previous = active
    watcher = threading.Thread(target=sample_active_test, daemon=True)
    watcher.start()
    try:
        runpy.run_path(path, run_name="__main__")
    finally:
        done.set()
        watcher.join(0.2)
        faulthandler.cancel_dump_traceback_later()


def stop_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            # Python 3.x on Windows cannot kill a process group with Popen.kill().
            stopped = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if stopped.returncode != 0 and proc.poll() is None:
                raise OSError("taskkill did not confirm process-tree termination")
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        # Even when tree cleanup fails, never leave the owned direct child
        # running. The caller reports INCOMPLETE rather than claiming cleanup.
        if proc.poll() is None:
            proc.kill()


def run_entry(entry: TestEntry, *, firmware: Path | None, timeout: float,
              desktop: bool, release: bool, log_dir: Path | None) -> CheckResult:
    if entry.category in ("desktop", "visual"):
        if not desktop:
            return CheckResult("SKIP", "native desktop required; rerun with --desktop on a real desktop")
        if sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return CheckResult("SKIP", "no desktop display is available; offscreen GL cannot satisfy this gate")
    path = ROOT / entry.path
    if not path.is_file():
        return CheckResult("INCOMPLETE", f"missing test file: {entry.path}")
    argv = [sys.executable, "-u", str(Path(__file__).resolve()), "--_execute", str(path), *entry.args]
    started = time.monotonic()
    stdout = stderr = ""
    result = None
    with tempfile.TemporaryDirectory(prefix="verify_all_") as user_data:
        try:
            proc = subprocess.Popen(
                argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                env=child_environment(user_data, firmware, desktop=entry.category in ("desktop", "visual"), release=release),
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_process_tree(proc)
                stdout, stderr = proc.communicate(timeout=10)
                result = CheckResult("INCOMPLETE", f"timeout after {timeout:g}s; child tree stopped")
            if result is None:
                result = classify_output(proc.returncode, stdout + "\n" + stderr,
                                         completion_pattern=entry.completion_pattern,
                                         check_count_pattern=entry.check_count_pattern)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = CheckResult("INCOMPLETE", f"could not finish check: {exc}")
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        name = entry.id.replace("/", "__")
        (log_dir / f"{name}.stdout.log").write_text(stdout, encoding="utf-8")
        (log_dir / f"{name}.stderr.log").write_text(stderr, encoding="utf-8")
    return CheckResult(**{**asdict(result), "duration_s": round(time.monotonic() - started, 3)})


#: 前后端对齐审计（防倒退闸门）。
#: 孤儿基线当前为 **21**（排除翻译表后的口径），只许减不许增。
#: 演进：71（初始真实值）→ 68（瞄准+遥测）→ 56（战况面板 12 项）
#: → 53（顶栏 3 项）→ 51（覆盖层与准星）→ 50（渲染后端 Renderer）→ 48（超限脉冲与发光）→ 47（审计修正：常量别名不再被误判）
#: → 43（启动二级页、锁定框、弹药告警、缓冲能量）
#: → 42（装甲盒深度边；并修正电容读 mock 字段的既有 bug）
#: → 40（RM 指示灯开关；并修正缓冲上限写错分支的死代码）
#: → 38（雷达照射高亮、锁定脉冲）→ 36（两颗增益灯）
#: → 34（地图标记类型与广播开关）→ 31（云台电压三项）→ 30（装甲盒开关）
#: → 29（锁定目标加分；上一轮判成“需改绘制层”，实际只差一行供值）
#: → 27（功率保护的两个告警阈值；该模块默认关着，所以接线零画面风险）
#: → 24（增益时间轴三项；模块本来连面板都没有，位置是量出来而不是猜的）
#: → 23（装甲盒配色 Color；默认值改成等于接线前画面的 White，三个消费点逐值不变）
#: → 22（雷达叠加 Map；默认值改成等于接线前画面的 Both，两条面板路径共用一个门）。
#: 2026-10-16：PR17 基线与当前实测均为 21；这是既有统计口径对账，非本轮新增接线。
#: 其中两项查证后故意不接（MQTTLink/Auto Connect 与 UDPVideo/Reassemble），
#: 理由见 tools/SETTINGS_GAPS.md。
#: 早期版本没排除 `jello_locale.py`（中英翻译表），把“有中文名”当成“有人读”，
#: 曾错报 36/33。假数字比不知道数字更坑。
#: 另有 51 个同名设置项被判 AMBIGUOUS，**不计入也不计减**基线。
AUDIT_CHECKS = (
    ("tools/audit_settings.py", "二级界面设置项孤儿数不超基线",
     ["--max-orphans", "21"]),
)


def firmware_identity(repo: Path) -> str:
    """Bind reports to all worktree bytes, including untracked source files."""
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=10)
    if head.returncode != 0:
        raise ValueError("firmware target is not a Git worktree")
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(repo):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in sorted(files):
            path = Path(directory) / name
            if path.name == ".git":
                continue
            relative = path.relative_to(repo).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            if path.is_symlink():
                digest.update(b"symlink:" + os.readlink(path).encode("utf-8"))
            else:
                content = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        content.update(chunk)
                digest.update(content.digest())
    return f"path={repo} commit={head.stdout.strip()} tree_sha256={digest.hexdigest()}"


_LOCK = None


def acquire_lock() -> bool:
    """同一时刻只允许一个 verify_all，避免共享测试资源互相干扰。

    用文件锁而不是 pid 文件：进程被杀也不会留下假的占用者（锁随句柄关闭自动释放）。
    """
    global _LOCK
    path = Path(tempfile.gettempdir()) / "rm_verify_all.lock"
    handle = None
    try:
        handle = open(path, "a+", encoding="utf-8")
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if handle is not None:
            handle.close()
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _LOCK = handle
    return True


def write_json(path: Path, payload: dict) -> None:
    """Publish one complete report without exposing truncated JSON to readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def incomplete_report(payload: dict, path: Path | None, detail: str, *, state: str = "blocked") -> None:
    """Do not leave an earlier successful attempt as this run's report."""
    payload.update(run_state=state, passed=False,
                   counts={"PASS": 0, "SKIP": 0, "INCOMPLETE": 1, "FAIL": 0},
                   results=[{"id": "verification_run", "category": "evidence",
                             "status": "INCOMPLETE", "detail": detail}])
    if path is not None:
        write_json(path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--firmware", type=Path, help="exact prepared firmware worktree to validate read-only")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="offline")
    parser.add_argument("--category", choices=CATEGORIES, action="append", default=[])
    parser.add_argument("--test", "--suite", action="append", default=[], help="exact suite stem or relative path; repeatable")
    parser.add_argument("--list", action="store_true", help="list selected suites without importing or executing them")
    parser.add_argument("--desktop", action="store_true", help="explicitly enable native desktop GL/visual tests")
    parser.add_argument("--timeout", type=float, default=300.0, help="positive per-check timeout in seconds")
    parser.add_argument("--json", type=Path, help="write machine-readable inventory or complete execution report")
    parser.add_argument("--log-dir", type=Path, help="retain each suite's stdout and stderr")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    entries = discover(ROOT)
    try:
        selected = select(entries, profile=args.profile, categories=tuple(args.category), names=tuple(args.test))
    except ValueError as exc:
        parser.error(str(exc))
    payload = {"schema_version": 1, "profile": args.profile, "total_discovered": len(entries),
               "selected_count": len(selected), "evidence_scope": "Selected checks only. Mock/offline success is not hardware or field qualification.",
               "selected": [entry.to_dict() for entry in selected],
               "not_selected": [entry.to_dict() for entry in entries if entry not in selected]}
    if args.list:
        for entry in selected:
            print(f"{entry.category:10} {entry.path}" + (" " + " ".join(entry.args) if entry.args else ""))
        print(f"Selected {len(selected)} / {len(entries)} suites; nothing executed.")
        if args.json:
            write_json(args.json, payload)
        return 0
    payload["run_id"] = uuid.uuid4().hex
    payload["run_started_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    incomplete_report(payload, args.json, "Verification has started but has not completed", state="running")
    firmware = args.firmware.expanduser().resolve() if args.firmware else None
    identity = None
    if firmware is not None:
        try:
            identity = firmware_identity(firmware)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            detail = f"Invalid firmware target: {exc}"
            incomplete_report(payload, args.json, detail)
            print(f"[INCOMPLETE] {detail}")
            return 2
        print(f"[target] {identity}")
    if not acquire_lock():
        detail = "Another verifier is running; shared resources cannot be verified concurrently."
        incomplete_report(payload, args.json, detail)
        print(f"[INCOMPLETE] {detail}", file=sys.stderr)
        return 2
    results = []
    try:
        payload["provenance"] = collect_provenance(ROOT)
        sources_before = capture_sources(ROOT)
        for entry in selected:
            print(f"RUN [{entry.category}] {entry.path}", flush=True)
            result = run_entry(entry, firmware=firmware, timeout=args.timeout, desktop=args.desktop,
                               release=args.profile == "release", log_dir=args.log_dir)
            print(f"[{result.status}] {entry.path}: {result.detail}", flush=True)
            results.append({"id": entry.id, "path": entry.path, "category": entry.category,
                            "evidence": entry.evidence, **asdict(result)})
        # Named/category runs are intentionally scoped. Full verification and
        # release also share the existing settings alignment gate.
        if not args.test and not args.category:
            for rel, label, extra in AUDIT_CHECKS:
                entry = TestEntry(rel, args=tuple(extra))
                result = run_entry(entry, firmware=firmware, timeout=args.timeout, desktop=False,
                                   release=args.profile == "release", log_dir=args.log_dir)
                print(f"[{result.status}] {label}: {result.detail}", flush=True)
                results.append({"id": entry.id, "path": rel, "category": "audit", **asdict(result)})
        if firmware:
            entry = TestEntry("tools/firmware/prepare_hero_build.py", "firmware", (str(firmware), "--check-only"))
            result = run_entry(entry, firmware=firmware, timeout=args.timeout, desktop=False,
                               release=False, log_dir=args.log_dir)
            results.append({"id": entry.id, "path": entry.path, "category": "firmware", **asdict(result)})
            try:
                if firmware_identity(firmware) != identity:
                    results.append({"id": "firmware_unchanged", "status": "FAIL", "detail": "Firmware worktree changed; results invalid"})
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                results.append({"id": "firmware_unchanged", "status": "FAIL", "detail": str(exc)})
        sources_after = capture_sources(ROOT)
        source_status, source_detail = compare_sources(sources_before, sources_after)
        payload["source_stability"] = {"schema_version": 1, "status": source_status,
                                       "detail": source_detail, "scope": SOURCE_SCOPE,
                                       "before": sources_before, "after": sources_after}
        if source_status != "PASS":
            results.append({"id": "verification_source_stability", "category": "evidence",
                            "status": source_status, "detail": source_detail})
            print(f"[{source_status}] {source_detail}", flush=True)
        counts = {status: sum(item["status"] == status for item in results)
                  for status in ("PASS", "SKIP", "INCOMPLETE", "FAIL")}
        complete = bool(results) and counts["PASS"] == len(results)
        payload.update(results=results, counts=counts, passed=complete, firmware_identity=identity,
                       run_state="completed")
        if args.json:
            write_json(args.json, payload)
        print("\n" + ("PASS" if complete else "NO-GO") + " (selected scope): " +
              ", ".join(f"{count} {status}" for status, count in counts.items()))
        print(payload["evidence_scope"])
        return 0 if complete else 1
    finally:
        global _LOCK
        if _LOCK is not None:
            _LOCK.close()
            _LOCK = None


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--_execute":
        execute_script(sys.argv[2], sys.argv[3:])
    else:
        raise SystemExit(main())
