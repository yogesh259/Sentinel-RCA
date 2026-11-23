"""Streamlit entry point: `streamlit run ui/app.py`."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `streamlit run` only adds ui/ to the path

import streamlit as st

from ui.home import home_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

st.set_page_config(page_title="Sentinel RCA", layout="wide")
st.navigation(
    [
        st.Page(home_page, title="Home", default=True),
        st.Page("evals_page.py", title="Evals"),
    ]
).run()
