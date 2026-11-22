# Sentinel

Build spec (source of truth, shared with Antigravity's `.agents/rules/architecture.md`):

@ARCHITECTURE.md

## Working rules
- Every change must pass `uv run ruff check .`.
- Do not write tests unless the owner asks. Quality is measured by the evals: `uv run python -m evals.run_eval --config <name>` (live LLM calls; keys in `.env`).
- Remove dead code and redundant work when touching a module.
- For code reviews, use the `staff-code-reviewer` skill (`.claude/skills/staff-code-reviewer` → `.agents/rules/skills/staff-code-reviewer`).
