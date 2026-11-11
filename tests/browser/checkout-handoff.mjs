import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const root = resolve(process.env.BROWSER_DIST_ROOT || resolve(import.meta.dirname, '../../web/dist'));
const product = { id: 'recovery-product', name: '回復測試白米', unit: '包', member_price: 100, nonmember_price: 100, stock_quantity: 50, is_active: true, daily_sales_enabled: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg' };

async function stopPendingChromiumNavigation(page) {
  if (engine !== chromium) return;
  // Use the browser stop action before inspecting controls in the outgoing document.
  const session = await page.context().newCDPSession(page);
  try { await session.send('Page.stopLoading'); }
  finally { await session.detach(); }
}

async function fixture(t, { payable = false, providerDelay = 1800, shipping = false } = {}) {
  const evidence = process.env.CHECKOUT_EVENT_EVIDENCE_DIR
    ? (await import('./checkout-event-evidence.mjs')).createCheckoutEventEvidence({ directory: process.env.CHECKOUT_EVENT_EVIDENCE_DIR, testName: t.name, engine: process.env.BROWSER_ENGINE || 'chromium' }) : null;
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(evidence ? () => evidence.close(() => browser.close()) : () => browser.close());
  const state = { total: 100, creates: [], orders: new Map(), payments: [], logistics: [], errors: [], unknown: [], serverErrors: [], reads: [], fullList: false };
  let providerStarted;
  const providerReady = new Promise(resolve => { providerStarted = resolve; });
  const view = order => ({ ...order, sales_channel: 'regular', created_at: '2030-01-02T01:00:00Z',
    fulfillment_method: shipping ? 'ecpay_logistics' : 'cooperative_pickup', fulfillment_status: 'pending_confirmation', invoice_status: 'not_eligible',
    available_actions: payable ? ['pay'] : [],
    ...(shipping ? { shipment: { status: 'selection_pending', channel: 'home_delivery', temperature: 'ambient', shipping_fee: 0 } } : {}),
    items: [{ id: 'recovery-line', product_name: product.name, quantity: 1, unit_price: order.amount_total, subtotal: order.amount_total, unit_label: '包', tax_type: 'tax_exempt' }],
  });
  const sockets = new Set(), pendingProviders = new Map();
  let base;
  const server = createServer((request, response) => {
    void (async () => {
      const url = new URL(request.url, base), path = url.pathname;
      assert.equal(url.origin, base);
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        assert.ok(size <= 1048576);
        chunks.push(chunk);
      }
      const send = (status, data, contentType = 'application/json') => {
        if (response.destroyed) return;
        response.writeHead(status, { 'Content-Type': contentType,
          'Content-Security-Policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; base-uri 'self'; form-action 'self'" });
        response.end(contentType === 'application/json' ? JSON.stringify(data) : data);
      };
      if (path === '/isolated-recovery-payment') {
        providerStarted();
        await new Promise(resolve => {
          const timer = setTimeout(() => { pendingProviders.delete(timer); resolve(); }, providerDelay);
          pendingProviders.set(timer, resolve);
        });
        return send(200, '<!doctype html><meta charset="utf-8"><h1>隔離付款頁</h1>', 'text/html; charset=utf-8');
      }
      if (!path.startsWith('/v1/')) {
        const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
        assert.ok(file.startsWith(`${root}/`));
        return send(200, await readFile(file), types[extname(file)] || 'application/octet-stream');
      }
      if (path === '/v1/auth/refresh') return send(200, { access_token: 'isolated-recovery', user: { id: 'recovery-buyer', email: 'buyer@example.com', display_name: '買家', membership_type: 'nonmember', user_role: 'customer' } });
      if (path === '/v1/platform') return send(200, { orders_open: true, closed_message: '暫停接單' });
      if (path === `/v1/products/${product.id}`) return send(200, product);
      if (path === '/v1/pickup-locations') return send(200, [{ id: 'pickup', name: '合作社門市', is_active: true }]);
      if (path === '/v1/orders/quote') return send(200, { confirmation_fingerprint: 'a'.repeat(64), amount_total: state.total, membership_type: 'nonmember', tax_amount: 0,
        items: [{ product_id: product.id, product_name: product.name, quantity: 1, unit_price: state.total, subtotal: state.total, tax_type: 'tax_exempt' }] });
      if (path === '/v1/orders' && request.method === 'POST') {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        state.creates.push(body);
        let order = state.orders.get(body.client_request_id);
        if (!order) {
          if (body.expected_total !== state.total || body.expected_quote_fingerprint !== 'a'.repeat(64)) return send(409, { detail: '價格已變動，請重新確認訂單明細' });
          order = { id: `recovery-${state.orders.size + 1}`, order_number: `SLF-RECOVERY-${state.orders.size + 1}`, payment_status: 'pending', amount_total: state.total };
          state.orders.set(body.client_request_id, order);
        }
        return send(201, order);
      }
      if (/^\/v1\/orders\/recovery-\d+\/payment-attempts$/.test(path)) {
        state.payments.push(path);
        return send(201, { id: 'recovery-payment', payment_url: `${base}/isolated-recovery-payment` });
      }
      if (/^\/v1\/orders\/recovery-\d+\/logistics\/selection-link$/.test(path)) {
        state.logistics.push(path);
        return send(201, { selection_url: `${base}/isolated-recovery-payment` });
      }
      if (path === '/v1/orders') { state.reads.push(path); return send(200, state.fullList ? [...state.orders.values()].map(view) : []); }
      if (path === '/v1/shipping-rates') return send(200, []);
      if (/^\/v1\/orders\/recovery-\d+$/.test(path) && request.method === 'GET') {
        assert.equal(url.searchParams.get('own_only'), 'true');
        const order = [...state.orders.values()].find(item => item.id === path.split('/').at(-1));
        state.reads.push(path);
        return send(order ? 200 : 404, order ? view(order) : { detail: '找不到本人訂單' });
      }
      state.unknown.push(`${request.method} ${path}`);
      return send(404, { detail: 'Undefined isolated route' });
    })().catch(error => { state.serverErrors.push(error); response.destroy(); });
  });
  server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); });
  t.after(async () => {
    for (const [timer, release] of pendingProviders) { clearTimeout(timer); release(); }
    pendingProviders.clear();
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('隔離HTTP server清理逾時')), 1000);
      server.close(error => { clearTimeout(timer); error && error.code !== 'ERR_SERVER_NOT_RUNNING' ? reject(error) : resolve(); });
      for (const socket of sockets) socket.destroy();
    });
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('隔離HTTP server啟動逾時')), 1000);
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.listen(0, '127.0.0.1', () => { clearTimeout(timer); resolve(); });
  });
  base = `http://127.0.0.1:${server.address().port}`;
  const context = await browser.newContext({ viewport: { width: 390, height: 1000 }, serviceWorkers: 'block' });
  await context.addInitScript(item => {
    if (!sessionStorage.getItem('recovery-seeded')) {
      localStorage.setItem('slf_cart_v2:user:recovery-buyer', JSON.stringify({ items: [{ product: item, quantity: 1 }] }));
      sessionStorage.setItem('recovery-seeded', '1');
    }
  }, product);
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  if (evidence) evidence.attach(page, () => state.errors);
  const strict = () => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.serverErrors, []); };
  t.after(evidence ? () => evidence.assertAndSave(strict) : strict);
  await page.goto(`${base}/checkout`);
  await page.locator('.summary-total dd').getByText('$100', { exact: true }).waitFor();
  const pay = () => page.getByRole('button', { name: /^(前往線上付款|重試原訂單)$/ });
  return { page, state, pay, base, providerReady };
}

