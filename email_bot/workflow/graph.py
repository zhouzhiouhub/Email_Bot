"""
LangGraph main workflow for email processing.

State machine flow:
  parse_email → detect_language → extract_info → check_sensitive
    → check_info_completeness → retrieve_knowledge → generate_draft
    → route_decision
      ├─ AUTO_SEND   → send_auto_reply → archive_training → END
      ├─ HUMAN_REVIEW → push_to_dingtalk → wait_human_review (interrupt)
      │                   ├─ approve/edit → send_reply → archive_training → END
      │                   └─ reject       → archive_training → END
      │                        (email NOT marked read; 2-day cooldown then reopen)
      └─ MORE_INFO   → send_more_info_request → archive_training → END
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, TypedDict

import structlog
from sqlalchemy.exc import IntegrityError
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from human_review.confidence_router import RouteDecision, decide_route
from human_review.dingtalk_notifier import push_review_card
from knowledge_retrieval.vector_search import search_kb
from mail_gateway.imap_client import mark_imap_message_seen
from mail_gateway.smtp_sender import send_reply
from message_understanding.info_extractor import (
    extract_info,
    is_sensitive,
    is_software_acquisition_issue,
    needs_video_evidence,
    should_request_more_info,
)
from message_understanding.language_detector import detect_language
from models.db import (
    EmailMessage,
    EmailThread,
    MessageDirection,
    ReplyDraft,
    ReviewStatus,
    ReviewTask,
    ThreadStatus,
)
from models.schemas import ExtractedInfo, KbHit, ParsedEmail, ReplyOutput
from api.deps import get_session_factory
from ops_admin.data_collector import record_training_sample
from response_generator.draft_builder import build_draft
from response_generator.kb_gap_escalation import is_operator_kb_gap_reply
from response_generator.polisher import generate_more_info_request, polish_reply

log = structlog.get_logger(__name__)

# "postgres" | "memory" | "not_initialized" — set in init_graph(), exposed via GET /health.
checkpointer_backend: str = "not_initialized"


# ── Graph State ────────────────────────────────────────────────────────────────

class EmailState(TypedDict):
    # Input
    parsed_email: dict             # ParsedEmail.model_dump()

    # Processing results
    detected_language: str
    extracted_info: Optional[dict]  # ExtractedInfo.model_dump()
    kb_hits: list[dict]             # list[KbHit.model_dump()]
    reply_output: Optional[dict]    # ReplyOutput.model_dump()
    polished_body: Optional[str]

    # Routing
    route_decision: Optional[str]   # RouteDecision value
    route_reason: Optional[str]

    # DB IDs (set during processing)
    thread_db_id: Optional[int]
    message_db_id: Optional[int]
    draft_db_id: Optional[int]
    review_task_db_id: Optional[int]

    # Flags
    is_sensitive: bool
    needs_more_info: bool
    needs_video: bool
    kb_gap_operator_only: bool

    # Human review outcome (set via Command resume)
    review_action: Optional[str]    # "approve" | "edit" | "reject"
    reviewed_body: Optional[str]

    # Final
    final_reply_sent: bool
    resolution_type: Optional[str]
    error: Optional[str]


# ── Status Machine ─────────────────────────────────────────────────────────────

_ALLOWED_TRANSITIONS: dict[ThreadStatus, list[ThreadStatus]] = {
    ThreadStatus.NEW: [ThreadStatus.PARSED],
    ThreadStatus.PARSED: [ThreadStatus.LANGUAGE_DETECTED],
    ThreadStatus.LANGUAGE_DETECTED: [ThreadStatus.RETRIEVED, ThreadStatus.NEED_MORE_INFO],
    ThreadStatus.RETRIEVED: [ThreadStatus.DRAFT_GENERATED],
    ThreadStatus.DRAFT_GENERATED: [
        ThreadStatus.AUTO_REPLIED,
        ThreadStatus.PENDING_HUMAN_REVIEW,
        ThreadStatus.NEED_MORE_INFO,
    ],
    ThreadStatus.PENDING_HUMAN_REVIEW: [
        ThreadStatus.HUMAN_APPROVED,
        ThreadStatus.HUMAN_REJECTED,
    ],
    ThreadStatus.HUMAN_APPROVED: [ThreadStatus.REPLIED, ThreadStatus.CLOSED],
    ThreadStatus.HUMAN_REJECTED: [ThreadStatus.NEED_MORE_INFO, ThreadStatus.CLOSED],
    ThreadStatus.NEED_MORE_INFO: [ThreadStatus.WAITING_USER_REPLY],
    ThreadStatus.WAITING_USER_REPLY: [ThreadStatus.PARSED],
    ThreadStatus.AUTO_REPLIED: [ThreadStatus.CLOSED, ThreadStatus.WAITING_USER_REPLY],
    ThreadStatus.REPLIED: [ThreadStatus.CLOSED, ThreadStatus.WAITING_USER_REPLY],
}


class ThreadStatusMachine:
    """Enforces legal state transitions."""

    @staticmethod
    def transition(thread: EmailThread, new_status: ThreadStatus) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(thread.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Illegal transition: {thread.status} → {new_status}. "
                f"Allowed: {allowed}"
            )
        thread.status = new_status
        thread.updated_at = datetime.now(timezone.utc)


# ── Node helpers ───────────────────────────────────────────────────────────────

REJECTION_COOLDOWN = timedelta(days=2)


class AlreadyProcessedError(Exception):
    """Raised when a thread has already been fully processed (CLOSED/REPLIED)."""


class RejectionCooldownError(Exception):
    """Raised when a rejected thread is still within the 2-day cooldown window."""


async def _get_or_create_thread(
    db: AsyncSession,
    parsed: ParsedEmail,
) -> EmailThread:
    from sqlalchemy import select

    result = await db.execute(
        select(EmailThread).where(EmailThread.thread_id == parsed.thread_id)
    )
    thread = result.scalar_one_or_none()

    if thread is None:
        thread = EmailThread(
            thread_id=parsed.thread_id,
            customer_email=parsed.real_user_email,
            subject=parsed.subject,
            status=ThreadStatus.NEW,
            message_count=0,
        )
        db.add(thread)
        await db.flush()
    elif thread.status in (ThreadStatus.CLOSED, ThreadStatus.AUTO_REPLIED, ThreadStatus.REPLIED):
        if thread.rejected_at is not None:
            elapsed = datetime.now(timezone.utc) - thread.rejected_at
            if elapsed < REJECTION_COOLDOWN:
                raise RejectionCooldownError(
                    f"Thread {parsed.thread_id!r} rejected {elapsed} ago, "
                    f"cooldown expires in {REJECTION_COOLDOWN - elapsed}."
                )
            # Cooldown expired — reopen the thread for re-processing
            log.info(
                "thread_reopen_after_rejection_cooldown",
                thread_id=parsed.thread_id,
                rejected_at=thread.rejected_at.isoformat(),
            )
            thread.status = ThreadStatus.NEW
            thread.rejected_at = None
            thread.updated_at = datetime.now(timezone.utc)
        else:
            raise AlreadyProcessedError(
                f"Thread {parsed.thread_id!r} already in status {thread.status}, skipping."
            )

    return thread


async def _save_message(
    db: AsyncSession,
    thread: EmailThread,
    parsed: ParsedEmail,
) -> EmailMessage:
    from sqlalchemy import select

    # Reopened threads may re-encounter the same message_id
    if parsed.message_id:
        existing = (
            await db.execute(
                select(EmailMessage).where(EmailMessage.message_id == parsed.message_id)
            )
        ).scalar_one_or_none()
        if existing:
            thread.last_message_at = datetime.now(timezone.utc)
            return existing

    msg = EmailMessage(
        thread_id=thread.id,
        direction=MessageDirection.INBOUND,
        raw_body=parsed.raw_body,
        cleaned_body=parsed.cleaned_body,
        attachments_json=parsed.attachments,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        email_type=parsed.email_type,
        real_recipient_email=parsed.real_user_email,
        language_source_text=parsed.language_source_text,
        imap_uid=parsed.imap_uid,
        imap_folder=parsed.imap_folder,
        received_inbox=parsed.received_at_account,
    )
    db.add(msg)
    await db.flush()
    thread.message_count += 1
    thread.last_message_at = datetime.now(timezone.utc)
    return msg


async def _record_outbound_text(db: AsyncSession, thread_id: int, body: str) -> None:
    db.add(
        EmailMessage(
            thread_id=thread_id,
            direction=MessageDirection.OUTBOUND,
            raw_body=body,
            cleaned_body=body,
            message_id=None,
        )
    )
    await db.flush()


# ── Graph Nodes ────────────────────────────────────────────────────────────────

async def node_parse_email(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    db_factory = get_session_factory()

    async with db_factory() as db:
        try:
            thread = await _get_or_create_thread(db, parsed)
        except RejectionCooldownError as e:
            log.info("thread_rejection_cooldown", reason=str(e))
            return {"error": "rejection_cooldown", "thread_db_id": None, "message_db_id": None}
        except AlreadyProcessedError as e:
            log.info("thread_already_processed", reason=str(e))
            return {"error": "already_processed", "thread_db_id": None, "message_db_id": None}

        try:
            msg = await _save_message(db, thread, parsed)
            ThreadStatusMachine.transition(thread, ThreadStatus.PARSED)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            log.info("inbound_duplicate_message_id", message_id=parsed.message_id)
            return {"error": "duplicate_message", "thread_db_id": None, "message_db_id": None}

        return {
            "thread_db_id": thread.id,
            "message_db_id": msg.id,
        }


async def node_detect_language(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    lang = detect_language(parsed.language_source_text)
    db_factory = get_session_factory()

    async with db_factory() as db:
        thread = await db.get(EmailThread, state["thread_db_id"])
        thread.detected_language = lang
        ThreadStatusMachine.transition(thread, ThreadStatus.LANGUAGE_DETECTED)
        await db.commit()

    return {"detected_language": lang}


async def node_extract_info(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    info = extract_info(parsed.cleaned_body)
    sensitive = is_sensitive(parsed.cleaned_body)
    video = needs_video_evidence(parsed.cleaned_body)
    more_info = should_request_more_info(parsed.cleaned_body, info)

    return {
        "extracted_info": info.model_dump(),
        "is_sensitive": sensitive,
        "needs_video": video,
        "needs_more_info": more_info,
    }


async def node_retrieve_knowledge(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    db_factory = get_session_factory()

    async with db_factory() as db:
        hits = await search_kb(parsed.cleaned_body, db)
        thread = await db.get(EmailThread, state["thread_db_id"])
        ThreadStatusMachine.transition(thread, ThreadStatus.RETRIEVED)
        await db.commit()

    return {"kb_hits": [h.model_dump() for h in hits]}


async def node_generate_draft(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    info = ExtractedInfo(**state["extracted_info"])
    hits = [KbHit(**h) for h in state["kb_hits"]]

    reply, kb_gap_operator_only = await build_draft(
        email_body=parsed.cleaned_body,
        detected_language=state["detected_language"],
        extracted_info=info,
        kb_hits=hits,
    )
    if is_operator_kb_gap_reply(reply.reply_body):
        polished = reply.reply_body
    else:
        polished = await polish_reply(reply.reply_body)

    db_factory = get_session_factory()
    async with db_factory() as db:
        draft = ReplyDraft(
            thread_id=state["thread_db_id"],
            draft_body=polished,
            confidence=reply.confidence,
            needs_human_review=reply.needs_human_review,
            retrieval_refs_json=[h.model_dump() for h in hits],
            decision_reason=None,
        )
        db.add(draft)
        thread = await db.get(EmailThread, state["thread_db_id"])
        ThreadStatusMachine.transition(thread, ThreadStatus.DRAFT_GENERATED)
        await db.flush()
        draft_id = draft.id
        await db.commit()

    return {
        "reply_output": reply.model_dump(),
        "polished_body": polished,
        "draft_db_id": draft_id,
        "kb_gap_operator_only": kb_gap_operator_only,
    }


async def node_route_decision(state: EmailState) -> dict:
    reply = ReplyOutput(**state["reply_output"])
    info = ExtractedInfo(**state["extracted_info"])
    parsed = ParsedEmail(**state["parsed_email"])
    kb_hits = [KbHit(**h) for h in state.get("kb_hits", [])]

    if state.get("kb_gap_operator_only"):
        return {
            "route_decision": RouteDecision.HUMAN_REVIEW.value,
            "route_reason": "KB gap / undocumented version — internal draft only; no customer auto-reply",
        }

    db_factory = get_session_factory()
    async with db_factory() as db:
        thread = await db.get(EmailThread, state["thread_db_id"])
        msg_count = thread.message_count

    decision, reason = decide_route(
        reply=reply,
        extracted=info,
        email_body=parsed.cleaned_body,
        thread_message_count=msg_count,
        is_sensitive=state["is_sensitive"],
        has_image_or_video=parsed.has_image_or_video,
        kb_hits=kb_hits,
    )

    # Override if more_info needed — never for image/video or download/acquisition issues
    if (
        state["needs_more_info"]
        and not parsed.has_image_or_video
        and not is_software_acquisition_issue(parsed.cleaned_body)
    ):
        decision = RouteDecision.MORE_INFO
        reason = "Missing required info fields"

    return {
        "route_decision": decision.value,
        "route_reason": reason,
    }


async def node_send_auto_reply(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    body = state["polished_body"]

    if state.get("kb_gap_operator_only") or (body and is_operator_kb_gap_reply(body)):
        log.critical(
            "kb_gap_should_not_reach_auto_reply",
            thread_db_id=state["thread_db_id"],
        )
        return {"final_reply_sent": False, "resolution_type": "misroute_kb_gap_auto_blocked"}

    success = send_reply(
        to_address=parsed.real_user_email,
        subject=parsed.subject,
        body=body,
        from_account=parsed.received_at_account,
        in_reply_to=parsed.message_id,
    )

    db_factory = get_session_factory()
    async with db_factory() as db:
        thread = await db.get(EmailThread, state["thread_db_id"])
        if success:
            await _record_outbound_text(db, thread.id, body)
        ThreadStatusMachine.transition(thread, ThreadStatus.AUTO_REPLIED)
        await db.commit()

    if success:
        account = settings.get_account(parsed.received_at_account)
        if account:
            mark_imap_message_seen(parsed.imap_uid, account, parsed.imap_folder)

    return {
        "final_reply_sent": success,
        "resolution_type": "auto_replied",
    }


async def node_push_dingtalk(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    reply = ReplyOutput(**state["reply_output"])

    msg_id = await push_review_card(
        thread_db_id=state["thread_db_id"],
        customer_email=parsed.real_user_email,
        subject=parsed.subject,
        detected_language=state["detected_language"],
        user_summary=parsed.cleaned_body[:200],
        draft_reply=state["polished_body"] or reply.reply_body,
        confidence=reply.confidence,
    )

    db_factory = get_session_factory()
    async with db_factory() as db:
        review_task = ReviewTask(
            thread_id=state["thread_db_id"],
            dingtalk_msg_id=msg_id,
        )
        db.add(review_task)
        thread = await db.get(EmailThread, state["thread_db_id"])
        ThreadStatusMachine.transition(thread, ThreadStatus.PENDING_HUMAN_REVIEW)
        await db.flush()
        review_task_id = review_task.id
        await db.commit()

    return {"review_task_db_id": review_task_id}


def node_wait_human_review(state: EmailState) -> Command:
    """
    Interrupt the graph and wait for a human review decision.
    The API endpoint resumes the graph with Command(resume={action, edited_body}).
    """
    decision = interrupt(
        {
            "thread_db_id": state["thread_db_id"],
            "draft": state["polished_body"],
            "confidence": state["reply_output"]["confidence"] if state["reply_output"] else 0,
        }
    )
    return Command(
        update={
            "review_action": decision.get("action"),
            "reviewed_body": decision.get("edited_body"),
        },
        goto="handle_review_outcome",
    )


async def node_handle_review_outcome(state: EmailState) -> dict:
    action = state.get("review_action", "reject")
    db_factory = get_session_factory()

    async with db_factory() as db:
        task = await db.get(ReviewTask, state["review_task_db_id"])
        _action_to_review_status = {
            "approve": ReviewStatus.APPROVED,
            "edit":    ReviewStatus.MODIFIED,
            "reject":  ReviewStatus.REJECTED,
        }
        if task:
            task.review_status = _action_to_review_status.get(action, ReviewStatus.REJECTED)
            task.reviewed_at = datetime.now(timezone.utc)
            if action == "edit":
                task.reviewed_body = state.get("reviewed_body")

        thread = await db.get(EmailThread, state["thread_db_id"])

        if action in ("approve", "edit"):
            ThreadStatusMachine.transition(thread, ThreadStatus.HUMAN_APPROVED)
        else:
            ThreadStatusMachine.transition(thread, ThreadStatus.HUMAN_REJECTED)
            thread.rejected_at = datetime.now(timezone.utc)

        await db.commit()

    return {}


async def node_send_reviewed_reply(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    action = state.get("review_action", "approve")
    kb_gap = state.get("kb_gap_operator_only", False)

    if action == "edit":
        body = state.get("reviewed_body") or state["polished_body"]
    else:
        body = state["polished_body"]

    approve_without_edit = action == "approve" and not state.get("reviewed_body")
    skip_smtp = bool(kb_gap and approve_without_edit and body and is_operator_kb_gap_reply(body))

    if skip_smtp:
        log.info(
            "human_review_approve_kb_gap_no_customer_email",
            thread_db_id=state["thread_db_id"],
        )
        success = False
    else:
        success = send_reply(
            to_address=parsed.real_user_email,
            subject=parsed.subject,
            body=body,
            from_account=parsed.received_at_account,
            in_reply_to=parsed.message_id,
        )

    db_factory = get_session_factory()
    async with db_factory() as db:
        thread = await db.get(EmailThread, state["thread_db_id"])
        if success:
            await _record_outbound_text(db, thread.id, body)
        if skip_smtp:
            ThreadStatusMachine.transition(thread, ThreadStatus.CLOSED)
        else:
            ThreadStatusMachine.transition(thread, ThreadStatus.REPLIED)
        await db.commit()

    if success:
        account = settings.get_account(parsed.received_at_account)
        if account:
            mark_imap_message_seen(parsed.imap_uid, account, parsed.imap_folder)

    return {
        "final_reply_sent": success,
        "resolution_type": (
            "human_approve_out_of_band_kb_gap" if skip_smtp else f"human_{action}"
        ),
    }


async def node_send_more_info_request(state: EmailState) -> dict:
    parsed = ParsedEmail(**state["parsed_email"])
    body = await generate_more_info_request(
        detected_language=state["detected_language"],
        needs_video=state["needs_video"],
    )

    success = send_reply(
        to_address=parsed.real_user_email,
        subject=parsed.subject,
        body=body,
        from_account=parsed.received_at_account,
        in_reply_to=parsed.message_id,
    )

    db_factory = get_session_factory()
    async with db_factory() as db:
        thread = await db.get(EmailThread, state["thread_db_id"])
        if success:
            await _record_outbound_text(db, thread.id, body)
        ThreadStatusMachine.transition(thread, ThreadStatus.NEED_MORE_INFO)
        await db.commit()

    if success:
        account = settings.get_account(parsed.received_at_account)
        if account:
            mark_imap_message_seen(parsed.imap_uid, account, parsed.imap_folder)

    return {
        "final_reply_sent": success,
        "resolution_type": "more_info_requested",
    }


async def node_archive_training(state: EmailState) -> dict:
    """Write training sample and update thread to final status."""
    parsed = ParsedEmail(**state["parsed_email"])
    reply = ReplyOutput(**state["reply_output"]) if state.get("reply_output") else None
    info = ExtractedInfo(**state["extracted_info"]) if state.get("extracted_info") else ExtractedInfo()
    hits = [KbHit(**h) for h in state.get("kb_hits", [])]

    if reply is None:
        return {}

    final_body = state.get("reviewed_body") or state.get("polished_body") or reply.reply_body
    review_status = state.get("review_action")
    db_factory = get_session_factory()

    async with db_factory() as db:
        await record_training_sample(
            db=db,
            thread_db_id=state["thread_db_id"],
            message_db_id=state.get("message_db_id"),
            customer_email=parsed.real_user_email,
            detected_language=state["detected_language"],
            extracted_info=info,
            kb_hits=hits,
            reply_output=reply,
            final_reply_text=final_body,
            resolution_type=state.get("resolution_type", "unknown"),
            review_status=review_status,
        )

        thread = await db.get(EmailThread, state["thread_db_id"])
        if thread.status in (
            ThreadStatus.AUTO_REPLIED,
            ThreadStatus.REPLIED,
            ThreadStatus.NEED_MORE_INFO,
        ):
            ThreadStatusMachine.transition(thread, ThreadStatus.WAITING_USER_REPLY)
        elif thread.status not in (
            ThreadStatus.WAITING_USER_REPLY,
            ThreadStatus.CLOSED,
        ):
            ThreadStatusMachine.transition(thread, ThreadStatus.CLOSED)
        await db.commit()

    return {}


# ── Conditional Edges ──────────────────────────────────────────────────────────

def _route_after_decision(state: EmailState) -> Literal[
    "send_auto_reply", "push_dingtalk", "send_more_info_request"
]:
    decision = state["route_decision"]
    if decision == RouteDecision.AUTO_SEND:
        return "send_auto_reply"
    if decision == RouteDecision.MORE_INFO:
        return "send_more_info_request"
    return "push_dingtalk"


def _route_after_review(state: EmailState) -> Literal[
    "send_reviewed_reply", "archive_training"
]:
    action = state.get("review_action", "reject")
    if action in ("approve", "edit"):
        return "send_reviewed_reply"
    return "archive_training"


# ── Build Graph ────────────────────────────────────────────────────────────────

def build_email_graph() -> StateGraph:
    builder = StateGraph(EmailState)

    # Nodes
    builder.add_node("parse_email", node_parse_email)
    builder.add_node("detect_language", node_detect_language)
    builder.add_node("extract_info", node_extract_info)
    builder.add_node("retrieve_knowledge", node_retrieve_knowledge)
    builder.add_node("generate_draft", node_generate_draft)
    builder.add_node("decide_route", node_route_decision)
    builder.add_node("send_auto_reply", node_send_auto_reply)
    builder.add_node("push_dingtalk", node_push_dingtalk)
    builder.add_node("wait_human_review", node_wait_human_review)
    builder.add_node("handle_review_outcome", node_handle_review_outcome)
    builder.add_node("send_reviewed_reply", node_send_reviewed_reply)
    builder.add_node("send_more_info_request", node_send_more_info_request)
    builder.add_node("archive_training", node_archive_training)

    # Edges
    builder.add_edge(START, "parse_email")
    builder.add_conditional_edges(
        "parse_email",
        lambda s: END if s.get("error") else "detect_language",
        {"detect_language": "detect_language", END: END},
    )
    builder.add_edge("detect_language", "extract_info")
    builder.add_edge("extract_info", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "generate_draft")
    builder.add_edge("generate_draft", "decide_route")
    builder.add_conditional_edges(
        "decide_route",
        _route_after_decision,
        {
            "send_auto_reply": "send_auto_reply",
            "push_dingtalk": "push_dingtalk",
            "send_more_info_request": "send_more_info_request",
        },
    )
    builder.add_edge("send_auto_reply", "archive_training")
    builder.add_edge("push_dingtalk", "wait_human_review")
    builder.add_conditional_edges(
        "handle_review_outcome",
        _route_after_review,
        {
            "send_reviewed_reply": "send_reviewed_reply",
            "archive_training": "archive_training",
        },
    )
    builder.add_edge("send_reviewed_reply", "archive_training")
    builder.add_edge("send_more_info_request", "archive_training")
    builder.add_edge("archive_training", END)

    return builder


async def init_graph() -> None:
    """
    Initialize the email graph with a persistent PostgreSQL checkpointer.
    Must be called once during application startup (async context).
    Falls back to MemorySaver if PostgreSQL is unavailable.
    """
    global email_graph, checkpointer_backend
    try:
        from psycopg_pool import AsyncConnectionPool
        from config import settings

        pg_url = settings.database_url_sync.replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        pool = AsyncConnectionPool(conninfo=pg_url, max_size=10, open=False)
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        checkpointer_backend = "postgres"
        log.info("pg_checkpointer_ready")
    except Exception as e:
        log.warning("pg_checkpointer_unavailable", error=str(e), fallback="MemorySaver")
        checkpointer = MemorySaver()
        checkpointer_backend = "memory"

    email_graph = build_email_graph().compile(checkpointer=checkpointer)


# Default compiled graph with MemorySaver (overridden by init_graph() at startup)
email_graph = build_email_graph().compile(checkpointer=MemorySaver())

