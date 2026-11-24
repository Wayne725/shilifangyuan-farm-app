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
  pickup_slots: [{ id: 'fri', weekday: 4, starts_at: '11:30', ends_at: '13:00', label: '每週五 11:30~13:00' }], estimated_pickup_start: '2030-01-06T04:00:00Z', estimated_pickup_end: '2030-01-07T04:00:00Z',
  preorder_only: true, decision_status: 'confirmed', intake_status: 'open', can_ship: false, allowed_shipping_channels: [],
};

async function fixture(t, { membership = 'nonmember', width = 1280, deferred = false, fail = false, slots = null, userMembership = 'member', description = null, pickupLocationIds = null } = {}) {
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
      access_token: 'isolated', user: { id: 'buyer', display_name: '隔離買家', email: 'buyer@example.test', user_role: 'customer', membership_type: userMembership },
    } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, group_orders_enabled: true, regular_orders_enabled: true, shipping_enabled: false } });
    if (path === '/v1/group-campaigns/campaign') return route.fulfill({ json: { ...campaign, ...(slots ? { pickup_slots: slots } : {}), ...(description ? { description } : {}),
      ...(pickupLocationIds ? { pickup_location_ids: pickupLocationIds } : {}) } });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'store', name: '隔離取貨點', is_active: true }, { id: 'hall', name: '隔離行政大樓', is_active: true }] });
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

test('多個取貨時段須由買家點選一個才能付款', async t => {
  const slots = [
    { id: 'wed', weekday: 2, starts_at: '11:30', ends_at: '13:00', label: '每週三 11:30~13:00' },
    { id: 'fri', weekday: 4, starts_at: '11:30', ends_at: '13:00', label: '每週五 11:30~13:00' },
  ];
  const { page, state } = await fixture(t, { width: 390, slots });
  await priceRow(page).getByText('一般單價', { exact: true }).waitFor();
  const choices = page.getByRole('group', { name: '取貨時段' });
  assert.equal(await choices.getByRole('radio').count(), 2);
  assert.equal(await choices.getByRole('radio', { checked: true }).count(), 0);
  await choices.getByLabel('每週三 11:30~13:00', { exact: true }).check();
  assert.equal(await choices.getByLabel('每週三 11:30~13:00', { exact: true }).isChecked(), true);
  assert.equal(await page.getByText('每週三 11:30~13:00、每週五 11:30~13:00', { exact: true }).count(), 1);
  await verify(page, state);
});

test('只有一個取貨時段時自動選取', async t => {
  const { page, state } = await fixture(t, { width: 390 });
  await priceRow(page).getByText('一般單價', { exact: true }).waitFor();
  assert.equal(await page.getByRole('group', { name: '取貨時段' }).getByLabel('每週五 11:30~13:00', { exact: true }).isChecked(), true);
  await verify(page, state);
});

test('共同購買平常不顯示每人上限，加到上限才提示', async t => {
  const { page, state } = await fixture(t, { width: 390 });
  await priceRow(page).getByText('一般單價', { exact: true }).waitFor();
  const quantity = page.locator('.offer-quantity');
  assert.equal(await quantity.getByText(/上限|剩餘供應/).count(), 0);
  const plus = page.getByRole('button', { name: '增加數量', exact: true });
  for (let step = 1; step < 5; step++) await plus.click();
  await quantity.getByText('已達每人上限 5 份', { exact: true }).waitFor();
  assert.equal(await plus.isDisabled(), true);
  await page.getByRole('button', { name: '減少數量', exact: true }).click();
  assert.equal(await quantity.getByText(/上限|剩餘供應/).count(), 0);
  await verify(page, state);
});

for (const userMembership of ['nonmember', 'trainee']) test(`共同購買僅限正式社員：${userMembership} 不能報價或付款`, async t => {
  const { page, state } = await fixture(t, { width: 390, userMembership });
  await page.getByText('共同購買僅限正式社員參加', { exact: false }).first().waitFor();
  assert.equal(await page.getByRole('link', { name: '了解如何加入社員' }).getAttribute('href'), '/membership');
  assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
  assert.deepEqual(state.quotes, []);
  await verify(page, state);
});

test('共同購買只開社員免稅個人發票，不提供統編', async t => {
  const { page, state } = await fixture(t, { width: 390 });
  await priceRow(page).getByText('一般單價', { exact: true }).waitFor();
  await page.getByText('社員免稅個人電子發票', { exact: true }).waitFor();
  assert.equal(await page.getByRole('radio', { name: '公司統編發票' }).count(), 0);
  assert.ok(state.quotes.every(quote => quote.invoice_buyer_type === 'personal'));
  await verify(page, state);
});

test('共同購買說明保留換行分段', async t => {
  const description = '第一段說明\n\n🌱 商品特色\n・水耕栽培';
  const { page, state } = await fixture(t, { width: 390, description });
  const copy = page.locator('.offer-copy .campaign-description');
  await copy.waitFor();
  assert.equal(await copy.evaluate(node => getComputedStyle(node).whiteSpace), 'pre-line');
  assert.match(await copy.innerText(), /第一段說明\n\n🌱 商品特色\n・水耕栽培/);
  await verify(page, state);
});

test('共同購買只列出開放的取貨地點並自動選取', async t => {
  const { page, state } = await fixture(t, { pickupLocationIds: ['hall'] });
  const select = page.getByRole('combobox', { name: /取貨地點/ });
  await page.getByRole('option', { name: '隔離行政大樓', exact: true }).waitFor({ state: 'attached' });
  assert.deepEqual(await select.locator('option:not([disabled])').allTextContents(), ['隔離行政大樓']);
  assert.equal(await select.inputValue(), 'hall');
  await verify(page, state);
});

test('共同購買未限定時列出全部取貨地點', async t => {
  const { page, state } = await fixture(t);
  const select = page.getByRole('combobox', { name: /取貨地點/ });
  await page.getByRole('option', { name: '隔離行政大樓', exact: true }).waitFor({ state: 'attached' });
  assert.deepEqual(await select.locator('option:not([disabled])').allTextContents(), ['隔離取貨點', '隔離行政大樓']);
  await verify(page, state);
});
