#!/usr/bin/env python3
"""视觉红线守卫 (visual freeze guard) — 机器可校验的"不许动视觉"清单。

红线原文：**千万不要修改任何视觉相关的一切要素。**

本文件把这条红线从"承诺"变成"可执行检查"：

1. `visual_freeze_manifest.json` 记录每个受保护文件的 SHA-256。
2. `python tools/visual_freeze_guard.py --check` 比对工作区与清单：
   任何受保护文件被改动/新增/删除 → 退出码非 0，并逐条打印。
3. `--update` 用于**有意**扩大/重建基线（必须显式调用，且需在 commit message 里说明）。

冻结范围（保守口径，宁可多冻不可漏冻）：

* 整个 `rm_client/ui/**`      —— 一切绘制、布局、字体、动画、GPU 合成、ClickGUI、HUD、地图。
* 一切 `assets/**`             —— 视觉素材本体。
* 视觉专属的表现层模块        —— 击杀特效、HUD 渲染、启动壁纸。
* 视频解码链路                —— 解码路径的任何改动都会改变画面像素。

不冻结（可改）：后端协议/传输/状态模型/配置/视觉识别/测试/工具/文档。
但注意：**冻结的是"文件内容"**。非视觉模块若在运行时被 UI 消费，
改动它仍可能间接影响画面 —— 因此所有非视觉改动还必须通过
`tests/api_freeze_test.py` + 视觉基线截图回归，二者共同构成红线。

用法::

    python tools/visual_freeze_guard.py --check          # CI / 提交前门禁
    python tools/visual_freeze_guard.py --list           # 打印冻结清单
    python tools/visual_freeze_guard.py --update         # 重建基线（需显式意图）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "tools" / "visual_freeze_manifest.json"

# --- 冻结规则 ---------------------------------------------------------------
# 用 glob 规则表达，命中即冻结。新增文件只要命中规则同样被守卫，
# 因此将来有人在冻结目录里新建渲染文件也会被自动盯住。

FROZEN_GLOBS: tuple[str, ...] = (
    # 全部 UI 表现层：绘制 / 布局 / 字体 / 动画 / GPU 合成 / 各类 GUI 与 HUD
    "rm_client/ui/**",
    # 视觉素材
    "assets/**",
    # 视觉专属表现层模块
    "rm_client/kill_fx.py",
    "rm_client/combat_hud.py",
    "rm_client/startup_wallpaper.py",
    # 视频解码链路：任何改动都可能改变画面像素
    "rm_client/backend/video_backend.py",
    "rm_client/vision_wire_gpu.py",
    # 视觉回归基线本身
    "tests/baselines/**",
)

# 永远不冻结的目录（避免把临时产物/解释器缓存算进基线）
EXCLUDED_DIR_PARTS = frozenset(
    {"__pycache__", ".git", ".venv", ".venv-polar", "node_modules", "release", "build", "dist"}
)

EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".zip", ".7z", ".png~"})


def _is_excluded(rel: Path) -> bool:
    if any(part in EXCLUDED_DIR_PARTS for part in rel.parts):
        return True
    if rel.suffix in EXCLUDED_SUFFIXES:
        return True
    return rel.name.endswith("~")


def _matches(rel: str, pattern: str) -> bool:
    """把 glob 规则匹配到仓库相对路径（POSIX 风格，** 跨目录）。"""
    from pathlib import PurePosixPath

    return PurePosixPath(rel).match(pattern) or PurePosixPath(rel).match(
        pattern.replace("/**/", "/*/")
    )


def collect_frozen_files() -> list[str]:
    """枚举当前应当受保护的仓库相对路径列表（已排序）。"""
    found: set[str] = set()
    for pattern in FROZEN_GLOBS:
        # 目录规则直接 walk；文件规则逐条 stat
        if pattern.endswith("/**"):
            base = REPO_ROOT / pattern[: -len("/**")]
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(REPO_ROOT)
                if not _is_excluded(rel):
                    found.add(rel.as_posix())
        else:
            path = REPO_ROOT / pattern
            if path.is_file():
                rel = path.relative_to(REPO_ROOT)
                if not _is_excluded(rel):
                    found.add(rel.as_posix())
    return sorted(found)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest() -> dict:
    files = collect_frozen_files()
    entries = {}
    for rel in files:
        path = REPO_ROOT / rel
        entries[rel] = sha256_of(path) if path.is_file() else None
    return {
        "schema": 1,
        "frozen_globs": list(FROZEN_GLOBS),
        "file_count": len(entries),
        "files": entries,
    }


def load_manifest() -> dict | None:
    if not MANIFEST_PATH.is_file():
        return None
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check() -> int:
    manifest = load_manifest()
    if manifest is None:
        print("[visual-freeze] 缺少基线 tools/visual_freeze_manifest.json")
        print("[visual-freeze] 请先运行: python tools/visual_freeze_guard.py --update")
        return 2

    recorded: dict[str, str | None] = manifest.get("files", {})
    current_files = set(collect_frozen_files())
    current: dict[str, str | None] = {}
    for rel in current_files:
        path = REPO_ROOT / rel
        current[rel] = sha256_of(path) if path.is_file() else None

    problems: list[str] = []

    for rel in sorted(set(recorded) - set(current)):
        problems.append(f"  [删除] {rel}  ← 冻结文件被移除")
    for rel in sorted(set(current) - set(recorded)):
        problems.append(f"  [新增] {rel}  ← 冻结范围内出现新文件（需人工确认非视觉）")
    for rel in sorted(set(recorded) & set(current)):
        if recorded[rel] != current[rel]:
            problems.append(f"  [修改] {rel}  ← 视觉红线！内容已变")

    if problems:
        print("[visual-freeze] 视觉红线被触碰，工作已停止：")
        print("\n".join(problems))
        print(f"\n共 {len(problems)} 处违规。冻结基线覆盖 {len(recorded)} 个文件。")
        print("如确有必要改动视觉，必须由人工显式执行 --update 并在提交信息中说明理由。")
        return 1

    print(
        f"[visual-freeze] OK — {len(current)} 个受保护文件逐字节未变，"
        f"{len(FROZEN_GLOBS)} 条冻结规则全部满足。"
    )
    return 0


def update() -> int:
    manifest = build_manifest()
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"[visual-freeze] 基线已重建：{manifest['file_count']} 个文件 → "
        f"{MANIFEST_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


def list_frozen() -> int:
    files = collect_frozen_files()
    for rel in files:
        size = (REPO_ROOT / rel).stat().st_size
        print(f"{size:>12,}  {rel}")
    print(f"\n共 {len(files)} 个受保护文件，规则 {len(FROZEN_GLOBS)} 条。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="视觉红线守卫")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="校验工作区未触碰冻结文件")
    group.add_argument("--update", action="store_true", help="重建冻结基线（需显式意图）")
    group.add_argument("--list", action="store_true", help="列出冻结文件")
    args = parser.parse_args(argv)

    if args.check:
        return check()
    if args.update:
        return update()
    return list_frozen()


if __name__ == "__main__":
    sys.exit(main())
