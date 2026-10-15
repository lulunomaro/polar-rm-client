"""Jello fonts, native shadows and duration-based Qt presentation.

Fonts are the exact Sigma 5.1.0 faces already used by the ClickGUI. This module
does not introduce a blur shader or a CPU camera/desktop blur. Entrance/exit
scales one cached widget surface, never reflows every child on every frame.
"""
from __future__ import annotations
from functools import lru_cache
import time
from PySide6.QtCore import QRectF, QRect, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget, QCheckBox, QComboBox, QStyle, QStyleOptionButton
from ..runtime_paths import RuntimePaths
from .fonts import FontBook, has_cjk, cjk_ui_font
from .jello_clickgui import JelloMainTimeline, JelloReversibleAnimation

_book = None


def use_jello_font_book(book):
    """Reuse the window's already registered faces on the first native dialog."""
    global _book
    if _book is None and isinstance(book, FontBook):
        _book = book


def jello_font(pixels=20, medium=False):
    global _book
    if _book is None:
        _book = FontBook()
        _book.load_from_project_assets(
            RuntimePaths.discover().resource_root,
            keys=("jello_light", "jello_medium", "jello_regular"),
        )
    font = _book.font("jello_medium" if medium else "jello_light", pixels * 72 / 96)
    return QFont(font)


def jello_regular_font(pixels=20):
    jello_font()  # Initialize the shared native font bank once.
    return _book.font("jello_regular",pixels*72/96)


def ease_out_back(raw):
    raw = max(0.0, min(1.0, float(raw)))
    t = raw - 1.0
    return t * t * ((1.70158 + 1.0) * t + 1.70158) + 1.0


@lru_cache(maxsize=1)
def shadow_images():
    root = RuntimePaths.discover().asset("jello")
    names = ("shadow_corner", "shadow_corner_2", "shadow_corner_3", "shadow_corner_4",
             "shadow_left", "shadow_right", "shadow_top", "shadow_bottom")
    return {name: QImage(str(root / (name + ".png"))) for name in names}


def draw_shadow(painter, rect, radius=20, alpha=.35):
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    rectangles = (("shadow_corner", QRectF(x-radius,y-radius,radius,radius)),
                  ("shadow_corner_2", QRectF(x+w,y-radius,radius,radius)),
                  ("shadow_corner_3", QRectF(x-radius,y+h,radius,radius)),
                  ("shadow_corner_4", QRectF(x+w,y+h,radius,radius)),
                  ("shadow_left", QRectF(x-radius,y,radius,h)),
                  ("shadow_right", QRectF(x+w,y,radius,h)),
                  ("shadow_top", QRectF(x,y-radius,w,radius)),
                  ("shadow_bottom", QRectF(x,y+h,w,radius)))
    painter.save(); painter.setOpacity(alpha)
    for name, area in rectangles:
        img = shadow_images()[name]
        if not img.isNull(): painter.drawImage(area, img)
    painter.restore()


def jello_stylesheet(scope="QWidget"):
    font = jello_font()
    families = ",".join('"' + name + '"' for name in font.families())
    return f"""
        {scope}, {scope} QWidget {{color:#333333;font-family:{families};font-size:18px;font-weight:300;}}
        {scope} QLabel {{background:transparent;}}
        {scope} QScrollArea, {scope} QScrollArea > QWidget > QWidget {{background:#fefefe;border:0;}}
        {scope} QTabWidget::pane {{background:#fefefe;border:0;}}
        {scope} QTabBar::tab {{padding:15px 18px;background:#fefefe;color:#888888;border-bottom:2px solid transparent;}}
        {scope} QTabBar::tab:selected {{color:#29a6ff;border-bottom-color:#29a6ff;}}
        {scope} QTabBar::tab:hover {{color:#333333;}}
        {scope} QGroupBox {{background:#fefefe;border:0;border-top:1px solid #eeeeee;margin-top:24px;padding:25px 18px 18px;}}
        {scope} QGroupBox::title {{subcontrol-origin:margin;left:18px;color:#666666;}}
        {scope} QComboBox, {scope} QDoubleSpinBox, {scope} QSpinBox, {scope} QLineEdit {{
            color:#555555;background:#f6f6f6;padding:8px;border:0;border-radius:5px;}}
        {scope} QComboBox QAbstractItemView {{background:#fefefe;color:#333333;border:0;selection-background-color:#dcefff;}}
        {scope} QPushButton {{background:#f0f0f0;color:#555555;padding:10px 18px;border:0;border-radius:6px;}}
        {scope} QPushButton:hover {{background:#e8f4ff;color:#29a6ff;}}
        {scope} QPushButton:pressed {{background:#dcefff;}}
        {scope} QPushButton:focus {{border:1px solid #29a6ff;}}
        {scope} QPushButton[primary="true"] {{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #29a6ff,stop:1 #29b8ff);color:white;}}
        {scope} QCheckBox::indicator {{width:22px;height:22px;border:0;border-radius:10px;background:#dfdfdf;}}
        {scope} QCheckBox::indicator:checked {{background:#29a6ff;image:url("{RuntimePaths.discover().asset('jello','check.png').as_posix()}");}}
        {scope} QSlider::groove:horizontal {{height:4px;background:#d8ecff;border-radius:2px;}}
        {scope} QSlider::sub-page:horizontal {{background:#3b99fd;border-radius:2px;}}
        {scope} QSlider::handle:horizontal {{width:22px;margin:-9px 0;background:#fefefe;border:1px solid #eeeeee;border-radius:11px;}}
        {scope} QScrollBar:vertical {{background:#fefefe;width:7px;border:0;}}
        {scope} QScrollBar::handle:vertical {{background:#cccccc;border-radius:3px;min-height:24px;}}
        {scope} QScrollBar::add-line:vertical,{scope} QScrollBar::sub-line:vertical {{height:0;}}
        {scope} QToolTip {{background:#fefefe;color:#333333;border:1px solid #eeeeee;}}
        {scope} QWidget[jelloCjk="true"] {{font-family:"Microsoft YaHei UI","Microsoft YaHei","DengXian";font-weight:400;}}
    """


