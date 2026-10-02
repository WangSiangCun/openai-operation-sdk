import base64
import json


def token_account_id(token: str) -> str:
    claims = token_claims(token)
    auth = claims.get("https://api.openai.com/auth") if isinstance(claims, dict) else {}
    auth = auth if isinstance(auth, dict) else {}
    return str((auth or {}).get("chatgpt_account_id") or (auth or {}).get("account_id") or "")


def token_claims(token: str) -> dict:
    """Decode non-sensitive JWT claims used to build downstream auth metadata.

    This intentionally does not verify a signature or return the token itself;
    the token is still sent only to the configured remote service.  The claims
    are useful when exporting a CPA file because CPA uses the Codex auth claim
    to determine the subscription tier.
    """
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
