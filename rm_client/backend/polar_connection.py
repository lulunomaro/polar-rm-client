"""Blocking acquisition of a Polar endpoint, without publishing or closing it.

The transport reserves one connector before calling this boundary outside its
lifecycle lock. Every returned resource, including a partially opened failure,
belongs to that connector until it publishes or confirms rejection cleanup.
This module never starts workers, authorizes controls, or emits wire frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import serial_stream
from .serial_stream import StreamOpenError


@dataclass(frozen=True)
class PolarConnectionConfig:
    mode: str
    port: str
    baud: int
    host: str
    tcp_port: int
    opener: Any
    # Even though acquisition does not use mock mode, changing it revokes the
    # snapshot before publication, just like changing a physical endpoint.
    mock_writes: bool

    @property
    def uses_bridge(self) -> bool:
        return self.mode in ("tcp", "wifi", "bridge")


@dataclass(frozen=True)
class PolarConnectionResult:
    resource: Any = None
    resolved_port: str = ""
    error: str = ""
    error_code: str = ""


def acquire_polar_connection(
    config: PolarConnectionConfig, *, resolve_port: Callable[[str], str],
) -> PolarConnectionResult:
    """Resolve/open one captured configuration; retain failed-open resources."""
    opened = None
    resolved = ""
    try:
        if config.uses_bridge:
            if not str(config.host or "").strip():
                return PolarConnectionResult(error="TCP 模式要填桥接主机地址（车上小电脑的 IP）",
                                             error_code="NO_BRIDGE_HOST")
            resolved = f"tcp://{config.host}:{config.tcp_port}"
        else:
            resolved = resolve_port(config.port)
            if resolved in ("", "Stub", "stub", "None", "none"):
                if str(config.port).strip().lower() == "auto":
                    return PolarConnectionResult(error="未找到机器人 USB 串口（可重试，或在 RobotLink.Port 里显式选口）",
                                                 error_code="NO_ROBOT_USB")
                return PolarConnectionResult(error="未配置真实机器人串口（Stub）",
                                             error_code="PORT_STUB")
        opened = serial_stream.open_stream(
            config.mode, port=resolved, baud=config.baud, host=config.host,
            tcp_port=config.tcp_port, serial_opener=config.opener,
        )
        if not (opened and getattr(opened, "is_open", False)):
            return PolarConnectionResult(opened, resolved, f"链路 {resolved} 打开失败", "PORT_OPEN_FAILED")
        return PolarConnectionResult(opened, resolved)
    except Exception as exc:
        if isinstance(exc, StreamOpenError):
            opened = exc.resource_owner
        if config.uses_bridge:
            return PolarConnectionResult(opened, resolved, f"连不上车端桥 {resolved}：{exc}", "BRIDGE_UNREACHABLE")
        return PolarConnectionResult(opened, resolved, f"{resolved}: {exc}", "PORT_BUSY")
