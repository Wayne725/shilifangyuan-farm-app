import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname)
  || !['http:', 'https:'].includes(testUrl.protocol)
  || testUrl.username || testUrl.password || testUrl.pathname !== '/' || testUrl.search || testUrl.hash) {
  throw new Error('Meal launch acceptance must use an isolated local website origin');
}
const base = testUrl.origin;
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const eventId = 'd6486292-92c5-4b02-bd1a-64c911851561';
const mealId = '3cb36aee-e5b7-4573-a14b-d9abecdcf9cd';
const offeringId = '461de78d-fb5c-4ed8-843e-d4e43b210983';
const orderId = '78320911-df49-4ad2-bcb2-9c39538a3797';
const eventTitle = '隔離正式線上付款驗收場次';
const mealName = '1 元線上付款測試（不供餐）';
const orderPath = `/v1/meal-events/${eventId}/orders`;
const paymentPath = `/v1/orders/${orderId}/payment-attempts`;
const fakePaymentPath = '/isolated-online-meal-payment';
const orderItems = [{ offering_id: offeringId, quantity: 1, option_ids: [] }];

const mealEvent = {
  id: eventId, title: eventTitle, location: '隔離驗收點（不供餐）', status: 'published',
  ordering_starts_at: '2026-09-12T01:00:00Z', ordering_ends_at: '2026-09-12T02:25:00Z',
  pickup_starts_at: '2026-09-12T03:10:00Z', pickup_ends_at: '2026-09-12T03:55:00Z',
  offerings: [{
    id: offeringId, meal_id: mealId, meal_name: mealName,
    description: '僅供線上付款驗收，不供餐；所有付款請求皆由本機測試攔截。',
    image_url: '/assets/meals/taiwanese-lunchbox.webp', price: 1,
    capacity: 1, reserved_quantity: 0, paid_quantity: 0, available_quantity: 1,
    position: 0, is_active: true, option_groups: [],
  }],
};

const quote = {
  amount_total: 1,
  items: [{ offering_id: offeringId, meal_id: mealId, meal_name: mealName,
    quantity: 1, base_price: 1, option_price: 0, unit_price: 1, subtotal: 1,
    tax_type: 'taxable', selections: [] }],
  pickup: { location: mealEvent.location, starts_at: mealEvent.pickup_starts_at, ends_at: mealEvent.pickup_ends_at },
};

const order = {
  id: orderId, order_number: 'M2609120900009C39538A3797',
  sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
  meal_event_id: eventId, meal_event_title: eventTitle, venue_name: mealEvent.location,
  pickup_start: mealEvent.pickup_starts_at, pickup_end: mealEvent.pickup_ends_at,
  pickup_code: null, pickup_qr_payload: null, paid_at: null, cancelled_at: null,
  payment_status: 'pending', invoice_status: 'not_eligible', invoice_number: null, invoice_date: null,
  fulfillment_status: 'pending', amount_total: 1, created_at: '2026-09-12T01:05:00Z',
  available_actions: ['pay', 'cancel'], items: quote.items,
};

async function fixture(t) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block',
  });
  const state = { mutations: [], quotes: [], unknownApi: [], blockedExternal: [], paymentVisits: 0 };
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.origin !== base) {
      state.blockedExternal.push(url.origin);
      return route.abort();
    }
    if (url.pathname === fakePaymentPath && method === 'GET') {
      state.paymentVisits++;
      return route.fulfill({ contentType: 'text/html; charset=utf-8',
        body: '<!doctype html><meta charset="utf-8"><h1>隔離線上付款頁</h1><p>此頁不會扣款，也不會呼叫供應商。</p>' });
    }
    if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/payments/') || url.pathname.startsWith('/internal/')) {
      if (url.pathname === '/v1/auth/refresh' && method === 'POST') {
        return route.fulfill({ json: { access_token: 'isolated-launch-token',
          user: { id: 'isolated-launch-buyer', email: 'buyer@example.test', display_name: '隔離買家', user_role: 'customer' } } });
      }
      if (method === 'GET') {
        if (url.pathname === `/v1/meal-events/${eventId}`) return route.fulfill({ json: mealEvent });
        if (url.pathname === '/v1/meal-events') return route.fulfill({ json: [mealEvent] });
        if (['/v1/products', '/v1/group-campaigns'].includes(url.pathname)) return route.fulfill({ json: [] });
        if (url.pathname === '/v1/meal-orders') return route.fulfill({ json: [order] });
        if (url.pathname === `/v1/meal-orders/${orderId}`) return route.fulfill({ json: order });
        if (url.pathname === '/v1/orders') return route.fulfill({ json: [{ ...order,
          order_kind: 'regular', tax_amount: 0, invoice: null, shipment: null,
          items: [{ id: 'isolated-item', product_name: mealName, unit_label: '份',
            quantity: 1, unit_price: 1, subtotal: 1, tax_type: 'taxable' }],
        }] });
      }
      if (method === 'POST' && url.pathname === `/v1/meal-events/${eventId}/quote`) {
        state.quotes.push(request.postDataJSON());
        return route.fulfill({ json: quote });
      }
      if (method === 'POST' && url.pathname === orderPath) {
        state.mutations.push({ path: orderPath, body: request.postDataJSON() });
        return route.fulfill({ status: 201, json: order });
      }
      if (method === 'POST' && url.pathname === paymentPath) {
        state.mutations.push({ path: paymentPath });
        return route.fulfill({ status: 201, json: { id: 'isolated-payment-attempt',
          payment_url: `${base}${fakePaymentPath}`, status: 'pending' } });
      }
      state.unknownApi.push(`${method} ${url.pathname}`);
      return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API；禁止連線真實後端' } });
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  return { page, state };
}

