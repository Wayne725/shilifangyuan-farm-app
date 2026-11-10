import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local website');
const base = target.origin;
const product = { id: 'account-cart-product', name: '合成購物車白米', description: '', category: '米', unit: '包', member_price: 100, nonmember_price: 100, stock_quantity: 100, is_active: true, daily_sales_enabled: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
const user = id => ({ id, email: `${id}@cart.example.test`, display_name: `合成${id}`, user_role: 'customer', membership_type: 'nonmember', saved_mobile_barcode: null });
const key = id => id ? `slf_cart_v2:user:${id}` : 'slf_cart_v2:guest';
async function fixture(t, { owner = 'A', legacy = false } = {}) {
  const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 950 }, serviceWorkers: 'block' });
  if (legacy) await context.addInitScript(item => {
    if (!localStorage.getItem('slf_cart')) localStorage.setItem('slf_cart', JSON.stringify([{ product: item, quantity: 9 }]));
  }, product);
  const state = { owner, quotes: [], creates: [], unknown: [], errors: [] };
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: state.owner ? 200 : 401, json: state.owner ? { access_token: `synthetic-${state.owner}`, user: user(state.owner) } : { detail: '合成訪客' } });
    if (path === '/v1/auth/logout') { state.owner = null; return route.fulfill({ status: 204 }); }
    if (path === '/v1/auth/login') { state.owner = req.postDataJSON().email.split('@')[0]; return route.fulfill({ json: { access_token: `synthetic-${state.owner}`, user: user(state.owner) } }); }
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { enabled: false, ready: false } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/products') return route.fulfill({ json: [product] });
    if (path === `/v1/products/${product.id}`) return route.fulfill({ json: product });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'synthetic-pickup', name: '合成取貨點', is_active: true }] });
    if (path === '/v1/orders/quote') {
      const body = req.postDataJSON(); state.quotes.push({ owner: state.owner, body });
      const qty = body.items[0].quantity;
      return route.fulfill({ json: { confirmation_fingerprint: 'a'.repeat(64), membership_type: 'nonmember', tax_amount: 0, amount_total: qty * 100, items: [{ product_id: product.id, product_name: product.name, unit_price: 100, quantity: qty, subtotal: qty * 100, tax_type: 'tax_exempt' }] } });
    }
    if (path === '/v1/orders' && req.method() === 'POST') state.creates.push(req.postDataJSON());
    if (['/v1/meal-events', '/v1/group-campaigns', '/v1/activities', '/v1/notifications', '/v1/orders', '/v1/meal-orders'].includes(path)) return route.fulfill({ json: [] });
    state.unknown.push(`${req.method()} ${path}`); return route.fulfill({ status: 404, json: { detail: 'Undefined synthetic cart route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', error => state.errors.push(error.message));
  t.after(() => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.creates, []); });
  const visitProduct = async (tab = page) => { await tab.goto(`${base}/products/${product.id}`); await tab.getByRole('button', { name: `加入購物車：${product.name}`, exact: true }).waitFor(); };
  const add = async (quantity, tab = page) => {
    await visitProduct(tab);
    await tab.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption(String(quantity));
    await tab.getByRole('button', { name: `加入購物車：${product.name}`, exact: true }).click();
    await tab.getByRole('status').filter({ hasText: '已加入' }).waitFor();
  };
  const logout = async () => { await page.getByRole('button', { name: '開啟導覽', exact: true }).click(); await page.getByRole('button', { name: `登出 合成${state.owner}`, exact: true }).click(); await page.locator('a[aria-label^="會員中心：合成"]').waitFor({ state: 'detached' }); };
  return { page, context, state, add, logout };
}

test('390px A登出後B看不到也不會取得A報價，A再登入恢復原cart', async t => {
  const { page, state, add, logout } = await fixture(t);
  await add(2); await logout();
  await page.goto(`${base}/checkout`); await page.getByRole('heading', { name: '結帳清單還是空的', exact: true }).waitFor();
  state.owner = 'B'; await page.reload(); await page.locator('a[aria-label="會員中心：合成B"]').waitFor({ state: 'attached' });
  await page.getByRole('heading', { name: '結帳清單還是空的', exact: true }).waitFor();
  assert.equal(state.quotes.some(row => row.owner === 'B'), false);
  await add(1); await page.goto(`${base}/checkout`); await page.locator('.summary-total dd').getByText('$100', { exact: true }).waitFor();
  assert.equal(state.quotes.at(-1).owner, 'B'); assert.equal(state.quotes.at(-1).body.items[0].quantity, 1);
  await logout(); state.owner = 'A'; await page.goto(`${base}/checkout`); await page.locator('.summary-total dd').getByText('$200', { exact: true }).waitFor();
  assert.equal(state.quotes.at(-1).owner, 'A'); assert.equal(state.quotes.at(-1).body.items[0].quantity, 2);
});
test('訪客加入後經Email登入接續本人結帳，重整不重複加入', async t => {
  const { page, state, add } = await fixture(t, { owner: null });
  await add(3); await page.goto(`${base}/login?return_to=%2Fcheckout`);
  await page.getByLabel('電子信箱', { exact: true }).fill('A@cart.example.test'); await page.getByLabel('密碼', { exact: true }).fill('synthetic-password');
  await page.getByRole('button', { name: '登入系統', exact: true }).click(); await page.waitForURL(`${base}/checkout`);
  await page.locator('.summary-total dd').getByText('$300', { exact: true }).waitFor();
  await page.reload(); await page.locator('.summary-total dd').getByText('$300', { exact: true }).waitFor();
  assert.equal(state.quotes.at(-1).body.items[0].quantity, 3);
  assert.deepEqual(await page.evaluate(k => JSON.parse(localStorage.getItem(k)).items, key(null)), []);
});
test('舊無主cart保留在本機，A與B登入均不會自動顯示或計價', async t => {
  const { page, state } = await fixture(t, { legacy: true });
  await page.goto(`${base}/checkout`); await page.getByRole('heading', { name: '結帳清單還是空的', exact: true }).waitFor();
  state.owner = 'B'; await page.reload(); await page.getByRole('heading', { name: '結帳清單還是空的', exact: true }).waitFor();
  assert.deepEqual(state.quotes, []);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 9);
});
test('同帳號兩分頁同步數量，另一帳號cart異動不污染本頁', async t => {
  const { page, context, add } = await fixture(t);
  await add(2); await page.goto(`${base}/checkout`); await page.locator('.summary-total dd').getByText('$200', { exact: true }).waitFor();
  const other = await context.newPage(); await add(1, other);
  await page.locator('.summary-total dd').getByText('$300', { exact: true }).waitFor();
  await other.evaluate(({ k, item }) => localStorage.setItem(k, JSON.stringify({ items: [{ product: item, quantity: 8 }] })), { k: key('B'), item: product });
  assert.equal(await page.locator('.summary-total dd').getByText('$300', { exact: true }).count(), 1);
  await other.close();
});
