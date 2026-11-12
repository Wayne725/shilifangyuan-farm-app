import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import test from 'node:test';
import { mealCalendarFixture } from '../fixtures/meal-calendar.mjs';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname)
  || !['http:', 'https:'].includes(testUrl.protocol)
  || testUrl.username || testUrl.password || testUrl.pathname !== '/' || testUrl.search || testUrl.hash) {
  throw new Error('Meal browser acceptance must use an isolated local website origin');
}
const base = testUrl.origin;
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const orderId = 'eca04958-169c-4f0f-bd93-55702f324800';
const orderNumber = 'SLF-MEAL-ISOLATED';
const pickupCode = '316927';
const credentialPath = `/v1/meal-orders/${orderId}/pickup-credential`;
const paymentPath = `/v1/orders/${orderId}/payment-attempts`;

function mealOrder(overrides = {}) {
  return {
    id: orderId, order_number: orderNumber, revision: 1,
    sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
    meal_event_id: 'event-isolated', meal_event_title: '隔離便當場次',
    venue_name: '隔離取餐點', pickup_start: '2026-09-14T03:00:00Z', pickup_end: '2026-09-14T05:00:00Z',
    pickup_code: null, pickup_qr_payload: null, paid_at: null, cancelled_at: null,
    payment_status: 'pending', invoice_status: 'not_eligible', invoice_number: null, invoice_date: null,
    fulfillment_status: 'pending', amount_total: 120, created_at: '2026-09-12T01:00:00Z',
    available_actions: ['pay'],
    items: [{
      offering_id: 'offering-isolated', meal_id: 'meal-isolated', meal_name: '隔離蔬食便當',
      quantity: 1, base_price: 120, option_price: 0, unit_price: 120, subtotal: 120, selections: [],
    }],
    ...overrides,
  };
}

function paidOrder(overrides = {}) {
  return mealOrder({
    payment_status: 'paid', paid_at: '2026-09-14T03:00:00Z', fulfillment_status: 'ready',
    pickup_code: pickupCode, pickup_qr_payload: 'isolated-opaque-qr-token',
    invoice_status: 'issued', invoice_number: 'AB12345678', invoice_date: '2026-09-14T03:00:00Z',
    available_actions: [],
    ...overrides,
  });
}

