import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const initialTime = new Date('2030-01-02T10:00:00+08:00');
const order = {
  id: 'contact-order', order_number: 'O2030010201', sales_channel: 'regular', fulfillment_method: 'coop_pickup',
  payment_method: 'online', payment_status: 'paid', invoice_status: 'issued', fulfillment_status: 'pending_confirmation',
  amount_total: 120, tax_amount: 6, created_at: '2030-01-02T01:00:00Z', available_actions: [],
  customer_name: '隔離聯絡人', contact_phone: '0912345678', contact_phone_status: 'available', contact_email: 'contact@example.test',
  invoice_buyer_tax_id: '15989995', invoice_buyer_name: '隔離報帳單位',
  items: [{ id: 'item', product_name: '隔離白米', quantity: 1, unit_label: '袋', unit_price: 120, subtotal: 120, tax_type: 'taxable' }],
};

async function fixture(t, { width = 1280, channel = 'regular', bookingType = 'standard', lateResponse = false, contactEmail = order.contact_email } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { status: 200, reads: [], unknown: [], errors: [], consoleErrors: [], apiRequests: [], release: null };
  const payload = { items: [{ ...order, sales_channel: channel, meal_booking_type: bookingType, contact_email: contactEmail }], total: 1, limit: 12, offset: 0 };
  if (lateResponse) await context.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      if (typeof input === 'string' && input.includes('/v1/admin/order-search?') && input.includes('q=slow')) {
        // Exercise the result guard even if an intermediary ignores cancellation.
        const response = await originalFetch(input, { ...init, signal: undefined });
        await response.clone().json();
        window.contactLateResponseArrived = true;
        return response;
      }
      return originalFetch(input, init);
    };
  });
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {
      const expectedFont = (url.hostname === 'fonts.googleapis.com' && request.resourceType() === 'stylesheet')
        || (url.hostname === 'fonts.gstatic.com' && request.resourceType() === 'font');
      if (!expectedFont) state.unknown.push(`EXTERNAL ${url.origin}`);
      return route.abort();
    }
    if (!path.startsWith('/v1/')) return route.continue();
    state.apiRequests.push(`${request.method()} ${path}`);
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'isolated-admin', email: 'admin@example.test', display_name: '隔離管理者', user_role: 'admin', membership_type: 'nonmember' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '暫停接受新訂單' } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/admin/order-search') {
      state.reads.push(url.search);
      if (url.searchParams.get('q') === 'slow') return new Promise(resolve => {
        state.release = async () => { await route.fulfill({ json: payload }); resolve(); };
      });
      return route.fulfill(state.status === 200 ? { json: payload } : { status: state.status, json: { detail: '隔離讀取失敗' } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') state.consoleErrors.push(message.text()); });
  await page.clock.setFixedTime(initialTime);
  await page.goto(`${base}/admin?section=orders`);
  try { await page.getByRole('link', { name: order.contact_phone, exact: true }).waitFor(); }
  catch (error) {
    t.diagnostic(JSON.stringify({ url: page.url(), errors: state.errors, consoleErrors: state.consoleErrors.slice(0, 10),
      unknown: state.unknown, apiRequests: state.apiRequests, body: (await page.locator('body').innerText()).slice(0, 6000) }));
    throw error;
  }
  return { page, state };
}

async function focusStaleQuery(page) {
  await page.clock.setFixedTime(new Date(initialTime.getTime() + 31_000));
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    window.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    window.dispatchEvent(new Event('visibilitychange'));
  });
}

async function assertNoPrivateContact(page, state, loggedOut = false) {
  if (loggedOut) {
    await page.getByRole('heading', { name: '管理工作台', exact: true }).waitFor();
    await page.getByRole('button', { name: '管理者登入', exact: true }).waitFor();
  } else {
    await page.getByRole('heading', { name: '管理權限待重新驗證', exact: true }).waitFor();
  }
  assert.equal(await page.getByText(order.customer_name, { exact: true }).count(), 0);
  assert.equal(await page.getByRole('link', { name: order.contact_phone, exact: true }).count(), 0);
  assert.equal(await page.getByRole('link', { name: order.contact_email, exact: true }).count(), 0);
  assert.equal(await page.getByText(order.invoice_buyer_tax_id, { exact: true }).count(), 0);
  assert.equal(await page.getByText(order.invoice_buyer_name, { exact: true }).count(), 0);
  assert.equal(await page.getByRole('navigation', { name: '訂單分頁' }).count(), 0);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
}

