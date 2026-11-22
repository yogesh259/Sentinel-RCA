"""Run the eval dataset against one configuration and write results + a markdown table.

    python -m evals.run_eval --config graph_routed [--group victim] [--ids a,b] [--model <litellm model>]

Human review is auto-approved and DRY_RUN is forced, so evals never open PRs.
"""

import argparse
import json
import logging
import os
import time
import uuid
from datetime import date
from pathlib import Path

from langgraph.types import Command

from evals.baseline_agent import run_baseline
from src.graph.graph_builder import build_graph, is_paused, new_run
from src.llm.client import track_usage

EVALS_DIR = Path(__file__).parent
DATASET = EVALS_DIR / "dataset.json"
RESULTS_DIR = EVALS_DIR / "results"
CONFIGS = ("baseline", "graph_single_model", "graph_routed", "graph_no_reviewer")


def load_cases(group: str | None = None, ids: list[str] | None = None) -> list[dict]:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if (not group or c["group"] == group) and (not ids or c["id"] in ids)]


def score(case: dict, report) -> dict:
    """Compare a report with the case's gold answer. Shared with the UI's Expected-vs-Sentinel box."""
    files = [s.file for s in report.suspects] if report else []
    gold_lines = case["gold_lines"]
    line_hit = bool(report) and any(
        s.start_line <= end and start <= s.end_line
        for s in report.suspects
        for start, end in gold_lines.get(s.file, [])
    )
    commit_hit = None
    if case.get("introduced_by"):
        found = (report.introduced_by or "") if report else ""
        commit_hit = len(found) >= 7 and (
            found.startswith(case["introduced_by"]) or case["introduced_by"].startswith(found)
        )
    return {
        "top1_file": bool(files) and files[0] in case["gold_files"],
        "top3_file": any(f in case["gold_files"] for f in files[:3]),
        "line_overlap": line_hit,
        "commit_hit": commit_hit,
    }


def run_case(case: dict, config: str) -> dict:
    """Run one case; any exception counts as a crash instead of stopping the whole eval."""
    started = time.perf_counter()
    report, grounded, error = None, None, None
    with track_usage() as usage:
        try:
            report, grounded = run_config(case, config)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
    return {
        "id": case["id"],
        "group": case["group"],
        **score(case, report),
        "grounded": grounded,
        "top_suspect": f"{report.suspects[0].file}:{report.suspects[0].start_line}"
        if report and report.suspects
        else None,
        "introduced_by": report.introduced_by if report else None,
        "cost_usd": round(usage.cost_usd, 5),
        "latency_s": round(time.perf_counter() - started, 1),
        "llm_calls": usage.llm_calls,
        "tool_calls": usage.tool_calls,
        "crash": error is not None,
        "error": error,
    }


def run_config(case: dict, config: str):
    """Returns (report, reviewer grounded or None)."""
    if config == "baseline":
        return run_baseline(case["repo_url"], case["stack_trace"], case["base_commit"]), None
    graph = build_graph(with_reviewer=config != "graph_no_reviewer")
    thread = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = graph.invoke(new_run(case["repo_url"], case["stack_trace"], case["base_commit"]), thread)
    if is_paused(graph, thread):
        state = graph.invoke(Command(resume={"action": "approve"}), thread)
    if state["error"]:
        raise RuntimeError(state["error"])
    return state["report"], state["review"].grounded if state["review"] else None


def summarise(results: list[dict]) -> dict:
    """Metrics per group and overall, so easy victim cases never hide weak public ones."""
    groups = {"overall": results} | {g: [r for r in results if r["group"] == g] for g in {r["group"] for r in results}}
    return {name: group_metrics(rows) for name, rows in groups.items() if rows}


def group_metrics(rows: list[dict]) -> dict:
    def pct(key: str) -> float | None:
        values = [r[key] for r in rows if r[key] is not None]
        return round(100 * sum(values) / len(values), 1) if values else None

    return {
        "cases": len(rows),
        "top1_file_pct": pct("top1_file"),
        "top3_file_pct": pct("top3_file"),
        "line_overlap_pct": pct("line_overlap"),
        "commit_hit_pct": pct("commit_hit"),
        "grounded_pct": pct("grounded"),
        "crash_pct": pct("crash"),
        "avg_cost_usd": round(sum(r["cost_usd"] for r in rows) / len(rows), 5),
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 1),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / len(rows), 1),
    }


def markdown(config: str, summary: dict) -> str:
    header = (
        "| config | group | cases | top-1 file | top-3 file | lines | commit | grounded | crash | cost $ | latency s |"
    )
    rows = [header, "|" + "---|" * 11]
    for group, m in summary.items():
        cells = [
            config,
            group,
            m["cases"],
            m["top1_file_pct"],
            m["top3_file_pct"],
            m["line_overlap_pct"],
            m["commit_hit_pct"],
            m["grounded_pct"],
            m["crash_pct"],
            m["avg_cost_usd"],
            m["avg_latency_s"],
        ]
        rows.append("| " + " | ".join("–" if c is None else str(c) for c in cells) + " |")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", choices=CONFIGS, required=True)
    parser.add_argument("--group", help="victim or public (default: both)")
    parser.add_argument("--ids", help="comma-separated case ids")
    parser.add_argument("--model", default="gemini/gemini-2.5-pro", help="model for graph_single_model")
    args = parser.parse_args()

    # Settings are read on first use (get_settings), so env overrides set here still apply.
    os.environ["DRY_RUN"] = "true"
    if args.config == "graph_single_model":
        os.environ["MODEL_OVERRIDE"] = args.model
    logging.basicConfig(level=logging.WARNING)

    cases = load_cases(args.group, args.ids.split(",") if args.ids else None)
    results = []
    for case in cases:
        result = run_case(case, args.config)
        print(f"{case['id']}: top1={result['top1_file']} commit={result['commit_hit']} crash={result['crash']}")
        results.append(result)

    summary = summarise(results)
    table = markdown(args.config, summary)
    stem = RESULTS_DIR / f"{date.today().isoformat()}_{args.config}"
    stem.with_suffix(".json").write_text(
        json.dumps(
            {"config": args.config, "date": date.today().isoformat(), "summary": summary, "cases": results}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    stem.with_suffix(".md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)


if __name__ == "__main__":
    main()