test('價格變動二次確認後，慢速付款交接不得產生未處理錯誤或重複建單', { timeout: 45000 }, async t => {
  const { page, state, pay, base } = await fixture(t);
  state.total = 120;
  await pay().click();
  await page.getByText('價格已變動，請重新確認訂單明細', { exact: true }).waitFor();
  await page.locator('.summary-total dd').getByText('$120', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0);
  assert.equal(state.creates.length, 1);
  assert.equal(state.payments.length, 0);
  await pay().click();
  await page.waitForURL(`${base}/isolated-recovery-payment`);
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates.length, 2);
  assert.equal(state.creates[1].expected_total, 120);
  assert.match(state.creates[0].client_request_id, /^[0-9a-f-]{36}$/);
  assert.notEqual(state.creates[1].client_request_id, state.creates[0].client_request_id);
  assert.deepEqual(state.payments, ['/v1/orders/recovery-1/payment-attempts']);
});


test('付款後上一頁、下一頁與重整接續本人原單，不重複建單或付款', { timeout: 45000 }, async t => {
  const { page, state, pay, base } = await fixture(t, { payable: true });
  await pay().click();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  await page.goBack();
  await pay().waitFor();
  assert.equal(page.url(), `${base}/orders?order_id=recovery-1`);
  await page.goForward();
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
  await page.goBack();
  await pay().waitFor();
  await page.reload();
  await pay().waitFor();
  assert.equal(page.url(), `${base}/orders?order_id=recovery-1`);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates.length, 1);
  assert.deepEqual(state.payments, ['/v1/orders/recovery-1/payment-attempts']);
  assert.ok(state.reads.includes('/v1/orders/recovery-1'));
  assert.equal(await page.getByRole('dialog').count(), 0);
});

