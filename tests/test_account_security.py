import base64
import json
from types import SimpleNamespace

import pytest

from openai_operation_sdk.accounts import AccountSecurityClient
from openai_operation_sdk.core.errors import AccountError as TeamError


def response(body, status=200):
    return SimpleNamespace(status_code=status, json=lambda: body)


def web_session(email="test@example.com"):
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "personal-account"}}
    token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
    return response({"user": {"email": email}, "accessToken": token})


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.session = SimpleNamespace(cookies=SimpleNamespace(set=lambda *a, **k: None))

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def client(*responses):
    transport = Transport(*responses)
    return AccountSecurityClient("test@example.com", "session", "", transport=transport), transport


def test_preflight_uses_selected_accounts_web_identity():
    c, t = client(web_session(), response({"eligible": True}), response({
        "mfa_enabled_v2": True, "factors": {"totp": [{"id": "factor"}]}}))
    assert c.preflight() == {"password_eligible": True, "totp_count": 1, "mfa_enabled": True}
    assert t.calls[1][1]["headers"]["ChatGPT-Account-ID"] == "personal-account"


def test_wrong_session_identity_stops_before_security_requests():
    c, t = client(web_session("owner@example.com"))
    with pytest.raises(TeamError, match="session"):
        c.preflight()
    assert len(t.calls) == 1
    assert c.token == ""


def test_failed_reauthentication_clears_old_token():
    c, _ = client(web_session(), web_session("another@example.com"))
    c.authenticate()
    with pytest.raises(TeamError):
        c.authenticate()
    assert c.token == c.account_id == ""


def test_activation_200_without_success_is_not_success():
    c, _ = client(web_session(), response({"success": False}))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.activate_totp("enrollment", "123456", source="test")
    assert error.value.code == "security_activation"
    assert error.value.uncertain


def test_mutation_timeout_is_uncertain_and_never_retried():
    c, t = client(web_session(), RuntimeError("private request secret"))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.enroll_totp(source="test")
    assert error.value.uncertain
    assert "private" not in str(error.value)
    assert len(t.calls) == 2


@pytest.mark.parametrize("factor", [{"id": "other", "is_recovery": False},
                                     {"id": "selected", "is_recovery": True}])
def test_disable_rejects_unrelated_and_recovery_factors(factor):
    c, t = client(web_session(), response({"factors": {"totp": [factor]}}))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.disable_totp("selected")
    assert error.value.code == "security_factor"
    assert all(args[0] == "GET" for args, _ in t.calls)


def test_disable_requires_readback_confirmation():
    info = {"factors": {"totp": [{"id": "selected", "is_recovery": False}]}}
    c, _ = client(web_session(), response(info), response({}), response(info))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.disable_totp("selected")
    assert error.value.code == "security_disable_unconfirmed"


def test_remote_error_body_is_not_exposed():
    c, _ = client(web_session(), response({"error": "private session details"}, 401))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.enroll_totp(source="test")
    assert error.value.code == "security_reauthentication"
    assert error.value.uncertain is False
    assert "private" not in str(error.value)


@pytest.mark.parametrize('status', [403, 500, 502, 503])
def test_other_mutation_rejections_stay_conservative(status):
    c,t=client(web_session(),response({'error':'private'},status))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.enroll_totp(source='test')
    assert error.value.uncertain is True
    assert len(t.calls)==2


def test_authentication_rejection_is_not_automatically_retried_by_sdk():
    c,t=client(web_session(),response({},401))
    c.authenticate()
    with pytest.raises(TeamError) as error:
        c.activate_totp('enrollment','123456',source='test')
    assert error.value.code=='security_reauthentication'
    assert error.value.uncertain is False
    assert len(t.calls)==2
