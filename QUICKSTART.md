# Quickstart — run your own copy

This tool finds & ranks B2B leads in the India hardware/aluminium trade. It runs
**entirely on your own machine** — your data and API keys stay local, nothing is
uploaded or shared.

## 1. Prerequisites
- **Python 3.11+** (`python --version`)
- **git**

## 2. Clone & install
```bash
git clone https://github.com/sshaurya-code/b2b-lead-gen-agent
cd b2b-lead-gen-agent
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-app.txt
playwright install chromium
```

## 3. Get API keys
You need **two** to start; the rest are optional (they add more data):

| Service | Needed? | Get a key |
|---|---|---|
| **Apollo.io** | **Required** (contacts) | https://app.apollo.io → Settings → API |
| **A search provider** | **Required** (discovery) — pick one: | |
| · Google CSE | | key: https://console.cloud.google.com (enable "Custom Search JSON API") · engine id: https://programmablesearchengine.google.com |
| · SerpAPI | | https://serpapi.com/dashboard |
| · Brave Search | | https://api.search.brave.com/app/keys |
| Google Places (New) | Optional (verified location/phone/rating) | https://console.cloud.google.com (enable "Places API (New)") |
| NewsAPI | Optional (news/expansion signals) | https://newsapi.org |
| GST verification | Optional (GST-active check) | any GST-verify API provider |

> These are **paid third-party services** billed by those providers — not by this
> tool. Most have free tiers to start.

## 4. Run it

**Easiest — the UI:**
```bash
streamlit run app.py        # opens http://localhost:8501
```
Paste your keys in the **Configure** tab → **Run** tab → **Run pipeline** → view
results in the **Dashboard** tab. (Click **Load sample data** first if you just
want to see the interface before getting keys.)

**Or the command line:**
```bash
cp .env.example .env         # paste your keys into this file
python main.py               # generates leads into ./leads
python serve.py              # view dashboard at http://localhost:8000
```

## 5. Use the leads
- Sort is by **Fit score (0–7)** — work top-down.
- Click a lead to expand contacts, buying signals (with sources), and Google
  Places business details.
- The green **WhatsApp** button opens a pre-filled message you review and send
  from your own WhatsApp.
- **Export CSV** exports whatever is currently filtered.

## Notes
- Everything is local; your leads (`./leads/`) and `.env` are gitignored — they
  never get committed or shared.
- A full run (~100 companies) can take several minutes.
- Don't publicly publish exported contact data — it's covered by Apollo's ToS and
  India's DPDP Act.
