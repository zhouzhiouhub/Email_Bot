"""
KB writeback service — convert a resolved TrainingSample into a KbDocument.

Data flow:
    TrainingSample
      ├─ thread → EmailThread.subject → title
      ├─ thread → first INBOUND EmailMessage.cleaned_body → question
      ├─ final_reply → answer
      ├─ language → lang
      ├─ issue_category → category
      └─ id → deterministic KB id: "review-{sample_id}"

The resulting KbDocument has source_type="manual_review" and is immediately
searchable via pgvector after embedding.
"""
from __future__ import annotations

from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import (
    EmailMessage,
    EmailThread,
    KbDocument,
    MessageDirection,
    TrainingSample,
)
from knowledge_retrieval.vector_search import upsert_document

log = structlog.get_logger(__name__)


async def convert_training_to_kb(
    sample_id: int,
    db: AsyncSession,
    *,
    title_override: Optional[str] = None,
    category_override: Optional[str] = None,
    lang_override: Optional[str] = None,
) -> dict:
    """Convert a TrainingSample into a KbDocument (source_type=manual_review).

    Args:
        sample_id: PK of the TrainingSample.
        db: Async SQLAlchemy session.
        title_override: Custom title (defaults to thread subject).
        category_override: Custom category (defaults to issue_category).
        lang_override: Custom language (defaults to detected language).

    Returns:
        Dict with ``kb_id``, ``title``, ``created`` (bool — True if new, False if updated).

    Raises:
        ValueError: If sample not found or has no final_reply.
    """
    sample = await db.get(TrainingSample, sample_id)
    if sample is None:
        raise ValueError(f"TrainingSample {sample_id} not found")
    if not sample.final_reply:
        raise ValueError(f"TrainingSample {sample_id} has no final_reply")

    thread = await db.get(EmailThread, sample.thread_id)

    inbound_row = (
        await db.execute(
            select(
                EmailMessage.cleaned_body,
                EmailMessage.raw_body,
            )
            .where(
                EmailMessage.thread_id == sample.thread_id,
                EmailMessage.direction == MessageDirection.INBOUND,
            )
            .order_by(EmailMessage.created_at.asc())
            .limit(1)
        )
    ).first()

    question = ""
    if inbound_row:
        question = (inbound_row.cleaned_body or inbound_row.raw_body or "").strip()

    subject = (thread.subject or "") if thread else ""
    title = title_override or subject or question[:80] or f"Review sample #{sample_id}"
    lang = lang_override or sample.language or "en"
    category = category_override or (
        sample.issue_category.value if sample.issue_category else None
    )

    kb_id = f"review-{sample_id}"
    content = _compose_content(title, question, sample.final_reply)

    existing = await db.get(KbDocument, kb_id)
    created = existing is None

    doc_data = {
        "id": kb_id,
        "source_type": "manual_review",
        "title": title,
        "lang": lang,
        "content": content,
        "category": category,
    }
    await upsert_document(doc_data, db)

    sample.is_used_for_training = True
    await db.commit()

    log.info(
        "kb_writeback_done",
        kb_id=kb_id,
        sample_id=sample_id,
        created=created,
        title=title[:60],
    )
    return {"kb_id": kb_id, "title": title, "created": created}


def _compose_content(title: str, question: str, answer: str) -> str:
    """Build searchable content combining question and answer."""
    parts = []
    if question:
        parts.append(f"Q: {question[:2000]}")
    else:
        parts.append(f"Q: {title}")
    parts.append(f"A: {answer[:3000]}")
    return "\n\n".join(parts)
