"""Accepted profile identity and delayed-save ownership, without UI or file I/O.

Selection and storage effects remain with the adapter. The explicit phases
preserve partial publication when those effects fail: accepting an identity
precedes selection, and retiring its old delayed save follows selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .pending import ProfileIdentity


@dataclass(frozen=True)
class ProfileSaveIntent:
    profile: str
    explicit: bool


@dataclass
class ProfileSaveState:
    accepted: ProfileIdentity | None = None
    draft: str | None = None
    pending: bool = False
    pending_owner: ProfileIdentity | None = None
    due_at: float = 0.0
    close_retry_required: bool = False
    error_reported: bool = False

    def identity(self, selected_name: Callable[[], str]) -> ProfileIdentity:
        if self.accepted is None:
            self.accepted = ProfileIdentity(selected_name())
        return self.accepted

    def accept_profile(self, profile: str, previous: ProfileIdentity) -> None:
        self.accepted = ProfileIdentity(profile, previous.generation + 1)
        self.draft = None
        self.close_retry_required = False

    def cancel_pending(self) -> None:
        self.pending = False
        self.pending_owner = None

    def save_identity(self, accepted: ProfileIdentity) -> ProfileIdentity | None:
        owner = self.pending_owner or accepted
        if owner != accepted:
            self.cancel_pending()
            return None
        return owner

    @property
    def close_requires_save(self) -> bool:
        return bool(self.draft is not None or self.pending)

    def close_intent(self, draft: str | None, accepted: ProfileIdentity) -> ProfileSaveIntent | None:
        # `draft` is the adapter's snapshot from before resolving accepted
        # identity. A reentrant resolver may alter the live draft afterwards.
        if draft is None and self.save_identity(accepted) is None:
            return None
        self.close_retry_required = True
        return ProfileSaveIntent(draft or accepted.name, explicit=draft is not None)

    def defer_save(self, identity: Callable[[], ProfileIdentity],
                   clock: Callable[[], float], delay: float) -> None:
        # Publish each stage only when its predecessor succeeds. A failing
        # identity leaves the old owner; a failing clock leaves the old due time.
        self.pending = True
        self.pending_owner = identity()
        self.due_at = clock() + delay

    def begin_draft(self, profile: str) -> None:
        self.draft = profile

    @property
    def automatic_save_blocked(self) -> bool:
        return self.draft is not None or self.close_retry_required

    def is_due(self, now: float) -> bool:
        # Preserve comparison semantics for non-finite values supplied by old
        # callers; this owner does not introduce new clock validation policy.
        return not now < self.due_at

    def complete_save(self) -> None:
        self.cancel_pending()
        self.error_reported = False
        self.close_retry_required = False
