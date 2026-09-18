"""Presentation of Sub2API quota snapshots; unknown is never zero."""

import math
from datetime import datetime, timezone, timedelta


def parse_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def snapshot_usage(record):
    extra = record.get("extra") or {}
    usage = {"source": "账号缓存", "updated_at": extra.get("codex_usage_updated_at")}
    for short, key in [("5h", "five_hour"), ("7d", "seven_day")]:
        value = extra.get(f"codex_{short}_used_percent")
        if value is None:
            continue
        reset = extra.get(f"codex_{short}_reset_at")
        if not reset:
            base = parse_time(extra.get("codex_usage_updated_at"))
            seconds = extra.get(f"codex_{short}_reset_after_seconds")
            try:
                if base and seconds is not None:
                    reset = (base + timedelta(seconds=float(seconds))).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        usage[key] = {"utilization": value, "resets_at": reset}
    return usage


def quota_cell(usage, key, *, now=None):
    if usage.get("error"):
        return "读取失败"
    window = usage.get(key)
    if not isinstance(window, dict) or window.get("utilization") is None:
        return "暂无"
    reset = parse_time(window.get("resets_at"))
    if reset and reset <= (now or datetime.now(timezone.utc)):
        return "待更新"
    try:
        value = float(window["utilization"])
    except (ValueError, TypeError):
        return "暂无"
    if not math.isfinite(value) or value < 0:
        return "暂无"
    return f"{value:.1f}%"


def usage_details(usage):
    lines = ["用量来源：Sub2API（已用百分比，不是剩余Token）"]
    if usage.get("error"):
        lines.append("读取失败：" + str(usage["error"])[:200])
    lines.append("快照/读取时间：" + str(usage.get("updated_at") or "未知"))
    for label, key in [("5小时", "five_hour"), ("7天", "seven_day")]:
        window = usage.get(key) or {}
        reset = parse_time(window.get("resets_at"))
        reset_text = reset.astimezone().strftime("%m-%d %H:%M:%S") if reset else "未知"
        lines.append(f"{label}已用：{quota_cell(usage, key)}；重置：{reset_text}")
        stats = window.get("window_stats") or {}
        if stats:
            lines.append(
                f"  窗口请求 {stats.get('requests', '未知')} 次；Token {stats.get('tokens', '未知')}；账号费用 ${stats.get('cost', '未知')}"
            )
    return "\n".join(lines)


def scheduling_cell(record, *, now=None):
    """Show the scheduling switch separately from account activation/cooldown."""
    enabled = record.get('schedulable')
    if enabled is False:
        return '已关闭'
    if enabled is not True:
        return '未知'
    if record.get('status') == 'inactive':
        return '开启·账号停用'
    if record.get('status') != 'active':
        return '开启·账号异常'
    current = now or datetime.now(timezone.utc)
    expiry = record.get('expires_at')
    if isinstance(expiry, (int, float)) and not isinstance(expiry, bool):
        try:
            expiry = datetime.fromtimestamp(expiry, timezone.utc)
        except (ValueError, OSError, OverflowError):
            expiry = None
    else:
        expiry = parse_time(expiry)
    if record.get('auto_pause_on_expired') and expiry and expiry <= current:
        return '开启·已到期'
    for field, label in [('temp_unschedulable_until','临时冷却'), ('rate_limit_reset_at','限流冷却'), ('overload_until','过载冷却')]:
        until = parse_time(record.get(field))
        if until and until > current:
            return '开启·' + label
    return '参与调度'


def sort_account_rows(records, column='id', descending=False, usage_reader=snapshot_usage):
    known, missing = [], []
    for record in records:
        if column == 'id': value=int(record.get('id') or 0)
        elif column in ('quota5','quota7'):
            text=quota_cell(usage_reader(record),'five_hour' if column=='quota5' else 'seven_day')
            value=float(text[:-1]) if text.endswith('%') else None
        elif column == 'email': value=str(record.get('name') or record.get('email') or '').casefold()
        elif column == 'groups': value=', '.join(record.get('group_names') or []).casefold()
        elif column == 'scheduling': value=scheduling_cell(record)
        elif column == 'error': value=str(record.get('error_message') or '').casefold()
        else: value=str(record.get(column) or '').casefold()
        (missing if value is None else known).append((value,record))
    known.sort(key=lambda pair:(pair[0],int(pair[1].get('id') or 0)),reverse=descending)
    return [r for _,r in known+missing]
