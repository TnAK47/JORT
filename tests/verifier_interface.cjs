const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('C:/Users/KA/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jort-ui-fixture-'));
const fixtureDir = path.join(tempRoot, 'dist');
const outputDir = path.join(root, 'outputs');
const apiOrigin = 'http://127.0.0.1:8765';
fs.mkdirSync(fixtureDir, { recursive: true });
fs.mkdirSync(outputDir, { recursive: true });

const files = [
  'index.html', 'collecte.html', 'style.css', 'collecte.css', 'interface.css', 'app.js', 'collecte.js'
];
for (const file of files) fs.copyFileSync(path.join(root, 'dist', file), path.join(fixtureDir, file));
const appPath = path.join(fixtureDir, 'app.js');
fs.writeFileSync(appPath, fs.readFileSync(appPath, 'utf8').replace("fetch('/api/collecte/status'", `fetch('${apiOrigin}/api/collecte/status'`));

const data = {
  revision: 'fixture-2years',
  coverage: { available_years: [2024, 2025], year: 2024 },
  stats: {},
  sources: [
    { id: 's2024', number: 12, date: '2024-03-20', pdf: 'documents/2024/jort-12.pdf' },
    { id: 's2025', number: 37, date: '2025-06-11', pdf: 'documents/2025/jort-37.pdf' }
  ],
  announcements: [
    { id: 'a1', source_id: 's2024', reference: 'JORT 12/2024 · annonce 1', category: 'societe', pages: [5], status: 'a_verifier', text: 'Constitution de la société Carthage Services.' },
    { id: 'a2', source_id: 's2024', reference: 'JORT 12/2024 · annonce 2', category: 'societe', pages: [8], status: 'a_verifier', text: 'Nomination publiée pour Bizerta Marine.' },
    { id: 'a3', source_id: 's2025', reference: 'JORT 37/2025 · annonce 1', category: 'societe', pages: [11], status: 'a_verifier', text: 'Renouvellement et cession pour Sahara Logistique.' }
  ],
  entities: [
    { id: 'org1', announcement_id: 'a1', type: 'societe', name: 'Carthage Services', identifier: 'TN-001' },
    { id: 'person1', announcement_id: 'a1', type: 'personne', name: 'Amira Ben Salem', cin: '12345678', identity_match_status: 'a_verifier' },
    { id: 'org2', announcement_id: 'a2', type: 'societe', name: 'Bizerta Marine', identifier: 'TN-002' },
    { id: 'person2', announcement_id: 'a2', type: 'personne', name: 'Amira B. Salem', cin: '12345678', identity_match_status: 'a_verifier' },
    { id: 'person3', announcement_id: 'a3', type: 'personne', name: 'Nadia Kefi', cin: '87654321', identity_match_status: 'confirme' },
    { id: 'person4', announcement_id: 'a3', type: 'personne', name: 'Nadia Kefi épouse H.', cin: '87654321', identity_match_status: 'confirme' },
    { id: 'org3', announcement_id: 'a3', type: 'societe', name: 'Sahara Logistique', identifier: 'TN-003' }
  ],
  relations: [
    { id: 'r1', from: 'person1', to: 'org1', announcement_id: 'a1', type: 'role', role: 'Gérante', event_type: 'appointment', quote: 'Mme Amira Ben Salem est nommée gérante.' },
    { id: 'r2', from: 'person2', to: 'org2', announcement_id: 'a2', type: 'role', role: 'Associée', event_type: 'revocation', quote: 'Révocation de la fonction publiée.' },
    { id: 'r3', from: 'person3', to: 'org3', announcement_id: 'a3', type: 'role', role: 'Administratrice', event_type: 'renewal', quote: 'Mandat renouvelé.' },
    { id: 'r4', from: 'person4', to: 'org3', announcement_id: 'a3', type: 'vente_fonds_commerce', role: 'Acheteuse', event_type: 'vente', quote: 'Fonds cédé à Mme Nadia Kefi épouse H.' }
  ]
};
fs.writeFileSync(path.join(fixtureDir, 'data.js'), `window.JORT_DATA = ${JSON.stringify(data)};`);

