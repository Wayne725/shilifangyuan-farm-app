import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4239';
const root = resolve(import.meta.dirname, '../../web/dist');
const product = { id: 'recovery-product', name: '回復測試白米', unit: '包', member_price: 100, nonmember_price: 100, stock_quantity: 50, is_active: true, daily_sales_enabled: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg' };

async function fixture(t, { loseResponse = false, holdHandoff = false, stock = 50, stockStatus = 200, enforceStock = false, extraProducts = [], clock = false } = {}) {
  const evidence = process.env.CHECKOUT_EVENT_EVIDENCE_DIR
    ? (await import('./checkout-event-evidence.mjs')).createCheckoutEventEvidence({
      directory: process.env.CHECKOUT_EVENT_EVIDENCE_DIR, testName: t.name, engine: process.env.BROWSER_ENGINE || 'chromium',
    }) : null;
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(evidence ? () => evidence.close(() => browser.close()) : () => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 1000 }, serviceWorkers: 'block' });
  await context.addInitScript(items => {
    if (!sessionStorage.getItem('recovery-seeded')) {
      localStorage.setItem('slf_cart_v2:user:recovery-buyer', JSON.stringify({ items }));
      sessionStorage.setItem('recovery-seeded', '1');
    }
  }, [{ product, quantity: 1 }, ...extraProducts.map(item => ({ product: item, quantity: 1 }))]);
  const products = new Map([product, ...extraProducts].map(item => [item.id, item]));
  const state = { total: 100, fingerprint: 'a'.repeat(64), open: true, loseResponse, creates: [], orders: new Map(), payments: [], errors: [], unknown: [],
    stock, stockStatus, stockReads: [], quotes: [], holdStockRead: false };
  let releaseStock;
  const stockGate = new Promise(resolve => { releaseStock = resolve; });
  state.releaseStock = releaseStock;
  t.after(releaseStock);
  let handoffStarted;
  state.handoffStarted = new Promise(resolve => { handoffStarted = resolve; });
  let releaseHandoff;
  const handoffGate = new Promise(resolve => { releaseHandoff = resolve; });
  state.releaseHandoff = releaseHandoff;
  t.after(releaseHandoff);
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-recovery-payment') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><meta charset="utf-8"><h1>隔離付款頁</h1>' });
    if (holdHandoff && /OrdersPage-[^/]+\.js$/.test(path)) { handoffStarted(); await handoffGate; }
    if (path === '/v1/auth/logout') return route.fulfill({ status: 204, body: '' });
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: types[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-recovery', user: { id: 'recovery-buyer', email: 'buyer@example.com', display_name: '買家', membership_type: 'nonmember', user_role: 'customer' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: state.open, closed_message: '暫停接單' } });
    if (path.startsWith('/v1/products/')) {
      const id = path.split('/').at(-1), item = products.get(id);
      assert.ok(item);
      state.stockReads.push(id);
      if (state.holdStockRead) await stockGate;
      if (id !== product.id) return route.fulfill({ json: item });
      return state.stockStatus === 200
        ? route.fulfill({ json: { ...product, stock_quantity: state.stock } })
        : route.fulfill({ status: state.stockStatus, json: { detail: '隔離庫存狀態' } });
    }
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'pickup', name: '合作社門市', is_active: true }] });
    if (path === '/v1/orders/quote') {
      const requested = request.postDataJSON().items;
      state.quotes.push(requested);
      if (enforceStock) {
        const rejected = requested.find(({ product_id, quantity }) => {
          const item = products.get(product_id);
          return !item.daily_sales_enabled || quantity > (product_id === product.id ? state.stock : item.stock_quantity);
        });
        if (rejected) return route.fulfill({ status: 409, json: { detail: '庫存已變動，請調整數量' } });
      }
      const items = requested.map(({ product_id, quantity }) => ({ product_id, product_name: products.get(product_id).name, quantity, unit_price: state.total, subtotal: state.total * quantity, tax_type: 'tax_exempt' }));
      return route.fulfill({ json: { confirmation_fingerprint: state.fingerprint, amount_total: items.reduce((sum, item) => sum + item.subtotal, 0), membership_type: 'nonmember', tax_amount: 0, items } });
    }
    if (path === '/v1/orders' && request.method() === 'POST') {
      const body = request.postDataJSON();
      state.creates.push(body);
      const key = body.client_request_id || `missing-${state.creates.length}`;
      let order = state.orders.get(key);
      if (!order) {
        if (!state.open) return route.fulfill({ status: 409, json: { detail: '暫停接單' } });
        if ((body.expected_total !== undefined && body.expected_total !== state.total)
          || (body.expected_quote_fingerprint && body.expected_quote_fingerprint !== state.fingerprint)) return route.fulfill({ status: 409, json: { detail: '價格已變動，請重新確認訂單明細' } });
        order = { id: `recovery-${state.orders.size + 1}`, order_number: `SLF-RECOVERY-${state.orders.size + 1}`, payment_status: 'pending', amount_total: state.total };
        state.orders.set(key, order);
      }
      if (state.loseResponse) { state.loseResponse = false; return route.abort('failed'); }
      return route.fulfill({ status: 201, json: order });
    }
    if (/^\/v1\/orders\/recovery-\d+\/payment-attempts$/.test(path)) {
      state.payments.push(path);
      return route.fulfill({ status: 201, json: { id: 'recovery-payment', payment_url: `${base}/isolated-recovery-payment` } });
    }
    if (path === '/v1/orders' || path === '/v1/shipping-rates') return route.fulfill({ json: [] });
    if (/^\/v1\/orders\/recovery-\d+$/.test(path) && request.method() === 'GET') {
      assert.equal(url.searchParams.get('own_only'), 'true');
      const order = [...state.orders.values()].find(item => item.id === path.split('/').at(-1));
      return route.fulfill({ status: order ? 200 : 404, json: order ? { ...order, sales_channel: 'regular',
        created_at: '2030-01-02T01:00:00Z', fulfillment_method: 'cooperative_pickup', fulfillment_status: 'pending_confirmation',
        invoice_status: 'not_eligible', available_actions: [], items: [{ id: 'recovery-line', product_name: product.name,
          quantity: 1, unit_price: order.amount_total, subtotal: order.amount_total, unit_label: '包', tax_type: 'tax_exempt' }],
      } : { detail: '找不到本人訂單' } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  if (clock) await page.clock.install();
  page.on('pageerror', error => state.errors.push(error.message));
  if (evidence) evidence.attach(page, () => state.errors);
  await page.goto(`${base}/checkout`);
  if (extraProducts.length) await page.locator('.checkout-item').first().waitFor();
  else await page.locator('.summary-total dd').getByText('$100', { exact: true }).waitFor();
  const pay = () => page.getByRole('button', { name: /^(前往線上付款|重試原訂單)$/ });
  const assertFixtureErrors = () => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); };
  t.after(evidence ? () => evidence.assertAndSave(assertFixtureErrors) : assertFixtureErrors);
  return { page, state, pay };
}