async function fixture(t, initialOrder) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block',
  });
  const state = {
    order: initialOrder, credentialStatus: 200, credentialReads: 0,
    mealLookupStatus: 200, mealLookupReads: 0, generalLookupReads: [], ownOrderVisible: true,
    extraMealOrders: [], refreshStatus: 'pending', refreshReads: 0,
    blockedExternal: [], mutations: [], unknownApi: [],
    credential: { order_id: orderId, pickup_code: pickupCode, qr_token: 'isolated-opaque-qr-token' },
  };
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) {
      state.blockedExternal.push(url.origin);
      return route.abort();
    }
    if (url.pathname === '/isolated-meal-payment') {
      return route.fulfill({
        contentType: 'text/html; charset=utf-8',
        body: `<!doctype html><meta charset="utf-8"><h1>隔離付款頁</h1><a href="/orders?order_id=${orderId}&payment=paid">返回取餐憑證</a>`,
      });
    }
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    if (url.pathname === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '', support_phone: '' } });
    if (url.pathname === '/v1/auth/refresh') {
      return route.fulfill({ json: {
        access_token: 'isolated-meal-token',
        user: { id: 'buyer-isolated', email: 'buyer@example.test', display_name: '隔離買家', user_role: 'customer' },
      } });
    }
    if (request.method() === 'GET' && url.pathname === '/v1/meal-orders') {
      return route.fulfill({ json: [...state.extraMealOrders, ...(state.ownOrderVisible ? [state.order] : [])] });
    }
    if (request.method() === 'GET' && url.pathname === `/v1/meal-orders/${orderId}`) {
      state.mealLookupReads++;
      const status = state.ownOrderVisible ? state.mealLookupStatus : 404;
      return route.fulfill({ status, json: status === 200 ? state.order : { detail: '隔離取餐訂單查詢暫不可用' } });
    }
    if (request.method() === 'GET' && url.pathname === credentialPath) {
      state.credentialReads++;
      return route.fulfill({
        status: state.credentialStatus,
        json: state.credentialStatus === 200 ? state.credential : { detail: '此取餐憑證已失效，請核對訂單狀態' },
      });
    }
    if (request.method() === 'GET' && url.pathname === '/v1/orders') {
      return route.fulfill({ json: state.ownOrderVisible ? [{ ...state.order, order_kind: 'regular', tax_amount: 6,
        invoice: state.order.invoice_number ? { invoice_number: state.order.invoice_number, invoice_date: state.order.invoice_date } : null,
        shipment: null, items: [{ id: 'item-isolated', product_name: '隔離蔬食便當', unit_label: '份', quantity: 1, unit_price: 120, subtotal: 120, tax_type: 'taxable' }],
      }] : [] });
    }
    if (request.method() === 'GET' && /^\/v1\/orders\/[^/]+$/.test(url.pathname)) {
      const status = url.pathname === `/v1/orders/${orderId}` && state.ownOrderVisible ? 200 : 404;
      state.generalLookupReads.push({ path: url.pathname, ownOnly: url.searchParams.get('own_only'), status });
      return route.fulfill({ status, json: status === 200 ? state.order : { detail: '找不到本人訂單' } });
    }
    if (request.method() === 'POST' && url.pathname === paymentPath) {
      state.mutations.push({ path: paymentPath });
      return route.fulfill({ json: { payment_url: `${base}/isolated-meal-payment` } });
    }
    if (request.method() === 'POST' && url.pathname === '/v1/payment-attempts/attempt-isolated/refresh') {
      state.refreshReads++;
      return route.fulfill({ json: { id: 'attempt-isolated', order_id: orderId, order_revision: 1,
        superseded: false, status: state.refreshStatus } });
    }
    state.unknownApi.push(`${request.method()} ${url.pathname}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API，禁止連線真實後端' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.goto(`${base}/meal-orders`);
  await page.locator('.order-detail-head .order-number').filter({ hasText: orderNumber }).waitFor();
  const qr = page.getByLabel(`訂單 ${orderNumber} 取餐 QR Code`, { exact: true });
  return { page, state, qr };
}

async function visibleCredential(page, qr, code = pickupCode) {
  await qr.waitFor();
  assert.equal(await qr.locator('svg').count(), 1);
  await page.getByText(code, { exact: true }).waitFor();
  assert.match(code, /^\d{6}$/);
}

async function noCredential(page, qr) {
  await qr.waitFor({ state: 'hidden' });
  assert.equal(await page.getByText(pickupCode, { exact: true }).count(), 0);
}

async function verifyIsolation(page, state) {
  assert.deepEqual(state.unknownApi, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.doesNotMatch(await page.locator('.meal-orders-page').innerText(), /測試金流|測試付款|展示付款/);
}

test('便當待付款經隔離付款頁回來，顯示六位取餐碼、QR 與已開立發票', { timeout: 30000 }, async (t) => {
  const { page, state, qr } = await fixture(t, mealOrder());
  await page.getByRole('heading', { name: '付款後顯示取餐憑證', exact: true }).waitFor();
  assert.equal(state.credentialReads, 0);
  await noCredential(page, qr);
  await verifyIsolation(page, state);
  await page.getByRole('button', { name: '前往付款 $120', exact: true }).click();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  state.order = paidOrder();
  await page.getByRole('link', { name: '返回取餐憑證', exact: true }).click();
  await page.waitForURL(`${base}/meal-orders?order_id=${orderId}`);
  await visibleCredential(page, qr);
  const invoice = page.getByRole('region', { name: '電子發票', exact: true });
  await invoice.getByText('已開立', { exact: true }).waitFor();
  assert.match(await invoice.innerText(), /AB12345678/);
  assert.match(await invoice.innerText(), /9\/14.*11:00/);
  assert.deepEqual(state.mutations, [{ path: paymentPath }]);
  await verifyIsolation(page, state);
});

for (const [invoiceStatus, label] of [['pending', '開立處理中'], ['failed', '開立待處理']]) {
  test(`付款成功但發票 ${invoiceStatus}，仍可取餐且不要求重新付款`, { timeout: 20000 }, async (t) => {
    const { page, state, qr } = await fixture(t, paidOrder({ invoice_status: invoiceStatus, invoice_number: null, invoice_date: null }));
    await visibleCredential(page, qr);
    const invoice = page.getByRole('region', { name: '電子發票', exact: true });
    await invoice.getByText(label, { exact: true }).waitFor();
    assert.match(await invoice.innerText(), /不影響已付款訂單的取餐資格；請勿為了發票重新付款/);
    assert.equal(await page.getByRole('button', { name: /^前往付款/ }).count(), 0);
    assert.deepEqual(state.mutations, []);
    await verifyIsolation(page, state);
  });
}

for (const [label, update] of [
  ['已取消', { cancelled_at: '2026-09-14T03:01:00Z', fulfillment_status: 'cancelled', available_actions: [] }],
  ['退款中', { payment_status: 'refund_pending', available_actions: [] }],
  ['已退款', { payment_status: 'refunded', available_actions: [] }],
  ['已核銷', { fulfillment_status: 'picked_up', available_actions: [] }],
]) {
  test(`已顯示取餐碼後變為${label}，輪詢與重整均不保留舊 QR`, { timeout: 25000 }, async (t) => {
    const { page, state, qr } = await fixture(t, paidOrder());
    await visibleCredential(page, qr);
    // 保留已快取的碼，確認終止狀態本身就能讓前端撤下憑證。
    state.order = { ...state.order, ...update };
    await noCredential(page, qr);
    await page.getByText('為避免重複核銷，這筆訂單目前不顯示取餐憑證。', { exact: true }).waitFor();
    const readsAfterInvalidation = state.credentialReads;
    await page.reload();
    await page.locator('.order-detail-head .order-number').filter({ hasText: orderNumber }).waitFor();
    await noCredential(page, qr);
    assert.equal(state.credentialReads, readsAfterInvalidation);
    assert.deepEqual(state.mutations, []);
    await verifyIsolation(page, state);
  });
}

test('取餐憑證補查回覆 409 時立即移除舊 QR，重新查詢成功才顯示新碼', { timeout: 25000 }, async (t) => {
  const { page, state, qr } = await fixture(t, paidOrder());
  await visibleCredential(page, qr);
  state.credentialStatus = 409;
  await noCredential(page, qr);
  await page.getByText('此取餐憑證已失效，請核對訂單狀態', { exact: true }).waitFor();
  state.credentialStatus = 200;
  state.credential = { order_id: orderId, pickup_code: '628415', qr_token: 'isolated-revalidated-token' };
  await page.getByRole('button', { name: '重新查詢', exact: true }).click();
  await visibleCredential(page, qr, '628415');
  assert.equal(await page.getByText(pickupCode, { exact: true }).count(), 0);
  assert.deepEqual(state.mutations, []);
  await verifyIsolation(page, state);
});

test('已付款便當僅電話聯絡退單，不自行取消或撤下取餐 QR', { timeout: 20000 }, async (t) => {
  const { page, state, qr } = await fixture(t, paidOrder());
  await visibleCredential(page, qr);
  const phone = page.getByRole('button', { name: '退單聯絡電話尚未設定', exact: true });
  await phone.waitFor();
  assert.equal(await phone.isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: /申請取消|取消這筆便當|確認取消|送出退單申請/ }).count(), 0);
  assert.equal(await qr.isVisible(), true);
  assert.deepEqual(state.mutations, []);
  assert.equal(state.order.payment_status, 'paid');
  await verifyIsolation(page, state);
});

test('便當付款返回保留查款輪詢，確認成功後選取原訂單而非第一筆', { timeout: 25000 }, async (t) => {
  const { page, state, qr } = await fixture(t, mealOrder());
  state.extraMealOrders = [paidOrder({ id: 'another-meal-order', order_number: 'SLF-OTHER-MEAL', meal_event_title: '另一筆便當場次' })];
  await page.goto(`${base}/orders?order_id=${orderId}&payment=confirming&attempt_id=attempt-isolated`);
  await page.getByText('付款結果確認中', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/orders');
  assert.ok(state.refreshReads >= 1);
  assert.equal(state.credentialReads, 0);
  state.order = paidOrder();
  state.refreshStatus = 'paid';
  await page.waitForURL(`${base}/meal-orders?order_id=${orderId}`);
  await visibleCredential(page, qr);
  await page.locator('.order-detail-head .order-number').filter({ hasText: orderNumber }).waitFor();
  assert.deepEqual(state.mutations, []);
  await verifyIsolation(page, state);
});

for (const kind of ['regular', 'missing', 'not-owner']) {
  test(`付款返回 ${kind} 訂單不誤導向便當頁`, { timeout: 15000 }, async (t) => {
    const { page, state } = await fixture(t, mealOrder());
    if (kind === 'regular') state.order.sales_channel = 'regular';
    if (kind === 'not-owner') state.ownOrderVisible = false;
    const targetId = kind === 'missing' ? 'unknown-order' : orderId;
    const lookup = kind !== 'regular' ? page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === `/v1/orders/${targetId}` && url.searchParams.get('own_only') === 'true';
    }) : null;
    await page.goto(`${base}/orders?order_id=${targetId}&payment=paid`);
    if (lookup) {
      assert.equal((await lookup).status(), 404);
      await page.getByText('無法確認返回訂單', { exact: true }).waitFor();
      assert.deepEqual(state.generalLookupReads, [{ path: `/v1/orders/${targetId}`, ownOnly: 'true', status: 404 }]);
    } else {
      await page.getByRole('heading', { name: orderNumber, exact: true }).waitFor();
      assert.deepEqual(state.generalLookupReads, []);
    }
    assert.equal(new URL(page.url()).pathname, '/orders');
    assert.equal(await page.locator('.meal-orders-page').count(), 0);
    assert.equal(state.credentialReads, 0);
    assert.equal(state.mealLookupReads, 0);
    assert.deepEqual(state.unknownApi, []);
    assert.deepEqual(state.mutations, []);
  });
}

test('便當返回查詢失敗會顯示錯誤，不吞掉失敗或無限轉址', { timeout: 15000 }, async (t) => {
  const { page, state } = await fixture(t, mealOrder());
  state.mealLookupStatus = 503;
  await page.goto(`${base}/orders?order_id=${orderId}&payment=paid`);
  await page.getByText('便當取餐頁暫時無法開啟', { exact: true }).waitFor();
  await page.getByText('隔離取餐訂單查詢暫不可用', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/orders');
  assert.equal(state.credentialReads, 0);
  assert.equal(state.mealLookupReads, 1);
  assert.deepEqual(state.unknownApi, []);
  assert.deepEqual(state.mutations, []);
});

function scheduledEvent(overrides = {}) {
  return {
    id: 'scheduled-lunch', title: '隔離每日午餐', location: '隔離取餐區', status: 'published', can_edit: true,
    service_date: '2026-09-17', meal_period: 'lunch', schedule_template_id: null,
    ordering_starts_at: '2026-09-16T00:00:00Z', ordering_ends_at: '2026-09-16T12:00:00Z',
    pickup_starts_at: '2026-09-17T03:00:00Z', pickup_ends_at: '2026-09-17T05:30:00Z',
    offerings: [{ id: 'scheduled-offering', meal_id: 'scheduled-meal', meal_name: '隔離每日便當', description: '隔離驗收',
      image_url: null, price: 120, capacity: 50, reserved_quantity: 0, paid_quantity: 0, available_quantity: 50,
      position: 0, is_active: true, option_groups: [] }],
    ...overrides,
  };
}

async function scheduleFixture(t, { admin = false } = {}) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'America/Los_Angeles', serviceWorkers: 'block' });
  const dinner = scheduledEvent({ id: 'scheduled-dinner', title: '隔離每日晚餐', meal_period: 'dinner',
    pickup_starts_at: '2026-09-17T08:30:00Z', pickup_ends_at: '2026-09-17T10:30:00Z' });
  const state = { events: [scheduledEvent(), dinner], quotes: [], orders: [], schedules: [], writes: [], reads: [], unknownApi: [], blockedExternal: [], rejectEdit: false, adminMealReads: 0, adminEventReads: 0, adminPickupOrderReads: 0,
    submissionLookups: [], submissionResults: [], conflictOnCreate: false, rejectOrderOnce: false };
  const meal = { id: 'scheduled-meal', name: '隔離每日便當', description: '隔離驗收', slug: 'isolated', price: 120, is_active: true, tax_type: 'taxable', option_groups: [] };
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) { state.blockedExternal.push(url.origin); return route.abort(); }
    if (url.pathname === '/isolated-meal-payment') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<meta charset="utf-8"><h1>隔離付款頁</h1>' });
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    const path = url.pathname;
    const method = request.method();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-schedule-token', user: { id: 'schedule-user', email: 'schedule@example.test', display_name: '隔離使用者', user_role: admin ? 'admin' : 'customer' } } });
    if (method === 'GET' && path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (method === 'GET' && path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: 0, invoice_attention: 0, today_pickup: 0 } });
    if (method === 'GET' && path === '/v1/admin/meal-orders') return route.fulfill({ json: { items: [], total: 0, limit: 20, offset: 0 } });
    if (method === 'GET' && ['/v1/products', '/v1/group-campaigns', '/v1/orders', '/v1/meal-orders', '/v1/pickup-locations'].includes(path)) return route.fulfill({ json: [] });
    if (method === 'GET' && path === '/v1/meal-events/calendar') {
      state.reads.push(path);
      return route.fulfill({ json: mealCalendarFixture(state.events) });
    }
    if (method === 'GET' && path === '/v1/admin/meal-events') {
      state.adminEventReads++;
      return route.fulfill({ json: state.events });
    }
    if (method === 'GET' && path === '/v1/admin/meals') { state.adminMealReads++; return route.fulfill({ json: [meal] }); }
    if (method === 'PUT' && path === '/v1/admin/meal-events/scheduled-lunch') {
      const payload = request.postDataJSON();
      state.writes.push({ method, path, payload });
      if (state.rejectEdit) return route.fulfill({ status: 409, json: { detail: '此場次已有訂單，不能修改原場次' } });
      const { offerings, ...fields } = payload;
      state.events[0] = { ...state.events[0], ...fields, offerings: state.events[0].offerings.map((offering) => ({ ...offering, ...offerings.find((item) => item.meal_id === offering.meal_id) })) };
      return route.fulfill({ json: state.events[0] });
    }
    if (method === 'GET' && path === '/v1/admin/meal-calendar') return route.fulfill({ json: { service_date: '2026-09-17', is_workday: true, previous_workday: '2026-09-16', next_workday: '2026-09-18', ordering_starts_at: '2026-09-15T16:00:00Z', ordering_ends_at: '2026-09-16T12:00:00Z' } });
    if (method === 'GET' && path === '/v1/admin/meal-schedules/calendar-status') return route.fulfill({ json: { available: true, from_date: '2025-01-01', to_date: '2027-12-31', message: '', source_url: 'https://data.gov.tw/dataset/14718' } });
    if (method === 'GET' && path === '/v1/admin/meal-schedules') return route.fulfill({ json: state.schedules });
    if (method === 'POST' && path === '/v1/admin/meal-schedules') {
      const payload = request.postDataJSON();
      state.writes.push({ method, path, payload });
      const result = { ...payload, id: 'schedule-template', timezone: 'Asia/Taipei', created_at: '2026-09-17T01:00:00Z' };
      state.schedules.push(result);
      return route.fulfill({ json: result });
    }
    if (method === 'PUT' && path === '/v1/admin/meal-schedules/schedule-template') {
      const payload = request.postDataJSON();
      state.writes.push({ method, path, payload });
      state.schedules[0] = { ...state.schedules[0], ...payload };
      return route.fulfill({ json: state.schedules[0] });
    }
    if (method === 'POST' && path === '/v1/admin/meal-schedules/schedule-template/generate') {
      state.writes.push({ method, path });
      return route.fulfill({ json: { created_count: 0, created_event_ids: [] } });
    }
    if (method === 'GET' && path === '/v1/admin/meal-events/scheduled-lunch/orders') {
      state.adminPickupOrderReads++;
      return route.fulfill({ json: [paidOrder({ pickup_at: '2026-09-17T04:05:00Z' })] });
    }
    const event = state.events.find((candidate) => path === `/v1/meal-events/${candidate.id}` || path.startsWith(`/v1/meal-events/${candidate.id}/`));
    if (event && method === 'GET' && path === `/v1/meal-events/${event.id}`) {
      state.reads.push(path);
      return route.fulfill({ json: event });
    }
    if (event && method === 'POST' && path.endsWith('/quote')) {
      const payload = request.postDataJSON();
      state.quotes.push(payload);
      return route.fulfill({ json: { amount_total: 120, pickup_at: payload.pickup_at,
        pickup: { location: event.location, starts_at: event.pickup_starts_at, ends_at: event.pickup_ends_at },
        items: [{ offering_id: 'scheduled-offering', meal_name: meal.name, quantity: 1, base_price: 120, option_price: 0, unit_price: 120, subtotal: 120, selections: [] }] } });
    }
    if (event && method === 'GET' && path.includes('/order-submissions/')) {
      state.submissionLookups.push(path.split('/').at(-1));
      const result = state.submissionResults.shift();
      return result === 'old_api'
        ? route.fulfill({ status: 404, json: { detail: 'Not Found' } })
        : route.fulfill({ json: { order_id: result ?? null } });
    }
    if (event && method === 'POST' && path.endsWith('/orders')) {
      const payload = request.postDataJSON();
      state.orders.push(payload);
      if (state.conflictOnCreate) return route.fulfill({ status: 409, json: { detail: '此送單識別碼已使用，請重新確認訂單' } });
      if (state.rejectOrderOnce) {
        state.rejectOrderOnce = false;
        return route.fulfill({ status: 503, json: { detail: '隔離測試暫未收到回應' } });
      }
      return route.fulfill({ json: { id: orderId, payment_method: payload.payment_method } });
    }
    if (method === 'POST' && path === paymentPath) return route.fulfill({ json: { payment_url: `${base}/isolated-meal-payment` } });
    state.unknownApi.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.clock.install({ time: new Date('2026-09-16T01:00:00Z') });
  return { page, state };
}

async function captureMealScreenshot(page, name) {
  if (!process.env.BROWSER_SCREENSHOT_DIR) return;
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); });
  await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/${name}`, fullPage: true });
}

