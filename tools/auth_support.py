"""Local TOTP and response-shape handling shared by the two authorization clients."""

import base64
import hashlib
import hmac
import struct
import time


def totp_code(secret, *, timestamp=None, digits=6):
    normalized = "".join(str(secret).split()).replace("-", "").upper()
    try:
        key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    except Exception as exc:
        raise ValueError("2FA密匙不是有效的Base32；请填写密匙而不是6位验证码") from exc
    if not key:
        raise ValueError("2FA密匙不能为空")
    counter = int(time.time() if timestamp is None else timestamp) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def fresh_totp(secret):
    remaining = 30 - time.time() % 30
    if remaining < 5:
        time.sleep(remaining + 0.2)
    return totp_code(secret)


def workspace_from_session(payload, parsed_cookie=None):
    """Use authenticated MFA JSON metadata when no legacy session cookie exists."""
    session = (payload or {}).get("oai-client-auth-session")
    if not isinstance(session, dict):
        session = parsed_cookie or {}
    workspaces = session.get("workspaces") or []
    if not workspaces and parsed_cookie:
        workspaces = parsed_cookie.get("workspaces") or []
    if workspaces and isinstance(workspaces[0], dict) and workspaces[0].get("id"):
        return str(workspaces[0]["id"])
    raise RuntimeError(
        "2FA已通过，但响应与会话均无工作区信息；请使用“授权”页完成官方浏览器登录"
    )


def continuation_without_orgs(payload):
    orgs = (payload.get("data") or {}).get("orgs") or []
    if orgs:
        return ""
    url = str(payload.get("continue_url") or "").strip()
    if not url:
        raise RuntimeError("服务端未返回组织或后续授权地址，请使用官方浏览器授权")
    return url


def failure_advice(error):
    text = str(error).lower()
    if "cloudflare" in text or "just a moment" in text:
        return (
            "登录入口被安全检查拦截；请在“授权”页使用官方浏览器登录，勿反复批量重试。"
        )
    if "调试端口" in text or "devtools" in text:
        return "浏览器启动失败，请检查浏览器路径、端口占用；关闭本工具之前启动的授权窗口后重试。"
    if "invalid_grant" in text or "incorrect" in text:
        return "请核对账号、密码和系统时间，再完成官方交互授权。"
    if "cookie" in text or "session" in text or "orgs" in text or "工作区" in text:
        return (
            "授权会话衔接失败；无需重复输入验证码，建议使用“授权”页的官方浏览器流程。"
        )
    return "请查看失败阶段；网络超时可稍后重试，凭据或安全检查失败请使用官方交互授权。"
