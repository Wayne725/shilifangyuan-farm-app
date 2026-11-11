import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4195');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const product = { id: 'feedback-product', name: '隔離測試黑豆蔭油', description: '隔離測試商品', category: '合作社選品', unit: '瓶', supplier_name: '隔離合作社', member_price: 300, nonmember_price: 320, stock_quantity: 20, is_active: true, daily_sales_enabled: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'taxable' };
const soldOut = { ...product, id: 'sold-out-product', name: '隔離缺貨商品', stock_quantity: 0 };

async function fixture(t, width, path, { stock = 20, initialStock = 100, initialQuantity = 2, signedIn = false, membershipType = 'nonmember', quoteError = false, quotePrice = 315 } = {}) {
  const item = { ...product, stock_quantity: stock };
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  await context.addInitScript(({ item, initialStock, initialQuantity, signedIn }) => {
    const key = signedIn ? 'slf_cart_v2:user:cart-buyer' : 'slf_cart_v2:guest';
    window.__fixtureCartKey = key;
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ items: [{ product: { ...item, stock_quantity: initialStock }, quantity: initialQuantity }] }));
  }, { item, initialStock, initialQuantity, signedIn });
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
    if (['/v1/meal-events', '/v1/meal-events/calendar', '/v1/group-campaigns', '/v1/activities', '/v1/pickup-locations'].includes(path)) return route.fulfill({ json: [] });
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
    const button = scope.getByRole('button', { name: `加入購物車：${product.name}`, exact: true });
    const result = scope.getByRole('status');
    await button.waitFor();
    assert.equal(await result.textContent(), '');
    await button.focus();
    await button.press('Enter');
    await page.waitForFunction(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items.find((row) => row.product.id === 'feedback-product').quantity === 3);
    assert.equal(await result.textContent(), `${product.name}已加入，清單共 3 瓶`);
    assert.equal(await button.evaluate((element) => document.activeElement === element), true, '回饋不可搶走鍵盤焦點');
    assert.equal(await result.getAttribute('aria-live'), 'polite');
    assert.equal(await result.getAttribute('aria-atomic'), 'true');
    await page.clock.fastForward(2000);
    await button.evaluate((element) => { element.click(); element.click(); element.click(); });
    await page.waitForFunction(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items.find((row) => row.product.id === 'feedback-product').quantity === 6);
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
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items.find((row) => row.product.id === 'feedback-product').quantity), 6);
    if (!detail) {
      const disabled = page.getByRole('button', { name: `加入購物車：${soldOut.name}`, exact: true });
      assert.equal(await disabled.isDisabled(), true);
      assert.equal(await disabled.locator('..').getByRole('status').textContent(), '');
    }
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

