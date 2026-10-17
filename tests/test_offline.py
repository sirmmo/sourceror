"""Offline unit tests — no network, so CI is fast and deterministic.

The live-API checks live in tests/smoke.py and run on a schedule instead.
"""
import re

import pytest

from sourceror import audit, cite, config, discover, reliability, wikitext


# --------------------------------------------------------------- wikitext

@pytest.mark.parametrize("text,expected", [
    ("R. Binns ''The Loch Ness Mystery Solved'' pp&nbsp;27-8", "shortcite"),
    ("Lorenzetti, p. 42", "shortcite"),
    ("Rossi 1990, pp. 12-14", "shortcite"),
    ("Sembra tuttavia che l'idea di una sola arcata sia stata dello Scamozzi.", "note"),
    ("Smith, John. The Big Book of Things. Oxford University Press, 1999.", "textcite"),
    ("", "empty"),
    ("ibid.", "text"),
])
def test_classify_prose(text, expected):
    assert wikitext._classify_prose(text) == expected


def test_template_parsing_handles_nesting():
    raw = "{{cita web|url=http://x.test/a?b=c|titolo=[[Foo|Bar]] e {{small|z}}|accesso=1}}"
    tpls = wikitext.find_templates(raw)
    assert tpls[0].name == "cita web"
    p = tpls[0].params
    assert p["url"] == "http://x.test/a?b=c"          # '=' inside a URL is not a separator
    assert p["titolo"] == "[[Foo|Bar]] e {{small|z}}"  # '|' inside a link/template is not either
    assert p["accesso"] == "1"


def test_ref_extraction_and_reuse():
    src = ("Testo<ref name=\"a\">{{cita web|url=http://x.test|titolo=T}}</ref> "
           "altro<ref name=\"a\" /> e<ref>Nota libera che spiega qualcosa di "
           "interessante e discorsivo.</ref>\n\n== Note ==\n")
    parsed = wikitext.parse_page(src)
    refs = parsed["refs"]
    assert len(refs) == 2                 # the named re-use is not a second citation
    assert refs[0].reused == 2
    assert refs[0].kind == "web"
    assert refs[1].kind == "note"


def test_reference_work_template_is_a_citation():
    src = "X<ref>{{DBI|nome=Tizio|autore=Caio|anno=1964}}</ref>"
    ref = wikitext.parse_page(src)["refs"][0]
    assert ref.kind == "referencework"
    assert ref.template.name == "dbi"


def test_citation_needed_and_unsourced_detection():
    src = ("== Storia ==\nIl ponte fu costruito nel 1591.{{senza fonte}}\n\n"
           + "Un paragrafo lungo senza alcuna nota. " * 12 + "\n")
    parsed = wikitext.parse_page(src)
    assert len(parsed["cn_tags"]) == 1
    assert "1591" in parsed["cn_tags"][0].claim
    assert parsed["cn_tags"][0].section == "Storia"
    assert len(parsed["unsourced"]) >= 1


def test_comments_are_stripped_but_offsets_preserved():
    src = "A<!-- hidden <ref>x</ref> -->B<ref>Rossi, p. 3</ref>"
    parsed = wikitext.parse_page(src)
    assert len(parsed["refs"]) == 1       # the commented-out ref does not count


def test_doi_and_isbn_extraction():
    src = "<ref>{{cita pubblicazione|titolo=T|doi=10.1038/nature12373}}</ref>"
    assert wikitext.parse_page(src)["refs"][0].doi == "10.1038/nature12373"
    src2 = "<ref>{{cita libro|titolo=T|isbn=978-88-06-12345-6}}</ref>"
    assert wikitext.parse_page(src2)["refs"][0].isbn == "9788806123456"


# ------------------------------------------------------------- reliability


def test_rsp_table_parses_all_statuses(kb):
    by_name = {e["name"]: e for e in kb.rsp["entries"]}
    assert by_name["Daily Mail"]["status"] == "d"
    assert by_name["BBC"]["status"] == "gr"
    assert by_name["112 Ukraine"]["status"] == "gu"
    assert by_name["Vice"]["status"] == "nc"


