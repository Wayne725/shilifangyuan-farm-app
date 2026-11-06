import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4198');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const campaign = {
  id: 'campaign', title: '價格核對共同購買', description: '隔離測試',
  target_type: 'product', target_id: 'product', member_price: 90, nonmember_price: 100,
  min_paid_quantity: 10, supply_cap: 30, per_user_cap: 5, paid_quantity: 0,
  reserved_quantity: 0, available_quantity: 30, deadline: '2030-01-03T04:00:00Z',
  estimated_pickup_start: '2030-01-06T04:00:00Z', estimated_pickup_end: '2030-01-07T04:00:00Z',
  decision_status: 'recruiting', intake_status: 'open', can_ship: false, allowed_shipping_channels: [],
};

async function fixture(t, { membership = 'nonmember', width = 1280, deferred = false, fail = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  const state = { fail, unknown: [], errors: [], quotes: [], release: null };
  let quoteStarted;
  state.quoteStarted = new Promise(resolve => { quoteStarted = resolve; });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: {
      access_token: 'isolated', user: { id: 'buyer', display_name: '隔離買家', email: 'buyer@example.test', user_role: 'customer', membership_type: membership === 'nonmember' ? 'member' : 'nonmember' },
    } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, group_orders_enabled: true, regular_orders_enabled: true, shipping_enabled: false } });
    if (path === '/v1/group-campaigns/campaign') return route.fulfill({ json: campaign });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'store', name: '隔離取貨點', is_active: true }] });
    if (path === '/v1/group-campaigns/campaign/quote') {
      const body = request.postDataJSON();
      state.quotes.push(body);
      if (deferred) await new Promise(resolve => { state.release = resolve; quoteStarted(); });
      if (state.fail) return route.fulfill({ status: 503, json: { detail: '隔離報價失敗' } });
      const price = membership === 'nonmember' ? 100 : 90;
      return route.fulfill({ json: { membership_type: membership, quantity: body.quantity, unit_price: price, product_subtotal: price * body.quantity, shipping_fee: 0, amount_total: price * body.quantity, tax_type: 'taxable', tax_amount: 5 } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.install({ time: new Date('2030-01-02T10:00:00+08:00') });
  await page.goto(`${base}/groups/campaign`);
  return { page, state };
}

function priceRow(page) {
  return page.locator('.offer-summary dt').filter({ hasText: /^(一般|社員)?單價$/ }).locator('..');
}

async function verify(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const [membership, label, amount, width] of [
  ['nonmember', '一般單價', '$100', 1280],
  ['nonmember', '一般單價', '$100', 390],
  ['member', '社員單價', '$90', 1280],
  ['trainee', '社員單價', '$90', 1280],
]) test(`共同購買以伺服器 ${membership} 報價顯示身分與價格 ${width}px`, async t => {
  const { page, state } = await fixture(t, { membership, width });
  await priceRow(page).getByText(label, { exact: true }).waitFor();
  assert.equal(await priceRow(page).locator('dd').innerText(), amount);
  assert.equal(state.quotes[0].membership_type, undefined);
  await verify(page, state);
});

test('共同購買等待報價時不顯示預設社員價，也不能付款', async t => {
  const { page, state } = await fixture(t, { deferred: true, width: 390 });
  await state.quoteStarted;
  await priceRow(page).getByText('單價', { exact: true }).waitFor();
  assert.equal(await priceRow(page).locator('dd').innerText(), '—');
  assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
  state.release();
  await priceRow(page).getByText('一般單價', { exact: true }).waitFor();
  assert.equal(await priceRow(page).locator('dd').innerText(), '$100');
  await verify(page, state);
});

test('共同購買初次報價失敗不以社員價代替', async t => {
  const { page, state } = await fixture(t, { fail: true });
  await page.getByText('隔離報價失敗', { exact: true }).waitFor();
  assert.equal(await priceRow(page).locator('dt').innerText(), '單價');
  assert.equal(await priceRow(page).locator('dd').innerText(), '—');
  assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
  await verify(page, state);
});

test('共同購買改量後報價失敗不沿用前次單價及總額', async t => {
  const { page, state } = await fixture(t, { membership: 'member' });
  await priceRow(page).getByText('社員單價', { exact: true }).waitFor();
  state.fail = true;
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.getByText('隔離報價失敗', { exact: true }).waitFor();
  assert.equal(await priceRow(page).locator('dt').innerText(), '單價');
  assert.equal(await priceRow(page).locator('dd').innerText(), '—');
  assert.equal(await page.locator('.offer-total dd').innerText(), '—');
  assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
  await verify(page, state);
});
