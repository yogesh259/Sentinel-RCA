# Sentinel-RCA Workspace Rules

- **Package Manager:** Always use `uv` (`uv add`, `uv run pytest`, `uv run uvicorn`). Never use raw `pip`.
- **Thin UI Only:** Never build a chatbot UI. The only frontend is `ui/console.py` (Streamlit) acting as an Ops & Chaos Control Panel.
- **1-Second Webhook SLA:** `POST /v1/webhooks/sentry` must verify HMAC-SHA256, check Redis `SETNX` fingerprint cooldown (30m TTL), enqueue background work, and return `202 Accepted` immediately.
- **Deterministic Security:** PII/secret redaction (emails, JWTs, API keys, DB URIs), Redis deduplication, and RBAC/approval gates MUST be pure Python—never delegated to LLM prompts.
- **Strict Schemas:** Use Pydantic v2 for all webhook payloads, MCP tool inputs/outputs, and `ProposedResolution` objects.
- **MCP Isolation:** Keep `trace_mcp`, `git_mcp`, and `tracker_mcp` as separate, standalone MCP server modules.
- **Cloud-Friendly Hybrid Routing:** Support `USE_LOCAL_OLLAMA=true|false` in `.env` so local dev can use Ollama while hosted deployments use a fast/cheap API model for the triage tier.
- **Testing:** Every new module requires deterministic `pytest` unit tests run via `uv run pytest`.
