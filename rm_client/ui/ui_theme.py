"""终端界面偏好；一次启动固定一种表现层，独立于机器人配置。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..runtime_paths import RuntimePaths

THEMES = ("endfield", "jello")
_active_theme: str | None = None


def preference_path() -> Path:
    return RuntimePaths.discover().user_data_root / "ui_theme.json"


def read_theme() -> str:
    try:
        value = json.loads(preference_path().read_text(encoding="utf-8")).get("theme")
        return value if value in THEMES else "endfield"
    except (OSError, ValueError, AttributeError, TypeError):
        return "endfield"


def current_theme() -> str:
    global _active_theme
    if _active_theme is None:
        _active_theme = read_theme()
    return _active_theme


def is_jello() -> bool:
    return current_theme() == "jello"


def save_theme(theme: str) -> None:
    """原子写入下次启动的界面，避免改变当前进程已构造的控件类型。"""
    if theme not in THEMES:
        raise ValueError("未知界面：" + str(theme))
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps({"theme": theme}) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