test('缺貨詳情保留禁用，切換商品不沿用另一商品加入成功結果', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`);
  await page.getByRole('button', { name: `加入購物車：${product.name}`, exact: true }).click();
  await page.getByRole('status').filter({ hasText: '清單共 3 瓶' }).waitFor();
  await page.goto(`${base}/products/${soldOut.id}`);
  const disabled = page.getByRole('button', { name: `加入購物車：${soldOut.name}`, exact: true });
  await disabled.waitFor();
  assert.equal(await disabled.isDisabled(), true);
  assert.equal(await page.locator('.cart-add-feedback').textContent(), '');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items.length), 1);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('儲存失敗仍以記憶體加入三份，原清單不覆寫，重整後恢復原清單並可正常加入', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`);
  const button = page.getByRole('button', { name: `加入購物車：${product.name}`, exact: true });
  const result = page.locator('.cart-add-feedback');
  await button.waitFor();
  const nativeCart = await page.evaluate(() => localStorage.getItem(window.__fixtureCartKey));
  await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption('3');
  await page.evaluate(() => {
    const set_item = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (this === localStorage && key === window.__fixtureCartKey) throw new DOMException('隔離測試儲存失敗', 'QuotaExceededError');
      return set_item.call(this, key, value);
    };
  });
  await button.click();
  await result.getByText(`${product.name}已加入，清單共 5 瓶`, { exact: true }).waitFor();
  await page.getByText('瀏覽器儲存受限，部分資料只在本頁暫存，重整後可能無法保留。', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem(window.__fixtureCartKey)), nativeCart);
  assert.equal(await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).inputValue(), '3');
  await page.reload();
  await button.waitFor();
  assert.equal(await result.textContent(), '');
  assert.equal(await page.evaluate(() => localStorage.getItem(window.__fixtureCartKey)), nativeCart);
  await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption('3');
  await button.click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity === 5);
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
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 2, '選取數量不應直接加入購物車');
    const button = scope.locator('.product-add-control > button');
    assert.equal(await button.textContent(), '加入購物車');
    await button.focus(); await button.press('Enter');
    await page.waitForFunction(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity === 5);
    assert.equal(await scope.getByRole('status').textContent(), `${product.name}已加入，清單共 5 瓶`);
    assert.equal(await button.evaluate((element) => document.activeElement === element), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].product.stock_quantity), 20, '新增時更新商品快照');
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
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 5);
    assert.match(await scope.getByRole('alert').textContent(), /最多還可加入 2 瓶/);
    assert.equal(await scope.getByRole('status').textContent(), '');
    assert.equal(await button.isDisabled(), true);
    await select.selectOption('2'); await button.click();
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 7);
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
  assert.equal(await select.count(), 0, '自訂模式不可同時顯示數量下拉');
  const input = page.getByRole('spinbutton', { name: `${product.name}自訂數量`, exact: true });
  const button = page.locator('.product-add-control > button');
  assert.equal(await input.getAttribute('max'), '997');
  for (const value of ['0', '2.5', '998', '1000']) {
    await input.fill(value);
    assert.equal(await button.isDisabled(), true);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 2);
  }
  await input.fill('997'); await button.click();
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 999);
  assert.equal(await button.isDisabled(), true);
  await page.getByRole('button', { name: '改用常用數量', exact: true }).click();
  assert.equal(await select.isDisabled(), true);
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
  assert.equal(await row.getByRole('button', { name: '增加數量', exact: true }).isDisabled(), true);
  await row.getByText('目前最多可買 6 瓶', { exact: true }).waitFor();
  for (let count = 6; count > 0; count--) await row.getByRole('button', { name: '減少數量', exact: true }).click();
  assert.equal(await page.locator('.checkout-item').count(), 0);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items), []);
  assert.ok(state.quotes.includes(5) && state.quotes.includes(6));
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('目前庫存低於已選量時不靜默刪減，結帳仍接受後端核價阻擋', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`, { stock: 1, signedIn: true, quoteError: true });
  const button = page.locator('.product-add-control > button');
  await button.waitFor(); assert.equal(await button.isDisabled(), true);
  assert.match(await page.getByRole('alert').textContent(), /清單已有 2 瓶，目前可購上限為 1 瓶/);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 2);
  await page.goto(`${base}/checkout`);
  await page.getByText('商品庫存已變動，請調整選購數量', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isDisabled(), true);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('結帳快速增減不漏計且不超單項上限，儲存失敗用記憶體保留原清單，單價採後端核價', async (t) => {
  const { page, state } = await fixture(t, 390, '/checkout', { initialStock: 5000, stock: 5000, initialQuantity: 996, signedIn: true });
  const row = page.locator('.checkout-item');
  await row.getByText('$315／瓶', { exact: true }).waitFor();
  const increase = row.getByRole('button', { name: '增加數量', exact: true });
  const decrease = row.getByRole('button', { name: '減少數量', exact: true });
  await page.waitForFunction(() => !document.querySelector('.checkout-item [aria-label=增加數量]')?.disabled);
  await increase.evaluate((element) => { for (let i = 0; i < 4; i++) element.click(); });
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 999);
  assert.equal(await increase.isDisabled(), true);
  assert.match(await page.getByRole('alert').textContent(), /可購上限為 999 瓶/);
  await decrease.evaluate((element) => { element.click(); element.click(); });
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 997);
  const nativeCart = await page.evaluate(() => localStorage.getItem(window.__fixtureCartKey));
  await page.evaluate(() => {
    const set_item = Storage.prototype.setItem;
    window.__auditCartStorageBlocked = true;
    Storage.prototype.setItem = function (key, value) {
      if (this === localStorage && key === window.__fixtureCartKey && window.__auditCartStorageBlocked) throw new DOMException('隔離測試儲存失敗', 'QuotaExceededError');
      return set_item.call(this, key, value);
    };
  });
  await increase.click();
  await page.locator('.summary-lines').getByText(`${product.name} × 998 · 應稅`, { exact: true }).waitFor();
  await page.getByText('瀏覽器儲存受限，部分資料只在本頁暫存，重整後可能無法保留。', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem(window.__fixtureCartKey)), nativeCart);
  await page.reload();
  await row.getByText('$315／瓶', { exact: true }).waitFor();
  await page.locator('.summary-lines').getByText(`${product.name} × 997 · 應稅`, { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem(window.__fixtureCartKey)), nativeCart);
  await increase.click();
  await page.locator('.summary-lines').getByText(`${product.name} × 998 · 應稅`, { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 998);
  assert.ok(state.quotes.includes(998));
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('舊清單超庫存仍可連續減量與移除，核價失敗不開放付款', async (t) => {
  const { page, state } = await fixture(t, 390, '/checkout', { stock: 5, initialQuantity: 10, signedIn: true, quoteError: true });
  await page.getByText('商品庫存已變動，請調整選購數量', { exact: true }).waitFor();
  const row = page.locator('.checkout-item');
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).isDisabled(), true);
  const decrease = row.getByRole('button', { name: '減少數量', exact: true });
  await decrease.evaluate((element) => { element.click(); element.click(); });
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 8);
  await decrease.evaluate((element) => { for (let i = 0; i < 8; i++) element.click(); });
  await page.getByRole('heading', { name: '結帳清單還是空的', exact: true }).waitFor();
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items), []);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('舊清單庫存快照不阻擋補貨後加量，數量交由最新核價確認', async (t) => {
  const { page, state } = await fixture(t, 390, '/checkout', { initialStock: 2, stock: 20, signedIn: true });
  await page.locator('.summary-lines').getByText(`${product.name} × 2 · 應稅`, { exact: true }).waitFor();
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.locator('.summary-lines').getByText(`${product.name} × 3 · 應稅`, { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 3);
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

test('選三份加原兩份後核價送單都是五份，未知結果凍結原單，同UUID重試至明確拒絕才允許改量', async (t) => {
  const { page, state } = await fixture(t, 390, `/products/${product.id}`, { signedIn: true });
  await page.getByRole('combobox', { name: `${product.name}加入數量`, exact: true }).selectOption('3');
  await page.locator('.product-add-control > button').click();
  await page.route('**/v1/pickup-locations', (route) => route.fulfill({ json: [{ id: 'isolated-pickup', name: '隔離測試取貨點', is_active: true }] }));
  let releaseOrder;
  const gate = new Promise((resolve) => { releaseOrder = resolve; });
  t.after(() => releaseOrder());
  const orders = [];
  await page.route('**/v1/orders', async (route) => {
    orders.push(route.request().postDataJSON());
    if (orders.length === 1) {
      await gate;
      return route.fulfill({ status: 503, json: { detail: '隔離測試：送單結果暫時未知' } });
    }
    return route.fulfill({ status: 422, json: { detail: '隔離測試：已明確拒絕且未建立訂單' } });
  });
  await page.goto(`${base}/checkout`);
  await page.locator('.summary-lines').getByText(`${product.name} × 5 · 應稅`, { exact: true }).waitFor();
  const orderRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/v1/orders');
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await orderRequest;
  assert.deepEqual(orders[0].items, [{ product_id: product.id, quantity: 5 }]);
  assert.match(orders[0].client_request_id, /^[0-9a-f-]{36}$/i);
  assert.ok(state.quotes.includes(5));
  const frozen = page.locator('.checkout-recovery');
  await frozen.getByText(`${product.name} × 5`, { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '增加數量', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '減少數量', exact: true }).count(), 0);
  assert.equal(await frozen.getByRole('button', { name: '確認訂單中…', exact: true }).isDisabled(), true);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 5);
  releaseOrder();
  await page.getByText('隔離測試：送單結果暫時未知', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '增加數量', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '減少數量', exact: true }).count(), 0);
  await frozen.getByRole('button', { name: '重試原訂單', exact: true }).click();
  await page.getByText('隔離測試：已明確拒絕且未建立訂單', { exact: true }).waitFor();
  assert.equal(orders.length, 2);
  assert.deepEqual(orders[1], orders[0]);
  const row = page.locator('.checkout-item');
  assert.equal(await row.getByRole('button', { name: '增加數量', exact: true }).isDisabled(), false);
  assert.equal(await row.getByRole('button', { name: '減少數量', exact: true }).isDisabled(), false);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem(window.__fixtureCartKey)).items[0].quantity), 5);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});
