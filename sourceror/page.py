"""Analyse any web article — news, blog, report — not just Wikipedia.

The browser extension does the extraction: it reads the page as the reader
sees it (past paywalls, cookie walls and bot blocks a server fetch would hit),
isolates the article body, and sends its paragraphs and outbound links. Each
paragraph also comes as `marked` text, with a `[L<id>]` token after every link,
so the claim extractor can tell which statement a link is attached to.

From there the same machinery as for Wikipedia applies: every linked source is
audited for reliability, and statements that rely on nothing are found and
given candidate sources.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import apis, discover, llm, wikitext
from .analyze import NON_SOURCE_KINDS, SEVERITIES, _error_report, _report_dict
from .audit import Auditor, Flag
from .reliability import Reliability

MAX_LINKS = 150
MAX_PARAGRAPHS = 120
LLM_CHARS = 24000          # how much article text the claim extractor sees
DISCOVER_CLAIMS = 4        # each one costs up to two LLM round-trips

# Links that are page furniture rather than sources.
SHARE_RE = re.compile(
    r"(facebook\.com/(sharer|share\.php|dialog)|twitter\.com/(intent|share)|"
    r"x\.com/intent|linkedin\.com/(share|sharing|shareArticle)|"
    r"pinterest\.[a-z.]+/pin/create|reddit\.com/submit|t\.me/share|"
    r"wa\.me/|api\.whatsapp\.com/send|telegram\.me/share|mailto:|tel:|"
    r"javascript:)", re.I)
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_[ce]id$|ref$|ref_src$|"
                             r"cmpid$|ocid$|smid$|xtor$)", re.I)
DOI_URL_RE = re.compile(r"^https?://(dx\.)?doi\.org/(10\.\d{4,9}/\S+)$", re.I)
MARKER_RE = re.compile(r"\s*\[L(\d+)\]")

# Heuristic claim detection, used when no LLM is configured.
CLAIMY_RE = re.compile(
    r"\d|%|\b(stud(y|ies|io|i)|research|ricerc[ah]e?|report|rapporto|survey|"
    r"sondaggio|data|dati|statistic|statistich|according|secondo|percent|"
    r"per cento|million|milion|billion|miliard|record|first|primo|prima|"
    r"largest|biggest|più grande|maggiore|increase|decrease|aumento|calo|"
    r"doubled|raddoppiat|caused|causato|because|perché|found that|ha rivelato|"
    r"scientists|scienziati|experts|esperti)\b", re.I)
ATTRIBUTION_RE = re.compile(
    r"\b(according to|said|says|told|reported|reports|announced|stated|"
    r"secondo|ha detto|ha dichiarato|ha spiegato|ha affermato|riporta|"
    r"riferisce|ha annunciato|fa sapere|dichiara|afferma)\b", re.I)
SENTENCE_RE = re.compile(r"(?<=[.!?…])[\"”»']?\s+(?=[\"“«(]?[A-ZÀ-Ý0-9])")


def analyze_page(payload: dict, lang: str = "it", *, rel: Reliability,
                 check_links: bool = True, discover_sources: bool = True,
                 use_llm: bool = True, max_workers: int = 14,
                 progress=None) -> dict:
    def step(done, total, phase):
        if progress:
            try:
                progress(done, total, phase)
            except Exception:
                pass

    t0 = time.time()
    url = (payload.get("url") or "").strip()
    title = (payload.get("title") or "").strip() or url
    page_domain = _bare(apis.domain_of(url)) if url else ""
    paragraphs = _paragraphs(payload)
    links = clean_links(payload.get("links") or [], url)

    auditor = Auditor(rel, lang)
    ctx = {"check_links": check_links}
    refs = _refs(links, paragraphs)
    total = max(1, len(refs))
    reports = []
    step(0, total, "checking sources")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(auditor.audit, r, ctx): r for r in refs}
        for n, f in enumerate(as_completed(futures), 1):
            ref = futures[f]
            try:
                rep = f.result()
            except Exception as exc:          # one bad link must not kill the run
                rep = _error_report(exc)
                rep.index = ref.index
            _annotate(rep, ref, page_domain)
            reports.append(rep)
            step(n, total, "checking sources")
    reports.sort(key=lambda r: r.index)
    by_index = {x.index: x for x in refs}
    link_ids = {n: l["ids"] for n, l in enumerate(links)}
    sources = [_source_dict(r, lang, by_index.get(r.index), link_ids.get(r.index, []))
               for r in reports]

    step(0, 1, "reading claims")
    claims, claims_by = _claims(paragraphs, title, use_llm)
    _attach_sources(claims, sources)

    if discover_sources:
        step(0, 1, "searching for sources")
        _discover(claims, title, rel, lang, use_llm)

    return {
        "page": {"url": url, "title": title, "domain": page_domain,
                 "site_name": payload.get("site_name"),
                 "byline": payload.get("byline"),
                 "published": payload.get("published"), "lang": lang},
        "outlet": outlet(rel, page_domain),
        "stats": _stats(reports, sources, claims, paragraphs),
        "sources": sources,
        "claims": claims,
        "claims_by": claims_by,
        "elapsed": round(time.time() - t0, 1),
    }


# ------------------------------------------------------------------ input

def _paragraphs(payload: dict) -> list[dict]:
    out = []
    for p in (payload.get("paragraphs") or [])[:MAX_PARAGRAPHS]:
        if isinstance(p, str):
            p = {"text": p}
        text = re.sub(r"\s+", " ", str(p.get("text") or "")).strip()
        if not text:
            continue
        marked = re.sub(r"\s+", " ", str(p.get("marked") or text)).strip()
        # Keep the extension's numbering: links and highlights refer to it.
        i = p.get("i")
        out.append({"i": i if isinstance(i, int) else len(out),
                    "text": text, "marked": marked})
    return out


def clean_links(links: list[dict], page_url: str = "") -> list[dict]:
    """Drop share buttons and anchors, strip tracking parameters, merge
    duplicates (keeping every place the link appears)."""
    page_norm = normalise_url(page_url) if page_url else ""
    merged: dict[str, dict] = {}
    for l in links:
        href = str(l.get("href") or "").strip()
        if not re.match(r"^https?://", href, re.I) or SHARE_RE.search(href):
            continue
        norm = normalise_url(href)
        if not norm or norm == page_norm:
            continue
        entry = merged.get(norm)
        if entry is None:
            entry = merged[norm] = {"id": l.get("id"), "href": norm,
                                    "text": re.sub(r"\s+", " ", str(l.get("text") or "")).strip()[:200],
                                    "ids": [], "paragraphs": []}
        entry["ids"].append(l.get("id"))
        if l.get("p") is not None and l["p"] not in entry["paragraphs"]:
            entry["paragraphs"].append(l["p"])
        if not entry["text"] and l.get("text"):
            entry["text"] = str(l["text"]).strip()[:200]
    return list(merged.values())[:MAX_LINKS]


def normalise_url(url: str) -> str:
    try:
        s = urlsplit(url.strip())
    except ValueError:
        return ""
    if not s.netloc:
        return ""
    query = urlencode([(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
                       if not TRACKING_PARAMS.match(k)])
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path or "/", query, ""))


def _refs(links: list[dict], paragraphs: list[dict]) -> list[wikitext.Ref]:
    """Wrap each link (and each DOI written out in the text) as a Ref, so the
    Wikipedia auditor can judge it unchanged."""
    refs = []
    seen_dois = set()
    for n, l in enumerate(links):
        m = DOI_URL_RE.match(l["href"])
        doi = m.group(2).rstrip(".,;)").lower() if m else None
        if doi:
            seen_dois.add(doi)
        p = l["paragraphs"][0] if l["paragraphs"] else None
        refs.append(wikitext.Ref(
            index=n, raw=l["href"], inner=l["text"], name=None, start=0, end=0,
            section=f"¶{p + 1}" if p is not None else "", kind="link",
            urls=[l["href"]], doi=doi, reused=len(l["ids"])))
    for p in paragraphs:
        for m in wikitext.DOI_RE.finditer(p["text"]):
            doi = m.group(0).rstrip(".,;)").lower()
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
            refs.append(wikitext.Ref(
                index=len(refs), raw=doi, inner=f"doi:{doi}", name=None,
                start=0, end=0, section=f"¶{p['i'] + 1}", kind="doi", doi=doi))
    return refs


# --------------------------------------------------------------- sources

def _annotate(rep, ref, page_domain: str) -> None:
    rep.display = (ref.inner or rep.domain or ref.raw)[:180]
    if ref.kind == "link" and page_domain and rep.domain \
            and _bare(rep.domain).endswith(page_domain):
        rep.add(Flag("SAME_SITE", "info", "Links to the publisher's own content",
                     "The link points back to the same site. That is navigation "
                     "or earlier coverage, not independent confirmation.",
                     "Look for the original source the earlier piece relied on."))
        rep.finalise()


def _source_dict(rep, lang: str, ref, link_ids: list) -> dict:
    d = _report_dict(rep, lang)
    d.pop("raw", None)
    d["link_ids"] = [i for i in link_ids if i is not None]
    d["anchor"] = ref.inner if ref else ""
    d["href"] = ref.urls[0] if ref and ref.urls else None
    d["same_site"] = any(f.code == "SAME_SITE" for f in rep.flags)
    return d


def outlet(rel: Reliability, domain: str) -> dict:
    if not domain:
        return {"domain": None, "verdict": "unlisted", "verdicts": []}
    vs = rel.check_domain(domain)
    return {"domain": domain,
            "verdict": vs[0].severity if vs else "unlisted",
            "verdicts": [{"severity": v.severity, "label": v.label,
                          "name": v.name, "detail": (v.detail or "")[:600],
                          "url": v.url} for v in vs]}


def _bare(domain: str) -> str:
    domain = (domain or "").lower()
    return domain[4:] if domain.startswith("www.") else domain


# ---------------------------------------------------------------- claims

PAGE_CLAIM_SYS = """You read paragraphs of a published article (news, blog, \
report) and list the factual statements a careful reader would want to check.

