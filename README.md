# Email Auto-Processing Bot / 邮件自动处理机器人

<details open>
<summary><b>English</b></summary>

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

</details>

<details>
<summary><b>中文</b></summary>

基于 LLM（大语言模型）的智能邮件客服系统。自动接收、理解并回复客户邮件，内置人工审核机制以保障回复质量。

## 功能特性

- **邮件自动处理**：通过 IMAP 轮询或 IDLE 推送模式从多个邮箱账户接收邮件
- **AI 智能回复**：基于 LangGraph 工作流 + GPT-4o，生成上下文感知的自然回复
- **知识库检索**：基于 pgvector 的语义搜索 + 词法重排序，确保回答准确
- **多语言支持**：自动检测 15+ 种语言，以客户使用的语言进行回复
- **置信度路由**：高置信度回复自动发送，不确定的回复转人工审核
- **人工审核系统**：Web 审核页面 + Webhook 通知（钉钉/Slack/飞书）
- **训练数据采集**：每次交互均归档，用于后续模型微调
- **FAQ 自动抓取**：定期抓取产品 FAQ 页面并更新知识库
- **管理后台 API**：会话管理、知识库管理、训练数据导出、未命中分析

## 系统架构

```
IMAP 收件箱 → Celery Worker → FastAPI Webhook → LangGraph 工作流
  → 语言检测 → 信息提取 → 知识库检索 → 草稿生成 → 路由决策
    ├─ 高置信度    → 自动发送 → 归档训练数据 → 结束
    ├─ 中/低置信度  → Webhook 通知 → 人工审核（中断）
    │                  ├─ 通过/编辑 → 发送回复 → 归档 → 结束
    │                  └─ 拒绝       → 归档 → 结束
    └─ 信息不足     → 发送「补充信息」请求 → 归档 → 结束
```

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 工作流引擎 | LangGraph（PostgreSQL checkpointer） |
| 大语言模型 | OpenAI GPT-4o（可配置，支持任何 OpenAI 兼容 API） |
| 数据库 | PostgreSQL + pgvector（向量相似度搜索） |
| 任务队列 | Celery + Redis |
| 邮件协议 | IMAP（轮询/IDLE） + SMTP |
| 向量嵌入 | OpenAI text-embedding-3-small |
| 语言检测 | langdetect |
| 网页抓取 | trafilatura + BeautifulSoup4 |

## 项目结构

```
email_bot/
├── run.py                          # FastAPI 入口
├── config.py                       # 统一配置（pydantic-settings）
├── celery_app.py                   # Celery 任务和定时调度
├── idle_watcher.py                 # IMAP IDLE 推送监听器
├── start.bat / stop.bat            # Windows 启动/停止脚本
├── .env.example                    # 环境变量模板
├── requirements.txt                # Python 依赖
│
├── api/
│   ├── main.py                     # FastAPI 路由（webhook、审核、健康检查）
│   ├── deps.py                     # 依赖注入（数据库会话）
│   ├── dev_mail_tester.py          # 开发环境邮件预览 UI
│   └── internal_qa.py              # 内部问答测试工具
│
├── workflow/
│   └── graph.py                    # LangGraph 状态机（13 个节点）
│
├── mail_gateway/
│   ├── imap_client.py              # IMAP 轮询 / IDLE / 邮件解析
│   ├── smtp_sender.py              # SMTP 回复发送
│   └── thread_tracker.py           # 邮件类型检测、会话 ID、去重
│
├── message_understanding/
│   ├── language_detector.py        # 语言检测（langdetect + CJK 修正）
│   └── info_extractor.py           # 系统/设备/版本提取（规则 + LLM）
│
├── knowledge_retrieval/
│   ├── vector_search.py            # pgvector 搜索 + 词法重排序
│   ├── faq_scraper.py              # 网站 FAQ 自动抓取
│   └── sop_loader.py               # SOP 知识导入
│
├── response_generator/
│   ├── prompts.py                  # 所有 LLM 提示词模板
│   ├── draft_builder.py            # LLM 草稿生成
│   ├── polisher.py                 # 回复语气润色 + 补充信息生成
│   ├── kb_gap_escalation.py        # 知识库缺口检测与内部上报
│   └── reply_templates.py          # 本地化写作指南
│
├── human_review/
│   ├── confidence_router.py        # 路由决策（自动/审核/补充信息）
│   └── dingtalk_notifier.py        # 审核 Webhook 通知
│
├── models/
│   ├── db.py                       # SQLAlchemy ORM 模型
│   └── schemas.py                  # Pydantic 数据模型
│
├── ops_admin/
│   ├── router.py                   # 管理 API 端点
│   └── data_collector.py           # 训练样本归档器
│
├── services/
│   ├── admin_stats.py              # 仪表盘统计
│   ├── kb_writeback.py             # 训练样本 → 知识库转换
│   ├── miss_analyzer.py            # 未命中问题聚类
│   └── training_export.py          # JSONL 导出
│
├── data/
│   ├── website_kb_entries.json     # 示例知识库导入数据
│   └── import_website_kb.py        # 知识库数据导入脚本
│
└── alembic/                        # 数据库迁移
    ├── env.py
    └── versions/
```

