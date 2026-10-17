"""Student frontend: authoritative displays, inert drafts and modal isolation.

Use Windows Qt for font checks; offscreen runs may register the Windows font
explicitly because the offscreen plugin doesn't enumerate system fonts.
No live capture, protocol publisher, evidence/Arm or user config is touched.
"""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from window_lifecycle_helpers import close_window_safely, settle_window_lifecycle
os.environ["POLAR_DISABLE_LOCAL_CAPTURE"]="1"
os.environ["POLAR_DISABLE_MEDIA_PRELOAD"]="1"
DATA=TemporaryDirectory(prefix="polar_student_")
os.environ["POLAR_USER_DATA_ROOT"]=DATA.name
if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtCore import QPointF, QRectF, Qt, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QImage, QKeyEvent, QPainter
from PySide6.QtWidgets import QApplication
from rm_client.model import RobotState, build_default_registry
from rm_client.config_store import apply_registry_config, export_client_config
from rm_client.combat_events import CombatEvent
from rm_client.match_chat import MatchChatController
from rm_client.student_ui import active_buffs, damage_rows, device_rows, rune_summary, StudentUiState, StudentNoticeAdapter
from rm_client.ui.overlay_coordinator import OverlayKind
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.student_reload import append_reload_details, reload_detail_height
from rm_client.ui.clickgui_gpu import ClickGuiDrawList
from rm_client.ui.polar.primitives import EconomyScenePhase
from rm_client.ui.minecraft_presentation import plain_text

COUNT=0
def check(condition, label):
    global COUNT
    assert condition,label
    COUNT+=1
    print(f"[PASS] {label}",flush=True)

def key(k, pressed=True):
    return QKeyEvent(QEvent.Type.KeyPress if pressed else QEvent.Type.KeyRelease,k,Qt.KeyboardModifier.NoModifier)

class Mouse:
    def __init__(self,point,button=Qt.MouseButton.LeftButton):self.point=point;self._button=button
    def position(self):return self.point
    def button(self):return self._button
    def accept(self):pass

def draw(w,width=1280,height=720,dpr=1.0):
    image=QImage(round(width*dpr),round(height*dpr),QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr);image.fill("#202832")
    painter=QPainter(image);w.draw_student_frontend(painter,width,height);painter.end()
    return image

