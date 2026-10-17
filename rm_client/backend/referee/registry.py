"""Explicit, reviewed referee profiles. Never infer a year from packet bytes.

Add a new year's codec, contract, adapter and transport policy here. Team USB,
DT7 and PRM1 are deliberately outside this registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ...referee_profiles import (
    DEFAULT_PROFILE, STUDENT_PREVIEW_PROFILE, RefereeProfileDefinition,
    UnsupportedRefereeProfile, get_profile_definition, supported_profiles,
)
from .rm2026 import codec
from .student_legacy import codec as student_codec

@dataclass(frozen=True, slots=True)
class RefereeProfile:
    profile_id: str
    year: int
    protocol_version: str
    codec: Any
    mqtt_host: str
    mqtt_port: int
    client_ip: str
    udp_port: int
    udp_codec: str
    uplink_hz: Mapping[str, float]
    max_qos: int = 1
    read_only: bool = False

    def validate_client_id(self, client_id: str) -> str:
        # Official IDs are robot IDs, not player-end 0x0103 or a random UUID.
        value = str(client_id).strip()
        if not value.isascii() or not value.isdigit():
            raise ValueError("MQTT ClientId must be a robot ID (1-9 or 101-109)")
        number = int(value)
        if number not in {*range(1, 10), *range(101, 110)}:
            raise ValueError("MQTT ClientId must be a robot ID (1-9 or 101-109)")
        return str(number)

    def validate_publish(self, topic: str, qos: int) -> tuple[bool, str]:
        if self.read_only:
            return False, f"{self.profile_id}: READ ONLY; no control/economy uplink"
        if str(topic) not in self.codec.UPLINK_MESSAGES:
            return False, f"{self.profile_id}: not an official uplink topic: {topic}"
        if isinstance(qos, bool) or qos not in range(self.max_qos + 1):
            return False, f"{self.profile_id}: QoS must be 0..{self.max_qos}"
        return True, "ok"

    def create_state_adapter(self, host: Any) -> Any:
        # An explicit import also keeps PyInstaller's dependency graph complete.
        if self.profile_id == DEFAULT_PROFILE:
            from .rm2026.state_adapter import Rm2026StateAdapter
            return Rm2026StateAdapter(host)
        if self.profile_id == STUDENT_PREVIEW_PROFILE:
            from .student_legacy.state_adapter import StudentLegacyStateAdapter
            return StudentLegacyStateAdapter(host)
        raise UnsupportedRefereeProfile(self.profile_id)

    def available_actions(self, state: Any) -> list:
        if self.profile_id == DEFAULT_PROFILE:
            from .rm2026.economy_rules import available_actions
            return available_actions(state)
        if self.read_only:
            return []
        raise UnsupportedRefereeProfile(self.profile_id)


def _assemble_profile(
    definition: RefereeProfileDefinition,
    codec: Any,
    uplink_hz: Mapping[str, float],
) -> RefereeProfile:
    """Bind reviewed metadata to a codec without a domain-to-backend edge."""
    return RefereeProfile(
        profile_id=definition.profile_id,
        year=definition.year,
        protocol_version=definition.protocol_version,
        codec=codec,
        mqtt_host=definition.mqtt_host,
        mqtt_port=definition.mqtt_port,
        client_ip=definition.client_ip,
        udp_port=definition.udp_port,
        udp_codec=definition.udp_codec,
        uplink_hz=uplink_hz,
        max_qos=definition.max_qos,
        read_only=definition.read_only,
    )


_RM2026 = _assemble_profile(
    get_profile_definition(DEFAULT_PROFILE), codec,
    # Operator role is not authenticated by this client. Use the stricter
    # semi-automatic operator interval (3 s) for all map marks, also satisfying
    # the gimbal operator's 0.5 s minimum in section 1.3.1.
    MappingProxyType({
        name: (75.0 if name in {"KeyboardMouseControl", "CustomControl"}
               else 1.0 / 3.0 if name == "MapClickCmd" else 10.0)
        for name in codec.UPLINK_MESSAGES
    }),
)
_STUDENT = _assemble_profile(
    get_profile_definition(STUDENT_PREVIEW_PROFILE), student_codec, MappingProxyType({}),
)
_PROFILES = MappingProxyType({_RM2026.profile_id: _RM2026, _STUDENT.profile_id: _STUDENT})


def get_profile(profile_id: str = DEFAULT_PROFILE) -> RefereeProfile:
    return _PROFILES[get_profile_definition(profile_id).profile_id]
