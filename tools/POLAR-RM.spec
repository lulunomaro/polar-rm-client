# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规则。

# 关于重量级依赖的取舍（原来这里是"看不见的决定"）

`cv2` 与 `openvino` 都会连带拖进大量二进制资源。openvino 实测独占 **211 MB**，
占压缩后发布包的 22.7%，而全仓库只有 `tongji_yolo.py` 一处懒加载引用它 ——
偏偏 `requirements-core.txt` 自己写着"openvino 体积大、只选用同济本地视觉时才需要，
不该进首发路径"。也就是说打包规则和依赖说明**互相矛盾**。

原来的写法是无条件 `collect_all(...)`，外面套一个 `except: pass`：
装了就把 211 MB 塞进去，没装就静默跳过。两种情况都不留痕，于是：

* 包体突然变大时没人知道是哪一步；
* 现场反馈"包 700 多 MB"时，构建日志里找不到对应证据；
* 想瘦身的人只能去 spec 里手改，改完也没有任何记录说明改了什么。

现在改成**显式三态**，且默认行为不变（装了且未显式排除 → 照收），所以对
现有发布流程零回归；变化只在于它**会说话**了：

* 每次构建都打印每个包收了多少字节 / 多少文件，合计多少；
* 结果写进 `BUILD_MANIFEST.json` 的 `bundled_packages`；
* 设 `POLAR_SLIM_RELEASE=1` 可显式排除 openvino（发布包不跑同济视觉时用）。
  排除会**告警**而不是静默，因为漏了同济视觉的用户会得到一个功能缺失的包。

# 判据

`import cv2` / `import openvino` 能不能成功，就决定收不收。代码里这两处都是
**函数内懒加载**（`tongji_yolo.py` 的 `__init__`、各处的 `import cv2`），
所以"能不能 import"是"运行时会不会用得上"的可靠代理。
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


def _resolve_root() -> Path:
    """找到仓库根（含 main.py 的那一层），并把判断依据打印出来。

    为什么不再直接写 ``Path(SPECPATH).parent``
    -------------------------------------------
    ``SPECPATH`` 到底是"spec 文件所在目录"还是"spec 文件全路径"，各版本/各入口
    的约定并不一致，而原写法把它**两级**都赌上了：
    ``Path(SPECPATH).parent / "main.py"``。
    只要 SPECPATH 是文件全路径，root 就会落到 ``tools/`` 下面，
    ``main.py`` 找不到、``Analysis`` 报一个和真实原因无关的错。

    现在改成从 spec 所在目录往上找 ``main.py``，找到即止，
    并把结果打出来 —— 这样无论 SPECPATH 是哪种形式都能落到同一层。
    """
    start = Path(SPECPATH)
    for candidate in [start, *start.parents]:
        if (candidate / "main.py").is_file():
            print(f"[POLAR-RM.spec] 仓库根 = {candidate}（由 main.py 定位，SPECPATH={start}）")
            return candidate
    # 兜底：保持与旧行为一致，不要因为定位失败就把整个发布弄挂
    fallback = start.parent if start.is_file() else start
    print(
        f"[POLAR-RM.spec] 警告：没找到 main.py，root 退回 {fallback}；"
        f"若 Analysis 随后报找不到 main.py，就是这里没兜住",
        file=sys.stderr,
    )
    return fallback


root = _resolve_root()

#: 收哪些包。键是包名，值是"排除它会不会影响功能"的说明（会打印出来）。
CANDIDATE_PACKAGES = ("cv2", "openvino")

#: 设成 "1"/"true"/"yes" 就把这些包排除掉，做瘦身发布包。
SLIM_ENV = "POLAR_SLIM_RELEASE"
SLIM_EXCLUDE = {"openvino"}

#: 记录这次决定，build_release.ps1 会把它读进 BUILD_MANIFEST.json。
BUNDLED_REPORT_PATH = root / "build" / "bundled_packages.json"


def _installed(package: str) -> bool:
    """能不能 import —— 懒加载依赖下，这就是"运行时会不会用得上"的可靠代理。"""
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


datas = []
binaries = []
hiddenimports = []
report = {}

for package in CANDIDATE_PACKAGES:
    if package in SLIM_EXCLUDE and os.environ.get(SLIM_ENV, "").strip().lower() in {"1", "true", "yes"}:
        report[package] = {
            "status": "excluded_by_SLIM_RELEASE",
            "files": 0,
            "bytes": 0,
        }
        print(f"[POLAR-RM.spec] {package}: 已按 {SLIM_ENV} 排除（发布包不含该功能）")
        continue

    if not _installed(package):
        report[package] = {
            "status": "not_installed",
            "files": 0,
            "bytes": 0,
        }
        print(f"[POLAR-RM.spec] {package}: 本机未安装，跳过（运行时需要它的那条路径会失效）")
        continue

    try:
        d, b, h = collect_all(package)
    except Exception as exc:  # noqa: BLE001
        # 原来这里是 `pass`，收集失败等于"这个包没进去"却没有任何痕迹。
        # 现在照样继续（不让一次收集失败把整个发布弄挂），但要说出来。
        report[package] = {
            "status": f"collect_failed: {type(exc).__name__}: {exc}",
            "files": 0,
            "bytes": 0,
        }
        print(f"[POLAR-RM.spec] {package}: collect_all 失败 —— {type(exc).__name__}: {exc}")
        continue

    names = [name for name in h if ".tests" not in name and ".scripts" not in name]
    datas += d
    binaries += b
    hiddenimports += names
    total_bytes = 0
    total_files = 0
    for path in list(d) + list(b):
        try:
            total_bytes += os.path.getsize(path)
            total_files += 1
        except OSError:
            pass
    report[package] = {
        "status": "bundled",
        "files": total_files,
        "bytes": total_bytes,
        "hiddenimports": len(names),
    }
    print(
        f"[POLAR-RM.spec] {package}: 收 {total_files} 个文件 / "
        f"{total_bytes / 1048576:.1f} MB / {len(names)} 个 hiddenimports"
    )

grand_bytes = sum(int(v.get("bytes", 0)) for v in report.values())
print(f"[POLAR-RM.spec] 合计 {grand_bytes / 1048576:.1f} MB（{len(CANDIDATE_PACKAGES)} 个候选包）")

# 两个包可能带出同一个模块名（cv2 与 openvino 都依赖 numpy 的一部分子模块）。
# 去重不是为了好看：重复的 hiddenimports 会让 PyInstaller 重复分析同一棵子树，
# 拖慢构建，也让报告里的数字对不上。
if len(hiddenimports) != len(set(hiddenimports)):
    before = len(hiddenimports)
    hiddenimports = sorted(set(hiddenimports))
    print(f"[POLAR-RM.spec] hiddenimports 去重：{before} -> {len(hiddenimports)}")

try:
    BUNDLED_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUNDLED_REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[POLAR-RM.spec] 决定记录已写到 {BUNDLED_REPORT_PATH}")
except OSError as exc:
    print(f"[POLAR-RM.spec] 无法写 {BUNDLED_REPORT_PATH}: {exc}（不影响本次打包）", file=sys.stderr)

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
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
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="POLAR-RM")
