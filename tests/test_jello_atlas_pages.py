"""Real Qt/font raster regressions; only GL texture upload is replaced.

No device or backend is connected. These checks do not claim GPU acceptance.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QFont, QImage
from PySide6.QtCore import QRectF
from rm_client.ui import clickgui_gpu as gpu
from rm_client.ui.fonts import FontBook

APP=QApplication.instance() or QApplication([])
REAL_TEXTURE=gpu.QOpenGLTexture
class Texture:
    MipMapGeneration=REAL_TEXTURE.MipMapGeneration
    Filter=REAL_TEXTURE.Filter
    WrapMode=REAL_TEXTURE.WrapMode
    created=[]
    def __init__(self,image,*args):
        self.image=QImage(image); self.destroyed=0; self.created.append(self)
    def setMinificationFilter(self,*args): pass
    def setMagnificationFilter(self,*args): pass
    def setWrapMode(self,*args): pass
    def destroy(self): self.destroyed+=1
    def bind(self,*args): pass
    def release(self): pass

class ShapedPageTests(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(gpu,'QOpenGLTexture',Texture);self.patch.start()
        self.addCleanup(self.patch.stop);Texture.created=[]
        self.renderer=gpu.ClickGuiGpuRenderer();self.renderer.available=True
        self.fonts=FontBook();self.fonts.load_from_project_assets(ROOT,keys={'jello_light','jello_medium'})
        self.font=self.fonts.font('jello_light',18.75)
    def ensure(self,texts):
        return self.renderer._ensure_shaped_atlas(self.font,texts,preserve_weight=True)
    def test_missing_line_keeps_existing_texture_and_pixels(self):
        first=self.ensure({'先前设置 Previous setting'})
        texture=first.texture;pixels=texture.image.copy()
        second=self.ensure({'先前设置 Previous setting','新设置 New setting'})
        self.assertEqual(texture.destroyed,0,'Adding one line must not replace/repaint the old page')
        self.assertEqual(texture.image,pixels)
        self.assertEqual(len(second.pages),2)
        self.assertIs(second.line_pages['先前设置 Previous setting'].texture,texture)
        self.assertEqual(set(second.lines),{'先前设置 Previous setting','新设置 New setting'})
    def test_sequential_large_sets_do_not_overflow_historical_page(self):
        self.renderer._pixel_ratio=2
        for group in range(12):
            requested={f'{group:02}.{i:02} 设置 Configuration abcdefghijklmnopqrstuvwxyz0123456789' for i in range(24)}
            atlas=self.ensure(requested)
            self.assertTrue(requested.issubset(atlas.lines))
        self.assertTrue(all(t.image.width()<=4096 and t.image.height()<=4096 for t in Texture.created))
    def test_failed_publication_keeps_known_lines_and_old_texture(self):
        first=self.ensure({'old'})
        key=self.renderer._font_key(self.font,1.0,True)
        with patch.object(gpu,'QOpenGLTexture',side_effect=RuntimeError('upload failed')):
            with self.assertRaises(RuntimeError):self.ensure({'new'})
        self.assertIs(self.renderer.shaped_atlases[key],first)
        self.assertEqual(self.renderer._shaped_texts[key],{'old'})
        self.assertEqual(first.texture.destroyed,0)
    def test_render_lock_does_not_change_published_coverage(self):
        first=self.ensure({'old'})
        key=self.renderer._font_key(self.font,1.0,True)
        self.renderer._atlas_locked=True
        with self.assertRaisesRegex(RuntimeError,'atlas mutation'):self.ensure({'new'})
        self.assertEqual(self.renderer._shaped_texts[key],{'old'})
        self.assertIs(self.ensure({'old'}),first)

    def test_eviction_preserves_requested_pages_and_reclaims_layouts(self):
        self.renderer._SHAPED_CACHE_PAGES=2
        first=self.ensure({'first'});first_texture=first.texture
        self.ensure({'second'})
        current=self.ensure({'first','third'})
        self.assertEqual(set(current.lines),{'first','third'})
        self.assertEqual(first_texture.destroyed,0)
        self.assertEqual(Texture.created[1].destroyed,1)
        key=self.renderer._font_key(self.font,1.0,True)
        self.assertEqual({text for (font_key,text) in self.renderer._shaped_layouts if font_key==key},set(current.lines))

    def test_partial_allocation_failure_destroys_only_new_pages_and_retries(self):
        self.renderer._SHAPED_PAGE_LINES=1
        old=self.ensure({'old'})
        original=self.renderer._rasterize_shaped_page
        calls=[]
        def fail_second(*args):
            if calls: raise RuntimeError('second upload failed')
            result=original(*args);calls.append(result);return result
        with patch.object(self.renderer,'_rasterize_shaped_page',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'second upload'):self.ensure({'new1','new2'})
        self.assertEqual(calls[0].texture.destroyed,1)
        self.assertEqual(old.texture.destroyed,0)
        retried=self.ensure({'new1','new2'})
        self.assertTrue({'old','new1','new2'}.issubset(retried.lines))

    def test_page_batching_keeps_adjacent_command_order(self):
        self.renderer._SHAPED_PAGE_LINES=1
        atlas=self.ensure({'A','B'})
        recorded=[]
        self.renderer._draw_shaped_page_batch=lambda fn,commands,page,w,h:recorded.append(([c.text for c in commands],page))
        self.renderer._draw_shaped_text_batch(None,[NS(text=t) for t in ('A','A','B','A')],atlas,1280,720)
        self.assertEqual([texts for texts,page in recorded],[['A','A'],['B'],['A']])
        self.assertEqual([page for texts,page in recorded],[atlas.line_pages[t] for t in ('A','B','A')])

    def test_release_destroys_all_retained_pages_once(self):
        self.renderer._SHAPED_PAGE_LINES=1
        self.ensure({'A','B','C'})
        textures=list(Texture.created)
        self.renderer.release();self.renderer.release()
        self.assertTrue(all(t.destroyed==1 for t in textures))
        self.assertFalse(self.renderer._shaped_layouts)
        self.assertFalse(self.renderer._shaped_texts)

    def test_glyph_upload_failure_does_not_poison_warm_retry(self):
        first=self.renderer._ensure_atlas(self.font,'old')
        key=self.renderer._font_key(self.font)
        with patch.object(gpu,'QOpenGLTexture',side_effect=RuntimeError('upload failed')):
            with self.assertRaises(RuntimeError):self.renderer._ensure_atlas(self.font,'new')
        self.assertIs(self.renderer.atlases[key],first)
        self.assertEqual(self.renderer._atlas_chars[key],set('old'))
        retried=self.renderer._ensure_atlas(self.font,'new')
        self.assertTrue(set('new').issubset(retried.glyphs))

    def test_production_page_submission_packs_every_command_once(self):
        self.renderer._SHAPED_PAGE_LINES=1
        self.ensure({'A','B'})
        commands=gpu.ClickGuiDrawList()
        for text in ('A','A','B','A'):
            commands.text(QRectF(10,10,100,40),text,self.font,QColor('white'),
                          shadow=False,text_profile='jello',preserve_weight=True)
        self.renderer.text_program=Mock()
        self.renderer.text_uniforms={'viewport':0,'glyph':1}
        self.renderer.instance_buffer=Mock()
        self.renderer._bind_quad=lambda p:None
        self.renderer._unbind_quad=lambda p:None
        functions=Mock()
        self.renderer._draw_text_batches(functions,commands.texts,1280,720)
        self.assertEqual(self.renderer.draw_calls,3)
        self.assertEqual([call.args[1] for call in self.renderer.instance_buffer.allocate.call_args_list],[120,60,60])
        self.assertEqual([call.args[-1] for call in functions.glDrawArraysInstanced.call_args_list],[2,1,1])

if __name__=='__main__':unittest.main(verbosity=2)
