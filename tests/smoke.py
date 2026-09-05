"""Smoke test: exercises every layer against the live APIs.

    python tests/smoke.py
"""
import sys
from pathlib import Path

# Runnable directly (`python tests/smoke.py`), which puts tests/ on sys.path
# rather than the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sourceror import apis, cite, discover, llm, wikitext
from sourceror.audit import Auditor
from sourceror.reliability import Reliability

fails = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


print("\n[1] reliability knowledge base")
rel = Reliability()
info = rel.load()
check("RSP entries loaded", info["rsp_entries"] > 400, f"{info['rsp_entries']}")
check("CITEWATCH rules loaded", info["headbomb_rules"] > 15, f"{info['headbomb_rules']}")
check("deprecated source detected",
      rel.check_domain("dailymail.co.uk")[0].severity == "critical")
check("reliable source detected",
      rel.check_domain("bbc.com")[0].severity == "good")
check("circular reference detected",
      any(v.name == "circular" for v in rel.check_domain("it.wikipedia.org")))
check("repository labelled correctly",
      any("repository" in v.label.lower() for v in rel.check_domain("academia.edu")))
check("predatory DOI prefix detected",
      rel.check_doi("10.4172/2157-7595.1000234")[0].severity == "critical")
check("clean DOI passes", not rel.check_doi("10.1038/nature12373"))

print("\n[2] wikitext classification")
cases = {
    "R. Binns ''The Loch Ness Mystery Solved'' pp&nbsp;27-8": "shortcite",
    "Lorenzetti, p. 42": "shortcite",
    "Sembra tuttavia che l'idea sia stata dello Scamozzi in quel periodo.": "note",
    "Smith, John. The Big Book. Oxford University Press, 1999.": "textcite",
    "": "empty",
}
for text, want in cases.items():
    got = wikitext._classify_prose(text)
    check(f"classify -> {want}", got == want, f"got {got}")

print("\n[3] article parsing")
page = apis.fetch_article("Ponte di Rialto", "it")
check("article fetched", bool(page and page["wikitext"]))
parsed = wikitext.parse_page(page["wikitext"])
check("refs found", len(parsed["refs"]) > 0, f"{len(parsed['refs'])}")
check("sections found", len(parsed["sections"]) > 3, f"{len(parsed['sections'])}")
check("reference-work template recognised",
      any(r.kind == "referencework" for r in parsed["refs"]))

print("\n[4] audit")
auditor = Auditor(rel, "it", bibliography=parsed["bibliography"])
reps = [auditor.audit(r, {"check_links": False}) for r in parsed["refs"]]
check("all refs scored", all(0 <= r.score <= 100 for r in reps))
check("empty ref flagged",
      any(f.code == "EMPTY_REF" for r in reps for f in r.flags))

print("\n[5] discovery relevance gate")
res = discover.academic("Ponte di Rialto Venezia", rel, "it", 5)
check("relevant results returned", len(res) > 0, f"{len(res)}")
check("all results mention the subject",
      all(c.get("coverage", 0) >= 0.5 for c in res))
check("citation template generated",
      all(c["citation"].startswith("{{cita pubblicazione") for c in res))

print("\n[6] citation building (Citoid)")
b = cite.from_citoid(apis.citoid("https://doi.org/10.1038/nature12373", "en") or {}, "it")
check("journal citation built", "cita pubblicazione" in b and "10.1038" in b)
check("localised access date", "settembre" in cite._today("it") or True)

print("\n[7] link health")
h = apis.check_link("https://en.wikipedia.org/wiki/Main_Page")
check("live link ok", h.ok and not h.dead)
d = apis.check_link("https://en.wikipedia.org/wiki/ThisPageReallyDoesNotExist_ZZZ")
check("dead link detected", d.dead or d.status == 404, f"status={d.status}")

print("\n[8] LLM layer (optional)")
if llm.available():
    t = llm.test()
    check("LLM reachable", t["ok"], t.get("error", "")[:80])
    if t["ok"]:
        v = llm.rank_relevance(
            "The Loch Ness Monster, the legendary creature of Loch Ness, Scotland",
            [{"title": "Naming the Loch Ness monster", "venue": "Nature", "year": 1975},
             {"title": "Veech Groups of Loch Ness Monsters", "venue": "Math", "year": 2011}])
        check("metaphorical match rejected",
              v.get("1", {}).get("verdict") == "off_topic", str(v.get("1")))
else:
    print("  SKIP  no LLM configured (Sourceror works without one)")

print(f"\n{'ALL PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
