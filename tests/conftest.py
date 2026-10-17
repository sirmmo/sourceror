import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from fixtures_rsp import HEADBOMB_JS, RSP_WIKITEXT
from sourceror import reliability


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """A knowledge base built entirely from the fixtures — no network.

    `reliability` binds get_text at import time, so it is that name which has
    to be replaced; patching sourceror.http.get_text would silently let the
    test hit the live wiki instead.
    """
    monkeypatch.setattr(reliability, "get_text", lambda url, **kw: HEADBOMB_JS)
    r = reliability.Reliability(tmp_path)
    entries = reliability._parse_rsp_table(RSP_WIKITEXT)
    by_domain = {}
    for e in entries:
        for d in e["domains"]:
            by_domain.setdefault(d, e)
    r.rsp = {"entries": entries, "by_domain": by_domain}
    r.headbomb = reliability.fetch_headbomb()
    assert len(r.headbomb["rules"]) == 3, "fixture JS was not used"
    return r

