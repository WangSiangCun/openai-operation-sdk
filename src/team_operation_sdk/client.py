from __future__ import annotations
from dataclasses import dataclass, field
from urllib.parse import quote
from typing import Any
import base64
import json
import time
from .errors import TeamError
from .transport import ProxyFingerprintSession

BASE_URL = "https://chatgpt.com/backend-api"

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


def _token_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def _token_account_id(token: str) -> str:
    auth = _token_claims(token).get("https://api.openai.com/auth") or {}
    return str(auth.get("chatgpt_account_id") or auth.get("account_id") or "")


class TeamClient:
    """Team seat API client with one proxy-locked fingerprint session."""
    def __init__(self, workspace_id: str, admin_email: str, access_token: str,
                 session_token: str = "", proxy_url: str = "", *,
                 base_url: str = BASE_URL, impersonate: str = "safari18_0"):
        if not access_token.strip():
            raise TeamError("missing_access_token", "管理员 access token 为空")
        self.workspace_id = workspace_id.strip()
        self.admin_email = admin_email.lower().strip()
        self.access_token = access_token.strip()
        self.session_token = session_token.strip()
        self.base_url = base_url.rstrip("/")
        self.http = ProxyFingerprintSession(proxy_url, impersonate=impersonate)
        if self.session_token:
            self.http.session.cookies.set("__Secure-next-auth.session-token", self.session_token,
                                           domain=".chatgpt.com", path="/")
        self._ready = False

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _headers(self, path: str = "", token: str | None = None) -> dict:
        headers = {"Authorization": "Bearer " + (token or self.access_token),
                   "Accept": "application/json", "Content-Type": "application/json",
                   "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/"}
        if path:
            headers["x-openai-target-path"] = path
            headers["x-openai-target-route"] = path
        if self.workspace_id:
            headers["ChatGPT-Account-ID"] = self.workspace_id
        return headers

    def _request(self, method: str, path: str, *, params=None, body=None,
                 auth_url: str | None = None, token: str | None = None) -> dict:
        response = self.http.request(method, auth_url or self.base_url + path,
                                     headers=self._headers(path, token), params=params, json=body)
        if response.status_code not in (200, 201, 204):
            labels = {401: "凭证未授权或已失效", 403: "远端拒绝当前身份或代理出口",
                      409: "远端状态冲突，请先重新同步", 429: "远端限流，请稍后重试"}
            raise TeamError(f"http_{response.status_code}", labels.get(response.status_code, f"远端 HTTP {response.status_code}"), uncertain=method != "GET")
        if response.status_code == 204 or not response.text.strip():
            return {}
        try:
            value = response.json()
        except ValueError:
            raise TeamError("invalid_response", "远端返回不是 JSON") from None
        if not isinstance(value, dict):
            raise TeamError("invalid_response", "远端响应结构异常")
        return value

    def _ensure_access(self):
        if self._ready or not self.session_token:
            self._ready = True
            return
        path = "/api/auth/session"
        old = self.workspace_id
        self.workspace_id = ""
        try:
            data = self._request("GET", path, params={"exchange_workspace_token": "true", "workspace_id": old,
                                                        "reason": "setCurrentAccountWithoutRedirect"},
                                 auth_url="https://chatgpt.com" + path)
        finally:
            self.workspace_id = old
        user = data.get("user") or {}
        identity = str(user.get("email") or "").lower()
        if identity and identity != self.admin_email:
            raise TeamError("identity_mismatch", "session 身份与管理员邮箱不一致")
        token = str(data.get("accessToken") or data.get("access_token") or "").strip()
        if not token:
            raise TeamError("missing_workspace_token", "session 未返回目标工作区 access token")
        token_wid = _token_account_id(token)
        if old and token_wid and token_wid != old:
            raise TeamError("workspace_token_mismatch", "交换得到的 token 不属于目标工作区")
        self.access_token = token
        self._ready = True

    @staticmethod
    def _seat_type(row: dict) -> str:
        value = row.get("seat_type") or row.get("seatType") or row.get("plan_type") or row.get("plan")
        return str(value).strip().lower() if isinstance(value, str) else ""

    def _paged(self, kind: str) -> list[dict]:
        self._ensure_access(); items=[]; seen=set(); offset=0; total=None
        for _ in range(100):
            data=self._request("GET", f"/accounts/{quote(self.workspace_id, safe='')}/{kind}", params={"offset":offset,"limit":100})
            batch=data.get("items"); total=data.get("total", data.get("total_count", total)); more=data.get("has_more")
            if not isinstance(batch,list) or (total is None and not isinstance(more,bool)):
                raise TeamError("pagination_unknown", "远端列表缺少可靠分页信息")
            for row in batch:
                rid=row.get("id") or row.get("invite_id")
                if not rid or str(rid) in seen: raise TeamError("pagination_changed", "远端列表出现重复记录")
                seen.add(str(rid)); email=str(row.get("email") or row.get("email_address") or "").strip().lower()
                quota=row.get("quota") if isinstance(row.get("quota"),dict) else {}
                items.append({"id":str(rid),"email":email,"role":str(row.get("role") or row.get("account_user_role") or ""),"seat_type":self._seat_type(row),"quota":quota,"quota_exhausted":bool(row.get("quota_exhausted") or quota.get("exhausted")),"created_at":row.get("created_time") or row.get("created_at") or row.get("joined_at")})
            offset += len(batch)
            if (total is not None and offset >= total and more is not True) or (total is None and more is False): return items
            if not batch: raise TeamError("pagination_changed", "远端列表分页发生变化")
        raise TeamError("pagination_limit", "远端列表超过安全分页上限")

    def snapshot(self) -> TeamSnapshot:
        self._ensure_access(); identity=self._request("GET", "/accounts/check/v4-2023-04-27")
        accounts=identity.get("accounts") or {}; detail=accounts.get(self.workspace_id) or {}; account=detail.get("account") or {}
        if not account: raise TeamError("workspace_missing", "管理员凭证未返回目标工作区")
        role=str(account.get("account_user_role") or "")
        if role not in ("account-owner","account-admin"): raise TeamError("not_admin", "当前凭证不是工作区管理员")
        members=[TeamMember(id=x["id"],email=x["email"],role=x["role"],seat_type=x["seat_type"],joined_at=x["created_at"],quota=x["quota"],quota_exhausted=x["quota_exhausted"]) for x in self._paged("users")]
        invites=[TeamInvite(id=x["id"],email=x["email"],seat_type=x["seat_type"],invited_at=x["created_at"]) for x in self._paged("invites")]
        capacity=next((account.get(k) for k in ("seat_capacity","seat_limit","member_limit","max_members","max_users") if isinstance(account.get(k),int)),None)
        return TeamSnapshot(self.workspace_id,str(account.get("name") or ""),str(account.get("plan_type") or ""),role,tuple(members),tuple(invites),capacity,{},time.time())

    def invite(self, email: str, seat_type: str):
        self._ensure_access(); seat_type=seat_type.lower().strip()
        if seat_type not in ("default","prolite"): raise TeamError("seat_type_required", "邀请必须指定 default 或 prolite")
        data=self._request("POST",f"/accounts/{quote(self.workspace_id,safe='')}/invites",body={"email_addresses":[email],"role":"standard-user","resend_emails":True,"seat_type":seat_type})
        if data.get("success") is False or data.get("errored_emails"): raise TeamError("invite_rejected","远端拒绝邀请",uncertain=True)
        return data

    def remove(self, member_id: str):
        self._ensure_access(); return self._request("DELETE",f"/accounts/{quote(self.workspace_id,safe='')}/users/{quote(member_id,safe='')}")

    def revoke_invite(self, email: str):
        self._ensure_access()
        if not email.strip(): raise TeamError("invite_email", "远端邀请缺少邮箱")
        return self._request("DELETE",f"/accounts/{quote(self.workspace_id,safe='')}/invites",body={"email_address":email})


