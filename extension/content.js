/* Sourceror content script: reads the article, draws marks, reports clicks.
 *
 * Injected on demand by the side panel, together with vendor/Readability.js.
 * It never talks to the Sourceror server itself: a content script's requests
 * run under the page's origin, so the side panel makes every API call.
 */
(() => {
  if (window.__sourceror) return;
  window.__sourceror = true;

  const BLOCKS = 'p, li, blockquote, dd, figcaption';
  const MAX_PARAGRAPHS = 120;
  const TRACKING = /^(utm_|fbclid$|gclid$|mc_[ce]id$|ref$|ref_src$|cmpid$|ocid$|smid$|xtor$)/i;

  // Must match sourceror.page.normalise_url, so marks find their sources.
  function normaliseUrl(raw, base) {
    try {
      const u = new URL(raw, base || location.href);
      if (!/^https?:$/.test(u.protocol)) return '';
      const keep = [...u.searchParams].filter(([k]) => !TRACKING.test(k));
      const q = new URLSearchParams(keep).toString();
      return `${u.protocol}//${u.host.toLowerCase()}${u.pathname || '/'}${q ? '?' + q : ''}`;
    } catch (e) { return ''; }
  }

  // ------------------------------------------------------------ extract

  function wikipediaTarget() {
    const m = location.hostname.match(/^([a-z-]+)\.(?:m\.)?wikipedia\.org$/);
    if (!m || !location.pathname.startsWith('/wiki/')) return null;
    const title = decodeURIComponent(location.pathname.slice(6)).replace(/_/g, ' ');
    if (/^[^:]+:/.test(title) && !/^[^:]+: /.test(title)) return null;  // Special:, Talk:…
    return { kind: 'wikipedia', lang: m[1], title, url: location.href };
  }

  function meta(...names) {
    for (const n of names) {
      const el = document.querySelector(`meta[property="${n}"], meta[name="${n}"], meta[itemprop="${n}"]`);
      if (el && el.content) return el.content.trim();
    }
    return null;
  }

  function leafBlocks(root) {
    // A <li> that wraps a <p> would be read twice; keep the innermost block.
    return [...root.querySelectorAll(BLOCKS)].filter(el => !el.querySelector(BLOCKS));
  }

  function extractArticle() {
    let article = null;
    try {
      article = new Readability(document.cloneNode(true), { charThreshold: 300 }).parse();
    } catch (e) { article = null; }
    const root = article
      ? new DOMParser().parseFromString(article.content, 'text/html').body
      : (document.querySelector('article, main, [role=main]') || document.body);

    const paragraphs = [], links = [];
    for (const block of leafBlocks(root)) {
      if (paragraphs.length >= MAX_PARAGRAPHS) break;
      let text = '', marked = '';
      const walk = node => {
        if (node.nodeType === Node.TEXT_NODE) {
          text += node.nodeValue; marked += node.nodeValue; return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (/^(SCRIPT|STYLE|NOSCRIPT|BUTTON|svg)$/i.test(node.nodeName)) return;
        node.childNodes.forEach(walk);
        if (node.nodeName === 'A' && node.getAttribute('href')) {
          const href = normaliseUrl(node.getAttribute('href'), location.href);
          if (href) {
            const id = links.length;
            links.push({ id, href, text: node.textContent.trim().slice(0, 200),
                         p: paragraphs.length });
            marked += ` [L${id}]`;
          }
        }
      };
      walk(block);
      text = text.replace(/\s+/g, ' ').trim();
      if (text.length < 25) {
        // Too short to be prose, but its links (a "Source:" line) still count.
        links.forEach(l => { if (l.p === paragraphs.length) l.p = null; });
        continue;
      }
      paragraphs.push({ i: paragraphs.length, text,
                        marked: marked.replace(/\s+/g, ' ').trim() });
    }

    return {
      kind: 'article',
      url: location.href,
      title: (article && article.title) || meta('og:title') || document.title,
      byline: (article && article.byline) || meta('author', 'article:author'),
      site_name: (article && article.siteName) || meta('og:site_name'),
      published: (article && article.publishedTime) ||
                 meta('article:published_time', 'datePublished', 'date', 'DC.date.issued'),
      lang: (article && article.lang) || document.documentElement.lang || navigator.language,
      paragraphs, links,
    };
  }

  // -------------------------------------------------------------- marks

  const state = { claims: new Map(), dots: [], refMarks: [] };

  function clearMarks() {
    state.dots.forEach(d => d.remove());
    state.refMarks.forEach(el => el.classList.remove(
      'srcr-ref', 'srcr-solid', 'srcr-ok', 'srcr-weak', 'srcr-unreliable', 'srcr-flash'));
    state.dots = []; state.refMarks = [];
    state.claims.clear();
    if (window.CSS && CSS.highlights) {
      ['srcr-unsourced', 'srcr-weak', 'srcr-attributed', 'srcr-focus']
        .forEach(n => CSS.highlights.delete(n));
    }
  }

  function dot(grade, kind, id, title) {
    const d = document.createElement('span');
    d.className = `srcr-dot srcr-${grade || 'none'}`;
    d.dataset.srcrKind = kind;
    d.dataset.srcrId = id;
    d.title = title;
    d.setAttribute('role', 'button');
    d.tabIndex = 0;
    state.dots.push(d);
    return d;
  }

  function inProse(el) {
    return !!el.closest('p, li, blockquote, dd, td, figcaption') &&
           !el.closest('nav, header, footer, aside, [role=navigation]');
  }

  function markSources(sources) {
    const byHref = new Map(sources.filter(s => s.href).map(s => [s.href, s]));
    let marked = 0;
    for (const a of document.querySelectorAll('a[href]')) {
      if (!inProse(a)) continue;
      const s = byHref.get(normaliseUrl(a.getAttribute('href')));
      if (!s) continue;
      a.after(dot(s.grade, 'source', s.n, `Sourceror: ${s.grade} (${s.score}/100)`));
      marked++;
    }
    return marked;
  }

  // Wikipedia: tie each audited citation to its <li> in the reference list.
  function markWikipediaRefs(citations) {
    const keyed = new Map();
    citations.forEach(c => {
      (c.urls || []).forEach(u => { const k = normaliseUrl(u); if (k) keyed.set(k, c); });
      if (c.doi) keyed.set('doi:' + c.doi.toLowerCase(), c);
    });
    const byText = citations.filter(c => c.display && c.display.length > 12);
    let marked = 0;
    for (const li of document.querySelectorAll('ol.references > li[id]')) {
      let c = null;
      for (const a of li.querySelectorAll('a.external[href]')) {
        c = keyed.get(normaliseUrl(a.getAttribute('href')));
        if (c) break;
      }
      if (!c) {
        const doi = (li.textContent.match(/\b10\.\d{4,9}\/[^\s"<>]+/) || [])[0];
        if (doi) c = keyed.get('doi:' + doi.replace(/[.,;)]+$/, '').toLowerCase());
      }
      if (!c) {
        const t = li.textContent.replace(/\s+/g, ' ');
        c = byText.find(x => t.includes(x.display.slice(0, 40)));
      }
      if (!c) continue;
      const target = li.querySelector('.reference-text') || li;
      target.prepend(dot(c.grade, 'citation', c.n, `Sourceror: ${c.grade} (${c.score}/100)`));
      for (const sup of document.querySelectorAll(`sup.reference a[href="#${CSS.escape(li.id)}"]`)) {
        const s = sup.closest('sup');
        s.classList.add('srcr-ref', `srcr-${c.grade}`);
        s.dataset.srcrKind = 'citation';
        s.dataset.srcrId = c.n;
        state.refMarks.push(s);
      }
      marked++;
    }
    return marked;
  }

  // Text search over the live page, collapsing whitespace, so a claim string
  // from the server can be turned back into a Range.
  function textIndex() {
    const scope = document.querySelector('#mw-content-text, article, main, [role=main]') || document.body;
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.parentElement.closest('script, style, noscript, .srcr-dot, sup.reference, .mw-editsection')
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let str = '', map = [], lastSpace = true;
    for (let n; (n = walker.nextNode());) {
      const v = n.nodeValue;
      for (let i = 0; i < v.length; i++) {
        const ch = v[i];
        if (/\s/.test(ch)) {
          if (lastSpace) continue;
          str += ' '; lastSpace = true;
        } else {
          str += ch; lastSpace = false;
        }
        map.push([n, i]);
      }
    }
    return { str, map };
  }

  function findRange(idx, text) {
    const needle = text.replace(/\s+/g, ' ').trim();
    const tries = [needle, needle.slice(0, 90), needle.slice(0, 50)]
      .filter((t, i, a) => t.length >= 20 && a.indexOf(t) === i);
    for (const t of tries) {
      const at = idx.str.indexOf(t);
      if (at < 0) continue;
      const [sn, so] = idx.map[at];
      const [en, eo] = idx.map[at + t.length - 1];
      const r = document.createRange();
      r.setStart(sn, so);
      r.setEnd(en, eo + 1);
      return r;
    }
    return null;
  }

  function markClaims(claims) {
    if (!(window.CSS && CSS.highlights && window.Highlight)) return 0;
    const idx = textIndex();
    const groups = { 'srcr-unsourced': [], 'srcr-weak': [], 'srcr-attributed': [] };
    const group = { unsourced: 'srcr-unsourced', weakly_linked: 'srcr-weak',
                    attributed: 'srcr-attributed', tagged: 'srcr-unsourced',
                    unsourced_paragraph: 'srcr-unsourced' };
    let found = 0;
    for (const c of claims) {
      const g = group[c.status];
      if (!g) continue;
      const r = findRange(idx, c.text);
      if (!r) continue;
      state.claims.set(String(c.id), r);
      groups[g].push(r);
      found++;
    }
    for (const [name, ranges] of Object.entries(groups)) {
      if (ranges.length) CSS.highlights.set(name, new Highlight(...ranges));
    }
    return found;
  }

  function focus(kind, id) {
    if (kind === 'claim') {
      const r = state.claims.get(String(id));
      if (!r) return false;
      const box = r.getBoundingClientRect();
      window.scrollBy({ top: box.top - innerHeight / 3, behavior: 'smooth' });
      if (window.Highlight) {
        CSS.highlights.set('srcr-focus', new Highlight(r));
        setTimeout(() => CSS.highlights.delete('srcr-focus'), 2200);
      }
      return true;
    }
    const el = state.dots.find(d => d.dataset.srcrKind === kind && d.dataset.srcrId === String(id));
    if (!el) return false;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('srcr-flash');
    setTimeout(() => el.classList.remove('srcr-flash'), 2200);
    return true;
  }

  // Clicking a mark in the page jumps to its entry in the side panel.
  document.addEventListener('click', ev => {
    const el = ev.target.closest && ev.target.closest('[data-srcr-kind]');
    if (el && el.classList.contains('srcr-dot')) {
      ev.preventDefault(); ev.stopPropagation();
      chrome.runtime.sendMessage({ type: 'srcr-select', kind: el.dataset.srcrKind, id: el.dataset.srcrId });
      return;
    }
    if (!state.claims.size || !document.caretRangeFromPoint) return;
    const p = document.caretRangeFromPoint(ev.clientX, ev.clientY);
    if (!p) return;
    for (const [id, r] of state.claims) {
      try {
        if (r.isPointInRange(p.startContainer, p.startOffset)) {
          chrome.runtime.sendMessage({ type: 'srcr-select', kind: 'claim', id });
          return;
        }
      } catch (e) { /* range from a node that has since left the page */ }
    }
  }, true);

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    try {
      if (msg.type === 'srcr-extract') {
        reply(wikipediaTarget() || extractArticle());
      } else if (msg.type === 'srcr-mark') {
        clearMarks();
        const sources = msg.wikipedia ? markWikipediaRefs(msg.sources || [])
                                      : markSources(msg.sources || []);
        reply({ sources, claims: markClaims(msg.claims || []) });
      } else if (msg.type === 'srcr-clear') {
        clearMarks(); reply({ ok: true });
      } else if (msg.type === 'srcr-focus') {
        reply({ ok: focus(msg.kind, msg.id) });
      }
    } catch (e) {
      reply({ error: String(e) });
    }
  });
})();
