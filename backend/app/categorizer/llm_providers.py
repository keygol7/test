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
You are a news article categorizer. You will be given a batch of news articles and \
a list of existing situations (topics) that are already being tracked. Your job is to:

1. Match articles to existing situations where relevant
2. Discover NEW broad situations/topics from articles that don't fit existing ones
3. Every article should be assigned to at least one situation (existing or new)

## Existing situations being tracked:
{situations_block}

## Articles to categorize:
{articles_block}

## CRITICAL Instructions for creating NEW situations:
- Situations must be BROAD topics, not narrow subtopics
- GOOD examples: "Winter Olympics 2026", "US Immigration Policy", "AI Industry", "Middle East Conflict"
- BAD examples: "Olympic Speed Skating Medal Results", "H-1B Visa Processing Delays", "OpenAI Board Drama"
- Think of situations as ongoing news THEMES that will have many articles over days/weeks
- If multiple articles cover different angles of the same broad topic, group them under ONE broad situation
- Only propose a new situation if you believe multiple articles in this batch relate to it
- When in doubt, make the situation BROADER rather than narrower

## General instructions:
- Reuse an existing situation (by its ID) when an article clearly fits
- Give each new situation a clear, concise title (2-4 words preferred)
- Give each new situation a 1-2 sentence description and a search query
- Each article MUST be assigned to at least one situation
- An article can match multiple situations

Return ONLY valid JSON with this exact structure (no markdown, no extra text):
{{
  "new_situations": [
    {{
      "temp_id": "new_1",
      "title": "Broad Topic Title",
      "description": "1-2 sentence description of this news situation",
      "query": "search keywords for this topic"
    }}
  ],
  "results": [
    {{
      "article_id": "<feed_article_id>",
      "matches": [
        {{
          "situation_id": "<existing situation UUID or temp_id like new_1>",
          "relevance_score": 0.85,
          "reason": "1-2 sentence explanation"
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
If new_situations is empty (all articles fit existing situations), return an empty array for it.
"""


@dataclass
class NewSituation:
    temp_id: str
    title: str
    description: str
    query: str


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
    new_situations: list[NewSituation] = field(default_factory=list)


def _build_prompt(
    articles: list[dict],
    situations: list[dict],
) -> str:
    if situations:
        situations_block = "\n".join(
            f"- ID: {s['id']}\n  Title: {s['title']}\n  Description: {s.get('description') or 'N/A'}\n  Query: {s['query']}"
            for s in situations
        )
    else:
        situations_block = "(No existing situations yet — create new ones for all articles)"

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

    # Parse new situations
    new_situations = []
    for ns in data.get("new_situations", []):
        new_situations.append(NewSituation(
            temp_id=ns["temp_id"],
            title=ns["title"],
            description=ns.get("description", ""),
            query=ns.get("query", ns["title"]),
        ))

    # Parse article results
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
    return BatchResult(results=results, new_situations=new_situations)


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
            max_tokens=8192,
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
            max_tokens=8192,
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
