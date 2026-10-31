"""CS2-style video settings for this Qt client, not a CS2 configuration editor.

Window resolution/HUD cadence and video sampling apply locally. Swap interval
and MSAA need a new OpenGL context (restart). Physical display mode is read-only:
borderless Qt cannot honestly claim it changed the monitor's refresh rate.
"""
from __future__ import annotations

from dataclasses import asdict,dataclass,replace
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import tempfile

from ..runtime_paths import RuntimePaths


@dataclass(frozen=True)
class VideoPreferences:
    window_mode: str = "Windowed"
    resolution: str = "Current"
    hud_hz: int = 0
    fps_limit: int = 0
    vsync: bool = True
    msaa: int = 0
    video_detail: str = "Legacy"
    video_filter: str = "Auto"
    render_adapter: str = "auto"


def validate_preferences(data):
    defaults=asdict(VideoPreferences())
    if not isinstance(data,dict) or set(data)-set(defaults):
        raise ValueError("未知视频设置")
    values={**defaults,**data}
    from .graphics_devices import valid_adapter
    if not valid_adapter(values['render_adapter']):
        raise ValueError("无效渲染显卡")
    if values["window_mode"] not in ("Windowed","Borderless"):
        raise ValueError("不支持的显示模式")
    if values["resolution"] not in ("Current","1280×720","1600×900","1920×1080","2560×1440","3840×2160"):
        raise ValueError("不支持的分辨率")
    for key,choices in (("hud_hz",(0,60,75,90,120,144,165,240)),("fps_limit",(0,30,60,75,90,120,144,165,240)),("msaa",(0,2,4,8))):
        if type(values[key]) is not int or values[key] not in choices:
            raise ValueError("无效视频数值："+key)
    if type(values["vsync"]) is not bool or values["video_detail"] not in ("Legacy","720p","1080p","Native") or values["video_filter"] not in ("Auto","Linear","Nearest"):
        raise ValueError("无效画质选项")
    return VideoPreferences(**values)


@lru_cache(maxsize=1)
def preferences_path():
    return RuntimePaths.discover().user_data_root/"video_preferences.json"


@lru_cache(maxsize=1)
def current_preferences():
    path=preferences_path()
    try:
        if path.is_file() and path.stat().st_size<=8192:
            return validate_preferences(json.loads(path.read_text(encoding="utf-8")))
    except (OSError,ValueError,TypeError):
        pass
    return VideoPreferences()


