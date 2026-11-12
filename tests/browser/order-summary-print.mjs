import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || !['http:', 'https:'].includes(target.protocol)
  || target.pathname !== '/' || target.username || target.password || target.search || target.hash) {
  throw new Error('Summary-print acceptance must use an isolated local origin');
}
const base = target.origin;
const summaryPath = '/v1/admin/order-summary';
const jobsPath = `${summaryPath}/print-jobs`;
const fingerprint = 'a'.repeat(64);
const totals = { order_count: 20, quantity: 100, product_count: 28 };
const previewPages = [
  '十里方圓\n已付款訂單彙總\n4 × 能量腿\n  1 × 主食：紫米飯\n  1 × 主食：豆皮壽司\n  2 × 主食：壽司\n第 1／2 張',
  '十里方圓\n已付款訂單彙總\n整單備註\nTEST-MEAL-100\n不要馬鈴薯、番茄，謝謝\n相關品項：香菇便當 1 份\n第 2／2 張',
];

function report(query) {
  const filters = { ...query };
  delete filters.limit; delete filters.offset;
  const allProducts = Array.from({ length: 28 }, (_, index) => ({ key: `product-${index}`, sales_channel: query.order_category === 'meal' ? 'meal_preorder' : 'regular',
    category: query.order_category === 'meal' ? '便當' : '加工品', name: `隔離品項 ${index + 1}`, unit: query.order_category === 'meal' ? '份' : '瓶', quantity: 2, order_count: 1 }));
  const limit = Number(query.limit || 25), offset = Number(query.offset || 0);
  return { ...filters, summary: { ...totals, paid_order_count: 20, pending_order_count: 3, inactive_order_count: 0 },
    categories: [{ category: allProducts[0].category, order_count: 20, quantity: 100 }],
    channels: [{ sales_channel: allProducts[0].sales_channel, order_count: 20, quantity: 100 }],
    category_options: [allProducts[0].category], products: { items: allProducts.slice(offset, offset + limit), total: allProducts.length, limit, offset } };
}

async function fixture(t, { width = 390, role = 'admin', mode = 'live' } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = { summaries: [], previews: [], writes: [], resolutions: [], history: [], lookups: [], batches: new Map(), unknown: [], external: [], errors: [],
    mode, stale: false, empty: false, lostResponse: '', rejectPost: 0, holdSend: false, releaseSend: null, queryStatuses: ['printed', 'printed'] };
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin === 'https://fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
    if (url.origin !== base) { state.external.push(url.origin); return route.abort(); }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh' && method === 'POST') return route.fulfill({ status: role === 'guest' ? 401 : 200, json: role === 'guest' ? { detail: '隔離訪客' } : {
      access_token: 'summary-print-isolated', user: { id: `isolated-${role}`, display_name: '隔離管理員', email: 'admin@example.test', user_role: role, membership_type: 'nonmember' },
    } });
    if (path === '/v1/platform' && method === 'GET') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/members/me' && method === 'GET') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
    if (path === '/v1/notifications' && method === 'GET') return route.fulfill({ json: [] });
    if (path === summaryPath && method === 'GET') {
      const query = Object.fromEntries(url.searchParams); state.summaries.push(query);
      return route.fulfill({ json: report(query) });
    }
    if (path === `${summaryPath}/print-preview` && method === 'GET') {
      const filters = Object.fromEntries(url.searchParams); state.previews.push(filters);
      if (state.empty) return route.fulfill({ status: 422, json: { detail: '沒有可列印的已付款訂單' } });
      return route.fulfill({ json: { fingerprint, filters: { ...filters, status: 'paid' }, summary: totals, pages: previewPages, page_count: 2, mode: state.mode } });
    }
    if (path === jobsPath && method === 'GET') {
      state.history.push(Object.fromEntries(url.searchParams));
      return route.fulfill({ json: { items: [...state.batches.values()] } });
    }
    if (path === jobsPath && method === 'POST') {
      const body = request.postDataJSON(); state.writes.push({ path, body });
      if (state.holdSend) await new Promise((resolve) => { state.releaseSend = resolve; });
      if (state.rejectPost) return route.fulfill({ status: state.rejectPost, json: { detail: '隔離測試：登入或權限已變更' } });
      if (state.stale) return route.fulfill({ status: 409, json: { detail: '訂單資料或出單設備已變更，請重新預覽後列印' } });
      const lost = state.lostResponse; state.lostResponse = '';
      if (lost === 'before') return route.abort('failed');
      if (!state.batches.has(body.request_id)) state.batches.set(body.request_id, {
        request_id: body.request_id, fingerprint: body.fingerprint, mode: state.mode, created_at: '2030-01-02T02:00:00Z', filters: body.filters, summary: totals,
        pages: [1, 2].map((number) => ({ id: `${body.request_id}-${number}`, page: number, status: state.mode === 'dry_run' ? 'simulated' : 'pending', last_error: null })),
      });
      if (lost === 'after') return route.abort('failed');
      return route.fulfill({ status: 201, json: state.batches.get(body.request_id) });
    }
    if (path.startsWith(`${jobsPath}/`) && path.endsWith('/resolve') && method === 'POST') {
      const [, requestId, , pageId] = path.slice(jobsPath.length).split('/');
      const body = request.postDataJSON(); state.resolutions.push({ requestId, pageId, body });
      const batch = state.batches.get(requestId), selected = batch?.pages.find((page) => page.id === pageId);
      if (!selected) return route.fulfill({ status: 404, json: { detail: '找不到此批彙總列印工作' } });
      selected.status = body.printed ? 'printed' : 'unknown';
      selected.last_error = body.printed ? null : 'admin_has_not_seen_paper';
      state.queryStatuses[selected.page - 1] = selected.status;
      return route.fulfill({ json: batch });
    }
    if (path.startsWith(`${jobsPath}/`) && method === 'GET') {
      const requestId = path.slice(jobsPath.length + 1); state.lookups.push(requestId);
      const batch = state.batches.get(requestId);
      if (!batch) return route.fulfill({ status: 404, json: { detail: '找不到彙總列印工作' } });
      batch.pages = batch.pages.map((page, index) => ({ ...page, status: state.queryStatuses[index],
        last_error: state.queryStatuses[index] === 'failed' ? 'summary_changed_before_send' : null }));
      return route.fulfill({ json: batch });
    }
    state.unknown.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated summary-print API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

