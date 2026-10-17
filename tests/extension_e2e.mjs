// End-to-end check of the Chrome extension against a running Sourceror server.
//
//   docker run --rm --network host -v "$PWD":/src -w /src \
//     mcr.microsoft.com/playwright:v1.60.0 \
//     sh -c "npm i --no-save --no-package-lock --silent playwright@1.60.0 && node tests/extension_e2e.mjs"
//
// Loads extension/ into Chromium, opens a page, drives the side panel and
// checks that a report appears and that marks land in the page. Screenshots
// go to $OUT (default /tmp/srcr-e2e). Pages to try come from argv; the default
// is the local fixture article plus one Wikipedia entry.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT = resolve(new URL('..', import.meta.url).pathname);
const OUT = process.env.OUT || '/tmp/srcr-e2e';
const LLM = process.env.USE_LLM === '1';
const LINKS = process.env.CHECK_LINKS !== '0';
mkdirSync(OUT, { recursive: true });

const fixture = readFileSync(join(ROOT, 'tests/fixtures/article.html'));
const server = createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(fixture);
}).listen(8765, '127.0.0.1');

const targets = process.argv.slice(2).length ? process.argv.slice(2) : [
  'http://127.0.0.1:8765/economy/2026/10/remote-work',
  'https://it.wikipedia.org/wiki/Ponte_di_Rialto',
];

const ctx = await chromium.launchPersistentContext(join(tmpdir(), 'srcr-profile-' + Date.now()), {
  channel: 'chromium',
  headless: true,
  locale: process.env.UI_LANG || 'en-US',
  args: [`--disable-extensions-except=${ROOT}/extension`, `--load-extension=${ROOT}/extension`],
  viewport: { width: 1100, height: 900 },
});
let [sw] = ctx.serviceWorkers();
if (!sw) sw = await ctx.waitForEvent('serviceworker');
const extId = new URL(sw.url()).host;

const article = await ctx.newPage();
const panel = await ctx.newPage();
await panel.setViewportSize({ width: 400, height: 1100 });
panel.on('console', m => { if (m.type() === 'error') console.log('  [panel]', m.text()); });

let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? '  ' + detail : ''}`);
  if (!ok) failures++;
};

await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
await panel.evaluate(o => chrome.storage.local.set(o),
  { server: process.env.SERVER || 'http://127.0.0.1:5010',
    use_llm: LLM, check_links: LINKS, discover_sources: true });

for (const [n, url] of targets.entries()) {
  console.log(`\n[${n + 1}] ${url}`);
  await article.goto(url, { waitUntil: 'domcontentloaded' });
  await article.waitForTimeout(1500);

  // A side panel's "active tab" is the page beside it. Opened as a tab here,
  // the panel would find itself, so point it at the article's tab instead.
  await panel.reload();
  await panel.evaluate(async u => {
    const [tab] = await chrome.tabs.query({ url: u.split('#')[0] + '*' });
    const q = chrome.tabs.query.bind(chrome.tabs);
    chrome.tabs.query = async info => (info && info.active ? [tab] : q(info));
  }, article.url());
  await panel.evaluate(() => showTab());

  const t0 = Date.now();
  await panel.click('#analyze');
  await panel.waitForFunction(() => !document.querySelector('#report').hidden ||
    !document.querySelector('#message').hidden, null, { timeout: 360000 });
  const msg = await panel.$eval('#message', el => el.hidden ? '' : el.textContent);
  check('report rendered', !msg, msg || `${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (msg) continue;

  await panel.waitForFunction(() => document.querySelector('#marks')?.textContent, null, { timeout: 10000 }).catch(() => {});
  const items = await panel.$$eval('#list .item', els => els.length);
  const marks = await panel.$eval('#marks', el => el.textContent).catch(() => '');
  const pageMarks = await article.evaluate(() => ({
    dots: document.querySelectorAll('.srcr-dot').length,
    highlights: CSS.highlights ? [...CSS.highlights.keys()] : [],
  }));
  check('panel lists items', items > 0, `${items}`);
  check('dots placed in page', pageMarks.dots > 0, `${pageMarks.dots} · ${marks}`);
  console.log(`        highlights: ${pageMarks.highlights.join(', ') || 'none'}`);

  if (url.includes('127.0.0.1:8765')) {
    const r = await panel.evaluate(() => [...results.values()][0].result);
    const dm = r.sources.find(s => s.domain && s.domain.includes('dailymail'));
    check('tabloid link judged unreliable', dm && dm.grade === 'unreliable', dm && dm.grade);
    check('share buttons ignored', !r.sources.some(s => /facebook|twitter/.test(s.href || '')));
    check('nav/footer links ignored', !r.sources.some(s => /\/privacy|\/sport/.test(s.href || '')));
    const wages = r.claims.find(c => /6%/.test(c.text));
    check('claim on tabloid is weakly linked', wages && wages.status === 'weakly_linked', wages && wages.status);
    const unsourced = r.claims.filter(c => c.status === 'unsourced').length;
    check('unsourced claims found', unsourced > 0, `${unsourced} (${r.claims_by})`);

    // Clicking a dot in the page should select the matching panel entry.
    await article.click('.srcr-dot.srcr-unreliable');
    const flashed = await panel.waitForSelector('.item.flash', { timeout: 3000 }).then(() => true, () => false);
    check('page dot selects panel item', flashed);
  }

  const tag = url.replace(/^https?:\/\//, '').replace(/[^a-z0-9]+/gi, '_').slice(0, 50);
  await panel.screenshot({ path: `${OUT}/${n + 1}-panel-${tag}.png`, fullPage: true });
  await article.screenshot({ path: `${OUT}/${n + 1}-page-${tag}.png` });
}

await ctx.close();
server.close();
console.log(`\n${failures ? `FAILURES: ${failures}` : 'ALL PASSED'}  (screenshots in ${OUT})`);
process.exit(failures ? 1 : 0);
