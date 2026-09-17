import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4187');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname)
  || !['http:', 'https:'].includes(testUrl.protocol)
  || testUrl.username || testUrl.password || testUrl.pathname !== '/' || testUrl.search || testUrl.hash) {
  throw new Error('Meal date layout acceptance must use an isolated local website origin');
}
const base = testUrl.origin;

function mealEvent(id, date, period = 'lunch', overrides = {}) {
  return {
    id, title: `隔離${id}`, location: '隔離取餐點', status: 'published', service_date: date,
    meal_period: period, schedule_template_id: null, ordering_starts_at: '2026-09-16T00:00:00Z',
    ordering_ends_at: `${date}T${period === 'lunch' ? '13:00' : '19:00'}:00+08:00`,
    pickup_starts_at: `${date}T${period === 'lunch' ? '11:00' : '17:00'}:00+08:00`,
    pickup_ends_at: `${date}T${period === 'lunch' ? '14:00' : '20:00'}:00+08:00`,
    offerings: [{ id: `offering-${id}`, meal_id: 'isolated-meal', meal_name: '隔離便當', price: 120,
      description: '不供餐，僅本機測試', image_url: null, capacity: 50, reserved_quantity: 0,
      paid_quantity: 0, available_quantity: 50, position: 0, is_active: true, option_groups: [] }],
    ...overrides,
  };
}

const defaultEvents = [
  mealEvent('今日午餐', '2026-09-17'),
  mealEvent('明日晚餐', '2026-09-18', 'dinner'),
];

