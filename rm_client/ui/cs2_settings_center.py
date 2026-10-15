"""CS2-style settings navigation backed by actual RM client settings.

The installed game's settings.xml is a read-only layout reference, not executable
code. No Valve ConVars or files are modified. Vehicle authorization switches are
deliberately excluded: a preferences page must never act as an Arm shortcut.
"""
from __future__ import annotations

import math
from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from ..model import CONTROL_BIND_OPTIONS, MODULE_BIND_OPTIONS, CONTROL_RESERVED_KEYS, CONTROL_SLOT_ORDER, build_default_registry, Setting
from .jello_locale import module_label, option_label, setting_label
from .jello_widgets import JelloDialog, JelloCheckBox as QCheckBox, JelloComboBox as QComboBox, apply_jello_widgets, jello_font, use_jello_font_book
from .settings_preparation import SettingsBuildQueue


# None means all scalar preferences, never action/action_group controls.
PAGES = {
    "音频": (
        ("AudioFeedback", None, True),
        ("EliminationFX", ("Sound Enabled", "Sound Volume"), False),
        ("HUD", ("Jello List Sound",), False),
    ),
    "游戏": (
        ("MatchMode", None, False), ("RobotProfile", None, False),
        ("AllianceProfile", None, False), ("MatchChat", None, True),
        ("EconomyPanel", ("Shop Layout", "CS2 Feedback", "Confirm", "Hold Seconds"), False),
        ("QuickActionWheel", None, True),
    ),
    "键盘 / 鼠标": (
        ("RobotLink", ("Mouse Sensitivity", "Invert Mouse Y", "Mouse Wheel Scale",
                       *(f"Bind {slot}" for slot in CONTROL_SLOT_ORDER)), False),
    ),
    "界面 / HUD": (
        ("ClickGui", ("Scale", "Language", "Save Layout"), False),
        ("HUD", None, True), ("UIScale", None, True),
        ("TopMatchBar", None, True), ("MinecraftCombatHUD", None, False),
        ("TacticalMap", None, True), ("CombatTargetHUD", None, True),
        ("EliminationFX", ("Preset", "Element Visibility", "Scale", "Responsive Scale",
                           "Anchor Policy", "X Offset", "Y Offset", "Kill Feed", "Card Icons"), True),
    ),
}
_EXTRA_LABELS = {"Mouse Sensitivity": "鼠标灵敏度", "Invert Mouse Y": "反转鼠标垂直方向",
                 "Mouse Wheel Scale": "滚轮倍率"}


def settings_specs(registry, page):
    """Explicit capability allowlist; cannot expose TX/Mock/Arm gates by accident."""
    for name, allowed, toggle in PAGES[page]:
        module = registry.get(name)
        if module is not None:
            settings = [s for s in module.settings if s.kind in ("bool", "mode", "number")
                        and (allowed is None or s.name in allowed)]
            yield module, settings, toggle


