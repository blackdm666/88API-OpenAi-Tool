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
    """Recover one enrolled identity at a time, with durable upload/test phases."""
    from .credential_vault import CredentialVault, credential_revision
    from .recovery_support import (NeedsUser, authorize_saved_account, remote_health,
                                   test_sub2api_account, validate_new_credentials)
    from .integrations import get_sub2api_account, get_sub2api_account_credentials
    from .utils import now_rfc3339

    cfg = (settings.get("integrations") or {}).get("sub2api") or {}
    server = normalize_server_url(cfg.get("api_url", ""))
    locals_ = [r for r in store.load_all() if (r.get("sub2api_recovery") or {}).get("enabled")]
    result = {"checked": 0, "recovered": 0, "blocked": 0, "records": []}
    if not locals_:
        return result
    proxy = settings.get("http_proxy", "")
    remotes = fetch_sub2api_accounts(settings, proxy_url=proxy, filters={"platform": "openai"})
    result['records'] = remotes
    saved_accounts, vault_error = {}, ''
    if cfg.get('auto_reauthorize_401', True):
        try:
            saved_accounts = CredentialVault().load()
        except Exception as exc:
            vault_error = str(exc)

    for local in locals_:
        if cancelled():
            break
        state = dict(local.get('sub2api_recovery') or {})
        revision = token_revision(local)
        now = time.time()
        saved = saved_accounts.get(str(local.get('email', '')).strip().lower())
        saved_revision = credential_revision(saved)

        def persist():
            local['sub2api_recovery'] = state
            store.save_record(local, filename=local.get('_filename'))

        def stage(text):
            state.update(status=text, message=text)
            persist()
            if log_fn:
                log_fn(f"Sub2API #{state.get('remote_id', '?')}：{text}")

        def observe(remote):
            state.update(remote_id=remote['id'], server=server, checked_at=time.time(),
                         remote_status=remote.get('status', ''), remote_health=remote_health(remote),
                         remote_exists=True,
                         credentials_match=((remote.get('credentials') or {}).get('access_token') == local.get('access_token'))
                         if (remote.get('credentials') or {}).get('access_token') else None)
            for i, row in enumerate(remotes):
                if row['id'] == remote['id']:
                    remotes[i] = {**row, **remote}
                    break

        def verify_and_test(remote):
            observe(remote)
            if not state['credentials_match'] or remote.get('status') != 'active':
                raise RuntimeError('远端凭据或启用状态读回不通过')
            local.setdefault('uploads', {})['sub2api'] = {
                'ok': True, 'message': f"原账号 #{remote['id']} 凭据及启用状态已核验",
                'updated_at': now_rfc3339(), 'remote_id': remote['id'], 'server': server}
            state.update(pending_upload=False, verification_pending=True, attempts=0,
                         next_attempt_at=0, revision=token_revision(local))
            persist()
            if remote_health(remote) != '已启用·可调度':
                state.update(status=remote_health(remote), message='补授权已生效，等待账号可调度后测试')
                return
            if cancelled():
                state.update(status='待运行验证', message='已补授权，停止后保留运行验证阶段')
                return
            if not cfg.get('recovery_test_enabled', True):
                state.update(status='已恢复', message='凭据、启用和调度状态已核验；模型测试未开启', verification_pending=False)
                result['recovered'] += 1
                return
            current_revision = token_revision(local)
            if state.get('probe_revision') == current_revision:
                state.update(verification_pending=False,
                             status='已恢复' if state.get('probe_ok') else '测试未通过',
                             message=state.get('probe_message', '先前模型测试结果未确认，请人工复核'))
                return
            # Record attempt before issuing the billable request. Restarting must
            # not repeat a request whose result may have been lost.
            state.update(probe_revision=current_revision, probe_ok=False,
                         probe_message='测试结果未确认', status='模型测试中')
            persist()
            if log_fn:
                log_fn(f"Sub2API #{remote['id']}：启用及调度已核验，开始原生模型测试")
            try:
                probe = test_sub2api_account(settings, remote['id'], proxy_url=proxy, cancelled=cancelled)
                state.update(probe_ok=True, probe_model=probe['model'], probe_at=probe['checked_at'],
                             probe_message='模型请求成功')
            except Exception as exc:
                state.update(probe_ok=False, probe_message=redact_error(exc), probe_at=time.time())
            state['verification_pending'] = False
            # Testing can change the remote error/cooldown; inspect the final state.
            final = get_sub2api_account(settings, remote['id'], proxy_url=proxy)
            if not match_remote(local, [final]):
                raise NeedsUser('模型测试后账号身份变化，请人工核对')
            observe(final)
            if state['probe_ok'] and remote_health(final) == '已启用·可调度':
                state.update(status='已恢复', message='原账号凭据、启用、调度及模型测试全部通过')
                result['recovered'] += 1
            else:
                state.update(status='测试未通过' if not state['probe_ok'] else remote_health(final),
                             message='补授权已提交；' + state['probe_message'] + '；' + remote_health(final))
                result['blocked'] += 1

        try:
            if state.get('server') and state['server'] != server:
                raise NeedsUser('监控绑定的服务器与当前地址不同，请重新开启账号监控')
            remote = match_remote(local, remotes)
            result['checked'] += 1
            if not remote:
                state.update(status='未上传/未匹配', message='远端不存在唯一匹配账号，不自动创建',
                             remote_exists=False, checked_at=now)
                continue
            if (auth_failure_kind(remote) or state.get('pending_upload') or state.get('verification_pending')) and not (remote.get('credentials') or {}).get('access_token'):
                detail = get_sub2api_account_credentials(settings, remote['id'], proxy_url=proxy)
                if not match_remote(local, [detail]):
                    raise NeedsUser('凭据读取后身份不一致，停止恢复')
                remote = {**remote, **detail}
            observe(remote)
            kind = auth_failure_kind(remote)
            local_changed = bool(state.get('revision') and state['revision'] != revision)
            material_changed = state.get('credential_revision', '') != saved_revision
            policy_changed = state.get('reauth_enabled') != bool(cfg.get('auto_reauthorize_401', True))
            state['reauth_enabled'] = bool(cfg.get('auto_reauthorize_401', True))
            if local_changed:
                state.update(attempts=0, next_attempt_at=0, blocked_revision='', pending_upload=False)
            if material_changed or policy_changed:
                state.update(reauth_attempts=0, reauth_next_at=0, blocked_revision='',
                             credential_revision=saved_revision, attempts=0, next_attempt_at=0)
            pending = state.get('pending_upload', False)
            if remote.get('status') == 'inactive':
                state.update(status='已停用', message='远端主动停用，保留停用状态', pending_upload=False,
                             verification_pending=False)
                continue
            if not kind and not pending:
                if state.get('verification_pending'):
                    verify_and_test(remote)
                else:
                    health = remote_health(remote)
                    state.update(status='正常' if health == '已启用·可调度' else health,
                                 message='远端存在；' + health, attempts=0)
                    if state.get('probe_revision') == revision and state.get('probe_ok') is False:
                        state.update(status='测试未通过', message=state.get('probe_message', '模型测试未确认'))
                continue
            if state.get('blocked_revision') == revision or (state.get('status') == '凭据冲突' and not local_changed):
                continue
            if not local_changed and now < state.get('next_attempt_at', 0):
                continue
            if pending and state['credentials_match'] and remote.get('status') == 'active':
                verify_and_test(remote)
                continue
            if pending and state.get('remote_revision') and state['remote_revision'] != remote_revision(remote):
                state.update(status='凭据冲突', message='待同步期间远端凭据变化，请核对后重新开启监控', blocked_revision=revision)
                result['blocked'] += 1
                continue
            attempts = int(state.get('attempts', 0))
            if attempts >= 3:
                raise NeedsUser('连续恢复失败3次，已停止重试，请检查网络或授权资料')
            state.update(attempts=attempts + 1, next_attempt_at=now + 300 * 2**attempts, revision=revision)
            if not pending:
                state['remote_revision'] = remote_revision(remote)
            persist()
            if not pending and not local_changed:
                reauthorize = kind == 'permanent'
                if not reauthorize:
                    stage('刷新凭据中')
                    source = deepcopy(local)
                    credentials = remote.get('credentials') or {}
                    if credentials.get('refresh_token'):
                        source['refresh_token'] = credentials['refresh_token']
                        source['client_id'] = credentials.get('client_id') or source.get('client_id', '')
                    try:
                        ok, message = refresh_record(store, source, settings, proxy_url=proxy, sync_plan=False)
                        if not ok:
                            raise RuntimeError(message)
                    except Exception as exc:
                        if any(code in str(exc).lower() for code in ('invalid_grant', 'token_revoked', 'token_invalidated', 'refresh_token_reused')):
                            reauthorize = True
                        else:
                            raise
                    if not reauthorize:
                        local = next(r for r in store.load_all() if r.get('email', '').lower() == local.get('email', '').lower())
                if reauthorize:
                    if not cfg.get('auto_reauthorize_401', True):
                        raise NeedsUser('自动重新授权未开启，请在 Sub2API 设置中开启或手动授权')
                    if not saved:
                        raise NeedsUser(vault_error or '缺少已保存的2FA资料，请在2FA页导入并加密保存')
                    if now - state.get('reauth_window_at', 0) >= 3600:
                        state.update(reauth_window_at=now, reauth_attempts=0)
                    if state.get('reauth_attempts', 0) >= 3:
                        raise NeedsUser('一小时内重新授权已达3次，请人工核对账号异常原因')
                    if now < state.get('reauth_next_at', 0):
                        state.update(status='授权冷却中', next_attempt_at=state['reauth_next_at'], attempts=attempts)
                        continue
                    state.update(reauth_attempts=state.get('reauth_attempts', 0) + 1, reauth_next_at=now + 300)
                    stage('自动重新授权中')
                    local = authorize_saved_account(local, remote, saved, settings, log_fn=log_fn)
                    # Persist the newly authorized token with the upload phase in
                    # the same atomic write; never repeat login after upload failure.
            if not pending:
                validate_new_credentials(local, local, remote)
            state.update(pending_upload=True, verification_pending=False, revision=token_revision(local), status='待补授权')
            persist()
            if cancelled():
                state.update(next_attempt_at=0, status='待同步')
                break
            stage('补授权中')
            verified = apply_sub2api_credentials(local, remote, settings, proxy_url=proxy)
            verify_and_test(verified)
            if log_fn:
                log_fn(f"Sub2API #{remote['id']}：{state['status']}；{state['message']}")
        except NeedsUser as exc:
            state.update(status='需要重新授权', message=redact_error(exc), blocked_revision=token_revision(local),
                         revision=token_revision(local), credential_revision=saved_revision)
            result['blocked'] += 1
            if log_fn:
                log_fn(f"Sub2API #{state.get('remote_id', '?')}：{state['message']}")
        except Exception as exc:
            state.update(status='恢复失败', message=redact_error(exc), revision=token_revision(local))
            result['blocked'] += 1
            if log_fn:
                log_fn(f"Sub2API #{state.get('remote_id', '?')}：恢复失败；{state['message']}")
        finally:
            persist()
    return result
