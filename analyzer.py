"""
================================================================================
  SaaS Opportunity Hunter Bot — analyzer.py
  Step B: Intelligence Filter (LLM categorisation + consolidation)
  Step C: Scoring Engine (VibeScore computation + enrichment)
================================================================================
"""

from __future__ import annotations

import json
import math
import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import anthropic
import openai
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    AIRTABLE_API_KEY,
    AIRTABLE_BASE_ID,
    AIRTABLE_TABLE,
    DISCARD_CATEGORIES,
    LLM_MAX_TOKENS,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OUTPUT_CSV,
    OUTPUT_JSON,
    REVIEW_BATCH_SIZE,
    VIBE_WEIGHTS,
)
from prompts import build_enrichment_prompt, build_extraction_prompt, build_triage_prompt
from scraper import RawReview


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class FeatureGap:
    """A consolidated, scored feature-gap opportunity."""
    product_url:         str
    platform:            str
    gap_title:           str
    category:            str
    description:         str
    frequency:           int
    severity_score:      float
    build_complexity:    float
    vibe_score:          float
    severity_rationale:  str
    complexity_rationale: str
    source_indices:      list[int]   = field(default_factory=list)
    # Enrichment fields (populated in second pass)
    opportunity_summary: str         = ""
    monetization_angle:  str         = ""
    competitive_moat:    str         = ""
    target_persona:      str         = ""


# ---------------------------------------------------------------------------
# LLM Client Factory
# ---------------------------------------------------------------------------

def _call_llm(system: str, user: str) -> str:
    """
    Dispatch to the configured LLM provider.
    Returns raw response text.
    """
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(system, user)
    return _call_openai(system, user)


