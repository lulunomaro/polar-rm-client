"""Pure admission for mutable MQTTLink values, before transport side effects.

This is the live setting contract (1000..9999), not a DNS resolver or a change
to the lower-level transport/scenario port contract. An invalid field retains
its current accepted value independently of the other field.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MqttEndpointAdmission:
    host: str
    port: int
    note: str


def _live_port(value: object) -> int | None:
    """Accept integer text and finite integral UI/profile numbers without loss."""
    if type(value) is str:
        try:
            port = int(value)
        except ValueError:
            return None
    elif type(value) is int:
        port = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        port = int(value)
    else:
        return None
    return port if 1000 <= port <= 9999 else None


def admit_mqtt_endpoint(host: object, port: object, *, current_host: str,
                        current_port: int) -> MqttEndpointAdmission:
    """Return canonical values; never mutate settings or acquire resources.

    JSON scalar numbers and integer strings are the supported port inputs.
    Booleans, nonintegral/nonfinite numbers and other objects are invalid.
    Current values are already owned by the backend; rejection must not invent
    a default or reinterpret that owner's endpoint.
    """
    notes = []
    wanted_host = host.strip() if isinstance(host, str) else ''
    if not wanted_host or any(character.isspace() for character in wanted_host):
        wanted_host = current_host
        notes.append('MQTT 地址为空或含空格，沿用 ' + current_host)
    wanted_port = _live_port(port)
    if wanted_port is None:
        wanted_port = current_port
        notes.append('MQTT 端口非法，沿用 ' + str(current_port))
    return MqttEndpointAdmission(wanted_host, wanted_port, '；'.join(notes))