async function waitForReport(page) {
  await page.getByRole('heading', { name: '訂單彙整', level: 1, exact: true }).waitFor();
  await page.locator('.admin-order-summary > [aria-busy="false"]').waitFor();
}

async function openPreview(page) {
  await page.getByRole('button', { name: '列印彙總單', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '彙總單列印', exact: true });
  await dialog.getByRole('region', { name: '第 2 張預覽', exact: true }).waitFor();
  return dialog;
}

async function verifyIsolated(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.external, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不應橫向溢出');
}

for (const width of [390, 1280]) {
  test(`${width}px 已套用條件預覽完整彙總、確認才送印且顯示每頁結果`, { timeout: 35000 }, async (t) => {
    const { page, state } = await fixture(t, { width });
    await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
    assert.deepEqual(state.previews, []); assert.deepEqual(state.history, []); assert.deepEqual(state.writes, []);
    await page.getByRole('button', { name: '下一頁', exact: true }).click(); await waitForReport(page);
    assert.equal(state.summaries.at(-1).offset, '25');
    await page.getByLabel('品項名稱', { exact: true }).fill('尚未查詢的品項');
    await page.getByLabel('取餐日期迄（台灣）', { exact: true }).fill('2030-01-15');
    const dialog = await openPreview(page);
    const applied = { ...state.summaries.at(-1), status: 'paid' }; delete applied.limit; delete applied.offset;
    assert.deepEqual(state.previews.at(-1), applied, '列印只能使用畫面已套用條件，並排除分頁');
    await dialog.getByText('只印已付款且未取消', { exact: true }).waitFor();
    await dialog.getByText('20 筆訂單 · 100 份／件 · 28 種品項 · 2 張', { exact: true }).waitFor();
    assert.match(await dialog.getByRole('region', { name: '第 1 張預覽', exact: true }).innerText(), /主食：紫米飯/);
    assert.match(await dialog.getByRole('region', { name: '第 2 張預覽', exact: true }).innerText(), /整單備註[\s\S]*不要馬鈴薯、番茄/);
    assert.deepEqual(state.writes, []);
    if (process.env.BROWSER_SCREENSHOT_DIR) {
      await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.BROWSER_SCREENSHOT_DIR, `summary-print-preview-${width}.png`) });
    }
    state.holdSend = true;
    await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
    await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).isDisabled(), true);
    assert.equal(await dialog.getByRole('button', { name: '查詢送印結果', exact: true }).isDisabled(), true);
    assert.equal(state.writes.length, 1);
    assert.match(state.writes[0].body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.equal(state.writes[0].body.fingerprint, fingerprint);
    assert.deepEqual(state.writes[0].body.filters, applied);
    state.releaseSend();
    await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
    assert.equal(await dialog.getByText('等待發送', { exact: true }).count(), 2);
    assert.equal(await dialog.getByRole('button', { name: '建立另一份彙總單', exact: true }).count(), 0);
    state.queryStatuses = ['printed', 'failed'];
    await dialog.getByRole('button', { name: '查詢送印結果', exact: true }).click();
    await dialog.getByText('已確認列印', { exact: true }).waitFor();
    await dialog.getByText('出單失敗', { exact: true }).waitFor();
    await dialog.getByText('訂單資料已變更，這張未送印；請核對已印頁數後重新查詢。', { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '建立另一份彙總單', exact: true }).isDisabled(), true);
    assert.equal(state.writes.length, 1);
    await verifyIsolated(page, state);
    if (process.env.BROWSER_SCREENSHOT_DIR) {
      await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.BROWSER_SCREENSHOT_DIR, `summary-print-${width}.png`), fullPage: true });
    }
    await dialog.getByRole('button', { name: '關閉彙總列印', exact: true }).click();
    await page.locator('.order-summary-print > details > summary').click();
    await page.locator('.order-summary-print > details').getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
    assert.equal(state.history.length, 1);
    assert.equal(state.history[0].limit, '10');
    assert.equal(state.writes.length, 1);
    await verifyIsolated(page, state);
  });
}

