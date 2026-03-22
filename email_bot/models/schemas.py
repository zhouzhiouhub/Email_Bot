from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ── AI Output (LLM → Pydantic) ────────────────────────────────────────────────

class ReplyOutput(BaseModel):
    """Strict schema for LLM-generated reply.  Validation failure → human review."""

    reply_body: str = Field(..., min_length=1)
    language: str = Field(..., pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    confidence: float = Field(..., ge=0.0, le=1.0)
    needs_human_review: bool = Field(default=False)
    missing_info_fields: list[str] = Field(default_factory=list)
    cited_kb_ids: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 4)


# ── Email Parsing ─────────────────────────────────────────────────────────────

class ParsedEmail(BaseModel):
    """Normalised representation after mail_gateway processing."""

    message_id: str
    thread_id: str
    from_address: str
    real_user_email: str          # equals from_address for Type A; extracted for Type B
    received_at_account: str      # which inbox received this email (used as reply-from address)
    subject: str
    raw_body: str
    cleaned_body: str
    language_source_text: str     # text slice used for language detection
    email_type: str               # "TYPE_A" | "TYPE_B"
    attachments: list[dict] = Field(default_factory=list)
    has_image_or_video: bool = Field(
        default=False,
        description="Any image/* or video/* part (attachment or inline, e.g. HTML embed).",
    )
    imap_uid: int = Field(..., ge=1)
    imap_folder: str = "INBOX"
    in_reply_to: Optional[str] = None


# ── Extracted Information ─────────────────────────────────────────────────────

class ExtractedInfo(BaseModel):
    os: Optional[str] = None
    device_model: Optional[str] = None
    software_version: Optional[str] = None
    error_text: Optional[str] = None
    use_case: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    intent: Optional[str] = None   # feature_inquiry / bug_report / complaint / refund


# ── Knowledge Retrieval ───────────────────────────────────────────────────────

class KbHit(BaseModel):
    kb_id: str
    title: str
    content: str
    score: float
    source_type: str


# ── DingTalk Review ───────────────────────────────────────────────────────────

class ReviewDecision(BaseModel):
    thread_db_id: int
    action: str                   # "approve" | "edit" | "reject"
    edited_body: Optional[str] = None
    reviewer: Optional[str] = None

    @model_validator(mode="after")
    def edited_body_required_for_edit(self) -> "ReviewDecision":
        if self.action == "edit" and not self.edited_body:
            raise ValueError("edited_body is required when action is 'edit'")
        return self


# ── API Request/Response ──────────────────────────────────────────────────────

class ThreadSummary(BaseModel):
    id: int
    thread_id: str
    customer_email: str
    subject: Optional[str]
    detected_language: Optional[str]
    status: str
    message_count: int
    last_message_at: Optional[str]

    model_config = {"from_attributes": True}


class KbDocumentCreate(BaseModel):
    id: str
    source_type: str
    title: str
    url: Optional[str] = None
    lang: str = "en"
    content: str
    category: Optional[str] = None
    symptom: Optional[str] = None
    steps_json: Optional[list] = None
    caution: Optional[str] = None
    official_reply_template: Optional[str] = None


class SopImportItem(BaseModel):
    """Single SOP entry for bulk import.

    ``id`` is optional — auto-generated from title+lang when omitted.
    ``content`` is optional — auto-composed from symptom/steps/caution when omitted.
    """

    id: Optional[str] = None
    title: str = Field(..., min_length=1)
    lang: str = "en"
    content: Optional[str] = None
    category: Optional[str] = None
    symptom: Optional[str] = None
    steps_json: Optional[list[str]] = Field(None, alias="steps")
    caution: Optional[str] = None
    official_reply_template: Optional[str] = None
    url: Optional[str] = None

    model_config = {"populate_by_name": True}
