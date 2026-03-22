"""
SMTP sender.

Replies are always sent from the same address that received the original email
(`received_at_account`).  SMTP credentials are looked up from settings by address.
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import structlog

from config import settings

log = structlog.get_logger(__name__)


def send_reply(
    to_address: str,
    subject: str,
    body: str,
    from_account: Optional[str] = None,   # reply-from address (received_at_account)
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    body_html: Optional[str] = None,
) -> bool:
    """
    Send an email reply via SMTP.

    `from_account` must match one of the configured email accounts.
    Falls back to the first configured account if not found or not provided.

    Returns True on success, False on failure.
    """
    # Resolve account credentials
    account = None
    if from_account:
        account = settings.get_account(from_account)
    if account is None:
        accounts = settings.email_accounts
        if not accounts:
            log.error("smtp_no_accounts_configured")
            return False
        account = accounts[0]
        if from_account:
            log.warning("smtp_account_not_found_using_fallback", requested=from_account, fallback=account.address)

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{account.from_name} <{account.address}>"
    msg["To"] = to_address
    msg["Subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    elif in_reply_to:
        msg["References"] = in_reply_to

    msg.attach(MIMEText(body, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(account.smtp_host, account.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(account.address, account.password)
            server.sendmail(account.address, [to_address], msg.as_bytes())
        log.info("smtp_sent", from_addr=account.address, to=to_address, subject=msg["Subject"])
        return True
    except Exception:
        log.exception("smtp_send_failed", from_addr=account.address, to=to_address)
        return False
