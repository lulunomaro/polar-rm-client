"""Explicit Windows/OpenGL/owned-D3D integration check, never field qualification.

Uses a temporary user profile and inert transport starts. Screenshots read this
client's owned render target only. No desktop capture or game is launched.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def check_backdrop(window, out):
    """Explicit owned-GL readback only; never used by the runtime renderer."""
    import numpy as np
    from PySide6.QtGui import QImage
    compositor = window.gpu_compositor
    treatment = compositor._backdrop_treatment
    assert treatment.active and treatment.blur_kernel == "endfield_gaussian", (
        treatment, compositor.available, compositor.last_error,
        window.home_visible, window.escape_menu_visible)
    assert not treatment.include_overlay and treatment.dim_alpha > .25

    def rgba(image):
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        return np.frombuffer(converted.constBits(), dtype=np.uint8).reshape(
            converted.height(), converted.width(), 4).copy()

    shown = window.grabFramebuffer()
    shown.save(str(out / "esc_world_backdrop.png"))
    window.makeCurrent()
    try:
        raw = compositor.background_fbo.toImage()
        raw.save(str(out / "untreated_world.png"))
        empty = {}
        for name in ("overlay", "vision", "dynamic", "target", "hud_glass", "glass"):
            pixels = rgba(getattr(compositor, name + "_fbo").toImage())
            empty[name] = int(pixels[:, :, 3].max())
        assert not any(empty.values()), empty
    finally:
        window.doneCurrent()
    source = rgba(raw)[:, :, :3].astype(np.float32)
    shown_pixels = rgba(shown)
    # Edge energy alone accepts an all-white (or all-black) failed output!
    # This pass has no foreground: every pixel must be opaque, dimmed and
    # retain the world's mean luminance within the bounded blur-edge error.
    ceiling = 255*(1-treatment.dim_alpha)
    assert int(shown_pixels[:, :, 3].min()) == 255, "Backdrop lost opacity"
    assert int(shown_pixels[:, :, :3].max()) <= ceiling + 3, "White/undimmed backdrop"
    blurred = shown_pixels[:, :, :3].astype(np.float32) / (1-treatment.dim_alpha)
    mean_error = float(np.abs(blurred.mean(axis=(0, 1))-source.mean(axis=(0, 1))).max())
    assert mean_error < 5, ("Backdrop lost the world", mean_error)

    def gradient(image):
        return float(np.abs(np.diff(image, axis=0)).mean() + np.abs(np.diff(image, axis=1)).mean())

    source_edges, soft_edges = gradient(source), gradient(blurred)
    assert source_edges > .05, "A flat input cannot prove a blur"
    assert soft_edges < source_edges * .9, (source_edges, soft_edges)
    return {"empty_combat_layers_alpha_max": empty, "world_edge_energy": source_edges,
            "gaussian_edge_energy_after_undoing_dim": soft_edges,
            "kernel": treatment.blur_kernel, "radius_dp": treatment.blur_radius_px,
            "dim_alpha": treatment.dim_alpha, "maximum_rgb": int(shown_pixels[:, :, :3].max()),
            "undimmed_mean_error": mean_error}


def check_native_surface(menu, parent, *, visible=True):
    """Inspect only the HWND explicitly returned by this menu's own worker."""
    from ctypes import wintypes
    api = menu.api.lib.pe_surface
    api.argtypes, api.restype = [ctypes.c_uint64], ctypes.c_size_t
    surface = api(menu.handle)
    assert surface
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.GetWindow.argtypes, user.GetWindow.restype = [wintypes.HWND, ctypes.c_uint], wintypes.HWND
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    hwnd = int(parent.winId())
    style, extended = user.GetWindowLongPtrW(surface, -16), user.GetWindowLongPtrW(surface, -20)
    assert not style & 0x40000000, "WS_CHILD can clip away Qt's world backdrop"
    assert style & 0x80000000 and user.GetWindow(surface, 4) == hwnd, "Not owned by this client"
    assert extended & 0x08000000 and extended & 0x00200000, "Activation/redirection contract changed"
    assert not extended & 0x8, "Menu must never be a desktop-topmost overlay"
    origin, rect = wintypes.POINT(), wintypes.RECT()
    assert user.ClientToScreen(hwnd, ctypes.byref(origin))
    assert user.GetWindowRect(surface, ctypes.byref(rect))
    dpr = parent.devicePixelRatioF()
    expected = (origin.x, origin.y, round(parent.width()*dpr), round(parent.height()*dpr))
    actual = (rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top)
    assert bool(user.IsWindowVisible(surface)) == visible, "Owner visibility did not propagate"
    if visible:
        assert actual == expected, (actual, expected)
    return {"owned_popup": True, "clips_parent": False, "topmost": False,
            "aligned_physical_size": list(actual[2:]), "visible": bool(user.IsWindowVisible(surface))}