def save_preferences(prefs):
    prefs=validate_preferences(asdict(prefs))
    path=preferences_path();path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix="video-",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(asdict(prefs),stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
    current_preferences.cache_clear()


def render_target(existing,cap=240):
    prefs=current_preferences()
    target=prefs.hud_hz or existing
    if prefs.fps_limit:
        target=min(target,prefs.fps_limit)
    return max(30,min(cap,target))


def sampled_video_size(width,height):
    detail=current_preferences().video_detail
    if detail=="Legacy":
        return None
    if detail=="Native":
        return (width,height)
    budget=1280*720 if detail=="720p" else 1920*1080
    factor=min(1,math.sqrt(budget/max(1,width*height)))
    return max(1,round(width*factor)),max(1,round(height*factor))


def smooth_video(default):
    value=current_preferences().video_filter
    return default if value=="Auto" else value=="Linear"


def configure_surface(surface):
    prefs=current_preferences()
    surface.setSwapInterval(1 if prefs.vsync else 0)
    surface.setSamples(prefs.msaa)


def apply_window_settings(host,prefs):
    prefs = validate_preferences(asdict(prefs))
    logical = None
    if prefs.window_mode == "Windowed" and prefs.resolution != "Current":
        width,height=(int(v) for v in prefs.resolution.split("×"))
        ratio=max(1,host.devicePixelRatioF())
        logical=(round(width/ratio),round(height/ratio))
        if logical[0]<host.minimumWidth() or logical[1]<host.minimumHeight():
            raise ValueError("该物理分辨率在当前DPI下低于客户端最小窗口；未修改分辨率")
    # Changing focus/size never resumes stale controls or re-arms a vehicle.
    host.backend.lock_control("video settings applied")
    host.control.release_all(host.state,host.registry)
    host._control_mouse_point = None
    host.invalidate_transient_overlay_scene()
    from .window_shortcuts import set_fullscreen
    set_fullscreen(host, prefs.window_mode == "Borderless")
    if prefs.window_mode == "Windowed" and logical is not None:
        host.showNormal()
        host.resize(*logical)
    host.invalidate_transient_overlay_scene()
    host.frame_scheduler.force()


def restore_startup_window(host):
    # No settings file = preserve the historical 1280×720 initial window.
    if preferences_path().is_file():
        try:
            apply_window_settings(host,current_preferences())
        except ValueError:
            # A moved high-DPI display can make yesterday's saved mode invalid.
            import logging
            logging.getLogger(__name__).warning("Saved resolution incompatible with current DPI; using current window")


def open_video_settings(host, embedded=False):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QComboBox,QDialog,QFormLayout,QHBoxLayout,QLabel,
                                  QPushButton,QTabWidget,QVBoxLayout,QMessageBox)
    from .settings_ui import ComboBox as QComboBox, add_setting_row
    if not embedded:
        host.backend.lock_control("video settings opened")
        host.control.release_all(host.state,host.registry)
    prefs=replace(current_preferences(), window_mode="Borderless" if host.isFullScreen() else "Windowed")
    from .settings_ui import SettingsDialogBase
    dialog=QDialog(host) if embedded else SettingsDialogBase(host)
    try:
        dialog.setWindowTitle("POLAR · 视频设置")
        if embedded:
            dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.resize(1000,720);dialog.setMinimumSize(740,580)
        if embedded:dialog.setMinimumSize(0,0)
        dialog.setObjectName("JelloVideoSettings")
        root=QVBoxLayout(dialog) if embedded else dialog.panel.layout()
        if embedded:
            root.setContentsMargins(0,0,0,0)
        title=QLabel("视频设置");title.setStyleSheet("font-size:30px;font-weight:600;padding:12px;")
        root.addWidget(title)
        title.setVisible(not embedded)
        from PySide6.QtWidgets import QWidget
        basic=QWidget();advanced=QWidget();basic_form=QFormLayout(basic);advanced_form=QFormLayout(advanced)
        basic_form.setSpacing(6);advanced_form.setSpacing(6)
        for form in (basic_form,advanced_form):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            if embedded:
                form.setContentsMargins(0,0,0,0)
        from .settings_ui import ScrollArea as QScrollArea
        if embedded:
            # 连续列表直接取得页面所有权，避免隐藏旧ScrollArea继续调整已移走的子页。
            from PySide6.QtWidgets import QGroupBox
            content=QWidget()
            content_layout=QVBoxLayout(content)
            content_layout.setContentsMargins(0,24,24,0)
            content_layout.setSpacing(12)
            for widget,label in ((basic,""),(advanced,"性能与画面")):
                box=QGroupBox(label)
                box_layout=QVBoxLayout(box)
                box_layout.setContentsMargins(0,0,0,0)
                widget.setParent(box)
                box_layout.addWidget(widget)
                content_layout.addWidget(box)
                if widget is basic:
                    box.setObjectName("DisplayInformation")
            content_layout.addStretch()
            scroll=QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            root.addWidget(scroll,1)
        else:
            tabs=QTabWidget()
            root.addWidget(tabs)
            for widget,label in ((basic,"视频"),(advanced,"高级视频")):
                scroll=QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(widget)
                tabs.addTab(scroll,label)
        controls={}
        def combo(form,key,label,rows,current):
            widget=QComboBox()
            for text,value in rows:
                widget.addItem(text,value)
            widget.setCurrentIndex(max(0,widget.findData(current)))
            add_setting_row(form,label,widget);controls[key]=widget
            return widget
        screen=host.screen()
        actual_hz=screen.refreshRate() if screen else 0
        geom=screen.geometry() if screen else None
        add_setting_row(basic_form,"当前显示器",QLabel(f"{screen.name() if screen else '未知'}  ·  {actual_hz:.1f} Hz"))
        mode=combo(basic_form,"window_mode","显示模式",[("窗口","Windowed"),("全屏（无边框）","Borderless")],prefs.window_mode)
        resolution=combo(basic_form,"resolution","窗口渲染分辨率（物理像素）",[("保持当前","Current"),*((v,v) for v in ("1280×720","1600×900","1920×1080","2560×1440","3840×2160"))],prefs.resolution)
        resolution.setEnabled(mode.currentData()=="Windowed")
        mode.currentIndexChanged.connect(lambda _:resolution.setEnabled(mode.currentData()=="Windowed"))
        combo(basic_form,"hud_hz","HUD刷新节奏",[("跟随现有HUD设置",0),*((f"{v} Hz",v) for v in (60,75,90,120,144,165,240))],prefs.hud_hz)
        note=QLabel("无边框模式使用Windows当前显示模式；此页不会伪装修改显示器物理刷新率。\nWindows全屏保留Qt建议的1像素合成边线，以兼容OpenGL窗口和弹窗。\n窗口分辨率按DPR换算；裁判逻辑、KMC与图传不随HUD帧率限额降频。")
        note.setWordWrap(True);basic_form.addRow(note)
        combo(advanced_form,"fps_limit","最大渲染帧率",[("跟随HUD节奏",0),*((str(v)+" FPS",v) for v in (30,60,75,90,120,144,165,240))],prefs.fps_limit)
        combo(advanced_form,"vsync","等待垂直同步（重启生效）",[("已启用",True),("已禁用",False)],prefs.vsync)
        combo(advanced_form,"msaa","多重采样抗锯齿（重启生效）",[("无",0),("2× MSAA",2),("4× MSAA",4),("8× MSAA",8)],prefs.msaa)
        combo(advanced_form,"video_detail","图传显示细节",[("保留当前优化","Legacy"),("720p预算","720p"),("1080p预算","1080p"),("原生分辨率","Native")],prefs.video_detail)
        combo(advanced_form,"video_filter","图传纹理过滤",[("保留当前","Auto"),("线性过滤","Linear"),("最近邻","Nearest")],prefs.video_filter)
        from .graphics_devices import adapter_rows, device_summary, graphics_inventory
        rows, inventory_error = adapter_rows(prefs.render_adapter)
        adapter = combo(advanced_form,'render_adapter','渲染显卡（Esc D3D，重启生效）',rows,prefs.render_adapter)
        adapter.setObjectName('RenderAdapter')
        diagnostics = QLabel(device_summary(host))
        diagnostics.setObjectName('GraphicsDiagnostics')
        diagnostics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        advanced_form.addRow(diagnostics)
        graphics_note = QLabel('主窗口 QOpenGLWidget 使用 WGL：显卡由 Windows/驱动决定，不能用 Qt Quick 的 D3D 选项切换。\n'
            '上方选择用于 Esc 独立 D3D 设备；主窗口请在 Windows 图形设置选择此客户端，再重启并核对实际设备。\n'
            + (('显卡枚举失败：'+inventory_error) if inventory_error else ''))
        advanced_form.addRow(graphics_note)
        refresh_graphics = QPushButton('刷新设备诊断')
        def refresh_device_info():
            graphics_inventory.cache_clear()
            refreshed, error = adapter_rows(adapter.currentData())
            selected = adapter.currentData()
            adapter.clear()
            for label,value in refreshed:adapter.addItem(label,value)
            adapter.setCurrentIndex(max(0,adapter.findData(selected)))
            diagnostics.setText(device_summary(host) + ('\n枚举失败：'+error if error else ''))
        refresh_graphics.clicked.connect(refresh_device_info)
        advanced_form.addRow(refresh_graphics)
        if os.name == 'nt':
            windows_graphics = QPushButton('打开 Windows 图形设置（主窗口 OpenGL）')
            def open_windows_graphics():
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl('ms-settings:display-advancedgraphics'))
            windows_graphics.clicked.connect(open_windows_graphics)
            advanced_form.addRow(windows_graphics)
        quality=QComboBox();quality.addItems(("自定义","低（图传720p / 无MSAA）","中（图传1080p / 2× MSAA）","高（原生图传 / 4× MSAA）"))
        from .settings_ui import SettingsRow
        advanced_form.insertRow(0,SettingsRow(QLabel("画面质量"),quality))
        def set_quality(index):
            if not index:return
            detail,aa=(("720p",0),("1080p",2),("Native",4))[index-1]
            controls["video_detail"].setCurrentIndex(controls["video_detail"].findData(detail))
            controls["msaa"].setCurrentIndex(controls["msaa"].findData(aa))
            controls["video_filter"].setCurrentIndex(controls["video_filter"].findData("Linear"))
        quality.currentIndexChanged.connect(set_quality)
        advanced_form.addRow(QLabel("任何画质档都保留液态玻璃、原版MC像素层和清晰UI字体；\n仅调整图传采样与Qt呈现，未提供NVIDIA Reflex/FSR等虚假引擎选项。"))
        status=QLabel("修改分辨率会锁定操控；重新操作车辆前须再次人工Arm。");status.setWordWrap(True);root.addWidget(status)
        buttons=QHBoxLayout();root.addLayout(buttons)
        crosshair=QPushButton("准星设置 / 分享码")
        def open_crosshair():
            from ..cs2_crosshair import open_crosshair_editor
            open_crosshair_editor(host)
        crosshair.clicked.connect(open_crosshair);buttons.addWidget(crosshair)
        buttons.addStretch()
        apply=QPushButton("应用更改");back=QPushButton("返回")
        buttons.addWidget(apply);buttons.addWidget(back)
        def apply_draft():
            updated=validate_preferences({key:widget.currentData() for key,widget in controls.items()})
            apply_window_settings(host,updated)
            save_preferences(updated)
            status.setText("已保存设置；渲染显卡/VSync/MSAA需重启。OpenGL由Windows/驱动控制，请核对实际设备。液态玻璃未更改。")
        def apply_changes():
            try:
                apply_draft()
            except (OSError,ValueError) as exc:
                QMessageBox.warning(dialog,"未应用",str(exc))
        apply.clicked.connect(apply_changes);back.clicked.connect(dialog.accept)
        def reset_draft():
            defaults=asdict(VideoPreferences())
            for key,widget in controls.items():
                widget.setCurrentIndex(widget.findData(defaults[key]))
            quality.setCurrentIndex(0)
        dialog.apply_draft=apply_draft
        dialog.reset_draft=reset_draft
        dialog.controls=controls
        if embedded:
            # 显示信息放在顶部；可编辑项按本体PC页的连续设置列表排列。
            for index in range(3):
                taken=basic_form.takeRow(1)
                advanced_form.insertRow(index+1,taken.fieldItem.widget())
            note.hide()
            note.setParent(dialog)
            add_setting_row(basic_form,"屏幕输出",QLabel(
                f"{geom.width()} × {geom.height()}" if geom else "未知"))
            for row in basic.findChildren(SettingsRow):
                row.setProperty("compact",True)
            for label in advanced.findChildren(QLabel):
                if "任何画质档" in label.text():
                    label.hide()
            status.hide()
        for label in dialog.findChildren(QLabel):label.setWordWrap(True)
        from .settings_ui import apply_widgets as apply_jello_widgets
        apply_jello_widgets(dialog)
        if embedded:
            crosshair.hide();apply.hide();back.hide()
            return dialog
        from .emergency_shortcut import install_estop_shortcut
        dialog.estop_shortcut=install_estop_shortcut(dialog,host)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
    except Exception as error:
        # Embedded pages belong to the host until their caller attaches them.
        # Retire only this acquired tree if construction never hands it off.
        try:
            dialog.deleteLater()
        finally:
            raise error