const mockStatus = year => ({
  year,
  coverage: { expected: 24, available: year === 2025 ? 19 : 20, known: 24, indexed: year === 2025 ? 4 : 3, inventory_complete: false },
  stats: { announcements: year === 2025 ? 4 : 3, organizations: 3, persons: 2, relations: 4, pages: 24, unclassified: 1 },
  logs: Array.from({ length: 86 }, (_, i) => ({ time: `2026-10-10 09:${String(i % 60).padStart(2, '0')}`, level: i > 80 ? 'warning' : 'info', task: 'indexation_annonce', number: 12, current: i + 1, total: 86, message: `Événement de démonstration ${i + 1} — aucun traitement réel.` })),
  paths: { inbox: 'C:\\JORT\\2025\\inbox' },
  files: Array.from({ length: 31 }, (_, i) => ({ number: i + 1, date: `${year}-01-${String(i % 28 + 1).padStart(2, '0')}`, filename: `jort-${i + 1}.pdf`, path: `/documents/${year}/jort-${i + 1}.pdf`, status: i < 19 ? 'present' : 'unknown', indexed: i < 4, processing_state: i < 4 ? 'termine' : 'a_traiter', processing_updated_at: '2026-10-10T09:10:00' })),
  job: currentJob,
  data_revision: 'fixture-2years'
});
let currentJob = { id: 'fixture-idle', action: '', state: 'done', message: 'Aucun traitement actif.' };
let browser;
const pageErrors = [];
const requests = [];
const blockedNetwork = [];

