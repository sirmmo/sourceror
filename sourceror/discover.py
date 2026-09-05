"""Find sources: for the article as a whole, and for individual unsourced claims.

Query strategy: the article title alone is a poor query (Wikipedia titles are
disambiguated and terse), so we build terms from the Wikidata label, aliases and
description, plus the section heading or the text of the claim being sourced.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from . import apis, cite, llm
from .reliability import Reliability

# Wiki-markup and boilerplate words that pollute a search query.
STOP = set("""
the a an of and or in on at to for with from by as is are was were be been
il lo la i gli le un uno una di da del della dei delle in con su per tra fra
e ed che non si come anche più molto quando dove quale essere stato sono era
also known such which these those there their its his her they them been
""".split())


def build_queries(page: dict, facts: dict, parsed: dict, limit: int = 4) -> list[str]:
    """A handful of complementary search strings for the article as a whole."""
    title = re.sub(r"\s*\(.*?\)\s*$", "", page["title"]).strip()
    queries = [title]
    label = facts.get("label")
    if label and label.lower() != title.lower():
        queries.append(label)
    desc = facts.get("description")
    if desc:
        queries.append(f"{title} {desc}")
    for alias in (facts.get("aliases") or [])[:2]:
        if alias.lower() != title.lower():
            queries.append(alias)
    # Section headings often name the sub-topics that need sourcing.
    heads = [s.title for s in parsed["sections"]
             if s.level == 2 and s.char_count > 400
             and not re.match(r"^(note|bibliograf|collegamenti|voci|altri|"
                              r"references|external|see also|further)",
                              s.title.lower())]
    for h in heads[:2]:
        queries.append(f"{title} {h}")
    seen, out = set(), []
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower()); out.append(q)
    return out[:limit]


def claim_query(claim: str, title: str, max_terms: int = 9) -> str:
    """Turn a sentence needing a citation into a search query."""
    claim = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", claim)
    claim = re.sub(r"[^\w\sÀ-ÿ]", " ", claim)
    terms = [w for w in claim.split()
             if len(w) > 3 and w.lower() not in STOP]
    # Keep proper nouns, numbers and the longest content words.
    proper = [w for w in terms if w[:1].isupper()]
    numeric = [w for w in claim.split() if re.match(r"^\d{3,4}$", w)]
    rest = sorted((w for w in terms if w not in proper), key=len, reverse=True)
    picked = list(dict.fromkeys(proper[:5] + numeric[:2] + rest[:4]))[:max_terms]
    base = re.sub(r"\s*\(.*?\)\s*$", "", title).strip()
    return " ".join(dict.fromkeys([base] + picked))



def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[\wÀ-ÿ]{3,}", (text or "").lower())
            if w not in STOP}


def relevance(query: str, candidate: dict) -> float:
    """Share of the query's content words that appear in the candidate.

    Bibliographic APIs rank on full text and on fields we cannot see, so their
    own scores let plainly unrelated work through. Requiring the title (or
    venue) to actually contain the query's words is a far better filter.
    """
    q = _tokens(query)
    if not q:
        return 0.0
    hay = _tokens(f"{candidate.get('title','')} {candidate.get('venue') or ''}")
    if not hay:
        return 0.0
    cov = len(q & hay) / len(q)
    # An exact phrase match is decisive.
    if len(q) > 1 and query.lower().strip() in (candidate.get("title") or "").lower():
        cov = max(cov, 0.95)
    return cov


def _relevant_enough(query: str, candidate: dict) -> tuple[bool, float]:
    cov = relevance(query, candidate)
    n = len(_tokens(query))
    threshold = 0.5 if n <= 4 else 0.34
    return cov >= threshold and cov > 0, cov

def _score_candidate(c: dict, rel: Reliability) -> tuple[int, list[str]]:
    """Rank discovered sources: peer review, citations, recency, open access."""
    score, why = 50, []
    t = (c.get("type") or "").lower()
    if t in ("article", "journal-article", "review", "book-chapter", "book"):
        score += 20; why.append("peer-reviewed")
    if t in ("preprint", "posted-content"):
        score -= 15; why.append("preprint")
    if c.get("is_retracted"):
        return -100, ["RETRACTED"]
    cited = c.get("cited_by") or 0
    if cited >= 200: score += 18; why.append(f"{cited} citations")
    elif cited >= 50: score += 12; why.append(f"{cited} citations")
    elif cited >= 10: score += 6; why.append(f"{cited} citations")
    if c.get("in_doaj"): score += 6; why.append("DOAJ")
    if c.get("open_access"): score += 8; why.append("open access")
    year = c.get("year") or 0
    if year >= 2015: score += 5
    elif year and year < 1970: score -= 5
    if c.get("venue"):
        for v in rel.check_domain(str(c.get("venue")).lower().replace(" ", "")):
            if v.severity in ("critical", "serious"):
                score -= 40; why.append(f"⚠ {v.label}")
    if c.get("doi"):
        for v in rel.check_doi(c["doi"]):
            if v.severity == "critical":
                score -= 50; why.append(f"⚠ {v.label}")
            elif v.severity == "serious":
                score -= 25; why.append(f"⚠ {v.label}")
    return score, why


def academic(query: str, rel: Reliability, lang: str = "it",
             limit: int = 6) -> list[dict]:
    """Search OpenAlex and Crossref, merge on DOI, rank."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_oa = ex.submit(apis.openalex_search, query, limit + 4)
        f_cr = ex.submit(apis.crossref_search, query, limit + 2)
        oa, cr = f_oa.result(), f_cr.result()

    merged: dict[str, dict] = {}
    for w in oa:
        s = apis.openalex_summary(w)
        if not s.get("title"):
            continue
        merged[(s.get("doi") or s["title"]).lower()] = s
    for w in cr:
        doi = (w.get("DOI") or "").lower()
        title = (w.get("title") or [""])[0]
        if not title:
            continue
        key = doi or title.lower()
        if key in merged:
            continue
        merged[key] = {
            "doi": doi or None, "title": title,
            "venue": (w.get("container-title") or [None])[0],
            "year": ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0],
            "type": w.get("type"), "publisher": w.get("publisher"),
            "cited_by": w.get("is-referenced-by-count", 0),
            "authors": [f"{a.get('given','')} {a.get('family','')}".strip()
                        for a in (w.get("author") or [])[:6]],
            "open_access": False, "landing": w.get("URL"),
        }

    out = []
    for c in merged.values():
        keep, cov = _relevant_enough(query, c)
        if not keep:
            continue
        score, why = _score_candidate(c, rel)
        if score < 0:
            continue
        c = dict(c)
        # Relevance dominates: a highly-cited paper on another subject is worse
        # than a modest one that is actually about this.
        c["rank"] = round(score * (0.45 + 0.55 * cov))
        c["coverage"] = round(cov, 2)
        c["signals"] = why
        c["citation"] = cite.journal(c, lang)
        c["kind"] = "academic"
        out.append(c)
    out.sort(key=lambda x: -x["rank"])
    return out[:limit]