class CandidateClient(TeamClient):
    """Candidate-side invite acceptance using the same proxy/fingerprint."""
    def __init__(self, workspace_id: str, email: str, access_token: str,
                 session_token: str = "", proxy_url: str = "", **kwargs):
        super().__init__(workspace_id, email, access_token, session_token, proxy_url, **kwargs)
        self.email = email.lower().strip()
        self.personal_token = access_token.strip()

    def _candidate_headers(self, path: str) -> dict:
        headers = {"Authorization": "Bearer " + self.personal_token,
                   "Accept": "application/json", "Content-Type": "application/json",
                   "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/",
                   "x-openai-target-path": path, "x-openai-target-route": path}
        account_id = _token_account_id(self.personal_token)
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        return headers

    def _candidate_request(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self.http.request(method, self.base_url + path,
                                     headers=self._candidate_headers(path), json=body)
        if response.status_code not in (200, 201, 204):
            raise TeamError(f"candidate_http_{response.status_code}",
                            f"候补接口 HTTP {response.status_code}", uncertain=method != "GET")
        if response.status_code == 204 or not response.text.strip():
            return {}
        try:
            return response.json()
        except ValueError:
            raise TeamError("invalid_response", "远端返回不是 JSON") from None

    def accept_invite(self, seat_type: str) -> dict:
        seat_type = seat_type.lower().strip()
        if seat_type not in ("default", "prolite"):
            raise TeamError("seat_type_required", "接受邀请必须指定 default 或 prolite")
        self._candidate_request("POST", f"/accounts/{quote(self.workspace_id, safe='')}/invites/accept",
                                {"seat_type": seat_type})
        check = self._candidate_request("GET", "/accounts/check/v4-2023-04-27")
        accounts = check.get("accounts") if isinstance(check, dict) else None
        detail = accounts.get(self.workspace_id) if isinstance(accounts, dict) else None
        account = detail.get("account") if isinstance(detail, dict) else None
        if not isinstance(account, dict) or account.get("account_user_role") != "standard-user":
            raise TeamError("candidate_not_joined", "接受邀请后未读回目标工作区成员身份")
        return {"ok": True, "email": self.email, "workspace_id": self.workspace_id}
