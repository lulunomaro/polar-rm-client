"""Independent value/command rendering without a window, registry or bridge."""
from dataclasses import FrozenInstanceError, asdict, replace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from rm_client.economy_values import EconomyContext, EconomyOffer
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.economy_content import (
    EconomyContentStyle, EconomyPaintPrimitives, EconomyTransactionText,
    append_gpu_categories, append_gpu_confirmation, append_gpu_offer,
    append_gpu_offer_detail, append_gpu_status, append_gpu_transaction,
    paint_economy_categories, paint_economy_confirmation, paint_economy_offer,
)
from rm_client.ui.economy_layout import compat_economy_layout, economy_module_geometry
from rm_client.ui.economy_presentation import EconomyConfirmationState
from rm_client.ui.economy_workspace import EconomyWorkspaceLayer

OFFER = EconomyOffer("small", "ammo", "Buy ammo", "Small ammo", "Rule text", "item", 1, 30, 1, 1, True)


def font(family, size):
    result = QFont(family)
    result.setPointSizeF(size)
    return result


def style():
    return EconomyContentStyle(font, 1., lambda: False)


class Painter:
    def __init__(self):
        self.calls = []
        self.saved = 0

    def save(self):
        self.saved += 1

    def restore(self):
        self.saved -= 1

    def setClipPath(self, path):
        self.calls.append(("clip", path.boundingRect()))


def primitives():
    def record(kind):
        return lambda p, *args, **kwargs: p.calls.append((kind, args, kwargs))
    return EconomyPaintPrimitives(record("text"), record("rounded"), record("glass"), lambda p, f, t: 10.)


class EconomyContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_offer_renderer_uses_only_supplied_immutable_offer_and_clip(self):
        rect, clip = QRectF(10., 20., 300., 80.), QRectF(12., 22., 290., 70.)
        for enabled, status in ((True, False), (False, False), (True, True)):
            offer = replace(OFFER, enabled=enabled, status_only=status, reason="blocked")
            before = asdict(offer)
            commands = ClickGuiDrawList()
            append_gpu_offer(commands, offer, rect, clip, 18., .8, style())
            texts = commands.texts
            self.assertEqual([item.text for item in texts], ["Small ammo", "仅状态" if status else "30 金币", "Rule text" if enabled else "blocked"])
            self.assertTrue(all(item.clip == clip for item in texts))
            self.assertEqual(asdict(offer), before)
            painter = Painter()
            paint_economy_offer(painter, offer, rect, style(), primitives())
            self.assertEqual([call[1][1] for call in painter.calls], [item.text for item in texts])

    def test_detail_tags_distinguish_estimate_mock_and_authoritative_context(self):
        for estimated, mock, tag in ((True, True, "预计价格"), (True, False, "预计价格"),
                                      (False, True, "模拟请求"), (False, False, "裁判系统")):
            commands = ClickGuiDrawList()
            append_gpu_offer_detail(commands, replace(OFFER, estimated=estimated), QRectF(0., 0., 300., 116.),
                                    QRectF(0., 0., 300., 116.), mock, 1., style())
            self.assertEqual(commands.texts[1].text, f"item  ·  30 金币  ·  {tag}")
            self.assertEqual(commands.texts[-1].text, "可以购买")

    def test_status_geometry_clamps_zero_capacity_and_has_no_match_state_effects(self):
        context = EconomyContext.from_state(SimpleNamespace(remaining_economy=500, total_economy_obtained=0,
                                                            current_health=50, max_health=100, remaining_ammo=0, ammo_capacity=0))
        tiles = tuple(QRectF(i * 200., 0., 190., 50.) for i in range(3))
        commands = ClickGuiDrawList()
        append_gpu_status(commands, tiles, QRectF(0., 0., 600., 60.), context, 30, 1., style())
        self.assertEqual(commands.texts[1].text, "累计 0 · 待扣 30")
        self.assertEqual([r.rect.width() for r in commands.rects], [162., 81., 162., 0.])
        self.assertEqual(context.gold, 500)

    def test_transaction_view_contains_no_request_or_bridge_and_preserves_unknown_status(self):
        labels = {"succeeded": "操作成功", "rejected": "请求被拒绝", "timed_out": "确认超时", "pending": "等待裁判系统", "new-status": "new-status"}
        for status, label in labels.items():
            view = EconomyTransactionText(status, "Small ammo", "message")
            commands = ClickGuiDrawList()
            append_gpu_transaction(commands, view, QRectF(0., 10., 300., 44.), QRectF(0., 0., 300., 80.), 1., style())
            self.assertEqual([text.text for text in commands.texts], [f"{label} · Small ammo", "message"])
            with self.assertRaises(FrozenInstanceError):
                view.status = "succeeded"

    def test_pending_hold_cannot_be_rendered_as_enabled_but_compat_retains_held_fill(self):
        offer = replace(OFFER, confirmation="hold")
        view = EconomyConfirmationState(offer, True, "small", 10., "", 0.).present(now=10.3)
        commands = ClickGuiDrawList()
        rect = QRectF(10., 10., 200., 40.)
        append_gpu_confirmation(commands, rect, rect, view, show_hold=True, alpha=1., hover=0., press=0., style=style())
        self.assertEqual(len(commands.rects), 1)
        self.assertEqual(commands.texts[0].text, "请求处理中…")
        painter = Painter()
        paint_economy_confirmation(painter, rect, view, style(), primitives())
        fill = [call for call in painter.calls if call[0] == "rounded"]
        self.assertEqual(len(fill), 1)
        self.assertAlmostEqual(fill[0][1][0].width(), 100.)
        self.assertEqual(painter.saved, 0)

    def test_navigation_is_independent_of_window_and_keeps_clipped_category_sink(self):
        categories = ["ammo", "recovery", "revive", "support"]
        for size in ((1280, 720), (1920, 1080)):
            layout = EconomyWorkspaceLayer().layout(*size, 1.)
            geometry = economy_module_geometry(layout, category_count=len(categories))
            commands, hotspots, animation = ClickGuiDrawList(), [], []
            def animate(index):
                animation.append(index)
                return index
            append_gpu_categories(commands, layout, geometry, categories, "revive", style(),
                                  animate_selection=animate, register_category=lambda rect, key: hotspots.append((rect, key)))
            self.assertEqual([key for _, key in hotspots], categories)
            self.assertTrue(all(layout.body_clip.contains(rect) for rect, _ in hotspots))
            self.assertEqual(animation, [] if layout.three_column else [2.])
            painter = Painter()
            compat = compat_economy_layout(*size, scale=1., alpha=1., category_count=len(categories))
            compatibility_hits = []
            paint_economy_categories(painter, compat, categories, "revive", style(), primitives(),
                                    draw_capsule=lambda *a, **k: None,
                                    register_category=lambda rect, key: compatibility_hits.append(key))
            self.assertEqual(compatibility_hits, categories)

    def test_navigation_sink_failure_is_not_swallowed_or_followed_by_later_categories(self):
        categories = ["ammo", "recovery", "revive", "support"]
        layout = EconomyWorkspaceLayer().layout(1920, 1080, 1.)
        geometry = economy_module_geometry(layout, category_count=len(categories))
        commands, attempts = ClickGuiDrawList(), []
        def reject(rect, key):
            attempts.append(key)
            raise RuntimeError("hotspot registration failed")
        with self.assertRaisesRegex(RuntimeError, "hotspot registration failed"):
            append_gpu_categories(commands, layout, geometry, categories, "ammo", style(),
                                  animate_selection=lambda index: index, register_category=reject)
        self.assertEqual(attempts, ["ammo"])
        self.assertNotIn("恢复", [text.text for text in commands.texts])

    def test_import_has_no_window_model_backend_or_transport_dependency(self):
        code = '''
import sys
from rm_client.ui.economy_content import EconomyTransactionText
assert EconomyTransactionText("pending", "item", "message").status == "pending"
assert not any(name.startswith(("rm_client.model", "rm_client.backend", "rm_client.ui.polar_app", "rm_client.ui.polar.mixins")) for name in sys.modules)
assert "rm_client.economy" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
