"""Team workspace and invitation operations, shared by all consumers."""
from __future__ import annotations
import time
from urllib.parse import quote
from ..core.errors import TeamError
from ..core.tokens import token_account_id, token_claims
from ..core.transport import ProxyFingerprintSession

BASE = "https://chatgpt.com/backend-api"
BASE_URL = BASE


class TeamClient:
    def __init__(self, workspace_id: str, admin_email: str, access_token: str,
                 session_token: str = "", proxy_url: str = "", *,
                 base_url: str = BASE, impersonate: str = "safari18_0", transport=None):
        self.workspace_id = workspace_id
        self.admin_email = admin_email.lower().strip()
        self.access_token = access_token.strip()
        self.session_token = session_token.strip()
        if not self.access_token:
            raise TeamError("missing_access_token", "管理员 access token 为空")
        self.base_url = base_url.rstrip("/")
        self.http = transport if transport is not None else ProxyFingerprintSession(proxy_url, impersonate=impersonate)
        self.http.session.cookies.set("__Secure-next-auth.session-token", self.session_token,
                                      domain=".chatgpt.com", path="/")
        self.workspace_ready = False
        # The users/invites endpoints expose exact current totals, but they do
        # not expose the purchased seat limit. Keep those totals separately so
        # callers can explain the distinction instead of displaying them as a
        # guessed capacity.
        self._list_totals: dict[str, int | None] = {}

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _headers(self, token: str | None = None, path: str = "") -> dict:
        out = {"Authorization": "Bearer " + (token or self.access_token),
               "Accept": "application/json", "Content-Type": "application/json",
               "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/"}
        if path:
            out["x-openai-target-path"] = path
            out["x-openai-target-route"] = path
        if self.workspace_id:
            out["ChatGPT-Account-ID"] = self.workspace_id
        return out

    @staticmethod
    def _json(response):
        if response.status_code == 204 or not (response.text or "").strip():
            return {}
        try:
            value = response.json()
        except ValueError:
            raise TeamError("invalid_response", "远端返回不是 JSON") from None
        if not isinstance(value, dict):
            raise TeamError("invalid_response", "远端响应结构异常")
        return value

    def _request(self, method: str, path: str, *, token: str | None = None,
                 params: dict | None = None, body: dict | None = None,
                 auth_url: str | None = None) -> dict:
        response = self.http.request(method, auth_url or self.base_url + path,
                                     headers=self._headers(token, path), params=params, json_body=body)
        if response.status_code not in (200, 201, 204):
            labels = {401: "凭证未授权或已失效", 403: "远端拒绝当前身份或代理出口",
                      409: "远端状态冲突，请先重新同步", 429: "远端限流，请稍后重试"}
            raise TeamError(f"http_{response.status_code}", labels.get(response.status_code, f"远端 HTTP {response.status_code}"), uncertain=method != "GET")
        return self._json(response)

    def _exchange(self, workspace_id: str) -> str:
        path = "/api/auth/session"
        old = self.workspace_id
        self.workspace_id = ""
        try:
            data = self._request("GET", path, params={"exchange_workspace_token": "true",
                                  "workspace_id": workspace_id,
                                  "reason": "setCurrentAccountWithoutRedirect"},
                                 auth_url="https://chatgpt.com" + path)
        finally:
            self.workspace_id = old
        user = data.get("user") if isinstance(data, dict) else {}
        identity = str(user.get("email") or "").lower() if isinstance(user, dict) else ""
        if identity and identity != self.admin_email:
            raise TeamError("identity_mismatch", "session 身份与管理员邮箱不一致")
        token = str(data.get("accessToken") or data.get("access_token") or "").strip()
        if not token:
            raise TeamError("missing_workspace_token", "session 未返回目标工作区 access token")
        token_wid = token_account_id(token)
        if workspace_id and token_wid and token_wid != workspace_id:
            raise TeamError("workspace_token_mismatch", "交换得到的 token 不属于目标工作区")
        return token

    def ensure_access(self):
        if self.workspace_ready:
            return
        if self.session_token:
            try:
                self.access_token = self._exchange(self.workspace_id)
            except TeamError as exc:
                # Some web sessions return a token whose JWT account claim is
                # the parent account even though the original AT is already
                # scoped to the requested Team. Keep that AT and let the
                # normal read below validate the real remote access.
                if exc.code != "workspace_token_mismatch":
                    raise
        self.workspace_ready = True

    def _paged(self, kind: str) -> list[dict]:
        self.ensure_access()
        items, seen, offset = [], set(), 0
        reported_total = None
        for _ in range(100):
            data = self._request("GET", f"/accounts/{quote(self.workspace_id, safe='')}/{kind}",
                                 params={"offset": offset, "limit": 100})
            batch = data.get("items")
            total = data.get("total", data.get("total_count"))
            if isinstance(total, int) and total >= 0:
                reported_total = total
            more = data.get("has_more")
            if not isinstance(batch, list) or (total is None and not isinstance(more, bool)):
                raise TeamError("pagination_unknown", "远端列表缺少可靠分页信息")
            for row in batch:
                if not isinstance(row, dict):
                    raise TeamError("pagination_unknown", "远端列表成员结构异常")
                rid = row.get("id") or row.get("invite_id")
                if not rid or str(rid) in seen:
                    raise TeamError("pagination_changed", "远端列表出现重复记录")
                seen.add(str(rid))
                email = str(row.get("email") or row.get("email_address") or "").strip().lower()
                seat = row.get("seat_type") or row.get("seatType") or row.get("plan_type") or row.get("plan")
                quota = row.get("quota") if isinstance(row.get("quota"), dict) else {}
                quota_exhausted = bool(row.get("quota_exhausted") or row.get("quota_used_up") or
                                       quota.get("exhausted") or quota.get("used_up"))
                created_at = row.get("created_time") or row.get("created_at") or row.get("joined_at")
                items.append({"id": str(rid), "email": email,
                              "role": str(row.get("role") or row.get("account_user_role") or ""),
                              "seat_type": str(seat).strip().lower() if isinstance(seat, str) else "",
                              "quota_exhausted": quota_exhausted,
                              "quota": quota,
                              "joined_at": created_at if kind == "users" else None,
                              "invited_at": created_at if kind == "invites" else None})
            offset += len(batch)
            if total is not None and offset == total and more is not True:
                self._list_totals[kind] = reported_total
                return items
            if total is None and more is False:
                self._list_totals[kind] = reported_total
                return items
            if not batch or (total is not None and offset > total):
                raise TeamError("pagination_changed", "远端列表分页发生变化")
        raise TeamError("pagination_limit", "远端列表超过安全分页上限")

    def snapshot(self) -> dict:
        self.ensure_access()
        # The identity read is deliberately first; writes only happen after it succeeds.
        identity = self._request("GET", "/accounts/check/v4-2023-04-27")
        accounts = identity.get("accounts")
        detail = accounts.get(self.workspace_id) if isinstance(accounts, dict) else None
        account = detail.get("account") if isinstance(detail, dict) else None
        if not isinstance(account, dict):
            raise TeamError("workspace_missing", "管理员凭证未返回目标工作区")
        role = str(account.get("account_user_role") or "")
        if role not in ("account-owner", "account-admin"):
            raise TeamError("not_admin", "当前凭证不是工作区管理员")
        # Different workspace API versions use different names for the member
        # seat limit. Preserve a reliable value when one is returned. The
        # refill worker refuses to guess when this value is absent.
        seat_capacity = None
        seat_type_capacity = {}
        for key in ("seat_capacity", "seat_limit", "member_limit", "max_members", "max_users"):
            value = account.get(key)
            if isinstance(value, int) and value >= 0:
                seat_capacity = value
                break
        if seat_capacity is None and isinstance(detail, dict):
            metadata = detail.get("seat_metadata")
            if isinstance(metadata, dict):
                for key in ("seat_capacity", "seat_limit", "member_limit"):
                    value = metadata.get(key)
                    if isinstance(value, int) and value >= 0:
                        seat_capacity = value
                        break
        for source in (account, detail):
            if not isinstance(source, dict):
                continue
            for key in ("seat_capacities", "seat_limits", "member_seat_capacities"):
                raw = source.get(key)
                if isinstance(raw, dict):
                    for name, value in raw.items():
                        if isinstance(value, int) and value >= 0:
                            seat_type_capacity[str(name)] = value
        if isinstance(detail, dict) and isinstance(detail.get("seat_metadata"), dict):
            raw = detail["seat_metadata"].get("seat_capacities")
            if isinstance(raw, dict):
                for name, value in raw.items():
                    if isinstance(value, int) and value >= 0:
                        seat_type_capacity[str(name)] = value
        members = self._paged("users")
        invites = self._paged("invites")
        return {"workspace_id": self.workspace_id, "name": account.get("name") or "",
                "plan": account.get("plan_type") or "", "role": role,
                "seat_capacity": seat_capacity, "seat_type_capacity": seat_type_capacity,
                "capacity_source": "remote" if seat_capacity is not None or seat_type_capacity else "unavailable",
                "capacity_message": "远端接口未提供购买席位上限；已加入/待接受数量仍为远端实时总数" if seat_capacity is None and not seat_type_capacity else "",
                "remote_member_total": self._list_totals.get("users", len(members)),
                "remote_invite_total": self._list_totals.get("invites", len(invites)),
                "members": members, "invites": invites,
                "checked_at": time.time()}

    def remove(self, member_id: str):
        self.ensure_access()
        return self._request("DELETE", f"/accounts/{quote(self.workspace_id, safe='')}/users/{quote(member_id, safe='')}")

    def invite(self, email: str, seat_type: str):
        self.ensure_access()
        seat_type = (seat_type or "").lower().strip()
        if seat_type not in ("default", "prolite"):
            raise TeamError("seat_type_required", "邀请必须沿用当前席位的 seat_type")
        data = self._request("POST", f"/accounts/{quote(self.workspace_id, safe='')}/invites",
                             body={"email_addresses": [email], "role": "standard-user",
                                   "resend_emails": True, "seat_type": seat_type})
        if data.get("success") is False or data.get("errored_emails"):
            raise TeamError("invite_rejected", "远端拒绝邀请", uncertain=True)
        return data

    def revoke_invite(self, invite_id: str, email: str = ""):
        self.ensure_access()
        if not email:
            raise TeamError("invite_email", "远端邀请缺少邮箱")
        # The working Team contract cancels by deleting from the collection
        # with the invited email. Item DELETE returns 405 and item PATCH can
        # return success without changing the pending state.
        return self._request("DELETE", f"/accounts/{quote(self.workspace_id, safe='')}/invites",
                      body={"email_address": email})


