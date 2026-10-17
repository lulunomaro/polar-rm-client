"""Value-only economy selection resolution; never writes UI or match state.

The caller decides when to commit a fallback. In particular, rendering and
input adapters must name that command instead of relying on a query mutation.
The legacy window query-shaped entry point keeps its old side effect solely
for callers outside the migrated production paths.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..economy_values import EconomyOffer


def selected_offer(offers: Sequence[EconomyOffer], selected_key: str) -> EconomyOffer | None:
    """Return the matching offer or first available-category offer, read-only."""
    if not offers:
        return None
    return next((offer for offer in offers if offer.key == selected_key), offers[0])