test('一般訂單沿用下單日期、通路及搜尋條件，模擬模式不顯示正式送印文案', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t, { mode: 'dry_run' });
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  await page.getByRole('button', { name: '一般訂單', exact: true }).click(); await waitForReport(page);
  await page.getByRole('combobox', { name: '銷售類型', exact: true }).selectOption('group');
  await page.getByRole('combobox', { name: '訂單狀態', exact: true }).selectOption('pending');
  await page.getByLabel('品項名稱', { exact: true }).fill('  白米  ');
  await page.getByRole('button', { name: '查詢', exact: true }).click(); await waitForReport(page);
  const dialog = await openPreview(page);
  assert.deepEqual(state.previews[0], { starts_on: '2030-01-01', ends_on: '2030-01-02', date_basis: 'order', sales_channel: 'group', order_category: 'general', status: 'paid', category: '', search: '白米' });
  await dialog.getByText('目前為模擬模式，不會送到出單機。', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).count(), 0);
  await dialog.getByRole('button', { name: '確認模擬 2 張', exact: true }).click();
  await dialog.getByText('模擬列印 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  assert.equal(await dialog.getByText('模擬完成', { exact: true }).count(), 2);
  assert.equal(state.writes.length, 1);
  await verifyIsolated(page, state);
});

test('送印回應遺失後重試沿用相同識別碼、相同篩選及指紋', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t); state.lostResponse = 'before';
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  const dialog = await openPreview(page);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  assert.equal(state.writes.length, 1); assert.equal(state.batches.size, 0);
  const attempt = state.writes[0].body;
  assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem('order-summary-print:isolated-admin'))), attempt);
  await dialog.getByRole('button', { name: '查詢送印結果', exact: true }).click();
  await dialog.getByRole('alert').getByText('尚未查到這筆送印，可重試同一筆送印；系統會沿用原識別碼。', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).click();
  await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  assert.equal(state.writes.length, 2); assert.equal(state.batches.size, 1);
  assert.deepEqual(state.writes[1].body, attempt);
  assert.deepEqual(state.lookups, [attempt.request_id]);
  await verifyIsolated(page, state);
});

