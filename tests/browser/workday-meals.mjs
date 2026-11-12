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
async function fixture(t, width, { events = [event('monday', '2026-11-09')], now = '2026-11-06T12:00:00+08:00', admin = false, schedules = [], adminEvents = [], eventOrders = {}, pauseConflict = false, capacityConflict = false, disablePreview = null, changedDisablePreview = null, scheduleUpdateConflict = false } = {}) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const unknown = [], created = [], pauseRequests = [], capacityRequests = [], disableRequests = [], disablePreviewReads = [], scheduleUpdateRequests = [];
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    if (method === 'POST' && url.pathname === '/v1/auth/refresh') return route.fulfill(admin
      ? { json: { access_token: 'isolated-admin-token', user: { id: 'isolated-admin', user_role: 'admin', email: 'admin@example.test', display_name: '隔離管理員' } } }
      : { status: 401, json: { detail: '隔離未登入' } });
    if (method === 'GET') {
      if (url.pathname === '/v1/meal-checkout/contact') return route.fulfill({ json: { phone: '0912345678' } });
      if (url.pathname === '/v1/meal-announcements') return route.fulfill({ json: [] });
      if (url.pathname === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '', support_phone: '' } });
      if (url.pathname === '/v1/meal-events/calendar') return route.fulfill({ json: mealCalendarFixture(events) });
      const detail = events.find(({ id }) => url.pathname === `/v1/meal-events/${id}`);
      if (detail) return route.fulfill({ json: detail });
      if (url.pathname === '/v1/pickup-locations') return route.fulfill({ json: [] });
      if (url.pathname === '/v1/admin/meals') return route.fulfill({ json: [meal] });
      if (url.pathname === '/v1/admin/meal-schedules') return route.fulfill({ json: schedules });
      if (/^\/v1\/admin\/meal-schedules\/[^/]+\/disable-preview$/.test(url.pathname)) {
        disablePreviewReads.push(disablePreview?.preview_version);
        return disablePreview ? route.fulfill({ json: disablePreview })
          : route.fulfill({ status: 404, json: { detail: 'Not Found' } });
      }
      if (url.pathname === '/v1/admin/meal-events') return route.fulfill({ json: adminEvents });
      const orderEvent = url.pathname.match(/^\/v1\/admin\/meal-events\/([^/]+)\/orders$/);
      if (orderEvent && Object.hasOwn(eventOrders, orderEvent[1])) return route.fulfill({ json: eventOrders[orderEvent[1]] });
      if (['/v1/products', '/v1/group-campaigns'].includes(url.pathname)) return route.fulfill({ json: [] });
      if (url.pathname === '/v1/admin/meal-calendar') {
        const date = url.searchParams.get('service_date') || '2026-11-09';
        return route.fulfill({ json: { service_date: date, is_workday: date === '2026-11-09', previous_workday: '2026-11-06', next_workday: date === '2026-11-09' ? '2026-11-10' : '2026-11-09', ordering_starts_at: '2026-11-05T16:00:00Z', ordering_ends_at: date === '2026-11-09' ? '2026-11-08T12:00:00Z' : '2026-11-06T12:00:00Z' } });
      }
      if (url.pathname === '/v1/admin/meal-schedules/calendar-status') return route.fulfill({ json: { available: true, from_date: '2025-01-01', to_date: '2027-12-31', message: '', source_url: 'https://data.gov.tw/dataset/14718' } });
    }
    const scheduleId = url.pathname.match(/^\/v1\/admin\/meal-schedules\/([^/]+)$/)?.[1];
    if (method === 'PUT' && scheduleId && scheduleUpdateConflict) {
      scheduleUpdateRequests.push(request.postDataJSON());
      const schedule = schedules.find(({ id }) => id === scheduleId);
      schedule.enabled = false;
      schedule.updated_at = '2026-11-06T04:05:00Z';
      return route.fulfill({ status: 409, json: { detail: '每日供餐設定已更新，請重新開啟編輯後再試' } });
    }
    const disableId = url.pathname.match(/^\/v1\/admin\/meal-schedules\/([^/]+)\/disable$/)?.[1];
    if (method === 'POST' && disableId && disablePreview) {
      const body = request.postDataJSON();
      disableRequests.push(body);
      if (changedDisablePreview) {
        disablePreview = changedDisablePreview;
        changedDisablePreview = null;
      }
      if (body.preview_version !== disablePreview.preview_version) {
        return route.fulfill({ status: 409, json: { detail: '預覽資料已變動，請重新預覽' } });
      }
      const schedule = schedules.find(({ id }) => id === disableId);
      schedule.enabled = false;
      const pausedCount = body.scope === 'pause_generated'
        ? disablePreview.events.filter(event => ['open', 'scheduled'].includes(event.ordering_status)).length : 0;
      return route.fulfill({ json: { paused_event_count: pausedCount } });
    }
    const pauseEventId = url.pathname.match(/^\/v1\/admin\/meal-events\/([^/]+)\/ordering-pause$/)?.[1];
    const pauseEvent = pauseEventId && adminEvents.find(({ id }) => id === pauseEventId);
    if (method === 'PATCH' && pauseEvent) {
      const body = request.postDataJSON();
      pauseRequests.push(body);
      if (pauseConflict) {
        pauseConflict = false;
        pauseEvent.ordering_pause_version += 2;
      }
      if (body.expected_version !== pauseEvent.ordering_pause_version) {
        return route.fulfill({ status: 409, json: { detail: '接單狀態已更新，請重新整理後再試' } });
      }
      pauseEvent.ordering_paused = body.paused;
      pauseEvent.ordering_pause_version += 1;
      pauseEvent.new_orders_blocked_reason = body.paused ? '此場次暫停接受新訂單' : null;
      return route.fulfill({ json: pauseEvent });
    }
    const capacityPath = url.pathname.match(/^\/v1\/admin\/meal-events\/([^/]+)\/offerings\/([^/]+)\/capacity$/);
    if (method === 'PATCH' && capacityPath) {
      const capacityEvent = adminEvents.find(({ id }) => id === capacityPath[1]);
      const offering = capacityEvent?.offerings.find(({ id }) => id === capacityPath[2]);
      if (!offering) return route.fulfill({ status: 404, json: { detail: '找不到餐點' } });
      const body = request.postDataJSON();
      capacityRequests.push({ offeringId: offering.id, body });
      if (capacityConflict) {
        capacityConflict = false;
        offering.capacity += 2;
        offering.available_quantity += 2;
      }
      if (body.expected_capacity !== offering.capacity) {
        return route.fulfill({ status: 409, json: { detail: '供應份數已更新，請重新核對份數' } });
      }
      offering.available_quantity += body.capacity - offering.capacity;
      offering.capacity = body.capacity;
      return route.fulfill({ json: capacityEvent });
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
  return { page, unknown, created, pauseRequests, capacityRequests, disableRequests, disablePreviewReads, scheduleUpdateRequests };
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
  assert.doesNotMatch(await card.innerText(), /週六/);
  await card.getByRole('button', { name: '編輯排程' }).click();
  assert.equal(await form.getByLabel('取餐前幾天截單', { exact: true }).inputValue(), '1');
  assert.equal(await form.getByLabel('每日截單時間', { exact: true }).inputValue(), '20:00');
  assert.equal(await form.getByLabel('週六・只接受會餐', { exact: true }).isChecked(), false);
  await form.getByLabel('週六・只接受會餐', { exact: true }).check();
  assert.equal(await form.getByLabel('週六・只接受會餐', { exact: true }).isChecked(), true);
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

test('場次可暫停及恢復新預訂，衝突後讀取新版本並保留原取餐訂單', { timeout: 30000 }, async t => {
  const date = '2026-11-07';
  const lunch = event('paused-lunch', date, {
    title: '隔離午餐', booking_policy: 'special', status: 'pickup_open', ordering_paused: false, ordering_pause_version: 3,
    booking_starts_at: `${date}T08:00:00+08:00`, booking_ends_at: `${date}T11:45:00+08:00`, same_day_preparation_minutes: 60,
  });
  const dinner = event('open-dinner', '2026-11-08', { location: '隔離晚餐點', booking_policy: 'special' });
  const existing = { id: 'paid-lunch', order_number: 'M2026110701', available_actions: [], payment_method: 'online', payment_status: 'paid',
    pickup_at: `${date}T12:00:00+08:00`, venue_name: lunch.location, items: [], amount_total: 120 };
  const { page, unknown, pauseRequests } = await fixture(t, 390, {
    admin: true, now: `${date}T10:30:00+08:00`, events: [lunch, dinner], adminEvents: [lunch],
    eventOrders: { [lunch.id]: [existing] }, pauseConflict: true,
  });
  const adminUrl = `${base}/admin/meals?tab=events`;
  await page.goto(adminUrl);
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: lunch.title, exact: true }) });
  const pause = card.getByRole('button', { name: '暫停新預訂', exact: true });
  await card.getByRole('button', { name: '查看本場訂單與取餐時間' }).click();
  const existingOrder = card.getByRole('heading', { name: existing.order_number, exact: true });
  await existingOrder.waitFor();
  await pause.click();
  await card.getByRole('alert').filter({ hasText: '接單狀態已更新' }).waitFor();
  await pause.click();
  await card.getByRole('status').filter({ hasText: '新預訂已暫停' }).waitFor();
  assert.deepEqual(pauseRequests, [{ paused: true, expected_version: 3 }, { paused: true, expected_version: 5 }]);
  assert.equal(await existingOrder.isVisible(), true);
  assert.equal(await card.getByRole('button', { name: '確認核銷', exact: true }).count(), 0);

  await page.goto(`${base}/shop`);
  const navigation = page.getByRole('region', { name: '便當日期與取餐時間' });
  await navigation.getByRole('button', { name: '11/7 週六，暫停預訂', exact: true }).click();
  assert.equal(await navigation.getByRole('button', { name: '隔離取餐點 12:00，暫停預訂', exact: true }).isDisabled(), true);
  assert.equal(await navigation.getByRole('link', { name: '隔離取餐點 12:00 取餐', exact: true }).count(), 0);
  await navigation.getByRole('button', { name: '11/8 週日，可預訂', exact: true }).click();
  assert.equal(await navigation.getByRole('link', { name: '隔離晚餐點 12:00 取餐', exact: true }).isVisible(), true);

  await page.goto(`${base}/meals/${lunch.id}?pickup_at=${encodeURIComponent(`${date}T12:00`)}`);
  await page.getByText('此場次暫停接受新訂單', { exact: false }).waitFor();
  const pickup = page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true });
  assert.equal(await pickup.inputValue(), `${date}T12:00`);
  assert.equal(await pickup.isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('link', { name: '查看我的便當訂單', exact: true }).getAttribute('href'), '/meal-orders');

  await page.goto(adminUrl);
  await card.getByRole('button', { name: '恢復新預訂', exact: true }).click();
  await pause.waitFor();
  assert.deepEqual(pauseRequests.at(-1), { paused: false, expected_version: 6 });
  await page.goto(`${base}/shop`);
  await navigation.getByRole('link', { name: '隔離取餐點 12:00 取餐', exact: true }).waitFor();
  await noOverflow(page);
  assert.deepEqual(unknown, []);
});