test('便當按已公告供餐日選擇，舊規則僅明日場次提供九個固定時段', { timeout: 25000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  state.events.push(scheduledEvent({ id: 'scheduled-today', service_date: '2026-09-16',
    pickup_starts_at: '2026-09-16T03:00:00Z', pickup_ends_at: '2026-09-16T05:30:00Z' }),
  scheduledEvent({ id: 'scheduled-day-after', service_date: '2026-09-18',
    ordering_ends_at: '2026-09-17T12:00:00Z', pickup_starts_at: '2026-09-18T03:00:00Z', pickup_ends_at: '2026-09-18T05:30:00Z' }));
  await page.goto(`${base}/shop`);
  await page.getByRole('link', { name: '隔離取餐區 11:00 取餐', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '9/17 週四，可預訂', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByRole('group', { name: '餐別', exact: true }).count(), 0);
  const dateChoices = page.getByRole('group', { name: '取餐日期', exact: true });
  assert.equal(await dateChoices.getByRole('button', { name: /9\/16/ }).count(), 0);
  assert.equal(await dateChoices.getByRole('button', { name: '9/18 週五，尚未開放', exact: true }).count(), 1);
  const slots = page.locator('.meal-time-selection .meal-pickup-options a');
  assert.deepEqual(await slots.allTextContents(), ['11:00', '11:30', '12:00', '12:30', '13:00', '16:30', '17:00', '17:30', '18:00']);
  const hrefs = await slots.evaluateAll((links) => links.map((link) => link.getAttribute('href')));
  assert.ok(hrefs.every((href) => /\/meals\/scheduled-(lunch|dinner)\?/.test(href) && new URL(href, base).searchParams.get('pickup_at').startsWith('2026-09-17T')));
  assert.ok(state.reads.length > 0 && state.reads.every(path => path === '/v1/meal-events/calendar'));
  await captureMealScreenshot(page, 'meal-tomorrow-fixed-slots-mobile.png');
  state.events = [];
  await page.reload();
  await page.getByText('目前沒有已公告的供餐日期', { exact: true }).waitFor();
  assert.equal(await page.locator('.meal-pickup-options a').count(), 0);
  assert.deepEqual(state.orders, []);
  assert.deepEqual(state.quotes, []);
  assert.deepEqual(state.unknownApi, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
});

test('晚餐僅准16:30至18:00半小時時段，非法跨午夜時間不報價，合法時段送入原報價與下單', { timeout: 25000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  await page.goto(`${base}/meals/scheduled-dinner?pickup_at=2026-09-18T00%3A10`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  const pay = page.getByRole('button', { name: '預訂並前往線上付款', exact: true });
  const time = page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true });
  assert.equal(await pay.isDisabled(), true);
  assert.equal(await time.inputValue(), '');
  assert.deepEqual(await time.locator('option:not([value=""])').evaluateAll((options) => options.map((option) => option.value)),
    ['2026-09-17T16:30', '2026-09-17T17:00', '2026-09-17T17:30', '2026-09-17T18:00']);
  for (const invalid of ['2026-09-18T00:10', '2026-09-17T16:45', '2026-09-17T18:30']) {
    assert.equal(await time.locator(`option[value="${invalid}"]`).count(), 0);
  }
  assert.equal(state.quotes.length, 0);
  assert.equal(state.orders.length, 0);
  await Promise.all([page.waitForResponse((response) => response.url().endsWith('/quote')), time.selectOption('2026-09-17T18:00')]);
  await captureMealScreenshot(page, 'meal-pickup-time-mobile.png');
  await pay.click();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  assert.equal(state.quotes.at(-1).pickup_at, '2026-09-17T10:00:00.000Z');
  assert.equal(state.orders.length, 1);
  assert.equal(state.orders[0].pickup_at, '2026-09-17T10:00:00.000Z');
  assert.deepEqual(state.unknownApi, []);
  assert.ok(state.blockedExternal.every((origin) => origin === 'https://fonts.googleapis.com'));
});

test('手機從日期摘要選時段後才讀完整菜單，原時段帶入一般便當報價、建單與付款', { timeout: 25000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  await page.goto(`${base}/shop`);
  const slot = page.getByRole('link', { name: '隔離取餐區 18:00 取餐', exact: true });
  await slot.waitFor();
  assert.ok(state.reads.length > 0 && state.reads.every(path => path === '/v1/meal-events/calendar'));
  await slot.click();
  const meal = page.getByRole('button', { name: '增加隔離每日便當', exact: true });
  await meal.waitFor();
  assert.ok(state.reads.includes('/v1/meal-events/scheduled-dinner'));
  assert.equal(await page.getByRole('combobox', { name: '開票時機', exact: true }).count(), 0);
  const quote = page.waitForResponse(response => response.url().endsWith('/quote'));
  await meal.click();
  await quote;
  await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  assert.equal(state.quotes.at(-1).pickup_at, '2026-09-17T10:00:00.000Z');
  assert.equal(state.orders.length, 1);
  assert.equal(state.orders[0].pickup_at, '2026-09-17T10:00:00.000Z');
  assert.equal(Object.hasOwn(state.quotes.at(-1), 'invoice_handling'), false);
  assert.equal(Object.hasOwn(state.orders[0], 'meal_booking_type'), false);
  assert.equal(Object.hasOwn(state.orders[0], 'invoice_handling'), false);
  assert.equal(state.orders[0].payment_method, 'online');
  assert.deepEqual(state.unknownApi, []);
});

function legacyMealSubmissionKey(paymentMethod, invoiceHandling, invoicePreference = {
  buyerType: 'personal', buyerEmail: 'schedule@example.test', carrierType: 'cloud',
}) {
  const oldFingerprint = JSON.stringify({
    userId: 'schedule-user', eventId: 'scheduled-lunch', couponCode: '', expectedTotal: 120,
    pickupAt: '2026-09-17T03:00:00.000Z', pickupLocationId: '', needsUtensils: false,
    items: [{ offering_id: 'scheduled-offering', quantity: 1, option_ids: [] }],
    contactEmail: 'schedule@example.test', invoicePreference, paymentMethod, kitchenNote: '', invoiceHandling,
  });
  return `meal-submission:${createHash('sha256').update(oldFingerprint).digest('hex')}`;
}

for (const { name, results, submit, recover, method = 'online', handling = 'pickup_day' } of [
  { name: '找回已成立的原單', results: [orderId], submit: false, recover: true },
  { name: '未成立時沿用原識別，以新版預設送單', results: [null], submit: true, recover: false },
  { name: '舊請求先完成時找回原單', results: [null, orderId], submit: true, recover: true },
  { name: '舊 API 暫時無查詢入口時開啟訂單中心', results: ['old_api'], submit: false, recover: false },
  { name: '現場付款的取餐日開票找回原單', results: [orderId], submit: false, recover: true, method: 'cash_on_pickup' },
  { name: '現場付款的收款後開票找回原單', results: [orderId], submit: false, recover: true, method: 'cash_on_pickup', handling: 'on_payment' },
]) {
  test(`一般便當更新後重試舊開票請求，${name}`, { timeout: 25000 }, async (t) => {
    const { page, state } = await scheduleFixture(t);
    const cash = method === 'cash_on_pickup';
    state.events[0].allow_cash_payment = cash;
    state.submissionResults = [...results];
    state.conflictOnCreate = submit && recover;
    const requestId = '680456c6-b8af-4b63-83b0-1a3b5f86da43';
    const key = legacyMealSubmissionKey(method, handling);
    await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
    await page.evaluate(([key, id]) => sessionStorage.setItem(key, id), [key, requestId]);
    await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
    if (cash) await page.getByRole('radio', { name: '合作社現場付款', exact: true }).check();
    await page.getByRole('button', { name: cash ? '確認預訂・現場付款' : '預訂並前往線上付款', exact: true }).click();
    if (submit && !recover) {
      await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
    } else {
      await page.waitForURL(recover ? `**/meal-orders?order_id=${orderId}` : '**/meal-orders');
    }
    assert.deepEqual(state.submissionLookups, results.map(() => requestId));
    assert.equal(state.orders.length, Number(submit));
    if (submit) {
      assert.equal(state.orders[0].client_request_id, requestId);
      assert.equal(Object.hasOwn(state.orders[0], 'invoice_handling'), false);
    }
    assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), results[0] === 'old_api' ? requestId : null);
  });
}

