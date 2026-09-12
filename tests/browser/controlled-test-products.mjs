import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname)
  || !['http:', 'https:'].includes(testUrl.protocol)
  || testUrl.username || testUrl.password || testUrl.pathname !== '/' || testUrl.search || testUrl.hash) {
  throw new Error('Controlled product acceptance must use an isolated local website origin');
}
const base = testUrl.origin;
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const pickupId = '716050ea-fdcc-4a7d-80cb-6eae5c208111';
const buyerEmail = 'buyer@example.test';
const invoiceEmail = 'invoice@example.test';
const products = [
  { id: 'e1d2d8ab-0044-491e-8550-6c6136118b79', sku: 'PRIMARY-TEST-10', name: '主要付款驗收品（隔離測試）',
    orderId: 'd348b030-fbb5-48c2-8d0f-ff828a35d771' },
  { id: '82e8bd39-7384-473e-b13a-150831145edc', sku: 'REMOTE-PAYMENT-10', name: '遠端付款驗收品（隔離測試）',
    orderId: '91c981ce-5d27-4022-af41-112bb2b6e701' },
];
const catalog = products.map(({ orderId: _orderId, ...product }) => ({ ...product,
  slug: product.sku.toLowerCase(), description: '隔離線上付款驗收；本機測試不會扣款。',
  category: '測試', unit: '件', supplier_name: '隔離合作社',
  image_url: '/assets/products/remote-payment-10.svg', member_price: 10, nonmember_price: 10,
  stock_quantity: 1, tax_type: 'taxable', can_ship: false, shipping_temperature: 'ambient',
  allowed_shipping_channels: [], is_active: true,
}));

async function fixture(t, selected) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block',
  });
  const paymentPath = `/v1/orders/${selected.orderId}/payment-attempts`;
  const fakePaymentPath = `/isolated-product-payment/${selected.sku}`;
  const order = {
    id: selected.orderId, order_number: `SLF-${selected.sku}`, order_kind: 'regular', sales_channel: 'regular',
    fulfillment_method: 'cooperative_pickup', pickup_location_id: pickupId,
    payment_status: 'pending', invoice_status: 'not_eligible', fulfillment_status: 'unfulfilled',
    amount_total: 10, tax_amount: 0, contact_email: buyerEmail, created_at: '2026-09-12T10:00:00Z',
    available_actions: ['pay', 'cancel'], invoice: null, shipment: null,
    items: [{ product_id: selected.id, product_name: selected.name, unit_label: '件',
      quantity: 1, unit_price: 10, subtotal: 10, tax_type: 'taxable' }],
  };
  const state = { quotes: [], mutations: [], unknownApi: [], blockedExternal: [], paymentVisits: 0 };
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
        body: `<!doctype html><meta charset="utf-8"><h1>隔離商品付款頁</h1><p>${selected.sku}</p><p>僅本機假付款，不會扣款。</p>` });
    }
    if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/payments/') || url.pathname.startsWith('/internal/')) {
      if (method === 'POST' && url.pathname === '/v1/auth/refresh') {
        return route.fulfill({ json: { access_token: 'isolated-product-token',
          user: { id: 'isolated-buyer', email: buyerEmail, display_name: '隔離買家', user_role: 'customer' } } });
      }
      if (method === 'GET') {
        if (url.pathname === '/v1/products') return route.fulfill({ json: catalog });
        if (['/v1/group-campaigns', '/v1/meal-events', '/v1/shipping-rates'].includes(url.pathname)) return route.fulfill({ json: [] });
        if (url.pathname === '/v1/pickup-locations') return route.fulfill({ json: [{ id: pickupId,
          name: '隔離自取點', address: '隔離地址', instructions: '僅用於本機測試', is_active: true }] });
        if (url.pathname === '/v1/orders') return route.fulfill({ json: state.mutations.length ? [order] : [] });
        if (url.pathname === `/v1/orders/${selected.orderId}`) return route.fulfill({ json: order });
      }
      if (method === 'POST' && url.pathname === '/v1/orders/quote') {
        const body = request.postDataJSON();
        state.quotes.push(body);
        const valid = body.items?.length === 1 && body.items[0].product_id === selected.id && body.items[0].quantity === 1;
        return route.fulfill({ status: valid ? 200 : 409, json: valid ? {
          items: [{ product_id: selected.id, product_name: selected.name, quantity: 1,
            unit_price: 10, subtotal: 10 }], amount_total: 10, membership_type: 'nonmember',
        } : { detail: '隔離試算收到錯誤商品或數量' } });
      }
      if (method === 'POST' && url.pathname === '/v1/orders') {
        state.mutations.push({ path: '/v1/orders', body: request.postDataJSON() });
        return route.fulfill({ status: 201, json: order });
      }
      if (method === 'POST' && url.pathname === paymentPath) {
        state.mutations.push({ path: paymentPath });
        return route.fulfill({ status: 201, json: { id: `isolated-${selected.sku}`,
          payment_url: `${base}${fakePaymentPath}`, status: 'pending' } });
      }
      state.unknownApi.push(`${method} ${url.pathname}`);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  return { page, state, paymentPath, fakePaymentPath };
}

