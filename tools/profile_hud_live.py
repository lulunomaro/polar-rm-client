"""Bounded real-OpenGL HUD probe using an isolated mock-only configuration.

Capture/inference require explicit opt-in; real serial/DT7/MQTT output is always
disabled. Draw timing precedes capture, never from offscreen Qt screenshots.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import functools
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1392)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--interactive", action="store_true", help="Keep the mock-only window open after measurements for native UI qualification")
    parser.add_argument("--cprofile", action="store_true")
    parser.add_argument("--webcam", action="store_true", help="Opt in to the configured webcam, never to real control")
    parser.add_argument("--local-vision", action="store_true", help="Opt in to existing local inference; requires --webcam")
    parser.add_argument("--vsync", type=int, choices=(0, 1), default=1)
    parser.add_argument("--stencil", type=int, choices=(-1, 0, 8), default=0)
    parser.add_argument("--preview-controls", action="store_true", help="Retain enabled RobotLink module presentation, while all output stays mock")
    parser.add_argument("--scene", choices=("hud", "clickgui", "economy", "cs2-kill",'startup','escape','settings','keybinds'), default="hud")
    parser.add_argument('--source-root', type=Path, help='Explicit local baseline checkout; no downloads or source edits')
    parser.add_argument('--fps-limit', type=int, choices=(60,75,90,120,144), default=90)
    parser.add_argument('--startup-trace', action='store_true', help='Record launch phases and event-loop long tasks; use --warmup 0 for initial frames')
    parser.add_argument("--cs2-assets", type=Path, help="Explicit local personal pack to copy into the isolated test runtime")
    parser.add_argument("--settings", help="Open an existing module's Jello settings (requires --scene clickgui)")
    parser.add_argument("--cycles", type=int, default=0, help="After timing, exercise up to 100 rapid ClickGUI/ESC reversals; mock control only")
    args = parser.parse_args()
    if args.source_root:
        ROOT = args.source_root.resolve()
        sys.path.insert(0,str(ROOT))
    if not 0 < args.seconds <= 60 or not 0 <= args.warmup <= 30:
        parser.error("Use bounded positive timing budgets")
    if args.local_vision and not args.webcam:
        parser.error("Local vision requires the explicit webcam opt-in")
    if args.settings and args.scene != "clickgui":
        parser.error("Settings require the explicit ClickGUI scene")
    if not 0 <= args.cycles <= 100:
        parser.error("Use a bounded cycle count (0-100)")
    if args.out.exists():
        parser.error("Output directory must be new")
    args.out.mkdir(parents=True)
    user = args.out.resolve() / "isolated-user-data"
    config_path = user / "configs" / "default.json"
    config_path.parent.mkdir(parents=True)
    source = ROOT / "user_data" / "configs" / "default.json"
    if not source.exists():
        source = ROOT / "configs" / "default.json"
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    modules = config.setdefault("modules", {})
    for name in ("RobotLink", "ControllerAngles", "MQTTLink", "UDPVideo", "SerialBridge", "TongjiVisionBridge", "AudioFeedback"):
        if name in modules:
            if name == "RobotLink" and args.preview_controls:
                continue
            modules[name]["enabled"] = False
    if args.local_vision:
        modules["TongjiVisionBridge"]["enabled"] = True
    modules["Scenario"]["settings"].update({"Mode": "LabDebug", "Mock Writes": True, "Show Startup": False})
    modules.setdefault("RobotLink", {"enabled": False, "settings": {}})["settings"].update({"Mock Writes": True, "Allow Drive": False, "Direct DT7 Egress": False, "DT7 Real TX": False})
    modules["CameraFeed"]["settings"]["Source"] = "Webcam"
    modules["ConfigManager"]["settings"]["Auto Save"] = False
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    os.environ["POLAR_USER_DATA_ROOT"] = str(user)
    os.environ["POLAR_DISABLE_LOCAL_CAPTURE"] = "0" if args.webcam else "1"
    os.environ["POLAR_VISION_SOURCE"] = "Local" if args.local_vision else "Mock"
    (user/'video_preferences.json').write_text(json.dumps({'hud_hz':args.fps_limit,'fps_limit':args.fps_limit,'vsync':bool(args.vsync)}),encoding='utf-8')
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in ("offscreen", "minimal"):
        parser.error("This probe requires an actual desktop OpenGL surface")

    launch_started = time.perf_counter()
    launch_phases = {}
    long_events = []
    def phase(name):
        launch_phases[name] = (time.perf_counter()-launch_started)*1000
    from PySide6.QtCore import QTimer, Qt, QEvent
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap, QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    from rm_client.ui.polar_app import PolarRMWindow
    phase('ui_imported')

    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setSwapInterval(args.vsync)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(args.stencil)
    QSurfaceFormat.setDefaultFormat(fmt)
    samples = defaultdict(list)
    active = False
    previous_present = None
    class ProbeApplication(QApplication):
        def notify(self, receiver, event):
            if args.startup_trace:
                started = time.perf_counter()
                try:
                    return super().notify(receiver,event)
                finally:
                    elapsed=(time.perf_counter()-started)*1000
                    if elapsed >= 8 and len(long_events)<2048:
                        long_events.append(dict(at_ms=(started-launch_started)*1000,
                            duration_ms=elapsed,receiver=receiver.metaObject().className(),event=int(event.type())))
            if not active or event.type()!=QEvent.Type.Paint:
                return super().notify(receiver,event)
            started=time.perf_counter()
            try:return super().notify(receiver,event)
            finally:samples['qt.paint.'+receiver.metaObject().className()].append((time.perf_counter()-started)*1000)
    app = ProbeApplication(["POLAR HUD mock-only OpenGL probe"])
    phase('application_created')
    if args.cs2_assets:
        from rm_client.ui.cs2_assets import Cs2Assets
        pack = Cs2Assets(args.cs2_assets)
        if not pack.available or pack.error:
            parser.error("Invalid explicit CS2 personal pack: " + pack.error)
        private = user / "cs2_ui_pack"
        private.mkdir()
        for source in pack.paths.values():
            shutil.copyfile(source, private / source.name)
        shutil.copyfile(args.cs2_assets / "manifest.json", private / "manifest.json")
    # Diagnostics belong to this probe, even when UI imports come from an
    # explicitly selected older checkout which has no sampler module yet.
    import importlib.util
    sampler_spec=importlib.util.spec_from_file_location('owned_gpu_sampler',Path(__file__).parent/'diagnostics/gpu_engines.py')
    sampler_module=importlib.util.module_from_spec(sampler_spec)
    sampler_spec.loader.exec_module(sampler_module)
    engines = sampler_module.GpuEngineSampler()
    utilization = []
    cpu_previous = [time.process_time(),time.perf_counter()]

    class ProbeWindow(PolarRMWindow):
        def ensure_camera_started(self):
            if args.webcam:
                return super().ensure_camera_started()

        def refresh_vision_runtime(self):
            if args.local_vision:
                return super().refresh_vision_runtime()

        def consume_capture_frame(self):
            if args.webcam:
                return super().consume_capture_frame()

        def paintGL(self):
            nonlocal previous_present
            started = time.perf_counter()
            super().paintGL()
            ended = time.perf_counter()
            if active:
                samples["paintGL"].append((ended - started) * 1000)
                if previous_present is not None:
                    samples["present_interval"].append((ended - previous_present) * 1000)
                previous_present = ended

    window = ProbeWindow()
    phase('window_constructed')
    def first_swap():
        if 'first_swap' not in launch_phases:
            phase('first_swap')
    window.frameSwapped.connect(first_swap)
    window.home_visible = args.scene == 'startup'
    window.gui_visible = False
    window.ui_audio_enabled = False
    window.resize(args.width, args.height)
    image = QImage(960, 540, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 960, 540)
    gradient.setColorAt(0, QColor("#9ebfc2"))
    gradient.setColorAt(1, QColor("#294357"))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(QColor("#d2e6db"))
    for x in range(0, 960, 48):
        painter.drawLine(x, 0, x, 540)
    painter.end()
    window.frame_image = image
    window.frame_pixmap = QPixmap.fromImage(image)
    window._normal_backend_mode = "webcam"
    window._normal_frame_source = "webcam"
    window._display_frame_sequence = 1

    def instrument(obj, name, label):
        original = getattr(obj, name, None)
        if not callable(original):
            return
        @functools.wraps(original)
        def traced(*a, **kw):
            started = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                if active:
                    samples[label].append((time.perf_counter() - started) * 1000)
        setattr(obj, name, traced)

    for name in ("paint_gpu_background", "paint_overlay", "paint_hud_motion_overlay", "draw_tactical_map_hud", "draw_minecraft_module_list", "draw_dynamic_island", "paint_gpu_target_hud_layer", "flush_gpu_hud_commands", "draw_vision_overlay", "draw_hero_deploy_interface", "gpu_static_ui_key", "on_tick"):
        instrument(window, name, name)
    for obj, names, prefix in (
        (window.gpu_compositor, ("prepare_blur", "prepare_treated_backdrop", "render_glass", "present"), "compositor."),
        (window.minecraft_hud, ("draw",), "minecraft."),
        (window.clickgui_gpu, ("prepare_frame", "render"), "clickgui."),
    ):
        for name in names:
            instrument(obj, name, prefix + name)
    video = QTimer(window)
    def next_frame():
        window._display_frame_sequence += 1
    video.timeout.connect(next_frame)
    if not args.webcam:
        video.start(33)
    window.show()
    phase('show_returned')
    child = None
    if args.scene=='escape':window.set_escape_menu_visible(True)
    elif args.scene=='settings':
        from rm_client.ui.cs2_settings_center import build_settings_center
        child=build_settings_center(window);child.show()
    elif args.scene=='keybinds':
        from rm_client.ui.jello_keybinds import build_keyboard_manager
        child=build_keyboard_manager(window);child.show()
    if args.scene == "clickgui":
        window.set_clickgui_visible(True, announce=False, play_sound=False)
        if args.settings:
            if window.registry.get(args.settings) is None:
                raise ValueError(f"Unknown settings module: {args.settings}")
            window.jello_state.open_settings(args.settings)
    elif args.scene == "economy":
        window.open_economy_workspace()
    elif args.scene == "cs2-kill":
        from rm_client.combat_events import CombatEvent
        fx_module = window.registry.get("EliminationFX")
        fx_module.enabled = True
        fx_module.get_setting("Preset").value = "CS2 Native"
        fx_module.get_setting("Element Visibility").value = "Icons Only"
        # Explicitly synthetic diagnostic events, never injected into the
        # referee feed/scoreboard. No sound spam and no control/transaction.
        fx_timer = QTimer(window)
        kill_index = 0
        def preview_kill():
            nonlocal kill_index
            kill_index += 1
            window.kill_fx.trigger(CombatEvent("elimination", "preview", True, time.perf_counter(),
                                               victim_id=101, combo=1+(kill_index-1)%5),
                                   combo_window=5, sound_enabled=False, volume=0,
                                   preset="CS2 Native", visibility="Icons Only")
        fx_timer.timeout.connect(preview_kill)
        fx_timer.start(800)
        preview_kill()
    profiler = None
    native_start = [0.,0.]
    utilization_timer=QTimer(window)
    def sample_utilization():
        if not active:return
        cpu,wall=time.process_time(),time.perf_counter()
        core_percent=100*(cpu-cpu_previous[0])/max(.001,wall-cpu_previous[1])
        cpu_previous[:]=[cpu,wall]
        utilization.append(dict(cpu_one_core_percent=core_percent,
            cpu_machine_percent=core_percent/max(1,os.cpu_count() or 1),gpu_engines=engines.sample()))
    utilization_timer.timeout.connect(sample_utilization)
    utilization_timer.start(500)
    def begin():
        nonlocal active, profiler
        samples.clear()
        active = True
        cpu_previous[:]=[time.process_time(),time.perf_counter()]
        native=getattr(window,'_endfield_menu',None)
        native_start[:]=[native.stats()['frames'] if native is not None else 0.,time.perf_counter()]
        if args.cprofile:
            import cProfile
            profiler = cProfile.Profile()
            profiler.enable()
        QTimer.singleShot(round(args.seconds * 1000), finish)
    def finish():
        nonlocal active
        active = False
        utilization_timer.stop();engines.close()
        if args.capture:
            window.grabFramebuffer().save(str(args.out / "scene.png"))
        if profiler is not None:
            profiler.disable()
            profiler.dump_stats(str(args.out / "python.prof"))
        cycle_result = None
        if args.cycles:
            from PySide6.QtTest import QTest
            def one_cycle():
                if args.scene == "economy":
                    window.open_economy_workspace()
                    QTest.qWait(10)
                    window.close_economy_workspace(play_sound=False)
                    QTest.qWait(10)
                    return
                window.set_escape_menu_visible(False)
                window.set_clickgui_visible(True, announce=False, play_sound=False)
                window.jello_state.open_settings("HUD")
                QTest.qWait(10)
                window.set_clickgui_visible(False, announce=False, play_sound=False)
                window.set_escape_menu_visible(True)
                QTest.qWait(10)
                window.set_escape_menu_visible(False)
                QTest.qWait(10)
            # A ten-ms reversal does not reveal every settling font/scene.
            # Warm one fully opened settings page and ESC before retention.
            window.set_escape_menu_visible(False)
            window.set_clickgui_visible(True, announce=False, play_sound=False)
            window.jello_state.open_settings("HUD")
            QTest.qWait(1000)
            window.set_clickgui_visible(False, announce=False, play_sound=False)
            QTest.qWait(600)
            window.set_escape_menu_visible(True)
            QTest.qWait(300)
            window.set_escape_menu_visible(False)
            QTest.qWait(600)
            deadline = time.perf_counter() + 8.0
            while window._gpu_prewarm_jobs_pending is not None and time.perf_counter() < deadline:
                QTest.qWait(20)
            prewarm_complete = window._gpu_prewarm_jobs_pending is None
            before = (len(window.clickgui_gpu.atlases), len(window.clickgui_gpu.shaped_atlases),
                      len(window.clickgui_gpu.image_textures))
            for _ in range(args.cycles):
                one_cycle()
            QTest.qWait(600)
            window.repaint()
            after = (len(window.clickgui_gpu.atlases), len(window.clickgui_gpu.shaped_atlases),
                     len(window.clickgui_gpu.image_textures))
            cycle_result = dict(kind="rapid reversal, not 100 fully settled open animations",
                                count=args.cycles, font_and_asset_textures_before=before,
                                font_and_asset_textures_after=after,
                                texture_count_stable=before == after,
                                prewarm_complete_before=prewarm_complete,
                                menu_closed=not window.menu_overlay_active(),
                                gpu_available=window.gpu_compositor.available,
                                gpu_error=window.gpu_compositor.last_error)
        result = {}
        for key, values in samples.items():
            ordered = sorted(values)
            result[key] = dict(count=len(values), mean_ms=statistics.mean(values), median_ms=statistics.median(values), p95_ms=ordered[min(len(ordered)-1, int(len(ordered)*.95))],p99_ms=ordered[min(len(ordered)-1,int(len(ordered)*.99))], max_ms=max(values))
        window.makeCurrent()
        driver = {}
        try:
            functions = window.context().extraFunctions()
            for name, code in (("vendor", 0x1F00), ("renderer", 0x1F01), ("version", 0x1F02)):
                value = functions.glGetString(code)
                driver[name] = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        finally:
            window.doneCurrent()
        surface = window.context().format() if window.context() else None
        worker = window.local_vision
        vision = dict(running=worker.running, requested_device=worker.device,
                      reported_device=worker.active_device, inference_fps=worker.inference_fps,
                      latency_ms=worker.latency_ms, error=worker.last_error)
        detector = getattr(worker, "_detector", None)
        if detector is not None:
            try:
                vision["execution_devices"] = list(detector.compiled.get_property("EXECUTION_DEVICES"))
            except Exception as exc:
                vision["execution_devices_error"] = str(exc)
        payload = dict(scope="Desktop OpenGL; real control disabled; capture/inference only when explicitly opted in", scene=args.scene, cs2_pack=bool(args.cs2_assets), settings=args.settings, cycles=cycle_result, driver=driver, webcam=args.webcam, local_vision=args.local_vision, vision=vision, vsync=args.vsync, stencil_request=args.stencil, actual_stencil=surface.stencilBufferSize() if surface else None, size=[window.width(), window.height()], dpr=window.devicePixelRatioF(), camera_connected=window.camera_worker.connected, gpu_available=window.gpu_compositor.available, gpu_error=window.gpu_compositor.last_error, metrics=result)
        native=getattr(window,'_endfield_menu',None)
        payload.update(source_root=str(ROOT),fps_limit=args.fps_limit,utilization=utilization,
            gpu_counter_error=engines.error,cprofile=bool(profiler),
            native_device=native.device_info() if native and hasattr(native,'device_info') else {},
            native_observed_fps=((native.stats()['frames']-native_start[0])/max(.001,time.perf_counter()-native_start[1])) if native is not None else None,
            graphics_runtime=window.render_runtime.snapshot() if hasattr(window,'render_runtime') else {},
            limitations='Synthetic 30Hz video, no real decoder/camera. GPU counters are per-engine percentages, not summed GPU utilization. Missing counters are null, not zero.')
        if args.startup_trace:
            payload['startup_trace'] = dict(phases_ms=launch_phases,long_events=long_events,
                warmup_max_ms=window._gpu_warmup.max_ms,warmup_completed=window._gpu_warmup.completed,
                scope='Process-local launch phases after environment setup, synthetic video/no devices; not OS disk cold-cache time')
        import hashlib
        payload['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'rm_client/ui/polar_app.py','rm_client/ui/minecraft_hud.py',
            'rm_client/ui/clickgui_gpu.py','rm_client/ui/polar/gpu_warmup.py',
            'rm_client/ui/polar/renderers/combat_target_hud.py','rm_client/ui/endfield/menu/native.py',
            'rm_client/ui/endfield/menu/assets/native_renderer/CURRENT.json')}
        payload['probe_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (args.out / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        if args.capture:
            window.grabFramebuffer().save(str(args.out / "hud.png"))
            window.set_escape_menu_visible(True)
            app.processEvents()
            window.grabFramebuffer().save(str(args.out / "escape.png"))
        if not args.interactive:
            if child is not None:child.close()
            window.close()
            close_deadline = time.perf_counter() + 10.0
            def finish_close():
                if window._window_close_finalized:
                    app.quit()
                elif time.perf_counter() >= close_deadline:
                    print("Native probe shutdown did not finish within 10 seconds", file=sys.stderr, flush=True)
                    app.exit(1)
                else:
                    QTimer.singleShot(20, finish_close)
            finish_close()
    QTimer.singleShot(round(args.warmup * 1000), begin)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
