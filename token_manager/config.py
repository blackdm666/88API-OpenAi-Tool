from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .constants import (
    APP_DIR,
    APP_CONFIG_FILE,
    LEGACY_APP_CONFIG_FILE,
    DEFAULT_AUTO_REFRESH_INTERVAL,
    DEFAULT_AUTO_REFRESH_THRESHOLD,
    DEFAULT_OAUTH_AUTH_URL,
    DEFAULT_OAUTH_CLIENT_ID,
    DEFAULT_OAUTH_REDIRECT_URI,
    DEFAULT_OAUTH_SCOPE,
    DEFAULT_OAUTH_TOKEN_URL,
    DEFAULT_OUTPUTS_DIR,
    DEFAULT_REFRESH_WORKERS,
    DEFAULT_SUB2API_GROUP_IDS,
    DEFAULT_TOKENS_DIR,
    DEFAULT_UPLOAD_WORKERS,
)


from .utils import atomic_write_json


def _known_documents_dirs() -> set[Path]:
    """Return common Windows Documents locations used by this app.

    A Documents root is a user workspace, not a token directory. Treating it
    as one makes startup recurse through unrelated project files.
    """
    home = Path.home()
    candidates = {
        home / "Documents",
        home / "OneDrive" / "Documents",
        home / "OneDrive" / "文档",
    }
    return {path.resolve() for path in candidates if path.exists()}


def _safe_token_directory(raw: Any) -> str:
    configured = str(raw or "").strip()
    if not configured:
        return str(DEFAULT_TOKENS_DIR)
    try:
        path = Path(configured).expanduser().resolve()
    except (OSError, RuntimeError):
        return configured
    legacy_desktop = (APP_DIR / "tokens").resolve()
    if path == legacy_desktop or path in _known_documents_dirs():
        return str(DEFAULT_TOKENS_DIR)
    return configured


def _safe_output_directory(raw: Any) -> str:
    configured = str(raw or "").strip()
    if not configured:
        return str(DEFAULT_OUTPUTS_DIR)
    try:
        path = Path(configured).expanduser().resolve()
    except (OSError, RuntimeError):
        return configured
    legacy_desktop = (APP_DIR / "outputs").resolve()
    if path == legacy_desktop or path in _known_documents_dirs():
        return str(DEFAULT_OUTPUTS_DIR)
    return configured


def default_config() -> dict[str, Any]:
    return {
        "tokens_dir": str(DEFAULT_TOKENS_DIR),
        "outputs_dir": str(DEFAULT_OUTPUTS_DIR),
        "refresh_workers": DEFAULT_REFRESH_WORKERS,
        "upload_workers": DEFAULT_UPLOAD_WORKERS,
        "auth_2fa_mode": "protocol",
        "auth_2fa_live_workers": 3,
        "auth_2fa_live_save_token": False,
        "browser_executable_path": "",
        "browser_auth_start_port": 9333,
        "auto_refresh_interval_seconds": DEFAULT_AUTO_REFRESH_INTERVAL,
        "auto_refresh_threshold_seconds": DEFAULT_AUTO_REFRESH_THRESHOLD,
        "organize_tokens_by_plan": True,
        "http_proxy": "",
        "open_browser_on_auto_auth": True,
        "auto_auth_timeout_seconds": 300,
        "oauth": {
            "auth_url": DEFAULT_OAUTH_AUTH_URL,
            "token_url": DEFAULT_OAUTH_TOKEN_URL,
            "client_id": DEFAULT_OAUTH_CLIENT_ID,
            "redirect_uri": DEFAULT_OAUTH_REDIRECT_URI,
            "scope": DEFAULT_OAUTH_SCOPE,
        },
        "integrations": {
            "sub2api": {
                "api_url": "",
                "api_key": "",
                "group_ids": DEFAULT_SUB2API_GROUP_IDS,
                "auth_mode": "auto",
                "concurrency": 10,
                "priority": 1,
                "rate_multiplier": 1,
                "proxy_id": None,
                "codex_fingerprint_mode": "off",
                "ws_mode": "ctx_pool",
                "auto_pause_on_expired": True,
                "auto_reauthorize_401": True,
                "auto_monitor_uploaded_accounts": True,
                "auto_enable_schedulable": True,
                "recovery_test_enabled": True,
                "recovery_test_model": "gpt-5.5",
                "default_list_group_ids": "2",
                "admin_email": "",
                "admin_password": "",
                "access_token": "",
                "refresh_token": "",
                "token_expires_at": 0,
            },
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _migrate_legacy_config(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(raw or {})
    if migrated.get("custom_scan_root") and not migrated.get("tokens_dir"):
        root = Path(str(migrated["custom_scan_root"])).expanduser()
        migrated["tokens_dir"] = str(root if root.name.lower() == "tokens" else root / "tokens")
    migrated["tokens_dir"] = _safe_token_directory(migrated.get("tokens_dir"))
    migrated["outputs_dir"] = _safe_output_directory(migrated.get("outputs_dir"))
    (migrated.get("integrations") or {}).pop("cpa", None)
    return migrated


def load_app_config() -> dict[str, Any]:
    config = default_config()
    for path in (APP_CONFIG_FILE, LEGACY_APP_CONFIG_FILE):
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(raw, dict):
            return _deep_merge(config, _migrate_legacy_config(raw))
    return config


def save_app_config(config: dict[str, Any]) -> None:
    atomic_write_json(APP_CONFIG_FILE, config)
