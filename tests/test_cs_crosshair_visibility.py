"""Physical-pixel crosshair visibility across viewport, DPR and HUD transforms."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
import numpy as np
from PySide6.QtGui import QImage,QPainter
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from rm_client.model import build_default_registry
from rm_client.cs2_crosshair import paint_crosshair,profile_from_module,encode_share_code,apply_profile
APP=QApplication.instance() or QApplication([])


def raster(module,physical_height=720,dpr=1,hud_scale=1,zoom=1):
    """Same centered coordinate transforms as the GPU's small target patch."""
    image=QImage(512,512,QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr);image.fill(0)
    painter=QPainter(image)
    height=physical_height/dpr/hud_scale
    width=height*16/9
    painter.translate(256/dpr,256/dpr)
    painter.scale(hud_scale*zoom,hud_scale*zoom)
    painter.translate(-width/2,-height/2)
    ok=paint_crosshair(painter,module,width,height)
    painter.end()
    pixels=np.frombuffer(image.constBits(),np.uint8).reshape(512,image.bytesPerLine())[:,:2048].reshape(512,512,4).copy()
    return ok,pixels


def bounds(mask):
    yy,xx=np.where(mask)
    return (int(xx.max()-xx.min()+1),int(yy.max()-yy.min()+1)) if len(xx) else (0,0)


def foreground(pixels):
    return (pixels[:,:,1]>pixels[:,:,0])&(pixels[:,:,3]>0)


class VisibilityTests(unittest.TestCase):
    def setUp(self):self.module=build_default_registry().get('Crosshair')

    def test_default_foreground_survives_all_resolution_dpr_and_scale_cases(self):
        for height in [720,1080,1440,2160]:
            for dpr in [1,1.25,1.5,2,2.5]:
                for multiplier in [.5,1,2]:
                    with self.subTest(height=height,dpr=dpr,multiplier=multiplier):
                        self.module.get_setting('Crosshair Scale').value=multiplier
                        ok,pixels=raster(self.module,height,dpr)
                        self.assertTrue(ok);self.assertTrue(foreground(pixels).any())

    def test_each_static_style_remains_visible_at_720p(self):
        for style in range(10):
            with self.subTest(style=style):
                self.module.get_setting('CS2 Style').value=style
                ok,pixels=raster(self.module)
                self.assertTrue(ok);self.assertTrue(foreground(pixels).any())

    def test_zero_thickness_is_still_explicitly_invisible(self):
        self.module.get_setting('Thickness').value=0
        for style in range(10):
            self.module.get_setting('CS2 Style').value=style
            ok,pixels=raster(self.module)
            self.assertTrue(ok);self.assertFalse(pixels[:,:,3].any())

    def test_dpr_is_applied_once_in_physical_geometry(self):
        for height in [720,1080,1440,2160]:
            _,one=raster(self.module,height,1)
            for dpr in [1.25,1.5,2,2.5]:
                _,other=raster(self.module,height,dpr)
                self.assertEqual(bounds(other[:,:,3]>0),bounds(one[:,:,3]>0))
                self.assertEqual(bounds(foreground(other)),bounds(foreground(one)))

    def test_hud_scale_does_not_shrink_crosshair_or_minimum_stroke(self):
        _,reference=raster(self.module)
        for hud_scale in [.55,.82,1.34,1.85,2.4]:
            for dpr in [1,2]:
                with self.subTest(hud_scale=hud_scale,dpr=dpr):
                    _,pixels=raster(self.module,720,dpr,hud_scale)
                    self.assertTrue(foreground(pixels).any())
                    self.assertEqual(bounds(pixels[:,:,3]>0),bounds(reference[:,:,3]>0))

    def test_legacy_small_positive_stroke_is_not_lost(self):
        self.module.get_setting('Geometry Units').value='Legacy'
        self.module.get_setting('Reference Height').value=480
        self.module.get_setting('Thickness').value=.1
        _,pixels=raster(self.module)
        self.assertTrue(foreground(pixels).any())

    def test_render_preserves_profile_and_share_code_and_saved_display_scale(self):
        self.module.get_setting('Crosshair Scale').value=2.5
        before=profile_from_module(self.module)
        code=encode_share_code(before,'cs2-v1')
        for height in [720,1080,1440,2160]:raster(self.module,height,2)
        self.assertEqual(profile_from_module(self.module),before)
        self.assertEqual(encode_share_code(profile_from_module(self.module),'cs2-v1'),code)
        apply_profile(self.module,before)
        self.assertEqual(self.module.get_setting('Crosshair Scale').value,2.5)

    def test_transparent_profile_does_not_become_visible(self):
        self.module.get_setting('Alpha').value=0
        self.module.get_setting('Outline Alpha').value=0
        _,pixels=raster(self.module)
        self.assertFalse(pixels[:,:,3].any())


