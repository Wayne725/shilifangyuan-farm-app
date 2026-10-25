import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4195');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const product = { id: 'feedback-product', name: '隔離測試黑豆蔭油', description: '隔離測試商品', category: '合作社選品', unit: '瓶', supplier_name: '隔離合作社', member_price: 300, nonmember_price: 320, stock_quantity: 20, is_active: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'taxable' };
const soldOut = { ...product, id: 'sold-out-product', name: '隔離缺貨商品', stock_quantity: 0 };

async function fixture(t, width, path, { stock = 20, initialStock = 100, initialQuantity = 2, signedIn = false, membershipType = 'nonmember', quoteError = false, quotePrice = 315 } = {}) {
  const item = { ...product, stock_quantity: stock };
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  await context.addInitScript(({ item, initialStock, initialQuantity }) => {
    if (!localStorage.getItem('slf_cart')) localStorage.setItem('slf_cart', JSON.stringify([{ product: { ...item, stock_quantity: initialStock }, quantity: initialQuantity }]));
  }, { item, initialStock, initialQuantity });
  const state = { unknown: [], errors: [], quotes: [] };
  await context.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? { access_token: 'isolated-cart', user: { id: 'cart-buyer', email: 'buyer@example.test', display_name: '隔離購物者', user_role: 'customer', membership_type: membershipType } } : { detail: '未登入的隔離購物者' } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/products') return route.fulfill({ json: [item, soldOut] });
    if (path === `/v1/products/${product.id}`) return route.fulfill({ json: item });
    if (path === `/v1/products/${soldOut.id}`) return route.fulfill({ json: soldOut });
    if (path === '/v1/orders/quote') {
      const quantity = request.postDataJSON().items[0].quantity;
      state.quotes.push(quantity);
      return route.fulfill({ status: quoteError ? 409 : 200, json: quoteError ? { detail: '商品庫存已變動，請調整選購數量' } : { amount_total: quantity * quotePrice, membership_type: membershipType, items: [{ product_id: item.id, product_name: item.name, quantity, unit_price: quotePrice, subtotal: quantity * quotePrice, tax_type: 'taxable' }] } });
    }
    if (['/v1/meal-events', '/v1/group-campaigns', '/v1/activities', '/v1/pickup-locations'].includes(path)) return route.fulfill({ json: [] });
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

test('首次儲存失敗保留所選三份且不改清單，重試成功只增加三份', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`);
  const button = page.getByRole('button', { name: '加入結帳清單', exact: true });
  const result = page.locator('.cart-add-feedback');
  await button.waitFor();
  await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption('3');
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
  await button.click();
  assert.equal(await page.getByRole('alert').textContent(), '無法儲存結帳清單，請再試一次。');
  assert.equal(await result.textContent(), '');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 2);
  assert.equal(await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).inputValue(), '3');
  await button.click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity === 5);
  assert.equal(await result.textContent(), `${product.name}已加入，清單共 5 瓶`);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

for (const width of [1440, 390]) for (const path of ['/', '/shop', `/products/${product.id}`]) {
  test(`${width}px ${path} 先選三份再用鍵盤加入，與原兩份合併為五份`, async (t) => {
    const { page, state } = await fixture(t, width, path);
    const scope = path.startsWith('/products/') ? page.locator('.product-detail-copy') : page.locator('.product-card').filter({ has: page.getByRole('heading', { name: product.name, exact: true }) });
    const select = scope.getByRole('combobox', { name: `${product.name}加入數量`, exact: true });
    await select.selectOption('3');
    const button = scope.locator('.product-add-control > button');
    await button.focus(); await button.press('Enter');
    await page.waitForFunction(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity === 5);
    assert.equal(await scope.getByRole('status').textContent(), `${product.name}已加入，清單共 5 瓶`);
    assert.equal(await button.evaluate((element) => document.activeElement === element), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].product.stock_quantity), 20, '新增時更新商品快照');
    if (process.env.BROWSER_SCREENSHOT_DIR && width === 390) {
      await scope.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
      await page.screenshot({ animations: 'disabled', path: `${process.env.BROWSER_SCREENSHOT_DIR}/商品選購數量-${path === '/' ? '首頁' : path === '/shop' ? '商品卡' : '詳情'}-手機.png` });
    }
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

for (const detail of [false, true]) {
  test(`${detail ? '詳情' : '卡片'}快速連續加入三份不超庫存，拒絕整次追加且不截斷`, async (t) => {
    const { page, state } = await fixture(t, 390, detail ? `/products/${product.id}` : '/shop', { stock: 7 });
    const scope = detail ? page.locator('.product-detail-copy') : page.locator('.product-card').filter({ has: page.getByRole('heading', { name: product.name, exact: true }) });
    const select = scope.getByRole('combobox', { name: `${product.name}加入數量`, exact: true });
    await select.selectOption('3');
    const button = scope.locator('.product-add-control > button');
    await button.evaluate((element) => { element.click(); element.click(); element.click(); });
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 5);
    assert.match(await scope.getByRole('alert').textContent(), /最多還可加入 2 瓶/);
    assert.equal(await scope.getByRole('status').textContent(), '');
    assert.equal(await button.isDisabled(), true);
    await select.selectOption('2'); await button.click();
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 7);
    assert.equal(await button.isDisabled(), true); assert.equal(await select.isDisabled(), true);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

test('大量庫存只有常用下拉與自訂數量，不能超過單項 999 或接受非整數', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`, { stock: 5000 });
  const select = page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true });
  await select.waitFor();
  assert.equal(await select.locator('option').count(), 11);
  await select.selectOption('custom');
  const input = page.getByRole('spinbutton', { name: `${product.name}自訂數量`, exact: true });
  const button = page.locator('.product-add-control > button');
  assert.equal(await input.getAttribute('max'), '997');
  for (const value of ['0', '2.5', '998', '1000']) {
    await input.fill(value);
    assert.equal(await button.isDisabled(), true);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 2);
  }
  await input.fill('997'); await button.click();
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 999);
  assert.equal(await button.isDisabled(), true); assert.equal(await select.isDisabled(), true);
  assert.match(await page.getByRole('alert').textContent(), /可購上限為 999/);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('加入後結帳能減量及移除，核價收到正確數量', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`, { stock: 6, signedIn: true });
  await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption('3');
  await page.locator('.product-add-control > button').click();
  await page.goto(`${base}/checkout`);
  await page.locator('.summary-lines').getByText(`${product.name} × 5 · 應稅`, { exact: true }).waitFor();
  const row = page.locator('.checkout-item');
  await row.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.locator('.summary-lines').getByText(`${product.name} × 6 · 應稅`, { exact: true }).waitFor();
  assert.equal(await row.getByRole('button', { name: '增加數量', exact: true }).isDisabled(), false);
  for (let count = 6; count > 0; count--) await row.getByRole('button', { name: '減少數量', exact: true }).click();
  assert.equal(await page.locator('.checkout-item').count(), 0);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), []);
  assert.ok(state.quotes.includes(5) && state.quotes.includes(6));
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('目前庫存低於已選量時不靜默刪減，結帳仍接受後端核價阻擋', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`, { stock: 1, signedIn: true, quoteError: true });
  const button = page.locator('.product-add-control > button');
  await button.waitFor(); assert.equal(await button.isDisabled(), true);
  assert.match(await page.getByRole('alert').textContent(), /清單已有 2 瓶，目前可購上限為 1 瓶/);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 2);
  await page.goto(`${base}/checkout`);
  await page.getByText('商品庫存已變動，請調整選購數量', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isDisabled(), true);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('結帳快速增減不漏計且不超單項上限，儲存失敗可重試，單價採後端核價', async (t) => {
  const { page, state } = await fixture(t, 390, '/checkout', { initialStock: 5000, initialQuantity: 996, signedIn: true });
  const row = page.locator('.checkout-item');
  await row.getByText('$315／瓶', { exact: true }).waitFor();
  const increase = row.getByRole('button', { name: '增加數量', exact: true });
  const decrease = row.getByRole('button', { name: '減少數量', exact: true });
  await increase.evaluate((element) => { for (let i = 0; i < 4; i++) element.click(); });
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 999);
  assert.equal(await increase.isDisabled(), true);
  assert.match(await page.getByRole('alert').textContent(), /可購上限為 999 瓶/);
  await decrease.evaluate((element) => { element.click(); element.click(); });
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 997);
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
  await increase.click();
  assert.equal(await page.getByRole('alert').textContent(), '無法儲存結帳清單，請再試一次。');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 997);
  await increase.click();
  await page.locator('.summary-lines').getByText(`${product.name} × 998 · 應稅`, { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 998);
  assert.ok(state.quotes.includes(998));
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('舊清單超庫存仍可連續減量與移除，核價失敗不開放付款', async (t) => {
  const { page, state } = await fixture(t, 390, '/checkout', { initialStock: 5, initialQuantity: 10, signedIn: true, quoteError: true });
  await page.getByText('商品庫存已變動，請調整選購數量', { exact: true }).waitFor();
  const row = page.locator('.checkout-item');
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isDisabled(), true);
  const decrease = row.getByRole('button', { name: '減少數量', exact: true });
  await decrease.evaluate((element) => { element.click(); element.click(); });
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 8);
  await decrease.evaluate((element) => { for (let i = 0; i < 8; i++) element.click(); });
  await page.getByRole('heading', { name: '結帳清單還是空的', exact: true }).waitFor();
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), []);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('舊清單庫存快照不阻擋補貨後加量，數量交由最新核價確認', async (t) => {
  const { page, state } = await fixture(t, 390, '/checkout', { initialStock: 2, stock: 20, signedIn: true });
  await page.locator('.summary-lines').getByText(`${product.name} × 2 · 應稅`, { exact: true }).waitFor();
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.locator('.summary-lines').getByText(`${product.name} × 3 · 應稅`, { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 3);
  assert.ok(state.quotes.includes(3));
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

for (const [signedIn, membershipType, price] of [[false, 'nonmember', 320], [true, 'member', 300], [true, 'trainee', 300]]) {
  test(`結帳尚無報價時，${signedIn ? membershipType : '未登入'}單價使用正確身分`, async (t) => {
    const { page, state } = await fixture(t, 390, '/checkout', { signedIn, membershipType, quoteError: true });
    if (signedIn) await page.getByText('商品庫存已變動，請調整選購數量', { exact: true }).waitFor();
    await page.locator('.checkout-item').getByText(`$${price}／瓶`, { exact: true }).waitFor();
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

test('選三份加原兩份後，核價與送單都是五份，送單期間暫停改量', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`, { signedIn: true });
  await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption('3');
  await page.locator('.product-add-control > button').click();
  await page.route('**/v1/pickup-locations', (route) => route.fulfill({ json: [{ id: 'isolated-pickup', name: '隔離測試取貨點', is_active: true }] }));
  let releaseOrder;
  const gate = new Promise((resolve) => { releaseOrder = resolve; });
  const orders = [];
  await page.route('**/v1/orders', async (route) => {
    orders.push(route.request().postDataJSON());
    await gate;
    return route.fulfill({ status: 503, json: { detail: '隔離測試：未建立真實訂單' } });
  });
  await page.goto(`${base}/checkout`);
  await page.locator('.summary-lines').getByText(`${product.name} × 5 · 應稅`, { exact: true }).waitFor();
  const orderRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/v1/orders');
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await orderRequest;
  assert.deepEqual(orders[0].items, [{ product_id: product.id, quantity: 5 }]);
  assert.ok(state.quotes.includes(5));
  const row = page.locator('.checkout-item');
  await page.waitForFunction(() => document.querySelector('.checkout-item [aria-label="增加數量"]')?.disabled);
  assert.equal(await row.getByRole('button', { name: '增加數量', exact: true }).isDisabled(), true);
  assert.equal(await row.getByRole('button', { name: '減少數量', exact: true }).isDisabled(), true);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 5);
  releaseOrder();
  await page.getByText('隔離測試：未建立真實訂單', { exact: true }).waitFor();
  assert.equal(await row.getByRole('button', { name: '增加數量', exact: true }).isDisabled(), false);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});
