"""Opt-in recovery for locally owned accounts; no account creation or deletion."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import time

from .integrations import fetch_sub2api_accounts, apply_sub2api_credentials
from .services import refresh_record
from .sub2api_policy import (
    auth_failure_kind,
    match_remote,
    normalize_server_url,
    redact_error,
)


def token_revision(record):
    return hashlib.sha256(
        (
            str(record.get("access_token", ""))
            + "\0"
            + str(record.get("refresh_token", ""))
        ).encode()
    ).hexdigest()


def remote_revision(remote):
    return token_revision(remote.get("credentials") or {})


def recovery_cycle(store, settings, *, log_fn=None, cancelled=lambda: False):
    """One bounded pass. Persist backoff across restarts and resume failed uploads."""
    cfg = (settings.get("integrations") or {}).get("sub2api") or {}
    server = normalize_server_url(cfg.get("api_url", ""))
    locals_ = [
        r for r in store.load_all() if (r.get("sub2api_recovery") or {}).get("enabled")
    ]
    if not locals_:
        return {"checked": 0, "recovered": 0, "blocked": 0, "records": []}
    proxy = settings.get("http_proxy", "")
    remotes = fetch_sub2api_accounts(
        settings, proxy_url=proxy, filters={"platform": "openai"}
    )
    result = {"checked": 0, "recovered": 0, "blocked": 0, "records": remotes}
    for local in locals_:
        if cancelled():
            break
        state = dict(local.get("sub2api_recovery") or {})
        revision = token_revision(local)
        now = time.time()
        try:
            if state.get("server") and state["server"] != server:
                raise ValueError("监控绑定的服务器与当前地址不同，请重新开启账号监控")
            remote = match_remote(local, remotes)
            result["checked"] += 1
            if not remote:
                state.update(
                    status="未匹配", message="远端没有唯一匹配账号；不会自动新建"
                )
            else:
                state.update(
                    remote_id=remote["id"],
                    server=server,
                    remote_status=remote.get("status", ""),
                    checked_at=now,
                )
                kind = auth_failure_kind(remote)
                pending = state.get("pending_upload", False)
                if remote.get("status") == "inactive":
                    state.update(
                        status="已跳过",
                        message="远端账号已停用，保持停用",
                        pending_upload=False,
                    )
                elif not kind and not pending:
                    state.update(
                        status="正常"
                        if remote.get("status") == "active"
                        else "非401异常",
                        message="本轮无需恢复",
                        attempts=0,
                    )
                elif state.get("revision") == revision and (
                    state.get("status") in ("需要重新授权", "凭据冲突")
                    or now < state.get("next_attempt_at", 0)
                ):
                    continue
                elif (
                    pending
                    and (remote.get("credentials") or {}).get("access_token")
                    == local.get("access_token")
                    and remote.get("status") == "active"
                ):
                    state.update(
                        status="已恢复",
                        message="此前提交已生效，读回确认",
                        pending_upload=False,
                        attempts=0,
                        next_attempt_at=0,
                    )
                    result["recovered"] += 1
                elif (
                    pending
                    and state.get("remote_revision")
                    and state["remote_revision"] != remote_revision(remote)
                ):
                    state.update(
                        status="凭据冲突",
                        message="待同步期间远端凭据被其他任务修改，请人工核对后重新开启监控",
                    )
                    result["blocked"] += 1
                else:
                    # A fresh manually authorized local token can be uploaded even
                    # when the remote token was revoked. Never retry that old token.
                    local_changed = (
                        state.get("revision") and state["revision"] != revision
                    )
                    if kind == "permanent" and not local_changed and not pending:
                        state.update(
                            status="需要重新授权",
                            message="远端授权已撤销或不可刷新；完成官方登录后将重新同步",
                            revision=revision,
                        )
                        result["blocked"] += 1
                    else:
                        attempts = 0 if local_changed else int(state.get("attempts", 0))
                        if attempts >= 3:
                            state.update(
                                status="需要重新授权",
                                message="连续恢复失败3次，已停止重试",
                                revision=revision,
                            )
                            result["blocked"] += 1
                        else:
                            state.update(
                                attempts=attempts + 1,
                                next_attempt_at=now + min(3600, 300 * 2**attempts),
                                revision=revision,
                            )
                            if not pending:
                                state["remote_revision"] = remote_revision(remote)
                            local["sub2api_recovery"] = state
                            store.save_record(local, filename=local.get("_filename"))
                            if not pending and not local_changed:
                                # Sub2API may have rotated its token since the local
                                # copy was imported. Use the freshest owned identity.
                                source = deepcopy(local)
                                rt = (remote.get("credentials") or {}).get(
                                    "refresh_token"
                                )
                                if rt:
                                    source["refresh_token"] = rt
                                    source["client_id"] = (
                                        remote.get("credentials") or {}
                                    ).get("client_id") or source.get("client_id", "")
                                ok, message = refresh_record(
                                    store,
                                    source,
                                    settings,
                                    proxy_url=proxy,
                                    sync_plan=False,
                                )
                                if not ok:
                                    raise RuntimeError(message)
                                local = next(
                                    r
                                    for r in store.load_all()
                                    if str(r.get("email", "")).lower()
                                    == str(local.get("email", "")).lower()
                                )
                            if cancelled():
                                state.update(
                                    pending_upload=True,
                                    revision=token_revision(local),
                                    status="待同步",
                                    next_attempt_at=0,
                                )
                                local["sub2api_recovery"] = state
                                store.save_record(
                                    local, filename=local.get("_filename")
                                )
                                break
                            # Durable phase boundary: retry uploads using the saved
                            # refreshed token, not another refresh-token rotation.
                            state.update(
                                pending_upload=True,
                                revision=token_revision(local),
                                status="待同步",
                            )
                            local["sub2api_recovery"] = state
                            store.save_record(local, filename=local.get("_filename"))
                            apply_sub2api_credentials(
                                local, remote, settings, proxy_url=proxy
                            )
                            state.update(
                                status="已恢复",
                                message="新凭据已写回原账号并校验",
                                pending_upload=False,
                                attempts=0,
                                next_attempt_at=0,
                                revision=token_revision(local),
                            )
                            result["recovered"] += 1
                            if log_fn:
                                log_fn(f"Sub2API 恢复成功：账号 #{remote['id']}")
        except Exception as exc:
            message = redact_error(exc)
            permanent = any(
                s in message.lower()
                for s in (
                    "invalid_grant",
                    "token_revoked",
                    "token_invalidated",
                    "refresh_token_reused",
                )
            )
            state.update(
                status="需要重新授权" if permanent else "恢复失败",
                message=message,
                revision=token_revision(local),
            )
            result["blocked"] += 1
            if log_fn:
                log_fn("Sub2API 恢复跳过/失败：" + message)
        local["sub2api_recovery"] = state
        store.save_record(local, filename=local.get("_filename"))
    return result
