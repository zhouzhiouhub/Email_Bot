"""
DingTalk ActionCard push notification for human review.

Uses the 'actionCard' message type with independent jump buttons (btnOrientation=1).
Three buttons: 批准发送 / 编辑后发送 / 驳回.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Optional

import httpx
import structlog

from config import settings

log = structlog.get_logger(__name__)


def _build_sign() -> tuple[str, str]:
    """Generate DingTalk webhook signature and timestamp."""
    timestamp = str(round(time.time() * 1000))
    secret = settings.dingtalk_secret
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def _build_action_card(
    thread_db_id: int,
    customer_email: str,
    subject: str,
    detected_language: str,
    user_summary: str,
    draft_reply: str,
    confidence: float,
) -> dict:
    base_url = settings.service_base_url.rstrip("/")
    approve_url = f"{base_url}/review/action?thread_id={thread_db_id}&action=approve"
    edit_url = f"{base_url}/review/edit/{thread_db_id}"
    reject_url = f"{base_url}/review/action?thread_id={thread_db_id}&action=reject"

    preview = user_summary[:200].replace("\n", " ")
    draft_preview = draft_reply[:300].replace("\n", " ")
    if len(draft_reply) > 300:
        draft_preview += "…"

    text = (
        f"## 📬 待审核邮件回复\n\n"
        f"**用户邮箱：** {customer_email}  \n"
        f"**邮件主题：** {subject}  \n"
        f"**检测语言：** {detected_language}  \n"
        f"**置信度：** {confidence * 100:.0f}%\n\n"
        f"---\n\n"
        f"**用户问题摘要：**\n\n{preview}\n\n"
        f"**AI 草拟：**\n\n{draft_preview}\n\n"
    )

    return {
        "msgtype": "actionCard",
        "actionCard": {
            "title": f"待审核：{subject[:40]}",
            "text": text,
            "btnOrientation": "1",
            "btns": [
                {"title": "✅ 批准发送", "actionURL": approve_url},
                {"title": "✏️ 编辑后发送", "actionURL": edit_url},
                {"title": "❌ 驳回", "actionURL": reject_url},
            ],
        },
    }


async def push_review_card(
    thread_db_id: int,
    customer_email: str,
    subject: str,
    detected_language: str,
    user_summary: str,
    draft_reply: str,
    confidence: float,
) -> Optional[str]:
    """
    Push an ActionCard review card to DingTalk.
    Returns a message ID on success, None on failure.
    """
    if not settings.dingtalk_webhook_url:
        log.warning("dingtalk_webhook_not_configured")
        return None

    webhook_url = settings.dingtalk_webhook_url
    if settings.dingtalk_secret:
        timestamp, sign = _build_sign()
        webhook_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

    payload = _build_action_card(
        thread_db_id=thread_db_id,
        customer_email=customer_email,
        subject=subject,
        detected_language=detected_language,
        user_summary=user_summary,
        draft_reply=draft_reply,
        confidence=confidence,
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("errcode") == 0:
                msg_id = data.get("requestId") or str(thread_db_id)
                log.info("dingtalk_card_sent", thread_db_id=thread_db_id, msg_id=msg_id)
                return msg_id
            else:
                log.warning("dingtalk_api_error", response=data)
                return None
    except Exception:
        log.exception("dingtalk_push_failed", thread_db_id=thread_db_id)
        return None
