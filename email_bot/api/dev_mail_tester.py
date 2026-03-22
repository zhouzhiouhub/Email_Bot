"""
Local dev-only mail flow preview: language → extract → KB → draft → route.
Does not send email, DingTalk, or write to the workflow DB.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from config import settings
from mail_gateway.thread_tracker import clean_plain_body
from human_review.confidence_router import RouteDecision, decide_route
from knowledge_retrieval.vector_search import search_kb
from message_understanding.info_extractor import (
    extract_info,
    is_sensitive,
    is_software_acquisition_issue,
    needs_video_evidence,
    should_request_more_info,
)
from message_understanding.language_detector import detect_language
from models.schemas import ParsedEmail
from response_generator.draft_builder import build_draft
from response_generator.kb_gap_escalation import is_operator_kb_gap_reply
from response_generator.polisher import polish_reply

log = structlog.get_logger(__name__)

router = APIRouter()


class DevMailRunRequest(BaseModel):
    subject: str = ""
    customer_email: str = Field(default="tester@example.com")
    body: str = Field(..., min_length=1)
    has_image_or_video: bool = False


def _require_inbox_account():
    accounts = settings.email_accounts
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail="请在 .env 中配置 EMAIL_ACCOUNTS（至少需要一条收件账号，用于预览中的 received_at_account）。",
        )
    return accounts[0]


def _synthetic_parsed(req: DevMailRunRequest) -> ParsedEmail:
    acc = _require_inbox_account()
    uid = uuid.uuid4().hex[:16]
    cleaned = clean_plain_body(req.body)
    return ParsedEmail(
        message_id=f"<dev.{uid}@local.test>",
        thread_id=f"dev-{uid}",
        from_address=req.customer_email,
        real_user_email=req.customer_email,
        received_at_account=acc.address,
        subject=req.subject.strip() or "(no subject)",
        raw_body=req.body,
        cleaned_body=cleaned,
        language_source_text=cleaned,
        email_type="TYPE_A",
        attachments=[],
        has_image_or_video=req.has_image_or_video,
        imap_uid=1,
        imap_folder=acc.imap_folder,
        in_reply_to=None,
    )


async def _run_preview(db: AsyncSession, parsed: ParsedEmail) -> dict[str, Any]:
    lang = detect_language(parsed.language_source_text)
    info = extract_info(parsed.cleaned_body)
    sensitive = is_sensitive(parsed.cleaned_body)
    video = needs_video_evidence(parsed.cleaned_body)
    more_info = should_request_more_info(parsed.cleaned_body, info)
    hits = await search_kb(parsed.cleaned_body, db)
    reply, kb_gap_operator_only = await build_draft(
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
        is_sensitive=sensitive,
        has_image_or_video=parsed.has_image_or_video,
        kb_hits=hits,
    )
    if kb_gap_operator_only:
        decision = RouteDecision.HUMAN_REVIEW
        reason = (
            "KB gap / undocumented version — draft is internal-only for reviewers; "
            "no customer email until a human composes one"
        )
    elif (
        more_info
        and not parsed.has_image_or_video
        and not is_software_acquisition_issue(parsed.cleaned_body)
    ):
        decision = RouteDecision.MORE_INFO
        reason = "Missing required info fields"
    return {
        "kb_gap_operator_only": kb_gap_operator_only,
        "parsed_preview": {
            "thread_id": parsed.thread_id,
            "customer_email": parsed.real_user_email,
            "subject": parsed.subject,
            "has_image_or_video": parsed.has_image_or_video,
        },
        "detected_language": lang,
        "extracted_info": info.model_dump(),
        "flags": {
            "is_sensitive": sensitive,
            "needs_video_evidence": video,
            "needs_more_info": more_info,
        },
        "kb_hits": [h.model_dump() for h in hits],
        "reply_output": reply.model_dump(),
        "polished_body": polished,
        "route_decision": decision.value,
        "route_reason": reason,
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def mail_tester_page():
    return HTMLResponse(content=_PAGE_HTML)


@router.post("/run")
async def mail_tester_run(req: DevMailRunRequest, db: AsyncSession = Depends(get_db)):
    parsed = _synthetic_parsed(req)
    try:
        result = await _run_preview(db, parsed)
    except Exception:
        log.exception("dev_mail_preview_failed")
        raise HTTPException(status_code=500, detail="预览流程执行失败，查看服务端日志。")
    return result


_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>邮件客服 AI — 本地预览</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Outfit:wght@400;600;700&display=swap" rel="stylesheet"/>
  <style>
    :root {
      --bg: #f4f0e8;
      --surface: #fffdf8;
      --ink: #1c1b18;
      --muted: #5c5850;
      --accent: #0d6b62;
      --accent-dim: #0a524b;
      --border: #d8d0c4;
      --warn: #8b5a00;
      --err: #9e2a2b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Outfit", system-ui, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.5;
      min-height: 100vh;
    }
    .wrap { max-width: 920px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }
    header h1 {
      font-weight: 700;
      font-size: 1.65rem;
      letter-spacing: -0.02em;
      margin: 0 0 0.35rem;
    }
    header p { margin: 0; color: var(--muted); font-size: 0.95rem; }
    .badge {
      display: inline-block;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: var(--accent);
      color: #fff;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      margin-left: 0.5rem;
      vertical-align: middle;
    }
    .grid {
      display: grid;
      gap: 1.25rem;
      margin-top: 1.5rem;
    }
    @media (min-width: 800px) {
      .grid-split { grid-template-columns: 1fr 1fr; }
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem 1.35rem;
      box-shadow: 0 2px 12px rgba(28,27,24,.05);
    }
    .card h2 {
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      margin: 0 0 0.85rem;
    }
    label { display: block; font-size: 0.88rem; font-weight: 600; margin-bottom: 0.35rem; }
    input[type="text"], input[type="email"], textarea {
      width: 100%;
      font-family: inherit;
      font-size: 0.95rem;
      padding: 0.6rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fff;
    }
    textarea { min-height: 160px; resize: vertical; }
    .row { margin-bottom: 0.9rem; }
    .row:last-child { margin-bottom: 0; }
    .chk { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; margin-top: 0.75rem; }
    .chk input { width: auto; }
    button.primary {
      font-family: inherit;
      font-weight: 600;
      font-size: 1rem;
      padding: 0.65rem 1.4rem;
      border: none;
      border-radius: 8px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      margin-top: 1rem;
    }
    button.primary:hover { background: var(--accent-dim); }
    button.primary:disabled { opacity: 0.55; cursor: not-allowed; }
    .note { font-size: 0.82rem; color: var(--muted); margin-top: 0.75rem; }
    #out { margin-top: 1.5rem; }
    .result-block { margin-bottom: 1rem; }
    .result-block h3 {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      margin: 0 0 0.4rem;
    }
    .pill {
      display: inline-block;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 600;
      background: #e8f5f3;
      color: var(--accent-dim);
    }
    pre, .mono {
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 0.8rem;
      background: #f0ebe3;
      padding: 0.75rem 1rem;
      border-radius: 8px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
    }
    .err { color: var(--err); font-size: 0.9rem; margin-top: 0.75rem; }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>邮件客服 AI <span class="badge">Dev</span></h1>
      <p>本地预览：语言检测 → 信息抽取 → 知识库检索 → 草稿与路由（不写库、不发信）。需 APP_ENV=development。</p>
    </header>
    <div class="grid grid-split">
      <div class="card">
        <h2>用户来信</h2>
        <div class="row">
          <label for="subject">主题</label>
          <input id="subject" type="text" placeholder="例如：USB 无法识别"/>
        </div>
        <div class="row">
          <label for="email">用户邮箱</label>
          <input id="email" type="email" value="tester@example.com"/>
        </div>
        <div class="row">
          <label for="body">正文</label>
          <textarea id="body" placeholder="输入用户问题正文…"></textarea>
        </div>
        <label class="chk">
          <input id="media" type="checkbox"/>
          邮件含图片或视频（将强制转人工预览）
        </label>
        <button class="primary" type="button" id="send">运行预览</button>
        <p class="note">调用 POST /dev/mail-tester/run。请确保 Postgres、OpenAI 与知识库可用。</p>
        <div id="err" class="err" hidden></div>
      </div>
      <div class="card" id="out">
        <h2>回复与路由</h2>
        <p class="muted" id="placeholder" style="color:var(--muted);margin:0;font-size:0.95rem;">尚无结果，点击左侧「运行预览」。</p>
        <div id="content" hidden></div>
      </div>
    </div>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    $("send").onclick = async () => {
      const err = $("err");
      const ph = $("placeholder");
      const box = $("content");
      err.hidden = true;
      const body = $("body").value.trim();
      if (!body) { err.textContent = "请填写正文"; err.hidden = false; return; }
      const btn = $("send");
      btn.disabled = true;
      ph.hidden = false;
      box.hidden = true;
      ph.textContent = "运行中…";
      try {
        const r = await fetch("/dev/mail-tester/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            subject: $("subject").value,
            customer_email: $("email").value || "tester@example.com",
            body,
            has_image_or_video: $("media").checked,
          }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          let d = data.detail;
          if (Array.isArray(d)) d = d.map(function(x) { return x.msg || JSON.stringify(x); }).join("; ");
          throw new Error(d || r.statusText || "请求失败");
        }
        ph.hidden = true;
        box.hidden = false;
        const ro = data.reply_output || {};
        const hits = (data.kb_hits || []).map(h =>
          h.title + " — score " + (h.score != null ? h.score.toFixed(3) : "?") + " — " + h.kb_id
        ).join("\\n") || "(无命中)";
        box.innerHTML =
          '<div class="result-block"><h3>检测语言</h3><p class="pill">' + esc(data.detected_language) + '</p></div>' +
          (data.kb_gap_operator_only ? '<div class="result-block"><h3>KB 缺口</h3><p class="pill">内部草稿 · 不得自动发给用户</p></div>' : '') +
          '<div class="result-block"><h3>路由</h3><p><strong>' + esc(data.route_decision) + '</strong> — ' + esc(data.route_reason) + '</p></div>' +
          '<div class="result-block"><h3>标志</h3><pre class="mono">' + esc(JSON.stringify(data.flags, null, 2)) + '</pre></div>' +
          '<div class="result-block"><h3>抽取信息</h3><pre class="mono">' + esc(JSON.stringify(data.extracted_info, null, 2)) + '</pre></div>' +
          '<div class="result-block"><h3>知识库命中</h3><pre class="mono">' + esc(hits) + '</pre></div>' +
          '<div class="result-block"><h3>模型输出（ReplyOutput）</h3><pre class="mono">' + esc(JSON.stringify(ro, null, 2)) + '</pre></div>' +
          '<div class="result-block"><h3>润色后正文（拟发送）</h3><pre class="mono">' + esc(data.polished_body || "") + '</pre></div>';
      } catch (e) {
        ph.textContent = "";
        ph.hidden = true;
        err.textContent = String(e.message || e);
        err.hidden = false;
      } finally {
        btn.disabled = false;
      }
    };
    function esc(s) {
      if (s == null) return "";
      return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }
  </script>
</body>
</html>
"""
