import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { mkdir } from 'node:fs/promises';
import { mealCalendarFixture } from '../fixtures/meal-calendar.mjs';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4199');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname) || !['http:', 'https:'].includes(testUrl.protocol) || testUrl.pathname !== '/' || testUrl.username || testUrl.password || testUrl.search || testUrl.hash) throw new Error('Workday acceptance requires an isolated local website');
const base = testUrl.origin;
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const meal = { id: 'isolated-meal', name: '工作日測試便當', price: 120, description: '', is_active: true, tax_type: 'taxable', option_groups: [] };
function event(id, date, overrides = {}) {
  return { id, booking_policy: 'workday', title: '隔離供餐場次', location: '隔離取餐點', status: 'published', service_date: date, meal_period: 'lunch',
    ordering_starts_at: '2026-11-06T00:00:00+08:00', ordering_ends_at: '2026-11-08T20:00:00+08:00',
    booking_starts_at: '2026-11-06T00:00:00+08:00', booking_ends_at: '2026-11-08T20:00:00+08:00',
    pickup_starts_at: `${date}T11:00:00+08:00`, pickup_ends_at: `${date}T13:30:00+08:00`,
    offerings: [{ id: 'isolated-offering', meal_id: meal.id, meal_name: meal.name, description: '', price: 120, capacity: 20, reserved_quantity: 0, paid_quantity: 0, available_quantity: 20, position: 0, is_active: true, option_groups: [] }], ...overrides };
}
async function fixture(t, width, { events = [event('monday', '2026-11-09')], now = '2026-11-06T12:00:00+08:00', admin = false, schedules = [], adminEvents = [] } = {}) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const unknown = [], created = [];
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    if (method === 'POST' && url.pathname === '/v1/auth/refresh') return route.fulfill(admin
      ? { json: { access_token: 'isolated-admin-token', user: { id: 'isolated-admin', user_role: 'admin', email: 'admin@example.test', display_name: '隔離管理員' } } }
      : { status: 401, json: { detail: '隔離未登入' } });
    if (method === 'GET') {
      if (url.pathname === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '', support_phone: '' } });
      if (url.pathname === '/v1/meal-events/calendar') return route.fulfill({ json: mealCalendarFixture(events) });
      const detail = events.find(({ id }) => url.pathname === `/v1/meal-events/${id}`);
      if (detail) return route.fulfill({ json: detail });
      if (url.pathname === '/v1/pickup-locations') return route.fulfill({ json: [] });
      if (url.pathname === '/v1/admin/meals') return route.fulfill({ json: [meal] });
      if (url.pathname === '/v1/admin/meal-schedules') return route.fulfill({ json: schedules });
      if (url.pathname === '/v1/admin/meal-events') return route.fulfill({ json: adminEvents });
      if (['/v1/products', '/v1/group-campaigns'].includes(url.pathname)) return route.fulfill({ json: [] });
      if (url.pathname === '/v1/admin/meal-calendar') {
        const date = url.searchParams.get('service_date') || '2026-11-09';
        return route.fulfill({ json: { service_date: date, is_workday: date === '2026-11-09', previous_workday: '2026-11-06', next_workday: date === '2026-11-09' ? '2026-11-10' : '2026-11-09', ordering_starts_at: '2026-11-05T16:00:00Z', ordering_ends_at: date === '2026-11-09' ? '2026-11-08T12:00:00Z' : '2026-11-06T12:00:00Z' } });
      }
      if (url.pathname === '/v1/admin/meal-schedules/calendar-status') return route.fulfill({ json: { available: true, from_date: '2025-01-01', to_date: '2027-12-31', message: '', source_url: 'https://data.gov.tw/dataset/14718' } });
    }
    if (method === 'POST' && (url.pathname === '/v1/admin/meal-events' || /^\/v1\/admin\/meal-events\/[^/]+\/duplicate$/.test(url.pathname))) {
      created.push(request.postDataJSON()); return route.fulfill({ status: 201, json: { id: 'created-special' } });
    }
    unknown.push(`${method} ${url.pathname}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.clock.install({ time: new Date(now) });
  return { page, unknown, created };
}
async function noOverflow(page) { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true); }
async function screenshot(page, name) {
  if (!process.env.BROWSER_SCREENSHOT_DIR) return;
  await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
  await page.evaluate(() => { document.activeElement?.blur(); });
  await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/${name}.png`, fullPage: true });
}
for (const width of [390, 1440]) {
  test(`${width}px 週五預設選週一並顯示真實日期與可預訂時段`, { timeout: 30000 }, async t => {
    const { page, unknown } = await fixture(t, width);
    await page.goto(`${base}/shop`);
    const navigation = page.getByRole('region', { name: '便當日期與取餐時間' });
    await navigation.getByRole('button', { name: '11/9 週一，可預訂', exact: true }).waitFor();
    assert.equal(await navigation.getByRole('button', { name: '11/9 週一，可預訂' }).getAttribute('aria-pressed'), 'true');
    const pickup = navigation.getByRole('link', { name: '隔離取餐點 12:00 取餐', exact: true });
    assert.match(await pickup.getAttribute('href'), /\/meals\/monday\?pickup_at=2026-11-09T12/);
    assert.equal(await navigation.getByText(/明天/).count(), 0);
    await noOverflow(page);
    assert.deepEqual(unknown, []);
    await screenshot(page, `workday-monday-${width}`);
  });
}
test('假日後週二與管理員週末加開均能切換，不會自行補出週一假日', { timeout: 30000 }, async t => {
  const { page, unknown } = await fixture(t, 390, { now: '2026-10-23T12:00:00+08:00', events: [
    event('holiday-tuesday', '2026-10-27', { booking_starts_at: '2026-10-23T00:00:00+08:00', booking_ends_at: '2026-10-26T20:00:00+08:00' }),
    event('special-saturday', '2026-10-24', { booking_policy: 'special', booking_starts_at: '2026-10-22T00:00:00+08:00', booking_ends_at: '2026-10-23T20:00:00+08:00' }),
  ] });
  await page.goto(`${base}/shop`);
  const navigation = page.getByRole('region', { name: '便當日期與取餐時間' });
  await navigation.getByRole('button', { name: '10/24 週六，可預訂' }).click();
  assert.match(await navigation.getByRole('link', { name: '隔離取餐點 12:00 取餐' }).getAttribute('href'), /special-saturday/);
  await navigation.getByRole('button', { name: '10/27 週二，可預訂' }).click();
  assert.match(await navigation.getByRole('link', { name: '隔離取餐點 12:00 取餐' }).getAttribute('href'), /holiday-tuesday/);
  assert.equal(await navigation.getByRole('button', { name: /10\/26/ }).count(), 0);
  await noOverflow(page); assert.deepEqual(unknown, []);
});
test('管理員預設採後端工作日，週末須明確改特殊加開才可建立', { timeout: 30000 }, async t => {
  const { page, unknown, created } = await fixture(t, 390, { admin: true });
  await page.goto(`${base}/admin/meals?tab=events`);
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: '建立便當場次', exact: true }) });
  await form.getByLabel('取餐開始（台灣時間）', { exact: true }).waitFor().catch(async error => { t.diagnostic(await page.locator('body').innerText()); t.diagnostic(JSON.stringify(unknown)); throw error; });
  await page.waitForFunction(() => [...document.querySelectorAll('label')].find(e => e.textContent === '取餐開始（台灣時間）')?.querySelector('input')?.value === '2026-11-09T11:00');
  assert.equal(await form.getByLabel('開放預訂（台灣時間）', { exact: true }).inputValue(), '2026-11-06T00:00');
  assert.equal(await form.getByLabel('預訂截止（台灣時間）', { exact: true }).inputValue(), '2026-11-08T20:00');
  await form.getByLabel('場次名稱', { exact: true }).fill('週六特殊餐');
  await form.getByLabel('取餐地點', { exact: true }).fill('隔離取餐點');
  await form.getByLabel('工作日測試便當').check();
  await form.getByLabel('取餐開始（台灣時間）', { exact: true }).fill('2026-11-07T11:00');
  await form.getByRole('alert').filter({ hasText: '這一天不是工作日' }).waitFor();
  assert.equal(await form.getByRole('button', { name: '建立草稿場次' }).isDisabled(), true);
  await form.getByRole('button', { name: '特殊日期加開', exact: true }).click();
  await noOverflow(page);
  await screenshot(page, 'workday-admin-special-390');
  await form.getByRole('button', { name: '建立草稿場次' }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('label')].find(e => e.textContent === '場次名稱')?.querySelector('input')?.value === '');
  assert.equal(created.length, 1);
  assert.equal(created[0].booking_policy, 'special');
  assert.equal(created[0].pickup_starts_at, '2026-11-07T03:00:00.000Z');
  assert.equal(created[0].ordering_ends_at, '2026-11-06T12:00:00.000Z');
  assert.deepEqual(unknown, []);
});

