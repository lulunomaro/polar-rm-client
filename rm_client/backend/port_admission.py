"""Detached, ordered port-conflict policy over immutable observations.

The adapter owns OS-name/alias normalization and reads current native owners on
every tick. This module never discovers ports, retains handles, or grants arm
permission. Reservations may be lazy so first-conflict short-circuiting does not
force observations of later OS aliases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class PortReservation:
    label: str
    key: str | None = None
    unresolved_auto: bool = False


def first_port_conflict(used_keys: set[str], reservations: Iterable[PortReservation]) -> str:
    """Auto is conservatively occupied; absent/Stub/None keys do not conflict.

    The first conflicting reservation owns the diagnostic. This decision only
    vetoes reception configuration; clearing a veto never authorizes forwarding.
    """
    for reservation in reservations:
        if reservation.unresolved_auto or (
                reservation.key is not None and reservation.key in used_keys):
            return f"{reservation.label} port may conflict; select distinct explicit ports or Stub"
    return ""
