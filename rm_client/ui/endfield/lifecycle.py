"""Endfield启动展示、可选语音和Esc菜单的装配及帧更新边界。"""
from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import TYPE_CHECKING

from .feedback import EndfieldFeedback
from ..ui_theme import is_jello

if TYPE_CHECKING:
    from .boot import EndfieldBootPresentation
    from .menu.native import EndfieldNativeMenu


@dataclass(frozen=True)
class EndfieldPresentations:
    feedback: EndfieldFeedback
    boot: EndfieldBootPresentation
    menu: EndfieldNativeMenu | None


def create_presentations(paths) -> EndfieldPresentations:
    from PySide6.QtWidgets import QApplication
    from .boot import EndfieldBootPresentation
    private = paths.user_data_root / "endfield_pack"
    menu = None
    if sys.platform == "win32" and QApplication.platformName() == "windows":
        from .menu.native import EndfieldNativeMenu
        menu = EndfieldNativeMenu(paths.endfield_menu_pack, renderer_pack=paths.endfield_renderer_pack)
    return EndfieldPresentations(EndfieldFeedback(private),EndfieldBootPresentation(private/"boot"),menu)


def advance_feedback(host, now: float) -> None:
    host.advance_endfield_menu()
    native = host._endfield_menu
    if host.gpu_compositor.available and (native is None or native.ready):
        host._endfield_feedback.boot.mark("graphics",now)
    if host._gpu_warmup.completed:
        host._endfield_feedback.boot.mark("atlases",now)
    module = host.registry.get("PowerGuard")
    audio = host.registry.get("AudioFeedback")
    host._endfield_feedback.tick(
        now=now,identity=(host.backend.referee.profile_id,host.state.robot_id),
        connected=host.mqtt.connected and not host.backend.mqtt.mock_output,
        enabled=bool(not is_jello() and module and module.enabled and (audio is None or audio.enabled)),
        threshold=host.get_setting_number(module,"Energy Warn",20),launcher=host.home_visible,
        audio_enabled=bool(not is_jello() and audio and audio.enabled and host.setting_bool(audio,"Enabled",True)),
        volume=host.get_setting_number(audio,"UI Volume",45))
