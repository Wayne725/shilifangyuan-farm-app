import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const phone = '0912345001';
const member = {
  id: 'membership:member', name: '王○○', member_number: 'MEMBER-1', membership_type: 'member',
  personal_tax_type: 'tax_exempt', company_tax_type: 'taxable',
};
const trainee = {
  id: 'membership:trainee', name: '李○○', member_number: 'TRAINEE-1', membership_type: 'trainee',
  personal_tax_type: 'taxable', company_tax_type: 'taxable',
};

async function fixture(t, { width = 390, matches = [member], status = 'matched', firstFailure } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
  const state = { posts: [], lookups: [], unknown: [], errors: [] };
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (url.origin !== base) {
      if (!['fonts.googleapis.com', 'fonts.gstatic.com'].includes(url.hostname)) state.unknown.push(url.origin);
      return route.abort();
    }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: {
      id: 'isolated-staff', display_name: '隔離店員', user_role: 'admin', membership_type: 'nonmember', email: 'staff@example.test',
    } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/admin/manual-invoice-records') return route.fulfill({ json: { items: [], total: 0 } });
    if (path.startsWith('/v1/admin/walk-in-sales/request/')) {
      return route.fulfill({ status: 404, json: { detail: '尚未找到此筆隔離交易' } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/walk-in-sales/member-phone') {
      state.lookups.push(request.postDataJSON());
      return route.fulfill({ json: { status, phone_masked: '******5001', matches } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/walk-in-sales') {
      state.posts.push({ raw: request.postData(), body: request.postDataJSON() });
      if (state.posts.length === 1 && firstFailure) {
        const headers = firstFailure === 409 ? { 'X-Error-Code': 'walk_in_member_quote_stale' } : {};
        return route.fulfill({ status: firstFailure, headers, json: { detail: '隔離交易需要再次核對' } });
      }
      return route.fulfill({ status: 201, json: { order_id: 'isolated-sale', order_number: 'O2030010201', amount_total: 240,
        invoice_handling: 'on_payment', invoice_issue_after: null } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/admin?section=manual-invoice`);
  const region = page.getByRole('region', { name: '現場收款與開票', exact: true });
  await region.getByLabel('品名', { exact: true }).waitFor();
  return { page, region, state };
}

async function fillTransaction(region) {
  await region.getByLabel('品名', { exact: true }).fill('合成餐點');
  await region.getByLabel('含稅單價', { exact: true }).fill('120');
  await region.getByLabel('數量', { exact: true }).fill('2');
  await region.getByLabel('手機條碼', { exact: true }).fill('/AB12+-.');
}

async function lookup(region, { typePhone = true } = {}) {
  const field = region.getByLabel('電話號碼', { exact: true });
  if (typePhone) await field.fill(phone);
  await region.getByRole('button', { name: '核對社員', exact: true }).click();
  await region.getByRole('button', { name: '更改電話', exact: true }).waitFor();
  assert.equal(await field.inputValue(), '******5001');
}

async function assertClean(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.equal(await page.evaluate(value => JSON.stringify({ ...localStorage, ...sessionStorage }).includes(value), phone), false);
}

for (const width of [390, 1280]) {
  test(`${width}px 手填品名與單價，電話核對社員及鎖定稅別，公司名稱可留空`, async t => {
    const { page, region, state } = await fixture(t, { width });
    await fillTransaction(region);
    const confirm = region.getByRole('button', { name: '確認', exact: true });
    const tax = region.getByLabel('稅別', { exact: true });
    assert.equal(await region.getByRole('group', { name: '顧客身分', exact: true }).count(), 0);
    assert.equal(await region.getByLabel('顧客姓名', { exact: true }).count(), 0);
    assert.equal(await region.getByLabel('社員編號', { exact: true }).count(), 0);
    assert.equal(await tax.isEnabled(), true);
    await region.getByRole('button', { name: '一般商品', exact: true }).click();
    await lookup(region);
    await region.getByText('社員：王○○（MEMBER-1）', { exact: true }).waitFor();
    assert.equal(await tax.inputValue(), 'tax_exempt');
    assert.equal(await tax.isDisabled(), true);
    assert.equal(await confirm.isEnabled(), true);
    await region.getByRole('radio', { name: '公司統編發票', exact: true }).click();
    await region.getByLabel('統一編號', { exact: true }).fill('15989995');
    assert.equal(await tax.inputValue(), 'taxable');
    assert.equal(await region.getByLabel('公司名稱', { exact: true }).inputValue(), '');
    if (process.env.UI_ARTIFACT_DIR) {
      await mkdir(process.env.UI_ARTIFACT_DIR, { recursive: true });
      await region.screenshot({ path: join(process.env.UI_ARTIFACT_DIR, `manual-invoice-phone-${width}.png`), animations: 'disabled' });
    }
    await confirm.click();
    await region.getByText(/已記錄收款.*O2030010201/).waitFor();
    assert.deepEqual(state.lookups, [{ phone }]);
    const { body } = state.posts[0];
    assert.equal(body.phone_match_id, member.id);
    assert.equal(body.sale_category, 'product');
    assert.equal(body.product_name, '合成餐點');
    assert.equal(body.unit_price, 120);
    assert.equal(body.preferences.invoice_buyer_name, '');
    assert.equal(body.preferences.invoice_buyer_tax_id, '15989995');
    await assertClean(page, state);
  });
}

test('同一支電話對應多位社員時，須選一人才能收款', async t => {
  const { page, region, state } = await fixture(t, { matches: [member, trainee] });
  await fillTransaction(region);
  await lookup(region);
  const confirm = region.getByRole('button', { name: '確認', exact: true });
  assert.equal(await confirm.isDisabled(), true);
  await region.getByLabel('核對社員身分', { exact: true }).selectOption(trainee.id);
  await region.getByText('實習社員：李○○（TRAINEE-1）', { exact: true }).waitFor();
  assert.equal(await region.getByLabel('稅別', { exact: true }).inputValue(), 'taxable');
  await confirm.click();
  await region.getByText(/已記錄收款/).waitFor();
  assert.equal(state.posts[0].body.phone_match_id, trainee.id);
  await assertClean(page, state);
});

for (const [status, message] of [['no_match', '非社員，以一般顧客開立'], ['inactive', '此電話的社員資格已停止，以一般顧客開立']]) {
  test(`${status} 可用一般顧客開票並手選稅別`, async t => {
    const { page, region, state } = await fixture(t, { matches: [], status });
    await fillTransaction(region);
    await lookup(region);
    await region.getByText(message, { exact: true }).waitFor();
    await region.getByLabel('稅別', { exact: true }).selectOption('tax_exempt');
    await region.getByRole('button', { name: '確認', exact: true }).click();
    await region.getByText(/已記錄收款/).waitFor();
    assert.equal(state.posts[0].body.phone_match_id, null);
    assert.equal(state.posts[0].body.tax_type, 'tax_exempt');
    await assertClean(page, state);
  });
}

test('社員資格改變時須再次核對並使用新的交易識別碼', async t => {
  const { page, region, state } = await fixture(t, { firstFailure: 409 });
  await fillTransaction(region);
  await lookup(region);
  await region.getByRole('button', { name: '確認', exact: true }).click();
  await region.getByRole('button', { name: '核對社員', exact: true }).waitFor();
  const confirm = region.getByRole('button', { name: '確認', exact: true });
  assert.equal(await confirm.isDisabled(), true);
  await lookup(region, { typePhone: false });
  await confirm.click();
  await region.getByText(/已記錄收款/).waitFor();
  assert.notEqual(state.posts[0].body.client_request_id, state.posts[1].body.client_request_id);
  await assertClean(page, state);
});

test('社員交易結果待查核時，保留同一交易識別碼和交易內容', async t => {
  const { page, region, state } = await fixture(t, { firstFailure: 503 });
  await fillTransaction(region);
  await lookup(region);
  await region.getByRole('button', { name: '確認', exact: true }).click();
  const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
  await retry.waitFor();
  assert.equal(await region.getByLabel('電話號碼', { exact: true }).isDisabled(), true);
  await retry.click();
  await region.getByText(/已記錄收款/).waitFor();
  assert.equal(state.posts[0].raw, state.posts[1].raw);
  assert.equal(state.lookups.length, 1);
  await assertClean(page, state);
});
