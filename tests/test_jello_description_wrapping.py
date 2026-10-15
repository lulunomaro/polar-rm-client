"""Real-font lossless wrapping, viewport ownership and DPR atlas regressions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD', '1')
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from types import MethodType
import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QWheelEvent
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.jello_clickgui import JelloClickGuiRenderer
from rm_client.ui.jello_locale import description_label, chinese_enabled
from rm_client.ui.fonts import FontBook, ui_text_layout
from rm_client.ui import clickgui_gpu as gpu
from test_clickgui_background_dismiss import Host, APP
from test_jello_atlas_pages import Texture

class DescriptionWrappingTests(unittest.TestCase):
    def setUp(self):
        self.host=Host();self.host.resize(1280,720)
        self.fonts=FontBook();self.fonts.load_from_project_assets(ROOT)
        self.host.font=self.fonts.font
        self.host.renderer=JelloClickGuiRenderer(ROOT/'assets/jello')
        self.host.jello_clickgui=self.host.renderer
        self.host._jello_gpu_fonts=MethodType(PolarRMWindow._jello_gpu_fonts,self.host)
        self.addCleanup(self.host.deleteLater)
    def commands(self,module_name):
        state=self.host.jello_state
        if state.settings_module!=module_name:state.open_settings(module_name)
        state.settings_panel_animation.reset(1);state.settings_background_animation.reset(1)
        self.host.hotspots=[];self.host.gui_scroll_viewports=[]
        return self.host.renderer.build(self.host,state,self.host.width(),self.host.height(),1)
    def test_lossless_unicode_whitespace_and_grapheme_boundaries(self):
        text='英文 description A😀B e\u0301 👩\u200d🔧 中文说明 '\
             'abcdefghijklmnopqrstuvwxyz0123456789\n\n最后一行'
        font=self.host._jello_gpu_fonts()[20]
        lines=self.host.renderer.wrapped_description(font,text,85)
        self.assertEqual(''.join(lines),text)
        self.assertGreater(len(lines),2)
        self.assertTrue(any('e\u0301' in line for line in lines))
        self.assertTrue(any('👩\u200d🔧' in line for line in lines))
        self.assertIs(lines,self.host.renderer.wrapped_description(font,text,85))
    def test_all_74_default_modules_prewarm_at_dpr_1_and_2(self):
        for dpr in (1,2):
            with self.subTest(dpr=dpr),patch.object(gpu,'QOpenGLTexture',Texture):
                renderer=gpu.ClickGuiGpuRenderer();renderer.available=True
                fonts=self.host._jello_gpu_fonts()
                for module in self.host.registry.modules:
                    pages=PolarRMWindow._jello_module_text_pages(self.host,module,fonts)
                    for size,texts in pages.items():
                        renderer.prewarm((fonts[size],),texts,dpr,preserve_weight=True,text_profile='jello')
                        key=renderer._font_key(fonts[size],1,True)
                        if any(texts):self.assertTrue({str(t) for t in texts if str(t)}.issubset(renderer.shaped_atlases[key].lines))
                    for language in ('English', '中文'):
                        self.host.registry.get('ClickGui').get_setting('Language').value=language
                        commands=self.commands(module.name)
                        renderer.prepare_frame(commands,dpr)
                        self.assertTrue(renderer._resources_ready(commands),(module.name,language,dpr))
                self.assertEqual(len(self.host.registry.modules),74)
                renderer.release()
    def test_module_description_lines_are_scrollable_without_overlapping_controls(self):
        self.host.registry.get('ClickGui').get_setting('Language').value='English'
        module=self.host.registry.get('AutoSpinOnHit')
        commands=self.commands(module.name)
        font=self.host._jello_gpu_fonts()[20]
        lines=self.host.renderer.wrapped_description(font,module.description,440)
        found=[t for t in commands.texts if t.font.pointSizeF()==font.pointSizeF() and t.text in lines]
        self.assertEqual(''.join(t.text for t in found),module.description)
        self.assertTrue(all(t.clip is not None and t.rect.width()<=t.clip.width() for t in found))
        first_control=next(h for h in self.host.hotspots if h.action.startswith('setting_'))
        self.assertGreaterEqual(first_control.rect.top(),found[-1].rect.bottom())
        module.description=('Long description 中文说明 and more information. '*80)
        commands=self.commands(module.name)
        viewport=next(v for v in self.host.gui_scroll_viewports if v['key']==module.name)
        self.assertGreater(viewport['maximum'],0)
        lines=self.host.renderer.wrapped_description(font,module.description,440)
        target=[t for t in commands.texts if t.font.pointSizeF()==font.pointSizeF() and t.text in lines][-1]
        needed=max(0,target.rect.bottom()-viewport['rect'].bottom())
        self.assertLessEqual(needed,viewport['maximum'])
        self.host.jello_state.settings_scroll[module.name]=needed
        visible=self.commands(module.name)
        target=[t for t in visible.texts if t.font.pointSizeF()==font.pointSizeF() and t.text in lines][-1]
        self.assertLessEqual(target.rect.bottom(),target.clip.bottom()+.1)
    def test_long_hint_stays_inside_viewport_and_preserves_all_text(self):
        module=self.host.registry.get('RobotLink')
        setting=max(module.settings,key=lambda s:len(s.description))
        for size in ((640,400),(1000,620),(1280,720),(2560,1440)):
            with self.subTest(size=size):
                self.host.resize(*size);self.commands(module.name)
                state=self.host.jello_state
                state.hovered_setting_name=setting.name
                state.hovered_setting_description=description_label(setting.description,chinese_enabled(self.host.registry))
                state.setting_hint_animation.reset(1)
                commands=self.commands(module.name)
                card=state._setting_hint_rect
                self.assertTrue(QRectF(0,0,*size).contains(card))
                self.assertTrue(any(h.action=='jello_noop' and h.rect==card for h in self.host.hotspots))
                self.host.hover_point=card.center()
                self.commands(module.name)
                self.assertEqual(state.setting_hint_animation.target,1)
                self.assertEqual(state.hovered_setting_name,setting.name)
                body=[t for t in commands.texts if t.clip is not None and card.contains(t.clip) and t.text!=setting.name]
                self.assertEqual(''.join(t.text for t in body),state.hovered_setting_description)
                self.host.hover_point=QPointF(-1,-1)
    def test_oversized_help_uses_existing_scroll_route_and_resets_on_new_module(self):
        self.commands('RobotLink');state=self.host.jello_state
        state.hovered_setting_name='Long help';state.hovered_setting_description='说明 full help text. '*500
        state.setting_hint_animation.reset(1);self.commands('RobotLink')
        viewport=next(v for v in self.host.gui_scroll_viewports if v['key'].startswith('hint:'))
        self.assertGreater(viewport['maximum'],0)
        self.assertEqual(viewport['mode'],'jello_settings')
        self.host.hover_point=viewport['rect'].center()
        state.scroll_settings_by(viewport['key'],100,viewport['maximum']);state.update(.05)
        self.commands('RobotLink')
        self.assertGreater(state.settings_scroll[viewport['key']],0)
        self.assertEqual(state.setting_hint_animation.target,1)
        state.open_settings('AutoSpinOnHit')
        self.assertTrue(state._setting_hint_rect.isEmpty())
        self.assertEqual(state.setting_hint_animation.progress,0)

    def test_entire_help_card_owns_wheel_without_scrolling_obscured_settings(self):
        module=self.host.registry.get('RobotLink')
        setting=max(module.settings,key=lambda s:len(s.description))
        for help_text in (description_label(setting.description,True),'Full help 中文说明. '*500):
            with self.subTest(overflow=len(help_text)>500):
                self.commands(module.name);state=self.host.jello_state
                state.hovered_setting_name=setting.name
                state.hovered_setting_description=help_text
                state.setting_hint_animation.reset(1)
                self.commands(module.name)
                card=state._setting_hint_rect
                module_view=next(v for v in self.host.gui_scroll_viewports if v['key']==module.name)
                self.assertGreater(module_view['maximum'],0)
                # Title, side padding and body are all owned by the topmost card.
                for point in (QPointF(card.center().x(),card.top()+16),
                              QPointF(card.left()+2,card.center().y()),card.center()):
                    prior=state.settings_scroll_target(module.name)
                    event=QWheelEvent(point,point,QPoint(0,0),QPoint(0,-120),
                                      Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,
                                      Qt.ScrollPhase.NoScrollPhase,False)
                    event.setAccepted(False)
                    APP.sendEvent(self.host,event)
                    self.assertTrue(event.isAccepted())
                    self.assertEqual(state.settings_scroll_target(module.name),prior)
                if len(help_text)>500:
                    self.assertGreater(state.settings_scroll_target(f'hint:{module.name}:{setting.name}'),0)

    def test_mixed_cjk_line_heights_do_not_clip_fallback_faces(self):
        self.commands('RobotLink')
        state=self.host.jello_state
        state.hovered_setting_name='完整说明'
        state.hovered_setting_description='中文字体的行高必须保留，不能按拉丁字体裁掉笔画。'*5
        state.setting_hint_animation.reset(1)
        commands=self.commands('RobotLink')
        for command in commands.texts:
            if command.clip is None or not command.text:
                continue
            if state._setting_hint_rect.contains(command.clip):
                height=ui_text_layout(command.font,command.text).lineAt(0).height()
                self.assertGreaterEqual(command.rect.height()/command.render_scale,height)
        module=self.host.registry.get('RobotLink')
        lines=self.host.renderer.wrapped_description(self.host._jello_gpu_fonts()[20],
                    description_label(module.description,True),440)
        for command in commands.texts:
            if command.text in lines and command.font.pointSizeF()==self.host._jello_gpu_fonts()[20].pointSizeF():
                height=ui_text_layout(command.font,command.text).lineAt(0).height()
                self.assertGreaterEqual(command.rect.height()/command.render_scale,height)

    def test_empty_descriptions_build_and_new_module_clears_stale_hint(self):
        self.host.registry.get('AutoSpinOnHit').description=''
        commands=self.commands('AutoSpinOnHit')
        state=self.host.jello_state
        state.hovered_setting_name='';state.hovered_setting_description=''
        state.setting_hint_animation.reset(1)
        self.commands('AutoSpinOnHit')
        state.open_settings('RobotLink')
        self.assertTrue(state._setting_hint_rect.isEmpty())
        self.assertFalse(state._setting_hint_hovered)

if __name__=='__main__':unittest.main(verbosity=2)
