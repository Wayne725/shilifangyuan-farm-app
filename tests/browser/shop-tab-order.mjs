import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { mealCalendarFixture } from '../fixtures/meal-calendar.mjs';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname)
  || !['http:', 'https:'].includes(testUrl.protocol)
  || testUrl.username || testUrl.password || testUrl.pathname !== '/' || testUrl.search || testUrl.hash) {
  throw new Error('Shop tab acceptance must use an isolated local website origin');
}
const base = testUrl.origin;
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const tabs = [
  { name: '便當預購', heading: '選取餐時間', value: 'meals' },
  { name: '共同購買', heading: '把需求聚在一起', value: 'groups' },
  { name: '日常選品', heading: '合作社選品', value: 'products' },
];
const event = {
  id: 'isolated-calendar', title: '隔離午餐', location: '合作社取餐點', status: 'published',
  booking_policy: 'workday', service_date: '2030-01-03', meal_period: 'lunch',
  ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-02T20:00:00+08:00',
  pickup_starts_at: '2030-01-03T11:00:00+08:00', pickup_ends_at: '2030-01-03T13:30:00+08:00',
  pickup_slots: ['2030-01-03T11:30:00+08:00'],
  offerings: [{ id: 'isolated-offering', is_active: true, available_quantity: 10 }],
};
const calendarPath = '/v1/meal-events/calendar';
const originalListPath = '/v1/meal-events';

