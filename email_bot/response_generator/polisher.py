"""
LLM polisher: final human-voice pass on the draft reply.
Does NOT change factual content — only tone, rhythm, and naturalness.
"""
from __future__ import annotations

import structlog
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

from config import settings

log = structlog.get_logger(__name__)

_POLISH_SYSTEM = """\
You are a senior support agent named {agent_name} doing a final read of your own reply before sending it.

Your goal: make the reply sound like it came from a thoughtful, experienced human — not a corporate template or a chatbot.

Checklist before sending:
✓ Does it feel like I actually read their message? (Reference their specific situation)
✓ Does it open naturally? (Not "Dear customer" or "Thank you for contacting us")
✓ Is the tone warm and direct, but not over-the-top friendly?
✓ Are there any robotic phrases? Remove: "do not hesitate", "valued customer", "rest assured",
  "I apologize for the inconvenience", "please be advised", "at your earliest convenience"
✓ Is the cause stated in absolute terms? If so, soften it:
  Change "这是因为…" → "这可能是…"
  Change "原因是…" → "初步判断可能和…有关"
  Change "this is caused by…" → "this could be caused by…"
  Change "the issue is…" → "the issue might be…"
  A support agent never knows for sure without seeing the device — always leave room for other causes.
✓ Does it use "I" naturally? (Not always "we")
✓ Is it easy to skim? (Short paragraphs, line breaks)
✓ Does the closing feel genuine? (Not a generic corporate sign-off)
✓ Is the length right? (Not padded, not too terse)

Hard rules:
- Keep ALL facts exactly as-is. Do NOT add or remove any technical information.
- Match the language of the draft exactly. Do NOT translate.
- Return ONLY the polished reply text — no JSON, no subject line, no extra commentary.
"""

_POLISH_USER_TEMPLATE = """\
Here's my draft reply. Give it a final polish:

---
{draft}
---
"""


async def polish_reply(draft: str) -> str:
    """
    Final human-voice pass on the draft reply.
    Falls back to original draft if LLM call fails.
    """
    if not draft.strip():
        return draft

    system_prompt = _POLISH_SYSTEM.format(agent_name=settings.support_agent_name)

    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.6,
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=_POLISH_USER_TEMPLATE.format(draft=draft)),
        ])
        polished = response.content.strip()
        log.debug("reply_polished", original_len=len(draft), polished_len=len(polished))
        return polished
    except Exception:
        log.exception("polish_failed", draft_preview=draft[:80])
        return draft


async def generate_more_info_request(detected_language: str, needs_video: bool = False) -> str:
    """
    Generate a language-appropriate "please provide more information" reply.
    """
    from response_generator.prompts import (
        REQUEST_MORE_INFO_PROMPT,
        get_more_info_request_fallback,
        get_video_suggestion,
    )

    video_line = get_video_suggestion(detected_language) if needs_video else ""

    prompt_text = REQUEST_MORE_INFO_PROMPT.format(
        agent_name=settings.support_agent_name,
        brand_name=settings.brand_name,
        detected_language=detected_language,
        video_suggestion=video_line,
    )

    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.5,
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt_text)])
        return response.content.strip()
    except Exception:
        log.exception("more_info_request_generation_failed")
        return get_more_info_request_fallback(detected_language)
