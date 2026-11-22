"""Sentinel demo UI: run a real investigation, watch each node finish, approve or reject the fix."""

import sys
import time
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `streamlit run` only adds this folder

import streamlit as st
from langgraph.types import Command

from evals.run_eval import load_cases, score
from src.agents.context import latest_findings
from src.config.settings import get_settings
from src.graph.graph_builder import build_graph, is_paused, new_run
from src.llm.client import track_usage
from src.models.report import Decision
from src.services.pull_request import pr_body
from src.services.repo_cache import RepoError, parse_repo_url, run_git

MAX_RUNS_PER_SESSION = 5
settings = get_settings()


@st.cache_resource
def graph():
    """Built once per server process, so the in-memory checkpointer survives Streamlit reruns."""
    return build_graph()


@st.cache_resource
def daily_spend() -> dict[str, float]:
    """USD spent per day across all visitors, in memory (resets on restart, which is fine for a demo)."""
    return {}


def can_run() -> str | None:
    """Why a new run is not allowed right now, or None."""
    if daily_spend().get(date.today().isoformat(), 0.0) >= settings.daily_budget_usd:
        return "Daily demo budget reached. See the Evals page or the demo video in the README."
    if st.session_state.get("runs", 0) >= MAX_RUNS_PER_SESSION:
        return f"You have used all {MAX_RUNS_PER_SESSION} runs for this session."
    return None


def thread_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def start_run(repo_url: str, raw_trace: str, base_commit: str | None, case: dict | None) -> None:
    if reason := can_run():
        st.warning(reason)
        return
    st.session_state.update(
        thread_id=str(uuid.uuid4()),
        case=case,
        steps=[],
        failure=None,
        tokens=0,
        cost=0.0,
        seconds=0.0,
        runs=st.session_state.get("runs", 0) + 1,
    )
    stream(new_run(repo_url, raw_trace, base_commit))


