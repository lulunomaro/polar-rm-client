"""设置行、原图标导航和胶囊控件，不持有业务设置值。"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QRectF, QRect, QSize, QTimer, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QTabBar, QWidget, QSizePolicy,
    QStyledItemDelegate, QStyle,
)

from .resources import texture, motion, source


def draw_image(painter, name, rect, opacity=1.0):
    image = texture(name)
    if image.isNull():
        return
    painter.save()
    painter.setOpacity(painter.opacity() * opacity)
    painter.drawImage(QRectF(rect), image)
    painter.restore()


def draw_sliced(painter, name, rect, scale):
    """按原Sprite的四侧Border九切片，保持细边框与材质灰度。"""
    image = texture(name)
    left,top,right,bottom = source()["slices"][name]
    sx = (0,left,image.width()-right,image.width())
    sy = (0,top,image.height()-bottom,image.height())
    dx = (rect.left(),rect.left()+left*scale,rect.right()-right*scale,rect.right())
    dy = (rect.top(),rect.top()+top*scale,rect.bottom()-bottom*scale,rect.bottom())
    for y in range(3):
        for x in range(3):
            painter.drawImage(QRectF(dx[x],dy[y],dx[x+1]-dx[x],dy[y+1]-dy[y]),image,
                              QRectF(sx[x],sy[y],sx[x+1]-sx[x],sy[y+1]-sy[y]))


class SettingsRow(QWidget):
    """本体 GameSettingItemCellV2 的三层背景及右侧控件区域。"""
    def __init__(self, label, control, parent=None):
        super().__init__(parent)
        self.setObjectName("EndfieldSettingRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.label, self.control = label, control
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setMinimumWidth(96)
        label.setWordWrap(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(28, 7, 15, 7)
        row.addWidget(label, 1)
        row.addWidget(control)
        self.setMinimumHeight(56)
        control.setMinimumWidth(140)
        control.setMaximumWidth(500)

    def apply_scale(self, scale):
        """由外层窗口尺寸决定列宽，禁止在行resize回调中反向修改布局约束。"""
        self.setMinimumHeight(max(38, round((54 if self.property("compact") else 76)*scale)))
        self.control.setFixedWidth(max(140, round(400*scale)))
        if isinstance(self.control, (QComboBox, QCheckBox)):
            self.control.setMinimumHeight(max(30, round(50*scale)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        scale = min(self.window().width()/1920,self.window().height()/1080)*.8
        draw_sliced(painter,"row_bg",area,scale)
        if self.underMouse():
            painter.fillRect(area,QColor(255,255,255,12))
        draw_image(painter, "row_left", QRectF(0, 0, min(self.width()*.35, 470), self.height()))
        draw_image(painter, "row_right", QRectF(self.width()*.55, 0, self.width()*.45, self.height()))
        draw_image(painter, "row_edge", QRectF(0, 0, min(100, self.width()*.08), self.height()))


def add_setting_row(form, label, control):
    """保留 QFormLayout 和原控件身份，视觉行作为跨列容器加入。"""
    if isinstance(label, str):
        label = QLabel(label)
    row = SettingsRow(label, control)
    form.setFieldGrowthPolicy(form.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(form.RowWrapPolicy.DontWrapRows)
    form.addRow(row)
    window = row.window()
    row.apply_scale(min(window.width()/1920,window.height()/1080))
    return row


class EndfieldComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumHeight(34)
        self.setItemDelegate(_OptionDelegate(self))
        self.setMaxVisibleItems(6)
        self._popup_timer = QTimer(self)
        self._popup_timer.setInterval(8)
        self._popup_timer.timeout.connect(self._animate_popup)
        self._popup = None
        self._closing = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.setBrush(QColor("#fff500") if self.underMouse() and self.isEnabled() else QColor("#eeeded"))
        if not self.isEnabled():
            p.setOpacity(.4)
        p.drawRoundedRect(r, r.height()/2, r.height()/2)
        p.setPen(QColor("#444444"))
        p.setFont(self.font())
        label = self.currentText() if self.currentIndex() >= 0 else self.placeholderText()
        text = self.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, self.width()-55)
        p.drawText(self.rect().adjusted(16, 0, -35, 0), Qt.AlignmentFlag.AlignVCenter, text)
        x, y = self.width()-22, self.height()/2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#444444"))
        p.drawPolygon(QPolygonF([QPointF(x-5, y-3), QPointF(x+5, y-3), QPointF(x, y+3)]))

    def showPopup(self):
        self._popup_timer.stop()
        self._closing = False
        self._popup = self.view().window()
        self._popup.installEventFilter(self)
        # Qt 原生 QRollEffect 会先显示列表快照，再交给本体从透明展开，造成二次闪现。
        # 只在本次同步调用内屏蔽原生展开，立即恢复应用原有设置。
        native_animation = QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        if native_animation:
            QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        try:
            super().showPopup()
        finally:
            if native_animation:
                QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, True)
        if self._popup.isVisible() and not self._popup_timer.isActive():
            self._begin_popup_animation()

    def eventFilter(self, watched, event):
        if watched is self._popup:
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                    and self._switch_popup_tab(event)):
                return True
            if event.type() == QEvent.Type.Show:
                # Windows 的 Qt 原生展开可能延迟 Show；此时才启动本体动画。
                self._begin_popup_animation()
            elif event.type() == QEvent.Type.Hide:
                self._popup_timer.stop()
                self._closing = False
                self._popup.setWindowOpacity(1)
        return super().eventFilter(watched, event)

    def _switch_popup_tab(self, event):
        """弹窗抓取的分类点击仍归分类栏处理，避免第一次点击只关闭列表。"""
        point = event.globalPosition().toPoint()
        if self._popup.rect().contains(self._popup.mapFromGlobal(point)):
            return False
        window = self.window()
        target = window.childAt(window.mapFromGlobal(point))
        if not isinstance(target, SettingsTabBar) or not target.isEnabled():
            return False
        index = target.tabAt(target.mapFromGlobal(point))
        if index < 0 or not target.isTabEnabled(index):
            return False
        self._popup_timer.stop()
        self._closing = False
        super().hidePopup()
        target.setCurrentIndex(index)
        event.accept()
        return True

    def _begin_popup_animation(self):
        self._popup_rect = QRect(self._popup.geometry())
        self._opens_up = self._popup_rect.center().y() < self.mapToGlobal(self.rect().center()).y()
        self._popup_started = time.perf_counter()
        self._popup.setWindowOpacity(0)
        # Show 事件发生在映射窗口之前，先应用初始高度，避免完整列表先露出一帧。
        self._animate_popup()
        self._popup_timer.start()

    def _animate_popup(self):
        from shiboken6 import isValid
        if not isValid(self._popup) or not self._popup.isVisible():
            self._popup_timer.stop()
            return
        elapsed = time.perf_counter()-self._popup_started
        clip = "dropdownlist_out" if self._closing else "dropdownlist_in"
        alpha = motion().sample(clip, "", "m_Alpha", elapsed, 1)
        height = motion().sample(clip, "", "m_SizeDelta.y", elapsed, 250)/250
        rect = QRect(self._popup_rect)
        rect.setHeight(max(1, round(rect.height()*height)))
        if self._opens_up:
            rect.moveBottom(self._popup_rect.bottom())
        self._popup.setGeometry(rect)
        self._popup.setWindowOpacity(max(0, min(1, alpha)))
        if elapsed >= motion().durations[clip]:
            self._popup_timer.stop()
            if self._closing:
                super().hidePopup()
            else:
                self._popup.setWindowOpacity(1)

    def hidePopup(self):
        from shiboken6 import isValid
        if self._popup is None or not isValid(self._popup) or not self._popup.isVisible():
            self._popup_timer.stop()
            return super().hidePopup()
        if not self._closing:
            self._closing = True
            self._popup_started = time.perf_counter()
            self._popup_timer.start()


class _OptionDelegate(QStyledItemDelegate):
    """使用本体下拉选中深底与浅色列表，避开平台原生彩色标记。"""
    def paint(self, painter, option, index):
        selected = index.row() == self.parent().currentIndex()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.fillRect(option.rect, QColor("#fff500" if hover else "#444444" if selected else "#d3d1cf"))
        painter.setPen(QColor("#eeeeee" if selected and not hover else "#444444"))
        painter.setFont(option.font)
        text = option.fontMetrics.elidedText(str(index.data()), Qt.TextElideMode.ElideRight, option.rect.width()-36)
        painter.drawText(option.rect.adjusted(18,0,-12,0), Qt.AlignmentFlag.AlignVCenter, text)
        if selected:
            painter.fillRect(option.rect.adjusted(3,8,-option.rect.width()+6,-8), QColor("#eeeeee"))
        painter.restore()

    def sizeHint(self, option, index):
        scale = min(self.parent().window().width()/1920, self.parent().window().height()/1080)
        return QSize(200,max(30,round(53*scale)))


class EndfieldCheckBox(QCheckBox):
    """沿用 Qt 布尔信号和键盘行为，选中背景按本体切换轨道移动。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumHeight(36)
        self._changed = 0.0
        self._from = self._target = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._tick)
        self.toggled.connect(self._start)

    def sizeHint(self):
        return QSize(300, 38)

    def hitButton(self, point):
        return self.rect().contains(point)

    def _start(self, checked):
        self._from = self._pose()
        self._target = float(checked)
        self._changed = time.perf_counter() if self.isVisible() else 0
        if self._changed:
            self._timer.start()
        self.update()

    def _pose(self):
        if not self._changed:
            return self._target
        clip = "common_toggle_to_right01" if self._target else "common_toggle_to_left01"
        value = motion().sample(clip, "SelectedBG", "m_AnchorMin.x", time.perf_counter()-self._changed, self._target)
        progress = value if self._target else 1-value
        return self._from+(self._target-self._from)*progress

    def _tick(self):
        self.update()
        if time.perf_counter()-self._changed >= .2:
            self._timer.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#595959"))
        p.drawRoundedRect(r, r.height()/2, r.height()/2)
        offset = self._pose()*r.width()/2
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, r.height()/2, r.height()/2)
        p.setClipPath(path)
        p.setBrush(QColor("#eeeded"))
        p.drawRoundedRect(QRectF(r.x()+offset, r.y(), r.width()/2, r.height()), r.height()/2, r.height()/2)
        p.restore()
        p.setFont(self.font())
        for index, text in enumerate(("关闭", "开启")):
            p.setPen(QColor("#444444" if index == int(self.isChecked()) else "#b9b9b9"))
            p.drawText(QRectF(r.x()+index*r.width()/2, r.y(), r.width()/2, r.height()), Qt.AlignmentFlag.AlignCenter, text)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#fff500"), 2))
            p.drawRoundedRect(r, r.height()/2, r.height()/2)


