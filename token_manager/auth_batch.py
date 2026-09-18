"""Read-only preflight before an explicit batch authorization."""
from .integrations import fetch_sub2api_accounts
from .maintenance import token_revision
from .sub2api_policy import auth_failure_kind, match_remote, normalize_server_url
from .usage_display import parse_time
from .utils import now_ts
from tools.auth_2fa_live import parse_account_lines, LINE_SPLIT_RE


def remove_account_lines(raw_text, emails):
    removed = {str(email).strip().casefold() for email in emails}
    return '\n'.join(line for line in raw_text.splitlines()
                     if LINE_SPLIT_RE.split(line.strip(), maxsplit=1)[0].strip().casefold() not in removed)


def plan_authorization(accounts, local_records, remotes):
    local_by_email = {}
    for record in local_records:
        local_by_email.setdefault(str(record.get('email') or '').strip().casefold(), []).append(record)
    eligible, skipped, seen = [], [], set()
    for account in accounts:
        key = account.email.strip().casefold()
        reason = ''
        if key in seen:
            reason = '重复输入，已跳过'
        seen.add(key)
        matches = local_by_email.get(key, [])
        local = matches[0] if len(matches) == 1 else {'email': account.email}
        if len(matches) > 1:
            reason = '本地存在重复身份，需人工核对'
        if not reason:
            try:
                remote = match_remote(local, remotes, allow_rebind=True)
            except ValueError as exc:
                reason = str(exc)
            else:
                state = local.get('sub2api_recovery') or {}
                expiry = parse_time(local.get('expired'))
                fresh_local = bool(local.get('access_token') and local.get('refresh_token')
                                   and expiry and expiry.timestamp() > now_ts())
                if remote and remote.get('status') == 'inactive':
                    reason = '远端账号已停用，保持停用'
                elif remote and remote.get('status') == 'active' and (remote.get('credentials_status') or {}).get('has_access_token') is not False:
                    reason = 'Sub2API 授权状态正常，无需重复登录（调度/冷却不影响此判断）'
                elif fresh_local and (state.get('pending_upload') or state.get('verification_pending') or
                                      (state.get('revision') and state['revision'] != token_revision(local))):
                    reason = '本地已有新凭据待同步/核验，请继续自动维护，无需重新登录'
                elif remote and not auth_failure_kind(remote) and not (
                        remote.get('status') == 'active' and (remote.get('credentials_status') or {}).get('has_access_token') is False):
                    reason = '非授权异常或状态不明，跳过重新登录'
                elif remote is None and fresh_local:
                    reason = '本地已有未过期凭据，远端未匹配；请先核对上传'
        if reason:
            skipped.append({'email': account.email, 'reason': reason})
        else:
            eligible.append(account)
    return eligible, skipped


def run_checked_authorization(raw_text, settings, local_records, runner, options, *, log_fn=None):
    accounts, errors = parse_account_lines(raw_text)
    if errors:
        raise ValueError('2FA 输入包含无效行，请修正后再补授权')
    normalize_server_url((settings.get('integrations') or {}).get('sub2api', {}).get('api_url', ''))
    # No cached GUI state and no fallback to logging in everybody on API failure.
    remotes = fetch_sub2api_accounts(settings, proxy_url=settings.get('http_proxy', ''), filters={'platform': 'openai'})
    eligible, skipped = plan_authorization(accounts, local_records, remotes)
    if log_fn:
        for item in skipped:
            log_fn(f"2FA 跳过 {item['email']}：{item['reason']}")
        log_fn(f'2FA 核对完成：输入 {len(accounts)}，需授权 {len(eligible)}，跳过 {len(skipped)}')
    result = (runner('\n'.join(a.raw_line for a in eligible), settings, **options) if eligible else
              {'success_count': 0, 'fail_count': 0, 'input_error_count': 0, 'results': [], 'summary_path': ''})
    return {**result, 'skipped_count': len(skipped), 'skipped': skipped, 'checked_count': len(accounts)}
