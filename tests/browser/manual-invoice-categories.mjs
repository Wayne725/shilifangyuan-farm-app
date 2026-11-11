import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;

async function fixture(t, { width = 390, meals = true, catalogError = false, uncertain = false } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
  const state = { posts: [], unknown: [], errors: [], catalogError };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {
      if (!['fonts.googleapis.com', 'fonts.gstatic.com'].includes(url.hostname)) state.unknown.push(url.origin);
      return route.abort();
    }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: {
      id: 'isolated-staff', display_name: '隔離店員', user_role: 'admin', membership_type: 'nonmember', email: 'staff@example.test',
    } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/products') return route.fulfill({ json: [
      { name: '合成一般商品', is_active: true, member_price: 90, nonmember_price: 100, tax_type: 'tax_exempt' },
      { name: '合成未上架商品', is_active: true, is_daily_listed: false, nonmember_price: 160, tax_type: 'taxable' },
      { name: '合成停用商品', is_active: false },
    ] });
    if (path === '/v1/admin/meals') return route.fulfill(state.catalogError
      ? { status: 503, json: { detail: '隔離品項載入失敗' } }
      : { json: meals ? [{ name: '合成便當', is_active: true, price: 149, tax_type: 'taxable' }, { name: '合成停用便當', is_active: false }] : [] });
    if (request.method() === 'GET' && path === '/v1/admin/manual-invoice-records') {
      return route.fulfill({ json: { items: [], total: 0 } });
    }
    if (request.method() === 'GET' && path.startsWith('/v1/admin/walk-in-sales/request/')) {
      return route.fulfill({ status: 404, json: { detail: '尚未找到此筆隔離交易' } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/walk-in-sales') {
      state.posts.push({ raw: request.postData(), body: request.postDataJSON() });
      if (uncertain && state.posts.length === 1) return route.fulfill({ status: 503, json: { detail: '隔離交易結果待確認' } });
      return route.fulfill({ status: 201, json: { order_id: 'isolated-sale', order_number: 'O2030010201', amount_total: 240,
        invoice_handling: 'on_payment', invoice_issue_after: null } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/admin?section=manual-invoice`);
  const region = page.getByRole('region', { name: '現場收款與開票', exact: true });
  await region.getByRole('combobox', { name: '品名', exact: true }).waitFor();
  return { page, region, state };
}

async function optionNames(region) {
  return region.getByRole('combobox', { name: '品名', exact: true }).getByRole('option').allTextContents();
}

async function completeSale(region) {
  await region.getByLabel('含稅單價', { exact: true }).fill('120');
  await region.getByLabel('數量', { exact: true }).fill('2');
  await region.getByLabel('發票通知 Email', { exact: true }).fill('customer@example.test');
  await region.getByRole('radio', { name: 'Email 會員載具', exact: true }).check();
}

async function assertClean(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [390, 1280]) {
  test(`${width}px 分類僅顯示對應啟用品項，切換清除品名與單價且不建立交易`, async t => {
    const { page, region, state } = await fixture(t, { width });
    const categories = region.getByRole('group', { name: '商品分類' });
    const product = region.getByRole('combobox', { name: '品名', exact: true });
    await product.getByRole('option', { name: '合成便當', exact: true }).waitFor({ state: 'attached' });
    assert.deepEqual(await optionNames(region), ['請選擇便當', '合成便當']);
    assert.equal(await categories.getByRole('button', { name: '便當', exact: true }).getAttribute('aria-pressed'), 'true');
    await product.selectOption({ label: '合成便當' });
    assert.equal(await region.getByLabel('含稅單價', { exact: true }).inputValue(), '149');
    assert.equal(await region.getByLabel('數量', { exact: true }).inputValue(), '1');
    assert.match(await region.locator('.admin-walk-in-confirm').innerText(), /149/);
    await region.getByLabel('含稅單價', { exact: true }).fill('150');
    await region.getByLabel('數量', { exact: true }).fill('3');
    assert.match(await region.locator('.admin-walk-in-confirm').innerText(), /450/);
    await completeSale(region);
    await categories.getByRole('button', { name: '一般商品', exact: true }).click();
    assert.equal(await categories.getByRole('button', { name: '一般商品', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.deepEqual(await optionNames(region), ['請選擇一般商品', '合成一般商品', '合成未上架商品']);
    assert.equal(await product.inputValue(), '');
    assert.equal(await region.getByLabel('含稅單價', { exact: true }).inputValue(), '');
    assert.equal(await region.getByRole('button', { name: '確認', exact: true }).isDisabled(), true);
    assert.equal(state.posts.length, 0);
    await product.selectOption({ label: '合成一般商品' });
    assert.equal(await region.getByLabel('含稅單價', { exact: true }).inputValue(), '100');
    assert.equal(await region.getByLabel('數量', { exact: true }).inputValue(), '1');
    assert.equal(await region.getByLabel('稅別', { exact: true }).inputValue(), 'tax_exempt');
    await region.getByLabel('含稅單價', { exact: true }).fill('110');
    await product.selectOption({ label: '合成未上架商品' });
    assert.equal(await region.getByLabel('含稅單價', { exact: true }).inputValue(), '160');
    assert.equal(await region.getByLabel('稅別', { exact: true }).inputValue(), 'taxable');
    await completeSale(region);
    await region.getByRole('button', { name: '確認', exact: true }).click();
    await region.getByText(/已記錄收款.*O2030010201/).waitFor();
    assert.equal(state.posts.length, 1);
    assert.equal(state.posts[0].body.product_name, '合成未上架商品');
    assert.equal(state.posts[0].body.invoice_handling, 'on_payment');
    assert.equal(state.posts[0].body.sale_category, 'product');
    assert.equal(Object.hasOwn(state.posts[0].body, 'order_kind'), false, '來源分類不變更核心訂單種類');
    assert.equal(await region.getByRole('radio', { name: '紙本' }).count(), 0);
    await assertClean(page, state);
  });
}

test('便當分類無品項時不預選虛構餐盒，可切換一般商品', async t => {
  const { page, region, state } = await fixture(t, { meals: false });
  await region.getByRole('option', { name: '此分類沒有啟用品項', exact: true }).waitFor({ state: 'attached' });
  assert.deepEqual(await optionNames(region), ['此分類沒有啟用品項']);
  assert.equal(await region.getByRole('button', { name: '確認', exact: true }).isDisabled(), true);
  await region.getByRole('button', { name: '一般商品', exact: true }).click();
  assert.deepEqual(await optionNames(region), ['請選擇一般商品', '合成一般商品', '合成未上架商品']);
  assert.equal(state.posts.length, 0);
  await assertClean(page, state);
});

test('品項載入失敗不得假設可販售品名，重試只讀目錄', async t => {
  const { page, region, state } = await fixture(t, { catalogError: true });
  await region.getByRole('button', { name: '重試載入品名', exact: true }).waitFor();
  assert.deepEqual(await optionNames(region), ['品項載入失敗']);
  assert.equal(await region.getByRole('button', { name: '確認', exact: true }).isDisabled(), true);
  state.catalogError = false;
  await region.getByRole('button', { name: '重試載入品名', exact: true }).click();
  await region.getByRole('option', { name: '合成便當', exact: true }).waitFor({ state: 'attached' });
  assert.deepEqual(await optionNames(region), ['請選擇便當', '合成便當']);
  assert.equal(state.posts.length, 0);
  await assertClean(page, state);
});

test('交易結果不明時分類也鎖定，重試沿用同一 UUID 與完整 payload', async t => {
  const { page, region, state } = await fixture(t, { uncertain: true });
  const product = region.getByRole('combobox', { name: '品名', exact: true });
  await product.getByRole('option', { name: '合成便當', exact: true }).waitFor({ state: 'attached' });
  await product.selectOption({ label: '合成便當' });
  await completeSale(region);
  await region.getByRole('button', { name: '確認', exact: true }).click();
  const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
  await retry.waitFor();
  assert.equal(await region.getByRole('button', { name: '一般商品', exact: true }).isDisabled(), true);
  assert.equal(await product.isDisabled(), true);
  await retry.click();
  await region.getByText(/已記錄收款.*O2030010201/).waitFor();
  assert.equal(state.posts.length, 2);
  assert.equal(state.posts[0].raw, state.posts[1].raw);
  await assertClean(page, state);
});
