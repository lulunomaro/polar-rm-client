"""共享快捷键会话，按界面偏好选择 Endfield 键位页或 Jello 原版画布。

The original Keys/Child/KeyboardScreen/PopOver classes in the user's original
archive were read, not executed. Native five-row coordinates are preserved;
function/navigation keys are an explicitly labelled RM extension. Draft changes
do not send controls, toggle modules, or silently evict conflicting bindings.
"""
from __future__ import annotations
from dataclasses import dataclass
import time
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget
from ..model import CONTROL_SLOT_ORDER, MODULE_RESERVED_KEYS
from .fonts import draw_ui_text, cjk_ui_font, has_cjk, elide_ui_text
from .jello_locale import module_label, category_label
from .settings_application import validate_draft, apply_registry_draft
from .emergency_shortcut import install_estop_shortcut
from .jello_widgets import JelloDialog, apply_jello_widgets, draw_shadow, ease_out_back, jello_font, jello_regular_font


@dataclass(frozen=True)
class Key:
    name: str
    label: str
    x: int
    y: int
    width: int
    height: int = 63
    native: bool = True


def _native(row, items):
    return tuple(Key(name, label, x, 74*(row-1), width) for name,label,x,width in items)


# Factual layout constants from Keys.class; no Java implementation is shipped.
NATIVE_KEYS = (
    *_native(1, [(n,n,x,63) for n,x in zip("`1234567890-=",(0,73,145,218,290,363,436,508,580,653,726,798,870))] + [("Backspace","Back",943,117)]),
    *_native(2, [("Tab","Tab",0,99)] + [(n,n,x,63) for n,x in zip("QWERTYUIOP[]",(108,181,253,325,399,471,543,615,689,761,833,905))] + [("\\","\\",978,82)]),
    *_native(3, [("CapsLock","Caps Lock",0,116)] + [(n,n,x,63) for n,x in zip("ASDFGHJKL;'",(127,199,271,343,417,489,561,633,707,779,851))] + [("Enter","Return",924,136)]),
    *_native(4, [("Shift","Shift",0,153)] + [(n,n,x,63) for n,x in zip("ZXCVBNM,./",(164,236,308,381,454,526,598,671,744,816))] + [("RightShift","Shift",888,172)]),
    *_native(5, [("Ctrl","Ctrl",0,97),("MetaLeft","Meta",106,63),("Alt","Alt",178,103),("Space","Space",290,427),
                ("AltRight","Alt Gr",726,97),("MetaRight","Meta",833,63),("Menu","Menu",905,63),("CtrlRight","Ctrl",978,82)]),
)
FUNCTION_KEYS = tuple(Key(name,name,x,-74,width,44,False) for name,x,width in
                     [("Escape",0,63), *((f"F{i}",73*i,63) for i in range(1,13)), ("Delete",943,117)])
NAV_KEYS = tuple(Key(name,label,i*118,382,108,44,False) for i,(name,label) in enumerate(
    [("Insert","Ins"),("Home","Home"),("End","End"),("PageUp","PgUp"),("PageDown","PgDn"),
     ("Left","←"),("Up","↑"),("Down","↓"),("Right","→")]))
ALL_KEYS = (*NATIVE_KEYS,*FUNCTION_KEYS,*NAV_KEYS)
RESERVED = MODULE_RESERVED_KEYS


@dataclass(frozen=True)
class Binding:
    identity: tuple[str,str]
    title: str
    category: str
    readonly: bool = False


