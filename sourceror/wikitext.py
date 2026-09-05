"""Wikitext parsing: citation templates, <ref> tags, sections, unsourced claims.

Deliberately hand-rolled rather than mwparserfromhell-based: we only need a few
constructs and we need byte offsets into the source so the UI can point the
editor at the exact spot in the article.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# Templates that flag a missing/insufficient source, per wiki.
CN_TEMPLATES = {
    "citation needed", "cn", "fact", "citation-needed", "cite needed",
    "senza fonte", "sf", "citazione necessaria",
    "unreferenced", "senza fonti", "unsourced", "verify source",
    "dubious", "dubbio", "better source needed", "fonte migliore",
    "unreliable source?", "fonte inaffidabile", "self-published source",
    "original research?", "ricerca originale",
}

# Citation templates we understand, mapped to a canonical kind.
CITE_TEMPLATES = {
    # en.wiki
    "cite web": "web", "cite news": "news", "cite journal": "journal",
    "cite book": "book", "cite magazine": "magazine", "cite report": "report",
    "cite thesis": "thesis", "cite conference": "conference",
    "cite encyclopedia": "encyclopedia", "cite press release": "press",
    "cite av media": "av", "cite interview": "interview", "cite tweet": "social",
    "citation": "generic",
    # it.wiki
    "cita web": "web", "cita news": "news", "cita notizia": "news",
    "cita pubblicazione": "journal", "cita libro": "book",
    "cita testo": "generic", "cita conferenza": "conference",
    "cita tesi": "thesis", "cita video": "av", "cita intervista": "interview",
    "cita necrologio": "news",
}

# Reference-work templates: wiki-specific wrappers around a named, edited
# encyclopedia or dictionary. These are proper citations even though they are
# not part of the {{cite ...}} family, so they must not be read as free text.
REFERENCE_WORK_TEMPLATES = {
    # it.wiki
    "dbi", "treccani", "cita treccani", "enciclopedia italiana", "sapere",
    "thesaurus bncf", "enciclopedia britannica", "catholic encyclopedia",
    "cita dbi", "openstreetmap", "scheda ministero", "cita enciclopedia",
    # en.wiki
    "cite odnb", "cite dnb", "cite eb1911", "cite ebi", "cite nie",
    "cite americana", "cite grove", "cite catholic encyclopedia",
    "cite dcb", "cite anb", "cite dsb", "cite collier's", "cite dictionary",
    "odnbweb", "dnb", "eb1911", "anb",
}

# Short-footnote templates that point into a Bibliography section rather than
# carrying the source themselves.
SFN_TEMPLATES = {"sfn", "sfnp", "harvnb", "harv", "harvnb", "cita", "sfn whitelist"}

URL_RE = re.compile(r"https?://[^\s|\]}<>\"']+")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
ISBN_RE = re.compile(r"\b(?:97[89][- ]?)?(?:\d[- ]?){9}[\dXx]\b")


@dataclass
class Template:
    name: str
    params: dict[str, str]
    start: int
    end: int
    raw: str


@dataclass
class Ref:
    """One <ref>...</ref> (or a {{sfn}} short footnote)."""
    index: int
    raw: str
    inner: str
    name: str | None
    start: int
    end: int
    section: str
    template: Template | None = None
    kind: str = "unknown"          # web/news/journal/book/... or "bare"/"text"
    urls: list[str] = field(default_factory=list)
    doi: str | None = None
    isbn: str | None = None
    reused: int = 1                # how many times this named ref is used


@dataclass
class CnTag:
    """A {{citation needed}}-style tag and the sentence it attaches to."""
    template: str
    reason: str | None
    section: str
    claim: str
    start: int


@dataclass
class Section:
    title: str
    level: int
    start: int
    end: int
    text: str
    ref_count: int = 0
    char_count: int = 0


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), text, flags=re.S)


def find_templates(text: str, start: int = 0) -> list[Template]:
    """Find top-level {{...}} templates, tracking nesting so that a `|` inside a
    nested template or [[link]] does not split a parameter."""
    out: list[Template] = []
    i = start
    n = len(text)
    while i < n - 1:
        if text[i] == "{" and text[i + 1] == "{":
            end = _match_braces(text, i)
            if end is None:
                i += 2
                continue
            raw = text[i:end]
            name, params = _parse_template(raw)
            out.append(Template(name=name, params=params, start=i, end=end, raw=raw))
            i = end
        else:
            i += 1
    return out


def _match_braces(text: str, i: int) -> int | None:
    """Return index just past the {{...}} starting at i, or None if unbalanced."""
    depth = 0
    n = len(text)
    while i < n - 1:
        two = text[i:i + 2]
        if two == "{{":
            depth += 1
            i += 2
        elif two == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return None


def _split_params(body: str) -> list[str]:
    """Split on | at nesting depth 0 (ignoring {{}}, [[]], <>, and tables)."""
    parts, buf = [], []
    depth_brace = depth_brack = 0
    i, n = 0, len(body)
    while i < n:
        two = body[i:i + 2]
        if two == "{{":
            depth_brace += 1; buf.append(two); i += 2; continue
        if two == "}}":
            depth_brace -= 1; buf.append(two); i += 2; continue
        if two == "[[":
            depth_brack += 1; buf.append(two); i += 2; continue
        if two == "]]":
            depth_brack -= 1; buf.append(two); i += 2; continue
        c = body[i]
        if c == "|" and depth_brace == 0 and depth_brack == 0:
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    parts.append("".join(buf))
    return parts


def _parse_template(raw: str) -> tuple[str, dict[str, str]]:
    body = raw[2:-2]
    parts = _split_params(body)
    name = parts[0].strip().lower().replace("_", " ")
    name = re.sub(r"\s+", " ", name)
    params: dict[str, str] = {}
    positional = 0
    for p in parts[1:]:
        if "=" in p:
            k, _, v = p.partition("=")
            k = k.strip().lower()
            # A '=' inside a URL or link is not a param separator.
            if k and not re.search(r"[\[\]{}<>]", k) and len(k) < 40:
                params[k] = v.strip()
                continue
        positional += 1
        params[str(positional)] = p.strip()
    return name, params


def parse_sections(text: str) -> list[Section]:
    """Split wikitext on == headings ==."""
    heads = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip())
             for m in re.finditer(r"^(={2,6})\s*(.+?)\s*\1\s*$", text, re.M)]
    out: list[Section] = []
    lead_end = heads[0][0] if heads else len(text)
    out.append(Section("(incipit)", 1, 0, lead_end, text[:lead_end]))
    for idx, (hs, he, level, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(text)
        out.append(Section(title, level, hs, end, text[he:end]))
    for s in out:
        s.char_count = len(re.sub(r"\{\{[^}]*\}\}|<ref.*?</ref>|<ref[^>]*/>", "", s.text, flags=re.S))
    return out


def _section_at(sections: list[Section], pos: int) -> str:
    for s in sections:
        if s.start <= pos < s.end:
            return s.title
    return "(incipit)"


def parse_refs(text: str, sections: list[Section]) -> list[Ref]:
    """Extract <ref>...</ref>, <ref name=x/>, and bare {{sfn}} footnotes."""
    refs: list[Ref] = []
    named: dict[str, Ref] = {}
    idx = 0

    pattern = re.compile(
        r"<ref(?P<attrs>[^>/]*)(?:/>|>(?P<inner>.*?)</ref\s*>)", re.S | re.I)
    for m in pattern.finditer(text):
        attrs = m.group("attrs") or ""
        inner = (m.group("inner") or "").strip()
        nm = re.search(r"""name\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s/>]+))""", attrs, re.I)
        refname = next((g for g in nm.groups() if g), None) if nm else None

        if not inner and refname and refname in named:
            named[refname].reused += 1
            continue

        idx += 1
        ref = Ref(index=idx, raw=m.group(0), inner=inner, name=refname,
                  start=m.start(), end=m.end(),
                  section=_section_at(sections, m.start()))
        _classify(ref)
        refs.append(ref)
        if refname:
            named[refname] = ref

    # {{sfn}} short footnotes outside <ref> tags.
    for t in find_templates(text):
        if t.name in SFN_TEMPLATES and t.name != "cita":
            idx += 1
            ref = Ref(index=idx, raw=t.raw, inner=t.raw, name=None,
                      start=t.start, end=t.end,
                      section=_section_at(sections, t.start),
                      template=t, kind="shortfootnote")
            refs.append(ref)

    for s in sections:
        s.ref_count = sum(1 for r in refs if s.start <= r.start < s.end)
    return refs


# "Lorenzetti, p. 42" / "Rossi 1990, pp. 12-14" / "Tassini, cap. III"
SHORTCITE_RE = re.compile(
    r"^\W*[\[\]']*[A-ZÀ-Ú][\w'’.\-]+"          # a surname
    r"(?:[\s,]+(?:e|and|&|et|al\.?)[\s,]*[A-ZÀ-Ú][\w'’.\-]+)?"
    r"[^\n]{0,80}?"
    r"\b(?:pp?|pag(?:g|ine)?|cap|vol|§)\.?\s*[\dIVXLivxl]",
    re.U)

# A bibliographic-looking plain-text reference: a year, page numbers, or an
# italicised work title. Offline sources are legitimate (WP:SOURCEACCESS), so
# these must not be treated as unsourced.
BIBLIOGRAPHIC_RE = re.compile(
    r"(\b(?:1[5-9]\d{2}|20[0-2]\d)\b"          # a plausible year
    r"|\b(?:pp?|pag(?:g|ine)?|cap|vol|no|n)\.?\s*\d"
    r"|''[^']{4,}''"                             # italicised title
    r"|\bISBN\b|\bed\.\b|\beditore\b|\bpress\b|\buniversity\b)", re.I)


def _classify(ref: Ref) -> None:
    inner = ref.inner
    tpls = find_templates(inner)
    cite = next((t for t in tpls if t.name in CITE_TEMPLATES), None)
    refwork = next((t for t in tpls if t.name in REFERENCE_WORK_TEMPLATES), None)
    if cite:
        ref.template = cite
        ref.kind = CITE_TEMPLATES[cite.name]
    elif refwork:
        ref.template = refwork
        ref.kind = "referencework"
    elif any(t.name in SFN_TEMPLATES for t in tpls):
        ref.kind = "shortfootnote"
        ref.template = next(t for t in tpls if t.name in SFN_TEMPLATES)
    elif URL_RE.search(inner):
        # A ref that is a URL with little else is a bare-URL citation.
        stripped = URL_RE.sub("", inner)
        stripped = re.sub(r"[\[\]\s]", "", stripped)
        ref.kind = "bare" if len(stripped) < 12 else "text"
    else:
        ref.kind = _classify_prose(inner)

    tpl = ref.template
    ref.urls = _dedupe(URL_RE.findall(inner))
    src = tpl.raw if tpl else inner
    doi = (tpl.params.get("doi") if tpl else None) or None
    if not doi:
        m = DOI_RE.search(src)
        doi = m.group(0) if m else None
    ref.doi = doi.strip().rstrip(".,;)") if doi else None
    isbn = (tpl.params.get("isbn") if tpl else None) or None
    if not isbn:
        m = ISBN_RE.search(src)
        isbn = m.group(0) if m else None
    ref.isbn = re.sub(r"[- ]", "", isbn) if isbn else None


def _classify_prose(inner: str) -> str:
    """A <ref> with no template and no link is one of three quite different
    things, and conflating them produces false alarms."""
    plain = re.sub(r"\{\{[^{}]*\}\}|<[^>]+>", "", inner)
    plain = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", plain)
    plain = html.unescape(plain).replace("\xa0", " ").strip()
    if not plain:
        return "empty"
    if SHORTCITE_RE.search(plain) and len(plain) < 160:
        return "shortcite"
    words = plain.split()
    lowercase = sum(1 for w in words if w[:1].islower())
    # Running prose that makes its own statement is an explanatory note, not a
    # citation that happens to be missing its link.
    if len(words) >= 8 and lowercase >= 4 and not BIBLIOGRAPHIC_RE.search(plain):
        return "note"
    if BIBLIOGRAPHIC_RE.search(plain):
        return "textcite"     # a real citation, just not in a template
    return "text"


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        x = x.rstrip(".,;)]}")
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def parse_cn_tags(text: str, sections: list[Section]) -> list[CnTag]:
    """Find {{citation needed}}-family tags and the claim each one follows."""
    out: list[CnTag] = []
    for t in find_templates(text):
        if t.name not in CN_TEMPLATES:
            continue
        reason = t.params.get("reason") or t.params.get("motivo") or t.params.get("1")
        if reason and re.match(r"^\s*(\w+\s+)?\d{4}\s*$", reason):
            reason = None  # it's a date param, not a reason
        out.append(CnTag(template=t.name, reason=reason,
                         section=_section_at(sections, t.start),
                         claim=_claim_before(text, t.start),
                         start=t.start))
    return out


def _claim_before(text: str, pos: int, window: int = 600) -> str:
    """The sentence preceding a tag — this is the thing needing a source."""
    chunk = text[max(0, pos - window):pos]
    chunk = re.sub(r"<ref.*?</ref>|<ref[^>]*/>", "", chunk, flags=re.S)
    chunk = re.sub(r"\{\{[^{}]*\}\}", "", chunk)
    chunk = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", chunk)
    chunk = re.sub(r"'{2,}", "", chunk).strip()
    # Take the trailing sentence.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú])|\n\n+", chunk)
    claim = parts[-1] if parts else chunk
    return re.sub(r"\s+", " ", claim).strip()[:400]


def unsourced_paragraphs(text: str, sections: list[Section],
                         min_chars: int = 320) -> list[dict]:
    """Substantial paragraphs carrying no <ref> and no short footnote."""
    out = []
    for s in sections:
        if re.match(r"^(note|references|bibliograf|collegamenti|voci correlate|"
                    r"altri progetti|external links|see also|further reading|"
                    r"bibliography|sources|fonti)", s.title.strip().lower()):
            continue
        base = s.start + (0 if s.title == "(incipit)" else 0)
        offset = s.end - len(s.text)
        for m in re.finditer(r"(?:^|\n\n)([^\n].*?)(?=\n\n|\Z)", s.text, re.S):
            para = m.group(1)
            if re.match(r"^\s*[\*#:;=|!{]", para):
                continue
            if "<ref" in para or re.search(r"\{\{\s*(sfn|harvnb|sfnp)", para, re.I):
                continue
            plain = re.sub(r"\{\{[^{}]*\}\}", "", para)
            plain = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", plain)
            plain = re.sub(r"<[^>]+>|'{2,}", "", plain).strip()
            if len(plain) < min_chars:
                continue
            out.append({
                "section": s.title,
                "text": re.sub(r"\s+", " ", plain)[:600],
                "chars": len(plain),
                "start": offset + m.start(1),
            })
    return out


def bibliography_entries(text: str, sections: list[Section]) -> list[Template]:
    """Citation templates sitting in a Bibliography section (not inside a ref)."""
    out = []
    for s in sections:
        if not re.match(r"^(bibliograf|bibliography|fonti|sources|further reading)",
                        s.title.strip().lower()):
            continue
        body = re.sub(r"<ref.*?</ref>", "", s.text, flags=re.S)
        out.extend(t for t in find_templates(body) if t.name in CITE_TEMPLATES)
    return out


def parse_page(wikitext: str) -> dict:
    """Full parse of one article's wikitext."""
    text = _strip_comments(wikitext)
    sections = parse_sections(text)
    refs = parse_refs(text, sections)
    return {
        "sections": sections,
        "refs": refs,
        "cn_tags": parse_cn_tags(text, sections),
        "unsourced": unsourced_paragraphs(text, sections),
        "bibliography": bibliography_entries(text, sections),
        "text": text,
    }