test('既有訂單場次可追加單一餐點份數，衝突後須核對且維持暫停', { timeout: 30000 }, async t => {
  const paused = event('paused-capacity', '2026-11-09', {
    title: '隔離容量場次', can_edit: false, ordering_paused: true, ordering_pause_version: 1,
    new_orders_blocked_reason: '此場次暫停接受新訂單',
  });
  const main = paused.offerings[0];
  paused.offerings.push(
    { ...main, id: 'add-capacity', meal_name: '可追加餐點', capacity: 30, available_quantity: 25, paid_quantity: 5 },
    { ...main, id: 'inactive-capacity', meal_name: '已停售餐點', is_active: false },
    { ...main, id: 'max-capacity', meal_name: '已達上限餐點', capacity: 100000 },
  );
  const editable = event('editable-capacity', '2026-11-10', { title: '隔離可編輯場次', can_edit: true });
  const { page, unknown, capacityRequests } = await fixture(t, 390, {
    admin: true, adminEvents: [paused, editable], capacityConflict: true,
  });
  await page.goto(`${base}/admin/meals?tab=events`);
  const card = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: paused.title, exact: true }) });
  const editableCard = page.locator('.meal-event-admin-card').filter({ has: page.getByRole('heading', { name: editable.title, exact: true }) });
  await card.getByRole('button', { name: '追加份數', exact: true }).click();
  assert.equal(await editableCard.getByRole('button', { name: '追加份數', exact: true }).count(), 0);
  assert.equal(await editableCard.getByRole('button', { name: '手動編輯場次', exact: true }).isEnabled(), true);
  assert.equal(await card.getByRole('button', { name: '手動編輯場次', exact: true }).isDisabled(), true);

  const form = card.getByRole('form', { name: '追加餐點份數' });
  const offering = form.getByRole('combobox', { name: '餐點', exact: true });
  assert.equal(await offering.getByRole('option', { name: '已停售餐點' }).count(), 0);
  assert.equal(await offering.getByRole('option', { name: '已達上限餐點' }).count(), 0);
  await offering.selectOption('add-capacity');
  const addition = form.getByRole('spinbutton', { name: '增加份數' });
  const save = form.getByRole('button', { name: '儲存份數', exact: true });
  await addition.fill('100000');
  assert.equal(await save.isDisabled(), true);
  await addition.fill('5');
  await form.getByText('目前總份數 30 · 追加後總份數 35', { exact: true }).waitFor();
  await save.click();
  await form.getByRole('alert').filter({ hasText: '供應份數已更新' }).waitFor();
  await form.getByText(/目前總份數 32/).waitFor();
  assert.equal(await save.isDisabled(), true);
  assert.equal(capacityRequests.length, 1);
  await form.getByRole('button', { name: '重新核對份數' }).click();
  assert.equal(await addition.inputValue(), '');
  assert.equal(await save.isDisabled(), true);
  await addition.fill('5');
  await form.getByText('目前總份數 32 · 追加後總份數 37', { exact: true }).waitFor();
  await save.click();
  await card.getByRole('status').filter({ hasText: '已儲存追加份數' }).waitFor();
  await card.getByText('可追加餐點 5/37', { exact: true }).waitFor();
  assert.equal(await card.getByText(`${main.meal_name} 0/20`, { exact: true }).isVisible(), true);
  assert.equal(await card.getByRole('status').filter({ hasText: '新預訂已暫停' }).isVisible(), true);
  assert.equal(await card.getByRole('button', { name: '恢復新預訂', exact: true }).isVisible(), true);
  assert.deepEqual(capacityRequests, [
    { offeringId: 'add-capacity', body: { expected_capacity: 30, capacity: 35 } },
    { offeringId: 'add-capacity', body: { expected_capacity: 32, capacity: 37 } },
  ]);
  await noOverflow(page);
  assert.deepEqual(unknown, []);
});

