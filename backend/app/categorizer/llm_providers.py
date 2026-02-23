"""
LLM provider abstraction for situation discovery and article categorization.

Supports Anthropic (Claude) and OpenAI with a common interface.
Provider selected via LLM_PROVIDER config setting.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger("categorizer.llm")

SITUATION_DISCOVERY_PROMPT = """\
You are a news situation discovery assistant. You will receive:
1) A list of existing situations already being tracked.
2) A compact list of article titles (ID + title only).

Your job is to propose only NEW broad situations that are not already covered.

Existing situations:
{situations_block}

Article titles:
{article_titles_block}

Rules:
- Propose BROAD ongoing topics, not narrow one-off story angles.
- Reuse existing situations conceptually; do not duplicate them.
- Only propose a situation if multiple titles clearly support it.
- For each proposed situation, include supporting_article_ids using ONLY IDs from the provided list.
- supporting_article_ids must be specific and should usually include at least 3 article IDs.

Return ONLY valid JSON (no markdown) in this exact structure:
{{
  "new_situations": [
    {{
      "temp_id": "new_1",
      "title": "Broad Topic Title",
      "description": "1-2 sentence description",
      "query": "search keywords",
      "supporting_article_ids": ["<article_id_1>", "<article_id_2>", "<article_id_3>"]
    }}
  ]
}}

If no new situations should be created, return:
{{"new_situations": []}}
"""

CATEGORIZATION_MATCH_PROMPT = """\
You are a news article categorizer. You will be given:
1) A batch of articles.
2) A list of existing situations (topics) already being tracked.

Your job is only to match each article to relevant EXISTING situations by ID.

Existing situations:
{situations_block}

Articles to categorize:
{articles_block}

Rules:
- Do NOT propose or invent new situations.
- Use only situation IDs from the existing situations list.
- Each article can match multiple situations.
- If an article does not match any existing situation, return an empty matches array for that article.
- Only include matches with relevance_score >= 0.3.

Scoring guide:
- 0.9-1.0: directly about this situation
- 0.6-0.8: closely related
- 0.3-0.5: tangentially related
- below 0.3: do not include

