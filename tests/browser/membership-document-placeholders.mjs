import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4248';
const root = resolve(process.env.BROWSER_DIST_ROOT || resolve(import.meta.dirname, '../../web/dist'));
const message = '示範資料，無實際檔案';
const contentTypes = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.woff2': 'font/woff2' };
const documents = [
  { id: 'placeholder', document_type: 'id_front', is_demo_placeholder: true },
  { id: 'ordinary', document_type: 'id_back', is_demo_placeholder: false },
  { id: 'legacy-api', document_type: 'secondary' },
].map(value => ({ ...value, status: 'confirmed', content_type: 'image/jpeg', size_bytes: 10, confirmed_at: '2026-01-01T00:00:00Z' }));

async function fixture(t, width, { admin = true, rejectDownload = false } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { unknown: [], errors: [], reads: [], writes: [], downloads: [], popups: 0 };
  t.after(async () => {
    await browser.close();
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
    assert.deepEqual(state.writes, []);
  });
  const user = { id: 'isolated-document-viewer', user_role: admin ? 'admin' : 'customer', display_name: '隔離文件管理員', email: 'document-admin@example.test', membership_type: 'nonmember' };
  const application = { id: 'isolated-application', user_id: 'isolated-applicant', status: 'needs_supplement', created_at: '2026-01-01T00:00:00Z', profile: { legal_name: '隔離舊版申請人' }, documents };
  const emptyPaths = new Set(['/v1/admin/activities', '/v1/admin/member-proposals', '/v1/wishes', '/v1/admin/meetings', '/v1/admin/members', '/v1/admin/member-roster', '/v1/admin/activity-refund-requests']);
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {
      if (!['https://fonts.googleapis.com', 'https://fonts.gstatic.com'].includes(url.origin)) state.unknown.push(request.method() + ' external');
      return route.abort();
    }
    if (path.startsWith('/__isolated-document/')) return route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><h1>隔離假文件</h1>' });
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, '.' + path) : resolve(root, 'index.html');
      assert.ok(file.startsWith(root + '/'));
      return route.fulfill({ contentType: contentTypes[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-document-token', user } });
    if (request.method() !== 'GET') { state.writes.push(request.method() + ' ' + path); return route.fulfill({ status: 405, json: { detail: '禁止隔離測試寫入' } }); }
    state.reads.push(path);
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, sales_scope: 'meals_only' } });
    if (path === '/v1/admin/membership-applications') return route.fulfill({ json: [application] });
    if (path === '/v1/admin/membership/admissions') return route.fulfill({ json: { items: [], total: 0, limit: 25, offset: 0 } });
    if (emptyPaths.has(path)) return route.fulfill({ json: [] });
    const download = path.match(/^\/v1\/admin\/membership-applications\/isolated-application\/documents\/([^/]+)\/download-url$/);
    if (download) {
      state.downloads.push(download[1]);
      return route.fulfill(rejectDownload
        ? { status: 409, headers: { 'X-Error-Code': 'demo_document_placeholder' }, json: { detail: message } }
        : { json: { download_url: base + '/__isolated-document/' + download[1], expires_in_seconds: 120 } });
    }
    state.unknown.push(request.method() + ' ' + path);
    return route.fulfill({ status: 404, json: { detail: '隔離測試未定義請求' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  context.on('page', popup => { if (popup !== page) { state.popups += 1; popup.on('pageerror', error => state.errors.push(error.message)); } });
  await page.goto(base + '/admin/social');
  if (admin) await page.getByRole('heading', { name: '隔離舊版申請人', exact: true }).waitFor();
  else await page.getByRole('heading', { name: '社務管理只向管理者開放。', exact: true }).waitFor();
  const card = page.locator('article.admin-review-card').filter({ has: page.getByRole('heading', { name: '隔離舊版申請人', exact: true }) });
  return { context, page, card, state };
}

async function layout(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.writes, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [390, 1440]) {
  test(`${width}px 示範附件顯示原因且不發下載請求或開啟分頁`, async t => {
    const { page, card, state } = await fixture(t, width);
    assert.equal(await card.getByRole('button', { name: '身分證正面', exact: true }).isDisabled(), true);
    await card.getByText(message, { exact: true }).waitFor();
    assert.deepEqual(state.downloads, []);
    assert.equal(state.popups, 0);
    await layout(page, state);
  });
  for (const [id, label] of [['ordinary', '身分證反面'], ['legacy-api', '第二證件']]) {
    test(`${width}px ${id}一般附件仍取得一次網址並顯示隔離假文件`, async t => {
      const { context, page, card, state } = await fixture(t, width);
      const button = card.getByRole('button', { name: label, exact: true });
      assert.equal(await button.isEnabled(), true);
      const opened = context.waitForEvent('page');
      await button.click();
      const popup = await opened;
      await popup.getByRole('heading', { name: '隔離假文件', exact: true }).waitFor();
      assert.deepEqual(state.downloads, [id]);
      assert.equal(state.popups, 1);
      await layout(page, state);
    });
  }
  test(`${width}px 下載API受控拒絕保留訊息且不開假成功分頁`, async t => {
    const { page, card, state } = await fixture(t, width, { rejectDownload: true });
    await card.getByRole('button', { name: '身分證反面', exact: true }).click();
    await card.locator('.form-error').getByText(message, { exact: true }).waitFor();
    assert.deepEqual(state.downloads, ['ordinary']);
    assert.equal(state.popups, 0);
    await layout(page, state);
  });
  test(`${width}px 非管理員保留原門禁且不讀附件或管理資料`, async t => {
    const { page, state } = await fixture(t, width, { admin: false });
    assert.equal(await page.getByText(message, { exact: true }).count(), 0);
    assert.deepEqual(state.reads.filter(path => path.startsWith('/v1/admin/')), []);
    assert.deepEqual(state.downloads, []);
    assert.equal(state.popups, 0);
    await layout(page, state);
  });
}
