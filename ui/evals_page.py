"""Evals page: every incident with its known answer, and what each configuration found."""

import json
from pathlib import Path

import streamlit as st

from evals.run_eval import load_cases, solved

RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results"
CONFIG_NAMES = {
    "graph_routed": "Sentinel",
    "baseline": "Single agent",
}
DIFFICULTIES = {
    "easy": "Easy: the bug is close to where the trace points",
    "hard": "Hard: the trace points somewhere else, or git blame misleads",
    "negative": "Negative: it should not be fooled",
}


def latest_results() -> dict[str, dict]:
    """Per configuration, its newest result for each case (file names start with the date)."""
    latest: dict[str, dict] = {}
    for path in sorted(RESULTS_DIR.glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        latest.setdefault(result["config"], {}).update({case["id"]: case for case in result["cases"]})
    return {config: latest[config] for config in CONFIG_NAMES if config in latest}


def expected(case: dict) -> str:
    if not case["gold_files"]:
        return "Nothing in this repo is to blame."
    lines = "; ".join(f"{file}:{start}-{end}" for file, ranges in case["gold_lines"].items() for start, end in ranges)
    return f"{lines}, introduced by `{case['introduced_by']}`"


def verdict(result: dict | None) -> str:
    if result is None:
        return "not run"
    if result["crash"]:
        return "crashed"
    return "solved" if solved(result) else "missed"


st.title("Evals")
st.write(
    "Each incident below has a known answer: the file, lines and commit that caused it. "
    "Sentinel and a single agent with the same tools investigate every incident, and each answer is checked "
    "against the known one."
)
with st.expander("What is compared"):
    st.markdown(
        "- **Sentinel**: the agent graph, each role on its own models\n"
        "- **Single agent**: one agent with all six tools and a single prompt\n\n"
        "**Solved** means the top suspect is the right file, overlaps the right lines and names the right commit. "
        "For incidents where nothing in the repo is to blame, it means no code was blamed."
    )

results = latest_results()
cases = load_cases()

if results:
    st.subheader("Summary")
    rows = []
    for config, by_case in results.items():
        runs = list(by_case.values())
        rows.append(
            {
                "": CONFIG_NAMES[config],
                "Solved": f"{sum(solved(r) for r in runs)} / {len(runs)}",
                "Avg cost": f"${sum(r['cost_usd'] for r in runs) / len(runs):.4f}",
                "Avg time": f"{sum(r['latency_s'] for r in runs) / len(runs):.0f} s",
                "Avg tool calls": f"{sum(r['tool_calls'] for r in runs) / len(runs):.1f}",
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)
    st.caption("Free-tier models, so time includes waiting on rate limits and fallbacks.")
else:
    st.info("No results yet.")

st.subheader("Incidents")
for difficulty, heading in DIFFICULTIES.items():
    group = [c for c in cases if c["difficulty"] == difficulty]
    if not group:
        continue
    st.markdown(f"#### {heading}")
    for case in group:
        runs = {config: by_case.get(case["id"]) for config, by_case in results.items()}
        label = case["title"]
        if runs:
            label += "  ·  " + "  ·  ".join(f"{CONFIG_NAMES[c]}: {verdict(r)}" for c, r in runs.items())
        with st.expander(label):
            st.write(case["tests"])
            st.code(case["stack_trace"].replace("\t", "    "), language="text")
            st.markdown(f"**Known answer:** {expected(case)}")
            ran = {config: r for config, r in runs.items() if r}
            if not ran:
                continue
            st.dataframe(
                [
                    {
                        "": CONFIG_NAMES[config],
                        "Result": verdict(r),
                        "Blamed": r["top_suspect"] or "nothing",
                        "Commit": (r["introduced_by"] or "–")[:7],
                        "Cost": f"${r['cost_usd']:.4f}",
                        "Time": f"{r['latency_s']:.0f} s",
                        "Tool calls": r["tool_calls"],
                    }
                    for config, r in ran.items()
                ],
                width="stretch",
                hide_index=True,
            )
            for config, r in ran.items():
                if r.get("root_cause"):
                    st.markdown(f"**{CONFIG_NAMES[config]} said:** {r['root_cause']}")
                if r.get("review_problems"):
                    st.caption("Reviewer objected: " + "; ".join(r["review_problems"]))
                if r["error"]:
                    st.caption(f"{CONFIG_NAMES[config]} error: {r['error']}")
