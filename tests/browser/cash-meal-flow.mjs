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
  const state = {
    order: structuredClone(order), locations: [], quotes: [], writes: [], unknown: [], errors: [], paymentAttempts: 0,
    email: 'buyer@example.test', phone: '0912345678',
    paymentUrl: `${base}/meal-orders?order_id=onsite-order`,
  };
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-meal-payment') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<h1>隔離付款頁</h1>' });
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', email: state.email, display_name: '隔離使用者', user_role: admin ? 'admin' : 'customer' } } });
    if (path === '/v1/meal-checkout/contact') return route.fulfill({ json: { phone: state.phone } });
    if (path === '/v1/meal-announcements') return route.fulfill({ json: [] });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: state.locations });
    if (path === '/v1/meal-events/onsite-event') return route.fulfill({ json: { ...event, allow_cash_payment: allowCash } });
    if (path === '/v1/meal-events') return route.fulfill({ json: [event] });
    if (path === '/v1/meal-orders') return route.fulfill({ json: [state.order] });
    if (path === '/v1/meal-orders/onsite-order') return route.fulfill({ json: state.order });
    if (path === '/v1/orders/onsite-order/payment-attempts') {
      state.paymentAttempts++;
      return route.fulfill({ json: { payment_url: state.paymentUrl } });
    }
    if (path === '/v1/admin/meals') return route.fulfill({ json: [] });
    if (path === '/v1/admin/meal-events') return route.fulfill({ json: [event] });
    if (path === '/v1/admin/meal-orders') {
      state.lastMealOrderQuery = Object.fromEntries(url.searchParams);
      return route.fulfill({ json: { items: [{ order: state.order, customer_name: '買家', customer_email: 'buyer@example.test', meal_period: 'lunch' }], total: 1, offset: 0, limit: 20 } });
    }
    if (path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: 0, invoice_attention: 0, today_pickup: 0 } });
    if (path === '/v1/meal-events/onsite-event/quote') {
      const body = request.postDataJSON();
      state.quotes.push(body);
      const location = state.locations.find(location => location.id === body.pickup_location_id);
      return route.fulfill({ json: { amount_total: 120, items: order.items, needs_utensils: body.needs_utensils, pickup: { location_id: location?.id || null, location: location?.name || event.location, address: location?.address || '', instructions: '', starts_at: event.pickup_starts_at, ends_at: event.pickup_ends_at } } });
    }
    if (path === '/v1/meal-events/onsite-event/orders') {
      const body = request.postDataJSON();
      const location = state.locations.find(location => location.id === body.pickup_location_id);
      state.writes.push({ path, body });
      state.order = { ...state.order, payment_method: body.payment_method, invoice_status: 'not_eligible', available_actions: ['pay'], pickup_location_id: location?.id || null, venue_name: location?.name || event.location, pickup_address: location?.address || '', needs_utensils: body.needs_utensils };
      return route.fulfill({ status: 201, json: state.order });
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
  test(`${width}px 舊場次即使開放現場付款，便當結帳只走線上付款並送出聯絡資料`, async t => {
    const { page, state } = await fixture(t, width);
    state.email = null;
    state.phone = null;
    state.paymentUrl = `${base}/isolated-meal-payment`;
    await page.goto(`${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`);
    await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
    await page.getByRole('heading', { name: '電子發票', exact: true }).waitFor();
    const email = page.getByLabel('聯絡 Email', { exact: true });
    const phone = page.getByLabel('聯絡電話', { exact: true });
    const submit = page.getByRole('button', { name: '預訂並前往線上付款', exact: true });
    assert.equal(await page.getByRole('radio', { name: '合作社現場付款', exact: true }).count(), 0);
    assert.equal(await email.getAttribute('required'), '');
    assert.equal(await phone.getAttribute('required'), '');
    assert.equal(await email.inputValue(), '');
    assert.equal(await phone.inputValue(), '');
    assert.equal(await submit.isDisabled(), true);
    await email.fill('order@example.test');
    await phone.focus();
    assert.equal(await submit.isDisabled(), true);
    assert.equal(state.writes.length, 0);
    await phone.fill('0911222333');
    await email.fill('');
    await phone.focus();
    assert.equal(await submit.isDisabled(), true);
    await page.getByText('請輸入有效的聯絡 Email', { exact: true }).waitFor();
    await email.fill('receipt@example.test');
    await phone.focus();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/online-contact-checkout-${width}.png`, fullPage: true });
    await submit.click();
    await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
    assert.equal(state.writes.length, 1);
    assert.equal(state.paymentAttempts, 1);
    assert.equal(state.writes[0].body.payment_method, 'online');
    assert.equal(state.writes[0].body.contact_email, 'receipt@example.test');
    assert.equal(state.writes[0].body.contact_phone, '0911222333');
    assert.equal(state.writes[0].body.invoice_buyer_email, 'order@example.test');
    assert.equal(state.writes[0].body.pickup_location_id, null);
    assert.equal(state.writes[0].body.needs_utensils, false);
    assert.equal('invoice_handling' in state.writes[0].body, false);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
  test(`${width}px 後台可登錄POS收款與更正未付款`, async t => {
    const { page, state } = await fixture(t, width, true);
    await page.goto(`${base}/admin/meals`);
    await page.getByRole('button', { name: '標記已付款', exact: true }).click();
    await page.getByLabel('實收金額', { exact: true }).fill('120');
    await page.getByLabel('處理原因', { exact: true }).fill('已核對 POS');
    await page.getByLabel('現場收款方式', { exact: true }).selectOption('pos');
    await page.getByLabel('POS 單號／收款參考', { exact: true }).fill('POS-123');
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
  await page.getByLabel('餐點備註（選填）', {exact: true}).fill('飯少，不要辣');
  await page.getByRole('button', {name: '預訂並前往線上付款', exact: true}).click();
  await page.getByText('測試連線中斷', {exact: true}).waitFor();
  await page.getByRole('button', {name: '預訂並前往線上付款', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('.form-error')?.textContent === '測試連線中斷');
  await page.reload();
  await page.getByLabel('餐點備註（選填）', {exact: true}).fill('飯少，不要辣');
  await page.getByRole('button', {name: '預訂並前往線上付款', exact: true}).click();
  await page.waitForURL('**/meal-orders?order_id=onsite-order');
  assert.equal(attempts.length, 3);
  assert.equal(new Set(attempts.map(body => body.client_request_id)).size, 1);
  assert.equal(attempts[2].kitchen_note, '飯少，不要辣');
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

for (const width of [390, 1280]) {
  test(`${width}px 便當沿用日常選品取餐點，餐具與取餐點草稿、試算、訂單保持一致`, async t => {
    const { page, state } = await fixture(t, width);
    state.locations = [
      { id: 'east', code: 'EAST', name: '合作社東站', address: '花蓮縣壽豐鄉東路 1 號', is_active: true },
      { id: 'west', code: 'WEST', name: '合作社西站', address: '花蓮縣壽豐鄉西路 2 號', is_active: true },
    ];
    await page.goto(`${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`);
    const firstQuote = page.waitForResponse(response => response.url().endsWith('/quote'));
    await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
    await firstQuote;
    assert.equal(state.quotes.at(-1).pickup_location_id, 'east');
    assert.equal(state.quotes.at(-1).needs_utensils, false);
    await page.getByLabel('取餐點', { exact: true }).selectOption('west');
    const updatedQuote = page.waitForResponse(response => response.url().endsWith('/quote') && response.request().postDataJSON().needs_utensils === true);
    await page.getByLabel('是否需要餐具', { exact: true }).selectOption('yes');
    await updatedQuote;
    assert.equal(state.quotes.at(-1).pickup_location_id, 'west');
    await page.reload();
    await page.getByRole('option', { name: '合作社西站', exact: true }).waitFor({ state: 'attached' });
    assert.equal(await page.getByLabel('取餐點', { exact: true }).inputValue(), 'west');
    assert.equal(await page.getByLabel('是否需要餐具', { exact: true }).inputValue(), 'yes');
    if (process.env.BROWSER_ARTIFACT_DIR) {
      await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); });
      await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/meal-pickup-${width === 390 ? 'mobile' : 'desktop'}.png`, fullPage: true });
    }
    await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
    await page.waitForURL('**/meal-orders?order_id=onsite-order');
    await page.locator('.meal-order-detail').getByText(`訂單編號：${order.order_number}`, { exact: true }).waitFor();
    assert.equal(state.writes[0].body.pickup_location_id, 'west');
    assert.equal(state.writes[0].body.needs_utensils, true);
    assert.equal(state.quotes.at(-1).pickup_location_id, 'west');
    assert.equal(state.quotes.at(-1).needs_utensils, true);
    assert.match(await page.locator('.meal-pickup-info').innerText(), /合作社西站.*花蓮縣壽豐鄉西路 2 號/s);
    await page.getByText('需要餐具', { exact: false }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

test('取餐點載入失敗阻止便當送單，可重試恢復', async t => {
  const { page, state } = await fixture(t, 390);
  await page.route('**/v1/pickup-locations', route => route.fulfill({ status: 503, json: { detail: '取餐點維護中' } }));
  await page.goto(`${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`);
  await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).isDisabled(), true);
  await page.getByRole('heading', { name: '取餐點暫時無法使用', exact: true }).waitFor({ timeout: 15000 });
  await page.unroute('**/v1/pickup-locations');
  await page.getByRole('button', { name: '重新連線', exact: true }).click();
  await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
  await page.waitForURL('**/meal-orders?order_id=onsite-order');
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].body.pickup_location_id, null);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('未開放的場次不顯示現場付款選項', async t => {
  const { page, state } = await fixture(t, 390, false, false);
  await page.goto(`${base}/meals/onsite-event?pickup_at=2030-01-03T11%3A30`);
  await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).waitFor();
  assert.equal(await page.getByLabel('聯絡 Email', { exact: true }).inputValue(), 'buyer@example.test');
  await page.waitForFunction(() => document.querySelector('input[autocomplete=tel]')?.value === '0912345678');
  assert.equal(await page.getByRole('radio', { name: '合作社現場付款', exact: true }).count(), 0);
  assert.deepEqual(state.unknown, []);
});

