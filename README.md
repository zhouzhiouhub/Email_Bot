**English** | [中文](README_CN.md)

# Email Auto-Processing Bot

An intelligent email customer service system powered by LLM (Large Language Model). It automatically receives, understands, and replies to customer emails, with a human-in-the-loop review mechanism for quality control.

## Features

- **Automated Email Processing**: IMAP polling or IDLE push-based email reception from multiple accounts
- **AI-Powered Replies**: LangGraph workflow with GPT-4o generates context-aware, natural-sounding replies
- **Knowledge Base Retrieval**: pgvector-based semantic search with lexical reranking for accurate answers
- **Multi-Language Support**: Auto-detects 15+ languages and replies in the customer's language
- **Confidence-Based Routing**: High-confidence replies are auto-sent; uncertain ones go to human review
- **Human Review System**: Web-based review page + webhook notifications (DingTalk/Slack/Feishu)
- **Training Data Collection**: Every interaction is archived for future model fine-tuning
- **FAQ Auto-Scraping**: Periodically scrapes your product's FAQ page and updates the knowledge base
- **Admin Dashboard API**: Thread management, KB management, training data export, miss analysis

## Architecture

```
IMAP Inbox → Celery Worker → FastAPI Webhook → LangGraph Workflow
  → Language Detection → Info Extraction → KB Retrieval → Draft Generation → Route Decision
    ├─ High Confidence  → Auto Send → Archive Training → END
    ├─ Medium/Low       → Webhook Notification → Human Review (interrupt)
    │                      ├─ Approve/Edit → Send Reply → Archive → END
    │                      └─ Reject       → Archive → END
    └─ Missing Info     → Send "More Info" Request → Archive → END
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Web Framework | FastAPI + Uvicorn |
| Workflow Engine | LangGraph (with PostgreSQL checkpointer) |
| LLM | OpenAI GPT-4o (configurable, supports any OpenAI-compatible API) |
| Database | PostgreSQL + pgvector (vector similarity search) |
| Task Queue | Celery + Redis |
| Email | IMAP (polling/IDLE) + SMTP |
| Embeddings | OpenAI text-embedding-3-small |
| Language Detection | langdetect |
| Web Scraping | trafilatura + BeautifulSoup4 |

## Project Structure

```
email_bot/
├── run.py                          # FastAPI entry point
├── config.py                       # Unified configuration (pydantic-settings)
├── celery_app.py                   # Celery tasks and beat schedule
├── idle_watcher.py                 # IMAP IDLE push-based listener
├── start.bat / stop.bat            # Windows startup/shutdown scripts
├── .env.example                    # Environment variable template
├── requirements.txt                # Python dependencies
│
├── api/
│   ├── main.py                     # FastAPI routes (webhook, review, health)
│   ├── deps.py                     # Dependency injection (DB sessions)
│   ├── dev_mail_tester.py          # Dev-only email preview UI
│   └── internal_qa.py              # Internal Q&A testing tool
│
├── workflow/
│   └── graph.py                    # LangGraph state machine (13 nodes)
│
├── mail_gateway/
│   ├── imap_client.py              # IMAP polling / IDLE / email parsing
│   ├── smtp_sender.py              # SMTP reply sending
│   └── thread_tracker.py           # Email type detection, thread ID, dedup
│
├── message_understanding/
│   ├── language_detector.py        # Language detection (langdetect + CJK fix)
│   └── info_extractor.py           # OS/device/version extraction (rules + LLM)
│
├── knowledge_retrieval/
│   ├── vector_search.py            # pgvector search + lexical rerank
│   ├── faq_scraper.py              # Website FAQ auto-scraper
│   └── sop_loader.py               # SOP knowledge import
│
├── response_generator/
│   ├── prompts.py                  # All LLM prompt templates
│   ├── draft_builder.py            # LLM draft generation
│   ├── polisher.py                 # Reply tone polishing + more-info generation
│   ├── kb_gap_escalation.py        # KB gap detection and internal escalation
│   └── reply_templates.py          # Localized writing guidance
│
├── human_review/
│   ├── confidence_router.py        # Route decision (auto/review/more-info)
│   └── dingtalk_notifier.py        # Webhook notification for review
│
├── models/
│   ├── db.py                       # SQLAlchemy ORM models
│   └── schemas.py                  # Pydantic schemas
│
├── ops_admin/
│   ├── router.py                   # Admin API endpoints
│   └── data_collector.py           # Training sample archiver
│
├── services/
│   ├── admin_stats.py              # Dashboard statistics
│   ├── kb_writeback.py             # Training sample → KB conversion
│   ├── miss_analyzer.py            # No-hit question clustering
│   └── training_export.py          # JSONL export
│
├── data/
│   ├── website_kb_entries.json     # Example KB import data
│   └── import_website_kb.py        # KB data import script
│
└── alembic/                        # Database migrations
    ├── env.py
    └── versions/
