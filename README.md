# B2B Lead Generation AI Agent — India Hardware & Aluminium Trade

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
| Scoring | `scorer.py` | Deterministic 0–100 score (pure stdlib) |
| Data Layer | `storage.py`, `models.py` | Pydantic validation, SQLite/JSON, dedup + merge |
| Dashboard | `dashboard.html`, `serve.py` | Single-file UI with filters, sort, expand, CSV export |
| Orchestrator | `main.py` | Runs the full pipeline |

## Requirements

- **Python 3.11+** (per PRD). Pinned dependencies are in `requirements.txt`.
- A `.env` file with your API keys (copy from `.env.example`).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # one-time browser download
cp .env.example .env                 # then fill in your keys
```

Minimum required keys: `APOLLO_API_KEY`, plus the keys for your chosen
`SEARCH_PROVIDER` (default `google_cse` needs `GOOGLE_CSE_API_KEY` +
`GOOGLE_CSE_ID`). See `.env.example` and Section 9 of the PRD for all variables.

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

The scoring engine and shared utilities are pure-stdlib and unit-tested in
`tests/test_scorer.py` and `tests/test_utils.py`.

## Compliance (Section 8)

- **robots.txt** is checked before scraping any domain; disallowed domains are skipped.
- **IndiaMart / TradeIndia** are never scraped directly — used only as search targets.
- **PII** (emails, phones) is masked in all log output; stored data is unmasked for use.
- A **3-second** minimum delay between requests to the same domain is enforced and not configurable lower.
- All data stays local; only Apollo.io and the configured search provider receive requests.

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
