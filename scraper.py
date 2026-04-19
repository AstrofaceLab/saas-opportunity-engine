"""
================================================================================
  SaaS Opportunity Hunter Bot — scraper.py  (The Harvester)
  Ethics Declaration:
    ✅ Public Data Only — Only scrapes publicly accessible review pages.
    ✅ No PII Collection — Only "Cons" text is extracted; no user identifiers.
    ✅ robots.txt Respected — Checked before every new domain is crawled.
    ✅ Rate-Limited — Randomized 2–5 s delays between requests.
================================================================================
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

import httpx
import requests
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    RetryError,
)
import logging

from config import (
    APIFY_API_KEY,
    APIFY_ACTOR_ID,
    BACKOFF_MAX_WAIT,
    BACKOFF_MULTIPLIER,
    GENERIC_SELECTORS,
    MAX_PAGES_PER_URL,
    MAX_RETRIES,
    MAX_REVIEW_STARS,
    MIN_REVIEW_STARS,
    RATE_LIMIT_MAX_SECS,
    RATE_LIMIT_MIN_SECS,
    SCRAPINGBEE_API_KEY,
    SCRAPINGBEE_JS_RENDER,
    SCRAPINGBEE_PREMIUM_PROXY,
    SCRAPING_ENGINE,
    SITE_SELECTORS,
    STATE_FILE,
)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class RawReview:
    """A single raw complaint extracted from a review page."""
    source_url:  str
    platform:    str
    star_rating: Optional[float]
    cons_text:   str
    page_num:    int


@dataclass
class HarvesterState:
    """Persistent state so the bot can resume after interruption."""
    completed_urls: list[str]         = field(default_factory=list)
    partial_url:    Optional[str]     = None
    partial_page:   int               = 1
    raw_reviews:    list[dict]        = field(default_factory=list)   # serialised RawReview

    def save(self, path: Path = STATE_FILE) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))
        logger.debug(f"State saved → {path}")

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "HarvesterState":
        if path.exists():
            data = json.loads(path.read_text())
            return cls(**data)
        return cls()


# ---------------------------------------------------------------------------
# robots.txt Cache
# ---------------------------------------------------------------------------

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

def _get_robots(base_url: str) -> urllib.robotparser.RobotFileParser:
    if base_url not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{base_url}/robots.txt")
        try:
            rp.read()
        except Exception as exc:
            logger.warning(f"Could not read robots.txt for {base_url}: {exc}")
        _robots_cache[base_url] = rp
    return _robots_cache[base_url]


def is_allowed(url: str, user_agent: str = "*") -> bool:
    parsed = urlparse(url)
    base   = f"{parsed.scheme}://{parsed.netloc}"
    rp     = _get_robots(base)
    allowed = rp.can_fetch(user_agent, url)
    if not allowed:
        logger.warning(f"robots.txt DISALLOWS: {url}")
    return allowed


# ---------------------------------------------------------------------------
# HTTP Fetching Backends
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SaaSOpportunityHunterBot/1.0; "
        "+https://github.com/your-org/saas-hunter)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(Exception):
    """Raised for non-retryable fetch failures."""


class RateLimitError(Exception):
    """Raised on HTTP 429."""


class ForbiddenError(Exception):
    """Raised on HTTP 403."""


@retry(
    retry=retry_if_exception_type((RateLimitError, httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=BACKOFF_MULTIPLIER, max=BACKOFF_MAX_WAIT),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _fetch_scrapingbee(url: str) -> str:
    """Fetch via ScrapingBee API with JS rendering and premium proxies."""
    if not SCRAPINGBEE_API_KEY:
        raise FetchError("SCRAPINGBEE_API_KEY is not set.")

    params = {
        "api_key":        SCRAPINGBEE_API_KEY,
        "url":            url,
        "render_js":      str(SCRAPINGBEE_JS_RENDER).lower(),
        "premium_proxy":  str(SCRAPINGBEE_PREMIUM_PROXY).lower(),
        "block_ads":      "true",
        "stealth_proxy":  "true",
    }
    resp = requests.get("https://app.scrapingbee.com/api/v1/", params=params, timeout=60)

    if resp.status_code == 429:
        raise RateLimitError(f"ScrapingBee 429 on {url}")
    if resp.status_code == 403:
        raise ForbiddenError(f"ScrapingBee 403 on {url}")
    if resp.status_code != 200:
        raise FetchError(f"ScrapingBee {resp.status_code} on {url}")

    return resp.text


@retry(
    retry=retry_if_exception_type((RateLimitError, httpx.TimeoutException)),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=BACKOFF_MULTIPLIER, max=BACKOFF_MAX_WAIT),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _fetch_apify(url: str) -> str:
    """Fetch via Apify Actor run (synchronous)."""
    if not APIFY_API_KEY:
        raise FetchError("APIFY_API_KEY is not set.")

    run_url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
    payload = {
        "startUrls": [{"url": url}],
        "maxRequestsPerCrawl": 1,
    }
    resp = requests.post(
        run_url,
        json=payload,
        headers={"Authorization": f"Bearer {APIFY_API_KEY}"},
        timeout=120,
    )
    if resp.status_code == 429:
        raise RateLimitError(f"Apify 429 on {url}")
    if resp.status_code != 200:
        raise FetchError(f"Apify {resp.status_code}: {resp.text[:200]}")

    items = resp.json()
    if not items:
        raise FetchError("Apify returned empty dataset.")
    return items[0].get("html", items[0].get("body", ""))


@retry(
    retry=retry_if_exception_type((RateLimitError, httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=BACKOFF_MULTIPLIER, max=BACKOFF_MAX_WAIT),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _fetch_plain(url: str) -> str:
    """Fallback: plain httpx fetch (no JS rendering)."""
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        resp = client.get(url)
    if resp.status_code == 429:
        raise RateLimitError(f"429 on {url}")
    if resp.status_code == 403:
        raise ForbiddenError(f"403 on {url}")
    resp.raise_for_status()
    return resp.text


def fetch_html(url: str) -> str:
    """Route to the configured scraping engine."""
    engine_map = {
        "scrapingbee": _fetch_scrapingbee,
        "apify":       _fetch_apify,
        "requests":    _fetch_plain,
    }
    fetcher = engine_map.get(SCRAPING_ENGINE, _fetch_plain)
    logger.info(f"[{SCRAPING_ENGINE.upper()}] Fetching → {url}")
    return fetcher(url)


# ---------------------------------------------------------------------------
# HTML Parsing
# ---------------------------------------------------------------------------

def _detect_platform(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for domain in SITE_SELECTORS:
        if domain in netloc:
            return domain
    return "unknown"


def _get_selectors(platform: str):
    return SITE_SELECTORS.get(platform, GENERIC_SELECTORS)


def _parse_star_rating(element, selectors) -> Optional[float]:
    for sel in selectors.star_rating:
        tag = element.select_one(sel)
        if not tag:
            continue
        for attr in ("data-rating", "title", "aria-label", "content"):
            val = tag.get(attr, "")
            if val:
                # Extract first float-like token
                import re
                m = re.search(r"(\d+(?:\.\d+)?)", str(val))
                if m:
                    return float(m.group(1))
    return None


def _is_in_star_range(rating: Optional[float]) -> bool:
    if rating is None:
        return True   # include when rating is ambiguous
    return MIN_REVIEW_STARS <= rating <= MAX_REVIEW_STARS


def _extract_cons_from_html(html: str, url: str) -> list[tuple[Optional[float], str]]:
    """
    Parse one page of HTML and return list of (star_rating, cons_text) pairs.
    No PII is extracted — only complaint text blocks.
    """
    platform  = _detect_platform(url)
    selectors = _get_selectors(platform)
    soup      = BeautifulSoup(html, "html.parser")
    results   = []

    cards = soup.select(" ,".join(selectors.review_card))
    if not cards:
        # Fallback: treat whole page as one block
        cards = [soup]

    for card in cards:
        rating = _parse_star_rating(card, selectors)
        if not _is_in_star_range(rating):
            continue

        for sel in selectors.cons:
            elems = card.select(sel)
            for elem in elems:
                text = elem.get_text(separator=" ", strip=True)
                if text and len(text) > 20:   # skip trivially short blobs
                    results.append((rating, text))

    return results


def _get_next_page_url(html: str, current_url: str) -> Optional[str]:
    platform  = _detect_platform(current_url)
    selectors = _get_selectors(platform)
    soup      = BeautifulSoup(html, "html.parser")

    for sel in selectors.next_page:
        tag = soup.select_one(sel)
        if tag and tag.get("href"):
            href = tag["href"]
            return urllib.parse.urljoin(current_url, href)
    return None


# ---------------------------------------------------------------------------
# The Harvester
# ---------------------------------------------------------------------------

class Harvester:
    """
    Orchestrates crawling of multiple review URLs with:
      - robots.txt checking
      - randomised rate limiting
      - graceful error handling
      - state persistence (resume on interruption)
    """

    def __init__(self, state: Optional[HarvesterState] = None) -> None:
        self.state = state or HarvesterState.load()

    def harvest(self, urls: list[str]) -> list[RawReview]:
        """
        Main entry point. Iterates over URLs, extracts cons, and returns
        a deduplicated list of RawReview objects.
        """
        already_done = set(self.state.completed_urls)
        reviews: list[RawReview] = [RawReview(**r) for r in self.state.raw_reviews]

        for url in urls:
            if url in already_done:
                logger.info(f"Skipping already-harvested URL: {url}")
                continue

            if not is_allowed(url):
                logger.warning(f"robots.txt blocks {url} — skipping.")
                continue

            platform   = _detect_platform(url)
            start_page = 1

            # Resume mid-URL if interrupted
            if self.state.partial_url == url:
                start_page = self.state.partial_page
                logger.info(f"Resuming {url} from page {start_page}")

            new_reviews = list(self._crawl_url(url, platform, start_page))
            reviews.extend(new_reviews)

            self.state.completed_urls.append(url)
            self.state.partial_url  = None
            self.state.partial_page = 1
            self.state.raw_reviews  = [asdict(r) for r in reviews]
            self.state.save()

        logger.success(f"Harvesting complete — {len(reviews)} cons extracted.")
        return reviews

    def _crawl_url(
        self, url: str, platform: str, start_page: int = 1
    ) -> Iterator[RawReview]:
        current_url = url
        page_num    = 1

        # Fast-forward to start_page
        for _ in range(start_page - 1):
            try:
                html = fetch_html(current_url)
            except (FetchError, ForbiddenError, RetryError) as exc:
                logger.error(f"Cannot fast-forward to page {_+1}: {exc}")
                return
            next_url = _get_next_page_url(html, current_url)
            if not next_url:
                return
            current_url = next_url
            page_num += 1
            _rate_limit()

        while current_url and page_num <= MAX_PAGES_PER_URL:
            # Save partial state before each page fetch
            self.state.partial_url  = url
            self.state.partial_page = page_num
            self.state.save()

            try:
                html = fetch_html(current_url)
            except ForbiddenError:
                logger.error(f"403 Forbidden — stopping crawl of {url}")
                break
            except RetryError as exc:
                logger.error(f"All retries exhausted for {current_url}: {exc}")
                break
            except Exception as exc:
                logger.exception(f"Unexpected error fetching {current_url}: {exc}")
                break

            cons_list = _extract_cons_from_html(html, current_url)
            logger.info(
                f"  Page {page_num} | {current_url} → {len(cons_list)} cons found"
            )

            for rating, cons_text in cons_list:
                yield RawReview(
                    source_url  = current_url,
                    platform    = platform,
                    star_rating = rating,
                    cons_text   = cons_text,
                    page_num    = page_num,
                )

            next_url = _get_next_page_url(html, current_url)
            if not next_url or next_url == current_url:
                logger.debug(f"No further pages found after page {page_num}.")
                break

            current_url = next_url
            page_num   += 1
            _rate_limit()


def _rate_limit() -> None:
    delay = random.uniform(RATE_LIMIT_MIN_SECS, RATE_LIMIT_MAX_SECS)
    logger.debug(f"Rate-limit pause: {delay:.2f}s")
    time.sleep(delay)
