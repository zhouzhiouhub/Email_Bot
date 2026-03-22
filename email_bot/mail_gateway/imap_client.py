"""
IMAP polling client — supports multiple accounts.

Each configured account is polled independently.  Every parsed email carries
`received_at_account` so the SMTP sender knows which address to reply from.

Polling uses SEARCH **UNSEEN** only (未读). Inbound messages are **not** marked \\Seen on
fetch; call `mark_imap_message_seen` after a reply (or “more info” email) has been sent.
"""
from __future__ import annotations

import email
import threading
from email.header import decode_header, make_header
from email.message import Message
from typing import Callable, Optional

import imapclient
import structlog

from config import EmailAccount, settings
from models.schemas import ParsedEmail
from mail_gateway.thread_tracker import (
    build_thread_id,
    clean_plain_body,
    detect_email_type,
    extract_feedback_text,
    extract_real_user_email,
)

log = structlog.get_logger(__name__)

# IMAP SEARCH: UNSEEN = messages that do not have \\Seen (未读). Never poll ALL or RECENT alone.
_UNREAD_ONLY_CRITERIA = ["UNSEEN"]

_sync_session_maker = None


def _sync_session_factory():
    global _sync_session_maker
    if _sync_session_maker is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        _sync_session_maker = sessionmaker(bind=engine)
    return _sync_session_maker


def inbound_message_already_stored(message_id: str) -> bool:
    from sqlalchemy import select

    from models.db import EmailMessage

    sm = _sync_session_factory()
    with sm() as s:
        return (
            s.scalar(select(EmailMessage.id).where(EmailMessage.message_id == message_id))
            is not None
        )


def is_thread_closed(thread_id_str: str) -> bool:
    """Return True if a thread with this ID already exists and is CLOSED."""
    from sqlalchemy import select

    from models.db import EmailThread, ThreadStatus

    sm = _sync_session_factory()
    with sm() as s:
        status = s.scalar(
            select(EmailThread.status).where(EmailThread.thread_id == thread_id_str)
        )
        return status == ThreadStatus.CLOSED


def try_mark_seen_if_inbound_already_answered(message_id: str) -> None:
    """
    Recovery: message is still UNSEEN on IMAP but already ingested — if we have
    sent any outbound mail after this inbound, mark \\Seen.
    """
    from sqlalchemy import select

    from models.db import EmailMessage, MessageDirection

    sm = _sync_session_factory()
    with sm() as s:
        inbound = s.scalar(
            select(EmailMessage).where(
                EmailMessage.message_id == message_id,
                EmailMessage.direction == MessageDirection.INBOUND,
            )
        )
        if not inbound or not inbound.imap_uid or not inbound.received_inbox:
            return
        uid = inbound.imap_uid
        inbox = inbound.received_inbox
        folder = inbound.imap_folder
        thread_id = inbound.thread_id
        created_at = inbound.created_at
        follow_up_id = s.scalars(
            select(EmailMessage.id).where(
                EmailMessage.thread_id == thread_id,
                EmailMessage.direction == MessageDirection.OUTBOUND,
                EmailMessage.created_at >= created_at,
            ).limit(1)
        ).first()
        has_outbound = follow_up_id is not None
    if not has_outbound:
        return
    account = settings.get_account(inbox)
    if account is None:
        log.warning("imap_mark_seen_skip_unknown_inbox", inbox=inbox)
        return
    mark_imap_message_seen(uid, account, folder)


def mark_imap_message_seen(
    uid: int,
    account: EmailAccount,
    folder: Optional[str] = None,
) -> None:
    """Add \\Seen after a user-facing reply was successfully sent."""
    path = folder or account.imap_folder
    try:
        with imapclient.IMAPClient(host=account.imap_host, port=account.imap_port, ssl=True) as client:
            client.login(account.address, account.password)
            client.select_folder(path)
            client.add_flags([uid], [imapclient.SEEN])
        log.info("imap_marked_seen", account=account.address, folder=path, uid=uid)
    except Exception:
        log.exception("imap_mark_seen_failed", account=account.address, folder=path, uid=uid)