for (const { handling, defaultOnline } of [
  { handling: 'pickup_day', defaultOnline: false },
  { handling: 'on_payment', defaultOnline: false },
  { handling: 'pickup_day', defaultOnline: true },
]) {
  test(`舊公司發票現金單待確認時先查看訂單（${handling}${defaultOnline ? '，新版預設線上付款' : ''}）`, { timeout: 25000 }, async (t) => {
    const { page, state } = await scheduleFixture(t);
    state.events[0].allow_cash_payment = true;
    const requestId = '680456c6-b8af-4b63-83b0-1a3b5f86da43';
    const key = legacyMealSubmissionKey('cash_on_pickup', handling, {
      buyerType: 'company', buyerTaxId: '12345675', buyerName: '隔離測試公司',
      buyerEmail: 'schedule@example.test', carrierType: 'cloud',
    });
    await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
    await page.evaluate(([key, id]) => sessionStorage.setItem(key, id), [key, requestId]);
    await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
    if (!defaultOnline) await page.getByRole('radio', { name: '合作社現場付款', exact: true }).check();
    await page.getByRole('button', { name: defaultOnline ? '預訂並前往線上付款' : '確認預訂・現場付款', exact: true }).click();
    await page.getByText('先前送出的便當訂單可能已成立。', { exact: false }).waitFor();
    assert.equal(await page.getByRole('link', { name: '查看便當訂單', exact: true }).getAttribute('href'), '/meal-orders');
    assert.equal(new URL(page.url()).pathname, '/meals/scheduled-lunch');
    assert.deepEqual(state.orders, []);
    assert.deepEqual(state.submissionLookups, []);
    assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), requestId);

    if (handling === 'on_payment') {
      const separate = page.getByRole('button', { name: '另下一筆', exact: true });
      await page.getByLabel(/餐點備註/).fill('隔離新訂單');
      await separate.click();
      await page.getByText('請再確認是否另下一筆。', { exact: false }).waitFor();
      assert.deepEqual(state.orders, []);
      await separate.click();
      await page.waitForURL(`**/meal-orders?order_id=${orderId}`);
      assert.equal(state.orders.length, 1);
      assert.notEqual(state.orders[0].client_request_id, requestId);
      assert.equal(state.orders[0].kitchen_note, '隔離新訂單');
      assert.equal(Object.hasOwn(state.orders[0], 'invoice_handling'), false);
      assert.equal(Object.hasOwn(state.orders[0], 'invoice_buyer_tax_id'), false);
      assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), requestId);
    }
  });
}