class SettingsTabBar(QTabBar):
    """本体顶部图标带；标签保留为无障碍名称和悬停提示。"""
    ICONS = ("tab_display", "tab_audio", "tab_game", "tab_keyboard", "tab_crosshair", "tab_hud")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_at = -1.0
        self._timer = QTimer(self)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._tick)
        self.currentChanged.connect(self._select)

    def _select(self, index):
        self._selected_at = time.perf_counter()
        self._timer.start()

    def _tick(self):
        self.update()
        if time.perf_counter()-self._selected_at >= .25:
            self._timer.stop()

    def tabSizeHint(self, index):
        return QSize(max(62, round(self.window().width()/1920*98)), max(54, round(self.window().height()/1080*80)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_image(p, "tabs_bg", self.rect())
        for index in range(self.count()):
            r = QRectF(self.tabRect(index))
            selected = index == self.currentIndex()
            if selected:
                at = time.perf_counter()-self._selected_at
                width = motion().sample("setting_tabcell_selected_in", "Bg1", "m_SizeDelta.x", at, 12)
                alpha = motion().sample("setting_tabcell_selected_in", "Bg1", "m_Alpha", at, 1)
                selected_rect = QRectF(r)
                selected_rect.setWidth(max(0, r.width()*(width+109.72)/121.72))
                p.save()
                p.setClipRect(selected_rect)
                draw_image(p, "tab_selected", r, alpha)
                p.restore()
            elif self.tabAt(self.mapFromGlobal(self.cursor().pos())) == index:
                p.fillRect(r, QColor(255, 255, 255, 25))
            icon = texture(self.ICONS[index])
            if not icon.isNull():
                tinted = icon.copy()
                q = QPainter(tinted)
                q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                q.fillRect(tinted.rect(), QColor("#111111" if selected else "#ffffff"))
                q.end()
                size = r.height()*.45
                p.save()
                if selected:
                    p.setOpacity(motion().sample("setting_tabcell_selected_in", "SelectedIcon", "m_Alpha", time.perf_counter()-self._selected_at, 1))
                p.drawImage(QRectF(r.center().x()-size/2, r.center().y()-size/2, size, size), tinted)
                p.restore()
            if index and not selected:
                p.setPen(QColor(255, 255, 255, 55))
                p.drawLine(r.topLeft()+QPointF(0, r.height()*.36), r.topLeft()+QPointF(0, r.height()*.64))