async function paymentPageReady(page) {
  await page.waitForURL(`${base}/isolated-recovery-payment`);
  await page.getByRole('heading', { name: '隔離付款頁', exact: true }).waitFor();
}

test('庫存競爭後立即重查，限制加量且保留減量及付款', async t => {
  const { page, state, pay } = await fixture(t, { stock: 2, enforceStock: true });
  const increase = page.getByRole('button', { name: '增加數量', exact: true });
  await page.waitForFunction(() => !document.querySelector('.checkout-item [aria-label=增加數量]')?.disabled);
  state.stock = 1;
  await increase.click();
  await page.getByText('庫存已變動，請調整數量', { exact: true }).waitFor();
  await page.getByText('目前最多可買 1 包', { exact: true }).waitFor();
  assert.equal(await increase.isDisabled(), true);
  assert.equal(await pay().isDisabled(), true);
  assert.ok(state.stockReads.length >= 2);
  assert.ok(state.quotes.some(items => items[0].quantity === 2));
  await page.getByRole('button', { name: '減少數量', exact: true }).click();
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.creates.length, 1);
  assert.deepEqual(state.creates[0].items, [{ product_id: product.id, quantity: 1 }]);
  assert.equal(state.creates[0].expected_total, 100);
});

test('每筆商品獨立顯示庫存、缺貨與停售，可移除阻擋品項', async t => {
  const soldOut = { ...product, id: 'out-of-stock', name: '已缺貨米', stock_quantity: 0 };
  const discontinued = { ...product, id: 'not-daily', name: '停止日常銷售米', daily_sales_enabled: false };
  const { page, state, pay } = await fixture(t, { stock: 1, enforceStock: true, extraProducts: [soldOut, discontinued] });
  const row = name => page.locator('.checkout-item').filter({ has: page.getByRole('heading', { name, exact: true }) });
  for (const [item, message] of [[product, '目前最多可買 1 包'], [soldOut, '目前缺貨'], [discontinued, '目前停止販售']]) {
    await row(item.name).getByText(message, { exact: true }).waitFor();
    assert.equal(await row(item.name).getByRole('button', { name: '增加數量', exact: true }).isDisabled(), true);
  }
  await row(soldOut.name).getByRole('button', { name: '移除商品', exact: true }).click();
  await row(discontinued.name).getByRole('button', { name: '移除商品', exact: true }).click();
  assert.equal(await page.locator('.checkout-item').count(), 1);
  await page.locator('.summary-total dd').getByText('$100', { exact: true }).waitFor();
  assert.equal(await pay().isEnabled(), true);
  assert.deepEqual(state.creates, []);
});