test('首次現金便當照常送出，回應失敗後重用新版識別並清除成功紀錄', { timeout: 25000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  state.events[0].allow_cash_payment = true;
  state.rejectOrderOnce = true;
  await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  await page.getByRole('radio', { name: '合作社現場付款', exact: true }).check();
  const submit = page.getByRole('button', { name: '確認預訂・現場付款', exact: true });
  await submit.click({ trial: true });
  await submit.evaluate(button => { button.click(); button.click(); });
  await page.getByText('隔離測試暫未收到回應', { exact: true }).waitFor();
  assert.equal(state.orders.length, 1);
  const id = state.orders[0].client_request_id;
  const key = legacyMealSubmissionKey('cash_on_pickup');
  const marker = key.replace('meal-submission:', 'meal-submission-current:');
  assert.deepEqual(await page.evaluate(keys => keys.map(item => sessionStorage.getItem(item)), [key, marker]), [id, id]);
  await submit.click();
  await page.waitForURL(`**/meal-orders?order_id=${orderId}`);
  assert.deepEqual(state.orders.map(order => order.client_request_id), [id, id]);
  assert.deepEqual(state.submissionLookups, []);
  assert.deepEqual(await page.evaluate(keys => keys.map(item => sessionStorage.getItem(item)), [key, marker]), [null, null]);
});