test('每日排程預設週一至五，既有週六與當天截單設定顯示有效規則', { timeout: 30000 }, async t => {
  const { page, unknown } = await fixture(t, 390, { admin: true, schedules: [{
    id: 'schedule', title: '既有午餐排程', location: '隔離取餐點', meal_period: 'lunch', timezone: 'Asia/Taipei',
    pickup_start_time: '11:00', pickup_end_time: '13:00', cutoff_time: '00:00', cutoff_days_before: 0,
    advance_days: 7, weekdays: [0, 1, 2, 3, 4, 5], enabled: true, auto_publish: true,
    offerings: [{ meal_id: meal.id, price: 120, capacity: 20, position: 0 }],
  }] });
  await page.goto(`${base}/admin/meals?tab=schedules`);
  await page.getByText('工作日日曆已就緒', { exact: true }).waitFor();
  const form = page.locator('form');
  for (const day of ['週一', '週二', '週三', '週四', '週五']) assert.equal(await form.getByLabel(day, { exact: true }).isChecked(), true);
  assert.equal(await form.getByLabel('週六', { exact: true }).count(), 0);
  const card = page.locator('article').filter({ has: page.getByRole('heading', { name: '既有午餐排程', exact: true }) });
  assert.match(await card.innerText(), /取餐前 1 天 20:00/);
  assert.match(await card.innerText(), /週六（需特殊加開）/);
  await card.getByRole('button', { name: '編輯排程' }).click();
  assert.equal(await form.getByLabel('取餐前幾天截單', { exact: true }).inputValue(), '1');
  assert.equal(await form.getByLabel('每日截單時間', { exact: true }).inputValue(), '20:00');
  await form.getByLabel('週六（需特殊加開）', { exact: true }).uncheck();
  assert.equal(await form.getByLabel('週六（需特殊加開）', { exact: true }).isChecked(), false);
  await noOverflow(page); assert.deepEqual(unknown, []);
});