test('庫存讀取失敗時停用加量，報價仍可付款並可單獨重查', async t => {
  const { page, state, pay } = await fixture(t, { stockStatus: 503 });
  const increase = page.getByRole('button', { name: '增加數量', exact: true });
  const retry = page.getByRole('button', { name: '重查庫存', exact: true });
  await retry.waitFor();
  assert.equal(await increase.isDisabled(), true);
  assert.equal(await pay().isEnabled(), true);
  state.stock = 1;
  state.stockStatus = 200;
  await retry.click();
  await page.getByText('目前最多可買 1 包', { exact: true }).waitFor();
  assert.equal(await increase.isDisabled(), true);
  assert.equal(await pay().isEnabled(), true);
});

test('過期庫存等候新回應時停用加量，既有商品及付款維持', async t => {
  const { page, state, pay } = await fixture(t, { stock: 2, clock: true });
  const increase = page.getByRole('button', { name: '增加數量', exact: true });
  await page.waitForFunction(() => !document.querySelector('.checkout-item [aria-label=增加數量]')?.disabled);
  state.holdStockRead = true;
  state.stock = 1;
  await page.clock.fastForward(31_000);
  await page.getByText('庫存待確認', { exact: true }).waitFor();
  assert.equal(await increase.isDisabled(), true);
  assert.equal(state.stockReads.length, 1);
  await page.getByRole('button', { name: '重查庫存', exact: true }).click();
  await page.getByText('正在確認庫存', { exact: true }).waitFor();
  assert.equal(await increase.isDisabled(), true);
  assert.equal(await pay().isEnabled(), true);
  state.releaseStock();
  await page.getByText('目前最多可買 1 包', { exact: true }).waitFor();
  assert.equal(await increase.isDisabled(), true);
  assert.equal(await pay().isEnabled(), true);
});

test('加量時以最新購物車數量檢查上限，畫面更新前不會加過庫存', async t => {
  const { page } = await fixture(t, { stock: 2 });
  const increase = page.getByRole('button', { name: '增加數量', exact: true });
  await page.waitForFunction(() => !document.querySelector('.checkout-item [aria-label=增加數量]')?.disabled);
  const quantity = await increase.evaluate(button => {
    const key = 'slf_cart_v2:user:recovery-buyer';
    const cart = JSON.parse(localStorage.getItem(key));
    cart.items[0].quantity = 2;
    localStorage.setItem(key, JSON.stringify(cart));
    button.click();
    return JSON.parse(localStorage.getItem(key)).items[0].quantity;
  });
  assert.equal(quantity, 2);
  await page.getByRole('alert').getByText('此商品目前可購上限為 2 包', { exact: true }).waitFor();
});

test('建單已接受但回應遺失，重整後重送仍只有一單並可付款', async t => {
  const { page, state, pay } = await fixture(t, { loseResponse: true });
  await pay().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  await page.reload();
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates.length, 2);
  assert.match(state.creates[0].client_request_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(state.creates[1], state.creates[0]);
  assert.equal(state.creates[0].expected_total, 100);
  assert.equal(state.creates[0].expected_quote_fingerprint, 'a'.repeat(64));
  assert.deepEqual(state.payments, ['/v1/orders/recovery-1/payment-attempts']);
});

