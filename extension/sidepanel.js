/* Sourceror side panel: drives the analysis of the active tab.
 *
 * All requests to the Sourceror server go from here, never from the content
 * script, so they carry the extension's host permission rather than the
 * page's origin. Everything shown comes from the page or third-party APIs and
 * is escaped before it reaches innerHTML.
 */
'use strict';

const T = {
  en: {
    settings: 'Settings', server_url: 'Sourceror server', opt_links: 'Check that links are alive',
    opt_llm: 'Use the LLM (if configured)', opt_discover: 'Search for sources for unsourced claims',
    server_hint: 'Start it with “docker compose up -d” in the sourceror folder. It listens only on this computer.',
    analyze: 'Check this page’s sources', analyzing: 'Checking…', reanalyze: 'Check again',
    clear_marks: 'Clear marks',
    g_solid: 'solid', g_ok: 'acceptable', g_weak: 'weak', g_unreliable: 'unreliable',
    limits: 'Sourceror judges who published a source and whether the link works. It does not read the source, so it cannot tell whether it really says what the article claims.',
    not_web: 'Open an article (a web page or a Wikipedia entry) to check its sources.',
    no_server: s => `The Sourceror server is not reachable at <code>${s}</code>. Start it with <code>docker compose up -d</code>, or fix the address in settings ⚙.`,
    inject_failed: 'Chrome does not let extensions read this page (browser pages, the Web Store, PDFs).',
    no_text: 'No article text was found on this page.',
    not_found: 'Wikipedia has no article with this title.',
    failed: 'The analysis failed: ',
    timeout: 'The analysis is taking too long. The server may be overloaded; try again with link checks off.',
    starting: 'Starting', fetching: 'Fetching the article', 'checking citations': 'Checking citations',
    'checking sources': 'Checking linked sources', 'reading claims': 'Reading the claims',
    'searching for sources': 'Searching for sources', complete: 'Done',
    outlet: 'Publisher', unlisted: 'Not in WP:RSP or CITEWATCH. That means no verdict, not “reliable”.',
    t_unsourced: 'claims with no source', t_weak: 'claims on weak sources',
    t_attributed: 'attributed, not linked', t_linked: 'linked to a source',
    sources_line: (n, s) => `${n} external source${n === 1 ? '' : 's'}` + (s ? ` · ${s} link${s === 1 ? '' : 's'} to the same site` : ''),
    dead: n => ` · ${n} dead`, by_llm: 'Claims read by the LLM.', by_heur: 'Claims found by a simple heuristic (no LLM configured): expect misses.',
    tab_claims: 'Claims', tab_sources: 'Sources', tab_citations: 'Citations', tab_gaps: 'Gaps', tab_sugg: 'Suggested',
    f_problems: 'Problems', f_all: 'All', f_unsourced: 'No source', f_weak: 'Weak source', f_attributed: 'Attributed', f_linked: 'Linked',
    s_unsourced: 'no source', s_weakly_linked: 'weak source', s_attributed: 'attributed', s_linked: 'linked',
    backed_by: 'Backed by', attributed_to: 'Attributed to', possible: 'Possible sources',
    no_items: 'Nothing here.', copy: 'copy', copied: 'copied',
    marks: (a, b) => `${a} sources and ${b} claims marked on the page.`,
    w_cited: 'citations', w_avg: 'average score', w_flagged: 'weak or unreliable', w_dead: 'dead links',
    w_cn: '“citation needed” tags', w_unsourced: 'paragraphs without notes',
    g_tagged: 'citation needed', g_para: 'paragraph without notes', same_site: 'same site',
  },
  it: {
    settings: 'Impostazioni', server_url: 'Server Sourceror', opt_links: 'Controlla che i link funzionino',
    opt_llm: 'Usa l’LLM (se configurato)', opt_discover: 'Cerca fonti per le affermazioni senza fonte',
    server_hint: 'Avvialo con “docker compose up -d” nella cartella sourceror. Ascolta solo su questo computer.',
    analyze: 'Controlla le fonti di questa pagina', analyzing: 'Controllo in corso…', reanalyze: 'Ricontrolla',
    clear_marks: 'Togli i segni',
    g_solid: 'solida', g_ok: 'accettabile', g_weak: 'debole', g_unreliable: 'inaffidabile',
    limits: 'Sourceror valuta chi ha pubblicato una fonte e se il link funziona. Non legge la fonte: non può sapere se dice davvero ciò che l’articolo le attribuisce.',
    not_web: 'Apri un articolo (una pagina web o una voce di Wikipedia) per controllarne le fonti.',
    no_server: s => `Il server Sourceror non risponde su <code>${s}</code>. Avvialo con <code>docker compose up -d</code>, o correggi l’indirizzo nelle impostazioni ⚙.`,
    inject_failed: 'Chrome non permette alle estensioni di leggere questa pagina (pagine del browser, Web Store, PDF).',
    no_text: 'In questa pagina non c’è testo di un articolo.',
    not_found: 'Wikipedia non ha una voce con questo titolo.',
    failed: 'L’analisi non è riuscita: ',
    timeout: 'L’analisi sta durando troppo. Il server potrebbe essere sovraccarico: riprova senza il controllo dei link.',
    starting: 'Avvio', fetching: 'Scarico la voce', 'checking citations': 'Controllo le note',
    'checking sources': 'Controllo le fonti collegate', 'reading claims': 'Leggo le affermazioni',
    'searching for sources': 'Cerco fonti', complete: 'Fatto',
    outlet: 'Testata', unlisted: 'Non è in WP:RSP né in CITEWATCH: nessun giudizio, che non vuol dire “affidabile”.',
    t_unsourced: 'affermazioni senza fonte', t_weak: 'affermazioni su fonti deboli',
    t_attributed: 'attribuite, senza link', t_linked: 'collegate a una fonte',
    sources_line: (n, s) => `${n} font${n === 1 ? 'e esterna' : 'i esterne'}` + (s ? ` · ${s} link allo stesso sito` : ''),
    dead: n => ` · ${n} morti`, by_llm: 'Affermazioni lette dall’LLM.', by_heur: 'Affermazioni trovate con un’euristica semplice (LLM non configurato): ne sfuggiranno alcune.',
    tab_claims: 'Affermazioni', tab_sources: 'Fonti', tab_citations: 'Note', tab_gaps: 'Lacune', tab_sugg: 'Suggerite',
    f_problems: 'Problemi', f_all: 'Tutte', f_unsourced: 'Senza fonte', f_weak: 'Fonte debole', f_attributed: 'Attribuite', f_linked: 'Collegate',
    s_unsourced: 'senza fonte', s_weakly_linked: 'fonte debole', s_attributed: 'attribuita', s_linked: 'collegata',
    backed_by: 'Fonte', attributed_to: 'Attribuita a', possible: 'Fonti possibili',
    no_items: 'Niente da mostrare.', copy: 'copia', copied: 'copiato',
    marks: (a, b) => `Segnate nella pagina ${a} fonti e ${b} affermazioni.`,
    w_cited: 'note', w_avg: 'punteggio medio', w_flagged: 'deboli o inaffidabili', w_dead: 'link morti',
    w_cn: 'template “senza fonte”', w_unsourced: 'paragrafi senza note',
    g_tagged: 'senza fonte', g_para: 'paragrafo senza note', same_site: 'stesso sito',
  },
};
const L = T[(navigator.language || 'en').toLowerCase().startsWith('it') ? 'it' : 'en'];
const t = (k, ...a) => { const v = L[k] ?? T.en[k] ?? k; return typeof v === 'function' ? v(...a) : v; };

