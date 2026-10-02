"""0.1 import/return-shape compatibility. New users import openai_operation_sdk.team."""
from openai_operation_sdk.team import TeamClient as _TeamClient, CandidateClient as _CandidateClient
from openai_operation_sdk.team.models import TeamMember, TeamInvite, TeamSnapshot
from openai_operation_sdk.core.tokens import token_claims as _token_claims, token_account_id as _token_account_id
from openai_operation_sdk.core.transport import ProxyFingerprintSession
from openai_operation_sdk.core.errors import TeamError
from openai_operation_sdk.team.client import BASE_URL


class TeamClient(_TeamClient):
    def _ensure_access(self):
        return self.ensure_access()

    def snapshot(self) -> TeamSnapshot:
        value = super().snapshot()
        return TeamSnapshot(
            workspace_id=value["workspace_id"], name=value["name"], plan=value["plan"], role=value["role"],
            members=tuple(TeamMember(**{k: row[k] for k in TeamMember.__dataclass_fields__ if k in row}) for row in value["members"]),
            invites=tuple(TeamInvite(**{k: row[k] for k in TeamInvite.__dataclass_fields__ if k in row}) for row in value["invites"]),
            seat_capacity=value["seat_capacity"], seat_type_capacity=value["seat_type_capacity"], checked_at=value["checked_at"])

    def revoke_invite(self, email: str):
        return super().revoke_invite("", email)


class CandidateClient(_CandidateClient, TeamClient):
    """Keep accept_invite and the legacy snapshot return shape."""
