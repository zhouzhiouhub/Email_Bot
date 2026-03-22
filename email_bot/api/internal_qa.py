"""
Internal Q&A tester — simplified pipeline preview for company staff.

Routes:
  GET  /internal/qa      — HTML page (question + answer)
  POST /internal/qa/ask  — Run pipeline, return answer only
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from config import settings
from human_review.confidence_router import RouteDecision, decide_route
from knowledge_retrieval.vector_search import search_kb
from message_understanding.info_extractor import extract_info
from message_understanding.language_detector import detect_language
from models.schemas import ParsedEmail
from response_generator.draft_builder import build_draft
from response_generator.kb_gap_escalation import is_operator_kb_gap_reply
from response_generator.polisher import polish_reply

log = structlog.get_logger(__name__)

router = APIRouter()


class InternalQARequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)


async def _run_qa(db: AsyncSession, question: str) -> dict:
    """Run the pipeline on a plain question and return a simplified result."""
    acc = settings.email_accounts[0] if settings.email_accounts else None
    if not acc:
        raise HTTPException(status_code=500, detail="EMAIL_ACCOUNTS not configured")

    uid = uuid.uuid4().hex[:16]
    parsed = ParsedEmail(
        message_id=f"<qa.{uid}@internal>",
        thread_id=f"qa-{uid}",
        from_address="internal@test.local",
        real_user_email="internal@test.local",
        received_at_account=acc.address,
        subject="Internal QA",
        raw_body=question,
        cleaned_body=question,
        language_source_text=question,
        email_type="TYPE_A",
        attachments=[],
        has_image_or_video=False,
        imap_uid=1,
        imap_folder="INBOX",
        in_reply_to=None,
    )

    lang = detect_language(parsed.language_source_text)
    info = extract_info(parsed.cleaned_body)
    hits = await search_kb(parsed.cleaned_body, db)
    reply, kb_gap = await build_draft(
        email_body=parsed.cleaned_body,
        detected_language=lang,
        extracted_info=info,
        kb_hits=hits,
    )

    if is_operator_kb_gap_reply(reply.reply_body):
        polished = reply.reply_body
    else:
        polished = await polish_reply(reply.reply_body)

    decision, reason = decide_route(
        reply=reply,
        extracted=info,
        email_body=parsed.cleaned_body,
        thread_message_count=1,
        is_sensitive=False,
        has_image_or_video=False,
        kb_hits=hits,
    )
    if kb_gap:
        decision = RouteDecision.HUMAN_REVIEW

    return {
        "answer": polished,
        "route": decision.value,
        "route_reason": reason,
        "language": lang,
        "confidence": reply.confidence,
        "kb_gap": kb_gap,
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def internal_qa_page():
    return HTMLResponse(content=_PAGE_HTML)


@router.post("/ask")
async def internal_qa_ask(req: InternalQARequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await _run_qa(db, req.question.strip())
    except HTTPException:
        raise
    except Exception:
        log.exception("internal_qa_failed")
        raise HTTPException(status_code=500, detail="Pipeline execution failed")
    return result


_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Email Bot AI Q&A — Internal</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    :root {
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface-2: #232733;
      --text: #e4e6ed;
      --muted: #7a7f8e;
      --accent: #6c5ce7;
      --accent-hover: #7c6ff7;
      --border: #2a2e3a;
      --green: #00b894;
      --orange: #fdcb6e;
      --red: #e17055;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "DM Sans", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .container {
      width: 100%;
      max-width: 780px;
      padding: 3rem 1.5rem 4rem;
    }
    header {
      text-align: center;
      margin-bottom: 2.5rem;
    }
    header h1 {
      font-size: 1.75rem;
      font-weight: 700;
      letter-spacing: -0.03em;
    }
    header h1 span {
      color: var(--accent);
    }
    header p {
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 0.4rem;
    }
    .qa-box {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.75rem;
    }
    .input-area {
      position: relative;
    }
    textarea {
      width: 100%;
      min-height: 120px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem 1.15rem;
      font-family: inherit;
      font-size: 0.95rem;
      color: var(--text);
      resize: vertical;
      outline: none;
      transition: border-color .2s;
    }
    textarea:focus {
      border-color: var(--accent);
    }
    textarea::placeholder {
      color: var(--muted);
    }
    .btn-row {
      display: flex;
      justify-content: flex-end;
      margin-top: 0.85rem;
    }
    button {
      font-family: inherit;
      font-weight: 600;
      font-size: 0.95rem;
      padding: 0.65rem 1.6rem;
      border: none;
      border-radius: 10px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      transition: background .15s, transform .1s;
    }
    button:hover { background: var(--accent-hover); }
    button:active { transform: scale(0.97); }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    .divider {
      height: 1px;
      background: var(--border);
      margin: 1.5rem 0;
    }
    .answer-area {
      min-height: 80px;
    }
    .answer-label {
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 0.75rem;
    }
    .answer-text {
      font-size: 0.95rem;
      line-height: 1.75;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--text);
    }
    .answer-text.placeholder {
      color: var(--muted);
      font-style: italic;
    }
    .meta {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-top: 1rem;
    }
    .tag {
      font-size: 0.72rem;
      font-weight: 600;
      padding: 0.2rem 0.6rem;
      border-radius: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .tag-auto { background: rgba(0,184,148,.15); color: var(--green); }
    .tag-human { background: rgba(253,203,110,.15); color: var(--orange); }
    .tag-more { background: rgba(225,112,85,.15); color: var(--red); }
    .tag-lang { background: rgba(108,92,231,.15); color: var(--accent); }
    .tag-conf { background: rgba(122,127,142,.15); color: var(--muted); }
    .loading {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      color: var(--muted);
    }
    .loading .dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: var(--accent);
      animation: pulse 1.2s ease-in-out infinite;
    }
    .loading .dot:nth-child(2) { animation-delay: 0.2s; }
    .loading .dot:nth-child(3) { animation-delay: 0.4s; }
    @keyframes pulse {
      0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
      40% { opacity: 1; transform: scale(1.1); }
    }
    .error { color: var(--red); font-size: 0.9rem; }
    .shortcut-hint {
      text-align: center;
      color: var(--muted);
      font-size: 0.78rem;
      margin-top: 1rem;
    }
    .shortcut-hint kbd {
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.1rem 0.4rem;
      font-family: inherit;
      font-size: 0.75rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Email Bot <span>AI</span> Q&A</h1>
      <p>Internal testing tool — pipeline preview without sending emails</p>
    </header>
    <div class="qa-box">
      <div class="input-area">
        <textarea id="question" placeholder="Type a customer question here..."></textarea>
        <div class="btn-row">
          <button id="askBtn" type="button">Ask AI</button>
        </div>
      </div>
      <div class="divider"></div>
      <div class="answer-area">
        <div class="answer-label">AI Response</div>
        <div id="answer" class="answer-text placeholder">Waiting for your question...</div>
        <div id="meta" class="meta" hidden></div>
      </div>
    </div>
    <div class="shortcut-hint">
      <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to send
    </div>
  </div>
  <script>
    const $ = id => document.getElementById(id);
    const routeClass = { AUTO_SEND: "tag-auto", HUMAN_REVIEW: "tag-human", MORE_INFO: "tag-more" };
    const routeLabel = { AUTO_SEND: "Auto Reply", HUMAN_REVIEW: "Human Review", MORE_INFO: "Need More Info" };

    async function askAI() {
      const q = $("question").value.trim();
      if (!q) return;
      const btn = $("askBtn");
      const ans = $("answer");
      const meta = $("meta");
      btn.disabled = true;
      meta.hidden = true;
      ans.className = "answer-text";
      ans.innerHTML = '<span class="loading"><span class="dot"></span><span class="dot"></span><span class="dot"></span> Thinking...</span>';
      try {
        const r = await fetch("/internal/qa/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || r.statusText);
        ans.textContent = data.answer || "(no answer)";
        const rc = data.route || "";
        const conf = data.confidence != null ? Math.round(data.confidence * 100) : "?";
        meta.innerHTML =
          '<span class="tag ' + (routeClass[rc] || "tag-lang") + '">' + (routeLabel[rc] || rc) + '</span>' +
          '<span class="tag tag-lang">Lang: ' + esc(data.language || "?") + '</span>' +
          '<span class="tag tag-conf">Confidence: ' + conf + '%</span>' +
          (data.kb_gap ? '<span class="tag tag-human">KB Gap</span>' : '');
        meta.hidden = false;
      } catch (e) {
        ans.innerHTML = '<span class="error">' + esc(e.message) + '</span>';
      } finally {
        btn.disabled = false;
      }
    }

    $("askBtn").onclick = askAI;
    $("question").addEventListener("keydown", e => {
      if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); askAI(); }
    });

    function esc(s) {
      if (s == null) return "";
      return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    }
  </script>
</body>
</html>
"""
