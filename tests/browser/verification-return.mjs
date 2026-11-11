import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4195');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const email = 'member@example.test';
const mealPath = '/meals/verify-meal?pickup_at=2030-01-03T12%3A00#selection';
const meal = { id: 'verify-meal', title: '隔離驗證便當', location: '合作社', status: 'published', service_date: '2030-01-03', meal_period: 'lunch', ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-03T00:00:00+08:00', pickup_starts_at: '2030-01-03T11:00:00+08:00', pickup_ends_at: '2030-01-03T13:00:00+08:00', offerings: [] };

function verificationPath(returnTo) {
  const query = new URLSearchParams({ email, intent: 'existing_member' });
  if (returnTo !== undefined) query.set('return_to', returnTo);
  return `/verify-email?${query}`;
}

async function fixture(t, { signedIn = false, returnTo, verificationStatus = 200, width = 390 } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { signedIn, verified: false, calls: [], unknown: [], external: [], errors: [] };
  const session = () => ({ access_token: 'isolated-verification', user: { id: 'verify-buyer', display_name: '隔離社員', email, email_verified_at: state.verified ? '2030-01-02T00:00:00Z' : null, membership_type: state.verified ? 'member' : 'nonmember', user_role: 'customer' } });
  await context.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {
      if (request.isNavigationRequest()) state.external.push(url.origin);
      return route.abort();
    }
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    state.calls.push({ path, method: request.method(), body: request.postData() ? request.postDataJSON() : null });
    if (path === '/v1/auth/refresh') return route.fulfill({ status: state.signedIn ? 200 : 401, json: state.signedIn ? session() : { detail: '未登入' } });
    if (path === '/v1/auth/line/callback') {
      state.signedIn = true;
      return route.fulfill({ json: { status: 'authenticated', intent: 'member', return_to: verificationPath(returnTo), session: session() } });
    }
    if (path === '/v1/auth/verify-email') {
      state.verified = verificationStatus === 200;
      return route.fulfill({ status: verificationStatus, json: state.verified ? { message: 'Email 驗證完成' } : { detail: '驗證碼無效或已過期' } });
    }
    if (path === '/v1/auth/login') {
      state.signedIn = true;
      return route.fulfill({ json: session() });
    }
    if (path === '/v1/auth/register' || path === '/v1/auth/register-existing-member') return route.fulfill({ status: 202, json: { message: '請驗證你的 Email' } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, social_open: true } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: state.signedIn } });
    if (path === '/v1/meal-events/verify-meal') return route.fulfill({ json: meal });
    if (['/v1/products', '/v1/meal-events/calendar', '/v1/group-campaigns', '/v1/pickup-locations'].includes(path)) return route.fulfill({ json: [] });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.install({ time: new Date('2030-01-02T01:00:00Z') });
  return { page, state };
}

async function verify(page) {
  await page.getByLabel('六位數驗證碼', { exact: true }).fill('123456');
  await page.getByRole('button', { name: '完成驗證', exact: true }).click();
  await page.getByRole('heading', { name: 'Email 驗證完成', exact: true }).waitFor();
}

async function continueToDestination(page, signedIn, destination) {
  await page.getByRole('button', { name: signedIn ? '繼續使用' : '登入開始使用', exact: true }).click();
  if (!signedIn) {
    await page.waitForURL((url) => url.pathname === '/login');
    assert.equal(new URL(page.url()).searchParams.get('return_to'), destination);
    await page.getByLabel('電子信箱', { exact: true }).fill(email);
    await page.getByLabel('密碼', { exact: true }).fill('isolated-password');
    await page.getByRole('button', { name: '登入系統', exact: true }).click();
  }
  await page.waitForURL(`${base}${destination}`);
  await page.getByRole('heading', { name: destination === '/shop' ? '我要訂餐' : '選餐點與份數', exact: true }).waitFor();
  if (destination === mealPath) {
    await page.getByText('修改取餐時間', { exact: true }).click();
    assert.equal(await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).inputValue(), '2030-01-03T12:00');
  }
}

function assertIsolated(state) {
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.external, []);
  assert.equal(state.calls.some(({ path }) => /payment|refund|invoice|\/orders/.test(path)), false);
}