function scheduleForDisable() {
  return {
    id: 'disable-schedule', title: '隔離停用排程', location: '隔離取餐點', meal_period: 'lunch', timezone: 'Asia/Taipei',
    pickup_start_time: '11:00', pickup_end_time: '13:00', cutoff_time: '20:00', cutoff_days_before: 1,
    advance_days: 7, weekdays: [0, 1, 2, 3, 4], enabled: true, auto_publish: true,
    offerings: [{ meal_id: meal.id, price: 120, capacity: 20, position: 0 }],
  };
}

test('停用排程須預覽日期和訂單，資料變動後要重新確認暫停範圍', { timeout: 30000 }, async t => {
  const schedule = scheduleForDisable();
  const preview = {
    preview_version: 'a'.repeat(64),
    events: [
      { id: 'open', title: '隔離午餐 11/09', service_date: '2026-11-09', ordering_status: 'open', order_count: 2 },
      { id: 'draft', title: '隔離午餐 11/10', service_date: '2026-11-10', ordering_status: 'draft', order_count: 0 },
    ],
  };
  const changed = { ...preview, preview_version: 'b'.repeat(64), events: [{ ...preview.events[0], order_count: 3 }, preview.events[1]] };
  const { page, unknown, disableRequests, disablePreviewReads, pauseRequests } = await fixture(t, 390, {
    admin: true, schedules: [schedule], disablePreview: preview, changedDisablePreview: changed,
  });
  await page.goto(`${base}/admin/meals?tab=schedules`);
  const card = page.locator('article').filter({ has: page.getByRole('heading', { name: schedule.title, exact: true }) });
  const edit = card.getByRole('button', { name: '編輯排程', exact: true });
  await edit.click();
  const enabled = page.getByRole('checkbox', { name: '啟用每日自動產生場次', exact: true });
  assert.equal(await enabled.isChecked(), true);
  assert.equal(await enabled.isDisabled(), true);
  await page.getByRole('button', { name: '取消編輯', exact: true }).click();
  await card.getByRole('button', { name: '停用排程', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '停用排程', exact: true });
  const scope = dialog.getByRole('combobox', { name: '停用範圍', exact: true });
  const confirm = dialog.getByRole('button', { name: '確認停用排程', exact: true });
  await dialog.getByText('2026/11/09 · 開放預訂 · 2 筆訂單', { exact: true }).waitFor();
  await dialog.getByText('2026/11/10 · 草稿 · 0 筆訂單', { exact: true }).waitFor();
  assert.equal(await scope.inputValue(), 'generation_only');
  await scope.selectOption('pause_generated');
  assert.deepEqual(disableRequests, []);
  assert.deepEqual(disablePreviewReads, [preview.preview_version]);
  await confirm.click();
  await dialog.getByText('場次資料已更新，請重新核對停用範圍。', { exact: true }).waitFor();
  await dialog.getByText('2026/11/09 · 開放預訂 · 3 筆訂單', { exact: true }).waitFor();
  assert.deepEqual(disablePreviewReads, [preview.preview_version, changed.preview_version]);
  assert.equal(disableRequests.length, 1);
  assert.equal(await scope.inputValue(), 'generation_only');
  await scope.selectOption('pause_generated');
  await confirm.click();
  await card.getByText('已停用排程並暫停 1 場的新預訂。', { exact: true }).waitFor();
  assert.equal(await card.getByRole('button', { name: '立即補齊未來場次' }).isDisabled(), true);
  assert.equal(await card.getByRole('button', { name: '停用排程', exact: true }).count(), 0);
  assert.deepEqual(disableRequests, [
    { scope: 'pause_generated', preview_version: preview.preview_version },
    { scope: 'pause_generated', preview_version: changed.preview_version },
  ]);
  assert.deepEqual(pauseRequests, []);
  await noOverflow(page);
  assert.deepEqual(unknown, []);
});