async function fixture(t, { width = 390, calendarStatus = 200 } = {}) {
    const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    t.after(() => browser.close());
    const context = await browser.newContext({
      viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block',
    });
    const unknownApi = [];
    const mutations = [];
    const mealReads = [];
    await context.route('**/*', (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== base) return route.abort();
      const method = request.method();
      if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/payments/') || url.pathname.startsWith('/internal/')) {
        if (method === 'POST' && url.pathname === '/v1/auth/refresh') {
          return route.fulfill({ status: 401, json: { detail: '隔離測試未登入' } });
        }
        if (method === 'GET' && url.pathname === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
        if (method === 'GET' && url.pathname === calendarPath) {
          mealReads.push(calendarPath);
          return calendarStatus === 200
            ? route.fulfill({ json: mealCalendarFixture([event]) })
            : route.fulfill({ status: calendarStatus, json: { detail: '隔離摘要回應' } });
        }
        if (method === 'GET' && url.pathname === originalListPath) {
          mealReads.push(originalListPath);
          return route.fulfill({ json: [event] });
        }
        if (method === 'GET' && ['/v1/products', '/v1/group-campaigns'].includes(url.pathname)) {
          return route.fulfill({ json: [] });
        }
        unknownApi.push(`${method} ${url.pathname}`);
        if (method !== 'GET') mutations.push(`${method} ${url.pathname}`);
        return route.abort();
      }
      if (method !== 'GET') {
        mutations.push(`${method} ${url.pathname}`);
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    await page.clock.install({ time: new Date('2030-01-02T09:00:00+08:00') });
    return { page, mealReads, mutations, unknownApi };
}

for (const width of [1440, 390]) {
  test(`${width}px 便當預設顯示時段並定時更新，其他分頁載入與停留皆不讀取便當`, { timeout: 30000 }, async (t) => {
    const { page, mealReads, mutations, unknownApi } = await fixture(t, { width });
    await page.goto(`${base}/shop?tab=products`);
    await page.getByText('商品整理中', { exact: true }).waitFor();
    await page.clock.fastForward(61_000);
    assert.deepEqual(mealReads, []);
    await page.goto(`${base}/shop`);
    const tabList = page.getByRole('tablist', { name: '消費類型', exact: true });
    await tabList.waitFor();
    const selected = tabList.getByRole('tab', { selected: true });
    assert.equal(await selected.innerText(), '便當預購');
    await page.getByRole('tabpanel', { name: '便當預購', exact: true })
      .getByRole('heading', { name: '選取餐時間', exact: true }).waitFor();
    const pickup = page.getByRole('link', { name: '合作社取餐點 11:30 取餐', exact: true });
    await pickup.waitFor();
    assert.equal(new URL(await pickup.getAttribute('href'), base).searchParams.get('pickup_at'), '2030-01-03T11:30');
    assert.deepEqual(mealReads, [calendarPath]);
    const refresh = page.waitForResponse(response => new URL(response.url()).pathname === calendarPath);
    await page.clock.fastForward(61_000);
    await refresh;
    assert.deepEqual(mealReads, [calendarPath, calendarPath]);
    assert.deepEqual(await tabList.getByRole('tab').allTextContents(), tabs.map(({ name }) => name));
    const positions = await tabList.getByRole('tab').evaluateAll((elements) => elements.map((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    }));
    for (let index = 1; index < positions.length; index++) {
      assert.ok(positions[index].x >= positions[index - 1].x + positions[index - 1].width - 1);
      assert.ok(Math.abs(positions[index].y - positions[0].y) <= 1);
    }
    for (const { name, heading, value } of tabs) {
      await tabList.getByRole('tab', { name, exact: true }).click();
      await page.waitForURL((url) => (url.searchParams.get('tab') || 'meals') === value);
      assert.equal(await tabList.getByRole('tab', { selected: true }).innerText(), name);
      const panel = page.getByRole('tabpanel', { name, exact: true });
      await panel.getByRole('heading', { name: heading, exact: true }).waitFor();
      assert.equal(await page.getByRole('tabpanel').count(), 1);
      if (name === '共同購買') await page.getByRole('heading', { name: '共同購買', exact: true }).waitFor();
    }
    const readsBeforeLeavingMeals = mealReads.length;
    await page.clock.fastForward(122_000);
    assert.equal(mealReads.length, readsBeforeLeavingMeals);
    await page.reload();
    await tabList.waitFor();
    assert.equal(await selected.innerText(), '日常選品');
    await page.getByRole('tabpanel', { name: '日常選品', exact: true })
      .getByRole('heading', { name: '合作社選品', exact: true }).waitFor();
    assert.equal(await page.getByRole('tabpanel').count(), 1);
    await page.goBack();
    await page.getByRole('tabpanel', { name: '共同購買', exact: true }).waitFor();
    await page.goto(`${base}/shop?tab=groups`);
    assert.equal(await selected.innerText(), '共同購買');
    assert.equal(mealReads.length, readsBeforeLeavingMeals);
    await page.goto(`${base}/shop?tab=unknown`);
    assert.equal(await selected.innerText(), '便當預購');
    assert.deepEqual(unknownApi, []);
    assert.deepEqual(mutations, []);
    assert.equal(new URL(page.url()).pathname, '/shop');
  });
}

test('摘要 404 時使用舊版清單，保留相同時段入口', async t => {
  const { page, mealReads, unknownApi } = await fixture(t, { calendarStatus: 404 });
  await page.goto(`${base}/shop`);
  const pickup = page.getByRole('link', { name: '合作社取餐點 11:30 取餐', exact: true });
  await pickup.waitFor();
  assert.equal(new URL(await pickup.getAttribute('href'), base).pathname, '/meals/isolated-calendar');
  assert.deepEqual(mealReads, [calendarPath, originalListPath]);
  assert.deepEqual(unknownApi, []);
});

test('摘要 503 顯示錯誤並沿用重試，無額外完整清單請求', async t => {
  const { page, mealReads, unknownApi } = await fixture(t, { calendarStatus: 503 });
  await page.goto(`${base}/shop`);
  await page.getByRole('heading', { name: '餐期資料暫時不可用', exact: true }).waitFor();
  assert.ok(mealReads.length > 0);
  assert.ok(mealReads.every(path => path === calendarPath));
  assert.deepEqual(unknownApi, []);
});
