from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


class FontBook:
    def __init__(self) -> None:
        self.loaded: dict[str, str] = {}

    def load_from_project_assets(self, workspace: Path) -> None:
        candidates = {
            "axiforma_regular": "axiforma_regular.ttf",
            "axiforma_bold": "axiforma_bold.ttf",
            "axiforma_extrabold": "axiforma_extrabold.ttf",
            "poppins_regular": "Poppins-Regular.ttf",
            "poppins_medium": "Poppins-Medium.ttf",
            "poppins_bold": "Poppins-Bold.ttf",
            "museo": "MuseoSansCyrl-900.ttf",
            "material": "MaterialIcons-Regular.ttf",
            "polaricon": "*icon-Regular.ttf",
            "pingfang": "pingfang_sc_regular.ttf",
        }
        for key, filename in candidates.items():
            path = self._find_font(workspace, filename)
            if not path.exists():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                self.loaded[key] = families[0]

    def _find_font(self, workspace: Path, pattern: str) -> Path:
        for path in workspace.rglob(pattern):
            if ".venv" in path.parts:
                continue
            return path
        return workspace / "__missing_font__"

    def font(self, key: str, size: float, weight: int | None = None) -> QFont:
        family = self.loaded.get(key)
        if family is None:
            family = {
                "material": "Segoe UI Symbol",
                "polaricon": "Segoe UI",
                "museo": "Segoe UI",
                "pingfang": "Microsoft YaHei UI",
            }.get(key, "Segoe UI")
        font = QFont(family)
        font.setPointSizeF(size)
        if weight is not None:
            font.setWeight(weight)
        return font