Each paragraph is numbered [P<n>]. Hyperlinks appear as [L<id>] right after the \
linked words.

For each statement return:
  "p": the paragraph number
  "text": the sentence, copied VERBATIM from the paragraph, without [L..] tokens
  "status": "linked"     - a link in the same sentence backs it (list the ids)
            "attributed" - attributed in the text to a named person, body or \
document, but not linked
            "unsourced"  - presented as fact, with no link and no attribution
  "links": the [L..] ids backing it, as integers (empty unless "linked")
  "attribution": who it is attributed to, for "attributed"; otherwise ""
  "why": why it needs checking, max 10 words

Prefer statements with numbers, dates, statistics, study results, records, \
quotes, superlatives or causal claims. Skip opinion clearly marked as such, \
background everybody knows, and navigation text.
Write "why" in the language of the article.
Return JSON: {"claims": [...]} — at most 6 per request, most checkable first."""


def _claims(paragraphs: list[dict], title: str, use_llm: bool):
    if use_llm and llm.available() and paragraphs:
        try:
            claims = _llm_claims(paragraphs, title)
            if claims:
                return claims, "llm"
        except llm.LLMUnavailable:
            pass
    return heuristic_claims(paragraphs), "heuristic"


def _llm_claims(paragraphs: list[dict], title: str) -> list[dict]:
    chunks, cur, size, budget = [], [], 0, LLM_CHARS
    for p in paragraphs:
        if budget <= 0:
            break
        if len(p["marked"]) < 60:          # captions, datelines, bylines
            continue
        cur.append(p)
        size += len(p["marked"])
        budget -= len(p["marked"])
        if size > 3500:
            chunks.append(cur); cur, size = [], 0
    if cur:
        chunks.append(cur)
    if not chunks:
        return []

    def run(chunk):
        body = "\n\n".join(f"[P{p['i']}] {p['marked'][:2500]}" for p in chunk)
        out = llm.chat_json([
            {"role": "system", "content": PAGE_CLAIM_SYS},
            {"role": "user", "content": f"Article: {title}\n\n{body}"}],
            max_tokens=3000)
        return out.get("claims") or []

    raw: list[dict] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
        for f in [ex.submit(run, c) for c in chunks]:
            try:
                raw.extend(f.result())
            except llm.LLMUnavailable:
                failures += 1
    if failures == len(chunks):
        raise llm.LLMUnavailable("every claim batch failed")

    by_i = {p["i"]: p for p in paragraphs}
    out = []
    for c in raw:
        try:
            pi = int(str(c.get("p")).lstrip("P"))
        except (TypeError, ValueError):
            continue
        text = MARKER_RE.sub("", str(c.get("text") or "")).strip()
        if pi not in by_i or len(text) < 15:
            continue
        status = c.get("status") if c.get("status") in ("linked", "attributed",
                                                         "unsourced") else "unsourced"
        ids = []
        for x in c.get("links") or []:
            try:
                ids.append(int(str(x).lstrip("L")))
            except (TypeError, ValueError):
                continue
        if status == "linked" and not ids:
            status = "unsourced"
        out.append(_claim(len(out), pi, text, status, ids,
                          str(c.get("attribution") or "")[:120],
                          str(c.get("why") or "")[:120],
                          verbatim=_norm(text) in _norm(by_i[pi]["text"])))
    return out


def heuristic_claims(paragraphs: list[dict], limit: int = 15) -> list[dict]:
    """Sentences that look like checkable facts, without an LLM: numbers,
    research words, superlatives. Coarse but transparent."""
    out = []
    for p in paragraphs:
        if len(p["text"]) < 60:
            continue
        for sent in SENTENCE_RE.split(p["marked"]):
            ids = [int(x) for x in MARKER_RE.findall(sent)]
            text = MARKER_RE.sub("", sent).strip()
            if len(text) < 40 or not CLAIMY_RE.search(text):
                continue
            if ids:
                status = "linked"
            elif ATTRIBUTION_RE.search(text):
                status = "attributed"
            else:
                status = "unsourced"
            out.append(_claim(len(out), p["i"], text, status, ids, "", "",
                              verbatim=True))
            if len(out) >= limit:
                return out
    return out


def _claim(n, p, text, status, ids, attribution, why, verbatim) -> dict:
    return {"id": n, "p": p, "text": text, "status": status, "links": ids,
            "attribution": attribution, "why": why, "verbatim": verbatim,
            "sources": [], "source_grade": None, "candidates": []}


def _attach_sources(claims: list[dict], sources: list[dict]) -> None:
    """Point each linked claim at the audited sources behind it, and let the
    weakest of them speak for the claim: a statistic linked to a deprecated
    site is not really sourced."""
    by_link_id: dict[int, int] = {}
    for n, s in enumerate(sources):
        for lid in s.get("link_ids") or []:
            by_link_id[lid] = n
    order = {"unreliable": 0, "weak": 1, "ok": 2, "solid": 3}
    for c in claims:
        idx = [by_link_id[i] for i in c["links"] if i in by_link_id]
        c["sources"] = list(dict.fromkeys(idx))
        grades = [sources[i]["grade"] for i in c["sources"]]
        if grades:
            c["source_grade"] = min(grades, key=lambda g: order.get(g, 9))
            if c["source_grade"] in ("weak", "unreliable"):
                c["status"] = "weakly_linked"


def _discover(claims, title, rel, lang, use_llm) -> None:
    todo = [c for c in claims if c["status"] in ("unsourced", "weakly_linked")]
    # Numbers and statistics first: they are the easiest to verify or refute.
    todo.sort(key=lambda c: (c["status"] != "unsourced",
                             not re.search(r"\d", c["text"])))
    todo = todo[:DISCOVER_CLAIMS]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(discover.for_claim, c["text"], title, rel, lang, 3,
                          use_llm): c for c in todo}
        for f, c in futs.items():
            try:
                c["candidates"] = f.result()
            except Exception:
                c["candidates"] = []


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


# ----------------------------------------------------------------- stats

def _stats(reports, sources, claims, paragraphs) -> dict:
    scored = [r for r in reports if r.kind not in NON_SOURCE_KINDS]
    grades = {"solid": 0, "ok": 0, "weak": 0, "unreliable": 0}
    for r in scored:
        grades[r.grade] = grades.get(r.grade, 0) + 1
    counts = {s: 0 for s in SEVERITIES}
    for r in reports:
        worst = r.flags[0].severity if r.flags else "good"
        counts[worst] = counts.get(worst, 0) + 1
    status = {"linked": 0, "weakly_linked": 0, "attributed": 0, "unsourced": 0}
    for c in claims:
        status[c["status"]] = status.get(c["status"], 0) + 1
    words = sum(len(p["text"].split()) for p in paragraphs)
    external = [s for s in sources if not s["same_site"]]
    return {
        "sources": len(sources),
        "external_sources": len(external),
        "same_site_links": len(sources) - len(external),
        "by_grade": grades,
        "by_severity": counts,
        "flagged": grades["weak"] + grades["unreliable"],
        "dead_links": sum(1 for r in reports if any(
            f.code in ("DEAD_LINK", "DEAD_LINK_ARCHIVED", "SOFT_404") for f in r.flags)),
        "claims": len(claims),
        "claims_by_status": status,
        "words": words,
        "external_per_1k_words": round(len(external) / (words / 1000), 1) if words > 150 else 0,
        "avg_score": round(sum(r.score for r in scored) / len(scored)) if scored else 0,
    }
