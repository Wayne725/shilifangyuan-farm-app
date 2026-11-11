import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/') throw new Error('Only isolated local checkout is allowed');
const base = target.origin;
const product = { id: 'pickup-product', name: '隔離測試蔭油', unit: '瓶', supplier_name: '隔離合作社', member_price: 300, nonmember_price: 300, stock_quantity: 20, is_active: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
const locations = [
  { id: 'store', name: '合作社門市（水木書苑內左側）', is_active: true },
  { id: 'campus', name: '教育學院大樓', is_active: true },
  { id: 'innovation', name: '創新育成大樓', is_active: true },
];

async function fixture(t, { width = 960, large = false, state = 'ready' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' });
  await context.addInitScript((item) => localStorage.setItem('slf_cart_v2:user:buyer', JSON.stringify({ items: [{ product: item, quantity: 1 }] })), product);
  const unknown = [], writes = [];
  let releaseLocations;
  const locationGate = new Promise((resolve) => { releaseLocations = resolve; });
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', email: 'buyer@example.test', display_name: '隔離買家', user_role: 'customer', membership_type: 'nonmember' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/shipping-rates') return route.fulfill({ json: [] });
    if (path === '/v1/orders/quote') return route.fulfill({ json: { amount_total: 300, membership_type: 'nonmember', items: [{ product_id: product.id, product_name: product.name, quantity: 1, unit_price: 300, subtotal: 300, tax_type: 'tax_exempt' }] } });
    if (path === '/v1/pickup-locations') {
      if (state === 'loading') await locationGate;
      return route.fulfill({ status: state === 'error' ? 503 : 200, json: state === 'error' ? { detail: '隔離錯誤' } : state === 'empty' ? [] : locations });
    }
    if (request.method() !== 'GET') writes.push(path);
    unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  await page.goto(`${base}/checkout`);
  await page.locator('.summary-total dd').getByText('$300', { exact: true }).waitFor();
  if (large) {
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    await page.locator('.reading-menu > summary').click();
  }
  return { page, unknown, writes, releaseLocations };
}

for (const [width, large] of [[1440, false], [960, false], [390, false], [960, true], [390, true]]) {
  test(`${width}px ${large ? '放大' : '標準'}：取貨選單與摘要同步，長地點不超出畫面`, async (t) => {
    const { page, unknown, writes } = await fixture(t, { width, large });
    const place = page.getByRole('combobox', { name: '取貨地點', exact: true });
    await page.waitForFunction(() => document.querySelector('.checkout-fulfillment select[required]')?.value === 'store');
    await place.selectOption('campus');
    assert.equal(await page.locator('.summary-lines').getByText('教育學院大樓', { exact: true }).count(), 1);
    await place.selectOption('store');
    const boxes = await page.locator('.checkout-fulfillment select').evaluateAll((elements) => elements.map((element) => {
      const box = element.getBoundingClientRect(), parent = element.parentElement.getBoundingClientRect();
      return { contained: box.left >= parent.left && box.right <= parent.right + 1, height: box.height };
    }));
    assert.ok(boxes.every((box) => box.contained && box.height >= 44));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await page.locator('.summary-pickup dt').evaluate((element) => (() => { const range = document.createRange(); range.selectNodeContents(element); return range.getClientRects().length === 1; })()), true);
    assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isEnabled(), true);
    assert.deepEqual(unknown, []);
    assert.deepEqual(writes, []);
    if (process.env.PICKUP_SCREENSHOT_DIR && width === 960 && !large) {
      await place.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${process.env.PICKUP_SCREENSHOT_DIR}/checkout-pickup-local.png` });
    }
  });
}

for (const state of ['loading', 'empty', 'error']) {
  test(`取貨地點${state}時停用選單及付款，不建立訂單`, async (t) => {
    const { page, unknown, writes, releaseLocations } = await fixture(t, { state });
    const place = page.getByRole('combobox', { name: '取貨地點', exact: true });
    if (state === 'error') await page.getByRole('heading', { name: '取貨點暫時無法使用', exact: true }).waitFor();
    assert.equal(await place.isEnabled(), false);
    assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isEnabled(), false);
    assert.deepEqual(unknown, []);
    assert.deepEqual(writes, []);
    releaseLocations();
  });
}
