# Sentinel-RCA

Project spec and architecture rules (source of truth, shared with Antigravity's `.agents/rules/architecture.md`):

@ARCHITECTURE.md

## Working rules
- Every change must pass `uv run ruff check .` and `uv run pytest`.
- Never create or modify unit/integration tests during refactoring; maintain only `tests/eval/` until explicitly requested.
- For code reviews, use the `staff-code-reviewer` skill (`.claude/skills/staff-code-reviewer` → `.agents/rules/skills/staff-code-reviewer`).
