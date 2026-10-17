"""Ordered startup activation through explicit, window-independent ports.

Construction publishes inert services first. This workflow then establishes the
baseline profile, loads saved settings, projects those settings, and admits a
scenario only when loading did not already request one. Callback reads are lazy:
profile loading can change the selected scenario, launcher visibility and pending
configuration. Neither a stale construction-time snapshot nor a second request
may supersede that generation.

This module imports no Qt, window, backend, configuration store or environment.
It does not own the event timer or claim rollback of already-applied effects.
A failed step propagates immediately, leaving precisely its executed prefix.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class StartupProfilePorts:
    """Ordered baseline/default-file setup followed by the saved profile."""

    sync_wheel: Callable[[], None]
    apply_match: Callable[[], None]
    apply_robot: Callable[[], None]
    refresh_profiles: Callable[[], None]
    refresh_wallpapers: Callable[[], None]
    refresh_sounds: Callable[[], None]
    ensure_default: Callable[[], None]
    load_saved: Callable[[], None]


@dataclass(frozen=True)
class StartupPresentationPorts:
    """Projection must observe the loaded settings before admitting runtime."""

    update_title: Callable[[], None]
    preload_feedback: Callable[[], None]
    apply_autostart: Callable[[], None]
    sync_mode: Callable[[], None]
    announce_ready: Callable[[], None]


@dataclass(frozen=True)
class StartupScenarioPorts:
    """Only the backend allocates a generation; the UI captures that exact one."""

    launcher_visible: Callable[[], bool]
    configuration_pending: Callable[[], bool]
    request: Callable[[], int]
    capture: Callable[[int], None]


def activate_startup_session(
    profile: StartupProfilePorts,
    presentation: StartupPresentationPorts,
    scenario: StartupScenarioPorts,
) -> None:
    """Activate one newly assembled window, before installing event delivery.

    Deliberately sequential rather than a callback registry: ordering is part
    of the startup contract, including the short-circuiting pending query.
    Re-entry/retry is not implicit because profile loading and requests have
    effects; a caller must not replay this workflow on a live window.
    """
    profile.sync_wheel()
    profile.apply_match()
    profile.apply_robot()
    profile.refresh_profiles()
    profile.refresh_wallpapers()
    profile.refresh_sounds()
    profile.ensure_default()
    profile.load_saved()
    presentation.update_title()
    presentation.preload_feedback()
    presentation.apply_autostart()
    presentation.sync_mode()
    if not scenario.launcher_visible() and not scenario.configuration_pending():
        generation = scenario.request()
        scenario.capture(generation)
    presentation.announce_ready()
