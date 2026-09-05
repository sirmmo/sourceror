"""Emit ready-to-paste citation templates in the target wiki's dialect."""
from __future__ import annotations

import datetime
import re

DIALECT = {
    "it": {
        "journal": "cita pubblicazione", "book": "cita libro",
        "web": "cita web", "news": "cita news",
        "title": "titolo", "author": "autore", "date": "data",
        "url": "url", "access": "accesso", "publisher": "editore",
        "journal_p": "rivista", "volume": "volume", "issue": "numero",
        "pages": "pp", "doi": "doi", "isbn": "isbn", "lang": "lingua",
        "year": "anno", "site": "sito",
    },
    "en": {
        "journal": "cite journal", "book": "cite book",
        "web": "cite web", "news": "cite news",
        "title": "title", "author": "author", "date": "date",
        "url": "url", "access": "access-date", "publisher": "publisher",
        "journal_p": "journal", "volume": "volume", "issue": "issue",
        "pages": "pages", "doi": "doi", "isbn": "isbn", "lang": "language",
        "year": "year", "site": "website",
    },
}


def _d(lang: str) -> dict:
    return DIALECT.get(lang, DIALECT["en"])


def _fmt(name: str, fields: list[tuple[str, str]]) -> str:
    parts = "".join(f"|{k}={v}" for k, v in fields if v)
    return "{{" + name + parts + "}}"


def _authors(names: list[str], limit: int = 4) -> str:
    if not names:
        return ""
    out = "; ".join(names[:limit])
    if len(names) > limit:
        out += " et al."
    return out


def journal(work: dict, lang: str = "it") -> str:
    """From an OpenAlex/Crossref summary dict (see apis.openalex_summary)."""
    d = _d(lang)
    return _fmt(d["journal"], [
        (d["author"], _authors(work.get("authors") or [])),
        (d["title"], _clean(work.get("title"))),
        (d["journal_p"], _clean(work.get("venue"))),
        (d["year"] if lang == "it" else d["date"], str(work.get("year") or "")),
        (d["doi"], work.get("doi") or ""),
        (d["url"], work.get("oa_url") or ""),
        (d["access"], _today(lang) if work.get("oa_url") else ""),
    ])


def book(doc: dict, lang: str = "it") -> str:
    d = _d(lang)
    isbns = doc.get("isbn") or []
    return _fmt(d["book"], [
        (d["author"], _authors(doc.get("author_name") or [])),
        (d["title"], _clean(doc.get("title"))),
        (d["publisher"], _clean((doc.get("publisher") or [""])[0])),
        (d["year"] if lang == "it" else d["date"],
         str(doc.get("first_publish_year") or "")),
        (d["isbn"], isbns[0] if isbns else ""),
    ])


def web(meta: dict, lang: str = "it") -> str:
    """From a Citoid result."""
    d = _d(lang)
    authors = []
    for c in meta.get("author") or []:
        authors.append(" ".join(x for x in c if x).strip())
    return _fmt(d["web"], [
        (d["url"], meta.get("url") or ""),
        (d["title"], _clean(meta.get("title"))),
        (d["author"], _authors([a for a in authors if a])),
        (d["site"], _clean(meta.get("websiteTitle") or meta.get("publicationTitle"))),
        (d["publisher"], _clean(meta.get("publisher"))),
        (d["date"], (meta.get("date") or "")[:10]),
        (d["access"], _today(lang)),
        (d["lang"], meta.get("language") or ""),
    ])


def from_citoid(meta: dict, lang: str = "it") -> str:
    kind = (meta.get("itemType") or "webpage").lower()
    if kind in ("journalarticle", "conferencepaper", "preprint"):
        d = _d(lang)
        authors = [" ".join(x for x in c if x).strip()
                   for c in (meta.get("author") or [])]
        return _fmt(d["journal"], [
            (d["author"], _authors([a for a in authors if a])),
            (d["title"], _clean(meta.get("title"))),
            (d["journal_p"], _clean(meta.get("publicationTitle"))),
            (d["volume"], meta.get("volume") or ""),
            (d["issue"], meta.get("issue") or ""),
            (d["pages"], meta.get("pages") or ""),
            (d["year"] if lang == "it" else d["date"], (meta.get("date") or "")[:4]),
            (d["doi"], (meta.get("DOI") or "")),
        ])
    if kind == "book":
        d = _d(lang)
        authors = [" ".join(x for x in c if x).strip()
                   for c in (meta.get("author") or [])]
        return _fmt(d["book"], [
            (d["author"], _authors([a for a in authors if a])),
            (d["title"], _clean(meta.get("title"))),
            (d["publisher"], _clean(meta.get("publisher"))),
            (d["year"] if lang == "it" else d["date"], (meta.get("date") or "")[:4]),
            (d["isbn"], (meta.get("ISBN") or [""])[0]
                        if isinstance(meta.get("ISBN"), list) else (meta.get("ISBN") or "")),
        ])
    if kind in ("newspaperarticle", "magazinearticle"):
        d = _d(lang)
        authors = [" ".join(x for x in c if x).strip()
                   for c in (meta.get("author") or [])]
        return _fmt(d["news"], [
            (d["author"], _authors([a for a in authors if a])),
            (d["title"], _clean(meta.get("title"))),
            (d["publisher"] if lang == "it" else "newspaper",
             _clean(meta.get("publicationTitle"))),
            (d["url"], meta.get("url") or ""),
            (d["date"], (meta.get("date") or "")[:10]),
            (d["access"], _today(lang)),
        ])
    return web(meta, lang)


def archive_fix(url: str, archive_url: str, archive_date: str,
                lang: str = "it") -> str:
    if lang == "it":
        return (f"|urlarchivio={archive_url} |dataarchivio={archive_date} "
                f"|urlmorto=sì")
    return (f"|archive-url={archive_url} |archive-date={archive_date} "
            f"|url-status=dead")


def _clean(s) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", str(s))
    return re.sub(r"\s+", " ", s).replace("|", "-").strip()


# strftime("%B") follows the C locale, which the container does not carry, so
# month names for the wikis that spell out dates are listed explicitly.
MONTHS = {
    "it": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
           "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"),
    "pt": ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
           "agosto", "setembro", "outubro", "novembro", "dezembro"),
    "de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"),
    "ca": ("gener", "febrer", "març", "abril", "maig", "juny", "juliol",
           "agost", "setembre", "octubre", "novembre", "desembre"),
}


def _today(lang: str) -> str:
    d = datetime.date.today()
    months = MONTHS.get(lang)
    if not months:
        return d.isoformat()
    return f"{d.day} {months[d.month - 1]} {d.year}"
