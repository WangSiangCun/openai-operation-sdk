import json
from types import SimpleNamespace

import pytest

from openai_operation_sdk.accounts import AccountSecurityClient, generate_totp
from openai_operation_sdk.core import transport
from openai_operation_sdk.core.errors import OpenAIOperationError
from openai_operation_sdk.team import TeamClient, CandidateClient
from team_operation_sdk import TeamClient as LegacyTeamClient, TeamSnapshot


class Http:
    def __init__(self, *responses, **kwargs):
        self.responses = list(responses)
        self.calls = []
        self.proxies = {}
        self.trust_env = True
        self.closed = False
        self.fingerprint = kwargs.get('impersonate')
        self.cookies = SimpleNamespace(set=lambda *a, **k: None)
        self.session = self

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        body = self.responses.pop(0) if self.responses else {}
        return SimpleNamespace(status_code=200, text=json.dumps(body), json=lambda: body)

    def close(self):
        self.closed = True


def test_modules_share_error_and_transport_identity():
    from team_operation_sdk.errors import TeamError
    from team_operation_sdk.transport import ProxyFingerprintSession
    from openai_operation_sdk.core.errors import AccountError
    assert TeamError is AccountError is OpenAIOperationError
    assert ProxyFingerprintSession is transport.ProxyFingerprintSession


def test_team_and_account_clients_lock_proxy_and_use_requested_fingerprint(monkeypatch):
    monkeypatch.setattr(transport, 'CffiSession', Http)
    proxy = 'socks5://proxy.example:1080'
    clients = [TeamClient('w', 'test@example.com', 'at', proxy_url=proxy, impersonate='safari18_0'),
               AccountSecurityClient('test@example.com', 'session', proxy, impersonate='safari18_0')]
    for client in clients:
        assert client.http.session.fingerprint == 'safari18_0'
        assert client.http.session.trust_env is False
        assert client.http.session.proxies == {'http': 'socks5h://proxy.example:1080', 'https': 'socks5h://proxy.example:1080'}
        client.http.request('GET', 'https://example.com')
        assert client.http.session.calls[-1][1]['allow_redirects'] is False
        client.http.session.proxies['https'] = 'http://another.example:8080'
        with pytest.raises(OpenAIOperationError) as error:
            client.http.request('GET', 'https://example.com')
        assert error.value.code == 'proxy_changed'
        assert len(client.http.session.calls) == 1
        client.close()


def test_missing_fingerprint_never_falls_back_to_plain_requests(monkeypatch):
    monkeypatch.setattr(transport, 'CffiSession', None)
    with pytest.raises(OpenAIOperationError) as error:
        transport.ProxyFingerprintSession('http://proxy.example:8080')
    assert error.value.code == 'fingerprint_unavailable'


def snapshot_http():
    return Http(
        {'accounts': {'workspace': {'account': {'account_user_role': 'account-owner', 'name': 'Fixture', 'plan_type': 'team'},
                                   'seat_metadata': {'seat_limit': 4, 'seat_capacities': {'default': 4}}}}},
        {'items': [{'id': 'member', 'email': 'a@example.com', 'role': 'standard-user', 'seat_type': 'default', 'created_time': 100}], 'total': 1},
        {'items': [{'id': 'invite', 'email': 'b@example.com', 'seat_type': 'default', 'created_time': 200}], 'total': 1})


def test_team_snapshot_preserves_production_metadata():
    with TeamClient('workspace', 'owner@example.com', 'at', transport=snapshot_http()) as client:
        result = client.snapshot()
    assert result['seat_capacity'] == 4
    assert result['seat_type_capacity'] == {'default': 4}
    assert result['members'][0]['joined_at'] == 100
    assert result['invites'][0]['invited_at'] == 200
    assert result['remote_member_total'] == result['remote_invite_total'] == 1


def test_legacy_snapshot_keeps_dataclass_return_shape_without_protocol_copy():
    with LegacyTeamClient('workspace', 'owner@example.com', 'at', transport=snapshot_http()) as client:
        result = client.snapshot()
    assert isinstance(result, TeamSnapshot)
    assert result.members[0].email == 'a@example.com'
    assert result.invites[0].invited_at == 200
    assert result.seat_type_capacity == {'default': 4}
    assert LegacyTeamClient.invite is TeamClient.invite


def test_revoke_legacy_signature_uses_single_core_implementation():
    http = Http()
    client = LegacyTeamClient('workspace', 'owner@example.com', 'at', transport=http)
    client.revoke_invite('a@example.com')
    args, kwargs = http.calls[0]
    assert args == ('DELETE', 'https://chatgpt.com/backend-api/accounts/workspace/invites')
    assert kwargs['json_body'] == {'email_address': 'a@example.com'}


def test_team_mutation_initializes_own_session_before_write():
    client = TeamClient('workspace', 'owner@example.com', 'at', transport=Http())
    calls = []
    client.ensure_access = lambda: calls.append('identity')
    client._request = lambda *a, **k: calls.append(a[0]) or {'success': True}
    client.invite('a@example.com', 'default')
    client.remove('member')
    assert calls == ['identity', 'POST', 'identity', 'DELETE']


def test_candidate_acceptance_and_exchange_reuse_invitation_readback():
    http = Http({}, {}, {'accounts': {'workspace': {'account': {'account_user_role': 'standard-user'}}}})
    client = CandidateClient('workspace', 'a@example.com', 'at', 'session', transport=http)
    client._exchange = lambda workspace: 'workspace-token'
    result = client.accept_and_exchange('default')
    assert result['access_token'] == 'workspace-token'
    assert [args[0] for args, _ in http.calls] == ['GET', 'POST', 'GET']


def test_totp_is_reusable_without_application_dependencies():
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    assert generate_totp(secret, at_time=59) == '287082'
    assert len(generate_totp(secret)) == 6
