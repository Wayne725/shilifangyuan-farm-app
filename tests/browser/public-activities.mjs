import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const url = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['localhost', '127.0.0.1'].includes(url.hostname) || url.pathname !== '/') throw new Error('Use an isolated local origin');
const base = url.origin;

async function fixture(t, membershipType = null, width = 390) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 844 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { writes: [], errors: [], unknown: [] };
  const user = membershipType && { id: 'isolated-user', email: 'isolated@example.test', display_name: '活動測試使用者', membership_type: membershipType, membership_status: membershipType === 'member' ? 'active' : null, user_role: 'customer' };
  const activity = { id: 'public-activity', title: '公開永續走讀', description: '認識社區環境', location: '合作社門口', status: 'published', starts_at: '2099-01-03T02:00:00Z', ends_at: '2099-01-03T04:00:00Z', registration_deadline: '2099-01-02T02:00:00Z', capacity: 20, registration_count: 0, waitlist_count: 0, my_registration: null };
  await context.route('**/*', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (new URL(req.url()).origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: user ? 200 : 401, json: user ? { access_token: 'isolated', user } : { detail: '尚未登入' } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: membershipType, membership: membershipType === 'member' ? { status: 'active' } : null } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/activities' && req.method() === 'GET') return route.fulfill({ json: [activity] });
    if (path === '/v1/activities/public-activity/register' && req.method() === 'POST') {
      state.writes.push(path); activity.registration_count = 1; activity.my_registration = { id: 'registration', activity_id: activity.id, user_id: user.id, revision: 1, status: 'registered', queue_position: 0, contact_name: user.display_name, contact_email: user.email, charge: null, refund_request: null, available_actions: ['cancel'] };
      return route.fulfill({ json: activity.my_registration });
    }
    if (path === '/v1/activities/public-activity/cancel-registration' && req.method() === 'POST') {
      state.writes.push(path); activity.registration_count = 0; activity.my_registration.status = 'cancelled';
      return route.fulfill({ json: activity.my_registration });
    }
    if (['/v1/products', '/v1/notifications'].includes(path)) return route.fulfill({ json: [] });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true } });
    state.unknown.push(`${req.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離測試路由' } });
  });
  const page = await context.newPage();
  page.on('pageerror', error => state.errors.push(error.message));
  page.setDefaultTimeout(6000);
  return { page, state };
}

for (const width of [390, 1024]) test(`${width}px 訪客可瀏覽公開活動，點報名才登入`, async t => {
  const { page, state } = await fixture(t, null, width);
  await page.goto(base);
  await page.locator('.community-preview').getByRole('heading', { name: '公開永續走讀', exact: true }).waitFor();
  await page.getByRole('link', { name: '查看永續活動', exact: true }).click();
  await page.getByRole('heading', { name: '公開永續走讀', exact: true }).waitFor();
  assert.equal(await page.getByRole('navigation', { name: '社務功能', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '發起活動', exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/public-activities-${width}.png`, fullPage: true });
  await page.getByRole('button', { name: '登入報名', exact: true }).click();
  await page.waitForURL(/\/login/);
  assert.equal(new URL(page.url()).searchParams.get('return_to'), '/activities');
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

for (const membershipType of ['nonmember', 'trainee', 'member']) test(`${membershipType} 可報名與取消，發起權限維持社員限定`, async t => {
  const { page, state } = await fixture(t, membershipType);
  await page.goto(`${base}/activities`);
  await page.getByRole('button', { name: '立即報名', exact: true }).waitFor();
  if (membershipType === 'member') await page.getByRole('button', { name: '發起活動', exact: true }).waitFor();
  else assert.equal(await page.getByRole('button', { name: '發起活動', exact: true }).count(), 0);
  await page.getByRole('button', { name: '立即報名', exact: true }).click();
  await page.getByRole('button', { name: '確認報名', exact: true }).click();
  await page.getByRole('button', { name: '取消報名', exact: true }).click();
  await page.getByRole('button', { name: '確認取消', exact: true }).click();
  await page.getByRole('button', { name: '立即報名', exact: true }).waitFor();
  assert.deepEqual(state.writes, ['/v1/activities/public-activity/register', '/v1/activities/public-activity/cancel-registration']);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});
