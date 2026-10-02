"""Security-setting requests for a pool account's own web session.

This client never falls back to the workspace owner's token. Callers must
complete recent-password authentication before requesting MFA changes.
Mutation calls are deliberately not retried: a timeout may follow a successful
remote write, so the caller must reconcile the factor list first.
"""
from __future__ import annotations

from ..core.errors import AccountError as TeamError
from ..core.tokens import token_account_id
from ..core.transport import ProxyFingerprintSession


class AccountSecurityClient:
    def __init__(self, email: str, session_token: str, proxy_url: str, *, impersonate: str = "safari18_0", transport=None):
        self.email = email.strip().lower()
        self.http = transport if transport is not None else ProxyFingerprintSession(proxy_url, impersonate=impersonate)
        self.token = ""
        self.account_id = ""
        self.http.session.cookies.set(
            "__Secure-next-auth.session-token", session_token,
            domain=".chatgpt.com", path="/")

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _decode(response, *, mutation=False):
        status = response.status_code
        if status not in (200, 201):
            code = "security_reauthentication" if status == 401 else "security_http_" + str(status)
            raise TeamError(code, "安全设置请求失败，请重新验证登录状态" if status == 401
                            else f"安全设置接口返回 HTTP {status}", uncertain=mutation)
        try:
            body = response.json()
        except (ValueError, TypeError):
            raise TeamError("security_response", "安全设置响应格式异常", uncertain=mutation) from None
        if not isinstance(body, dict):
            raise TeamError("security_response", "安全设置响应格式异常", uncertain=mutation)
        if body.get("error"):
            raise TeamError("security_rejected", "远端未确认安全设置操作", uncertain=mutation)
        return body

    def authenticate(self):
        # Clear prior state before checking a replacement session.
        self.token = self.account_id = ""
        response = self.http.request("GET", "https://chatgpt.com/api/auth/session",
                                     headers={"Accept": "application/json"})
        body = self._decode(response)
        user = body.get("user")
        if not isinstance(user, dict) or str(user.get("email") or "").strip().lower() != self.email:
            raise TeamError("security_identity", "网页 session 与选中的账号不一致")
        token = body.get("accessToken")
        if not isinstance(token, str) or not token:
            raise TeamError("security_session", "网页 session 未返回 access token")
        account_id = token_account_id(token)
        if not account_id:
            raise TeamError("security_identity", "网页凭据缺少账号身份")
        self.token, self.account_id = token, account_id

    def _request(self, method, path, body=None):
        if not self.token:
            raise TeamError("security_session", "请先验证账号本人的网页 session")
        try:
            response = self.http.request(method, "https://chatgpt.com/backend-api" + path,
                headers={"Authorization": "Bearer " + self.token,
                         "ChatGPT-Account-ID": self.account_id,
                         "Accept": "application/json", "Content-Type": "application/json",
                         "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/"},
                json_body=body)
        except Exception:
            # Do not expose response bodies, proxy credentials or request secrets.
            raise TeamError("security_transport", "安全设置连接中断，请核对远端状态",
                            uncertain=method != "GET") from None
        return self._decode(response, mutation=method != "GET")

    def preflight(self):
        self.authenticate()
        eligibility = self._request("GET", "/accounts/change_password/eligibility")
        info = self.mfa_info()
        return {"password_eligible": eligibility.get("eligible") is True,
                "totp_count": len(self.totp_factors(info)),
                "mfa_enabled": info.get("mfa_enabled_v2") is True}

    def mfa_info(self):
        return self._request("GET", "/accounts/mfa_info")

    @staticmethod
    def totp_factors(info):
        factors = info.get("factors")
        items = factors.get("totp") if isinstance(factors, dict) else None
        if items is None:
            return []
        if not isinstance(items, list) or any(not isinstance(x, dict) or not x.get("id") for x in items):
            raise TeamError("security_response", "TOTP 因子响应格式异常")
        return items

    def disable_totp(self, factor_id):
        # Refuse stale or unrelated factor IDs (including passkeys/recovery factors).
        factors = self.totp_factors(self.mfa_info())
        if not any(x["id"] == factor_id and x.get("is_recovery") is False for x in factors):
            raise TeamError("security_factor", "选中的 TOTP 因子已变化，请重新读取")
        self._request("POST", "/accounts/mfa/user/disable_in_house", {"factor_id": factor_id})
        if any(x["id"] == factor_id for x in self.totp_factors(self.mfa_info())):
            raise TeamError("security_disable_unconfirmed", "远端仍保留旧 TOTP 因子", uncertain=True)

    def enroll_totp(self, *, source):
        result = self._request("POST", "/accounts/mfa/enroll", {"factor_type": "totp", "source": source})
        if not all(isinstance(result.get(k), str) and result[k] for k in ("secret", "session_id")):
            raise TeamError("security_enrollment", "远端未返回完整 TOTP 注册信息", uncertain=True)
        return result

    def activate_totp(self, session_id, code, *, source):
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise TeamError("security_code", "请输入六位 TOTP 动态码")
        result = self._request("POST", "/accounts/mfa/user/activate_enrollment",
            {"factor_type": "totp", "session_id": session_id, "code": code, "source": source})
        if result.get("success") is not True:
            raise TeamError("security_activation", "远端未确认新 TOTP 已激活", uncertain=True)
