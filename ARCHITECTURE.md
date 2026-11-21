# Sentinel-RCA — Architecture & Refactoring Specification

## 1. Core Purpose
Self-hosted, language-agnostic Root-Cause Analysis (RCA) engine for production errors. Ingests error webhooks, suppresses alert storms, scrubs secrets deterministically, correlates stack traces with Git history via isolated MCP servers, and gates high-risk actions behind Human-in-the-Loop (HITL) RBAC approval.

---

## 2. Production Tech Stack ($0 Deployment)
- **Hosting:** Render (FastAPI + Streamlit) + Neon (Postgres) + Upstash (Redis).
- **Backend:** Python 3.11+, FastAPI, Pydantic v2 (`uv` for dependency management).
- **LLM Routing:** `litellm` Python SDK -> Groq ONLY:
  - **Triage Tier:** `groq/llama-3.1-8b-instant` (deduplication, fallback trace parsing).
  - **Deep RCA Tier:** `groq/llama-3.3-70b-versatile` (Git diff correlation & root cause).
  - **Resilience & Rate Limits:** No silent Simulation Mode fallback on rate limits. Redis rate-limit exhaustion rejects inbound webhooks immediately with `HTTP 429 (Too Many Requests)`. Upstream `litellm.RateLimitError` or missing `GROQ_API_KEY` raises domain exceptions (`LLMRateLimitError`, `LLMConfigurationError`) surfaced cleanly to callers and UI.
- **Workflow Engine:** LangGraph with `AsyncPostgresSaver` checkpointer (`interrupt()` for HITL).
- **Tool Layer:** 3 standalone `FastMCP` servers (`trace_mcp`, `git_mcp`, `tracker_mcp`) supporting `stdio` (Render single container) and Streamable HTTP with `MCP_AUTH_TOKEN` (Docker microservices).
- **Secret Redaction:** Yelp's `detect-secrets` library (deterministic secret scanning before any LLM call or DB write).
- **UI:** Thin 3-tab Streamlit Ops Console (`ui/console.py`) — no chatbot UI, no API key sidebar inputs.

### Explicitly Removed Bloat (Delete Completely)
- Remove `prometheus-client` and `/metrics` endpoint.
- Remove `langfuse`, `langchain-openai`, `langchain-google-genai`, `langchain-ollama`, and `USE_LOCAL_OLLAMA`.
- Remove hand-written regex walls in `redaction.py` (replace with `detect-secrets`).
- Remove dummy/placeholder API keys in `config.py` and `.env.example`.

---

## 3. API Endpoints
1. `POST /v1/incidents/ingest`
   - **Auth:** HMAC-SHA256 verification (`X-Webhook-Signature` using `WEBHOOK_SECRET`).
   - **Adapter:** Normalizes both Sentry payloads and generic payloads (`service`, `error`, `stack_trace`) into `IncidentEvent`.
   - **Redis Storm Suppression:** `SETNX` on `sha256(exception + file + line)` with 30-min TTL. Duplicates return `200 OK` (`$0` cost).
   - **Redis Rate Limiter:** Caps live Groq usage (e.g., `5` runs/IP/hour, `30` runs/day globally); excess traffic returns `HTTP 429 Too Many Requests` immediately with a clear detail message.
   - **Response:** Queues LangGraph background task and returns `202 Accepted` immediately.
2. `GET /v1/approvals` — Lists paused LangGraph threads awaiting HITL approval.
3. `POST /v1/approvals/{thread_id}/decision` — Accepts `decision` (`approve` | `reject`) and `reviewer_role` (`junior_dev` | `staff_sre`). Enforces RBAC (`junior_dev` -> `403` for `draft_hotfix_pr`) and resumes the paused Postgres checkpoint.
4. `GET /health` — Pings Neon Postgres and Upstash Redis; returns `200 OK` or `503 Service Unavailable`.

---

## 4. Storage & Observability (Neon Postgres)
Postgres serves 3 clean responsibilities (no external observability SaaS):
1. `issues` — Mini issue tracker queried/written by `tracker_mcp`.
2. LangGraph checkpoint tables — Persists paused/resumed workflow state by `thread_id`.
3. `execution_traces` — Structured audit log per incident (redacted payload, nodes visited, MCP tool calls, latency ms, tokens, cost USD, mode `LIVE_GROQ` | `SIMULATION`, verdict). Powers Streamlit Tab 1.

---

## 5. Agent Pipeline & Least-Privilege Policy
`parse_trace (trace_mcp)` -> `semantic_dedup (Groq 8B + tracker_mcp)` -> `investigate_git (Groq 70B + git_mcp)` -> `authorisation_gate (Pure Python)` -> `execute_action (tracker_mcp)`

- **`trace_mcp`:** Language-agnostic parser (Java, Python, JS/TS, Go; falls back to Groq 8B for unknown formats).
- **`git_mcp`:** Strictly read-only (`get_recent_commits`, `get_commit_diff`, `get_git_blame`, `get_file_contents`). Cloud-native via GitHub REST API (`httpx.AsyncClient`) using `GITHUB_TOKEN` and `GITHUB_REPO` with zero local disk dependency.
- **`tracker_mcp`:** `search_existing_issues`, `create_issue`, `draft_hotfix_pr`.
- **`authorisation_gate` (No LLM):**
  - Auto-approve `deduplicate` and `create_issue`.
  - Always `interrupt()` for `draft_hotfix_pr` or `CRITICAL` severity.
  - Escalate if `step_count >= 6` (circuit breaker) or prompt-injection patterns are detected.

---

## 6. Environment Variables (`src/core/config.py`)
Only these variables should exist (via Pydantic `BaseSettings`):
- `DATABASE_URL` (Neon Postgres)
- `REDIS_URL` (Upstash Redis)
- `WEBHOOK_SECRET` (HMAC signing secret)
- `GROQ_API_KEY` (Primary LLM key)
- `GEMINI_API_KEY` (Fallback LLM key)
- `PRIMARY_LLM_MODEL` (Default: `groq/llama-3.3-70b-versatile`)
- `FALLBACK_LLM_MODEL` (Default: `gemini/gemini-2.5-flash`)
- `GROQ_DAILY_LIMIT` (Default: `30`)
- `RATE_LIMIT_PER_HOUR` (Default: `5`)
- `GITHUB_TOKEN` (GitHub Personal Access Token)
- `GITHUB_REPO` (GitHub repository in `owner/repo` format)
- `MCP_AUTH_TOKEN` (Optional — used when running MCP over Streamable HTTP)

---

## 7. Code Quality Standards
- No god-classes, dead code, or cryptic `_` helper chains.
- Short, single-purpose functions with concise docstrings explaining *why* each exists.
- Every refactor must pass `uv run ruff check .` and `uv run pytest`.