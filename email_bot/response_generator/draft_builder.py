"""
Draft builder: constructs a ReplyOutput by combining knowledge base hits,
extracted info, and the LLM response.
"""
from __future__ import annotations

import json

import structlog
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from pydantic import ValidationError

from config import settings
from models.schemas import ExtractedInfo, KbHit, ReplyOutput
from response_generator.kb_gap_escalation import apply_kb_gap_handoff
from response_generator.prompts import (
    KNOWLEDGE_BLOCK_TEMPLATE,
    SYSTEM_PROMPT,
    USER_MESSAGE_TEMPLATE,
)
from response_generator.reply_templates import get_localized_writing_guidance

log = structlog.get_logger(__name__)

_FALLBACK_REPLY = ReplyOutput(
    reply_body="[Draft generation failed — please review manually]",
    language="en",
    confidence=0.0,
    needs_human_review=True,
    missing_info_fields=[],
    cited_kb_ids=[],
)


def _build_kb_excerpts(hits: list[KbHit]) -> str:
    if not hits:
        return "(No relevant knowledge base articles found.)"
    parts = []
    for hit in hits:
        is_sop = hit.source_type == "sop"
        limit = 1200 if is_sop else 600
        label = "SOP" if is_sop else hit.source_type
        parts.append(
            f"[{hit.kb_id}] {hit.title} (score={hit.score:.2f}, type={label})\n"
            f"{hit.content[:limit]}"
        )
    return "\n\n".join(parts)


async def build_draft(
    email_body: str,
    detected_language: str,
    extracted_info: ExtractedInfo,
    kb_hits: list[KbHit],
) -> tuple[ReplyOutput, bool]:
    """
    Call the LLM to generate a ReplyOutput given email content + KB evidence.

    Returns (reply, kb_gap_operator_only). When kb_gap_operator_only is True, the reply_body
    is internal-only for reviewers — never send it to the customer without editing.

    On failure returns (fallback_reply, False).
    """
    kb_block = KNOWLEDGE_BLOCK_TEMPLATE.format(
        kb_excerpts=_build_kb_excerpts(kb_hits)
    )

    system_msg = SYSTEM_PROMPT.format(
        agent_name=settings.support_agent_name,
        brand_name=settings.brand_name,
        company_description=settings.company_description,
        detected_language=detected_language,
        localized_guidance=get_localized_writing_guidance(detected_language),
    )
    user_msg = USER_MESSAGE_TEMPLATE.format(
        agent_name=settings.support_agent_name,
        detected_language=detected_language,
        email_body=email_body[:3000],
        os=extracted_info.os or "unknown",
        device_model=extracted_info.device_model or "unknown",
        software_version=extracted_info.software_version or "unknown",
        error_text=extracted_info.error_text or "none",
        use_case=extracted_info.use_case or "not specified",
        intent=extracted_info.intent or "general",
        knowledge_block=kb_block,
    )

    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=user_msg),
        ])
        raw_json = json.loads(response.content)
        reply = ReplyOutput(**raw_json)
        reply, kb_gap_operator_only = apply_kb_gap_handoff(reply)
        log.info(
            "draft_built",
            confidence=reply.confidence,
            needs_human=reply.needs_human_review,
            lang=reply.language,
            kb_gap_operator_only=kb_gap_operator_only,
        )
        return reply, kb_gap_operator_only
    except ValidationError as e:
        log.warning("draft_validation_failed", error=str(e))
        return _FALLBACK_REPLY, False
    except Exception:
        log.exception("draft_build_error")
        return _FALLBACK_REPLY, False
