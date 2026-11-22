"""CLI: python -m src.main --trace trace.txt --repo https://github.com/<owner>/<repo>"""

import argparse
import logging
import time
import uuid
from pathlib import Path

from langgraph.types import Command

from src.graph.graph_builder import build_graph, is_paused, new_run
from src.llm.client import track_usage
from src.models.report import Decision, RCAReport, ReviewResult


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace a stack trace to its root cause in a GitHub repo.")
    parser.add_argument("--trace", type=Path, required=True, help="file containing the stack trace")
    parser.add_argument("--repo", required=True, help="https://github.com/<owner>/<repo>")
    parser.add_argument("--ref", help="commit to analyse (default: the default branch)")
    parser.add_argument("--yes", action="store_true", help="approve without asking")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    started = time.perf_counter()
    with track_usage() as usage:
        state = graph.invoke(new_run(args.repo, args.trace.read_text(encoding="utf-8"), args.ref), config)
        if is_paused(graph, config):
            print(format_report(state["report"], state["review"]))
            decision = ask_decision(args.yes)
            state = graph.invoke(Command(resume=decision.model_dump()), config)

    if state["error"]:
        print(f"\nError: {state['error']}")
    elif state["decision"] is None:
        print(format_report(state["report"], state["review"]))
    if state["pr_url"]:
        print(f"\nDraft PR: {state['pr_url']}")
    elif state["decision"] and state["decision"].action == "approve":
        print("\nApproved. No PR opened (dry run, or no patch).")
    print(
        f"\n{usage.llm_calls} LLM calls, {usage.tool_calls} tool calls, {usage.tokens} tokens, "
        f"${usage.cost_usd:.4f}, {time.perf_counter() - started:.0f}s"
    )


def ask_decision(auto_approve: bool) -> Decision:
    if auto_approve:
        return Decision(action="approve")
    answer = input("\nApprove this report and open a draft PR? [y/N] ").strip().lower()
    note = input("Note (optional): ").strip() or None
    return Decision(action="approve" if answer == "y" else "reject", note=note)


def format_report(report: RCAReport, review: ReviewResult | None) -> str:
    lines = [f"\n=== {report.title} ===", report.root_cause, "", "Suspects:"]
    lines += [f"  {s.file}:{s.start_line}-{s.end_line} ({s.confidence:.0%}) {s.reason}" for s in report.suspects]
    lines += [f"Introduced by: {report.introduced_by or 'unknown'}", f"Confidence: {report.confidence:.0%}"]
    if report.suggested_patch:
        lines += ["", "Suggested patch:", report.suggested_patch]
    if review:
        lines += ["", f"Reviewer: {'grounded' if review.grounded else 'NOT grounded'}"]
        lines += [f"  - {problem}" for problem in review.problems]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
