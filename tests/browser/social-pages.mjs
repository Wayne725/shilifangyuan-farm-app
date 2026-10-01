import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['localhost', '127.0.0.1'].includes(testUrl.hostname) || testUrl.pathname !== '/' || testUrl.username || testUrl.password) {
  throw new Error('Social page acceptance must use an isolated local website');
}
const base = testUrl.origin;

async function fixture(t, membershipType = 'member', meetingFailure = false) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const reads = [];
  const unknownApi = [];
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    reads.push(url.pathname);
    if (url.pathname === '/v1/auth/refresh') return route.fulfill({ json: {
      access_token: 'isolated-social-token', user: { id: 'isolated-member', email: 'member@example.test', display_name: '測試社員', user_role: 'customer' },
    } });
    if (url.pathname === '/v1/members/me') return route.fulfill({ json: {
      membership_type: membershipType,
      membership: { member_number: 'ISOLATED-001', share_count: 999, share_capital_amount: 999999, share_certificate_number: 'MUST-NOT-DISPLAY' },
      directory: { is_public: false },
    } });
    if (url.pathname === '/v1/member-proposals') return route.fulfill({ json: [{
      id: 'proposal-isolated', title: '隔離測試提案', body: '只供本機測試', status: 'discussing', proposal_type: 'resolution', created_by_name: '測試社員', tally: { total: 0 },
    }] });
    if (url.pathname === '/v1/meetings') return route.fulfill({ status: meetingFailure ? 503 : 200, json: meetingFailure ? { detail: '隔離會議服務暫不可用' } : [{
      id: 'meeting-isolated', meeting_type: 'general_assembly', title: '隔離測試社員大會', starts_at: '2026-09-20T04:00:00Z', location: '本機測試地點',
      agenda: [{ title: '測試議程' }], resolutions: [{ id: 'resolution-isolated', title: '測試決議', resolution_text: '本機內容' }],
      attendance_rate: 0.5, attended_count: 1, eligible_member_count: 2,
    }] });
    if (['/v1/activities', '/v1/notifications'].includes(url.pathname)) return route.fulfill({ json: [] });
    unknownApi.push(url.pathname);
    return route.fulfill({ status: 404, json: { detail: 'API未設定於隔離測試' } });
  });
  const page = await context.newPage();
  return { page, reads, unknownApi };
}

test('社員提案及會議各自載入獨立內容，手機導覽可切換', async (t) => {
  const { page, reads, unknownApi } = await fixture(t);
  await page.goto(`${base}/governance`);
  await page.getByRole('heading', { name: '隔離測試提案', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '隔離測試社員大會', exact: true }).count(), 0);
  assert.equal(reads.includes('/v1/meetings'), false);
  await page.getByRole('navigation', { name: '社務功能' }).getByRole('link', { name: '社員會議', exact: true }).click();
  await page.getByRole('heading', { name: '隔離測試社員大會', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '隔離測試提案', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '提出提案', exact: true }).count(), 0);
  await page.getByText('測試議程', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/member-meetings.png`, fullPage: true });
  assert.deepEqual(unknownApi, []);
});

test('未轉正帳號不能讀取社員會議', async (t) => {
  const { page, reads } = await fixture(t, 'trainee');
  await page.goto(`${base}/meetings`);
  await page.getByRole('heading', { name: '此功能開放給正式社員', exact: true }).waitFor();
  assert.equal(reads.includes('/v1/meetings'), false);
});

test('會議讀取失敗顯示錯誤，不假裝沒有會議', async (t) => {
  const { page } = await fixture(t, 'member', true);
  await page.goto(`${base}/meetings`);
  await page.getByText('會議暫時無法讀取', { exact: true }).waitFor();
  assert.equal(await page.getByText('目前沒有會議紀錄', { exact: true }).count(), 0);
});

test('社員首頁保留社員編號，隱藏股金股數且分列提案會議', async (t) => {
  const { page, unknownApi } = await fixture(t);
  await page.goto(`${base}/social`);
  await page.getByText('ISOLATED-001', { exact: true }).waitFor();
  const content = await page.locator('main').innerText();
  assert.doesNotMatch(content, /股數|股金|股票號碼|999999|MUST-NOT-DISPLAY/);
  await page.getByRole('heading', { name: '提案進度', exact: true }).waitFor();
  await page.getByRole('heading', { name: '社員會議', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(unknownApi, []);
});