def main():
    app=QApplication.instance() or QApplication([])
    if not QFontMetricsF(QFont("Microsoft YaHei UI")).inFont("伤"):
        for candidate in (Path("C:/Windows/Fonts/msyh.ttc"),Path("C:/Windows/Fonts/msyhbd.ttc")):
            if candidate.exists():QFontDatabase.addApplicationFont(str(candidate))
    check(QFontMetricsF(QFont("Microsoft YaHei UI")).inFont("伤"),"Chinese font has real glyphs")
    state=RobotState();registry=build_default_registry()
    check(len({m.name for m in registry.modules})==len(registry.modules),"unique module names")
    state.injury_stats={101:{"total_damage":100},102:{"total_damage":130}}
    check(damage_rows(state)[0][1]=="130","cumulative damage is not summed")
    state.student_ui_data["injury_snapshot"]={"total_damage":12}
    check(damage_rows(state)[0][1]=="12","respawn snapshot can decrease")
    state.student_ui_data.clear();state.injury_stats.clear()
    check(all(value=="—" for _,value in damage_rows(state)),"unknown damage stays unknown")
    state.rune_status={"average_rings":8.5,"activated_arms":4}
    check("推算总环 34" in rune_summary(state),"rune total explicitly estimated")
    state.buffs=[{"robot_id":state.robot_id,"buff_type":2,"buff_level":-2,"buff_left_time":12},
                 {"robot_id":103,"buff_type":3,"buff_level":2,"buff_left_time":12},
                 {"robot_id":state.robot_id,"buff_type":1,"buff_left_time":0}]
    check(len(active_buffs(state))==1 and "易伤" in active_buffs(state)[0]["name"],"only active local buffs")
    state.student_ui_data["buffs"]=None
    check(len(active_buffs(state))==1,"malformed optional buff data harmless")
    registry.get("ArmorStatus").enabled=True
    check("等待" in device_rows(state,registry)[0][1],"module does not invent online")
    state.robot_module_status={"armor":2}
    check("安装异常" in device_rows(state,registry)[0][1],"official module status two is offline/improper")
    registry.get("ArmorStatus").get_setting("Show Status").value=False
    check(not device_rows(state,registry),"device setting is consumed")
    ui=StudentUiState();ui.open("darts");ui.update(.45)
    check(ui.progress==1,"exact 450ms timeline")
    ui.close();ui.update(.125)
    check(not ui.visible and ui.panel is None,"exact 125ms close")
    chat=MatchChatController();notices=StudentNoticeAdapter(chat)
    state.current_stage="COUNTDOWN";notices.poll(state,1)
    state.current_stage="MATCH";notices.poll(state,2)
    notices.combat(CombatEvent(kind="elimination",source="official",timestamp=3,killer_id=1,victim_id=101,confirmed=True))
    notices.combat(CombatEvent(kind="elimination",source="official",timestamp=4,killer_id=1,victim_id=102,confirmed=True))
    check(any("首血" in m.text for m in chat.messages) and any("双杀" in m.text for m in chat.messages),"official awards accompany existing kill FX")
    before=len(chat.messages)
    notices.combat(CombatEvent(kind="elimination",source="preview",timestamp=4.1,killer_id=1,victim_id=103,confirmed=True))
    check(len(chat.messages)==before,"preview cannot manufacture official awards")
    notices.combat(CombatEvent(kind="elimination",source="team_byte_block",timestamp=4.2,killer_id=1,victim_id=103,confirmed=True))
    notices.combat(CombatEvent(kind="elimination",source="referee",timestamp=4.3,killer_id=0,victim_id=103,confirmed=True))
    check(len(chat.messages)==before,"custom events and unknown killers don't create official awards")
    state.active_penalty={"penalty_type":3,"total_penalty_num":1,"penalty_effect_sec":10}
    notices.poll(state,5);notices.poll(state,6)
    check(sum("红牌" in m.text for m in chat.messages)==1,"penalty signature prevents per-frame spam")
    w=PolarRMWindow();w.timer.stop()
    try:
        # These are normal UI interactions, after startup configuration commits.
        settle_window_lifecycle(w,app)
        w.home_visible=False
        glyphs=[];bitmap_draw=w.server_chat_renderer.bitmap.draw
        def observe_glyph(*args,**kwargs):
            glyphs.append(args[3]);return bitmap_draw(*args,**kwargs)
        w.server_chat_renderer.bitmap.draw=observe_glyph
        w.server_chat_renderer.lines("A·汉",100,"#ffffff",1)
        w.server_chat_renderer.bitmap.draw=bitmap_draw
        check(glyphs==["A"],"chat punctuation is not mistaken for an unrelated MC bitmap tile")
        # All new UI operations must remain inert even if a backend publisher
        # is accidentally invoked in a future refactor.
        def forbidden(*args,**kwargs):raise AssertionError("student frontend published a command")
        for name in dir(w.backend):
            if name.startswith("publish_"):setattr(w.backend,name,forbidden)
        w.auto_save_config=lambda:None
        w.gui_visible=False;w.gui_timeline.progress=0
        w.registry.get("HUD").get_setting("Combat Preset").value="Minecraft"
        font=w.minecraft_hud.styled_font
        check(ord("伤") in font.hex_data and font.width("伤害")==18,"MC Chinese uses original Unihex pixel glyphs")
        colored=font.image("§eA §a2 §c3")
        colors={colored.pixelColor(x,y).name() for x in range(colored.width()) for y in range(colored.height()) if colored.pixelColor(x,y).alpha()}
        check({"#ffff55","#55ff55","#ff5555"}.issubset(colors),"vanilla palette textures correctly tint yellow/green/red")
        check(plain_text("§e§lRM§r UI")=="RM UI","formatting codes never enter layout widths")
        check(font.width("§lAB§rC")==font.width("ABC")+2,"MC bold advances are preserved without overlapping glyphs")
        check(all(not i.isNull() for i in w.student_renderer.items.images.values()),"vanilla inventory item resources present")
        check(w.student_renderer.items.item_name("ArmorAutoAim")=="aim_turret" and w.student_renderer.items.item_name("SpinMode")=="spin_chassis","original mod items depict functions, not vanilla loot substitutions")
        combat_icons=[w.student_renderer.items.image(name) for name in ("ArmorAutoAim","EnergyAutoAim","ArmorLock","HeroLob","FireControlStub","FrictionWheel","LockState")]
        check(len({hashlib.sha256(image.constBits()).hexdigest() for image in combat_icons})==7,"combat mods have seven distinct crafted silhouettes")
        check(w.student_renderer.items.image("ArmorAutoAim").size().width()==32 and
              w.student_renderer.items.image("ArmorAutoAim")!=w.student_renderer.items.images["arrow"],"crafted 32px pixel items are not untouched vanilla textures")
        for name in ("ArmorAutoAim","SpinMode","SkillConfiguration"):
            check(w.student_renderer.items.image(name).cacheKey()==w.minecraft_hud.items.image(name).cacheKey() or
                  w.student_renderer.items.image(name)==w.minecraft_hud.items.image(name),f"inventory/hotbar share item for {name}")
        check(not hasattr(w.student_renderer,"player"),"inventory has no Steve, portrait or mascot renderer")
        for name in ("steve.png","robot_operator.png","robot_operator_manifest.json"):
            check(not (w.student_renderer.asset_root/name).exists(),f"removed unused avatar resource {name}")
        check(w.handle_student_key(key(Qt.Key.Key_E),True),"E opens MC inventory")
        w.student_ui.progress=1;draw(w)
        check(w.student_ui.panel=="inventory" and w.overlay_coordinator.modal==OverlayKind.STUDENT,"inventory owns modal input")
        check(not w.combat_control_input_allowed(),"inventory blocks live combat keys")
        item=next(h for h in w.hotspots if h.action=="student_inventory_item")
        target=w.registry.get(item.payload);enabled=target.enabled
        w.handle_student_mouse(Mouse(item.rect.center(),Qt.MouseButton.RightButton),"press")
        check(not w.student_ui.dragging,"right click doesn't drag")
        w.handle_student_mouse(Mouse(item.rect.center()),"press")
        slot=next(h for h in w.hotspots if h.action=="student_slot" and h.payload==3)
        w.handle_student_mouse(Mouse(slot.rect.center()),"release")
        check(w.registry.get("MinecraftCombatHUD").get_setting("Slot 4").value==item.payload,"drag binds exact hotbar slot")
        check(target.enabled==enabled,"drag doesn't enable a module")
        w.handle_student_action("student_clear_slot",3)
        check(w.registry.get("MinecraftCombatHUD").get_setting("Slot 4").value=="Empty","right click clears binding")
        for resolution in ((1280,720),(1600,900),(1920,1080),(2560,1440)):
            for multiplier in (.5,1,1.5,2):
                w.registry.get("MinecraftCombatHUD").get_setting("Scale Multiplier").value=multiplier
                panel,scale=w.student_renderer.geometry(w,*resolution)
                check(QRectF(0,0,*resolution).contains(panel),f"inventory bounded {resolution} x{multiplier}")
        w.registry.get("MinecraftCombatHUD").get_setting("Scale Multiplier").value=1
        for dpr in (1,1.25,1.5,2):
            draw(w,dpr=dpr)
            check(all(QRectF(0,0,1280,720).contains(h.rect) for h in w.hotspots),f"DPR {dpr} hotspots bounded")
        w.open_student_panel("skills");w.student_ui.progress=1;draw(w)
        w.handle_student_action("student_skill",("Chassis Points",1))
        check(w.registry.get("SkillConfiguration").get_setting("Chassis Points").value==1,"skills local draft persisted")
        check(not w.state.performance_sync,"skill draft doesn't mutate official performance")
        saved=export_client_config(w.registry,gui_mode="JelloSigma",selected_category="Render",
                                   selected_module="HUD",module_expanded=set(),old_panels={},new_panels={},dropdown_panels={})
        migrated=build_default_registry();apply_registry_config(migrated,saved)
        check(migrated.get("SkillConfiguration").get_setting("Chassis Points").value==1,"new settings survive config migration")
        for panel_name in ("damage","settlement","darts","skills"):
            w.open_student_panel(panel_name);w.student_ui.progress=1;draw(w)
            check(all(h.action.startswith("student_") for h in w.hotspots),f"{panel_name} no underlying controls")
        w.open_student_panel("damage");w.student_ui.progress=1;draw(w)
        check(not w.hotspots,"Tab-style held damage list has no Jello card buttons")
        tab_key=w.gpu_static_ui_key(1280,720,1)
        w.student_ui.panel="inventory"
        check(w.gpu_static_ui_key(1280,720,1)!=tab_key,"panel transitions refresh HUD snapshot rather than retaining hidden glass lenses")
        w.student_ui.panel="damage"
        for width,height in ((1280,720),(1600,900),(1920,1080),(2560,1440)):
            rect,scale=w.student_renderer.geometry(w,width,height)
            check(QRectF(0,0,width,height).contains(rect) and abs(rect.width()/rect.height()-280/170)<.001,f"Tab geometry bounded {width}x{height}")
        w.handle_student_action("student_draft","dart_fire")
        check("未下发" in w.student_ui.note,"dart fire remains explicitly inert")
        w.open_student_panel("settlement");snapshot=w.student_ui.settlement;w.state.base_health=1
        check(w.student_ui.settlement==snapshot,"settlement is a frozen snapshot")
        w.student_ui.previous_stage="SETTLEMENT";w.state.current_stage="MATCH";w.update_student_frontend(0)
        check(w.student_renderer.content_rows(w)==snapshot,"stage changes do not unfreeze a displayed settlement")
        w.open_student_panel("damage");w.student_ui.progress=1;w.update_student_frontend(0)
        w.state.injury_stats={101:{"total_damage":100,"small_projectile_damage":0,"large_projectile_damage":0}}
        w.update_student_frontend(0)
        w.state.injury_stats[102]={"total_damage":130,"small_projectile_damage":0,"large_projectile_damage":0};w.update_student_frontend(0)
        w.state.injury_stats[101]={"total_damage":150,"small_projectile_damage":0,"large_projectile_damage":0};w.update_student_frontend(0)
        check(damage_rows(w.state)[0][1]=="150","returning killer doesn't show stale damage")
        w.close_student_panel(immediate=True)
        w.handle_student_key(key(Qt.Key.Key_QuoteLeft),True);w.student_ui.progress=1
        w.handle_student_key(key(Qt.Key.Key_QuoteLeft,False),False);w.update_student_frontend(.125)
        check(not w.student_ui.visible and w.overlay_coordinator.modal is None,"hold scoreboard releases modal")
        w.handle_student_key(key(Qt.Key.Key_QuoteLeft),True)
        repeated=QKeyEvent(QEvent.Type.KeyRelease,Qt.Key.Key_QuoteLeft,Qt.KeyboardModifier.NoModifier,"",True)
        w.handle_student_key(repeated,False)
        check(w.student_ui.opened,"autorepeat release doesn't collapse held scoreboard")
        w.close_student_panel(immediate=True)
        profile=w.state.robot_profile
        w.state.robot_profile="Dart"
        check(w.handle_student_key(key(Qt.Key.Key_F),True) and w.student_ui.panel=="darts","F opens darts only for the Dart profile")
        w.close_student_panel(immediate=True);w.state.robot_profile=profile
        check(not w.handle_student_key(key(Qt.Key.Key_F),True),"normal combat F is not captured by Dart UI")
        w.state.buffs=[{"robot_id":w.state.robot_id,"buff_type":3,"buff_left_time":20}]
        image=QImage(1280,720,QImage.Format_ARGB32_Premultiplied);image.fill("#202832")
        painter=QPainter(image)
        w.draw_student_status(painter,1280,720);rebuilds=w.student_renderer.status_rebuilds
        for _ in range(100):w.draw_student_status(painter,1280,720)
        painter.end()
        check(w.student_renderer.status_rebuilds==rebuilds,"buff/device text is cached, not rasterized every frame")
        w.economy_category="ammo";w.student_reload_expanded=True
        commands=ClickGuiDrawList();append_reload_details(w,commands,QRectF(20,20,300,290),QRectF(20,20,300,290),1)
        check(any(h.action=="student_gate" for h in w.hotspots) and reload_detail_height(w)==290,"reload details use existing economy viewport")
        check(any("等待官方" in t.text for t in commands.texts),"supply-zone data is unknown, not fake true")
        w.state.match_mode="RMUC7v7";w.state.robot_profile="Infantry1"
        w.state.current_health=0;offers=w.economy_offers("revive")
        check(any("免费复活" in o.title for o in offers) and any("买活" in o.title for o in offers),"revive labels use original offers")
        w.state.economy_mock_mode=False;w.state.respawn_gold_cost=0
        w.handle_student_key(key(Qt.Key.Key_F1),True)
        check(w.economy_selected_key=="rmuc_gold_respawn","F1 selects paid revive even with unknown price")
        generation=w._economy_generation;w.close_economy_workspace(play_sound=False)
        w._economy_restore_pending_generation=generation;w._finalize_economy_restore(generation)
        w.open_economy_workspace("ammo");w.open_student_panel("darts")
        check(w._student_pending_panel=="darts" and w.overlay_coordinator.modal==OverlayKind.ECONOMY,"economy keeps ownership until GL restore")
        generation=w._economy_generation;w._economy_restore_pending_generation=generation
        w._finalize_economy_restore(generation);app.processEvents();w.student_ui.progress=1
        check(w.student_ui.panel=="darts" and w.economy_phase==EconomyScenePhase.CLOSED,"economy deferred restore opens student modal")
        w.close_student_panel(immediate=True);w.open_student_panel("inventory");w.student_ui.progress=1;draw(w)
        rebuilds=w.student_renderer.rebuilds
        for i in range(100):
            w.close_student_panel();w.update_student_frontend(.125)
            w.open_student_panel("inventory");w.update_student_frontend(.45);draw(w)
        check(w.student_renderer.rebuilds==rebuilds,"100 opens do not rebuild static panel/font textures")
        check(len(w.hotspots)==len({(h.action,h.payload,h.button,h.rect.x(),h.rect.y()) for h in w.hotspots}),"100 opens don't leak duplicate hotspots")
        print(f"\n{COUNT} checks PASS",flush=True)
    finally:
        close_window_safely(w,app)
    return 0

if __name__=="__main__":raise SystemExit(main())
