import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { mealCalendarFixture } from '../fixtures/meal-calendar.mjs';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4196');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use local synthetic fixtures only');
const base = target.origin;
const opens = '2030-01-01T16:00:00Z', closes = '2030-01-02T16:00:00Z';
const meal = { id: 'offering-business', meal_id: 'meal-business', meal_name: '合成便當', description: '', price: 100, available_quantity: 20, capacity: 20, is_active: true,
  option_groups: [{ id: 'rice', name: '主食', min_selections: 1, max_selections: 1, is_active: true, options: ['白飯', '糙米', '無飯'].map((name, index) => ({ id: `rice-${index}`, name, price_delta: 0, is_active: true })) }] };
const event = { id: 'event-business', title: '合成午餐', location: '隔離取餐點', status: 'published', booking_policy: 'special', service_date: '2030-01-02', meal_period: 'lunch', allow_cash_payment: true,
  ordering_starts_at: '2030-01-01T00:00:00+08:00', ordering_ends_at: '2030-01-01T20:00:00+08:00', pickup_starts_at: '2030-01-02T11:00:00+08:00', pickup_ends_at: '2030-01-02T13:30:00+08:00', offerings: [meal] };
function order(overrides = {}) {
  return { id: 'business-order', order_number: 'M2030010101', meal_booking_type: 'business', sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup', meal_event_id: event.id,
    meal_event_title: event.title, venue_name: event.location, pickup_start: event.pickup_starts_at, pickup_end: event.pickup_ends_at, pickup_at: event.pickup_starts_at,
    payment_status: 'pending', payment_method: 'online', invoice_handling: 'pickup_day', invoice_status: 'not_eligible', fulfillment_status: 'pending', amount_total: 200, tax_amount: 10,
    payment_opens_at: opens, payment_closes_at: closes, payment_followup_required: false, available_actions: ['pay'], created_at: '2030-01-01T01:00:00Z',
    items: [{ offering_id: meal.id, meal_id: meal.meal_id, meal_name: meal.meal_name, product_name: meal.meal_name, quantity: 2, unit_price: 100, unit_label: '份', subtotal: 200, tax_type: 'taxable', selections: [] }], ...overrides };
}

async function fixture(t, { time = '2030-01-01T10:00:00+08:00', storedOrder = order(), role = 'customer', mealEvent = event } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { posts: [], reads: [], unknown: [], errors: [], order: storedOrder };
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    state.reads.push(`${request.method()} ${path}${url.search}`);
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'synthetic-token', user: { id: 'synthetic-buyer', email: 'buyer@example.test', display_name: '隔離買家', membership_type: 'nonmember', user_role: role } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, support_phone: '071234567', closed_message: '' } });
    if (path === '/v1/notifications' || path === '/v1/pickup-locations') return route.fulfill({ json: [] });
    if (path === '/v1/products' || path === '/v1/group-campaigns') return route.fulfill({ json: [] });
    if (path === '/v1/meal-events/calendar') return route.fulfill({ json: mealCalendarFixture([mealEvent]) });
    if (path === `/v1/meal-events/${event.id}`) return route.fulfill({ json: mealEvent });
    if (path === '/v1/admin/order-summary' || path === '/v1/admin/order-summary/print-preview') {
      const filters = Object.fromEntries(url.searchParams);
      const summary = { order_count: 1, quantity: 2, product_count: 1, paid_order_count: 0, pending_order_count: 1, inactive_order_count: 0, pending_business_order_count: 1 };
      if (path.endsWith('/print-preview')) return route.fulfill({ json: { fingerprint: 'synthetic-summary', summary, filters: { ...filters, include_unpaid_business: filters.include_unpaid_business === 'true' }, pages: ['會餐 · 未付款 · 合成便當 2份'], page_count: 1, mode: 'dry_run' } });
      return route.fulfill({ json: { ...filters, summary, categories: [{ category: '便當', order_count: 1, quantity: 2 }], channels: [{ sales_channel: 'meal_preorder', order_count: 1, quantity: 2 }], category_options: ['便當'], products: { items: [{ key: 'business-pending', name: meal.meal_name, unit: '份', quantity: 2, order_count: 1, sales_channel: 'meal_preorder', category: '便當', meal_booking_type: 'business', payment_status: 'pending' }], total: 1, offset: 0, limit: 25 } } });
    }
    if (path === '/v1/admin/platform') return route.fulfill({ json: { kitchen_sound: false } });
    if (path === '/v1/admin/printers/status') return route.fulfill({ json: { enabled: false, dry_run: true, status: 'disabled' } });
    if (path === '/v1/admin/printers/jobs') return route.fulfill({ json: { items: [], total: 0 } });
    if (path === '/v1/admin/meal-kitchen') return route.fulfill({ json: { service_date: '2030-01-02', meal_period: 'all', total: 1, limit: 50, offset: 0, meal_quantity: 2,
      products: [{ name: meal.meal_name, unit: '份', quantity: 2, meal_booking_type: 'business', payment_status: 'pending' }], items: [{ ...state.order, customer_name: '隔離買家', event_title: event.title, location: event.location, fulfillment_status: 'pending_confirmation', pickup_time_confirmed: true, invoice_buyer_tax_id: '15989995', invoice_buyer_name: '隔離單位', items: [{ name: meal.meal_name, unit: '份', quantity: 2, options: [] }] }] } });
    if (path === `/v1/meal-events/${event.id}/quote`) {
      const body = request.postDataJSON();
      const quantity = body.items.reduce((sum, item) => sum + item.quantity, 0);
      const amount = quantity * 100;
      const tax = body.invoice_buyer_type === 'company' ? amount - Math.round(amount / 1.05) : Math.floor(amount * 5 / 105);
      return route.fulfill({ json: { amount_total: amount, tax_amount: tax, needs_utensils: false, items: body.items.map(item => ({ ...item, meal_name: meal.meal_name, unit_price: 100, subtotal: item.quantity * 100, tax_type: 'taxable', selections: [] })), pickup: { location: event.location, starts_at: event.pickup_starts_at, ends_at: event.pickup_ends_at, address: '', instructions: '', location_id: null } } });
    }
    if (request.method() === 'POST') {
      state.posts.push({ path, body: request.postData() ? request.postDataJSON() : null });
      if (path === `/v1/meal-events/${event.id}/orders`) return route.fulfill({ status: 201, json: state.order });
      if (path === '/v1/orders/business-order/payment-attempts') return route.fulfill({ json: { payment_url: `${base}/synthetic-payment` } });
    }
    if (path === '/v1/meal-orders') return route.fulfill({ json: [state.order] });
    if (path === '/v1/meal-orders/business-order') return route.fulfill({ json: state.order });
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [{ ...state.order, customer_name: '隔離買家', contact_email: 'buyer@example.test', contact_phone_status: 'missing' }], total: 1, offset: 0, limit: 12 } });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined synthetic API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.install({ time: new Date(time) });
  return { page, state };
}

