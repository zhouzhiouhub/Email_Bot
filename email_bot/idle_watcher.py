"""
Standalone IMAP IDLE watcher process.

Maintains persistent IDLE connections to all configured email accounts.
When new mail arrives, dispatches to the Celery pipeline immediately
(near-zero latency vs 60s polling).

Usage:
    python idle_watcher.py

Deployment:
    Run alongside uvicorn / celery worker / celery beat via
    supervisord, systemd, or docker-compose.

Prerequisites:
    Set IMAP_IDLE_ENABLED=true in .env
"""
from __future__ import annotations

import signal
import sys
import threading

import structlog

from config import settings
from mail_gateway.imap_client import ImapIdleWatcher

log = structlog.get_logger("idle_watcher")


def _dispatch(emails) -> None:
    from celery_app import process_inbound_email

    for parsed in emails:
        process_inbound_email.delay(parsed.model_dump())
    log.info("idle_dispatched", count=len(emails))


def main() -> None:
    if not settings.imap_idle_enabled:
        log.warning("idle_disabled", hint="Set IMAP_IDLE_ENABLED=true in .env")
        sys.exit(0)

    accounts = settings.email_accounts
    if not accounts:
        log.warning("idle_no_accounts", hint="Set EMAIL_ACCOUNTS in .env")
        sys.exit(0)

    stop = threading.Event()

    def _shutdown(*_args) -> None:
        log.info("idle_shutdown_requested")
        stop.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    threads: list[threading.Thread] = []
    for acc in accounts:
        watcher = ImapIdleWatcher(
            account=acc,
            on_new_emails=_dispatch,
            stop_event=stop,
            idle_renew_seconds=settings.imap_idle_renew_seconds,
        )
        t = threading.Thread(
            target=watcher.run_forever,
            name=f"idle-{acc.address}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        log.info("idle_thread_started", account=acc.address)

    log.info("idle_watcher_running", accounts=len(accounts))

    try:
        stop.wait()
    except KeyboardInterrupt:
        stop.set()

    for t in threads:
        t.join(timeout=10)

    log.info("idle_watcher_stopped")


if __name__ == "__main__":
    main()
