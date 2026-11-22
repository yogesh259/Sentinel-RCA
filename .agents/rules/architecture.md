# Sentinel v2 — Build Spec

Build in the phases in §14, stop after each phase, and summarise what was built so the owner can review it. Prefer simple, readable code over clever abstractions. The owner must be able to explain every line in an interview.

## 1. What Sentinel is
Sentinel traces a stack trace to its root cause in your code. The user pastes a stack trace (Java or Python) and a GitHub repo URL. Sentinel:

1. Parses the trace and works out which frames belong to the user's code.
2. An orchestrator plans the investigation.
3. Two specialist agents run in parallel:
   - **Locator** reads the code around the failing frames.
   - **History** checks what recently changed in those files (git log / blame).
4. An RCA writer produces a structured root-cause report with file + line citations and a suggested patch.
5. A reviewer checks every citation is real; if not, the locator gets one retry.
6. A human approves or rejects (LangGraph `interrupt()`).
7. On approval, Sentinel opens a draft PR with the patch and the RCA in the description.

Resume line this project must support:
> Built Sentinel, a LangGraph multi-agent RCA system: an orchestrator routes stack traces to tool-restricted locator and git-history agents running in parallel, with Pydantic-validated reports, a grounding reviewer, human approval before any PR, and per-node model routing across Gemini and Groq via LiteLLM. Found the faulty file in X/20 real bugs vs Y/20 for a single-prompt agent baseline.

## 2. Rewrite rules
- Rewrite, not an extension of the old code. No Redis, Postgres, FastAPI, queue/worker, ORM or Docker Compose.
- Keep old code only if it fits a file in §4 without adding dependencies.
- No frameworks or dependencies beyond §3.
- Budget: ~1,000–1,400 lines of Python excluding tests; no file over ~250 lines. Graph, nodes, tools and prompts are most of the code; infrastructure near zero.
- No persistence. LangGraph's `InMemorySaver` only because `interrupt()` needs a checkpointer. A restart loses paused runs; no database files.

## 3. Tech stack
| Purpose | Library |
|---|---|
| Graph orchestration | langgraph (built-in InMemorySaver) |
| LLM calls (all providers) | litellm |
| Structured output / validation | pydantic, pydantic-settings |
| Config | pyyaml |
| GitHub PRs | PyGithub |
| UI | streamlit |
| Tests | pytest |

Git operations use the `git` CLI via subprocess (clone, log, blame, apply, branch, commit, push). Code search uses `git grep`.
API keys (in `.env`, never committed): `GEMINI_API_KEY`, `GROQ_API_KEY`, `GITHUB_TOKEN`.

## 4. Folder structure
```
src/
├── main.py                     # CLI: python -m src.main --trace trace.txt --repo <url>
├── config/
│   ├── settings.py             # pydantic-settings: keys, limits, paths
│   ├── models.yaml             # LiteLLM model per node role
│   └── prompts.yaml            # one prompt per node
├── models/                     # Pydantic models only, no logic
│   ├── state.py
│   ├── trace.py                # Frame, ParsedTrace
│   ├── plan.py                 # Plan
│   ├── finding.py              # Finding, Evidence
│   └── report.py               # RCAReport, ReviewResult, Decision
├── llm/client.py               # structured() and tool_agent() over LiteLLM
├── tools/
│   ├── trace_parser.py         # plain code: Java + Python trace parsing
│   ├── repo_tools.py           # read_file, search_code, list_dir
│   └── git_tools.py            # recent_commits, blame_lines, show_commit
├── agents/                     # one file per LLM node
│   ├── orchestrator.py
│   ├── locator.py
│   ├── history.py
│   ├── rca_writer.py
│   └── reviewer.py
├── graph/
│   ├── nodes.py                # plain nodes: prepare_repo, parse_trace, human_review, create_pr, report_external
│   ├── routing.py              # pure routing functions
│   └── graph_builder.py        # StateGraph wiring + compile with checkpointer
└── services/repo_cache.py      # clone/checkout into ./.cache/repos/<owner>__<repo>
streamlit_app/home.py
evals/{dataset.json, run_eval.py, baseline_agent.py, results/}
tests/
docs/graph.png
.env.example, requirements.txt, Dockerfile (optional), README.md
```

