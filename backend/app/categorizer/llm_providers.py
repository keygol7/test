"""
LLM provider abstraction for article categorization.

Supports Anthropic (Claude) and OpenAI with a common interface.
Provider selected via LLM_PROVIDER config setting.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger("categorizer.llm")

CATEGORIZATION_PROMPT = """\
You are a news article categorizer. You will be given one or more articles and a list of \
situations (topics/queries) that users are tracking. Your job is to determine which situations \
each article is relevant to.

## Situations being tracked:
{situations_block}

## Articles to categorize:
{articles_block}

## Instructions:
For each article, determine which situations it matches. Consider:
- The situation's title, description, and query keywords
- Semantic relevance, not just keyword overlap
- An article can match zero, one, or multiple situations

Return ONLY valid JSON with this exact structure (no markdown, no extra text):
{{
  "results": [
    {{
      "article_id": "<feed_article_id>",
      "matches": [
        {{
          "situation_id": "<situation uuid>",
          "relevance_score": <float 0.0-1.0>,
          "reason": "<1-2 sentence explanation>"
        }}
      ]
    }}
  ]
}}

Scoring guide:
- 0.9-1.0: Article is directly about this situation/topic
- 0.6-0.8: Article is closely related or has significant overlap
- 0.3-0.5: Article is tangentially related
- Below 0.3: Do not include as a match

Only include matches with relevance_score >= 0.3.
If an article matches no situations, return an empty matches array for it.
"""


@dataclass
class CategorizationMatch:
    situation_id: str
    relevance_score: float
    reason: str


@dataclass
class ArticleResult:
    article_id: str
    matches: list[CategorizationMatch] = field(default_factory=list)


@dataclass
class BatchResult:
    results: list[ArticleResult] = field(default_factory=list)


def _build_prompt(
    articles: list[dict],
    situations: list[dict],
) -> str:
    situations_block = "\n".join(
        f"- ID: {s['id']}\n  Title: {s['title']}\n  Description: {s.get('description') or 'N/A'}\n  Query: {s['query']}"
        for s in situations
    )
    articles_block = "\n".join(
        f"- ID: {a['id']}\n  Title: {a['title']}\n  Snippet: {a.get('snippet') or 'N/A'}\n  URL: {a['url']}"
        for a in articles
    )
    return CATEGORIZATION_PROMPT.format(
        situations_block=situations_block,
        articles_block=articles_block,
    )


def _parse_response(raw_text: str) -> BatchResult:
    """Parse LLM JSON response into BatchResult."""
    text = raw_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()

    data = json.loads(text)
    results = []
    for item in data.get("results", []):
        matches = [
            CategorizationMatch(
                situation_id=m["situation_id"],
                relevance_score=float(m["relevance_score"]),
                reason=m["reason"],
            )
            for m in item.get("matches", [])
        ]
        results.append(ArticleResult(article_id=item["article_id"], matches=matches))
    return BatchResult(results=results)


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    async def categorize_batch(
        self,
        articles: list[dict],
        situations: list[dict],
    ) -> BatchResult:
        """Categorize a batch of articles against all situations."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier string for tracking."""
        ...


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    def __init__(self, api_key: str, model: str = ""):
        import anthropic

        self._model = model or "claude-sonnet-4-20250514"
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    async def categorize_batch(
        self,
        articles: list[dict],
        situations: list[dict],
    ) -> BatchResult:
        prompt = _build_prompt(articles, situations)
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        log.debug("Anthropic response: %s", raw[:500])
        return _parse_response(raw)


class OpenAIProvider(LLMProvider):
    """OpenAI provider."""

    def __init__(self, api_key: str, model: str = ""):
        import openai

        self._model = model or "gpt-4o-mini"
        self._client = openai.AsyncOpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    async def categorize_batch(
        self,
        articles: list[dict],
        situations: list[dict],
    ) -> BatchResult:
        prompt = _build_prompt(articles, situations)
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        log.debug("OpenAI response: %s", raw[:500])
        return _parse_response(raw)


def create_provider(provider_name: str, api_key: str, model: str = "") -> LLMProvider:
    """Factory to create the configured LLM provider."""
    providers = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
    }
    cls = providers.get(provider_name.lower())
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. Supported: {list(providers)}"
        )
    return cls(api_key=api_key, model=model)
