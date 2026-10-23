import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || !['http:', 'https:'].includes(target.protocol)) throw new Error('Only isolated local storefront tests are allowed');
const base = target.origin;
const product = { id: 'scope-product', slug: 'scope-product', name: '範圍測試農產', category: '蔬果', unit: '袋', member_price: 90, nonmember_price: 100, stock_quantity: 20, can_ship: true, shipping_temperature: 'ambient', allowed_shipping_channels: ['home_delivery'], is_active: true };
const pickup = { orders_open: true, closed_message: '暫停接單', sales_scope: 'pickup', regular_orders_enabled: true, group_orders_enabled: true, shipping_enabled: false };
const mealsOnly = { ...pickup, sales_scope: 'meals_only', regular_orders_enabled: false, group_orders_enabled: false };

async function fixture(t, { platform = pickup, path = '/checkout', admin = false, delayed = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 900 }, serviceWorkers: 'block' });
  await context.addInitScript((product) => localStorage.setItem('slf_cart', JSON.stringify([{ product, quantity: 1 }])), product);
  const writes = [], errors = [], unknown = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    const path = url.pathname;
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-scope', user: { id: 'buyer', email: 'buyer@example.com', display_name: '隔離使用者', user_role: admin ? 'admin' : 'customer', membership_type: 'nonmember' } } });
    if (path === '/v1/platform') { if (delayed) await gate; return route.fulfill({ json: platform }); }
    if (path === '/v1/admin/platform') return route.fulfill({ json: { ...platform, printer_brand: '', printer_model: '', printer_connection: 'unknown', kitchen_sound: false } });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'store', name: '隔離自取點', is_active: true }] });
    if (path === '/v1/shipping-rates') return route.fulfill({ json: [] });
    if (path === '/v1/orders/quote') return route.fulfill({ json: { amount_total: 100, membership_type: 'nonmember', items: [{ product_id: product.id, product_name: product.name, quantity: 1, unit_price: 100, subtotal: 100 }] } });
    if (path === '/v1/group-campaigns/scope-group') return route.fulfill({ json: { id: 'scope-group', title: '隔離農產團購', description: '', member_price: 90, nonmember_price: 100, intake_status: 'open', decision_status: 'recruiting', paid_quantity: 0, min_paid_quantity: 5, supply_cap: 20, per_user_cap: 5, available_quantity: 20, deadline: '2026-11-01T04:00:00Z', estimated_pickup_start: '2026-11-02T04:00:00Z', can_ship: true, shipping_temperature: 'ambient', allowed_shipping_channels: ['home_delivery'] } });
    if (path === '/v1/group-campaigns/scope-group/quote') return route.fulfill({ json: { quantity: 1, unit_price: 100, product_subtotal: 100, shipping_fee: 0, amount_total: 100, membership_type: 'nonmember' } });
    if (request.method() !== 'GET') writes.push(path);
    unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(8000);
  await page.goto(`${base}${path}`);
  return { page, writes, errors, unknown, release };
}

for (const [path, button, blockedMessage] of [
  ['/checkout', '前往線上付款', '目前未開放一般商品訂購'],
  ['/groups/scope-group', '參加並前往付款', '目前未開放團購訂購'],
]) {
  test(`${path} 自取能力已開放時可付款，不呈現配送選項`, async t => {
    const { page, writes, errors, unknown } = await fixture(t, { path });
    const submit = page.getByRole('button', { name: button, exact: true });
    await submit.waitFor();
    await page.waitForFunction(text => [...document.querySelectorAll('button')].some(button => button.textContent.trim() === text && !button.disabled), button);
    assert.equal(await page.getByText('宅配／超商取貨', { exact: true }).count(), 0);
    assert.deepEqual(writes, []); assert.deepEqual(errors, []); assert.deepEqual(unknown, []);
  });
  test(`${path} 受限制時顯示原因並阻止建單`, async t => {
    const { page, writes, errors, unknown } = await fixture(t, { path, platform: mealsOnly });
    await page.getByText(blockedMessage, { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: button, exact: true }).isDisabled(), true);
    assert.deepEqual(writes, []); assert.deepEqual(errors, []); assert.deepEqual(unknown, []);
  });
}

test('舊 API 與延遲回應不誤封鎖；明確能力到達後立刻停止建立新單', async t => {
  const { page, writes, errors, unknown, release } = await fixture(t, { platform: mealsOnly, delayed: true });
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent.trim() === '前往線上付款' && !button.disabled));
  release();
  await page.getByText('目前未開放一般商品訂購', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isDisabled(), true);
  assert.deepEqual(writes, []); assert.deepEqual(errors, []); assert.deepEqual(unknown, []);
});

test('既有便當限定驗收商品仍能以單件自取結帳', async t => {
  const { page, writes, errors, unknown } = await fixture(t, { platform: { ...mealsOnly, limited_product_ids: [product.id] } });
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent.trim() === '前往線上付款' && !button.disabled));
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.getByText('目前未開放一般商品訂購', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isDisabled(), true);
  assert.deepEqual(writes, []); assert.deepEqual(errors, []); assert.deepEqual(unknown, []);
});

test('後台同步顯示真正銷售範圍與配送能力', async t => {
  const { page, writes, errors, unknown } = await fixture(t, { path: '/admin/settings', admin: true });
  await page.getByText('一般商品、團購、便當自取', { exact: true }).waitFor();
  assert.equal(await page.getByText('配送政策', { exact: true }).locator('..').getByText('未開放', { exact: true }).count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(writes, []); assert.deepEqual(errors, []); assert.deepEqual(unknown, []);
});
