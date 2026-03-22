"""
Mail type detection, thread ID resolution, deduplication, and reply address extraction.

Type A: Direct user email — reply to From address.
Type B: System-forwarded feedback — must extract real user email from HTML body.
"""
from __future__ import annotations

import hashlib
import re
from email.message import Message
from typing import Optional

import structlog
from bs4 import BeautifulSoup

from config import settings

log = structlog.get_logger(__name__)

# Regex patterns for Type B body parsing
_EMAIL_RE = r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"

_TYPE_B_EMAIL_PATTERNS = [
    # Contact-form style (website form submissions)
    re.compile(r"邮箱[：:]\s*" + _EMAIL_RE, re.IGNORECASE),
    re.compile(r"用户邮箱[：:]\s*" + _EMAIL_RE, re.IGNORECASE),
    re.compile(r"Email[：:]\s*" + _EMAIL_RE, re.IGNORECASE),
    re.compile(r"email address[：:]\s*" + _EMAIL_RE, re.IGNORECASE),
    re.compile(r"E-?mail[：:]\s*" + _EMAIL_RE, re.IGNORECASE),
]

_TYPE_B_FEEDBACK_PATTERNS = [
    re.compile(r"内容[：:]\s*(.+?)(?=\n昵称|\n邮箱|\n时间|\Z)", re.DOTALL),
    re.compile(r"反馈内容[：:]\s*(.+?)(?=\n|$)", re.DOTALL),
    re.compile(r"Feedback[：:]\s*(.+?)(?=\n|$)", re.DOTALL),
    re.compile(r"Content[：:]\s*(.+?)(?=\n|$)", re.DOTALL),
]

# Contact-form body signature: contains "昵称:" and "邮箱:" and "内容:"
_CONTACT_FORM_RE = re.compile(r"昵称[：:].*?邮箱[：:].+?内容[：:]", re.DOTALL | re.IGNORECASE)


def detect_email_type(from_address: str, plain_body: str = "") -> str:
    """Return 'TYPE_A' or 'TYPE_B' based on the sender address or body pattern."""
    sender = from_address.lower().strip()
    for known_sender in settings.type_b_sender_list:
        if sender == known_sender or sender.endswith(f"<{known_sender}>"):
            return "TYPE_B"
    # Detect contact-form submissions by body structure regardless of sender
    if plain_body and _CONTACT_FORM_RE.search(plain_body):
        return "TYPE_B"
    return "TYPE_A"


def extract_real_user_email(html_body: str, plain_body: str) -> Optional[str]:
    """
    Extract the actual user email from a Type B system email.
    Tries HTML first, then plain text regex patterns.
    """
    # Try to parse from HTML structure
    if html_body:
        soup = BeautifulSoup(html_body, "html.parser")
        text = soup.get_text(separator="\n")
        for pattern in _TYPE_B_EMAIL_PATTERNS:
            m = pattern.search(text)
            if m:
                return m.group(1).strip()

    # Fallback to plain text
    for pattern in _TYPE_B_EMAIL_PATTERNS:
        m = pattern.search(plain_body)
        if m:
            return m.group(1).strip()

    log.warning("type_b_email_extraction_failed", snippet=plain_body[:200])
    return None


def extract_feedback_text(html_body: str, plain_body: str) -> str:
    """
    For Type B: extract only the user's feedback content (not the template labels).
    Used as language_source_text to avoid being misled by Chinese template labels.
    """
    if html_body:
        soup = BeautifulSoup(html_body, "html.parser")
        text = soup.get_text(separator="\n")
        for pattern in _TYPE_B_FEEDBACK_PATTERNS:
            m = pattern.search(text)
            if m:
                return m.group(1).strip()

    for pattern in _TYPE_B_FEEDBACK_PATTERNS:
        m = pattern.search(plain_body)
        if m:
            return m.group(1).strip()

    # Fallback: return plain body as-is
    return plain_body


def build_thread_id(message: Message, fallback_uid: int = 0) -> str:
    """
    Derive a stable thread ID from References/In-Reply-To headers.
    If neither exists, use the Message-ID itself as the thread root.
    Never returns an empty string.
    """
    references = message.get("References", "")
    in_reply_to = message.get("In-Reply-To", "")
    message_id  = message.get("Message-ID", "").strip().strip("<>")

    if references:
        first_ref = references.strip().split()[0].strip("<>")
        if first_ref:
            return first_ref
    if in_reply_to:
        tid = in_reply_to.strip().strip("<>")
        if tid:
            return tid
    if message_id:
        return message_id
    # Absolute fallback: uid-based unique ID
    return f"no-id-{fallback_uid}"


def compute_dedup_key(message_id: str) -> str:
    """SHA-256 of the Message-ID for deduplication checks in Redis."""
    return hashlib.sha256(message_id.encode()).hexdigest()


def clean_plain_body(raw: str) -> str:
    """
    Remove quoted reply sections and excessive whitespace from plain text body.
    """
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        # Drop standard quoted-reply lines
        if stripped.startswith(">"):
            continue
        if re.match(r"^On .+ wrote:$", stripped):
            continue
        lines.append(line)

    return "\n".join(lines).strip()
