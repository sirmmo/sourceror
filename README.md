# Sourceror

**A source-checking helper for Wikipedia editors, and for anyone reading the
news.** It audits the citations already in an article and looks for sources to
fill the gaps. A [Chrome extension](#browser-extension) does the same for any
web article: who is behind each link, and which claims rely on nothing.

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
docker run -d --name sourceror -p 127.0.0.1:5010:5000 \
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
| `BIND` | `127.0.0.1` | Interface to bind (compose only) — see the warning below |
| `PORT` | `5010` | Host port (compose only) |
| `SOURCEROR_LANG` | `it` | Default wiki language |
| `FLASK_DEBUG` | `0` | Set `1` for the reloader |

> ⚠️ **Do not expose Sourceror to a network without putting authentication in
> front of it.** It has none of its own, and `POST /api/llm` lets any caller
> change `llm_base_url`; a following `POST /api/llm/test` would then send your
> `Authorization: Bearer <api key>` header to whatever endpoint they chose.
> Compose therefore binds `127.0.0.1` by default. The `docker run` example
> above publishes on all interfaces — use `-p 127.0.0.1:5010:5000` unless the
> host is firewalled.

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

## Browser extension

`extension/` is a Chrome side panel that checks **any article**: news, blog
posts, reports, and Wikipedia entries in every language. It is also published
on its own as [sirmmo/sourceror-extension](https://github.com/sirmmo/sourceror-extension);
`extension/` here is the source of truth, and the mirror is updated with
`git subtree push --prefix=extension https://github.com/sirmmo/sourceror-extension.git main`.

1. Start the server (see *Quick start*). The extension talks only to
   `http://127.0.0.1:5010`; the address can be changed in its settings.
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick the `extension/` folder.
3. On any article, click the Sourceror icon (or press **Alt+Shift+S**), then
   **Check this page's sources**.

On a web article it:

- extracts the article body as you see it, with Mozilla's
  [Readability](https://github.com/mozilla/readability). Paywalls, cookie walls
  and bot blocks that would stop a server-side fetch do not matter;
- judges every outbound link and every DOI in the text with the same checks
  used for Wikipedia references (WP:RSP, CITEWATCH, retractions, dead links).
  Share buttons, navigation and tracking parameters are dropped first, and
  links back to the publisher's own site are reported separately;
- lists the article's **checkable claims** (figures, study results, records,
  causal statements) and sorts them into *linked*, *linked to a weak source*,
  *attributed in the text but not linked*, and *no source*. Claims with no
  source get candidate sources from OpenAlex and Crossref. With an LLM
  configured the model reads the claims; without one a simple, transparent
  heuristic does, and misses more;
- marks the page itself: a coloured dot after each judged link and a
  highlight on each problem claim. Click a mark to jump to its entry in the
  panel, or click an entry to scroll the page to it.

On Wikipedia it runs the normal article audit and colours the reference list
and the `[1]` markers in the text.

The publisher of the page gets its own WP:RSP verdict when it has one. Most
outlets have none, and the panel says so instead of implying they are reliable.

**Permissions.** The extension asks to read all sites (`<all_urls>`) because it
has to read whichever article you open. It reads a page only when you click
the button, and sends what it extracts only to your Sourceror server.

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
| `POST /api/page` | `{url, title, lang, paragraphs, links, …}` from the extension → `{job}` (JSON only) |
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
python -m pytest tests -q                   # offline suite, no network
python tests/smoke.py                       # live: hits the real APIs
```

The `test_*.py` files run against fixtures and never touch the network, so CI
is fast and deterministic. `tests/smoke.py` exercises every layer against the
live services and runs weekly as a canary.

The extension has an end-to-end test that loads it into Chromium, analyses
the fixture article (`tests/fixtures/article.html`) and checks the panel and
the page marks. It needs a running server:

```bash
docker run --rm --network host -v "$PWD":/src -w /src \
  mcr.microsoft.com/playwright:v1.60.0 \
  sh -c "npm i --no-save --no-package-lock --silent playwright@1.60.0 && node tests/extension_e2e.mjs"
```

Pass URLs to test other pages; `USE_LLM=1` turns the LLM on, `CHECK_LINKS=0`
skips link checks, and `UI_LANG=it-IT` shows the Italian interface.

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
  page.py               any web article: links, claims, publisher
  llm.py, config.py     optional LLM layer
templates/index.html    UI (vanilla JS, no build step)
extension/              Chrome side panel (Manifest V3, no build step)
tests/                  offline suite, live smoke test, extension e2e
```

---

## License

No license is set yet, which means default copyright — others cannot legally
reuse it. If you intend this to be reusable, add one (MIT is the usual choice
for a tool like this).