async function verifyMobileOnlineOnly(page, state, content) {
  assert.doesNotMatch(await content.innerText(), /現場付款|現金付款|貨到付款/);
  assert.equal(await content.getByRole('button', { name: /現場付款|現金/ }).count(), 0);
  assert.equal(await content.getByRole('radio', { name: /現場付款|現金/ }).count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.unknownApi, []);
}

test('390px 正式 UUID 場次顯示完整臺灣時段，一次預訂只建立一單並交給本機線上付款頁', { timeout: 30000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/meals/${eventId}`);
  await page.getByRole('heading', { name: eventTitle, exact: true }).waitFor();
  const content = page.locator('.meal-offer-page');
  const details = content.locator('.offer-copy dl');
  for (const [label, time] of [['預訂截止', '10:25'], ['開始取餐', '11:10'], ['取餐結束', '11:55']]) {
    const field = details.locator('div').filter({ has: page.getByText(label, { exact: true }) });
    assert.match(await field.locator('dd').innerText(), new RegExp(`9/12.*${time}`));
  }
  const pickupWindow = await content.locator('.pickup-window').innerText();
  assert.match(pickupWindow, /9\/12.*11:10.*至.*9\/12.*11:55/);
  await page.getByRole('heading', { name: mealName, exact: true }).waitFor();
  await verifyMobileOnlineOnly(page, state, content);
  assert.deepEqual(state.mutations, []);
  await page.getByRole('button', { name: `增加${mealName}`, exact: true }).click();
  await content.locator('.offer-total dd').getByText('$1', { exact: true }).waitFor();
  await verifyMobileOnlineOnly(page, state, content);
  await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).click();
  await page.waitForURL(`${base}${fakePaymentPath}`);
  await page.getByRole('heading', { name: '隔離線上付款頁', exact: true }).waitFor();
  assert.deepEqual(state.mutations.map((entry) => entry.path), [orderPath, paymentPath]);
  assert.deepEqual(state.mutations[0].body.items, orderItems);
  assert.equal(state.mutations[0].body.contact_email, 'buyer@example.test');
  assert.equal(state.mutations[0].body.invoice_buyer_type, 'personal');
  assert.equal(state.mutations[0].body.invoice_carrier_type, 'cloud');
  assert.equal('payment_method' in state.mutations[0].body, false);
  assert.ok(state.quotes.length >= 1);
  assert.deepEqual(state.quotes.at(-1).items, orderItems);
  assert.equal(state.paymentVisits, 1);
  assert.deepEqual(state.unknownApi, []);
});

test('390px 商店便當卡片保留預訂截止與取餐起訖完整時段', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/shop`);
  await page.getByRole('tab', { name: '便當預購', exact: true }).click();
  const card = page.locator('.meal-card').filter({ has: page.getByRole('heading', { name: eventTitle, exact: true }) });
  await card.waitFor();
  const text = await card.innerText();
  assert.match(text, /預訂截止.*9\/12.*10:25/);
  assert.match(text, /取餐.*9\/12.*11:10.*至.*9\/12.*11:55/);
  assert.match(text, /不供餐/);
  assert.equal(await card.getAttribute('href'), `/meals/${eventId}`);
  await verifyMobileOnlineOnly(page, state, card);
  assert.deepEqual(state.mutations, []);
  assert.equal(state.paymentVisits, 0);
});