def test_rsp_summary_is_prose_not_discussion_links(kb):
    """Regression: the summary used to pick the discussions cell, yielding
    strings like '1 2 3 4 5'."""
    for e in kb.rsp["entries"]:
        assert len(e["summary"]) > 40, e
        assert not re.fullmatch(r"[\d\s]+", e["summary"])
    assert "deprecated" in {e["name"]: e for e in kb.rsp["entries"]}["Daily Mail"]["summary"]


def test_domain_verdicts(kb):
    assert kb.check_domain("dailymail.co.uk")[0].severity == "critical"
    assert kb.check_domain("www.bbc.com")[0].severity == "good"
    assert kb.check_domain("vice.com")[0].severity == "caution"
    assert kb.check_domain("unheard-of-site.test") == []


def test_builtin_categories_apply_without_rsp(kb):
    assert any(v.name == "circular" for v in kb.check_domain("it.wikipedia.org"))
    assert any(v.name == "user_generated" for v in kb.check_domain("imdb.com"))
    assert any(v.name == "press_release" for v in kb.check_domain("prnewswire.com"))
    assert any(v.name == "self_published" for v in kb.check_domain("medium.com"))


def test_headbomb_labels_preprint_before_predatory(kb):
    """Regression: the preprint rule's own text contains the word 'predatory',
    so a naive match labelled academia.edu a predatory publisher."""
    labels = [v.label for v in kb.check_domain("academia.edu")]
    assert "Preprint or open repository" in labels
    assert "Predatory journal or publisher" not in labels
    assert kb.check_domain("scirp.org")[0].label == "Predatory journal or publisher"


def test_predatory_doi_prefix(kb):
    assert kb.check_doi("10.4172/2157-7595.1000234")[0].severity == "critical"
    assert kb.check_doi("10.1038/nature12373") == []


def test_domain_suffix_walk():
    assert reliability._domain_suffixes("a.b.co.uk") == ["a.b.co.uk", "b.co.uk", "co.uk"]


# ------------------------------------------------------------------ audit

def _report(**kw):
    base = dict(index=1, section="S", kind="web", name=None, reused=1, urls=[],
                domain=None, doi=None, isbn=None, display="d", raw="")
    base.update(kw)
    return audit.CitationReport(**base)


def test_critical_flag_caps_score_regardless_of_good_ones():
    r = _report()
    r.add(audit.Flag("COMPLETE", "good", "t"), audit.Flag("PEER_REVIEWED", "good", "t"),
          audit.Flag("RETRACTED", "critical", "t"))
    r.finalise()
    assert r.grade == "unreliable"
    assert r.score <= audit.SEVERITY_CAP["critical"]


def test_serious_flag_caps_at_weak():
    r = _report()
    r.add(audit.Flag("COMPLETE", "good", "t"), audit.Flag("DEAD_LINK", "serious", "t"))
    r.finalise()
    assert r.score <= audit.SEVERITY_CAP["serious"]
    assert r.grade in ("weak", "unreliable")


def test_clean_citation_scores_well():
    r = _report()
    r.add(audit.Flag("COMPLETE", "good", "t"), audit.Flag("RSP_RELIABLE", "good", "t"))
    r.finalise()
    assert r.grade == "solid"


def test_flags_sorted_most_severe_first():
    r = _report()
    r.add(audit.Flag("NO_ACCESS_DATE", "info", "i"), audit.Flag("RETRACTED", "critical", "c"),
          audit.Flag("MISSING_DATE", "caution", "w"))
    r.finalise()
    assert [f.severity for f in r.flags] == ["critical", "caution", "info"]


def test_bibliography_surnames_resolve_short_cites():
    tpl = wikitext.find_templates("{{cita libro|Giulio|Lorenzetti|Venezia|1963}}")[0]
    assert "Lorenzetti" in audit._biblio_surnames([tpl])


# ---------------------------------------------------------------- discover

def test_relevance_gate_rejects_keyword_coincidence():
    """Regression: OpenAlex ranked a superstrings paper first for 'Aldo Moro'."""
    ok, cov = discover._relevant_enough(
        "Aldo Moro",
        {"title": "New dimensions at a millimeter to a Fermi and superstrings"})
    assert not ok and cov == 0.0


