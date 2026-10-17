"""Complete CS2-style RM shop matrix over the existing pull-down glass scene.

Five visible columns and a large item/detail preview. Product eligibility,
confirmation, transport, deadlines and scene restoration remain host-owned.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from PySide6.QtCore import QRectF, Qt

from ..economy_values import EconomyContext, CATEGORY_TITLES, TRANSACTION_LABELS
from ..student_ui import presentation_data, value_text
from .animation import Easings
from .clickgui_gpu import ClickGuiDrawList
from .cs2_feedback import _css_ease_in_out
from .gpu_frame_compositor import LensMaterial
from .theme import color
from .economy_workspace import _lerp_rect, _layer_transform_rect
from .economy_interaction import HOLD_SECONDS
from .student_reload import append_reload_details, reload_detail_height
from .fonts import cjk_ui_font


COLUMNS = ("ammo", "recovery", "revive", "support", "supplier")
TITLES = {**CATEGORY_TITLES, "supplier": "补给站"}


def grid_enabled(host) -> bool:
    return host.setting_mode(host.registry.get("EconomyPanel"), "Shop Layout", "CS2 Grid") == "CS2 Grid"


def grid_layout(layer, width, height, progress):
    base = layer.layout(width, height, progress)
    w, h = max(1, width), max(1, height)
    margin = 20 if w >= 1280 else 10
    target_w, target_h = min(1740, w-2*margin), min(880, h-32)
    target = QRectF((w-target_w)/2, 12, target_w, target_h)
    island = _lerp_rect(base.collapsed, QRectF((w-560)/2, 12, min(560, target_w), 84), base.island_progress)
    panel = _lerp_rect(island, target, base.body_progress)
    raw = max(-.02, min(1.12, progress))
    deform = 1 + .10*max(0, raw-1)
    def present(r):
        return _layer_transform_rect(r, panel.center().x(), panel.center().y(), 1/deform, deform, -48*(1-min(1,raw)))
    header = QRectF(panel.left()+20, panel.top()+16, max(1,panel.width()-40), 52)
    inner = target.adjusted(20, 90, -20, -66)
    detail_w = max(220, min(350, inner.width()*.23))
    detail = QRectF(inner.right()-detail_w, inner.top(), detail_w, inner.height())
    grid = QRectF(inner.left(), inner.top(), max(1,detail.left()-inner.left()-18), inner.height())
    button = QRectF(detail.left(), target.bottom()-54, detail.width(), 36)
    clip = present(panel).adjusted(4, 74, -4, -4)
    return replace(base, target_panel=target, panel=present(panel),
                   header=present(header), close_button=present(QRectF(header.right()-34,header.top()+4,30,30)),
                   grab_handle=present(QRectF(panel.center().x()-18,panel.top()+7,36,3)),
                   category_pane=QRectF(), offers_pane=present(grid), detail_pane=present(detail),
                   offers_viewport=present(grid), detail_viewport=present(detail),
                   action_button=present(button), body_clip=clip, tab_bar=QRectF(),
                   root_radius=min(panel.height()/2, 23+7*base.body_progress))


def icon_path(host, category):
    native = host.kill_fx.cs2.assets.paths.get("buy_"+category)
    return native or host.paths.asset("ui", "cs2_buy", category+".svg")


def fit_icon(host, rect, category):
    image = host.kill_fx.cs2.assets.image("buy_"+category)
    aspect = image.width()/image.height() if not image.isNull() else 2.0
    w = min(rect.width(),rect.height()*aspect)
    h = w/aspect
    return QRectF(rect.center().x()-w/2,rect.center().y()-h/2,w,h)


def build_grid(host, width, height):
    commands = ClickGuiDrawList()
    host.hotspots.clear()
    progress = host.economy_island.progress
    if progress < .02:
        return commands
    layout = grid_layout(host.economy_workspace_layer, width, height, progress)
    context = EconomyContext.from_state(host.state)
    offers = host.economy_offers()
    selected = host.reconcile_economy_selection()
    now = time.perf_counter()
    body_alpha, detail_alpha = layout.offers_alpha, layout.detail_alpha
    # All content and hit areas use the SAME final-grid -> animated-root clip.
    root_clip = layout.panel.adjusted(4,4,-4,-4)
    body_clip = layout.body_clip.intersected(root_clip)
    scale = max(.78, min(1.10, layout.target_panel.width()/1500))
    def label(rect, text, *, size=10.5, tint="#ffffff", alpha=body_alpha, align=Qt.AlignLeft|Qt.AlignVCenter, bold=False, clip=body_clip, dynamic=False):
        font = host.font("poppins_bold" if bold else "poppins_medium", size*scale)
        if dynamic:
            # Rolling numeric telemetry must reuse resident glyphs; baking
            # every new 17mm/42mm/gold value into a shaped texture caused 7ms
            # uploads. Explicit Regular CJK avoids the old implicit fallback.
            font = cjk_ui_font(font)
        commands.text(rect,text,font,color(tint,alpha),align,clip=clip,
                      text_profile="" if dynamic else "jello",preserve_weight=True)
    def lens(rect, alpha, material=LensMaterial.REGULAR, radius=12, clip=body_clip):
        if rect.intersected(clip).isEmpty() or alpha <= .01:
            return
        lens_scale = min(1, max(.20, min(rect.width(),rect.height())/68))
        host.gpu_compositor.add_glass(rect, radius, strength=1, surface="clear", opacity=alpha,
            material=material, refraction_height_px=max(1,24*lens_scale*alpha),
            refraction_amount_px=-max(1,48*lens_scale*alpha), chromatic_aberration=0,
            depth_effect=1, frost_amount=0, vibrancy_amount=alpha, clip_rect=clip)
        commands.rect(rect,radius,color("#000000",.05*alpha),clip=clip)
    def hot(rect, action, payload=None, tooltip=""):
        visible = rect.intersected(body_clip)
        if host.economy_visible and layout.interactive and not visible.isEmpty():
            host.add_hotspot(visible,action,payload,tooltip=tooltip)
    # The root uses the existing independent Control-Center lenses. No dark
    # CS2 full-screen rectangle and no new blur/refraction shader.
    lens(layout.header.adjusted(-6,-6,6,6),layout.header_alpha,LensMaterial.STRONG,24,root_clip)
    label(layout.header.adjusted(12,0,-120,-20),"战斗购买",size=20,bold=True,alpha=layout.header_alpha,clip=root_clip)
    pending = host.economy_bridge.latest is not None and host.economy_bridge.latest.status == "pending"
    spent = sum(t.request.cost for t in host.economy_bridge.history if t.status == "succeeded")
    info = f"可用 {context.gold} 金币  ·  本次已确认 {spent}  ·  " + ("模拟请求" if context.mock_mode else "裁判系统")
    label(layout.header.adjusted(12,28,-50,0),info,size=9.3,tint="#b8cadb",alpha=layout.header_alpha,clip=root_clip,dynamic=True)
    close = layout.close_button
    commands.rect(close,close.height()/2,color("#ffffff",.12*layout.header_alpha),clip=root_clip)
    label(close,"×",size=16,alpha=layout.header_alpha,align=Qt.AlignCenter,clip=root_clip)
    if host.economy_visible and layout.interactive:
        host.add_hotspot(close,"economy_close")
    # No fabricated CS buy-phase countdown or next-round money: RM's rules
    # do not provide these fields. Gold/pending/local state replace that bar.
    grid = layout.offers_pane
    gap, heading_h = 12*scale, 34*scale
    column_w = (grid.width()-4*gap)/5
    max_rows = max(5, max((sum(o.category==key for o in offers) for key in COLUMNS),default=0))
    row_h = max(68*scale, (grid.height()-heading_h-4*gap)/5)
    overflow = max(0,max_rows*(row_h+gap)-gap+heading_h-grid.height())
    host.economy_offers_scroll.set_bounds(overflow)
    scroll = host.economy_offers_scroll.position
    for column,key in enumerate(COLUMNS):
        x = grid.left()+column*(column_w+gap)
        heading = QRectF(x,grid.top(),column_w,heading_h)
        label(heading,f"{column+1}  {TITLES[key]}",size=14,bold=True,align=Qt.AlignCenter)
        items = [o for o in offers if o.category==key]
        if key == "supplier":
            data = presentation_data(host.state).get("reloading",{}) or {}
            zone = data.get("in_supplier_zone")
            statuses = (("发弹余量", f"17mm {value_text(host.state.projectile_allowance_17mm)} · 42mm {value_text(host.state.projectile_allowance_42mm)}"),
                        ("弹药库储备",value_text(data.get("virtual_reserve"))),
                        ("实体弹丸",value_text(data.get("physical_reserve"))),
                        ("补给位置","已在补给区" if zone is True else "未在补给区" if zone is False else "等待官方状态"))
            for index,(title,value) in enumerate(statuses):
                rect = QRectF(x,grid.top()+heading_h+index*(row_h+gap)-scroll,column_w,row_h)
                lens(rect,body_alpha)
                label(rect.adjusted(10,4,-10,-rect.height()+28*scale),title,size=9,tint="#b3c8d7")
                label(rect.adjusted(10,30*scale,-10,-8),str(value),size=9.5,bold=True,align=Qt.AlignCenter,dynamic=True)
            continue
        for index,offer in enumerate(items):
            rect = QRectF(x,grid.top()+heading_h+index*(row_h+gap)-scroll,column_w,row_h)
            if not rect.intersects(body_clip):
                continue
            lens(rect,body_alpha)
            active = selected is not None and selected.key==offer.key
            hovered = rect.contains(host.hover_point)
            hover = host.anim.value("economy.grid.hover."+offer.key, float(hovered),host.anim_duration(.2),_css_ease_in_out)
            flash = host.economy_workspace_layer.cs2_feedback.intensity(offer.key,now)
            rim = (.65 if active else .07)+.14*hover+.2*flash
            commands.rect(rect,12,color("#000000",0),color("#ffffff",min(1,rim)*body_alpha),1 if not active else 1.5,clip=body_clip)
            label(rect.adjusted(9,3,-9,-rect.height()+25*scale),f"{index+1}  {offer.short_title}",size=9.1,
                  tint="#b5d4ee" if offer.enabled else "#8093a1")
            icon = QRectF(rect.left()+18,rect.top()+28*scale,max(12,rect.width()-36),max(12,rect.height()-54*scale))
            commands.image(fit_icon(host,icon,key),icon_path(host,key),color("#b5d4ee" if offer.enabled else "#617281",body_alpha),clip=body_clip)
            label(QRectF(rect.left()+8,rect.bottom()-24*scale,rect.width()-16,20*scale),
                  "仅状态" if offer.status_only else f"{offer.cost} 金币",size=9.2,
                  tint="#b5d4ee" if offer.enabled else "#7b8b95",align=Qt.AlignRight|Qt.AlignVCenter)
            hot(rect,"economy_offer",offer.key,offer.reason or offer.description)
        if not items:
            label(QRectF(x,grid.top()+heading_h,column_w,row_h),"本赛制无此商品",size=9,tint="#879aa8",align=Qt.AlignCenter)
    detail = layout.detail_pane
    lens(detail,detail_alpha,LensMaterial.STRONG,18)
    detail_clip = detail.intersected(body_clip)
    content_height = min(220*scale,detail.height()*.35)+216+reload_detail_height(host)+80
    host.economy_detail_scroll.set_bounds(max(0,content_height-detail.height()))
    detail_content = detail.translated(0,-host.economy_detail_scroll.position)
    if selected is not None:
        preview_h = min(220*scale,detail.height()*.35)
        preview = QRectF(detail_content.left()+24,detail_content.top()+24,detail.width()-48,max(20,preview_h-40))
        commands.image(fit_icon(host,preview,selected.category),icon_path(host,selected.category),color("#b5d4ee",detail_alpha),clip=detail_clip)
        y = detail_content.top()+preview_h
        label(QRectF(detail.left()+16,y,detail.width()-32,36),selected.title,size=13.5,bold=True,alpha=detail_alpha,clip=detail_clip)
        y += 40
        for text,tint in ((f"{selected.cost} 金币 · {selected.unit}","#ffd56a"),
                          (selected.description,"#bed0e1"),
                          (selected.reason or "可以购买","#70e0ae" if selected.enabled else "#ffc080"),
                          ("按住确认" if selected.confirmation=="hold" else "二次确认后发送","#9caebb")):
            label(QRectF(detail.left()+16,y,detail.width()-32,28),text,size=9.5,tint=tint,alpha=detail_alpha,clip=detail_clip)
            y += 32
        if selected.category == "ammo":
            append_reload_details(host,commands,QRectF(detail.left()+16,y+8,detail.width()-32,290),detail_clip,
                                  detail_alpha,scale,layout.interactive and host.economy_visible)
            y += reload_detail_height(host)
        latest = host.economy_bridge.latest
        if latest is not None:
            label(QRectF(detail.left()+16,y+8,detail.width()-32,24),TRANSACTION_LABELS.get(latest.status,latest.status),
                  size=10,tint="#b5d4ee",alpha=detail_alpha,bold=True,clip=detail_clip)
            label(QRectF(detail.left()+16,y+34,detail.width()-32,28),latest.message,size=8.6,alpha=detail_alpha,clip=detail_clip)
    button = layout.action_button
    enabled = selected is not None and selected.enabled and not selected.status_only and not pending
    action, hold = "economy_confirm", 0.0
    if pending:
        text = "请求处理中…"
    elif selected is None:
        text = "请先选择商品"
    elif not enabled:
        text = selected.reason or "当前不可用"
    elif selected.confirmation=="hold":
        action = "economy_confirm_hold"
        hold = max(0,min(1,(now-host.economy_hold_started)/HOLD_SECONDS)) if host.economy_hold_key==selected.key else 0
        text = f"确认中 {round(hold*100)}%" if hold else "按住 0.6 秒确认"
    else:
        text = (f"再点一次确认（{max(0,host.economy_armed_until-now):.1f}s）" if host.economy_armed_key==selected.key and now<=host.economy_armed_until
                else f"确认购买 · {selected.cost} 金币")
    lens(button,detail_alpha,LensMaterial.STRONG,18)
    commands.rect(button,18,color("#0088ff" if enabled else "#ffffff",(.72 if enabled else .10)*detail_alpha),clip=body_clip)
    label(button,text,size=10.5,bold=True,alpha=detail_alpha,align=Qt.AlignCenter,dynamic=True)
    if hold>0:
        commands.rect(QRectF(button.left()+12,button.bottom()-5,(button.width()-24)*hold,2),1,color("#ffd56a",detail_alpha),clip=body_clip)
    if enabled:
        hot(button,action,selected.key,text)
    footer = QRectF(layout.target_panel.left()+24,layout.target_panel.bottom()-50,max(1,grid.width()),30)
    label(footer,"Esc / B 返回  ·  选择商品后在右侧确认  ·  所有交易沿用裁判链路",size=9,tint="#adc1d2",alpha=detail_alpha)
    return commands
