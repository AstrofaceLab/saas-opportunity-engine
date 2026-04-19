"""
================================================================================
  SaaS Opportunity Hunter Bot — config.py
  Ethics Declaration:
    ✅ Public Data Only — No login-walled or paywalled content is scraped.
    ✅ No PII Collection — Reviews are stored as anonymized complaint text only.
                           No usernames, emails, or profile data are retained.
    ✅ robots.txt Respected — The Harvester checks robots.txt before crawling.
    ✅ Rate-Limited — Randomized delays to avoid server overload.
================================================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Directory Layout
# ---------------------------------------------------------------------------
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
STATE_DIR  = BASE_DIR / "state"
LOG_DIR    = BASE_DIR / "logs"

for _d in (DATA_DIR, STATE_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# API Keys  (override via environment variables — never hardcode secrets)
# ---------------------------------------------------------------------------
SCRAPINGBEE_API_KEY: str = os.getenv("SCRAPINGBEE_API_KEY", "")
APIFY_API_KEY:       str = os.getenv("APIFY_API_KEY", "")
OPENAI_API_KEY:      str = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY:   str = os.getenv("ANTHROPIC_API_KEY", "")

# Supabase (optional — future vector-search layer)
SUPABASE_URL:    str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY:    str = os.getenv("SUPABASE_KEY", "")

# Airtable (optional — structured output layer)
AIRTABLE_API_KEY: str = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID: str = os.getenv("AIRTABLE_BASE_ID", "")
AIRTABLE_TABLE:   str = os.getenv("AIRTABLE_TABLE", "FeatureGaps")

# ---------------------------------------------------------------------------
# LLM Provider Selection
# ---------------------------------------------------------------------------
LLMProvider = Literal["openai", "anthropic"]
LLM_PROVIDER: LLMProvider = os.getenv("LLM_PROVIDER", "anthropic")  # type: ignore[assignment]

OPENAI_MODEL:    str = "gpt-4o"
ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
LLM_MAX_TOKENS:  int = 4096
LLM_TEMPERATURE: float = 0.2          # Low temp → deterministic JSON output

# ---------------------------------------------------------------------------
# Scraping Engine Selection
# ---------------------------------------------------------------------------
ScrapingEngine = Literal["scrapingbee", "apify", "requests"]
SCRAPING_ENGINE: ScrapingEngine = os.getenv("SCRAPING_ENGINE", "scrapingbee")  # type: ignore[assignment]

# ScrapingBee options
SCRAPINGBEE_JS_RENDER: bool = True
SCRAPINGBEE_PREMIUM_PROXY: bool = True

# Apify actor for general scraping
APIFY_ACTOR_ID: str = "apify/cheerio-scraper"

# ---------------------------------------------------------------------------
# Harvester Tuning
# ---------------------------------------------------------------------------
RATE_LIMIT_MIN_SECS: float = 2.0   # minimum delay between requests
RATE_LIMIT_MAX_SECS: float = 5.0   # maximum delay between requests

REVIEW_BATCH_SIZE:   int = 20      # cons sent to LLM per batch
MIN_REVIEW_STARS:    int = 2       # ≥ this star rating is harvested
MAX_REVIEW_STARS:    int = 3       # ≤ this star rating is harvested
MAX_PAGES_PER_URL:   int = 10      # safety cap on pagination

# Retry / backoff
MAX_RETRIES:       int = 5
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MAX_WAIT:  float = 60.0    # seconds

# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------
DISCARD_CATEGORIES: set[str] = {"Pricing", "Customer Support"}

@dataclass
class VibeScoreWeights:
    """Weights used in VibeScore = (Frequency × Severity) / BuildComplexity."""
    frequency_cap:        int   = 100   # clip outlier frequencies
    severity_min:         float = 1.0
    severity_max:         float = 10.0
    build_complexity_min: float = 1.0
    build_complexity_max: float = 10.0

VIBE_WEIGHTS = VibeScoreWeights()

# ---------------------------------------------------------------------------
# Storage Paths
# ---------------------------------------------------------------------------
STATE_FILE:  Path = STATE_DIR / "progress.json"
OUTPUT_CSV:  Path = DATA_DIR  / "feature_gaps.csv"
OUTPUT_JSON: Path = DATA_DIR  / "feature_gaps.json"
LOG_FILE:    Path = LOG_DIR   / "hunter.log"

# ---------------------------------------------------------------------------
# Site-Specific CSS Selectors
# ---------------------------------------------------------------------------
@dataclass
class SiteSelectors:
    """CSS selectors for extracting 'Cons' / 'What do you dislike?' text."""
    cons:        list[str]
    star_rating: list[str]
    next_page:   list[str]
    review_card: list[str]

SITE_SELECTORS: dict[str, SiteSelectors] = {
    "g2.com": SiteSelectors(
        cons=[
            "[data-testid='review-cons']",
            ".paper--white .review-answer--last p",
            "div[itemprop='reviewBody'] + div p",
        ],
        star_rating=[
            "div.stars-container[title]",
            "span[data-rating]",
        ],
        next_page=["a[aria-label='Next Page']", "a.pagination__link--next"],
        review_card=["div.paper.paper--white.paper--box"],
    ),
    "capterra.com": SiteSelectors(
        cons=[
            "div[data-testid='review-cons-text']",
            ".review-content__cons-text",
            "p.pros-cons__text:last-of-type",
        ],
        star_rating=["span.review-rating", "div[data-rating]"],
        next_page=["a[rel='next']", "button[aria-label='Next']"],
        review_card=["div.review-item", "article.review-card"],
    ),
    "trustpilot.com": SiteSelectors(
        cons=[
            "p[data-service-review-text-typography]",
            "section.review-content p",
            "div.review-content__body",
        ],
        star_rating=["div[data-service-review-rating]", "img.star-rating"],
        next_page=["a[name='pagination-button-next']", "a[data-page-number]"],
        review_card=["article.review-container", "div.review"],
    ),
}

# Fallback generic selectors when domain not in SITE_SELECTORS
GENERIC_SELECTORS = SiteSelectors(
    cons=[
        "[class*='cons']",
        "[class*='dislike']",
        "[class*='negative']",
        "[id*='cons']",
    ],
    star_rating=["[class*='star']", "[class*='rating']"],
    next_page=["a[rel='next']", "[aria-label*='Next']"],
    review_card=["[class*='review']"],
)
