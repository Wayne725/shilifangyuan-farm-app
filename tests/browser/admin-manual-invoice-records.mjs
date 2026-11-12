import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4327');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;

async function fixture(t, { width = 1280, role = 'admin', mode = 'records', path = '/admin/invoices', exactOrders = false, legacyOrderApi = false } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium && process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 960 }, serviceWorkers: 'block', timezoneId: 'Asia/Taipei' });
  const state = { reads: [], orderReads: [], writes: [], unknown: [], errors: [], mode, onlySibling: false };
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
  if (exactOrders) rows[0].order_number = 'O2030010210';
  const selectedOrder = {
    id: rows[0].order_id, order_number: rows[0].order_number, sales_channel: 'regular', payment_method: 'online',
    fulfillment_method: 'cooperative_pickup', payment_status: 'paid', invoice_status: 'issued', fulfillment_status: 'pending_confirmation',
    customer_name: '原單聯絡人', contact_email: 'target@example.test', contact_phone_status: 'missing',
    amount_total: 120, tax_amount: 0, created_at: '2030-01-02T01:00:00Z', available_actions: ['cancel'], items: [],
  };
  const similarOrder = {
    ...selectedOrder, id: 'order-similar', order_number: 'O20300102100', customer_name: '相似編號聯絡人',
    contact_email: 'similar@example.test', created_at: '2030-01-02T02:00:00Z', fulfillment_status: 'ready_for_pickup',
  };
  const invoice = {
    ...selectedOrder, order_id: selectedOrder.id, invoice_number: 'AB12345678', status: 'issued',
    amount: 120, provider: 'fanyu', email: selectedOrder.contact_email,
  };
  const sale = {
    ...selectedOrder, customer_email: selectedOrder.contact_email, membership_type: 'nonmember',
    order_amount: 120, period_received: 120, period_refunded: 0, period_net: 120, refunds: [],
  };
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
    if (path === '/v1/products' || path === '/v1/admin/meals') return route.fulfill({ json: [] });
    if (request.method() === 'GET' && path.startsWith('/v1/admin/walk-in-sales/request/')) {
      return route.fulfill({ status: 404, json: { detail: '尚未找到此筆隔離交易' } });
    }
    if (path === '/v1/admin/invoice-register') return route.fulfill({ json: { items: exactOrders ? [invoice] : [], total: Number(exactOrders) } });
    if (path === '/v1/admin/finance/meal-sales') return route.fulfill({ json: {
      summary: { received: 120, refunded: 0, net: 120, receipt_count: 1, refund_count: 0, pending_refund_amount: 0, paid_meal_quantity: 0 },
      orders: { items: [sale], total: 1, limit: 20, offset: 0 }, daily: [], weekly: [], monthly: [], products: [],
    } });
    if (path === '/v1/admin/order-search') {
      const filters = Object.fromEntries(url.searchParams);
      state.orderReads.push(filters);
      const candidates = state.onlySibling ? [similarOrder] : [similarOrder, selectedOrder];
      const items = candidates.filter(order => !legacyOrderApi && filters.order_id
        ? order.id === filters.order_id : order.order_number.includes(filters.q || ''));
      return route.fulfill({ json: { items, total: items.length, limit: 12, offset: 0 } });
    }
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
  await page.goto(`${base}${path}`);
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
    const original = new URL(await records.getByRole('link', { name: 'O2030010201', exact: true }).getAttribute('href'), base);
    assert.equal(original.pathname, '/admin');
    assert.deepEqual(Object.fromEntries(original.searchParams), { section: 'orders', q: 'O2030010201', order_id: 'order-0' });
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