def validate_draft(host, draft, module_binds=None):
    """Validate the entire page before touching any live preference or binding."""
    for (name, key), value in draft.items():
        module = host.registry.get(name)
        setting = module.get_setting(key) if module else None
        if setting is None:
            raise ValueError(f"设置已不存在：{name}/{key}")
        if setting.kind == "bool" and type(value) is not bool:
            raise ValueError("无效开关值：" + key)
        if setting.kind == "mode" and value not in setting.options:
            if not (key.startswith("Bind ") and isinstance(value, str)
                    and value.lower() in {s.lower() for s in setting.options}):
                raise ValueError("无效选项：" + key)
        if setting.kind == "number":
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("无效数值：" + key)
            if not float(setting.min_value) <= value <= float(setting.max_value):
                raise ValueError("数值超出范围：" + key)
    binds = dict(host.control.binds)
    for slot in CONTROL_SLOT_ORDER:
        key = ("RobotLink", f"Bind {slot}")
        if key in draft:
            binds[slot] = draft[key]
    seen = {}
    for slot, physical in binds.items():
        low = physical.lower()
        if low in CONTROL_RESERVED_KEYS:
            raise ValueError(f"{physical} 是界面 / 急停保留键")
        if low != "none":
            if low in seen:
                raise ValueError(f"{physical} 同时绑定了 {seen[low]} 与 {slot}；请先清除其中一个")
            seen[low] = slot
    for name, value in (module_binds or {}).items():
        module = host.registry.get(name)
        if value == module.keybind:
            continue  # Historical priority overlaps are not silently rewritten.
        low = value.lower()
        if low == "none":
            continue
        canonical_map_tab = name == "TacticalMap" and low == "tab"
        if (value not in MODULE_BIND_OPTIONS and not canonical_map_tab) or (low in CONTROL_RESERVED_KEYS | {"escape"} and not canonical_map_tab):
            raise ValueError(f"{value} 是保留键或不是可绑定按键")
        if low in seen:
            raise ValueError(f"{value} 已被驾驶协议槽 {seen[low]} 使用")
        for other in host.registry.modules:
            if other.name != name and (module_binds or {}).get(other.name, other.keybind).lower() == low:
                raise ValueError(f"{value} 已被模块 {module_label(other.name, True)} 使用")
    return binds


def apply_registry_draft(host, draft, toggles=None, module_binds=None):
    binds = validate_draft(host, draft, module_binds)
    changed = [(host.registry.get(name).get_setting(key), value)
               for (name, key), value in draft.items()
               if host.registry.get(name).get_setting(key).value != value]
    # Apply a valid binding permutation atomically, not slot-by-slot rejection.
    # Neutralize old held input first; never restore or arm after preferences.
    host.backend.lock_control("settings applied")
    host.control.release_all(host.state, host.registry)
    host.control.binds = binds
    for setting, value in changed:
        setting.value = value
    for setting, _ in changed:
        host.after_setting_changed(setting)
    for name, value in (toggles or {}).items():
        module = host.registry.get(name)
        if module.enabled != value:
            host.registry.toggle_module(module, source="settings")
    for name, value in (module_binds or {}).items():
        host.registry.get(name).keybind = value
    host.invalidate_gui_cache()
    host.invalidate_transient_overlay_scene()
    host.frame_scheduler.force()
    host.auto_save_config()


class BindingControl(QWidget):
    """Click a key box then press a physical key; staging never sends input."""
    def __init__(self, host, value, parent=None, *, manager=None, binding_id=None):
        super().__init__(parent)
        self.host = host
        self.value = str(value)
        self.manager, self.binding_id = manager, binding_id
        row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0)
        self.button = QPushButton(self.value); row.addWidget(self.button)
        self.clear = QPushButton("清除"); row.addWidget(self.clear)
        self.button.clicked.connect(self.capture)
        self.clear.clicked.connect(lambda: self.set_value("None"))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.capturing = False

    def set_value(self, value):
        self.value = str(value); self.capturing = False
        self.button.setText(self.value)

    def capture(self):
        if self.manager is not None:
            from .jello_keybinds import open_keyboard_manager
            open_keyboard_manager(self.host, self.manager, self.binding_id)
            return
        self.capturing = True; self.button.setText("请按键 · Esc 清除")
        self.setFocus()

    def keyPressEvent(self, event):
        if self.capturing:
            if event.isAutoRepeat():
                event.accept(); return
            name = "None" if event.key() == Qt.Key.Key_Escape else self.host.key_name(event)
            normalized = next((key for key in CONTROL_BIND_OPTIONS if key.lower() == name.lower()), None)
            if normalized is not None:
                self.set_value(normalized)
            else:
                self.button.setText("保留键 / 不支持 · 请重试")
            event.accept(); return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        if self.capturing:
            self.set_value(self.value)
        super().focusOutEvent(event)


