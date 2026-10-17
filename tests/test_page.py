"""Offline tests for analysing arbitrary web articles (sourceror.page)."""
import pytest

from sourceror import apis, llm, page


def _payload():
    return {
        "url": "https://www.example-news.test/2026/10/story?utm_source=x",
        "title": "Unemployment falls to record low",
        "lang": "en",
        "paragraphs": [
            {"i": 0, "text": "Unemployment fell to 4.1 percent in September, "
                             "the lowest rate on record, the agency said.",
             "marked": "Unemployment fell to 4.1 percent in September, "
                       "the lowest rate on record, the agency said."},
            {"i": 1, "text": "A study found that remote work raised productivity "
                             "by 13 percent across 16,000 workers.",
             "marked": "A study [L0] found that remote work raised productivity "
                       "by 13 percent across 16,000 workers."},
            {"i": 2, "text": "Wages grew 6% last year, faster than in any year "
                             "since 1990, while rents doubled.",
             "marked": "Wages grew 6% last year [L1], faster than in any year "
                       "since 1990, while rents doubled."},
            {"i": 3, "text": "The paper is doi:10.4172/2157-7595.1000234 in a journal."},
        ],
        "links": [
            {"id": 0, "href": "https://doi.org/10.1038/nature12373", "text": "A study", "p": 1},
            {"id": 1, "href": "https://www.dailymail.co.uk/news/wages.html?utm_campaign=z",
             "text": "last year", "p": 2},
            {"id": 2, "href": "https://www.dailymail.co.uk/news/wages.html", "text": "again", "p": 2},
            {"id": 3, "href": "https://www.facebook.com/sharer/sharer.php?u=x", "text": "Share"},
            {"id": 4, "href": "mailto:desk@example-news.test", "text": "Email us"},
            {"id": 5, "href": "https://example-news.test/2026/09/earlier", "text": "earlier", "p": 0},
            {"id": 6, "href": "https://www.example-news.test/2026/10/story", "text": "self"},
        ],
    }


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(apis, "openalex_doi", lambda doi: None)
    monkeypatch.setattr(apis, "crossref_doi",
                        lambda doi: {"type": "journal-article", "DOI": doi})
    monkeypatch.setattr(apis, "check_link",
                        lambda url, **kw: pytest.fail(f"network call to {url}"))


def test_clean_links_drops_furniture_and_merges_duplicates():
    links = page.clean_links(_payload()["links"], _payload()["url"])
    hrefs = [l["href"] for l in links]
    assert not any("facebook" in h or h.startswith("mailto") for h in hrefs)
    assert "https://www.example-news.test/2026/10/story" not in hrefs   # the page itself
    dm = [l for l in links if "dailymail" in l["href"]]
    assert len(dm) == 1, "tracking parameters must not split one source in two"
    assert dm[0]["ids"] == [1, 2]


def test_normalise_url_strips_tracking_but_keeps_real_parameters():
    assert page.normalise_url("HTTPS://Ex.test/a?id=7&utm_medium=m&fbclid=q#frag") \
        == "https://ex.test/a?id=7"
    assert page.normalise_url("not a url") == ""


def test_heuristic_claims_classify_by_link_and_attribution():
    claims = page.heuristic_claims(page._paragraphs(_payload()))
    by_text = {c["text"][:20]: c for c in claims}
    assert by_text["Unemployment fell to"]["status"] == "attributed"   # "the agency said"
    assert by_text["A study found that r"]["status"] == "linked"
    assert by_text["A study found that r"]["links"] == [0]
    assert "[L" not in by_text["A study found that r"]["text"]


def test_analyze_page_end_to_end_offline(kb, offline):
    res = page.analyze_page(_payload(), rel=kb, lang="en", check_links=False,
                            discover_sources=False, use_llm=False)
    assert res["page"]["domain"] == "example-news.test"
    src = {s["href"]: s for s in res["sources"]}

    dm = src["https://www.dailymail.co.uk/news/wages.html"]
    assert dm["grade"] == "unreliable"
    assert dm["link_ids"] == [1, 2]

    assert src["https://doi.org/10.1038/nature12373"]["doi"] == "10.1038/nature12373"
    assert src["https://example-news.test/2026/09/earlier"]["same_site"]

    # A DOI written out in the text, with no link, is still checked.
    bare = [s for s in res["sources"] if s["kind"] == "doi"]
    assert bare and bare[0]["doi"] == "10.4172/2157-7595.1000234"
    assert bare[0]["grade"] == "unreliable"          # predatory prefix

    # A link to an unreliable source does not make a claim sourced.
    wages = next(c for c in res["claims"] if c["text"].startswith("Wages grew"))
    assert wages["status"] == "weakly_linked"
    assert wages["source_grade"] == "unreliable"
    assert res["claims_by"] == "heuristic"
    assert res["stats"]["same_site_links"] == 1


def test_link_kind_gets_no_template_flags(kb, offline):
    res = page.analyze_page(_payload(), rel=kb, lang="en", check_links=False,
                            discover_sources=False, use_llm=False)
    codes = {f["code"] for s in res["sources"] for f in s["flags"]}
    assert not codes & {"MISSING_TITLE", "MISSING_DATE", "MISSING_WORK", "BARE_URL"}


def test_llm_claims_are_validated(monkeypatch):
    """Model output is untrusted: bad paragraph numbers and empty texts go,
    'linked' without ids becomes 'unsourced', [L..] tokens are stripped."""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"claims": [
        {"p": 1, "text": "A study [L0] found that remote work raised productivity",
         "status": "linked", "links": ["L0"]},
        {"p": 99, "text": "Nowhere to be found in this article at all", "status": "unsourced"},
        {"p": "P0", "text": "Unemployment fell to 4.1 percent in September",
         "status": "linked", "links": []},
        {"p": 2, "text": "", "status": "unsourced"},
    ]})
    claims, by = page._claims(page._paragraphs(_payload()), "t", use_llm=True)
    assert by == "llm"
    assert [c["p"] for c in claims] == [1, 0]
    assert claims[0]["links"] == [0] and "[L" not in claims[0]["text"]
    assert claims[0]["verbatim"]
    assert claims[1]["status"] == "unsourced"


def test_page_endpoint_rejects_non_json(monkeypatch):
    """A cross-origin form or text/plain POST must not start a job."""
    import app as app_module
    client = app_module.app.test_client()
    r = client.post("/api/page", data='{"paragraphs": ["x"]}',
                    content_type="text/plain")
    assert r.status_code == 400
    r = client.post("/api/page", json={"url": "https://x.test"})
    assert r.status_code == 400 and r.get_json()["error"] == "empty_page"


def test_job_runner_calling_convention(kb, offline):
    """app._run_job calls fn(subject, lang, rel=..., progress=..., **opts) for
    both analysers; a keyword-only lang once broke every page job."""
    seen = []
    res = page.analyze_page(_payload(), "en", rel=kb,
                            progress=lambda *a: seen.append(a),
                            check_links=False, discover_sources=False, use_llm=False)
    assert res["page"]["lang"] == "en" and seen