test('已建立但回應遺失，重新整理後查到原批次而不另送印', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t); state.lostResponse = 'after';
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  let dialog = await openPreview(page);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  const requestId = state.writes[0].body.request_id;
  assert.equal(state.batches.size, 1);
  await page.reload(); await waitForReport(page);
  await page.getByRole('button', { name: '查看彙總送印結果', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '彙總單列印', exact: true });
  await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  assert.equal(await dialog.getByText('已確認列印', { exact: true }).count(), 2);
  assert.equal(state.writes.length, 1); assert.equal(state.previews.length, 1);
  assert.deepEqual(state.lookups, [requestId]);
  assert.equal(await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).count(), 0);
  await verifyIsolated(page, state);
});

for (const status of [401, 403]) {
  test(`已建立且回應遺失後重試 ${status}，保留原識別碼避免重複出單`, { timeout: 25000 }, async (t) => {
    const { page, state } = await fixture(t); state.lostResponse = 'after';
    await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
    let dialog = await openPreview(page);
    await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
    await dialog.getByRole('alert').waitFor();
    const originalAttempt = structuredClone(state.writes[0].body);
    assert.equal(state.batches.size, 1);
    state.rejectPost = status;
    await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).click();
    await page.getByRole('button', { name: status === 401 ? '管理者登入' : '重新驗證管理權限', exact: true }).waitFor();
    assert.equal(await dialog.count(), 0);
    assert.equal(await page.locator('.order-summary-print').count(), 0);
    assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem('order-summary-print:isolated-admin'))), originalAttempt);
    assert.ok(state.writes.length >= 2);
    for (const request of state.writes) assert.deepEqual(request.body, originalAttempt);
    assert.equal(state.batches.size, 1);
    assert.equal(await dialog.getByRole('button', { name: '重新產生預覽', exact: true }).count(), 0);
    assert.equal(await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).count(), 0);
    state.rejectPost = 0;
    const postCount = state.writes.length;
    await page.reload();
    await page.getByRole('button', { name: '查看彙總送印結果', exact: true }).click();
    dialog = page.getByRole('dialog', { name: '彙總單列印', exact: true });
    await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
    assert.equal(state.writes.length, postCount); assert.equal(state.batches.size, 1);
    assert.deepEqual(state.lookups, [originalAttempt.request_id]);
    await verifyIsolated(page, state);
  });
}

test('關閉預覽並更改日期後，新預覽失敗不保留舊單或送印按鈕', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  let dialog = await openPreview(page);
  assert.equal(state.previews[0].ends_on, '2030-01-02');
  await dialog.getByRole('button', { name: '關閉彙總列印', exact: true }).click();
  await page.getByLabel('取餐日期迄（台灣）', { exact: true }).fill('2030-01-15');
  await page.getByRole('button', { name: '查詢', exact: true }).click(); await waitForReport(page);
  state.empty = true;
  await page.getByRole('button', { name: '列印彙總單', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '彙總單列印', exact: true });
  await dialog.getByRole('alert').getByText('沒有可列印的已付款訂單', { exact: true }).waitFor();
  assert.equal(state.previews[1].ends_on, '2030-01-15');
  assert.equal(await dialog.getByRole('region', { name: /第 .* 張預覽/ }).count(), 0);
  assert.equal(await dialog.getByRole('button', { name: /確認列印/ }).count(), 0);
  assert.equal(await dialog.getByRole('button', { name: '重新產生預覽', exact: true }).isEnabled(), true);
  assert.deepEqual(state.writes, []);
  state.empty = false;
  await dialog.getByRole('button', { name: '重新產生預覽', exact: true }).click();
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).waitFor();
  assert.equal(state.previews[2].ends_on, '2030-01-15');
  assert.deepEqual(state.writes, []);
  await verifyIsolated(page, state);
});