test('手機手動開票的近期紀錄預設收合，展開後可查最近五筆與完整紀錄', async t => {
  const { page, records, state } = await fixture(t, { width: 390, path: '/admin?section=manual-invoice' });
  const recent = page.locator('details.admin-manual-invoice-recent');
  const summary = recent.locator('summary');
  await summary.waitFor();
  assert.equal(await recent.getAttribute('open'), null);
  assert.equal(await recent.locator('table').count(), 0);
  assert.deepEqual(state.reads, []);
  await summary.focus();
  await summary.press('Enter');
  await recent.getByRole('link', { name: 'O2030010201', exact: true }).waitFor();
  assert.deepEqual(state.reads, [{ limit: '5', offset: '0' }]);
  assert.equal(await recent.locator('tbody tr').count(), 5);
  assert.equal(await recent.getByRole('navigation', { name: '手動開票紀錄分頁' }).count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  const history = recent.getByRole('link', { name: '查看完整紀錄', exact: true });
  assert.equal(await history.getAttribute('href'), '/admin/invoices#manual-invoice-records');
  if (process.env.RECORDS_TEST_ARTIFACT_DIR) await recent.screenshot({ path: `${process.env.RECORDS_TEST_ARTIFACT_DIR}/recent-invoice-open-390.png` });
  await summary.click();
  await recent.locator('table').waitFor({ state: 'detached' });
  if (process.env.RECORDS_TEST_ARTIFACT_DIR) await recent.screenshot({ path: `${process.env.RECORDS_TEST_ARTIFACT_DIR}/recent-invoice-closed-390.png` });
  await summary.click();
  await history.click();
  await records.getByRole('link', { name: 'O2030010201', exact: true }).waitFor();
  await records.getByRole('button', { name: '下一頁', exact: true }).click();
  await records.getByRole('link', { name: 'O2030010207', exact: true }).waitFor();
  assert.deepEqual(state.reads.at(-1), { limit: '5', offset: '5' });
  await records.locator('summary').click();
  await records.locator('table').waitFor({ state: 'detached' });
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

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

for (const [label, path, legacyOrderApi, width] of [
  ['手動開票', '/admin?section=manual-invoice', true, 390],
  ['發票清單', '/admin/invoices', false, 1280],
  ['銷售明細', '/admin/finance', true, 1280],
]) {
  test(`${label}只開啟所選訂單，尾碼 10 與 100 保持各自資料`, async t => {
    const { page, state } = await fixture(t, { path, width, exactOrders: true, legacyOrderApi });
    let link;
    if (label === '手動開票') {
      const recent = page.locator('details.admin-manual-invoice-recent');
      await recent.locator('summary').click();
      link = recent.getByRole('link', { name: 'O2030010210', exact: true });
    } else if (label === '發票清單') {
      link = page.getByRole('link', { name: '開啟訂單與發票', exact: true });
    } else {
      await page.locator('.meal-finance-sale > summary').click();
      link = page.getByRole('link', { name: '處理付款／發票', exact: true });
    }
    await link.click();
    const cards = page.locator('.operations-list article');
    await cards.getByText('原單聯絡人', { exact: true }).waitFor();
    assert.equal(await cards.count(), 1);
    await cards.getByLabel('履約：準備中', { exact: true }).waitFor();
    await cards.getByRole('button', { name: '進入備貨', exact: true }).waitFor();
    assert.equal(await cards.getByText('O20300102100', { exact: true }).count(), 0);
    assert.equal(await cards.getByRole('button', { name: '辦理退單', exact: true }).count(), 1);
    assert.equal(new URL(page.url()).searchParams.get('order_id'), 'order-0');
    assert.equal(state.orderReads.at(-1).order_id, 'order-0');
    assert.equal(state.orderReads.at(-1).q, 'O2030010210');

    if (label === '手動開票') {
      await page.reload();
      await cards.getByText('原單聯絡人', { exact: true }).waitFor();
      assert.equal(await cards.count(), 1);
      const search = page.getByRole('searchbox', { name: '搜尋訂單', exact: true });
      await search.fill('O20300102');
      await page.getByRole('button', { name: '搜尋', exact: true }).click();
      await cards.getByText('相似編號聯絡人', { exact: true }).waitFor();
      assert.equal(await cards.count(), 2);
      await cards.getByLabel('履約：可領取', { exact: true }).waitFor();
      await cards.getByRole('button', { name: '完成取貨', exact: true }).waitFor();
      assert.equal(state.orderReads.at(-1).order_id, undefined);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(state.writes, []);
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });
}

test('舊 API 只回傳相似編號時，指定單筆連結無法操作該相似訂單', async t => {
  const { page, state } = await fixture(t, { path: '/admin/invoices', exactOrders: true, legacyOrderApi: true });
  state.onlySibling = true;
  await page.getByRole('link', { name: '開啟訂單與發票', exact: true }).click();
  await page.getByText('找不到這筆訂單。', { exact: true }).waitFor();
  assert.equal(await page.locator('.operations-list article').count(), 0);
  assert.equal(await page.getByRole('button', { name: '辦理退單', exact: true }).count(), 0);
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
