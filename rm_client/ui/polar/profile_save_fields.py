"""Fixed compatibility aliases for the window's profile-save owner."""
from ...configuration.profile_saves import ProfileSaveState


class ProfileSaveField:
    """Fixed legacy fields backed by the single delayed-save owner."""
    def __init__(self, field_name: str) -> None:
        if field_name not in ProfileSaveState.__dataclass_fields__:
            raise ValueError(f"Unknown profile-save field: {field_name}")
        self.field_name = field_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance._profile_saves, self.field_name)

    def __set__(self, instance, value):
        setattr(instance._profile_saves, self.field_name, value)