def _decode_header_str(value: Optional[str]) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _get_body_parts(msg: Message) -> tuple[str, str]:
    """Return (plain_text, html_text) from a (possibly multipart) message."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd.lower():
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(charset, errors="replace")
            if ct == "text/plain" and not plain:
                plain = text
            elif ct == "text/html" and not html:
                html = text
    else:
        ct = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        text = payload.decode(charset, errors="replace") if payload else ""
        if ct == "text/html":
            html = text
        else:
            plain = text
    return plain, html


def _collect_attachments_and_media(msg: Message) -> tuple[list[dict], bool]:
    attachments: list[dict] = []
    has_image_or_video = False

    def consider(part: Message) -> None:
        nonlocal has_image_or_video
        ctype = part.get_content_type()
        maintype = part.get_content_maintype()
        if maintype == "multipart":
            return
        is_media = ctype.startswith("image/") or ctype.startswith("video/")
        if is_media:
            has_image_or_video = True
        cd = str(part.get("Content-Disposition", "")).lower()
        is_attachment = "attachment" in cd
        if not is_attachment and not is_media:
            return
        payload = part.get_payload(decode=True) or b""
        disp = "attachment" if is_attachment else "inline"
        attachments.append(
            {
                "filename": part.get_filename() or ("media" if is_media else "unknown"),
                "content_type": ctype,
                "size": len(payload),
                "disposition": disp,
            }
        )

    if msg.is_multipart():
        for part in msg.walk():
            consider(part)
    else:
        consider(msg)
    return attachments, has_image_or_video


def _parse_raw_email(
    uid: int,
    raw_bytes: bytes,
    account: EmailAccount,
) -> Optional[ParsedEmail]:
    msg = email.message_from_bytes(raw_bytes)

    raw_mid = msg.get("Message-ID", "").strip()
    message_id = raw_mid if raw_mid else f"no-id-{uid}"
    from_address = _decode_header_str(msg.get("From", ""))
    subject = _decode_header_str(msg.get("Subject", ""))

    plain_body, html_body = _get_body_parts(msg)
    attachments, has_image_or_video = _collect_attachments_and_media(msg)

    email_type = detect_email_type(from_address, plain_body)

    if email_type == "TYPE_B":
        real_user_email = extract_real_user_email(html_body, plain_body) or from_address
        language_source_text = extract_feedback_text(html_body, plain_body)
    else:
        real_user_email = from_address
        language_source_text = clean_plain_body(plain_body)

    cleaned_body = clean_plain_body(plain_body)
    thread_id = build_thread_id(msg, fallback_uid=uid)

    return ParsedEmail(
        message_id=message_id,
        thread_id=thread_id,
        from_address=from_address,
        real_user_email=real_user_email,
        received_at_account=account.address,
        subject=subject,
        raw_body=plain_body,
        cleaned_body=cleaned_body,
        language_source_text=language_source_text,
        email_type=email_type,
        attachments=attachments,
        has_image_or_video=has_image_or_video,
        imap_uid=uid,
        imap_folder=account.imap_folder,
        in_reply_to=msg.get("In-Reply-To"),
    )


class ImapPoller:
    """Poll a single IMAP account using SEARCH UNSEEN only (未读邮件)."""

    def __init__(self, account: EmailAccount) -> None:
        self._account = account

    def poll_once(self) -> list[ParsedEmail]:
        """Fetch RFC822 only for UNSEEN messages; 不拉取已读邮件。"""
        parsed_emails: list[ParsedEmail] = []
        acc = self._account

        try:
            with imapclient.IMAPClient(host=acc.imap_host, port=acc.imap_port, ssl=True) as client:
                client.login(acc.address, acc.password)
                client.select_folder(acc.imap_folder)

                uids = client.search(_UNREAD_ONLY_CRITERIA)
                if not uids:
                    return []

                log.info("imap_poll", account=acc.address, unseen_count=len(uids))

                all_account_addresses = {
                    a.address.lower()
                    for a in settings.email_accounts
                }

                messages = client.fetch(uids, ["RFC822"])
                for uid, data in messages.items():
                    raw = data.get(b"RFC822", b"")
                    parsed = _parse_raw_email(uid, raw, acc)
                    if parsed is None:
                        continue
                    sender_addr = parsed.from_address.lower()
                    is_own_sender = any(own in sender_addr for own in all_account_addresses)
                    is_contact_form = parsed.email_type == "TYPE_B"
                    if is_own_sender and not is_contact_form:
                        log.debug("imap_self_sent_skipped", from_address=parsed.from_address)
                        client.add_flags([uid], [imapclient.SEEN])
                        continue
                    if inbound_message_already_stored(parsed.message_id):
                        try_mark_seen_if_inbound_already_answered(parsed.message_id)
                        continue
                    if is_thread_closed(parsed.thread_id):
                        log.info("imap_thread_closed_mark_seen", account=acc.address, uid=uid, thread_id=parsed.thread_id)
                        client.add_flags([uid], [imapclient.SEEN])
                        continue
                    parsed_emails.append(parsed)

        except Exception:
            log.exception("imap_poll_error", account=acc.address)

        return parsed_emails


def poll_all_accounts() -> list[ParsedEmail]:
    """Poll every configured account and return all new parsed emails."""
    all_emails: list[ParsedEmail] = []
    for account in settings.email_accounts:
        poller = ImapPoller(account)
        emails = poller.poll_once()
        all_emails.extend(emails)
        log.info("imap_account_polled", account=account.address, new=len(emails))
    return all_emails


# ---------------------------------------------------------------------------
# IMAP IDLE watcher (push-based, persistent connection)
# ---------------------------------------------------------------------------

class ImapIdleWatcher:
    """Persistent IDLE connection for one IMAP account.

    Lifecycle (per thread):
        run_forever → _idle_loop → [IDLE wait → fetch UNSEEN → dispatch] (repeat)

    On connection failure the watcher backs off exponentially then reconnects.
    """

    _MAX_BACKOFF = 300

    def __init__(
        self,
        account: EmailAccount,
        on_new_emails: Callable[[list[ParsedEmail]], None],
        stop_event: threading.Event,
        idle_renew_seconds: int = 1500,
    ) -> None:
        self._account = account
        self._on_new_emails = on_new_emails
        self._stop = stop_event
        self._renew = idle_renew_seconds
        self._backoff = 5

    # -- public --------------------------------------------------------------

    def run_forever(self) -> None:
        """Entry point — blocks until *stop_event* is set."""
        acc_addr = self._account.address
        while not self._stop.is_set():
            try:
                self._idle_loop()
            except Exception:
                log.exception("idle_loop_error", account=acc_addr, backoff=self._backoff)
                self._stop.wait(self._backoff)
                self._backoff = min(self._backoff * 2, self._MAX_BACKOFF)

    # -- internals -----------------------------------------------------------

    def _idle_loop(self) -> None:
        acc = self._account
        with imapclient.IMAPClient(host=acc.imap_host, port=acc.imap_port, ssl=True) as client:
            client.login(acc.address, acc.password)
            client.select_folder(acc.imap_folder)
            log.info("idle_connected", account=acc.address, folder=acc.imap_folder)
            self._backoff = 5

            self._fetch_and_dispatch(client)

            while not self._stop.is_set():
                client.idle()
                try:
                    responses = client.idle_check(timeout=self._renew)
                finally:
                    client.idle_done()

                if responses:
                    self._fetch_and_dispatch(client)

    def _fetch_and_dispatch(self, client: imapclient.IMAPClient) -> None:
        """SEARCH UNSEEN on the live connection, filter, and dispatch."""
        acc = self._account
        uids = client.search(_UNREAD_ONLY_CRITERIA)
        if not uids:
            return

        all_account_addresses = {a.address.lower() for a in settings.email_accounts}
        parsed_emails: list[ParsedEmail] = []

        messages = client.fetch(uids, ["RFC822"])
        for uid, data in messages.items():
            raw = data.get(b"RFC822", b"")
            parsed = _parse_raw_email(uid, raw, acc)
            if parsed is None:
                continue
            sender_addr = parsed.from_address.lower()
            is_own = any(own in sender_addr for own in all_account_addresses)
            if is_own and parsed.email_type != "TYPE_B":
                client.add_flags([uid], [imapclient.SEEN])
                continue
            if inbound_message_already_stored(parsed.message_id):
                try_mark_seen_if_inbound_already_answered(parsed.message_id)
                continue
            if is_thread_closed(parsed.thread_id):
                log.info("imap_thread_closed_mark_seen", account=acc.address, uid=uid, thread_id=parsed.thread_id)
                client.add_flags([uid], [imapclient.SEEN])
                continue
            parsed_emails.append(parsed)

        if parsed_emails:
            log.info("idle_new_mail", account=acc.address, count=len(parsed_emails))
            self._on_new_emails(parsed_emails)
