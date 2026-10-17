"""第 17 批验收：`Crosshair / Style`（RM Dot / Circle / Cross）接通。

这是一个**被同名项洗白的隐藏孤儿**：审计原先把它归在 AMBIGUOUS（"Style 有人读"），
但那些读取点属于 Armor3DESP / ChassisGimbalIndicator。给准星写的那处
`draw_crosshair()` 只读了 `Size`，从来没读 `Style`——所以界面上选 Circle
还是 Cross，画出来的都是 RM Dot 那一套。

发现方式：本轮给 `audit_settings.py` 加了按模块归属的能力（含调用点/闭包/
自写谓词三种证据），51 个同名项里 12 个仍归不上，逐个查证后抓到这个。

安全约束（本文件的主要用处）：
  * 默认档 "RM Dot" 必须与接线前**逐图元一致**——升级不会让准星突变。
  * 查不到模块/设置项/未知样式一律回落默认，绝不抛异常：准星画不出来
    比画得普通严重得多。
  * 圆环必须无填充，否则糊住目标。

另：`rm_client/app.py`（Tk 版）里也有一份 `draw_crosshair`，但那个入口被
明确禁用了（文件末尾注释：所有受支持的启动路径都走 GPU 客户端），
所以不构成第二个消费点，没有"一处有效一处无效"的问题。

    python tests/hud_batch17_crosshair_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["POLAR_USER_DATA_ROOT"] = tempfile.mkdtemp(prefix="polar_b17_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtGui import QColor, QTransform, Qt  # noqa: E402

from rm_client.model import build_default_registry  # noqa: E402
from rm_client.ui.polar_app import PolarRMWindow  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILED.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


class Host:
    """只借真方法的桩宿主：不建窗口，不碰 GPU。"""

    crosshair_style = PolarRMWindow.crosshair_style
    crosshair_primitives = PolarRMWindow.crosshair_primitives
    draw_crosshair = PolarRMWindow.draw_crosshair
    # 真方法里引用了这个类常量，桩宿主必须一并带，否则直接 AttributeError。
    CROSSHAIR_DEFAULT_STYLE = PolarRMWindow.CROSSHAIR_DEFAULT_STYLE

    def __init__(self) -> None:
        self.registry = build_default_registry()

    def setting_mode(self, module, setting_name: str, default: str) -> str:
        # 与 polar_app.py 同一实现：查不到就回落默认。
        setting = module.get_setting(setting_name) if module else None
        return str(setting.value) if setting else default

    def crosshair_color(self) -> QColor:
        return QColor("#FFFFFF")

    def set_style(self, value) -> None:
        module = self.registry.get("Crosshair")
        assert module is not None
        setting = module.get_setting("Style")
        assert setting is not None
        setting.value = value


class RecordingPainter:
    """按顺序记录画笔状态与图元，用来证明 draw_crosshair 真的在按样式分支。"""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.brush_style = Qt.BrushStyle.SolidPattern
        self.transform = QTransform()
        self.pen_width = 0.0
        self.saved_states: list[tuple] = []

    def save(self) -> None:
        self.saved_states.append((QTransform(self.transform), self.brush_style, self.pen_width))

    def restore(self) -> None:
        self.transform, self.brush_style, self.pen_width = self.saved_states.pop()

    def translate(self, x, y) -> None:
        self.transform.translate(x, y)

    def scale(self, x, y) -> None:
        self.transform.scale(x, y)

    def setPen(self, pen) -> None:
        self.pen_width = pen.widthF()
        self.events.append(("pen", pen.widthF()))

    def setBrush(self, brush) -> None:
        # 真代码里两种传法都出现：setBrush(QColor) 与 setBrush(Qt.BrushStyle.NoBrush)。
        # 不区分就会把画笔状态记旧，圆环“无填充”这条就测不出来了。
        if hasattr(brush, "style"):
            self.brush_style = brush.style()
        elif isinstance(brush, Qt.BrushStyle):
            self.brush_style = brush
        elif isinstance(brush, QColor):
            self.brush_style = Qt.BrushStyle.SolidPattern
        self.events.append(("brush", self.brush_style))

    def drawEllipse(self, center, rx, ry) -> None:  # noqa: N802
        center = self.transform.map(center)
        rx *= abs(self.transform.m11())
        self.events.append(("ellipse", round(center.x(), 3), round(center.y(), 3),
                            round(rx, 3), self.brush_style))

    def drawLine(self, a, b) -> None:  # noqa: N802
        a, b = self.transform.map(a), self.transform.map(b)
        self.events.append(("line", round(a.x(), 3), round(a.y(), 3),
                            round(b.x(), 3), round(b.y(), 3)))


def kinds(primitives: list[tuple]) -> list[str]:
    return [p[0] for p in primitives]


def test_default_style_is_unchanged() -> None:
    """默认档必须与接线前逐图元一致：中心点 2.4 + 四条留 6px 缺口的手臂。"""
    host = Host()
    check("默认取值就是 RM Dot", host.crosshair_style() == "RM Dot", host.crosshair_style())
    ops = host.crosshair_primitives("RM Dot", 400.0, 300.0, 18.0)
    expected = [
        ("dot", 400.0, 300.0, 2.4),
        ("line", 382.0, 300.0, 394.0, 300.0),
        ("line", 406.0, 300.0, 418.0, 300.0),
        ("line", 400.0, 282.0, 400.0, 294.0),
        ("line", 400.0, 306.0, 400.0, 318.0),
    ]
    check("RM Dot 图元与接线前逐项相同", ops == expected, str(ops))

    painter = RecordingPainter()
    host.draw_crosshair(painter, 800, 600)
    ellipses = [e for e in painter.events if e[0] == "ellipse"]
    lines = [e for e in painter.events if e[0] == "line"]
    check("默认绘制仍是一个实心点 + 四条线",
          len(ellipses) == 1 and len(lines) == 4 and ellipses[0][4] == Qt.BrushStyle.SolidPattern,
          str(painter.events))
    check("默认手臂端点与旧代码一致",
          lines[0] == ("line", 382.0, 300.0, 394.0, 300.0), str(lines))


def test_other_two_styles_reach_the_canvas() -> None:
    """选 Circle / Cross 后，画出来的必须真的换了图元（以前换了没反应）。"""
    host = Host()
    host.set_style("Circle")
    check("取值能读到 Circle", host.crosshair_style() == "Circle", host.crosshair_style())
    ops = host.crosshair_primitives("Circle", 400.0, 300.0, 20.0)
    check("Circle 有以 Size 为半径的圆环", ("ring", 400.0, 300.0, 20.0) in ops, str(ops))
    check("Circle 不再画手臂", "line" not in kinds(ops), str(ops))

    painter = RecordingPainter()
    host.draw_crosshair(painter, 800, 600)
    ellipses = [e for e in painter.events if e[0] == "ellipse"]
    check("Circle 实际画了两个图元（点 + 环）", len(ellipses) == 2, str(painter.events))
    check("圆环是无填充的（实心会糊住目标）",
          ellipses[-1][4] == Qt.BrushStyle.NoBrush and ellipses[0][4] == Qt.BrushStyle.SolidPattern,
          str(ellipses))
    check("圆环半径跟着 Size 走", ellipses[-1][3] == 18.0, str(ellipses[-1]))

    host.set_style("Cross")
    ops = host.crosshair_primitives("Cross", 400.0, 300.0, 24.0)
    check("Cross 是穿过中心的满长十字，无缺口",
          ("line", 376.0, 300.0, 424.0, 300.0) in ops and len(ops) == 2, str(ops))
    check("Cross 不画中心点（会被线盖住）", "dot" not in kinds(ops), str(ops))


def test_style_switch_does_not_step_on_size_or_color() -> None:
    """只改样式：Size 与热量色各自的行为不能被牵连。"""
    host = Host()
    module = host.registry.get("Crosshair")
    module.get_setting("Size").value = 40
    host.set_style("Cross")
    ops = host.crosshair_primitives(host.crosshair_style(), 0.0, 0.0, 40.0)
    check("Size 仍决定准星跨度", ops[0] == ("line", -40.0, 0.0, 40.0, 0.0), str(ops))

    host.state = None            # crosshair_color 已被桩接管，热量不参与本批
    check("换样式不影响 Size 设置项本身", float(module.get_setting("Size").value) == 40.0)
    check("样式取值始终落在界面提供的三个选项里",
          host.crosshair_style() in [str(o) for o in module.get_setting("Style").options],
          f"{host.crosshair_style()} vs {module.get_setting('Style').options}")


def test_display_scale_preserves_aim_center_and_painter_state() -> None:
    """Use actual Qt transforms so the recorder cannot silently ignore scale."""
    host = Host()
    scale = host.registry.get("Crosshair").get_setting("Crosshair Scale")
    for multiplier in (0.5, 2.0, 8.0):
        scale.value = multiplier
        host.set_style("Cross")
        painter = RecordingPainter()
        host.draw_crosshair(painter, 800, 600)
        lines = [e for e in painter.events if e[0] == "line"]
        expected = [
            ("line", 400.0 - 18.0 * multiplier, 300.0, 400.0 + 18.0 * multiplier, 300.0),
            ("line", 400.0, 300.0 - 18.0 * multiplier, 400.0, 300.0 + 18.0 * multiplier),
        ]
        check(f"Cross {multiplier}x 以同一瞄准中心缩放", lines == expected, str(lines))
        check(f"Cross {multiplier}x 恢复画笔状态",
              not painter.saved_states and painter.transform.isIdentity()
              and painter.pen_width == 0.0 and painter.brush_style == Qt.BrushStyle.SolidPattern)
        host.set_style("Circle")
        painter = RecordingPainter()
        host.draw_crosshair(painter, 800, 600)
        ellipses = [e for e in painter.events if e[0] == "ellipse"]
        check(f"Circle {multiplier}x 圆环缩放且中心不漂移",
              ellipses[-1] == ("ellipse", 400.0, 300.0, 18.0 * multiplier, Qt.BrushStyle.NoBrush),
              str(ellipses))


def test_fallbacks_never_break_the_crosshair() -> None:
    """查不到设置项 / 未知样式 / 模块不存在，都回落默认，不许抛。"""
    host = Host()

    class NoSettings:
        def get_setting(self, _name):
            return None

    host.registry = type("R", (), {"get": staticmethod(lambda _n: NoSettings())})()
    check("查不到 Style 设置项时回落 RM Dot", host.crosshair_style() == "RM Dot", host.crosshair_style())
    default_ops = host.crosshair_primitives("RM Dot", 10.0, 10.0, 18.0)
    check("未知样式回落默认而不是抛异常",
          host.crosshair_primitives("没这个选项", 10.0, 10.0, 18.0) == default_ops)
    # 真把模块抽掉（registry.get 返 None），而不是只抽设置项。
    host.registry = type("R", (), {"get": staticmethod(lambda _n: None)})()
    check("模块整个不存在时回落默认",
          host.crosshair_style() == PolarRMWindow.CROSSHAIR_DEFAULT_STYLE,
          host.crosshair_style())
    painter = RecordingPainter()
    try:
        host.draw_crosshair(painter, 800, 600)
        drawn = True
    except Exception as exc:            # noqa: BLE001 - 本条就是要捕获一切报错
        drawn = False
        print(f"    报错：{exc!r}")
    check("模块不存在时准星仍画得出来", drawn and len([e for e in painter.events if e[0] == "line"]) == 4)


def main() -> int:
    for fn in (
        test_default_style_is_unchanged,
        test_other_two_styles_reach_the_canvas,
        test_style_switch_does_not_step_on_size_or_color,
        test_display_scale_preserves_aim_center_and_painter_state,
        test_fallbacks_never_break_the_crosshair,
    ):
        fn()
    print(f"\n合计 {len(FAILED)} 项失败")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
