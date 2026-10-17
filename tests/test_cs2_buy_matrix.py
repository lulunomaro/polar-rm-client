"""Full buy matrix: resolution/clip, product fidelity and safe selection."""
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtCore import QPointF,QRectF
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from rm_client.economy import EconomyOffer
from rm_client.model import RobotState,build_default_registry
from rm_client.runtime_paths import RuntimePaths
from rm_client.ui.cs2_assets import Cs2Assets
from rm_client.ui.cs2_buy_menu import build_grid,grid_layout
from rm_client.ui.economy_workspace import EconomyWorkspaceLayer
from rm_client.ui.polar.primitives import InertialScroll
from rm_client.ui.polar.mixins.input_dispatch_mixin import _on_action_economy_offer


class Host:
    def __init__(self):
        self.registry=build_default_registry();self.state=RobotState()
        self.paths=RuntimePaths.discover()
        self.economy_workspace_layer=EconomyWorkspaceLayer()
        self.kill_fx=NS(cs2=NS(assets=Cs2Assets()))
        self.economy_island=NS(progress=1)
        self.economy_bridge=NS(latest=None,history=[])
        self.economy_category="ammo";self.economy_visible=True
        self.economy_selected_key="ammo0";self.economy_armed_key="";self.economy_armed_until=0
        self.economy_hold_key="";self.economy_hold_started=0
        self.student_reload_expanded=False
        self.student_ui=NS(gate="Left",quantity=10)
        self.economy_offers_scroll=InertialScroll();self.economy_detail_scroll=InertialScroll()
        self.hover_point=QPointF(-10,-10)
        self.anim=NS(value=lambda key,target,*args,**kwargs:target)
        self.glass=[];self.hotspots=[]
        self.gpu_compositor=NS(add_glass=lambda r,*args,**kw:self.glass.append((r,kw)))
        self.calls=[]
        self.offers=[EconomyOffer(category+str(i),category,"完整商品标题",category+str(i),"规则描述","颗",1,10,1,1,True)
                     for category,count in (("ammo",7),("recovery",3),("revive",2),("support",2)) for i in range(count)]

    def setting_mode(self,m,n,d):return str(m.get_setting(n).value) if m and m.get_setting(n) else d
    def font(self,family,size):
        f=QFont("Microsoft YaHei UI");f.setPointSizeF(size);return f
    def anim_duration(self,value):return value
    def add_hotspot(self,r,action,payload=None,tooltip=""):self.hotspots.append((QRectF(r),action,payload))
    def economy_offers(self,category=None):return [o for o in self.offers if category is None or o.category==category]
    def reconcile_economy_selection(self):return next(o for o in self.offers if o.key==self.economy_selected_key)
    def select_economy_offer(self,key):self.calls.append(key);self.economy_selected_key=key


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_all_target_resolutions_complete_columns_and_preview_inside_screen(self):
        for width,height in ((1280,720),(1600,900),(1920,1080),(2560,1440)):
            with self.subTest(size=(width,height)):
                host=Host();commands=build_grid(host,width,height)
                layout=grid_layout(host.economy_workspace_layer,width,height,1)
                viewport=QRectF(0,0,width,height)
                self.assertTrue(viewport.contains(layout.panel))
                self.assertTrue(layout.target_panel.contains(layout.offers_pane))
                self.assertTrue(layout.target_panel.contains(layout.detail_pane))
                self.assertTrue(layout.target_panel.contains(layout.action_button))
                titles={t.text for t in commands.texts}
                self.assertTrue({"1  弹丸","2  恢复","3  复活","4  支援","5  补给站"}<=titles)
                for rect,action,payload in host.hotspots:
                    self.assertTrue(layout.panel.contains(rect),action)
                self.assertGreater(host.economy_offers_scroll.maximum,0)

    def test_column_selection_is_not_purchase_and_reuses_real_offer_key(self):
        host=Host()
        _on_action_economy_offer(host,"revive1",QPointF(),1,QRectF())
        self.assertEqual(host.economy_category,"revive")
        self.assertEqual(host.calls,["revive1"])
        _on_action_economy_offer(host,"not-an-offer",QPointF(),1,QRectF())
        self.assertEqual(host.calls,["revive1"])

    def test_modal_open_close_phases_never_register_invisible_hits(self):
        host=Host()
        for progress in (0,.1,.2,.35,.5,.65):
            host.economy_island.progress=progress
            build_grid(host,1280,720)
            if not grid_layout(host.economy_workspace_layer,1280,720,progress).interactive:
                self.assertFalse(host.hotspots)
        host.economy_island.progress=1;host.economy_visible=False
        build_grid(host,1280,720)
        self.assertFalse(host.hotspots)

    def test_pending_and_disabled_offers_cannot_expose_confirm_hotspot(self):
        host=Host();host.economy_bridge.latest=NS(status="pending",message="wait")
        build_grid(host,1920,1080)
        self.assertFalse(any(action.startswith("economy_confirm") for _,action,_ in host.hotspots))
        host.economy_bridge.latest=None
        host.offers[0]=replace(host.offers[0],enabled=False,reason="条件不足")
        build_grid(host,1920,1080)
        self.assertFalse(any(action.startswith("economy_confirm") for _,action,_ in host.hotspots))

    def test_hold_and_double_confirm_keep_existing_dispatch(self):
        host=Host();build_grid(host,1920,1080)
        self.assertIn("economy_confirm",{action for _,action,_ in host.hotspots})
        host.offers[0]=replace(host.offers[0],confirmation="hold")
        build_grid(host,1920,1080)
        self.assertIn("economy_confirm_hold",{action for _,action,_ in host.hotspots})

    def test_glass_uses_existing_material_parameters_and_root_clip(self):
        host=Host();build_grid(host,1920,1080)
        for rect,kwargs in host.glass:
            self.assertEqual(kwargs["depth_effect"],1)
            self.assertEqual(kwargs["frost_amount"],0)
            self.assertEqual(kwargs["chromatic_aberration"],0)
            self.assertEqual(kwargs["strength"],1)
            self.assertFalse(kwargs["clip_rect"].isEmpty())

    def test_supplier_unknown_is_not_faked_known_and_reload_controls_remain(self):
        host=Host();commands=build_grid(host,1920,1080)
        self.assertIn("等待官方状态",{t.text for t in commands.texts})
        self.assertIn("student_reload_toggle",{action for _,action,_ in host.hotspots})
        host.student_reload_expanded=True
        commands=build_grid(host,1920,1080)
        self.assertIn("student_gate",{action for _,action,_ in host.hotspots})


if __name__=="__main__":unittest.main()
