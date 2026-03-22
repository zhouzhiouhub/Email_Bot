"""
Training data collection and privacy masking.

- Writes TrainingSample records after each completed interaction.
- Masks customer email (user@example.com → ***@example.com).
- Auto-assigns quality_label based on review outcome.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import (
    TrainingSample,
    QualityLabel,
    ReviewStatus,
    IssueCategory,
)
from models.schemas import ExtractedInfo, KbHit, ReplyOutput

log = structlog.get_logger(__name__)

_EMAIL_MASK_RE = re.compile(r"^([^@]+)(@.+)$")


def mask_email(email: str) -> str:
    """Replace local part with *** for privacy."""
    m = _EMAIL_MASK_RE.match(email)
    if m:
        return "***" + m.group(2)
    return "***@unknown"


def _map_review_status_to_quality(review_status: Optional[str]) -> Optional[QualityLabel]:
    mapping = {
        ReviewStatus.APPROVED: QualityLabel.CORRECT,
        ReviewStatus.MODIFIED: QualityLabel.MODIFIED,
        ReviewStatus.REJECTED: QualityLabel.REJECTED,
        "approved": QualityLabel.CORRECT,
        "modified": QualityLabel.MODIFIED,
        "rejected": QualityLabel.REJECTED,
        "approve": QualityLabel.CORRECT,
        "edit": QualityLabel.MODIFIED,
        "reject": QualityLabel.REJECTED,
    }
    return mapping.get(review_status)


async def record_training_sample(
    db: AsyncSession,
    thread_db_id: int,
    message_db_id: Optional[int],
    customer_email: str,
    detected_language: str,
    extracted_info: ExtractedInfo,
    kb_hits: list[KbHit],
    reply_output: ReplyOutput,
    final_reply_text: str,
    resolution_type: str,
    review_status: Optional[str] = None,
    reviewer_note: Optional[str] = None,
    issue_category: Optional[str] = None,
) -> TrainingSample:
    """
    Persist a training sample record.  Email is masked before storage.
    """
    quality = _map_review_status_to_quality(review_status)

    # Map issue_category string to enum
    category_enum: Optional[IssueCategory] = None
    if issue_category:
        try:
            category_enum = IssueCategory(issue_category)
        except ValueError:
            category_enum = IssueCategory.OTHER

    sample = TrainingSample(
        thread_id=thread_db_id,
        message_id=message_db_id,
        customer_email_masked=mask_email(customer_email),
        language=detected_language,
        issue_category=category_enum,
        user_input_cleaned=None,  # set by caller if needed
        extracted_info=extracted_info.model_dump(),
        kb_hits=[h.model_dump() for h in kb_hits],
        ai_draft=reply_output.reply_body,
        final_reply=final_reply_text,
        confidence=reply_output.confidence,
        resolution_type=resolution_type,
        quality_label=quality,
        reviewer_note=reviewer_note,
        is_used_for_training=False,
    )

    db.add(sample)
    await db.commit()
    await db.refresh(sample)

    log.info(
        "training_sample_recorded",
        sample_id=sample.id,
        quality=quality,
        language=detected_language,
    )
    return sample