class PreviewTests(unittest.TestCase):
    def test_preview_defaults_to_unmagnified_current_physical_viewport(self):
        from test_cs2_settings_center import Host
        from rm_client.cs2_crosshair import build_crosshair_editor
        from PySide6.QtCore import QCoreApplication,QEvent
        host=Host()
        try:
            with patch.object(host,'devicePixelRatioF',return_value=2):
                editor=build_crosshair_editor(host)
            self.assertEqual(editor.resolution.currentText(),str(host.height()*2))
            self.assertEqual(editor.zoom.value(),1)
            editor.deleteLater()
        finally:
            host.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def test_preview_zoom_magnifies_target_one_pixel_floor(self):
        from test_cs2_settings_center import Host
        from rm_client.cs2_crosshair import build_crosshair_editor
        from PySide6.QtCore import QCoreApplication,QEvent
        host=Host();editor=build_crosshair_editor(host)
        preview=editor.previews[0];preview.setFixedSize(600,400)
        editor.resolution.setCurrentText('720')
        editor.controls['Length'].setValue(10)
        editor.controls['Gap'].setValue(3)
        editor.controls['Outline Mode'].setCurrentIndex(0)
        dpr=preview.devicePixelRatioF()
        try:
            for thickness in [.1,.5]:
                editor.controls['Thickness'].setValue(thickness)
                for zoom in [1,2,3,4,8]:
                    with self.subTest(thickness=thickness,zoom=zoom):
                        editor.zoom.setValue(zoom)
                        image=QImage(round(preview.width()*dpr),round(preview.height()*dpr),QImage.Format.Format_ARGB32_Premultiplied)
                        image.setDevicePixelRatio(dpr);image.fill(0);preview.render(image)
                        pixels=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
                        green=(pixels[:,:,1]>240)&(pixels[:,:,0]<120)&(pixels[:,:,2]<120)
                        # Sample inside the right arm, clear of dot/vertical arm.
                        column=round(image.width()/2+6*zoom)
                        self.assertEqual(int(green[:,column].sum()),zoom)
        finally:
            editor.deleteLater();host.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def test_preview_divides_target_physical_height_by_its_dpr_once(self):
        from test_cs2_settings_center import Host
        from rm_client.cs2_crosshair import build_crosshair_editor
        from PySide6.QtCore import QCoreApplication,QEvent
        host=Host();editor=build_crosshair_editor(host)
        preview=editor.previews[0];preview.resize(300,200)
        editor.resolution.setCurrentText('1080')
        try:
            for dpr in [.75,1,2]:
                image=QImage(600,400,QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr);image.fill(0)
                with patch.object(preview,'devicePixelRatioF',return_value=dpr), patch('rm_client.cs2_crosshair.paint_crosshair',return_value=True) as paint:
                    preview.render(image)
                self.assertTrue(paint.called)
                self.assertEqual(paint.call_args.args[3],1080/dpr)
                self.assertEqual(paint.call_args.kwargs['display_scale'],1)
        finally:
            editor.deleteLater();host.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


if __name__=='__main__':unittest.main()
