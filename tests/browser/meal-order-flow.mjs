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
        body: `<!doctype html><meta charset="utf-8"><h1>隔離付款頁</h1><a href="/orders?order_id=${orderId}&payment=paid">返回餐點訂單</a>`,
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
  await page.clock.install({ time: new Date("2026-09-14T02:00:00Z") });
  await page.goto(`${base}/meal-orders`);
  await page.locator('.order-detail-head .order-number').filter({ hasText: orderNumber }).waitFor();
  const qr = page.getByLabel(`訂單 ${orderNumber} 取餐 QR Code`, { exact: true });
  return { page, state, qr };
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

test('便當待付款經隔離付款頁回來，保留已開立發票且不查詢取餐碼或 QR', { timeout: 30000 }, async (t) => {
  const { page, state, qr } = await fixture(t, mealOrder());
  await page.getByRole('button', { name: '前往付款 $120', exact: true }).waitFor();
  assert.equal(state.credentialReads, 0);
  await noCredential(page, qr);
  await verifyIsolation(page, state);
  await page.getByRole('button', { name: '前往付款 $120', exact: true }).click();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  state.order = paidOrder();
  await page.getByRole('link', { name: '返回餐點訂單', exact: true }).click();
  await page.waitForURL(`${base}/meal-orders?order_id=${orderId}`);
  await noCredential(page, qr);
  const invoice = page.getByRole('region', { name: '電子發票', exact: true });
  await invoice.getByText('已開立', { exact: true }).waitFor();
  assert.match(await invoice.innerText(), /AB12345678/);
  assert.match(await invoice.innerText(), /9\/14.*11:00/);
  await captureMealScreenshot(page, 'meal-paid-without-code-mobile.png');
  assert.deepEqual(state.mutations, [{ path: paymentPath }]);
  await verifyIsolation(page, state);
});

for (const [invoiceStatus, label] of [['pending', '開立處理中'], ['failed', '開立待處理']]) {
  test(`付款成功但發票 ${invoiceStatus}，仍可取餐且不要求重新付款`, { timeout: 20000 }, async (t) => {
    const { page, state, qr } = await fixture(t, paidOrder({ invoice_status: invoiceStatus, invoice_number: null, invoice_date: null }));
    await noCredential(page, qr);
    const invoice = page.getByRole('region', { name: '電子發票', exact: true });
    await invoice.getByText(label, { exact: true }).waitFor();
    assert.match(await invoice.innerText(), /不影響已付款訂單的取餐資格；請勿為了發票重新付款/);
    assert.equal(await page.getByRole('button', { name: /^前往付款/ }).count(), 0);
    assert.deepEqual(state.mutations, []);
    await verifyIsolation(page, state);
  });
}

test('取餐截止後顯示取餐時段已結束，沒有取餐碼、核銷文字或自助退單', { timeout: 20000 }, async (t) => {
  const { page, state, qr } = await fixture(t, paidOrder({ fulfillment_status: 'no_show' }));
  await page.clock.fastForward(4 * 60 * 60 * 1000);
  await page.getByText('取餐時段已結束', { exact: true }).waitFor();
  await noCredential(page, qr);
  assert.doesNotMatch(await page.locator('.meal-orders-page').innerText(), /逾時未取|核銷|可領取|取餐碼/);
  assert.equal(await page.getByText('申請退單', { exact: true }).count(), 0);
  assert.equal(state.credentialReads, 0);
  await verifyIsolation(page, state);
});

