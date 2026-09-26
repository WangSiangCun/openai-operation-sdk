import pytest
from team_operation_sdk import TeamClient, TeamError

def test_proxy_required():
    with pytest.raises(TeamError) as exc:
        TeamClient("w", "a@example.com", "AT", proxy_url="")
    assert exc.value.code == "proxy_required"