class NumberControl(QWidget):
    def __init__(self, setting, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(float(setting.min_value), float(setting.max_value))
        self.spin.setSingleStep(float(setting.step))
        self.spin.setDecimals(0 if setting.step >= 1 else 2)
        self.spin.setSuffix(setting.unit)
        self.spin.setValue(float(setting.value))
        self.spin.setMinimumWidth(110)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        row.addWidget(self.slider, 1); row.addWidget(self.spin)
        low, high = self.spin.minimum(), self.spin.maximum()
        def update_slider(value):
            self.slider.blockSignals(True)
            self.slider.setValue(round(1000 * (value - low) / max(1e-9, high - low)))
            self.slider.blockSignals(False)
        def update_spin(value):
            raw = low + (high - low) * value / 1000
            step = float(setting.step)
            self.spin.setValue(max(low, min(high, round(raw / step) * step)))
        self.spin.valueChanged.connect(update_slider)
        self.slider.valueChanged.connect(update_spin)
        update_slider(self.spin.value())


class RegistrySettingsPage(QScrollArea):
    def __init__(self, host, page, defaults, *, deferred=False):
        super().__init__()
        self.host, self.page, self.defaults = host, page, defaults
        self.controls, self.toggle_controls, self.module_bind_controls = {}, {}, {}
        self.audio_controls = {}
        self.search_rows, self.groups = [], []
        self.setWidgetResizable(True)
        self.content = QWidget(); self.setWidget(self.content)
        root = QVBoxLayout(self.content); root.setSpacing(22)
        self.loading = QLabel("正在准备设置…")
        root.addWidget(self.loading)
        self.content.setEnabled(False)
        self.builder = SettingsBuildQueue(self, self._build_rows())
        self.builder.finished.connect(self._finish_build)
        if not deferred:
            for _ in self.builder.steps:
                pass
            self.builder.ready = True
            self._finish_build()

    def _finish_build(self):
        if self.builder.error:
            self.loading.setText("设置加载失败：" + self.builder.error)
        else:
            self.loading.hide()
            self.content.setEnabled(True)
        self.filter(getattr(self, "_query", ""))

    def _build_rows(self):
        host, page = self.host, self.page
        root = self.content.layout()
        if page == "音频":
            from ..audio_preferences import current_audio
            from PySide6.QtMultimedia import QMediaDevices
            prefs = current_audio()
            box = QGroupBox("音频输出"); apply_jello_widgets(box, stylesheet=False); root.addWidget(box)
            form = QFormLayout(box); form.setSpacing(17); self.groups.append((box, []))
            yield
            device = QComboBox(); device.addItem("系统默认设备", "default")
            for item in QMediaDevices.audioOutputs(): device.addItem(item.description(), bytes(item.id()).hex())
            if device.findData(prefs.device_id) < 0:
                device.addItem("已离线的设备（当前回退系统默认）", prefs.device_id)
            device.setCurrentIndex(device.findData(prefs.device_id))
            volume = NumberControl(Setting("Master", "number", prefs.master_volume, 0, 100, 1, unit="%"))
            mute = QCheckBox("切换至其他窗口时静音"); mute.setChecked(prefs.mute_unfocused)
            self.audio_controls = {"device_id": device, "master_volume": volume, "mute_unfocused": mute}
            self.add_row(form, "Audio", "音频输出设备", device, "output device")
            yield
            self.add_row(form, "Audio", "主音量", volume, "master volume")
            yield
            self.add_row(form, "Audio", "后台静音", mute, "mute unfocused")
            yield
        for module, settings, toggle in settings_specs(host.registry, page):
            title = module_label(module.name, True)
            if module.name == "RobotLink": title = "鼠标设置与驾驶键位"
            box = QGroupBox(title); apply_jello_widgets(box, stylesheet=False); root.addWidget(box)
            form = QFormLayout(box); form.setSpacing(17)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self.groups.append((box, []))
            yield
            if toggle:
                widget = QCheckBox("已启用"); widget.setChecked(module.enabled)
                self.toggle_controls[module.name] = widget
                self.add_row(form, module.name, "模块开关", widget)
                yield
            for setting in settings:
                if setting.name.startswith("Bind "):
                    widget = BindingControl(host, setting.value, manager=self, binding_id=(module.name,setting.name))
                elif setting.kind == "bool":
                    widget = QCheckBox("已启用"); widget.setChecked(bool(setting.value))
                elif setting.kind == "mode":
                    widget = QComboBox()
                    for option in setting.options:
                        widget.addItem(option_label(option, True), option)
                    widget.setCurrentIndex(max(0, widget.findData(setting.value)))
                else:
                    widget = NumberControl(setting)
                widget.setObjectName(module.name + "." + setting.name)
                widget.setToolTip(setting.description or f"{title} · {setting_label(setting.name, True)}")
                self.controls[(module.name, setting.name)] = widget
                self.add_row(form, module.name, _EXTRA_LABELS.get(setting.name, setting_label(setting.name, True)), widget, setting.name)
                yield
        if page == "键盘 / 鼠标":
            from .jello_keybinds import open_keyboard_manager
            keyboard_button = QPushButton("Open Keybind Manager / 打开键盘绑定管理器")
            keyboard_button.clicked.connect(lambda: open_keyboard_manager(host,self))
            apply_jello_widgets(keyboard_button, stylesheet=False)
            root.insertWidget(0,keyboard_button)
            note = QLabel("驾驶键发送的仍是官方协议槽位；不会改固件。鼠标倍率只作用于本客户端输入，不修改NUC自瞄或DT7原始输入。\n"
                          "F12急停、RightShift/Tab等界面保留键不能改绑。已有快捷键与驾驶键的历史重叠保留；新绑定会检查冲突。")
            apply_jello_widgets(note, stylesheet=False)
            note.setWordWrap(True); root.insertWidget(0, note)
            box = QGroupBox("模块快捷键"); apply_jello_widgets(box, stylesheet=False); root.addWidget(box)
            form = QFormLayout(box); form.setSpacing(15); self.groups.append((box, []))
            yield
            for module in host.registry.modules:
                if module.name in ("ClickGui", "MatchReadiness"): continue
                widget = BindingControl(host, module.keybind, manager=self, binding_id=("module",module.name))
                self.module_bind_controls[module.name] = widget
                self.add_row(form, module.name, module_label(module.name, True), widget, "keybind")
                yield
        root.addStretch()
        yield

    def add_row(self, form, module, title, widget, original=""):
        label = QLabel(title); label.setMinimumWidth(240)
        apply_jello_widgets(label, stylesheet=False)
        apply_jello_widgets(widget, stylesheet=False)
        form.addRow(label, widget)
        record = (label, widget, f"{module} {title} {original}".casefold())
        self.search_rows.append(record); self.groups[-1][1].append(record)

    def filter(self, query):
        query = query.strip().casefold()
        self._query = query
        for label, widget, text in self.search_rows:
            visible = not query or query in text
            if label.isHidden() == visible: label.setVisible(visible)
            if widget.isHidden() == visible: widget.setVisible(visible)
        for group, rows in self.groups:
            visible = not query or any(query in text for _, _, text in rows)
            if group.isHidden() == visible: group.setVisible(visible)

    def apply_draft(self):
        if not self.builder.ready:
            raise ValueError("请等待当前页完成加载")
        draft = {}
        for key, widget in self.controls.items():
            if isinstance(widget, BindingControl): value = widget.value
            elif isinstance(widget, QCheckBox): value = widget.isChecked()
            elif isinstance(widget, QComboBox): value = widget.currentData()
            else: value = widget.spin.value()
            draft[key] = value
        audio = None
        if self.audio_controls:
            from ..audio_preferences import validate_audio
            audio = validate_audio({"device_id": self.audio_controls["device_id"].currentData(),
                                    "master_volume": int(self.audio_controls["master_volume"].spin.value()),
                                    "mute_unfocused": self.audio_controls["mute_unfocused"].isChecked()})
        apply_registry_draft(self.host, draft,
                             {name: w.isChecked() for name, w in self.toggle_controls.items()},
                             {name: w.value for name, w in self.module_bind_controls.items()})
        if audio is not None:
            from ..audio_preferences import save_audio
            save_audio(audio)

    def reset_draft(self):
        if not self.builder.ready:
            return
        if self.audio_controls:
            self.audio_controls["device_id"].setCurrentIndex(0)
            self.audio_controls["master_volume"].spin.setValue(100)
            self.audio_controls["mute_unfocused"].setChecked(False)
        for (name, key), widget in self.controls.items():
            value = self.defaults.get(name).get_setting(key).value
            if isinstance(widget, BindingControl): widget.set_value(value)
            elif isinstance(widget, QCheckBox): widget.setChecked(value)
            elif isinstance(widget, QComboBox): widget.setCurrentIndex(widget.findData(value))
            else: widget.spin.setValue(value)
        for name, widget in self.toggle_controls.items(): widget.setChecked(self.defaults.get(name).enabled)
        for name, widget in self.module_bind_controls.items(): widget.set_value(self.defaults.get(name).keybind)


def install_estop_shortcut(dialog, host):
    # An ordinary WindowShortcut misses nested modal file/import dialogs.
    class EstopFilter(QObject):
        def eventFilter(self, watched, event):
            # Most events are layout/style/paint traffic. Do not enter Qt
            # visibility checks or import Shiboken for every constructed child.
            if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                return False
            from shiboken6 import isValid
            if event.key() == Qt.Key.Key_F12 and isValid(dialog) and dialog.isVisible():
                if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    host.control.reset_inputs()
                    ok, reason = host.backend.trigger_estop()
                    host.control.clear_pending()
                    host.toast("E-STOP ACTIVE" if ok else f"E-STOP ACTIVE ({reason})")
                event.accept(); return True
            return super().eventFilter(watched, event)
    filter_ = EstopFilter(dialog)
    app = QApplication.instance(); app.installEventFilter(filter_)
    dialog.finished.connect(lambda _result: app.removeEventFilter(filter_))
    return filter_


class CrosshairSettingsPage(QScrollArea):
    """Defer editor creation/style/attachment into separate event-loop turns."""
    def __init__(self, host):
        super().__init__()
        self.host, self.editor = host, None
        self.setWidgetResizable(True)
        self.setObjectName("CrosshairSettingsScroll")
        self.loading = QLabel("正在准备准星设置…")
        self.setWidget(self.loading)
        self.builder = SettingsBuildQueue(self, self._build())
        self.builder.finished.connect(self._finish_build)

    def _build(self):
        from ..cs2_crosshair import build_crosshair_editor
        self.editor = build_crosshair_editor(self.host)
        self.editor.hide()
        # The factory initially parents to the host; tie temporary lifetime
        # to this page even if it is closed before the attachment step.
        self.destroyed.connect(self.editor.deleteLater)
        yield True
        apply_jello_widgets(self.editor)
        yield True
        self.setWidget(self.editor)
        self.editor.show()
        yield True

    def _finish_build(self):
        if self.builder.error:
            self.loading.setText("设置加载失败：" + self.builder.error)

    def apply_draft(self):
        if not self.builder.ready:
            raise ValueError("请等待当前页完成加载")
        self.editor.apply()

    def reset_draft(self):
        if self.builder.ready:
            from ..cs2_crosshair import DEFAULTS
            self.editor.load_profile(DEFAULTS)


def build_settings_center(host):
    """Non-modal construction for visual/regression tests; open owns input lock."""
    from .cs2_video_settings import open_video_settings
    use_jello_font_book(getattr(host, "fonts", None))
    dialog = JelloDialog(host); dialog.setWindowTitle("POLAR · Jello 设置")
    dialog.setObjectName("JelloSettingsCenter")
    screen = host.screen()
    available = screen.availableGeometry() if screen else host.rect()
    dialog.resize(min(1280,available.width()-30,max(720,host.width()-20)),
                  min(840,available.height()-40,max(540,host.height()-20)))
    dialog.setMinimumSize(720, 540)
    center = host.mapToGlobal(host.rect().center())
    dialog.move(max(available.left(), min(center.x() - dialog.width() // 2, available.right() - dialog.width())),
                max(available.top(), min(center.y() - dialog.height() // 2, available.bottom() - dialog.height())))
    root = dialog.panel.layout()
    top = QHBoxLayout(); root.addLayout(top)
    title = QLabel("Jello · 设置"); title.setFont(jello_font(36,True)); title.setStyleSheet("font-size:36px;font-weight:500;color:#666666;"); top.addWidget(title)
    top.addStretch()
    search = QLineEdit(); search.setPlaceholderText("搜索鼠标、键位、声音、HUD…")
    search.setMaximumWidth(370); top.addWidget(search)
    tabs = QTabWidget(); tabs.setObjectName("SettingsTabs"); root.addWidget(tabs, 1)
    video = open_video_settings(host, embedded=True); tabs.addTab(video, "视频")
    defaults = build_default_registry()
    # Lightweight shells preserve tab identity and drafts. Only the selected
    # page builds, after the entrance animation, in bounded GUI-thread slices.
    pages = {}
    for page in PAGES:
        widget = RegistrySettingsPage(host, page, defaults, deferred=True)
        pages[page] = widget; tabs.addTab(widget, page)
    crosshair_scroll = CrosshairSettingsPage(host)
    tabs.insertTab(4, crosshair_scroll, "准星")
    deferred_pages = (*pages.values(), crosshair_scroll)
    search.textChanged.connect(lambda text: [page.filter(text) for page in pages.values()])
    status = QLabel("F12 急停始终有效 · 设置不暂停裁判监测 · 返回后不会自动恢复操控")
    status.setWordWrap(True); root.addWidget(status)
    row = QHBoxLayout(); root.addLayout(row)
    reset = QPushButton("恢复当前页默认值"); row.addWidget(reset); row.addStretch()
    apply = QPushButton("应用当前页"); back = QPushButton("返回")
    apply.setProperty("primary",True)
    row.addWidget(apply); row.addWidget(back)
    def apply_current():
        widget = tabs.currentWidget()
        try:
            widget.apply_draft()
            status.setText("当前页已应用；操控保持锁定，重新驾驶前需人工确认。视频重启项另有标注。")
        except (OSError, ValueError, TypeError) as exc:
            status.setText("未完整应用：" + str(exc))
    def reset_current():
        widget = tabs.currentWidget()
        if QMessageBox.question(dialog, "恢复默认", "只恢复当前页的编辑值，点击应用后才生效。继续？") == QMessageBox.StandardButton.Yes:
            widget.reset_draft()
    apply.clicked.connect(apply_current); reset.clicked.connect(reset_current); back.clicked.connect(dialog.accept)
    def current_changed(_index=None):
        current = tabs.currentWidget()
        for page in deferred_pages:
            page.builder.set_active(page is current)
        builder = getattr(current, "builder", None)
        ready = builder is None or builder.ready
        apply.setEnabled(ready); reset.setEnabled(ready)
    tabs.currentChanged.connect(current_changed)
    for page in deferred_pages:
        page.builder.finished.connect(current_changed)
        dialog.finished.connect(page.builder.cancel)
    dialog.tabs, dialog.pages, dialog.crosshair = tabs, pages, crosshair_scroll

    dialog.search, dialog.status, dialog.apply_button = search, status, apply
    apply_jello_widgets(dialog)
    dialog.estop_shortcut = install_estop_shortcut(dialog, host)
    return dialog


def open_settings_center(host):
    host.backend.lock_control("settings opened")
    host.control.release_all(host.state, host.registry)
    host._control_mouse_point = None
    dialog = build_settings_center(host)
    try:
        dialog.exec()
    finally:
        host.control.reset_inputs(); host.control.clear_pending()
        host._control_mouse_point = None
        host.invalidate_transient_overlay_scene()
        host.frame_scheduler.force()
        dialog.deleteLater()