test('管理員須明確填入當日截止與備餐時間才建立單場設定', { timeout: 30000 }, async t => {
  const { page, unknown, created } = await fixture(t, 390, { admin: true });
  await page.goto(`${base}/admin/meals?tab=events`);
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: '建立便當場次', exact: true }) });
  const sameDay = form.getByRole('checkbox', { name: '開放當日訂餐（僅線上付款）', exact: true });
  await sameDay.waitFor();
  assert.equal(await sameDay.isChecked(), false);
  await form.getByRole('button', { name: '特殊日期加開', exact: true }).click();
  await form.getByLabel('場次名稱', { exact: true }).fill('當日加開');
  await form.getByLabel('取餐地點', { exact: true }).fill('隔離取餐點');
  await form.getByLabel('工作日測試便當').check();
  await form.getByLabel('取餐開始（台灣時間）', { exact: true }).fill('2026-11-07T11:00');
  await form.getByLabel('開放預訂（台灣時間）', { exact: true }).fill('2026-11-07T08:00');
  await sameDay.check();
  const preparation = form.getByLabel('最短備餐時間（分鐘）', { exact: true });
  const cutoff = form.getByLabel('預訂截止（台灣時間）', { exact: true });
  const submit = form.getByRole('button', { name: '建立草稿場次' });
  assert.equal(await preparation.inputValue(), '');
  assert.equal(await cutoff.inputValue(), '');
  await preparation.fill('60');
  await cutoff.fill('2026-11-06T10:45');
  await form.getByRole('alert').filter({ hasText: '截止日期須與取餐開始日期相同' }).waitFor();
  assert.equal(await submit.isDisabled(), true);
  assert.equal(created.length, 0);

  await cutoff.fill('2026-11-07T11:45');
  const businessOnly = form.getByRole('checkbox', { name: '僅開放會餐', exact: true });
  await businessOnly.check();
  assert.equal(await sameDay.isDisabled(), true);
  assert.equal(await preparation.count(), 0);
  assert.equal(await cutoff.inputValue(), '2026-11-06T20:00');
  await businessOnly.uncheck();
  assert.equal(await preparation.inputValue(), '60');
  assert.equal(await cutoff.inputValue(), '2026-11-07T11:45');
  await submit.click();
  await page.waitForFunction(() => [...document.querySelectorAll('label')].find(e => e.textContent === '場次名稱')?.querySelector('input')?.value === '');
  assert.equal(created.length, 1);
  assert.equal(created[0].same_day_preparation_minutes, 60);
  assert.equal(created[0].ordering_ends_at, '2026-11-07T03:45:00.000Z');
  assert.equal(created[0].allow_cash_payment, false);
  assert.equal(await sameDay.isChecked(), false);
  await noOverflow(page);
  assert.deepEqual(unknown, []);
});

