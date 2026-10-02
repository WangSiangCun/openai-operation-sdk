from urllib.parse import urlsplit
import requests
from .errors import TeamError

try:
    from curl_cffi.requests import Session as CffiSession
    from curl_cffi.requests.exceptions import RequestException as CffiRequestException
except ImportError:  # pragma: no cover
    CffiSession = None
    CffiRequestException = requests.RequestException


def normalize_proxy(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise TeamError("proxy_required", "必须提供代理；未发起请求")
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlsplit(raw)
        if (parsed.scheme.lower() not in ("http", "https", "socks4", "socks5", "socks5h")
                or not parsed.hostname or parsed.port is None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or any(ch.isspace() for ch in raw)):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise TeamError("proxy_invalid", "代理格式无效") from None
    return raw


class ProxyFingerprintSession:
    """One immutable proxy + browser fingerprint for the whole client lifetime."""
    def __init__(self, proxy_url: str, *, impersonate: str = "safari18_0"):
        self.proxy_url = normalize_proxy(proxy_url)
        self.impersonate = impersonate
        if CffiSession is None:
            raise TeamError("fingerprint_unavailable", "浏览器指纹组件未安装；未发起请求")
        self.session = CffiSession(impersonate=impersonate)
        self.session.trust_env = False
        proxy = self.proxy_url.replace("socks5://", "socks5h://", 1)
        self.session.proxies = {"http": proxy, "https": proxy}
        self._proxies = dict(self.session.proxies)

    def request(self, method: str, url: str, *, headers=None, params=None,
                json=None, json_body=None, files=None, **kwargs):
        if self.session.trust_env or dict(self.session.proxies) != self._proxies:
            raise TeamError("proxy_changed", "请求代理或环境代理已改变，已停止")
        try:
            follow_redirects = kwargs.pop("allow_redirects", getattr(self, "follow_redirects", False))
            return self.session.request(method, url, headers=headers, params=params,
                                        json=json if json is not None else json_body,
                                        files=files, timeout=(10, 30),
                                        allow_redirects=follow_redirects, **kwargs)
        except (requests.RequestException, CffiRequestException):
            raise TeamError("network", "代理请求失败；没有回退直连") from None

    def close(self):
        self.session.close()
