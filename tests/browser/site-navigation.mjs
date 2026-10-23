import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const cases = [
  { name: '訪客', user: null, social: false },
  { name: '非社員', user: { membership_type: 'nonmember' }, social: false },
  { name: '實習社員', user: { membership_type: 'trainee', membership_status: 'trainee' }, social: false },
  { name: '正式社員', user: { membership_type: 'member', membership_status: 'active' }, social: true },
  { name: '停權社員', user: { membership_type: 'member', membership_status: 'suspended' }, social: false },
  { name: '管理員', user: { membership_type: 'nonmember', user_role: 'admin' }, social: true },
];

async function fixture(t, width, sample) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const user = sample.user && { id: 'isolated', email: 'navigation@example.test', display_name: '隔離導覽測試帳號名稱很長', user_role: 'customer', ...sample.user };
  const state = { reads: [], unknown: [], errors: [], signedIn: Boolean(user) };
  await context.route('**/*', async (route) => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    state.reads.push(path);
    if (path === '/v1/auth/refresh') return route.fulfill({ status: state.signedIn ? 200 : 401, json: state.signedIn ? { access_token: 'isolated', user } : { detail: '尚未登入' } });
    if (path === '/v1/auth/logout') { state.signedIn = false; return route.fulfill({ status: 204, body: '' }); }
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: sample.confirmedMembership || (sample.social ? 'member' : user?.membership_type || 'nonmember'), membership: { status: 'active' } } });
    if (['/v1/products', '/v1/group-campaigns', '/v1/meal-events', '/v1/meal-orders', '/v1/activities', '/v1/membership/charges', '/v1/notifications', '/v1/me/badges', '/v1/me/surplus-distributions'].includes(path)) return route.fulfill({ json: [] });
    if (path === '/v1/me/points') return route.fulfill({ json: { balance: 0 } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false, identity_verified: false, message: '' } });
    state.unknown.push(`${req.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated navigation route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { page, state };
}

async function verifyLayout(page, width) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  if (width <= 900) return;
  const layout = await page.locator('.site-header').evaluate((header) => {
    const nav = header.querySelector('.desktop-nav').getBoundingClientRect();
    const actions = header.querySelector('.header-actions').getBoundingClientRect();
    const frame = header.getBoundingClientRect();
    return { navRight: nav.right, actionsLeft: actions.left, actionsRight: actions.right, frameRight: frame.right };
  });
  assert.ok(layout.navRight <= layout.actionsLeft, JSON.stringify(layout));
  assert.ok(layout.actionsRight <= layout.frameRight, JSON.stringify(layout));
}

for (const width of [1440, 1024, 390]) for (const sample of cases) {
  test(`${width}px ${sample.name}導覽資格、桌面選單、常見問題與放大排版`, async (t) => {
    const { page, state } = await fixture(t, width, sample);
    await page.goto(base);
    await page.getByRole('heading', { name: /讓一日三餐/ }).waitFor();
    await page.waitForFunction((signedIn) => {
      const account = document.querySelector('.site-header .account-button');
      return signedIn ? account?.tagName === 'A' : account?.tagName === 'BUTTON' && !account.disabled;
    }, Boolean(sample.user));
    assert.equal(await page.locator('a[href="/social"]').count() > 0, sample.social);
    assert.equal(state.reads.includes('/v1/activities'), sample.social);
    await page.locator('.reading-menu > summary').click();
    const reading = page.getByRole('group', { name: '文字大小', exact: true });
    assert.doesNotMatch(await reading.innerText(), /字體大小/);
    assert.deepEqual(await reading.getByRole('button').allTextContents(), ['標準', '放大']);
    for (const size of ['標準', '放大']) {
      if (size === '放大') await page.locator('.reading-menu > summary').click();
      await reading.getByRole('button', { name: size, exact: true }).click();
      await page.waitForFunction((large) => document.documentElement.dataset.textSize === (large ? 'large' : 'standard'), size === '放大');
      await verifyLayout(page, width);
    }
    const toggle = page.getByRole('button', { name: '開啟導覽', exact: true });
    assert.equal(await toggle.isVisible(), true);
    await toggle.click();
    const menu = page.getByRole('navigation', { name: '網站選單', exact: true });
    await menu.waitFor();
    assert.equal(await menu.getByRole('link', { name: '社務系統', exact: true }).count(), 1);
    assert.equal(await menu.getByRole('link', { name: '管理工作台', exact: true }).count(), sample.user?.user_role === 'admin' ? 1 : 0);
    await menu.getByRole('link', { name: '常見問題', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '關閉導覽', exact: true }).getAttribute('aria-expanded'), 'true');
    await page.keyboard.press('Escape');
    assert.equal(await menu.count(), 0);
    assert.equal(await toggle.evaluate((element) => document.activeElement === element), true);
    await toggle.click();
    if (process.env.NAV_SCREENSHOTS && width === 1440 && sample.name === '正式社員') {
      await page.locator('.site-header').screenshot({ path: '/Users/liuweiwei/Documents/farm/outputs/desktop-member-header-verified.png' });
      await menu.screenshot({ path: '/Users/liuweiwei/Documents/farm/outputs/desktop-member-menu-verified.png' });
    }
    await menu.getByRole('link', { name: '常見問題', exact: true }).click();
    await page.waitForURL(`${base}/help`);
    assert.equal(await menu.count(), 0);
    await page.locator('.reading-menu > summary').click();
    assert.equal(await reading.getByRole('button', { name: '放大', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.locator('.reading-menu > summary').click();
    if (['訪客', '非社員', '實習社員'].includes(sample.name)) {
      await toggle.click();
      await menu.getByRole('link', { name: '社務系統', exact: true }).click();
      const gateTitle = !sample.user ? '登入後進入社務系統' : sample.name === '實習社員' ? '實習社員資格已啟用' : '開始入社程序';
      await page.getByRole('heading', { name: gateTitle, exact: true }).waitFor();
      assert.equal(await menu.count(), 0);
      assert.equal(await page.getByRole('navigation', { name: '社務功能', exact: true }).count(), 0);
      assert.equal(state.reads.some((path) => ['/v1/activities', '/v1/member-proposals', '/v1/meetings'].includes(path)), false);
    }
    assert.deepEqual(state.errors, []);
    assert.deepEqual(state.unknown, []);
  });
}

test('登出後即隱藏原社員導覽入口，購物與常見問題仍可使用', async (t) => {
  const { page, state } = await fixture(t, 1440, cases.find((row) => row.name === '正式社員'));
  await page.goto(base);
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('button', { name: /^登出 / }).click();
  await page.getByRole('button', { name: '帳號登入', exact: true }).waitFor();
  assert.equal(await page.locator('a[href="/social"]').count(), 0);
  assert.equal(await page.getByRole('navigation', { name: '網站選單', exact: true }).count(), 0);
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('navigation', { name: '網站選單', exact: true }).getByRole('link', { name: '生活消費', exact: true }).click();
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('navigation', { name: '網站選單', exact: true }).getByRole('link', { name: '常見問題', exact: true }).waitFor();
  assert.equal(await page.getByRole('link', { name: '需要幫忙', exact: true }).count(), 0);
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []);
});

for (const width of [1440, 390]) {
  test(`${width}px 消費類型切換同步標題與訂單入口，常見問題僅在選單`, async (t) => {
    const { page, state } = await fixture(t, width, cases.find((row) => row.name === '非社員'));
    await page.goto(`${base}/shop`);
    await page.getByRole('heading', { name: '我要訂餐', exact: true }).waitFor();
    for (const [tabName, title] of [['共同購買', '共同購買'], ['日常選品', '日常選品'], ['便當預購', '我要訂餐']]) {
      await page.getByRole('tab', { name: tabName, exact: true }).click();
      assert.equal(await page.getByRole('heading', { level: 1 }).innerText(), title);
      assert.equal(await page.getByRole('link', { name: '我的便當訂單', exact: true }).count(), tabName === '便當預購' ? 1 : 0);
    }
    assert.equal(await page.getByRole('link', { name: /常見問題/ }).count(), 0);
    await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
    assert.equal(await page.getByRole('link', { name: /常見問題/ }).count(), 1);
    await page.getByRole('button', { name: '關閉導覽', exact: true }).click();
    await page.getByRole('link', { name: '我的便當訂單', exact: true }).click();
    await page.getByRole('heading', { name: '餐點訂單', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: /常見問題/ }).count(), 0);
    if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/meal-orders-heading-${width}.png`, fullPage: true });
    assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []);
  });
}

test('會員中心取得最新社員資格後導覽同步更新，不額外查詢或依註冊意向升權', async (t) => {
  const sample = { name: '新轉正帳號', user: { membership_type: 'nonmember' }, confirmedMembership: 'member', social: false };
  const { page, state } = await fixture(t, 1440, sample);
  await page.goto(`${base}/account`);
  const navigation = page.getByRole('navigation', { name: '主要導覽', exact: true });
  await navigation.getByRole('link', { name: '社務系統', exact: true }).waitFor();
  assert.equal(sample.user.membership_type, 'nonmember');
  assert.equal(state.reads.filter((path) => path === '/v1/members/me').length, 1);
  await navigation.getByRole('link', { name: '首頁', exact: true }).click();
  await navigation.getByRole('link', { name: '社務系統', exact: true }).waitFor();
  assert.equal(state.reads.filter((path) => path === '/v1/members/me').length, 1);
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []);
});
