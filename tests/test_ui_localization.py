"""Display-only Simplified Chinese coverage and registry compatibility guards."""
import ast
import copy
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.config_store import apply_registry_config, export_client_config
from rm_client.model import CATEGORIES, build_default_registry
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.jello_clickgui import JelloClickGuiRenderer, JelloClickGuiState
from rm_client.ui.jello_locale import (
    CATEGORY_ZH, DESCRIPTION_ZH, LANGUAGE_CHINESE, LANGUAGE_ENGLISH, MODULE_ZH,
    SETTING_ZH, action_label, category_label, chinese_enabled, description_label,
    module_label, option_label, setting_label, toast_text, ui_text,
)
from rm_client.ui.polar.mixins.escape_menu_mixin import EscapeMenuMixin
from rm_client.ui.fonts import FontBook

APP = QApplication.instance() or QApplication([])
FONTS = FontBook()
FONTS.load_from_project_assets(Path(__file__).resolve().parents[1])


def snapshot(registry):
    return export_client_config(
        registry, gui_mode="JelloSigma", selected_category="Combat", selected_module="HeroLob",
        module_expanded=set(), old_panels={}, new_panels={}, dropdown_panels={},
    )


class DisplayHost(EscapeMenuMixin):
    def __init__(self):
        self.registry = build_default_registry()
        self.hover_point = QPointF(-1, -1)
        self.gui_scroll_viewports = []
        self.hotspots = []
        self._escape_menu_visible = True
        self._escape_menu_selected = 0

    def font(self, family, size):
        return FONTS.font(family, size)

    def add_hotspot(self, rect, action, payload=None, button=None, tooltip=""):
        self.hotspots.append(NS(rect=rect, action=action, payload=payload, button=button, tooltip=tooltip))


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_registry()

    def test_default_and_old_config_without_language_are_chinese(self):
        self.assertTrue(chinese_enabled(self.registry))
        self.assertEqual(self.registry.get("ClickGui").get_setting("Language").value, LANGUAGE_CHINESE)
        apply_registry_config(self.registry, {"modules": {"ClickGui": {"settings": {"Scale": 1.25}}}})
        self.assertTrue(chinese_enabled(self.registry))
        self.assertTrue(chinese_enabled(None))
        self.assertTrue(chinese_enabled(NS(get=lambda name: None)))

    def test_explicit_saved_english_is_respected_and_round_trips(self):
        apply_registry_config(self.registry, {"modules": {"ClickGui": {"settings": {"Language": LANGUAGE_ENGLISH}}}})
        self.assertFalse(chinese_enabled(self.registry))
        data = snapshot(self.registry)
        clone = build_default_registry()
        apply_registry_config(clone, data)
        self.assertEqual(clone.get("ClickGui").get_setting("Language").value, LANGUAGE_ENGLISH)
        self.assertFalse(chinese_enabled(clone))
        self.assertEqual(data["modules"]["RobotLink"]["settings"]["Link Mode"], "serial")
        self.assertEqual(data["modules"]["HeroLob"]["settings"]["Mode"], "Radar Assisted")

    def test_all_builtin_module_category_and_setting_names_are_localized(self):
        self.assertTrue(set(CATEGORIES) <= CATEGORY_ZH.keys())
        self.assertTrue({m.name for m in self.registry.modules} <= MODULE_ZH.keys())
        for module in self.registry.modules:
            self.assertRegex(module_label(module.name, True), r"[\u4e00-\u9fff]")
            self.assertRegex(category_label(module.category, True), r"[\u4e00-\u9fff]")
            for setting in module.settings:
                with self.subTest(module=module.name, setting=setting.name):
                    self.assertRegex(setting_label(setting.name, True), r"[\u4e00-\u9fff]")
                    if setting.description:
                        self.assertRegex(description_label(setting.description, True), r"[\u4e00-\u9fff]")

    def test_builtin_options_have_labels_or_are_intentional_identifiers(self):
        # Proper theme names, units, hardware codes, keys and network addresses
        # stay recognizable. Newly added user-facing prose must be translated.
        identifiers = {"", "%", "CPU", "GPU", "Ctrl", "Shift", "Shark UDP", "UDP HEVC",
                       "LiquidBounce", "Minecraft 1.21.11", "Polar", "rm2026-v1.3.1", "dps", "rpm"}
        for module in self.registry.modules:
            for setting in module.settings:
                for option in setting.options:
                    text = str(option)
                    known_identifier = (text in identifiers or text.startswith("Valorant ") or
                                        bool(re.fullmatch(r"[A-Z]|F\d+|COM\d+|\d+(?:x)?|\d+(?:\.\d+){3}", text)))
                    if not known_identifier:
                        with self.subTest(module=module.name, setting=setting.name, value=text):
                            self.assertRegex(option_label(text, True), r"[\u4e00-\u9fff]")

    def test_no_registry_or_persisted_id_is_modified_by_display(self):
        before = copy.deepcopy(snapshot(self.registry))
        for module in self.registry.modules:
            module_label(module.name, True)
            for setting in module.settings:
                setting_label(setting.name, True)
                description_label(setting.description, True)
                for option in setting.options:
                    option_label(option, True)
                    action_label(option, True)
        self.assertEqual(snapshot(self.registry), before)

    def test_english_and_unknown_text_remain_byte_for_byte(self):
        for value in ("Bind X", "1x", "0x0310", "X", "192.168.12.1", "COM3",
                      "custom/Auto Pilot.json", "Untitled_Profile-v2", "device: speed=42; x=2"):
            self.assertEqual(setting_label(value, False), value)
            self.assertEqual(option_label(value, False), value)
            self.assertEqual(toast_text(value, False), value)
            if value != "Bind X":
                self.assertEqual(option_label(value, True), value)
        self.assertEqual(setting_label("Bind X", True), "绑定 X")
        self.assertEqual(option_label("Native 0x0310", True), "原生 0x0310")
        self.assertEqual(description_label("An unknown driver error: 0x0310", True), "An unknown driver error: 0x0310")

    def test_templates_translate_before_inserting_user_data(self):
        profile = "Auto {Field} / USB X"
        self.assertEqual(ui_text("PROFILE  {profile}", True, profile=profile), "配置  " + profile)
        self.assertEqual(ui_text("PROFILE  {profile}", False, profile=profile), "PROFILE  " + profile)
        self.assertEqual(ui_text("未知 {source}", True, source="0x0310"), "未知 0x0310")

    def test_common_messages_and_errors_translate_without_losing_details(self):
        for text, expected in (
            ("ArmorAutoAim enabled", "装甲自瞄 已启用"),
            ("ArmorAutoAim 已关闭", "装甲自瞄 已关闭"),
            ("E-STOP ACTIVE", "急停已触发"),
            ("E-STOP ACTIVE (USB -42)", "急停已触发（USB -42）"),
            ("Sound import failed: /tmp/Auto X.wav", "音效导入失败：/tmp/Auto X.wav"),
            ("MC slot 9: HeroLob", "功能栏 9：英雄吊射"),
            ("MC slot 2 is empty", "功能栏 2 为空"),
            ("BLOCKED: telemetry stale, yaw not calibrated", "受阻：遥测已超时、偏航未标定"),
            ("Invalid configuration value: RobotLink.DT7 Port", "配置值无效：RobotLink.DT7 Port"),
            ("Hero lob yaw calibrated -2.50 deg", "英雄吊射偏航已标定：-2.50 度"),
        ):
            with self.subTest(message=text):
                self.assertEqual(toast_text(text, True), expected)
                self.assertEqual(toast_text(text, False), text)
        self.assertEqual(toast_text("Unknown error: USB 0x0310\npath=Auto X", True), "Unknown error: USB 0x0310\npath=Auto X")

    def test_literal_english_toasts_have_explicit_display_translations(self):
        root = Path(__file__).resolve().parents[1] / "rm_client" / "ui"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "toast" and node.args):
                    continue
                arg = node.args[0]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                message = arg.value
                if re.search(r"[A-Za-z]", message) and not re.search(r"[\u4e00-\u9fff]", message):
                    with self.subTest(path=path.name, line=node.lineno, message=message):
                        self.assertNotEqual(toast_text(message, True), message)

    def test_jello_render_uses_chinese_labels_but_original_hotspot_payloads(self):
        host = DisplayHost()
        renderer = JelloClickGuiRenderer(Path("/nonexistent-assets"))
        state = JelloClickGuiState()
        commands = renderer.build(host, state, 1280, 720, 1.0)
        text = [c.text for c in commands.texts]
        self.assertIn("战斗", text)
        self.assertIn("装甲自瞄", text)
        self.assertTrue(any(h.action == "jello_module_press" and h.payload == "ArmorAutoAim" for h in host.hotspots))
        self.assertNotIn("ArmorAutoAim", text)
        host.registry.get("ClickGui").get_setting("Language").value = LANGUAGE_ENGLISH
        text = [c.text for c in renderer.build(host, state, 1280, 720, 1.0).texts]
        self.assertIn("Combat", text)
        self.assertIn("ArmorAutoAim", text)

    def test_jello_hint_and_qualification_action_display_are_localized(self):
        host = DisplayHost()
        renderer = JelloClickGuiRenderer(Path("/nonexistent-assets"))
        state = JelloClickGuiState()
        host.hover_point = QPointF(5, 5)
        setting = host.registry.get("HeroLob").get_setting("Mock Output")
        renderer._label(host, state, ClickGuiDrawList(), setting, QRectF(0, 0, 200, 30), QRectF(0, 0, 500, 500), 1.0, 1.0)
        self.assertEqual(state.hovered_setting_description, DESCRIPTION_ZH[setting.description])
        module = host.registry.get("MatchReadiness")
        setting = module.get_setting("Qualification Actions")
        state.expanded_action_group = f"{module.name}:{setting.name}"
        state.old_group_animation.reset(1.0)
        commands = ClickGuiDrawList()
        renderer._build_setting(host, state, commands, module, setting, QRectF(0, 0, 500, 1400), QRectF(0, 0, 500, 1400), 1.0, 1.0)
        actions = [h for h in host.hotspots if h.action == "config_action"]
        self.assertEqual([h.payload for h in actions], setting.options)
        labels = [c.text for c in commands.texts]
        self.assertTrue(any("资格" in label for label in labels))
        self.assertFalse(any("New Qualification Session" == label for label in labels))

    def test_import_dialogs_localize_titles_without_changing_paths_or_filters(self):
        from rm_client.ui.polar.mixins.persistence_mixin import PersistenceMixin
        for language in (LANGUAGE_CHINESE, LANGUAGE_ENGLISH):
            host = NS(registry=build_default_registry(), ui_audio=NS(custom_dir=Path("/tmp/Auto X")),
                      paths=NS(asset=lambda key: Path("/tmp/User Wallpapers")))
            host.registry.get("ClickGui").get_setting("Language").value = language
            with patch("rm_client.ui.polar.mixins.persistence_mixin.QFileDialog.getOpenFileName", return_value=("", "")) as dialog:
                PersistenceMixin.import_custom_ui_sound(host, "enable")
                _, title, directory, filters = dialog.call_args.args
                self.assertEqual(title, "导入开启音效" if language == LANGUAGE_CHINESE else "Import enable sound")
                self.assertEqual(Path(directory), host.ui_audio.custom_dir)
                self.assertIn("(*.wav *.ogg *.mp3 *.flac *.m4a *.aac)", filters)
                PersistenceMixin.import_wallpaper_from_dialog(host)
                _, title, directory, filters = dialog.call_args.args
                self.assertEqual(title, "导入启动壁纸" if language == LANGUAGE_CHINESE else "Import startup wallpaper")
                self.assertEqual(Path(directory), host.paths.asset("wallpapers/user"))
                self.assertIn("(*.mp4 *.webm *.mov *.avi *.mkv *.gif *.png *.jpg *.jpeg)", filters)

    def test_qualification_dialog_cancel_is_localized_and_has_no_side_effect(self):
        from rm_client.ui.polar.mixins.config_actions_mixin import ConfigActionsMixin
        host = ConfigActionsMixin()
        host.registry, host.backend, host.toast = build_default_registry(), Mock(), Mock()
        host.update_backend_readiness, host.frame_scheduler = Mock(), Mock()
        with patch("rm_client.ui.polar.mixins.config_actions_mixin.QInputDialog.getText", return_value=("", False)) as dialog:
            ConfigActionsMixin.handle_qualification_action(host, "New Qualification Session")
            self.assertEqual(dialog.call_args.args[1:], ("现场资格检查", "操作员姓名："))
        host.backend.qualification.new_session.assert_not_called()
        host.backend.lock_control.assert_not_called()

    def test_safety_dialog_preserves_literal_confirmation_phrase(self):
        from rm_client.ui.polar.mixins.config_actions_mixin import ConfigActionsMixin
        for phrase, passed in (("已观察台架", False), ("", False), ("OBSERVED ON FIXTURE", True)):
            host = ConfigActionsMixin()
            host.registry, host.backend, host.toast = build_default_registry(), Mock(), Mock()
            host.update_backend_readiness, host.frame_scheduler = Mock(), Mock()
            with patch("rm_client.ui.polar.mixins.config_actions_mixin.QInputDialog.getText", side_effect=[("Observer", True), (phrase, True)]) as dialog:
                ConfigActionsMixin.handle_qualification_action(host, "Review PRM1 Spin Schema")
                prompt = dialog.call_args.args[2]
                self.assertIn("英文确认口令 OBSERVED ON FIXTURE", prompt)
                # No default text is prefilled, especially not the safety token.
                self.assertEqual(len(dialog.call_args.args), 3)
                self.assertEqual(dialog.call_args.kwargs, {})
            self.assertEqual(host.backend.qualification.record_supervised_prm1.call_args.args, ("prm1_spin_schema_review", passed))
        self.assertIn("ZERO OBSERVED", ui_text("After physically observing neutral output, type ZERO OBSERVED:", True))

    def test_escape_menu_follows_language_without_changing_choice_order(self):
        host = DisplayHost()
        self.assertEqual(host.escape_menu_choices(), ("继续", "设置", "返回启动页", "退出客户端"))
        host._escape_menu_confirm_exit = True
        self.assertEqual(host.escape_menu_choices(), ("取消", "退出客户端"))
        from rm_client.ui.polar.mixins import escape_menu_mixin as menu
        image = QImage(1000, 620, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            with patch.object(menu, "_menu_text", wraps=menu._menu_text) as labels:
                host.draw_escape_menu(painter, 1000, 620)
                texts = [call.args[-1] for call in labels.call_args_list]
        finally:
            painter.end()
        self.assertIn("退出客户端？", texts)
        self.assertIn("退出前将锁止控制输出", texts)
        self.assertIn("Esc 返回　↑ ↓ 选择　Enter 确认", texts)
        host.registry.get("ClickGui").get_setting("Language").value = LANGUAGE_ENGLISH
        self.assertEqual(host.escape_menu_choices(), ("Cancel", "Exit client"))


if __name__ == "__main__":
    unittest.main()
