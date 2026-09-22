from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit
from typing import Any


SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


def _split_proxy_values(value: Any) -> list[str]:
    """Split a proxy pool while keeping credentials and URL syntax intact."""
    raw = str(value or "").replace("，", ",").replace("；", ";")
    values: list[str] = []
    for line in raw.splitlines():
        for item in line.replace(";", ",").split(","):
            candidate = item.strip()
            if candidate:
                values.append(candidate)
    return values


def normalize_proxy_url(value: Any, *, label: str = "代理") -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        supported = "、".join(sorted(SUPPORTED_PROXY_SCHEMES))
        raise ValueError(f"{label}仅支持 {supported}，当前为 {parsed.scheme or '未知协议'}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError(f"{label}格式无效：{candidate}")
    return candidate


def browser_proxy_url(value: Any) -> str:
    """Return a proxy URL accepted by Chromium's ``--proxy-server`` flag.

    Requests/PySocks distinguishes ``socks5`` (local DNS resolution) from
    ``socks5h`` (proxy-side DNS resolution). Chromium only accepts the
    ``socks5`` spelling for its command-line proxy server option, so map the
    latter without changing the value used by the protocol authorization
    chain.
    """
    normalized = normalize_proxy_url(value, label="OAuth授权代理")
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() != "socks5h":
        return normalized
    return urlunsplit(("socks5", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def authorization_proxy_pool(settings: dict[str, Any]) -> list[str]:
    """Return validated OAuth/2FA proxy endpoints in configured order."""
    return [
        normalize_proxy_url(value, label="OAuth授权代理")
        for value in _split_proxy_values(settings.get("auth_proxy"))
    ]


def authorization_proxy(settings: dict[str, Any], index: int = 0) -> str:
    """Return one dedicated OAuth/2FA proxy.

    Batch authorization passes the complete pool to the authorization worker.
    Single-account flows use a stable endpoint (the first configured entry)
    so a refresh or callback retry does not silently switch egress.
    """
    pool = authorization_proxy_pool(settings)
    return pool[int(index) % len(pool)] if pool else ""


def authorization_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Build auth-tool settings without inheriting Sub2API account proxies."""
    prepared = deepcopy(settings)
    prepared["http_proxy"] = authorization_proxy(settings)
    return prepared
