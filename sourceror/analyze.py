"""Orchestration: fetch an article, audit its citations, find what's missing."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from . import apis, cite, discover, wikitext
from .audit import Auditor, CitationReport
from .reliability import Reliability

SEVERITIES = ("critical", "serious", "caution", "info", "good")
# Explanatory notes are commentary, not sourcing claims, so they are excluded
# from the "how well sourced is this article" arithmetic. An empty <ref> stays
# in: it is a broken citation, not a non-citation.
NON_SOURCE_KINDS = {"note"}


def analyze(title: str, lang: str = "it", *, rel: Reliability,
            check_links: bool = True, discover_sources: bool = True,
            use_llm: bool = True, max_workers: int = 14, progress=None) -> dict:
    """progress(done, total, phase) is called as the run advances; checking
    ~200 links takes half a minute, so the caller needs to show movement."""
    def step(done, total, phase):
        if progress:
            try:
                progress(done, total, phase)
            except Exception:
                pass

    t0 = time.time()
    step(0, 1, "fetching")
    page = apis.fetch_article(title, lang)
    if not page:
        return {"error": "not_found", "title": title, "lang": lang,
                "suggestions": apis.search_titles(title, lang)}

    parsed = wikitext.parse_page(page["wikitext"])
    facts = apis.wikidata_facts(page["wikidata"]) if page.get("wikidata") else {}

    auditor = Auditor(rel, lang, bibliography=parsed["bibliography"])
    ctx = {"check_links": check_links,
           "official_websites": facts.get("official_website") or []}

    reports: list[CitationReport] = []
    total = max(1, len(parsed["refs"]))
    step(0, total, "checking citations")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(auditor.audit, r, ctx) for r in parsed["refs"]]
        for n, f in enumerate(as_completed(futures), 1):
            try:
                reports.append(f.result())
            except Exception as exc:          # one bad ref must not kill the run
                reports.append(_error_report(exc))
            step(n, total, "checking citations")
    reports.sort(key=lambda r: r.index)

    step(0, 1, "searching for sources")
    suggestions: list[dict] = []
    if discover_sources:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_gaps = ex.submit(_build_gaps, parsed, page, rel, lang, True, use_llm)
            f_sugg = ex.submit(discover.for_article, page, facts, parsed, rel,
                               lang, 8, use_llm)
            try:
                gaps = f_gaps.result()
            except Exception:
                gaps = _build_gaps(parsed, page, rel, lang, False)
            try:
                suggestions = f_sugg.result()
            except Exception:
                suggestions = []
    else:
        gaps = _build_gaps(parsed, page, rel, lang, False)

    return {
        "page": {k: page[k] for k in ("title", "url", "lang", "revid", "wikidata")},
        "redirected_from": page.get("redirected_from"),
        "wikidata": facts,
        "stats": _stats(reports, parsed),
        "citations": [_report_dict(r, lang) for r in reports],
        "gaps": gaps,
        "suggestions": suggestions,
        "sections": [{"title": s.title, "level": s.level, "refs": s.ref_count,
                      "chars": s.char_count}
                     for s in parsed["sections"] if s.char_count > 0],
        "bibliography": [{"template": t.name, "raw": t.raw[:400]}
                         for t in parsed["bibliography"]],
        "elapsed": round(time.time() - t0, 1),
    }


def _error_report(exc: Exception) -> CitationReport:
    r = CitationReport(index=0, section="", kind="error", name=None, reused=1,
                       urls=[], domain=None, doi=None, isbn=None,
                       display=f"check failed: {exc}", raw="")
    r.finalise()
    return r


def _build_gaps(parsed, page, rel, lang, do_discover, use_llm=True) -> list[dict]:
    """Places the article asserts something with no source behind it."""
    gaps = []
    for c in parsed["cn_tags"]:
        gaps.append({"type": "tagged", "template": c.template,
                     "reason": c.reason, "section": c.section,
                     "claim": c.claim, "candidates": []})
    for u in parsed["unsourced"]:
        gaps.append({"type": "unsourced_paragraph", "template": None,
                     "reason": None, "section": u["section"],
                     "claim": u["text"], "chars": u["chars"], "candidates": []})

    if do_discover and gaps:
        # Only hunt sources for explicitly tagged claims — searching for every
        # unsourced paragraph is slow and mostly noise.
        # Each claim costs up to two LLM round-trips, so the count is capped.
        tagged = [g for g in gaps if g["type"] == "tagged"][:4]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(discover.for_claim, g["claim"], page["title"],
                              rel, lang, 3, use_llm): g for g in tagged}
            for f, g in futs.items():
                try:
                    g["candidates"] = f.result()
                except Exception:
                    g["candidates"] = []
    return gaps


def _stats(reports, parsed) -> dict:
    scored = [r for r in reports if r.kind not in NON_SOURCE_KINDS]
    counts = {s: 0 for s in SEVERITIES}
    for r in reports:
        worst = r.flags[0].severity if r.flags else "good"
        counts[worst] = counts.get(worst, 0) + 1

    grades = {"solid": 0, "ok": 0, "weak": 0, "unreliable": 0}
    for r in scored:
        grades[r.grade] += 1

    dead = sum(1 for r in reports
               if any(f.code in ("DEAD_LINK", "DEAD_LINK_ARCHIVED", "SOFT_404")
                      for f in r.flags))
    prose = sum(s.char_count for s in parsed["sections"])
    return {
        "citations": len(reports),
        "scored": len(scored),
        "notes": sum(1 for r in reports if r.kind == "note"),
        "by_severity": counts,
        "by_grade": grades,
        "flagged": grades["weak"] + grades["unreliable"],
        "dead_links": dead,
        "cn_tags": len(parsed["cn_tags"]),
        "unsourced_paragraphs": len(parsed["unsourced"]),
        "prose_chars": prose,
        "refs_per_1k": round(len(scored) / (prose / 1000), 1) if prose > 400 else 0,
        "avg_score": round(sum(r.score for r in scored) / len(scored)) if scored else 0,
    }


def _report_dict(r: CitationReport, lang: str) -> dict:
    d = asdict(r)
    d["flags"] = [asdict(f) for f in r.flags]
    link = (r.metadata or {}).get("link") or {}
    # Turn a found archive into a paste-ready parameter string, but only when
    # the citation does not already carry one.
    if link.get("archived") and not link.get("has_archive"):
        d["archive_snippet"] = cite.archive_fix(
            r.urls[0] if r.urls else "", link["archived"],
            link.get("archive_date") or "", lang)
    if r.upgrade:
        d["upgrade_citation"] = cite.journal(r.upgrade, lang)
    return d


def build_citation(url: str, lang: str = "it") -> dict:
    """Turn a bare URL into a filled citation template via Citoid."""
    meta = apis.citoid(url, "en")
    if not meta:
        return {"error": "no_metadata",
                "detail": "Citoid could not extract metadata from this URL."}
    return {"itemType": meta.get("itemType"),
            "title": meta.get("title"),
            "citation": cite.from_citoid(meta, lang),
            "metadata": {k: v for k, v in meta.items()
                         if k in ("title", "date", "publicationTitle", "publisher",
                                  "websiteTitle", "DOI", "ISBN", "language", "url")}}
