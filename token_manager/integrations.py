from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .converters import to_sub2api_payload
from .sub2api_policy import normalize_server_url, upload_options, match_remote, redact_error, assigned_proxy
from .utils import build_requests_proxies, now_rfc3339, now_ts, safe_int


def _response_error(response: requests.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("message") or data.get("msg") or data.get("error") or "").strip()
    except Exception:
        pass
    return response.text[:300].strip() or f"HTTP {response.status_code}"












def _sub2api_settings(settings: dict[str, Any]) -> dict[str, Any]:
    integrations = settings.get("integrations")
    if not isinstance(integrations, dict):
        return {}
    sub2api = integrations.get("sub2api")
    if not isinstance(sub2api, dict):
        return {}
    return sub2api


_sub2api_auth_lock = threading.Lock()


def _sub2api_api_url(settings: dict[str, Any]) -> str:
    return normalize_server_url(_sub2api_settings(settings).get("api_url", ""))


def _sub2api_api_key(settings: dict[str, Any]) -> str:
    return str(_sub2api_settings(settings).get("api_key") or "").strip()


def _sub2api_admin_email(settings: dict[str, Any]) -> str:
    return str(_sub2api_settings(settings).get("admin_email") or "").strip()


def _sub2api_admin_password(settings: dict[str, Any]) -> str:
    return str(_sub2api_settings(settings).get("admin_password") or "").strip()


def _sub2api_access_token(settings: dict[str, Any]) -> str:
    return str(_sub2api_settings(settings).get("access_token") or "").strip()


def _sub2api_refresh_token(settings: dict[str, Any]) -> str:
    return str(_sub2api_settings(settings).get("refresh_token") or "").strip()


def _sub2api_token_expires_at(settings: dict[str, Any]) -> int:
    try:
        return int(_sub2api_settings(settings).get("token_expires_at") or 0)
    except Exception:
        return 0


def _sub2api_base_headers(settings: dict[str, Any], *, token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
    }
    auth_token = str(token or "").strip()
    api_key = _sub2api_api_key(settings)
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key:
        if api_key.startswith('admin-'):
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _sub2api_public_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
    }


def _sub2api_is_session_expired(settings: dict[str, Any]) -> bool:
    expires_at = _sub2api_token_expires_at(settings)
    return bool(expires_at and expires_at <= now_ts() + 30)


def _set_sub2api_session(settings: dict[str, Any], data: dict[str, Any]) -> None:
    integrations = settings.get("integrations")
    if not isinstance(integrations, dict):
        return
    sub2api = integrations.get("sub2api")
    if not isinstance(sub2api, dict):
        return
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or sub2api.get("refresh_token") or "").strip()
    expires_in = int(data.get("expires_in") or 0)
    sub2api["access_token"] = access_token
    sub2api["refresh_token"] = refresh_token
    sub2api["token_expires_at"] = now_ts() + max(60, expires_in - 30) if access_token and expires_in > 0 else 0


def _sub2api_response_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _sub2api_response_data(response: requests.Response) -> Any:
    body = _sub2api_response_json(response)
    if isinstance(body, dict) and "code" in body:
        if int(body.get("code") or 0) != 0:
            raise RuntimeError(str(body.get("message") or body.get("error") or "Sub2API 请求失败").strip())
        return body.get("data")
    return body


