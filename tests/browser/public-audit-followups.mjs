import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const deadline = new Date('2030-01-02T12:00:00Z');
const activity = { id: 'synthetic-activity', title: '合成社區走讀', description: '隔離活動', location: '隔離場地', status: 'published', starts_at: '2030-01-03T02:00:00Z', ends_at: '2030-01-03T04:00:00Z', registration_deadline: deadline.toISOString(), capacity: 20, registration_count: 0, waitlist_count: 0, my_registration: null };
const product = { id: 'synthetic-product', name: '合成醬油', description: '隔離商品', supplier_name: '隔離供應者', category: '加工品', sku: 'INTERNAL-SKU-PRIVATE', product_number: 'INTERNAL-ITEM-123', member_price: 100, nonmember_price: 120, tax_type: 'taxable', unit: '瓶', is_active: true, daily_sales_enabled: true, image_url: '', stock_quantity: 20 };
const adminQuestion = '會計如何依原有格式匯出檔案？';
const adminAnswer = '隔離驗收用的管理員說明';

async function fixture(t, role = null) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'America/Los_Angeles', serviceWorkers: 'block' });
  const state = { role, writes: [], unknown: [], errors: [], adminHelpRequests: [], denyAdminHelp: false };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') {
      const user = state.role && { id: 'isolated-user', email: 'isolated@example.test', display_name: '隔離使用者', user_role: state.role, membership_type: 'member', membership_status: 'active' };
      return route.fulfill({ status: user ? 200 : 401, json: user ? { access_token: 'isolated', user } : { detail: '尚未登入' } });
    }
    if (path === '/v1/admin/help') {
      state.adminHelpRequests.push(request.headers().authorization);
      return state.denyAdminHelp || state.role !== 'admin'
        ? route.fulfill({ status: 403, json: { detail: '需要管理員權限' } })
        : route.fulfill({ json: [{ question: adminQuestion, answer: adminAnswer }] });
    }
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/products') return route.fulfill({ json: [product] });
    if (path === `/v1/products/${product.id}`) return route.fulfill({ json: product });
    if (path === '/v1/activities') return route.fulfill({ json: [activity] });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'member', membership: { status: 'active' } } });
    if (path === '/v1/notifications' || path === '/v1/pickup-locations') return route.fulfill({ json: [] });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true } });
    if (request.method() !== 'GET') state.writes.push(`${request.method()} ${path}`);
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  return { page, state };
}

function verify(state) {
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
}

for (const path of ['/', '/activities']) test(`${path} 保持頁面開啟跨過台北報名截止時，標籤與按鈕同步更新`, async t => {
  const { page, state } = await fixture(t);
  await page.clock.setFixedTime(new Date(deadline.getTime() - 1000));
  await page.goto(`${base}${path}`);
  const card = path === '/' ? page.locator('.community-preview article').filter({ hasText: activity.title }) : page.locator('.activity-list article').filter({ hasText: activity.title });
  await card.getByText(path === '/' ? '開放報名' : '報名中', { exact: true }).waitFor();
  await page.clock.setFixedTime(deadline);
  await card.getByText('報名已截止', { exact: true }).first().waitFor();
  assert.equal(await card.getByText(path === '/' ? '開放報名' : '報名中', { exact: true }).count(), 0);
  if (path === '/activities') assert.equal(await card.getByRole('button', { name: '報名已截止', exact: true }).isEnabled(), false);
  verify(state);
});

for (const role of [null, 'customer', 'admin']) test(`${role || 'guest'} 的FAQ保留會餐與取貨說明，管理問答僅由管理員讀取`, async t => {
  const { page, state } = await fixture(t, role);
  await page.goto(`${base}/help`);
  await page.getByRole('heading', { name: '操作說明與常見問題', exact: true }).waitFor();
  if (role) await page.locator('a[aria-label="會員中心：隔離使用者"]').waitFor({ state: 'attached' });
  await page.getByText('什麼是會餐？', { exact: true }).waitFor();
  await page.getByText('各取貨點有哪些領取說明？', { exact: true }).waitFor();
  await page.getByText('社員頭像如何更新？', { exact: true }).waitFor();
  const privateFaq = page.getByRole('region', { name: '管理員常見問題', exact: true });
  if (role === 'admin') {
    await privateFaq.getByText(adminQuestion, { exact: true }).click();
    await privateFaq.getByText(adminAnswer, { exact: true }).waitFor();
    assert.deepEqual(state.adminHelpRequests, ['Bearer isolated']);
    state.role = 'customer';
    await page.reload();
    await page.locator('a[aria-label="會員中心：隔離使用者"]').waitFor({ state: 'attached' });
    assert.deepEqual(state.adminHelpRequests, ['Bearer isolated']);
  } else {
    assert.deepEqual(state.adminHelpRequests, []);
  }
  assert.equal(await privateFaq.count(), 0);
  assert.equal(await page.getByText(adminQuestion, { exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  verify(state);
});

test('管理員問答讀取失權時，移除舊問答且保留公開內容', async t => {
  const { page, state } = await fixture(t, 'admin');
  await page.goto(`${base}/help`);
  const privateFaq = page.getByRole('region', { name: '管理員常見問題', exact: true });
  await privateFaq.getByText(adminQuestion, { exact: true }).click();
  await privateFaq.getByText(adminAnswer, { exact: true }).waitFor();
  state.denyAdminHelp = true;
  const denied = page.waitForResponse(response => new URL(response.url()).pathname === '/v1/admin/help' && response.status() === 403);
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
  await denied;
  await privateFaq.waitFor({ state: 'detached' });
  assert.equal(await page.getByText(adminAnswer, { exact: true }).count(), 0);
  await page.getByText('什麼是會餐？', { exact: true }).waitFor();
  assert.deepEqual(state.adminHelpRequests, ['Bearer isolated', 'Bearer isolated']);
  verify(state);
});

test('買家商品卡保留供應者、品名、價格與選購，隱藏內部料號', async t => {
  const { page, state } = await fixture(t);
  await page.goto(base);
  const card = page.locator('.product-card').filter({ hasText: product.name });
  await card.getByRole('heading', { name: product.name, exact: true }).waitFor();
  await card.getByText(product.supplier_name, { exact: true }).waitFor();
  await card.getByText('$100', { exact: true }).waitFor();
  assert.equal(await page.getByText(product.sku, { exact: true }).count(), 0);
  assert.equal(await page.getByText(product.product_number, { exact: true }).count(), 0);
  assert.equal(await card.getByRole('button', { name: `加入購物車：${product.name}`, exact: true }).isEnabled(), true);
  await card.getByRole('heading', { name: product.name, exact: true }).getByRole('link').click();
  await page.getByRole('heading', { name: product.name, exact: true, level: 1 }).waitFor();
  await page.getByText(product.supplier_name, { exact: true }).waitFor();
  await page.getByText('應稅', { exact: true }).waitFor();
  await page.getByText('$120', { exact: true }).waitFor();
  assert.equal(await page.getByText(product.sku, { exact: true }).count(), 0);
  assert.equal(await page.getByText(product.product_number, { exact: true }).count(), 0);
  assert.equal(await page.getByRole('heading', { name: '正式主檔', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: `加入購物車：${product.name}`, exact: true }).isEnabled(), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  verify(state);
});
