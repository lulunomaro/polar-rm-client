"""设置业务使用的窄表现层接口，按本次启动的 UI 选择原有控件。"""
from .ui_theme import is_jello

if not is_jello():
    from .endfield.settings.shell import EndfieldSettingsDialog as SettingsDialogBase
    from .endfield.settings.shell import apply_settings_widgets as apply_widgets
    from .endfield.settings.widgets import (
        EndfieldCheckBox as CheckBox, EndfieldComboBox as ComboBox,
        SettingsRow, SettingsTabBar, add_setting_row,
    )
    from .endfield.settings.scroll import EndfieldScrollArea as ScrollArea
else:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel, QTabBar as SettingsTabBar, QWidget,
    )
    from .jello_scroll import JelloScrollArea as ScrollArea
    from .jello_widgets import (
        JelloDialog, JelloCheckBox as CheckBox, JelloComboBox as ComboBox,
        apply_jello_widgets as apply_widgets,
    )

    class SettingsRow(QWidget):
        def __init__(self, label, control):
            super().__init__()
            row = QHBoxLayout(self)
            row.setContentsMargins(6, 8, 6, 8)
            row.addWidget(label, 1)
            row.addWidget(control, 1)

    def add_setting_row(form, label, control):
        row = SettingsRow(QLabel(label) if isinstance(label, str) else label, control)
        form.addRow(row)
        return row

    class SettingsDialogBase(JelloDialog):
        """复用 Jello 白色弹性壳，补齐共享设置工厂需要的布局接口。"""
        def __init__(self, parent=None):
            super().__init__(parent)
            self._timer = self._transition_timer
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._enter_pending = False

        def install_header(self):
            row = QHBoxLayout()
            row.addWidget(self.heading)
            row.addStretch()
            self.close_button.setText("×")
            self.close_button.setIcon(QIcon())
            row.addWidget(self.close_button)
            self.panel.layout().insertLayout(0, row)

        def apply_layout_scale(self):
            apply_widgets(self)

        def animate_page(self, _index):
            self.update()