## 5. Pydantic models
- `Frame(raw, language: java|python, file, line: int|None, function: str|None, is_user_code=False)`
- `ParsedTrace(language, exception_type, message, frames (top of stack first), caused_by: list[str])`
- `Plan(focus_frames: list[int] max 3, hypotheses: 1–3, look_for: list[str])`
- `Evidence(file, start_line, end_line, snippet (≤~15 lines), why_relevant)`
- `Finding(source: locator|history, summary, evidence, confidence 0–1)`
- `SuspectLocation(file, start_line, end_line, reason, confidence 0–1)`
- `RCAReport(title, root_cause, suspects ≤3 ranked, introduced_by: str|None, suggested_patch: str|None, confidence 0–1)`
- `ReviewResult(grounded, problems)`
- `Decision(action: approve|reject, note=None)`

## 6. Graph state
```python
class SentinelState(TypedDict):
    repo_url: str
    raw_trace: str
    repo_path: str | None
    trace: ParsedTrace | None
    plan: Plan | None
    findings: Annotated[list[Finding], operator.add]
    report: RCAReport | None
    review: ReviewResult | None
    retries: int  # locator retries, max 1
    decision: Decision | None
    pr_url: str | None
    error: str | None
```

## 7. Tools
Typed inputs (Pydantic), never raise to the LLM (return an error string), output truncated to 6,000 chars, read-only.

| Tool | Signature | Notes |
|---|---|---|
| read_file | (path, start=1, end=120) | Numbered lines, max 200 per call, path must be inside repo |
| search_code | (pattern, glob="*") | `git grep -n`, max 30 hits |
| list_dir | (path="") | Max 100 entries; skips .git, build, target, node_modules, __pycache__ |
| recent_commits | (path, limit=5) | sha, date, author, subject; max 10 |
| blame_lines | (path, start, end) | `git blame -L`, max 40 lines |
| show_commit | (sha, path=None) | `git show --stat` + diff for that path, truncated |

Tool restriction per agent, enforced in code: orchestrator none; locator read_file/search_code/list_dir; history recent_commits/blame_lines/show_commit; rca_writer none; reviewer none (plain checks + one LLM call).

## 8. LLM layer (`src/llm/client.py`)
- `structured(role, prompt, schema, context) -> T`: model from models.yaml; JSON matching the schema; validate, retry once with the error appended; record tokens + cost (`litellm.completion_cost`) in a per-run cost log.
- `tool_agent(role, prompt, tools, output, max_steps, context) -> T`: tool schemas from each tool's Pydantic input model; loop call → run tools → append → repeat; stop on a `finish` tool whose arguments are `output`; at max_steps force a final `output` call; block an identical tool call repeated twice in a row.

## 9. Nodes
| Node | Type | Does |
|---|---|---|
| prepare_repo | plain | Validate URL; partial clone (`--filter=blob:none`) into cache or reuse; checkout default branch (or base_commit in evals) |
| parse_trace | plain | Parse Java/Python; mark is_user_code if the file exists in the repo |
| orchestrator | structured → Plan | Up to 3 focus frames, hypotheses |
| locator | tool_agent, 8 steps → Finding | Read code around focus frames; on retry also gets review.problems |
| history | tool_agent, 5 steps → Finding | Recent commits + blame on focus lines; likely introducing commit |
| rca_writer | structured → RCAReport | Combine findings into report + optional unified diff |
| reviewer | plain + structured → ReviewResult | Files exist, line ranges valid, patch passes `git apply --check`; then LLM checks claims vs evidence |
| human_review | plain + interrupt() | Pause with report; resume value is a Decision dict |
| create_pr | plain | Branch `sentinel/<short-hash>`, apply, commit, push, draft PR; no patch → no PR; respect DRY_RUN |
| report_external | plain | No user-code frames → library/infra error report |
| fail | plain | Surface error → END |

## 10. Graph wiring
```
START → prepare_repo ─(error)→ fail → END
                     └→ parse_trace ─(error)→ fail
                                    ├─(no user frames)→ report_external → END
                                    └→ orchestrator → [locator ∥ history] → rca_writer → reviewer
reviewer ─(not grounded and retries < 1)→ locator
         └→ human_review ─(approve)→ create_pr → END
                         └─(reject)→ END
```
Routing functions are pure functions of state in `routing.py`. Compile with `InMemorySaver()`; each run has a uuid `thread_id`. In Streamlit build the graph once with `@st.cache_resource`. Export `docs/graph.png` via `graph.get_graph().draw_mermaid_png()`.

