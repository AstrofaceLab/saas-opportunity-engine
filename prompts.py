"""
================================================================================
  SaaS Opportunity Hunter Bot — prompts.py
  LLM Prompt Templates for Intelligence Filter & Scoring Engine
================================================================================
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# STEP B — Feature Extraction & Categorization Prompt
# ---------------------------------------------------------------------------

FEATURE_EXTRACTION_SYSTEM = """\
You are a Senior Product Analyst specializing in competitive intelligence for B2B SaaS.
Your task is to process raw user complaints from software review platforms and extract
structured, actionable "Feature Gap" signals.

RULES — follow strictly:
1. Respond ONLY with valid JSON — no preamble, no markdown fences, no explanation.
2. Discard any complaint that is primarily about "Pricing" or "Customer Support".
3. Group semantically identical or near-identical complaints into ONE consolidated entry.
4. Use only these categories: UI/UX, Performance, Missing Feature, Integration, Other.
5. Do not store, infer, or output any Personally Identifiable Information (PII).
6. Be specific — vague inputs like "it's bad" must be discarded (return empty list for those).
"""

FEATURE_EXTRACTION_USER = """\
Below is a batch of "Cons" / "What do you dislike?" excerpts from {platform} reviews
of the SaaS product at: {product_url}

Each excerpt is on a new line, prefixed with its index.

--- COMPLAINTS ---
{complaints}
--- END ---

Return a JSON array of objects. Each object must have exactly these keys:

{{
  "gap_title":    "<concise 5-10 word title for the feature gap>",
  "category":     "<UI/UX | Performance | Missing Feature | Integration | Other>",
  "description":  "<one paragraph summarising the consolidated complaint>",
  "source_indices": [<list of complaint index integers that contributed>],
  "frequency":    <integer — how many source complaints map to this gap>,
  "severity_score": <float 1.0–10.0 — how deal-breaking is this for users>,
  "build_complexity": <float 1.0–10.0 — engineering effort to fix (1=weekend, 10=core infra)>,
  "severity_rationale":    "<one sentence explaining the severity score>",
  "complexity_rationale":  "<one sentence explaining the build complexity score>"
}}

Critical constraints:
- severity_score: 1.0 = minor annoyance, 10.0 = users actively churn because of this.
- build_complexity: 1.0 = simple UI tweak or config option, 10.0 = requires rearchitecting core systems.
- If all complaints are Pricing/Support related, return an empty JSON array: []
"""

# ---------------------------------------------------------------------------
# STEP C — VibeScore Enrichment Prompt (optional second-pass)
# ---------------------------------------------------------------------------

VIBE_ENRICHMENT_SYSTEM = """\
You are a startup opportunity analyst. Given a list of feature gaps with their
VibeScore (pre-computed), enrich each entry with strategic context.
Respond ONLY with valid JSON.
"""

VIBE_ENRICHMENT_USER = """\
Here are the top feature gaps by VibeScore for the product: {product_url}

{gaps_json}

For each gap, add these fields (do NOT change existing fields):
{{
  "opportunity_summary": "<2-3 sentences: who would pay for this standalone tool>",
  "monetization_angle":  "<SaaS, marketplace, API, plugin — pick the best fit>",
  "competitive_moat":    "<what would differentiate a new solution here>",
  "target_persona":      "<job title / role most affected by this gap>"
}}

Return the enriched JSON array.
"""

# ---------------------------------------------------------------------------
# STEP B — Single-complaint triage (used when batch fails to parse)
# ---------------------------------------------------------------------------

SINGLE_TRIAGE_SYSTEM = """\
You are a SaaS product analyst. Classify a single user complaint.
Respond ONLY with a JSON object, no markdown, no explanation.
"""

SINGLE_TRIAGE_USER = """\
Classify this complaint from a review of {product_url}:

"{complaint}"

Return JSON:
{{
  "category": "<UI/UX | Performance | Missing Feature | Integration | Pricing | Customer Support | Other>",
  "keep": <true if NOT Pricing or Customer Support, else false>,
  "summary": "<one sentence rephrasing without PII>"
}}
"""

# ---------------------------------------------------------------------------
# Prompt builder helpers
# ---------------------------------------------------------------------------

def build_extraction_prompt(
    complaints: list[str],
    platform: str,
    product_url: str,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the extraction call."""
    indexed = "\n".join(f"{i}. {c}" for i, c in enumerate(complaints))
    user = FEATURE_EXTRACTION_USER.format(
        platform=platform,
        product_url=product_url,
        complaints=indexed,
    )
    return FEATURE_EXTRACTION_SYSTEM, user


def build_enrichment_prompt(gaps_json: str, product_url: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the VibeScore enrichment call."""
    user = VIBE_ENRICHMENT_USER.format(
        product_url=product_url,
        gaps_json=gaps_json,
    )
    return VIBE_ENRICHMENT_SYSTEM, user


def build_triage_prompt(complaint: str, product_url: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for single-complaint triage."""
    user = SINGLE_TRIAGE_USER.format(
        complaint=complaint,
        product_url=product_url,
    )
    return SINGLE_TRIAGE_SYSTEM, user
