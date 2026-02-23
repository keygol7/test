"""
Deterministic keyword matcher for situation assignment/backfill.

Stage 1 intentionally keeps matching logic simple and explicit:
- whole-word token matching on title/snippet
- small hardcoded denylist for generic terms
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GENERIC_QUERY_TOKEN_DENYLIST = {
    "policy",
    "international",
    "update",
    "news",
    "report",
    "latest",
    "breaking",
    "new",
    "says",
    "could",
    "today",
    "analysis",
    "official",
    "statement",
    "developments",
}


@dataclass(frozen=True)
class KeywordMatch:
    relevance_score: float
    reason: str
    matched_tokens: list[str]


def tokenize_query(query: str | None) -> list[str]:
    """Tokenize and normalize a situation query for deterministic matching."""
    if not query:
        return []
    tokens: list[str] = []
    for raw in query.split():
        token = re.sub(r"^\W+|\W+$", "", raw.lower())
        if not token or token in GENERIC_QUERY_TOKEN_DENYLIST:
            continue
        tokens.append(token)
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(tokens))


def _has_whole_word(text: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", text, flags=re.IGNORECASE) is not None


def match_article_to_query(
    title: str | None,
    snippet: str | None,
    query: str | None,
) -> KeywordMatch | None:
    """
    Return deterministic keyword match confidence for one article vs one query.

    Scoring:
    - Title hit: 0.75
    - Snippet-only with >=2 distinct token hits: 0.50
    """
    tokens = tokenize_query(query)
    if not tokens:
        return None

    title_text = title or ""
    snippet_text = snippet or ""

    title_hits: set[str] = set()
    snippet_hits: set[str] = set()

    for token in tokens:
        if title_text and _has_whole_word(title_text, token):
            title_hits.add(token)
            continue
        if snippet_text and _has_whole_word(snippet_text, token):
            snippet_hits.add(token)

    if title_hits:
        matched = sorted(title_hits)
        return KeywordMatch(
            relevance_score=0.75,
            reason=f"keyword:title:{','.join(matched)}",
            matched_tokens=matched,
        )

    if len(snippet_hits) >= 2:
        matched = sorted(snippet_hits)
        return KeywordMatch(
            relevance_score=0.50,
            reason=f"keyword:snippet:{','.join(matched)}",
            matched_tokens=matched,
        )

    return None
