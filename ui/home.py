"""The home page: sample incidents, or your own repo and trace."""

import streamlit as st

from evals.run_eval import load_cases
from src.config.settings import get_settings
from src.services.repo import RepoError, parse_repo_url
from ui.runner import can_run, request_run, start_run
from ui.views import show_run


def home_page() -> None:
    st.title("Sentinel RCA")
    st.markdown("**Paste a stack trace and a GitHub repo.**")
    st.write(
        "A team of agents finds the root cause, cites the code and the commit that introduced it, "
        "and drafts a fix for you to approve."
    )

    samples, own = st.tabs(["Try a sample incident", "Use your own repo"])
    with samples:
        demo_cases = [c for c in load_cases() if c.get("demo")]
        victim_url = demo_cases[0]["repo_url"]
        st.markdown(
            f"Every sample runs live against [{victim_url.removeprefix('https://github.com/')}]({victim_url}), "
            "a small Spring Boot service where the following sample bugs were introduced."
        )
        for column, case in zip(st.columns(len(demo_cases)), demo_cases, strict=True):
            with column.container(border=True):
                st.markdown(f"**{case['title']}**")
                st.caption(case["stack_trace"].splitlines()[0].split(":")[0])
                with st.expander("Stack trace"):
                    st.code(case["stack_trace"].replace("\t", "    "), language="text")
                if st.button("Investigate", key=case["id"], disabled=bool(can_run())):
                    request_run(
                        "samples",
                        repo_url=case["repo_url"],
                        raw_trace=case["stack_trace"],
                        base_commit=case["base_commit"],
                        case=case,
                    )
        if get_settings().example_pr_url:
            st.markdown(f"[See an example PR Sentinel opened]({get_settings().example_pr_url})")
        run_area("samples")
    with own:
        repo_url = st.text_input("Public GitHub repo", placeholder="https://github.com/owner/repo")
        raw_trace = st.text_area("Stack trace (Java or Python)", height=200)
        with st.expander("Use your own API keys (optional)"):
            st.caption(
                "For when the demo's free quota runs out. Keys are kept only in this browser session's memory "
                "for your runs: never stored, logged or shown. Free keys: console.groq.com, aistudio.google.com."
            )
            own_keys = {
                "groq": st.text_input("Groq API key", type="password", autocomplete="off"),
                "gemini": st.text_input("Gemini API key", type="password", autocomplete="off"),
            }
        has_keys = any(own_keys.values())
        if st.button("Run", disabled=bool(can_run(own_keys=has_keys)) or not (repo_url and raw_trace)):
            try:
                parse_repo_url(repo_url)
                request_run("own", repo_url=repo_url, raw_trace=raw_trace, base_commit=None, case=None, keys=own_keys)
            except RepoError as exc:
                st.error(str(exc))
        run_area("own")

    if reason := can_run():
        st.info(reason)


def run_area(origin: str) -> None:
    """Start a pending run from this tab, and show the latest run only in the tab that started it."""
    pending = st.session_state.get("pending")
    if pending and pending["origin"] == origin:
        del st.session_state["pending"]
        start_run(**pending)
    if st.session_state.get("origin") == origin and "thread_id" in st.session_state:
        st.divider()
        show_run()