class BindingSession:
    def __init__(self, host, manager=None):
        self.host, self.manager = host, manager
        self.entries = []
        self.values = {}
        for slot in CONTROL_SLOT_ORDER:
            identity = ("RobotLink",f"Bind {slot}")
            self.entries.append(Binding(identity,f"驾驶协议槽 {slot}","驾驶键位"))
            setting = host.registry.get("RobotLink").get_setting(identity[1])
            self.values[identity] = str(setting.value)
        for module in host.registry.modules:
            identity = ("module",module.name)
            self.entries.append(Binding(identity,module_label(module.name,True),category_label(module.category,True),module.name=="ClickGui"))
            self.values[identity] = "RightShift" if module.name=="ClickGui" else str(module.keybind)
        if manager is not None:
            for identity, widget in manager.controls.items():
                if identity in self.values: self.values[identity] = str(widget.value)
            for name, widget in manager.module_bind_controls.items(): self.values[("module",name)] = str(widget.value)
            owned = set(manager.controls) | {("module",n) for n in manager.module_bind_controls}
            self.entries = [Binding(e.identity,e.title,e.category,e.readonly or e.identity not in owned) for e in self.entries]
        self.original = dict(self.values)

    def bound(self,key):
        return [entry for entry in self.entries if self.values[entry.identity].lower()==key.lower()]

    def _validate(self, values):
        driver = {identity:value for identity,value in values.items() if identity[0]=="RobotLink"}
        modules = {identity[1]:value for identity,value in values.items() if identity[0]=="module" and identity[1]!="ClickGui"}
        validate_draft(self.host,driver,modules)

    def reason(self, entry, key):
        canonical_map_tab = entry.identity==("module","TacticalMap") and key.lower()=="tab"
        if entry.readonly or (key.lower() in RESERVED and not canonical_map_tab): return "界面 / 急停保留键，不可改绑"
        try:
            values = dict(self.values); values[entry.identity] = key
            self._validate(values)
        except ValueError as exc:
            return str(exc)
        return ""

    def assign(self, entry, key):
        why = self.reason(entry,key)
        if why: raise ValueError(why)
        self.values[entry.identity] = key

    def clear(self, entry):
        if entry.readonly: raise ValueError("界面保留绑定不可删除")
        values = dict(self.values); values[entry.identity] = "None"
        self._validate(values); self.values = values

    def apply(self):
        self._validate(self.values)
        if self.manager is not None:
            # Keyboard editor edits this page's draft, never the live registry.
            for identity, value in self.values.items():
                if identity[0]=="module":
                    widget = self.manager.module_bind_controls.get(identity[1])
                else:
                    widget = self.manager.controls.get(identity)
                if widget is not None: widget.set_value(value)
            return
        driver = {k:v for k,v in self.values.items() if k[0]=="RobotLink" and v!=self.original[k]}
        modules = {k[1]:v for k,v in self.values.items() if k[0]=="module" and v!=self.original[k] and k[1]!="ClickGui"}
        if driver or modules: apply_registry_draft(self.host,driver,module_binds=modules)


class BindingPicker(JelloDialog):
    def __init__(self, owner, key, target=None):
        super().__init__(owner,keyboard=True)
        try:
            self.timeline.reverse_duration = .120
            self.setObjectName("JelloBindingPicker")
            self.resize(544,644); self.setMinimumSize(390,480)
            screen = owner.screen().availableGeometry()
            self.resize(min(544,screen.width()-30),min(644,screen.height()-40))
            self.selected_entry = None
            self.key = key
            self.owner = owner
            layout = self.panel.layout()
            title = QLabel("选择要绑定的功能"); title.setFont(jello_font(36));title.setStyleSheet("font-size:30px;color:#666666;"); layout.addWidget(title)
            self.search = QLineEdit(); self.search.setPlaceholderText("Search… / 搜索功能");self.search.setMinimumHeight(60); layout.addWidget(self.search)
            self.list = QListWidget(); layout.addWidget(self.list,1)
            self.reason_label = QLabel(); self.reason_label.setWordWrap(True); layout.addWidget(self.reason_label)
            buttons = QHBoxLayout(); layout.addLayout(buttons)
            back = QPushButton("返回"); add = QPushButton("Add / 添加")
            add.setProperty("primary",True); buttons.addWidget(back); buttons.addStretch(); buttons.addWidget(add)
            self.items = []
            entries = sorted(owner.session.entries,key=lambda e:(e.identity!=target,e.category,e.title))
            for entry in entries:
                if entry.readonly: continue
                from PySide6.QtWidgets import QListWidgetItem
                item = QListWidgetItem(entry.title+"  ·  "+entry.category)
                why = owner.session.reason(entry,key)
                item.setData(Qt.ItemDataRole.UserRole,entry); item.setToolTip(why or f"绑定到 {key}")
                if why:
                    item.setForeground(QColor("#aaaaaa"))
                self.list.addItem(item); self.items.append(item)
            self.search.textChanged.connect(self.filter)
            self.list.currentItemChanged.connect(lambda current,old:self.reason_label.setText(current.toolTip() if current else ""))
            self.list.itemDoubleClicked.connect(lambda _item:self.add_selected())
            add.clicked.connect(self.add_selected); back.clicked.connect(self.reject)
            apply_jello_widgets(self)
            self.list.setFont(cjk_ui_font(jello_font()))
            self.list.setStyleSheet("QListWidget {background:#fefefe;border:0;color:#555555;font-family:'Microsoft YaHei UI','Microsoft YaHei','DengXian';font-weight:400;font-size:18px;} QListWidget::item {padding:12px;} QListWidget::item:selected {background:#e8f4ff;color:#29a6ff;}")
            self.reason_label.setStyleSheet("font-family:'Microsoft YaHei UI','Microsoft YaHei','DengXian';font-weight:400;")
            if self.items:self.list.setCurrentRow(0)
        except Exception as error:
            # A failed constructor still owns the Qt tree acquired by super.
            try:
                self.deleteLater()
            finally:
                raise error

    def filter(self, text):
        for item in self.items: item.setHidden(text.casefold() not in item.text().casefold())

    def add_selected(self):
        item = self.list.currentItem()
        if item is None: return
        entry = item.data(Qt.ItemDataRole.UserRole)
        why = self.owner.session.reason(entry,self.key)
        if why: self.reason_label.setText(why); return
        self.selected_entry = entry; self.accept()