test('便當訂單顯示實際選定的取餐時間，舊單不捏造已選時間', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t, paidOrder({ pickup_at: '2026-09-14T04:05:00Z' }));
  const pickup = page.locator('.meal-pickup-info');
  assert.match(await pickup.innerText(), /預計取餐時間.*9\/14.*12:05/s);
  state.order = paidOrder();
  await page.reload();
  await page.getByText('舊訂單未指定時間，請於供餐時段內取餐', { exact: true }).waitFor();
  assert.deepEqual(state.unknownApi, []);
});

test('每日午晚餐排程預設週一至五且停用，須明確啟用及自動發布才開售', { timeout: 30000 }, async (t) => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals`);
  await page.getByRole('tab', { name: '每日午晚餐排程', exact: true }).click();
  await page.getByRole('heading', { name: '建立每日午晚餐排程', exact: true }).waitFor();
  const enable = page.getByLabel('啟用每日自動產生場次', { exact: true });
  const publish = page.getByLabel('新產生場次自動發布並開放預訂（未勾選僅建立草稿）', { exact: true });
  assert.equal(await enable.isChecked(), false);
  assert.equal(await publish.isChecked(), false);
  for (const day of ['週一', '週二', '週三', '週四', '週五']) assert.equal(await page.getByLabel(day, { exact: true }).isChecked(), true);
  assert.equal(await page.getByLabel('週六', { exact: true }).count(), 0);
  assert.equal(await page.getByLabel('週日', { exact: true }).count(), 0);
  await page.getByLabel('排程／場次名稱', { exact: true }).fill('隔離每日晚餐排程');
  await page.getByLabel('取餐地點', { exact: true }).fill('隔離取餐區');
  await page.getByLabel('排程餐別', { exact: true }).selectOption('dinner');
  await page.getByLabel('每日取餐開始', { exact: true }).fill('16:30');
  await page.getByLabel('每日取餐結束', { exact: true }).fill('18:00');
  await page.getByLabel('每日截單時間', { exact: true }).fill('20:00');
  assert.equal(await page.getByLabel('取餐前幾天截單', { exact: true }).inputValue(), '1');
  await page.getByRole('checkbox', { name: '隔離每日便當 $120', exact: true }).check();
  await captureMealScreenshot(page, 'meal-weekday-schedule-mobile.png');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await page.getByRole('button', { name: '儲存每日排程', exact: true }).click();
  await page.getByRole('heading', { name: '隔離每日晚餐排程', exact: true }).waitFor();
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].payload.enabled, false);
  assert.equal(state.writes[0].payload.auto_publish, false);
  assert.deepEqual(state.writes[0].payload.weekdays, [0, 1, 2, 3, 4]);
  assert.equal(await page.getByRole('button', { name: '立即補齊未來場次', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '編輯排程', exact: true }).click();
  await enable.check();
  await publish.check();
  await page.getByRole('button', { name: '儲存每日排程', exact: true }).click();
  await page.getByRole('button', { name: '立即補齊未來場次', exact: true }).click();
  await page.getByText('已完成排程補齊，既有日期不會重複建立。', { exact: true }).waitFor();
  assert.equal(state.writes[1].method, 'PUT');
  assert.equal(state.writes[1].payload.enabled, true);
  assert.equal(state.writes[1].payload.auto_publish, true);
  assert.equal(state.writes[1].payload.pickup_start_time, '16:30');
  assert.equal(state.writes[1].payload.pickup_end_time, '18:00');
  assert.equal(state.writes[1].payload.cutoff_days_before, 1);
  assert.equal(state.writes[1].payload.cutoff_time, '20:00');
  assert.match(state.writes[2].path, /\/generate$/);
  assert.deepEqual(state.unknownApi, []);
  assert.ok(state.blockedExternal.every((origin) => origin === 'https://fonts.googleapis.com'));
});

test('便當管理者可依場次查看所選取餐時間，僅展開時查詢訂單', { timeout: 20000 }, async (t) => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals`);
  await page.getByRole('tab', { name: '場次與核銷', exact: true }).click();
  const event = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await event.getByRole('heading', { name: '隔離每日午餐', exact: true }).waitFor();
  assert.equal(state.adminPickupOrderReads, 0);
  await event.getByRole('button', { name: '查看本場訂單與取餐時間', exact: true }).click();
  const orders = event.getByRole('region', { name: '本場訂單與取餐時間', exact: true });
  await orders.getByRole('heading', { name: orderNumber, exact: true }).waitFor();
  assert.match(await orders.innerText(), /預計取餐：.*9\/17.*12:05/);
  assert.equal(state.adminPickupOrderReads, 1);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknownApi, []);
});