test('停用排程只有不可暫停的場次時只停止生成，舊 API 無預覽時不得送出', { timeout: 30000 }, async t => {
  for (const supported of [true, false]) {
    await t.test(supported ? '只停止生成' : '舊 API', async subtest => {
      const schedule = scheduleForDisable();
      const preview = {
        preview_version: 'a'.repeat(64),
        events: [{ id: 'closed', title: '隔離已截止場次', service_date: '2026-11-09', ordering_status: 'closed', order_count: 4 }],
      };
      const { page, unknown, disableRequests } = await fixture(subtest, 390, {
        admin: true, schedules: [schedule], disablePreview: supported ? preview : null,
      });
      await page.goto(`${base}/admin/meals?tab=schedules`);
      await page.getByRole('button', { name: '停用排程', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '停用排程', exact: true });
      const confirm = dialog.getByRole('button', { name: '確認停用排程', exact: true });
      if (supported) {
        await dialog.getByText('2026/11/09 · 已截止 · 4 筆訂單', { exact: true }).waitFor();
        assert.equal(await dialog.getByRole('combobox', { name: '停用範圍' }).count(), 0);
        await confirm.click();
        await page.getByText('已停用排程。', { exact: true }).waitFor();
        assert.deepEqual(disableRequests, [{ scope: 'generation_only', preview_version: preview.preview_version }]);
      } else {
        await dialog.getByRole('alert').filter({ hasText: '停用預覽暫時無法使用' }).waitFor();
        assert.equal(await confirm.isDisabled(), true);
        assert.equal(await dialog.getByText('目前沒有已建立的未來場次。', { exact: true }).count(), 0);
        assert.deepEqual(disableRequests, []);
      }
      assert.deepEqual(unknown, []);
    });
  }
});

