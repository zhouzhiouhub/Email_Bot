from __future__ import annotations

import json
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailAccount:
    """One IMAP/SMTP account pair."""

    def __init__(self, d: dict) -> None:
        self.address: str = d["address"]
        self.password: str = d["password"]
        self.imap_host: str = d.get("imap_host", "imap.gmail.com")
        self.imap_port: int = int(d.get("imap_port", 993))
        self.smtp_host: str = d.get("smtp_host", "smtp.gmail.com")
        self.smtp_port: int = int(d.get("smtp_port", 587))
        self.from_name: str = d.get("from_name", "Support")
        self.imap_folder: str = d.get("imap_folder", "INBOX")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Brand / Identity (users MUST configure these) ──────────────────────────
    brand_name: str = Field(default="MyBrand")
    support_agent_name: str = Field(default="Alex")
    company_description: str = Field(
        default="a software company providing technical support"
    )
    support_email_signature: str = Field(
        default="Technical Support Team\nsupport@example.com"
    )
    faq_url: str = Field(default="")

    # ── Notification platform ──────────────────────────────────────────────────
    notification_platform: str = Field(
        default="dingtalk",
        description="Webhook notification platform: dingtalk, slack, feishu, or generic",
    )

    # Database
    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/email_bot")
    database_url_sync: str = Field(default="postgresql://postgres:postgres@localhost:5432/email_bot")

    # Redis / Celery
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_broker_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/1")

    # Email accounts — stored as JSON array in EMAIL_ACCOUNTS env var
    # Example:
    # EMAIL_ACCOUNTS=[{"address":"support@example.com","password":"xxx",...},...]
    email_accounts_json: str = Field(
        default='[]',
        alias="EMAIL_ACCOUNTS",
    )

    # Legacy single-account fields (kept for backward compat / simple setups)
    imap_poll_interval_seconds: int = Field(default=60)

    # IMAP IDLE (push-based inbox monitoring)
    imap_idle_enabled: bool = Field(default=False)
    imap_idle_renew_seconds: int = Field(default=1500, ge=60, le=1740)

    # OpenAI
    openai_api_key: str = Field(default="")
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="gpt-4o")
    openai_embedding_model: str = Field(default="text-embedding-3-small")

    # Webhook Notification (DingTalk / Slack / Feishu / generic)
    dingtalk_webhook_url: str = Field(default="")
    dingtalk_secret: str = Field(default="")

    # Service
    service_base_url: str = Field(default="http://localhost:8000")

    # Confidence thresholds
    confidence_auto_reply: float = Field(default=0.85)
    confidence_human_review: float = Field(default=0.60)

    # Knowledge retrieval (recall + lexical rerank on top of pgvector)
    kb_recall_limit: int = Field(default=24, ge=4, le=64)
    kb_rerank_vector_weight: float = Field(default=0.65, ge=0.0, le=1.0)
    kb_rerank_lexical_weight: float = Field(default=0.35, ge=0.0, le=1.0)

    # Type B senders (comma-separated)
    type_b_sender_addresses: str = Field(default="")

    # App
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # ── Derived properties ─────────────────────────────────────────────────────

    @property
    def imap_effective_poll_interval(self) -> int:
        """When IDLE is active, polling becomes a 5-min fallback safety net."""
        if self.imap_idle_enabled:
            return max(self.imap_poll_interval_seconds, 300)
        return self.imap_poll_interval_seconds

    @property
    def email_accounts(self) -> list[EmailAccount]:
        try:
            raw = json.loads(self.email_accounts_json)
            return [EmailAccount(a) for a in raw]
        except Exception:
            return []

    @property
    def type_b_sender_list(self) -> list[str]:
        return [s.strip().lower() for s in self.type_b_sender_addresses.split(",") if s.strip()]

    def get_account(self, address: str) -> Optional[EmailAccount]:
        """Look up an account by its email address."""
        for acc in self.email_accounts:
            if acc.address.lower() == address.lower():
                return acc
        return None


settings = Settings()
