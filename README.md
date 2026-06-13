# B2B Lead Generation AI Agent — India Hardware & Aluminium Trade

> **New here? → [QUICKSTART.md](QUICKSTART.md)** — clone, add your own API keys, and run it locally in a few minutes.

An autonomous, local Python pipeline that **discovers → scrapes → enriches →
detects buying signals → scores → stores → and surfaces** high-quality B2B leads
in the India hardware and aluminium materials trade, then renders them in a
single-file interactive dashboard. Built to the PRD v1.0 (Claude Code handoff).

Everything runs locally. No cloud infrastructure. A full run (100 candidate
companies) targets completion within 30 minutes on a standard laptop.

## Pipeline

```
Discovery → Scraping → Enrichment → Signal Detection → Scoring → Data Layer → Dashboard
```

| Module | File | Responsibility |
|---|---|---|
| Discovery | `discovery.py`, `providers.py` | Query the search provider, dedup by domain, daily/per-run query caps |
| Scraping | `scraper.py` | Playwright + BeautifulSoup contact extraction, robots.txt, anti-bot |
| Enrichment | `enricher.py` | Apollo.io org enrich + people search + bulk match |
| Signals | `signals.py` | Website-keyword + news/expansion buying signals (optional LLM) |
| Scoring | `scorer.py` | Deterministic 0–100 lead score + simple 0–7 qualification score (pure stdlib) |
| Data Layer | `storage.py`, `models.py` | Pydantic validation, SQLite/JSON, dedup + merge |
| Dashboard | `dashboard.html`, `serve.py` | Single-file UI with filters, sort, expand, CSV export |
| Orchestrator | `main.py` | Runs the full pipeline |
| UI (optional) | `app.py` | Streamlit control panel: configure keys, run, view results |

## Requirements

- **Python 3.11+** (per PRD). Pinned dependencies are in `requirements.txt`.
- A `.env` file with your API keys (copy from `.env.example`).

## Easiest start — Streamlit UI

For a no-terminal experience, use the bundled Streamlit control panel: paste API
keys into a form, click **Run pipeline**, watch live logs, and browse results in
the embedded dashboard.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-app.txt
playwright install chromium
streamlit run app.py                 # opens http://localhost:8501
```

It runs locally (Playwright can't run on Streamlit's hosted cloud, and you'd be
sharing keys/quota — so each user runs their own). The CLI below remains fully
usable and is better for unattended/scheduled runs.

## Setup (CLI)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # one-time browser download
cp .env.example .env                 # then fill in your keys
```

Minimum required keys: `APOLLO_API_KEY`, plus the keys for your chosen
`SEARCH_PROVIDER` (default `google_cse` needs `GOOGLE_CSE_API_KEY` +
`GOOGLE_CSE_ID`). See `.env.example` and Section 9 of the PRD for all variables.

**Data sources (who supplies what):**
- **Apollo.io** (`APOLLO_API_KEY`, **required**) — decision-maker contacts + firmographics (headcount, revenue).
- **Search provider** (Google CSE / SerpAPI / Brave, **required**) — company discovery, and news-signal fallback.
- **Google Places API (New)** (`GOOGLE_PLACES_API_KEY`, recommended) — verified location (city/state/address/geo), business phone, website, type & rating. Skipped if unset.
- **NewsAPI** (`NEWSAPI_API_KEY`, recommended) — news/expansion buying signals with exact publish dates + article URLs. Falls back to the search provider if unset.
- **GST verification** (`GST_API_KEY`, optional) — confirms a company's GSTIN (extracted from its own site) is active; powers the "GST verified" qualification point.
- **IndiaMART / JustDial** — used **only** as Google `site:` search targets (never scraped directly); the verified tag + GSTIN are harvested from search snippets.

**Required to run:** `APOLLO_API_KEY` + your chosen search provider's keys. Everything else is optional — the agent runs without it and simply leaves those fields empty (and the corresponding qualification points unscored).

## Run

```bash
python main.py        # full pipeline; writes leads to OUTPUT_DIR (default ./leads)
python serve.py       # view the dashboard at http://localhost:8000
```

To preview the dashboard with bundled sample data (no API keys needed):

```bash
mkdir -p leads && cp leads.sample.json leads/leads.json
python serve.py       # open http://localhost:8000
```

## Lead ranking (qualification score)

Leads aren't a flat list — each gets a simple **0–7 qualification score** so your
team can work top-down (this is the dashboard's default sort):

| Signal | Points |
|---|---|
| Has a website | +1 |
| Listed on IndiaMART with a verified tag | +1 |
| Recent news or activity (signal within 90 days) | +2 |
| GST verified and active | +2 |
| Has a direct mobile / WhatsApp number | +1 |

Each card shows a **Fit N/7** chip; expanding a card shows the per-criterion
scorecard. The original 0–100 lead score is still available as a sort option.

## WhatsApp outreach (click-to-chat)

Each lead card (and each contact in the expand panel) has a **WhatsApp** button.
Clicking it opens `wa.me/<number>` with a short, personalized message
**pre-filled** — you review and send it from your own WhatsApp. This is
human-in-the-loop by design: it does **not** auto-send or bulk-blast, which keeps
it within WhatsApp's ToS and avoids India's TRAI DND issues for unsolicited
messaging (fully automated WhatsApp/phone outreach is out of scope per PRD
Section 10, slated for v3 via the official WhatsApp Business API + opt-in).

To personalize the drafts, edit the `OUTREACH` block near the top of the
`<script>` in `dashboard.html`:

```js
const OUTREACH = {
  senderName: "Aarush from Acme Aluminium",   // blank = no sign-off
  product: "aluminium & hardware supply",     // what you sell
};
```

Indian numbers are auto-normalized to `wa.me` format; leads without a phone show
a disabled button.

## Output

All artifacts land in `OUTPUT_DIR` (default `./leads`, gitignored):
`leads.db` (or `leads.json`), `agent.log`, `query_counter.json`, and the
dashboard data export `leads.json`.

## Tests

```bash
pytest                # full suite (requires deps installed)
```

The scoring engine, shared utilities, and Google Places parser are pure-stdlib
and unit-tested in `tests/test_scorer.py`, `tests/test_utils.py`, and
`tests/test_places.py` (23 tests).

## Compliance (Section 8)

- **robots.txt** is checked before scraping any domain; disallowed domains are skipped.
- **IndiaMart / TradeIndia / JustDial** are never scraped directly — used only as Google search targets.
- **PII** (emails, phones) is masked in all log output; stored data is unmasked for use.
- A **3-second** minimum delay between requests to the same domain is enforced and not configurable lower.
- All data stays local; only the configured enrichment APIs (Apollo, Google Places, NewsAPI, GST, search provider) receive requests.

## Notes / spec interpretations

- **Contact seniority enum** uses the superset of all values referenced in the
  PRD (`c_suite, owner, founder, head, director, manager, other, unknown`) rather
  than the 5-value list in Table 9, so Apollo `owner/founder/head` contacts are
  not dropped at validation and seniority sorting/scoring stay faithful. See the
  docstring in `models.py`.
- **Google CSE** deprecates 2027-01-01; the agent warns after 2026-12-01 and can
  fall back to SerpAPI/Brave via `.env` with no code changes (Section 8.5).

## Out of scope (v1)

CRM integration, email/outreach automation, scheduled runs, real-time feeds,
LinkedIn scraping, international geographies, multi-user/auth, cloud deployment.
See Section 10 of the PRD.
