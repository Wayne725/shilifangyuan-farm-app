import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const locations = [
  { id: 'instructions-only', name: '教育學院領取點', address: '', instructions: '請到一樓服務台領取。\n請攜帶取貨碼。', is_active: true },
  { id: 'address', name: '合作社門市', address: '測試路 1 號', instructions: '', is_active: true },
];

async function fixture(t, width, mode = 'ready') {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 844 }, serviceWorkers: 'block' });
  const state = { mode, reads: 0, writes: [], unknown: [], errors: [], release: null };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: 401, json: { detail: '尚未登入' } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/pickup-locations' && request.method() === 'GET') {
      state.reads += 1;
      if (state.mode === 'loading') await new Promise(resolve => { state.release = resolve; });
      return route.fulfill({ status: state.mode === 'error' ? 503 : 200, json: state.mode === 'error' ? { detail: '隔離測試錯誤' } : state.mode === 'empty' ? [] : locations });
    }
    if (request.method() !== 'GET') state.writes.push(path);
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/help`);
  await page.getByRole('heading', { name: '操作說明與常見問題', exact: true }).waitFor();
  return { page, state };
}

async function verify(page, state) {
  assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [1440, 390]) test(`${width}px 訪客展開取貨 FAQ 才讀取，無地址的說明及換行完整保留`, async t => {
  const { page, state } = await fixture(t, width, 'loading');
  await page.getByText('下拉選單如何使用？', { exact: true }).click();
  assert.equal(state.reads, 0);
  await page.getByText('各取貨點有哪些領取說明？', { exact: true }).click();
  await page.getByRole('status').filter({ hasText: '讀取取貨點中…' }).waitFor();
  assert.equal(state.reads, 1);
  state.release();
  await page.getByRole('heading', { name: '教育學院領取點', exact: true }).waitFor();
  assert.equal(await page.locator('.pickup-location-help-instructions').textContent(), locations[0].instructions);
  await page.getByText('測試路 1 號', { exact: true }).waitFor();
  await page.locator('.reading-menu > summary').click();
  await page.getByRole('button', { name: '放大', exact: true }).click();
  await verify(page, state);
});

test('取貨 FAQ 載入失敗可重試，空清單有明確結果', async t => {
  const { page, state } = await fixture(t, 390, 'error');
  await page.getByText('各取貨點有哪些領取說明？', { exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '取貨點說明暫時無法讀取。' }).waitFor();
  assert.equal(state.reads, 1);
  state.mode = 'empty';
  await page.getByRole('button', { name: '重新讀取', exact: true }).click();
  await page.getByText('目前沒有可用的取貨點。', { exact: true }).waitFor();
  assert.equal(state.reads, 2);
  await verify(page, state);
});
