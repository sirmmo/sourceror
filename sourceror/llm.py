"""Optional LLM assistance over any OpenAI-compatible endpoint (OpenRouter by
default).

Sourceror is fully functional without this. The LLM is used only where the
API-and-heuristics path is genuinely weak:

  * turning a prose claim into a good scholarly search query;
  * judging whether a candidate source actually supports the specific claim,
    rather than merely being about the same topic.

Every entry point falls back to the non-LLM behaviour on any failure.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import config
from .http import request


class LLMUnavailable(RuntimeError):
    pass


class Truncated(LLMUnavailable):
    """The token budget ran out before the model finished answering."""


def available() -> bool:
    cfg = config.load()
    return bool(cfg.get("llm_enabled") and cfg.get("llm_api_key")
                and cfg.get("llm_base_url") and cfg.get("llm_model"))


def _headers(cfg: dict) -> dict:
    h = {"Authorization": f"Bearer {cfg['llm_api_key']}",
         "Content-Type": "application/json"}
    if "openrouter" in (cfg.get("llm_base_url") or ""):
        # OpenRouter attributes requests to an app via these.
        h["HTTP-Referer"] = "https://github.com/sirmmo/sourceror"
        h["X-Title"] = "Sourceror"
    return h


def chat(messages: list[dict], *, json_mode: bool = False,
         max_tokens: int | None = None) -> str:
    cfg = config.load()
    if not available():
        raise LLMUnavailable("no API key configured")
    body = {
        "model": cfg["llm_model"],
        "messages": messages,
        "temperature": cfg.get("llm_temperature", 0.1),
        "max_tokens": max_tokens or cfg.get("llm_max_tokens", 1200),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = request("POST", cfg["llm_base_url"].rstrip("/") + "/chat/completions",
                headers=_headers(cfg), json=body,
                timeout=cfg.get("llm_timeout", 45), cache=False)
    if r is None:
        raise LLMUnavailable("network error reaching the LLM endpoint")
    if r.status_code >= 400:
        raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("error"):
        raise LLMUnavailable(str(data["error"])[:300])
    try:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
    except (KeyError, IndexError):
        raise LLMUnavailable(f"unexpected response shape: {str(data)[:200]}")
    content = (msg.get("content") or "").strip()
    if not content:
        # Reasoning models sometimes place the answer outside `content`.
        content = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
    if not content:
        if choice.get("finish_reason") == "length":
            # Reasoning models spend max_tokens on hidden reasoning before
            # emitting anything, so a budget that looks generous can yield an
            # empty answer. The caller retries with more room.
            raise Truncated(
                "the model used the whole token budget on reasoning and "
                "returned no content")
        raise LLMUnavailable(
            f"model returned an empty completion (finish_reason="
            f"{choice.get('finish_reason')!r}) — check the model id and that the "
            "endpoint supports chat completions")
    if choice.get("finish_reason") == "length":
        raise Truncated("the response was cut off by the token limit")
    return content


def chat_json(messages: list[dict], max_tokens: int | None = None,
              _retry: bool = True) -> dict:
    try:
        raw = chat(messages, json_mode=True, max_tokens=max_tokens)
    except Truncated:
        if not _retry:
            raise
        budget = (max_tokens or config.load().get("llm_max_tokens", 3000)) * 2
        return chat_json(messages, max_tokens=min(budget, 8000), _retry=False)
    except LLMUnavailable as e:
        # Not every model accepts response_format; a plain call usually works.
        if "response_format" not in str(e) and "json" not in str(e).lower():
            raise
        raw = chat(messages + [{"role": "system",
                                "content": "Respond with JSON only."}],
                   max_tokens=max_tokens)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)     # some models wrap it in prose
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise LLMUnavailable("model did not return valid JSON")


def list_models() -> list[dict]:
    cfg = config.load()
    if not cfg.get("llm_api_key") or not cfg.get("llm_base_url"):
        return []
    r = request("GET", cfg["llm_base_url"].rstrip("/") + "/models",
                headers=_headers(cfg), timeout=20)
    if r is None or r.status_code >= 400:
        return []
    try:
        data = r.json().get("data", [])
    except ValueError:
        return []
    out = []
    for m in data:
        out.append({"id": m.get("id"),
                    "name": m.get("name") or m.get("id"),
                    "context": m.get("context_length")})
    return sorted([m for m in out if m["id"]], key=lambda m: m["id"])


def test() -> dict:
    model = config.load().get("llm_model")
    try:
        # Enough headroom that a reasoning model does not spend the whole
        # budget thinking and return nothing.
        reply = chat([{"role": "user",
                       "content": "Reply with the single word: ok"}],
                     max_tokens=2000).strip()
    except LLMUnavailable as e:
        return {"ok": False, "error": str(e), "model": model}
    return {"ok": True, "reply": reply[:120], "model": model}


# ---------------------------------------------------------------- features

QUERY_SYS = """You turn a sentence from a Wikipedia article into search queries \
for academic and news databases (OpenAlex, Crossref).

