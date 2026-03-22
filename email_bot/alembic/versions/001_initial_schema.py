"""Initial schema with pgvector extension

Revision ID: 001
Revises:
Create Date: 2026-03-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "email_threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(512), nullable=False),
        sa.Column("customer_email", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(1024), nullable=True),
        sa.Column("detected_language", sa.String(16), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "NEW", "PARSED", "LANGUAGE_DETECTED", "RETRIEVED", "DRAFT_GENERATED",
                "AUTO_REPLIED", "PENDING_HUMAN_REVIEW", "HUMAN_APPROVED", "HUMAN_REJECTED",
                "NEED_MORE_INFO", "WAITING_USER_REPLY", "REPLIED", "CLOSED",
                name="threadstatus",
            ),
            nullable=False,
            server_default="NEW",
        ),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index("ix_email_threads_thread_id", "email_threads", ["thread_id"])
    op.create_index("ix_email_threads_customer_email", "email_threads", ["customer_email"])

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("inbound", "outbound", name="messagedirection"),
            nullable=False,
        ),
        sa.Column("raw_body", sa.Text(), nullable=True),
        sa.Column("cleaned_body", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("message_id", sa.String(512), nullable=True),
        sa.Column("in_reply_to", sa.String(512), nullable=True),
        sa.Column(
            "email_type",
            sa.Enum("TYPE_A", "TYPE_B", name="emailtype"),
            nullable=True,
        ),
        sa.Column("real_recipient_email", sa.String(255), nullable=True),
        sa.Column("language_source_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["email_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index("ix_email_messages_thread_id", "email_messages", ["thread_id"])
    op.create_index("ix_email_messages_message_id", "email_messages", ["message_id"])

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("inner_faq", "web_faq", "sop", "manual_review", name="sourcetype"),
            nullable=False,
        ),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("lang", sa.String(8), nullable=False, server_default="en"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("symptom", sa.Text(), nullable=True),
        sa.Column("steps_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("caution", sa.Text(), nullable=True),
        sa.Column("official_reply_template", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_documents_source_type", "kb_documents", ["source_type"])
    op.create_index("ix_kb_documents_category", "kb_documents", ["category"])

    op.create_table(
        "reply_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("draft_body", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("retrieval_refs_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["email_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reply_drafts_thread_id", "reply_drafts", ["thread_id"])

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("dingtalk_msg_id", sa.String(256), nullable=True),
        sa.Column("reviewer", sa.String(128), nullable=True),
        sa.Column(
            "review_status",
            sa.Enum("pending", "approved", "modified", "rejected", name="reviewstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_body", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["email_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "training_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("customer_email_masked", sa.String(255), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column(
            "issue_category",
            sa.Enum(
                "device_recognition", "driver_install", "screen_sync", "music_sync",
                "software_crash", "lighting_effect", "feature_inquiry", "license",
                "update", "other",
                name="issuecategory",
            ),
            nullable=True,
        ),
        sa.Column("issue_subcategory", sa.String(64), nullable=True),
        sa.Column("user_input_cleaned", sa.Text(), nullable=True),
        sa.Column("extracted_info", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("kb_hits", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("ai_draft", sa.Text(), nullable=True),
        sa.Column("final_reply", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("resolution_type", sa.String(64), nullable=True),
        sa.Column(
            "quality_label",
            sa.Enum("correct", "modified", "rejected", name="qualitylabel"),
            nullable=True,
        ),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("is_used_for_training", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["email_threads.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["email_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_samples_thread_id", "training_samples", ["thread_id"])
    op.create_index("ix_training_samples_issue_category", "training_samples", ["issue_category"])
    op.create_index("ix_training_samples_quality_label", "training_samples", ["quality_label"])

    # ivfflat vector index for cosine similarity search
    op.execute(
        "CREATE INDEX ix_kb_documents_embedding ON kb_documents "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_table("training_samples")
    op.drop_table("review_tasks")
    op.drop_table("reply_drafts")
    op.drop_table("kb_documents")
    op.drop_table("email_messages")
    op.drop_table("email_threads")
    op.execute("DROP TYPE IF EXISTS qualitylabel")
    op.execute("DROP TYPE IF EXISTS issuecategory")
    op.execute("DROP TYPE IF EXISTS reviewstatus")
    op.execute("DROP TYPE IF EXISTS sourcetype")
    op.execute("DROP TYPE IF EXISTS emailtype")
    op.execute("DROP TYPE IF EXISTS messagedirection")
    op.execute("DROP TYPE IF EXISTS threadstatus")
