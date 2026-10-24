import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4195');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const product = { id: 'feedback-product', name: '隔離測試黑豆蔭油', description: '隔離測試商品', category: '合作社選品', unit: '瓶', supplier_name: '隔離合作社', member_price: 300, nonmember_price: 320, stock_quantity: 20, is_active: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'taxable' };
const soldOut = { ...product, id: 'sold-out-product', name: '隔離缺貨商品', stock_quantity: 0 };

async function fixture(t, width, path) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  await context.addInitScript((item) => {
    if (!localStorage.getItem('slf_cart')) localStorage.setItem('slf_cart', JSON.stringify([{ product: item, quantity: 2 }]));
  }, product);
  const state = { unknown: [], errors: [] };
  await context.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: 401, json: { detail: '未登入的隔離購物者' } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/products') return route.fulfill({ json: [product, soldOut] });
    if (path === `/v1/products/${product.id}`) return route.fulfill({ json: product });
    if (path === `/v1/products/${soldOut.id}`) return route.fulfill({ json: soldOut });
    if (['/v1/meal-events', '/v1/group-campaigns'].includes(path)) return route.fulfill({ json: [] });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.install();
  await page.goto(`${base}${path}`);
  if (path === '/shop') await page.getByRole('tab', { name: '日常選品', exact: true }).click();
  return { page, state };
}

for (const width of [1440, 390]) for (const detail of [false, true]) {
  test(`${width}px ${detail ? '商品詳情' : '商品卡片'}加入立即回饋，快速多次累加正確且成功結果會消失`, async (t) => {
    const { page, state } = await fixture(t, width, detail ? `/products/${product.id}` : '/shop');
    const scope = detail ? page.locator('.product-detail-copy') : page.locator('.product-card').filter({ has: page.getByRole('heading', { name: product.name, exact: true }) });
    const button = scope.getByRole('button', { name: detail ? '加入結帳清單' : `加入 ${product.name}`, exact: true });
    const result = scope.getByRole('status');
    await button.waitFor();
    assert.equal(await result.textContent(), '');
    await button.focus();
    await button.press('Enter');
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('slf_cart')).find((row) => row.product.id === 'feedback-product').quantity === 3);
    assert.equal(await result.textContent(), `${product.name}已加入，清單共 3 瓶`);
    assert.equal(await button.evaluate((element) => document.activeElement === element), true, '回饋不可搶走鍵盤焦點');
    assert.equal(await result.getAttribute('aria-live'), 'polite');
    assert.equal(await result.getAttribute('aria-atomic'), 'true');
    await page.clock.fastForward(2000);
    await button.evaluate((element) => { element.click(); element.click(); element.click(); });
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('slf_cart')).find((row) => row.product.id === 'feedback-product').quantity === 6);
    assert.equal(await result.textContent(), `${product.name}已加入，清單共 6 瓶`);
    await page.clock.fastForward(2000);
    assert.equal(await result.textContent(), `${product.name}已加入，清單共 6 瓶`, '再加入後應重設消失時間');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.BROWSER_SCREENSHOT_DIR && width === 390) {
      await button.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/商品加入回饋-${detail ? '詳情' : '卡片'}-手機.png` });
    }
    await page.clock.fastForward(1001);
    assert.equal(await result.textContent(), '');
    await page.reload();
    if (!detail) await page.getByRole('tab', { name: '日常選品', exact: true }).click();
    await button.waitFor();
    assert.equal(await result.textContent(), '', '成功回饋不應跨頁面重整保留');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart')).find((row) => row.product.id === 'feedback-product').quantity), 6);
    if (!detail) {
      const disabled = page.getByRole('button', { name: `加入 ${soldOut.name}`, exact: true });
      assert.equal(await disabled.isDisabled(), true);
      assert.equal(await disabled.locator('..').getByRole('status').textContent(), '');
    }
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

test('缺貨詳情保留禁用，切換商品不沿用另一商品加入成功結果', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`);
  await page.getByRole('button', { name: '加入結帳清單', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '清單共 3 瓶' }).waitFor();
  await page.goto(`${base}/products/${soldOut.id}`);
  const disabled = page.getByRole('button', { name: '目前缺貨', exact: true });
  await disabled.waitFor();
  assert.equal(await disabled.isDisabled(), true);
  assert.equal(await page.locator('.cart-add-feedback').textContent(), '');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart')).length), 1);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('首次儲存失敗不改數量或顯示成功，重試成功只增加一份', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`);
  const button = page.getByRole('button', { name: '加入結帳清單', exact: true });
  const result = page.locator('.cart-add-feedback');
  await button.waitFor();
  await page.evaluate(() => {
    const set_item = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (this === localStorage && key === 'slf_cart') {
        Storage.prototype.setItem = set_item;
        throw new DOMException('隔離測試儲存失敗', 'QuotaExceededError');
      }
      return set_item.call(this, key, value);
    };
  });
  const storage_error = page.waitForEvent('pageerror');
  await button.click();
  assert.match((await storage_error).message, /隔離測試儲存失敗/);
  assert.equal(await result.textContent(), '');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 2);
  await button.click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity === 3);
  assert.equal(await result.textContent(), `${product.name}已加入，清單共 3 瓶`);
  assert.deepEqual(state.unknown, []);
  assert.equal(state.errors.length, 1, '只應有刻意注入的首次儲存錯誤');
});
