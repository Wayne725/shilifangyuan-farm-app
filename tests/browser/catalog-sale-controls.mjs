import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4194');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const product = (id, name, extra = {}) => ({
  id, name, slug: id, description: '隔離測試商品', category: '加工品', unit: '瓶',
  product_number: `P-${id}`, sku: `SKU-${id}`, supplier_id: null, supplier_name: null,
  member_price: 100, nonmember_price: 120, stock_quantity: 20, tax_type: 'taxable',
  can_ship: false, shipping_temperature: null, allowed_shipping_channels: [],
  is_active: true, daily_sales_enabled: true, ...extra,
});

async function fixture(t, width, { role = 'admin', patchError = '', deferPatch = false, activeSupplier = true } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  const state = {
    items: [product('new', '最新測試果醬', { supplier_name: '山城農場', supplier_id: 'supplier-1' }), product('old', '停售測試醬油', { is_active: false }), product('group', '共同購買專用商品', { daily_sales_enabled: false })],
    patches: [], reads: 0, unknown: [], errors: [], releasePatch: null,
  };
  let patchStarted;
  state.patchStarted = new Promise(resolve => { patchStarted = resolve; });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'admin', display_name: '隔離管理員', user_role: role, membership_type: 'nonmember' } } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false } });
    if (path === '/v1/platform' && request.method() === 'GET') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/products' && request.method() === 'GET') {
      state.reads++;
      return route.fulfill({ json: structuredClone(state.items) });
    }
    if (path === '/v1/admin/suppliers') return route.fulfill({ json: [{ id: 'supplier-1', business_name: '山城農場', is_active: activeSupplier }] });
    if (path === '/v1/admin/pickup-locations') return route.fulfill({ json: [] });
    if (path.startsWith('/v1/products/') && request.method() === 'PATCH') {
      const id = path.split('/').at(-1), body = request.postDataJSON();
      state.patches.push({ id, body });
      if (deferPatch) await new Promise(resolve => { state.releasePatch = resolve; patchStarted(); });
      if (patchError) return route.fulfill({ status: 409, json: { detail: patchError } });
      const item = state.items.find(item => item.id === id);
      Object.assign(item, body);
      return route.fulfill({ json: item });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/admin/catalog?tab=products`);
  return { page, state };
}

function card(page, name = '最新測試果醬') {
  return page.locator('article.admin-record-card').filter({ has: page.getByRole('heading', { name, exact: true }) });
}

async function verify(page, state) {
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

test('商品清單有可直接操作的搜尋與停售入口', async t => {
  const { page, state } = await fixture(t, 1280);
  await page.getByRole('heading', { name: '商品清單', exact: true }).waitFor();
  assert.deepEqual({
    search: await page.getByLabel('搜尋商品', { exact: true }).count(),
    stopSale: await card(page).getByRole('button', { name: '停止販售', exact: true }).count(),
  }, { search: 1, stopSale: 1 }, '缺少商品搜尋或明確停售操作');
  await verify(page, state);
});

for (const width of [390, 1280]) {
  test(`${width}px 搜尋商品名稱、SKU、編號與供應者，搭配販售篩選及清除`, async t => {
    const { page, state } = await fixture(t, width);
    const search = page.getByLabel('搜尋商品', { exact: true });
    await search.waitFor();
    assert.equal(await search.getAttribute('placeholder'), '商品名稱、SKU、產品編號或供應者');
    const prices = card(page).locator('dl');
    await prices.getByText('社員價', { exact: true }).waitFor();
    assert.equal(await prices.locator('dt:text-is("社員價") + dd').innerText(), '$100');
    assert.equal(await prices.locator('dt:text-is("一般價") + dd').innerText(), '$120');
    for (const term of [' 果醬 ', ' sku-NEW ', 'p-new', '山城農場']) {
      await search.fill(term);
      await card(page).waitFor();
      assert.equal(await page.locator('article.admin-record-card').count(), 1);
    }
    const filters = page.getByRole('group', { name: '販售狀態', exact: true });
    await search.fill('');
    await filters.getByRole('button', { name: '不可販售', exact: true }).click();
    await card(page, '停售測試醬油').waitFor();
    assert.equal(await page.locator('article.admin-record-card').count(), 1);
    assert.equal(await filters.getByRole('button', { name: '不可販售', exact: true }).getAttribute('aria-pressed'), 'true');
    await filters.getByRole('button', { name: '可販售', exact: true }).click();
    await card(page).waitFor();
    assert.equal(await page.locator('article.admin-record-card').count(), 2);
    await card(page, '共同購買專用商品').getByText('未上架', { exact: true }).waitFor();
    await search.fill('不存在商品');
    await page.getByText('沒有符合條件的商品', { exact: true }).waitFor();
    assert.equal(await page.locator('article.admin-record-card').count(), 0);
    await page.getByRole('button', { name: '清除查詢', exact: true }).click();
    await card(page).waitFor();
    assert.equal(await search.inputValue(), '');
    assert.equal(await page.locator('article.admin-record-card').count(), 3);
    assert.equal(await filters.getByRole('button', { name: '全部', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(state.patches.length, 0);
    await verify(page, state);
  });
}

test('停售與恢復只修改販售狀態；重新編輯沿用最新狀態，取消捨棄未儲存值', async t => {
  const { page, state } = await fixture(t, 390);
  await card(page).getByRole('button', { name: '停止販售', exact: true }).click();
  await card(page).getByRole('button', { name: '恢復販售', exact: true }).waitFor();
  assert.deepEqual(state.patches, [{ id: 'new', body: { is_active: false } }]);
  await card(page).getByRole('button', { name: '編輯商品', exact: true }).click();
  const editor = page.locator('form.admin-master-form');
  assert.equal(await editor.getByRole('checkbox').first().isChecked(), false);
  await editor.getByLabel('商品名稱', { exact: true }).fill('尚未儲存的名稱');
  await editor.getByRole('button', { name: '取消', exact: true }).click();
  await card(page).getByRole('button', { name: '編輯商品', exact: true }).click();
  assert.equal(await editor.getByLabel('商品名稱', { exact: true }).inputValue(), '最新測試果醬');
  state.items[0].stock_quantity = 18;
  await editor.getByLabel('商品說明').fill('已更新商品說明');
  await editor.getByRole('button', { name: '儲存商品', exact: true }).click();
  await card(page).getByRole('button', { name: '恢復販售', exact: true }).waitFor();
  assert.deepEqual(state.patches[1].body, { description: '已更新商品說明' });
  assert.equal(state.items[0].is_active, false);
  assert.equal(state.items[0].stock_quantity, 18);
  assert.equal(state.items[0].description, '已更新商品說明');
  await card(page).getByRole('button', { name: '恢復販售', exact: true }).click();
  await card(page).getByRole('button', { name: '停止販售', exact: true }).waitFor();
  assert.deepEqual(state.patches[2], { id: 'new', body: { is_active: true } });
  assert.equal(state.items[0].daily_sales_enabled, true);
  await verify(page, state);
});

test('停售失敗顯示錯誤，保留原可販售狀態', async t => {
  const detail = '商品狀態更新失敗，請稍後重試';
  const { page, state } = await fixture(t, 1280, { patchError: detail });
  await card(page).getByRole('button', { name: '停止販售', exact: true }).click();
  await card(page).getByRole('alert').getByText(detail, { exact: true }).waitFor();
  assert.equal(await card(page).getByText('可販售', { exact: true }).count(), 1);
  assert.equal(await card(page).getByRole('button', { name: '停止販售', exact: true }).isEnabled(), true);
  assert.equal(await card(page).getByRole('button', { name: '恢復販售', exact: true }).count(), 0);
  assert.equal(state.items[0].is_active, true);
  assert.equal(state.patches.length, 1);
  await verify(page, state);
});

test('既有供應者已停用時仍保留選取，修改商品名稱不重送供應者或庫存', async t => {
  const { page, state } = await fixture(t, 1280, { activeSupplier: false });
  await card(page).getByRole('button', { name: '編輯商品', exact: true }).click();
  const editor = page.locator('form.admin-master-form');
  const supplier = editor.getByLabel('供應者');
  assert.equal(await supplier.inputValue(), 'supplier-1');
  assert.match(await supplier.locator('option:checked').innerText(), /山城農場.*停用/);
  await editor.getByLabel('商品名稱', { exact: true }).fill('更新後測試果醬');
  await editor.getByRole('button', { name: '儲存商品', exact: true }).click();
  await card(page, '更新後測試果醬').waitFor();
  assert.deepEqual(state.patches, [{ id: 'new', body: { name: '更新後測試果醬' } }]);
  assert.equal(state.items[0].supplier_id, 'supplier-1');
  await verify(page, state);
});

test('停售等待期間鎖定卡片操作，重複點擊不重送', async t => {
  const { page, state } = await fixture(t, 390, { deferPatch: true });
  await card(page).getByRole('button', { name: '停止販售', exact: true }).dblclick();
  await state.patchStarted;
  await card(page).getByRole('button', { name: '更新中…', exact: true }).waitFor();
  assert.equal(await card(page).getByRole('button', { name: '編輯商品', exact: true }).isEnabled(), false);
  const buttons = await card(page).getByRole('button').all();
  assert.ok(buttons.length >= 2);
  for (const button of buttons) assert.equal(await button.isEnabled(), false);
  assert.equal(state.patches.length, 1);
  state.releasePatch();
  await card(page).getByRole('button', { name: '恢復販售', exact: true }).waitFor();
  assert.equal(state.items[0].is_active, false);
  await verify(page, state);
});

test('一般會員不能讀取後台商品清單或操作停售', async t => {
  const { page, state } = await fixture(t, 390, { role: 'customer' });
  await page.getByRole('heading', { name: '商品與供應管理只向管理者開放。', exact: true }).waitFor();
  assert.equal(await page.getByLabel('搜尋商品', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '停止販售', exact: true }).count(), 0);
  assert.equal(state.reads, 0);
  assert.equal(state.patches.length, 0);
  await verify(page, state);
});
