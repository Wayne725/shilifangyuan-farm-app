import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Synthetic loopback only');
const information = { published: true, legal_name: '隔離合作社', tax_id: '15989995', address: '隔離營業地址',
  phone: '0223456789', email: 'support@example.com', policy_version: 'test-v1', privacy: '合成個資告知\n\n資料用於帳號與交易聯繫。', terms: '合成服務條款', returns: '合成退款政策' };

async function fixture(t, published = true) {
  const browser = await engine.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const posts = [];
  let policy = { ...information };
  await context.route('**/v1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === '/v1/auth/refresh') return route.fulfill({ status: 401, json: { detail: '未登入' } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, public_information: published ? policy : null } });
    if (path === '/v1/auth/register') {
      posts.push(request.postDataJSON());
      return route.fulfill({ status: 201, json: { message: '合成註冊完成，請驗證Email' } });
    }
    return route.fulfill({ status: 404, json: { detail: 'Undefined synthetic API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  return { page, posts, updatePolicy: value => { policy = value; } };
}

test('390px公開政策與營業資料，內容使用純文字顯示', async t => {
  const { page } = await fixture(t);
  await page.goto(`${base}/privacy`);
  await page.getByText('合成個資告知', { exact: true }).waitFor();
  assert.equal(await page.getByText('隔離合作社 · 版本 test-v1', { exact: true }).count(), 1);
  assert.equal(await page.getByRole('navigation', { name: '網站政策' }).isVisible(), true);
  assert.equal(await page.getByRole('link', { name: '退換貨與退款政策', exact: true }).count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test('Email註冊須明確同意並送出當前政策版本', async t => {
  const { page, posts } = await fixture(t);
  await page.goto(`${base}/register?intent=nonmember`);
  await page.getByRole('button', { name: '使用 Email 註冊', exact: true }).click();
  await page.getByLabel('姓名或顯示名稱', { exact: true }).fill('隔離新買家');
  await page.getByLabel('Email', { exact: true }).fill('new@example.com');
  await page.getByLabel('密碼', { exact: true }).fill('synthetic-password-123');
  await page.getByLabel('再次輸入密碼', { exact: true }).fill('synthetic-password-123');
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  assert.equal(posts.length, 0);
  await page.getByRole('checkbox', { name: '我已閱讀個資告知並同意服務條款', exact: true }).check();
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  await page.getByRole('heading', { name: '請驗證你的 Email', exact: true }).waitFor();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].data_consent, true);
  assert.equal(posts[0].policy_version, 'test-v1');
});

test('未發布政策不公開草稿，也不冒充完成告知', async t => {
  const { page } = await fixture(t, false);
  await page.goto(`${base}/privacy`);
  await page.getByRole('heading', { name: '內容尚未發布', exact: true }).waitFor();
  assert.equal(await page.getByText('隔離營業地址', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('navigation', { name: '網站政策' }).count(), 0);
});

test('註冊表單背景更新政策後，舊勾選不能當成新版同意', async t => {
  const { page, posts, updatePolicy } = await fixture(t);
  await page.clock.install();
  await page.goto(`${base}/register?intent=nonmember`);
  await page.getByRole('button', { name: '使用 Email 註冊', exact: true }).click();
  await page.getByLabel('姓名或顯示名稱', { exact: true }).fill('隔離新買家');
  await page.getByLabel('Email', { exact: true }).fill('new@example.com');
  await page.getByLabel('密碼', { exact: true }).fill('synthetic-password-123');
  await page.getByLabel('再次輸入密碼', { exact: true }).fill('synthetic-password-123');
  const consent = page.getByRole('checkbox', { name: '我已閱讀個資告知並同意服務條款', exact: true });
  await consent.check();
  updatePolicy({ ...information, policy_version: 'test-v2', privacy: '新版合成告知' });
  await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === '/v1/platform'), page.clock.fastForward(31000)]);
  await page.locator('input[type="checkbox"]:not(:checked)').waitFor();
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  assert.equal(posts.length, 0);
  await consent.check();
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  await page.getByRole('heading', { name: '請驗證你的 Email', exact: true }).waitFor();
  assert.equal(posts[0].policy_version, 'test-v2');
});