@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, max=60),
)
def _call_anthropic(system: str, user: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg    = client.messages.create(
        model      = ANTHROPIC_MODEL,
        max_tokens = LLM_MAX_TOKENS,
        system     = system,
        messages   = [{"role": "user", "content": user}],
        temperature= LLM_TEMPERATURE,
    )
    return msg.content[0].text


@retry(
    retry=retry_if_exception_type((openai.RateLimitError, openai.APIStatusError)),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, max=60),
)
def _call_openai(system: str, user: str) -> str:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    resp   = client.chat.completions.create(
        model      = OPENAI_MODEL,
        max_tokens = LLM_MAX_TOKENS,
        temperature= LLM_TEMPERATURE,
        messages   = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# JSON Safety Parser
# ---------------------------------------------------------------------------

def _safe_parse_json(raw: str) -> Any:
    """Strip markdown fences and parse JSON defensively."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines   = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning(f"JSON parse failed ({exc}). Attempting bracket extraction.")
        # Find first '[' or '{' and last ']' or '}'
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            s = cleaned.find(start_char)
            e = cleaned.rfind(end_char)
            if s != -1 and e != -1 and e > s:
                try:
                    return json.loads(cleaned[s: e + 1])
                except json.JSONDecodeError:
                    pass
        raise


# ---------------------------------------------------------------------------
# Step B — Intelligence Filter
# ---------------------------------------------------------------------------

class IntelligenceFilter:
    """
    Sends batches of cons to the LLM for categorisation and consolidation.
    Discards Pricing and Customer Support complaints.
    Groups similar complaints into consolidated FeatureGap entries.
    """

    def __init__(self) -> None:
        pass

    def process(self, reviews: list[RawReview]) -> list[dict]:
        """
        Group reviews by source URL, batch them, send to LLM, collect gaps.
        Returns a list of raw gap dicts (before VibeScore is applied).
        """
        # Group by product URL (strip query params for cleanliness)
        url_groups: dict[str, list[RawReview]] = {}
        for rv in reviews:
            key = _normalise_url(rv.source_url)
            url_groups.setdefault(key, []).append(rv)

        all_gaps: list[dict] = []

        for product_url, group in url_groups.items():
            platform = group[0].platform
            cons_texts = [r.cons_text for r in group]
            logger.info(
                f"Processing {len(cons_texts)} cons for {product_url} "
                f"in batches of {REVIEW_BATCH_SIZE}"
            )
            gaps = self._process_group(cons_texts, platform, product_url)
            for g in gaps:
                g["product_url"] = product_url
                g["platform"]    = platform
            all_gaps.extend(gaps)

        logger.success(f"Intelligence filter → {len(all_gaps)} raw feature gaps extracted.")
        return all_gaps

    def _process_group(
        self, cons: list[str], platform: str, product_url: str
    ) -> list[dict]:
        gaps: list[dict] = []

        for batch_start in range(0, len(cons), REVIEW_BATCH_SIZE):
            batch = cons[batch_start: batch_start + REVIEW_BATCH_SIZE]
            system, user = build_extraction_prompt(batch, platform, product_url)

            try:
                raw    = _call_llm(system, user)
                parsed = _safe_parse_json(raw)
                if not isinstance(parsed, list):
                    parsed = [parsed]
            except Exception as exc:
                logger.error(f"LLM batch failed ({exc}); falling back to single triage.")
                parsed = self._triage_single(batch, product_url)

            for item in parsed:
                cat = item.get("category", "Other")
                if cat in DISCARD_CATEGORIES:
                    logger.debug(f"Discarding '{cat}' gap: {item.get('gap_title', '')}")
                    continue
                gaps.append(item)

        return gaps

    def _triage_single(self, complaints: list[str], product_url: str) -> list[dict]:
        """Fallback: triage complaints one-by-one when batch JSON fails."""
        results = []
        for complaint in complaints:
            system, user = build_triage_prompt(complaint, product_url)
            try:
                raw    = _call_llm(system, user)
                parsed = _safe_parse_json(raw)
                if parsed.get("keep") and parsed.get("category") not in DISCARD_CATEGORIES:
                    results.append({
                        "gap_title":           parsed.get("summary", complaint[:80]),
                        "category":            parsed.get("category", "Other"),
                        "description":         parsed.get("summary", complaint),
                        "source_indices":      [],
                        "frequency":           1,
                        "severity_score":      5.0,
                        "build_complexity":    5.0,
                        "severity_rationale":  "Estimated (triage fallback)",
                        "complexity_rationale":"Estimated (triage fallback)",
                    })
            except Exception as exc:
                logger.error(f"Single triage failed for complaint: {exc}")
        return results


# ---------------------------------------------------------------------------
# Step C — Scoring Engine
# ---------------------------------------------------------------------------

class ScoringEngine:
    """
    Computes VibeScore = (Frequency × Severity) / BuildComplexity
    and optionally enriches top gaps with LLM-generated opportunity context.
    """

    @staticmethod
    def compute_vibe_score(frequency: int, severity: float, build_complexity: float) -> float:
        """
        VibeScore = (Frequency × Severity) / BuildComplexity

        Clamping applied to prevent extreme outliers from dominating.
        """
        freq    = min(frequency, VIBE_WEIGHTS.frequency_cap)
        sev     = max(VIBE_WEIGHTS.severity_min,
                      min(VIBE_WEIGHTS.severity_max, severity))
        build   = max(VIBE_WEIGHTS.build_complexity_min,
                      min(VIBE_WEIGHTS.build_complexity_max, build_complexity))
        raw     = (freq * sev) / build
        # Log-normalise for readability  (score in approx 0–100 range)
        return round(math.log1p(raw) * 10, 2)

    def score(self, gaps: list[dict]) -> list[FeatureGap]:
        """Convert raw gap dicts → scored FeatureGap objects, sorted by VibeScore desc."""
        feature_gaps: list[FeatureGap] = []

        for g in gaps:
            freq       = int(g.get("frequency", 1))
            severity   = float(g.get("severity_score", 5.0))
            complexity = float(g.get("build_complexity", 5.0))
            vibe       = self.compute_vibe_score(freq, severity, complexity)

            feature_gaps.append(FeatureGap(
                product_url          = g.get("product_url", ""),
                platform             = g.get("platform", "unknown"),
                gap_title            = g.get("gap_title", "Unnamed Gap"),
                category             = g.get("category", "Other"),
                description          = g.get("description", ""),
                frequency            = freq,
                severity_score       = severity,
                build_complexity     = complexity,
                vibe_score           = vibe,
                severity_rationale   = g.get("severity_rationale", ""),
                complexity_rationale = g.get("complexity_rationale", ""),
                source_indices       = g.get("source_indices", []),
            ))

        feature_gaps.sort(key=lambda x: x.vibe_score, reverse=True)
        logger.success(
            f"Scoring complete — top gap: '{feature_gaps[0].gap_title}' "
            f"(VibeScore {feature_gaps[0].vibe_score})"
            if feature_gaps else "Scoring complete — no gaps to score."
        )
        return feature_gaps

    def enrich_top_gaps(
        self, gaps: list[FeatureGap], top_n: int = 10
    ) -> list[FeatureGap]:
        """Run a second LLM pass on the top-N gaps to add strategic context."""
        top   = gaps[:top_n]
        rest  = gaps[top_n:]

        gaps_payload = [
            {
                "gap_title":       g.gap_title,
                "vibe_score":      g.vibe_score,
                "category":        g.category,
                "description":     g.description,
                "frequency":       g.frequency,
                "severity_score":  g.severity_score,
                "build_complexity":g.build_complexity,
            }
            for g in top
        ]
        product_url = top[0].product_url if top else "unknown"

        system, user = build_enrichment_prompt(
            json.dumps(gaps_payload, indent=2), product_url
        )
        try:
            raw        = _call_llm(system, user)
            enriched   = _safe_parse_json(raw)
            if not isinstance(enriched, list):
                enriched = [enriched]

            for i, g in enumerate(top):
                try:
                    extras = enriched[i]
                    g.opportunity_summary = extras.get("opportunity_summary", "")
                    g.monetization_angle  = extras.get("monetization_angle",  "")
                    g.competitive_moat    = extras.get("competitive_moat",    "")
                    g.target_persona      = extras.get("target_persona",      "")
                except (IndexError, TypeError):
                    pass

        except Exception as exc:
            logger.error(f"Enrichment LLM call failed: {exc}")

        return top + rest


# ---------------------------------------------------------------------------
# Orchestrator — ties Steps B and C together
# ---------------------------------------------------------------------------

class Analyzer:
    """High-level orchestrator: raw reviews → scored & enriched FeatureGaps."""

    def __init__(self) -> None:
        self.filter  = IntelligenceFilter()
        self.scorer  = ScoringEngine()

    def run(
        self,
        reviews: list[RawReview],
        enrich: bool = True,
        top_n: int = 10,
    ) -> list[FeatureGap]:
        raw_gaps  = self.filter.process(reviews)
        scored    = self.scorer.score(raw_gaps)
        if enrich and scored:
            scored = self.scorer.enrich_top_gaps(scored, top_n=top_n)
        return scored


# ---------------------------------------------------------------------------
# Storage Writers
# ---------------------------------------------------------------------------

def save_to_json(gaps: list[FeatureGap], path: Path = OUTPUT_JSON) -> None:
    data = [asdict(g) for g in gaps]
    path.write_text(json.dumps(data, indent=2))
    logger.info(f"JSON output → {path} ({len(gaps)} gaps)")


def save_to_csv(gaps: list[FeatureGap], path: Path = OUTPUT_CSV) -> None:
    if not gaps:
        logger.warning("No gaps to write to CSV.")
        return
    fieldnames = list(asdict(gaps[0]).keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for g in gaps:
            row = asdict(g)
            # Flatten lists for CSV readability
            row["source_indices"] = "|".join(str(i) for i in row["source_indices"])
            writer.writerow(row)
    logger.info(f"CSV output → {path} ({len(gaps)} rows)")


def save_to_airtable(gaps: list[FeatureGap]) -> None:
    """Push feature gaps to Airtable base. Requires AIRTABLE_* env vars."""
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE_ID:
        logger.warning("Airtable credentials not set — skipping Airtable export.")
        return

    import requests as req

    url     = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type":  "application/json",
    }

    # Airtable accepts max 10 records per request
    for batch_start in range(0, len(gaps), 10):
        batch   = gaps[batch_start: batch_start + 10]
        records = [
            {
                "fields": {
                    "Product URL":           g.product_url,
                    "Platform":              g.platform,
                    "Gap Title":             g.gap_title,
                    "Category":              g.category,
                    "Description":           g.description,
                    "Frequency":             g.frequency,
                    "Severity Score":        g.severity_score,
                    "Build Complexity":      g.build_complexity,
                    "Vibe Score":            g.vibe_score,
                    "Opportunity Summary":   g.opportunity_summary,
                    "Monetization Angle":    g.monetization_angle,
                    "Competitive Moat":      g.competitive_moat,
                    "Target Persona":        g.target_persona,
                }
            }
            for g in batch
        ]
        resp = req.post(url, json={"records": records}, headers=headers, timeout=30)
        if resp.status_code not in (200, 201):
            logger.error(f"Airtable push failed: {resp.status_code} — {resp.text[:200]}")
        else:
            logger.info(f"Airtable: pushed {len(batch)} records.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
