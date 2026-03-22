"""
KB miss analysis — cluster unanswered questions and suggest FAQ candidates.

Pipeline:
    1. Query recent TrainingSample records where kb_hits is empty/null
    2. Retrieve the original user question (first INBOUND EmailMessage)
    3. Batch-embed all questions via OpenAI
    4. Greedy cosine-similarity clustering
    5. For each cluster (≥ min_cluster_size): pick representative Q, best A
    6. Return clusters sorted by size (largest first)

All business logic lives here (RULE-101 / RULE-404).
"""
from __future__ import annotations

import math
from typing import Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import (
    EmailMessage,
    EmailThread,
    MessageDirection,
    TrainingSample,
)

log = structlog.get_logger(__name__)


async def analyze_kb_misses(
    db: AsyncSession,
    *,
    limit: int = 200,
    similarity_threshold: float = 0.75,
    min_cluster_size: int = 2,
    days: Optional[int] = None,
) -> dict:
    """Cluster no-KB-hit training samples and return suggested FAQ candidates.

    Args:
        db: Async session.
        limit: Max samples to analyse (most recent first).
        similarity_threshold: Cosine similarity to merge into same cluster (0-1).
        min_cluster_size: Only return clusters with at least this many samples.
        days: If set, only consider samples from the last N days.

    Returns:
        Dict with ``total_misses``, ``analyzed``, ``clusters`` list, and
        ``unclustered_count``.
    """
    from knowledge_retrieval.vector_search import embed_text

    samples = await _load_miss_samples(db, limit=limit, days=days)
    if not samples:
        return {
            "total_misses": 0,
            "analyzed": 0,
            "clusters": [],
            "unclustered_count": 0,
        }

    questions = await _load_questions(db, samples)

    texts = [q["question"] for q in questions]
    embeddings = await _batch_embed(texts, embed_text)

    raw_clusters = _greedy_cluster(embeddings, similarity_threshold)

    clusters = []
    unclustered = 0
    for indices in raw_clusters:
        if len(indices) < min_cluster_size:
            unclustered += len(indices)
            continue
        cluster_items = [questions[i] for i in indices]
        representative = max(cluster_items, key=lambda q: len(q["question"]))
        best_answer_item = max(
            (q for q in cluster_items if q["final_reply"]),
            key=lambda q: q["confidence"] or 0.0,
            default=representative,
        )
        clusters.append({
            "size": len(indices),
            "representative_question": representative["question"][:500],
            "suggested_answer": (best_answer_item.get("final_reply") or "")[:1000],
            "languages": sorted(set(q["language"] for q in cluster_items if q["language"])),
            "categories": sorted(set(
                q["issue_category"] for q in cluster_items if q["issue_category"]
            )),
            "sample_ids": [questions[i]["sample_id"] for i in indices],
        })

    clusters.sort(key=lambda c: c["size"], reverse=True)

    log.info(
        "miss_analysis_done",
        analyzed=len(questions),
        clusters=len(clusters),
        unclustered=unclustered,
    )
    return {
        "total_misses": len(samples),
        "analyzed": len(questions),
        "clusters": clusters,
        "unclustered_count": unclustered,
    }


# -- Internal helpers --------------------------------------------------------

async def _load_miss_samples(
    db: AsyncSession, *, limit: int, days: Optional[int],
) -> list[TrainingSample]:
    """Load recent TrainingSample rows where kb_hits is empty or null."""
    from datetime import datetime, timedelta, timezone

    q = (
        select(TrainingSample)
        .where(
            (func.coalesce(func.jsonb_array_length(TrainingSample.kb_hits), 0) == 0)
        )
        .order_by(TrainingSample.created_at.desc())
        .limit(limit)
    )
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.where(TrainingSample.created_at >= cutoff)
    result = await db.execute(q)
    return list(result.scalars().all())


async def _load_questions(
    db: AsyncSession, samples: list[TrainingSample],
) -> list[dict]:
    """For each sample, load the original user question from EmailMessage."""
    thread_ids = [s.thread_id for s in samples]
    rows = (
        await db.execute(
            select(
                EmailMessage.thread_id,
                EmailMessage.cleaned_body,
                EmailMessage.raw_body,
            )
            .where(
                EmailMessage.thread_id.in_(thread_ids),
                EmailMessage.direction == MessageDirection.INBOUND,
            )
            .distinct(EmailMessage.thread_id)
            .order_by(EmailMessage.thread_id, EmailMessage.created_at.asc())
        )
    ).all()

    body_map: dict[int, str] = {}
    for row in rows:
        body = (row.cleaned_body or row.raw_body or "").strip()
        if body and row.thread_id not in body_map:
            body_map[row.thread_id] = body

    questions = []
    for s in samples:
        q_text = body_map.get(s.thread_id, "")
        if not q_text:
            continue
        questions.append({
            "sample_id": s.id,
            "thread_id": s.thread_id,
            "question": q_text,
            "final_reply": s.final_reply,
            "confidence": s.confidence,
            "language": s.language,
            "issue_category": s.issue_category.value if s.issue_category else None,
        })
    return questions


async def _batch_embed(texts: list[str], embed_fn) -> list[list[float]]:
    """Embed all texts. Uses the existing OpenAI embedder (batched internally)."""
    from langchain_openai import OpenAIEmbeddings
    from config import settings

    embedder = OpenAIEmbeddings(
        model=settings.openai_embedding_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    truncated = [t[:8000] for t in texts]
    return await embedder.aembed_documents(truncated)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _greedy_cluster(
    embeddings: list[list[float]], threshold: float,
) -> list[list[int]]:
    """O(n²) greedy clustering — good enough for n ≤ 200."""
    n = len(embeddings)
    assigned: set[int] = set()
    clusters: list[list[int]] = []

    for i in range(n):
        if i in assigned:
            continue
        cluster = [i]
        assigned.add(i)
        for j in range(i + 1, n):
            if j in assigned:
                continue
            if _cosine_similarity(embeddings[i], embeddings[j]) >= threshold:
                cluster.append(j)
                assigned.add(j)
        clusters.append(cluster)

    return clusters
