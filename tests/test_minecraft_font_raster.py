"""Production HUD cache must retain the bundled Unihex pixels without re-styling."""
from pathlib import Path
import unittest
from unittest.mock import patch
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.model import RobotState, build_default_registry
from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer, HUD_LAYER_DPR

ROOT = Path(__file__).resolve().parents[1]

def white_mask(image, color=QColor('white')):
    return {(x,y) for x in range(image.width()) for y in range(image.height())
            if image.pixelColor(x,y) == color}

class FontRasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.renderer = MinecraftCombatHudRenderer(ROOT / 'assets/minecraft_hud')
        self.registry = build_default_registry(discover_devices=False)
        self.module = self.registry.get('MinecraftCombatHUD')
        self.registry.get('ClickGui').get_setting('Language').value = '简体中文'
        self.module.get_setting('GUI Scale').value = '2'
        self.state = RobotState()
    def draw(self, width=1280, height=720, dpr=1):
        image = QImage(width*dpr, height*dpr, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr); image.fill(Qt.transparent)
        painter = QPainter(image)
        try: layout = self.renderer.draw(painter,width,height,self.state,self.registry,self.module)
        finally: painter.end()
        return image, layout
    def test_actual_cached_scoreboard_retains_every_native_cjk_stroke(self):
        for char in '热量剩余时间弹药受击小陀螺':
            with self.subTest(char=char), patch.object(self.renderer,'scoreboard_lines',return_value=(('',QColor('white')),(char,QColor('white')),('',QColor('white')))):
                self.renderer._layer_next_refresh_at=0
                _, screen = self.draw()
                logical = self.renderer._logical_layout(*screen.logical_size, ('',char,''))
                x=round((logical.scoreboard.left()+3)*HUD_LAYER_DPR)
                y=round((logical.scoreboard.top()+2+9)*HUD_LAYER_DPR)
                actual = self.renderer._layer_image.copy(x,y,16,16)
                glyph,_=self.renderer.styled_font.glyph(char)
                self.assertEqual(white_mask(actual),white_mask(glyph))
    def test_native_glyph_survives_final_two_times_screen_composition(self):
        with patch.object(self.renderer,'scoreboard_lines',return_value=(('',QColor('white')),('热',QColor('white')),('',QColor('white')))):
            image, screen = self.draw()
            self.assertEqual(screen.scale,2)
            x=round(screen.scoreboard.left()+6);y=round(screen.scoreboard.top()+22)
            self.assertEqual(white_mask(image.copy(x,y,16,16)),white_mask(self.renderer.styled_font.glyph('热')[0]))
    def test_previous_one_times_cache_reproduces_lost_strokes(self):
        with patch.object(self.renderer,'scoreboard_lines',return_value=(('',QColor('white')),('热',QColor('white')),('',QColor('white')))), patch('rm_client.ui.minecraft_hud.HUD_LAYER_DPR',1):
            image,screen=self.draw()
            x=round(screen.scoreboard.left()+6);y=round(screen.scoreboard.top()+22)
            original=white_mask(self.renderer.styled_font.glyph('热')[0])
            actual=white_mask(image.copy(x,y,16,16))
            self.assertGreater(len(original-actual),0)
            self.assertNotEqual(original,actual)
    def test_fractional_gui_scale_keeps_logical_geometry_and_native_cache(self):
        self.module.get_setting('Scale Multiplier').value=1.25
        _,layout=self.draw()
        self.assertEqual(layout.scale,2.5)
        self.assertEqual(self.renderer._layer_image.devicePixelRatio(),2)
        self.assertEqual((self.renderer._layer_image.width()/2,self.renderer._layer_image.height()/2),layout.logical_size)
    def test_sim_heat_label_preserves_same_native_glyph(self):
        self.state.mujoco_operator=True
        self.state.mujoco_telemetry_valid=True
        _,screen=self.draw()
        logical=self.renderer._logical_layout(*screen.logical_size)
        x=round(logical.armor[0].left()*2)
        y=round((logical.armor[0].top()-7)*2)
        actual=self.renderer._layer_image.copy(x,y,16,16)
        self.assertEqual(white_mask(actual,QColor('#55ffff')),white_mask(self.renderer.styled_font.glyph('热')[0]))
    def test_120hz_telemetry_changes_do_not_force_120_rebuilds(self):
        with patch('rm_client.ui.minecraft_hud.time.perf_counter') as clock:
            for i in range(120):
                clock.return_value=1000+i/120
                self.state.remaining_ammo=i
                self.draw()
        self.assertGreaterEqual(self.renderer.layer_rebuilds,29)
        self.assertLessEqual(self.renderer.layer_rebuilds,31)
    def test_cache_logical_extent_and_reuse_are_unchanged(self):
        _, layout = self.draw()
        layer=self.renderer._layer_image
        self.assertEqual(layer.devicePixelRatio(),2)
        self.assertEqual((layer.width()/2,layer.height()/2),layout.logical_size)
        builds=self.renderer.layer_rebuilds
        self.draw();self.assertEqual(builds,self.renderer.layer_rebuilds)
        self.draw(1440,900);self.assertEqual(builds+1,self.renderer.layer_rebuilds)
    def test_destination_dpr_does_not_change_layout_or_rebuild_cache(self):
        _, layout1=self.draw();builds=self.renderer.layer_rebuilds
        _, layout2=self.draw(dpr=2)
        self.assertEqual(layout1,layout2)
        self.assertEqual(builds,self.renderer.layer_rebuilds)
    def test_bitmap_ascii_and_hotbar_remain_exact_nearest_neighbour(self):
        image, layout=self.draw()
        # Uncovered far edge of the hotbar has an exact 2x upscale of the source.
        tile=self.renderer.images['hotbar']
        expected=tile.copy(180,0,2,22).scaled(4,44,Qt.IgnoreAspectRatio,Qt.FastTransformation)
        actual=image.copy(round(layout.hotbar.right()-4),round(layout.hotbar.top()),4,44)
        self.assertEqual(actual,expected)
        font=self.renderer.styled_font
        for char in 'HEAT ROOM 123':
            glyph,_=font.glyph(char)
            # Existing bitmap provider pixels are still doubled, not smoothed.
            for y in range(0,16,2):
                for x in range(0,glyph.width(),2):
                    self.assertEqual(glyph.pixelColor(x,y),glyph.pixelColor(x,min(y+1,15)))

if __name__=='__main__':unittest.main()
