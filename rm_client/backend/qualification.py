"""Fail-closed field qualification evidence.

Evidence lives beside the portable application and is authenticated with a
per-installation HMAC key.  It is not a substitute for physical supervision;
it prevents ordinary config edits or accidental corruption from unlocking
control and keeps every authorization bound to build and firmware identities.

Evidence writes go through :func:`_replace_with_retry` below: a bare
``os.replace`` loses a qualification record outright whenever an antivirus
scan, backup agent or sync client happens to hold the file open.
"""

from __future__ import annotations

import errno
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time
from typing import Any, Mapping
import uuid

from .protocol import PROTOCOL_VERSION


EVIDENCE_SCHEMA = 1
DYNAMIC_EVIDENCE_MAX_AGE_S = 24 * 60 * 60
CORE_TESTS = (
    "preflight",
    "codec_field_test",
    "control_heartbeat_test",
    "estop_robot_test",
    "firmware_loss_zero_test",
    "loss_recovery_test",
    "hero_deploy_status_test",
    "latency_soak_test",
    "field_signoff",
)
DYNAMIC_TESTS = frozenset(CORE_TESTS + (
    "economy_live_test",
    "prm1_vision_bench_test",
    "prm1_spin_bench_test",
))
OPTIONAL_TESTS = (
    "economy_live_test",
    "prm1_vision_schema_review",
    "prm1_vision_bench_test",
    "prm1_spin_schema_review",
    "prm1_spin_bench_test",
)
PRM1_TESTS = frozenset(
    {
        "prm1_vision_schema_review",
        "prm1_vision_bench_test",
        "prm1_spin_schema_review",
        "prm1_spin_bench_test",
    }
)



# 第 104 轮：证据写入的有界退避。与 `config_store` 里那份同殐，但**故意不导入那个模块**。
#
# 为什么不直接用 `config_store._replace_with_retry`：那个模块在**模块级**
# `atexit.register(_flush_at_exit)`。**从别处 import 它会顺带把那个钩子装上** ——
# 于是一个跟配置保存毫无关系的进程，退出时也要去跑一遍配置的排队落盘。
# **「import 一个函数」不等于「只拿到那个函数」。**
#
# 这里的重复是有意的：证据链的可靠性优先于去重复。
_REPLACE_RETRY_DELAYS: tuple[float, ...] = (
    0.0, 0.0005, 0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.064,
)
_REPLACE_RETRY_ERRORS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})


def _replace_with_retry(temp_path: Path, path: Path) -> None:
    """``os.replace`` + 有界退避重试，总退避上限 127.5 ms。

    钉子的是 ``winerror 5``（拒绝访问）与 ``32``（文件被占用）。
    非这两类的错误**不重试**，立即抛出。
    """
    for delay in _REPLACE_RETRY_DELAYS:
        try:
            os.replace(temp_path, path)
            return
        except OSError as exc:
            retryable = (
                getattr(exc, "winerror", None) in (5, 32)
                or exc.errno in _REPLACE_RETRY_ERRORS
            )
            if not retryable:
                raise
            time.sleep(delay)
    os.replace(temp_path, path)

class TestStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"


class Capability(str, Enum):
    CORE_KMC = "core_kmc"
    HERO = "hero"
    ECONOMY = "economy"
    PRM1_VISION = "prm1_vision"
    PRM1_SPIN = "prm1_spin"


@dataclass(slots=True)
class TestResult:
    key: str
    status: str = TestStatus.NOT_RUN.value
    measured: Any = None
    threshold: Any = None
    recorded_at: float = 0.0
    notes: str = ""
    source: str = "qualification-runner"


@dataclass(slots=True)
class CapabilityAuthorization:
    capability: str
    authorized: bool = False
    reason: str = "not qualified"
    authorized_at: float = 0.0
    firmware_hash: str = ""


