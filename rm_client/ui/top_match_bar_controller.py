"""Small read-only integration boundary for the optional TopMatchBar module.

No input ownership, new timers, transport calls, RobotState mutation or commands.
The backend invokes observe_referee on its existing decoded-message/UI thread.
"""
from __future__ import annotations
from dataclasses import replace
import logging
from typing import Any, Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont

from ..robot_profiles import profiles_for_match
from ..top_match_bar import LifeState, TopMatchBarFeed
from .top_match_bar import TopMatchBarOptions, TopMatchBarRenderer
from .overlay_coordinator import OverlayCoordinator
from .jello_locale import chinese_enabled
from .match_strip_locale import match_role_text, match_strip_text

_LOG = logging.getLogger(__name__)


class TopMatchBarController:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.renderer = TopMatchBarRenderer()
        self.feed: TopMatchBarFeed | None = None
        self._context: tuple[object, ...] | None = None
        self._custom_rosters: tuple[tuple[int, ...], tuple[int, ...], str] | None = None
        self._last_error = ""
        self.last_layout = None
        self.last_progress_bounds = None
        self._reset_progress = True

    def enabled(self) -> bool:
        module = self.owner.registry.get("TopMatchBar")
        if not module or not module.enabled:
            return False
        scope = module.get_setting("Match Scope")
        return (str(scope.value) == "All Match Modes" if scope is not None else False) or self.owner.state.match_mode == "RMUC7v7"

    def configure_rosters(self, red: Sequence[int], blue: Sequence[int], *, label: str = "ROSTER") -> None:
        """Optional host API for an explicit current-match participant roster."""
        TopMatchBarFeed(red, blue)  # Validate before replacing any current config.
        self._custom_rosters = (tuple(red), tuple(blue), str(label)[:14])
        self.reset()

    def hide(self) -> None:
        """Clear this frame's presentation without discarding fresh telemetry."""
        self.last_layout = None
        self.last_progress_bounds = None
        self.renderer.reset_motion()

    def reset(self) -> None:
        self.feed = None
        self._context = None
        self.renderer.reset_motion()
        self.last_layout = None
        self.last_progress_bounds = None
        self._reset_progress = True

    def _setting(self, name: str, default: object) -> object:
        module = self.owner.registry.get("TopMatchBar")
        setting = module.get_setting(name) if module else None
        return setting.value if setting is not None else default

    def _ensure_feed(self) -> TopMatchBarFeed:
        owner, state = self.owner, self.owner.state
        mqtt = owner.backend.mqtt
        mode = str(state.match_mode)
        scope = str(self._setting("Roster Scope", "Match Roles"))
        label = "RM SCORE" if self._setting("Score Metric", "Referee Score") == "Referee Score" else ""
        source = (str(owner.backend.scenario_key), str(owner.backend.referee.profile_id),
                  str(mqtt.host), int(mqtt.port), int(mqtt.receive_generation),
                  float(mqtt.disconnected_at), bool(mqtt.connected))
        context = (mode, str(state.alliance_color), int(state.robot_id), source, scope, label, self._custom_rosters)
        if self.feed is None or context != self._context:
            profiles = profiles_for_match(mode)
            # Profiles are configuration candidates, not proof of life. Ground
            # scope selects only role profiles with a modeled HP capacity.
            ground = {"Hero", "Engineer", "Infantry1", "Infantry2", "Sentinel"}
            red = tuple(p.robot_id for p in profiles if scope != "Ground Robots" or p.key in ground)
            blue = tuple(robot_id + 100 for robot_id in red)
            if self._custom_rosters is not None:
                red, blue, _ = self._custom_rosters
            self.feed = TopMatchBarFeed(red, blue, left_alliance=str(state.alliance_color), score_label=label)
            self._context = context
            self.renderer.reset_motion()
            self._reset_progress = True
        return self.feed

    def observe_referee(self, name: str, data: dict[str, object], received_at: float) -> None:
        if not self.enabled():
            self.reset()
            return
        feed = self._ensure_feed()
        owner = self.owner
        # This projection is specifically the audited RM2026 v1.3.1 contract.
        # A preview/other-year codec must not silently reuse its field meanings.
        if owner.backend.referee.profile_id != "rm2026-v1.3.1" or not owner.backend.mqtt.connected:
            feed.reset()
            return
        expected_alliance = "Blue" if int(owner.state.robot_id) >= 100 else "Red"
        if str(owner.state.alliance_color) != expected_alliance:
            feed.reset()  # Ambiguous identity must not swap own/enemy HP.
            return
        feed.consume_referee(name, data, received_at=received_at,
                             own_alliance=str(owner.state.alliance_color),
                             local_robot_id=int(owner.state.robot_id))

    def visible(self) -> bool:
        owner = self.owner
        if not self.enabled() or bool(getattr(owner, "home_visible", True)):
            return False
        hud = owner.registry.get("HUD")
        if hud is not None and not hud.enabled:
            return False
        if owner.menu_overlay_active():
            return False
        hero = getattr(owner, "hero_deploy_ui", None)
        if hero is not None and bool(getattr(hero, "active", False)):
            return False
        coordinator = getattr(owner, "overlay_coordinator", None)
        if coordinator is not None and getattr(coordinator, "modal", None) is not None:
            return False
        return True

    def owns_progress(self) -> bool:
        """The integrated strip owns BF6 progress even when temporarily hidden.

        This prevents the legacy path drawing over a menu, expanded map or HUD
        row. Disabling TopMatchBar restores the original EliminationFX path.
        """
        return self.enabled()

    def _paint_progress(self, painter: Any, frame: Any, width: int, now: float,
                        left: float, right: float, bottom: float, maximum_bottom: float) -> None:
        if not bool(self._setting("BF6 Progress", True)) or self.owner.state.match_mode != "RMUC7v7":
            return  # RMUL victory points are a different metric.
        renderer = getattr(getattr(self.owner, "kill_fx", None), "match_progress", None)
        if renderer is None:
            return
        if self._reset_progress:
            renderer.shown_own = renderer.shown_enemy = 0.0
            renderer.shown_y = None
            renderer.own_score_scroll.reset()
            renderer.enemy_score_scroll.reset()
            self._reset_progress = False
        scores = ({frame.left.alliance: frame.left.score, frame.right.alliance: frame.right.score}
                  if bool(self._setting("Show Scores", True)) else {})
        available = max(1, int(width - left - right))
        progress_scale = .90
        y = bottom + 12.0 * progress_scale
        painter.save()
        try:
            painter.translate(left, 0)
            # A resize/MC toggle may introduce a lower obstacle while progress
            # still eases from its previous position. Clamp only that unsafe
            # tail; ordinary island-collapse easing remains unchanged.
            if renderer.shown_y is not None:
                renderer.shown_y = min(renderer.shown_y, maximum_bottom - 30.0)
            previous_y = renderer.shown_y if renderer.shown_y is not None else y
            # Keep the existing downward position visible while it eases up
            # after an island collapse; a fixed 64px clip would make it blink.
            clip_height = max(64.0, float(previous_y) - y + 40.0)
            painter.setClipRect(QRectF(0, y, available, clip_height), Qt.IntersectClip)
            # RM2026 rules: base starts at 5000. This is a rule capacity,
            # not RobotState's default current HP. No observation means None.
            renderer.draw(painter, available, now, progress_scale,
                          own_base_health=frame.own_base_health, own_base_max=5000,
                          enemy_base_health=frame.enemy_base_health, enemy_base_max=5000,
                          red_score=scores.get("Red"), blue_score=scores.get("Blue"),
                          own_blue=self.owner.state.alliance_color == "Blue",
                          anchor_bottom=bottom, minimum_y=y)
        finally:
            painter.restore()
        self.last_progress_bounds = QRectF(left, renderer.shown_y, available, 30)

    @staticmethod
    def _paint_systems(painter: Any, frame: Any, layout: Any, *, chinese: bool = False) -> float:
        if not frame.left.systems and not frame.right.systems:
            return layout.bounds.bottom
        y = layout.bounds.bottom + 2.0
        painter.save()
        try:
            font = QFont("DejaVu Sans")
            font.setPixelSize(9)
            painter.setFont(font)
            for team, x, w, align in (
                    (frame.left, layout.bounds.x, layout.center.x - layout.bounds.x - 8, Qt.AlignRight),
                    (frame.right, layout.center.right + 8, layout.bounds.right - layout.center.right - 8, Qt.AlignLeft)):
                painter.setPen(QColor("#B5D4EE") if team.alliance == "Blue" else QColor("#EAD18A"))
                text = "  ·  ".join(f"{match_role_text(card, chinese)} {card.robot_id}: " +
                                    match_strip_text({LifeState.ALIVE: "ALIVE", LifeState.DEAD: "DOWN", LifeState.UNKNOWN: "—"}[card.life], chinese)
                                    for card in team.systems)
                painter.drawText(QRectF(x, y, max(0, w), 16), align | Qt.AlignVCenter, text)
        finally:
            painter.restore()
        return y + 16.0

    def paint(self, painter: Any, width: int, height: int, now: float, *,
              minecraft_layout: Any = None, hud_scale: float = 1.0,
              include_map: bool = True) -> None:
        self.last_layout = None
        self.last_progress_bounds = None
        try:
            if not self.enabled():
                self.reset()
                return
            if not self.visible():
                self.hide()
                return
            simulation_provider = getattr(self.owner.backend, "simulation_match_bar", None)
            if callable(simulation_provider):
                frame = simulation_provider(now)
            else:
                feed = self._ensure_feed()
                if not self.owner.backend.mqtt.connected:
                    feed.reset()
                frame = feed.snapshot(now, local_robot_id=int(self.owner.state.robot_id))
            # Counts are ground-robot lives, never the nominal mode/team size.
            scope_label = (self._custom_rosters[2] if self._custom_rosters is not None else "GROUND ALIVE")
            frame = replace(frame, roster_label=scope_label)
            owner = self.owner
            hud = owner.registry.get("HUD")
            top = 5.0
            dynamic_island = owner.setting_bool(hud, "Dynamic Island", True)
            if dynamic_island:
                island = owner.dynamic_island_state
                top = max(top, float(island.y) + float(island.height) + 8.0, 62.0)
            left = right = 12.0
            tactical = owner.registry.get("TacticalMap")
            map_visible = include_map and tactical is not None and tactical.enabled
            if owner.setting_bool(hud, "Watermark", True) and top < 58:
                left = max(left, min(290.0, width * .28))
            right_mode = owner.setting_mode(hud, "Right Display", "ModuleListHud")
            if owner.setting_bool(hud, "Module List", True):
                right = max(right, 270.0)
                if (minecraft_layout is None and right_mode in ("ModuleListHud", "TacticalMapHud") and
                        owner.setting_mode(hud, "ModuleList Style", "PolarTextList") == "PolarTextList"):
                    # Bare Polar text uses x=18, width=260, independently of
                    # the right-side panels. Keep a 10px gap even without map.
                    left = max(left, 288.0)
            # Other right HUD modes are independent of the Module List toggle.
            # Values are their actual fixed left insets in the existing HUD
            # renderers plus a 10px gap, never a percentage capped on small UI.
            panel_insets = {"KeyBindsHud": ("KeyBinds", 236.0),
                            "RobotListHud": ("Robot List", 264.0),
                            "TargetHud": ("Target HUD", 258.0),
                            "StatusCardsHud": ("Status Cards", 204.0),
                            "RMIndicatorsHud": ("RM Indicators", 254.0)}
            panel = panel_insets.get(right_mode)
            if minecraft_layout is None and panel and owner.setting_bool(hud, panel[0], True):
                right = max(right, panel[1])
            if owner.setting_bool(hud, "KeyBinds", True):
                right = max(right, 164.0)  # Compact strip: 136px + 18 margin + gap.
            # Symmetric corridor keeps the CS clock and BF6 midpoint on the
            # viewport center while leaving both existing side HUDs untouched.
            safe = OverlayCoordinator.top_hud_safe_area(
                width, height, top=top, inset=max(left, right),
                minecraft_layout=minecraft_layout, hud_scale=hud_scale,
                dynamic_island=dynamic_island,
                # MC effect visibility follows the module, not include_map.
                tactical_map=tactical is not None and tactical.enabled)
            left = right = safe.inset
            top = safe.top
            if map_visible:
                rect = owner.tactical_map_rect(width, height)
                # Side overlays can intersect any part of the final reserved band.
                # Test the final reserved band, not the old 180px estimate.
                if rect.bottom() > top and rect.top() < safe.bottom:
                    left = right = max(left, float(rect.right()) + 10.0)
            if left + right + 108 >= width:
                return
            requested_scale = max(.35, min(1.4, float(self._setting("Scale", .72))))
            systems_height = 18.0 if frame.left.systems or frame.right.systems else 0.0
            progress_height = (40.8 if bool(self._setting("BF6 Progress", True)) and
                               owner.state.match_mode == "RMUC7v7" else 0.0)
            # Use actual fitted row height, not a fixed 150px estimate. This
            # keeps the complete row and progress inside the available space.
            available_scale = (safe.bottom - top - systems_height - progress_height) / 120.0
            if available_scale < .35:
                return
            options = TopMatchBarOptions(
                enabled=True, scale=min(requested_scale, available_scale),
                top=top, safe_left=left, safe_right=right,
                reduced_motion=not bool(self._setting("Source Motion Preview", False)),
                show_kills=bool(self._setting("Show Kills", True)),
                show_scores=bool(self._setting("Show Scores", True)),
                show_health=bool(self._setting("Show Health", True)),
                chinese=chinese_enabled(owner.registry),
                custom_roster_label=self._custom_rosters is not None)
            self.last_layout = self.renderer.paint(painter, frame, width, now=now, options=options)
            if self.last_layout is not None:
                bottom = self._paint_systems(painter, frame, self.last_layout, chinese=options.chinese)
                self._paint_progress(painter, frame, width, now, left, right, bottom, safe.bottom)
            self._last_error = ""
        except Exception as exc:
            # A presentation fault cannot abort the shared HUD/control frame.
            self.last_layout = None
            self.last_progress_bounds = None
            try:
                error = f"{type(exc).__name__}: {exc}"
            except Exception:
                error = type(exc).__name__
            if error != self._last_error:
                self._last_error = error
                try:
                    _LOG.warning("TopMatchBar hidden after presentation error: %s", error)
                except Exception:
                    pass
            self.renderer.reset_motion()