def test_relevance_gate_accepts_real_match():
    ok, cov = discover._relevant_enough(
        "Aldo Moro", {"title": "The Aldo Moro Murder Case"})
    assert ok and cov == 1.0


def test_relevance_gate_needs_majority_of_terms():
    # "ponte" alone out of {ponte, rialto, venezia} is not enough.
    ok, _ = discover._relevant_enough(
        "Ponte di Rialto Venezia", {"title": "Il Ponte Mosca, verifica statica"})
    assert not ok
    ok2, _ = discover._relevant_enough(
        "Ponte di Rialto Venezia", {"title": "Il restauro del Ponte di Rialto a Venezia"})
    assert ok2


def test_claim_query_keeps_names_and_years():
    q = discover.claim_query(
        "Il ponte fu progettato da Antonio da Ponte nel 1591 dopo il crollo.",
        "Ponte di Rialto")
    assert q.startswith("Ponte di Rialto")   # the article title anchors the query
    assert "1591" in q                       # dates are kept
    assert "Antonio" in q                    # proper nouns are kept
    terms = q[len("Ponte di Rialto"):].split()
    assert not ({"dopo", "nel", "che", "una"} & set(terms))   # stopwords dropped


# -------------------------------------------------------------------- cite

def test_italian_and_english_templates_differ():
    work = {"authors": ["A. Rossi"], "title": "T", "venue": "Rivista",
            "year": 1999, "doi": "10.1/x"}
    it = cite.journal(work, "it")
    en = cite.journal(work, "en")
    assert it.startswith("{{cita pubblicazione|") and "|titolo=T" in it and "|anno=1999" in it
    assert en.startswith("{{cite journal|") and "|title=T" in en and "|date=1999" in en


def test_access_date_is_localised_without_a_system_locale():
    """Regression: strftime('%B') gave English month names in the container."""
    assert re.match(r"\d{1,2} settembre \d{4}|\d{1,2} \w+ \d{4}", cite._today("it"))
    assert cite._today("it").split()[1] in cite.MONTHS["it"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cite._today("en"))


def test_pipes_in_values_do_not_break_the_template():
    out = cite.journal({"title": "A|B", "authors": [], "venue": None}, "it")
    assert out.count("|titolo=") == 1
    assert "A-B" in out


def test_archive_fix_dialect():
    assert "urlarchivio=" in cite.archive_fix("u", "a", "2011-01-01", "it")
    assert "archive-url=" in cite.archive_fix("u", "a", "2011-01-01", "en")


# ------------------------------------------------------------------ config

def test_config_roundtrip_and_key_masking(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_cache", None)
    fake = "DUMMY-not-a-real-credential-0000"
    config.save({"llm_api_key": fake, "llm_model": "m/x"})
    pub = config.public()
    assert pub["llm_model"] == "m/x"
    assert pub["llm_configured"] is True
    assert fake not in pub["llm_api_key"]        # never leaked to the browser
    assert pub["llm_api_key"].startswith("DUMMY-")   # masked, not blank


def test_resolver_and_www_redirects_are_not_flagged(kb, monkeypatch):
    """doi.org always forwards to the publisher, and www/no-www is the same
    site; neither means the domain changed hands."""
    from sourceror import apis

    def fake(url, **kw):
        target = {"https://doi.org/10.1/x": "https://www.nature.com/articles/x",
                  "https://bbc.com/a": "https://www.bbc.com/a",
                  "https://old.test/a": "https://casino.test/"}[url]
        return apis.LinkHealth(url=url, status=200, ok=True, redirect_to=target)
    monkeypatch.setattr(apis, "check_link", fake)
    aud = audit.Auditor(kb, "en")
    for url, flagged in [("https://doi.org/10.1/x", False), ("https://bbc.com/a", False),
                         ("https://old.test/a", True)]:
        rep = _report(urls=[url])
        aud._check_link(rep, url, {})
        assert any(f.code == "UNVERIFIABLE" for f in rep.flags) is flagged, url