test('會餐訂單顯示統編和抬頭，並保留付款失敗原因', { timeout: 20000 }, async (t) => {
  const { page, state, qr } = await fixture(t, mealOrder({
    meal_booking_type: 'business', invoice_buyer_tax_id: '24536806', invoice_buyer_name: '隔離測試公司',
    payment_opens_at: '2026-09-13T16:00:00Z', payment_closes_at: '2026-09-14T16:00:00Z',
  }));
  await page.evaluate(({ id }) => sessionStorage.setItem(`slf_meal_payment_issue:buyer-isolated:${id}`, '隔離金流暫時無法建立付款'), { id: orderId });
  await page.reload();
  await page.getByText('統編：24536806', { exact: true }).waitFor();
  await page.getByText('抬頭：隔離測試公司', { exact: true }).waitFor();
  await page.getByRole('alert').filter({ hasText: '無法開啟付款頁：隔離金流暫時無法建立付款' }).waitFor();
  await noCredential(page, qr);
  assert.equal(state.credentialReads, 0);
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
  await noCredential(page, qr);
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

async function scheduleFixture(t, { admin = false, width = 390 } = {}) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 844 }, timezoneId: 'America/Los_Angeles', serviceWorkers: 'block' });
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
    if (method === 'GET' && path === '/v1/meal-checkout/contact') return route.fulfill({ json: { phone: state.phone === undefined ? '0912345678' : state.phone } });
    if (method === 'GET' && path === '/v1/meal-announcements') return route.fulfill({ json: state.announcements || [] });
    if (method === 'GET' && path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (method === 'GET' && path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: 0, invoice_attention: 0, today_pickup: 0 } });
    if (method === 'GET' && path === '/v1/admin/meal-orders') {
      const items = state.adminOrders || [];
      return route.fulfill({ json: { items, total: items.length, limit: 20, offset: 0 } });
    }
    if (method === 'GET' && path === '/v1/meal-orders') return route.fulfill({ json: state.savedOrder ? [state.savedOrder] : [] });
    if (method === 'GET' && path === `/v1/meal-orders/${orderId}` && state.savedOrder) return route.fulfill({ json: state.savedOrder });
    if (method === 'GET' && ['/v1/products', '/v1/group-campaigns', '/v1/orders', '/v1/pickup-locations'].includes(path)) return route.fulfill({ json: [] });
    if (method === 'GET' && path === '/v1/meal-events/calendar') {
      state.reads.push(path);
      return route.fulfill({ json: mealCalendarFixture(state.events.filter(event => !['draft', 'cancelled', 'completed'].includes(event.status))) });
    }
    if (method === 'POST' && path === '/v1/admin/meal-disaster/preview') {
      const payload = request.postDataJSON();
      state.writes.push({ path, payload });
      const events = state.events.filter(event => event.service_date === payload.service_date && payload.periods.includes(event.meal_period)
        && !['cancelled', 'completed'].includes(event.status));
      return route.fulfill({ json: { preview_token: 'a'.repeat(64), events: events.map(event => ({
        id: event.id, title: event.title, period: event.meal_period, order_count: 3, paid_count: 1, paid_total: 120, blocked: false,
        ordering_paused: event.ordering_paused ?? false, ordering_pause_version: event.ordering_pause_version ?? 0,
        ordering_pause_blocked_reason: event.ordering_pause_blocked_reason ?? null,
      })) } });
    }
    if (method === 'POST' && path === '/v1/admin/meal-disaster/cancel') {
      const payload = request.postDataJSON();
      state.writes.push({ path, payload });
      const affected = state.events.filter(event => event.service_date === payload.service_date && payload.periods.includes(event.meal_period));
      for (const event of affected) Object.assign(event, { status: 'cancelled', can_edit: false });
      state.announcements = [{ service_date: payload.service_date, periods: payload.periods, message: payload.announcement,
        cancelled_event_ids: affected.map(event => event.id) }];
      return route.fulfill({ json: { cancelled_event_count: 2, order_count: 6, paid_count: 2, paid_total: 240 } });
    }
    if (method === 'PATCH' && path.endsWith('/ordering-pause')) {
      const payload = request.postDataJSON();
      state.writes.push({ path, payload });
      const event = state.events.find(item => path.includes(item.id));
      const version = event.ordering_pause_version ?? 0;
      if (payload.expected_version !== version) {
        return route.fulfill({ status: 409, json: { detail: '接單狀態已更新，請重新整理後再試' } });
      }
      Object.assign(event, { ordering_paused: payload.paused, ordering_pause_version: version + 1 });
      return route.fulfill({ json: event });
    }
    if (method === 'POST' && path === '/v1/admin/meal-events/scheduled-lunch/cancel') {
      state.writes.push({ path, payload: request.postDataJSON() });
      const event = state.events.find(item => item.id === 'scheduled-lunch');
      Object.assign(event, { status: 'cancelled', can_edit: false });
      return route.fulfill({ json: event });
    }
    if (method === 'GET' && path === '/v1/admin/meal-events') {
      state.adminEventReads++;
      return route.fulfill({ json: state.events });
    }
    if (method === 'POST' && path === '/v1/admin/meal-events') {
      const payload = request.postDataJSON();
      state.writes.push({ method, path, payload });
      const source = state.events.find(event => event.id === payload.replaces_event_id && event.status === 'cancelled');
      if (!source) return route.fulfill({ status: 409, json: { detail: '原場次未取消' } });
      const { offerings, ...fields } = payload;
      const draft = { ...source, ...fields, id: 'replacement-lunch', status: 'draft', can_edit: true,
        schedule_template_id: null, offerings: source.offerings.filter(offering => offerings.some(item => item.meal_id === offering.meal_id)) };
      state.events.push(draft);
      return route.fulfill({ status: 201, json: draft });
    }
    if (method === 'POST' && path === '/v1/admin/meal-events/replacement-lunch/publish') {
      state.writes.push({ method, path, payload: request.postDataJSON() });
      const draft = state.events.find(event => event.id === 'replacement-lunch');
      draft.status = 'published';
      return route.fulfill({ json: draft });
    }
    if (method === 'PATCH' && path === '/v1/admin/meal-events/archivable-meal/archive') {
      const payload = request.postDataJSON();
      state.writes.push({ path, payload });
      const event = state.events.find(item => item.id === 'archivable-meal');
      event.archived_at = payload.archived ? '2026-09-16T01:00:00Z' : null;
      return route.fulfill({ json: event });
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
    if (method === 'GET' && path === '/v1/admin/meal-schedules/calendar-status') return route.fulfill({ json: { available: true, from_date: '2025-01-01', to_date: '2027-12-31', message: '', source_url: 'https://data.gov.tw/dataset/14718', update_year: state.calendarUpdateYear } });
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
      return route.fulfill({ json: [paidOrder({ pickup_at: '2026-09-17T04:05:00Z', customer_name: '隔離買家', customer_email: 'buyer@example.test', contact_phone: '0912345678' })] });
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
    if (method === 'POST' && path === paymentPath) {
      if (state.paymentError) return route.fulfill({ status: 503, json: { detail: state.paymentError } });
      return route.fulfill({ json: { payment_url: `${base}/isolated-meal-payment` } });
    }
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

for (const { name, results, submit, recover } of [
  { name: '找回已成立的原單', results: [orderId], submit: false, recover: true },
  { name: '未成立時沿用原識別，以新版預設送單', results: [null], submit: true, recover: false },
  { name: '舊請求先完成時找回原單', results: [null, orderId], submit: true, recover: true },
  { name: '舊 API 暫時無查詢入口時開啟訂單中心', results: ['old_api'], submit: false, recover: false },
]) {
  test(`一般便當更新後重試舊開票請求，${name}`, { timeout: 25000 }, async (t) => {
    const { page, state } = await scheduleFixture(t);
    state.submissionResults = [...results];
    state.conflictOnCreate = submit && recover;
    const requestId = '680456c6-b8af-4b63-83b0-1a3b5f86da43';
    const key = legacyMealSubmissionKey('online', 'pickup_day');
    await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
    await page.evaluate(([key, id]) => sessionStorage.setItem(key, id), [key, requestId]);
    await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
    await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
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

for (const { handling, company } of [
  { handling: 'pickup_day', company: true },
  { handling: 'on_payment', company: true },
  { handling: 'pickup_day', company: false },
]) {
  test(`舊${company ? '公司' : '個人'}發票現金單待確認時先查看訂單（${handling}）`, { timeout: 25000 }, async (t) => {
    const { page, state } = await scheduleFixture(t);
    state.events[0].allow_cash_payment = true;
    const requestId = '680456c6-b8af-4b63-83b0-1a3b5f86da43';
    const key = legacyMealSubmissionKey('cash_on_pickup', handling, company ? {
      buyerType: 'company', buyerTaxId: '12345675', buyerName: '隔離測試公司',
      buyerEmail: 'schedule@example.test', carrierType: 'cloud',
    } : undefined);
    await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
    await page.evaluate(([key, id]) => sessionStorage.setItem(key, id), [key, requestId]);
    await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
    assert.equal(await page.getByRole('radio', { name: '合作社現場付款', exact: true }).count(), 0);
    await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
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
      await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
      assert.equal(state.orders.length, 1);
      assert.equal(state.orders[0].payment_method, 'online');
      assert.notEqual(state.orders[0].client_request_id, requestId);
      assert.equal(state.orders[0].kitchen_note, '隔離新訂單');
      assert.equal(Object.hasOwn(state.orders[0], 'invoice_handling'), false);
      assert.equal(Object.hasOwn(state.orders[0], 'invoice_buyer_tax_id'), false);
      assert.equal(await page.evaluate(key => sessionStorage.getItem(key), key), requestId);
    }
  });
}

test('首次線上便當照常送出，回應失敗後重用新版識別並清除成功紀錄', { timeout: 25000 }, async (t) => {
  const { page, state } = await scheduleFixture(t);
  state.events[0].allow_cash_payment = true;
  state.rejectOrderOnce = true;
  await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  const submit = page.getByRole('button', { name: '預訂並前往線上付款', exact: true });
  await submit.click({ trial: true });
  await submit.evaluate(button => { button.click(); button.click(); });
  await page.getByText('隔離測試暫未收到回應', { exact: true }).waitFor();
  assert.equal(state.orders.length, 1);
  const id = state.orders[0].client_request_id;
  const key = legacyMealSubmissionKey('online');
  const marker = key.replace('meal-submission:', 'meal-submission-current:');
  assert.deepEqual(await page.evaluate(keys => keys.map(item => sessionStorage.getItem(item)), [key, marker]), [id, id]);
  await submit.click();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
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
  await page.getByRole('tab', { name: '場次', exact: true }).click();
  const event = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await event.getByRole('heading', { name: '隔離每日午餐', exact: true }).waitFor();
  assert.equal(state.adminPickupOrderReads, 0);
  await event.getByRole('button', { name: '查看本場訂單與取餐時間', exact: true }).click();
  const orders = event.getByRole('region', { name: '本場訂單與取餐時間', exact: true });
  await orders.getByRole('heading', { name: orderNumber, exact: true }).waitFor();
  assert.match(await orders.innerText(), /預計取餐：.*9\/17.*12:05/);
  assert.match(await orders.innerText(), /隔離買家.*buyer@example.test.*0912345678/);
  assert.equal(state.adminPickupOrderReads, 1);
  await captureMealScreenshot(page, 'meal-roster-contacts-mobile.png');
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknownApi, []);
});

for (const width of [390, 1440]) {
  test(`${width}px 場次可依台灣供餐日、狀態與關鍵字查詢並還原封存`, { timeout: 25000 }, async (t) => {
    const { page, state } = await scheduleFixture(t, { admin: true, width });
    const historic = scheduledEvent({ id: 'archivable-meal', title: '已完成驗收場次', status: 'completed', can_edit: false,
      location: '驗收取餐區', service_date: null, pickup_starts_at: '2026-09-15T16:30:00Z', pickup_ends_at: '2026-09-15T18:30:00Z' });
    state.events.push(historic, ...Array.from({ length: 10 }, (_, index) => scheduledEvent({ id: `extra-${index}`, title: `未來場次 ${index + 1}` })));
    await page.goto(`${base}/admin/meals?tab=events`);
    const search = page.getByRole('form', { name: '查詢場次' });
    const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: historic.title, exact: true }) });
    const status = search.getByLabel('場次狀態');
    const submit = () => search.getByRole('button', { name: '查詢場次', exact: true }).click();
    await search.waitFor();
    assert.equal(await page.locator('.meal-event-admin-card').count(), 10);
    await page.getByRole('navigation', { name: '場次分頁' }).getByRole('button', { name: '下一頁' }).click();
    await card.waitFor();
    await search.getByLabel('供餐日期').fill('2026-09-16');
    await status.selectOption('published');
    await submit();
    await page.getByText('沒有符合條件的場次', { exact: true }).waitFor();
    await status.selectOption('completed');
    await search.getByLabel('場次名稱或地點').fill('驗收取餐區');
    await submit();
    await card.waitFor();
    assert.equal(await page.locator('.meal-event-admin-card').count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await captureMealScreenshot(page, `meal-session-search-${width}.png`);
    await card.getByRole('button', { name: '封存場次' }).click();
    await card.waitFor({ state: 'hidden' });
    await status.selectOption('archived');
    await submit();
    await card.getByRole('button', { name: '查看本場訂單與取餐時間' }).waitFor();
    await card.getByRole('button', { name: '取消封存' }).click();
    await card.waitFor({ state: 'hidden' });
    await status.selectOption('completed');
    await submit();
    await card.waitFor();
    assert.deepEqual(state.writes.map(({ payload }) => payload), [{ archived: true }, { archived: false }]);
    assert.deepEqual(state.unknownApi, []);
  });
}