def run(pack: Path | None, out: Path, cycles: int) -> None:
    if sys.platform != "win32":
        raise RuntimeError("A real Windows desktop is required")
    os.environ.update(QT_QPA_PLATFORM="windows", POLAR_AUTOSTART="0", CI="true",
                      POLAR_DISABLE_LOCAL_CAPTURE="1", POLAR_DISABLE_MEDIA_PRELOAD="1")
    from PySide6.QtCore import Qt, QTimer, QPoint
    from PySide6.QtGui import QImage, QCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from rm_client.backend.mqtt_backend import RmMqttBackend
    from rm_client.backend.serial_backend import RmSerialBackend
    from rm_client.backend.video_backend import RmVideoBackend
    from rm_client.ui.endfield.menu.native import EndfieldNativeMenu
    from rm_client.ui.polar_app import PolarRMWindow
    from rm_client.ui.cs2_settings_center import build_settings_center
    from rm_client.ui.window_shortcuts import set_fullscreen
    from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle
    app = QApplication.instance() or QApplication([])
    out.mkdir(parents=True, exist_ok=False)
    def until(predicate, timeout=10):
        deadline = time.perf_counter() + timeout
        while not predicate():
            if time.perf_counter() > deadline:
                raise TimeoutError(f"Native desktop check timed out: {menu.__dict__}; "
                                   f"backdrop={window.gpu_compositor._backdrop_treatment}; "
                                   f"gpu={window.gpu_compositor.last_error}; "
                                   f"rejected={window.gpu_compositor.last_rejected_frame}; "
                                   f"fps={window.state.fps}; visible={window.isVisible()}")
            app.processEvents()
            time.sleep(.005)
    def wait(seconds):
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(.003)
    with tempfile.TemporaryDirectory() as profile, patch.dict(os.environ, POLAR_USER_DATA_ROOT=profile), \
         patch.object(RmMqttBackend, "start"), patch.object(RmSerialBackend, "start"), patch.object(RmVideoBackend, "start"):
        window = PolarRMWindow()
        if pack is not None:
            menu = EndfieldNativeMenu(pack)
            window._endfield_menu = menu
        else:
            menu = window._endfield_menu
            assert menu is not None, "Production Windows menu was not assembled"
        original_cursor = QCursor.pos()
        report = {"scope": "owned Windows GPU + inert transports; NOT real match validation", "cycles": cycles}
        report["pack_selection"] = ("production-default" if pack is None else "explicit-test-pack")
        report["private_pack_present"] = (window.paths.user_data_root / "endfield_pack").exists()
        report["uses_shipped_pack"] = menu.pack == window.paths.endfield_bundled_menu_pack
        report["renderer_uses_shipped_pack"] = menu.renderer_pack == window.paths.endfield_renderer_pack
        try:
            window.resize(1280, 720)
            window.show()
            window.raise_()
            window.activateWindow()
            assert QTest.qWaitForWindowExposed(window, 5000)
            settle_window_lifecycle(window, app)
            until(lambda: menu.ready or bool(menu.error), 30)
            if menu.error:
                raise RuntimeError(menu.error)
            window.home_visible = False
            window.set_clickgui_visible(False, announce=False)
            window.set_escape_menu_visible(True)
            until(lambda: menu.stats()["frames"] > 30)
            wait(1)
            report["initial"] = menu.stats()
            report["display_hz"] = window.screen().refreshRate()
            report["dpr"] = window.devicePixelRatioF()
            report["esc_backdrop"] = check_backdrop(window, out)
            report["native_surface"] = check_native_surface(menu, window)
            window.move(147, 93)
            wait(.3)
            report["moved_surface"] = check_native_surface(menu, window)
            for width, height in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440)):
                window.resize(width, height)
                wait(.5)
                dpr = window.devicePixelRatioF()
                w, h = round(window.width()*dpr), round(window.height()*dpr)
                check_native_surface(menu, window)
                target = out / f"source_{w}x{h}.bgra"
                menu.api.capture(menu.handle, str(target).encode("utf-8"))
                until(lambda: menu.api.captured(menu.handle) != 0)
                if menu.api.captured(menu.handle) != 1:
                    raise RuntimeError(menu.api.error(menu.handle))
                image = QImage(target.read_bytes(), w, h, w*4, QImage.Format.Format_ARGB32)
                assert image.pixelColor(0, 0).alpha() == 0, "Native empty backdrop is not transparent"
                image.save(str(target.with_suffix(".png")))
            window.showMinimized()
            wait(.3)
            report["minimized_surface"] = check_native_surface(menu, window, visible=False)
            window.showNormal()
            wait(.5)
            report["restored_surface"] = check_native_surface(menu, window)
            set_fullscreen(window, True)
            wait(.4)
            assert window.isFullScreen()
            report["fullscreen_surface"] = check_native_surface(menu, window)
            from tools.endfield.verify_hover import check_hover
            # A visible native HWND must use the real OS cursor: otherwise its
            # genuine WM_MOUSELEAVE correctly supersedes an injected Qt point.
            def position(x, y):
                dpr = window.devicePixelRatioF()
                QCursor.setPos(window.mapToGlobal(QPoint(round(x/dpr), round(y/dpr))))
            hover = check_hover(menu, wait, until, codes=(2, 6, 5), position=position)
            assert all(row["stationary"] and row["duplicate_moves"] for row in hover), hover
            report["fullscreen_hover"] = hover
            from tools.endfield.menu.verify_placeholder import check_window_switch
            report["ui_switch"] = check_window_switch(window,wait,until,out)
            with patch.object(window.backend, "trigger_estop", return_value=(True, "test-only")) as estop:
                QTest.keyClick(window, Qt.Key.Key_F12)
                app.processEvents()
                assert estop.called, "Menu swallowed the emergency shortcut"
            # Exact source-projected hit rectangle, not guessed screenshot coords.
            rect = (ctypes.c_double * 4)()
            until(lambda: bool(menu.api.button(menu.handle, 2, rect)))
            x, y = rect[0]+rect[2]/2, rect[1]+rect[3]/2
            position(x, y)
            menu.pointer(1, x, y)
            menu.pointer(2, x, y)
            # Observe the actual source hit -> semantic action without opening
            # a blocking dialog in a test callback.
            seen = []
            with patch.object(window, "dispatch_endfield_action", side_effect=seen.append):
                menu.pointer(3, x, y)
                until(lambda: bool(seen))
            assert seen == [2], seen
            assert window.escape_menu_visible
            assert window.isFullScreen()
            dialog = build_settings_center(window)
            try:
                dialog.show()
                wait(.4)
                assert window.isFullScreen()
                dialog.hide()
                wait(.2)
                assert window.isFullScreen()
            finally:
                dialog.deleteLater()
            from rm_client.ui.endfield.dialogs import confirm_exit
            QTimer.singleShot(100, lambda: QApplication.activeModalWidget().reject())
            assert not confirm_exit(window)
            assert window.isFullScreen()
            window.set_escape_menu_visible(True)
            wait(1.1)
            baseline = menu.stats()
            for i in range(cycles):
                window.set_escape_menu_visible(False)
                if i % 5 == 0:  # reverse an in-flight authored clip
                    window.set_escape_menu_visible(True)
                    wait(.05)
                    window.set_escape_menu_visible(False)
                until(lambda: not window.escape_menu_visible, 4)
                assert menu.phase == 0, (i, menu.phase, menu.error, menu.stats())
                window.set_escape_menu_visible(True)
                wait(.82)
                if menu.error:
                    raise RuntimeError(menu.error)
            final = menu.stats()
            assert final["textures"] == baseline["textures"], (baseline, final)
            assert final["meshes"] == baseline["meshes"], (baseline, final)
            assert final["owners"] == 1
            report.update(baseline=baseline, final=final, fullscreen_preserved=True,
                          actual_source_hit=True, texture_growth=0)
            window.set_escape_menu_visible(False)
            until(lambda: not window.escape_menu_visible)
            swaps = []
            window.frameSwapped.connect(lambda: swaps.append(time.perf_counter()))
            wait(3)
            intervals = sorted((b-a)*1000 for a,b in zip(swaps, swaps[1:]))
            if intervals:
                report["synthetic_hud_frame_ms_p95"] = intervals[int(.95*(len(intervals)-1))]
                report["synthetic_hud_fps"] = 1000 / (sum(intervals)/len(intervals))
            report["result"] = "PASS"
        finally:
            report["native_error"] = menu.error
            close_window_safely(window, app)
            QCursor.setPos(original_cursor)
            report["retired"] = menu.handle == 0
            (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            window.deleteLater()
        assert report["retired"], "Native owner leaked at final teardown"
    print("Endfield Windows owned-target integration PASS", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, help="Explicit test pack; omit to verify production path selection")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 100:
        parser.error("cycles must be 1–100")
    run(args.pack.resolve() if args.pack is not None else None, args.out.resolve(), args.cycles)