## 快速开始

### 前置条件

- Python 3.10+
- PostgreSQL 15+，需安装 [pgvector 扩展](https://github.com/pgvector/pgvector)
- Redis 6+
- OpenAI API Key（或任何 OpenAI 兼容 API）
- 一个或多个支持 IMAP/SMTP 的邮箱账户

### 1. 克隆并安装

```bash
git clone <your-repo-url>
cd email_bot

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境（Windows）
.venv\Scripts\activate

# 激活虚拟环境（Linux/macOS）
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装 LangGraph PostgreSQL checkpointer（可选但推荐）
pip install langgraph-checkpoint-postgres psycopg[binary] psycopg-pool
```

### 2. 配置环境变量

```bash
# 复制示例配置
cp .env.example .env
```

编辑 `.env` 文件，填入你的配置：

```ini
# ── 品牌信息（请自定义！） ──
BRAND_NAME=你的公司名
SUPPORT_AGENT_NAME=小智
COMPANY_DESCRIPTION=一个项目管理 SaaS 平台
SUPPORT_EMAIL_SIGNATURE=你的公司客服团队\nsupport@yourcompany.com

# ── 数据库 ──
DATABASE_URL=postgresql+asyncpg://postgres:yourpassword@localhost:5432/email_bot
DATABASE_URL_SYNC=postgresql://postgres:yourpassword@localhost:5432/email_bot

# ── 邮箱账户（JSON 数组） ──
EMAIL_ACCOUNTS=[{"address":"support@yourcompany.com","password":"your-app-password","imap_host":"imap.gmail.com","imap_port":993,"smtp_host":"smtp.gmail.com","smtp_port":587,"from_name":"客服团队"}]

# ── OpenAI ──
OPENAI_API_KEY=sk-your-api-key
OPENAI_MODEL=gpt-4o

# ── FAQ 地址（可选，用于自动抓取） ──
FAQ_URL=https://yourcompany.com/faq
```

### 3. 初始化数据库

```bash
# 创建数据库
createdb email_bot

# 启用 pgvector 扩展
psql email_bot -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 运行迁移
alembic upgrade head
```

### 4. 导入知识库（可选）

将 FAQ/SOP 数据添加到 `data/website_kb_entries.json`，然后运行：

```bash
python data/import_website_kb.py
```

或通过管理 API：

```bash
# 添加单条知识库条目
curl -X POST http://localhost:8000/admin/kb \
  -H "Content-Type: application/json" \
  -d '{
    "id": "faq-001",
    "source_type": "inner_faq",
    "title": "如何重置设置",
    "lang": "zh",
    "content": "Q: 如何重置？\nA: 前往 设置 > 恢复默认值。"
  }'

# 批量导入 SOP
curl -X POST http://localhost:8000/admin/kb/sop/import \
  -H "Content-Type: application/json" \
  -d '[{
    "title": "设备未检测到",
    "symptom": "软件无法识别 USB 设备",
    "steps": ["检查 USB 线缆", "尝试更换端口", "重新安装驱动"],
    "caution": "请勿使用 USB 集线器"
  }]'
```

### 5. 启动服务

**Windows（一键启动）：**

```bash
start.bat
```

**手动启动：**

```bash
# 终端 1：FastAPI 服务器
python run.py

# 终端 2：Celery Worker
celery -A celery_app worker --loglevel=info --pool=solo

# 终端 3：Celery Beat（定时任务）
celery -A celery_app beat --loglevel=info

# 终端 4（可选）：IMAP IDLE 监听器
# 先在 .env 中设置 IMAP_IDLE_ENABLED=true
python idle_watcher.py
```

### 6. 验证

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"email-auto-processing-bot","langgraph_checkpointer":"postgres"}
```

访问 `http://localhost:8000/dev/mail-tester/` 查看开发预览 UI（仅在 `APP_ENV=development` 下可用）。