Rules:
- Produce queries a librarian would use: the key entities, events, dates and \
technical terms, WITHOUT boolean operators, quotes or field prefixes.
- Prefer the scholarly or historical register over Wikipedia's phrasing.
- If the claim names a person, place, work or date, keep it.
- Give one query in the article's own language and one in English.
Return JSON: {"queries": ["...", "..."], "topic": "<3-6 word topic>"}"""


def refine_queries(claim: str, article_title: str, lang: str) -> list[str]:
    """Better search strings than keyword extraction can produce."""
    out = chat_json([
        {"role": "system", "content": QUERY_SYS},
        {"role": "user", "content":
            f"Wikipedia article: {article_title} ({lang}.wikipedia)\n"
            f"Claim needing a source:\n{claim[:900]}"}],
        max_tokens=1500)
    qs = [q.strip() for q in (out.get("queries") or []) if isinstance(q, str) and q.strip()]
    return qs[:3]


RELEVANCE_SYS = """You judge whether a source could support a specific factual \
claim from a Wikipedia article. You see only bibliographic metadata, so judge \
plausibility, not proof.

For each candidate return:
  "id": the given id
  "verdict": "supports" | "related" | "off_topic"
  "why": at most 8 words

"supports"  - the work is squarely about this claim's subject and period, and a \
reader would plausibly find the claim in it.
"related"   - same broad topic, but would not directly establish the claim.
"off_topic" - a keyword coincidence.

Be strict: most search results are "related" at best.
Return JSON: {"results": [...]}"""


BATCH = 4


def _rank_batch(claim: str, batch: list[tuple[int, dict]]) -> dict[str, dict]:
    listing = []
    for i, c in batch:
        listing.append(
            f"[{i}] title: {c.get('title')}\n"
            f"    venue: {c.get('venue') or '-'} ({c.get('year') or '-'})\n"
            f"    authors: {', '.join((c.get('authors') or [])[:3]) or '-'}")
    out = chat_json([
        {"role": "system", "content": RELEVANCE_SYS},
        {"role": "user", "content":
            f"Claim:\n{claim[:700]}\n\nCandidates:\n" + "\n".join(listing)}],
        max_tokens=1800)
    mapped: dict[str, dict] = {}
    valid = {i for i, _ in batch}
    for r in out.get("results") or []:
        try:
            idx = int(str(r.get("id")).strip("[]"))
        except (TypeError, ValueError):
            continue
        if idx in valid:
            mapped[str(idx)] = {"verdict": r.get("verdict", "related"),
                                "why": (r.get("why") or "")[:120]}
    return mapped


def rank_relevance(claim: str, candidates: list[dict]) -> dict[str, dict]:
    """Map candidate id -> {verdict, why}. Empty dict when unavailable.

    Judging 12 candidates in one request costs about two minutes on a reasoning
    model, because the hidden reasoning grows with the batch. Small batches run
    concurrently instead, so wall time is roughly that of one batch.
    """
    if not candidates:
        return {}
    indexed = list(enumerate(candidates))
    batches = [indexed[i:i + BATCH] for i in range(0, len(indexed), BATCH)]
    mapped: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as ex:
        futs = [ex.submit(_rank_batch, claim, b) for b in batches]
        for f in futs:
            try:
                mapped.update(f.result())
            except LLMUnavailable:
                continue          # that batch keeps its lexical ranking
    if not mapped:
        raise LLMUnavailable("every relevance batch failed")
    return mapped


CLAIM_SYS = """You extract, from a Wikipedia paragraph, the specific factual \
statements that would need an inline citation under WP:V.

Skip: definitions, obvious background, statements already attributed in text, \
and pure navigation. Prefer statements with numbers, dates, names, \
attributions, superlatives or causal claims.
Return JSON: {"claims": [{"text": "<the sentence, verbatim>", \
"why": "<why it needs a source, max 12 words>"}]}  (at most 4)"""


def extract_claims(paragraph: str, article_title: str) -> list[dict]:
    out = chat_json([
        {"role": "system", "content": CLAIM_SYS},
        {"role": "user", "content":
            f"Article: {article_title}\n\nParagraph:\n{paragraph[:2500]}"}],
        max_tokens=2500)
    return [c for c in (out.get("claims") or []) if c.get("text")][:4]
