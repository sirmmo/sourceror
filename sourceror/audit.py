"""Quality assessment for the citations already present in an article.

Every check emits a Flag. Severity drives the final grade: a single `critical`
flag caps the citation at "unreliable" no matter how well-formed it is, because
a well-formatted citation to a deprecated source is still unusable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from . import apis
from .reliability import Reliability, SEVERITY_ORDER

# A flag's severity caps the score, regardless of everything else.
SEVERITY_CAP = {"critical": 18, "serious": 45, "caution": 70}
BASE_SCORE = 74

GRADES = [(75, "solid"), (55, "ok"), (35, "weak"), (0, "unreliable")]

# Citation-template parameter names, en + it.
P_TITLE = ("title", "titolo", "trans-title", "titolotradotto", "nome", "voce")
P_AUTHOR = ("author", "author1", "last", "last1", "autore", "autore1",
            "cognome", "cognome1", "authors", "autori", "curatore", "editor")
P_DATE = ("date", "data", "year", "anno", "publication-date", "dataoriginale",
          "anno_pubblicazione")
P_WORK = ("work", "website", "sito", "publisher", "editore", "newspaper",
          "journal", "rivista", "pubblicazione", "magazine", "periodico",
          "opera", "testata")
P_ACCESS = ("access-date", "accessdate", "accesso", "urlaccesso")
P_ARCHIVE = ("archive-url", "archiveurl", "urlarchivio", "archive-date",
             "dataarchivio", "urlmorto", "url-status")
P_URL = ("url", "URL", "link")

ACADEMIC_REPOS = ("academia.edu", "researchgate.net", "ssrn.com", "arxiv.org",
                  "biorxiv.org", "medrxiv.org", "preprints.org", "zenodo.org",
                  "semanticscholar.org", "scribd.com", "core.ac.uk")


@dataclass
class Flag:
    code: str
    severity: str          # critical | serious | caution | info | good
    title: str
    detail: str = ""
    fix: str = ""
    url: str | None = None


@dataclass
class CitationReport:
    index: int
    section: str
    kind: str
    name: str | None
    reused: int
    urls: list[str]
    domain: str | None
    doi: str | None
    isbn: str | None
    display: str
    raw: str
    score: int = BASE_SCORE
    grade: str = "ok"
    flags: list[Flag] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    upgrade: dict | None = None      # a better version of the same source

    def add(self, *flags: Flag) -> None:
        self.flags.extend(flags)

    def finalise(self) -> None:
        score = BASE_SCORE
        cap = 100
        for f in self.flags:
            score += FLAG_DELTA.get(f.code, _default_delta(f.severity))
            if f.severity in SEVERITY_CAP:
                cap = min(cap, SEVERITY_CAP[f.severity])
        self.score = max(0, min(cap, min(100, score)))
        self.grade = next(g for threshold, g in GRADES if self.score >= threshold)
        self.flags.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))


def _default_delta(sev: str) -> int:
    return {"critical": -60, "serious": -30, "caution": -12,
            "info": 0, "good": +8}[sev]


FLAG_DELTA = {
    "BARE_URL": -14,
    "VAGUE_REF": -30,
    "UNSTRUCTURED_CITE": -10,
    "MISSING_TITLE": -8,
    "MISSING_AUTHOR": -4,
    "MISSING_DATE": -6,
    "MISSING_WORK": -5,
    "NO_ACCESS_DATE": -2,
    "DEAD_LINK": -22,
    "DEAD_LINK_ARCHIVED": -6,
    "SOFT_404": -18,
    "UNVERIFIABLE": -6,
    "SELF_SOURCED": -18,
    "PREPRINT": -12,
    "NO_DOI_ACADEMIC": -6,
    "DOI_NOT_FOUND": -20,
    "RETRACTED": -95,
    "HAS_ARCHIVE": +4,
    "PEER_REVIEWED": +12,
    "IN_DOAJ": +5,
    "WELL_CITED": +5,
    "COMPLETE": +6,
    "RSP_RELIABLE": +10,
    "EMPTY_REF": -30,
    "EXPLANATORY_NOTE": 0,
    "REFERENCE_WORK": +14,
    "SHORTCITE_OK": +10,
    "SHORTCITE_ORPHAN": -12,
    "SHORTCITE_NO_BIBLIO": -14,
    "UPGRADE_AVAILABLE": 0,
}


class Auditor:
    def __init__(self, rel: Reliability, lang: str = "it",
                 bibliography: list | None = None):
        self.rel = rel
        self.lang = lang
        self._biblio_names = _biblio_surnames(bibliography or [])

    # ---------------------------------------------------------------- main
    def audit(self, ref, context: dict | None = None) -> CitationReport:
        ctx = context or {}
        tpl = ref.template
        params = tpl.params if tpl else {}
        url = _first(params, P_URL) or (ref.urls[0] if ref.urls else None)
        domain = apis.domain_of(url) if url else None

        rep = CitationReport(
            index=ref.index, section=ref.section, kind=ref.kind, name=ref.name,
            reused=ref.reused, urls=ref.urls, domain=domain, doi=ref.doi,
            isbn=ref.isbn, display=_display(ref, params, url),
            raw=ref.raw[:1200],
        )

        self._check_structure(rep, ref, params)
        if domain:
            self._check_domain(rep, domain, url, ctx)
        if ref.doi:
            self._check_doi(rep, ref.doi)
        elif rep.kind == "journal":
            rep.add(Flag("NO_DOI_ACADEMIC", "caution",
                         "Academic citation without a DOI",
                         "A journal citation with no DOI is harder for a reader "
                         "to verify and cannot be checked for retraction.",
                         "Add |doi= if the article has one."))
        if url and ctx.get("check_links", True):
            self._check_link(rep, url, params)
        if domain and any(r in domain for r in ACADEMIC_REPOS):
            self._try_upgrade(rep, params)

        rep.finalise()
        return rep

    # ------------------------------------------------------------ checks
    def _check_structure(self, rep, ref, params) -> None:
        if ref.kind == "empty":
            rep.add(Flag("EMPTY_REF", "serious", "Empty reference",
                         "The <ref></ref> tag contains nothing at all, so the "
                         "claim it marks is effectively unsourced.",
                         "Fill it in or remove the tag."))
            return
        if ref.kind == "note":
            # Explanatory commentary, not a citation. Judging it as a source
            # would be a false alarm, but the note itself may need one.
            rep.add(Flag("EXPLANATORY_NOTE", "info", "Explanatory note",
                         "This footnote is commentary rather than a citation, so "
                         "it is not scored as a source.",
                         "If it states a fact of its own, give it a citation, or "
                         "move it to a separate notes group."))
            return
        if ref.kind in ("shortcite", "shortfootnote"):
            self._check_shortcite(rep, ref)
            return
        if ref.kind == "referencework":
            name = ref.template.name if ref.template else "reference work"
            rep.add(Flag("REFERENCE_WORK", "good",
                         f"Named reference work ({name})",
                         "Cites an edited encyclopedia or dictionary through its "
                         "dedicated template — an editorially controlled source."))
            if not _first(params, P_TITLE):
                rep.add(Flag("MISSING_TITLE", "caution", "No entry title",
                             "The template does not name the entry being cited.",
                             "Add the entry name parameter."))
            return
        if ref.kind == "bare":
            rep.add(Flag("BARE_URL", "caution", "Bare URL",
                         "The reference is a naked link with no title, author or "
                         "date, so it cannot be recovered if the link dies.",
                         "Wrap it in a citation template — Sourceror can generate "
                         "one from the URL."))
            return
        if ref.kind == "textcite":
            # A book or article written out in prose. Offline sources are
            # perfectly valid (WP:SOURCEACCESS) — this is a formatting issue.
            rep.add(Flag("UNSTRUCTURED_CITE", "caution", "Citation not in a template",
                         "The source is written as free text. It is readable, but "
                         "the fields cannot be checked, linked or reused.",
                         "Convert to a citation template so the author, date and "
                         "identifiers become explicit."))
            return
        if ref.kind == "text" and not ref.urls and not ref.doi and not ref.isbn:
            rep.add(Flag("VAGUE_REF", "serious", "Vague reference",
                         "The footnote names no identifiable work — no title, "
                         "year, page, link or identifier — so a reader has no way "
                         "to check it.",
                         "Name the work and page, or convert to a citation "
                         "template or {{sfn}} pointing at the bibliography."))
            return
        if ref.kind == "shortfootnote":
            return

        missing = []
        if not _first(params, P_TITLE):
            rep.add(Flag("MISSING_TITLE", "caution", "No title",
                         "Without a title the citation cannot be traced once the "
                         "URL rots.", "Add |title= / |titolo=."))
            missing.append("title")
        if not _first(params, P_DATE):
            rep.add(Flag("MISSING_DATE", "caution", "No publication date",
                         "Undated sources cannot be assessed for currency.",
                         "Add |date= / |data=."))
            missing.append("date")
        if not _first(params, P_AUTHOR) and ref.kind in ("journal", "book", "news"):
            rep.add(Flag("MISSING_AUTHOR", "caution", "No author",
                         "Attribution is needed to weigh the source's authority.",
                         "Add |author= / |autore=."))
            missing.append("author")
        if not _first(params, P_WORK):
            rep.add(Flag("MISSING_WORK", "caution", "No publisher or work",
                         "It is unclear who published this, which is the main "
                         "signal of reliability.",
                         "Add |publisher= / |editore= or |work= / |sito=."))
            missing.append("work")
        if ref.urls and not _first(params, P_ACCESS):
            rep.add(Flag("NO_ACCESS_DATE", "info", "No access date",
                         "An access date records when the page said what you cite.",
                         "Add |access-date= / |accesso=."))
        if not missing and params:
            rep.add(Flag("COMPLETE", "good", "Complete citation",
                         "Title, date, author and publisher are all present."))
        if _first(params, P_ARCHIVE):
            rep.add(Flag("HAS_ARCHIVE", "good", "Archived",
                         "The citation already records an archive copy."))

    def _check_shortcite(self, rep, ref) -> None:
        """A short citation is only as good as the bibliography entry it points
        at; an orphan one is unverifiable."""
        biblio = self._biblio_names
        text = ref.template.raw if ref.template else ref.inner
        names = set(re.findall(r"[A-ZÀ-Ú][\w'’\-]{2,}", text))
        if not biblio:
            rep.add(Flag("SHORTCITE_NO_BIBLIO", "caution",
                         "Short citation with no bibliography",
                         "The footnote points at a full reference, but the article "
                         "has no Bibliography section to resolve it against.",
                         "Add the full citation to a Bibliografia / Bibliography "
                         "section."))
            return
        if names & biblio:
            rep.add(Flag("SHORTCITE_OK", "good", "Short citation resolved",
                         "Matches an entry in the article's bibliography."))
        else:
            rep.add(Flag("SHORTCITE_ORPHAN", "caution",
                         "Short citation does not match the bibliography",
                         "No bibliography entry appears to correspond to this "
                         "footnote, so a reader cannot resolve it to a source.",
                         "Add the full work to the bibliography, or correct the "
                         "author name."))

    def _check_domain(self, rep, domain, url, ctx) -> None:
        for v in self.rel.check_domain(domain):
            code = {"good": "RSP_RELIABLE"}.get(v.severity, f"DOMAIN_{v.severity.upper()}")
            fix = ""
            if v.severity in ("critical", "serious"):
                fix = ("Replace with an independent, editorially-controlled "
                       "source. Sourceror's 'suggested sources' tab can look "
                       "for a replacement.")
            elif v.severity == "caution":
                fix = "Usable in some contexts — check that this use is one of them."
            rep.add(Flag(code, v.severity,
                         f"{v.label}" + (f" — {v.name}" if v.name and v.source == "rsp" else ""),
                         v.detail, fix, v.url))

        official = ctx.get("official_websites") or []
        if official and any(apis.domain_of(o) and apis.domain_of(o) in domain
                            for o in official):
            rep.add(Flag("SELF_SOURCED", "caution", "Source is the article subject",
                         "This cites the subject's own website. It is a primary "
                         "self-published source: fine for uncontroversial facts, "
                         "but it cannot establish notability or support contested "
                         "claims (WP:ABOUTSELF).",
                         "Balance with an independent secondary source."))

    def _check_doi(self, rep, doi) -> None:
        for v in self.rel.check_doi(doi):
            rep.add(Flag(f"DOI_{v.severity.upper()}", v.severity, v.label,
                         v.detail,
                         "Verify this paper is not from a predatory or "
                         "paper-mill venue before relying on it.", v.url))

        work = apis.openalex_doi(doi)
        summary = apis.openalex_summary(work) if work else {}
        cr = apis.crossref_doi(doi)

        if not work and not cr:
            rep.add(Flag("DOI_NOT_FOUND", "serious", "DOI does not resolve",
                         f"{doi} is not in Crossref or OpenAlex. It may be "
                         "mistyped or fabricated.",
                         "Check the DOI against the publisher's page."))
            return

        rep.metadata.update({k: v for k, v in summary.items() if v not in (None, [], "")})

        retracted = summary.get("is_retracted") or (cr and apis.crossref_is_retracted(cr))
        if retracted:
            rep.add(Flag("RETRACTED", "critical", "RETRACTED PUBLICATION",
                         "This paper has been retracted by its publisher. It must "
                         "not be used to support any claim.",
                         "Remove the citation and any text depending on it.",
                         f"https://doi.org/{doi}"))

        wtype = (summary.get("type") or (cr or {}).get("type") or "").lower()
        venue_type = (summary.get("venue_type") or "").lower()
        if wtype in ("preprint", "posted-content") or venue_type == "repository":
            rep.add(Flag("PREPRINT", "caution", "Preprint — not peer reviewed",
                         f"Published on {summary.get('venue') or 'a preprint server'}. "
                         "Preprints have not been through peer review and are "
                         "weak sources for contested claims (WP:SCHOLARSHIP).",
                         "Look for the peer-reviewed version of record."))
        elif wtype in ("article", "journal-article", "book-chapter", "review"):
            rep.add(Flag("PEER_REVIEWED", "good", "Peer-reviewed publication",
                         f"{summary.get('venue') or 'Journal'}"
                         + (f" ({summary['year']})" if summary.get("year") else "")))
        if summary.get("in_doaj"):
            rep.add(Flag("IN_DOAJ", "good", "Journal indexed in DOAJ",
                         "Listed in the Directory of Open Access Journals, which "
                         "vets editorial standards."))
        if (summary.get("cited_by") or 0) >= 50:
            rep.add(Flag("WELL_CITED", "good", "Well cited",
                         f"Cited {summary['cited_by']} times per OpenAlex."))

    def _check_link(self, rep, url, params) -> None:
        h = apis.check_link(url)
        has_archive = bool(_first(params, P_ARCHIVE))
        rep.metadata["link"] = {"status": h.status, "reason": h.reason,
                                "redirect_to": h.redirect_to,
                                "archived": h.archived,
                                "archive_date": h.archive_date,
                                "has_archive": has_archive}
        if h.dead:
            if h.archived:
                rep.add(Flag("DEAD_LINK_ARCHIVED", "caution", "Dead link — archive available",
                             f"The URL is dead ({h.reason}) but the Wayback Machine "
                             f"has a copy from {h.archive_date}.",
                             f"Add |archive-url={h.archived} |archive-date={h.archive_date} "
                             "|url-status=dead", h.archived))
            elif has_archive:
                rep.add(Flag("DEAD_LINK_ARCHIVED", "info", "Dead link, already archived",
                             f"The live URL is dead ({h.reason}), but the citation "
                             "already carries an archive copy."))
            else:
                rep.add(Flag("DEAD_LINK", "serious", "Dead link, no archive",
                             f"{h.reason}. No Wayback snapshot exists, so the claim "
                             "is currently unverifiable.",
                             "Find a replacement source, or save a copy at "
                             "web.archive.org if the content is still online elsewhere."))
        elif h.status == 403:
            rep.add(Flag("UNVERIFIABLE", "info", "Could not verify automatically",
                         "The site blocked our request (HTTP 403). This says "
                         "nothing about the source's quality — check by hand."))
        elif h.redirect_to and apis.domain_of(h.redirect_to) != apis.domain_of(url):
            rep.add(Flag("UNVERIFIABLE", "caution", "Redirects to another domain",
                         f"The URL now lands on {apis.domain_of(h.redirect_to)}, "
                         "which may mean the domain changed hands.",
                         f"Confirm the target still supports the claim: {h.redirect_to}"))

    def _try_upgrade(self, rep, params) -> None:
        """academia.edu / ResearchGate / preprint servers host copies of papers
        that usually also exist in a peer-reviewed venue. Find that version."""
        title = _first(params, P_TITLE)
        if not title or len(title) < 12:
            return
        title = re.sub(r"\s+", " ", re.sub(r"\[\[|\]\]|'{2,}", "", title)).strip()
        work = apis.openalex_title_lookup(title)
        if not work:
            return
        s = apis.openalex_summary(work)
        if not s.get("doi") or (s.get("type") or "") in ("preprint", "posted-content"):
            return
        if _similar(title, s.get("title") or "") < 0.6:
            return
        rep.upgrade = s
        rep.add(Flag("UPGRADE_AVAILABLE", "info",
                     "Peer-reviewed version found",
                     f"The same paper appears in {s.get('venue')} "
                     f"({s.get('year')}) with DOI {s['doi']}. Citing the version "
                     "of record is stronger than citing a repository copy.",
                     f"Cite doi:{s['doi']} instead.",
                     f"https://doi.org/{s['doi']}"))


# ------------------------------------------------------------------ helpers

def _first(params: dict, keys) -> str | None:
    for k in keys:
        v = params.get(k)
        if v and v.strip():
            return v.strip()
    return None


def _display(ref, params, url) -> str:
    t = _first(params, P_TITLE)
    if t:
        t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", t)
        return re.sub(r"\s+", " ", t).strip()[:180]
    if url:
        return url[:180]
    if ref.doi:
        return f"doi:{ref.doi}"
    txt = re.sub(r"\{\{[^{}]*\}\}|<[^>]+>", "", ref.inner)
    txt = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", txt)
    return re.sub(r"\s+", " ", txt).strip()[:180] or "(empty reference)"


def _similar(a: str, b: str) -> float:
    ta = set(re.findall(r"\w{4,}", a.lower()))
    tb = set(re.findall(r"\w{4,}", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _biblio_surnames(entries) -> set[str]:
    """Capitalised words from the bibliography, used to resolve short cites."""
    names: set[str] = set()
    for t in entries:
        for key in ("2", "1", "cognome", "last", "author", "autore", "3", "titolo", "title"):
            v = t.params.get(key)
            if v:
                names.update(re.findall(r"[A-ZÀ-Ú][\w'’\-]{2,}", v))
    return names


def flag_to_dict(f: Flag) -> dict:
    return asdict(f)
