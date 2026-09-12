import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

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
  { name: '便當預購', heading: '預約一份剛好的便當' },
  { name: '共同團購', heading: '把需求聚在一起' },
  { name: '日常選品', heading: '合作社選品' },
];

for (const width of [1440, 390]) {
  test(`${width}px 生活消費依便當、團購、選品排序，保留選品預設及各分頁內容`, { timeout: 30000 }, async (t) => {
    const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    t.after(() => browser.close());
    const context = await browser.newContext({
      viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block',
    });
    const unknownApi = [];
    const mutations = [];
    await context.route('**/*', (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== base) return route.abort();
      const method = request.method();
      if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/payments/') || url.pathname.startsWith('/internal/')) {
        if (method === 'POST' && url.pathname === '/v1/auth/refresh') {
          return route.fulfill({ status: 401, json: { detail: '隔離測試未登入' } });
        }
        if (method === 'GET' && ['/v1/products', '/v1/group-campaigns', '/v1/meal-events'].includes(url.pathname)) {
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
    await page.goto(`${base}/shop`);
    const tabList = page.getByRole('tablist', { name: '消費類型', exact: true });
    await tabList.waitFor();
    const selected = tabList.getByRole('tab', { selected: true });
    assert.equal(await selected.innerText(), '日常選品');
    await page.getByRole('tabpanel', { name: '日常選品', exact: true })
      .getByRole('heading', { name: '合作社選品', exact: true }).waitFor();
    assert.deepEqual(await tabList.getByRole('tab').allTextContents(), tabs.map(({ name }) => name));
    const positions = await tabList.getByRole('tab').evaluateAll((elements) => elements.map((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    }));
    for (let index = 1; index < positions.length; index++) {
      assert.ok(positions[index].x >= positions[index - 1].x + positions[index - 1].width - 1);
      assert.ok(Math.abs(positions[index].y - positions[0].y) <= 1);
    }
    for (const { name, heading } of tabs) {
      await tabList.getByRole('tab', { name, exact: true }).click();
      assert.equal(await tabList.getByRole('tab', { selected: true }).innerText(), name);
      const panel = page.getByRole('tabpanel', { name, exact: true });
      await panel.getByRole('heading', { name: heading, exact: true }).waitFor();
      assert.equal(await page.getByRole('tabpanel').count(), 1);
    }
    assert.deepEqual(unknownApi, []);
    assert.deepEqual(mutations, []);
    assert.equal(new URL(page.url()).pathname, '/shop');
  });
}
