"""Run the eval dataset against one configuration and write results + a markdown table.

    python -m evals.run_eval --config graph_routed [--difficulty hard] [--ids a,b]

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
from src.llm.run_context import track_usage

EVALS_DIR = Path(__file__).parent
DATASET = EVALS_DIR / "dataset.json"
RESULTS_DIR = EVALS_DIR / "results"
CONFIGS = ("baseline", "graph_routed")
TABLE_COLUMNS = {
    "cases": "cases",
    "solved": "solved_pct",
    "top-1 file": "top1_file_pct",
    "top-3 file": "top3_file_pct",
    "lines": "line_overlap_pct",
    "commit": "commit_hit_pct",
    "grounded": "grounded_pct",
    "crash": "crash_pct",
    "cost $": "avg_cost_usd",
    "latency s": "avg_latency_s",
    "tool calls": "avg_tool_calls",
}


def load_cases(group: str | None = None, ids: list[str] | None = None) -> list[dict]:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if (not group or c["group"] == group) and (not ids or c["id"] in ids)]


def score(case: dict, report) -> dict:
    """Compare a report with the case's gold answer. Also used by the UI's Expected-vs-Sentinel table."""
    files = [s.file for s in report.suspects] if report else []
    if not case["gold_files"]:  # nothing in the repo is to blame, so the right answer names no suspects
        return {"top1_file": not files, "top3_file": not files, "line_overlap": None, "commit_hit": None}
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


def solved(result: dict) -> bool:
    """Right file, and right lines and commit where the case has them. A crash is never solved."""
    if result["crash"] or not result["top1_file"]:
        return False
    return result["line_overlap"] is not False and result["commit_hit"] is not False


def run_case(case: dict, config: str) -> dict:
    """Run one case; any exception counts as a crash instead of stopping the whole eval."""
    started = time.perf_counter()
    report, review, error = None, None, None
    with track_usage() as usage:
        try:
            report, review = run_config(case, config)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
    top = report.suspects[0] if report and report.suspects else None
    result = {
        "id": case["id"],
        "group": case["group"],
        "difficulty": case["difficulty"],
        **score(case, report),
        "grounded": review.grounded if review else None,
        "top_suspect": f"{top.file}:{top.start_line}-{top.end_line}" if top else None,
        "introduced_by": report.introduced_by if report else None,
        "root_cause": report.root_cause if report else None,
        "review_problems": review.problems if review else [],
        "cost_usd": round(usage.cost_usd, 5),
        "latency_s": round(time.perf_counter() - started, 1),
        "models": sorted(usage.models),
        "llm_calls": usage.llm_calls,
        "tool_calls": usage.tool_calls,
        "crash": error is not None,
        "error": error,
    }
    return result | {"solved": solved(result)}


def run_config(case: dict, config: str):
    """Returns (report, review or None)."""
    if config == "baseline":
        return run_baseline(case["repo_url"], case["stack_trace"], case["base_commit"]), None
    graph = build_graph()
    thread = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = graph.invoke(new_run(case["repo_url"], case["stack_trace"], case["base_commit"]), thread)
    if is_paused(graph, thread):
        state = graph.invoke(Command(resume={"action": "approve"}), thread)
    if state["error"]:
        raise RuntimeError(state["error"])
    return state["report"], state["review"]


def summarise(results: list[dict]) -> dict:
    """Metrics overall and per difficulty, so easy cases never hide weak hard ones."""
    levels = ("easy", "hard", "negative")
    groups = {"overall": results} | {level: [r for r in results if r["difficulty"] == level] for level in levels}
    return {name: group_metrics(rows) for name, rows in groups.items() if rows}


def group_metrics(rows: list[dict]) -> dict:
    def pct(key: str) -> float | None:
        values = [r[key] for r in rows if r[key] is not None]
        return round(100 * sum(values) / len(values), 1) if values else None

    return {
        "cases": len(rows),
        "solved_pct": pct("solved"),
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
    header = ["config", "set", *TABLE_COLUMNS]
    rows = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for group, metrics in summary.items():
        cells = [config, group, *(metrics[key] for key in TABLE_COLUMNS.values())]
        rows.append("| " + " | ".join("–" if cell is None else str(cell) for cell in cells) + " |")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", choices=CONFIGS, required=True)
    parser.add_argument("--group", help="victim or public (default: both)")
    parser.add_argument("--ids", help="comma-separated case ids")
    parser.add_argument("--difficulty", help="easy, hard or negative (default: all)")
    args = parser.parse_args()

    # Settings are read lazily on first use, so these still take effect.
    os.environ["DRY_RUN"] = "true"
    logging.basicConfig(level=logging.WARNING)

    cases = load_cases(args.group, args.ids.split(",") if args.ids else None)
    cases = [c for c in cases if not args.difficulty or c["difficulty"] == args.difficulty]
    results = []
    for case in cases:
        result = run_case(case, args.config)
        print(f"{case['id']}: solved={result['solved']} crash={result['crash']}")
        results.append(result)

    stem = RESULTS_DIR / f"{date.today().isoformat()}_{args.config}"
    if stem.with_suffix(".json").exists():  # a second run on the same day adds to that day's results
        earlier = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))["cases"]
        rerun = {r["id"] for r in results}
        results = [r for r in earlier if r["id"] not in rerun] + results
    summary = summarise(results)
    table = markdown(args.config, summary)
    result = {"config": args.config, "date": date.today().isoformat(), "summary": summary, "cases": results}
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)


if __name__ == "__main__":
    main()