test('便當後台每30秒更新場次，供餐狀態更新時仍隱藏核銷且保留未儲存編輯', { timeout: 20000 }, async (t) => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals`);
  await page.getByRole('tab', { name: '場次', exact: true }).click();
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await card.getByRole('button', { name: '編輯場次', exact: true }).click();
  const editor = page.getByRole('form', { name: '編輯便當場次', exact: true });
  await editor.getByText('名稱、地點、時間與餐點', { exact: true }).click();
  await editor.getByLabel('場次名稱', { exact: true }).fill('尚未儲存的午餐修改');
  await editor.getByLabel('取餐地點', { exact: true }).fill('尚未儲存的取餐地點');
  assert.equal(await card.getByRole('button', { name: '確認核銷', exact: true }).count(), 0);
  const mealReads = state.adminMealReads;
  const eventReads = state.adminEventReads;
  state.events[0] = { ...state.events[0], status: 'pickup_open', can_edit: false };
  await page.clock.fastForward(30000);
  await card.getByText('供餐中', { exact: true }).waitFor();
  assert.equal(await card.getByRole('button', { name: '確認核銷', exact: true }).count(), 0);
  assert.equal(await editor.getByLabel('場次名稱', { exact: true }).inputValue(), '尚未儲存的午餐修改');
  assert.equal(await editor.getByLabel('取餐地點', { exact: true }).inputValue(), '尚未儲存的取餐地點');
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
  await page.getByRole('tab', { name: '場次', exact: true }).click();
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await card.getByRole('button', { name: '編輯場次', exact: true }).click();
  const editor = page.getByRole('form', { name: '編輯便當場次', exact: true });
  const startsAt = editor.getByLabel('取餐開始（台灣時間）', { exact: true });
  await editor.getByText('名稱、地點、時間與餐點', { exact: true }).waitFor();
  assert.equal(await startsAt.isVisible(), false);
  await editor.getByText('名稱、地點、時間與餐點', { exact: true }).click();
  await editor.getByLabel('場次名稱', { exact: true }).fill('手動更新午餐');
  await startsAt.fill('2026-09-17T12:00');
  await editor.getByLabel('取餐結束（台灣時間）', { exact: true }).fill('2026-09-17T13:00');
  state.rejectEdit = true;
  await editor.getByRole('button', { name: '儲存場次修改', exact: true }).click();
  await editor.getByText('此場次已有訂單，不能修改原場次', { exact: true }).waitFor();
  assert.equal(await editor.getByLabel('場次名稱', { exact: true }).inputValue(), '手動更新午餐');
  state.rejectEdit = false;
  await editor.getByRole('button', { name: '儲存場次修改', exact: true }).click();
  await page.getByRole('heading', { name: '手動更新午餐', exact: true }).waitFor();
  assert.equal(state.writes.length, 2);
  assert.equal(state.writes[1].payload.pickup_starts_at, '2026-09-17T04:00:00.000Z');
  assert.equal(state.writes[1].payload.pickup_ends_at, '2026-09-17T05:00:00.000Z');
  state.events[0].can_edit = false;
  await page.reload();
  const updated = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '手動更新午餐', exact: true }) });
  await updated.getByRole('heading', { name: '手動更新午餐', exact: true }).waitFor();
  assert.equal(await updated.getByRole('button', { name: '編輯場次', exact: true }).isDisabled(), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.unknownApi, []);
});

test('快速切換僅開放會餐可直接儲存並保留取餐時間', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  Object.assign(state.events[0], { booking_policy: 'workday' });
  await page.goto(`${base}/admin/meals?tab=events`);
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  await card.getByRole('button', { name: '編輯場次', exact: true }).click();
  const form = page.getByRole('form', { name: '編輯便當場次', exact: true });
  await form.getByRole('checkbox', { name: '僅開放會餐', exact: true }).check();
  assert.equal(await form.getByLabel('取餐開始（台灣時間）', { exact: true }).isVisible(), false);
  await form.getByRole('button', { name: '儲存場次修改', exact: true }).click();
  await card.getByText('僅開放會餐', { exact: true }).waitFor();
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].payload.business_only, true);
  assert.equal(state.writes[0].payload.pickup_starts_at, '2026-09-17T03:00:00.000Z');
  assert.equal(state.writes[0].payload.pickup_ends_at, '2026-09-17T05:30:00.000Z');
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

test('手機尺寸沒有電話的便當帳號須先填電話', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t);
  state.phone = null;
  await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  const phone = page.getByRole('textbox', { name: '聯絡電話', exact: true });
  assert.equal(await phone.getAttribute('required'), '');
  const submit = page.getByRole('button', { name: '預訂並前往線上付款', exact: true });
  assert.equal(await submit.isDisabled(), true);
  await phone.fill('0912345678');
  await submit.waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button =>
    button.textContent.trim() === '預訂並前往線上付款' && !button.disabled));
  await captureMealScreenshot(page, 'meal-contact-phone-mobile.png');
  await submit.click();
  await page.waitForURL('**/isolated-meal-payment');
  assert.equal(state.orders[0].contact_phone, '0912345678');
  assert.deepEqual(state.unknownApi, []);
});

test('手機尺寸颱風停餐先顯示午晚餐與退款，確認後公告', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals?tab=disaster`);
  await page.getByLabel('停餐日期', { exact: true }).fill('2026-09-17');
  await page.getByRole('button', { name: '預覽影響場次' }).click();
  await page.getByText(/隔離每日晚餐.*3 筆訂單/).waitFor();
  await page.getByRole('button', { name: '取消供餐與退款', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText(/將退款 2 筆、共 \$240，無法復原/).waitFor();
  assert.equal(state.writes.length, 1);
  await captureMealScreenshot(page, 'meal-disaster-confirm-mobile.png');
  await dialog.getByRole('button', { name: '取消場次並退款', exact: true }).click();
  await page.getByText(/已取消 2 個場次、6 筆訂單/).waitFor();
  assert.equal(state.writes[1].payload.reason, '颱風停止上班上課');
  assert.deepEqual(state.writes[1].payload.periods, ['lunch', 'dinner']);
  await page.goto(`${base}/shop?tab=meals`);
  await page.getByRole('group', { name: '取餐日期' }).getByRole('button', { name: /9\/17.*颱風暫停供餐/ }).click();
  await page.getByRole('list', { name: '停餐資訊' }).getByText(/午餐、晚餐 · 颱風期間暫停供餐/).waitFor();
  assert.deepEqual(state.unknownApi, []);
});

