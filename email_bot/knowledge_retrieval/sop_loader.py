"""
SOP (Standard Operating Procedure) knowledge source loader.

Handles content assembly from structured SOP fields and batch import
into the kb_documents table as 'sop' entries.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_retrieval.vector_search import upsert_document

log = structlog.get_logger(__name__)


def make_sop_id(title: str, lang: str = "en") -> str:
    """Deterministic ID: sop- + MD5(lang:title)[:12]."""
    raw = f"{lang}:{title}"
    return "sop-" + hashlib.md5(raw.encode()).hexdigest()[:12]


def compose_sop_content(
    title: str,
    symptom: Optional[str] = None,
    steps: Optional[list[str]] = None,
    caution: Optional[str] = None,
) -> str:
    """Build a unified text block from structured SOP fields for embedding.

    The resulting string is stored in ``content`` and used by pgvector
    for semantic retrieval.  Keeping all fields in one prose block
    maximises overlap with natural-language queries.
    """
    parts = [f"Issue: {title}"]

    if symptom:
        parts.append(f"Symptom: {symptom}")

    if steps:
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        parts.append(f"Steps:\n{numbered}")

    if caution:
        parts.append(f"Caution: {caution}")

    return "\n\n".join(parts)


async def upsert_sop(
    sop_data: dict,
    db: AsyncSession,
    *,
    auto_id: bool = True,
) -> str:
    """Validate, compose content, and upsert a single SOP document.

    Args:
        sop_data: Dict with keys matching ``SopImportItem`` fields.
        db: Async SQLAlchemy session.
        auto_id: When True and ``id`` is absent, generate one from title+lang.

    Returns:
        The KB document id.
    """
    title = sop_data["title"]
    lang = sop_data.get("lang", "en")
    kb_id = sop_data.get("id") or (make_sop_id(title, lang) if auto_id else sop_data["id"])

    steps = sop_data.get("steps_json") or sop_data.get("steps")
    symptom = sop_data.get("symptom")
    caution = sop_data.get("caution")

    content = sop_data.get("content") or compose_sop_content(
        title, symptom, steps, caution,
    )

    doc = {
        "id": kb_id,
        "source_type": "sop",
        "title": title,
        "lang": lang,
        "content": content,
        "category": sop_data.get("category"),
        "symptom": symptom,
        "steps_json": steps,
        "caution": caution,
        "official_reply_template": sop_data.get("official_reply_template"),
        "url": sop_data.get("url"),
    }

    await upsert_document(doc, db)
    log.info("sop_upserted", kb_id=kb_id, title=title[:60])
    return kb_id


async def bulk_import_sops(
    items: list[dict],
    db: AsyncSession,
) -> dict:
    """Import a batch of SOP documents.

    Returns:
        Summary dict with ``imported`` count and list of ``ids``.
    """
    ids: list[str] = []
    for item in items:
        kb_id = await upsert_sop(item, db)
        ids.append(kb_id)
    log.info("sop_bulk_import_done", count=len(ids))
    return {"imported": len(ids), "ids": ids}
