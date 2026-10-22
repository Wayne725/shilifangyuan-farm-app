import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const event = {
  id: 'onsite-event', title: '合作社現場付款午餐', location: '合作社', status: 'published', allow_cash_payment: true,
  ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-02T20:00:00+08:00',
  pickup_starts_at: '2030-01-03T11:00:00+08:00', pickup_ends_at: '2030-01-03T13:30:00+08:00',
  offerings: [{ id: 'offering', meal_id: 'meal', meal_name: '蔬食便當', price: 120, capacity: 20, available_quantity: 20, reserved_quantity: 0, paid_quantity: 0, position: 0, is_active: true, option_groups: [] }],
};
const order = {
  id: 'onsite-order', order_number: 'SLF-ONSITE-001', sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
  meal_event_id: event.id, meal_event_title: event.title, venue_name: event.location,
  pickup_start: event.pickup_starts_at, pickup_end: event.pickup_ends_at, pickup_at: '2030-01-03T11:30:00+08:00',
  payment_method: 'cash_on_pickup', payment_status: 'pending', invoice_status: 'external', fulfillment_status: 'pending', amount_total: 120,
  created_at: '2030-01-02T03:00:00Z', available_actions: ['confirm_cash_payment'],
  items: [{ offering_id: 'offering', meal_name: '蔬食便當', quantity: 1, unit_price: 120, subtotal: 120, tax_type: 'taxable', selections: [] }],
};

