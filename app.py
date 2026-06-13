"""Streamlit control panel for the B2B Lead Generation Agent.

    streamlit run app.py

A friendly front-end over the existing CLI: paste your API keys, run the
pipeline with a button, watch live logs, and browse results in the embedded
dashboard. The CLI (`python main.py`) and the static dashboard remain fully
usable on their own — this is just an easier on-ramp for non-technical users.

Only depends on streamlit + the standard library; the pipeline itself runs as a
subprocess so Streamlit's rerun model never fights Playwright's event loop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
SAMPLE = ROOT / "leads.sample.json"

st.set_page_config(page_title="B2B Lead Gen Agent", page_icon="🧲", layout="wide")


# --------------------------------------------------------------------------- #
# .env + data helpers
# --------------------------------------------------------------------------- #
def read_env() -> dict:
    data: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data


def write_env(values: dict) -> None:
    lines = ["# Written by app.py (Streamlit control panel) — gitignored, never commit.", ""]
    for key, val in values.items():
        if val not in (None, ""):
            lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def _output_dir() -> Path:
    raw = read_env().get("OUTPUT_DIR") or "./leads"
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / raw)


def load_leads() -> list:
    leads_file = _output_dir() / "leads.json"
    if leads_file.exists():
        try:
            data = json.loads(leads_file.read_text())
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    return []


def int_env(env: dict, key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.title("🧲 B2B Lead Generation Agent")
st.caption(
    "India hardware & aluminium trade · Discovery → Scraping → Enrichment → "
    "Signals → Scoring → Dashboard"
)

env = read_env()
configured = [k for k in ("APOLLO_API_KEY", "GOOGLE_CSE_API_KEY", "SERPAPI_API_KEY",
                          "BRAVE_API_KEY", "GOOGLE_PLACES_API_KEY", "NEWSAPI_API_KEY",
                          "GST_API_KEY") if env.get(k)]
st.sidebar.header("Status")
st.sidebar.write("**Keys set:**", ", ".join(configured) if configured else "none yet")
st.sidebar.write("**Leads stored:**", len(load_leads()))
st.sidebar.caption("Tip: the CLI still works — `python main.py` then `python serve.py`.")

tab_cfg, tab_run, tab_dash = st.tabs(["⚙️ Configure", "▶️ Run", "📊 Dashboard"])


# --------------------------------------------------------------------------- #
# Configure
# --------------------------------------------------------------------------- #
with tab_cfg:
    st.subheader("API keys & settings")
    st.caption("Saved to a local `.env` file (gitignored). Only Apollo + one "
               "search provider are required; the rest are optional enrichers.")
    with st.form("config"):
        st.markdown("**Required**")
        apollo = st.text_input("APOLLO_API_KEY", value=env.get("APOLLO_API_KEY", ""), type="password")
        provider = st.selectbox(
            "SEARCH_PROVIDER", ["google_cse", "serpapi", "brave"],
            index=["google_cse", "serpapi", "brave"].index(env.get("SEARCH_PROVIDER", "google_cse")),
        )
        st.caption("Fill the keys for the provider you selected above.")
        gcse_key = st.text_input("GOOGLE_CSE_API_KEY", value=env.get("GOOGLE_CSE_API_KEY", ""), type="password")
        gcse_id = st.text_input("GOOGLE_CSE_ID", value=env.get("GOOGLE_CSE_ID", ""))
        serp = st.text_input("SERPAPI_API_KEY", value=env.get("SERPAPI_API_KEY", ""), type="password")
        brave = st.text_input("BRAVE_API_KEY", value=env.get("BRAVE_API_KEY", ""), type="password")

        st.markdown("**Optional enrichment**")
        places = st.text_input("GOOGLE_PLACES_API_KEY", value=env.get("GOOGLE_PLACES_API_KEY", ""), type="password",
                               help="Verified location, phone, rating, Maps link.")
        newsapi = st.text_input("NEWSAPI_API_KEY", value=env.get("NEWSAPI_API_KEY", ""), type="password",
                                help="News/expansion buying signals with exact dates.")
        gst = st.text_input("GST_API_KEY", value=env.get("GST_API_KEY", ""), type="password",
                            help="Confirms a company's GSTIN is active.")

        st.markdown("**Run settings**")
        c1, c2 = st.columns(2)
        qpr = c1.number_input("QUERIES_PER_RUN", 1, 100, int_env(env, "QUERIES_PER_RUN", 20))
        mcb = c2.number_input("MAX_CONCURRENT_BROWSERS", 1, 10, int_env(env, "MAX_CONCURRENT_BROWSERS", 3))
        outdir = c1.text_input("OUTPUT_DIR", value=env.get("OUTPUT_DIR", "./leads"))
        backend = c2.selectbox("STORAGE_BACKEND", ["sqlite", "json"],
                               index=["sqlite", "json"].index(env.get("STORAGE_BACKEND", "sqlite")))

        if st.form_submit_button("💾 Save configuration", type="primary"):
            write_env({
                "APOLLO_API_KEY": apollo, "SEARCH_PROVIDER": provider,
                "GOOGLE_CSE_API_KEY": gcse_key, "GOOGLE_CSE_ID": gcse_id,
                "SERPAPI_API_KEY": serp, "BRAVE_API_KEY": brave,
                "GOOGLE_PLACES_API_KEY": places, "NEWSAPI_API_KEY": newsapi, "GST_API_KEY": gst,
                "QUERIES_PER_RUN": str(qpr), "MAX_CONCURRENT_BROWSERS": str(mcb),
                "OUTPUT_DIR": outdir, "STORAGE_BACKEND": backend,
            })
            st.success("Saved to .env ✅  Head to the Run tab.")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
with tab_run:
    st.subheader("Run the pipeline")
    st.caption("Runs `python main.py`. A full run (up to 100 companies) can take "
               "several minutes; live logs appear below.")
    col1, col2 = st.columns(2)
    run_clicked = col1.button("▶️ Run pipeline", type="primary", use_container_width=True)
    sample_clicked = col2.button("Load sample data (no keys)", use_container_width=True)

    if sample_clicked:
        od = _output_dir()
        od.mkdir(parents=True, exist_ok=True)
        (od / "leads.json").write_text(SAMPLE.read_text())
        st.success("Sample data loaded — open the Dashboard tab to preview the UI.")

    if run_clicked:
        if not read_env().get("APOLLO_API_KEY"):
            st.error("APOLLO_API_KEY is missing — set it in the Configure tab first.")
        else:
            logs: list[str] = []
            log_box = st.empty()
            with st.status("Running pipeline…", expanded=True) as status:
                proc = subprocess.Popen(
                    [sys.executable, "main.py"], cwd=str(ROOT),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, env={**os.environ},
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    logs.append(line.rstrip())
                    log_box.code("\n".join(logs[-400:]))
                proc.wait()
                if proc.returncode == 0:
                    status.update(label="Run complete ✅", state="complete")
                else:
                    status.update(label=f"Exited with code {proc.returncode}", state="error")
            n = len(load_leads())
            (st.success if proc.returncode == 0 else st.warning)(
                f"{n} leads now in store. Open the Dashboard tab to explore."
            )


# --------------------------------------------------------------------------- #
# Dashboard (embed the existing dashboard.html with injected data)
# --------------------------------------------------------------------------- #
with tab_dash:
    leads = load_leads()
    if not leads:
        st.info("No data yet. Run the pipeline or load sample data in the Run tab.")
    else:
        total = len(leads)
        with_contacts = sum(1 for l in leads if l.get("contacts"))
        with_signals = sum(1 for l in leads if l.get("buying_signals"))
        avg_fit = round(sum(l.get("qual_score", 0) for l in leads) / total, 1)
        m = st.columns(4)
        m[0].metric("Leads", total)
        m[1].metric("With contacts", with_contacts)
        m[2].metric("With buying signals", with_signals)
        m[3].metric("Avg Fit /7", avg_fit)

        html = (ROOT / "dashboard.html").read_text()
        # Inject data so the embedded dashboard renders without fetching.
        payload = json.dumps(leads).replace("</", "<\\/")
        html = html.replace("</head>", f"<script>window.__LEADS = {payload};</script></head>", 1)
        components.html(html, height=900, scrolling=True)