def _sub2api_datetime_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        raw = int(value)
    except Exception:
        return str(value or "").strip()
    if raw <= 0:
        return ""
    if raw > 10_000_000_000:
        raw = raw // 1000
    dt = datetime.fromtimestamp(raw, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def login_sub2api_admin(settings: dict[str, Any], *, proxy_url: str = "") -> dict[str, Any]:
    email = _sub2api_admin_email(settings)
    password = _sub2api_admin_password(settings)
    if not email or not password:
        raise RuntimeError("Sub2API 管理邮箱或密码未配置")
    if '@' not in email or email.startswith('admin-'):
        raise ValueError('请填写完整的管理员邮箱；管理 API Key 请填到 API Key 栏并选择该鉴权方式')
    response = requests.post(
        f"{_sub2api_api_url(settings)}/api/v1/auth/login",
        headers={
            "Content-Type": "application/json",
            **_sub2api_public_headers(),
        },
        json={"email": email, "password": password},
        timeout=30,
        verify=True,
        proxies=build_requests_proxies(proxy_url),
    )
    if response.status_code != 200:
        raise RuntimeError(_response_error(response))
    data = _sub2api_response_data(response)
    if not isinstance(data, dict):
        raise RuntimeError("Sub2API 登录结果异常")
    _set_sub2api_session(settings, data)
    return data


def refresh_sub2api_admin_session(settings: dict[str, Any], *, proxy_url: str = "") -> dict[str, Any]:
    refresh_token = _sub2api_refresh_token(settings)
    if not refresh_token:
        raise RuntimeError("Sub2API Refresh Token 未配置")
    response = requests.post(
        f"{_sub2api_api_url(settings)}/api/v1/auth/refresh",
        headers={
            "Content-Type": "application/json",
            **_sub2api_public_headers(),
        },
        json={"refresh_token": refresh_token},
        timeout=30,
        verify=True,
        proxies=build_requests_proxies(proxy_url),
    )
    if response.status_code != 200:
        raise RuntimeError(_response_error(response))
    data = _sub2api_response_data(response)
    if not isinstance(data, dict):
        raise RuntimeError("Sub2API 刷新登录态结果异常")
    _set_sub2api_session(settings, data)
    return data


def _ensure_sub2api_auth(settings: dict[str, Any], *, proxy_url: str = "") -> str:
    mode = _sub2api_settings(settings).get('auth_mode', 'auto')
    if mode == 'api_key' or (mode == 'auto' and _sub2api_api_key(settings)):
        if not _sub2api_api_key(settings):
            raise ValueError('请填写 Sub2API 管理 API Key')
        return ''
    with _sub2api_auth_lock:
        access_token = _sub2api_access_token(settings)
        if access_token and not _sub2api_is_session_expired(settings):
            return access_token
        if access_token and _sub2api_refresh_token(settings):
            try:
                refresh_sub2api_admin_session(settings, proxy_url=proxy_url)
                return _sub2api_access_token(settings)
            except Exception:
                pass
        if _sub2api_admin_email(settings) and _sub2api_admin_password(settings):
            login_sub2api_admin(settings, proxy_url=proxy_url)
            return _sub2api_access_token(settings)
        if mode == "password":
            raise ValueError("请填写 Sub2API 管理邮箱和密码")
        raise ValueError("请配置 Sub2API 管理 API Key 或邮箱密码")


def _sub2api_request(
    settings: dict[str, Any],
    method: str,
    path: str,
    *,
    proxy_url: str = "",
    require_auth: bool = True,
    **kwargs,
) -> requests.Response:
    api_url = _sub2api_api_url(settings)
    token = _ensure_sub2api_auth(settings, proxy_url=proxy_url) if require_auth else ""
    extra_headers = dict(kwargs.pop("headers", {}) or {})
    request_timeout = kwargs.pop('timeout', 30)
    retry_headers = dict(kwargs.pop("retry_headers", {}) or {})
    headers = {
        **_sub2api_base_headers(settings, token=token),
        **extra_headers,
    }
    response = requests.request(
        method.upper(),
        f"{api_url}{path}",
        headers=headers,
        timeout=request_timeout,
        verify=True,
        proxies=build_requests_proxies(proxy_url),
        **kwargs,
    )
    if response.status_code != 401 or not require_auth:
        return response
    mode = _sub2api_settings(settings).get('auth_mode', 'auto')
    if mode == 'api_key' or (mode == 'auto' and _sub2api_api_key(settings)):
        return response  # Never fall back to an unrelated saved login.

    refreshed = False
    if _sub2api_refresh_token(settings):
        try:
            refresh_sub2api_admin_session(settings, proxy_url=proxy_url)
            refreshed = True
        except Exception:
            refreshed = False
    if not refreshed and _sub2api_admin_email(settings) and _sub2api_admin_password(settings):
        login_sub2api_admin(settings, proxy_url=proxy_url)
        refreshed = True
    if not refreshed:
        return response

    retry_headers = {
        **_sub2api_base_headers(settings, token=_sub2api_access_token(settings)),
        **extra_headers,
        **retry_headers,
    }
    return requests.request(
        method.upper(),
        f"{api_url}{path}",
        headers=retry_headers,
        timeout=request_timeout,
        verify=True,
        proxies=build_requests_proxies(proxy_url),
        **kwargs,
    )


def fetch_sub2api_groups(settings: dict[str, Any], *, proxy_url: str = "") -> list[dict[str, Any]]:
    response = _sub2api_request(settings, "GET", "/api/v1/admin/groups/all", proxy_url=proxy_url)
    if response.status_code != 200:
        raise RuntimeError(_response_error(response))
    data = _sub2api_response_data(response)
    items = data if isinstance(data, list) else []
    groups: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        groups.append(
            {
                "id": safe_int(item.get("id")),
                "name": str(item.get("name") or "").strip(),
                "platform": str(item.get("platform") or "").strip(),
                "status": str(item.get("status") or "").strip(),
            }
        )
    groups.sort(key=lambda item: (item.get("platform") or "", item.get("name") or ""))
    return groups


def fetch_sub2api_accounts(
    settings: dict[str, Any],
    *,
    proxy_url: str = "",
    filters: dict[str, Any] | None = None,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    query_filters = {key: value for key, value in dict(filters or {}).items() if str(value or "").strip()}
    page = 1
    pages = 1
    records: list[dict[str, Any]] = []
    group_name_by_id: dict[int, str] = {}
    while page <= pages:
        response = _sub2api_request(
            settings,
            "GET",
            "/api/v1/admin/accounts",
            proxy_url=proxy_url,
            params={
                "page": page,
                "page_size": page_size,
                "sort_by": "id",
                "sort_order": "asc",
                **query_filters,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(_response_error(response))
        data = _sub2api_response_data(response)
        items = data.get("items") if isinstance(data, dict) else []
        pages = max(1, int(data.get("pages") or ((int(data.get('total') or 0) + page_size - 1) // page_size) or 1)) if isinstance(data, dict) else 1
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            credentials = item.get("credentials") or {}
            if not isinstance(credentials, dict):
                credentials = {}
            extra = item.get("extra") or {}
            if not isinstance(extra, dict):
                extra = {}
            groups = item.get("groups") or []
            if not isinstance(groups, list):
                groups = []
            group_ids = item.get("group_ids") or []
            if not isinstance(group_ids, list):
                group_ids = []
            group_names: list[str] = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                try:
                    group_id = safe_int(group.get("id"))
                except Exception:
                    group_id = 0
                group_name = str(group.get("name") or "").strip()
                if group_id > 0 and group_name:
                    group_name_by_id[group_id] = group_name
                if group_name:
                    group_names.append(group_name)
            if not group_names:
                group_names = [group_name_by_id.get(safe_int(group_id), f"#{safe_int(group_id)}") for group_id in group_ids if safe_int(group_id) > 0]
            email = str(credentials.get("email") or extra.get("email") or item.get("name") or "").strip()
            records.append(
                {
                    "id": safe_int(item.get("id")),
                    "name": str(item.get("name") or "").strip(),
                    "email": email,
                    "platform": str(item.get("platform") or "").strip(),
                    "type": str(item.get("type") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "error_message": str(item.get("error_message") or "").strip(),
                    "group_ids": [safe_int(group_id) for group_id in group_ids if safe_int(group_id) > 0],
                    "group_names": group_names,
                    "concurrency": safe_int(item.get("concurrency")),
                    "current_concurrency": item.get("current_concurrency"),
                    "active_sessions": item.get("active_sessions"),
                    "current_rpm": item.get("current_rpm"),
                    "priority": safe_int(item.get("priority")),
                    "rate_multiplier": item.get('rate_multiplier', 1),
                    "parent_account_id": item.get('parent_account_id'),
                    "schedulable": item.get("schedulable"),
                    "proxy_id": item.get("proxy_id"),
                    "last_used_at": str(item.get("last_used_at") or "").strip(),
                    "expires_at": item.get("expires_at"),
                    "expires_at_text": _sub2api_datetime_text(item.get("expires_at")),
                    "rate_limited_at": str(item.get("rate_limited_at") or "").strip(),
                    "rate_limit_reset_at": str(item.get("rate_limit_reset_at") or "").strip(),
                    "overload_until": str(item.get('overload_until') or '').strip(),
                    "temp_unschedulable_until": str(item.get("temp_unschedulable_until") or "").strip(),
                    "temp_unschedulable_reason": str(item.get('temp_unschedulable_reason') or ''),
                    "auto_pause_on_expired": bool(item.get("auto_pause_on_expired", False)),
                    "credentials": credentials,
                    "credentials_status": item.get('credentials_status') or {},
                    "extra": extra,
                    "groups": groups,
                    "proxy": item.get("proxy") or {},
                }
            )
        page += 1
    return records


def fetch_sub2api_concurrency_snapshot(
    settings: dict[str, Any], *, proxy_url: str = "", filters: dict[str, Any] | None = None,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Read the lightweight live concurrency counters from Sub2API."""
    query_filters = {key: value for key, value in dict(filters or {}).items() if str(value or "").strip()}
    page, pages, records = 1, 1, []
    while page <= pages:
        response = _sub2api_request(
            settings, "GET", "/api/v1/admin/accounts", proxy_url=proxy_url,
            params={"page": page, "page_size": page_size, "lite": "1", "sort_by": "id", "sort_order": "asc", **query_filters},
        )
        if response.status_code != 200:
            raise RuntimeError(_response_error(response))
        data = _sub2api_response_data(response)
        items = data.get("items") if isinstance(data, dict) else []
        pages = max(1, int(data.get("pages") or ((int(data.get("total") or 0) + page_size - 1) // page_size) or 1)) if isinstance(data, dict) else 1
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                records.append({
                    "id": safe_int(item.get("id")),
                    "status": str(item.get("status") or "").strip(),
                    "schedulable": item.get("schedulable"),
                    "concurrency": safe_int(item.get("concurrency")),
                    "current_concurrency": item.get("current_concurrency"),
                    "active_sessions": item.get("active_sessions"),
                    "current_rpm": item.get("current_rpm"),
                    "temp_unschedulable_until": str(item.get("temp_unschedulable_until") or "").strip(),
                    "rate_limit_reset_at": str(item.get("rate_limit_reset_at") or "").strip(),
                    "overload_until": str(item.get("overload_until") or "").strip(),
                })
        page += 1
    return records


def fetch_sub2api_usage(settings, account_ids, *, proxy_url=''):
    ids = list(dict.fromkeys(int(i) for i in account_ids if int(i) > 0))
    if len(ids) > 50:
        raise ValueError('每次最多更新50个账号的用量，请缩小筛选或选择账号')
    result = {'usage': {}, 'errors': {}}
    for start in range(0, len(ids), 20):
        batch = ids[start:start+20]
        response = _sub2api_request(settings, 'POST', '/api/v1/admin/accounts/usage/batch', proxy_url=proxy_url,
                                   json={'account_ids': batch, 'force': False})
        if response.status_code != 200:
            raise RuntimeError(redact_error(_response_error(response)))
        data = _sub2api_response_data(response)
        result['usage'].update(data.get('usage') or {})
        result['errors'].update({str(k): redact_error(v) for k,v in (data.get('errors') or {}).items()})
    return result


def refresh_sub2api_accounts(
    settings: dict[str, Any],
    account_ids: list[int],
    *,
    proxy_url: str = "",
) -> dict[str, Any]:
    response = _sub2api_request(
        settings,
        "POST",
        "/api/v1/admin/accounts/batch-refresh",
        proxy_url=proxy_url,
        headers={"Content-Type": "application/json"},
        json={"account_ids": [safe_int(account_id) for account_id in account_ids if safe_int(account_id) > 0]},
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(_response_error(response))
    data = _sub2api_response_data(response)
    return data if isinstance(data, dict) else {}


def bulk_update_sub2api_accounts(
    settings: dict[str, Any],
    account_ids: list[int],
    updates: dict[str, Any],
    *,
    proxy_url: str = "",
) -> dict[str, Any]:
    payload = {"account_ids": [safe_int(account_id) for account_id in account_ids if safe_int(account_id) > 0], **dict(updates or {})}
    response = _sub2api_request(
        settings,
        "POST",
        "/api/v1/admin/accounts/bulk-update",
        proxy_url=proxy_url,
        headers={"Content-Type": "application/json"},
        json=payload,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(_response_error(response))
    data = _sub2api_response_data(response)
    return data if isinstance(data, dict) else {}


def delete_sub2api_account(
    settings: dict[str, Any],
    account_id: int,
    *,
    proxy_url: str = "",
) -> tuple[bool, str]:
    response = _sub2api_request(
        settings,
        "DELETE",
        f"/api/v1/admin/accounts/{safe_int(account_id)}",
        proxy_url=proxy_url,
    )
    if response.status_code in (200, 201):
        try:
            data = _sub2api_response_data(response)
            if isinstance(data, dict):
                return True, str(data.get("message") or "删除成功")
        except Exception:
            pass
        return True, "删除成功"
    return False, _response_error(response)




















def upload_to_sub2api(record: dict[str, Any], settings: dict[str, Any], proxy_url: str = "") -> tuple[bool, str]:
    try:
        config = _sub2api_settings(settings)
        upload_options(config)  # Validate all settings before any remote write.
        matches = fetch_sub2api_accounts(settings, proxy_url=proxy_url, filters={'platform': 'openai', 'search': record.get('email', '')})
        existing = match_remote(record, matches)
        if existing:
            verified = apply_sub2api_credentials(record, existing, settings, proxy_url=proxy_url)
            # Manual upload explicitly applies the user's saved upload options.
            options = upload_options(config)
            options['proxy_id'] = assigned_proxy(record, config, verified.get('proxy_id'))
            options['extra'] = {**(verified.get('extra') or {}), **options['extra']}
            options['proxy_id'] = options['proxy_id'] or 0  # official update API uses 0 to detach
            response = _sub2api_request(settings, 'PUT', f"/api/v1/admin/accounts/{existing['id']}", proxy_url=proxy_url, json=options)
            if response.status_code != 200:
                return False, '凭据已同步，但参数更新失败：' + redact_error(_response_error(response))
            _sub2api_response_data(response)
            return True, f"已更新远端账号 #{existing['id']}，代理 #{options['proxy_id']}" if options['proxy_id'] else f"已更新远端账号 #{existing['id']}，直连"
        payload = sub2api_upload_payload(record, settings)
        response = _sub2api_request(
            settings,
            "POST",
            "/api/v1/admin/accounts",
            proxy_url=proxy_url,
            headers={
                "Content-Type": "application/json",
                "Referer": f"{_sub2api_api_url(settings)}/admin/accounts",
            },
            json=payload,
        )
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        return False, redact_error(exc)
    if response.status_code in (200, 201):
        try:
            _sub2api_response_data(response)
        except Exception as exc:
            return False, str(exc)
        return True, f"上传成功，代理 #{payload['proxy_id']}" if payload.get('proxy_id') else "上传成功，直连"
    return False, _response_error(response)


def sub2api_upload_payload(record, settings):
    options = upload_options(_sub2api_settings(settings))
    options['proxy_id'] = assigned_proxy(record, _sub2api_settings(settings))
    payload = to_sub2api_payload(record, group_ids=options['group_ids'])
    payload['extra'].update(options.pop('extra'))
    payload.update(options)
    return payload


def get_sub2api_account(settings, account_id, *, proxy_url=''):
    response = _sub2api_request(settings, 'GET', f'/api/v1/admin/accounts/{int(account_id)}', proxy_url=proxy_url)
    if response.status_code != 200:
        raise RuntimeError(redact_error(_response_error(response)))
    data = _sub2api_response_data(response)
    if not isinstance(data, dict):
        raise RuntimeError('远端账号详情格式异常')
    return data


def get_sub2api_account_credentials(settings, account_id, *, proxy_url=''):
    """Use the official ID-scoped export when normal DTOs hide OAuth tokens.

    Exported data stays in memory and never includes proxy credentials.
    """
    current = get_sub2api_account(settings, account_id, proxy_url=proxy_url)
    if (current.get('credentials') or {}).get('access_token'):
        return current
    response = _sub2api_request(settings, 'GET', '/api/v1/admin/accounts/data',
                               proxy_url=proxy_url, params={'ids': str(int(account_id)), 'include_proxies': 'false'})
    if response.status_code != 200:
        raise RuntimeError(f'官方凭据读取接口 HTTP {response.status_code}，无法安全核验；请检查导出权限')
    data = _sub2api_response_data(response)
    accounts = data.get('accounts', []) if isinstance(data, dict) else []
    if len(accounts) != 1:
        raise ValueError('指定账号凭据导出不唯一，停止恢复')
    exported = accounts[0]
    # Export records do not carry IDs. The request selects exactly one ID and
    # its returned owner/workspace must independently match the detail record.
    identity = {'email': (exported.get('credentials') or {}).get('email') or (exported.get('extra') or {}).get('email') or exported.get('name'),
                'account_id': (exported.get('credentials') or {}).get('chatgpt_account_id')}
    if exported.get('platform') != current.get('platform') or exported.get('type') != current.get('type') or not match_remote(identity, [current]):
        raise ValueError('导出凭据的身份与目标账号不一致')
    credentials = exported.get('credentials') or {}
    if not credentials.get('access_token'):
        raise ValueError('官方导出未返回OAuth凭据，停止恢复')
    return {**current, 'credentials': credentials}


def set_sub2api_schedulable(settings, account_id, enabled=True, *, proxy_url=''):
    """Set persistent scheduler participation and verify the read-back."""
    response = _sub2api_request(
        settings, 'POST', f'/api/v1/admin/accounts/{int(account_id)}/schedulable',
        proxy_url=proxy_url, json={'schedulable': bool(enabled)},
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(redact_error(_response_error(response)))
    _sub2api_response_data(response)
    verified = get_sub2api_account_credentials(settings, account_id, proxy_url=proxy_url)
    if verified.get('schedulable') is not bool(enabled):
        raise RuntimeError('调度开关已提交，但远端读回校验不通过')
    return verified


def apply_sub2api_credentials(local, remote, settings, *, proxy_url=''):
    # Re-read just before updating: don't reactivate a manually paused account,
    # overwrite a rotated refresh token, or write to a changed workspace.
    current = get_sub2api_account_credentials(settings, remote['id'], proxy_url=proxy_url)
    if not match_remote(local, [current]) or current.get('status') == 'inactive':
        raise ValueError('账号身份或启用状态已变化，停止同步')
    old_rt = (remote.get('credentials') or {}).get('refresh_token')
    if old_rt and (current.get('credentials') or {}).get('refresh_token') != old_rt:
        raise ValueError('远端凭据已被其他任务刷新，下一轮重新检查')
    tokens = to_sub2api_payload(local)['credentials']
    credentials = dict(current.get('credentials') or {})
    # Optional token claims must not erase valid remote workspace metadata.
    credentials.update({k: v for k, v in tokens.items() if v not in ('', None)})
    response = _sub2api_request(settings, 'POST', f"/api/v1/admin/accounts/{remote['id']}/apply-oauth-credentials",
                               proxy_url=proxy_url, json={'type': 'oauth', 'credentials': credentials})
    if response.status_code != 200:
        raise RuntimeError(redact_error(_response_error(response)))
    _sub2api_response_data(response)
    verified = get_sub2api_account_credentials(settings, remote['id'], proxy_url=proxy_url)
    if verified.get('status') != 'active' or (verified.get('credentials') or {}).get('access_token') != local.get('access_token'):
        raise RuntimeError('凭据已提交，但远端读回校验不通过')
    return verified


def fetch_sub2api_proxies(settings, *, proxy_url=''):
    response = _sub2api_request(settings, 'GET', '/api/v1/admin/proxies/all', proxy_url=proxy_url)
    if response.status_code != 200:
        raise RuntimeError(redact_error(_response_error(response)))
    data = _sub2api_response_data(response)
    items = data if isinstance(data, list) else data.get('items', [])
    # The selector needs names only, never proxy passwords.
    return [{'id': p['id'], 'name': p.get('name', ''), 'status': p.get('status', '')} for p in items]


def upload_state_patch(target: str, ok: bool, message: str) -> dict[str, Any]:
    return {
        "uploads": {
            target: {
                "ok": bool(ok),
                "message": str(message or "").strip(),
                "updated_at": now_rfc3339(),
            }
        }
    }