test('手機後台從天災預覽暫停和恢復選定場次的新預訂', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  await page.goto(`${base}/admin/meals?tab=disaster`);
  await page.getByLabel('停餐日期', { exact: true }).fill('2026-09-17');
  await page.getByRole('checkbox', { name: '晚餐', exact: true }).uncheck();
  await page.getByRole('button', { name: '預覽影響場次' }).click();
  const list = page.getByRole('list', { name: '影響場次' });
  await list.getByRole('button', { name: '暫停新預訂', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '隔離每日午餐 · 已暫停新預訂' }).waitFor();
  const resume = list.getByRole('button', { name: '恢復新預訂', exact: true });
  await resume.waitFor();
  assert.equal(await list.getByText(/隔離每日晚餐/).count(), 0);
  await captureMealScreenshot(page, 'meal-disaster-pause-mobile.png');
  await resume.click();
  await page.getByRole('status').filter({ hasText: '隔離每日午餐 · 已恢復新預訂' }).waitFor();
  await list.getByRole('button', { name: '暫停新預訂', exact: true }).waitFor();
  assert.deepEqual(state.writes.filter(write => write.path.endsWith('/ordering-pause')), [
    { path: '/v1/admin/meal-events/scheduled-lunch/ordering-pause', payload: { paused: true, expected_version: 0 } },
    { path: '/v1/admin/meal-events/scheduled-lunch/ordering-pause', payload: { paused: false, expected_version: 1 } },
  ]);
  assert.ok(state.writes.every(write => !write.path.endsWith('/cancel')));
  assert.deepEqual(state.unknownApi, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
});