## 配置参考

### 品牌 / 身份

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BRAND_NAME` | 公司/产品名称（用于 AI 回复） | `MyBrand` |
| `SUPPORT_AGENT_NAME` | AI 客服人设名称 | `Alex` |
| `COMPANY_DESCRIPTION` | 公司简短描述 | `a software company...` |
| `SUPPORT_EMAIL_SIGNATURE` | 邮件签名（附加在回复末尾） | `Technical Support Team\n...` |
| `FAQ_URL` | FAQ 页面地址（用于自动抓取） | （空） |

### 邮箱账户

以 JSON 数组格式配置一个或多个 IMAP/SMTP 账户：

```json
EMAIL_ACCOUNTS=[
  {
    "address": "support@example.com",
    "password": "应用专用密码",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "from_name": "客服团队",
    "imap_folder": "INBOX"
  }
]
```

**Gmail 设置**：请使用[应用专用密码](https://support.google.com/accounts/answer/185833)，而非常规密码。

**多账户**：在数组中添加多个对象即可。每个账户独立轮询，回复邮件从接收邮件的同一账户发出。

### 置信度阈值

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CONFIDENCE_AUTO_REPLY` | 自动发送的最低置信度 | `0.85` |
| `CONFIDENCE_HUMAN_REVIEW` | 低于此阈值必须人工审核 | `0.60` |

### IMAP 模式

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `IMAP_POLL_INTERVAL_SECONDS` | 轮询间隔（秒） | `60` |
| `IMAP_IDLE_ENABLED` | 启用 IMAP IDLE 推送模式（近零延迟） | `false` |
| `IMAP_IDLE_RENEW_SECONDS` | IDLE 连接续期间隔 | `1500` |

### Webhook 通知

配置人工审核的 Webhook 通知：

```ini
# 钉钉
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=your-secret
```

### B 类邮件检测

如果你的网站有联系表单通过系统地址转发邮件：

```ini
TYPE_B_SENDER_ADDRESSES=noreply@example.com,contactform@example.com
```

B 类邮件会从转发的邮件正文中提取真实用户邮箱。

## API 端点

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/webhook/email` | 接收解析后的邮件（来自 Celery） |
| GET | `/review/action` | 人工审核回调（浏览器链接） |
| POST | `/review/action` | 人工审核回调（程序调用） |
| GET | `/review/edit/{id}` | 完整审核页面（可编辑草稿） |

### 管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/threads` | 邮件会话列表 |
| GET | `/admin/threads/{id}` | 会话详情 |
| GET | `/admin/training` | 训练样本列表 |
| GET | `/admin/training/export` | 导出训练数据（JSONL） |
| GET | `/admin/kb` | 知识库文档列表 |
| POST | `/admin/kb` | 添加/更新知识库文档 |
| POST | `/admin/kb/sop/import` | 批量导入 SOP 文档 |
| POST | `/admin/kb/from-training/{id}` | 将训练样本转为知识库条目 |
| GET | `/admin/kb/miss-analysis` | 未命中问题聚类分析 |
| GET | `/admin/stats` | 概览统计数据 |

