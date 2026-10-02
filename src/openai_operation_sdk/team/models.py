from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class TeamMember:
    id: str
    email: str
    role: str = ""
    seat_type: str = ""
    joined_at: Any = None
    quota: dict = field(default_factory=dict)
    quota_exhausted: bool = False

@dataclass(frozen=True)
class TeamInvite:
    id: str
    email: str
    seat_type: str = ""
    invited_at: Any = None

@dataclass(frozen=True)
class TeamSnapshot:
    workspace_id: str
    name: str
    plan: str
    role: str
    members: tuple[TeamMember, ...]
    invites: tuple[TeamInvite, ...]
    seat_capacity: int | None = None
    seat_type_capacity: dict = field(default_factory=dict)
    checked_at: float = 0.0