async function fixture(t, width, { events = defaultEvents, now = '2026-09-17T00:00:00Z' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({
    viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW',
    serviceWorkers: 'block', reducedMotion: 'reduce',
  });
  const unknownApi = [];
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/payments/') || url.pathname.startsWith('/internal/')) {
      if (request.method() === 'POST' && url.pathname === '/v1/auth/refresh') {
        return route.fulfill({ status: 401, json: { detail: '隔離測試未登入' } });
      }
      if (request.method() === 'GET' && ['/v1/products', '/v1/group-campaigns', '/v1/meal-events'].includes(url.pathname)) {
        return route.fulfill({ json: url.pathname === '/v1/meal-events' ? events : [] });
      }
      unknownApi.push(`${request.method()} ${url.pathname}`);
      return route.abort();
    }
    if (request.method() !== 'GET') return route.abort();
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.clock.install({ time: new Date(now) });
  await page.goto(`${base}/shop`);
  await page.getByRole('tabpanel', { name: '便當預購', exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  return { page, unknownApi };
}

function overlaps(a, b) {
  return Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > 1
    && Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 1;
}

for (const width of [1440, 390]) {
  test(`${width}px 便當日期按鈕與午晚餐分列不重疊，頁面不水平溢出`, { timeout: 20000 }, async (t) => {
    const { page, unknownApi } = await fixture(t, width);
    const navigation = page.getByRole('region', { name: '便當日期與餐別', exact: true });
    await navigation.waitFor();
    const dates = navigation.getByRole('group', { name: '取餐日期', exact: true });
    const periods = navigation.getByRole('group', { name: '餐別', exact: true });
    await dates.getByRole('button', { name: /今天 9\/17.*可預訂/ }).waitFor();
    const controls = await navigation.getByRole('button').evaluateAll((elements) => elements.map((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return { name: element.getAttribute('aria-label') || element.textContent, x, y, width, height };
    }));
    const dateRow = await dates.boundingBox();
    const periodRow = await periods.boundingBox();
    assert.ok(dateRow && periodRow, '日期與餐別必須各自可見');
    const pageWidth = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: innerWidth }));
    t.diagnostic(JSON.stringify({ viewport: width, dateRow, periodRow, controls, pageWidth }));
    const collisions = [];
    for (let i = 0; i < controls.length; i++) {
      for (let j = i + 1; j < controls.length; j++) {
        if (overlaps(controls[i], controls[j])) collisions.push(`${controls[i].name} × ${controls[j].name}`);
      }
    }
    assert.deepEqual(unknownApi, []);
    assert.deepEqual(collisions, [], `日期篩選控制項重疊：${collisions.join('、')}`);
    assert.ok(periodRow.y >= dateRow.y + dateRow.height + 23, '日期與餐別兩列之間至少留 24px');
    assert.ok(pageWidth.content <= pageWidth.viewport + 1, '頁面不能水平溢出');
    assert.equal(await page.getByRole('button', { name: '前一天', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '後一天', exact: true }).count(), 0);
    assert.equal(await page.locator('input[type="date"]').count(), 0);
    await dates.getByRole('button', { name: /明天 9\/18.*可預訂/ }).click();
    await periods.getByRole('button', { name: /晚餐，可預訂/ }).click();
    await page.getByRole('heading', { name: '隔離明日晚餐', exact: true }).waitFor();
    assert.equal(await dates.getByRole('button', { name: /9\/18/ }).getAttribute('aria-pressed'), 'true');
    assert.equal(await periods.getByRole('button', { name: /晚餐/ }).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.BROWSER_SCREENSHOT_DIR) {
      await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); });
      await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/meal-date-buttons-${width}.png`, fullPage: true });
    }
  });
}

test('無場次日期不可選，週日手動場與超過七天手動場仍可選且不受餐別影響', { timeout: 20000 }, async (t) => {
  const events = [...defaultEvents, mealEvent('週日午餐', '2026-09-20'), mealEvent('跨週晚餐', '2026-09-27', 'dinner')];
  const { page, unknownApi } = await fixture(t, 390, { events });
  const dates = page.getByRole('group', { name: '取餐日期', exact: true });
  const periods = page.getByRole('group', { name: '餐別', exact: true });
  await dates.getByRole('button', { name: /9\/20.*可預訂/ }).waitFor();
  assert.equal(await dates.getByRole('button', { name: /9\/19.*尚未開放/ }).isDisabled(), true);
  await dates.getByRole('button', { name: /9\/18/ }).click();
  await periods.getByRole('button', { name: /晚餐/ }).click();
  assert.equal(await dates.getByRole('button', { name: /9\/20.*可預訂/ }).isDisabled(), false);
  await dates.getByRole('button', { name: /9\/20/ }).click();
  await periods.getByRole('button', { name: '全部', exact: true }).click();
  await page.getByRole('heading', { name: '隔離週日午餐', exact: true }).waitFor();
  assert.equal(await periods.getByRole('button', { name: /晚餐/ }).isDisabled(), true);
  await dates.getByRole('button', { name: /9\/27.*可預訂/ }).click();
  await page.getByRole('heading', { name: '隔離跨週晚餐', exact: true }).waitFor();
  assert.equal(await dates.getByRole('button', { name: /9\/27/ }).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(unknownApi, []);
});

test('午夜後保留昨日未結束的手動跨夜場日期，不把取餐日誤算為今天', { timeout: 20000 }, async (t) => {
  const events = [mealEvent('昨日跨夜晚餐', '2026-09-16', 'dinner', {
    status: 'pickup_open', ordering_ends_at: '2026-09-17T00:20:00+08:00',
    pickup_starts_at: '2026-09-16T23:30:00+08:00', pickup_ends_at: '2026-09-17T00:30:00+08:00',
  })];
  const { page, unknownApi } = await fixture(t, 390, { events, now: '2026-09-16T16:10:00Z' });
  const dates = page.getByRole('group', { name: '取餐日期', exact: true });
  const yesterday = dates.getByRole('button', { name: /9\/16.*可預訂/ });
  await yesterday.waitFor();
  await yesterday.click();
  const card = page.getByRole('link').filter({ has: page.getByRole('heading', { name: '隔離昨日跨夜晚餐', exact: true }) });
  await card.waitFor();
  assert.equal(await yesterday.getAttribute('aria-pressed'), 'true');
  assert.match(await card.innerText(), /9\/16.*23:30.*9\/17.*00:30/s);
  assert.deepEqual(unknownApi, []);
});

test('週日沒有手動場時明示休息且不顯示虛構餐點', { timeout: 20000 }, async (t) => {
  const { page, unknownApi } = await fixture(t, 390, { events: [], now: '2026-09-19T16:10:00Z' });
  const sunday = page.getByRole('group', { name: '取餐日期', exact: true }).getByRole('button', { name: /今天 9\/20.*休息/ });
  await sunday.waitFor();
  assert.equal(await sunday.isDisabled(), true);
  await page.getByRole('heading', { name: '這一天尚無符合的供餐場次', exact: true }).waitFor();
  assert.equal(await page.locator('.meal-card').count(), 0);
  assert.deepEqual(unknownApi, []);
});