### 开发工具

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/dev/mail-tester/` | 邮件预览 UI（仅开发环境） |
| POST | `/dev/mail-tester/run` | 运行预览流水线 |
| GET | `/internal/qa/` | 内部问答测试页面 |
| POST | `/internal/qa/ask` | 向 AI 提问 |

## 工作原理

### 邮件处理流程

1. **接收**：IMAP 客户端轮询（或 IDLE 监听）新未读邮件
2. **解析**：提取发件人、主题、正文、附件、会话 ID
3. **语言检测**：识别客户使用的语言（支持 15+ 种语言）
4. **信息提取**：提取操作系统、设备型号、软件版本、错误信息
5. **知识库搜索**：语义向量搜索 + 词法重排序匹配知识库
6. **生成草稿**：LLM 结合知识库证据和提取的上下文生成回复
7. **润色**：第二轮 LLM 处理，使语气更自然、更像真人
8. **路由决策**：基于置信度评分：
   - **自动发送**（置信度 >= 0.85 且有知识库证据）：回复自动发出
   - **人工审核**（置信度 < 0.60 或涉及敏感话题）：发送 Webhook 通知，等待人工处理
   - **补充信息**（缺少关键细节）：向客户请求更多信息
9. **归档**：每次交互均保存为训练样本

### 知识库

知识库支持多种来源类型：
- `inner_faq` — 手动维护的 FAQ 条目
- `web_faq` — 从 FAQ 页面自动抓取
- `sop` — 标准操作流程（结构化字段）

所有文档使用 OpenAI Embeddings 生成向量，存储在 pgvector 中进行语义搜索。

### 人工审核

当回复需要人工审核时：
1. 通过 Webhook 发送通知，附带 AI 生成的草稿
2. 审核人员可以：
   - **通过**：直接发送 AI 草稿
   - **编辑**：修改草稿后发送
   - **拒绝**：丢弃草稿（不发送邮件）
3. 提供完整的审核页面，包含对话历史、知识库引用和可编辑的草稿

## 部署

### Linux (systemd)

为每个组件创建服务文件：

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

类似地为 `celery worker`、`celery beat` 以及可选的 `idle_watcher.py` 创建服务。

### Docker（示例）

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "run.py"]
```

使用 `docker-compose` 编排 PostgreSQL、Redis 和 Celery。

## 自定义

### 扩充知识库

提升回复质量最简单的方式：

1. **管理 API**：通过 `POST /admin/kb` 添加问答数据
2. **SOP 导入**：通过 `POST /admin/kb/sop/import` 导入结构化流程
3. **FAQ 抓取**：设置 `FAQ_URL`，每周定时任务自动更新
4. **训练回写**：通过 `POST /admin/kb/from-training/{id}` 将已解决工单转为知识库条目

### 调整 AI 行为

- **提示词**：编辑 `response_generator/prompts.py` 修改 AI 人设和行为
- **置信度**：调整 `CONFIDENCE_AUTO_REPLY` 和 `CONFIDENCE_HUMAN_REVIEW` 阈值
- **语气**：编辑 `response_generator/reply_templates.py` 自定义不同语言的语气指南
- **敏感话题**：修改 `message_understanding/info_extractor.py` 中的匹配规则

### 使用其他 LLM

支持任何 OpenAI 兼容 API：

```ini
OPENAI_BASE_URL=https://your-llm-provider.com/v1
OPENAI_API_KEY=your-key
OPENAI_MODEL=your-model-name
```

## 许可证

MIT

</details>
