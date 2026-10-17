"""Inert, ordered effect recorder for the keyboard entry-point differential tests.

Only keyPressEvent/keyReleaseEvent vary between the frozen and current versions.
Other production helpers retain their own unit/integration coverage. No widget,
transport, filesystem write or physical robot is exercised by this harness.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import runpy
from types import SimpleNamespace as NS
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, Qt

from rm_client.ui.polar.mixins import input_dispatch_mixin as current
from rm_client.ui.polar.pointer_interaction import PointerInteraction

BASELINE_PATH = Path(__file__).parent / "fixtures/keyboard_routing_baseline.py"
BASELINE = runpy.run_path(str(BASELINE_PATH))["KeyboardRoutingBaseline"]
_CURSOR = NS(pos=lambda: QPoint(12, 34))
for _method in (BASELINE.keyPressEvent, BASELINE.keyReleaseEvent):
    _method.__globals__["QCursor"] = _CURSOR


@dataclass(frozen=True)
class Key:
    code: int
    text: str = ""
    repeat: bool = False
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
    phase: str = "press"

    @property
    def name(self):
        return Qt.Key(self.code).name.removeprefix("Key_")


class Event:
    def __init__(self, key: Key, host):
        self.value = key
        self.host = host
        self.accepted = False

    def key(self): return self.value.code
    def text(self): return self.value.text
    def isAutoRepeat(self): return self.value.repeat
    def modifiers(self): return self.value.modifiers
    def accept(self):
        self.host.record("event.accept")
        self.accepted = True


@dataclass
class Case:
    lifecycle_pending: bool = False
    configuration_pending: bool = False
    closing: bool = False
    escape: bool = False
    binding: bool = False
    home: bool = False
    wallpaper: bool = False
    startup_handles: bool = False
    hero: bool = False
    deploy_mode: int = 0
    student: bool = False
    chat: bool = False
    wheel: bool = False
    radial: bool = False
    economy: bool = False
    search: bool = False
    gui: bool = False
    settings: bool = False
    legacy: bool = False
    blocks: bool = False
    minecraft: bool = False
    slot_handles: bool = False
    right_shift: bool = False
    categories: tuple[str, ...] = ("ammo", "support", "revive")
    offers: tuple[str, ...] = ("small", "medium", "large")
    category: str = "support"
    selected: str = "medium"
    confirmation: str = "hold"
    selected_available: bool = True
    chat_send_succeeds: bool = True
    bindings: dict[str, str] = field(default_factory=dict)
    disabled: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    pending_releases: tuple[int, ...] = ()


class InjectedFailure(RuntimeError):
    pass


def value(item):
    if isinstance(item, Event):
        return (item.value.name, item.value.repeat, item.value.phase)
    if isinstance(item, (QPoint, QPointF)):
        return (item.x(), item.y())
    if isinstance(item, NS) and hasattr(item, "key"):
        return item.key
    if isinstance(item, NS) and hasattr(item, "name"):
        return item.name
    return item


class Harness(current.InputDispatchMixin):
    def __init__(self, case: Case, fail_at: int | None = None):
        self.case = deepcopy(case)
        self.trace = []
        self.fail_at = fail_at
        self._pointer_interaction = PointerInteraction()
        self.pointer_interaction.own_background_press(QPointF(2, 3))
        cancel = self.pointer_interaction.cancel_background_press
        self.pointer_interaction.cancel_background_press = lambda: (self.record("pointer.cancel"), cancel())[-1]
        self._escape_menu_key_releases = set(case.pending_releases)
        self.escape_menu_visible = case.escape
        self.home_visible = case.home
        self.wallpaper_picker_visible = case.wallpaper
        self.hero_deploy_ui = NS(visible=case.hero)
        self.student_ui = NS(visible=case.student)
        self.state = NS(deploy_mode=case.deploy_mode)
        self.configuration_change_pending = case.configuration_pending
        self._window_lifecycle = NS(closing=case.closing)
        self.gui_visible = case.gui
        self.search_focus = case.search
        self.search_query = "seed中"
        self.economy_visible = case.economy
        self.economy_category = case.category
        self.economy_selected_key = case.selected
        self.economy_selected = 8
        self.economy_offers_scroll = NS(position=17.0, target=23.0)
        self.tactical_map_hold = False
        self.held_keys = set()
        self.backend = NS(lifecycle_pending=case.lifecycle_pending,
                          release_estop=self.effect("backend.release_estop"),
                          trigger_estop=self.effect("backend.trigger_estop", result=(True, "")),
                          lock_control=self.effect("backend.lock_control"))
        self.control = NS(reset_inputs=self.effect("control.reset_inputs"),
                          clear_pending=self.effect("control.clear_pending"))
        self.frame_scheduler = NS(force=self.effect("frame.force"))
        self.quick_action_wheel = NS(active=case.wheel)
        self.overlay_coordinator = NS(radial_active=case.radial, blocks_combat_input=case.blocks)
        self.jello_state = NS(settings_module="Armor" if case.settings else None,
                              legacy_mode="Old" if case.legacy else None,
                              close_settings=self.effect("settings.close"))
        self.match_chat = NS(input_open=case.chat, input_text="draft", history_cursor=0, scroll_offset=0)
        for name in ("send_input", "backspace", "history_older", "history_newer", "scroll", "insert"):
            setattr(self.match_chat, name, self.effect("chat." + name, result=case.chat_send_succeeds if name == "send_input" else None))
        names = ("HeroLob", "MatchChat", "QuickActionWheel", "EconomyPanel", "ProjectileShop", "TacticalMap", "Armor")
        self.modules = {name: NS(name=name, keybind=case.bindings.get(name, "None"), enabled=name not in case.disabled)
                        for name in names if name not in case.missing}
        self.registry = NS(get=self.get_module, toggle_module=self.toggle_module, modules=list(self.modules.values()))
        self.selected_module = None
        if case.binding:
            self.pointer_interaction.binding_module = self.modules.get("Armor")
        self.anim = NS(value=self.effect("anim.value"))
        self.anim_duration = lambda duration: duration
        for name in ("toast", "auto_save_config", "set_wallpaper_picker_visible", "close_hero_deploy_interface",
                     "set_clickgui_visible", "open_hero_deploy_interface", "close_match_chat", "open_match_chat",
                     "cancel_quick_action_wheel", "begin_quick_action_wheel", "finish_quick_action_wheel",
                     "close_economy_workspace", "open_economy_workspace", "select_economy_offer",
                     "begin_economy_hold", "activate_economy_offer", "finish_economy_hold",
                     "open_event_command_menu", "return_to_jello_clickgui", "set_escape_menu_visible"):
            setattr(self, name, self.effect(name))

    def record(self, name, *args, **kwargs):
        self.trace.append((name, tuple(value(arg) for arg in args), tuple(sorted(kwargs.items()))))
        if self.fail_at == len(self.trace):
            raise InjectedFailure(name)

    def effect(self, name, result=None):
        def invoke(*args, **kwargs):
            self.record(name, *args, **kwargs)
            if name == "set_clickgui_visible": self.gui_visible = args[0]
            elif name == "set_escape_menu_visible": self.escape_menu_visible = args[0]
            elif name == "set_wallpaper_picker_visible": self.wallpaper_picker_visible = args[0]
            elif name == "close_hero_deploy_interface": self.hero_deploy_ui.visible = False
            elif name == "open_hero_deploy_interface": self.hero_deploy_ui.visible = True
            elif name == "close_match_chat": self.match_chat.input_open = False
            elif name == "open_match_chat": self.match_chat.input_open = True
            elif name in ("cancel_quick_action_wheel", "finish_quick_action_wheel"): self.quick_action_wheel.active = False
            elif name == "begin_quick_action_wheel": self.quick_action_wheel.active = True
            elif name == "close_economy_workspace": self.economy_visible = False
            elif name == "open_economy_workspace":
                self.economy_visible = True
                if args: self.economy_category = args[0]
            elif name == "select_economy_offer": self.economy_selected_key = args[0]
            elif name == "settings.close": self.jello_state.settings_module = None
            elif name == "return_to_jello_clickgui": self.jello_state.legacy_mode = None
            elif name == "chat.backspace": self.match_chat.input_text = self.match_chat.input_text[:-1]
            elif name == "chat.insert": self.match_chat.input_text += args[0]
            elif name == "chat.history_older": self.match_chat.history_cursor += 1
            elif name == "chat.history_newer": self.match_chat.history_cursor -= 1
            elif name == "chat.scroll": self.match_chat.scroll_offset += args[0]
            elif name == "chat.send_input" and result: self.match_chat.input_text = ""
            elif name == "control.reset_inputs": self.held_keys.clear()
            return result
        return invoke

    def _lifecycle_owns_input(self):
        self.record("lifecycle.guard")
        return super()._lifecycle_owns_input()

    def get_module(self, name):
        self.record("registry.get", name)
        return self.modules.get(name)

    def toggle_module(self, module, *, source):
        self.record("registry.toggle", module, source=source)
        module.enabled = not module.enabled

    def key_name(self, event):
        self.record("key.name", event)
        return event.value.name

    def keybind_matches(self, binding, key_name, event):
        self.record("key.matches", binding, key_name)
        return binding == key_name

    def is_right_shift_event(self, event):
        self.record("key.right_shift", event)
        return event.key() == Qt.Key.Key_Shift and self.case.right_shift

    def handle_escape_menu_key(self, event):
        self.record("escape.handle", event)
        if not self.escape_menu_visible: return False
        self._escape_menu_key_releases.add(event.key())
        if event.key() == Qt.Key.Key_Escape and not event.isAutoRepeat():
            self.set_escape_menu_visible(False)
        self.frame_scheduler.force()
        event.accept()
        return True

    def handle_startup_key(self, event):
        self.record("startup.handle", event)
        return self.case.startup_handles

    def handle_student_key(self, event, pressed):
        self.record("student.handle", event, pressed)
        return self.student_ui.visible

    def minecraft_hud_active(self):
        self.record("minecraft.active")
        return self.case.minecraft

    def activate_minecraft_function_slot(self, slot):
        self.record("minecraft.slot", slot)
        return self.case.slot_handles

    def mapFromGlobal(self, point):
        self.record("cursor.map", point)
        return point + QPoint(1, 1)

    def economy_categories(self):
        self.record("economy.categories")
        return list(self.case.categories)

    def economy_offers(self, category):
        self.record("economy.offers", category)
        return [NS(key=key, confirmation=self.case.confirmation) for key in self.case.offers]

    def reconcile_economy_selection(self):
        # Both names model the same original command for frozen input traces.
        # Query purity is verified separately against the real window methods.
        return self.economy_selected_offer()

    def economy_selected_offer(self):
        self.record("economy.selected")
        offers = self.economy_offers(self.economy_category)
        if not self.case.selected_available or not offers: return None
        selected = next((offer for offer in offers if offer.key == self.economy_selected_key), offers[0])
        self.economy_selected_key = selected.key
        return selected

    def handle_control_key_event(self, event, pressed):
        self.record("control.handle", event, pressed)
        if event.isAutoRepeat() or not self.combat_control_input_allowed(): return False
        if event.value.name not in ("W", "A", "S", "D", "X", "G", "V"): return False
        if pressed: self.held_keys.add(event.value.name)
        else: self.held_keys.discard(event.value.name)
        return True

    def snapshot(self):
        return {
            "pending": sorted(self._escape_menu_key_releases), "held": sorted(self.held_keys),
            "pointer": (self.pointer_interaction.background_press,
                        self.pointer_interaction.background_release_owned,
                        value(self.pointer_interaction.binding_module)),
            "views": (self.escape_menu_visible, self.home_visible, self.wallpaper_picker_visible,
                      self.hero_deploy_ui.visible, self.gui_visible, self.economy_visible,
                      self.quick_action_wheel.active, self.tactical_map_hold),
            "chat": (self.match_chat.input_open, self.match_chat.input_text,
                     self.match_chat.history_cursor, self.match_chat.scroll_offset),
            "search": (self.search_focus, self.search_query),
            "economy": (self.economy_category, self.economy_selected_key,
                        self.economy_offers_scroll.position, self.economy_offers_scroll.target),
            "gui": (self.jello_state.settings_module, self.jello_state.legacy_mode),
            "modules": tuple((name, module.enabled, module.keybind) for name, module in self.modules.items()),
            "selected_module": value(self.selected_module),
        }


def run_case(case: Case, keys: list[Key] | tuple[Key, ...], *, baseline=False, fail_at=None, correct_economy_repeat_release=False):
    host = Harness(case, fail_at)
    accepted, error = [], None
    implementation = BASELINE if baseline else current.InputDispatchMixin
    with patch.object(current, "QCursor", _CURSOR):
        for key in keys:
            event = Event(key, host)
            method = implementation.keyPressEvent if key.phase == "press" else implementation.keyReleaseEvent
            finish_hold = host.finish_economy_hold
            # Explicitly corrected historical behavior, without replacing the
            # frozen route or suppressing any earlier modal-owner observations.
            if (baseline and correct_economy_repeat_release and key.phase == "release"
                    and key.repeat and key.code in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
                host.finish_economy_hold = lambda: None
            try:
                method(host, event)
            except Exception as exc:
                error = (type(exc).__name__, str(exc))
            finally:
                host.finish_economy_hold = finish_hold
            accepted.append(event.accepted)
            if error is not None: break
    return host.trace, host.snapshot(), accepted, error
