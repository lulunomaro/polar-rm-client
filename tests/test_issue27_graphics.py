"""Device choices/diagnostics and render-hotspot contracts; no device I/O."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD','1')
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from rm_client.model import RobotState,build_default_registry
from rm_client.ui import graphics_devices as devices,cs2_video_settings as video
from rm_client.ui.endfield.menu.native import EndfieldNativeMenu
from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer
from rm_client.ui.polar.renderers.combat_target_hud import CombatTargetHudMixin
from rm_client.ui.render_runtime import RenderRuntime

APP=QApplication.instance() or QApplication([])


class GraphicsTests(unittest.TestCase):
    def tearDown(self):
        devices.graphics_inventory.cache_clear();video.current_preferences.cache_clear()

    def test_old_config_defaults_and_malformed_selection_rejected(self):
        self.assertEqual(video.validate_preferences({'hud_hz':90}).render_adapter,'auto')
        for value in (None,True,3,'gpu0','../driver.dll','luid:ABCDEF00:00000001','luid:0:1'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                video.validate_preferences({'render_adapter':value})
        self.assertEqual(video.validate_preferences({'render_adapter':'luid:00000001:00000002'}).render_adapter,'luid:00000001:00000002')

    def test_selected_device_roundtrip_and_no_live_switch_on_save(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(video,'preferences_path',return_value=Path(directory)/'video.json'),patch.dict(os.environ,{'POLAR_RENDER_ADAPTER':'auto'}):
            prefs=replace(video.VideoPreferences(),render_adapter='luid:00000001:00000002')
            video.save_preferences(prefs)
            self.assertEqual(video.current_preferences(),prefs)
            self.assertEqual(os.environ['POLAR_RENDER_ADAPTER'],'auto')
            devices.configure_process_graphics(prefs.render_adapter)
            self.assertEqual(os.environ['POLAR_RENDER_ADAPTER'],prefs.render_adapter)

    def test_single_hybrid_missing_and_failed_enumeration(self):
        first=devices.GraphicsAdapter('luid:00000000:00000001','GPU A')
        second=devices.GraphicsAdapter('luid:00000000:00000002','GPU B')
        for inventory in ((first,),(first,second),()):
            with patch.object(devices,'_dxgi_inventory',return_value=inventory) as provider:
                devices.graphics_inventory.cache_clear()
                rows,error=devices.adapter_rows(second.id)
                self.assertFalse(error);self.assertEqual(rows[0][1],'auto')
                self.assertIn(second.id,[value for _,value in rows])
                devices.adapter_rows('auto');provider.assert_called_once()
        with patch.object(devices,'_dxgi_inventory',side_effect=OSError('driver missing')):
            devices.graphics_inventory.cache_clear();rows,error=devices.adapter_rows('auto')
            self.assertEqual(rows,[('自动 / 系统默认','auto')]);self.assertIn('driver missing',error)

    def test_real_renderer_not_configured_name_and_decode_is_not_graphics_device(self):
        runtime=RenderRuntime();runtime.initialized({'renderer':'Actual GL GPU'},True)
        host=NS(render_runtime=runtime,_endfield_menu=NS(device_info=lambda:{'name':'Actual D3D GPU','selection_note':'fallback'}))
        text=devices.device_summary(host)
        self.assertIn('Actual GL GPU',text);self.assertIn('Actual D3D GPU',text)
        self.assertIn('FFmpeg/CPU',text);self.assertIn('fallback',text)

    def test_runtime_counters_percentiles_and_memory_are_bounded(self):
        runtime=RenderRuntime();runtime.initialized({'renderer':'llvmpipe'},False,'initialization error')
        for index in range(1000):runtime.frame(index%2==0,index,'rejected')
        report=runtime.snapshot()
        self.assertEqual(report['initializations'],1);self.assertEqual(report['initialization_failures'],1)
        self.assertEqual(report['samples'],720);self.assertEqual(report['compatibility_frames'],500)
        self.assertEqual(report['gpu_frames'],500)
        self.assertIn('software',report['driver']['implementation'])
        self.assertLessEqual(report['cpu_submit_p95_ms'],report['cpu_submit_p99_ms'])
        report['driver'].clear();self.assertTrue(runtime.driver)

    def test_native_diagnostics_optional_and_bounded(self):
        menu=EndfieldNativeMenu(Path('unused'));self.assertEqual(menu.device_info(),{})
        menu.handle=1;menu.api=Mock()
        for payload in (b'[]',b'invalid',b'x'*8193):
            menu.api.device_info.return_value=payload
            self.assertIn('unavailable',menu.device_info()['selection_note'])
        menu.api.device_info.return_value=b'{"name":"Actual GPU","luid":"luid:00000000:00000001"}'
        self.assertEqual(menu.device_info()['name'],'Actual GPU')

    def test_failed_native_device_creation_is_not_reported_as_success(self):
        menu=EndfieldNativeMenu(Path('unused'))
        api=Mock();api.create.side_effect=OSError('Create D3D11 device failed')
        with patch('rm_client.ui.endfield.menu.native.library_path',return_value=Path('owned.dll')),patch('rm_client.ui.endfield.menu.native.NativeApi',return_value=api):
            menu.prepare(0,1280,720,offscreen=True)
        self.assertEqual(menu.handle,0);self.assertFalse(menu.ready)
        self.assertIn('Create D3D11',menu.error);self.assertEqual(menu.device_info(),{})

    def test_video_page_contains_real_inventory_preserves_selection_and_diagnostics(self):
        sys.path.insert(0,str(ROOT/'tests'))
        from test_cs2_settings_center import Host
        gpu=devices.GraphicsAdapter('luid:00000000:00000001','Test hardware GPU')
        with tempfile.TemporaryDirectory() as directory,patch.object(video,'preferences_path',return_value=Path(directory)/'video.json'),patch.object(devices,'graphics_inventory',return_value=((gpu,),'')):
            video.current_preferences.cache_clear();host=Host()
            dialog=video.open_video_settings(host,embedded=True)
            try:
                combo=dialog.controls['render_adapter']
                combo.setCurrentIndex(combo.findData(gpu.id))
                with patch.object(video,'apply_window_settings') as window_apply:
                    dialog.apply_draft()
                self.assertEqual(window_apply.call_args.args[1].render_adapter,gpu.id)
                self.assertEqual(video.current_preferences().render_adapter,gpu.id)
            finally:dialog.deleteLater();host.deleteLater()

    def test_saved_selection_is_configured_before_application_context_creation(self):
        from rm_client.ui.polar import qt_application
        events=[];application=Mock(_polar_ui_restart=None);application.exec.return_value=0
        prefs=replace(video.VideoPreferences(),render_adapter='luid:00000000:00000001')
        with patch.object(qt_application,'QApplication') as qt,patch.object(qt_application,'_retain_window_until_close'),patch.object(video,'configure_surface'),patch.object(video,'restore_startup_window'),patch.object(video,'current_preferences',return_value=prefs),patch.object(devices,'configure_process_graphics',side_effect=lambda value:events.append(('selection',value))):
            qt.instance.return_value=None
            qt.side_effect=lambda argv:(events.append(('application',None)) or application)
            with self.assertRaises(SystemExit) as exited:
                qt_application.launch_qt_application(lambda:Mock())
        self.assertEqual(exited.exception.code,0)
        self.assertEqual(events,[('selection',prefs.render_adapter),('application',None)])

    def test_native_cadence_tracks_saved_cap_without_repeated_driver_calls(self):
        menu=EndfieldNativeMenu(Path('unused'));menu.handle=1;menu.api=Mock()
        with patch.object(video,'current_preferences',return_value=replace(video.VideoPreferences(),hud_hz=144,fps_limit=90)):
            for _ in range(100):menu.sync_frame_limit()
        menu.api.frame_rate.assert_called_once_with(1,90)
        with patch.object(video,'current_preferences',return_value=replace(video.VideoPreferences(),fps_limit=75)):
            menu.sync_frame_limit()
        self.assertEqual(menu.api.frame_rate.call_args.args,(1,75))


class HotspotTests(unittest.TestCase):
    def setUp(self):
        self.renderer=MinecraftCombatHudRenderer(ROOT/'assets/minecraft_hud')
        self.registry=build_default_registry();self.state=RobotState()

    def test_numeric_content_same_extents_reuses_geometry(self):
        first=self.renderer.layout(1920,1080,'2x',('123456',),1)
        logical_count=len(self.renderer._logical_geometry_cache)
        second=self.renderer.layout(1920,1080,'2x',('654321',),1)
        self.assertEqual(first,second)
        self.assertEqual(len(self.renderer._logical_geometry_cache),logical_count)
        third=self.renderer.layout(1280,720,'2x',('123456'*30,),2)
        self.assertNotEqual(first,third)
        self.assertTrue(third.hotbar.width()>0)

    def test_retained_metrics_and_geometry_have_finite_capacity(self):
        for index in range(400):
            self.renderer.layout(1280+index,720,'2x',(str(index),),1)
        self.assertLessEqual(len(self.renderer._metrics_cache),256)
        self.assertLessEqual(len(self.renderer._logical_geometry_cache),16)
        self.assertLessEqual(len(self.renderer._screen_geometry_cache),16)

    def test_public_qt_rectangles_cannot_mutate_retained_hit_geometry(self):
        first=self.renderer.layout(1280,720,'2x',('123',),1)
        original=first.slots[0].getRect()
        first.slots[0].translate(1000,1000);first.scoreboard.setWidth(9999)
        again=self.renderer.layout(1280,720,'2x',('123',),1)
        self.assertEqual(again.slots[0].getRect(),original)
        self.assertLess(again.scoreboard.width(),9999)

    def test_cached_and_forced_recalculation_pixels_and_hits_match(self):
        for language in ('English','简体中文'):
            self.registry.get('ClickGui').get_setting('Language').value=language
            for width,height,multiplier in ((1280,720,.5),(1600,900,1),(1920,1080,1.5),(2560,1440,2)):
                module=self.registry.get('MinecraftCombatHUD')
                module.get_setting('Scale Multiplier').value=multiplier
                frames=[]
                for clear in (False,True):
                    if clear:
                        self.renderer._metrics_cache.clear();self.renderer._logical_geometry_cache.clear();self.renderer._screen_geometry_cache.clear()
                    image=QImage(width,height,QImage.Format_ARGB32_Premultiplied);image.fill(0)
                    painter=QPainter(image)
                    try:layout=self.renderer.draw(painter,width,height,self.state,self.registry,module)
                    finally:painter.end()
                    frames.append((image,layout))
                self.assertEqual(frames[0],frames[1])
                self.assertEqual(frames[1][1].slot_at(frames[1][1].slots[3].center()),3)

    def test_invisible_target_does_not_rasterize_upload_blank_roi(self):
        compositor=Mock();compositor.clear_target_layer.return_value=True
        self.registry.get('Crosshair').enabled=False
        host=NS(menu_overlay_active=lambda:False,gpu_compositor=compositor,
            get_hud_scale=lambda *args:1,registry=self.registry,
            combat_target_visual=lambda m:'Clear',setting_bool=lambda *args:False,
            interpolated_vision_result=lambda now:None,has_valid_combat_target=lambda result:False,
            target_hud_presence=NS(update=lambda *args:NS(visible=False)))
        with patch('rm_client.ui.polar.renderers.combat_target_hud.QImage',side_effect=AssertionError('blank image allocation')):
            self.assertTrue(CombatTargetHudMixin.paint_gpu_target_hud_layer(host,None,1920,1080,1))
        compositor.clear_target_layer.assert_called_once()
        compositor.upload_target_region.assert_not_called()
        compositor.paint_raster.assert_not_called()


if __name__=='__main__':unittest.main()