test('390px 後台場次卡隱藏現場付款設定，既有場次仍可檢視', async t => {
  const { page } = await fixture(t, 390, true);
  await page.goto(`${base}/admin/meals?tab=events`);
  await page.getByRole('heading', { name: event.title, exact: true }).waitFor();
  assert.equal(await page.getByRole('checkbox', { name: '開放合作社現場付款', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '查看本場訂單與取餐時間' }).count(), 1);
  if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/admin-online-meal-390.png`, fullPage: true });
});

test('390px 後台以現金未收篩選並為會餐確認付現', async t => {
  const { page, state } = await fixture(t, 390, true);
  state.order = { ...state.order, meal_booking_type: 'business', invoice_handling: 'pickup_day', invoice_status: 'not_eligible' };
  await page.goto(`${base}/admin/meals?tab=orders&queue=today_business_cash_uncollected`);
  await page.getByText('付款方式：現金・未收', { exact: true }).waitFor();
  assert.equal(state.lastMealOrderQuery.work_queue, 'today_business_cash_uncollected');
  await page.getByText('更多篩選', { exact: true }).click();
  await page.getByRole('checkbox', { name: '現金未收', exact: true }).check();
  const filtered = page.waitForResponse(response => new URL(response.url()).searchParams.get('cash_uncollected') === 'true');
  await page.getByRole('button', { name: '查詢訂單', exact: true }).click();
  await filtered;
  await page.getByRole('button', { name: '標記已付款', exact: true }).click();
  assert.equal(await page.getByLabel('開票方式', { exact: true }).count(), 0);
  await page.getByText('收款後系統會自動開立統編發票。', { exact: true }).waitFor();
  await page.getByLabel('實收金額', { exact: true }).fill('120');
  await page.getByRole('checkbox', { name: '已核對上述款項確實收到', exact: true }).check();
  await page.getByRole('button', { name: '確認已付款', exact: true }).click();
  await page.getByText('付款方式：現金・已收', { exact: true }).waitFor();
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].body.collection_method, 'cash');
  assert.equal(state.writes[0].body.invoice_handling, 'pickup_day');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});
