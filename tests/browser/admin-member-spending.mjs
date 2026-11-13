import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile } from 'node:fs/promises';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['localhost', '127.0.0.1'].includes(testUrl.hostname) || testUrl.pathname !== '/' || testUrl.username || testUrl.password) {
  throw new Error('Use an isolated local origin');
}
const base = testUrl.origin;
const reportPath = '/v1/admin/member-spending';
const person = {
  id: '01234567-abcd-1234-9876-000000000001', member_number: 'SPEND-001', name: '合成社員',
  status_label: '正式社員', net_amount: 160, order_count: 2, latest_paid_date: '2026-10-31',
  categories: { regular: 60, group: 0, meal: 0, business: 100, walk_in: 0 },
};
const oldMember = {
  ...person, id: '01234567-abcd-1234-9876-000000000002',
  member_number: 'SPEND-002', name: '第二位社員', status_label: '已退社',
};
const detail = {
  id: 'refund:fixture', order_id: '01234567-abcd-1234-9876-000000000003', order_number: 'ORDER-SPEND-01',
  date: '2026-11-01', category_label: '會餐', amount: 0, payment_method: '現金', invoice_number: 'AB12345678', refunded: 40,
};

for (const width of [390, 1440]) {
  test(`社員帳冊可查詢、翻頁及匯出跨期退款明細 ${width}px`, async t => {
    const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
    t.after(() => browser.close());
    const context = await browser.newContext({ viewport: { width, height: 960 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block' });
    const seen = [];
    const unexpected = [];
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) return route.abort();
      const path = url.pathname;
      if (!path.startsWith('/v1/')) return route.continue();
      if (path === '/v1/auth/refresh') return route.fulfill({ json: {
        access_token: 'isolated-member-spending', user: { id: 'admin-report', email: 'isolated@example.test', display_name: '隔離管理員', user_role: 'admin' },
      } });
      if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, sales_scope: 'all', closed_message: '' } });
      if (path === '/v1/admin/member-register') return route.fulfill({ json: { items: [], total: 0 } });
      if (path === '/v1/notifications') return route.fulfill({ json: [] });
      if (!path.startsWith(reportPath)) {
        unexpected.push(path);
        return route.fulfill({ status: 404, json: {} });
      }
      const params = Object.fromEntries(url.searchParams);
      seen.push({ path, ...params });
      if (path.endsWith('.csv')) return route.fulfill({ contentType: 'text/csv', body: '日期,金額\n2026-11-01,-40' });
      if (path.endsWith(person.id)) return route.fulfill({ json: { items: [detail], total: 1 } });
      if (params.q) return route.fulfill({ json: { items: [person], total: 1 } });
      return route.fulfill({ json: { items: [params.offset === '0' ? person : oldMember], total: 21 } });
    });
    const page = await context.newPage();
    await page.goto(`${base}/admin/users?tab=records`);
    await page.getByRole('button', { name: '社員消費紀錄', exact: true }).click();
    await page.getByRole('button', { name: '查看 合成社員 消費明細' }).waitFor();
    const currentYear = await page.locator('select').filter({ has: page.getByRole('option', { name: '自訂日期' }) }).inputValue();
    assert.equal(seen[0].starts_on, `${currentYear}-01-01`);
    assert.equal(seen[0].status, 'active');
    await page.getByRole('button', { name: '下一頁' }).click();
    await page.getByRole('button', { name: '查看 第二位社員 消費明細' }).waitFor();
    assert.equal(seen.at(-1).offset, '20');

    await page.getByLabel('社員編號、姓名或 Email').fill('SPEND-001');
    await page.getByLabel('期間', { exact: true }).selectOption('custom');
    await page.getByLabel('開始日期', { exact: true }).fill('2026-11-01');
    await page.getByLabel('結束日期', { exact: true }).fill('2026-11-30');
    await page.getByLabel('會籍狀態', { exact: true }).selectOption('all');
    await page.getByLabel('排序', { exact: true }).selectOption('name_asc');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    const selected = page.getByRole('button', { name: '查看 合成社員 消費明細' });
    await selected.waitFor();
    assert.deepEqual(seen.at(-1), {
      path: reportPath, starts_on: '2026-11-01', ends_on: '2026-11-30', q: 'SPEND-001', status: 'all',
      sort: 'name_asc', limit: '20', offset: '0',
    });
    const [summaryExport] = await Promise.all([
      page.waitForEvent('download'), page.getByRole('button', { name: '匯出彙總 CSV' }).click(),
    ]);
    assert.equal(await readFile(await summaryExport.path(), 'utf8'), '日期,金額\n2026-11-01,-40');
    assert.equal(seen.at(-1).q, 'SPEND-001');

    await selected.click();
    const row = page.getByRole('row').filter({ hasText: 'ORDER-SPEND-01' });
    await row.waitFor();
    assert.match(await row.getByRole('link').getAttribute('href'), /order_id=01234567-abcd-1234-9876-000000000003/);
    assert.match(await row.innerText(), /2026\/11\/01.*ORDER-SPEND-01.*會餐.*現金.*AB12345678.*40/s);
    assert.equal(seen.at(-1).starts_on, '2026-11-01');
    const [detailExport] = await Promise.all([
      page.waitForEvent('download'), page.getByRole('button', { name: '匯出明細 CSV' }).click(),
    ]);
    assert.equal(detailExport.suggestedFilename(), '社員消費明細.csv');
    assert.equal(seen.at(-1).path, `${reportPath}/${person.id}/details.csv`);
    assert.equal(seen.at(-1).ends_on, '2026-11-30');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    assert.deepEqual(unexpected, []);
    if (process.env.BROWSER_SCREENSHOT_DIR) {
      await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
      await page.evaluate(() => document.activeElement?.blur());
      await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/member-spending-${width}.png`, fullPage: true });
    }
  });
}
