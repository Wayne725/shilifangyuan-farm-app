import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { mkdir } from 'node:fs/promises';

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
async function fixture(t, width, { events = [event('monday', '2026-11-09')], now = '2026-11-06T12:00:00+08:00', admin = false, schedules = [] } = {}) {
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
      if (url.pathname === '/v1/meal-events') return route.fulfill({ json: events });
      if (url.pathname === '/v1/admin/meals') return route.fulfill({ json: [meal] });
      if (url.pathname === '/v1/admin/meal-schedules') return route.fulfill({ json: schedules });
      if (['/v1/products', '/v1/group-campaigns', '/v1/admin/meal-events'].includes(url.pathname)) return route.fulfill({ json: [] });
      if (url.pathname === '/v1/admin/meal-calendar') {
        const date = url.searchParams.get('service_date') || '2026-11-09';
        return route.fulfill({ json: { service_date: date, is_workday: date === '2026-11-09', previous_workday: '2026-11-06', next_workday: date === '2026-11-09' ? '2026-11-10' : '2026-11-09', ordering_starts_at: '2026-11-05T16:00:00Z', ordering_ends_at: date === '2026-11-09' ? '2026-11-08T12:00:00Z' : '2026-11-06T12:00:00Z' } });
      }
      if (url.pathname === '/v1/admin/meal-schedules/calendar-status') return route.fulfill({ json: { available: true, from_date: '2025-01-01', to_date: '2027-12-31', message: '', source_url: 'https://data.gov.tw/dataset/14718' } });
    }
    if (method === 'POST' && url.pathname === '/v1/admin/meal-events') {
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
