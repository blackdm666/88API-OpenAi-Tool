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