async function fixture(t, width, admin = false, allowCash = true) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { order: structuredClone(order), writes: [], unknown: [], errors: [] };
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', email: 'buyer@example.test', display_name: '隔離使用者', user_role: admin ? 'admin' : 'customer' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (path === '/v1/meal-events/onsite-event') return route.fulfill({ json: { ...event, allow_cash_payment: allowCash } });
    if (path === '/v1/meal-events') return route.fulfill({ json: [event] });
    if (path === '/v1/meal-orders') return route.fulfill({ json: [state.order] });
    if (path === '/v1/admin/meals') return route.fulfill({ json: [] });
    if (path === '/v1/admin/meal-events') return route.fulfill({ json: [event] });
    if (path === '/v1/admin/meal-orders') return route.fulfill({ json: { items: [{ order: state.order, customer_name: '買家', customer_email: 'buyer@example.test', meal_period: 'lunch' }], total: 1, offset: 0, limit: 20 } });
    if (path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: 0, invoice_attention: 0, today_pickup: 0 } });
    if (path === '/v1/meal-events/onsite-event/quote') return route.fulfill({ json: { amount_total: 120, items: order.items, pickup: { location: event.location, starts_at: event.pickup_starts_at, ends_at: event.pickup_ends_at } } });
    if (path === '/v1/meal-events/onsite-event/orders') {
      state.writes.push({ path, body: request.postDataJSON() });
      return route.fulfill({ status: 201, json: { ...state.order, available_actions: ['request_cancellation'] } });
    }
    if (['/v1/orders/onsite-order/admin/cash-payment', '/v1/orders/onsite-order/admin/cash-unpaid'].includes(path)) {
      state.writes.push({ path, body: request.postDataJSON() });
      state.order = { ...state.order, onsite_payment: path.endsWith('cash-unpaid') ? null : { method: request.postDataJSON().collection_method, reference: request.postDataJSON().reference, paid_at: '2030-01-02T02:00:00Z' }, payment_status: path.endsWith('cash-unpaid') ? 'pending' : 'paid', available_actions: path.endsWith('cash-unpaid') ? ['confirm_cash_payment'] : ['mark_cash_unpaid'] };
      return route.fulfill({ json: state.order });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

for (const width of [390, 1280]) {
  test(`${width}px 現場付款只建立未付款預訂，發票交給POS且無線上金流`, async t => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`);
    await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
    await page.getByRole('radio', { name: '合作社現場付款', exact: true }).check();
    await page.getByRole('heading', { name: '合作社現場開票', exact: true }).waitFor();
    assert.equal(await page.getByRole('region', { name: '電子發票設定' }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/onsite-checkout-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: '確認預訂・現場付款', exact: true }).click();
    await page.waitForURL('**/meal-orders?order_id=onsite-order');
    await page.getByRole('heading', { name: '預訂成功・待現場付款', exact: true }).waitFor();
    assert.equal(state.writes.length, 1);
    assert.equal(state.writes[0].body.payment_method, 'cash_on_pickup');
    assert.equal('invoice_carrier_type' in state.writes[0].body, false);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
  test(`${width}px 後台可登錄POS收款與更正未付款`, async t => {
    const { page, state } = await fixture(t, width, true);
    await page.goto(`${base}/admin/meals`);
    await page.getByRole('button', { name: '標記已付款', exact: true }).click();
    await page.getByLabel('實收金額', { exact: true }).fill('120');
    await page.getByLabel('處理原因', { exact: true }).fill('已核對 POS');
    await page.getByLabel('現場收款方式', { exact: true }).selectOption('pos');
    await page.getByLabel('POS 單號／收款參考（選填）', { exact: true }).fill('POS-123');
    assert.equal(await page.getByRole('button', { name: '確認已付款', exact: true }).isEnabled(), false);
    await page.getByLabel('已核對上述款項確實收到', { exact: true }).check();
    await page.getByRole('button', { name: '確認已付款', exact: true }).click();
    await page.getByRole('button', { name: '更正為未付款', exact: true }).waitFor();
    await page.getByText(/已收款：POS 刷卡.*POS-123/).waitFor();
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/onsite-admin-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: '更正為未付款', exact: true }).click();
    await page.getByLabel('核對訂單金額', { exact: true }).fill('120');
    await page.getByLabel('處理原因', { exact: true }).fill('尚未收到款項');
    await page.getByLabel('已核對此筆誤標付款，實際尚未收款', { exact: true }).check();
    await page.getByRole('button', { name: '確認更正為未付款', exact: true }).click();
    await page.getByRole('button', { name: '標記已付款', exact: true }).waitFor();
    assert.equal(state.writes[0].body.collection_method, 'pos');
    assert.equal(state.writes[0].body.reference, 'POS-123');
    assert.equal(state.writes[1].path, '/v1/orders/onsite-order/admin/cash-unpaid');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}
test('送單回應中斷後重試與重新整理沿用同一識別碼及備註', async t => {
  const { page, state } = await fixture(t, 390);
  const attempts = [];
  await page.route('**/v1/meal-events/onsite-event/orders', route => {
    attempts.push(route.request().postDataJSON());
    if (attempts.length < 3) return route.fulfill({status: 503, json: {detail: '測試連線中斷'}});
    return route.fallback();
  });
  const url = `${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`;
  await page.goto(url);
  await page.getByRole('button', {name: '增加蔬食便當', exact: true}).click();
  await page.getByRole('radio', {name: '合作社現場付款', exact: true}).check();
  await page.getByLabel('餐點備註（選填）', {exact: true}).fill('飯少，不要辣');
  await page.getByRole('button', {name: '確認預訂・現場付款', exact: true}).click();
  await page.getByText('測試連線中斷', {exact: true}).waitFor();
  await page.getByRole('button', {name: '確認預訂・現場付款', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('.form-error')?.textContent === '測試連線中斷');
  await page.reload();
  await page.getByRole('radio', {name: '合作社現場付款', exact: true}).check();
  await page.getByLabel('餐點備註（選填）', {exact: true}).fill('飯少，不要辣');
  await page.getByRole('button', {name: '確認預訂・現場付款', exact: true}).click();
  await page.waitForURL('**/meal-orders?order_id=onsite-order');
  assert.equal(attempts.length, 3);
  assert.equal(new Set(attempts.map(body => body.client_request_id)).size, 1);
  assert.equal(attempts[2].kitchen_note, '飯少，不要辣');
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('未開放的場次不顯示現場付款選項', async t => {
  const { page, state } = await fixture(t, 390, false, false);
  await page.goto(`${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`);
  await page.getByRole('radio', { name: '線上付款', exact: true }).waitFor();
  assert.equal(await page.getByRole('radio', { name: '合作社現場付款', exact: true }).count(), 0);
  assert.deepEqual(state.unknown, []);
});