test('手機天災預覽保留已取消場次並只計入仍可取消的場次', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  Object.assign(state.events[0], { status: 'cancelled', ordering_paused: true, can_edit: false });
  await page.goto(`${base}/admin/meals?tab=disaster`);
  await page.getByLabel('停餐日期', { exact: true }).fill('2026-09-17');
  await page.getByRole('button', { name: '預覽影響場次' }).click();
  const history = page.getByRole('list', { name: '已取消場次' });
  await history.getByText(/隔離每日午餐.*已取消/).waitFor({ timeout: 5000 });
  assert.equal(await history.getByRole('button').count(), 0);
  assert.equal(await page.getByRole('list', { name: '影響場次' }).getByText(/隔離每日午餐/).count(), 0);
  await page.getByRole('button', { name: '取消供餐與退款', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText('取消 2026-09-17 的 1 個場次', { exact: true }).waitFor();
  await dialog.getByText(/將退款 1 筆、共 \$120/).waitFor();
  await dialog.getByRole('button', { name: '返回' }).click();
  await page.getByRole('checkbox', { name: '晚餐', exact: true }).uncheck();
  await page.getByRole('button', { name: '預覽影響場次' }).click();
  await history.getByText(/隔離每日午餐.*已取消/).waitFor();
  assert.equal(await page.getByRole('button', { name: '取消供餐與退款', exact: true }).count(), 0);
  await page.getByRole('link', { name: '查看當日場次' }).click();
  await page.waitForURL(url => url.searchParams.get('tab') === 'events' && url.searchParams.get('date') === '2026-09-17');
  assert.equal(await page.getByLabel('供餐日期', { exact: true }).inputValue(), '2026-09-17');
  assert.ok(state.writes.every(write => write.path.endsWith('/preview')));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
});

test('手機場次取消先確認，已取消場次不顯示恢復新預訂', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  Object.assign(state.events[0], { ordering_paused: true });
  Object.assign(state.events[1], { status: 'cancelled', ordering_paused: true, can_edit: false });
  await page.goto(`${base}/admin/meals?tab=events`);
  const lunch = page.getByRole('article').filter({ has: page.getByRole('heading', { name: '隔離每日午餐', exact: true }) });
  const dinner = page.getByRole('article').filter({ has: page.getByRole('heading', { name: '隔離每日晚餐', exact: true }) });
  await dinner.getByText('已取消', { exact: true }).waitFor();
  assert.equal(await dinner.getByText('新預訂已暫停', { exact: true }).count(), 0);
  assert.equal(await dinner.getByRole('button', { name: '恢復新預訂', exact: true }).count(), 0);
  await lunch.getByRole('button', { name: '取消場次', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText(/取消本場訂單，並為已付款的訂單安排退款/).waitFor({ timeout: 5000 });
  await dialog.getByRole('button', { name: '返回' }).click();
  assert.deepEqual(state.writes, []);
  await lunch.getByLabel('處理原因').fill('隔離驗收');
  await lunch.getByRole('button', { name: '取消場次', exact: true }).click();
  await dialog.getByRole('button', { name: '取消場次並退款', exact: true }).click();
  await lunch.getByText('已取消', { exact: true }).waitFor();
  assert.deepEqual(state.writes, [{ path: '/v1/admin/meal-events/scheduled-lunch/cancel', payload: { reason: '隔離驗收' } }]);
});

test('手機從天災取消紀錄帶入補開草稿，原場次維持取消', { timeout: 25000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  const original = state.events[0];
  Object.assign(original, { status: 'cancelled', can_edit: false, booking_policy: 'workday', ordering_paused: true });
  original.offerings.push({ ...original.offerings[0], id: 'old-offering', meal_id: 'inactive-meal', is_active: false });
  await page.goto(`${base}/admin/meals?tab=disaster`);
  await page.getByLabel('停餐日期', { exact: true }).fill('2026-09-17');
  await page.getByRole('button', { name: '預覽影響場次', exact: true }).click();
  await page.getByRole('link', { name: '為「隔離每日午餐」建立補開草稿', exact: true }).click();
  const form = page.getByRole('form', { name: '建立補開草稿', exact: true });
  assert.equal(await form.getByLabel('場次名稱', { exact: true }).inputValue(), original.title);
  assert.equal(await form.getByLabel('取餐開始（台灣時間）', { exact: true }).inputValue(), '2026-09-17T11:00');
  assert.equal(await form.getByLabel('份數', { exact: true }).count(), 1);
  assert.equal(await form.getByLabel('份數', { exact: true }).inputValue(), '50');
  assert.equal(state.writes.length, 1);
  await captureMealScreenshot(page, 'meal-replacement-draft-mobile.png');
  await form.getByRole('button', { name: '建立補開草稿', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '已建立「隔離每日午餐」草稿' }).waitFor();
  assert.equal(state.writes.length, 2);
  const request = state.writes[1];
  assert.equal(request.method, 'POST');
  assert.equal(request.path, '/v1/admin/meal-events');
  assert.equal(request.payload.replaces_event_id, original.id);
  assert.equal(request.payload.allow_cash_payment, false);
  assert.deepEqual(request.payload.offerings, [{ meal_id: 'scheduled-meal', price: 120, capacity: 50, position: 0 }]);
  assert.equal(state.events.find(event => event.id === original.id).status, 'cancelled');
  assert.equal(state.events.find(event => event.id === 'replacement-lunch').status, 'draft');
  const draft = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('button', { name: '發布', exact: true }) });
  await draft.getByRole('button', { name: '發布', exact: true }).click();
  await draft.waitFor({ state: 'hidden' });
  assert.equal(state.writes.length, 3);
  assert.equal(state.events.find(event => event.id === 'replacement-lunch').status, 'published');
  assert.equal(state.events.find(event => event.id === original.id).status, 'cancelled');
  assert.deepEqual(state.unknownApi, []);
});

