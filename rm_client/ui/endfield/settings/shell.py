"""整页设置壳：本体进退、页签切换曲线和自适应灰黑材质。"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, QRectF, QEvent, QCoreApplication, QSize
from PySide6.QtGui import QColor, QPainter, QRadialGradient, QShortcut, QKeySequence, QFontMetrics
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget, QGraphicsOpacityEffect, QGridLayout

from .resources import motion, ui_font, font_family
from .widgets import draw_image, SettingsRow, SettingsTabBar
from .background import capture_backdrop, paint_background
from .geometry import SettingsGeometryGuard


def settings_stylesheet(scale=1):
    px = max(13, round(24*scale))
    # 按字体高度、上下内边距和边框计算胶囊半径，避免大屏时超过按钮高度的一半。
    button_radius = (QFontMetrics(ui_font(px)).height() + 2*(8+1)) // 2
    return f"""
    QWidget {{color:#eeeeee;font-family:"{font_family()}";font-size:{px}px;background:transparent;}}
    QLabel {{background:transparent;border:0;}}
    QLabel[hint="true"] {{font-size:{max(11,round(16*scale))}px;color:#bdbebc;}}
    QTabWidget::pane {{border:0;background:transparent;}}
    QTabWidget::tab-bar {{subcontrol-position:top center;}}
    QTabBar::tab {{color:#dddddd;padding:8px 18px;border:0;}}
    QTabBar::tab:selected {{color:#fff500;}}
    QScrollArea, QScrollArea>QWidget>QWidget {{border:0;background:transparent;}}
    QGroupBox {{border:0;border-top:1px solid #777875;margin-top:30px;padding-top:18px;}}
    QGroupBox::title {{subcontrol-origin:margin;left:0;color:#fff500;padding:0 14px 0 8px;border-left:5px solid #fff500;}}
    QGroupBox#DisplayInformation {{border:0;margin:0;padding:0;}}
    QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit {{border:1px solid #d7d7d7;border-radius:{max(10,round(18*scale))}px;background:#eeeded;color:#444444;padding:5px 14px;}}
    QLineEdit {{background:rgba(255,255,255,16);color:#dddddd;border:1px solid #666666;}}
    QAbstractItemView {{background:#d3d1cf;color:#444444;border:1px solid #999999;outline:0;selection-background-color:#444444;selection-color:#eeeeee;}}
    QAbstractItemView::item {{padding:8px 16px;min-height:24px;}}
    QAbstractItemView::item:hover {{background:#fff500;color:#222222;}}
    QPushButton {{border:1px solid #ffffff;border-radius:{button_radius}px;background:#eeeded;color:#444444;padding:8px 22px;}}
    QPushButton:hover {{background:#fff500;color:#222222;border-color:#fff500;}}
    QPushButton:pressed {{background:#bcbca1;}}
    QPushButton:focus {{border:2px solid #fff500;}}
    QPushButton:disabled {{color:#999999;background:#555555;border-color:#777777;}}
    QPushButton#EndfieldSettingsClose {{padding:0;border:0;border-radius:0;background:transparent;}}
    QPushButton#EndfieldSettingsClose:hover {{background:#fff500;}}
    QDoubleSpinBox,QSpinBox {{background:transparent;color:#eeeeee;border:0;padding:2px;}}
    QDoubleSpinBox::up-button,QDoubleSpinBox::down-button,QSpinBox::up-button,QSpinBox::down-button {{width:0;height:0;border:0;}}
    QSlider::groove:horizontal {{height:5px;background:#6b6b6b;border-radius:2px;}}
    QSlider::sub-page:horizontal {{background:#d0d0cc;border-radius:2px;}}
    QSlider::handle:horizontal {{width:36px;height:14px;margin:-5px 0;background:#f6f6f6;border:1px solid #dadada;border-radius:7px;}}
    QSlider::handle:horizontal:hover {{background:#fff500;}}
    QScrollBar:vertical {{background:#414242;width:3px;border:0;}}
    QScrollBar::handle:vertical {{background:#c6c6c6;min-height:32px;}}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}}
    QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:transparent;}}
    QToolTip {{background:#d3d1cf;color:#333333;border:1px solid #777777;padding:6px;}}
    """


class SettingsSurface(QWidget):
    backdrop = None

    def paintEvent(self, event):
        p = QPainter(self)
        paint_background(p,self.rect(),self.backdrop)


def apply_settings_widgets(widget, scope=None, *, stylesheet=True):
    """兼容原工厂的样式调用，延迟创建的子页也使用同一表现层。"""
    widget.setFont(ui_font(18))
    if stylesheet:
        for child in [widget, *widget.findChildren(QWidget)]:
            child.setStyleSheet("")
        window = widget.window()
        scale = min(window.width()/1920, window.height()/1080)
        if not isinstance(window, EndfieldSettingsDialog) or widget is window:
            widget.setStyleSheet(settings_stylesheet(scale))


class EndfieldSettingsDialog(QDialog):
    """保留 panel/timeline 兼容接口，动画按秒采样本体关键帧。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        if parent is not None:
            from ...fullscreen_composition import FullscreenDialogGuard
            self._fullscreen_guard = FullscreenDialogGuard(parent.window(), self)
        self.panel = SettingsSurface(self)
        self._window_layout = QVBoxLayout(self)
        self._window_layout.setContentsMargins(0,0,0,0)
        self._window_layout.addWidget(self.panel)
        self.panel.setLayout(QVBoxLayout())
        self.timeline = _Timeline()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._advance)
        self._surface = None
        self._resize_surface = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._finish_client_resize)
        self._result = QDialog.DialogCode.Rejected
        self._started = 0.0
        self._page_started = 0.0
        self._page_effect = None
        self.capture_count = 0
        self._enter_pending = False
        self._keys = []
        for key, step in (("Q", -1), ("E", 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda delta=step: self._cycle_tab(delta))
            self._keys.append(shortcut)
        self._geometry_guard = SettingsGeometryGuard(parent.window(),self) if parent is not None else None

    def _cycle_tab(self, delta):
        from PySide6.QtWidgets import QApplication, QLineEdit, QAbstractSpinBox
        if not hasattr(self, "tabs") or isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox)):
            return
        self.tabs.setCurrentIndex((self.tabs.currentIndex()+delta) % self.tabs.count())

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in (Qt.Key.Key_Q, Qt.Key.Key_E):
            from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QAbstractSpinBox
            if isinstance(QApplication.focusWidget(), (QComboBox,QLineEdit,QAbstractSpinBox)):
                event.accept()
                return True
        return super().event(event)

    def install_header(self):
        """顶部使用真实布局，标题、分类和关闭按钮随窗口一起约束。"""
        self.header = QWidget(self.panel)
        layout = QGridLayout(self.header)
        layout.setContentsMargins(0,0,0,0)
        layout.setColumnStretch(1,1)
        self.navigation = SettingsTabBar(self.header)
        for index in range(self.tabs.count()):
            self.navigation.addTab(self.tabs.tabText(index))
            self.navigation.setTabToolTip(index,self.tabs.tabText(index))
        self.navigation.setAccessibleName("设置分类 · Q / E 切换")
        self.tabs.tabBar().hide()
        layout.addWidget(self.heading,0,0)
        layout.addWidget(self.navigation,0,1,Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.close_button,0,2,Qt.AlignmentFlag.AlignRight)
        self.panel.layout().insertWidget(0,self.header)
        self.navigation.currentChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.navigation.setCurrentIndex)
        self.navigation.setCurrentIndex(self.tabs.currentIndex())

    def apply_layout_scale(self):
        """只从窗口边界向内更新尺寸，隐藏子页也使用最终窗口尺度。"""
        scale = min(self.width()/1920,self.height()/1080)
        self.panel.layout().setContentsMargins(round(86*scale),0,round(64*scale),round(24*scale))
        stylesheet = settings_stylesheet(scale)
        if hasattr(self,"tabs"):
            # 更新根样式会强制重抛光全部隐藏页；按当前页和独立的顶部/底部区域更新。
            for scope in self.panel.findChildren(QWidget,options=Qt.FindChildOption.FindDirectChildrenOnly):
                if scope is not self.tabs:
                    self._scale_scope(scope,scale,stylesheet)
            self._scale_scope(self.tabs.currentWidget(),scale,stylesheet)
        else:
            self._scale_scope(self,scale,stylesheet)
        if hasattr(self,"header"):
            side = max(110,round(210*scale))
            self.header.layout().setColumnMinimumWidth(0,side)
            self.header.layout().setColumnMinimumWidth(2,side)
            close_size = max(30,round(42*scale))
            self.close_button.setFixedSize(close_size,close_size)
            icon_size = close_size-2
            self.close_button.setIconSize(QSize(icon_size,icon_size))
            self.navigation.updateGeometry()

    def _scale_scope(self, scope, scale, stylesheet):
        if scope.styleSheet() != stylesheet:
            scope.setStyleSheet(stylesheet)
        for row in scope.findChildren(SettingsRow):
            row.apply_scale(scale)

    def fit_client_geometry(self, target):
        """先用现有设置帧覆盖新范围，避免原生GL窗口先放大时露出选手端。"""
        if target.size() == self.size():
            self.move(target.topLeft())
            return
        if self._resize_surface is None:
            self._resize_surface = self.grab()
            self.panel.hide()
        self.setGeometry(target)
        self.repaint()
        self._resize_timer.start(0)

    def _finish_client_resize(self):
        if self._resize_surface is None or not self.isVisible():
            return
        self.apply_layout_scale()
        if self._surface is not None:
            self._capture()
        else:
            self.panel.show()
            self.settle_layout()
        self._resize_surface = None
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._resize_surface is not None:
            return
        self.apply_layout_scale()
        if self._surface is not None and self.isVisible():
            self._capture()

    def showEvent(self, event):
        super().showEvent(event)
        if self._geometry_guard is not None:
            self._geometry_guard.sync()
        self.panel.backdrop = capture_backdrop(self.parentWidget())
        self.apply_layout_scale()
        self._enter_pending = True
        # 先让原生show/resize及滚动区域布局落定，再制作入场快照。
        QTimer.singleShot(0,self,self._begin_entrance)

    def hideEvent(self, event):
        self._resize_timer.stop()
        if self._resize_surface is not None:
            self._resize_surface = None
            self.panel.show()
        super().hideEvent(event)

    def _begin_entrance(self):
        if not self._enter_pending or not self.isVisible():
            return
        self._enter_pending = False
        self.timeline.target = 1
        self.timeline.progress = 0
        self._started = time.perf_counter()
        self._capture()
        self._timer.start()

    def _capture(self):
        self.panel.show()
        self.panel.ensurePolished()
        self.settle_layout()
        self._surface = self.panel.grab()
        self.capture_count += 1
        self.panel.hide()

    def settle_layout(self):
        """窗口改尺寸或换字体后，把滚动内容及内部行布局一次性同步到最终边界。"""
        self._window_layout.activate()
        # QScrollArea在子LayoutRequest后才调整内容宽度，仅activate根布局不足。
        for _ in range(3):
            for widget in (self.panel,*self.panel.findChildren(QWidget)):
                if not widget.isVisibleTo(self.panel):
                    continue
                layout = widget.layout()
                if layout is not None:
                    layout.activate()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.LayoutRequest)

    def animate_page(self, index):
        page = self.tabs.widget(index)
        scale = min(self.width()/1920,self.height()/1080)
        self._scale_scope(page,scale,settings_stylesheet(scale))
        if self._page_effect is not None:
            self._page_effect.setOpacity(1)
        effect = page.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
        self._page_effect = effect
        self._page_started = time.perf_counter()
        effect.setOpacity(0)
        self._timer.start()

    def _advance(self):
        if self._resize_surface is not None:
            return
        now = time.perf_counter()
        clip = "gamesetting_in" if self.timeline.target else "gamesetting_out"
        elapsed = now-self._started
        duration = motion().durations[clip]
        self.timeline.progress = min(1, elapsed/duration) if self.timeline.target else max(0, 1-elapsed/duration)
        if self._surface is not None:
            self.update()
            if elapsed >= duration:
                self._surface = None
                if not self.timeline.target:
                    self._timer.stop()
                    super().done(self._result)
                    return
                self.panel.show()
        if self._page_effect is not None:
            at = now-self._page_started
            alpha = motion().sample("gamesetting_change", "Main/ContentNode/SettingListNode", "m_Alpha", at, 1)
            self._page_effect.setOpacity(max(0, min(1, alpha)))
            if at >= motion().durations["gamesetting_change"]:
                self._page_effect.setOpacity(1)
                self._page_effect = None
        if self._surface is None and self._page_effect is None:
            self._timer.stop()

    def done(self, result):
        self._enter_pending = False
        if not self.isVisible():
            return super().done(result)
        if self.timeline.target == 0:
            return
        self._result = result
        # 立即撤销未完成的构建任务，不等退场完成后才取消。
        from ...settings_preparation import SettingsBuildQueue
        for builder in self.findChildren(SettingsBuildQueue):
            builder.cancel()
        self._capture()
        self.timeline.target = 0
        self._started = time.perf_counter()
        self._timer.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0a0d0e"))
        if self._resize_surface is not None:
            p.drawPixmap(self.rect(),self._resize_surface)
            return
        if self._surface is None:
            return
        clip = "gamesetting_in" if self.timeline.target else "gamesetting_out"
        at = time.perf_counter()-self._started
        scale = min(self.width()/1920, self.height()/1080)
        # 顶部与内容分别采样；本体顶部向上退，内容向下退。
        boundary = round(100*scale)
        for node, rect, baseline in (("Main/TopNode", QRectF(0, 0, self.width(), boundary), 0),
                                     ("Main/ContentNode", QRectF(0, boundary, self.width(), self.height()-boundary), -45)):
            alpha = motion().sample(clip, node, "m_Alpha", at, 1)
            y = motion().sample(clip, node, "m_AnchoredPosition.y", at, baseline)
            p.save()
            p.setOpacity(max(0, min(1, alpha)))
            p.translate(0, -(y-baseline)*scale)
            p.drawPixmap(rect, self._surface, QRectF(rect.x()*self._surface.devicePixelRatio(), rect.y()*self._surface.devicePixelRatio(), rect.width()*self._surface.devicePixelRatio(), rect.height()*self._surface.devicePixelRatio()))
            p.restore()


class _Timeline:
    target = 1
    progress = 1.0

    def active(self):
        return 0 < self.progress < 1
