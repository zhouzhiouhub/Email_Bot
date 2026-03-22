"""
Ops/Admin API router.

Endpoints:
  GET  /admin/threads              - List email threads with status/filter
  GET  /admin/threads/{id}         - Thread detail
  GET  /admin/training             - List training samples
  GET  /admin/training/export      - JSONL export of training data
  GET  /admin/kb                   - List KB documents
  POST /admin/kb                   - Add/update KB document
  POST /admin/kb/sop/import        - Bulk import SOP documents
  POST /admin/kb/from-training/{id} - Convert training sample to KB entry
  GET  /admin/kb/miss-analysis     - Cluster no-KB-hit questions and suggest FAQs
  GET  /admin/stats                - Overview statistics (+ KB hit coverage)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db import (
    EmailThread,
    KbDocument,
    TrainingSample,
)
from models.schemas import KbDocumentCreate, SopImportItem, ThreadSummary
from knowledge_retrieval.vector_search import upsert_document
from knowledge_retrieval.sop_loader import bulk_import_sops
from api.deps import get_db
from services.admin_stats import compute_admin_overview_stats
from services.kb_writeback import convert_training_to_kb
from services.miss_analyzer import analyze_kb_misses
from services.training_export import stream_training_export

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(
    status: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    query = select(EmailThread).order_by(EmailThread.updated_at.desc())
    if status:
        query = query.where(EmailThread.status == status)
    if language:
        query = query.where(EmailThread.detected_language == language)
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    threads = result.scalars().all()
    return [
        ThreadSummary(
            id=t.id,
            thread_id=t.thread_id,
            customer_email=t.customer_email,
            subject=t.subject,
            detected_language=t.detected_language,
            status=t.status.value,
            message_count=t.message_count,
            last_message_at=t.last_message_at.isoformat() if t.last_message_at else None,
        )
        for t in threads
    ]


@router.get("/threads/{thread_db_id}")
async def get_thread(thread_db_id: int, db: AsyncSession = Depends(get_db)):
    thread = await db.get(EmailThread, thread_db_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {
        "thread": ThreadSummary(
            id=thread.id,
            thread_id=thread.thread_id,
            customer_email=thread.customer_email,
            subject=thread.subject,
            detected_language=thread.detected_language,
            status=thread.status.value,
            message_count=thread.message_count,
            last_message_at=thread.last_message_at.isoformat() if thread.last_message_at else None,
        ),
    }


@router.get("/training")
async def list_training_samples(
    quality_label: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    query = select(TrainingSample).order_by(TrainingSample.created_at.desc())
    if quality_label:
        query = query.where(TrainingSample.quality_label == quality_label)
    if language:
        query = query.where(TrainingSample.language == language)
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    samples = result.scalars().all()
    return {"items": [s.__dict__ for s in samples], "total": len(samples)}


@router.get("/training/export")
async def export_training_samples(
    quality_label: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    issue_category: Optional[str] = Query(None),
    is_used_for_training: Optional[bool] = Query(None),
    exclude_used: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Stream training samples as JSONL (one JSON object per line)."""
    return StreamingResponse(
        stream_training_export(
            db,
            quality_label=quality_label,
            language=language,
            issue_category=issue_category,
            is_used_for_training=is_used_for_training,
            exclude_used=exclude_used,
        ),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=training_samples.jsonl"},
    )


@router.get("/kb")
async def list_kb_documents(
    source_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    query = select(KbDocument).order_by(KbDocument.updated_at.desc())
    if source_type:
        query = query.where(KbDocument.source_type == source_type)
    if category:
        query = query.where(KbDocument.category == category)
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    docs = result.scalars().all()
    return {"items": [d.__dict__ for d in docs], "total": len(docs)}


@router.post("/kb", status_code=201)
async def create_or_update_kb(
    body: KbDocumentCreate,
    db: AsyncSession = Depends(get_db),
):
    await upsert_document(body.model_dump(), db)
    return {"status": "ok", "id": body.id}


@router.post("/kb/sop/import", status_code=201)
async def import_sop_documents(
    items: list[SopImportItem],
    db: AsyncSession = Depends(get_db),
):
    """Bulk import SOP documents into the knowledge base.

    Accepts a JSON array of SOP items. Each item's ``content`` is auto-composed
    from structured fields (symptom, steps, caution) when not provided explicitly.
    ``id`` is auto-generated from title+lang when omitted.
    """
    raw = [item.model_dump(by_alias=False, exclude_none=True) for item in items]
    result = await bulk_import_sops(raw, db)
    return result


@router.post("/kb/from-training/{sample_id}", status_code=201)
async def convert_training_to_kb_entry(
    sample_id: int,
    title: Optional[str] = Query(None, description="Custom title (default: thread subject)"),
    category: Optional[str] = Query(None, description="Custom category (default: issue_category)"),
    lang: Optional[str] = Query(None, description="Custom language (default: detected language)"),
    db: AsyncSession = Depends(get_db),
):
    """One-click: convert a resolved TrainingSample into a searchable KB entry.

    The Q&A pair is embedded and immediately available for future retrieval.
    The training sample is marked ``is_used_for_training=True``.
    """
    try:
        result = await convert_training_to_kb(
            sample_id, db,
            title_override=title,
            category_override=category,
            lang_override=lang,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.get("/kb/miss-analysis")
async def kb_miss_analysis(
    limit: int = Query(200, le=500, description="Max samples to analyse"),
    similarity: float = Query(0.75, ge=0.5, le=0.95, description="Cosine similarity threshold"),
    min_cluster: int = Query(2, ge=1, le=20, description="Minimum cluster size to report"),
    days: Optional[int] = Query(None, ge=1, description="Only last N days"),
    db: AsyncSession = Depends(get_db),
):
    """Cluster no-KB-hit questions and suggest FAQ candidates.

    Each cluster groups semantically similar user questions that had no KB match.
    Use ``sample_ids`` with ``POST /admin/kb/from-training/{id}`` to convert
    promising candidates into KB entries.
    """
    return await analyze_kb_misses(
        db,
        limit=limit,
        similarity_threshold=similarity,
        min_cluster_size=min_cluster,
        days=days,
    )


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    return await compute_admin_overview_stats(db)
