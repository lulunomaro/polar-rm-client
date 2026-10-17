"""Synthetic state/effect recorder for the frozen economy command boundary."""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF
from rm_client.economy import EconomyOffer, EconomyRequest
from rm_client.ui.polar.mixins import economy_mixin as current

BASELINE_PATH = Path(__file__).with_name("fixtures") / "economy_interaction_baseline.py"
spec = importlib.util.spec_from_file_location("frozen_economy_interactions", BASELINE_PATH)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
BASELINE = baseline.EconomyInteractionBaseline
OFFER = EconomyOffer("small", "ammo", "Buy ammo", "Small ammo", "Rules", "item", 1, 30, 1, 1, True)
REQUEST = EconomyRequest(1, 100., "RMUC7v7", "Infantry1", "small", 1, 30, 1, 1, "CommonCommand", b"test", False)


def value(item):
    if isinstance(item, (QPointF, QRectF)):
        return tuple(item.getRect()) if isinstance(item, QRectF) else (item.x(), item.y())
    if is_dataclass(item):
        return asdict(item)
    if isinstance(item, NS):
        return vars(item)
    if isinstance(item, dict):
        return {key: value(entry) for key, entry in item.items()}
    if isinstance(item, (list, tuple)):
        return tuple(value(entry) for entry in item)
    return item


@dataclass(frozen=True)
class Case:
    selected: str = "small"
    selected_index: int = 7
    category: str = "ammo"
    offers: tuple[EconomyOffer, ...] = (OFFER, replace(OFFER, key="large"))
    enabled: bool = True
    status_only: bool = False
    confirmation: str = "double"
    armed_key: str = ""
    armed_until: float = 101.
    hold_key: str = ""
    hold_started: float = 99.4
    hold_rect: tuple = (0., 0., 20., 20.)
    pending: bool = False
    mock: bool = False
    scenario: str = "OfficialEvent"
    token: str | None = "capability"
    session: bool = True
    accepted: bool = True
    submit_error: str = ""
    now: float = 100.


class Harness:
    TRACKED = frozenset(("economy_selected", "economy_selected_key", "economy_armed_key", "economy_armed_until",
                         "economy_hold_key", "economy_hold_started", "economy_hold_rect", "_economy_qualification_token"))

    def __init__(self, case, implementation, fail_at, *, observe_confirmation_reads=False):
        self.observe_confirmation_reads = observe_confirmation_reads
        self.case = case
        self.implementation = implementation
        self.fail_at = fail_at
        self.trace = []
        self.recording = False
        self.economy_category = case.category
        self.economy_selected = case.selected_index
        self.economy_selected_key = case.selected
        self.economy_armed_key = case.armed_key
        self.economy_armed_until = case.armed_until
        self.economy_hold_key = case.hold_key
        self.economy_hold_started = case.hold_started
        self.economy_hold_rect = QRectF(*case.hold_rect)
        self._economy_qualification_token = case.token
        self.state = NS(identity="state")
        self.scenario_manager = NS(current=NS(key=case.scenario))
        self.frame_scheduler = NS(force=lambda: self.effect("frame.force"))
        self.economy_bridge = NS(latest=NS(status="pending") if case.pending else None, submit=self.submit)
        self.mqtt = NS(last_error="old error", publish_air_support_command=self.publish_air,
                       publish_common_command=self.publish_common)
        self.backend = NS(lock_control=lambda reason: self.effect("control.lock", reason),
                          qualification=NS(session=NS(session_id="session") if case.session else None),
                          begin_qualification=self.qualify,
                          field_log=NS(emit=lambda *args, **kwargs: self.effect("audit", *args, **kwargs)))
        self.recording = True

    def __getattribute__(self, name):
        if name in ("economy_armed_key", "economy_armed_until"):
            state = object.__getattribute__(self, "__dict__")
            if state.get("observe_confirmation_reads") and state.get("recording"):
                self.effect("read", name)
        return object.__getattribute__(self, name)

    def __getattr__(self, name):
        return getattr(self.implementation, name).__get__(self, type(self))

    def __setattr__(self, name, item):
        object.__setattr__(self, name, item)
        if name in self.TRACKED and self.__dict__.get("recording"):
            self.trace.append(("write", name, value(item)))

    def effect(self, name, *args, **kwargs):
        self.trace.append((name, value(args), value(kwargs)))
        calls = sum(entry[0] != "write" for entry in self.trace)
        if self.fail_at == calls:
            raise RuntimeError(f"injected {name}")

    def clock(self):
        self.effect("clock")
        return self.case.now

    def toast(self, message):
        self.effect("toast", message)

    def economy_offers(self, category=None):
        self.effect("offers", category)
        return [item for item in self.case.offers if category is None or item.category == category]

    def economy_uses_mock_transport(self):
        self.effect("mock.query")
        return self.case.mock

    def submit(self, state, offer, quantity, *, mock):
        self.effect("submit", state, offer, quantity, mock=mock)
        if self.case.submit_error == "ValueError":
            raise ValueError("invalid request")
        if self.case.submit_error == "RuntimeError":
            raise RuntimeError("transport failure")
        return NS(message="submitted")

    def publish_air(self, *args, **kwargs):
        self.effect("publish.air", *args, **kwargs)
        return self.case.accepted

    def publish_common(self, *args, **kwargs):
        self.effect("publish.common", *args, **kwargs)
        return self.case.accepted

    def qualify(self, *args, **kwargs):
        self.effect("qualify", *args, **kwargs)
        return NS(value="new capability", expires_at=130.)

    def snapshot(self):
        return ({key: value(getattr(self, key)) for key in sorted(self.TRACKED)}, self.mqtt.last_error)


def run(case, method, args=(), *, frozen=False, fail_at=None, observe_confirmation_reads=False):
    implementation = BASELINE if frozen else current.EconomyMixin
    host = Harness(case, implementation, fail_at, observe_confirmation_reads=observe_confirmation_reads)
    error = None
    result = None
    with patch.object(current.time, "perf_counter", side_effect=host.clock), \
         patch.object(current, "play_buy_sound", side_effect=lambda owner, key: owner.effect("sound", key)), \
         patch.object(baseline, "play_buy_sound", side_effect=lambda owner, key: owner.effect("sound", key)):
        try:
            result = getattr(host, method)(*args)
        except Exception as exc:
            error = (type(exc).__name__, str(exc))
    host.recording = False
    return host.trace, host.snapshot(), value(result), error
