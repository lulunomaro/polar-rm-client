from __future__ import annotations

"""POLAR 视觉 token：整体偏向 macOS / iOS Dark 系统色。

液态玻璃材料语义对齐 liquid-glass-react（霜面、边缘折射、弹性），
颜色管理对齐 Apple HIG Dark Mode system colors。
"""

from dataclasses import dataclass, field

from PySide6.QtGui import QColor


def color(hex_value: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_value)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


@dataclass
class PolarTheme:
    # Backgrounds (iOS grouped / system)
    bg: QColor = field(default_factory=lambda: color("#000000"))
    grouped_bg: QColor = field(default_factory=lambda: color("#1C1C1E"))
    secondary_bg: QColor = field(default_factory=lambda: color("#2C2C2E"))
    panel: QColor = field(default_factory=lambda: color("#1C1C1E", 0.92))
    panel_soft: QColor = field(default_factory=lambda: color("#ffffff", 0.10))
    row_hover: QColor = field(default_factory=lambda: color("#ffffff", 0.08))

    # Labels
    text: QColor = field(default_factory=lambda: color("#F5F5F7"))  # label
    muted: QColor = field(default_factory=lambda: color("#98989D"))  # secondaryLabel
    tertiary: QColor = field(default_factory=lambda: color("#6C6C70"))  # tertiaryLabel
    dim: QColor = field(default_factory=lambda: color("#000000", 0.36))
    separator: QColor = field(default_factory=lambda: color("#ffffff", 0.12))

    # System accents (iOS Dark)
    system_blue: QColor = field(default_factory=lambda: color("#0A84FF"))
    system_green: QColor = field(default_factory=lambda: color("#30D158"))
    system_red: QColor = field(default_factory=lambda: color("#FF453A"))
    system_orange: QColor = field(default_factory=lambda: color("#FF9F0A"))
    system_yellow: QColor = field(default_factory=lambda: color("#FFD60A"))
    system_pink: QColor = field(default_factory=lambda: color("#FF375F"))
    system_purple: QColor = field(default_factory=lambda: color("#BF5AF2"))
    system_teal: QColor = field(default_factory=lambda: color("#64D2FF"))
    system_indigo: QColor = field(default_factory=lambda: color("#5E5CE6"))

    # 兼容旧代码名 → 映射到 system 色（UI 主强调用蓝，非霓虹）
    green: QColor = field(default_factory=lambda: color("#30D158"))
    gray: QColor = field(default_factory=lambda: color("#8E8E93"))
    accent: QColor = field(default_factory=lambda: color("#F5F5F7"))
    cyan: QColor = field(default_factory=lambda: color("#64D2FF"))  # systemTeal，更 iOS
    pink: QColor = field(default_factory=lambda: color("#FF375F"))
    blue: QColor = field(default_factory=lambda: color("#0A84FF"))
    red: QColor = field(default_factory=lambda: color("#FF453A"))
    orange: QColor = field(default_factory=lambda: color("#FF9F0A"))

    # 材料
    material_fill: QColor = field(default_factory=lambda: color("#ffffff", 0.14))
    material_stroke: QColor = field(default_factory=lambda: color("#ffffff", 0.22))

    # Continuous 圆角（逻辑 px，绘制时 * scale）
    radius_chip: float = 12.0
    radius_card: float = 16.0
    radius_panel: float = 22.0
    radius_pill: float = 999.0


THEME = PolarTheme()