class KeyboardCanvas(QWidget):
    LOGICAL_WIDTH = 1200
    LOGICAL_HEIGHT = 750
    def __init__(self, dialog, session, target=None):
        super().__init__(dialog.panel)
        self.dialog, self.session, self.target = dialog, session, target
        self.setMouseTracking(True); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.selected = None; self.pop_started = 0.0; self.hover = None
        self.held = set(); self.hover_values = {}
        self.hotspots = []; self.note = ""; self.pop_scroll = 0
        self._last_frame = time.perf_counter()
        self._motion_timer = QTimer(self); self._motion_timer.setInterval(7)
        self._motion_timer.timeout.connect(self._motion_step)
        self.row_motion = {}; self.pending_removals = {}
        self.removed_rows = {}

    def mapping(self):
        scale = min(1.25,self.width()/self.LOGICAL_WIDTH,self.height()/self.LOGICAL_HEIGHT)
        return ((self.width()-self.LOGICAL_WIDTH*scale)/2,(self.height()-self.LOGICAL_HEIGHT*scale)/2,scale)

    def logical_point(self, point):
        x,y,s = self.mapping(); return QPointF((point.x()-x)/s,(point.y()-y)/s)

    @staticmethod
    def key_rect(key): return QRectF(70+key.x,250+key.y,key.width,key.height)

    def key_at(self, point):
        return next((key for key in ALL_KEYS if self.key_rect(key).contains(point)),None)

    def _motion_step(self):
        now = time.perf_counter(); dt = max(0,now-self._last_frame); self._last_frame = now
        moving = False
        for key in ALL_KEYS:
            wanted = 1.0 if self.hover==key.name or key.name in self.held else 0.0
            value = self.hover_values.get(key.name,0.0)
            value = min(wanted,value+dt*12) if wanted>value else max(wanted,value-dt*12)
            self.hover_values[key.name] = value
            moving |= abs(wanted-value)>.0001
        for identity,started in list(self.pending_removals.items()):
            if now-started>=.180:
                del self.pending_removals[identity];self.removed_rows.pop(identity,None)
            else: moving = True
        moving |= bool(self.selected and now-self.pop_started<.250)
        moving |= any(now-started<.150 for started in self.row_motion.values())
        self.update()
        if not moving: self._motion_timer.stop()

    def animate(self):
        self._last_frame = time.perf_counter(); self._motion_timer.start(); self.update()

    def _retire_popover_hotspots(self):
        # Painted popup targets belong to the selection/layout that made them.
        # Keep the independent Done button live until the next frame.
        self.hotspots = [hit for hit in self.hotspots if hit[1] == "done"]
        self.update()

    def resizeEvent(self, event):
        self._retire_popover_hotspots()
        super().resizeEvent(event)

    def select_key(self, key):
        self._retire_popover_hotspots()
        self.selected = None if self.selected==key.name else key.name
        self.pop_started = time.perf_counter(); self.pop_scroll = 0; self.note = ""
        self.animate()

    def pop_rect(self):
        key = next(k for k in ALL_KEYS if k.name==self.selected)
        anchor = self.key_rect(key)
        # Native 250x330 popover. Flip above when the lower screen edge is near.
        y = anchor.bottom()+10
        if y+330>740: y=anchor.bottom()-400
        return QRectF(max(24,min(926,anchor.center().x()-125)),max(8,y),250,330)

    def _text(self, painter, rect, text, pixels, color="#666666", medium=False, align=Qt.AlignLeft|Qt.AlignVCenter, regular=False):
        font = jello_regular_font(pixels) if regular else jello_font(pixels,medium)
        if has_cjk(text): font = cjk_ui_font(font)
        text=elide_ui_text(font,text,rect.width(),painter.device())
        painter.setPen(QColor(color))
        draw_ui_text(painter,rect,text,font,align)
        painter.setPen(Qt.PenStyle.NoPen)

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        x,y,scale = self.mapping(); p.translate(x,y); p.scale(scale,scale)
        p.setPen(Qt.PenStyle.NoPen)
        self.hotspots = []
        self._text(p,QRectF(70,22,1020,55),"Keybind Manager",40,"#666666",True)
        title = next((e.title for e in self.session.entries if e.identity==self.target),"所有快捷键")
        self._text(p,QRectF(70,90,1060,40),f"{title} · 点击键帽，查看绑定并添加功能",20)
        self._text(p,QRectF(70,134,1060,27),"RM 功能键扩展 · F12 急停 / Esc 返回 / RightShift 与 Tab 保留",14,"#999999")
        board = QRectF(50,230,1100,402)
        draw_shadow(p,board,20,.35); p.setBrush(QColor("#fefefe")); p.drawRoundedRect(board,14,14)
        for key in ALL_KEYS:
            rect = self.key_rect(key); hover = self.hover_values.get(key.name,0)
            bound = bool(self.session.bound(key.name))
            p.setBrush(QColor.fromRgbF((208+14*hover)/255,(208+14*hover)/255,(208+14*hover)/255))
            p.drawRoundedRect(rect.translated(0,5),8,8)
            p.setBrush(QColor("#f0f0f0")); p.drawRoundedRect(rect.translated(0,3*hover),8,8)
            tint = "#666666" if bound else "#999999"
            text_rect = rect.translated(0,3*hover)
            if key.label in ("Back","Return"):
                p.setPen(QPen(QColor(tint),2)); cx,cy=text_rect.center().x(),text_rect.center().y()
                p.drawLine(QPointF(cx-14,cy),QPointF(cx+14,cy)); p.drawLine(QPointF(cx-14,cy),QPointF(cx-8,cy-4)); p.drawLine(QPointF(cx-14,cy),QPointF(cx-8,cy+4))
                if key.label=="Return": p.drawLine(QPointF(cx+14,cy),QPointF(cx+14,cy-9))
                p.setPen(Qt.PenStyle.NoPen)
            elif key.label=="Meta":
                p.setBrush(QColor(tint)); p.drawEllipse(text_rect.center(),7,7)
            elif key.label=="Menu":
                p.setPen(QPen(QColor(tint),2)); center=text_rect.center()
                for dy in (-6,0,6):p.drawLine(center+QPointF(-7,dy),center+QPointF(7,dy))
                p.setPen(Qt.PenStyle.NoPen)
            elif key.label!="Space":
                self._text(p,text_rect,key.label,20 if key.native else 16,QColor(1,1,1,153 if bound else 102),False,Qt.AlignCenter,regular=bound)
            if key.name.lower() in RESERVED:
                p.setBrush(QColor("#f19999") if key.name=="F12" else QColor("#cccccc"))
                p.drawEllipse(QPointF(rect.right()-9,rect.bottom()-9),2.5,2.5)
        self._text(p,QRectF(70,698,900,36),self.note or "灰色为未绑定 · 冲突不会覆盖 · 设置页中修改的是待应用草稿",16,"#888888")
        done = QRectF(1010,696,120,38); p.setBrush(QColor("#29a6ff")); p.drawRoundedRect(done,5,5)
        self._text(p,done,"完成",20,"#fefefe",False,Qt.AlignCenter); self.hotspots.append((done,"done",None))
        if self.selected: self.draw_popover(p)
        p.end()

    def draw_popover(self,p):
        rect = self.pop_rect(); progress=min(1,(time.perf_counter()-self.pop_started)/.250)
        eased=ease_out_back(progress);factor=.8+.2*eased
        anchor = self.key_rect(next(k for k in ALL_KEYS if k.name==self.selected))
        below = rect.top()>anchor.top()
        p.save();p.translate(0,50*(1-eased)*(1 if below else -1))
        p.translate(rect.center()); p.scale(factor,factor); p.translate(-rect.center())
        p.setOpacity(2*progress-progress*progress)
        draw_shadow(p,rect,35,progress*.35); p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f4f4f4")); p.drawRoundedRect(rect,10,10)
        edge = rect.top() if below else rect.bottom()
        pointer_x=max(rect.left()+24,min(rect.right()-24,anchor.center().x()))
        p.drawPolygon(QPolygonF([QPointF(pointer_x-23.5,edge),QPointF(pointer_x+23.5,edge),
                                QPointF(pointer_x,edge+(-18 if below else 18))]))
        p.setOpacity(1)
        self._text(p,QRectF(rect.x()+25,rect.y()+16,200,44),self.selected+" Key",25,QColor(1,1,1,round(204*progress)))
        p.fillRect(QRectF(rect.x()+25,rect.y()+68,200,1),QColor("#e8e8e8"))
        entries=self.session.bound(self.selected) + [e for e,key in self.removed_rows.values() if key==self.selected]
        order={e.identity:i for i,e in enumerate(self.session.entries)}
        entries.sort(key=lambda e:order[e.identity])
        viewport=QRectF(rect.x(),rect.y()+75,250,185)
        p.save(); p.setClipRect(viewport)
        row_y=rect.y()+75-self.pop_scroll
        for entry in entries:
            factor_row=1.0
            now=time.perf_counter()
            if entry.identity in self.row_motion:
                t=min(1,(now-self.row_motion[entry.identity])/.150);factor_row=2*t-t*t
            if entry.identity in self.pending_removals:
                t=min(1,(now-self.pending_removals[entry.identity])/.180);factor_row=1-(2*t-t*t)
            row=QRectF(rect.x(),row_y,250,55*factor_row)
            p.save();p.setClipRect(row,Qt.ClipOperation.IntersectClip)
            self._text(p,QRectF(row.x()+25,row.y(),170,32),entry.title,20,QColor(1,1,1,round(153*progress)),regular=True)
            self._text(p,QRectF(row.x()+25,row.y()+29,170,22),entry.category,12,QColor(1,1,1,round(153*progress)))
            delete=QRectF(row.right()-47,row.center().y()-13,24,26)
            if not entry.readonly:
                self._text(p,delete,"×",22,"#999999",False,Qt.AlignCenter)
                if progress>=1 and factor_row>.9:
                    hit=delete.intersected(viewport)
                    if not hit.isEmpty():self.hotspots.append((hit,"remove",entry))
            p.restore();row_y+=55*factor_row
        if not entries: self._text(p,QRectF(rect.x()+25,rect.y()+105,200,75),"此键暂无绑定",18,"#aaaaaa")
        p.restore()
        add=QRectF(rect.x()+145,rect.bottom()-60,85,40)
        readonly=self.selected.lower() in RESERVED and self.selected.lower()!="tab"
        self._text(p,add,"保留" if readonly else "Add",25,"#aaaaaa" if readonly else "#29a6ff",False,Qt.AlignCenter)
        if not readonly and progress>=1:self.hotspots.append((add,"add",None))
        p.restore()

    def mouseMoveEvent(self,event):
        point=self.logical_point(event.position());key=self.key_at(point)
        hover=key.name if key else None
        if hover!=self.hover:self.hover=hover;self.animate()

    def leaveEvent(self,event):self.hover=None;self.animate()

    def mousePressEvent(self,event):
        point=self.logical_point(event.position())
        if event.button()!=Qt.MouseButton.LeftButton:
            self._retire_popover_hotspots()
            self.selected=None;self.update();event.accept();return
        for rect,action,payload in reversed(self.hotspots):
            if not rect.contains(point):continue
            if action=="done":self.dialog.accept()
            elif action=="remove":
                self._retire_popover_hotspots()
                self.session.clear(payload)
                self.removed_rows[payload.identity]=(payload,self.selected)
                self.pending_removals[payload.identity]=time.perf_counter();self.animate()
            elif action=="add":self.open_picker()
            event.accept();return
        if self.selected and self.pop_rect().contains(point):event.accept();return
        key=self.key_at(point)
        if key:self.select_key(key)
        else:
            self._retire_popover_hotspots()
            self.selected=None;self.update()
        event.accept()

    def open_picker(self):
        self._retire_popover_hotspots()
        key = self.selected
        if key is None:
            return
        picker=BindingPicker(self.dialog,key,self.target)
        try:
            result = picker.exec()
            # The nested event loop can repaint and republish the old rows.
            self._retire_popover_hotspots()
            if result==QDialog.DialogCode.Accepted and picker.selected_entry:
                self.session.assign(picker.selected_entry,key)
                self.row_motion[picker.selected_entry.identity]=time.perf_counter()
                self.note="已更新草稿，完成后在设置页应用";self.animate()
        finally:picker.deleteLater()

    def wheelEvent(self,event):
        if self.selected:
            maximum=max(0,len(self.session.bound(self.selected))*55-185)
            scroll=max(0,min(maximum,self.pop_scroll-event.angleDelta().y()/3))
            if scroll != self.pop_scroll:
                self._retire_popover_hotspots()
                self.pop_scroll = scroll
            self.update()
        event.accept()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self._retire_popover_hotspots()
            if self.selected:self.selected=None;self.update()
            else:self.dialog.reject()
        elif not event.isAutoRepeat():
            name=self.session.host.key_name(event)
            self.held.add(name);self.animate()
        event.accept()

    def keyReleaseEvent(self,event):
        self.held.discard(self.session.host.key_name(event));self.animate();event.accept()


