"""
================================================================================
  SaaS Opportunity Hunter Bot — main.py
  CLI Entry Point

  Usage:
      python main.py --url "https://www.g2.com/products/notion/reviews"
      python main.py --url <url1> --url <url2> --no-enrich --output airtable
      python main.py --url <url> --engine requests --provider openai

  Ethics Declaration:
    ✅ Public Data Only — Only scrapes publicly accessible review pages.
    ✅ No PII Collection — Reviews are stored as anonymized complaint text only.
    ✅ robots.txt Respected — Checked before crawling any domain.
================================================================================
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from loguru import logger

# ---------------------------------------------------------------------------
# Logging bootstrap (before any other imports that might log)
# ---------------------------------------------------------------------------
from config import LOG_FILE, STATE_FILE

logger.remove()
logger.add(sys.stderr, level="INFO",  colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add(LOG_FILE,   level="DEBUG", rotation="10 MB", retention="7 days",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}")

from analyzer import Analyzer, save_to_airtable, save_to_csv, save_to_json
from scraper import Harvester, HarvesterState


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--url", "-u",
    multiple=True,
    required=True,
    help="Review page URL(s) to harvest. Can be specified multiple times.",
)
@click.option(
    "--engine",
    type=click.Choice(["scrapingbee", "apify", "requests"]),
    default=None,
    help="Scraping engine override (default: from config / env var SCRAPING_ENGINE).",
)
@click.option(
    "--provider",
    type=click.Choice(["anthropic", "openai"]),
    default=None,
    help="LLM provider override (default: from config / env var LLM_PROVIDER).",
)
@click.option(
    "--output",
    type=click.Choice(["csv", "json", "airtable", "all"]),
    default="all",
    show_default=True,
    help="Where to write results.",
)
@click.option(
    "--no-enrich",
    is_flag=True,
    default=False,
    help="Skip the LLM enrichment pass (faster, fewer API calls).",
)
@click.option(
    "--top-n",
    default=10,
    show_default=True,
    help="Number of top gaps to enrich with strategic context.",
)
@click.option(
    "--fresh",
    is_flag=True,
    default=False,
    help="Ignore saved state and start fresh (re-scrapes all URLs).",
)
def main(
    url: tuple[str, ...],
    engine: str | None,
    provider: str | None,
    output: str,
    no_enrich: bool,
    top_n: int,
    fresh: bool,
) -> None:
    """
    🕵️  SaaS Opportunity Hunter Bot

    Harvests 2-3 star reviews from G2, Capterra, and Trustpilot,
    extracts feature gaps via LLM analysis, and scores them with
    the VibeScore algorithm.
    """
    import config as cfg

    # Dynamic overrides
    if engine:
        cfg.SCRAPING_ENGINE = engine  # type: ignore[assignment]
    if provider:
        cfg.LLM_PROVIDER = provider  # type: ignore[assignment]

    urls = list(url)
    logger.info(f"🚀 Starting SaaS Opportunity Hunter — {len(urls)} URL(s)")
    logger.info(f"   Engine: {cfg.SCRAPING_ENGINE} | LLM: {cfg.LLM_PROVIDER}")

    # ------------------------------------------------------------------
    # Step A — Harvesting
    # ------------------------------------------------------------------
    if fresh and STATE_FILE.exists():
        STATE_FILE.unlink()
        logger.info("Fresh run — cleared saved state.")

    state     = HarvesterState.load()
    harvester = Harvester(state=state)

    try:
        reviews = harvester.harvest(urls)
    except KeyboardInterrupt:
        logger.warning("Interrupted — progress saved. Re-run to resume.")
        sys.exit(0)

    if not reviews:
        logger.error("No reviews harvested. Check your URLs, API keys, or robots.txt status.")
        sys.exit(1)

    logger.info(f"✅ Harvested {len(reviews)} cons from {len(urls)} URL(s).")

    # ------------------------------------------------------------------
    # Step B + C — Analysis & Scoring
    # ------------------------------------------------------------------
    analyzer = Analyzer()

    try:
        gaps = analyzer.run(
            reviews = reviews,
            enrich  = not no_enrich,
            top_n   = top_n,
        )
    except KeyboardInterrupt:
        logger.warning("Analysis interrupted.")
        sys.exit(0)

    if not gaps:
        logger.warning("No feature gaps identified after filtering. "
                       "All complaints may have been Pricing/Support related.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    logger.info(f"\n{'='*60}")
    logger.info(f"  TOP FEATURE GAPS BY VIBE SCORE")
    logger.info(f"{'='*60}")
    for i, g in enumerate(gaps[:10], 1):
        logger.info(
            f"  #{i:02d}  [{g.vibe_score:6.2f}]  {g.gap_title}  "
            f"({g.category}) — Freq={g.frequency}, "
            f"Sev={g.severity_score}, Cmplx={g.build_complexity}"
        )
    logger.info(f"{'='*60}\n")

    if output in ("json", "all"):
        save_to_json(gaps)

    if output in ("csv", "all"):
        save_to_csv(gaps)

    if output in ("airtable", "all"):
        save_to_airtable(gaps)

    logger.success(
        f"Done! {len(gaps)} feature gaps written. "
        f"Check data/ for CSV/JSON output."
    )


if __name__ == "__main__":
    main()