```

## Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL 15+ with [pgvector extension](https://github.com/pgvector/pgvector)
- Redis 6+
- An OpenAI API key (or any OpenAI-compatible API)
- One or more email accounts with IMAP/SMTP access

### 1. Clone and Install

```bash
git clone <your-repo-url>
cd email_bot

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/macOS)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install LangGraph PostgreSQL checkpointer (optional but recommended)
pip install langgraph-checkpoint-postgres psycopg[binary] psycopg-pool
```

### 2. Configure Environment

```bash
# Copy the example config
cp .env.example .env
```

Edit `.env` and fill in your values:

```ini
# ── Brand Identity (customize these!) ──
BRAND_NAME=YourCompany
SUPPORT_AGENT_NAME=Alex
COMPANY_DESCRIPTION=a SaaS platform for project management
SUPPORT_EMAIL_SIGNATURE=YourCompany Support Team\nsupport@yourcompany.com

# ── Database ──
DATABASE_URL=postgresql+asyncpg://postgres:yourpassword@localhost:5432/email_bot
DATABASE_URL_SYNC=postgresql://postgres:yourpassword@localhost:5432/email_bot

# ── Email Accounts (JSON array) ──
EMAIL_ACCOUNTS=[{"address":"support@yourcompany.com","password":"your-app-password","imap_host":"imap.gmail.com","imap_port":993,"smtp_host":"smtp.gmail.com","smtp_port":587,"from_name":"YourCompany Support"}]

# ── OpenAI ──
OPENAI_API_KEY=sk-your-api-key
OPENAI_MODEL=gpt-4o

# ── FAQ URL (optional, for auto-scraping) ──
FAQ_URL=https://yourcompany.com/faq
```

### 3. Set Up Database

```bash
# Create the database
createdb email_bot

# Enable pgvector extension
psql email_bot -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run migrations
alembic upgrade head
```

### 4. Import Knowledge Base (Optional)

Add your FAQ/SOP data to `data/website_kb_entries.json` and run:

```bash
python data/import_website_kb.py
```

Or use the Admin API:

```bash
# Add a single KB entry
curl -X POST http://localhost:8000/admin/kb \
  -H "Content-Type: application/json" \
  -d '{
    "id": "faq-001",
    "source_type": "inner_faq",
    "title": "How to reset settings",
    "lang": "en",
    "content": "Q: How to reset?\nA: Go to Settings > Reset to Defaults."
  }'

# Bulk import SOPs
curl -X POST http://localhost:8000/admin/kb/sop/import \
  -H "Content-Type: application/json" \
  -d '[{
    "title": "Device not detected",
    "symptom": "USB device not recognized by software",
    "steps": ["Check USB cable", "Try different port", "Reinstall drivers"],
    "caution": "Do not use USB hubs"
  }]'
```

### 5. Start Services

**Windows (one-click):**

```bash
start.bat
```

**Manual startup:**

```bash
# Terminal 1: FastAPI server
python run.py

# Terminal 2: Celery worker
celery -A celery_app worker --loglevel=info --pool=solo

# Terminal 3: Celery beat (scheduled tasks)
celery -A celery_app beat --loglevel=info

# Terminal 4 (optional): IMAP IDLE watcher
# First set IMAP_IDLE_ENABLED=true in .env
python idle_watcher.py
```

### 6. Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"email-auto-processing-bot","langgraph_checkpointer":"postgres"}
```

Visit `http://localhost:8000/dev/mail-tester/` for the dev preview UI (only in `APP_ENV=development`).

## Configuration Reference

### Brand / Identity

| Variable | Description | Default |
|----------|-------------|---------|
| `BRAND_NAME` | Your company/product name (used in AI replies) | `MyBrand` |
| `SUPPORT_AGENT_NAME` | AI support persona name | `Alex` |
| `COMPANY_DESCRIPTION` | Short description of your company | `a software company...` |
| `SUPPORT_EMAIL_SIGNATURE` | Email signature appended to replies | `Technical Support Team\n...` |
| `FAQ_URL` | URL to your FAQ page for auto-scraping | (empty) |

### Email Accounts

Configure one or more IMAP/SMTP accounts as a JSON array:

```json
EMAIL_ACCOUNTS=[
  {
    "address": "support@example.com",
    "password": "app-specific-password",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "from_name": "Support Team",
    "imap_folder": "INBOX"
  }
]
```

