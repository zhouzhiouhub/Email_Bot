"""
Knowledge base vector search using pgvector + cosine similarity.

All queries are embedded in English to support cross-language retrieval.
Phase 2: wide recall from pgvector, then lexical overlap reranking (no extra LLM calls).
"""
from __future__ import annotations

import re
from typing import Optional

import structlog
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.db import KbDocument
from models.schemas import KbHit

log = structlog.get_logger(__name__)

_embedder = OpenAIEmbeddings(
    model=settings.openai_embedding_model,
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)

_DEFAULT_TOP_K = 8       # retrieve more candidates for better context
_DEFAULT_THRESHOLD = 0.28  # lower threshold enables fuzzy / partial matches

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """Lightweight tokens: ASCII/alnum words + single CJK/Hangul/Kana chars for overlap."""
    if not text:
        return set()
    lowered = text.lower()
    out = set(_TOKEN_RE.findall(lowered))
    for ch in lowered:
        if ("\u4e00" <= ch <= "\u9fff") or ("\u3040" <= ch <= "\u30ff") or ("\uac00" <= ch <= "\ud7af"):
            out.add(ch)
    return out


def _lexical_overlap(query: str, title: str, content: str) -> float:
    q = _tokenize(query)
    if not q:
        return 0.0
    doc = _tokenize(f"{title}\n{content[:2000]}")
    if not doc:
        return 0.0
    inter = len(q & doc)
    return min(1.0, inter / len(q))


def _rerank_hits(
    query: str,
    hits: list[KbHit],
    top_k: int,
    vector_weight: float,
    lexical_weight: float,
) -> list[KbHit]:
    """
    Order by blended vector + lexical score; each KbHit.score stays the original cosine
    similarity so confidence_router and auto-send thresholds stay calibrated.
    """
    if not hits:
        return []
    wsum = vector_weight + lexical_weight
    if wsum <= 0:
        vw, lw = 1.0, 0.0
    else:
        vw, lw = vector_weight / wsum, lexical_weight / wsum

    ranked: list[tuple[float, KbHit]] = []
    for h in hits:
        lex = _lexical_overlap(query, h.title, h.content)
        combined = vw * float(h.score) + lw * lex
        ranked.append((combined, h))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in ranked[:top_k]]


async def embed_text(text_content: str) -> list[float]:
    """Return the embedding vector for a single text string."""
    vectors = await _embedder.aembed_documents([text_content])
    return vectors[0]


async def search_kb(
    query: str,
    db: AsyncSession,
    top_k: int = _DEFAULT_TOP_K,
    threshold: float = _DEFAULT_THRESHOLD,
    source_types: Optional[list[str]] = None,
) -> list[KbHit]:
    """
    Embed the query and return the top-k knowledge base hits above the threshold.

    Args:
        query: The user's question (any language; will be embedded as English).
        db: Async SQLAlchemy session.
        top_k: Maximum number of results.
        threshold: Minimum cosine similarity (0–1) to include a result.
        source_types: Optional filter for source types (e.g. ["inner_faq"]).

    Returns:
        List of KbHit ordered by reranked relevance; each ``score`` is still the cosine
        similarity from pgvector (order may differ from raw vector ranking).
    """
    query_vector = await embed_text(query)
    vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"

    recall_limit = max(top_k, settings.kb_recall_limit)

    # Build source_type filter
    filter_clause = ""
    if source_types:
        quoted = ", ".join(f"'{st}'" for st in source_types)
        filter_clause = f"AND source_type IN ({quoted})"

    # Inline the vector literal to avoid `:param::type` cast conflicts with asyncpg
    sql = text(f"""
        SELECT
            id,
            title,
            content,
            source_type,
            1 - (embedding <=> '{vector_str}'::vector) AS score
        FROM kb_documents
        WHERE embedding IS NOT NULL
        {filter_clause}
        ORDER BY embedding <=> '{vector_str}'::vector
        LIMIT :recall_limit
    """)

    result = await db.execute(sql, {"recall_limit": recall_limit})
    rows = result.fetchall()

    vector_hits = [
        KbHit(
            kb_id=row.id,
            title=row.title,
            content=row.content,
            score=float(row.score),
            source_type=row.source_type,
        )
        for row in rows
        if float(row.score) >= threshold
    ]

    hits = _rerank_hits(
        query,
        vector_hits,
        top_k,
        settings.kb_rerank_vector_weight,
        settings.kb_rerank_lexical_weight,
    )

    log.info(
        "kb_search",
        query_preview=query[:60],
        recall=len(vector_hits),
        hits=len(hits),
        rerank=True,
    )
    return hits


async def upsert_document(doc_data: dict, db: AsyncSession) -> None:
    """Insert or update a KB document, computing its embedding."""
    kb_id = doc_data["id"]
    content = doc_data["content"]

    embedding = await embed_text(content)
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

    # Check if exists
    existing = await db.get(KbDocument, kb_id)
    if existing:
        for key, value in doc_data.items():
            setattr(existing, key, value)
        await db.execute(
            text(f"UPDATE kb_documents SET embedding = '{vector_str}'::vector WHERE id = :id"),
            {"id": kb_id},
        )
    else:
        doc = KbDocument(**doc_data)
        db.add(doc)
        await db.flush()
        await db.execute(
            text(f"UPDATE kb_documents SET embedding = '{vector_str}'::vector WHERE id = :id"),
            {"id": kb_id},
        )

    await db.commit()
    log.info("kb_upserted", kb_id=kb_id)
