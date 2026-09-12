import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

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
const cancelPath = `/v1/meal-orders/${orderId}/cancel`;

function mealOrder(overrides = {}) {
  return {
    id: orderId, order_number: orderNumber,
    sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
    meal_event_id: 'event-isolated', meal_event_title: '隔離便當場次',
    venue_name: '隔離取餐點', pickup_start: '2026-09-14T03:00:00Z', pickup_end: '2026-09-14T05:00:00Z',
    pickup_code: null, pickup_qr_payload: null, paid_at: null, cancelled_at: null,
    payment_status: 'pending', invoice_status: 'not_eligible', invoice_number: null, invoice_date: null,
    fulfillment_status: 'pending', amount_total: 120, created_at: '2026-09-12T01:00:00Z',
    available_actions: ['pay', 'cancel'],
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
    available_actions: ['cancel'],
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
    mealLookupStatus: 200, mealLookupReads: 0, extraMealOrders: [], refreshStatus: 'pending', refreshReads: 0,
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
    if (url.pathname === '/v1/auth/refresh') {
      return route.fulfill({ json: {
        access_token: 'isolated-meal-token',
        user: { id: 'buyer-isolated', email: 'buyer@example.test', display_name: '隔離買家', user_role: 'customer' },
      } });
    }
    if (request.method() === 'GET' && url.pathname === '/v1/meal-orders') {
      return route.fulfill({ json: [...state.extraMealOrders, state.order] });
    }
    if (request.method() === 'GET' && url.pathname === `/v1/meal-orders/${orderId}`) {
      state.mealLookupReads++;
      return route.fulfill({ status: state.mealLookupStatus, json: state.mealLookupStatus === 200 ? state.order : { detail: '隔離取餐訂單查詢暫不可用' } });
    }
    if (request.method() === 'GET' && url.pathname === credentialPath) {
      state.credentialReads++;
      return route.fulfill({
        status: state.credentialStatus,
        json: state.credentialStatus === 200 ? state.credential : { detail: '此取餐憑證已失效，請核對訂單狀態' },
      });
    }
    if (request.method() === 'GET' && url.pathname === '/v1/orders') {
      return route.fulfill({ json: [{ ...state.order, order_kind: 'regular', tax_amount: 6,
        invoice: state.order.invoice_number ? { invoice_number: state.order.invoice_number, invoice_date: state.order.invoice_date } : null,
        shipment: null, items: [{ id: 'item-isolated', product_name: '隔離蔬食便當', unit_label: '份', quantity: 1, unit_price: 120, subtotal: 120, tax_type: 'taxable' }],
      }] });
    }
    if (request.method() === 'POST' && url.pathname === paymentPath) {
      state.mutations.push({ path: paymentPath });
      return route.fulfill({ json: { payment_url: `${base}/isolated-meal-payment` } });
    }
    if (request.method() === 'POST' && url.pathname === '/v1/payment-attempts/attempt-isolated/refresh') {
      state.refreshReads++;
      return route.fulfill({ json: { id: 'attempt-isolated', order_id: orderId, status: state.refreshStatus } });
    }
    if (request.method() === 'POST' && url.pathname === cancelPath) {
      state.mutations.push({ path: cancelPath, body: request.postDataJSON() });
      state.order = { ...state.order, payment_status: 'refund_pending', fulfillment_status: 'cancelled',
        cancelled_at: '2026-09-14T03:01:00Z', pickup_code: null, pickup_qr_payload: null, available_actions: [] };
      state.credentialStatus = 409;
      return route.fulfill({ json: state.order });
    }
    state.unknownApi.push(`${request.method()} ${url.pathname}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API，禁止連線真實後端' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.goto(`${base}/meal-orders`);
  await page.getByRole('heading', { name: '隔離便當場次', exact: true }).waitFor();
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
    await page.getByRole('heading', { name: '隔離便當場次', exact: true }).waitFor();
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

test('已付款取消便當只送一次原因，退款中立即撤下取餐 QR', { timeout: 20000 }, async (t) => {
  const { page, state, qr } = await fixture(t, paidOrder());
  await visibleCredential(page, qr);
  await page.getByRole('button', { name: '取消這筆便當預購', exact: true }).click();
  await page.getByLabel('取消原因', { exact: true }).fill('隔離驗收取消原因');
  await page.getByRole('button', { name: '確認取消', exact: true }).click();
  await noCredential(page, qr);
  await page.getByText('退款中', { exact: true }).waitFor();
  assert.deepEqual(state.mutations, [{ path: cancelPath, body: { reason: '隔離驗收取消原因' } }]);
  assert.equal(await page.getByRole('button', { name: '確認取消', exact: true }).count(), 0);
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
  await page.getByRole('heading', { name: '隔離便當場次', exact: true }).waitFor();
  assert.deepEqual(state.mutations, []);
  await verifyIsolation(page, state);
});

for (const kind of ['regular', 'missing', 'not-owner']) {
  test(`付款返回 ${kind} 訂單不誤導向便當頁`, { timeout: 15000 }, async (t) => {
    const { page, state } = await fixture(t, mealOrder());
    if (kind === 'regular') state.order.sales_channel = 'regular';
    if (kind === 'not-owner') state.mealLookupStatus = 404;
    const targetId = kind === 'missing' ? 'unknown-order' : orderId;
    const lookup = kind === 'not-owner' ? page.waitForResponse((response) => response.url().endsWith(`/v1/meal-orders/${orderId}`)) : Promise.resolve();
    await page.goto(`${base}/orders?order_id=${targetId}&payment=paid`);
    await page.locator('.order-index').waitFor();
    await lookup;
    await page.waitForTimeout(250);
    assert.equal(new URL(page.url()).pathname, '/orders');
    assert.equal(state.credentialReads, 0);
    if (kind !== 'not-owner') assert.equal(state.mealLookupReads, 0);
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
