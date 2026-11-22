# Sentinel-RCA

Project spec and architecture rules (source of truth, shared with Antigravity's `.agents/rules/architecture.md`):

@ARCHITECTURE.md

## Working rules
- Every change must pass `uv run ruff check .` and `uv run pytest`.
- Testing during refactoring: do not add new tests or modify unit/integration tests; `tests/eval/` may be updated when a refactor requires it (e.g. moved imports), until explicitly requested otherwise.
- Tools are reached only over MCP (`src/services/mcp_client.py`); never import `src/mcp_servers/` from application code.
- Remove dead code and redundant work when touching a module.
- For code reviews, use the `staff-code-reviewer` skill (`.claude/skills/staff-code-reviewer` → `.agents/rules/skills/staff-code-reviewer`).
