"""Starting, streaming and resuming graph runs for the UI, plus the per-visitor limits."""

import time
import uuid
from datetime import date

import streamlit as st

from src.config.settings import get_settings
from src.graph.graph_builder import build_graph, new_run
from src.llm.run_context import track_usage, use_visitor_keys

MAX_RUNS_PER_SESSION = 5


@st.cache_resource
def graph():
    """Built once per server process, so the in-memory checkpointer survives Streamlit reruns."""
    return build_graph()


@st.cache_resource
def daily_spend() -> dict[str, float]:
    """USD spent per day across all visitors, in memory (resets on restart, which is fine for a demo)."""
    return {}


def can_run(own_keys: bool = False) -> str | None:
    """Why a new run is not allowed right now, or None. Runs on the visitor's own keys skip the budget."""
    if not own_keys and daily_spend().get(date.today().isoformat(), 0.0) >= get_settings().daily_budget_usd:
        return "Daily demo budget reached. Add your own API key under 'Use your own repo' to keep going."
    if st.session_state.get("runs", 0) >= MAX_RUNS_PER_SESSION:
        return f"You have used all {MAX_RUNS_PER_SESSION} runs for this session."
    return None


def thread_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def request_run(origin: str, **run: object) -> None:
    """Remember a button click; the run itself happens at the bottom of the tab that asked for it."""
    st.session_state.pending = {"origin": origin, **run}


def start_run(
    origin: str,
    repo_url: str,
    raw_trace: str,
    base_commit: str | None,
    case: dict | None,
    keys: dict[str, str] | None = None,
) -> None:
    keys = {provider: key for provider, key in (keys or {}).items() if key}
    if reason := can_run(own_keys=bool(keys)):
        st.warning(reason)
        return
    st.session_state.update(
        thread_id=str(uuid.uuid4()),
        origin=origin,
        case=case,
        run_keys=keys,  # session memory only; gone when the tab closes
        steps=[],
        seen_problems=set(),
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
    keys = st.session_state.get("run_keys", {})
    box = st.container(border=True)  # not st.status: inside a tab it renders collapsed, hiding the steps
    status = box.empty()
    status.markdown("**Investigating…**")
    with track_usage() as usage, use_visitor_keys(keys), box:
        st.session_state.streamed_now = True  # this script run already shows the steps live
        for line in st.session_state.steps:  # a resume continues the list from the first part
            st.markdown(line)
        try:
            for mode, chunk in graph().stream(payload, thread_config(), stream_mode=["updates", "custom"]):
                if mode == "custom":  # a model retry or fallback, so waits are never silent
                    problem = chunk["notice"].split(";")[0]  # e.g. "gemini/x is rate limited"
                    if problem in st.session_state.seen_problems:  # agents and loop steps repeat it
                        continue
                    st.session_state.seen_problems.add(problem)
                    line = f"{badge(chunk['role'])} :orange[{chunk['notice']}]"
                    status.markdown(f"**Investigating…** :orange[{chunk['notice']}]")
                    st.markdown(line)
                    st.session_state.steps.append(line)
                    continue
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        continue
                    now = time.perf_counter()
                    line = step_line(node, now - last, describe_update(node, update or {}))
                    last = now
                    st.markdown(line)
                    st.session_state.steps.append(line)
                    status.markdown(f"**Investigating…** {NODE_STYLE.get(node, ('', node))[1]} done")
            status.markdown("**Finished**")
        except Exception as exc:  # e.g. a provider outage; shown to the visitor instead of a stack trace
            message = hide_keys(str(exc), keys)
            st.session_state.failure = f"The run crashed: {message}"
            status.markdown(f"**Run failed:** :red[{message[:200]}]")
    st.session_state.tokens += usage.tokens
    st.session_state.cost += usage.cost_usd
    st.session_state.seconds += time.perf_counter() - started
    if not keys:  # the visitor pays for runs on their own keys
        today = date.today().isoformat()
        daily_spend()[today] = daily_spend().get(today, 0.0) + usage.cost_usd


def hide_keys(text: str, keys: dict[str, str]) -> str:
    """Provider errors can echo a request URL; never show a key back on the page."""
    for key in keys.values():
        text = text.replace(key, "***")
    return text


NODE_STYLE = {  # badge colour and label per node, so each agent stands out in the step list
    "prepare_repo": ("gray", "Prepare repo"),
    "parse_trace": ("gray", "Parse trace"),
    "orchestrator": ("violet", "Orchestrator"),
    "locator": ("blue", "Locator agent"),
    "history": ("orange", "History agent"),
    "rca_writer": ("green", "RCA writer"),
    "reviewer": ("red", "Reviewer"),
    "human_review": ("gray", "Human review"),
    "create_pr": ("gray", "Create PR"),
    "report_external": ("gray", "External error"),
    "fail": ("red", "Failed"),
}


def badge(node: str) -> str:
    colour, label = NODE_STYLE.get(node, ("gray", node))
    return f":{colour}-badge[{label}]"


def step_line(node: str, seconds: float, summary: str) -> str:
    return f"{badge(node)} :gray[{seconds:.1f}s] {summary}"


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