test('當日一般便當只顯示來得及備餐的時段，切到會餐後保留選擇並阻擋送單', { timeout: 30000 }, async t => {
  const date = '2026-11-07';
  const sameDay = event('same-day', date, {
    booking_policy: 'special',
    same_day_preparation_minutes: 60,
    ordering_starts_at: `${date}T08:00:00+08:00`,
    booking_starts_at: `${date}T08:00:00+08:00`,
    ordering_ends_at: `${date}T11:45:00+08:00`,
    booking_ends_at: `${date}T11:45:00+08:00`,
    allow_cash_payment: false,
  });
  const { page, unknown } = await fixture(t, 390, { now: `${date}T10:30:00+08:00`, events: [sameDay], admin: true });
  await page.goto(`${base}/shop`);
  const navigation = page.getByRole('region', { name: '便當日期與取餐時間' });
  await navigation.getByRole('button', { name: '11/7 週六，可預訂', exact: true }).waitFor();
  assert.equal(await navigation.getByRole('link', { name: '隔離取餐點 11:30 取餐', exact: true }).count(), 0);
  await navigation.getByRole('link', { name: '隔離取餐點 12:00 取餐', exact: true }).click();
  const pickupTime = page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true });
  await pickupTime.waitFor();
  assert.equal(await pickupTime.inputValue(), `${date}T12:00`);
  assert.deepEqual(await pickupTime.locator('option').evaluateAll(options => options.map(option => option.value)), [
    '', `${date}T12:00`, `${date}T12:30`, `${date}T13:00`,
  ]);
  assert.match(await page.locator('.meal-booking-deadline').innerText(), /11:00/);
  assert.equal(await page.getByRole('radio', { name: '合作社現場付款', exact: true }).count(), 0);

  const business = page.getByRole('radio', { name: '會餐（統編報帳）', exact: true });
  await business.check();
  await page.getByText('會餐預訂已截止。', { exact: true }).waitFor();
  assert.equal(await business.isChecked(), true);
  assert.equal(await page.getByRole('button', { name: '送出會餐訂單（取餐當日付款）' }).isDisabled(), true);
  assert.match(await page.locator('.meal-booking-deadline').innerText(), /20:00/);
  await page.getByRole('radio', { name: '一般餐點', exact: true }).check();
  assert.equal(await pickupTime.inputValue(), `${date}T12:00`);
  await noOverflow(page);
  assert.deepEqual(unknown, []);
});

test('週六場次可手動只開放會餐，場次卡與複製草稿保留會餐規則', { timeout: 30000 }, async t => {
  const date = '2026-11-07';
  const existing = event('saturday-business', date, {
    title: '隔離週六會餐', booking_policy: 'special', status: 'draft', business_only: true, can_edit: true,
    ordering_ends_at: '2026-11-06T20:00:00+08:00',
  });
  const { page, unknown, created } = await fixture(t, 390, { admin: true, adminEvents: [existing] });
  await page.goto(`${base}/admin/meals?tab=events`);
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: '建立便當場次', exact: true }) });
  const businessOnly = form.getByRole('checkbox', { name: '僅開放會餐', exact: true });
  await businessOnly.waitFor();
  assert.equal(await businessOnly.isChecked(), false);
  await form.getByRole('button', { name: '特殊日期加開', exact: true }).click();
  await form.getByLabel('場次名稱', { exact: true }).fill('週六僅會餐');
  await form.getByLabel('取餐地點', { exact: true }).fill('隔離新取餐點');
  await form.getByLabel('工作日測試便當').check();
  await form.getByLabel('取餐開始（台灣時間）', { exact: true }).fill(`${date}T11:00`);
  await businessOnly.check();
  assert.equal(await form.getByLabel('預訂截止（台灣時間）', { exact: true }).inputValue(), '2026-11-06T20:00');
  await form.getByRole('button', { name: '建立草稿場次' }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('label')].find(e => e.textContent === '場次名稱')?.querySelector('input')?.value === '');
  assert.equal(created[0].business_only, true);
  assert.equal(created[0].allow_cash_payment, false);
  assert.equal(created[0].same_day_preparation_minutes, null);
  assert.equal(created[0].ordering_ends_at, '2026-11-06T12:00:00.000Z');

  const card = page.locator('article').filter({ has: page.getByRole('heading', { name: existing.title, exact: true }) });
  await card.getByText('僅開放會餐', { exact: true }).waitFor();
  assert.equal(await card.getByRole('checkbox', { name: '開放合作社現場付款' }).count(), 0);
  const duplicated = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/duplicate'));
  await card.getByRole('button', { name: '複製至隔日' }).click();
  await duplicated;
  assert.equal(created[1].business_only, true);
  assert.equal(created[1].allow_cash_payment, false);
  await card.getByRole('button', { name: '手動編輯場次' }).click();
  const editForm = card.locator('form').filter({ has: page.getByRole('heading', { name: '手動編輯便當場次', exact: true }) });
  assert.equal(await editForm.getByRole('checkbox', { name: '僅開放會餐' }).isChecked(), true);
  await noOverflow(page);
  assert.deepEqual(unknown, []);
});