def stream(payload) -> None:
    """Run (or resume) the graph, writing one line per finished node as it happens."""
    started = last = time.perf_counter()
    with track_usage() as usage, st.status("Investigating…", expanded=True) as status:
        try:
            for chunk in graph().stream(payload, thread_config(), stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        continue
                    now = time.perf_counter()
                    line = f"**{node}** ({now - last:.1f}s): {describe_update(node, update or {})}"
                    last = now
                    st.markdown(line)
                    st.session_state.steps.append(line)
            status.update(label="Finished", state="complete", expanded=False)
        except Exception as exc:  # e.g. a provider outage; shown to the visitor instead of a stack trace
            st.session_state.failure = f"The run crashed: {exc}"
            status.update(label=f"Run failed: {exc}", state="error")
    st.session_state.tokens += usage.tokens
    st.session_state.cost += usage.cost_usd
    st.session_state.seconds += time.perf_counter() - started
    today = date.today().isoformat()
    daily_spend()[today] = daily_spend().get(today, 0.0) + usage.cost_usd


def describe_update(node: str, update: dict) -> str:
    """One short line per node for the progress list."""
    if update.get("error"):
        return f"error: {update['error']}"
    if trace := update.get("trace"):
        user = sum(f.is_user_code for f in trace.frames)
        return f"{trace.exception_type}, {len(trace.frames)} frames, {user} in your code"
    if plan := update.get("plan"):
        return "; ".join(plan.hypotheses)
    if findings := update.get("findings"):
        return findings[-1].summary
    if review := update.get("review"):
        return "grounded" if review.grounded else f"not grounded: {'; '.join(review.problems)}"
    if report := update.get("report"):
        return report.title
    if update.get("decision"):
        return update["decision"].action
    return "done"


def github_links(values: dict) -> tuple[str, str]:
    """Base URLs for file and commit links at the exact commit that was analysed."""
    owner, name = parse_repo_url(values["repo_url"])
    sha = run_git(Path(values["repo_path"]), "rev-parse", "HEAD").strip()
    return f"https://github.com/{owner}/{name}/blob/{sha}", f"https://github.com/{owner}/{name}/commit"


def show_run() -> None:
    values = graph().get_state(thread_config()).values
    paused = is_paused(graph(), thread_config())
    with st.expander("Steps", expanded=False):
        for line in st.session_state.steps:
            st.markdown(line)
    for problem in (values.get("error"), st.session_state.failure):
        if problem:
            st.error(problem)

    findings = {f.source: f for f in latest_findings(values.get("findings", []))}
    if findings:
        for column, source in zip(st.columns(2), ("locator", "history"), strict=True):
            if finding := findings.get(source):
                column.markdown(f"**{source.title()} agent** ({finding.confidence:.0%})\n\n{finding.summary}")

    if report := values.get("report"):
        show_report(values, report)
        if case := st.session_state.case:
            show_expected(case, report)
    if paused:
        show_approval(values)
    elif values.get("decision"):
        show_result(values)
    st.caption(f"{st.session_state.tokens:,} tokens · ${st.session_state.cost:.4f} · {st.session_state.seconds:.0f}s")


def show_report(values: dict, report) -> None:
    st.subheader(report.title)
    st.write(report.root_cause)
    blob, commit = github_links(values) if values.get("repo_path") else ("", "")
    for s in report.suspects:
        st.markdown(
            f"- [{s.file}:{s.start_line}-{s.end_line}]({blob}/{s.file}#L{s.start_line}-L{s.end_line}) "
            f"({s.confidence:.0%}): {s.reason}"
        )
    if report.introduced_by:
        st.markdown(f"Introduced by [{report.introduced_by}]({commit}/{report.introduced_by})")
    st.markdown(f"Confidence: **{report.confidence:.0%}**")
    if report.suggested_patch:
        st.code(report.suggested_patch, language="diff")
    if review := values.get("review"):
        (st.success if review.grounded else st.warning)(
            "Reviewer: every citation checked out."
            if review.grounded
            else "Reviewer problems:\n" + "\n".join(f"- {p}" for p in review.problems)
        )


def show_expected(case: dict, report) -> None:
    """Lets a visitor check the answer without knowing the codebase."""
    result = score(case, report)
    top = report.suspects[0] if report.suspects else None
    mark = {True: "✅", False: "❌", None: "–"}
    st.markdown("#### Expected vs Sentinel")
    st.table(
        [
            {
                "": "File",
                "Expected": ", ".join(case["gold_files"]),
                "Sentinel": top.file if top else "–",
                "Match": mark[result["top1_file"]],
            },
            {
                "": "Lines",
                "Expected": str(case["gold_lines"]),
                "Sentinel": f"{top.start_line}-{top.end_line}" if top else "–",
                "Match": mark[result["line_overlap"]],
            },
            {
                "": "Commit",
                "Expected": case["introduced_by"] or "–",
                "Sentinel": report.introduced_by or "–",
                "Match": mark[result["commit_hit"]],
            },
        ]
    )


def show_approval(values: dict) -> None:
    st.markdown("#### Your decision")
    note = st.text_input("Note (optional)")
    approve, reject = st.columns(2)
    for column, action in ((approve, "approve"), (reject, "reject")):
        if column.button(
            action.title(), use_container_width=True, type="primary" if action == "approve" else "secondary"
        ):
            stream(Command(resume=Decision(action=action, note=note or None).model_dump()))
            st.rerun()


def show_result(values: dict) -> None:
    report, decision = values["report"], values["decision"]
    if decision.action == "reject":
        st.info("Rejected. Nothing was sent to GitHub.")
    elif values.get("pr_url"):
        st.success(f"Draft PR opened: {values['pr_url']}")
    elif report.suggested_patch:
        st.info("Dry run: this is the draft PR that would be opened.")
        st.markdown(f"**{report.title}**\n\n{pr_body(report, decision)}")
    else:
        st.info("Approved. There is no patch, so no PR is needed.")


st.set_page_config(page_title="Sentinel", page_icon="🛰️", layout="wide")
st.title("Sentinel")
st.write(
    "Paste a stack trace and a GitHub repo. A team of agents finds the root cause, cites the code "
    "and the commit that introduced it, and drafts a fix for you to approve."
)

samples, own = st.tabs(["Try a sample incident", "Use your own repo"])
with samples:
    demo_cases = [c for c in load_cases() if c.get("demo")]
    for column, case in zip(st.columns(len(demo_cases)), demo_cases, strict=True):
        with column.container(border=True):
            st.markdown(f"**{case['title']}**")
            st.caption(case["stack_trace"].splitlines()[0].split(":")[0])
            if st.button("Investigate", key=case["id"], disabled=bool(can_run())):
                start_run(case["repo_url"], case["stack_trace"], case["base_commit"], case)
    if settings.example_pr_url:
        st.markdown(f"[See an example PR Sentinel opened]({settings.example_pr_url})")
with own:
    repo_url = st.text_input("Public GitHub repo", placeholder="https://github.com/owner/repo")
    raw_trace = st.text_area("Stack trace (Java or Python)", height=200)
    if st.button("Run", disabled=bool(can_run()) or not (repo_url and raw_trace)):
        try:
            parse_repo_url(repo_url)
            start_run(repo_url, raw_trace, None, None)
        except RepoError as exc:
            st.error(str(exc))

if reason := can_run():
    st.info(reason)
if "thread_id" in st.session_state:
    st.divider()
    show_run()
