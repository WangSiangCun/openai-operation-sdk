"""TOTP generation; secrets stay in caller-owned memory and storage."""
import base64
import hashlib
import hmac
import struct
import time


def generate_totp(secret: str, *, at_time: float | None = None) -> str:
    raw = secret.replace(" ", "").upper().rstrip("=")
    key = base64.b32decode(raw + "=" * (-len(raw) % 8))
    instant = time.time() if at_time is None else at_time
    digest = hmac.new(key, struct.pack(">Q", int(instant) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset:offset+4])[0] & 0x7fffffff) % 1000000:06d}"