const DEFAULTS = { server: 'http://127.0.0.1:5010', check_links: true, use_llm: true, discover_sources: true };
const POLL_MS = 1000, POLL_LIMIT_MS = 6 * 60 * 1000;
const PROBLEMS = new Set(['weak', 'unreliable']);

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeUrl = u => { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : ''; } catch (e) { return ''; } };
const link = (u, label) => { const s = safeUrl(u); return s ? `<a href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

let opts = { ...DEFAULTS };
let tabId = null;
const results = new Map();          // tabId -> {url, kind, result, view}
const injected = new Set();         // tabIds whose current document has our script

// ------------------------------------------------------------- settings

async function loadOptions() {
  try { opts = { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) }; } catch (e) { /* defaults */ }
  $('#opt-server').value = opts.server;
  $('#opt-links').checked = opts.check_links;
  $('#opt-llm').checked = opts.use_llm;
  $('#opt-discover').checked = opts.discover_sources;
}

function saveOptions() {
  opts = {
    server: ($('#opt-server').value.trim() || DEFAULTS.server).replace(/\/+$/, ''),
    check_links: $('#opt-links').checked,
    use_llm: $('#opt-llm').checked,
    discover_sources: $('#opt-discover').checked,
  };
  chrome.storage.local.set(opts).catch(() => {});
  ping();
}

async function api(path, body) {
  const res = await fetch(opts.server + path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.error) data.error = `HTTP ${res.status}`;
  return data;
}

async function ping() {
  const el = $('#server');
  try {
    const kb = await api('/api/kb');
    el.className = 'server up';
    el.title = `${opts.server} · ${kb.rsp_entries ?? '?'} RSP · ${kb.headbomb_rules ?? '?'} CITEWATCH`;
    return true;
  } catch (e) {
    el.className = 'server down';
    el.title = opts.server;
    return false;
  }
}

// ----------------------------------------------------------------- tabs

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function showTab() {
  const tab = await activeTab();
  if (!tab) return;
  tabId = tab.id;
  $('#target-title').textContent = tab.title || '';
  $('#target-url').textContent = tab.url ? new URL(tab.url).host + new URL(tab.url).pathname : '';
  const web = /^https?:/.test(tab.url || '');
  $('#analyze').disabled = !web;
  hide('#progress');
  const saved = results.get(tab.id);
  if (saved && saved.url === tab.url) {
    render(saved);
  } else {
    $('#report').hidden = true; $('#legend').hidden = true; $('#clear').hidden = true;
    $('#analyze').textContent = t('analyze');
    web ? hide('#message') : message(t('not_web'));
  }
}

function send(msg) { return chrome.tabs.sendMessage(tabId, msg); }

async function inject(id) {
  if (injected.has(id)) return;
  await chrome.scripting.insertCSS({ target: { tabId: id }, files: ['content.css'] });
  await chrome.scripting.executeScript({ target: { tabId: id }, files: ['vendor/Readability.js', 'content.js'] });
  injected.add(id);
}

// ------------------------------------------------------------- analysis

async function analyze() {
  const tab = await activeTab();
  if (!tab || !/^https?:/.test(tab.url || '')) return message(t('not_web'));
  tabId = tab.id;
  const btn = $('#analyze');
  btn.disabled = true; btn.textContent = t('analyzing');
  hide('#message');
  try {
    if (!(await ping())) return message(t('no_server', esc(opts.server)), true);
    try { await inject(tab.id); } catch (e) { return message(t('inject_failed'), true); }
    const page = await send({ type: 'srcr-extract' });
    if (!page || page.error) return message(t('failed') + esc(page && page.error), true);

    const settings = { check_links: opts.check_links, use_llm: opts.use_llm, discover_sources: opts.discover_sources };
    let start;
    if (page.kind === 'wikipedia') {
      start = await api('/api/analyze', { title: page.title, lang: page.lang, ...settings });
    } else {
      if (!page.paragraphs.length && !page.links.length) return message(t('no_text'), true);
      start = await api('/api/page', { ...page, ...settings });
    }
    if (start.error) return message(t('failed') + esc(start.error), true);

    const result = await poll(start.job);
    if (!result) return;
    if (result.error === 'not_found') return message(t('not_found'), true);

    const entry = { url: tab.url, kind: page.kind, result, view: null };
    index(entry);
    results.set(tab.id, entry);
    render(entry);
    await mark(entry);
  } catch (e) {
    message(t('failed') + esc(e.message || e), true);
  } finally {
    btn.disabled = false;
    hide('#progress');
    if (results.get(tab.id)) btn.textContent = t('reanalyze');
    else btn.textContent = t('analyze');
  }
}

async function poll(job) {
  show('#progress');
  const began = Date.now();
  while (Date.now() - began < POLL_LIMIT_MS) {
    const j = await api(`/api/job/${encodeURIComponent(job)}`);
    if (j.state === 'done') return j.result;
    if (j.state === 'error' || j.error) { message(t('failed') + esc(j.error), true); return null; }
    $('#phase').textContent = t(j.phase || 'starting');
    const bar = $('#bar');
    if (j.total > 1) {
      bar.classList.remove('indeterminate');
      bar.style.width = `${Math.max(5, Math.round(100 * j.done / j.total))}%`;
      $('#count').textContent = `${j.done}/${j.total}`;
    } else {
      bar.classList.add('indeterminate'); bar.style.width = '';
      $('#count').textContent = '';
    }
    await sleep(POLL_MS);
  }
  message(t('timeout'), true);
  return null;
}

// Give every entry a stable index (n) that the page marks refer back to.
function index(entry) {
  const r = entry.result;
  if (entry.kind === 'wikipedia') {
    r.citations.forEach((c, n) => { c.n = n; });
    r.gaps.forEach((g, n) => { g.id = `g${n}`; g.status = g.type === 'tagged' ? 'tagged' : 'unsourced_paragraph'; g.text = g.claim; });
  } else {
    r.sources.forEach((s, n) => { s.n = n; });
  }
}

async function mark(entry) {
  const r = entry.result;
  const msg = entry.kind === 'wikipedia'
    ? { type: 'srcr-mark', wikipedia: true,
        sources: r.citations.map(c => ({ n: c.n, urls: c.urls, doi: c.doi, display: c.display, grade: c.grade, score: c.score })),
        claims: r.gaps.map(g => ({ id: g.id, text: g.claim, status: g.status })) }
    : { type: 'srcr-mark',
        sources: r.sources.map(s => ({ n: s.n, href: s.href, grade: s.grade, score: s.score })),
        claims: r.claims.map(c => ({ id: c.id, text: c.text, status: c.status })) };
  try {
    const got = await send(msg);
    if (got && !got.error) {
      entry.marked = got;
      $('#marks').textContent = t('marks', got.sources, got.claims);
    }
  } catch (e) { /* the tab navigated away; the report is still useful */ }
  $('#clear').hidden = false;
}

// --------------------------------------------------------------- render

function render(entry) {
  hide('#message');
  $('#report').hidden = false;
  $('#legend').hidden = false;
  $('#analyze').textContent = t('reanalyze');
  $('#clear').hidden = false;
  if (entry.kind === 'wikipedia') renderWikipedia(entry); else renderArticle(entry);
}

function tabsHtml(entry, defs) {
  return `<nav class="tabs" role="tablist">${defs.map(([k, label, n]) =>
    `<button class="tab" role="tab" data-view="${k}" aria-selected="${entry.view === k}">${esc(label)}<span class="n">${n}</span></button>`
  ).join('')}</nav>`;
}

function gradeBar(byGrade) {
  const total = Object.values(byGrade).reduce((a, b) => a + b, 0) || 1;
  return `<div class="gradebar">${['solid', 'ok', 'weak', 'unreliable'].map(g =>
    byGrade[g] ? `<i class="${g}" style="width:${(100 * byGrade[g] / total).toFixed(1)}%" title="${byGrade[g]} ${esc(t('g_' + g))}"></i>` : ''
  ).join('')}</div>`;
}

function renderArticle(entry) {
  const r = entry.result, s = r.stats, cs = s.claims_by_status;
  entry.view = entry.view || 'claims';
  entry.filter = entry.filter || 'problems';
  const o = r.outlet, ov = o.verdicts[0];
  $('#report').innerHTML = `
    <div class="outlet">
      <div class="k">${esc(t('outlet'))}</div>
      <div class="v"><i class="d ${esc(o.verdict)}"></i>${esc(r.page.site_name || o.domain || '')}</div>
      <p>${ov ? `${link(ov.url, ov.label)}${ov.name ? ' · ' + esc(ov.name) : ''}` : esc(t('unlisted'))}</p>
    </div>
    <div class="tiles">
      <div class="tile unsourced"><b>${cs.unsourced}</b><span>${esc(t('t_unsourced'))}</span></div>
      <div class="tile weak"><b>${cs.weakly_linked}</b><span>${esc(t('t_weak'))}</span></div>
      <div class="tile attributed"><b>${cs.attributed}</b><span>${esc(t('t_attributed'))}</span></div>
      <div class="tile linked"><b>${cs.linked}</b><span>${esc(t('t_linked'))}</span></div>
    </div>
    ${gradeBar(s.by_grade)}
    <div class="meta">${esc(t('sources_line', s.external_sources, s.same_site_links))}${s.dead_links ? esc(t('dead', s.dead_links)) : ''}
      · ${esc(r.claims_by === 'llm' ? t('by_llm') : t('by_heur'))} <span id="marks"></span></div>
    ${tabsHtml(entry, [['claims', t('tab_claims'), r.claims.length], ['sources', t('tab_sources'), r.sources.length]])}
    <div id="filters" class="filters"></div>
    <div id="list" class="list"></div>`;
  wireTabs(entry, renderArticleList);
  renderArticleList(entry);
}

function renderArticleList(entry) {
  const r = entry.result;
  if (entry.view === 'claims') {
    const keys = [['problems', 'f_problems'], ['all', 'f_all'], ['unsourced', 'f_unsourced'],
                  ['weakly_linked', 'f_weak'], ['attributed', 'f_attributed'], ['linked', 'f_linked']];
    filters(entry, keys);
    const pick = c => entry.filter === 'all' ? true
      : entry.filter === 'problems' ? (c.status === 'unsourced' || c.status === 'weakly_linked')
      : c.status === entry.filter;
    list(r.claims.filter(pick).map(c => claimHtml(c, r)));
  } else {
    filters(entry, [['problems', 'f_problems'], ['all', 'f_all']]);
    const items = r.sources.filter(x => entry.filter === 'all' || PROBLEMS.has(x.grade) || x.flags.some(f => f.severity === 'serious' || f.severity === 'critical'));
    items.sort((a, b) => a.score - b.score);
    list(items.map(sourceHtml));
  }
}

function claimHtml(c, r) {
  const backed = c.sources.map(i => r.sources[i]).filter(Boolean).map(s =>
    `<div class="sub"><i class="d ${esc(s.grade)}"></i> ${esc(t('backed_by'))}: ${link(s.href, s.anchor || s.domain)} <span class="score">${esc(s.domain)} · ${s.score}</span></div>`).join('');
  return `<div class="item ${esc(c.status)}" data-kind="claim" data-id="${esc(c.id)}">
    <div class="head"><span class="chip ${esc(c.status)}">${esc(t('s_' + c.status))}</span></div>
    <blockquote>${esc(c.text)}</blockquote>
    ${c.why ? `<div class="why">${esc(c.why)}</div>` : ''}
    ${c.attribution ? `<div class="sub">${esc(t('attributed_to'))}: ${esc(c.attribution)}</div>` : ''}
    ${backed}
    ${candidatesHtml(c.candidates)}
  </div>`;
}

function candidatesHtml(cands) {
  if (!cands || !cands.length) return '';
  return `<div class="cands"><div class="k">${esc(t('possible'))}</div>${cands.map(x => {
    const href = x.landing || (x.doi ? `https://doi.org/${x.doi}` : '');
    const rel = x.relevance ? `<span class="rel ${esc(x.relevance)}">${esc(x.relevance)}${x.relevance_why ? ' — ' + esc(x.relevance_why) : ''}</span>` : '';
    return `<div class="cand">${link(href, stripTags(x.title))} <span class="score">${esc([x.venue, x.year].filter(Boolean).join(', '))}</span>
      ${x.citation ? `<button class="copy" data-copy="${esc(x.citation)}">${esc(t('copy'))}</button>` : ''}<br>${rel}</div>`;
  }).join('')}</div>`;
}

function stripTags(s) {
  const d = new DOMParser().parseFromString(String(s || ''), 'text/html');
  return d.body.textContent || '';
}

function flagsHtml(flags) {
  const shown = flags.filter(f => f.severity !== 'good' && f.code !== 'SAME_SITE').slice(0, 4);
  const good = flags.filter(f => f.severity === 'good').slice(0, 2);
  return `<ul class="flags">${[...shown, ...good].map(f => `<li><i class="d ${esc(f.severity)}"></i>
    <details><summary>${esc(f.title)}</summary>${f.detail ? `<p>${esc(f.detail.slice(0, 500))}</p>` : ''}${f.fix ? `<p>→ ${esc(f.fix)}</p>` : ''}${f.url ? `<p>${link(f.url, f.url.replace(/^https?:\/\//, '').slice(0, 60))}</p>` : ''}</details></li>`).join('')}</ul>`;
}

function sourceHtml(s) {
  return `<div class="item" data-kind="source" data-id="${s.n}">
    <div class="head"><i class="d ${esc(s.grade)}"></i><span class="title">${esc(s.anchor || s.display || s.domain)}</span><span class="score">${s.score}/100</span></div>
    <div class="sub">${link(s.href || (s.doi ? 'https://doi.org/' + s.doi : ''), s.domain || ('doi:' + s.doi))}${s.same_site ? ' · ' + esc(t('same_site')) : ''}</div>
    ${flagsHtml(s.flags)}
  </div>`;
}

function renderWikipedia(entry) {
  const r = entry.result, s = r.stats;
  entry.view = entry.view || 'citations';
  entry.filter = entry.filter || 'problems';
  $('#report').innerHTML = `
    <div class="outlet">
      <div class="k">Wikipedia · ${esc(r.page.lang)}</div>
      <div class="v">${link(r.page.url, r.page.title)}</div>
      ${r.redirected_from ? `<p>← ${esc(r.redirected_from)}</p>` : ''}
    </div>
    <div class="tiles">
      <div class="tile"><b>${s.scored}</b><span>${esc(t('w_cited'))}</span></div>
      <div class="tile"><b>${s.avg_score}</b><span>${esc(t('w_avg'))}</span></div>
      <div class="tile weak"><b>${s.flagged}</b><span>${esc(t('w_flagged'))}</span></div>
      <div class="tile unsourced"><b>${s.cn_tags + s.unsourced_paragraphs}</b><span>${esc(t('w_cn'))} + ${esc(t('w_unsourced'))}</span></div>
    </div>
    ${gradeBar(s.by_grade)}
    <div class="meta">${s.dead_links ? `${s.dead_links} ${esc(t('w_dead'))} · ` : ''}<span id="marks"></span></div>
    ${tabsHtml(entry, [['citations', t('tab_citations'), r.citations.length], ['gaps', t('tab_gaps'), r.gaps.length], ['suggestions', t('tab_sugg'), r.suggestions.length]])}
    <div id="filters" class="filters"></div>
    <div id="list" class="list"></div>`;
  wireTabs(entry, renderWikipediaList);
  renderWikipediaList(entry);
}

function renderWikipediaList(entry) {
  const r = entry.result;
  $('#filters').innerHTML = '';
  if (entry.view === 'citations') {
    filters(entry, [['problems', 'f_problems'], ['all', 'f_all']]);
    const items = r.citations.filter(c => entry.filter === 'all' || PROBLEMS.has(c.grade) || c.flags.some(f => f.severity === 'serious' || f.severity === 'critical'));
    list(items.map(c => `<div class="item" data-kind="citation" data-id="${c.n}">
      <div class="head"><i class="d ${esc(c.grade)}"></i><span class="title">${esc(c.display)}</span><span class="score">${c.score}/100</span></div>
      <div class="sub">${c.urls && c.urls[0] ? link(c.urls[0], c.domain || c.urls[0]) : esc(c.kind)}${c.section ? ' · § ' + esc(c.section) : ''}</div>
      ${flagsHtml(c.flags)}
      ${c.archive_snippet ? `<div class="cands"><div class="cand"><code>${esc(c.archive_snippet)}</code><button class="copy" data-copy="${esc(c.archive_snippet)}">${esc(t('copy'))}</button></div></div>` : ''}
    </div>`));
  } else if (entry.view === 'gaps') {
    list(r.gaps.map(g => `<div class="item unsourced" data-kind="claim" data-id="${esc(g.id)}">
      <div class="head"><span class="chip unsourced">${esc(g.type === 'tagged' ? t('g_tagged') : t('g_para'))}</span><span class="score">§ ${esc(g.section)}</span></div>
      <blockquote>${esc(g.claim.length > 400 ? g.claim.slice(0, 400) + '…' : g.claim)}</blockquote>
      ${candidatesHtml(g.candidates)}
    </div>`));
  } else {
    list([`<div class="item">${candidatesHtml(r.suggestions).replace('class="cands"', 'class="cands" style="border:0;margin:0;padding:0"')}</div>`]);
  }
}

function wireTabs(entry, renderList) {
  document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
    entry.view = b.dataset.view;
    entry.filter = 'problems';
    document.querySelectorAll('.tab').forEach(x => x.setAttribute('aria-selected', x === b));
    renderList(entry);
  }));
}

function filters(entry, keys) {
  const el = $('#filters');
  el.innerHTML = keys.map(([k, label]) =>
    `<button class="filter" data-f="${k}" aria-pressed="${entry.filter === k}">${esc(t(label))}</button>`).join('');
  el.querySelectorAll('.filter').forEach(b => b.addEventListener('click', () => {
    entry.filter = b.dataset.f;
    (entry.kind === 'wikipedia' ? renderWikipediaList : renderArticleList)(entry);
  }));
}

function list(items) {
  $('#list').innerHTML = items.length ? items.join('') : `<div class="empty">${esc(t('no_items'))}</div>`;
}

// ------------------------------------------------------------- helpers

function show(sel) { $(sel).hidden = false; }
function hide(sel) { $(sel).hidden = true; }
function message(html, isError) {
  const el = $('#message');
  el.innerHTML = html;
  el.className = 'message' + (isError ? ' error' : '');
  el.hidden = false;
  hide('#progress');
}

function flashItem(kind, id) {
  const entry = results.get(tabId);
  if (!entry) return;
  const wantView = kind === 'claim' ? (entry.kind === 'wikipedia' ? 'gaps' : 'claims')
                                    : (entry.kind === 'wikipedia' ? 'citations' : 'sources');
  const sel = `.item[data-kind="${kind}"][data-id="${CSS.escape(String(id))}"]`;
  if (entry.view !== wantView || !document.querySelector(sel)) {
    entry.view = wantView;
    entry.filter = 'all';
    document.querySelectorAll('.tab').forEach(x => x.setAttribute('aria-selected', x.dataset.view === wantView));
    (entry.kind === 'wikipedia' ? renderWikipediaList : renderArticleList)(entry);
  }
  const el = document.querySelector(sel);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 1600);
}

// --------------------------------------------------------------- wiring

document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });

$('#analyze').addEventListener('click', analyze);
$('#clear').addEventListener('click', async () => {
  try { await send({ type: 'srcr-clear' }); } catch (e) { /* page gone */ }
  results.delete(tabId);
  showTab();
});
$('#toggle-settings').addEventListener('click', e => {
  const s = $('#settings');
  s.hidden = !s.hidden;
  e.currentTarget.setAttribute('aria-expanded', String(!s.hidden));
});
['#opt-server', '#opt-links', '#opt-llm', '#opt-discover'].forEach(sel => $(sel).addEventListener('change', saveOptions));

$('#report').addEventListener('click', async e => {
  const copy = e.target.closest('.copy');
  if (copy) {
    e.stopPropagation();
    await navigator.clipboard.writeText(copy.dataset.copy).catch(() => {});
    copy.textContent = t('copied');
    setTimeout(() => { copy.textContent = t('copy'); }, 1200);
    return;
  }
  if (e.target.closest('a, summary')) return;
  const item = e.target.closest('.item[data-kind]');
  if (item) send({ type: 'srcr-focus', kind: item.dataset.kind, id: item.dataset.id }).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === 'srcr-select' && sender.tab && sender.tab.id === tabId) flashItem(msg.kind, msg.id);
});
chrome.tabs.onActivated.addListener(showTab);
chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === 'loading') { injected.delete(id); if (info.url) results.delete(id); }
  if (id === tabId && (info.status === 'complete' || info.title)) showTab();
});
chrome.tabs.onRemoved.addListener(id => { results.delete(id); injected.delete(id); });

loadOptions().then(() => { ping(); showTab(); });