@dataclass(slots=True)
class QualificationSession:
    session_id: str
    build_commit: str
    robot_id: str
    firmware_hash: str
    match_mode: str
    video_source: str
    operator: str
    safety_lead: str = ""
    protocol_version: str = PROTOCOL_VERSION
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tests: dict[str, TestResult] = field(default_factory=dict)
    authorizations: dict[str, CapabilityAuthorization] = field(default_factory=dict)
    signed_at: float = 0.0

    def __post_init__(self) -> None:
        for key in (*CORE_TESTS, *OPTIONAL_TESTS):
            self.tests.setdefault(key, TestResult(key=key))
        for capability in Capability:
            self.authorizations.setdefault(
                capability.value,
                CapabilityAuthorization(
                    capability=capability.value,
                    firmware_hash=self.firmware_hash,
                ),
            )


class QualificationEvidenceStore:
    def __init__(self, root: Path, *, build_commit: str) -> None:
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.key_path = self.root / ".integrity_key"
        self.current_path = self.root / "current.json"
        self.build_commit = str(build_commit or "UNKNOWN")
        self.last_error = ""

    def create(
        self,
        *,
        robot_id: str,
        firmware_hash: str,
        match_mode: str,
        video_source: str,
        operator: str,
    ) -> QualificationSession:
        self._validate_identity(robot_id, firmware_hash, operator)
        session = QualificationSession(
            session_id=f"{int(time.time())}-{uuid.uuid4().hex[:12]}",
            build_commit=self.build_commit,
            robot_id=str(robot_id).strip(),
            firmware_hash=self._normalise_hash(firmware_hash),
            match_mode=str(match_mode).strip(),
            video_source=str(video_source).strip(),
            operator=str(operator).strip(),
        )
        self.save(session)
        return session

    def save(self, session: QualificationSession) -> Path:
        session.updated_at = time.time()
        body = asdict(session)
        envelope = {"schema": EVIDENCE_SCHEMA, "session": body}
        envelope["hmac_sha256"] = self._sign(envelope)
        path = self.sessions_dir / f"{session.session_id}.json"
        self._atomic_json(path, envelope)
        pointer = {"schema": EVIDENCE_SCHEMA, "session_id": session.session_id}
        pointer["hmac_sha256"] = self._sign(pointer)
        self._atomic_json(self.current_path, pointer)
        return path

    def load_current(self) -> QualificationSession | None:
        if not self.current_path.is_file():
            return None
        try:
            pointer = json.loads(self.current_path.read_text(encoding="utf-8"))
            self._verify(pointer)
            session_id = str(pointer["session_id"])
            return self.load(session_id)
        except Exception as exc:
            self.last_error = f"evidence pointer invalid: {exc}"
            return None

    def load(self, session_id: str) -> QualificationSession | None:
        path = self.sessions_dir / f"{session_id}.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            self._verify(envelope)
            raw = dict(envelope["session"])
            raw["tests"] = {
                str(key): TestResult(**value) for key, value in dict(raw.get("tests", {})).items()
            }
            raw["authorizations"] = {
                str(key): CapabilityAuthorization(**value)
                for key, value in dict(raw.get("authorizations", {})).items()
            }
            session = QualificationSession(**raw)
            if session.build_commit != self.build_commit:
                raise ValueError("evidence build does not match running build")
            self.last_error = ""
            return session
        except Exception as exc:
            self.last_error = f"evidence invalid: {exc}"
            return None

    def delete_current(self) -> None:
        session = self.load_current()
        if session is not None:
            (self.sessions_dir / f"{session.session_id}.json").unlink(missing_ok=True)
        self.current_path.unlink(missing_ok=True)

    def export_current(self, destination: Path) -> Path:
        session = self.load_current()
        if session is None:
            raise ValueError(self.last_error or "no qualification session")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"POLAR-field-evidence-{session.session_id}"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copy2(self.sessions_dir / f"{session.session_id}.json", target / "session.json")
        # Export the installation key with the evidence bundle so an external
        # verifier can authenticate the session bytes. This key authorises
        # nothing inside the running client; MATCH_ARMED still requires the
        # original installation, live identity checks, and explicit operator
        # action on every launch.
        shutil.copy2(self.key_path, target / "evidence-hmac.key")
        # Portable evidence lives at <app>/field_evidence. Source tests may
        # override it under <app>/user_data/field_evidence.
        candidate_roots = (self.root.parent, self.root.parent.parent)
        app_root = next(
            (root for root in candidate_roots if (root / "BUILD_MANIFEST.json").is_file()),
            self.root.parent,
        )
        manifest = app_root / "BUILD_MANIFEST.json"
        manifest_sidecar = app_root / "BUILD_MANIFEST.sha256"
        if manifest.is_file():
            shutil.copy2(manifest, target / "BUILD_MANIFEST.json")
        if manifest_sidecar.is_file():
            shutil.copy2(manifest_sidecar, target / "BUILD_MANIFEST.sha256")
        (target / "VERIFY.txt").write_text(
            "Verify session.json HMAC-SHA256 over the canonical envelope without hmac_sha256, "
            "using evidence-hmac.key. Then verify build identity using BUILD_MANIFEST.sha256.\n"
            "The bundle is sensitive qualification material; store it with restricted access.\n"
            f"Build: {session.build_commit}\nSession: {session.session_id}\n",
            encoding="utf-8",
        )
        return target

    def _key(self) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.key_path.is_file():
            key = self.key_path.read_bytes()
            if len(key) == 32:
                return key
        key = secrets.token_bytes(32)
        temporary = self.key_path.with_suffix(".new")
        temporary.write_bytes(key)
        os.replace(temporary, self.key_path)
        return key

    def _sign(self, envelope: Mapping[str, Any]) -> str:
        clean = {key: value for key, value in envelope.items() if key != "hmac_sha256"}
        canonical = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key(), canonical, hashlib.sha256).hexdigest().upper()

    def _verify(self, envelope: Mapping[str, Any]) -> None:
        expected = str(envelope.get("hmac_sha256", ""))
        if not expected or not hmac.compare_digest(expected, self._sign(envelope)):
            raise ValueError("HMAC mismatch")

    @staticmethod
    def _normalise_hash(value: str) -> str:
        clean = str(value).strip().upper()
        if len(clean) != 64 or any(character not in "0123456789ABCDEF" for character in clean):
            raise ValueError("firmware hash must be a 64-character SHA-256")
        return clean

    @classmethod
    def _validate_identity(cls, robot_id: str, firmware_hash: str, operator: str) -> None:
        if not str(robot_id).strip() or not str(operator).strip():
            raise ValueError("robot id and operator are required")
        cls._normalise_hash(firmware_hash)

    def _atomic_json(self, path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # 第 104 轮：原来这里是**裸 `os.replace`**——单次尝试，不重试。
            #
            # 而 `config_store._replace_with_retry` 会对 `winerror 5/32`
            # （文件被别的进程占住）与 `EACCES/EPERM/EBUSY` 做**有界退避**，总上限 127.5 ms。
            #
            # **后果是实质的**：只要有一个正在扫描的杀软 / 备份程序 / 同步盘
            # 开着这个证据文件，**一次现场资格签字就直接丢掉**——而配置写入能靠退避撑过去。
            # **证据写不进去 = 这次资格作废**，它比配置更需要这份重试。
            #
            # ⚠ **为什么在这里重试，而不是 import `config_store` 的那份**：
            # `config_store` 在模块级 `atexit.register(_flush_at_exit)`，
            # **从别处 import 它会顺带把那个钩子装上**——
            # 于是一个跟配置保存毫无关系的进程，退出时也会去跑一遍配置的排队落盘。
            # 「import 一个函数」不等于「只拿到那个函数」。
            # 两份的逻辑相同，**留着重复也强过让证据链依赖一个不相关的退出钩子**。
            _replace_with_retry(Path(temp_name), path)
        finally:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


class QualificationManager:
    """Only this runner may create technical evidence records."""

    def __init__(self, store: QualificationEvidenceStore) -> None:
        self.store = store
        self.session = store.load_current()

    def new_session(self, **identity: str) -> QualificationSession:
        self.session = self.store.create(**identity)
        return self.session

    def reset(self) -> None:
        self.store.delete_current()
        self.session = None

    def record_automatic(
        self,
        key: str,
        passed: bool,
        *,
        measured: Any = None,
        threshold: Any = None,
        notes: str = "",
        source: str = "qualification-runner",
    ) -> TestResult:
        if self.session is None:
            raise ValueError("create a qualification session first")
        if source in {"config", "ui-boolean"}:
            raise ValueError("configuration cannot create technical evidence")
        if str(key) in PRM1_TESTS:
            raise ValueError("PRM1 evidence requires the supervised firmware-bound runner")
        return self._record_result(
            key,
            passed,
            measured=measured,
            threshold=threshold,
            notes=notes,
            source=source,
        )

    def _record_result(
        self,
        key: str,
        passed: bool,
        *,
        measured: Any = None,
        threshold: Any = None,
        notes: str = "",
        source: str,
    ) -> TestResult:
        """Persist a runner result after the public entry point has gated it."""

        if self.session is None:
            raise ValueError("create a qualification session first")
        result = TestResult(
            key=str(key),
            status=TestStatus.PASS.value if passed else TestStatus.FAIL.value,
            measured=measured,
            threshold=threshold,
            recorded_at=time.time(),
            notes=str(notes),
            source=str(source),
        )
        self.session.tests[result.key] = result
        self._refresh_authorizations()
        self.store.save(self.session)
        return result

    def record_supervised_prm1(
        self,
        key: str,
        passed: bool,
        *,
        reviewer: str,
        measured: Any = None,
        threshold: Any = None,
        notes: str = "",
    ) -> TestResult:
        """Record firmware-bound PRM1 evidence from the supervised runner.

        Generic ``record_automatic`` deliberately cannot mint a PASS for these
        capabilities, which prevents a config edit or an unrelated test helper
        from silently unlocking a team-defined CustomControl schema.
        """

        if self.session is None:
            raise ValueError("create a qualification session first")
        if key not in PRM1_TESTS:
            raise ValueError("not a PRM1 evidence key")
        named = str(reviewer).strip()
        if not named:
            raise ValueError("a named PRM1 reviewer/observer is required")
        details = dict(measured) if isinstance(measured, Mapping) else {"measurement": measured}
        details.update({
            "reviewer": named,
            "robot_id": self.session.robot_id,
            "firmware_hash": self.session.firmware_hash,
        })
        return self._record_result(
            key,
            passed,
            measured=details,
            threshold=threshold,
            notes=notes,
            source="supervised-prm1-fixture",
        )

    def sign_off(self, safety_lead: str) -> TestResult:
        if self.session is None:
            raise ValueError("create a qualification session first")
        lead = str(safety_lead).strip()
        if not lead or lead.casefold() == self.session.operator.casefold():
            raise ValueError("safety lead must be a second named person")
        technical = tuple(key for key in CORE_TESTS if key != "field_signoff")
        if not all(self.test_passed(key) for key in technical):
            raise ValueError("all core technical tests must pass before sign-off")
        # Freeze an authenticated copy of every technical record *before* the
        # human sign-off. A later rerun would otherwise silently preserve the
        # old signatures while changing the evidence that was reviewed.
        signed_digest = self._technical_digest()
        self.session.safety_lead = lead
        self.session.signed_at = time.time()
        result = TestResult(
            key="field_signoff",
            status=TestStatus.PASS.value,
            measured={
                "operator": self.session.operator,
                "safety_lead": lead,
                "technical_digest_sha256": signed_digest,
            },
            threshold="two distinct named people",
            recorded_at=time.time(),
            notes="",
            source="dual-human-signoff",
        )
        self.session.tests[result.key] = result
        self.store.save(self.session)
        return result

    def test_passed(self, key: str, *, now: float | None = None) -> bool:
        if self.session is None:
            return False
        result = self.session.tests.get(key)
        if result is None or result.status != TestStatus.PASS.value:
            return False
        wall = time.time() if now is None else float(now)
        if key in DYNAMIC_TESTS and wall - float(result.recorded_at) > DYNAMIC_EVIDENCE_MAX_AGE_S:
            result.status = TestStatus.EXPIRED.value
            # Expiry is a state transition, not a display-only calculation.
            # Persist it so clock rollback or process restart cannot resurrect
            # a previously expired technical result.
            self.store.save(self.session)
            return False
        return True

    def core_ready(self) -> tuple[bool, str]:
        if self.session is None:
            return False, "no qualification session"
        if self.session.build_commit != self.store.build_commit:
            return False, "build mismatch"
        if self.session.protocol_version != PROTOCOL_VERSION:
            return False, "protocol mismatch"
        if self.session.video_source not in {"udp", "native_0310"}:
            return False, "qualification video source is undeclared"
        missing = [key for key in CORE_TESTS if not self.test_passed(key)]
        if missing:
            return False, "missing/expired: " + ", ".join(missing)
        signoff = self.session.tests.get("field_signoff")
        measured = signoff.measured if signoff is not None else None
        signed_digest = (
            str(measured.get("technical_digest_sha256", ""))
            if isinstance(measured, Mapping)
            else ""
        )
        if not signed_digest or not hmac.compare_digest(signed_digest, self._technical_digest()):
            return False, "technical evidence changed after field sign-off"
        return True, "qualified"

    def _technical_digest(self) -> str:
        if self.session is None:
            return ""
        keys = tuple(key for key in (*CORE_TESTS, *OPTIONAL_TESTS) if key != "field_signoff")
        payload = {
            key: asdict(self.session.tests[key])
            for key in keys
            if key in self.session.tests
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest().upper()

    def identity_matches(
        self,
        *,
        robot_id: str,
        firmware_hash: str,
        match_mode: str,
        video_source: str,
    ) -> tuple[bool, str]:
        if self.session is None:
            return False, "no qualification session"
        try:
            firmware = self.store._normalise_hash(firmware_hash)
        except ValueError as exc:
            return False, str(exc)
        expected = (
            str(self.session.robot_id),
            self.session.firmware_hash,
            str(self.session.match_mode),
            str(self.session.video_source),
        )
        actual = (
            str(robot_id).strip(),
            firmware,
            str(match_mode).strip(),
            str(video_source).strip(),
        )
        if actual != expected:
            return False, "runtime robot/firmware/match/video identity differs from evidence"
        return True, "identity matched"

    def verified_core_ready(self) -> tuple[bool, str]:
        """Reload authenticated bytes before the safety layer arms control."""

        if self.session is None:
            return False, "no qualification session"
        verified = self.store.load(self.session.session_id)
        if verified is None:
            return False, self.store.last_error or "evidence integrity failure"
        self.session = verified
        return self.core_ready()

    def capabilities(self) -> frozenset[str]:
        if self.session is None:
            return frozenset()
        self._refresh_authorizations()
        return frozenset(
            key for key, value in self.session.authorizations.items() if value.authorized
        )

    def evidence_flags(self) -> dict[str, bool]:
        if self.session is None:
            return {}
        return {key: self.test_passed(key) for key in self.session.tests}

    def _refresh_authorizations(self) -> None:
        if self.session is None:
            return
        core, reason = self.core_ready()
        mapping = {
            Capability.CORE_KMC.value: core,
            Capability.HERO.value: core and self.test_passed("hero_deploy_status_test"),
            Capability.ECONOMY.value: core and self.test_passed("economy_live_test"),
            # PRM1 can never inherit core authorization. It needs a reviewed
            # schema/bench record tied to this exact firmware hash.
            Capability.PRM1_VISION.value: core
            and self.test_passed("prm1_vision_schema_review")
            and self.test_passed("prm1_vision_bench_test"),
            Capability.PRM1_SPIN.value: core
            and self.test_passed("prm1_spin_schema_review")
            and self.test_passed("prm1_spin_bench_test"),
        }
        for capability, authorized in mapping.items():
            item = self.session.authorizations.setdefault(
                capability,
                CapabilityAuthorization(capability=capability),
            )
            item.authorized = bool(authorized)
            item.reason = "qualified" if authorized else reason if capability == Capability.CORE_KMC.value else "capability evidence missing"
            item.authorized_at = time.time() if authorized else 0.0
            item.firmware_hash = self.session.firmware_hash
