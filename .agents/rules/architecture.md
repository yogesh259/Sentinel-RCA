# Sentinel-RCA — Architecture Specification

## 1. Core Purpose
Self-hosted, language-agnostic Root-Cause Analysis (RCA) engine for production errors. Ingests error webhooks, suppresses alert storms, scrubs secrets deterministically, correlates stack traces with Git history through least-privilege MCP servers, has an LLM judge the evidence, and gates risky actions behind Human-in-the-Loop (HITL) RBAC approval.

Design principle: **code retrieves and decides, the LLM judges.** Deterministic code gathers evidence and chooses actions; the LLM only returns schema-validated verdicts over evidence it was shown.

---

## 2. Production Tech Stack ($0 Deployment)
- **Hosting:** Render (FastAPI + Streamlit) + Neon (Postgres) + Upstash (Redis).
- **Backend:** Python 3.11+, FastAPI, Pydantic v2 (`uv` for dependency management).
- **Persistence:** SQLAlchemy 2.0 async ORM over psycopg 3 (the single Postgres driver); Alembic migrations.
- **LLM Routing:** `litellm` with two tiers, Groq primary and Gemini fallback:
  - **Triage tier:** `groq/llama-3.1-8b-instant` — semantic dedup, fallback trace parsing.
  - **Deep RCA tier:** `PRIMARY_LLM_MODEL` (`groq/llama-3.3-70b-versatile`) — root-cause judgement.
  - **Fallback:** a failed primary call (typically a free-tier 429) is retried once on `FALLBACK_LLM_MODEL` (`gemini/gemini-2.5-flash`).
  - **No simulation mode.** If no provider can serve a call, domain exceptions (`LLMConfigurationError`, `LLMRateLimitError`, `LLMOutputError`) are raised; the investigate node records `investigation_error` and the gate escalates the incident to a human.
  - **Structured output only:** every call returns a Pydantic model (`RCAVerdict`, `DedupVerdict`, `TraceLocation`), with one repair retry on validation failure.