test('已過期場次補開須選新供餐日期，避免誤建過去場次', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  Object.assign(state.events[0], { status: 'cancelled', can_edit: false, booking_policy: 'special',
    ordering_starts_at: '2026-09-14T00:00:00Z', ordering_ends_at: '2026-09-14T12:00:00Z',
    pickup_starts_at: '2026-09-15T03:00:00Z', pickup_ends_at: '2026-09-15T05:30:00Z', service_date: '2026-09-15' });
  await page.goto(`${base}/admin/meals?tab=events&date=2026-09-15`);
  await page.getByRole('link', { name: '建立補開草稿', exact: true }).click();
  const form = page.getByRole('form', { name: '建立補開草稿', exact: true });
  const save = form.getByRole('button', { name: '建立補開草稿', exact: true });
  await form.getByRole('alert').filter({ hasText: '原取餐時段已過' }).waitFor();
  assert.equal(await save.isDisabled(), true);
  await form.getByLabel('取餐開始（台灣時間）', { exact: true }).fill('2026-09-18T11:00');
  assert.equal(await form.getByLabel('取餐結束（台灣時間）', { exact: true }).inputValue(), '2026-09-18T13:30');
  assert.equal(await save.isEnabled(), true);
  assert.deepEqual(state.writes, []);
  await form.getByRole('button', { name: '取消編輯', exact: true }).click();
  await form.waitFor({ state: 'hidden' });
  assert.deepEqual(state.writes, []);
});

