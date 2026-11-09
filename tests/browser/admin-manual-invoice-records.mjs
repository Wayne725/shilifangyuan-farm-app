import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4327');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;

async function fixture(t, { width = 1280, role = 'admin', mode = 'records' } = {}) {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 960 }, serviceWorkers: 'block', timezoneId: 'Asia/Taipei' });
  const state = { reads: [], writes: [], unknown: [], errors: [], mode };
  const rows = Array.from({ length: 7 }, (_, index) => ({
    id: `audit-${index}`, order_id: `order-${index}`, order_number: `O203001020${index + 1}`,
    recorded_at: '2030-01-02T01:00:00Z', operator_name: index === 4 ? null : '合成店員',
    action: index % 2 ? 'invoice.arranged' : 'walk_in_sale.collected',
    sale_category: index % 2 ? null : index === 0 ? 'meal' : 'product',
    handling: index % 2 ? 'pickup_day' : 'on_payment', amount: 120,
    reason: '合成開票原因', invoice_status: ['pending', 'issued', 'failed', 'voided', 'void_pending', 'pending', 'issued'][index],
    invoice_number: index === 1 ? 'AB12345678' : null,
    invoice_date: index === 1 ? '2030-01-02T02:00:00Z' : null,
    requested_issue_after: index % 2 ? '2030-01-03T00:00:00+08:00' : null,
  }));
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    if (request.method() !== 'GET' && path !== '/v1/auth/refresh') state.writes.push(`${request.method()} ${path}`);
    if (path === '/v1/auth/refresh') return role === 'anonymous'
      ? route.fulfill({ status: 401, json: { detail: '尚未登入' } })
      : route.fulfill({ json: { access_token: 'isolated', user: { id: 'isolated-staff', user_role: role,
        display_name: '合成店員', email: 'staff@example.test', membership_type: 'nonmember' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/admin/invoice-register') return route.fulfill({ json: { items: [], total: 0 } });
    if (path === '/v1/admin/manual-invoice-records') {
      state.reads.push(Object.fromEntries(url.searchParams));
      if (state.mode === 'error') return route.fulfill({ status: 503, json: { detail: '隔離紀錄暫時無法讀取' } });
      if (state.mode === 'empty') return route.fulfill({ json: { items: [], total: 0 } });
      const offset = Number(url.searchParams.get('offset')), limit = Number(url.searchParams.get('limit'));
      return route.fulfill({ json: { items: rows.slice(offset, offset + limit), total: rows.length } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離請求' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/admin/invoices`);
  const records = page.locator('details.admin-manual-invoice-records');
  return { page, records, state };
}

for (const width of [1280, 390]) {
  test(`${width}px 手動開票紀錄預設收合，唯讀查詢、分頁及原交易連結`, async t => {
    const { page, records, state } = await fixture(t, { width });
    await page.getByText('全部訂單 · 共 0 筆', { exact: true }).waitFor();
    assert.equal(await records.getAttribute('open'), null);
    assert.deepEqual(state.reads, [], '未展開不可增加紀錄請求');
    assert.ok((await records.boundingBox()).height <= 80, '附屬功能收合時應只占一行');
    const summary = records.locator('summary');
    await summary.focus();
    await summary.press('Enter');
    await records.getByRole('link', { name: 'O2030010201', exact: true }).waitFor();
    assert.deepEqual(state.reads.at(-1), { limit: '5', offset: '0' });
    assert.equal(await records.locator('tbody tr').count(), 5);
    assert.match(await records.locator('thead').innerText(), /目前發票狀態/);
    const pending = records.locator('tbody tr').filter({ hasText: 'O2030010201' });
    assert.match(await pending.innerText(), /待開立/);
    assert.match(await pending.innerText(), /便當/);
    assert.match(await records.locator('tbody tr').filter({ hasText: 'O2030010203' }).innerText(), /一般商品/);
    assert.doesNotMatch(await pending.innerText(), /已開立|AB12345678/);
    const issued = records.locator('tbody tr').filter({ hasText: 'O2030010202' });
    assert.match(await issued.innerText(), /已開立.*\nAB12345678/);
    assert.match(await issued.innerText(), /取餐日開立/);
    assert.match(await records.innerText(), /開立失敗|開票失敗/);
    assert.match(await records.innerText(), /已作廢/);
    assert.match(await records.innerText(), /未記錄姓名/);
    assert.equal(await records.getByRole('link', { name: 'O2030010201', exact: true }).getAttribute('href'), '/admin?section=orders&q=O2030010201');
    const pagination = records.getByRole('navigation', { name: '手動開票紀錄分頁' });
    await pagination.getByRole('button', { name: '下一頁', exact: true }).click();
    await records.getByRole('link', { name: 'O2030010206', exact: true }).waitFor();
    assert.equal(await records.locator('tbody tr').count(), 2);
    assert.deepEqual(state.reads.at(-1), { limit: '5', offset: '5' });
    assert.equal(await pagination.getByRole('button', { name: '下一頁', exact: true }).isDisabled(), true);
    await pagination.getByRole('button', { name: '上一頁', exact: true }).click();
    await records.getByRole('link', { name: 'O2030010201', exact: true }).waitFor();
    if (process.env.RECORDS_TEST_ARTIFACT_DIR) await records.screenshot({ path: `${process.env.RECORDS_TEST_ARTIFACT_DIR}/manual-invoice-records-${width}.png` });
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await summary.click();
    assert.equal(await records.getAttribute('open'), null);
    await records.locator('table').waitFor({ state: 'detached' });
    assert.equal(await records.locator('table').count(), 0);
    assert.deepEqual(state.writes, []);
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });
}

test('手動開票紀錄空白與讀取失敗可更新恢復，不觸發開票或收款', async t => {
  const { records, state } = await fixture(t, { mode: 'empty' });
  await records.locator('summary').click();
  await records.getByText('尚無手動開票紀錄', { exact: true }).waitFor();
  state.mode = 'error';
  await records.getByRole('button', { name: '更新紀錄', exact: true }).click();
  await records.getByRole('alert').getByText('隔離紀錄暫時無法讀取', { exact: true }).waitFor();
  state.mode = 'records';
  await records.getByRole('button', { name: '更新紀錄', exact: true }).click();
  await records.getByRole('link', { name: 'O2030010201', exact: true }).waitFor();
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

for (const role of ['user', 'anonymous']) {
  test(`${role} 不顯示或請求手動開票紀錄`, async t => {
    const { page, records, state } = await fixture(t, { role });
    await page.getByRole('heading', { name: '發票管理', exact: true }).waitFor();
    assert.equal(await records.count(), 0);
    assert.deepEqual(state.reads, []);
    assert.deepEqual(state.writes, []);
    assert.deepEqual(state.unknown, []);
  });
}
