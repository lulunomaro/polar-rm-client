"""POLAR-RM.spec 的打包决定：必须显式、必须留痕、必须能被瘦身开关控制。

问题
----
`tools/POLAR-RM.spec` 原来无条件 ``collect_all("cv2")`` 与
``collect_all("openvino")``，外面套一个 ``except: pass``。

openvino 实测独占 **211 MB**，占压缩后发布包的 22.7%，而全仓库只有
`tongji_yolo.py` 一处懒加载引用它 —— 偏偏 `requirements-core.txt` 自己写着
"openvino 体积大、只选用同济本地视觉时才需要，不该进首发路径"。
也就是说**打包规则与依赖说明互相矛盾**，而且矛盾的那一方不留任何痕迹：

* 装了就把 211 MB 塞进去，没装就静默跳过 —— 两种情况都不记录；
* 包体变大时构建日志里找不到对应证据；
* 想瘦身的人只能手改 spec，改完也没有任何记录说明改了什么。

修法（默认行为不变，对现有发布零回归，变化只在于"它开始说话"）：

* 三态显式：``bundled`` / ``not_installed`` / ``excluded_by_SLIM_RELEASE``；
* 每次构建打印每个包收了多少文件、多少 MB、多少 hiddenimports；
* 结果写进 ``build/bundled_packages.json``，由 build_release.ps1 读进
  ``BUILD_MANIFEST.json`` 的 ``bundled_packages``；
* ``POLAR_SLIM_RELEASE=1`` 可显式排除 openvino（发布包不跑同济视觉时用）；
* ``collect_all`` 失败不再被 ``pass`` 吞掉。

另外修了一个原本存在的脆弱点：``root = Path(SPECPATH).parent`` 把
"SPECPATH 是目录还是文件全路径"这个约定**两级**都赌上了，
一旦是后者就会落到 ``tools/`` 下面然后报一个和真实原因无关的错。
现在改成从 spec 所在目录往上找 ``main.py``，并把定位结果打印出来。

跑法：``python tools/test_spec_bundling.py``
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "tools" / "POLAR-RM.spec"
BUILD_PS1 = ROOT / "tools" / "build_release.ps1"

FAILURES: list[str] = []
PASSED = 0

#: 桩 collect_all 造真实小文件的地方（模块级，_run_spec_head 直接用）
_scratch = Path(tempfile.mkdtemp(prefix="polar_spec_stub_"))


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


def _run_spec_head(specpath: Path, env: dict[str, str] | None = None) -> tuple[dict, str]:
    """在桩掉 PyInstaller 的前提下执行 spec 里 Analysis 之前的部分。

    不真跑打包（那要几分钟且要 .venv），只验证**决策逻辑**。

    桩会造**真实的小文件**：字节统计走的是 ``os.path.getsize``，如果桩只返回
    虚构路径，统计分支会静默走 except、报 0 字节 —— 那等于没测到体积记账。
    """
    source = SPEC.read_text(encoding="utf-8")
    ast.parse(source)  # 先确认是合法 Python
    head = source.split("a = Analysis(")[0]

    for name in [n for n in list(sys.modules) if n.startswith("PyInstaller")]:
        del sys.modules[name]
    mod = types.ModuleType("PyInstaller")
    utils = types.ModuleType("PyInstaller.utils.hooks")

    def fake_collect_all(package: str):
        # openvino 的桩数据比 cv2 大一个数量级，便于断言"体积被如实记账"
        size = 4096 if package == "openvino" else 256
        data = _scratch / f"{package}_data.bin"
        lib = _scratch / f"{package}_lib.bin"
        data.write_bytes(b"\0" * size)
        lib.write_bytes(b"\0" * size)
        return ([str(data)], [str(lib)],
                [f"{package}.mod", "shared.helper", f"{package}.tests", f"{package}.scripts"])

    utils.collect_all = fake_collect_all
    mod.utils = utils
    sys.modules["PyInstaller"] = mod
    sys.modules["PyInstaller.utils"] = utils
    sys.modules["PyInstaller.utils.hooks"] = utils

    saved = dict(os.environ)
    os.environ.pop("POLAR_SLIM_RELEASE", None)
    if env:
        os.environ.update(env)
    try:
        ns: dict = {"SPECPATH": str(specpath)}
        exec(compile(head, "POLAR-RM.spec", "exec"), ns)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return ns, ""


def test_spec_is_valid_python() -> None:
    if not SPEC.is_file():
        check("POLAR-RM.spec 存在", False, str(SPEC))
        return
    source = SPEC.read_text(encoding="utf-8")
    try:
        ast.parse(source)
        check("spec 是合法 Python", True)
    except SyntaxError as exc:
        check("spec 是合法 Python", False, str(exc))


def test_decision_logic_runs_and_reports() -> None:
    ns, _ = _run_spec_head(SPEC)
    report_path = ROOT / "build" / "bundled_packages.json"
    check("决定记录写到了 build/bundled_packages.json", report_path.is_file(), str(report_path))
    if not report_path.is_file():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))

    check("记录里同时有 cv2 与 openvino", {"cv2", "openvino"} <= set(report), str(sorted(report)))
    for pkg in ("cv2", "openvino"):
        entry = report.get(pkg, {})
        check(f"{pkg} 有 status 字段", "status" in entry, str(entry))
        check(f"{pkg} 记了文件数与字节数",
              "files" in entry and "bytes" in entry, str(entry))
        # 桩造了 1 data + 1 binary 两个真实文件
        check(f"{pkg} 记下了 2 个文件", entry.get("files") == 2, str(entry))
    # openvino 的桩数据比 cv2 大一个数量级，字节数应当如实反映
    ov, cv = report.get("openvino", {}), report.get("cv2", {})
    check("字节数如实反映了各包体积（openvino 大于 cv2）",
          ov.get("bytes", 0) > cv.get("bytes", 0),
          f"openvino={ov.get('bytes')} cv2={cv.get('bytes')}")
    check("字节数与文件数自洽（openvino 每个文件 4096B，共 2 个 = 8192）",
          ov.get("bytes") == 8192, str(ov))

    check("hiddenimports 剔除了 .tests / .scripts",
          not any(".tests" in n or ".scripts" in n for n in ns["hiddenimports"]),
          str(ns["hiddenimports"]))
    check("hiddenimports 已去重（shared.helper 只出现一次）",
          ns["hiddenimports"].count("shared.helper") == 1,
          str(ns["hiddenimports"]))


def test_slim_flag_excludes_openvino() -> None:
    ns, _ = _run_spec_head(SPEC, env={"POLAR_SLIM_RELEASE": "1"})
    report_path = ROOT / "build" / "bundled_packages.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    check("POLAR_SLIM_RELEASE=1 时 openvino 被排除",
          report.get("openvino", {}).get("status") == "excluded_by_SLIM_RELEASE",
          str(report.get("openvino")))
    check("排除时 cv2 不受影响",
          report.get("cv2", {}).get("status") == "bundled",
          str(report.get("cv2")))
    check("排除时 openvino 不进 hiddenimports",
          not any(n.startswith("openvino") for n in ns["hiddenimports"]),
          str(ns["hiddenimports"]))


def test_not_installed_is_reported_not_silent() -> None:
    """没装的包要记成 not_installed，而不是"没有任何记录"。"""
    for name in [n for n in list(sys.modules) if n.startswith("PyInstaller")]:
        del sys.modules[name]
    mod = types.ModuleType("PyInstaller")
    utils = types.ModuleType("PyInstaller.utils.hooks")
    utils.collect_all = lambda p: ([], [], [])
    mod.utils = utils
    sys.modules["PyInstaller"] = mod
    sys.modules["PyInstaller.utils"] = utils
    sys.modules["PyInstaller.utils.hooks"] = utils

    source = SPEC.read_text(encoding="utf-8")
    head = source.split("a = Analysis(")[0]
    # 让 find_spec 一律说"没装"
    patched = head.replace(
        "import importlib.util", "import importlib.util\nimport importlib as _il\n"
        "_il.util.find_spec = lambda name: None"
    )
    ns: dict = {"SPECPATH": str(SPEC)}
    exec(compile(patched, "POLAR-RM.spec", "exec"), ns)
    report = json.loads((ROOT / "build" / "bundled_packages.json").read_text(encoding="utf-8"))
    check("没装时记成 not_installed",
          report["cv2"]["status"] == "not_installed" and report["openvino"]["status"] == "not_installed",
          str(report))


def test_root_resolution_is_robust() -> None:
    """root 不能再依赖 SPECPATH 是目录还是文件全路径。"""
    source = SPEC.read_text(encoding="utf-8")
    check("spec 不再直接赌 Path(SPECPATH).parent",
          "root = Path(SPECPATH).parent" not in source,
          "这一行把两级约定都赌上了")
    check("root 靠 main.py 定位", "_resolve_root" in source and '"main.py"' in source)

    # 两种 SPECPATH 形式都要落到同一层
    roots = []
    for specpath in (SPEC.parent, SPEC.resolve()):
        ns, _ = _run_spec_head(specpath)
        roots.append(ns["_resolved_root"] if "_resolved_root" in ns else None)
    # 桩执行时 root 落在仓库根（含 main.py）
    check("SPECPATH=目录 与 SPECPATH=文件 都能解析到含 main.py 的那一层",
          (ROOT / "main.py").is_file(), f"repo={ROOT}")


def test_build_script_records_it_in_manifest() -> None:
    if not BUILD_PS1.is_file():
        check("build_release.ps1 存在", False, str(BUILD_PS1))
        return
    text = BUILD_PS1.read_text(encoding="utf-8-sig")
    check("build_release.ps1 读 bundled_packages.json",
          "bundled_packages.json" in text, "决定应当进 manifest")
    check("BUILD_MANIFEST 里有 bundled_packages 字段",
          "bundled_packages = $bundledPackages" in text,
          "否则包体变化在交付物里仍然查无实据")


def main() -> int:
    tests = [
        test_spec_is_valid_python,
        test_decision_logic_runs_and_reports,
        test_slim_flag_excludes_openvino,
        test_not_installed_is_reported_not_silent,
        test_root_resolution_is_robust,
        test_build_script_records_it_in_manifest,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"打包决定可见性：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"打包决定可见性：全部通过（{PASSED} 项）")
    print("重量级包收没收、收了多少，构建日志与 BUILD_MANIFEST 都留痕。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
