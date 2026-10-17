"""CS-only sizing and bounded GPU target uploads, using real Qt raster pixels."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import math
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QPointF,QRect,QSize
from PySide6.QtGui import QColor,QImage,QPainter,QFont
from test_cs2_settings_center import APP
from test_cs_crosshair_visibility import raster,bounds,foreground
from rm_client.model import build_default_registry
from rm_client.cs2_crosshair import paint_crosshair,profile_from_module,encode_share_code
from rm_client.ui.polar.renderers import combat_target_hud as target
from rm_client.ui.gpu_frame_compositor import GpuFrameCompositor


def array(image):
    return np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4).copy()


class TargetHost(target.CombatTargetHudMixin):
    def __init__(self,hud_scale=1):
        self.registry=build_default_registry()
        self.module=self.registry.get('Crosshair')
        self.module.get_setting('Style').value='CS2'
        self.hud_scale=hud_scale
        self.uploads=[];self.draw_calls=0;self.clear_calls=0;self.presence_calls=0
        self.target_hud_presence=NS(update=self.update_presence)
        self.menu=False;self.clear_ok=True;self.upload_ok=True
        self.gpu_compositor = GpuFrameCompositor()
        self.gpu_compositor.clear_target_layer = self.clear
        self.gpu_compositor.upload_target_region = self.upload
    def clear(self,_functions):
        self.clear_calls+=1;return self.clear_ok
    def upload(self,_functions,image,x,y):
        self.uploads.append((image.copy(),x,y));return self.upload_ok
    def menu_overlay_active(self):return self.menu
    def get_hud_scale(self,_width,_height):return self.hud_scale
    def interpolated_vision_result(self,_now):return None
    def has_valid_combat_target(self,_result):return False
    def update_presence(self,_now,_valid):
        self.presence_calls+=1;return NS(visible=False)
    def draw_crosshair(self,p,width,height):
        self.draw_calls+=1
        paint_crosshair(p,self.module,width,height)
    def combat_target_visual(self,_module):return 'Clear'
    def setting_bool(self,_module,_name,default):return default
    def run(self,physical_height,dpr=1):
        physical_width=round(physical_height*16/9)
        self.width,self.height=physical_width/dpr,physical_height/dpr
        self.dpr=dpr
        return target.CombatTargetHudMixin.paint_gpu_target_hud_layer(self,None,self.width,self.height,dpr)
    def composite_and_reference(self):
        rect=QRect()
        for image,x,y in self.uploads:rect=rect.united(QRect(x,y,image.width(),image.height()))
        composite=QImage(rect.size(),QImage.Format.Format_ARGB32_Premultiplied);composite.fill(0)
        p=QPainter(composite)
        for image,x,y in self.uploads:p.drawImage(x-rect.left(),y-rect.top(),image)
        p.end()
        reference=QImage(rect.size(),QImage.Format.Format_ARGB32_Premultiplied);reference.fill(0)
        p=QPainter(reference)
        p.translate(round(self.width*self.dpr)*.5-rect.left(),round(self.height*self.dpr)*.5-rect.top())
        p.scale(self.hud_scale*self.dpr,self.hud_scale*self.dpr)
        logical_width=max(1,int(self.width/self.hud_scale));logical_height=max(1,int(self.height/self.hud_scale))
        p.translate(-logical_width*.5,-logical_height*.5)
        paint_crosshair(p,self.module,logical_width,logical_height)
        p.end()
        return rect,array(composite),array(reference)


class PresentationTests(unittest.TestCase):
    def test_default_cs_bounds_double_and_half_scale_restores_reference_size(self):
        module=build_default_registry().get('Crosshair')
        before=profile_from_module(module);code=encode_share_code(before,'cs2-v1')
        for height,expected in [(720,24),(1080,36),(1440,48),(2160,72)]:
            for dpr in [1,2]:
                with self.subTest(height=height,dpr=dpr):
                    module.get_setting('Crosshair Scale').value=1
                    _,pixels=raster(module,height,dpr)
                    self.assertEqual(bounds(pixels[:,:,3]>0),(expected,expected))
                    module.get_setting('Crosshair Scale').value=.5
                    _,pixels=raster(module,height,dpr)
                    self.assertEqual(bounds(pixels[:,:,3]>0),(expected//2,expected//2))
        self.assertEqual(profile_from_module(module),before)
        self.assertEqual(encode_share_code(profile_from_module(module),'cs2-v1'),code)
        self.assertEqual(module.get_setting('Crosshair Scale').value,.5)

    def test_non_cs_styles_do_not_use_cs_presentation_baseline(self):
        from rm_client.ui.polar_app import PolarRMWindow
        from types import MethodType
        registry=build_default_registry()
        for style in ['RM Dot','Circle','Cross']:
            host=NS(registry=registry,crosshair_style=lambda:style,crosshair_color=lambda:QColor('#50ff50'))
            host.crosshair_primitives=MethodType(PolarRMWindow.crosshair_primitives,host)
            images=[]
            for baseline in [1,2]:
                image=QImage(128,128,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
                p=QPainter(image)
                with patch('rm_client.cs2_crosshair.CS2_PRESENTATION_SCALE',baseline):PolarRMWindow.draw_crosshair(host,p,128,128)
                p.end();images.append(array(image))
            self.assertTrue(np.array_equal(*images),style)


class TargetBoundsTests(unittest.TestCase):
    def test_default_and_max_scale_match_direct_raster_without_clipping(self):
        for height in [720,1080,1440,2160]:
            for dpr in [1,2]:
                for scale in [1,8]:
                    with self.subTest(height=height,dpr=dpr,scale=scale):
                        host=TargetHost(1.34);host.module.get_setting('Crosshair Scale').value=scale
                        self.assertTrue(host.run(height,dpr))
                        self.assertEqual(host.draw_calls,1);self.assertEqual(host.clear_calls,1);self.assertEqual(host.presence_calls,1)
                        rect,pixels,reference=host.composite_and_reference()
                        self.assertTrue(np.array_equal(pixels,reference))
                        self.assertTrue(foreground(pixels).any())
                        self.assertFalse(pixels[0,:,3].any());self.assertFalse(pixels[-1,:,3].any())
                        self.assertFalse(pixels[:,0,3].any());self.assertFalse(pixels[:,-1,3].any())
                        self.assertTrue(all(im.width()<=512 and im.height()<=512 for im,_,_ in host.uploads))
                        if height==2160 and scale==8:self.assertGreater(rect.width(),512)

    def test_extreme_import_stays_inside_viewport_with_bounded_tiles(self):
        host=TargetHost(1.34)
        for key,value in [('Length',255),('Gap',0),('Reference Height',240),('Crosshair Scale',8)]:host.module.get_setting(key).value=value
        shapes=[]
        real_image=target.QImage
        def image(width,height,format):
            shapes.append((width,height));return real_image(width,height,format)
        image.Format=real_image.Format
        with patch.object(target,'QImage',new=image):
            self.assertTrue(host.run(2160,1))
        self.assertEqual(host.draw_calls,1)
        self.assertGreater(len(shapes),1)
        self.assertTrue(all(0<w<=512 and 0<h<=512 for w,h in shapes))
        self.assertLessEqual(len(shapes),math.ceil(3840/512)*math.ceil(2160/512))
        for image,x,y in host.uploads:
            self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+image.width(),3840);self.assertLessEqual(y+image.height(),2160)

    def test_tiled_render_is_seamless_for_circle_quad_and_large_outline(self):
        for style in [1,6,7,8,9]:
            with self.subTest(style=style):
                host=TargetHost(1.34)
                for key,value in [('CS2 Style',style),('Length',30),('Thickness',4),('Outline Width',8),('Crosshair Scale',8)]:host.module.get_setting(key).value=value
                self.assertTrue(host.run(2160,2))
                _,pixels,reference=host.composite_and_reference()
                self.assertTrue(np.array_equal(pixels,reference))
                self.assertEqual(host.draw_calls,1)


    def test_offscreen_and_sparse_profiles_do_not_upload_transparent_viewport(self):
        for dpr in [1,2]:
            host=TargetHost()
            host.module.get_setting('Gap').value=3840
            host.module.get_setting('Crosshair Scale').value=8
            self.assertTrue(host.run(2160,dpr))
            self.assertFalse(host.uploads)
            host=TargetHost()
            host.module.get_setting('Length').value=255
            host.module.get_setting('Reference Height').value=240
            host.module.get_setting('Crosshair Scale').value=8
            self.assertTrue(host.run(2160,dpr))
            self.assertTrue(host.uploads)
            uploaded=sum(im.sizeInBytes() for im,_,_ in host.uploads)
            self.assertLess(uploaded,3840*2160*4//2)

    def test_real_compositor_gl_subimage_commands_are_bounded(self):
        from rm_client.ui.gpu_frame_compositor import GpuFrameCompositor
        from unittest.mock import Mock
        for dpr in [1,2]:
            for scale in [1,8]:
                with self.subTest(dpr=dpr,scale=scale):
                    host=TargetHost(1.34)
                    host.module.get_setting('Crosshair Scale').value=scale
                    compositor=GpuFrameCompositor()
                    compositor._size=QSize(3840,2160)
                    compositor.target_fbo=NS(bind=lambda:True,release=lambda:None,texture=lambda:17)
                    host.gpu_compositor=compositor
                    gl=Mock()
                    self.assertTrue(host.paint_gpu_target_hud_layer(gl,3840/dpr,2160/dpr,dpr))
                    commands=gl.glTexSubImage2D.call_args_list
                    self.assertTrue(commands)
                    for command in commands:
                        _,_,x,y,w,h,_,_,payload=command.args
                        self.assertTrue(0<w<=512 and 0<h<=512)
                        self.assertTrue(0<=x<x+w<=3840 and 0<=y<y+h<=2160)
                        self.assertEqual(len(payload),w*h*4)
                        pixels=np.frombuffer(payload,np.uint8).reshape(h,w,4)
                        self.assertTrue(pixels[:,:,3].any())
                        self.assertTrue(np.all(pixels[:,:,:3]<=pixels[:,:,3,None]))
                    gl.glClear.assert_called_once()
                    gl.glTexImage2D.assert_not_called()
                    self.assertEqual(host.presence_calls,1)

    def test_active_target_hud_keeps_direct_raster_text_and_single_presence_update(self):
        from rm_client.model import RobotState
        from rm_client.combat_hud import CombatHudAnimator
        from rm_client.ui.polar.text_metrics import TextMetricsMixin
        from rm_client.ui.vision_motion import TargetHudPresenceFrame
        class ActiveTargetHost(TargetHost):
            draw_text=TextMetricsMixin.draw_text
            combat_target_visual=target.CombatTargetHudMixin.combat_target_visual
            def __init__(self,visual,bars,phase):
                super().__init__(1.34)
                self.state=RobotState();self.gui_motion=NS(now=1);self.frame_dt=.016
                self._ui_frame_index=1;self._combat_hud_frame_index=0;self._combat_hud_last_time=.984
                self.combat_hud_animator=CombatHudAnimator()
                module=self.registry.get('CombatTargetHUD');module.enabled=True
                module.get_setting('Visual').value=visual;module.get_setting('Show Bars').value=bars
                self.presence=TargetHudPresenceFrame(.5 if phase=='Fading' else 1,1.08,phase)
            def update_presence(self,_now,_valid):
                self.presence_calls+=1;return self.presence
            def font(self,_name,size):
                font=QFont('DejaVu Sans');font.setPointSizeF(size);return font
            def elide(self,_p,_font,text,_width):return text
            def setting_bool(self,module,key,fallback):
                setting=module.get_setting(key) if module else None
                return bool(setting.value) if setting else fallback
        for visual in ['Classic','Clear','Enhanced']:
            for bars in [False,True]:
                for phase in ['Visible','Grace10s','Fading']:
                    for dpr in [1,2]:
                        with self.subTest(visual=visual,bars=bars,phase=phase,dpr=dpr):
                            host=ActiveTargetHost(visual,bars,phase)
                            with patch.object(target,'QPicture',side_effect=AssertionError('HUD must draw directly')):
                                self.assertTrue(host.run(720,dpr))
                            self.assertEqual(host.draw_calls,0);self.assertEqual(host.presence_calls,1)
                            self.assertEqual(len(host.uploads),1)
                            actual,_,_=host.uploads[0]
                            reference=QImage(actual.size(),actual.format());reference.fill(0)
                            p=QPainter(reference)
                            p.translate(reference.width()/2,reference.height()/2)
                            p.scale(host.hud_scale*dpr,host.hud_scale*dpr)
                            lw=max(1,int(host.width/host.hud_scale));lh=max(1,int(host.height/host.hud_scale))
                            p.translate(-lw/2,-lh/2)
                            direct=ActiveTargetHost(visual,bars,phase)
                            direct.paint_combat_target_overlay(p,lw,lh,None,1)
                            p.end()
                            self.assertTrue(np.array_equal(array(actual),array(reference)))

    def test_failed_clear_or_upload_propagates_without_extra_draws(self):
        host=TargetHost();host.clear_ok=False
        self.assertFalse(host.run(1080));self.assertEqual(host.draw_calls,0);self.assertFalse(host.uploads)
        host=TargetHost();host.upload_ok=False;host.module.get_setting('Crosshair Scale').value=8
        self.assertFalse(host.run(2160));self.assertEqual(host.draw_calls,1);self.assertEqual(len(host.uploads),1)

    def test_menu_clears_without_recording_crosshair(self):
        host=TargetHost();host.menu=True
        self.assertTrue(host.run(1080));self.assertEqual(host.draw_calls,0);self.assertFalse(host.uploads)

    def test_zero_thickness_has_no_target_upload(self):
        host=TargetHost();host.module.get_setting('Thickness').value=0
        self.assertTrue(host.run(1080));self.assertEqual(host.draw_calls,1);self.assertFalse(host.uploads)


if __name__=='__main__':unittest.main()
