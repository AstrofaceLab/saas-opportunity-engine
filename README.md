# 🕵️ SaaS Opportunity Hunter Bot

> **Ethics**: Public data only · No PII collected · robots.txt respected · Rate-limited

Analyse 2–3 star reviews from G2, Capterra, and Trustpilot to surface actionable
**Feature Gaps** in competing SaaS products — scored with the **VibeScore** algorithm.

---

## Project Structure

```
saas_hunter/
├── config.py          # All settings, API keys, selectors
├── prompts.py         # LLM prompt templates (editable)
├── scraper.py         # Step A — The Harvester
├── analyzer.py        # Steps B & C — Intelligence Filter + Scoring Engine
├── main.py            # CLI entry point
├── requirements.txt
├── .env.example
├── data/              # Output CSV + JSON (auto-created)
├── state/             # Resume state JSON (auto-created)
└── logs/              # Rotating log files (auto-created)
```

---

## 1 · Installation

```bash
# Clone / copy the project folder
cd saas_hunter

# Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2 · Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```dotenv
# .env
# --- Required (pick one scraping engine) ---
SCRAPING_ENGINE=scrapingbee        # scrapingbee | apify | requests
SCRAPINGBEE_API_KEY=your_key_here
# APIFY_API_KEY=your_key_here      # alternative

# --- Required (pick one LLM provider) ---
LLM_PROVIDER=anthropic             # anthropic | openai
ANTHROPIC_API_KEY=your_key_here
# OPENAI_API_KEY=your_key_here     # alternative

# --- Optional Storage ---
AIRTABLE_API_KEY=
AIRTABLE_BASE_ID=
SUPABASE_URL=
SUPABASE_KEY=
```

Load the `.env` before running:

```bash
export $(cat .env | xargs)
# Or use python-dotenv — add `from dotenv import load_dotenv; load_dotenv()`
# at the top of main.py if preferred
```

---

## 3 · Running the Bot

### Basic — single URL

```bash
python main.py --url "https://www.g2.com/products/notion/reviews"
```

### Multiple URLs

```bash
python main.py \
  --url "https://www.g2.com/products/notion/reviews" \
  --url "https://www.capterra.com/p/59003/Notion/" \
  --url "https://www.trustpilot.com/review/notion.so"
```

### Force plain requests (no API key needed for testing)

```bash
python main.py \
  --url "https://www.g2.com/products/notion/reviews" \
  --engine requests \
  --provider anthropic
```

### Skip enrichment pass (faster / fewer LLM calls)

```bash
python main.py \
  --url "https://www.g2.com/products/notion/reviews" \
  --no-enrich
```

### Output only to CSV (skip Airtable)

```bash
python main.py \
  --url "https://www.g2.com/products/notion/reviews" \
  --output csv
```

### Start fresh (ignore saved resume state)

```bash
python main.py \
  --url "https://www.g2.com/products/notion/reviews" \
  --fresh
```

### Full options

```
Options:
  -u, --url TEXT         Review page URL(s). Repeat for multiple.  [required]
  --engine [scrapingbee|apify|requests]
                         Scraping engine override.
  --provider [anthropic|openai]
                         LLM provider override.
  --output [csv|json|airtable|all]
                         Output destination.       [default: all]
  --no-enrich            Skip the strategic enrichment LLM pass.
  --top-n INTEGER        Gaps to enrich.           [default: 10]
  --fresh                Ignore saved state and re-scrape all URLs.
  --help                 Show this message and exit.
```

---

## 4 · Output

### Console (top 10 gaps by VibeScore)

```
================================================================
  TOP FEATURE GAPS BY VIBE SCORE
================================================================
  #01  [ 38.14]  No offline editing mode          (Missing Feature) — Freq=47, Sev=8.5, Cmplx=6.0
  #02  [ 31.88]  Slow load on large databases     (Performance)     — Freq=62, Sev=7.2, Cmplx=8.0
  #03  [ 27.43]  Missing Gantt / timeline view    (Missing Feature) — Freq=39, Sev=8.0, Cmplx=5.0
  ...
================================================================
```

### data/feature_gaps.csv

| Column | Description |
|---|---|
| `gap_title` | Concise label for the feature gap |
| `category` | UI/UX · Performance · Missing Feature · Integration · Other |
| `description` | Consolidated paragraph from LLM |
| `frequency` | Number of reviews mentioning this |
| `severity_score` | 1–10 how deal-breaking it is |
| `build_complexity` | 1–10 engineering effort |
| `vibe_score` | `log(1 + (freq × severity) / complexity) × 10` |
| `opportunity_summary` | Who would pay for a standalone solution |
| `monetization_angle` | SaaS · API · Plugin · Marketplace |
| `competitive_moat` | What would differentiate a new solution |
| `target_persona` | Job title most affected |

---

## 5 · VibeScore Algorithm

```
VibeScore = log(1 + (Frequency × Severity) / BuildComplexity) × 10
```

| Variable | Source | Range |
|---|---|---|
| **Frequency** | Count of source complaints | 1 – 100 (capped) |
| **Severity** | LLM-assigned (deal-breaking-ness) | 1.0 – 10.0 |
| **BuildComplexity** | LLM-estimated engineering effort | 1.0 – 10.0 |

A log transform prevents extreme frequency outliers from drowning out
high-severity/low-complexity gaps. Scores typically land in the **0 – 60** range.

---

## 6 · Sample LLM Prompt (Feature Extraction)

See `prompts.py` → `FEATURE_EXTRACTION_SYSTEM` and `FEATURE_EXTRACTION_USER`.

Key design decisions:
- **JSON-only output** instruction prevents hallucinated prose.
- **Indexed complaints** allow the LLM to cite source indices per gap.
- **Discard rule** baked into the system prompt, not post-processing.
- **Low temperature (0.2)** for deterministic, consistent categorisation.

---

## 7 · Resuming After Interruption

The bot auto-saves progress to `state/progress.json` before every page fetch.
Simply re-run the same command — already-processed URLs are skipped automatically.

```bash
# Interrupted run
python main.py --url "https://www.g2.com/products/notion/reviews"
# ^C

# Resume — just run the same command again
python main.py --url "https://www.g2.com/products/notion/reviews"
# > Resuming https://... from page 4
```

---

## 8 · Ethics & Compliance

| Rule | Implementation |
|---|---|
| **Public Data Only** | No login forms are submitted; no auth headers are used |
| **No PII** | Only `cons_text` is stored — no usernames, emails, or profile data |
| **robots.txt** | `Harvester` calls `is_allowed()` before every domain crawl |
| **Rate Limiting** | Randomised 2–5 s delay between every page request |
| **Exponential Backoff** | 403/429 trigger up to 5 retries with doubling wait |