test('新分頁由最近紀錄恢復待確認批次並核對紙本，不建立新列印', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  let dialog = await openPreview(page);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  const originalAttempt = structuredClone(state.writes[0].body), batch = state.batches.get(originalAttempt.request_id);
  batch.pages[0].status = 'unknown'; batch.pages[1].status = 'printed'; state.queryStatuses = ['unknown', 'printed'];
  const otherPage = await page.context().newPage(); otherPage.setDefaultTimeout(8000);
  otherPage.on('pageerror', (error) => state.errors.push(error.message));
  await otherPage.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  await page.close();
  await otherPage.goto(`${base}/admin?section=summary`); await waitForReport(otherPage);
  assert.equal(await otherPage.evaluate(() => sessionStorage.getItem('order-summary-print:isolated-admin')), null);
  assert.deepEqual(state.history, []);
  await otherPage.locator('.order-summary-print > details > summary').click();
  await otherPage.getByRole('button', { name: '查看／核對這批', exact: true }).click();
  dialog = otherPage.getByRole('dialog', { name: '彙總單列印', exact: true });
  await dialog.getByText('結果待確認', { exact: true }).waitFor();
  assert.deepEqual(await otherPage.evaluate(() => JSON.parse(sessionStorage.getItem('order-summary-print:isolated-admin'))), originalAttempt);
  assert.equal(state.previews.length, 1); assert.equal(state.writes.length, 1);
  await dialog.getByRole('button', { name: '查詢送印結果', exact: true }).click();
  await dialog.getByText('結果待確認', { exact: true }).waitFor();
  assert.deepEqual(state.lookups, [originalAttempt.request_id]);
  await dialog.locator('details.summary-print-manual > summary').click();
  await dialog.getByLabel('核對頁次', { exact: true }).selectOption(batch.pages[0].id);
  await dialog.getByLabel('核對紀錄', { exact: true }).fill('新分頁核對第一張紙本');
  await dialog.getByRole('button', { name: '確認已收到此張', exact: true }).click();
  await dialog.getByLabel('已核對紙本，需要另印一份彙總單', { exact: true }).waitFor();
  assert.equal(await dialog.getByText('已確認列印', { exact: true }).count(), 2);
  assert.equal(state.resolutions.length, 1); assert.equal(state.resolutions[0].requestId, originalAttempt.request_id);
  assert.equal(state.writes.length, 1); assert.equal(state.batches.size, 1);
  await verifyIsolated(otherPage, state);
});

test('訂單變更 409 清除舊預覽，不宣稱成功且必須重新產生', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t); state.stale = true;
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  const dialog = await openPreview(page);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByRole('alert').getByText('訂單資料或出單設備已變更，請重新預覽後列印', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('region', { name: '第 1 張預覽', exact: true }).count(), 0);
  assert.equal(await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('order-summary-print:isolated-admin')), null);
  assert.equal(state.batches.size, 0);
  state.stale = false;
  await dialog.getByRole('button', { name: '重新產生預覽', exact: true }).click();
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).waitFor();
  assert.equal(state.previews.length, 2); assert.equal(state.writes.length, 1);
  await verifyIsolated(page, state);
});

test('結果待確認必須勾選紙本核對與重複列印確認，才可建立新的批次', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  const dialog = await openPreview(page);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  state.queryStatuses = ['submitted', 'unknown'];
  await dialog.getByRole('button', { name: '查詢送印結果', exact: true }).click();
  await dialog.getByText('結果待確認', { exact: true }).waitFor();
  const another = dialog.getByRole('button', { name: '建立另一份彙總單', exact: true });
  assert.equal(await another.isDisabled(), true);
  assert.equal(state.writes.length, 1);
  await dialog.getByLabel('已核對紙本，接受可能重複列印並另印一份', { exact: true }).check();
  await another.click();
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).waitFor();
  assert.equal(state.previews.length, 2); assert.equal(state.writes.length, 1);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  assert.equal(state.writes.length, 2); assert.equal(state.batches.size, 2);
  assert.notEqual(state.writes[0].body.request_id, state.writes[1].body.request_id);
  assert.deepEqual(state.writes[0].body.filters, state.writes[1].body.filters);
  await verifyIsolated(page, state);
});

