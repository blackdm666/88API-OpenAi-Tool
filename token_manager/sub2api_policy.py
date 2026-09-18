"""Account identity, upload options and conservative recovery classification."""

from __future__ import annotations

import math
import re
from random import SystemRandom
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def normalize_server_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        raise ValueError("请先填写 Sub2API 地址")
    if "://" not in value:
        value = "https://" + value
    p = urlsplit(value)
    if (
        p.scheme not in ("https", "http")
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
    ):
        raise ValueError(
            "Sub2API 地址必须是有效的 HTTP/HTTPS 地址，不能包含密码、查询参数或锚点"
        )
    if any(c.isspace() for c in value):
        raise ValueError("Sub2API 地址不能包含空格")
    try:
        p.port
    except ValueError as exc:
        raise ValueError("Sub2API 地址端口无效") from exc
    # Users commonly paste the API root or the login URL into the server field.
    path = p.path.rstrip("/")
    for suffix in ("/api/v1/auth/login", "/api/v1/admin", "/api/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


def proxy_candidates(config: dict[str, Any]) -> list[int]:
    """Keep compatibility with the old scalar proxy_id config; API stays scalar."""
    raw = config.get('proxy_id')
    if raw is None or raw == '' or raw == 0 or raw == '0':
        return []
    parts = raw if isinstance(raw, list) else str(raw).replace('，', ',').split(',')
    ids = []
    for part in parts:
        text = str(part).strip()
        if not re.fullmatch(r'[0-9]+', text) or int(text) <= 0:
            raise ValueError('代理ID须为正整数，以逗号分隔；直连请单独填0或留空，不能混入代理池')
        if int(text) not in ids:
            ids.append(int(text))
    return ids


def default_list_group_ids(value='2') -> set[int]:
    parts = value if isinstance(value, list) else str(value or '').replace('，', ',').split(',')
    result = set()
    for part in parts:
        text = str(part).strip()
        if not text:
            continue
        if not text.isascii() or not text.isdigit() or int(text) < 1:
            raise ValueError('默认列表分组须为正整数ID，多个用逗号分隔；留空显示全部')
        result.add(int(text))
    return result


def assigned_proxy(record, config, current_proxy=None):
    candidates = proxy_candidates(config)
    endpoint = config.get('api_url', '')
    server = normalize_server_url(endpoint) if endpoint else ''
    saved = record.get('sub2api_proxy_assignment') or {}
    if not candidates:
        chosen = None
    elif current_proxy in candidates:
        chosen = current_proxy
    elif saved.get('server') == server and saved.get('proxy_id') in candidates:
        chosen = saved['proxy_id']
    else:
        chosen = SystemRandom().choice(candidates)
    record['sub2api_proxy_assignment'] = {'server': server, 'proxy_id': chosen}
    return chosen


def upload_options(config: dict[str, Any]) -> dict[str, Any]:
    def integer(key, default, minimum, maximum):
        raw = config.get(key, default)
        if isinstance(raw, bool) or not re.fullmatch(r"\d+", str(raw).strip()):
            raise ValueError(f"{key} 必须是整数")
        result = int(raw)
        if not minimum <= result <= maximum:
            raise ValueError(f"{key} 范围为 {minimum}–{maximum}")
        return result

    raw_groups = config.get("group_ids", "2")
    values = (
        raw_groups
        if isinstance(raw_groups, list)
        else str(raw_groups).replace("，", ",").split(",")
    )
    groups = []
    for value in values:
        if not str(value).strip():
            continue
        if not str(value).strip().isdigit() or int(value) < 1:
            raise ValueError("分组必须填写有效的正整数 ID，多个分组用逗号分隔")
        if int(value) not in groups:
            groups.append(int(value))
    if not groups:
        raise ValueError("请至少选择一个上传分组")
    rate = float(config.get("rate_multiplier", 1))
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("倍率必须是大于或等于0的有限数字")
    mode = str(config.get("codex_fingerprint_mode", "off"))
    if mode not in ("off", "device", "session", "full"):
        raise ValueError("设备指纹模式无效")
    ws_mode = str(config.get('ws_mode', 'ctx_pool'))
    if ws_mode not in ('off', 'ctx_pool', 'passthrough', 'http_bridge'):
        raise ValueError('WS mode 无效')
    proxies = proxy_candidates(config)
    return {
        "group_ids": groups,
        "concurrency": integer("concurrency", 10, 1, 10000),
        "priority": integer("priority", 1, 0, 100000),
        "rate_multiplier": rate,
        "proxy_id": proxies[0] if len(proxies) == 1 else None,
        "auto_pause_on_expired": bool(config.get("auto_pause_on_expired", True)),
        "extra": {
            "codex_fingerprint_mode": mode,
            "openai_oauth_responses_websockets_v2_mode": ws_mode,
            "openai_oauth_responses_websockets_v2_enabled": ws_mode != 'off',
        },
    }


def account_identity(record: dict[str, Any]) -> tuple[str, str]:
    credentials = record.get("credentials") or {}
    extra = record.get("extra") or {}
    email = (
        str(
            record.get("email")
            or credentials.get("email")
            or extra.get("email")
            or record.get("name")
            or ""
        )
        .strip()
        .lower()
    )
    account_id = str(
        record.get("account_id") or credentials.get("chatgpt_account_id") or ""
    ).strip()
    return email, account_id


def match_remote(
    local: dict[str, Any], remotes: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Require one OAuth owner; never choose an arbitrary same-email workspace."""
    email, identity = account_identity(local)
    matches = []
    same_email = []
    for remote in remotes:
        if (
            remote.get("platform") != "openai"
            or remote.get("type") != "oauth"
            or remote.get("parent_account_id")
        ):
            continue
        other_email, other_id = account_identity(remote)
        if not email or email != other_email:
            continue
        same_email.append(remote)
        if identity and other_id and identity != other_id:
            continue
        matches.append(remote)
    if len(matches) > 1:
        raise ValueError(
            "同邮箱存在多个远端账号，请先消除重复或核对工作区；已跳过自动写入"
        )
    if not matches:
        if same_email:
            raise ValueError("同邮箱远端账号工作区不一致，请核对身份；不会新建重复账号")
        return None
    remote = matches[0]
    binding = (local.get("sub2api_recovery") or {}).get("remote_id")
    if binding and int(binding) != int(remote["id"]):
        raise ValueError("远端账号绑定已变化，请重新开启该账号的监控")
    return remote


def auth_failure_kind(remote: dict[str, Any]) -> str:
    """Return permanent / refreshable / empty. A generic error is not a 401."""
    if remote.get("status") == "inactive":
        return ""
    msg = str(remote.get("error_message") or "").lower()
    # Temporary cooldown is left to Sub2API's own refresh worker.
    if remote.get("status") != "error":
        return ""
    if any(
        code in msg
        for code in (
            "token_invalidated",
            "token_revoked",
            "token revoked",
            "token invalidated",
            "token has been invalidated",
            "refresh_token missing",
            "invalid_grant",
            "refresh_token_reused",
            "authentication failed permanently",
        )
    ):
        return "permanent"
    return "refreshable" if re.search(r"\b401\b", msg) else ""


def redact_error(value: Any) -> str:
    text = str(value)
    text = re.sub(
        r'(?i)(Bearer\s+|(?:access_token|refresh_token|id_token|api_key)[\s"\x27:=]+)[^\s,}"\x27]+',
        r"\1[已隐藏]",
        text,
    )
    text = re.sub(
        r"\b(?:eyJ[A-Za-z0-9_.-]+|admin-[a-fA-F0-9]+|sk-[A-Za-z0-9_-]+)\b",
        "[已隐藏]",
        text,
    )
    return text[:400]