test('價格變動先更新明細，必須再次點確認才建立新價訂單', async t => {
  const { page, state, pay } = await fixture(t);
  state.total = 120;
  await pay().click();
  await page.getByText('價格已變動，請重新確認訂單明細', { exact: true }).waitFor();
  await page.locator('.summary-total dd').getByText('$120', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0);
  assert.equal(state.creates.length, 1);
  assert.equal(state.payments.length, 0);
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates[1].expected_total, 120);
  assert.notEqual(state.creates[1].client_request_id, state.creates[0].client_request_id);
});

test('總額相同但品項單價或稅別已變，仍須重新確認', async t => {
  const { page, state, pay } = await fixture(t);
  state.fingerprint = 'b'.repeat(64);
  await pay().click();
  await page.getByText('價格已變動，請重新確認訂單明細', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0);
  assert.equal(state.creates[0].expected_quote_fingerprint, 'a'.repeat(64));
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates[1].expected_quote_fingerprint, 'b'.repeat(64));
  assert.notEqual(state.creates[1].client_request_id, state.creates[0].client_request_id);
});

test('未知建單結果後購物車另加商品，恢復舊單不清除後來的選購', async t => {
  const { page, state, pay } = await fixture(t, { loseResponse: true });
  await pay().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  await page.evaluate(item => localStorage.setItem('slf_cart_v2:user:recovery-buyer', JSON.stringify({ items: [{ product: item, quantity: 2 }] })), product);
  await page.reload();
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates[1].items[0].quantity, 1);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart_v2:user:recovery-buyer')).items[0].quantity), 2);
});

test('重整後已關店仍可恢復先前接受的同一單', async t => {
  const { page, state, pay } = await fixture(t, { loseResponse: true });
  await pay().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  state.open = false;
  await page.reload();
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.orders.size, 1);
  assert.deepEqual(state.creates[1], state.creates[0]);
});

test('瀏覽器暫時無法保存送單時先凍結明細，恢復權限後才送原單', async t => {
  const { page, state, pay } = await fixture(t);
  await page.evaluate(() => {
    window.blockCheckoutStorage = true;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (this === window.sessionStorage && key.startsWith('slf_checkout_submission:') && window.blockCheckoutStorage) {
        throw new DOMException('Synthetic storage quota failure', 'QuotaExceededError');
      }
      return setItem.call(this, key, value);
    };
  });
  await pay().click();
  await page.getByText('瀏覽器無法儲存送單識別，請允許網站儲存資料後重試', { exact: true }).waitFor();
  assert.equal(state.creates.length, 0);
  await page.locator('.checkout-recovery .summary-total dd').getByText('$100', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '增加數量', exact: true }).count(), 0);
  await page.evaluate(() => { window.blockCheckoutStorage = false; });
  await pay().click();
  await paymentPageReady(page);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates.length, 1);
  assert.equal(state.creates[0].expected_total, 100);
  assert.equal(state.creates[0].items[0].quantity, 1);
  assert.deepEqual(state.payments, ['/v1/orders/recovery-1/payment-attempts']);
});

test('交接頁載入期間登出，完成後不開啟前帳號付款且保留恢復識別', async t => {
  const { page, state, pay } = await fixture(t, { holdHandoff: true });
  const click = pay().click();
  let timeout;
  await Promise.race([
    state.handoffStarted,
    new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('交接未載入預期的訂單頁 chunk')), 6000); }),
  ]).finally(() => clearTimeout(timeout));
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('button', { name: '登出 買家', exact: true }).click();
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('button', { name: '帳號登入', exact: true }).waitFor();
  state.releaseHandoff();
  await click;
  await page.getByRole('heading', { name: '登入後查看訂單進度。', exact: true }).waitFor();
  await page.waitForURL(`${base}/orders?order_id=recovery-1`);
  assert.notEqual(page.url(), `${base}/isolated-recovery-payment`);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates.length, 1);
  assert.equal(state.payments.length, 1);
  const saved = await page.evaluate(() => JSON.parse(sessionStorage.getItem('slf_checkout_submission:recovery-buyer')));
  assert.equal(saved.requestId, state.creates[0].client_request_id);
});