for (const [channel, bookingType, width] of [['regular', 'standard', 1280], ['meal_preorder', 'standard', 390], ['meal_preorder', 'business', 390]]) {
  test(`${channel} ${bookingType} 後台顯示聯絡資料，會餐另顯示報帳資料`, async t => {
    const { page, state } = await fixture(t, { channel, bookingType, width });
    assert.equal(await page.getByText(order.customer_name, { exact: true }).count(), 1);
    assert.equal(await page.getByRole('link', { name: order.contact_phone, exact: true }).getAttribute('href'), `tel:${order.contact_phone}`);
    assert.equal(await page.getByRole('link', { name: order.contact_email, exact: true }).getAttribute('href'), `mailto:${order.contact_email}`);
    const contact = page.locator('.admin-order-contact');
    assert.equal(await contact.getByText(order.invoice_buyer_tax_id, { exact: true }).count(), bookingType === 'business' ? 1 : 0);
    assert.equal(await contact.getByText(order.invoice_buyer_name, { exact: true }).count(), bookingType === 'business' ? 1 : 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(state.errors, []);
    assert.deepEqual(state.unknown, []);
  });
}

for (const status of [401, 403]) {
  test(`後台訂單 200 → ${status} 清除已顯示的個資`, async t => {
    const { page, state } = await fixture(t, { channel: 'meal_preorder', bookingType: 'business' });
    assert.equal(await page.getByText(order.invoice_buyer_tax_id, { exact: true }).count(), 1);
    state.status = status;
    await focusStaleQuery(page);
    await assertNoPrivateContact(page, state, status === 401);
    assert.ok(state.reads.length >= 2);
  });
}

test('缺少 Email 顯示未提供，長 Email 在手機寬度仍可閱讀', async t => {
  const empty = await fixture(t, { width: 390, contactEmail: null });
  assert.equal(await empty.page.locator('.admin-order-contact > div').filter({ has: empty.page.locator('dt', { hasText: /^Email$/ }) }).locator('dd').innerText(), '未提供');
  const email = `${'a'.repeat(64)}@${'b'.repeat(63)}.example.test`;
  const long = await fixture(t, { width: 390, contactEmail: email });
  assert.equal(await long.page.getByRole('link', { name: email, exact: true }).getAttribute('href'), `mailto:${email}`);
  assert.equal(await long.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(empty.state.errors, []);
  assert.deepEqual(long.state.errors, []);
});

test('權限拒絕後，忽略取消的舊 200 回應不能恢復個資', async t => {
  const { page, state } = await fixture(t, { lateResponse: true });
  const search = page.getByRole('searchbox', { name: '搜尋訂單', exact: true });
  await search.fill('slow');
  const slowRequest = page.waitForRequest(request => new URL(request.url()).searchParams.get('q') === 'slow');
  await page.getByRole('button', { name: '搜尋', exact: true }).click();
  await slowRequest;
  state.status = 403;
  await search.fill('denied');
  await page.getByRole('button', { name: '搜尋', exact: true }).click();
  await assertNoPrivateContact(page, state);
  assert.equal(typeof state.release, 'function');
  await state.release();
  await page.waitForFunction(() => window.contactLateResponseArrived === true);
  await assertNoPrivateContact(page, state);
});

test('暫時 503 不永久封鎖管理權限，恢復後可重新搜尋', async t => {
  const { page, state } = await fixture(t);
  state.status = 503;
  await focusStaleQuery(page);
  await page.getByRole('heading', { name: '訂單資料無法讀取', exact: true }).waitFor();
  assert.equal(await page.getByRole('link', { name: order.contact_phone, exact: true }).count(), 0);
  assert.equal(await page.getByRole('link', { name: order.contact_email, exact: true }).count(), 0);
  assert.equal(await page.getByRole('heading', { name: '管理權限待重新驗證', exact: true }).count(), 0);
  state.status = 200;
  await page.getByRole('searchbox', { name: '搜尋訂單', exact: true }).fill('recovered');
  await page.getByRole('button', { name: '搜尋', exact: true }).click();
  await page.getByRole('link', { name: order.contact_phone, exact: true }).waitFor();
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});