test('停用排程後拒絕另一分頁的舊編輯版本並保留輸入', { timeout: 30000 }, async t => {
  const schedule = { ...scheduleForDisable(), updated_at: '2026-11-06T04:00:00Z' };
  const { page, unknown, scheduleUpdateRequests, disableRequests } = await fixture(t, 390, {
    admin: true, schedules: [schedule], scheduleUpdateConflict: true,
  });
  await page.goto(`${base}/admin/meals?tab=schedules`);
  const card = page.locator('article').filter({ has: page.getByRole('heading', { name: schedule.title, exact: true }) });
  await card.getByRole('button', { name: '編輯排程', exact: true }).click();
  const title = page.getByLabel('排程／場次名稱', { exact: true });
  await title.fill('保留隔離排程草稿');
  await page.getByRole('button', { name: '儲存每日排程', exact: true }).click();
  await page.getByText('每日供餐設定已更新，請重新開啟編輯後再試', { exact: true }).waitFor();
  await card.getByText('排程停用 · 午餐', { exact: true }).waitFor();
  assert.equal(await title.inputValue(), '保留隔離排程草稿');
  assert.equal(scheduleUpdateRequests.length, 1);
  assert.equal(scheduleUpdateRequests[0].expected_updated_at, '2026-11-06T04:00:00Z');
  assert.equal(scheduleUpdateRequests[0].enabled, true);
  assert.deepEqual(disableRequests, []);
  assert.deepEqual(unknown, []);
});