for (const selected of products) {
  test(`${selected.sku} 從日常選品加入單件、自取及發票試算後，只建一次正確商品訂單與線上付款`, { timeout: 30000 }, async (t) => {
    const { page, state, paymentPath, fakePaymentPath } = await fixture(t, selected);
    await page.goto(`${base}/shop`);
    await page.getByRole('tab', { name: '日常選品', exact: true }).click();
    const card = page.locator('.product-card').filter({ has: page.getByText(selected.sku, { exact: true }) });
    await card.waitFor();
    assert.equal(await page.locator('.product-card').count(), 2);
    await card.getByRole('button', { name: `加入 ${selected.name}`, exact: true }).click();
    await page.getByRole('link', { name: /查看結帳清單/ }).click();
    await page.waitForURL(`${base}/checkout`);
    const checkout = page.locator('.checkout-page');
    await checkout.getByRole('heading', { name: selected.name, exact: true }).waitFor();
    assert.equal(await checkout.locator('.checkout-item').count(), 1);
    assert.equal(await checkout.locator('.checkout-item .quantity-control span').innerText(), '1');
    const other = products.find((product) => product.id !== selected.id);
    assert.equal(await checkout.getByRole('heading', { name: other.name, exact: true }).count(), 0);
    const pickup = checkout.getByRole('radio', { name: /合作社取貨/ });
    await pickup.click();
    assert.equal(await pickup.getAttribute('aria-checked'), 'true');
    await checkout.getByRole('radio', { name: /隔離自取點/ }).check();
    await checkout.getByRole('radio', { name: /個人電子發票/ }).click();
    await checkout.getByRole('radio', { name: /Email 會員載具/ }).check();
    await checkout.getByLabel('發票通知 Email', { exact: true }).fill(invoiceEmail);
    await checkout.locator('.summary-total dd').getByText('$10', { exact: true }).waitFor();
    assert.doesNotMatch(await checkout.innerText(), /現場付款|現金付款|貨到付款/);
    assert.equal(await checkout.getByRole('radio', { name: /現場付款|現金/ }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(state.mutations, []);
    await checkout.getByRole('button', { name: '前往線上付款', exact: true }).click();
    await page.waitForURL(`${base}${fakePaymentPath}`);
    await page.getByRole('heading', { name: '隔離商品付款頁', exact: true }).waitFor();
    await page.getByText(selected.sku, { exact: true }).waitFor();
    assert.deepEqual(state.mutations, [{ path: '/v1/orders', body: {
      items: [{ product_id: selected.id, quantity: 1 }], contact_email: buyerEmail,
      invoice_buyer_type: 'personal', invoice_buyer_email: invoiceEmail, invoice_carrier_type: 'cloud',
      fulfillment_method: 'cooperative_pickup', pickup_location_id: pickupId,
    } }, { path: paymentPath }]);
    assert.ok(state.quotes.length >= 1);
    assert.ok(state.quotes.every((body) => body.items.length === 1
      && body.items[0].product_id === selected.id && body.items[0].quantity === 1));
    assert.equal(state.paymentVisits, 1);
    assert.deepEqual(state.unknownApi, []);
  });
}
