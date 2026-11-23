<div align="center">

# Sentinel RCA

**Root-cause analysis for production errors, by a team of AI agents.**

[**Live demo**](https://sentinel-rca.streamlit.app/) &nbsp;·&nbsp; [How it works](#how-it-works) &nbsp;·&nbsp; [Evals](#evals) &nbsp;·&nbsp; [Run it locally](#run-it-locally)

Paste a stack trace and a GitHub repo. Sentinel finds the code that broke, the commit that introduced it,
and drafts a fix. Nothing is pushed until a human approves it.

![Python](https://img.shields.io/badge/Python-3.11%20·%203.12-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-multi--agent-1C3C3C?logo=langchain&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B?logo=streamlit&logoColor=white)
![Models](https://img.shields.io/badge/models-Gemini%20·%20Groq%20(free%20tiers)-8250df)
![Evals](https://img.shields.io/badge/evals-Sentinel%2014%2F15%20·%20single%20agent%209%2F15-1a7f37)

<br>

<a href="https://sentinel-rca.streamlit.app/">
  <img src="https://img.shields.io/badge/Try%20the%20live%20demo-sentinel--rca.streamlit.app-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white&labelColor=070707" alt="Try the live demo" height="40">
</a>

<br><br>

![Sentinel investigating a sample incident](.github/demo.gif)

</div>

## Run it locally

```bash
uv sync                                  # or: pip install -r requirements.txt
cp .env.example .env                     # add GEMINI_API_KEY and/or GROQ_API_KEY

uv run streamlit run ui/app.py                                                     # UI
uv run python -m src.main --trace trace.txt --repo https://github.com/owner/repo   # CLI
```

## How it works

```mermaid
flowchart TD
    input([Stack trace + GitHub repo]):::io
    input --> prepare[Prepare repo<br/><i>partial clone at the failing commit</i>]:::code
    prepare --> parse[Parse trace<br/><i>Java · Python, no LLM</i>]:::code
    parse --> orchestrator[Orchestrator<br/><i>focus frames + hypotheses</i>]:::orchestrator

    subgraph parallel [Run in parallel]
        direction LR
        locator[Locator agent<br/><i>read_file · search_code · list_dir</i>]:::locator
        history[History agent<br/><i>recent_commits · blame_lines · show_commit</i>]:::history
    end

    orchestrator --> locator
    orchestrator --> history
    locator --> writer[RCA writer<br/><i>suspects, introducing commit, patch</i>]:::writer
    history --> writer
    writer --> reviewer[Reviewer<br/><i>checks every citation is real</i>]:::reviewer
    reviewer -. "not grounded: one retry" .-> locator
    reviewer -- grounded --> human{{Human approval}}:::human
    human -- approve --> pr([Draft PR]):::io
    human -- reject --> stop([Done, nothing pushed]):::io

    parse -. "no frame in the repo" .-> external([Library / infra error report]):::io
    prepare -. error .-> failed([Error shown]):::fail
    parse -. error .-> failed

    classDef io fill:#f6f8fa,stroke:#8c959f,color:#24292f
    classDef code fill:#eaeef2,stroke:#8c959f,color:#24292f
    classDef orchestrator fill:#efe7ff,stroke:#8250df,color:#3e1f79
    classDef locator fill:#ddf4ff,stroke:#0969da,color:#0a3069
    classDef history fill:#fff1e5,stroke:#bc4c00,color:#762c00
    classDef writer fill:#dafbe1,stroke:#1a7f37,color:#044f1e
    classDef reviewer fill:#ffebe9,stroke:#cf222e,color:#82071e
    classDef human fill:#fff8c5,stroke:#9a6700,color:#4d2d00
    classDef fail fill:#ffebe9,stroke:#cf222e,color:#82071e
    style parallel fill:#ffffff,stroke:#8c959f,stroke-dasharray:4 3
```

| Node | What it does |
|---|---|
| `prepare_repo` | Fetches the repo with a partial clone: commit history plus the files at the failing commit, never old file versions. A few MB and 2–3 s even for a repo with 6k+ commits. Blame, log and grep then run locally, with no GitHub API rate limits. |
| `parse_trace` | Parses Java and Python traces, including `Caused by:` and chained exceptions, and marks which frames are files in the repo. Plain code, no LLM. |
| `orchestrator` | Picks up to 3 frames to focus on and a few hypotheses to test. |
| `locator` | Reads the code around the failing lines and finds where and why it breaks. |
| `history` | Blames the failing lines and reads diffs to find the commit that introduced the bug. Runs in parallel with the locator. |
| `rca_writer` | Turns both findings into a report: ranked suspects, the introducing commit and an optional patch. |
| `reviewer` | Checks the report in code first (files exist, line numbers are real, the commit exists, `git apply --check` passes), then asks an LLM whether each claim is backed by the evidence. |
| `human_review` | Pauses with LangGraph `interrupt()` until someone approves or rejects. |
| `create_pr` | Opens a **draft** PR with the patch and the RCA as its description. Dry run by default. |

| Agent | Tools |
|---|---|
| Orchestrator | none |
| Locator | `read_file`, `search_code`, `list_dir` |
| History | `recent_commits`, `blame_lines`, `show_commit` |
| RCA writer | none |
| Reviewer | none (code checks plus one LLM call) |

- **Every LLM answer is a Pydantic model.** A reply that doesn't validate is sent back once with the validation error.
- **Code checks before LLM checks.** A file that doesn't exist or a patch that doesn't apply is caught without asking a model.
- **It can't be talked into things.** The trace, code and git history are given to the models as data to read, never as instructions to follow. The tools can only read files, and only inside the repo.

## Free models with automatic fallback

Each agent has a short list of free Gemini and Groq models. If one is busy or out of quota, Sentinel switches to the next and shows it live. A run costs under a cent.

If the demo runs out of quota, you can paste your own free key. It stays in your browser session and is never stored.

## Evals

15 incidents are planted in the [victim repo](https://github.com/yogesh259/sentinel-victim-service), each with a known file, lines and commit. Sentinel is compared with a single agent that has the same six tools and one prompt. An incident counts as **solved** when the top suspect is the right file and lines, with the right commit. For the no-fault incidents, solved means no code was blamed.

| Incidents | Sentinel | Single agent |
|---|---|---|
| Easy: bug at or near the trace line (8) | 7/8 | 6/8 |
| Hard: cause in another file or a config file, misleading `git blame`, buried startup error (4) | 4/4 | 3/4 |
| Negative: prompt injection, broken client JSON, trace from another project (3) | 3/3 | 0/3 |
| **Total** | **14/15** | **9/15** |
| Avg LLM calls / time per incident | 12.9 / 145 s | 9.1 / 123 s |

- **On real bugs, Sentinel is a little more accurate for a little more work.** It solved 11 of the 12 against 9. It made about 40% more LLM calls and took about 20% longer. Dollar cost isn't compared: free-tier limits put the two on different models.
- **The big difference is safety.** The single agent followed instructions injected into the trace, and blamed `InvoiceService.java`, a file that doesn't exist in the repo. Sentinel parses the trace in plain code first, so a trace from outside the repo never reaches a model, and the reviewer checks every citation against the repo.
- **Both missed one incident**, a 100% discount that gets rejected. Only the incident's title says a free order is valid, and the agents see just the stack trace.

Per-incident results are on the app's Evals page and in [`evals/results/`](evals/results/).