test('390px會餐切換保留一般偏好，統編必填且錯誤延後顯示', async t => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/meals/${event.id}?pickup_at=2030-01-02T11%3A00`);
  await page.getByRole('radio', { name: '會餐（統編報帳）', exact: true }).check();
  assert.equal(await page.getByRole('combobox', { name: '開票時機', exact: true }).count(), 0);
  assert.equal(await page.getByRole('radio', { name: '個人電子發票', exact: true }).count(), 0);
  assert.equal(await page.getByLabel('統一編號', { exact: true }).getAttribute('required'), '');
  assert.equal(await page.getByText('請填寫有效的 8 碼統一編號', { exact: true }).count(), 0);
  await page.getByRole('button', { name: '增加合成便當', exact: true }).click();
  assert.equal(await page.getByText('請完成此項選擇', { exact: true }).count(), 0);
  assert.equal(await page.getByText('會餐至少 2 份', { exact: true }).count(), 0);
  await page.getByRole('button', { name: '送出會餐訂單（取餐當日付款）', exact: true }).click();
  await page.getByText('會餐至少 2 份', { exact: true }).waitFor();
  await page.getByText('請填寫有效的 8 碼統一編號', { exact: true }).waitFor();
  assert.equal(state.posts.length, 0);
  await page.getByRole('radio', { name: '一般餐點', exact: true }).check();
  await page.getByRole('button', { name: '修改發票設定', exact: true }).click();
  assert.equal(await page.getByRole('radio', { name: '個人電子發票', exact: true }).getAttribute('aria-checked'), 'true');
  assert.equal(await page.getByRole('combobox', { name: '開票時機', exact: true }).count(), 0);
  assert.equal(await page.getByText('發票於取餐當日開立', { exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('僅開放會餐的場次自動使用統編報帳，普通買家可送出會餐預訂', async t => {
  const { page, state } = await fixture(t, { mealEvent: { ...event, business_only: true } });
  await page.goto(`${base}/shop`);
  await page.getByRole('link', { name: '隔離取餐點 11:00 · 會餐 取餐', exact: true }).click();
  await page.getByText('會餐（統編報帳）', { exact: true }).waitFor();
  assert.equal(await page.getByRole('radio', { name: '一般餐點', exact: true }).count(), 0);
  assert.equal(await page.getByRole('radio', { name: '合作社現場付款', exact: true }).count(), 0);
  const taxId = page.getByLabel('統一編號', { exact: true });
  assert.equal(await taxId.getAttribute('required'), '');
  await page.getByRole('button', { name: '增加合成便當', exact: true }).click({ clickCount: 2 });
  await page.getByRole('combobox', { name: '合成便當 主食', exact: true }).selectOption('rice-0');
  const submit = page.getByRole('button', { name: '送出會餐訂單（取餐當日付款）', exact: true });
  await submit.click();
  await page.getByText('請填寫有效的 8 碼統一編號', { exact: true }).waitFor();
  assert.deepEqual(state.posts, []);
  await taxId.fill('15989995');
  const quoted = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/quote'));
  await page.getByLabel('公司名稱', { exact: true }).fill('隔離單位');
  await quoted;
  await submit.click();
  await page.getByRole('heading', { name: '會餐預訂成功', exact: true }).waitFor();
  assert.equal(state.posts.length, 1);
  assert.equal(state.posts[0].body.meal_booking_type, 'business');
  assert.equal(state.posts[0].body.invoice_handling, 'pickup_day');
  assert.equal(state.posts[0].body.invoice_buyer_tax_id, '15989995');
  assert.equal(state.posts[0].body.payment_method, 'online');
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('重整保留會餐訂餐方式，清空草稿後回復一般餐點', async t => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/meals/${event.id}?pickup_at=2030-01-02T11%3A00`);
  await page.getByRole('radio', { name: '會餐（統編報帳）', exact: true }).check();
  await page.getByRole('button', { name: '增加合成便當', exact: true }).click();
  await page.reload();
  assert.equal(await page.getByRole('radio', { name: '會餐（統編報帳）', exact: true }).isChecked(), true);
  await page.getByRole('button', { name: '清空選餐與取餐時間', exact: true }).click();
  assert.equal(await page.getByRole('radio', { name: '一般餐點', exact: true }).isChecked(), true);
  assert.equal(state.posts.length, 0);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('390px 從日期摘要進入會餐，建單送公司統編與兩份且不提前付款', async t => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/shop`);
  await page.getByRole('link', { name: '隔離取餐點 11:00 取餐', exact: true }).click();
  await page.waitForURL(url => url.pathname === `/meals/${event.id}` && url.searchParams.get('pickup_at') === '2030-01-02T11:00');
  await page.getByRole('radio', { name: '會餐（統編報帳）', exact: true }).check();
  await page.getByRole('button', { name: '增加合成便當', exact: true }).click({ clickCount: 2 });
  await page.getByRole('combobox', { name: '合成便當 主食', exact: true }).selectOption('rice-0');
  await page.getByLabel('統一編號', { exact: true }).fill('15989995');
  const quoted = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/quote'));
  await page.getByLabel('公司名稱', { exact: true }).fill('隔離單位');
  const submit = page.getByRole('button', { name: '送出會餐訂單（取餐當日付款）', exact: true });
  await quoted;
  await page.getByText('$10', { exact: true }).waitFor();
  if (process.env.BUSINESS_SCREENSHOT) await page.screenshot({ path: process.env.BUSINESS_SCREENSHOT, fullPage: true });
  await submit.click();
  await page.waitForURL('**/meal-orders?order_id=business-order');
  await page.getByRole('heading', { name: '會餐預訂成功', exact: true }).waitFor();
  assert.equal(state.posts.length, 1);
  assert.equal(state.posts[0].body.meal_booking_type, 'business');
  assert.equal(state.posts[0].body.payment_method, 'online');
  assert.equal(state.posts[0].body.invoice_handling, 'pickup_day');
  assert.equal(state.posts[0].body.invoice_buyer_type, 'company');
  assert.equal(state.posts[0].body.invoice_buyer_tax_id, '15989995');
  assert.equal(state.posts[0].body.items[0].quantity, 2);
  assert.equal(state.posts[0].body.pickup_at, '2030-01-02T03:00:00.000Z');
  const calendarRead = state.reads.indexOf('GET /v1/meal-events/calendar');
  assert.ok(calendarRead >= 0 && state.reads.indexOf(`GET /v1/meal-events/${event.id}`) > calendarRead);
  assert.equal(state.reads.includes('GET /v1/meal-events'), false);
  assert.equal(await page.getByRole('button', { name: '前往付款 $200', exact: true }).isDisabled(), true);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('取餐日00:00前停用付款，跨日後開放，結束當下立即停用', async t => {
  const { page, state } = await fixture(t, { time: '2030-01-01T23:59:59+08:00', storedOrder: order({ pickup_code: '123456', pickup_qr_payload: 'synthetic-unpaid-credential' }) });
  await page.goto(`${base}/meal-orders`);
  const pay = page.getByRole('button', { name: '前往付款 $200', exact: true });
  await pay.waitFor();
  assert.equal(await pay.isDisabled(), true);
  assert.equal(await page.getByText('123456', { exact: true }).count(), 0);
  assert.equal(await page.locator('.pickup-ticket').count(), 0);
  await page.clock.fastForward(2000);
  await page.getByRole('heading', { name: '會餐待付款', exact: true }).waitFor();
  assert.equal(await pay.isEnabled(), true);
  assert.equal(await page.locator('.pickup-ticket').count(), 0);
  await page.clock.fastForward(86_400_000);
  await page.getByRole('heading', { name: '會餐付款已逾期', exact: true }).waitFor();
  assert.equal(await pay.count(), 0);
  assert.equal(state.posts.length, 0);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('當日仍須API付款權限；缺時段或權限時禁止送出付款', async t => {
  const { page, state } = await fixture(t, { time: '2030-01-02T10:00:00+08:00', storedOrder: order({ available_actions: [] }) });
  await page.goto(`${base}/meal-orders`);
  await page.getByRole('heading', { name: '付款資格確認中', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '前往付款 $200', exact: true }).isDisabled(), true);
  assert.equal(state.posts.length, 0);
  assert.deepEqual(state.errors, []);
});

test('取餐當日付款按鈕只建立原會餐的金流交易', async t => {
  const { page, state } = await fixture(t, { time: '2030-01-02T10:00:00+08:00' });
  await page.goto(`${base}/meal-orders`);
  await page.getByRole('heading', { name: '會餐待付款', exact: true }).waitFor();
  await page.getByRole('button', { name: '前往付款 $200', exact: true }).click();
  await page.waitForURL('**/synthetic-payment');
  assert.deepEqual(state.posts.map(post => post.path), ['/v1/orders/business-order/payment-attempts']);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('已付款會餐的發票異常顯示待合作社處理，不再提供付款', async t => {
  const { page, state } = await fixture(t, { time: '2030-01-03T10:00:00+08:00', storedOrder: order({ payment_status: 'paid', available_actions: [], invoice_status: 'pending', invoice_followup_required: true }) });
  await page.goto(`${base}/meal-orders`);
  await page.getByText('發票待合作社處理', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: /^前往付款/ }).count(), 0);
  assert.deepEqual(state.posts, []);
  assert.deepEqual(state.errors, []);
});

test('後台會餐未付款篩選帶入專用條件，顯示逾期追蹤標記', async t => {
  const { page, state } = await fixture(t, { role: 'admin', storedOrder: order({ payment_followup_required: true }) });
  await page.goto(`${base}/admin?section=orders`);
  await page.getByText('會餐 · 未付款待追蹤', { exact: true }).waitFor();
  const filtered = page.waitForRequest(request => new URL(request.url()).searchParams.get('business_unpaid') === 'true');
  await page.getByRole('checkbox', { name: '會餐未付款', exact: true }).check();
  await filtered;
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('彙整分辨未付款會餐，列印開關明確帶入預覽範圍', async t => {
  const { page, state } = await fixture(t, { role: 'admin' });
  await page.goto(`${base}/admin?section=summary`);
  await page.getByText('會餐 · 未付款', { exact: true }).waitFor();
  await page.getByRole('checkbox', { name: '列印時含未付款會餐', exact: true }).check();
  const changed = page.waitForResponse(response => new URL(response.url()).pathname === '/v1/admin/order-summary' && new URL(response.url()).searchParams.get('include_unpaid_business') === 'true');
  await page.getByRole('button', { name: '查詢', exact: true }).click();
  await changed;
  await page.getByRole('button', { name: '列印彙總單', exact: true }).click();
  await page.getByText('已付款訂單及未付款會餐，均須未取消', { exact: true }).waitFor();
  assert.ok(state.reads.some(path => path.includes('/print-preview?') && path.includes('include_unpaid_business=true')));
  assert.equal(state.posts.length, 0);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('廚房清單與數量彙總可辨識會餐及未付款', async t => {
  const { page, state } = await fixture(t, { role: 'admin', time: '2030-01-02T10:00:00+08:00' });
  await page.goto(`${base}/admin/kitchen`);
  await page.getByText('會餐 · 未付款', { exact: true }).waitFor();
  await page.getByText('合成便當（會餐／未付款）：2 份', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});
