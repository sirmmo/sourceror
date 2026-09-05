"""Source-reliability knowledge base.

Three layers, all fetched live from Wikipedia and cached on disk:

1. WP:RSP  - Wikipedia:Reliable sources/Perennial sources. ~490 sources the
   community has repeatedly discussed, each with a status and a rationale.
2. User:Headbomb/unreliable.js - the data behind the widely-used citation
   highlighter: predatory publishers, hijacked journals, fake DOI resolvers,
   preprint repositories. Matches on domain *and* on DOI prefix.
3. A small built-in table for categories neither list covers well
   (self-published platforms, press-release wires, user-generated sites).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .http import get_json, get_text

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_TTL = 24 * 3600

RSP_SUBPAGES = 8
RSP_URL = ("https://en.wikipedia.org/w/api.php?action=parse"
           "&page=Wikipedia:Reliable_sources/Perennial_sources/{n}"
           "&prop=wikitext&format=json&formatversion=2")
HEADBOMB_URL = ("https://en.wikipedia.org/w/index.php"
                "?title=User:Headbomb/unreliable.js&action=raw")

# RSP status code -> (severity, label)
RSP_STATUS = {
    "gr": ("good", "Generally reliable"),
    "nc": ("caution", "No consensus / marginally reliable"),
    "m": ("caution", "Mixed — depends on the specific use"),
    "gu": ("serious", "Generally unreliable"),
    "d": ("critical", "Deprecated"),
    "b": ("critical", "Blacklisted"),
}

# Headbomb highlight colour -> severity
HB_COLOUR = {
    "#ffbbbb": "critical",   # predatory / hijacked / deprecated / fake news
    "#dddddd": "critical",   # blacklisted
    "#ffdddd": "serious",    # generally unreliable / sponsored / circular
    "orange": "serious",     # AI-generated slop
    "#fffdd0": "caution",    # borderline, preprints, depends-on-contributor
}

# Categories the community lists do not model explicitly.
BUILTIN = {
    "self_published": {
        "severity": "serious",
        "label": "Self-published platform",
        "detail": "Anyone can publish here without editorial oversight. "
                  "Usable only for uncontroversial claims about the author "
                  "themselves (WP:SELFPUB / WP:BLOGS).",
        "domains": {
            "medium.com", "substack.com", "blogspot.com", "blogger.com",
            "wordpress.com", "tumblr.com", "wixsite.com", "weebly.com",
            "livejournal.com", "typepad.com", "ghost.io", "svbtle.com",
            "scribd.com", "slideshare.net", "issuu.com", "calameo.com",
            "academia.edu", "wattpad.com", "lulu.com", "authorhouse.com",
            "xlibris.com", "blurb.com", "smashwords.com", "linkedin.com",
        },
    },
    "user_generated": {
        "severity": "serious",
        "label": "User-generated content",
        "detail": "Content is written by readers with no fact-checking, so it "
                  "is not a reliable source (WP:UGC).",
        "domains": {
            "imdb.com", "fandom.com", "wikia.com", "wikihow.com", "quora.com",
            "reddit.com", "answers.com", "discogs.com", "rateyourmusic.com",
            "geni.com", "ancestry.com", "findagrave.com", "tripadvisor.com",
            "yelp.com", "goodreads.com", "everipedia.org", "ask.fm",
            "stackexchange.com", "stackoverflow.com", "genius.com",
            "musicbrainz.org", "openstreetmap.org", "urbandictionary.com",
        },
    },
    "circular": {
        "severity": "critical",
        "label": "Circular reference to Wikimedia",
        "detail": "Citing Wikipedia or a Wikipedia mirror to source Wikipedia "
                  "is circular and never acceptable (WP:CIRCULAR).",
        "domains": {
            "wikipedia.org", "wikimedia.org", "wikidata.org", "wikiquote.org",
            "wikisource.org", "wiktionary.org", "wikinews.org", "wikivoyage.org",
            "wikibooks.org", "dbpedia.org", "alchetron.com", "wikiwand.com",
        },
    },
    "social_media": {
        "severity": "caution",
        "label": "Social media / primary self-source",
        "detail": "A primary self-published post. Acceptable only for the "
                  "account holder's own uncontroversial statements, never for "
                  "third-party claims (WP:SPS).",
        "domains": {
            "twitter.com", "x.com", "facebook.com", "instagram.com",
            "tiktok.com", "threads.net", "t.me", "vk.com", "weibo.com",
            "youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "flickr.com",
            "pinterest.com", "bsky.app", "mastodon.social",
        },
    },
    "press_release": {
        "severity": "caution",
        "label": "Press-release wire",
        "detail": "Press releases are promotional primary sources and do not "
                  "establish notability or independence (WP:PRSOURCE).",
        "domains": {
            "prnewswire.com", "businesswire.com", "globenewswire.com",
            "newswire.com", "prweb.com", "eurekalert.org", "einpresswire.com",
            "accesswire.com", "presseportal.de", "adnkronos.com/comunicati",
        },
    },
}


@dataclass
class Verdict:
    """One reliability determination about a domain or DOI."""
    source: str          # "rsp" | "headbomb" | "builtin"
    severity: str        # good | caution | serious | critical
    label: str
    detail: str
    name: str | None = None
    url: str | None = None
    matched: str | None = None


# --------------------------------------------------------------------------
# WP:RSP
# --------------------------------------------------------------------------

def _parse_rsp_table(wikitext: str) -> list[dict]:
    """Rows look like:

        |- class="s-gu" id="112 Ukraine"
        | [[112 Ukraine]]
        | {{WP:RSPSTATUS|gu}}
        | ...discussions...
        | {{WP:RSPLAST|2020}}
        | <summary prose>
        | {{WP:RSPUSES|112.ua|112.international}}
    """
    rows = []
    chunks = re.split(r"^\|-\s*class=\"s-[a-z]+\"", wikitext, flags=re.M)
    ids = re.findall(r"^\|-\s*class=\"s-([a-z]+)\"\s*id=\"([^\"]*)\"",
                     wikitext, flags=re.M)
    for (status, ident), chunk in zip(ids, chunks[1:]):
        uses = re.search(r"\{\{WP:RSPUSES\|(.*?)\}\}", chunk, re.S)
        domains = []
        if uses:
            for d in uses.group(1).split("|"):
                d = d.strip().lower()
                if d and "=" not in d and re.match(r"^[a-z0-9.\-]+\.[a-z]{2,}", d):
                    domains.append(d)
        # Cells run: name | status | discussions | last | summary | uses.
        cells = [c.strip() for c in re.split(r"^\|(?!-)", chunk, flags=re.M)]
        summary = ""
        uses_at = next((i for i, c in enumerate(cells) if "RSPUSES" in c), None)
        if uses_at is not None and uses_at > 0:
            summary = _clean_wiki(cells[uses_at - 1])
        if len(summary) < 40:
            # Fall back to the longest cell once wiki markup is stripped, so a
            # cell that is mostly discussion links does not win on raw length.
            for c in cells:
                if "RSPUSES" in c or c.startswith("{{"):
                    continue
                cleaned = _clean_wiki(c)
                if len(cleaned) > len(summary):
                    summary = cleaned
        rows.append({
            "name": ident.strip(),
            "status": status,
            "domains": domains,
            "summary": summary[:700],
        })
    return rows


def _clean_wiki(s: str) -> str:
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"<ref.*?</ref>|<[^>]+>", "", s, flags=re.S)
    s = re.sub(r"'{2,}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_rsp() -> dict:
    entries = []
    for n in range(1, RSP_SUBPAGES + 1):
        data = get_json(RSP_URL.format(n=n))
        entries.extend(_parse_rsp_table(data["parse"]["wikitext"]))
    index: dict[str, dict] = {}
    for e in entries:
        for d in e["domains"]:
            index.setdefault(d, e)
    return {"fetched": time.time(), "entries": entries, "by_domain": index}


# --------------------------------------------------------------------------
# Headbomb unreliable.js
# --------------------------------------------------------------------------

def _js_regex_to_py(pat: str) -> str:
    """The rules use plain JS regexes; the only construct we must neutralise is
    the %2F alternation used for URL-encoded slashes, which Python handles fine.
    Named groups and lookbehind are not used in this file."""
    return pat


def fetch_headbomb() -> dict:
    js = get_text(HEADBOMB_URL)
    body = js[js.find("var rules"):]
    rules = []
    for m in re.finditer(
            r"\{\s*comment\s*:\s*(?P<q>['\"])(?P<comment>.*?)(?P=q)\s*,\s*"
            r"regex\s*:\s*/(?P<re>.*?)/(?P<flags>[a-z]*)\s*,\s*"
            r"css\s*:\s*\{(?P<css>.*?)\}", body, re.S):
        colour = re.search(r"background-color\"?'?\s*:\s*[\"']([#a-zA-Z0-9]+)",
                           m.group("css"))
        colour = colour.group(1) if colour else "#fffdd0"
        pattern = _js_regex_to_py(m.group("re"))
        try:
            compiled = re.compile(pattern, re.I if "i" in m.group("flags") else 0)
        except re.error:
            continue
        rules.append({
            "comment": _clean_wiki(m.group("comment")),
            "pattern": pattern,
            "ignorecase": "i" in m.group("flags"),
            "colour": colour,
            "severity": HB_COLOUR.get(colour, "caution"),
            "_re": compiled,
        })
    return {"fetched": time.time(), "rules": rules}


# --------------------------------------------------------------------------
# Cache + public API
# --------------------------------------------------------------------------

class Reliability:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = data_dir
        self.dir.mkdir(exist_ok=True)
        self.rsp: dict = {}
        self.headbomb: dict = {}

    def load(self, force: bool = False) -> dict:
        self.rsp = self._cached("rsp.json", fetch_rsp, force)
        self.headbomb = self._cached("headbomb.json", fetch_headbomb, force,
                                     strip=("_re",))
        for r in self.headbomb.get("rules", []):
            if "_re" not in r:
                r["_re"] = re.compile(r["pattern"], re.I if r["ignorecase"] else 0)
        return {
            "rsp_entries": len(self.rsp.get("entries", [])),
            "rsp_domains": len(self.rsp.get("by_domain", {})),
            "headbomb_rules": len(self.headbomb.get("rules", [])),
            "fetched": max(self.rsp.get("fetched", 0),
                           self.headbomb.get("fetched", 0)),
        }

    def _cached(self, fname: str, fetch, force: bool, strip=()) -> dict:
        path = self.dir / fname
        if not force and path.exists():
            age = time.time() - path.stat().st_mtime
            if age < CACHE_TTL:
                try:
                    return json.loads(path.read_text())
                except json.JSONDecodeError:
                    pass
        try:
            data = fetch()
        except Exception:
            if path.exists():            # stale beats nothing
                return json.loads(path.read_text())
            raise
        dumpable = json.loads(json.dumps(
            data, default=lambda o: None))
        if strip:
            for r in dumpable.get("rules", []):
                for k in strip:
                    r.pop(k, None)
        path.write_text(json.dumps(dumpable, ensure_ascii=False))
        return data

    # -- lookups ----------------------------------------------------------

    def check_domain(self, domain: str) -> list[Verdict]:
        """All verdicts that apply to a hostname, most severe first."""
        out: list[Verdict] = []
        d = domain.lower().lstrip(".")
        if d.startswith("www."):
            d = d[4:]
        candidates = _domain_suffixes(d)

        by_dom = self.rsp.get("by_domain", {})
        for c in candidates:
            if c in by_dom:
                e = by_dom[c]
                sev, label = RSP_STATUS.get(e["status"], ("caution", e["status"]))
                out.append(Verdict(
                    source="rsp", severity=sev,
                    label=f"WP:RSP — {label}",
                    detail=e["summary"], name=e["name"], matched=c,
                    url="https://en.wikipedia.org/wiki/Wikipedia:Reliable_sources/"
                        "Perennial_sources#" + e["name"].replace(" ", "_")))
                break

        for kind, spec in BUILTIN.items():
            for c in candidates:
                if c in spec["domains"]:
                    out.append(Verdict(source="builtin", severity=spec["severity"],
                                       label=spec["label"], detail=spec["detail"],
                                       name=kind, matched=c))
                    break

        for rule in self.headbomb.get("rules", []):
            if rule["_re"].search(d):
                out.append(Verdict(source="headbomb", severity=rule["severity"],
                                   label=_hb_label(rule["comment"]),
                                   detail=rule["comment"], matched=d,
                                   url="https://en.wikipedia.org/wiki/User:Headbomb/unreliable"))
        return _rank(out)

    def check_doi(self, doi: str) -> list[Verdict]:
        out = []
        for rule in self.headbomb.get("rules", []):
            if rule["_re"].search(doi):
                out.append(Verdict(source="headbomb", severity=rule["severity"],
                                   label=_hb_label(rule["comment"]),
                                   detail=rule["comment"], matched=doi,
                                   url="https://en.wikipedia.org/wiki/User:Headbomb/unreliable"))
        return _rank(out)


def _hb_label(comment: str) -> str:
    c = comment.lower()
    for key, label in (
        # Most specific first: the preprint rule's own text contains the word
        # "predatory", so it has to be matched before the predatory rule.
        ("preprint or general repository", "Preprint or open repository"),
        ("paper mills", "Paper-mill retraction risk"),
        ("predatory", "Predatory journal or publisher"),
        ("hijacked", "Hijacked journal"),
        ("deprecated", "Deprecated source"),
        ("fake news", "Fake-news site"),
        ("misleading journal metric", "Misleading journal metric"),
        ("fake doi", "Fake DOI resolver"),
        ("blacklisted", "Blacklisted source"),
        ("ai-generated", "Possible AI-generated content"),
        ("sponsored", "Sponsored content"),
        ("wikimedia-related", "Circular / Wikimedia mirror"),
        ("preprint", "Preprint or open repository"),
        ("paper mills", "Paper-mill retraction risk"),
        ("generally unreliable", "Generally unreliable"),
        ("borderline", "Borderline source"),
        ("depends on", "Reliability varies by author or topic"),
    ):
        if key in c:
            return label
    return "Flagged by WP:CITEWATCH"


SEVERITY_ORDER = {"critical": 0, "serious": 1, "caution": 2, "info": 3, "good": 4}


def _rank(v: list[Verdict]) -> list[Verdict]:
    return sorted(v, key=lambda x: SEVERITY_ORDER.get(x.severity, 9))


def _domain_suffixes(d: str) -> list[str]:
    """example.co.uk -> [example.co.uk, co.uk]  (registrable-ish walk up)."""
    parts = d.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]