test('當天補開場次須明確啟用當日訂餐，修改一般截止時間仍不能送出', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  Object.assign(state.events[0], { status: 'cancelled', can_edit: false, booking_policy: 'special' });
  await page.clock.setSystemTime(new Date('2026-09-17T01:00:00Z'));
  await page.goto(`${base}/admin/meals?tab=events&date=2026-09-17&rebuild=scheduled-lunch`);
  const form = page.getByRole('form', { name: '建立補開草稿', exact: true });
  const cutoff = form.getByLabel('預訂截止（台灣時間）', { exact: true });
  const save = form.getByRole('button', { name: '建立補開草稿', exact: true });
  await cutoff.fill('2026-09-17T10:30');
  await form.getByRole('alert').filter({ hasText: '預訂截止已過' }).waitFor();
  assert.equal(await save.isDisabled(), true);
  await form.getByRole('checkbox', { name: '開放當日訂餐（僅線上付款）', exact: true }).check();
  await form.getByLabel('最短備餐時間（分鐘）', { exact: true }).fill('30');
  assert.equal(await save.isEnabled(), true);
  assert.deepEqual(state.writes, []);
});

test('手機尺寸便當建立付款失敗，原訂單保留原因並可重新付款', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t);
  state.savedOrder = mealOrder({ pickup_start: state.events[0].pickup_starts_at, pickup_end: state.events[0].pickup_ends_at });
  state.paymentError = '隔離金流維護中';
  await page.goto(`${base}/meals/scheduled-lunch?pickup_at=2026-09-17T11%3A00`);
  await page.getByRole('button', { name: '增加隔離每日便當', exact: true }).click();
  await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
  await page.waitForURL(`**/meal-orders?order_id=${orderId}`);
  await page.getByRole('alert').filter({ hasText: '隔離金流維護中' }).waitFor();
  await captureMealScreenshot(page, 'meal-payment-retry-mobile.png');
  state.paymentError = null;
  await page.getByRole('button', { name: '前往付款 $120', exact: true }).click();
  await page.waitForURL('**/isolated-meal-payment');
  assert.equal(state.orders.length, 1);
  assert.deepEqual(state.unknownApi, []);
});

