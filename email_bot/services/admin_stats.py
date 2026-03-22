"""
Admin overview statistics (aggregations only; no side effects).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import (
    EmailThread,
    KbDocument,
    ReplyDraft,
    ThreadStatus,
    TrainingSample,
)


async def compute_admin_overview_stats(db: AsyncSession) -> dict:
    """
    Return thread / review / training / KB counts plus KB retrieval coverage
    from persisted drafts and completed training samples.
    """
    total_threads = (await db.execute(select(func.count(EmailThread.id)))).scalar()
    pending_review = (
        await db.execute(
            select(func.count(EmailThread.id)).where(
                EmailThread.status == ThreadStatus.PENDING_HUMAN_REVIEW
            )
        )
    ).scalar()
    auto_replied = (
        await db.execute(
            select(func.count(EmailThread.id)).where(
                EmailThread.status == ThreadStatus.AUTO_REPLIED
            )
        )
    ).scalar()
    total_samples = (await db.execute(select(func.count(TrainingSample.id)))).scalar()
    total_kb = (await db.execute(select(func.count(KbDocument.id)))).scalar()

    _kb_hit_len = func.coalesce(func.jsonb_array_length(TrainingSample.kb_hits), 0)
    training_samples_no_kb_hit = (
        await db.execute(select(func.count(TrainingSample.id)).where(_kb_hit_len == 0))
    ).scalar()
    training_samples_with_kb_hit = (
        await db.execute(select(func.count(TrainingSample.id)).where(_kb_hit_len > 0))
    ).scalar()

    _ref_len = func.coalesce(func.jsonb_array_length(ReplyDraft.retrieval_refs_json), 0)
    reply_drafts_no_retrieval_refs = (
        await db.execute(select(func.count(ReplyDraft.id)).where(_ref_len == 0))
    ).scalar()
    reply_drafts_with_retrieval_refs = (
        await db.execute(select(func.count(ReplyDraft.id)).where(_ref_len > 0))
    ).scalar()

    rate: float | None = None
    if total_samples and total_samples > 0:
        rate = round(training_samples_no_kb_hit / total_samples, 4)

    return {
        "total_threads": total_threads,
        "pending_review": pending_review,
        "auto_replied": auto_replied,
        "total_training_samples": total_samples,
        "total_kb_documents": total_kb,
        "training_samples_no_kb_hit": training_samples_no_kb_hit,
        "training_samples_with_kb_hit": training_samples_with_kb_hit,
        "training_samples_no_kb_hit_rate": rate,
        "reply_drafts_no_retrieval_refs": reply_drafts_no_retrieval_refs,
        "reply_drafts_with_retrieval_refs": reply_drafts_with_retrieval_refs,
    }
