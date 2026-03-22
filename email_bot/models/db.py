from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import enum


class Base(DeclarativeBase):
    pass


# ── Enums ──────────────────────────────────────────────────────────────────────

class ThreadStatus(str, enum.Enum):
    NEW = "NEW"
    PARSED = "PARSED"
    LANGUAGE_DETECTED = "LANGUAGE_DETECTED"
    RETRIEVED = "RETRIEVED"
    DRAFT_GENERATED = "DRAFT_GENERATED"
    AUTO_REPLIED = "AUTO_REPLIED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    NEED_MORE_INFO = "NEED_MORE_INFO"
    WAITING_USER_REPLY = "WAITING_USER_REPLY"
    REPLIED = "REPLIED"
    CLOSED = "CLOSED"


class EmailType(str, enum.Enum):
    TYPE_A = "TYPE_A"   # Direct user email
    TYPE_B = "TYPE_B"   # System-forwarded feedback


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SourceType(str, enum.Enum):
    INNER_FAQ = "inner_faq"
    WEB_FAQ = "web_faq"
    SOP = "sop"
    MANUAL_REVIEW = "manual_review"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"


class QualityLabel(str, enum.Enum):
    CORRECT = "correct"
    MODIFIED = "modified"
    REJECTED = "rejected"


class IssueCategory(str, enum.Enum):
    DEVICE_RECOGNITION = "device_recognition"
    DRIVER_INSTALL = "driver_install"
    SCREEN_SYNC = "screen_sync"
    MUSIC_SYNC = "music_sync"
    SOFTWARE_CRASH = "software_crash"
    LIGHTING_EFFECT = "lighting_effect"
    FEATURE_INQUIRY = "feature_inquiry"
    LICENSE = "license"
    UPDATE = "update"
    OTHER = "other"


# ── Tables ─────────────────────────────────────────────────────────────────────

class EmailThread(Base):
    __tablename__ = "email_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    detected_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status: Mapped[ThreadStatus] = mapped_column(
        Enum(ThreadStatus), default=ThreadStatus.NEW, nullable=False
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["EmailMessage"]] = relationship(
        "EmailMessage", back_populates="thread", cascade="all, delete-orphan"
    )
    drafts: Mapped[list["ReplyDraft"]] = relationship(
        "ReplyDraft", back_populates="thread", cascade="all, delete-orphan"
    )
    review_tasks: Mapped[list["ReviewTask"]] = relationship(
        "ReviewTask", back_populates="thread", cascade="all, delete-orphan"
    )
    training_samples: Mapped[list["TrainingSample"]] = relationship(
        "TrainingSample", back_populates="thread", cascade="all, delete-orphan"
    )


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("email_threads.id"), nullable=False, index=True)
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    raw_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cleaned_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(512), unique=True, nullable=True, index=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_type: Mapped[Optional[EmailType]] = mapped_column(Enum(EmailType), nullable=True)
    real_recipient_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language_source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    imap_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    imap_folder: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    received_inbox: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    thread: Mapped["EmailThread"] = relationship("EmailThread", back_populates="messages")


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    symptom: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps_json: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    caution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    official_reply_template: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ReplyDraft(Base):
    __tablename__ = "reply_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("email_threads.id"), nullable=False, index=True)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retrieval_refs_json: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    thread: Mapped["EmailThread"] = relationship("EmailThread", back_populates="drafts")


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("email_threads.id"), nullable=False, index=True)
    dingtalk_msg_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    reviewer: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, values_callable=lambda x: [e.value for e in x]),
        default=ReviewStatus.PENDING,
        nullable=False,
    )
    reviewed_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    thread: Mapped["EmailThread"] = relationship("EmailThread", back_populates="review_tasks")


class TrainingSample(Base):
    __tablename__ = "training_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("email_threads.id"), nullable=False, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("email_messages.id"), nullable=True)
    customer_email_masked: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    issue_category: Mapped[Optional[IssueCategory]] = mapped_column(
        Enum(IssueCategory, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
        index=True,
    )
    issue_subcategory: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_input_cleaned: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_info: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    kb_hits: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    ai_draft: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolution_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quality_label: Mapped[Optional[QualityLabel]] = mapped_column(
        Enum(QualityLabel, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
        index=True,
    )
    reviewer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_used_for_training: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    thread: Mapped["EmailThread"] = relationship("EmailThread", back_populates="training_samples")