test('排程手機畫面顯示週六會餐並可停用單週', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  state.calendarUpdateYear = 2028;
  state.events.push(scheduledEvent({
    id: 'saturday-business', title: '隔離週六會餐', business_only: true, schedule_template_id: 'sat-template',
    service_date: '2026-09-19', ordering_starts_at: '2026-09-17T16:00:00Z', ordering_ends_at: '2026-09-18T12:00:00Z',
    pickup_starts_at: '2026-09-19T03:00:00Z', pickup_ends_at: '2026-09-19T05:30:00Z',
  }));
  await page.goto(`${base}/admin/meals?tab=schedules`);
  await page.getByRole('checkbox', { name: '週六・只接受會餐' }).waitFor();
  assert.equal(await page.getByRole('link', { name: '請更新 2028 年官方日曆' }).getAttribute('href'), 'https://data.gov.tw/dataset/14718');
  const list = page.getByRole('region', { name: '接下來的週六會餐' });
  await list.getByText('隔離週六會餐').waitFor();
  await captureMealScreenshot(page, 'meal-saturday-schedule-mobile.png');
  await list.getByRole('button', { name: '停用這週預訂' }).click();
  await list.getByRole('button', { name: '恢復這週預訂' }).waitFor();
  assert.equal(state.writes[0].path, '/v1/admin/meal-events/saturday-business/ordering-pause');
  assert.deepEqual(state.unknownApi, []);
});

test('手機後台便當查詢顯示聯絡方式、特殊場次與單一退單原因', { timeout: 20000 }, async t => {
  const { page, state } = await scheduleFixture(t, { admin: true });
  state.adminOrders = [{
    customer_name: '隔離買家', customer_email: 'buyer@example.test', meal_period: null,
    order: mealOrder({ contact_phone: '0912345678', cancelled_at: '2026-09-14T01:00:00Z', available_actions: [],
      cancellation_request: { id: 'cancelled-order', initiated_by_admin: true, status: 'approved',
        reason: '現場無法供餐', review_reason: '現場無法供餐', requested_at: '2026-09-14T01:00:00Z' },
    }),
  }];
  await page.goto(`${base}/admin/meals?tab=orders`);
  const card = page.locator('.meal-operation-order');
  await card.getByText('隔離買家 · buyer@example.test · 0912345678', { exact: true }).waitFor();
  await card.getByText(/特殊場次/).waitFor();
  await card.getByText('查看餐點、發票與退單明細', { exact: true }).click();
  await card.getByText('退單原因：現場無法供餐', { exact: true }).waitFor();
  assert.equal(await card.getByText(/現場無法供餐/).count(), 1);
  await captureMealScreenshot(page, 'meal-admin-contacts-mobile.png');
  assert.deepEqual(state.unknownApi, []);
});
