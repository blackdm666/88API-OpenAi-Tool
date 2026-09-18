from __future__ import annotations
import time
from typing import Any
from .constants import DEFAULT_OAUTH_CLIENT_ID
from .utils import decode_jwt, get_auth_claims


def _decode_exp_timestamp(access_token):
    value = decode_jwt(access_token).get('exp')
    return int(value) if isinstance(value, int) else 0


def _parse_group_ids(raw: Any) -> list[int]:
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    elif raw is None:
        items = []
    else:
        items = [raw]
    values: list[int] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            values.append(int(text))
        except ValueError:
            continue
    return values or [2]


def to_sub2api_payload(record: dict[str, Any], group_ids: Any = None) -> dict[str, Any]:
    access_token = str(record.get("access_token") or "")
    refresh_token = str(record.get("refresh_token") or "")
    id_token = str(record.get("id_token") or "")
    email = str(record.get("email") or "")
    access_auth = get_auth_claims(decode_jwt(access_token))
    id_auth = get_auth_claims(decode_jwt(id_token))
    organization_id = str(
        id_auth.get("organization_id")
        or access_auth.get("organization_id")
        or ""
    ).strip()
    expires_at = _decode_exp_timestamp(access_token) or int(time.time()) + 863999
    client_id = str(record.get("client_id") or DEFAULT_OAUTH_CLIENT_ID).strip() or DEFAULT_OAUTH_CLIENT_ID
    return {
        "name": email,
        "notes": "",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 863999,
            "expires_at": expires_at,
            "chatgpt_account_id": str(
                access_auth.get("chatgpt_account_id") or record.get("account_id") or ""
            ).strip(),
            "chatgpt_user_id": str(access_auth.get("chatgpt_user_id") or "").strip(),
            "organization_id": organization_id,
            "client_id": client_id,
            "id_token": id_token,
        },
        "extra": {"email": email},
        "group_ids": _parse_group_ids(group_ids),
        "concurrency": 10,
        "priority": 1,
        "auto_pause_on_expired": True,
    }


def from_local_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": str(payload.get("email") or "").strip(),
        "access_token": str(payload.get("access_token") or "").strip(),
        "refresh_token": str(payload.get("refresh_token") or "").strip(),
        "id_token": str(payload.get("id_token") or "").strip(),
        "account_id": str(payload.get("account_id") or "").strip(),
        "expired": str(payload.get("expired") or "").strip(),
        "last_refresh": str(payload.get("last_refresh") or "").strip(),
        "type": str(payload.get("type") or "codex"),
    }


def from_sub2api_payload(payload: dict[str, Any]) -> dict[str, Any]:
    credentials = payload.get("credentials") or {}
    extra = payload.get("extra") or {}
    return {
        "email": str(extra.get("email") or payload.get("name") or "").strip(),
        "access_token": str(credentials.get("access_token") or "").strip(),
        "refresh_token": str(credentials.get("refresh_token") or "").strip(),
        "id_token": str(credentials.get("id_token") or "").strip(),
        "account_id": str(credentials.get("chatgpt_account_id") or "").strip(),
        "type": "codex",
        "metadata": {
            "imported_from": "sub2api",
            "sub2api_group_ids": payload.get("group_ids") or [],
            "sub2api_concurrency": payload.get("concurrency"),
            "sub2api_priority": payload.get("priority"),
        },
    }