test('便當後台每30秒更新場次，開放取餐時自動出現核銷且保留未儲存編輯', { timeout: 20000 }, async (t) => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals`);
  await page.getByRole('tab', { name: '場次與核銷', exact: true }).click();
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await card.getByRole('button', { name: '手動編輯場次', exact: true }).click();
  await card.getByLabel('場次名稱', { exact: true }).fill('尚未儲存的午餐修改');
  await card.getByLabel('取餐地點', { exact: true }).fill('尚未儲存的取餐地點');
  assert.equal(await card.getByRole('button', { name: '確認核銷', exact: true }).count(), 0);
  const mealReads = state.adminMealReads;
  const eventReads = state.adminEventReads;
  state.events[0] = { ...state.events[0], status: 'pickup_open', can_edit: false };
  await page.clock.fastForward(30000);
  await card.getByRole('button', { name: '確認核銷', exact: true }).waitFor();
  assert.equal(await card.getByLabel('場次名稱', { exact: true }).inputValue(), '尚未儲存的午餐修改');
  assert.equal(await card.getByLabel('取餐地點', { exact: true }).inputValue(), '尚未儲存的取餐地點');
  assert.ok(state.adminEventReads > eventReads);
  assert.equal(state.adminMealReads, mealReads);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknownApi, []);
});

test('便當跨台灣午夜自動改顯示新明天，瀏覽器時區不同也不保留舊日期時段', { timeout: 15000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  state.events.push(scheduledEvent({ id: 'scheduled-next-day', service_date: '2026-09-18',
    ordering_starts_at: '2026-09-17T00:00:00Z', ordering_ends_at: '2026-09-17T12:00:00Z',
    pickup_starts_at: '2026-09-18T03:00:00Z', pickup_ends_at: '2026-09-18T05:30:00Z' }));
  await page.goto(`${base}/shop`);
  await page.getByRole('link', { name: '隔離取餐區 11:00 取餐', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '9/17 週四，可預訂', exact: true }).getAttribute('aria-pressed'), 'true');
  await page.clock.fastForward(24 * 60 * 60 * 1000);
  await page.getByRole('button', { name: '9/18 週五，可預訂', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: /9\/17/ }).count(), 0);
  const slots = page.locator('.meal-pickup-options a');
  await page.getByRole('link', { name: '隔離取餐區 11:00 取餐', exact: true }).waitFor();
  assert.deepEqual(await slots.allTextContents(), ['11:00', '11:30', '12:00', '12:30', '13:00']);
  assert.ok((await slots.evaluateAll((links) => links.map((link) => link.getAttribute('href'))))
    .every((href) => href.includes('/meals/scheduled-next-day?') && new URL(href, base).searchParams.get('pickup_at').startsWith('2026-09-18T')));
  assert.deepEqual(state.orders, []);
  assert.deepEqual(state.quotes, []);
  assert.deepEqual(state.unknownApi, []);
});

test('便當保留手動更新，409保存輸入且後端can_edit為false時不允許修改', { timeout: 25000 }, async (t) => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals`);
  await page.getByRole('tab', { name: '場次與核銷', exact: true }).click();
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await card.getByRole('button', { name: '手動編輯場次', exact: true }).click();
  await card.getByLabel('場次名稱', { exact: true }).fill('手動更新午餐');
  await card.getByLabel('取餐開始（台灣時間）', { exact: true }).fill('2026-09-17T12:00');
  await card.getByLabel('取餐結束（台灣時間）', { exact: true }).fill('2026-09-17T13:00');
  state.rejectEdit = true;
  await card.getByRole('button', { name: '儲存場次修改', exact: true }).click();
  await card.getByText('此場次已有訂單，不能修改原場次', { exact: true }).waitFor();
  assert.equal(await card.getByLabel('場次名稱', { exact: true }).inputValue(), '手動更新午餐');
  state.rejectEdit = false;
  await card.getByRole('button', { name: '儲存場次修改', exact: true }).click();
  await page.getByRole('heading', { name: '手動更新午餐', exact: true }).waitFor();
  assert.equal(state.writes.length, 2);
  assert.equal(state.writes[1].payload.pickup_starts_at, '2026-09-17T04:00:00.000Z');
  assert.equal(state.writes[1].payload.pickup_ends_at, '2026-09-17T05:00:00.000Z');
  state.events[0].can_edit = false;
  await page.reload();
  const updated = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '手動更新午餐', exact: true }) });
  await updated.getByRole('heading', { name: '手動更新午餐', exact: true }).waitFor();
  assert.equal(await updated.getByRole('button', { name: '手動編輯場次', exact: true }).isDisabled(), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.unknownApi, []);
});