def books(query: str, lang: str = "it", limit: int = 4) -> list[dict]:
    out = []
    for d in apis.openlibrary_search(query, limit + 4):
        if not d.get("title"):
            continue
        keep, cov = _relevant_enough(query, {"title": d["title"]})
        if not keep:
            continue
        isbns = d.get("isbn") or []
        out.append({
            "kind": "book",
            "title": d["title"],
            "authors": d.get("author_name") or [],
            "year": d.get("first_publish_year"),
            "venue": (d.get("publisher") or [None])[0],
            "isbn": isbns[:1],
            "editions": d.get("edition_count", 0),
            "landing": f"https://openlibrary.org{d.get('key','')}",
            "rank": round((40 + min(20, (d.get("edition_count") or 0)))
                          * (0.45 + 0.55 * cov)),
            "coverage": round(cov, 2),
            "signals": ([f"{d['edition_count']} editions"]
                        if d.get("edition_count") else []),
            "citation": cite.book(d, lang),
        })
    out.sort(key=lambda x: -x["rank"])
    return out[:limit]


def for_article(page: dict, facts: dict, parsed: dict, rel: Reliability,
                lang: str = "it", limit: int = 8, use_llm: bool = True) -> list[dict]:
    queries = build_queries(page, facts, parsed)
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(academic, q, rel, lang, 5) for q in queries]
        futs.append(ex.submit(books, queries[0], lang, 4))
        for f in futs:
            try:
                for c in f.result():
                    key = (c.get("doi") or c.get("title", "")).lower()
                    if key and key not in results:
                        results[key] = c
            except Exception:
                continue
    out = sorted(results.values(), key=lambda x: -x["rank"])

    # Lexical overlap cannot tell a work about the subject from one that merely
    # uses its name as a metaphor ("Cancer Stem Cells the Loch Ness Monster").
    # Only a reader can, so use the LLM here when one is configured.
    if use_llm and out and llm.available():
        subject = page["title"]
        if facts.get("description"):
            subject += f" — {facts['description']}"
        try:
            verdicts = llm.rank_relevance(
                f"Sources for the Wikipedia article about {subject}", out[:12])
            for i, c in enumerate(out[:12]):
                v = verdicts.get(str(i))
                if not v:
                    continue
                c["relevance"] = v["verdict"]
                c["relevance_why"] = v["why"]
                c["rank"] += {"supports": 30, "related": 0, "off_topic": -70}.get(
                    v["verdict"], 0)
            out = [c for c in out if c.get("relevance") != "off_topic"]
            out.sort(key=lambda x: -x["rank"])
        except llm.LLMUnavailable:
            pass
    return out[:limit]


def for_claim(claim: str, title: str, rel: Reliability, lang: str = "it",
              limit: int = 4, use_llm: bool = True) -> list[dict]:
    """Search for sources that could support one specific claim.

    Keyword extraction gets the topic roughly right but often misses the point
    of the sentence, so when an LLM is configured we let it write the queries
    and then judge whether each hit actually bears on the claim."""
    queries = [claim_query(claim, title)]
    llm_used = False
    if use_llm and llm.available():
        try:
            refined = llm.refine_queries(claim, title, lang)
            if refined:
                queries = refined + queries[:1]
                llm_used = True
        except llm.LLMUnavailable:
            pass

    merged: dict[str, dict] = {}
    for q in queries[:3]:
        try:
            for c in academic(q, rel, lang, limit + 2):
                key = (c.get("doi") or c.get("title", "")).lower()
                if key and key not in merged:
                    c["query"] = q
                    merged[key] = c
        except Exception:
            continue
    out = sorted(merged.values(), key=lambda x: -x["rank"])

    if llm_used and out:
        try:
            verdicts = llm.rank_relevance(claim, out[:10])
            for i, c in enumerate(out[:10]):
                v = verdicts.get(str(i))
                if not v:
                    continue
                c["relevance"] = v["verdict"]
                c["relevance_why"] = v["why"]
                c["rank"] += {"supports": 40, "related": 0, "off_topic": -60}.get(
                    v["verdict"], 0)
            out = [c for c in out if c.get("relevance") != "off_topic"]
            out.sort(key=lambda x: -x["rank"])
        except llm.LLMUnavailable:
            pass
    return out[:limit]


def existing_domains(reports) -> set[str]:
    return {r.domain for r in reports if r.domain}
