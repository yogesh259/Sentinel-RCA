"""Shows a run: findings, report, Expected-vs-Sentinel table, approval buttons and the PR result."""

from pathlib import Path

import streamlit as st
from langgraph.types import Command

from evals.run_eval import score
from src.agents.context import latest_findings
from src.graph.graph_builder import is_paused
from src.models.report import Decision
from src.services.pull_request import pr_body
from src.services.repo import parse_repo_url, run_git
from ui.runner import badge, graph, stream, thread_config


def github_links(values: dict) -> tuple[str, str]:
    """Base URLs for file and commit links at the exact commit that was analysed."""
    owner, name = parse_repo_url(values["repo_url"])
    sha = run_git(Path(values["repo_path"]), "rev-parse", "HEAD").strip()
    return f"https://github.com/{owner}/{name}/blob/{sha}", f"https://github.com/{owner}/{name}/commit"


def show_run() -> None:
    values = graph().get_state(thread_config()).values
    paused = is_paused(graph(), thread_config())
    if not st.session_state.pop("streamed_now", False):  # later reruns: the live box is gone, keep the steps
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
                column.markdown(f"{badge(source)} :gray[{finding.confidence:.0%} confident]\n\n{finding.summary}")

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
    mark = {True: "Yes", False: "No", None: "–"}
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
                "Expected": ", ".join(
                    f"{start}-{end}" for ranges in case["gold_lines"].values() for start, end in ranges
                )
                or "–",
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
        if column.button(action.title(), width="stretch", type="primary" if action == "approve" else "secondary"):
            stream(Command(resume=Decision(action=action, note=note or None).model_dump()))
            st.session_state.pop("streamed_now", None)  # the rerun below must show the steps again
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
