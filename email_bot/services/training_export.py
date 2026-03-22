"""
Training data export service.

Streams TrainingSample rows as JSONL for fine-tuning pipelines.
Filters: quality_label, language, is_used_for_training, issue_category.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import TrainingSample


def _sample_to_export_dict(s: TrainingSample) -> dict:
    """Map ORM row to a flat dict suitable for training export (no PII)."""
    return {
        "id": s.id,
        "thread_id": s.thread_id,
        "language": s.language,
        "issue_category": s.issue_category.value if s.issue_category else None,
        "issue_subcategory": s.issue_subcategory,
        "user_input_cleaned": s.user_input_cleaned,
        "extracted_info": s.extracted_info,
        "kb_hits": s.kb_hits,
        "ai_draft": s.ai_draft,
        "final_reply": s.final_reply,
        "confidence": s.confidence,
        "resolution_type": s.resolution_type,
        "quality_label": s.quality_label.value if s.quality_label else None,
        "reviewer_note": s.reviewer_note,
        "is_used_for_training": s.is_used_for_training,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


async def stream_training_export(
    db: AsyncSession,
    *,
    quality_label: Optional[str] = None,
    language: Optional[str] = None,
    issue_category: Optional[str] = None,
    is_used_for_training: Optional[bool] = None,
    exclude_used: bool = False,
) -> AsyncIterator[str]:
    """
    Yield one JSONL line per matching TrainingSample.

    The caller wraps this in a StreamingResponse so the entire table
    is never buffered in memory.
    """
    query = select(TrainingSample).order_by(TrainingSample.id)

    if quality_label:
        query = query.where(TrainingSample.quality_label == quality_label)
    if language:
        query = query.where(TrainingSample.language == language)
    if issue_category:
        query = query.where(TrainingSample.issue_category == issue_category)
    if is_used_for_training is not None:
        query = query.where(TrainingSample.is_used_for_training == is_used_for_training)
    if exclude_used:
        query = query.where(TrainingSample.is_used_for_training == False)  # noqa: E712

    result = await db.execute(query)
    for row in result.scalars():
        yield json.dumps(_sample_to_export_dict(row), ensure_ascii=False) + "\n"