def apply_jello_widgets(widget, scope=None, *, stylesheet=True):
    scope = scope or ("QWidget#" + widget.objectName() if widget.objectName() else "QWidget")
    widget.setFont(jello_font())
    palette = widget.palette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base): palette.setColor(role, QColor("#fefefe"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text): palette.setColor(role, QColor("#333333"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#555555"))
    widget.setPalette(palette)
    # Set selector properties BEFORE installing the sheet. Qt then resolves
    # each descendant once; unpolish/polish on every child used to repeat that
    # work after Qt had already styled the entire subtree.
    for child in [widget,*widget.findChildren(QWidget)]:
        text = ""
        for name in ("text","currentText","placeholderText","title"):
            method = getattr(child,name,None)
            if callable(method):
                try: text += str(method())
                except TypeError: pass
        cjk = has_cjk(text)
        child.setProperty("jelloCjk",cjk)
        if cjk: child.setFont(cjk_ui_font(jello_font()))
    if stylesheet:
        widget.setStyleSheet(jello_stylesheet(scope))


@lru_cache(maxsize=1)
def check_image():
    return QImage(str(RuntimePaths.discover().asset("jello","check.png")))


class JelloCheckBox(QCheckBox):
    """Native Checkbox: checked reveal90ms/uncheck70ms, 1.5→1 tick scale."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args,**kwargs)
        self.motion=JelloReversibleAnimation(.090,.070)
        self._last_time=0.0
        self._timer=QTimer(self);self._timer.setInterval(7)
        self._timer.timeout.connect(self._animate)
        self.toggled.connect(self._start)

    def _start(self,checked):
        if not self.isVisible():
            self.motion.reset(float(checked));self._timer.stop();return
        self.motion.set_target(float(checked));self._last_time=time.perf_counter();self._timer.start()

    def _animate(self):
        now=time.perf_counter();self.motion.update(now-self._last_time);self._last_time=now
        self.update()
        if not self.motion.active():self._timer.stop()

    def paintEvent(self,event):
        opt=QStyleOptionButton();self.initStyleOption(opt)
        indicator=QRectF(self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator,opt,self))
        label=self.style().subElementRect(QStyle.SubElement.SE_CheckBoxContents,opt,self)
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        p.setPen(Qt.PenStyle.NoPen)
        value=self.motion.progress
        p.setBrush(QColor(192,192,192,round(255*.43*(1-value))))
        p.drawRoundedRect(indicator,10,10)
        p.setBrush(QColor(41,166,255,round(255*value)));p.drawRoundedRect(indicator,10,10)
        if value>0:
            p.save();p.setOpacity(value);p.setClipRect(self.rect())
            tick_scale=1.5-.5*value;area=QRectF(0,0,indicator.width()*tick_scale,indicator.height()*tick_scale);area.moveCenter(indicator.center())
            p.drawImage(area,check_image());p.restore()
        p.setFont(self.font());p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        p.drawText(label,Qt.AlignLeft|Qt.AlignVCenter,self.text());p.end()


class JelloComboBox(QComboBox):
    """Native Qt model/input, Jello 220ms down/up expansion and collapse."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._drop_motion=QVariantAnimation(self)
        self._drop_motion.setDuration(220)
        self._drop_motion.valueChanged.connect(self._draw_drop_frame)
        self._drop_motion.finished.connect(self._finish_drop)
        self._popup=None;self._drop_closing=False;self._drop_progress=0.0

    def showPopup(self):
        self._drop_motion.stop();self._drop_closing=False
        self._drop_motion.setDuration(220)
        super().showPopup()
        self._popup=self.view().window()
        self._drop_rect=QRect(self._popup.geometry())
        anchor=self.mapToGlobal(self.rect().bottomLeft())
        available=self.screen().availableGeometry()
        self._opens_up=available.bottom()-anchor.y()<self._drop_rect.height()
        if self._opens_up:
            self._drop_rect.moveBottom(self.mapToGlobal(self.rect().topLeft()).y())
        else:self._drop_rect.moveTop(anchor.y()+1)
        self._drop_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._drop_motion.setStartValue(0.0);self._drop_motion.setEndValue(1.0)
        self._draw_drop_frame(0.0);self._drop_motion.start()

    def hidePopup(self):
        from shiboken6 import isValid
        if self._popup is None or not isValid(self._popup) or not self._popup.isVisible():
            self._popup=None;return super().hidePopup()
        if self._drop_closing:return
        self._drop_motion.stop();self._drop_closing=True
        self._drop_motion.setDuration(max(1,round(220*self._drop_progress)))
        # Jello closes with raw^2 as raw decreases: (1-t)^2, not 1-t^2.
        self._drop_motion.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._drop_motion.setStartValue(self._drop_progress);self._drop_motion.setEndValue(0.0)
        self._drop_motion.start()

    def _draw_drop_frame(self,value):
        from shiboken6 import isValid
        if self._popup is None or not isValid(self._popup):return
        self._drop_progress=float(value)
        rect=QRect(self._drop_rect);height=max(1,round(rect.height()*self._drop_progress))
        if self._opens_up:rect.setTop(rect.bottom()-height+1)
        else:rect.setHeight(height)
        self._popup.setGeometry(rect)

    def _finish_drop(self):
        if self._drop_closing:
            super().hidePopup();self._popup=None;self._drop_closing=False
        else:
            self._drop_progress=1.0
            if self._popup is not None:self._popup.setGeometry(self._drop_rect)


class JelloSurface(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("JelloSurface {background:#fefefe;border-radius:10px;}")


class JelloDialog(QDialog):
    """450/125ms ClickGuiScreen timeline; a single DPR-aware cached surface."""
    def __init__(self, parent=None, *, keyboard=False):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.panel = JelloSurface(self)
        self.panel.setObjectName("JelloPanel")
        self.panel.setLayout(QVBoxLayout())
        self.panel.layout().setContentsMargins(26, 22, 26, 20)
        self.timeline = JelloReversibleAnimation(.200, .125) if keyboard else JelloMainTimeline()
        self.keyboard_timeline = keyboard
        self._surface = None
        self._result = QDialog.DialogCode.Rejected
        self._last_tick = 0.0
        self._transition_timer = QTimer(self)
        self._transition_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._transition_timer.setInterval(7)
        self._transition_timer.timeout.connect(self._advance)
        self.capture_count = 0
        self._snap_size = None

    def showEvent(self, event):
        super().showEvent(event)
        self._position_panel()
        self.panel.layout().activate()
        self.panel.show()
        self.timeline.reset(0); self.timeline.set_target(1)
        self._capture()
        self._last_tick = time.perf_counter()
        self._transition_timer.start()

    def _capture(self):
        self.panel.show(); self.panel.ensurePolished(); self.panel.layout().activate()
        self._surface = self.panel.grab()
        self._snap_size = self.size()
        self.capture_count += 1
        self.panel.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_panel()
        if self._surface is not None and self._snap_size != self.size(): self._capture()

    def _position_panel(self):
        # Reserve the original elastic overshoot and shadow rather than
        # clipping the white corners against the native dialog window.
        margin=22 if self.keyboard_timeline else max(44,int(self.width()*.04+24))
        self.panel.setGeometry(self.rect().adjusted(margin,margin,-margin,-margin))

    def visual_factor(self):
        if self.keyboard_timeline:
            return .8 + .2 * ease_out_back(self.timeline.progress) if self.timeline.target else 2*self.timeline.progress-self.timeline.progress**2
        return self.timeline.visual_factor()

    def _advance(self):
        now = time.perf_counter(); self.timeline.update(now - self._last_tick); self._last_tick = now
        self.update()
        if not self.timeline.active():
            self._transition_timer.stop()
            self._surface = None
            if self.timeline.target:
                self.panel.show()
                focus_widget=getattr(self,"focus_widget",None)
                if focus_widget is not None:focus_widget.setFocus()
            else:
                super().done(self._result)

    def done(self, result):
        if not self.isVisible():
            return super().done(result)
        self._result = result
        if self.timeline.target == 0: return
        if self._surface is None: self._capture()
        self.timeline.set_target(0)
        self._last_tick = time.perf_counter(); self._transition_timer.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        factor = self.visual_factor()
        rect = QRectF(self.panel.geometry())
        if self._surface is not None:
            painter.translate(rect.center()); painter.scale(factor,factor); painter.translate(-rect.center())
            draw_shadow(painter,rect,20,.25*min(1,self.timeline.progress))
            painter.setOpacity(min(1,self.timeline.progress*4))
            path = QPainterPath(); path.addRoundedRect(rect,10,10); painter.setClipPath(path)
            painter.drawPixmap(rect.toRect(),self._surface)
        else:
            draw_shadow(painter,rect,20,.25)
        painter.end()
