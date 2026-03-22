"""
Confidence-based routing decision.

Determines whether a reply draft should be:
  - AUTO_SEND   : directly emailed to the user
  - HUMAN_REVIEW: queued for DingTalk review
  - MORE_INFO   : request more details from the user
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Optional

import structlog

from config import settings
from message_understanding.info_extractor import is_software_acquisition_issue
from models.schemas import ExtractedInfo, KbHit, ReplyOutput

log = structlog.get_logger(__name__)


class RouteDecision(str, Enum):
    AUTO_SEND = "AUTO_SEND"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    MORE_INFO = "MORE_INFO"


def compute_adjusted_confidence(
    llm_confidence: float,
    kb_hits: list[KbHit],
) -> float:
    """
    Blend LLM self-reported confidence with KB retrieval quality.

    KB quality signal:
    - top_score   : best cosine similarity from vector search (0–1)
    - hit_density : how many hits scored above 0.5 (normalized to 0–1)

    Final score = 0.55 * llm + 0.30 * top_score + 0.15 * hit_density
    Clamped to [0, 1] and rounded to 4 dp.
    """
    if not kb_hits:
        # No KB evidence — LLM is flying blind; penalise slightly
        return round(min(llm_confidence * 0.85, 1.0), 4)

    top_score = max(h.score for h in kb_hits)
    strong_hits = sum(1 for h in kb_hits if h.score >= 0.5)
    hit_density = min(strong_hits / 3.0, 1.0)   # saturates at 3 strong hits

    adjusted = (
        0.55 * llm_confidence
        + 0.30 * top_score
        + 0.15 * hit_density
    )
    adjusted = round(min(max(adjusted, 0.0), 1.0), 4)
    log.debug(
        "confidence_adjusted",
        llm=llm_confidence,
        top_kb=round(top_score, 3),
        hit_density=round(hit_density, 3),
        adjusted=adjusted,
    )
    return adjusted


def decide_route(
    reply: ReplyOutput,
    extracted: ExtractedInfo,
    email_body: str,
    thread_message_count: int = 1,
    is_sensitive: bool = False,
    has_image_or_video: bool = False,
    kb_hits: Optional[list[KbHit]] = None,
) -> tuple[RouteDecision, str]:
    """
    Return (decision, reason) tuple.

    Routing rules (in priority order):
    1. Pydantic validation failed (empty reply body) → HUMAN_REVIEW
    2. Sensitive topics (refund/warranty/DRM) → HUMAN_REVIEW
    2b. Image or video attached → HUMAN_REVIEW
    2c. Download / installer acquisition (pre-install) → HUMAN_REVIEW
    3. Thread has > 3 messages (complex context) → HUMAN_REVIEW
    4. Body < 20 chars or ≥ 2 missing info fields → MORE_INFO
    5. Use KB-adjusted confidence for final routing
    6. Low adjusted confidence → HUMAN_REVIEW
    7. High adjusted confidence ≥ auto_threshold → AUTO_SEND
    8. Medium adjusted confidence → HUMAN_REVIEW
    """
    # Rule 1: Validation failure
    if not reply.reply_body.strip():
        return RouteDecision.HUMAN_REVIEW, "LLM output validation failed (empty body)"

    # Rule 2: Sensitive content — always human review
    if is_sensitive:
        return RouteDecision.HUMAN_REVIEW, "Sensitive topic detected (refund/warranty/DRM)"

    # Rule 2b: Image or video in the email — always human review
    if has_image_or_video:
        return RouteDecision.HUMAN_REVIEW, "Email contains image or video (manual review required)"

    # Rule 2c: User cannot obtain the software yet (download / site / mirror).
    # KB hits are often irrelevant post-install FAQ; do not substitute with MORE_INFO.
    if is_software_acquisition_issue(email_body):
        return (
            RouteDecision.HUMAN_REVIEW,
            "Software download or installer acquisition issue (pre-install); "
            "needs human handling — KB entries are mainly for post-install troubleshooting",
        )

    # Rule 3: Long thread
    if thread_message_count > 3:
        return RouteDecision.HUMAN_REVIEW, f"Thread has {thread_message_count} messages (complex context)"

    # Rule 4: Insufficient info
    if len(email_body.strip()) < 20:
        return RouteDecision.MORE_INFO, "Email body too short (< 20 chars)"
    if len(extracted.missing_fields) >= 2:
        return RouteDecision.MORE_INFO, f"Missing info fields: {extracted.missing_fields}"

    # Compute KB-adjusted confidence
    adjusted = compute_adjusted_confidence(reply.confidence, kb_hits or [])

    # Rule 5: Low adjusted confidence
    if adjusted < settings.confidence_human_review:
        return RouteDecision.HUMAN_REVIEW, f"Low adjusted confidence ({adjusted:.2f})"

    # Rule 6: High confidence — auto-send (cited_kb_ids OR strong KB evidence)
    has_kb_evidence = bool(reply.cited_kb_ids) or (
        kb_hits is not None and any(h.score >= 0.45 for h in kb_hits)
    )
    if adjusted >= settings.confidence_auto_reply and has_kb_evidence:
        return RouteDecision.AUTO_SEND, f"High adjusted confidence ({adjusted:.2f}) with KB evidence"

    # Rule 7: Medium confidence → human review
    return (
        RouteDecision.HUMAN_REVIEW,
        f"Medium adjusted confidence ({adjusted:.2f}), pending human review",
    )