for (const width of [1440, 390]) for (const returnTo of [undefined, mealPath]) {
  test(`${width}px 首次 LINE 社員認領先驗證，成功返回 ${returnTo || '預設便當頁'}`, async (t) => {
    const { page, state } = await fixture(t, { returnTo, width });
    await page.goto(`${base}/auth/line/callback?state=isolated&code=isolated`);
    await page.getByLabel('六位數驗證碼', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/verify-email');
    assert.equal(new URL(page.url()).searchParams.get('return_to'), returnTo ?? null);
    assert.equal(await page.locator('.desktop-nav a[href="/social"]').count(), 0, '驗證前仍為非社員');
    await verify(page);
    await continueToDestination(page, true, returnTo || '/shop');
    assert.equal(await page.locator('.desktop-nav a[href="/social"]').count(), 1, '返回原頁時重新還原已驗證資格');
    assert.equal(state.calls.filter(({ path }) => path === '/v1/auth/line/callback').length, 1);
    assert.equal(state.calls.filter(({ path }) => path === '/v1/auth/verify-email').length, 1);
    assertIsolated(state);
  });
}

for (const intent of ['existing_member', 'nonmember']) {
  test(`${intent} Email 註冊保留原訂餐頁，未登入驗證後先登入再回訂餐`, async (t) => {
    const { page, state } = await fixture(t);
    await page.goto(`${base}/login?return_to=${encodeURIComponent(mealPath)}`);
    await page.getByRole('link', { name: '註冊帳號', exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get('return_to'), mealPath);
    await page.getByRole('button', { name: intent === 'existing_member' ? '我是既有社員' : '我不是社員', exact: true }).click();
    await page.getByRole('button', { name: '使用 Email 註冊', exact: true }).click();
    await page.getByLabel(intent === 'existing_member' ? '姓名' : '姓名或顯示名稱', { exact: true }).fill('隔離社員');
    if (intent === 'existing_member') await page.getByLabel('社員編號', { exact: true }).fill('TEST-001');
    await page.getByLabel('Email', { exact: true }).fill(email);
    await page.getByLabel('密碼', { exact: true }).fill('isolated-password');
    await page.getByLabel('再次輸入密碼', { exact: true }).fill('isolated-password');
    await page.getByRole('button', { name: '建立帳號', exact: true }).click();
    await page.getByRole('link', { name: '輸入驗證碼', exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get('return_to'), mealPath);
    await verify(page);
    assert.equal(state.signedIn, false);
    await continueToDestination(page, false, mealPath);
    assert.equal(state.calls.filter(({ path }) => path === '/v1/auth/login').length, 1);
    assertIsolated(state);
  });
}

for (const signedIn of [true, false]) {
  for (const returnTo of ['https://evil.example/shop', '//evil.example/shop', '/\\evil.example', '/shop?x=\n', '/shop?x=%0a', '/%2fevil.example', '/shop?x=%5c', '/verify-email?return_to=%2Fverify-email', '/verify%2demail', '/VERIFY-EMAIL/', '%2Fshop', '/login', '/auth/line/callback', '/%']) {
    test(`${signedIn ? '已登入' : '未登入'}驗證後拒絕不安全或循環返回 ${JSON.stringify(returnTo)}`, async (t) => {
      const { page, state } = await fixture(t, { signedIn });
      await page.goto(`${base}${verificationPath(returnTo)}`);
      await verify(page);
      await continueToDestination(page, signedIn, '/shop');
      assertIsolated(state);
    });
  }
  test(`${signedIn ? '已登入' : '未登入'}驗證碼失效留在驗證頁且不給繼續按鈕`, async (t) => {
    const { page, state } = await fixture(t, { signedIn, verificationStatus: 400 });
    await page.goto(`${base}${verificationPath(mealPath)}`);
    await page.getByLabel('六位數驗證碼', { exact: true }).fill('123456');
    await page.getByRole('button', { name: '完成驗證', exact: true }).click();
    await page.getByRole('alert').waitFor();
    assert.equal(new URL(page.url()).pathname, '/verify-email');
    assert.equal(await page.getByRole('button', { name: /^(繼續使用|登入開始使用)$/ }).count(), 0);
    assert.equal(state.verified, false);
    assertIsolated(state);
  });
}

test('註冊頁已有帳號直接登入，保留原便當參數與取餐時間', async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/register?intent=existing_member&return_to=${encodeURIComponent(mealPath)}`);
  await page.getByRole('link', { name: '直接登入', exact: true }).click();
  await page.waitForURL((url) => url.pathname === '/login');
  assert.equal(new URL(page.url()).searchParams.get('return_to'), mealPath);
  await page.getByLabel('電子信箱', { exact: true }).fill(email);
  await page.getByLabel('密碼', { exact: true }).fill('isolated-password');
  await page.getByRole('button', { name: '登入系統', exact: true }).click();
  await page.waitForURL(`${base}${mealPath}`);
  await page.getByRole('heading', { name: '選餐點與份數', exact: true }).waitFor();
  await page.getByText('修改取餐時間', { exact: true }).click();
  assert.equal(await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).inputValue(), '2030-01-03T12:00');
  assert.equal(state.calls.filter(({ path }) => path === '/v1/auth/login').length, 1);
  assert.equal(state.calls.some(({ path }) => /register|verify-email|line\/callback/.test(path)), false);
  assertIsolated(state);
});
