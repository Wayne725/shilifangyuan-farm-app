import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
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

async function fixture(t, { width = 390, calendarStatus = 200, products = [], announcements = [], events = [event] } = {}) {
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
        if (url.pathname === '/v1/meal-checkout/contact') return route.fulfill({ json: { phone: '0912345678' } });
        if (url.pathname === '/v1/meal-announcements') return route.fulfill({ json: announcements });
        if (method === 'GET' && url.pathname === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
        if (method === 'GET' && url.pathname === calendarPath) {
          mealReads.push(calendarPath);
          return calendarStatus === 200
            ? route.fulfill({ json: mealCalendarFixture(events) })
            : route.fulfill({ status: calendarStatus, json: { detail: '隔離摘要回應' } });
        }
        if (method === 'GET' && url.pathname === originalListPath) {
          mealReads.push(originalListPath);
          return route.fulfill({ json: events });
        }
        if (method === 'GET' && url.pathname === '/v1/products') return route.fulfill({ json: products });
        const selectedProduct = products.find(product => url.pathname === `/v1/products/${product.id}`);
        if (method === 'GET' && selectedProduct) return route.fulfill({ json: selectedProduct });
        if (method === 'GET' && ['/v1/group-campaigns', '/v1/pickup-locations', '/v1/shipping-rates'].includes(url.pathname)) {
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

test('手機取餐日期內顯示停餐餐別，保留未停餐的時段並更新已復供狀態', { timeout: 30000 }, async t => {
  const announcements = [
    { service_date: '2030-01-01', periods: ['lunch'], message: '前一日停餐' },
    { service_date: '2030-01-02', periods: ['dinner', 'lunch'], message: '颱風暫停供餐' },
    { service_date: '2030-01-03', periods: ['dinner'], message: '晚間因颱風暫停供餐', cancelled_event_ids: ['isolated-dinner'] },
  ];
  const dinnerEvent = {
    ...event, id: 'isolated-dinner', title: '隔離晚餐', meal_period: 'dinner',
    pickup_starts_at: '2030-01-03T16:30:00+08:00', pickup_ends_at: '2030-01-03T18:30:00+08:00',
    pickup_slots: ['2030-01-03T17:00:00+08:00'],
  };
  const otherDinner = { ...dinnerEvent, id: 'other-dinner', location: '另一取餐點' };
  const { page, mutations, unknownApi } = await fixture(t, { announcements, events: [event, dinnerEvent, otherDinner] });
  await page.goto(`${base}/shop`);
  const calendar = page.getByRole('region', { name: '便當日期與取餐時間' });
  const dates = calendar.getByRole('group', { name: '取餐日期' });
  const notices = calendar.getByRole('list', { name: '停餐資訊' });
  const partial = dates.getByRole('button', { name: '1/3 週四，可預訂，部分晚餐停供', exact: true });
  const stopped = dates.getByRole('button', { name: '1/2 週三，颱風暫停供餐', exact: true });
  const lunch = calendar.getByRole('link', { name: '合作社取餐點 11:30 取餐', exact: true });
  const dinner = calendar.getByRole('link', { name: '合作社取餐點 17:00 取餐', exact: true });
  await partial.waitFor();
  assert.equal(await partial.getAttribute('aria-pressed'), 'true');
  await notices.getByText('部分晚餐 · 晚間因颱風暫停供餐', { exact: true }).waitFor();
  await lunch.waitFor();
  await calendar.getByRole('link', { name: '另一取餐點 17:00 取餐', exact: true }).waitFor();
  assert.equal(await dinner.count(), 0);
  assert.equal(await dates.getByRole('button', { name: /^1\/1 / }).count(), 0);
  assert.equal(await page.getByRole('region', { name: '供餐公告' }).count(), 0);

  await stopped.click();
  await notices.getByText('午餐、晚餐 · 颱風暫停供餐', { exact: true }).waitFor();
  assert.equal(await calendar.getByRole('link').count(), 0);
  assert.equal(await page.getByText('這一天的取餐時段尚未開放').count(), 0);
  if (process.env.BROWSER_SCREENSHOT_DIR) {
    await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
    await calendar.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/meal-disaster-date-mobile.png`, animations: 'disabled' });
  }

  await partial.click();
  announcements.pop();
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/v1/meal-announcements');
  await page.clock.fastForward(61_000);
  await refreshed;
  await dates.getByRole('button', { name: '1/3 週四，可預訂', exact: true }).waitFor();
  assert.equal(await notices.count(), 0);
  await lunch.waitFor();
  await dinner.waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(mutations, []);
  assert.deepEqual(unknownApi, []);
});

test('摘要 404 時使用舊版清單，保留相同時段入口', async t => {
  const { page, mealReads, unknownApi } = await fixture(t, { calendarStatus: 404 });
  await page.goto(`${base}/shop`);
  const pickup = page.getByRole('link', { name: '合作社取餐點 11:30 取餐', exact: true });
  await pickup.waitFor();
  assert.equal(new URL(await pickup.getAttribute('href'), base).pathname, '/meals/isolated-calendar');
  assert.deepEqual(mealReads, [calendarPath, originalListPath]);
  assert.deepEqual(unknownApi, []);
});

test('摘要 503 保留已讀取的停餐資訊與重試，無額外完整清單請求', async t => {
  const { page, mealReads, unknownApi } = await fixture(t, {
    calendarStatus: 503,
    announcements: [{ service_date: '2030-01-02', periods: ['lunch'], message: '颱風暫停供餐' }],
  });
  await page.goto(`${base}/shop`);
  await page.getByRole('heading', { name: '餐期資料暫時不可用', exact: true }).waitFor();
  const calendar = page.getByRole('region', { name: '便當日期與取餐時間' });
  await calendar.getByRole('button', { name: '1/2 週三，颱風暫停供餐', exact: true }).waitFor();
  await calendar.getByRole('list', { name: '停餐資訊' }).getByText('午餐 · 颱風暫停供餐', { exact: true }).waitFor();
  assert.equal(await calendar.getByRole('link').count(), 0);
  const retried = page.waitForResponse(response => new URL(response.url()).pathname === calendarPath);
  await page.getByRole('button', { name: '重新連線', exact: true }).click();
  assert.equal((await retried).status(), 503);
  assert.ok(mealReads.every(path => path === calendarPath));
  assert.deepEqual(unknownApi, []);
});

function product(index, changes = {}) {
  return {
    id: `isolated-product-${index}`,
    name: `隔離商品 ${String(index).padStart(2, '0')}`,
    description: '當季手作 TEA',
    category: '加工品',
    unit: '件',
    member_price: 40,
    nonmember_price: 50,
    stock_quantity: 10,
    can_ship: false,
    allowed_shipping_channels: [],
    tax_type: 'tax_exempt',
    is_active: true,
    daily_sales_enabled: true,
    ...changes,
  };
}

const catalog = [
  ...Array.from({ length: 13 }, (_, index) => product(index + 1)),
  product(14, { category: '飲品', sku: 'PRIVATE-SKU-ISOLATED' }),
  product(15, { category: '生活用品', is_active: false }),
  product(16, { category: '蛋品', daily_sales_enabled: false }),
];

test('商品深連結、篩選及分頁保留在網址，重新整理和上一頁／下一頁可還原', { timeout: 30000 }, async (t) => {
  const { page, unknownApi, mutations } = await fixture(t, { width: 1440, products: catalog });
  const params = new URLSearchParams({ tab: 'products', q: 'ＴＥＡ', category: '加工品', page: '2' });
  await page.goto(`${base}/shop?${params}`);
  const search = page.getByRole('searchbox', { name: '搜尋商品' });
  const category = page.getByRole('combobox', { name: '商品分類' });
  const cards = page.locator('.catalog-grid .product-card');
  const last = cards.getByRole('heading', { name: '隔離商品 13', exact: true });
  const previous = page.getByRole('link', { name: '上一頁', exact: true });
  await last.waitFor();
  assert.equal(await cards.count(), 1);
  assert.equal(await search.inputValue(), 'ＴＥＡ');
  assert.equal(await category.inputValue(), '加工品');
  assert.deepEqual(new Set(await category.locator('option').allTextContents()), new Set(['全部分類', '加工品', '飲品']));

  await page.reload();
  await last.waitFor();
  await previous.click();
  await page.waitForURL(url => !url.searchParams.has('page'));
  await cards.getByRole('heading', { name: '隔離商品 01', exact: true }).waitFor();
  assert.equal(await cards.count(), 12);
  assert.equal(new URL(page.url()).searchParams.get('category'), '加工品');

  await page.goBack();
  await last.waitFor();
  assert.equal(new URL(page.url()).searchParams.get('page'), '2');
  await page.goForward();
  await cards.getByRole('heading', { name: '隔離商品 01', exact: true }).waitFor();
  assert.equal(await search.inputValue(), 'ＴＥＡ');

  await page.getByRole('tab', { name: '共同購買', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('tab') === 'groups');
  assert.equal(await search.count(), 0);
  await page.getByRole('tab', { name: '日常選品', exact: true }).click();
  await search.waitFor();
  assert.equal(await search.inputValue(), 'ＴＥＡ');
  assert.equal(await category.inputValue(), '加工品');

  await category.selectOption('飲品');
  await cards.getByRole('heading', { name: '隔離商品 14', exact: true }).waitFor();
  assert.equal(await cards.count(), 1);
  assert.equal(new URL(page.url()).searchParams.get('q'), 'ＴＥＡ');
  assert.equal(new URL(page.url()).searchParams.get('category'), '飲品');

  params.set('page', '999');
  await page.goto(`${base}/shop?${params}`);
  await page.waitForURL(url => url.searchParams.get('page') === '2');
  await last.waitFor();
  assert.equal(await cards.count(), 1);
  assert.deepEqual(unknownApi, []);
  assert.deepEqual(mutations, []);
});

test('390px 可用鍵盤搜尋，空結果可清除，篩選後的商品能加入原本的結帳清單', { timeout: 30000 }, async (t) => {
  const { page, unknownApi, mutations } = await fixture(t, { products: catalog });
  await page.goto(`${base}/shop?tab=products`);
  const search = page.getByRole('searchbox', { name: '搜尋商品' });
  const cards = page.locator('.catalog-grid .product-card');
  await search.fill('PRIVATE-SKU-ISOLATED');
  await search.press('Enter');
  await page.getByText('找不到符合的商品', { exact: true }).waitFor();
  assert.equal(await cards.count(), 0);

  await page.getByRole('button', { name: '清除篩選', exact: true }).click();
  await page.getByText('共 14 件商品', { exact: true }).waitFor();
  assert.equal(await search.inputValue(), '');
  assert.equal(new URL(page.url()).searchParams.has('q'), false);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);

  await search.fill('隔離商品 13');
  await search.press('Enter');
  const add = cards.getByRole('button', { name: '加入購物車：隔離商品 13', exact: true });
  await add.focus();
  await add.press('Enter');
  await page.getByText('隔離商品 13已加入，清單共 1 件', { exact: true }).waitFor();
  assert.equal(await cards.count(), 1);
  const inventory = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/v1/products/isolated-product-13' && response.ok()
  );
  await page.getByRole('link', { name: /查看結帳清單/ }).click();
  await page.waitForURL(`${base}/checkout`);
  await inventory;
  const item = page.locator('.checkout-item');
  await item.getByRole('heading', { name: '隔離商品 13', exact: true }).waitFor();
  assert.equal(await item.count(), 1);
  assert.equal(await item.locator('.quantity-control span').innerText(), '1');
  assert.deepEqual(unknownApi, []);
  assert.deepEqual(mutations, []);
});
