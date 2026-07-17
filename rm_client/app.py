from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Any

from .model import (
    CATEGORIES,
    GUI_MODES,
    GuiModeManager,
    Module,
    ModuleRegistry,
    RobotState,
    Setting,
    build_default_registry,
)
from .transports import CameraFeed, MQTTTransport, SerialBridgeStub, UDPVideoReceiver
from .vision import MockVisionPipeline, VisionTarget


BG = "#090a0d"
PANEL_BG = "#111114"
PANEL_BG_2 = "#17171b"
TEXT = "#ffffff"
MUTED = "#9b9ba0"
HOVER = "#24242a"
ACCENT = "#56fff0"
PINK = "#ff65df"
GREEN = "#4caf50"
RED = "#ff5555"


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class RMClientApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("POLAR-RM Custom Client")
        self.root.geometry("1280x720")
        self.root.minsize(1000, 620)
        self.root.configure(bg=BG)
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.registry = build_default_registry()
        self.state = RobotState()
        self.mode_manager = GuiModeManager("PolarPanel")
        self.vision = MockVisionPipeline()
        self.camera = CameraFeed(0)
        self.camera.start()
        self.mqtt = MQTTTransport()
        self.udp = UDPVideoReceiver()
        self.serial = SerialBridgeStub()

        self.gui_visible = True
        self.selected_category = "Combat"
        self.selected_module = self.registry.get("ArmorAutoAim") or self.registry.modules[0]
        self.search_query = ""
        self.search_focus = False
        self.hotspots: list[dict[str, Any]] = []
        self.drag: dict[str, Any] | None = None
        self.mouse = (0, 0)
        self.toasts: list[dict[str, Any]] = []
        self.module_expanded: set[str] = {"ArmorAutoAim", "EnergyAutoAim", "RobotOverlay"}
        self.photo = None
        self.last_frame = time.perf_counter()
        self.fps_samples: list[float] = []

        self.old_panels = {
            category: {"x": 20 + i * 160, "y": 20, "expanded": category in ("Combat", "Vision")}
            for i, category in enumerate(CATEGORIES)
        }
        self.new_panels = {
            category: {"scroll": 0.0, "x": 0.0, "y": 36.0}
            for category in CATEGORIES
        }
        self.dropdown_panels = {
            category: {"x": 20 + i * 260, "y": 52, "expanded": True}
            for i, category in enumerate(CATEGORIES[:4])
        }

        self.fonts = {
            "title": ("Segoe UI", 18, "bold"),
            "subtitle": ("Segoe UI", 10),
            "body": ("Segoe UI", 11),
            "small": ("Segoe UI", 9),
            "mono": ("Consolas", 10),
            "module": ("Segoe UI", 12),
            "bold": ("Segoe UI", 11, "bold"),
            "icon": ("Segoe UI Symbol", 14, "bold"),
        }

        self.root.bind("<KeyPress>", self.on_key)
        self.canvas.bind("<ButtonPress-1>", lambda event: self.on_mouse_down(event, 1))
        self.canvas.bind("<ButtonPress-3>", lambda event: self.on_mouse_down(event, 3))
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<ButtonRelease-3>", self.on_mouse_up)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<Motion>", self.on_mouse_move)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)

        self.sync_mode_setting()
        self.toast("ClickGUI 已就绪：PolarPanel")

    def run(self) -> None:
        self.loop()
        self.root.mainloop()

    def loop(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self.last_frame)
        self.last_frame = now
        if dt > 0:
            self.fps_samples.append(1.0 / dt)
            if len(self.fps_samples) > 30:
                self.fps_samples.pop(0)
            self.state.fps = sum(self.fps_samples) / len(self.fps_samples)
        self.state.camera_connected = self.camera.connected
        self.state.mqtt_connected = self.mqtt.connected
        self.state.udp_video_connected = self.udp.frames_received > 0
        self.state.serial_connected = self.serial.connected
        self.state.tick(dt)
        self.registry.tick(self.state, dt)
        self.render()
        self.root.after(16, self.loop)

    def render(self) -> None:
        self.canvas.delete("all")
        self.hotspots.clear()
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.draw_video(width, height)
        target = self.vision.process(width, height, self.state, "energy" if self.registry.get("EnergyAutoAim") and self.registry.get("EnergyAutoAim").enabled else "armor")
        self.draw_rm_hud(width, height, target)
        if self.gui_visible:
            self.draw_screen_dim(width, height)
            mode = self.mode_manager.mode
            if mode == "PolarOld":
                self.draw_old_gui(width, height)
            elif mode == "PolarPanel":
                self.draw_panel_gui(width, height)
            elif mode == "PolarNew":
                self.draw_new_gui(width, height)
            else:
                self.draw_dropdown_gui(width, height)
            self.draw_mode_switcher(width, height)
            self.draw_toasts(width, height)

    def draw_video(self, width: int, height: int) -> None:
        source = self.registry.get("CameraFeed")
        use_camera = source is None or source.get_setting("Source") is None or source.get_setting("Source").value == "Webcam"
        if use_camera:
            frame = self.camera.read_rgb((width, height))
            if frame is not None:
                try:
                    header = f"P6\n{width} {height}\n255\n".encode("ascii")
                    self.photo = tk.PhotoImage(data=header + frame.tobytes(), format="PPM")
                    self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
                    return
                except Exception:
                    pass
        self.draw_synthetic_video(width, height)

    def draw_synthetic_video(self, width: int, height: int) -> None:
        self.canvas.create_rectangle(0, 0, width, height, fill="#10141a", outline="")
        horizon = int(height * 0.58)
        self.canvas.create_rectangle(0, 0, width, horizon, fill="#151a24", outline="")
        self.canvas.create_rectangle(0, horizon, width, height, fill="#0c1114", outline="")
        for x in range(-width, width * 2, 80):
            self.canvas.create_line(x, horizon, x - width // 3, height, fill="#1f2b32")
        for y in range(horizon, height, 36):
            self.canvas.create_line(0, y, width, y, fill="#1d2a31")
        cx = width * 0.5 + math.sin(time.perf_counter()) * width * 0.18
        self.canvas.create_rectangle(cx - 62, horizon - 110, cx + 62, horizon - 12, fill="#20262f", outline="#384650", width=2)
        self.canvas.create_text(width / 2, height - 26, text="合成图传 - 摄像头不可用", fill="#9aa6ac", font=self.fonts["small"])

    def draw_rm_hud(self, width: int, height: int, target: VisionTarget) -> None:
        hud = self.registry.get("HUD")
        if hud and hud.get_setting("Watermark") and hud.get_setting("Watermark").value:
            self.canvas.create_text(12, 10, anchor="nw", text=f"POLAR-RM ({self.state.fps:.0f} FPS)", fill=ACCENT, font=("Segoe UI", 14, "bold"))
        overlay_enabled = any(
            (self.registry.get(name) and self.registry.get(name).enabled)
            for name in ("ArmorAutoAim", "EnergyAutoAim", "RobotOverlay")
        )
        if overlay_enabled:
            self.draw_target_overlay(target)
        if self.registry.get("Crosshair") and self.registry.get("Crosshair").enabled:
            self.draw_crosshair(width, height)
        if hud and hud.get_setting("Target HUD") and hud.get_setting("Target HUD").value:
            self.draw_target_hud(22, height - 118, target)
        if hud and hud.get_setting("Dynamic Island") and hud.get_setting("Dynamic Island").value:
            self.draw_dynamic_island(width)
        if hud and hud.get_setting("Module List") and hud.get_setting("Module List").value:
            self.draw_module_list(width)

    def draw_target_overlay(self, target: VisionTarget) -> None:
        x, y, w, h = target.x, target.y, target.w, target.h
        color = ACCENT if self.state.target_locked else RED
        self.canvas.create_rectangle(x, y, x + w, y + h, outline=color, width=2)
        self.canvas.create_line(x, y, x + 20, y, fill=color, width=4)
        self.canvas.create_line(x, y, x, y + 20, fill=color, width=4)
        self.canvas.create_line(x + w, y, x + w - 20, y, fill=color, width=4)
        self.canvas.create_line(x + w, y, x + w, y + 20, fill=color, width=4)
        label = f"{target.label}  {target.confidence:.0%}  {self.state.target_distance_m:.1f}m"
        self.draw_pill(x, y - 26, label, color)

    def draw_crosshair(self, width: int, height: int) -> None:
        cx, cy = width / 2, height / 2
        heat_ratio = self.state.current_heat / max(1.0, self.state.max_heat)
        color = RED if heat_ratio > 0.78 else ACCENT
        size_setting = self.registry.get("Crosshair").get_setting("Size") if self.registry.get("Crosshair") else None
        size = float(size_setting.value if size_setting else 18)
        self.canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=color, outline="")
        self.canvas.create_line(cx - size, cy, cx - 6, cy, fill=color, width=2)
        self.canvas.create_line(cx + 6, cy, cx + size, cy, fill=color, width=2)
        self.canvas.create_line(cx, cy - size, cx, cy - 6, fill=color, width=2)
        self.canvas.create_line(cx, cy + 6, cx, cy + size, fill=color, width=2)

    def draw_target_hud(self, x: float, y: float, target: VisionTarget) -> None:
        self.glow_rect(x, y, 210, 78, 10, "#0a0a0c")
        self.round_rect(x, y, 210, 78, 8, fill="#121217", outline="#2a2a33")
        self.canvas.create_text(x + 14, y + 13, anchor="nw", text="RobotTargetHUD", fill=TEXT, font=self.fonts["bold"])
        hp_ratio = self.state.target_health / max(1, self.state.target_max_health)
        self.draw_bar(x + 14, y + 42, 180, 8, hp_ratio, GREEN, "#31313a")
        self.canvas.create_text(x + 14, y + 56, anchor="nw", text=f"ID {self.state.target_id}  {self.state.target_distance_m:.1f}m  LOCK {self.state.target_locked}", fill=MUTED, font=self.fonts["small"])

    def draw_dynamic_island(self, width: int) -> None:
        text = f"{self.state.current_stage}  {self.state.stage_countdown_sec}s  {self.state.last_event}"
        island_w = max(250, min(520, len(text) * 7 + 44))
        x = width / 2 - island_w / 2
        y = 22
        self.round_rect(x, y, island_w, 30, 14, fill="#050506", outline="#2b2d34")
        self.canvas.create_text(x + island_w / 2, y + 15, text=text, fill=TEXT, font=self.fonts["small"])

    def draw_module_list(self, width: int) -> None:
        modules = [m for m in self.registry.enabled_modules() if m.name not in ("HUD",)]
        x = width - 18
        y = 18
        for i, module in enumerate(sorted(modules, key=lambda item: len(item.name), reverse=True)):
            color = self.accent_color(i)
            self.canvas.create_text(x, y + i * 17, anchor="ne", text=module.name, fill=color, font=self.fonts["body"])

    def draw_screen_dim(self, width: int, height: int) -> None:
        self.canvas.create_rectangle(0, 0, width, height, fill="#000000", stipple="gray50", outline="")

    def draw_old_gui(self, _width: int, _height: int) -> None:
        for category in CATEGORIES:
            panel = self.old_panels[category]
            x, y = panel["x"], panel["y"]
            panel_w, row_h = 140, 20
            self.round_rect(x, y, panel_w, row_h, 3, fill="#101014", outline="#25252b")
            self.canvas.create_text(x + 8, y + 10, anchor="w", text=self.category_icon(category), fill=TEXT, font=self.fonts["icon"])
            self.canvas.create_text(x + 30, y + 10, anchor="w", text=category, fill=TEXT, font=self.fonts["bold"])
            self.add_hotspot(x, y, panel_w, row_h, "old_drag", category)
            self.add_hotspot(x, y, panel_w, row_h, "old_expand", category, button=3)
            if not panel["expanded"]:
                continue
            yy = y + row_h
            for module in self.registry.by_category(category):
                bg = "#18181d" if not module.enabled else "#f7f7f7"
                fg = "#151515" if module.enabled else MUTED
                self.canvas.create_rectangle(x, yy, x + panel_w, yy + row_h, fill=bg, outline="#25252b")
                self.canvas.create_text(x + 9, yy + 10, anchor="w", text=module.name, fill=fg, font=self.fonts["small"])
                self.add_hotspot(x, yy, panel_w, row_h, "module_toggle", module.name, button=1)
                self.add_hotspot(x, yy, panel_w, row_h, "module_expand", module.name, button=3)
                yy += row_h
                if module.name in self.module_expanded:
                    yy = self.draw_inline_settings(module, x + 5, yy, panel_w - 10, compact=True)

    def draw_panel_gui(self, width: int, height: int) -> None:
        scale = self.get_gui_scale()
        panel_w, panel_h = 600 * scale, 400 * scale
        x, y = width / 2 - panel_w / 2, height / 2 - panel_h / 2
        self.glow_rect(x, y, panel_w, panel_h, 12 * scale, "#111115")
        self.round_rect(x, y, panel_w, panel_h, 8, fill="#101014", outline="#2e3036")
        self.draw_profile_widget(x + 14 * scale, y + 14 * scale, scale)
        self.draw_panel_category_bar(x + 420 * scale, y + 20 * scale, scale)
        self.draw_panel_module_list(x + 12 * scale, y + 43 * scale, 160 * scale, panel_h - 80 * scale, scale)
        self.draw_settings_area(x + 187 * scale, y + 43 * scale, 405 * scale, panel_h - 80 * scale, self.selected_module, scale)
        self.draw_search_bar(x + panel_w / 2 - 100 * scale, y + panel_h + 15 * scale, 200 * scale, 22 * scale, scale)

    def draw_new_gui(self, width: int, _height: int) -> None:
        start_x = width / 2 - 380
        for i, category in enumerate(CATEGORIES):
            panel = self.new_panels[category]
            panel["x"] = start_x + i * 128
            panel["y"] = 36
            self.draw_new_category_panel(category, panel["x"], panel["y"])

    def draw_dropdown_gui(self, _width: int, _height: int) -> None:
        for i, category in enumerate(["Combat", "Render", "Movement", "System"]):
            panel = self.dropdown_panels.setdefault(category, {"x": 20 + i * 260, "y": 52, "expanded": True})
            self.draw_dropdown_panel(category, panel["x"], panel["y"])

    def draw_mode_switcher(self, width: int, height: int) -> None:
        x = width - 560
        y = height - 36
        self.round_rect(x - 8, y - 6, 548, 28, 8, fill="#08080a", outline="#252932")
        for i, mode in enumerate(GUI_MODES):
            bx = x + i * 135
            active = mode == self.mode_manager.mode
            self.round_rect(bx, y, 128, 18, 5, fill="#ffffff" if active else "#1a1c22", outline="#333842")
            self.canvas.create_text(bx + 64, y + 9, text=mode.replace("Polar", "P "), fill="#08080a" if active else TEXT, font=self.fonts["small"])
            self.add_hotspot(bx, y, 128, 18, "gui_mode", mode)

    def draw_profile_widget(self, x: float, y: float, scale: float) -> None:
        self.canvas.create_text(x, y, anchor="nw", text="POLAR-RM", fill=TEXT, font=("Segoe UI", int(14 * scale), "bold"))
        self.canvas.create_text(x, y + 20 * scale, anchor="nw", text=f"Scale {self.get_gui_scale():.2f}  F9 cycle", fill=MUTED, font=self.fonts["small"])

    def draw_panel_category_bar(self, x: float, y: float, scale: float) -> None:
        for i, category in enumerate(CATEGORIES):
            cx = x + i * 25 * scale
            active = category == self.selected_category
            color = TEXT if active else "#78787f"
            if active:
                self.canvas.create_oval(cx - 5, y - 4, cx + 19, y + 20, outline="#ffffff", width=1)
            self.canvas.create_text(cx + 7, y + 8, text=self.category_icon(category), fill=color, font=self.fonts["icon"])
            self.add_hotspot(cx - 5, y - 8, 24, 30, "select_category", category)

    def draw_panel_module_list(self, x: float, y: float, w: float, h: float, scale: float) -> None:
        self.round_rect(x, y, w, h, 4, fill="#ffffff", outline="", stipple="gray75")
        self.canvas.create_text(x + 10 * scale, y + 13 * scale, anchor="w", text=self.selected_category, fill=TEXT, font=("Segoe UI", int(14 * scale), "bold"))
        modules = self.panel_modules()
        yy = y + 38 * scale
        for module in modules:
            hovered_bg = "#24242a"
            if module == self.selected_module:
                self.round_rect(x + 5, yy - 4, w - 10, 19 * scale, 4, fill=hovered_bg, outline="")
            color = TEXT if module.enabled else "#aaaaaa"
            font = ("Segoe UI", int(11 * scale), "bold" if module.enabled else "normal")
            if module.enabled:
                self.canvas.create_text(x + 10 * scale, yy + 4, anchor="w", text=module.name, fill=TEXT, font=font)
            else:
                self.canvas.create_text(x + 10 * scale, yy + 4, anchor="w", text=module.name, fill=color, font=font)
            self.add_hotspot(x, yy - 6, w, 20 * scale, "module_select", module.name, button=1)
            self.add_hotspot(x, yy - 6, w, 20 * scale, "module_toggle", module.name, button=3)
            yy += 21 * scale

    def draw_settings_area(self, x: float, y: float, w: float, h: float, module: Module, scale: float) -> None:
        self.round_rect(x, y, w, h, 4, fill="#1b1b20", outline="#2a2a31")
        self.canvas.create_text(x + 12 * scale, y + 16 * scale, anchor="w", text=module.name, fill=TEXT, font=("Segoe UI", int(15 * scale), "bold"))
        self.draw_toggle(x + w - 54 * scale, y + 11 * scale, module.enabled, scale, action=("module_toggle", module.name))
        yy = y + 44 * scale
        if module.settings:
            for setting in module.settings:
                yy = self.draw_setting_row(setting, x + 12 * scale, yy, w - 24 * scale, scale)
        else:
            self.canvas.create_text(x + 12 * scale, yy, anchor="nw", text=module.description or "No settings", fill=MUTED, font=self.fonts["small"], width=w - 24 * scale)

    def draw_search_bar(self, x: float, y: float, w: float, h: float, scale: float) -> None:
        fill = "#16161b" if not self.search_focus else "#202026"
        self.round_rect(x, y, w, h, h / 2, fill=fill, outline="#454851")
        text = self.search_query if self.search_query else "Search modules..."
        color = TEXT if self.search_query else "#77777c"
        self.canvas.create_text(x + 12 * scale, y + h / 2, anchor="w", text=text, fill=color, font=self.fonts["small"])
        self.add_hotspot(x, y, w, h, "search_focus", None)

    def draw_new_category_panel(self, category: str, x: float, y: float) -> None:
        modules = self.registry.by_category(category)
        panel_h = 20 + min(240, 20 * len(modules))
        self.glow_rect(x - 4, y - 4, 128, panel_h + 8, 8, "#000000")
        self.round_rect(x, y, 120, panel_h, 6, fill="#171717", outline="#292a30")
        self.canvas.create_line(x + 3, y + 1, x + 117, y + 1, fill=ACCENT, width=2)
        self.canvas.create_text(x + 8, y + 10, anchor="w", text=category, fill=TEXT, font=("Segoe UI", 9, "bold"))
        self.add_hotspot(x, y, 120, panel_h, "select_category", category)
        yy = y + 22
        for module in modules:
            row_h = 20 + (18 * len(module.settings) if module.name in self.module_expanded else 0)
            active_fill = "#22262b" if module.enabled else "#171717"
            self.canvas.create_rectangle(x + 1, yy, x + 119, yy + 20, fill=active_fill, outline="")
            self.canvas.create_text(x + 8, yy + 10, anchor="w", text=module.name, fill=TEXT if module.enabled else "#bbbbbb", font=self.fonts["small"])
            self.add_hotspot(x, yy, 120, 20, "module_toggle", module.name, button=1)
            self.add_hotspot(x, yy, 120, 20, "module_expand", module.name, button=3)
            yy += 20
            if module.name in self.module_expanded:
                yy = self.draw_inline_settings(module, x + 5, yy, 110, compact=True)
            if yy > y + panel_h - 4:
                break

    def draw_dropdown_panel(self, category: str, x: float, y: float) -> None:
        modules = self.registry.by_category(category)
        width = 240
        header_h = 34
        body_h = min(410, 24 * len(modules) + 160)
        self.round_rect(x, y, width, header_h + body_h, 8, fill="#2a2a2a", outline="")
        self.canvas.create_text(x + 12, y + 17, anchor="w", text=f"{self.category_icon(category)}  {category}", fill=TEXT, font=("Segoe UI", 16, "bold"))
        yy = y + header_h
        for idx, module in enumerate(modules):
            if module.enabled:
                c1 = self.accent_color(idx)
                self.canvas.create_rectangle(x, yy, x + width, yy + 24, fill=c1, outline="")
            else:
                self.canvas.create_rectangle(x, yy, x + width, yy + 24, fill="#101412", outline="#343434")
            self.canvas.create_text(x + 12, yy + 12, anchor="w", text=module.name, fill=TEXT, font=self.fonts["module"])
            self.add_hotspot(x, yy, width, 24, "module_toggle", module.name, button=1)
            self.add_hotspot(x, yy, width, 24, "module_expand", module.name, button=3)
            yy += 24
            if module.name in self.module_expanded:
                yy = self.draw_inline_settings(module, x + 10, yy, width - 20, compact=False)

    def draw_inline_settings(self, module: Module, x: float, y: float, w: float, compact: bool) -> float:
        scale = 0.8 if compact else 1.0
        for setting in module.settings[:6]:
            y = self.draw_setting_row(setting, x, y, w, scale, compact=compact)
        return y

    def draw_setting_row(self, setting: Setting, x: float, y: float, w: float, scale: float = 1.0, compact: bool = False) -> float:
        row_h = 22 * scale if not compact else 18 * scale
        font = ("Segoe UI", max(8, int(9 * scale)))
        self.canvas.create_text(x, y + row_h / 2, anchor="w", text=setting.name, fill="#d7d7dc", font=font)
        if setting.kind == "bool":
            self.draw_toggle(x + w - 34 * scale, y + 4 * scale, bool(setting.value), scale, action=("setting_toggle", setting))
        elif setting.kind == "number":
            bar_x = x + w * 0.42
            bar_w = w * 0.52
            self.draw_bar(bar_x, y + row_h / 2 - 3, bar_w, 6 * scale, setting.ratio(), PINK, "#5b5b5f")
            self.canvas.create_text(x + w - 2, y + row_h / 2 - 9 * scale, anchor="ne", text=setting.display_value(), fill=TEXT, font=font)
            self.add_hotspot(bar_x, y, bar_w, row_h, "setting_slider", setting)
        elif setting.kind == "mode":
            box_w = min(110 * scale, w * 0.55)
            box_x = x + w - box_w
            self.round_rect(box_x, y + 2 * scale, box_w, row_h - 4 * scale, 5 * scale, fill="#303033", outline="")
            self.canvas.create_text(box_x + 7 * scale, y + row_h / 2, anchor="w", text=str(setting.value), fill=TEXT, font=font)
            self.add_hotspot(box_x, y, box_w, row_h, "setting_mode_next", setting)
        return y + row_h

    def draw_toggle(self, x: float, y: float, enabled: bool, scale: float, action: tuple[str, Any] | None = None) -> None:
        w, h = 28 * scale, 14 * scale
        fill = GREEN if enabled else "#66666a"
        self.round_rect(x, y, w, h, h / 2, fill=fill, outline="")
        knob = h - 4 * scale
        kx = x + w - knob - 2 * scale if enabled else x + 2 * scale
        self.round_rect(kx, y + 2 * scale, knob, knob, knob / 2, fill=TEXT, outline="")
        if action:
            self.add_hotspot(x, y, w, h, action[0], action[1])

    def draw_toasts(self, width: int, _height: int) -> None:
        now = time.perf_counter()
        self.toasts = [toast for toast in self.toasts if now - toast["created"] < 3.0]
        for i, toast in enumerate(self.toasts):
            age = now - toast["created"]
            alpha_offset = 0 if age < 2.4 else int((age - 2.4) * 80)
            y = 70 + i * 30
            msg = toast["message"]
            tw = max(170, len(msg) * 7 + 24)
            x = width / 2 - tw / 2
            color = "#111114" if alpha_offset < 20 else "#0c0c0e"
            self.round_rect(x, y, tw, 24, 8, fill=color, outline="#343842")
            self.canvas.create_text(x + tw / 2, y + 12, text=msg, fill=TEXT, font=self.fonts["small"])

    def draw_bar(self, x: float, y: float, w: float, h: float, ratio: float, fill: str, bg: str) -> None:
        ratio = clamp(ratio, 0.0, 1.0)
        self.round_rect(x, y, w, h, h / 2, fill=bg, outline="")
        if ratio > 0:
            self.round_rect(x, y, max(h, w * ratio), h, h / 2, fill=fill, outline="")

    def draw_pill(self, x: float, y: float, text: str, color: str) -> None:
        w = max(86, len(text) * 7 + 18)
        self.round_rect(x, y, w, 20, 7, fill="#090a0d", outline=color)
        self.canvas.create_text(x + 9, y + 10, anchor="w", text=text, fill=color, font=self.fonts["small"])

    def round_rect(self, x: float, y: float, w: float, h: float, r: float, fill: str, outline: str = "", width: int = 1, stipple: str = "") -> None:
        r = min(r, w / 2, h / 2)
        points = [
            x + r, y,
            x + w - r, y,
            x + w, y,
            x + w, y + r,
            x + w, y + h - r,
            x + w, y + h,
            x + w - r, y + h,
            x + r, y + h,
            x, y + h,
            x, y + h - r,
            x, y + r,
            x, y,
        ]
        self.canvas.create_polygon(points, smooth=True, fill=fill, outline=outline, width=width, stipple=stipple)

    def glow_rect(self, x: float, y: float, w: float, h: float, r: float, color: str) -> None:
        for i, shade in enumerate(("#202026", "#18181d", "#101014")):
            pad = (3 - i) * 5
            self.round_rect(x - pad, y - pad, w + pad * 2, h + pad * 2, r + pad, fill=shade, outline="")

    def add_hotspot(self, x: float, y: float, w: float, h: float, action: str, payload: Any, button: int | None = None) -> None:
        self.hotspots.append({"rect": (x, y, w, h), "action": action, "payload": payload, "button": button})

    def on_mouse_down(self, event, button: int) -> None:
        self.mouse = (event.x, event.y)
        for hotspot in reversed(self.hotspots):
            if hotspot["button"] not in (None, button):
                continue
            x, y, w, h = hotspot["rect"]
            if x <= event.x <= x + w and y <= event.y <= y + h:
                self.dispatch(hotspot["action"], hotspot["payload"], event.x, event.y, button, hotspot["rect"])
                return
        if button == 1:
            self.search_focus = False

    def on_mouse_drag(self, event) -> None:
        self.mouse = (event.x, event.y)
        if not self.drag:
            return
        if self.drag["type"] == "slider":
            setting: Setting = self.drag["setting"]
            x, _y, w, _h = self.drag["rect"]
            setting.set_from_ratio((event.x - x) / max(1, w))
        elif self.drag["type"] == "old_panel":
            panel = self.old_panels[self.drag["category"]]
            panel["x"] = event.x - self.drag["offset_x"]
            panel["y"] = event.y - self.drag["offset_y"]

    def on_mouse_up(self, _event) -> None:
        self.drag = None

    def on_mouse_move(self, event) -> None:
        self.mouse = (event.x, event.y)

    def on_mouse_wheel(self, event) -> None:
        mode = self.mode_manager.mode
        if mode == "PolarNew":
            for category, panel in self.new_panels.items():
                x, y = panel["x"], panel["y"]
                if x <= event.x <= x + 120 and y <= event.y <= y + 260:
                    panel["scroll"] = max(0.0, panel["scroll"] - event.delta / 120 * 36)
        elif mode == "PolarPanel":
            pass

    def on_key(self, event) -> None:
        if event.keysym in ("F8", "Shift_R"):
            self.gui_visible = not self.gui_visible
            self.toast("ClickGUI 已打开" if self.gui_visible else "ClickGUI 已关闭")
            return
        if event.keysym == "F9":
            self.mode_manager.next_mode()
            self.sync_mode_setting()
            self.toast(f"GUI 模式：{self.mode_manager.mode}")
            return
        if event.keysym == "Escape":
            self.gui_visible = False
            return
        if self.search_focus:
            if event.keysym == "BackSpace":
                self.search_query = self.search_query[:-1]
            elif event.keysym == "Return":
                self.search_focus = False
            elif len(event.char) == 1 and event.char.isprintable():
                self.search_query += event.char

    def dispatch(self, action: str, payload: Any, x: float, y: float, button: int, rect) -> None:
        if action == "module_toggle":
            module = self.registry.get(payload)
            if module:
                self.registry.toggle_module(module, source="clickgui")
                self.selected_module = module
                self.toast(f"{module.name} 模块{'已启用' if module.enabled else '已关闭'}")
        elif action == "module_select":
            module = self.registry.get(payload)
            if module:
                self.selected_module = module
        elif action == "module_expand":
            if payload in self.module_expanded:
                self.module_expanded.remove(payload)
            else:
                self.module_expanded.add(payload)
        elif action == "setting_toggle":
            setting: Setting = payload
            setting.value = not bool(setting.value)
            self.after_setting_changed(setting)
        elif action == "setting_slider":
            setting = payload
            setting.set_from_ratio((x - rect[0]) / max(1, rect[2]))
            self.drag = {"type": "slider", "setting": setting, "rect": rect}
            self.after_setting_changed(setting)
        elif action == "setting_mode_next":
            setting = payload
            setting.cycle(-1 if button == 3 else 1)
            self.after_setting_changed(setting)
        elif action == "select_category":
            self.selected_category = payload
        elif action == "search_focus":
            self.search_focus = True
        elif action == "gui_mode":
            self.mode_manager.set_mode(payload)
            self.sync_mode_setting()
            self.toast(f"GUI 模式：{payload}")
        elif action == "old_expand":
            self.old_panels[payload]["expanded"] = not self.old_panels[payload]["expanded"]
        elif action == "old_drag":
            if button == 1:
                panel = self.old_panels[payload]
                self.drag = {
                    "type": "old_panel",
                    "category": payload,
                    "offset_x": x - panel["x"],
                    "offset_y": y - panel["y"],
                }

    def after_setting_changed(self, setting: Setting) -> None:
        click_gui = self.registry.get("ClickGui")
        if click_gui and setting is click_gui.get_setting("Mode"):
            self.mode_manager.set_mode(str(setting.value))
            self.toast(f"GUI 模式：{setting.value}")

    def sync_mode_setting(self) -> None:
        click_gui = self.registry.get("ClickGui")
        if click_gui:
            mode = click_gui.get_setting("Mode")
            if mode:
                mode.value = self.mode_manager.mode

    def panel_modules(self) -> list[Module]:
        if self.search_query:
            q = self.search_query.lower()
            return sorted(
                [module for module in self.registry.modules if q in module.name.lower()],
                key=lambda module: module.name.lower(),
            )
        return self.registry.by_category(self.selected_category)

    def get_gui_scale(self) -> float:
        module = self.registry.get("ClickGui")
        setting = module.get_setting("Scale") if module else None
        return float(setting.value) if setting else 1.0

    def category_icon(self, category: str) -> str:
        return {
            "Combat": "X",
            "Movement": ">",
            "Vision": "O",
            "Strategy": "#",
            "System": "*",
            "Render": "E",
        }.get(category, "?")

    def accent_color(self, index: int) -> str:
        colors = ["#ff65df", "#56fff0", "#7c80ff", "#75ff7a", "#ffd166", "#ff6b6b"]
        return colors[index % len(colors)]

    def toast(self, message: str) -> None:
        self.toasts.append({"message": message, "created": time.perf_counter()})
        self.state.push_event(message)


def main() -> None:
    RMClientApp().run()
