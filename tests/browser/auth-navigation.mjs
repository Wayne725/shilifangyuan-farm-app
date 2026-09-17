import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const base = process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
  throw new Error('Auth acceptance must use an isolated local website');
}
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const session = {
  access_token: 'isolated-auth-token',
  user: { id: 'auth-buyer', email: 'buyer@example.test', display_name: '驗證買家', user_role: 'customer', email_verified: true },
};

async function mockAccount(context, { signedIn = false, loginStatus = 200, verificationStatus = 200 } = {}) {
  let authenticated = signedIn;
  const calls = [];
  await context.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    calls.push({ path: url.pathname, method: request.method() });
    if (url.pathname === '/v1/auth/refresh') {
      return route.fulfill({ status: authenticated ? 200 : 401, json: authenticated ? session : { detail: '找不到登入工作階段' } });
    }
    if (url.pathname === '/v1/auth/login') {
      authenticated = loginStatus === 200;
      return route.fulfill({ status: loginStatus, json: authenticated ? session : { detail: '帳號或密碼錯誤' } });
    }
    if (url.pathname === '/v1/auth/verify-email') {
      return route.fulfill({ status: verificationStatus, json: verificationStatus === 200 ? { message: '驗證完成' } : { detail: '驗證碼已失效' } });
    }
    if (url.pathname.startsWith('/v1/auth/register')) {
      return route.fulfill({ json: { message: '請驗證你的 Email' } });
    }
    return route.fulfill({ json: [] });
  });
  return calls;
}

async function login(page) {
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('電子信箱', { exact: true }).fill('buyer@example.test');
  await dialog.getByLabel('密碼', { exact: true }).fill('Isolated-test-only-123');
  await dialog.getByRole('button', { name: '登入系統', exact: true }).click();
}

test('驗證完成後登入回到首頁，不再顯示登入提示', async () => {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext();
    const calls = await mockAccount(context);
    const page = await context.newPage();
    await page.goto(`${base}/verify-email?email=buyer%40example.test`);
    await page.getByLabel('六位數驗證碼', { exact: true }).fill('123456');
    await page.getByRole('button', { name: '完成驗證', exact: true }).click();
    await page.getByRole('button', { name: '登入開始使用', exact: true }).click();
    assert.equal(new URL(page.url()).pathname, '/verify-email');
    await login(page);
    await page.waitForURL(`${base}/`, { timeout: 5000 });
    assert.equal(await page.getByRole('dialog').count(), 0);
    assert.equal(await page.getByRole('button', { name: '登入開始使用', exact: true }).count(), 0);
    assert.equal(calls.filter(call => call.path === '/v1/auth/login').length, 1);
    await page.reload();
    await page.getByRole('link', { name: '驗證買家', exact: true }).first().waitFor();
    assert.equal(new URL(page.url()).pathname, '/');
  } finally {
    await browser.close();
  }
});

for (const intent of ['nonmember', 'existing_member']) {
  test(`${intent} 註冊、驗證、登入完整流程回首頁，上一頁不再要求登入`, async () => {
    const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    try {
      const context = await browser.newContext();
      const calls = await mockAccount(context);
      const page = await context.newPage();
      await page.goto(`${base}/shop`);
      await page.getByLabel('取餐日期', { exact: true }).waitFor();
      await page.goto(`${base}/register?intent=${intent}`);
      if (intent === 'existing_member') {
        await page.getByLabel('社員編號', { exact: true }).fill('TEST-001');
        await page.getByLabel('名冊登記姓名', { exact: true }).fill('隔離測試社員');
        await page.getByLabel('名冊登記手機', { exact: true }).fill('0912345678');
      }
      await page.getByLabel('顯示名稱', { exact: true }).fill('驗證買家');
      await page.getByLabel('電子信箱', { exact: true }).fill('buyer@example.test');
      await page.getByLabel('密碼', { exact: true }).fill('Isolated-test-only-123');
      await page.getByLabel('再次輸入密碼', { exact: true }).fill('Isolated-test-only-123');
      await page.getByRole('button', { name: '建立帳號', exact: true }).click();
      await page.getByRole('link', { name: '輸入驗證碼', exact: true }).click();
      await page.getByLabel('六位數驗證碼', { exact: true }).fill('123456');
      await page.getByRole('button', { name: '完成驗證', exact: true }).click();
      await page.getByRole('button', { name: '登入開始使用', exact: true }).click();
      await login(page);
      await page.waitForURL(`${base}/`, { timeout: 5000 });
      await page.goBack();
      await page.waitForURL(`${base}/`, { timeout: 5000 });
      assert.equal(await page.getByRole('dialog').count(), 0);
      assert.equal(calls.filter(call => call.path === '/v1/auth/login').length, 1);
      assert.equal(calls.filter(call => call.path.startsWith('/v1/auth/register')).length, 1);
      assert.equal(calls.filter(call => call.path === '/v1/auth/verify-email').length, 1);
    } finally {
      await browser.close();
    }
  });
}

for (const path of ['/register', '/register?intent=nonmember', '/verify-email?token=123456']) {
  test(`已登入開啟 ${path} 直接回首頁`, async () => {
    const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    try {
      const context = await browser.newContext();
      const calls = await mockAccount(context, { signedIn: true });
      const page = await context.newPage();
      await page.goto(`${base}${path}`);
      await page.waitForURL(`${base}/`, { timeout: 5000 });
      assert.equal(await page.getByRole('dialog').count(), 0);
      assert.equal(calls.filter(call => call.path === '/v1/auth/login' || call.path === '/v1/auth/verify-email').length, 0);
    } finally {
      await browser.close();
    }
  });
}

test('驗證完成但登入失敗時留在驗證頁，不誤判已登入', async () => {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext();
    await mockAccount(context, { loginStatus: 401 });
    const page = await context.newPage();
    await page.goto(`${base}/verify-email?email=buyer%40example.test&token=123456`);
    await page.getByLabel('六位數驗證碼', { exact: true }).fill('123456');
    await page.getByRole('button', { name: '完成驗證', exact: true }).click();
    await page.getByRole('button', { name: '登入開始使用', exact: true }).click();
    await login(page);
    await page.getByRole('dialog').getByText('帳號或密碼錯誤', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/verify-email');
  } finally {
    await browser.close();
  }
});

test('驗證碼失效時顯示錯誤，不能當成驗證或登入成功', async () => {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext();
    await mockAccount(context, { verificationStatus: 400 });
    const page = await context.newPage();
    await page.goto(`${base}/verify-email?email=buyer%40example.test&token=123456`);
    await page.getByLabel('六位數驗證碼', { exact: true }).fill('123456');
    await page.getByRole('button', { name: '完成驗證', exact: true }).click();
    await page.getByRole('alert').waitFor();
    assert.equal(await page.getByRole('alert').textContent(), '驗證碼已失效');
    assert.equal(new URL(page.url()).pathname, '/verify-email');
    assert.equal(await page.getByRole('button', { name: '登入開始使用', exact: true }).count(), 0);
  } finally {
    await browser.close();
  }
});

for (const path of ['/shop', '/checkout', '/orders?order_id=isolated-order']) {
  test(`${path} 中途登入保留原頁與網址參數`, async () => {
    const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    try {
      const context = await browser.newContext();
      await mockAccount(context);
      const page = await context.newPage();
      await page.goto(`${base}${path}`);
      await page.getByRole('button', { name: '帳號登入', exact: true }).first().click();
      await login(page);
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      await page.getByRole('link', { name: '驗證買家', exact: true }).first().waitFor();
      assert.equal(page.url(), `${base}${path}`);
    } finally {
      await browser.close();
    }
  });
}