Return ONLY valid JSON (no markdown) in this exact structure:
{{
  "results": [
    {{
      "article_id": "<feed_article_id>",
      "matches": [
        {{
          "situation_id": "<existing situation UUID>",
          "relevance_score": 0.85,
          "reason": "1-2 sentence explanation"
        }}
      ]
    }}
  ]
}}
"""


@dataclass
class NewSituation:
    temp_id: str
    title: str
    description: str
    query: str
    supporting_article_ids: list[str] = field(default_factory=list)


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
    # Kept for robustness in case a model still emits this key unexpectedly.
    new_situations: list[NewSituation] = field(default_factory=list)


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _build_discovery_prompt(
    article_titles: list[dict],
    situations: list[dict],
) -> str:
    if situations:
        situations_block = "\n".join(
            f"- ID: {s['id']}\n  Title: {s['title']}\n  Description: {s.get('description') or 'N/A'}"
            for s in situations
        )
    else:
        situations_block = "(No existing situations currently tracked)"

    article_titles_block = "\n".join(
        f"- ID: {a['id']}\n  Title: {a.get('title') or 'N/A'}"
        for a in article_titles
    )
    return SITUATION_DISCOVERY_PROMPT.format(
        situations_block=situations_block,
        article_titles_block=article_titles_block,
    )


def _build_categorization_prompt(
    articles: list[dict],
    situations: list[dict],
) -> str:
    if situations:
        situations_block = "\n".join(
            f"- ID: {s['id']}\n  Title: {s['title']}\n  Description: {s.get('description') or 'N/A'}\n  Query: {s['query']}"
            for s in situations
        )
    else:
        situations_block = "(No existing situations. Return empty matches for every article.)"

    articles_block = "\n".join(
        f"- ID: {a['id']}\n  Title: {a['title']}\n  Snippet: {a.get('snippet') or 'N/A'}\n  URL: {a['url']}"
        for a in articles
    )
    return CATEGORIZATION_MATCH_PROMPT.format(
        situations_block=situations_block,
        articles_block=articles_block,
    )


def _parse_discovery_response(raw_text: str) -> list[NewSituation]:
    text = _strip_code_fences(raw_text)
    data = json.loads(text)

    new_situations: list[NewSituation] = []
    for idx, ns in enumerate(data.get("new_situations", []), start=1):
        temp_id = str(ns.get("temp_id") or f"new_{idx}")
        title = str(ns.get("title") or "").strip()
        if not title:
            continue
        description = str(ns.get("description") or "").strip()
        query = str(ns.get("query") or title).strip()
        supporting_article_ids = [
            str(article_id).strip()
            for article_id in ns.get("supporting_article_ids", [])
            if str(article_id).strip()
        ]
        new_situations.append(
            NewSituation(
                temp_id=temp_id,
                title=title,
                description=description,
                query=query,
                supporting_article_ids=supporting_article_ids,
            )
        )
    return new_situations


def _parse_categorization_response(raw_text: str) -> BatchResult:
    text = _strip_code_fences(raw_text)
    data = json.loads(text)

    new_situations: list[NewSituation] = []
    for idx, ns in enumerate(data.get("new_situations", []), start=1):
        title = str(ns.get("title") or "").strip()
        if not title:
            continue
        new_situations.append(
            NewSituation(
                temp_id=str(ns.get("temp_id") or f"new_{idx}"),
                title=title,
                description=str(ns.get("description") or "").strip(),
                query=str(ns.get("query") or title).strip(),
                supporting_article_ids=[
                    str(article_id).strip()
                    for article_id in ns.get("supporting_article_ids", [])
                    if str(article_id).strip()
                ],
            )
        )

    results: list[ArticleResult] = []
    for item in data.get("results", []):
        article_id = str(item.get("article_id") or "").strip()
        if not article_id:
            continue
        matches = [
            CategorizationMatch(
                situation_id=str(m["situation_id"]),
                relevance_score=float(m["relevance_score"]),
                reason=str(m.get("reason") or ""),
            )
            for m in item.get("matches", [])
            if "situation_id" in m and "relevance_score" in m
        ]
        results.append(ArticleResult(article_id=article_id, matches=matches))
    return BatchResult(results=results, new_situations=new_situations)


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    async def discover_situations(
        self,
        article_titles: list[dict],
        existing_situations: list[dict],
    ) -> list[NewSituation]:
        """Discover new broad situations from compact article titles."""
        ...

    @abstractmethod
    async def categorize_batch(
        self,
        articles: list[dict],
        situations: list[dict],
    ) -> BatchResult:
        """Match a batch of articles to existing situations."""
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

    async def discover_situations(
        self,
        article_titles: list[dict],
        existing_situations: list[dict],
    ) -> list[NewSituation]:
        prompt = _build_discovery_prompt(article_titles, existing_situations)
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        log.debug("Anthropic discovery response: %s", raw[:500])
        return _parse_discovery_response(raw)

    async def categorize_batch(
        self,
        articles: list[dict],
        situations: list[dict],
    ) -> BatchResult:
        prompt = _build_categorization_prompt(articles, situations)
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        log.debug("Anthropic categorization response: %s", raw[:500])
        return _parse_categorization_response(raw)


class OpenAIProvider(LLMProvider):
    """OpenAI provider."""

    def __init__(self, api_key: str, model: str = ""):
        import openai

        self._model = model or "gpt-4o-mini"
        self._client = openai.AsyncOpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    async def discover_situations(
        self,
        article_titles: list[dict],
        existing_situations: list[dict],
    ) -> list[NewSituation]:
        prompt = _build_discovery_prompt(article_titles, existing_situations)
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        log.debug("OpenAI discovery response: %s", raw[:500])
        return _parse_discovery_response(raw)

    async def categorize_batch(
        self,
        articles: list[dict],
        situations: list[dict],
    ) -> BatchResult:
        prompt = _build_categorization_prompt(articles, situations)
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        log.debug("OpenAI categorization response: %s", raw[:500])
        return _parse_categorization_response(raw)


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
