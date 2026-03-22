"""
FastAPI application entry point.

Routes:
  POST /webhook/email       - Receive parsed email (from Celery worker)
  POST /review/action       - Human review callback (approve/edit/reject)
  GET  /review/edit/{id}    - Full review page (HTML): thread timeline, KB refs, editable draft
  GET  /health              - Health check (includes langgraph_checkpointer: postgres|memory)

Admin routes are mounted under /admin (see ops_admin/router.py).
In APP_ENV=development, GET /dev/mail-tester/ serves a local HTML preview UI.
"""
from __future__ import annotations

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contextlib import asynccontextmanager

from api.deps import get_db
from config import settings
from models.db import EmailThread, ReviewTask
from models.schemas import ParsedEmail, ReviewDecision
from ops_admin.router import router as admin_router
from workflow.graph import init_graph
import workflow.graph as _graph_module

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_graph()
    yield


app = FastAPI(
    title="Email Auto-Processing Bot",
    description="Automated email customer service with AI-powered replies and human-in-the-loop review",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)

from api.internal_qa import router as internal_qa_router
app.include_router(internal_qa_router, prefix="/internal/qa", tags=["internal"])

if settings.app_env == "development":
    from api.dev_mail_tester import router as dev_mail_tester_router

    app.include_router(dev_mail_tester_router, prefix="/dev/mail-tester", tags=["dev"])


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "email-auto-processing-bot",
        "langgraph_checkpointer": _graph_module.checkpointer_backend,
    }


# ── Email Ingestion ────────────────────────────────────────────────────────────

class InboundEmailPayload(BaseModel):
    parsed_email: dict


@app.post("/webhook/email", status_code=202)
async def receive_email(payload: InboundEmailPayload):
    """
    Called by the Celery worker after parsing an inbound email.
    Launches the LangGraph workflow asynchronously.
    """
    parsed = payload.parsed_email
    thread_id = parsed.get("thread_id", "unknown")

    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "parsed_email": parsed,
        "detected_language": "en",
        "extracted_info": None,
        "kb_hits": [],
        "reply_output": None,
        "polished_body": None,
        "route_decision": None,
        "route_reason": None,
        "thread_db_id": None,
        "message_db_id": None,
        "draft_db_id": None,
        "review_task_db_id": None,
        "is_sensitive": False,
        "needs_more_info": False,
        "needs_video": False,
        "kb_gap_operator_only": False,
        "review_action": None,
        "reviewed_body": None,
        "final_reply_sent": False,
        "resolution_type": None,
        "error": None,
    }

    try:
        result = await _graph_module.email_graph.ainvoke(initial_state, config)
        if result and result.get("error") in (
            "already_processed",
            "duplicate_message",
        ):
            log.info("email_workflow_skipped", reason=result.get("error"), thread_id=thread_id)
            return {"status": "skipped", "thread_id": thread_id}
    except Exception:
        log.exception("workflow_launch_failed", thread_id=thread_id)
        raise HTTPException(status_code=500, detail="Workflow execution failed")

    return {"status": "accepted", "thread_id": thread_id}


# ── Human Review Callback ──────────────────────────────────────────────────────