async function fulfillJson(route, payload, status = 200) {
  return route.fulfill({ status, contentType: 'application/json; charset=utf-8', headers: { 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify(payload) });
}

async function main() {
  const cachedBrowser = chromium.executablePath();
  const browserPath = fs.existsSync(cachedBrowser) ? cachedBrowser : 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  assert(fs.existsSync(browserPath), `Navigateur Chromium/Chrome introuvable (${browserPath}).`);
  browser = await chromium.launch({ headless: true, executablePath: browserPath, args: ['--disable-web-security'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  await context.route('**/*', async route => {
    const url = route.request().url();
    if (url.startsWith(apiOrigin + '/api/collecte/')) {
      const request = route.request();
      const parsed = new URL(url);
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, X-JORT-Local' } });
      const year = Number(parsed.searchParams.get('year') || 2024);
      requests.push({ method: request.method(), path: parsed.pathname, year, body: request.postData() });
      if (parsed.pathname.endsWith('/status')) return fulfillJson(route, mockStatus(year));
      if (parsed.pathname.endsWith('/tickets')) return fulfillJson(route, { items: [], total: 0 });
      if (parsed.pathname.endsWith('/job')) {
        const body = JSON.parse(request.postData() || '{}');
        currentJob = { id: 'fixture-job', action: body.action, state: 'running', message: 'Traitement simulé · aucun PDF téléchargé.' };
        return fulfillJson(route, { job: currentJob });
      }
      if (parsed.pathname.endsWith('/stop')) { currentJob = { ...currentJob, state: 'cancelled' }; return fulfillJson(route, { job: currentJob }); }
      if (parsed.pathname.endsWith('/open-inbox')) return fulfillJson(route, { ok: true });
      if (parsed.pathname.endsWith('/clear-logs')) return fulfillJson(route, { events_removed: 0, processing_cleared: false });
      if (parsed.pathname.endsWith('/upload')) return fulfillJson(route, { files: [], job: currentJob });
      return fulfillJson(route, { error: 'route mock inconnue' }, 404);
    }
    if (url.startsWith('file://')) return route.continue();
    blockedNetwork.push(url);
    return route.abort('blockedbyclient');
  });

  await page.goto(`file://${path.join(fixtureDir, 'index.html').replaceAll('\\', '/')}`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.getElementById('result-summary')?.textContent.includes('3 annonces'));
  assert.match(await page.locator('#coverage-status').textContent(), /2024, 2025/);
  await page.locator('#search').fill('Bizerta');
  assert.match(await page.locator('#result-summary').textContent(), /1 annonce/);
  assert.match(await page.locator('#announcement-list').textContent(), /Bizerta Marine/);
  await page.locator('#search').fill('');
  await page.locator('#announcement-list [data-announcement="a1"]').click();
  assert.match(await page.locator('#detail-panel').textContent(), /Carthage Services/);
  await page.locator('.tab[data-view="reseau"]').click();
  const graphValues = await page.locator('#organization-select option').evaluateAll(items => items.map(item => item.value));
  assert(graphValues.includes('person1') && graphValues.includes('person2'), 'Occurrences non confirmées partageant la même CIN doivent rester séparées.');
  assert.equal(graphValues.filter(value => value === 'personne:cin:87654321').length, 1, 'Le rapprochement confirmé doit apparaître comme un seul nœud.');
  const personLabels = await page.locator('#organization-select option').evaluateAll(items => items.filter(item => item.value.startsWith('person')).map(item => item.textContent));
  assert(personLabels.every(label => !label.includes('À classer')));
  assert(personLabels.some(label => label.includes('occurrence distincte')));
  await page.locator('#organization-select').selectOption('person2');
  await page.getByRole('button', { name: 'Amira B. Salem, personne' }).click();
  assert.match(await page.locator('#network-detail').textContent(), /révocation/);
  await page.locator('#organization-select').selectOption('personne:cin:87654321');
  await page.getByRole('button', { name: 'Nadia Kefi, personne' }).click();
  assert.match(await page.locator('#network-detail').textContent(), /renouvellement/);
  await page.evaluate(async () => { document.activeElement?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
  await page.screenshot({ path: path.join(outputDir, 'apercu-interface-exploration.png'), fullPage: true });

  const adminPage = await context.newPage();
  adminPage.on('pageerror', error => pageErrors.push(error.message));
  await adminPage.goto(`file://${path.join(fixtureDir, 'collecte.html').replaceAll('\\', '/')}`, { waitUntil: 'load' });
  await adminPage.waitForFunction(() => document.getElementById('coverage-title')?.textContent.includes('2024'));
  assert.match(await adminPage.locator('#events-page').textContent(), /3/);
  await adminPage.locator('#events-prev').click();
  assert.match(await adminPage.locator('#events-page').textContent(), /Page 2 sur 3/);
  await adminPage.locator('#collection-year').selectOption('2025');
  await adminPage.waitForFunction(() => document.getElementById('coverage-title')?.textContent.includes('2025'));
  await adminPage.locator('[data-action="inventory"]').click();
  await adminPage.waitForFunction(() => document.getElementById('job-status')?.textContent.includes('Traitement simulé'));
  const inventoryRequest = requests.find(item => item.method === 'POST' && item.path.endsWith('/job'));
  assert(inventoryRequest, 'Action de mise à jour inventaire doit appeler l’API mockée.');
  assert.equal(JSON.parse(inventoryRequest.body).year, 2025);
  assert.equal(JSON.parse(inventoryRequest.body).action, 'inventory');
  await adminPage.evaluate(async () => { document.activeElement?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
  await adminPage.screenshot({ path: path.join(outputDir, 'apercu-interface-admin.png') });
  await adminPage.screenshot({ path: path.join(outputDir, 'apercu-interface-admin-ecran.png') });
  await adminPage.screenshot({ path: path.join(outputDir, 'apercu-interface-admin-complet.png'), fullPage: true });

  await adminPage.setViewportSize({ width: 390, height: 844 });
  await adminPage.evaluate(async () => { window.scrollTo({ top: 0, behavior: 'instant' }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
  await adminPage.screenshot({ path: path.join(outputDir, 'apercu-interface-mobile.png') });
  await adminPage.screenshot({ path: path.join(outputDir, 'apercu-interface-mobile-complet.png'), fullPage: true });
  const adminOverflow = await adminPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(adminOverflow, false, 'La page admin ne doit pas déborder horizontalement sur mobile.');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(100);
  const exploreOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(exploreOverflow, false, 'La page exploration ne doit pas déborder horizontalement sur mobile.');
  assert.deepEqual(pageErrors, [], `Erreurs page: ${pageErrors.join('; ')}`);
  assert.deepEqual(blockedNetwork, [], `Réseau externe bloqué mais tenté: ${blockedNetwork.join(', ')}`);
  console.log(JSON.stringify({ ok: true, screenshots: ['apercu-interface-admin.png', 'apercu-interface-admin-ecran.png', 'apercu-interface-admin-complet.png', 'apercu-interface-exploration.png', 'apercu-interface-mobile.png', 'apercu-interface-mobile-complet.png'], mockedApiCalls: requests.length, verified: ['données 2024+2025', 'recherche', 'détail source', 'onglet réseau', 'CIN non confirmées séparées', 'CIN confirmée regroupée', 'libellés occurrence/rapprochement', 'étiquettes renewal/revocation', 'pagination logs', 'action inventaire année 2025', 'mobile sans débordement'], pageErrors, blockedNetwork }, null, 2));
  await browser.close();
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  const resolved = path.resolve(tempRoot);
  if (path.dirname(resolved) === path.resolve(os.tmpdir()) && resolved.includes('jort-ui-fixture-')) fs.rmSync(resolved, { recursive: true, force: true });
});