- **Ingress rate limits:** Redis caps runs (`RATE_LIMIT_PER_HOUR` per IP, `LLM_DAILY_LIMIT` per day); excess webhooks get `HTTP 429` immediately.
- **Workflow Engine:** LangGraph with `AsyncPostgresSaver` (psycopg pool, `prepare_threshold=0` for Neon's pooler). `interrupt()` pauses for HITL. Only `ENV=local` may fall back to `MemorySaver`.
- **Tool Layer:** 3 MCP servers (`trace_mcp`, `git_mcp`, `tracker_mcp`) built on the `mcp` SDK's `MCPServer`, reached only through `src/services/mcp_client.py`:
  - `MCP_TRANSPORT=stdio` (production): each server is a subprocess, started outside the repo directory, whose environment contains only its own secrets.
  - `MCP_TRANSPORT=inprocess` (tests): same MCP protocol over in-memory streams.
- **Secret Redaction:** Yelp's `detect-secrets` (pattern + keyword plugins; entropy plugins disabled) plus explicit email, bearer-token and LLM-key patterns. Applied at the gateway, in `parse_trace`, and on every text `git_mcp` returns.
- **UI:** Thin 3-tab Streamlit Ops Console (`ui/console.py`) — no chatbot UI, no API key inputs.

---

## 3. API Endpoints
1. `POST /v1/incidents/ingest` (alias `/v1/webhooks/sentry`)
   - **Auth:** HMAC-SHA256 verification (`X-Webhook-Signature` using `WEBHOOK_SECRET`).
   - **Adapter:** Accepts Sentry payloads and generic payloads (`service`, `error`, `stack_trace`).
   - **Storm suppression:** Redis `SETNX` on `sha256(exception|file|line)` with a 30-min TTL. Duplicates return `200 OK` at `$0`.
   - **Rate limiter:** see §2; returns `429` with a clear detail message.
   - **Response:** `202 Accepted`; the LangGraph run executes as a background task on a unique `thread_id`.
2. `GET /v1/approvals` — Threads whose checkpoint holds an unresolved `approval_required` interrupt (the checkpoint is the single source of truth), including proposal, reason and evidence.
3. `POST /v1/approvals/{thread_id}/decision` — `decision` (`approve` | `reject`), `reviewer_role` (`junior_dev` | `staff_sre`). RBAC: `junior_dev` approving `draft_hotfix_pr` → `403`. Resumes the paused checkpoint.
4. `GET /v1/traces` — Latest execution traces for the console.
5. `GET /health` — Pings Postgres and Redis; `200` or `503`.

---

## 4. Storage & Observability (Neon Postgres)
Schema is owned by migrations, never by application code: `python -m src.db.migrate` applies Alembic SQL revisions (`migrations/versions/`) plus LangGraph's checkpoint `setup()`. It runs before the API starts (docker compose `migrate` service, Render start command). Runtime code only reads and writes rows, through ORM entities (`src/db/models.py`) and repositories (`src/repositories/`); `alembic check` verifies entities and schema agree. LangGraph's checkpoint tables are library-owned and excluded from the ORM and autogenerate.

1. `issues` — Mini issue tracker owned by `tracker_mcp`.
2. LangGraph checkpoint tables — paused/resumed workflow state per `thread_id`.
3. `execution_traces` — Audit log per incident: redacted payload, `nodes_visited`, the real MCP tool calls (server, tool, args, latency, ok), tokens, cost USD, models used, verdict. Every node appends to append-only state lists; nothing is reconstructed after the fact.

---

## 5. Agent Pipeline & Least-Privilege Policy
`parse_trace` → `dedup` → `investigate_git` → `authorisation_gate` → `execute_action`

- **`parse_trace`:** redacts, parses via `trace_mcp` (Java/Kotlin/Scala, Python; triage-tier LLM fallback for unknown formats), then resolves each frame to a repository path via `git_mcp.resolve_repo_path` (GitHub tree + package-suffix matching; no hardcoded layouts).
- **`dedup`:** `tracker_mcp.search_existing_issues` finds open issues sharing the exception or file; only if candidates exist, the triage model decides whether one is the same bug (it may only answer with a candidate id).
- **`investigate_git` (the agentic step — retrieve, then judge):**
  1. *Retrieve (code):* blame lines `±5` around the top 2 repository frames (GitHub GraphQL blame), add recent commits per file, rank by exact-line blame > nearby blame > recently touched, keep 3, fetch per-file diff hunks and a file window.
  2. *Judge (LLM):* one deep-RCA call returns an `RCAVerdict` (`suspect_sha | null`, confidence, severity, root cause, mechanism, cited evidence, fix suggestion). All repository and log text is fenced in `<untrusted_data>`.
  3. *Validate (code):* a suspect or evidence SHA that was not retrieved is discarded and confidence forced to `low`. Prompt-injection traces skip the LLM entirely.
- **`git_mcp`:** read-only (`resolve_repo_path`, `get_recent_commits`, `get_commit_diff`, `get_blame_range`, `get_file_contents`) via GitHub REST/GraphQL with `GITHUB_TOKEN`/`GITHUB_REPO`; validated inputs; redacted outputs.
- **`tracker_mcp`:** `search_existing_issues`, `create_issue`, `draft_hotfix_pr` (a PR *preview*; git access is read-only by design).
- **`authorisation_gate` (no LLM, rules in `src/graph/policy.py`):**
  - Action policy: duplicate → `deduplicate`; investigation error → `escalate`; suspect + `high` confidence + `high|critical` severity + fix → `draft_hotfix_pr`; otherwise `create_issue`.
  - Auto-approve `deduplicate` and `create_issue` with medium/high confidence.
  - Always `interrupt()` for `draft_hotfix_pr`, `CRITICAL` severity, or a low-confidence `create_issue`.
  - Block and escalate if prompt-injection patterns are detected. The pipeline is a straight line with no loops; LangGraph's `recursion_limit` bounds any run.

---

## 6. Environment Variables (`src/core/config.py`)
- `ENV` (`local` | `staging` | `prod`)
- `DATABASE_URL` (Neon Postgres)
- `REDIS_URL` (Upstash Redis)
- `WEBHOOK_SECRET` (HMAC signing secret)
- `GROQ_API_KEY` (Primary LLM key)
- `GEMINI_API_KEY` (Fallback LLM key)
- `PRIMARY_LLM_MODEL` (Default: `groq/llama-3.3-70b-versatile`)
- `FALLBACK_LLM_MODEL` (Default: `gemini/gemini-2.5-flash`)
- `LLM_DAILY_LIMIT` (Default: `30`)
- `RATE_LIMIT_PER_HOUR` (Default: `5`)
- `GITHUB_TOKEN` (GitHub token; read access to the target repo)
- `GITHUB_REPO` (`owner/repo`; required, no default)
- `MCP_TRANSPORT` (`stdio` default | `inprocess`)

MCP servers read their own scoped settings (`GitServerSettings`, `TrackerServerSettings`) and never import the API config.

---

## 7. Evaluation (`tests/eval/`)
- Golden scenarios: true bugs, abstentions (correct answer is "no suspect"), storm duplicates, secret leaks, prompt injections.
- The harness runs the real nodes; secret-leak checks inspect graph state **and every prompt sent to the LLM**.
- **Mock mode (CI, $0):** measures parsing, path resolution, retrieval recall/ranking and safety properties. It never reports model accuracy.
- **Live mode (`--live`):** real repo and models plus an LLM judge, capped by `EVAL_MAX_COST_USD`; writes `tests/eval/live_report.json`. Only safety metrics are gated.

---

## 8. Code Layout
- `src/main.py` — bootstrap only: logging, lifespan (tables, checkpointer, MCP sessions), router registration.
- `src/api/` — HTTP layer, one `APIRouter` per REST resource (`incidents`, `approvals`, `traces`, `health`). Translates HTTP to service calls; no business logic.
- `src/services/` — business logic and integrations (`payload_adapter`, `workflow_runner`, `execution_traces`, `redis_dedup`, `llm_router`, `mcp_client`, ...).
- `src/graph/` — LangGraph state, nodes and policy; knows nothing about HTTP.
- `src/mcp_servers/` — tool processes; never imported by application code (only by `mcp_client`).
- `src/schemas/` — Pydantic models; import from submodules directly.
- `src/db/` — `models.py` (ORM entities, one per owned table), `engine.py` (engine/session factory), `session.py` (API `session_scope()`), `migrate.py`; `migrations/` holds Alembic revisions. No DDL anywhere else.
- `src/repositories/` — data access per table; functions take an `AsyncSession` and never commit (the caller owns the transaction).
- Python naming: no `Controller`/`Service` suffixes; the package name carries the layer.

## 9. Code Quality Standards
- No god-classes, dead code, redundant work, or cryptic `_` helper chains.
- Tools are reached only over MCP via `src/services/mcp_client.py`; no direct imports of `src/mcp_servers/`.
- Short, single-purpose functions with concise docstrings explaining *why* each exists.
- Every change must pass `uv run ruff check .` and `uv run pytest`.

Testing during refactoring: do not add new tests or modify unit/integration tests; `tests/eval/` may be updated when a refactor requires it (e.g. moved imports), until explicitly requested otherwise.