test('人工核對指定頁可略過紀錄，確認收到及尚未收到各自保留頁面狀態', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  const dialog = await openPreview(page);
  await dialog.getByRole('button', { name: '確認列印 2 張', exact: true }).click();
  await dialog.getByText('彙總單列印紀錄 · 20 筆訂單 · 2 張', { exact: true }).waitFor();
  state.queryStatuses = ['unknown', 'submitted'];
  await dialog.getByRole('button', { name: '查詢送印結果', exact: true }).click();
  await dialog.locator('details.summary-print-manual > summary').click();
  const received = dialog.getByRole('button', { name: '確認已收到此張', exact: true });
  assert.equal(await received.isDisabled(), true);
  const batch = [...state.batches.values()][0];
  await dialog.getByLabel('核對頁次', { exact: true }).selectOption(batch.pages[0].id);
  assert.equal(await received.isEnabled(), true);
  await received.click();
  await dialog.getByText('已確認列印', { exact: true }).waitFor();
  assert.deepEqual(state.resolutions[0], { requestId: batch.request_id, pageId: batch.pages[0].id, body: { printed: true, reason: '' } });
  assert.equal(await dialog.getByLabel('核對頁次', { exact: true }).inputValue(), '');
  assert.equal(await dialog.getByLabel('核對紀錄', { exact: true }).inputValue(), '');
  assert.equal(await dialog.getByLabel('核對頁次', { exact: true }).locator(`option[value="${batch.pages[0].id}"]`).count(), 0);
  await dialog.getByLabel('核對頁次', { exact: true }).selectOption(batch.pages[1].id);
  await dialog.getByLabel('核對紀錄', { exact: true }).fill('第二張尚未收到');
  await dialog.getByRole('button', { name: '記錄尚未收到', exact: true }).click();
  await dialog.getByText('結果待確認', { exact: true }).waitFor();
  await dialog.getByText('管理員尚未收到紙本，結果待確認。', { exact: true }).waitFor();
  assert.deepEqual(state.resolutions[1], { requestId: batch.request_id, pageId: batch.pages[1].id, body: { printed: false, reason: '第二張尚未收到' } });
  assert.equal(await dialog.getByText('已確認列印', { exact: true }).count(), 1);
  assert.equal(state.writes.length, 1, '人工核對不能另送列印工作');
  await verifyIsolated(page, state);
});

test('查詢含待付款但沒有可印已付款資料時顯示原因，不送印', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t); state.empty = true;
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  await page.getByRole('button', { name: '列印彙總單', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '彙總單列印', exact: true });
  await dialog.getByRole('alert').getByText('沒有可列印的已付款訂單', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: /確認列印/ }).count(), 0);
  assert.deepEqual(state.writes, []);
  await verifyIsolated(page, state);
});

for (const role of ['guest', 'customer']) {
  test(`${role} 無法看見彙總列印或查詢任何管理列印 API`, { timeout: 20000 }, async (t) => {
    const { page, state } = await fixture(t, { role });
    await page.goto(`${base}/admin?section=summary`);
    await page.getByRole('heading', { name: '管理工作台', level: 1, exact: true }).waitFor();
    assert.equal(await page.getByRole('region', { name: '彙總列印', exact: true }).count(), 0);
    assert.deepEqual(state.summaries, []); assert.deepEqual(state.previews, []); assert.deepEqual(state.history, []);
    assert.deepEqual(state.lookups, []); assert.deepEqual(state.writes, []);
    await verifyIsolated(page, state);
  });
}


test('缺少原始資料的彙總批次仍可查詢頁面結果，無法直接重送', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t);
  const requestId = 'ea5b5152-879b-4f66-9406-089a1fc2e905';
  state.queryStatuses = ['failed'];
  state.batches.set(requestId, {
    request_id: requestId, fingerprint: null, mode: 'live', created_at: '2030-01-02T02:00:00Z', filters: null, summary: null,
    pages: [{ id: `${requestId}-1`, page: 1, status: 'failed', last_error: 'summary_changed_before_send' }],
  });
  await page.goto(`${base}/admin?section=summary`); await waitForReport(page);
  const history = page.locator('.order-summary-print > details');
  const render = Promise.race([
    history.getByText('原始列印資料不完整', { exact: true }).waitFor(),
    page.waitForEvent('pageerror').then((error) => { throw error; }),
  ]);
  await page.getByText('最近列印紀錄', { exact: true }).click();
  await render;
  await history.getByText('出單失敗', { exact: true }).waitFor();
  await history.getByRole('button', { name: '查看／核對這批', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '彙總單列印', exact: true });
  await dialog.getByText('出單失敗', { exact: true }).waitFor();
  state.batches.delete(requestId);
  await page.reload(); await waitForReport(page);
  await page.getByRole('button', { name: '查看彙總送印結果', exact: true }).click();
  await dialog.getByText('找不到這筆列印紀錄。', { exact: true }).waitFor();
  assert.deepEqual(state.lookups, [requestId]);
  assert.equal(await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).count(), 0);
  assert.equal(state.writes.length, 0);
  await verifyIsolated(page, state);
});
