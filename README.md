# Sourceror

**A source-checking helper for Wikipedia editors.** It audits the citations
already in an article and looks for sources to fill the gaps.

[![CI](https://github.com/sirmmo/sourceror/actions/workflows/ci.yml/badge.svg)](https://github.com/sirmmo/sourceror/actions/workflows/ci.yml)
[![Publish image](https://github.com/sirmmo/sourceror/actions/workflows/publish.yml/badge.svg)](https://github.com/sirmmo/sourceror/actions/workflows/publish.yml)
[![Upstream canary](https://github.com/sirmmo/sourceror/actions/workflows/canary.yml/badge.svg)](https://github.com/sirmmo/sourceror/actions/workflows/canary.yml)

🇮🇹 *[Versione italiana di questo documento](README.it.md).*

Sourceror does not invent its own opinion of what a good source is. Reliability
verdicts come from the lists the Wikipedia community actually maintains, and
bibliographic checks come from Crossref and OpenAlex.

---

## Quick start

Pull the published image — nothing to build:

```bash
docker run -d --name sourceror -p 5010:5000 \
  -v sourceror-data:/app/data \
  ghcr.io/sirmmo/sourceror:latest
```

Then open <http://127.0.0.1:5010>.

Or from source:

```bash
git clone https://github.com/sirmmo/sourceror
cd sourceror
docker compose up -d --build
```

On first run it downloads the reliability lists from Wikipedia (a few seconds)
and caches them for 24 hours in the `sourceror-data` volume. Mount that volume
to keep the cache across restarts.

| Variable | Default | |
|---|---|---|
| `PORT` | `5010` | Host port (compose only) |
| `SOURCEROR_LANG` | `it` | Default wiki language |
| `FLASK_DEBUG` | `0` | Set `1` for the reloader |

---

## What it checks

Every `<ref>` in the article is extracted, classified and scored.

| Check | Where the verdict comes from |
|---|---|
| Deprecated / unreliable / no-consensus / blacklisted source | [WP:RSP](https://en.wikipedia.org/wiki/Wikipedia:Reliable_sources/Perennial_sources) — ~490 sources the community has repeatedly discussed |
| Predatory publisher, hijacked journal, fake DOI resolver, preprint repository | [User:Headbomb/unreliable](https://en.wikipedia.org/wiki/User:Headbomb/unreliable) (CITEWATCH), matched by domain **and by DOI prefix** |
| **Retracted publication** | Crossref (`update-to`) + OpenAlex (`is_retracted`) |
| DOI that does not resolve or was invented | Crossref + OpenAlex |
| Peer review, DOAJ listing, open access, citation count | OpenAlex |
| Dead link, soft 404, suspicious cross-domain redirect | direct HTTP request |
| Archived copy available | Wayback Machine |
| Article citing its own subject's website | Wikidata `P856` |
| Circular reference to Wikipedia | built-in table |
| Self-published platform, user-generated content, social media, press-release wire | built-in table |
| Missing title / date / author / publisher | citation-template parsing |
| Bare URL, empty `<ref>`, unidentifiable work | wikitext parsing |

Each citation gets a score of 0–100 and a grade — **solid / ok / weak /
unreliable**. The worst flag's severity *caps* the score: a perfectly formatted
citation to a deprecated source is still unreliable.

### It avoids the obvious false alarms

A naive checker would flag half of a well-sourced article. Sourceror
distinguishes:

- **Reference-work templates** — `{{DBI}}`, `{{Treccani}}`, `{{Cite ODNB}}` are
  proper citations, not untemplated text;
- **Explanatory notes** — a footnote that is the author's commentary is not
  scored as a source at all;
- **Short citations** — `Lorenzetti, p. 42` is resolved against the article's
  Bibliography section, and flagged only if nothing there matches;
- **Offline sources** — a book written out in prose is a *formatting* issue,
  not a verifiability one. Sources need not be online
  ([WP:SOURCEACCESS](https://en.wikipedia.org/wiki/Wikipedia:Verifiability#Access_to_sources)).

### Finding missing sources

Sourceror locates `{{citation needed}}` / `{{senza fonte}}` tags and long
unsourced paragraphs, then searches OpenAlex, Crossref and OpenLibrary. Results
are filtered by lexical overlap with the query — without that gate the APIs
happily return a superstring-theory paper as the top hit for *Aldo Moro* —
ranked by peer-review status, citations and open access, and returned with a
filled citation template ready to paste (`{{cite journal}}` on en.wiki,
`{{cita pubblicazione}}` on it.wiki).

### Tools

- **URL → citation**: uses **Citoid**, Wikimedia's hosted Zotero translation
  server, to extract metadata and emit a filled template.
- **Domain check**: is this outlet usable as a source?

---

## Optional LLM

**Sourceror is fully functional without one.** When configured, an LLM is used
at exactly two points where APIs and heuristics are genuinely weak:

1. **rewriting a claim** into sensible scholarly search queries;
2. **judging relevance** — telling a work *about* the subject from one that merely
   borrows its name. Lexical matching cannot know that *"Cancer Stem Cells: the
   Loch Ness Monster of tumour biology"* is not about the lake.

Configure it under **Strumenti → LLM** in the UI, or by environment. Any
OpenAI-compatible endpoint works: OpenRouter (default), OpenAI, LiteLLM, or a
local vLLM / llama.cpp server.

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1   # or OPENAI_BASE_URL
LLM_API_KEY=sk-or-v1-...                    # or OPENROUTER_API_KEY / OPENAI_API_KEY
LLM_MODEL=openai/gpt-4o-mini                # or OPENROUTER_MODEL / OPENAI_MODEL
```

Settings saved from the UI go to `data/config.json` (mode `0600` — it holds the
key) and take precedence over the environment. Every call degrades silently to
the no-LLM path on failure.

> **Model choice dominates latency.** *Reasoning* models spend the token budget
> on hidden reasoning *before* answering: with a low `max_tokens` they return an
> empty completion, and each call costs tens of seconds. Measured with
> `deepseek-v4-flash`: judging 12 candidates took **127 s** as one request,
> **28 s** split into parallel batches of 4 (what Sourceror now does); query
> generation ~50 s per claim. End-to-end on *Loch Ness Monster* (187 refs):
> **31.9 s** without an LLM, **74.6 s** with. A non-reasoning model is far
> faster at equal quality. The **usa LLM** checkbox disables it per run.

---

## HTTP API

| Endpoint | |
|---|---|
| `POST /api/analyze` | `{title, lang, check_links, discover_sources, use_llm}` → `{job}` |
| `GET /api/job/<id>` | state, progress and result |
| `GET /api/search?q=&lang=` | title autocomplete |
| `POST /api/cite` | `{url, lang}` → filled template via Citoid |
| `POST /api/discover` | `{claim, title, lang}` → candidate sources |
| `GET /api/domain?d=` | reliability verdict for a domain |
| `GET\|POST /api/llm` | read/write LLM settings |
| `POST /api/llm/test`, `GET /api/llm/models` | connectivity and model list |
| `GET /api/kb`, `POST /api/kb/refresh` | knowledge-base status and reload |

Analysis runs as a background job; poll `/api/job/<id>` for progress. `title`
also accepts a full Wikipedia URL.

---

## Limits — read before trusting it

- **Sourceror does not read the sources.** It checks *who published* and
  *whether the link works* — not whether the cited text supports the claim.
  Citation drift stays invisible.
- **WP:RSP is en.wikipedia custom**, not policy on other wikis, and it is
  explicitly context-dependent: a "generally unreliable" source can be correct
  for a claim about itself. Sourceror flags; it does not decide.
- **"Not listed" does not mean reliable.** Most outlets, including most
  non-English ones, appear in neither list.
- **academia.edu and ResearchGate cannot be searched** — both return HTTP 403 to
  automated requests and have no public API. They are treated as a *signal*
  instead: a citation pointing there is a self-published repository, and
  Sourceror looks up the peer-reviewed version of the same paper on OpenAlex and
  offers that citation instead.
- **Link checking costs one HTTP request per URL** — about half a minute on an
  article with 200 refs. It can be switched off.
- **An HTTP 403 during checking is not a defect in the source**; many sites
  block bots. It is reported as "verify by hand".

---

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_offline.py -q   # 34 tests, no network
python tests/smoke.py                       # live: hits the real APIs
```

`tests/test_offline.py` runs against fixtures and never touches the network, so
CI is fast and deterministic. `tests/smoke.py` exercises every layer against the
live services and runs weekly as a canary.

### Why there is a canary

Sourceror parses the *wikitext* of WP:RSP and the *JavaScript* of
`User:Headbomb/unreliable.js`. Both are community-maintained pages that can be
restructured at any time. If that happens the parsers return nothing, the
knowledge base goes empty, and every source silently looks clean — a false
negative here is much worse than a crash. The
[canary workflow](.github/workflows/canary.yml) checks weekly and opens an issue
labelled `upstream-drift` when the live data stops parsing.

### Layout

```
app.py                  Flask routes, background jobs
sourceror/
  wikitext.py           wikitext parser: refs, templates, sections, {{cn}} tags
  reliability.py        WP:RSP + CITEWATCH + built-in tables, cached to disk
  apis.py               MediaWiki, Wikidata, Citoid, Crossref, OpenAlex,
                        OpenLibrary, Wayback
  audit.py              scoring engine: flags, score, grade
  discover.py           source search, relevance gate, ranking
  cite.py               citation templates in the target wiki's dialect
  analyze.py            orchestration
  llm.py, config.py     optional LLM layer
templates/index.html    UI (vanilla JS, no build step)
tests/                  offline suite + live smoke test
```

---

## License

No license is set yet, which means default copyright — others cannot legally
reuse it. If you intend this to be reusable, add one (MIT is the usual choice
for a tool like this).
