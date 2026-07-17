#!/usr/bin/env python3
"""POLAR × Gestalt Lab Launcher — EVA / AIR aesthetic.

Visual language:
  - Evangelion Magi/NERV HUD: black field, warning orange, safety green, cut corners,
    monospace telemetry, Helvetica-like labels, compressed serif title cards.
  - AIR / Key "sincerity" undercurrent: soft sky wash, dedication line 「真心为你」.

Fonts (loaded if present under assets/launcher/fonts, else system fallbacks):
  - Share Tech Mono  — Magi telemetry
  - Orbitron         — English section headers
  - Noto Serif       — title card / 真心为你
  - Microsoft YaHei / SimHei / Times New Roman Condensed as fallbacks

Usage:
  .\\.venv\\Scripts\\python.exe tools\\gestalt_lab_launcher.py
  start_lab_launcher.bat
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BRIDGE_ROOT = WORKSPACE / "polar_input_bridge"
BRIDGE_BIN = BRIDGE_ROOT / "build" / "bin"
ASSETS = ROOT / "assets" / "launcher"
FONTS_DIR = ASSETS / "fonts"
CONFIG_PATH = ROOT / "configs" / "lab_launcher.json"
ERROR_LOG = ROOT / "tmp_selfcheck" / "launcher_error.txt"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ── Evangelion palette (Magi / NERV UI) ──────────────────────────────
C_BG = "#050505"
C_PANEL = "#0c0c0c"
C_PANEL_2 = "#121212"
C_ORANGE = "#ff6a00"  # classic warning / title orange
C_ORANGE_DIM = "#b34a00"
C_GREEN = "#39ff14"  # Magi active
C_GREEN_DIM = "#1f8f0e"
C_RED = "#ff2a2a"
C_GRAY = "#9a9a9a"
C_MUTE = "#5a5a5a"
C_WHITE = "#f2f2f2"
C_LCL = "#c8ff3d"  # LCL-ish highlight
# AIR soft accent (under-layer sky, not kawaii pink)
C_AIR_SKY = "#6b8cae"
C_AIR_AMBER = "#d4a574"


def qc(hex_color: str, a: float = 1.0) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


# ── Fonts ────────────────────────────────────────────────────────────


class Fonts:
    mono = "Consolas"
    display = "Arial Black"
    title = "Times New Roman"
    ui = "Microsoft YaHei UI"
    loaded: list[str] = []

    @classmethod
    def bootstrap(cls) -> None:
        loaded: list[str] = []
        if FONTS_DIR.is_dir():
            for path in FONTS_DIR.rglob("*"):
                if path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    fid = QFontDatabase.addApplicationFont(str(path))
                    if fid != -1:
                        families = QFontDatabase.applicationFontFamilies(fid)
                        loaded.extend(families)
        cls.loaded = loaded
        # Prefer downloaded families
        for fam in loaded:
            low = fam.lower()
            if "share tech mono" in low or "sharetechmono" in low:
                cls.mono = fam
            if "orbitron" in low:
                cls.display = fam
            if "noto serif" in low or "notoserif" in low:
                cls.title = fam
        # System fallbacks for CJK / condensed
        db = QFontDatabase()
        families = set(db.families())
        for cand in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "PingFang SC"):
            if cand in families:
                cls.ui = cand
                break
        for cand in ("Times New Roman", "Noto Serif", "SimSun", "STSong"):
            if cand in families and "noto" not in cls.title.lower():
                # keep Noto Serif if already set from file
                if cls.title == "Times New Roman" or cand == "Times New Roman":
                    cls.title = cand
                    break

    @classmethod
    def f(cls, family: str, size: float, bold: bool = False, mono: bool = False) -> QFont:
        font = QFont(family, int(size))
        font.setBold(bold)
        font.setStyleHint(QFont.StyleHint.Monospace if mono else QFont.StyleHint.SansSerif)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        # Eva title cards often use tight tracking via letter spacing
        return font


# ── Config / process layer (unchanged behaviour) ─────────────────────


def resolve_python() -> str:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.is_file() else sys.executable


def load_config() -> dict:
    defaults = {
        "camera_id": int(os.environ.get("POLAR_OBS_CAMERA_ID", "1") or 1),
        "vision_port": 9871,
        "aa_port": 9872,
        "ws_port": int(os.environ.get("POLAR_WS_PORT", "0") or 0),
        # shm = Gestalt VisionBridge 共享内存（默认）；obs/legacy = 旧 OBS 路径
        "pipeline": "shm",
        # True = 外挂 RBExtAim 闭环（client AA 门控；不走鼠标注入）；False = 只画框
        "aim_control": True,
        "player_id": 0,
        "theme_bg": "eva",
        "auto_start": False,
        "start_delay_ms": 650,
        # Input Bridge DLL：START ALL 时先注入 Shipping（键鼠夺舍通道）
        "auto_inject_dll": True,
        "client_use_input_bridge": True,
        "client_bridge_exclusive": False,
    }
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return defaults


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class LogBus(QObject):
    line = Signal(str)


def resolve_input_bridge_paths() -> tuple[Optional[Path], Optional[Path]]:
    """Return (injector.exe, dll) if built artifacts exist."""
    candidates = [
        BRIDGE_BIN,
        BRIDGE_ROOT / "build" / "Release",
        BRIDGE_ROOT / "build",
    ]
    for d in candidates:
        inj = d / "polar_input_injector.exe"
        dll = d / "polar_input_bridge.dll"
        if inj.is_file() and dll.is_file():
            return inj, dll
    return None, None


def run_input_bridge_inject(log: LogBus, *, timeout_s: float = 20.0) -> bool:
    """One-shot: LoadLibrary polar_input_bridge.dll into Shipping."""
    inj, dll = resolve_input_bridge_paths()
    if not inj or not dll:
        log.line.emit(
            "✗ INPUT BRIDGE DLL missing — build first:\n"
            f"  {BRIDGE_ROOT / 'tools' / 'build_mingw64.ps1'}\n"
            f"  expect: {BRIDGE_BIN / 'polar_input_bridge.dll'}"
        )
        return False
    cmd = [str(inj), "--dll", str(dll.resolve())]
    log.line.emit(f"▶ INJECT DLL :: {dll.name}")
    log.line.emit(f"  injector={inj}")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(inj.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        log.line.emit("✗ INJECT timeout — is the game running?")
        return False
    except OSError as exc:
        log.line.emit(f"✗ INJECT OS error: {exc}")
        return False
    out = (completed.stdout or "") + (completed.stderr or "")
    for line in out.splitlines():
        line = line.strip()
        if line:
            log.line.emit(f"[MAGI-I] {line}")
    if completed.returncode == 0:
        log.line.emit("✓ INPUT BRIDGE ARMED (pipe POLARInputBridge.<pid>)")
        return True
    log.line.emit(
        f"✗ INJECT failed code={completed.returncode} "
        "(start game first / rebuild static DLL if missing lib)"
    )
    return False


class ServiceProc:
    def __init__(
        self,
        key: str,
        title: str,
        code: str,
        subtitle: str,
        build_cmd: Callable[[dict], list[str]],
        *,
        capture: bool = True,
        extra_env: Optional[Callable[[dict], dict]] = None,
        oneshot: bool = False,
        oneshot_runner: Optional[Callable[[dict, LogBus], bool]] = None,
    ) -> None:
        self.key = key
        self.title = title
        self.code = code
        self.subtitle = subtitle
        self.build_cmd = build_cmd
        self.capture = capture
        self.extra_env = extra_env
        self.oneshot = oneshot
        self.oneshot_runner = oneshot_runner
        self.proc: Optional[subprocess.Popen] = None
        self.started_at = 0.0
        self.last_error = ""
        self.armed = False  # oneshot success latch
        self.armed_at = 0.0

    @property
    def running(self) -> bool:
        if self.oneshot:
            return bool(self.armed)
        return self.proc is not None and self.proc.poll() is None

    def start(self, cfg: dict, log: LogBus) -> bool:
        if self.oneshot and self.oneshot_runner is not None:
            if self.armed and self.key == "input_dll":
                log.line.emit(f"[{self.code}] already ARMED — re-inject…")
            ok = bool(self.oneshot_runner(cfg, log))
            self.armed = ok
            self.armed_at = time.time() if ok else 0.0
            self.last_error = "" if ok else "inject failed"
            self.started_at = time.time() if ok else 0.0
            return ok
        if self.running:
            log.line.emit(f"[{self.code}] ALREADY ACTIVE")
            return True
        cmd = self.build_cmd(cfg)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if self.extra_env:
            env.update(self.extra_env(cfg))
        log.line.emit(f"▶ START {self.code} :: {' '.join(cmd)}")
        try:
            kwargs: dict = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL}
            if self.capture:
                kwargs.update(
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if sys.platform == "win32":
                    kwargs["creationflags"] = CREATE_NO_WINDOW
            elif sys.platform == "win32":
                kwargs["creationflags"] = 0x00000200
            self.proc = subprocess.Popen(cmd, **kwargs)
            self.started_at = time.time()
            self.last_error = ""
            if self.capture and self.proc.stdout is not None:
                threading.Thread(
                    target=self._pump, args=(log,), name=f"log-{self.key}", daemon=True
                ).start()
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self.proc = None
            log.line.emit(f"✗ FAIL {self.code} :: {exc}")
            return False

    def _pump(self, log: LogBus) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.rstrip()
            if line:
                log.line.emit(f"[{self.code}] {line}")
        log.line.emit(f"■ HALT {self.code} code={self.proc.poll()}")

    def stop(self, log: LogBus) -> None:
        if self.oneshot:
            if self.armed:
                log.line.emit(
                    f"■ {self.code} note: DLL stays in game until process exit "
                    f"(armed latch cleared)"
                )
            self.armed = False
            self.armed_at = 0.0
            return
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            self.proc = None
            return
        log.line.emit(f"■ STOP {self.code} pid={self.proc.pid}")
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                self.proc.send_signal(signal.SIGTERM)
                try:
                    self.proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError as exc:
            log.line.emit(f"STOP ERR {self.code}: {exc}")
        finally:
            self.proc = None


def build_services() -> list[ServiceProc]:
    py = resolve_python()

    def bridge_cmd(_c: dict) -> list[str]:
        return [py, str(ROOT / "tools" / "gestalt_attr_http_bridge.py")]

    def aim_cmd(c: dict) -> list[str]:
        # SHM + RBExtAim closed-loop (default). Client ArmorAutoAim (:9872) gates claim/aim.
        # Never uses OS mouse inject for aim.
        cmd = [
            py,
            str(ROOT / "tools" / "tongji_desktop_runner.py"),
            "--port",
            str(int(c.get("vision_port", 9871))),
            "--aa-port",
            str(int(c.get("aa_port", 9872))),
            "--control",
            "--player-id",
            str(int(c.get("player_id", 0) or 0)),
        ]
        ws = int(c.get("ws_port", 0) or 0)
        if ws > 0:
            cmd.extend(["--ws-port", str(ws)])
        if c.get("aim_control") is False:
            # explicit opt-out
            if "--control" in cmd:
                cmd.remove("--control")
            cmd.append("--no-control")
        return cmd

    def client_cmd(_c: dict) -> list[str]:
        return [py, str(ROOT / "main.py")]

    def inject_runner(_c: dict, log: LogBus) -> bool:
        # Manual ACTIVATE always injects; START ALL skips this unit when config off
        return run_input_bridge_inject(log)

    def client_env(c: dict) -> dict:
        env = {"POLAR_VISION_SOURCE": "Tongji"}
        if c.get("client_use_input_bridge", True):
            env["POLAR_USE_INPUT_BRIDGE"] = "1"
        else:
            env["POLAR_USE_INPUT_BRIDGE"] = "0"
        if c.get("client_bridge_exclusive"):
            env["POLAR_BRIDGE_EXCLUSIVE"] = "1"
        return env

    return [
        ServiceProc(
            "input_dll",
            "Input Bridge DLL",
            "MAGI-I",
            "注入 Shipping：键鼠管道夺舍 | 需先开游戏",
            lambda _c: ["input-bridge-oneshot"],
            capture=True,
            oneshot=True,
            oneshot_runner=inject_runner,
        ),
        ServiceProc(
            "bridge",
            "数据桥",
            "MAGI-B",
            "Attr HTTP :9841 → UDP :9840",
            bridge_cmd,
            capture=True,
        ),
        ServiceProc(
            "aim",
            "同济自瞄 SHM/RBExtAim",
            "MAGI-A",
            "SHM + RBExtAim闭环(无鼠标) | gate=Client AA :9872",
            aim_cmd,
            capture=True,
            extra_env=lambda c: {
                "POLAR_TONGJI_PIPELINE": str(c.get("pipeline", "shm")),
                "POLAR_VISION_UDP_PORT": str(int(c.get("vision_port", 9871))),
                "POLAR_WS_PORT": str(int(c.get("ws_port", 0) or 0)),
            },
        ),
        ServiceProc(
            "client",
            "POLAR Client",
            "MAGI-S",
            "HUD / 框 / 夺舍键位 → Input Bridge",
            client_cmd,
            capture=False,
            extra_env=client_env,
        ),
    ]


# ── Painting helpers (Eva cut-corner panels) ─────────────────────────


def eva_panel_path(rect: QRectF, cut: float = 14.0) -> QPainterPath:
    """Diagonal-cut rectangle used throughout NERV/Magi UIs."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    c = min(cut, w * 0.2, h * 0.2)
    path = QPainterPath()
    path.moveTo(x + c, y)
    path.lineTo(x + w, y)
    path.lineTo(x + w, y + h - c)
    path.lineTo(x + w - c, y + h)
    path.lineTo(x, y + h)
    path.lineTo(x, y + c)
    path.closeSubpath()
    return path


