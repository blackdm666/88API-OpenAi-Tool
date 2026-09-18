"""Credential validation and Sub2API's native streaming account test."""
from copy import deepcopy
import json
import time

from .integrations import _sub2api_request
from .sub2api_policy import account_identity, match_remote, redact_error
from .usage_display import parse_time

DEFAULT_RECOVERY_MODEL = 'gpt-5.5'


class NeedsUser(RuntimeError):
    pass


def remote_health(remote):
    if remote.get('status') == 'inactive':
        return '已停用'
    if remote.get('status') != 'active':
        return '授权异常' if '401' in str(remote.get('error_message', '')) else '远端异常'
    if not remote.get('schedulable', True):
        return '已启用·停调度'
    expiry = parse_time(remote.get('expires_at'))
    if expiry and expiry.timestamp() <= time.time():
        return '已启用·已到期'
    for field in ('temp_unschedulable_until', 'rate_limit_reset_at', 'overload_until'):
        until = parse_time(remote.get(field))
        if until and until.timestamp() > time.time():
            return '已启用·冷却中'
    return '已启用·可调度'


def validate_new_credentials(local, token, remote):
    old_email, old_id = account_identity(local)
    email, identity = account_identity(token)
    if not email or email != old_email or not identity or (old_id and identity != old_id):
        raise NeedsUser('新授权邮箱或工作区与原账号不符，未覆盖凭据，请人工核对')
    if not token.get('access_token') or not token.get('refresh_token'):
        raise NeedsUser('新授权缺少完整凭据，未写入')
    expiry = parse_time(token.get('expired'))
    if not expiry or expiry.timestamp() <= time.time():
        raise NeedsUser('新授权凭据已过期或缺少有效期，未写入')
    candidate = {**local, **token}
    if not match_remote(candidate, [remote]):
        raise NeedsUser('新授权无法匹配原 Sub2API 账号')
    return candidate


def authorize_saved_account(local, remote, saved, settings, *, log_fn=None):
    from tools.auth_2fa_live import AuthAccount, authorize_account
    if not saved:
        raise NeedsUser('缺少已保存的2FA资料，请在2FA页导入并加密保存')
    if str(saved.get('email', '')).lower() != str(local.get('email', '')).lower():
        raise NeedsUser('2FA资料邮箱不一致')
    account = AuthAccount(email=saved['email'], password=saved['password'],
                          totp_secret=saved['totp_secret'], raw_line='')
    # No report/cookie/OTP/token dump for unattended authorization. Only fixed,
    # sanitized stage messages go to the main log.
    result = authorize_account(account, deepcopy(settings), save_token=False,
                               include_secrets=False, quiet=True, write_report=False)
    if not result.get('ok'):
        message = str(result.get('message') or '授权失败')
        for secret in (saved['password'], saved['totp_secret']):
            if secret:
                message = message.replace(secret, '[已隐藏]')
        message = redact_error(message)
        if any(word in message.lower() for word in ('captcha', 'cloudflare', 'password', '验证码', '验证失败', '封禁', 'deactivated', 'mfa', 'totp')):
            raise NeedsUser('自动授权需要人工处理：' + message)
        raise RuntimeError('自动授权失败：' + message)
    return validate_new_credentials(local, result.get('token_data') or {}, remote)


def test_sub2api_account(settings, account_id, *, proxy_url='', cancelled=lambda: False):
    cfg = settings['integrations']['sub2api']
    model = str(cfg.get('recovery_test_model') or DEFAULT_RECOVERY_MODEL).strip()
    if not model or len(model) > 160 or any(c.isspace() for c in model):
        raise ValueError('测试模型名称无效')
    response = _sub2api_request(settings, 'POST', f'/api/v1/admin/accounts/{int(account_id)}/test',
                               proxy_url=proxy_url, timeout=(10, 45), stream=True,
                               json={'model_id': model, 'prompt': '', 'mode': 'default'})
    started = time.monotonic()
    actual_model = model
    try:
        if response.status_code != 200:
            raise RuntimeError(f'Sub2API 测试接口 HTTP {response.status_code}')
        for line in response.iter_lines():
            if cancelled():
                raise RuntimeError('测试已停止，结果未确认')
            if time.monotonic() - started > 120:
                raise RuntimeError('模型测试超过120秒，结果未确认')
            if isinstance(line, bytes):
                line = line.decode('utf-8', errors='replace')
            if not line.startswith('data:'):
                continue
            raw = line[5:].strip()
            if not raw or raw == '[DONE]':
                continue
            event = json.loads(raw)
            if event.get('model'):
                actual_model = str(event['model'])
            if event.get('error') or event.get('type') == 'error':
                raise RuntimeError(redact_error(event.get('error') or '模型测试失败'))
            if event.get('type') == 'test_complete':
                if event.get('success') is not True:
                    raise RuntimeError('Sub2API 模型测试未成功')
                return {'ok': True, 'model': actual_model, 'checked_at': time.time()}
        raise RuntimeError('模型测试流提前结束，未收到成功确认')
    finally:
        response.close()
