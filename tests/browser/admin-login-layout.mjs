import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
async function fixture(t, width, { signedIn = true, failed = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block', timezoneId: 'Asia/Taipei' });
  const state = { signedIn, status: 'active', writes: [], unknown: [], errors: [] };
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: state.signedIn ? 200 : 401, json: state.signedIn ? { access_token: 'isolated', user: { id: 'admin', display_name: '隔離管理員', email: 'admin@example.test', user_role: 'admin' } } : { detail: '尚未登入' } });
    if (path === '/v1/admin/users') return route.fulfill({ json: { total: 1, items: [{ id: 'buyer', membership_id: 'membership-1', membership_status: state.status, membership_type: state.status === 'active' ? 'member' : 'nonmember', display_name: '隔離測試社員', email: 'buyer@example.test', member_number: 'TEST-001', is_active: true, user_role: 'customer', created_at: '2030-01-01T00:00:00Z' }] } });
    if (path === '/v1/admin/members/membership-1/suspend' && req.method() === 'POST') { state.writes.push(req.postDataJSON()); if (!failed) state.status = 'suspended'; return route.fulfill({ status: failed ? 409 : 200, json: failed ? { detail: '此會籍目前不可執行該操作' } : { id: 'membership-1', status: 'suspended' } }); }
    if (path === '/v1/admin/meal-kitchen') return route.fulfill({ json: { items: [], total: 0, meal_quantity: 0, products: [] } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false } });
    state.unknown.push(`${req.method()} ${path}`); return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000); page.on('pageerror', error => state.errors.push(error.message));
  return { page, state };
}
async function verify(page, state) { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true); }
for (const width of [816, 390]) {
  test(`${width}px 會員與廚房控制列等高、放大不溢出，會員標題無解釋副標`, async t => {
    const { page, state } = await fixture(t, width);
    for (const path of ['/admin/users', '/admin/kitchen']) {
      await page.goto(`${base}${path}`);
      await page.getByRole('navigation', { name: '管理工作台分類' }).waitFor();
      const selector = path.endsWith('users') ? '.admin-user-search' : '.meal-kitchen-filters';
      for (const size of ['標準', '放大']) {
        await page.getByRole('group', { name: '文字大小' }).getByRole('button', { name: size, exact: true }).click();
        await page.waitForFunction(large => document.documentElement.dataset.textSize === (large ? 'large' : 'standard'), size === '放大');
        const heights = await page.locator(`${selector} input, ${selector} select, ${selector} button`).evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
        assert.ok(Math.max(...heights) - Math.min(...heights) < 1, JSON.stringify(heights));
        if (width === 816) { const bottoms = await page.locator(`${selector} input, ${selector} select, ${selector} button`).evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().bottom)); assert.ok(Math.max(...bottoms) - Math.min(...bottoms) < 1, JSON.stringify(bottoms)); }
        await verify(page, state);
      }
      if (path.endsWith('users')) assert.equal(await page.getByText('查看所有註冊者，核對線下入社後授予社員資格。').count(), 0);
      if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/${path.endsWith('users') ? 'admin-users' : 'kitchen'}-aligned-${width}.png`, fullPage: true });
    }
  });
  test(`${width}px 移除社員需確認，取消不送出，成功後顯示非社員並保留恢復入口`, async t => {
    const { page, state } = await fixture(t, width); await page.goto(`${base}/admin/users`);
    await page.getByRole('button', { name: '移除社員資格', exact: true }).click();
    await page.getByRole('button', { name: '取消', exact: true }).click(); assert.equal(state.writes.length, 0);
    await page.getByRole('button', { name: '移除社員資格', exact: true }).click();
    await page.getByRole('button', { name: '社員資格異動', exact: true }).click();
    await page.getByRole('button', { name: '確認改為非社員', exact: true }).click();
    await page.getByText('非社員（會籍暫停）', { exact: true }).waitFor();
    assert.deepEqual(state.writes, [{ reason: '社員資格異動' }]);
    await page.getByRole('button', { name: '核對並恢復社員資格', exact: true }).waitFor(); await verify(page, state);
  });
  test(`${width}px 說明頁只有內容與回首頁，登入頁以Email為主並將LINE及社員選項放下方`, async t => {
    const { page, state } = await fixture(t, width, { signedIn: false });
    await page.goto(`${base}/help`); await page.getByRole('link', { name: '回到首頁', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '我要訂餐', exact: true }).count(), 0);
    assert.equal(await page.getByRole('link', { name: '我的便當訂單', exact: true }).count(), 0);
    await page.goto(`${base}/login?return_to=https%3A%2F%2Fevil.example`);
    await page.getByRole('heading', { name: '帳號登入', exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0);
    assert.equal(await page.getByRole('link', { name: '返回上一頁', exact: true }).getAttribute('href'), '/shop');
    const main = page.getByRole('main');
    const positions = await main.locator('.login-form > button, .login-secondary').evaluateAll(nodes => nodes.map(node => { const box = node.getBoundingClientRect(); return { top: box.top, bottom: box.bottom }; }));
    assert.ok(positions[1].top >= positions[0].bottom);
    await page.getByRole('group', { name: '文字大小' }).getByRole('button', { name: '放大', exact: true }).click(); await verify(page, state);
    if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/login-page-${width}.png`, fullPage: true });
  });
}
test('移除社員失敗顯示原錯誤，不假裝降為非社員', async t => {
  const { page, state } = await fixture(t, 816, { failed: true }); await page.goto(`${base}/admin/users`);
  await page.getByRole('button', { name: '移除社員資格', exact: true }).click(); await page.getByRole('button', { name: '確認改為非社員', exact: true }).click();
  await page.getByRole('alert').getByText('此會籍目前不可執行該操作').waitFor(); assert.equal(state.status, 'active'); assert.equal(state.writes.length, 1); await verify(page, state);
});
