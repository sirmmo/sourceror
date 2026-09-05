"""Clients for the bibliographic services Sourceror consults.

All are free and keyless. Semantic Scholar is rate-limited hard without a key,
so it is used only as a fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from .http import request, try_json

MAILTO = "sourceror@example.org"   # Crossref/OpenAlex "polite pool" contact


# --------------------------------------------------------------------------
# MediaWiki
# --------------------------------------------------------------------------

def api_url(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def fetch_article(title: str, lang: str = "it") -> dict | None:
    """Raw wikitext + metadata for one article. Follows redirects."""
    data = try_json(api_url(lang), params={
        "action": "query", "prop": "revisions|pageprops|info",
        "rvprop": "content|timestamp|ids", "rvslots": "main",
        "titles": title, "redirects": 1, "format": "json", "formatversion": "2",
        "inprop": "url",
    })
    if not data or "query" not in data:
        return None
    pages = data["query"].get("pages") or []
    if not pages or pages[0].get("missing"):
        return None
    p = pages[0]
    rev = (p.get("revisions") or [{}])[0]
    return {
        "title": p.get("title"),
        "pageid": p.get("pageid"),
        "url": p.get("fullurl"),
        "lang": lang,
        "wikitext": rev.get("slots", {}).get("main", {}).get("content", ""),
        "revid": rev.get("revid"),
        "touched": rev.get("timestamp"),
        "wikidata": (p.get("pageprops") or {}).get("wikibase_item"),
        "redirected_from": (data["query"].get("redirects") or [{}])[0].get("from"),
    }


def search_titles(query: str, lang: str = "it", limit: int = 8) -> list[dict]:
    data = try_json(api_url(lang), params={
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": limit, "format": "json", "formatversion": "2"})
    if not data:
        return []
    return [{"title": s["title"], "snippet": re.sub(r"<[^>]+>", "", s.get("snippet", ""))}
            for s in data.get("query", {}).get("search", [])]


def citoid(url: str, lang: str = "en") -> dict | None:
    """Wikipedia's own Citoid service — a hosted Zotero translation server.
    Turns any URL/DOI/ISBN into structured citation metadata."""
    r = request("GET",
                f"https://{lang}.wikipedia.org/api/rest_v1/data/citation/"
                f"mediawiki/{quote(url, safe='')}", timeout=25)
    if r is None or r.status_code >= 400:
        return None
    try:
        items = r.json()
    except ValueError:
        return None
    return items[0] if isinstance(items, list) and items else None


# --------------------------------------------------------------------------
# Wikidata
# --------------------------------------------------------------------------

def wikidata_entity(qid: str) -> dict | None:
    data = try_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    if not data:
        return None
    return data.get("entities", {}).get(qid)


def wikidata_facts(qid: str) -> dict:
    """The few Wikidata bits that matter for sourcing: official site (to detect
    self-sourcing), aliases (better search terms), and 'described by source'."""
    ent = wikidata_entity(qid)
    if not ent:
        return {}
    claims = ent.get("claims", {})

    def vals(pid):
        out = []
        for c in claims.get(pid, []):
            dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(dv, dict):
                dv = dv.get("id") or dv.get("text") or dv.get("time")
            if dv:
                out.append(dv)
        return out

    labels = ent.get("labels", {})
    aliases = ent.get("aliases", {})
    return {
        "qid": qid,
        "label": (labels.get("it") or labels.get("en") or {}).get("value"),
        "labels": {k: v["value"] for k, v in labels.items() if k in ("it", "en", "de", "fr", "es")},
        "aliases": [a["value"] for lg in ("it", "en") for a in aliases.get(lg, [])][:12],
        "description": ((ent.get("descriptions", {}).get("it")
                         or ent.get("descriptions", {}).get("en") or {}).get("value")),
        "official_website": vals("P856"),
        "instance_of": vals("P31"),
        "described_by": vals("P1343"),
        "doi": vals("P356"),
        "isbn": vals("P212") + vals("P957"),
    }


# --------------------------------------------------------------------------
# Crossref
# --------------------------------------------------------------------------

def crossref_doi(doi: str) -> dict | None:
    data = try_json(f"https://api.crossref.org/works/{quote(doi, safe='')}",
                    params={"mailto": MAILTO})
    return (data or {}).get("message")


def crossref_search(query: str, rows: int = 8, filters: str = "") -> list[dict]:
    params = {"query.bibliographic": query, "rows": rows, "mailto": MAILTO,
              "select": "DOI,title,author,issued,container-title,type,publisher,"
                        "is-referenced-by-count,abstract,URL,subject,score"}
    if filters:
        params["filter"] = filters
    data = try_json("https://api.crossref.org/works", params=params)
    return (data or {}).get("message", {}).get("items", [])


def crossref_is_retracted(msg: dict) -> dict | None:
    """Crossref records a retraction as an update-to / relation on the work."""
    for u in msg.get("update-to", []) or []:
        if u.get("type") in ("retraction", "withdrawal", "removal"):
            return u
    for kind in ("is-retracted-by", "is-withdrawn-by"):
        if msg.get("relation", {}).get(kind):
            return {"type": kind}
    if msg.get("type") == "retraction":
        return {"type": "retraction"}
    return None


# --------------------------------------------------------------------------
# OpenAlex
# --------------------------------------------------------------------------

def openalex_doi(doi: str) -> dict | None:
    return try_json(f"https://api.openalex.org/works/doi:{quote(doi, safe='')}",
                    params={"mailto": MAILTO})


def openalex_search(query: str, per_page: int = 8,
                    filters: str | None = None) -> list[dict]:
    """Search titles and abstracts.

    OpenAlex's plain `search` parameter also matches full text, which makes it
    useless for our purpose: for "Aldo Moro" it ranks a superstring-theory paper
    first. The title_and_abstract filter keeps recall while cutting that noise.
    """
    q = query.replace(",", " ").strip()
    flt = f"title_and_abstract.search:{q}"
    if filters:
        flt = f"{flt},{filters}"
    data = try_json("https://api.openalex.org/works",
                    params={"filter": flt, "per-page": per_page,
                            "mailto": MAILTO})
    results = (data or {}).get("results", [])
    if not results and not filters:
        data = try_json("https://api.openalex.org/works",
                        params={"filter": f"title.search:{q}",
                                "per-page": per_page, "mailto": MAILTO})
        results = (data or {}).get("results", [])
    return results


def openalex_title_lookup(title: str) -> dict | None:
    """Find the published version of a paper given only its title —
    used to upgrade an academia.edu/ResearchGate citation."""
    data = try_json("https://api.openalex.org/works",
                    params={"filter": f"title.search:{title[:250]}",
                            "per-page": 3, "mailto": MAILTO})
    results = (data or {}).get("results", [])
    return results[0] if results else None


def openalex_summary(work: dict) -> dict:
    """Flatten the fields we score on."""
    if not work:
        return {}
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    authors = [a.get("author", {}).get("display_name")
               for a in (work.get("authorships") or [])[:6]]
    return {
        "id": work.get("id"),
        "api_relevance": work.get("relevance_score"),
        "doi": (work.get("doi") or "").replace("https://doi.org/", "") or None,
        "title": work.get("title") or work.get("display_name"),
        "year": work.get("publication_year"),
        "type": work.get("type"),
        "is_retracted": work.get("is_retracted", False),
        "is_paratext": work.get("is_paratext", False),
        "cited_by": work.get("cited_by_count", 0),
        "open_access": (work.get("open_access") or {}).get("is_oa", False),
        "oa_url": (work.get("best_oa_location") or {}).get("pdf_url")
                  or (work.get("open_access") or {}).get("oa_url"),
        "venue": src.get("display_name"),
        "venue_type": src.get("type"),
        "publisher": src.get("host_organization_name"),
        "in_doaj": src.get("is_in_doaj", False),
        "issn": (src.get("issn") or [None])[0],
        "authors": [a for a in authors if a],
        "landing": loc.get("landing_page_url"),
        "concepts": [c["display_name"] for c in (work.get("concepts") or [])[:5]],
    }


# --------------------------------------------------------------------------
# Books
# --------------------------------------------------------------------------

def openlibrary_search(query: str, limit: int = 6) -> list[dict]:
    data = try_json("https://openlibrary.org/search.json",
                    params={"q": query, "limit": limit,
                            "fields": "title,author_name,first_publish_year,"
                                      "isbn,publisher,key,edition_count,language"})
    return (data or {}).get("docs", [])


def openlibrary_isbn(isbn: str) -> dict | None:
    data = try_json(f"https://openlibrary.org/isbn/{isbn}.json")
    return data


# --------------------------------------------------------------------------
# Link health
# --------------------------------------------------------------------------

DEAD_MARKERS = re.compile(
    r"(page not found|404 not found|non trovata|pagina non trovata|"
    r"errore 404|this domain is for sale|domain is parked|"
    r"account suspended|sito non disponibile)", re.I)


@dataclass
class LinkHealth:
    url: str
    status: int | None = None
    ok: bool = False
    dead: bool = False
    redirect_to: str | None = None
    reason: str = ""
    archived: str | None = None
    archive_date: str | None = None


def check_link(url: str, want_archive: bool = True) -> LinkHealth:
    h = LinkHealth(url=url)
    r = request("HEAD", url, timeout=12, allow_redirects=True, cache=False)
    if r is None or r.status_code in (403, 405, 501) or r.status_code >= 500:
        r = request("GET", url, timeout=15, allow_redirects=True, cache=False,
                    stream=True)
    if r is None:
        h.dead, h.reason = True, "unreachable (DNS or connection failure)"
    else:
        h.status = r.status_code
        h.ok = r.status_code < 400
        if r.url and r.url.rstrip("/") != url.rstrip("/"):
            h.redirect_to = r.url
        if r.status_code in (404, 410):
            h.dead, h.reason = True, f"HTTP {r.status_code}"
        elif r.status_code == 403:
            h.reason = "HTTP 403 — blocks automated checks, verify by hand"
        elif r.status_code >= 400:
            h.reason = f"HTTP {r.status_code}"
        else:
            try:
                body = r.text[:4000] if hasattr(r, "text") else ""
                if DEAD_MARKERS.search(body):
                    h.dead, h.reason = True, "soft 404 (error page returned with HTTP 200)"
            except Exception:
                pass
    if want_archive and (h.dead or not h.ok):
        arc = wayback(url)
        if arc:
            h.archived, h.archive_date = arc.get("url"), arc.get("timestamp")
    return h


def wayback(url: str) -> dict | None:
    data = try_json("https://archive.org/wayback/available",
                    params={"url": url})
    snap = ((data or {}).get("archived_snapshots") or {}).get("closest")
    if snap and snap.get("available"):
        ts = snap.get("timestamp", "")
        return {"url": snap.get("url"),
                "timestamp": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else ts}
    return None


def domain_of(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
        return host.split("@")[-1].split(":")[0]
    except Exception:
        return ""