test('付款頁沒有完成導航時，可直接返回原單，不永久停止查詢', { timeout: 45000 }, async t => {
  const { page, state, pay, base, providerReady } = await fixture(t, { payable: true, providerDelay: 6000 });
  await pay().click({ noWaitAfter: true });
  await providerReady;
  await stopPendingChromiumNavigation(page);
  const back = page.getByRole('link', { name: '返回訂單', exact: true });
  assert.equal(await back.getAttribute('href'), '/orders?order_id=recovery-1');
  assert.deepEqual(state.reads, []);
  assert.equal(await back.isVisible(), true);
  await back.click({ force: true, noWaitAfter: true });
  await pay().waitFor();
  assert.equal(page.url(), `${base}/orders?order_id=recovery-1`);
  assert.ok(state.reads.includes('/v1/orders/recovery-1'));
  assert.equal(state.creates.length, 1);
  assert.equal(state.payments.length, 1);
});

for (const shipping of [false, true]) {
  test(`已在訂單頁選另一單後${shipping ? '重開物流' : '續付'}，返回連結必須指向這次操作的原單`, { timeout: 45000 }, async t => {
    const { page, state, base, providerReady } = await fixture(t, { payable: true, providerDelay: 6000, shipping });
    for (const number of [1, 2]) state.orders.set(`seed-${number}`, {
      id: `recovery-${number}`, order_number: `SLF-RECOVERY-${number}`, payment_status: 'pending', amount_total: 100,
    });
    state.fullList = true;
    await page.goto(`${base}/orders?order_id=recovery-1`);
    await page.locator('.order-index').getByRole('button', { name: /SLF-RECOVERY-2/ }).click();
    await page.getByRole('button', { name: shipping ? '繼續選擇物流' : '前往線上付款', exact: true }).click({ noWaitAfter: true });
    await providerReady;
    await stopPendingChromiumNavigation(page);
    const back = page.getByRole('link', { name: '返回訂單', exact: true });
    assert.equal(await back.getAttribute('href'), '/orders?order_id=recovery-2');
    assert.equal(await back.isVisible(), true);
    await back.click({ force: true, noWaitAfter: true });
    await page.locator('.order-index').getByRole('button', { name: /SLF-RECOVERY-2/ }).waitFor();
    await page.locator('.order-detail').getByText('SLF-RECOVERY-2', { exact: true }).waitFor();
    assert.equal(page.url(), `${base}/orders?order_id=recovery-2`);
    assert.equal(state.creates.length, 0);
    assert.deepEqual(state.payments, shipping ? [] : ['/v1/orders/recovery-2/payment-attempts']);
    assert.deepEqual(state.logistics, shipping ? ['/v1/orders/recovery-2/logistics/selection-link'] : []);
  });
}