class CandidateClient(TeamClient):
    def __init__(self, workspace_id: str, email: str, access_token: str,
                 session_token: str = "", proxy_url: str = "", *,
                 base_url: str = BASE, impersonate: str = "safari18_0", transport=None):
        super().__init__(workspace_id, email, access_token, session_token, proxy_url,
                         base_url=base_url, impersonate=impersonate, transport=transport)
        self.email = email.lower().strip()
        self.personal_token = access_token.strip()

    def _candidate_headers(self, token: str, path: str):
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "Content-Type": "application/json", "Origin": "https://chatgpt.com",
                   "Referer": "https://chatgpt.com/", "x-openai-target-path": path,
                   "x-openai-target-route": path}
        account_id = token_account_id(token)
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        return headers

    def _candidate_request(self, method: str, path: str, token: str, body: dict | None = None) -> dict:
        response = self.http.request(method, self.base_url + path, headers=self._candidate_headers(token, path), json_body=body)
        if response.status_code not in (200, 201, 204):
            labels = {401: "候补账号凭证失效", 403: "候补账号或代理被拒绝", 409: "候补邀请状态冲突", 429: "候补接口限流"}
            raise TeamError(f"candidate_http_{response.status_code}", labels.get(response.status_code, f"候补接口 HTTP {response.status_code}"), uncertain=method != "GET")
        return self._json(response)

    def export_workspaces(self) -> list[dict]:
        # Read this account's actual memberships, independent of pool ownership.
        token = self._exchange("") if self.session_token else self.personal_token
        data = self._candidate_request("GET", "/accounts/check/v4-2023-04-27", token)
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            raise TeamError("workspace_response", "账号未返回工作区列表")
        return [{"id": wid, **value.get("account", {})} for wid, value in accounts.items()
                if wid != "default" and isinstance(value, dict) and isinstance(value.get("account"), dict)]

    def _accept_invitation(self, seat_type: str) -> None:
        # Validate the personal AT first. A session exchange is only used when the AT read is unauthorized.
        try:
            self._candidate_request("GET", "/accounts/check/v4-2023-04-27", self.personal_token)
            personal = self.personal_token
        except TeamError as exc:
            if exc.code != "candidate_http_401":
                raise
            personal = self._exchange("")
        seat_type = (seat_type or "").lower().strip()
        if seat_type not in ("default", "prolite"):
            raise TeamError("seat_type_required", "候补接受邀请缺少 seat_type")
        self._candidate_request("POST", f"/accounts/{quote(self.workspace_id, safe='')}/invites/accept",
                                personal, {"seat_type": seat_type})
        # Read back the personal account's workspace membership and obtain workspace-scoped AT.
        check = self._candidate_request("GET", "/accounts/check/v4-2023-04-27", personal)
        accounts = check.get("accounts") if isinstance(check, dict) else None
        detail = accounts.get(self.workspace_id) if isinstance(accounts, dict) else None
        account = detail.get("account") if isinstance(detail, dict) else None
        if not isinstance(account, dict) or account.get("account_user_role") != "standard-user":
            raise TeamError("candidate_not_joined", "接受邀请后未读回目标工作区成员身份")

    def accept_invite(self, seat_type: str) -> dict:
        self._accept_invitation(seat_type)
        return {"ok": True, "email": self.email, "workspace_id": self.workspace_id}

    def accept_and_exchange(self, seat_type: str) -> dict:
        self._accept_invitation(seat_type)
        self.access_token = self._exchange(self.workspace_id)
        return {"access_token": self.access_token, "session_token": self.session_token,
                "workspace_id": self.workspace_id, "email": self.email}

    def reauthorize(self) -> dict:
        """Refresh a personal account session without changing team membership."""
        if not self.session_token:
            raise TeamError("missing_session_token", "自用账号缺少 session token，无法重新授权")
        personal = self._exchange("")
        self.access_token = personal
        # Keep the credential useful to Team/CPA consumers: when this account
        # already belongs to a workspace, exchange the fresh personal token
        # back into the workspace-scoped token before returning it.
        scoped = self._exchange(self.workspace_id) if self.workspace_id else personal
        return {"access_token": scoped, "session_token": self.session_token,
                "email": self.email}
