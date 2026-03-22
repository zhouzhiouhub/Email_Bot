"""
Website FAQ scraper.

Fetches the configured FAQ URL, extracts Q&A pairs,
and upserts them into the kb_documents table as 'web_faq' entries.

Configure FAQ_URL in .env to point to your product's FAQ page.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

import httpx
import structlog
import trafilatura
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from knowledge_retrieval.vector_search import upsert_document

log = structlog.get_logger(__name__)

_REQUEST_TIMEOUT = 30


def _make_kb_id(question: str) -> str:
    return "web-" + hashlib.md5(question.encode()).hexdigest()[:12]


def _get_category_for_element(tag) -> str:
    """Walk up/back the DOM to find the nearest h2 category heading."""
    for sibling in tag.find_all_previous(["h2"]):
        text = sibling.get_text(strip=True)
        if text:
            return re.sub(r"^[^\w\u4e00-\u9fff]+", "", text).strip()
    return ""


def _scrape_faq_page(html: str) -> list[dict]:
    """
    Parse the FAQ page and extract question/answer pairs.

    Supports multiple HTML structures:
      1. h3.faq-question + div.answer (common FAQ layout)
      2. <details>/<summary> (accordion FAQ)
      3. h2/h3 heading traversal
      4. trafilatura plain-text fallback
    """
    soup = BeautifulSoup(html, "html.parser")
    faqs: list[dict] = []

    # Strategy 1: class="faq-question" h3 + sibling class="answer" div
    for q_tag in soup.find_all("h3", class_=lambda c: c and "faq-question" in c):
        question = q_tag.get_text(strip=True)
        parent = q_tag.parent
        a_tag = parent.find(class_=lambda c: c and "answer" in c) if parent else None
        if not a_tag:
            a_tag = q_tag.find_next_sibling(class_=lambda c: c and "answer" in c)
        answer = a_tag.get_text(separator=" ", strip=True) if a_tag else ""
        if question and answer:
            category = _get_category_for_element(q_tag)
            faqs.append({"question": question, "answer": answer, "category": category})

    if faqs:
        return faqs

    # Strategy 2: <details>/<summary>
    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary:
            continue
        question = summary.get_text(strip=True)
        answer_parts = [
            tag.get_text(strip=True)
            for tag in details.find_all(["p", "li"])
            if tag.get_text(strip=True)
        ]
        answer = " ".join(answer_parts)
        if question and answer:
            faqs.append({"question": question, "answer": answer})

    if faqs:
        return faqs

    # Strategy 3: h2/h3 sequential traversal
    current_category = ""
    current_q: Optional[str] = None
    answer_parts: list[str] = []

    for tag in soup.find_all(["h2", "h3", "p", "li"]):
        name = tag.name
        text = tag.get_text(strip=True)
        if not text:
            continue
        if name == "h2":
            if current_q and answer_parts:
                faqs.append({"question": current_q, "answer": " ".join(answer_parts), "category": current_category})
                current_q, answer_parts = None, []
            current_category = re.sub(r"^[^\w\u4e00-\u9fff]+", "", text).strip()
        elif name == "h3":
            if current_q and answer_parts:
                faqs.append({"question": current_q, "answer": " ".join(answer_parts), "category": current_category})
            current_q, answer_parts = text, []
        elif current_q:
            answer_parts.append(text)

    if current_q and answer_parts:
        faqs.append({"question": current_q, "answer": " ".join(answer_parts), "category": current_category})

    if faqs:
        return faqs

    # Strategy 4: trafilatura plain-text fallback
    text_content = trafilatura.extract(html) or ""
    lines = [ln.strip() for ln in text_content.splitlines() if ln.strip()]
    current_q = None
    answer_parts = []
    for line in lines:
        if re.search(r"[？?]\s*$", line):
            if current_q and answer_parts:
                faqs.append({"question": current_q, "answer": " ".join(answer_parts)})
            current_q, answer_parts = line, []
        elif current_q:
            answer_parts.append(line)
    if current_q and answer_parts:
        faqs.append({"question": current_q, "answer": " ".join(answer_parts)})

    return faqs


async def scrape_and_update(db: AsyncSession, url: str | None = None) -> int:
    """
    Fetch the FAQ page, extract Q&A pairs, and upsert into the KB.
    Returns the number of documents updated.
    """
    faq_url = url or settings.faq_url
    if not faq_url:
        log.warning("faq_url_not_configured", hint="Set FAQ_URL in .env")
        return 0

    log.info("faq_scrape_start", url=faq_url)
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(faq_url, headers={"User-Agent": "EmailBotFAQ/1.0"})
            response.raise_for_status()
            html = response.text
    except Exception:
        log.exception("faq_scrape_request_failed", url=faq_url)
        return 0

    faqs = _scrape_faq_page(html)
    log.info("faq_scraped", count=len(faqs))

    lang = "zh" if "/zh/" in faq_url else "en"
    updated = 0
    for faq in faqs:
        doc = {
            "id": _make_kb_id(faq["question"]),
            "source_type": "web_faq",
            "title": faq["question"],
            "url": faq_url,
            "lang": lang,
            "content": f"Q: {faq['question']}\nA: {faq['answer']}",
            "category": faq.get("category"),
        }
        await upsert_document(doc, db)
        updated += 1

    log.info("faq_upsert_done", updated=updated)
    return updated