def paint_eva_panel(
    p: QPainter,
    rect: QRectF,
    *,
    fill: QColor,
    border: QColor,
    cut: float = 14.0,
    border_w: float = 1.6,
    header: bool = False,
) -> None:
    path = eva_panel_path(rect, cut)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.fillPath(path, fill)
    pen = QPen(border)
    pen.setWidthF(border_w)
    p.setPen(pen)
    p.drawPath(path)
    if header:
        # top warning bar
        bar = QRectF(rect.x() + cut, rect.y(), rect.width() - cut, 3)
        p.fillRect(bar, border)


# ── Widgets ──────────────────────────────────────────────────────────


class EvaCanvas(QWidget):
    """Full-window background: EVA grid + optional AIR sky underlay."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = "eva"
        self._pix_eva: Optional[QPixmap] = None
        self._pix_air: Optional[QPixmap] = None
        self._t0 = time.perf_counter()
        self._load()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(50)

    def _load(self) -> None:
        pe = ASSETS / "bg_eva_magi.jpg"
        pa = ASSETS / "bg_air_sky.jpg"
        if pe.is_file():
            self._pix_eva = QPixmap(str(pe))
        if pa.is_file():
            self._pix_air = QPixmap(str(pa))

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in ("eva", "air", "hybrid") else "eva"
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = self.rect()
        p.fillRect(r, qc(C_BG))

        # AIR under-layer (soft sincerity)
        if self.mode in ("air", "hybrid") and self._pix_air and not self._pix_air.isNull():
            scaled = self._pix_air.scaled(
                r.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - r.width()) // 2
            y = (scaled.height() - r.height()) // 2
            p.setOpacity(0.28 if self.mode == "hybrid" else 0.55)
            p.drawPixmap(0, 0, scaled, x, y, r.width(), r.height())
            p.setOpacity(1.0)
            p.fillRect(r, qc(C_BG, 0.55 if self.mode == "hybrid" else 0.35))

        if self.mode in ("eva", "hybrid") and self._pix_eva and not self._pix_eva.isNull():
            scaled = self._pix_eva.scaled(
                r.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - r.width()) // 2
            y = (scaled.height() - r.height()) // 2
            p.setOpacity(0.22 if self.mode == "hybrid" else 0.35)
            p.drawPixmap(0, 0, scaled, x, y, r.width(), r.height())
            p.setOpacity(1.0)
            p.fillRect(r, qc(C_BG, 0.62 if self.mode == "hybrid" else 0.55))

        # Magi grid
        p.setPen(QPen(qc(C_ORANGE, 0.08), 1))
        step = 48
        for x in range(0, r.width(), step):
            p.drawLine(x, 0, x, r.height())
        for y in range(0, r.height(), step):
            p.drawLine(0, y, r.width(), y)

        # scanline pulse
        t = time.perf_counter() - self._t0
        scan_y = int((t * 40) % max(1, r.height()))
        grad = QLinearGradient(0, scan_y - 40, 0, scan_y + 40)
        grad.setColorAt(0.0, qc(C_ORANGE, 0.0))
        grad.setColorAt(0.5, qc(C_ORANGE, 0.07))
        grad.setColorAt(1.0, qc(C_ORANGE, 0.0))
        p.fillRect(0, scan_y - 40, r.width(), 80, grad)

        # corner brackets (Eva HUD)
        p.setPen(QPen(qc(C_ORANGE, 0.85), 2))
        m, L = 18, 36
        for x0, y0, sx, sy in (
            (m, m, 1, 1),
            (r.width() - m, m, -1, 1),
            (m, r.height() - m, 1, -1),
            (r.width() - m, r.height() - m, -1, -1),
        ):
            p.drawLine(x0, y0, x0 + sx * L, y0)
            p.drawLine(x0, y0, x0, y0 + sy * L)
        p.end()


class ServiceCard(QWidget):
    start_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self, service: ServiceProc, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setMinimumHeight(118)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self.lbl_code = QLabel(service.code)
        self.lbl_code.setFont(Fonts.f(Fonts.mono, 11, bold=True, mono=True))
        self.lbl_code.setStyleSheet(f"color: {C_ORANGE}; letter-spacing: 2px;")
        self.lbl_title = QLabel(service.title)
        self.lbl_title.setFont(Fonts.f(Fonts.ui, 15, bold=True))
        self.lbl_title.setStyleSheet(f"color: {C_WHITE};")
        top.addWidget(self.lbl_code)
        top.addSpacing(10)
        top.addWidget(self.lbl_title)
        top.addStretch(1)
        self.lbl_state = QLabel("STANDBY")
        self.lbl_state.setFont(Fonts.f(Fonts.mono, 10, bold=True, mono=True))
        self.lbl_state.setStyleSheet(f"color: {C_MUTE};")
        top.addWidget(self.lbl_state)
        lay.addLayout(top)

        self.lbl_sub = QLabel(service.subtitle)
        self.lbl_sub.setFont(Fonts.f(Fonts.mono, 10, mono=True))
        self.lbl_sub.setStyleSheet(f"color: {C_GRAY};")
        lay.addWidget(self.lbl_sub)

        self.lbl_meta = QLabel("—")
        self.lbl_meta.setFont(Fonts.f(Fonts.mono, 10, mono=True))
        self.lbl_meta.setStyleSheet(f"color: {C_MUTE};")
        lay.addWidget(self.lbl_meta)

        row = QHBoxLayout()
        self.btn_start = self._mk_btn("ACTIVATE", C_ORANGE)
        self.btn_stop = self._mk_btn("HALT", C_RED)
        self.btn_start.clicked.connect(self.start_clicked.emit)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        lay.addLayout(row)

    def _mk_btn(self, text: str, accent: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedHeight(30)
        b.setFont(Fonts.f(Fonts.mono, 10, bold=True, mono=True))
        b.setStyleSheet(
            f"""
            QPushButton {{
                color: {accent};
                background: #0a0a0a;
                border: 1px solid {accent};
                padding: 0 14px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ background: {accent}; color: #000; }}
            QPushButton:disabled {{ color: {C_MUTE}; border-color: {C_MUTE}; background: #080808; }}
            """
        )
        return b

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        active = self.service.running
        border = qc(C_GREEN if active else C_ORANGE, 0.9 if active else 0.55)
        paint_eva_panel(
            p,
            QRectF(self.rect()).adjusted(1, 1, -1, -1),
            fill=qc(C_PANEL, 0.92),
            border=border,
            cut=16,
            border_w=1.8,
            header=True,
        )
        # status edge tick
        if active:
            p.fillRect(4, 10, 3, self.height() - 20, qc(C_GREEN, 0.85))
        p.end()

    def refresh(self) -> None:
        if self.service.oneshot:
            inj, dll = resolve_input_bridge_paths()
            built = bool(inj and dll)
            if self.service.armed:
                self.lbl_state.setText("ARMED")
                self.lbl_state.setStyleSheet(f"color: {C_GREEN};")
                elapsed = int(time.time() - (self.service.armed_at or self.service.started_at or time.time()))
                self.lbl_meta.setText(f"DLL in game  T+{elapsed:04d}s  (HALT only clears latch)")
                self.btn_start.setEnabled(True)  # allow re-inject
                self.btn_start.setText("RE-INJECT")
                self.btn_stop.setEnabled(True)
            else:
                self.lbl_state.setText("STANDBY" if built else "NO BUILD")
                self.lbl_state.setStyleSheet(f"color: {C_MUTE if built else C_RED};")
                if not built:
                    self.lbl_meta.setText(f"build: {BRIDGE_ROOT / 'tools' / 'build_mingw64.ps1'}")
                else:
                    self.lbl_meta.setText(self.service.last_error or "ACTIVATE after game starts")
                self.btn_start.setEnabled(True)
                self.btn_start.setText("ACTIVATE")
                self.btn_stop.setEnabled(False)
            self.update()
            return
        if self.service.running:
            self.lbl_state.setText("ACTIVE")
            self.lbl_state.setStyleSheet(f"color: {C_GREEN};")
            elapsed = int(time.time() - self.service.started_at)
            pid = self.service.proc.pid if self.service.proc else "-"
            self.lbl_meta.setText(f"T+{elapsed:04d}s  PID {pid}")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_start.setText("ACTIVATE")
        else:
            self.lbl_state.setText("STANDBY")
            self.lbl_state.setStyleSheet(f"color: {C_MUTE};")
            self.lbl_meta.setText(self.service.last_error or "NO SIGNAL")
            self.btn_start.setEnabled(True)
            self.btn_start.setText("ACTIVATE")
            self.btn_stop.setEnabled(False)
        self.update()


class EvaButton(QPushButton):
    def __init__(self, text: str, *, primary: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(48)
        self.setFont(Fonts.f(Fonts.display if Fonts.display != "Arial Black" else Fonts.mono, 12, bold=True))
        accent = C_ORANGE if primary else C_AIR_SKY
        self.setStyleSheet(
            f"""
            QPushButton {{
                color: #000000;
                background: {accent};
                border: 2px solid {accent};
                padding: 0 22px;
                letter-spacing: 2px;
                font-weight: 800;
            }}
            QPushButton:hover {{ background: #ff8a33; border-color: #ff8a33; }}
            QPushButton:disabled {{ background: #2a2a2a; color: #666; border-color: #444; }}
            """
        )


class LauncherWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("POLAR LAB // MAGI BOOT")
        self.resize(1120, 740)
        self.setMinimumSize(960, 640)
        self.cfg = load_config()
        self.log_bus = LogBus()
        self.log_bus.line.connect(self._append_log)
        self.services = build_services()
        self._seq_timer: Optional[QTimer] = None
        self._seq_index = 0

        self.canvas = EvaCanvas()
        self.setCentralWidget(self.canvas)
        root = QVBoxLayout(self.canvas)
        root.setContentsMargins(32, 26, 32, 24)
        root.setSpacing(14)

        root.addWidget(self._build_title_card())
        root.addWidget(self._build_command_bar())
        mid = QHBoxLayout()
        mid.setSpacing(14)
        mid.addWidget(self._build_services(), 3)
        mid.addWidget(self._build_config(), 2)
        root.addLayout(mid, 1)
        root.addWidget(self._build_console(), 1)

        self.canvas.set_mode(str(self.cfg.get("theme_bg", "eva")))

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start(350)

        self.log_bus.line.emit("════════════════════════════════════════")
        self.log_bus.line.emit("  POLAR LAB  //  MAGI SYNCHRONIZATION")
        self.log_bus.line.emit("  BRIDGE · VISION · CLIENT")
        self.log_bus.line.emit("════════════════════════════════════════")
        self.log_bus.line.emit(f"PYTHON  {resolve_python()}")
        self.log_bus.line.emit(f"ROOT    {ROOT}")
        if Fonts.loaded:
            self.log_bus.line.emit(f"FONTS   {', '.join(Fonts.loaded[:6])}")
        self.log_bus.line.emit("READY. AWAITING COMMAND.")

        if self.cfg.get("auto_start"):
            QTimer.singleShot(500, self.start_all)

    # ── sections ─────────────────────────────────────────────────────

    def _build_title_card(self) -> QWidget:
        class Painted(QFrame):
            def paintEvent(self, event) -> None:  # noqa: N802
                p = QPainter(self)
                paint_eva_panel(
                    p,
                    QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                    fill=qc(C_PANEL, 0.94),
                    border=qc(C_ORANGE, 0.92),
                    cut=20,
                    border_w=2.0,
                    header=True,
                )
                p.fillRect(0, 10, 4, self.height() - 20, qc(C_ORANGE, 0.95))
                p.end()
                super().paintEvent(event)

        painted = Painted()
        painted.setFixedHeight(118)
        pl = QVBoxLayout(painted)
        pl.setContentsMargins(22, 14, 22, 12)
        pl.setSpacing(2)

        ep = QLabel("EPISODE: LAB / BOOT SEQUENCE")
        ep.setFont(Fonts.f(Fonts.mono, 10, mono=True))
        ep.setStyleSheet(f"color: {C_ORANGE}; background: transparent; letter-spacing: 3px;")
        pl.addWidget(ep)

        title = QLabel("POLAR  LAB  LAUNCHER")
        title.setFont(Fonts.f(Fonts.title, 28, bold=True))
        title.setStyleSheet(f"color: {C_WHITE}; background: transparent; letter-spacing: 4px;")
        pl.addWidget(title)

        row_w = QWidget()
        row_w.setStyleSheet("background: transparent;")
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        dedicate = QLabel("真心为你")
        dedicate.setFont(Fonts.f(Fonts.title, 14, bold=True))
        dedicate.setStyleSheet(
            f"color: {C_AIR_AMBER}; background: transparent; letter-spacing: 8px;"
        )
        sub = QLabel("—  FOR YOUR SAKE  /  MAGI BOOT INTERFACE")
        sub.setFont(Fonts.f(Fonts.mono, 10, mono=True))
        sub.setStyleSheet(f"color: {C_GRAY}; background: transparent;")
        self.lbl_clock = QLabel()
        self.lbl_clock.setFont(Fonts.f(Fonts.mono, 11, mono=True))
        self.lbl_clock.setStyleSheet(f"color: {C_GREEN}; background: transparent;")
        row_l.addWidget(dedicate)
        row_l.addSpacing(8)
        row_l.addWidget(sub)
        row_l.addStretch(1)
        row_l.addWidget(self.lbl_clock)
        pl.addWidget(row_w)
        return painted

    def _build_command_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(72)

        class Bar(QFrame):
            def paintEvent(self, event) -> None:  # noqa: N802
                p = QPainter(self)
                paint_eva_panel(
                    p,
                    QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                    fill=qc(C_PANEL_2, 0.94),
                    border=qc(C_ORANGE, 0.45),
                    cut=12,
                )
                p.end()
                super().paintEvent(event)

        bar = Bar()
        bar.setFixedHeight(72)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)

        info = QVBoxLayout()
        t1 = QLabel("COMMAND")
        t1.setFont(Fonts.f(Fonts.mono, 9, mono=True))
        t1.setStyleSheet(f"color: {C_ORANGE}; background: transparent;")
        t2 = QLabel("顺序：数据桥 → 同济自瞄 → POLAR Client")
        t2.setFont(Fonts.f(Fonts.ui, 12))
        t2.setStyleSheet(f"color: {C_WHITE}; background: transparent;")
        info.addWidget(t1)
        info.addWidget(t2)
        lay.addLayout(info, 1)

        self.btn_all = EvaButton("START ALL UNITS", primary=True)
        self.btn_all.setFixedWidth(210)
        self.btn_all.clicked.connect(self.start_all)
        self.btn_stop = EvaButton("EMERGENCY HALT", primary=False)
        self.btn_stop.setFixedWidth(180)
        self.btn_stop.setStyleSheet(
            self.btn_stop.styleSheet()
            .replace(C_AIR_SKY, C_RED)
            .replace("#ff8a33", "#ff5555")
        )
        self.btn_stop.clicked.connect(self.stop_all)
        lay.addWidget(self.btn_all)
        lay.addWidget(self.btn_stop)
        return bar

    def _build_services(self) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QLabel("MAGI UNITS  /  SERVICE STATUS")
        head.setFont(Fonts.f(Fonts.mono, 10, bold=True, mono=True))
        head.setStyleSheet(f"color: {C_ORANGE}; letter-spacing: 2px;")
        lay.addWidget(head)
        self.cards: dict[str, ServiceCard] = {}
        for svc in self.services:
            card = ServiceCard(svc)
            card.start_clicked.connect(lambda s=svc: self._start_one(s))
            card.stop_clicked.connect(lambda s=svc: self._stop_one(s))
            self.cards[svc.key] = card
            lay.addWidget(card)
        lay.addStretch(1)
        return wrap

    def _build_config(self) -> QWidget:
        class Panel(QFrame):
            def paintEvent(self, event) -> None:  # noqa: N802
                p = QPainter(self)
                paint_eva_panel(
                    p,
                    QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                    fill=qc(C_PANEL, 0.94),
                    border=qc(C_ORANGE, 0.5),
                    cut=14,
                )
                p.end()
                super().paintEvent(event)

        panel = Panel()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QLabel("PARAMETERS")
        head.setFont(Fonts.f(Fonts.mono, 10, bold=True, mono=True))
        head.setStyleSheet(f"color: {C_ORANGE}; background: transparent; letter-spacing: 2px;")
        lay.addWidget(head)

        def row(label: str, widget: QWidget) -> QHBoxLayout:
            h = QHBoxLayout()
            lab = QLabel(label)
            lab.setFont(Fonts.f(Fonts.mono, 10, mono=True))
            lab.setStyleSheet(f"color: {C_GRAY}; background: transparent; min-width: 100px;")
            h.addWidget(lab)
            h.addWidget(widget, 1)
            return h

        self.spin_ws = QSpinBox()
        self.spin_ws.setRange(0, 65535)
        self.spin_ws.setValue(int(self.cfg.get("ws_port", 0)))
        self.spin_ws.setSpecialValueText("AUTO")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1024, 65535)
        self.spin_port.setValue(int(self.cfg.get("vision_port", 9871)))
        self.combo_pipe = QComboBox()
        self.combo_pipe.addItems(["shm", "obs", "legacy"])
        pipe = str(self.cfg.get("pipeline", "shm"))
        if pipe not in ("shm", "obs", "legacy"):
            pipe = "shm"
        self.combo_pipe.setCurrentText(pipe)
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["eva", "hybrid", "air"])
        self.combo_theme.setCurrentText(str(self.cfg.get("theme_bg", "eva")))
        self.combo_theme.currentTextChanged.connect(self.canvas.set_mode)
        self.combo_inject = QComboBox()
        self.combo_inject.addItems(["on", "off"])
        self.combo_inject.setCurrentText("on" if self.cfg.get("auto_inject_dll", True) else "off")
        self.combo_bridge = QComboBox()
        self.combo_bridge.addItems(["on", "off"])
        self.combo_bridge.setCurrentText(
            "on" if self.cfg.get("client_use_input_bridge", True) else "off"
        )
        self.combo_excl = QComboBox()
        self.combo_excl.addItems(["off", "on"])
        self.combo_excl.setCurrentText(
            "on" if self.cfg.get("client_bridge_exclusive") else "off"
        )

        for w in (
            self.spin_ws,
            self.spin_port,
            self.combo_pipe,
            self.combo_theme,
            self.combo_inject,
            self.combo_bridge,
            self.combo_excl,
        ):
            w.setFont(Fonts.f(Fonts.mono, 10, mono=True))
            w.setStyleSheet(
                f"""
                QSpinBox, QComboBox {{
                    color: {C_WHITE};
                    background: #000;
                    border: 1px solid {C_ORANGE_DIM};
                    padding: 5px 8px;
                    min-height: 26px;
                }}
                QComboBox::drop-down {{ border: none; width: 18px; }}
                """
            )

        lay.addLayout(row("WS PORT", self.spin_ws))
        lay.addLayout(row("VISION UDP", self.spin_port))
        lay.addLayout(row("PIPELINE", self.combo_pipe))
        lay.addLayout(row("VISUAL", self.combo_theme))
        lay.addLayout(row("INJECT DLL", self.combo_inject))
        lay.addLayout(row("CLIENT BRIDGE", self.combo_bridge))
        lay.addLayout(row("EXCLUSIVE", self.combo_excl))

        save = QPushButton("COMMIT CONFIG")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setFont(Fonts.f(Fonts.mono, 10, bold=True, mono=True))
        save.setFixedHeight(34)
        save.setStyleSheet(
            f"""
            QPushButton {{
                color: {C_ORANGE}; background: #000;
                border: 1px solid {C_ORANGE}; letter-spacing: 1px;
            }}
            QPushButton:hover {{ background: {C_ORANGE}; color: #000; }}
            """
        )
        save.clicked.connect(self._save_settings)
        lay.addWidget(save)

        tip = QLabel(
            "PIPELINE\n"
            "  shm     — 游戏 VisionBridge 共享内存（默认）\n"
            "  obs     — 旧 OBS 虚拟摄像头\n"
            "  legacy  — 旧简化 YOLO\n\n"
            "INJECT DLL  — START ALL 第一步注入 polar_input_bridge\n"
            "CLIENT BRIDGE — client 键鼠走管道（非外置 SendInput）\n"
            "EXCLUSIVE — 屏蔽游戏原生键鼠（真·夺舍）\n\n"
            "顺序: 开游戏 → START ALL → R 自瞄 · F7 注入\n"
            "游戏需: -visionbridge -externalvisioncontrol\n"
            "DLL 构建: polar_input_bridge/tools/build_mingw64.ps1"
        )
        tip.setFont(Fonts.f(Fonts.mono, 9, mono=True))
        tip.setStyleSheet(f"color: {C_MUTE}; background: transparent;")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addStretch(1)
        return panel

    def _build_console(self) -> QWidget:
        class Panel(QFrame):
            def paintEvent(self, event) -> None:  # noqa: N802
                p = QPainter(self)
                paint_eva_panel(
                    p,
                    QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                    fill=qc("#000000", 0.9),
                    border=qc(C_GREEN_DIM, 0.7),
                    cut=12,
                )
                p.end()
                super().paintEvent(event)

        panel = Panel()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        t = QLabel("MAGI CONSOLE  //  STDOUT")
        t.setFont(Fonts.f(Fonts.mono, 10, bold=True, mono=True))
        t.setStyleSheet(f"color: {C_GREEN}; background: transparent;")
        head.addWidget(t)
        head.addStretch(1)
        clear = QPushButton("CLR")
        clear.setFixedSize(44, 22)
        clear.setFont(Fonts.f(Fonts.mono, 9, mono=True))
        clear.setStyleSheet(
            f"QPushButton {{ color:{C_MUTE}; background:transparent; border:1px solid {C_MUTE}; }}"
            f"QPushButton:hover {{ color:{C_GREEN}; border-color:{C_GREEN}; }}"
        )
        clear.clicked.connect(lambda: self.log.clear())
        head.addWidget(clear)
        lay.addLayout(head)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(Fonts.f(Fonts.mono, 10, mono=True))
        self.log.setStyleSheet(
            f"""
            QTextEdit {{
                background: transparent;
                color: {C_GREEN};
                border: none;
                selection-background-color: {C_ORANGE};
                selection-color: #000;
            }}
            """
        )
        self.log.setMinimumHeight(130)
        lay.addWidget(self.log)
        return panel

    # ── logic ────────────────────────────────────────────────────────

    def _collect(self) -> dict:
        self.cfg["ws_port"] = int(self.spin_ws.value())
        self.cfg["vision_port"] = int(self.spin_port.value())
        self.cfg["pipeline"] = self.combo_pipe.currentText()
        self.cfg["theme_bg"] = self.combo_theme.currentText()
        self.cfg["aa_port"] = int(self.cfg.get("aa_port", 9872))
        self.cfg["auto_inject_dll"] = self.combo_inject.currentText() == "on"
        self.cfg["client_use_input_bridge"] = self.combo_bridge.currentText() == "on"
        self.cfg["client_bridge_exclusive"] = self.combo_excl.currentText() == "on"
        return self.cfg

    def _save_settings(self) -> None:
        save_config(self._collect())
        self.log_bus.line.emit("✓ CONFIG COMMITTED → configs/lab_launcher.json")

    def _append_log(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        safe = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        self.log.append(f"<span style='color:#ff6a00'>{ts}</span>  {safe}")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_tick(self) -> None:
        self.lbl_clock.setText(time.strftime("%Y.%m.%d  %H:%M:%S"))
        for c in self.cards.values():
            c.refresh()
        n = sum(1 for s in self.services if s.running)
        self.btn_all.setEnabled(n < len(self.services) and self._seq_timer is None)
        self.btn_stop.setEnabled(n > 0 or self._seq_timer is not None)

    def _start_one(self, svc: ServiceProc) -> None:
        cfg = self._collect()
        save_config(cfg)
        os.environ["POLAR_TONGJI_PIPELINE"] = str(cfg.get("pipeline", "shm"))
        os.environ["POLAR_WS_PORT"] = str(int(cfg.get("ws_port", 0) or 0))
        svc.start(cfg, self.log_bus)

    def _stop_one(self, svc: ServiceProc) -> None:
        svc.stop(self.log_bus)

    def start_all(self) -> None:
        if self._seq_timer is not None:
            return
        self._collect()
        save_config(self.cfg)
        self.log_bus.line.emit("── BOOT SEQUENCE INIT ──")
        self._seq_index = 0
        self._seq_timer = QTimer(self)
        self._seq_timer.timeout.connect(self._seq_step)
        self._seq_timer.start(int(self.cfg.get("start_delay_ms", 650)))
        self._seq_step()

    def stop_all(self) -> None:
        if self._seq_timer is not None:
            self._seq_timer.stop()
            self._seq_timer = None
        self.log_bus.line.emit("── EMERGENCY HALT ──")
        for svc in reversed(self.services):
            svc.stop(self.log_bus)

    def _seq_step(self) -> None:
        if self._seq_index >= len(self.services):
            if self._seq_timer:
                self._seq_timer.stop()
                self._seq_timer = None
            self.log_bus.line.emit("── ALL UNITS SIGNALED ──")
            self.log_bus.line.emit(
                "NEXT: 上车 · R=自瞄 · Client F7=Inject Enabled · 键鼠走 Input Bridge"
            )
            return
        svc = self.services[self._seq_index]
        self._seq_index += 1
        # Skip inject unit if disabled in config
        if svc.key == "input_dll" and self.cfg.get("auto_inject_dll") is False:
            self.log_bus.line.emit("[MAGI-I] skipped (INJECT DLL=off)")
            return
        self._start_one(svc)

    def closeEvent(self, event) -> None:  # noqa: N802
        running = [s for s in self.services if s.running]
        if running:
            ans = QMessageBox.question(
                self,
                "MAGI SHUTDOWN",
                f"{len(running)} unit(s) still ACTIVE.\nHalt all before exit?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ans == QMessageBox.StandardButton.Yes:
                self.stop_all()
        save_config(self._collect())
        event.accept()


def _write_error_log(text: str) -> None:
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        ERROR_LOG.write_text(text, encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    os.chdir(ROOT)
    existing = QApplication.instance()
    if existing is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
    else:
        app = existing
    app.setApplicationName("POLAR Lab MAGI Launcher")
    Fonts.bootstrap()
    app.setStyle("Fusion")
    # Dark fusion palette
    from PySide6.QtGui import QPalette

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(C_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(C_WHITE))
    pal.setColor(QPalette.ColorRole.Base, QColor("#000000"))
    pal.setColor(QPalette.ColorRole.Text, QColor(C_GREEN))
    pal.setColor(QPalette.ColorRole.Button, QColor(C_PANEL))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(C_ORANGE))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(C_ORANGE))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
    app.setPalette(pal)

    win = LauncherWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    code = app.exec()
    if code is None or int(code) < 0:
        return 0
    return int(code)


if __name__ == "__main__":
    try:
        if ERROR_LOG.is_file():
            try:
                ERROR_LOG.unlink()
            except OSError:
                pass
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        msg = f"=== POLAR Lab Launcher CRASH ===\n{tb}\nError: {exc}\n"
        print(msg, file=sys.stderr)
        _write_error_log(msg)
        try:
            input("\nPress Enter to close...")
        except EOFError:
            time.sleep(8)
        raise SystemExit(1) from exc