async def _execute_review_action(
    thread_id: int,
    action: str,
    edited_body: str | None,
    db: AsyncSession,
) -> dict:
    """Core review logic shared by GET and POST handlers."""
    if action not in ("approve", "edit", "reject"):
        raise HTTPException(status_code=400, detail="Invalid action")
    if action == "edit" and not edited_body:
        raise HTTPException(status_code=400, detail="edited_body is required for 'edit' action")

    thread = await db.get(EmailThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    config = {"configurable": {"thread_id": thread.thread_id}}
    resume_value = {"action": action, "edited_body": edited_body}

    try:
        await _graph_module.email_graph.ainvoke(Command(resume=resume_value), config)
    except Exception:
        log.exception("review_resume_failed", thread_id=thread_id)
        raise HTTPException(status_code=500, detail="Failed to resume workflow")

    return {"action": action, "thread_id": thread_id, "subject": thread.subject or ""}


@app.get("/review/action", response_class=None)
async def review_action_get(
    thread_id: int = Query(...),
    action: str = Query(...),
    edited_body: str = Query(None),
    reviewer: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Notification link callback — opens in browser.
    Executes the review action and shows a confirmation page.
    """
    from fastapi.responses import HTMLResponse

    try:
        result = await _execute_review_action(thread_id, action, edited_body, db)
    except HTTPException as e:
        html_err = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Operation Failed</title>
        <style>body{{font-family:-apple-system,sans-serif;display:flex;justify-content:center;
        align-items:center;min-height:100vh;margin:0;background:#f5f5f5;}}
        .card{{background:white;border-radius:12px;padding:40px 48px;text-align:center;
        box-shadow:0 4px 24px rgba(0,0,0,.1);max-width:420px;width:90%;}}
        .icon{{font-size:56px;margin-bottom:16px;}}
        .msg{{color:#ef4444;font-size:18px;font-weight:600;margin-bottom:8px;}}
        .detail{{color:#888;font-size:13px;}}</style></head>
        <body><div class="card"><div class="icon">⚠️</div>
        <div class="msg">Operation Failed</div>
        <div class="detail">{e.detail}</div></div></body></html>"""
        return HTMLResponse(html_err, status_code=200)

    action_labels = {"approve": "✅ Approved & Sent", "reject": "❌ Rejected", "edit": "✏️ Edited & Sent"}
    action_colors = {"approve": "#22c55e", "reject": "#ef4444", "edit": "#3b82f6"}
    label = action_labels.get(action, action)
    color = action_colors.get(action, "#666")
    subject = result["subject"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Review Complete</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center;
            align-items: center; min-height: 100vh; margin: 0; background: #f5f5f5; }}
    .card {{ background: white; border-radius: 12px; padding: 40px 48px; text-align: center;
             box-shadow: 0 4px 24px rgba(0,0,0,.1); max-width: 420px; width: 90%; }}
    .icon {{ font-size: 56px; margin-bottom: 16px; }}
    .status {{ font-size: 24px; font-weight: 700; color: {color}; margin-bottom: 8px; }}
    .subject {{ color: #666; font-size: 14px; margin-bottom: 24px; word-break: break-all; }}
    .close {{ color: #999; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{'✅' if action=='approve' else ('✏️' if action=='edit' else '❌')}</div>
    <div class="status">{label}</div>
    <div class="subject">Subject: {subject}</div>
    <div class="close">You may close this page</div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


@app.post("/review/action")
async def review_action_post(
    thread_id: int = Query(...),
    action: str = Query(...),
    edited_body: str = Query(None),
    reviewer: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """POST version for programmatic use."""
    result = await _execute_review_action(thread_id, action, edited_body, db)
    return {"status": "ok", **result}


# ── Full Review Page ───────────────────────────────────────────────────────────

@app.get("/review/edit/{thread_db_id}", response_class=None)
async def review_edit_form(
    thread_db_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Full review page: thread message timeline + KB refs + editable AI draft + actions."""
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select
    from models.db import EmailMessage, MessageDirection, ReplyDraft

    thread = await db.get(EmailThread, thread_db_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    draft_result = await db.execute(
        select(ReplyDraft).where(ReplyDraft.thread_id == thread_db_id).order_by(ReplyDraft.id.desc())
    )
    draft_obj = draft_result.scalars().first()
    draft_body = draft_obj.draft_body if draft_obj else ""
    confidence = int((draft_obj.confidence or 0) * 100) if draft_obj else 0
    needs_hr = draft_obj.needs_human_review if draft_obj else True
    kb_refs = draft_obj.retrieval_refs_json if draft_obj and draft_obj.retrieval_refs_json else []

    msg_result = await db.execute(
        select(EmailMessage).where(EmailMessage.thread_id == thread_db_id).order_by(EmailMessage.id.asc())
    )
    messages = msg_result.scalars().all()

    def esc(s: str) -> str:
        return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"','&quot;')

    approve_url = f"/review/action?thread_id={thread_db_id}&action=approve"
    reject_url = f"/review/action?thread_id={thread_db_id}&action=reject"

    kb_items_html = ""
    for ref in kb_refs:
        if not isinstance(ref, dict):
            continue
        rid = esc(str(ref.get("kb_id", "")))
        rtitle = esc(str(ref.get("title", "")))
        rst = esc(str(ref.get("source_type", "")))
        sc = ref.get("score")
        try:
            pct = f"{float(sc) * 100:.1f}%" if sc is not None else "—"
        except (TypeError, ValueError):
            pct = "—"
        kb_items_html += (
            f'<li><span class="kb-id">{rid}</span> · <span class="kb-score">{pct}</span> '
            f'<span class="kb-type">({rst})</span><br><span class="kb-title">{rtitle}</span></li>'
        )
    if not kb_items_html:
        kb_items_html = '<li class="kb-empty">(No KB references)</li>'

    thread_timeline_html = ""
    if not messages:
        thread_timeline_html = '<p class="thread-empty">(No messages yet)</p>'
    else:
        for idx, m in enumerate(messages, start=1):
            if m.direction == MessageDirection.INBOUND:
                label = "Inbound"
                css_dir = "inbound"
            else:
                label = "Outbound"
                css_dir = "outbound"
            ts = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "—"
            body = (m.cleaned_body or "").strip() or "(empty)"
            thread_timeline_html += (
                f'<div class="thread-msg {css_dir}">'
                f'<div class="thread-msg-meta">#{idx} · {label} · {esc(ts)}</div>'
                f'<div class="thread-msg-body">{esc(body)}</div>'
                f"</div>"
            )

    hr_badge = (
        '<span class="badge warn">Needs Review</span>'
        if needs_hr
        else '<span class="badge ok">AI Auto-OK</span>'
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Email Review — #{thread_db_id}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
          background:linear-gradient(165deg,#eef2f7 0%,#e8ecf4 50%,#f4f6fa 100%);
          min-height:100vh;padding:20px 16px 40px}}
    .container{{max-width:1100px;margin:0 auto}}
    h1{{font-size:20px;font-weight:700;color:#141820;margin-bottom:6px;letter-spacing:-.02em}}
    .meta{{font-size:13px;color:#5c6578;margin-bottom:20px;line-height:1.6}}
    .badge{{display:inline-block;border-radius:999px;padding:3px 11px;font-size:11px;font-weight:700;
            vertical-align:middle}}
    .badge.mid{{background:#fff4e5;color:#b45309}}
    .badge.ok{{background:#dcfce7;color:#166534}}
    .badge.warn{{background:#fee2e2;color:#b91c1c}}
    .layout{{display:grid;grid-template-columns:1fr;gap:16px}}
    @media (min-width:960px){{.layout{{grid-template-columns:1fr 1.05fr}}}}
    .card{{background:#fff;border-radius:14px;padding:22px 26px;
           box-shadow:0 2px 8px rgba(15,23,42,.06),0 0 1px rgba(15,23,42,.08);margin-bottom:0}}
    .card-title{{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
                 letter-spacing:.08em;margin-bottom:14px}}
    .thread-timeline{{max-height:520px;overflow-y:auto;display:flex;flex-direction:column;gap:12px;
                     padding-right:4px}}
    .thread-msg{{border-radius:10px;padding:14px 16px;border-left:4px solid #94a3b8;background:#f8fafc}}
    .thread-msg.inbound{{border-left-color:#2563eb;background:#f1f5f9}}
    .thread-msg.outbound{{border-left-color:#16a34a;background:#f0fdf4}}
    .thread-msg-meta{{font-size:11px;color:#64748b;margin-bottom:8px;font-weight:600;
                      font-family:ui-monospace,monospace}}
    .thread-msg-body{{white-space:pre-wrap;font-size:14px;color:#334155;line-height:1.75}}
    .thread-empty{{color:#94a3b8;font-size:14px;font-style:italic}}
    .kb-list{{list-style:none;font-size:13px;color:#334155;line-height:1.5}}
    .kb-list li{{padding:10px 0;border-bottom:1px solid #f1f5f9}}
    .kb-list li:last-child{{border-bottom:none}}
    .kb-id{{font-family:ui-monospace,monospace;font-size:12px;color:#0f172a}}
    .kb-score{{font-weight:700;color:#2563eb}}
    .kb-type{{color:#94a3b8;font-size:12px}}
    .kb-title{{display:block;margin-top:4px;color:#475569}}
    .kb-empty{{color:#94a3b8;font-style:italic}}
    textarea{{width:100%;border:1px solid #cbd5e1;border-radius:10px;padding:16px;
              font-size:14px;line-height:1.75;color:#0f172a;resize:vertical;
              min-height:300px;outline:none;font-family:inherit;background:#fafbfc}}
    textarea:focus{{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12)}}
    .actions{{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}}
    .btn{{flex:1;min-width:140px;padding:14px 20px;border:none;border-radius:11px;
          font-size:15px;font-weight:600;cursor:pointer;transition:transform .12s,filter .12s}}
    .btn:hover{{filter:brightness(1.04)}}
    .btn:active{{transform:scale(.98)}}
    .btn-approve{{background:#16a34a;color:white}}
    .btn-edit{{background:#2563eb;color:white}}
    .btn-reject{{background:#fff;color:#dc2626;border:2px solid #fecaca}}
    .hint{{font-size:12px;color:#94a3b8;margin-top:12px;text-align:center;line-height:1.5}}
    .sticky-draft{{position:sticky;top:16px}}
  </style>
</head>
<body>
  <div class="container">
    <h1>Email Review</h1>
    <div class="meta">
      From: <b>{esc(thread.customer_email or "")}</b> &nbsp;·&nbsp;
      Subject: <b>{esc(thread.subject or "(no subject)")}</b><br>
      Language: <b>{esc(thread.detected_language or "?")}</b> &nbsp;·&nbsp;
      Messages: <b>{thread.message_count or 0}</b> &nbsp;·&nbsp;
      <span class="badge {'mid' if confidence < 80 else ''}">Confidence {confidence}%</span>
      &nbsp;{hr_badge}
    </div>

    <div class="layout">
      <div>
        <div class="card" style="margin-bottom:16px">
          <div class="card-title">Conversation History (chronological)</div>
          <div class="thread-timeline">{thread_timeline_html}</div>
        </div>
        <div class="card">
          <div class="card-title">KB References (used for draft generation)</div>
          <ul class="kb-list">{kb_items_html}</ul>
        </div>
      </div>
      <div class="sticky-draft">
        <div class="card">
          <div class="card-title">AI Draft Reply (editable)</div>
          <form id="editForm" method="GET" action="/review/action">
            <input type="hidden" name="thread_id" value="{thread_db_id}">
            <input type="hidden" name="action" value="edit">
            <textarea name="edited_body" id="draftArea">{esc(draft_body)}</textarea>
            <div class="actions">
              <button type="button" class="btn btn-approve"
                onclick="location.href='{approve_url}'">✅ Approve & Send</button>
              <button type="submit" class="btn btn-edit">✏️ Send Edited</button>
              <button type="button" class="btn btn-reject"
                onclick="if(confirm('Reject and close this thread?'))location.href='{reject_url}'">❌ Reject</button>
            </div>
            <p class="hint">Approve = send AI original &nbsp;|&nbsp; Send Edited = send textarea content</p>
          </form>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)
