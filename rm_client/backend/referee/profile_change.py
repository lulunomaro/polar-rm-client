"""Owner-thread profile target and completion evidence for direct edits."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RefereeProfileChange:
    requested_profile: str
    applied_profile: str
    transport_profile: str
    frontend_profile: str | None
    pending: bool
    error: str

    @property
    def completed(self) -> bool:
        return (not self.pending and not self.error
                and self.requested_profile == self.applied_profile == self.transport_profile
                and self.frontend_profile in (None, self.applied_profile))