## 11. Streamlit UI (`streamlit_app/home.py`, ~200 lines)
- **Tab A "Try a sample incident"** (default): cards from `evals/dataset.json` where `demo: true`; Investigate runs live on the victim repo; afterwards an "Expected vs Sentinel" box (file/lines/commit with ✅/❌). Link to a real example draft PR.
- **Tab B "Use your own repo"**: repo URL, trace, Run.
- Guardrails: `PUBLIC_DEMO=true` forces DRY_RUN; `DAILY_BUDGET_USD` (default $2) tracked in memory; max 5 runs per session; public repos ≤ 5,000 files.
- Run view: `graph.stream(..., stream_mode="updates")` with one `st.status` row per node; locator and history side by side; report with GitHub line links, patch in `st.code(language="diff")`, reviewer result; Approve/Reject on `__interrupt__` → `Command(resume=decision.model_dump())`; PR link or dry-run preview; footer with tokens, cost, time.
- Second page **Evals**: reads `evals/results/*.json`.

## 12. Evaluation (`evals/`)
- `dataset.json`: group `victim` (8–10 planted bugs in `yogesh259/sentinel-victim-service`, some `demo: true`) and group `public` (10–15 real bugs, analysed at `base_commit` before the fix). Fields: id, group, demo, title, repo_url, base_commit, stack_trace, gold_files, gold_lines, introduced_by, fix_commit. Convert the existing eval set rather than creating a new one.
- Metrics per case: top-1 / top-3 file hit, line overlap, introducing-commit hit, reviewer-grounded, cost, latency, tool calls, crash. Report per group and overall.
- Configs: `baseline` (one tool_agent, all tools), `graph_single_model`, `graph_routed`, `graph_no_reviewer`. Output `evals/results/<date>_<config>.json` + markdown table. Eval mode auto-approves with DRY_RUN.

## 13. Tests
No LLM calls in unit tests. (Owner's rule: no new tests are written during the rewrite unless the owner asks.)

## 14. Build phases (stop after each)
| Phase | Build | Done when |
|---|---|---|
| 1 | Clean out old infra; folder structure; settings; models; trace_parser | pytest passes; old infra gone |
| 2 | repo_cache, repo + git tools; llm/client.py | Tools work on a real cloned repo from a script |
| 3 | orchestrator, locator, rca_writer; linear graph | `python -m src.main` prints an RCAReport for a real trace |
| 4 | history in parallel; reviewer + retry; report_external; routing | Graph image shows every branch |
| 5 | human_review with interrupt() + InMemorySaver; create_pr with dry run | A run pauses and resumes in the same process |
| 6 | Streamlit UI | One click on a sample card runs end-to-end on the victim repo |
| 7 | Eval dataset, baseline agent, run_eval.py, Evals page | Results table for all 4 configs, per group |
| 8 | README, docs/graph.png, Dockerfile, deploy (Streamlit Community Cloud or HF Spaces) with PUBLIC_DEMO=true | A stranger can click a sample incident with no setup |

## 15. README must contain
Pitch + demo GIF; graph image + one sentence per node; "Why a graph and not one agent?" with the eval table; model routing table and cost per run; how to run locally; limitations (no code execution, patch correctness not guaranteed, Java + Python only, public repos).

## 16. Coding rules
- Type hints everywhere; Pydantic for every LLM output; no free-text parsing of LLM replies.
- No global mutable state except the compiled graph.
- Prompts live in `prompts.yaml`.
- Each agent file exposes `def <name>_node(state: SentinelState) -> dict` returning only changed keys.
- Log each node's start/end, tokens and cost with `logging`.
- Never log or display API keys. Treat stack traces and repo content as untrusted data in prompts.
- No features beyond this spec without asking.

## 17. Demo repo (`yogesh259/sentinel-victim-service`)
Owner-maintained; Sentinel only reads it.
- Before going public: remove every bug-revealing comment (`// BUG: ...`); replace hard-coded secrets with `@Value("${...}")`.
- Grow to ~12–15 files (controller/service/repository/dto/config/exception) with 8–10 planted bugs, each introduced by its own normal-looking commit, with real captured stack traces: NPE on optional field, unclosed JDBC connection, BigDecimal.divide without rounding, Long/Double→Integer ClassCastException, ConcurrentModificationException, Optional.get() on empty, unvalidated date/number parsing, re-wrapped `Caused by:` chain.
- Keep the bugs on main; tag the eval state (e.g. `eval-v1`) and use that SHA as base_commit.
