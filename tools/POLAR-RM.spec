# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

# The spec executes in the freezing process, not in the packaged application.
# Cover direct spec invocation as well as build_release.ps1, before hooks import
# optional SDKs. This is the observed OpenVINO opt-out, not network isolation.
os.environ["CI"] = "true"
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
# Keep data beside its importing frozen module, independent of the launch cwd.
datas = [
    (str(root / "rm_client/backend/polar_protocol.json"), "rm_client/backend"),
    (str(root / "configs/cc_key_usage.json"), "configs"),
]
binaries = []
hiddenimports = []
# Public exports are lazy to keep schema imports free of Qt/OpenCV. Explicit
# inclusion is required because import_module names are otherwise invisible.
hiddenimports += ["rm_client.backend." + name for name in (
    "manager", "safety", "mqtt_backend", "video_backend", "serial_backend",
    "readiness", "qualification", "protocol", "team_control",
)]
for package in ("cv2", "openvino"):
    try:
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hiddenimports += [name for name in h if ".tests" not in name and ".scripts" not in name]
    except Exception:
        pass

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
# Qt 6.11 on Windows uses the OS ICU forwarding DLLs (unversioned exports).
# An unrelated Poppler/Conda icuuc.dll on the build PATH exports *_78 instead;
# bundling it shadows the Windows shim and QtCore fails with WinError 127.
# The portable target is Windows 11: resolve its inbox ICU, never redistribute
# OS DLLs or pick up another application's ICU through the developer's PATH.
if sys.platform == "win32":
    a.binaries = [entry for entry in a.binaries
                  if Path(entry[0]).name.lower() not in {"icu.dll", "icuuc.dll", "icuin.dll"}
                  and not Path(entry[0]).name.lower().startswith("icudt")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="POLAR-RM",
    console=False,
    disable_windowed_traceback=False,
    target_arch="x86_64",
    version=str(root / "BUILD_VERSION_INFO.txt") if (root / "BUILD_VERSION_INFO.txt").is_file() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="POLAR-RM")
