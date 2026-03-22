# Email Auto-Processing Bot — Development Rules

Follow SOLID, DRY, SRP principles.
Maintain high cohesion, low coupling. Separate UI from core logic.

## Module Responsibilities

- `mail_gateway/` — Email send/receive only. No business logic, no LLM calls.
- `response_generator/` — Reply generation only. No direct DB access.
- `workflow/` — LangGraph orchestration only. No business logic.
- `services/` — All business logic lives here.
- `api/` — Parameter parsing and response formatting only.

## Key Rules

1. All workflow orchestration goes through LangGraph (`workflow/graph.py`).
2. LLM calls are only allowed in designated modules (response_generator, message_understanding).
3. Language detection must NOT use LLM — use `langdetect` library.
4. No hardcoded business data — use config or data layer.
5. Check for existing implementations before writing new code.