test('今天即使已開放取餐且原場次尚未截止，仍不得新報價或下單', { timeout: 20000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  state.events[0] = scheduledEvent({ service_date: '2026-09-16', status: 'pickup_open', ordering_ends_at: '2026-09-16T05:00:00Z',
    pickup_starts_at: '2026-09-16T03:00:00Z', pickup_ends_at: '2026-09-16T05:30:00Z' });
  await page.clock.fastForward(3 * 60 * 60 * 1000);
  await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-16T12%3A30`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  const time = page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true });
  await page.getByText('本場次目前不在預訂開放時間內。', { exact: true }).waitFor();
  assert.equal(await time.isDisabled(), true);
  assert.equal(await time.locator('option:not([value=""])').count(), 0);
  assert.equal(await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).isDisabled(), true);
  assert.equal(state.quotes.length, 0);
  assert.equal(state.orders.length, 0);
  assert.deepEqual(state.unknownApi, []);
});

test('後端暫停舊假日場次的新訂單，直接連結也不報價或送單', { timeout: 20000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  state.events[0] = scheduledEvent({ new_orders_blocked_reason: '本場次不開放新的預訂，已成立訂單請至訂單中心查看。' });
  await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T12%3A30`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  await page.getByText(state.events[0].new_orders_blocked_reason, { exact: true }).waitFor();
  assert.equal(await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).isDisabled(), true);
  assert.equal(state.quotes.length, 0);
  assert.equal(state.orders.length, 0);
  assert.deepEqual(state.unknownApi, []);
});

test('明日便當在台灣20點準時截單，已選合法時段仍不得再送單', { timeout: 20000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  await page.clock.fastForward((10 * 60 + 59) * 60 * 1000);
  await page.goto(`${base}/meals/scheduled-lunch`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  const time = page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true });
  const pay = page.getByRole('button', { name: '預訂並前往線上付款', exact: true });
  await Promise.all([page.waitForResponse((response) => response.url().endsWith('/quote')), time.selectOption('2026-09-17T12:30')]);
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent === '預訂並前往線上付款' && !button.disabled));
  const quotesBeforeCutoff = state.quotes.length;
  await page.clock.fastForward(60 * 1000);
  await page.getByText('本場次目前不在預訂開放時間內。', { exact: true }).waitFor();
  assert.equal(await pay.isDisabled(), true);
  assert.equal(await time.locator('option[value="2026-09-17T12:30"]').count(), 1);
  assert.equal(state.quotes.length, quotesBeforeCutoff);
  assert.equal(state.orders.length, 0);
  assert.deepEqual(state.unknownApi, []);
});

test('循環排程預設固定午晚餐與前一天20點截單，不覆寫自訂或既有模板時段', { timeout: 30000 }, async (t) => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals`);
  await page.getByRole('tab', { name: '每日午晚餐排程', exact: true }).click();
  const start = page.getByLabel('每日取餐開始', { exact: true });
  const end = page.getByLabel('每日取餐結束', { exact: true });
  const cutoff = page.getByLabel('每日截單時間', { exact: true });
  const period = page.getByLabel('排程餐別', { exact: true });
  const times = async () => Promise.all([start.inputValue(), end.inputValue(), cutoff.inputValue()]);
  assert.deepEqual(await times(), ['11:00', '13:00', '20:00']);
  assert.equal(await page.getByLabel('取餐前幾天截單', { exact: true }).inputValue(), '1');
  await period.selectOption('dinner');
  assert.deepEqual(await times(), ['16:30', '18:00', '20:00']);
  await start.fill('17:00');
  await period.selectOption('lunch');
  assert.deepEqual(await times(), ['17:00', '18:00', '20:00']);
  await page.getByRole('button', { name: '套用午餐預設時段', exact: true }).click();
  assert.deepEqual(await times(), ['11:00', '13:00', '20:00']);
  await page.getByLabel('排程／場次名稱', { exact: true }).fill('隔離確認午餐排程');
  await page.getByLabel('取餐地點', { exact: true }).fill('隔離取餐區');
  await page.getByRole('checkbox', { name: '隔離每日便當 $120', exact: true }).check();
  const save = page.getByRole('button', { name: '儲存每日排程', exact: true });
  assert.equal(await save.isDisabled(), false);
  await end.fill('11:00');
  await page.getByText('開始與結束時間不可相同；預訂最晚於取餐前一天 20:00 截止。', { exact: true }).waitFor();
  assert.equal(await save.isDisabled(), true);
  await end.fill('13:00');
  await captureMealScreenshot(page, 'meal-confirmed-lunch-schedule-mobile.png');
  await save.click();
  await page.getByRole('heading', { name: '隔離確認午餐排程', exact: true }).waitFor();
  assert.equal(state.writes[0].payload.pickup_start_time, '11:00');
  assert.equal(state.writes[0].payload.pickup_end_time, '13:00');
  assert.equal(state.writes[0].payload.cutoff_time, '20:00');
  assert.equal(state.writes[0].payload.cutoff_days_before, 1);
  await page.getByRole('button', { name: '編輯排程', exact: true }).click();
  await period.selectOption('dinner');
  assert.deepEqual(await times(), ['11:00', '13:00', '20:00']);
  await page.getByRole('button', { name: '套用晚餐預設時段', exact: true }).click();
  assert.deepEqual(await times(), ['16:30', '18:00', '20:00']);
  await save.click();
  await page.getByRole('heading', { name: '隔離確認午餐排程', exact: true }).waitFor();
  assert.equal(state.writes[1].payload.meal_period, 'dinner');
  assert.equal(state.writes[1].payload.pickup_start_time, '16:30');
  assert.equal(state.writes[1].payload.pickup_end_time, '18:00');
  assert.equal(state.writes[1].payload.cutoff_time, '20:00');
  assert.equal(state.writes[1].payload.cutoff_days_before, 1);
  assert.deepEqual(state.unknownApi, []);
});
