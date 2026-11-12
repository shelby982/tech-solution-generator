"""发信服务：SMTP / Resend / 落库 outbox 三档。

没有配置邮件服务时**不报错**：把邮件写进 `mail_outbox`，
运营后台能看到内容与链接（开发/内测阶段可用）。

配置（任一即可）：
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
  RESEND_API_KEY / MAIL_FROM
"""
from __future__ import annotations

import logging
import os

from db import get_db

logger = logging.getLogger(__name__)


def _from_addr() -> str:
    return (os.environ.get("MAIL_FROM") or os.environ.get("SMTP_FROM")
            or "no-reply@example.com").strip()


def _smtp_config() -> dict | None:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587") or 587),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASS", "").strip(),
        "ssl": os.environ.get("SMTP_SSL", "0").strip() in ("1", "true"),
        "from": _from_addr(),
    }


def _resend_key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


def provider_name() -> str:
    if _resend_key():
        return "resend"
    if _smtp_config():
        return "smtp"
    return "outbox"


async def _record(to_email: str, subject: str, body: str, provider: str, ok: bool, error: str = "") -> None:
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO mail_outbox (to_email, subject, body, provider, ok, error) "
                "VALUES (?,?,?,?,?,?)",
                (str(to_email)[:200], str(subject)[:200], str(body)[:8000], provider, 1 if ok else 0,
                 str(error)[:300]))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"邮件落库失败（不影响主流程）：{exc}")


def _send_smtp(cfg: dict, to_email: str, subject: str, body: str) -> None:
    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = cfg["from"]
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    if cfg["ssl"]:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
        try:
            server.starttls()
        except Exception:
            pass
    try:
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _send_resend(key: str, to_email: str, subject: str, body: str) -> None:
    import json
    import urllib.request

    payload = json.dumps({"from": _from_addr(), "to": [to_email],
                          "subject": subject, "text": body}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=payload, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        if resp.status >= 300:
            raise RuntimeError(f"resend HTTP {resp.status}")


async def send(to_email: str, subject: str, body: str) -> dict:
    """发一封邮件；无论成败都写 outbox（便于运营排查/手动复制链接）。"""
    provider = provider_name()
    try:
        if provider == "resend":
            _send_resend(_resend_key(), to_email, subject, body)
        elif provider == "smtp":
            cfg = _smtp_config()
            if not cfg:
                raise RuntimeError("SMTP 配置不完整")
            _send_smtp(cfg, to_email, subject, body)
        else:
            logger.info(f"邮件未配置发信通道，已落 outbox：{to_email} · {subject}")
        await _record(to_email, subject, body, provider, True)
        return {"sent": provider != "outbox", "provider": provider}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"发信失败（{provider}）：{exc}")
        await _record(to_email, subject, body, provider, False, str(exc))
        return {"sent": False, "provider": provider, "error": str(exc)}