def build_keyboard_manager(host, manager=None, target=None, parent=None):
    from .ui_theme import is_jello
    if is_jello():
        dialog = JelloDialog(parent or host, keyboard=True)
        try:
            dialog.setObjectName("JelloKeyboardManager")
            dialog.setWindowTitle("Jello · Keybind Manager")
            available = host.screen().availableGeometry()
            dialog.resize(min(1280, available.width()-30, max(600, host.width()-20)),
                          min(860, available.height()-40, max(430, host.height()-20)))
            dialog.setMinimumSize(600, 430)
            dialog.session = BindingSession(host, manager)
            dialog.canvas = KeyboardCanvas(dialog, dialog.session, target)
            dialog.focus_widget = dialog.canvas
            dialog.panel.layout().setContentsMargins(0, 0, 0, 0)
            dialog.panel.layout().addWidget(dialog.canvas)
            dialog.estop_shortcut = install_estop_shortcut(dialog, host)
            apply_jello_widgets(dialog)
            return dialog
        except Exception:
            dialog.deleteLater()
            raise
    from .endfield.settings.shell import EndfieldSettingsDialog
    if manager is not None and isinstance(manager.window(), EndfieldSettingsDialog):
        from .endfield.settings.bindings import build_binding_manager
        return build_binding_manager(host, BindingSession(host, manager),
                                     [key.name for key in ALL_KEYS], target, parent)
    from .endfield.keybinds.dialog import build_keybind_dialog
    return build_keybind_dialog(host,BindingSession(host,manager),[key.name for key in ALL_KEYS],target,parent)


def open_keyboard_manager(host, manager=None, target=None):
    host.backend.lock_control("keyboard manager opened")
    host.control.release_all(host.state,host.registry);host._control_mouse_point=None
    parent=manager.window() if manager is not None else host
    dialog=build_keyboard_manager(host,manager,target,parent)
    try:
        if dialog.exec()==QDialog.DialogCode.Accepted:dialog.session.apply()
    except ValueError as exc:
        host.toast("绑定未应用："+str(exc),kind="warning")
    finally:
        host.control.reset_inputs();host.control.clear_pending();host._control_mouse_point=None
        host.invalidate_gui_cache();host.frame_scheduler.force();dialog.deleteLater()


def open_module_keyboard(host,module_name):
    open_keyboard_manager(host,target=("module",str(module_name)))
