"""
Celery application and task definitions.

Tasks:
  process_inbound_email  - Triggered by IMAP poller; calls the FastAPI webhook
  poll_imap              - Periodic beat task; triggers IMAP poll
  refresh_faq            - Periodic beat task; scrapes & updates web FAQ
"""
from __future__ import annotations

import asyncio
import httpx
import structlog
from celery import Celery
from celery.schedules import crontab

from config import settings

log = structlog.get_logger(__name__)

celery = Celery("email_bot")
celery.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "poll-imap-every-minute": {
            "task": "celery_app.poll_imap",
            "schedule": settings.imap_effective_poll_interval,
        },
        "refresh-faq-weekly": {
            "task": "celery_app.refresh_faq",
            "schedule": crontab(day_of_week="monday", hour=3, minute=0),
        },
    },
)


@celery.task(name="celery_app.process_inbound_email", bind=True, max_retries=3)
def process_inbound_email(self, parsed_email_dict: dict) -> dict:
    """
    Post the parsed email to the FastAPI webhook endpoint, which launches
    the LangGraph workflow.  Retries up to 3 times on failure.
    """
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{settings.service_base_url}/webhook/email",
                json={"parsed_email": parsed_email_dict},
            )
            response.raise_for_status()
        return {"status": "ok"}
    except Exception as exc:
        log.exception("process_inbound_email_failed", message_id=parsed_email_dict.get("message_id"))
        raise self.retry(exc=exc, countdown=30)


@celery.task(name="celery_app.poll_imap")
def poll_imap() -> dict:
    """Beat task: poll all configured IMAP accounts for new emails."""
    from mail_gateway.imap_client import poll_all_accounts
    emails = poll_all_accounts()
    for parsed in emails:
        process_inbound_email.delay(parsed.model_dump())
    return {"polled": len(emails)}


@celery.task(name="celery_app.refresh_faq")
def refresh_faq() -> dict:
    """Beat task: scrape FAQ page and update the knowledge base."""
    from api.deps import get_session_factory
    from knowledge_retrieval.faq_scraper import scrape_and_update

    async def _run():
        factory = get_session_factory()
        async with factory() as db:
            count = await scrape_and_update(db)
        return count

    updated = asyncio.run(_run())
    log.info("faq_refresh_done", updated=updated)
    return {"updated": updated}