**Gmail Setup**: Use an [App Password](https://support.google.com/accounts/answer/185833) instead of your regular password.

**Multiple Accounts**: Add multiple objects to the array. Each account is polled independently, and replies are sent from the same account that received the email.

### Confidence Thresholds

| Variable | Description | Default |
|----------|-------------|---------|
| `CONFIDENCE_AUTO_REPLY` | Minimum confidence for automatic sending | `0.85` |
| `CONFIDENCE_HUMAN_REVIEW` | Below this threshold, always send to human review | `0.60` |

### IMAP Modes

| Variable | Description | Default |
|----------|-------------|---------|
| `IMAP_POLL_INTERVAL_SECONDS` | Polling interval in seconds | `60` |
| `IMAP_IDLE_ENABLED` | Enable IMAP IDLE push mode (near-zero latency) | `false` |
| `IMAP_IDLE_RENEW_SECONDS` | IDLE connection renewal interval | `1500` |

### Webhook Notifications

Set up webhook notifications for human review alerts:

```ini
# DingTalk
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=your-secret
```

### Type B Email Detection

If your website has a contact form that forwards emails through a system address:

```ini
TYPE_B_SENDER_ADDRESSES=noreply@example.com,contactform@example.com
```

Type B emails extract the real user email from the forwarded message body.

## API Endpoints

### Core

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/webhook/email` | Receive parsed email (from Celery) |
| GET | `/review/action` | Human review callback (browser link) |
| POST | `/review/action` | Human review callback (programmatic) |
| GET | `/review/edit/{id}` | Full review page with editable draft |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/threads` | List email threads |
| GET | `/admin/threads/{id}` | Thread detail |
| GET | `/admin/training` | List training samples |
| GET | `/admin/training/export` | Export training data (JSONL) |
| GET | `/admin/kb` | List KB documents |
| POST | `/admin/kb` | Add/update KB document |
| POST | `/admin/kb/sop/import` | Bulk import SOP documents |
| POST | `/admin/kb/from-training/{id}` | Convert training sample to KB entry |
| GET | `/admin/kb/miss-analysis` | Cluster no-hit questions |
| GET | `/admin/stats` | Overview statistics |

### Dev Tools

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dev/mail-tester/` | Email preview UI (dev only) |
| POST | `/dev/mail-tester/run` | Run preview pipeline |
| GET | `/internal/qa/` | Internal Q&A testing page |
| POST | `/internal/qa/ask` | Ask AI a question |

## How It Works

### Email Processing Flow

1. **Receive**: IMAP client polls (or IDLE listens) for new unread emails
2. **Parse**: Extract sender, subject, body, attachments, thread ID
3. **Detect Language**: Identify the customer's language (15+ languages supported)
4. **Extract Info**: Pull out OS, device model, software version, error messages
5. **Search KB**: Semantic vector search + lexical reranking against knowledge base
6. **Generate Draft**: LLM creates a reply using KB evidence and extracted context
7. **Polish**: Second LLM pass for natural, human-like tone
8. **Route Decision**: Based on confidence score:
   - **Auto-Send** (confidence >= 0.85 + KB evidence): Reply sent automatically
   - **Human Review** (confidence < 0.60 or sensitive topic): Webhook notification sent, waits for human
   - **More Info** (missing critical details): Asks customer for more information
9. **Archive**: Every interaction is saved as a training sample

### Knowledge Base

The KB supports multiple source types:
- `inner_faq` — Manually curated FAQ entries
- `web_faq` — Auto-scraped from your FAQ page
- `sop` — Standard Operating Procedures with structured fields

All documents are embedded using OpenAI embeddings and stored with pgvector for semantic search.

### Human Review

When a reply needs human review:
1. A webhook notification is sent with the draft reply
2. The reviewer can:
   - **Approve**: Send the AI draft as-is
   - **Edit**: Modify the draft and send the edited version
   - **Reject**: Discard the draft (no email sent)
3. A full review page is available with conversation history, KB references, and an editable draft

## Deployment

### Linux (systemd)

Create service files for each component:

```ini
# /etc/systemd/system/email-bot-api.service
[Unit]
Description=Email Bot FastAPI
After=postgresql.service redis.service

[Service]
WorkingDirectory=/opt/email_bot
ExecStart=/opt/email_bot/.venv/bin/python run.py
Restart=always
EnvironmentFile=/opt/email_bot/.env

[Install]
WantedBy=multi-user.target
```

Create similar services for `celery worker`, `celery beat`, and optionally `idle_watcher.py`.

### Docker (example)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "run.py"]
```

Use `docker-compose` to orchestrate with PostgreSQL, Redis, and Celery.

## Customization

### Adding to the Knowledge Base

The easiest way to improve reply quality:

1. **Admin API**: `POST /admin/kb` with your Q&A data
2. **SOP Import**: `POST /admin/kb/sop/import` for structured procedures
3. **FAQ Scraping**: Set `FAQ_URL` and the weekly Celery beat task handles the rest
4. **Training Writeback**: `POST /admin/kb/from-training/{id}` to promote resolved tickets

### Adjusting AI Behavior

- **Prompts**: Edit `response_generator/prompts.py` to change the AI persona's behavior
- **Confidence**: Adjust `CONFIDENCE_AUTO_REPLY` and `CONFIDENCE_HUMAN_REVIEW` thresholds
- **Tone**: Edit `response_generator/reply_templates.py` for locale-specific tone guidance
- **Sensitive Topics**: Modify patterns in `message_understanding/info_extractor.py`

### Using a Different LLM

Any OpenAI-compatible API works:

```ini
OPENAI_BASE_URL=https://your-llm-provider.com/v1
OPENAI_API_KEY=your-key
OPENAI_MODEL=your-model-name
```

## License

MIT
