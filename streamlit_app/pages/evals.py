"""Evals page: the latest results per configuration, per group."""

import json
from pathlib import Path

import streamlit as st

RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"

st.set_page_config(page_title="Sentinel evals", page_icon="🛰️", layout="wide")
st.title("Evals")
st.write(
    "Each configuration runs the same dataset. `baseline` is one agent with every tool and one prompt; "
    "the graph configs are Sentinel itself. Victim cases are planted bugs with exact ground truth; "
    "public cases are real bugs from open-source repos."
)

latest: dict[str, dict] = {}
for path in sorted(RESULTS_DIR.glob("*.json")):  # file names start with the date, so later wins
    result = json.loads(path.read_text(encoding="utf-8"))
    latest[result["config"]] = result

if not latest:
    st.info("No results yet. Run `python -m evals.run_eval --config <name>`.")
else:
    rows = [
        {"config": config, "group": group, "date": result["date"], **metrics}
        for config, result in latest.items()
        for group, metrics in result["summary"].items()
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    with st.expander("Per-case results"):
        for config, result in latest.items():
            st.markdown(f"**{config}**")
            st.dataframe(result["cases"], use_container_width=True, hide_index=True)
