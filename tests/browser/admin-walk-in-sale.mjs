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
const requestKey = 'slf_admin_walk_in_request_v1';
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const staffBarcode = '/STAFF12';
const customerBarcode = '/AB12+-.';

async function fixture(t, { width = 1280, firstResponse = 'success', section = 'manual-invoice', role = 'admin' } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { posts: [], recoveries: [], adminRequests: [], sales: new Map(), recoveryVisible: true,
    unknown: [], errors: [], consoleErrors: [] };
  const firstPostResponse = firstResponse === 'hold' ? new Promise(resolve => { state.releasePost = resolve; }) : null;
  t.after(async () => { state.releasePost?.(); await browser.close(); });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {
      const expectedFont = (url.hostname === 'fonts.googleapis.com' && request.resourceType() === 'stylesheet')
        || (url.hostname === 'fonts.gstatic.com' && request.resourceType() === 'font');
      if (!expectedFont) state.unknown.push(`EXTERNAL ${url.origin}`);
      return route.abort();
    }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: role === 'guest' ? 401 : 200, json: role === 'guest' ? { detail: '隔離訪客' } : { access_token: 'isolated', user: {
      id: 'isolated-staff', email: 'staff@example.test', display_name: '隔離店員', user_role: role,
      membership_type: 'nonmember', saved_mobile_barcode: staffBarcode,
    } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '暫停接受新訂單' } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path.startsWith('/v1/admin/')) state.adminRequests.push(`${request.method()} ${path}`);
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [], total: 0, limit: 12, offset: 0 } });
    if (request.method() === 'GET' && path === '/v1/admin/manual-invoice-records') {
      const items = Array.from(state.sales.values(), ({ sale }) => ({
        id: sale.order_id, ...sale, amount: sale.amount_total,
        recorded_at: '2030-01-02T01:00:00Z', operator_name: '隔離店員',
        action: 'walk_in_sale.collected', handling: sale.invoice_handling,
        invoice_status: 'pending', reason: '現場客人收款',
      }));
      return route.fulfill({ json: { items, total: items.length } });
    }
    if (request.method() === 'GET' && path.startsWith('/v1/admin/walk-in-sales/request/')) {
      const id = path.split('/').at(-1);
      state.recoveries.push(id);
      const existing = state.recoveryVisible && state.sales.get(id);
      if (existing && state.holdRecovery) await state.holdRecovery;
      return route.fulfill(existing ? { json: existing.sale } : { status: 404, json: { detail: '尚未找到此筆隔離交易' } });
    }
    if (request.method() === 'GET' && path === '/v1/admin/walk-in-sales/isolated-walk-in-order') {
      const stored = [...state.sales.values()].find(({ sale }) => sale.order_id === 'isolated-walk-in-order');
      return route.fulfill(stored ? { json: stored.sale } : { status: 404, json: { detail: '尚未找到此筆隔離交易' } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/walk-in-sales') {
      const raw = request.postData(), body = request.postDataJSON();
      state.posts.push({ raw, body });
      const existing = state.sales.get(body.client_request_id);
      if (existing && existing.raw !== raw) return route.fulfill({ status: 409, json: { detail: '同一編號不得變更交易內容' } });
      const sale = existing?.sale || {
        order_id: 'isolated-walk-in-order', order_number: 'O2030010201', amount_total: body.unit_price * body.quantity,
        invoice_handling: body.invoice_handling, invoice_issue_after: null, sale_category: body.sale_category,
      };
      state.sales.set(body.client_request_id, { raw, sale });
      if (state.posts.length === 1 && firstResponse === 'abort') return route.abort('failed');
      if (state.posts.length === 1 && firstResponse === '503') return route.fulfill({ status: 503, json: { detail: '隔離回應中斷，交易結果待確認' } });
      if (state.posts.length === 1 && firstPostResponse) await firstPostResponse;
      if (existing && state.holdRetry) await state.holdRetry;
      return route.fulfill({ status: existing ? 200 : 201, json: sale });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') state.consoleErrors.push(message.text()); });
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  await page.goto(`${base}/admin?section=${section}`);
  const region = page.getByRole('region', { name: '現場收款與開票', exact: true });
  try {
    if (role === 'admin' && section === 'manual-invoice') {
      await page.getByRole('heading', { name: '手動開票', level: 1, exact: true }).waitFor();
      await region.getByLabel('含稅單價', { exact: true }).waitFor();
    } else if (role === 'admin') {
      await page.getByLabel('搜尋訂單', { exact: true }).waitFor();
    } else {
      await page.waitForFunction(signedIn => {
        const account = document.querySelector('.site-header .account-button');
        return signedIn ? account?.tagName === 'A' : account?.tagName === 'BUTTON' && !account.disabled;
      }, role !== 'guest');
      await page.getByRole('heading', { name: '管理工作台', level: 1, exact: true }).waitFor();
    }
  } catch (error) {
    t.diagnostic(JSON.stringify({ url: page.url(), errors: state.errors, unknown: state.unknown,
      consoleErrors: state.consoleErrors.slice(0, 10), body: (await page.locator('body').innerText()).slice(0, 6000) }));
    throw error;
  }
  return { page, region, state };
}

async function fillSale(region, { barcode = false } = {}) {
  const product = region.getByRole('textbox', { name: '品名', exact: true });
  assert.equal(await product.inputValue(), '');
  assert.equal(await product.evaluate(input => input.required), true);
  await product.fill('合成餐盒');
  await region.getByLabel('含稅單價', { exact: true }).fill('120');
  await region.getByLabel('數量', { exact: true }).fill('2');
  await region.getByLabel('發票通知 Email', { exact: true }).fill('customer@example.test');
  if (!barcode) await region.getByRole('radio', { name: 'Email 會員載具', exact: true }).check();
  if (barcode) {
    await region.getByRole('radio', { name: '手機條碼載具', exact: true }).check();
    assert.equal(await region.getByLabel('手機條碼', { exact: true }).inputValue(), '', '不得帶入店員已儲存的載具');
    await region.getByLabel('手機條碼', { exact: true }).fill(customerBarcode);
  }
}

async function assertRecorded(region) {
  await region.getByText(/已記錄收款.*O2030010201/).waitFor();
  await region.getByText('已安排系統開票', { exact: true }).waitFor();
  assert.equal(await region.getByRole('button', { name: '確認', exact: true }).count(), 0);
  assert.equal(await region.getByRole('button', { name: '重試同一筆交易', exact: true }).count(), 0);
}

async function assertDirectForm(page, region, state) {
  assert.equal(new URL(page.url()).searchParams.get('section'), 'manual-invoice');
  assert.equal(await page.getByLabel('搜尋訂單', { exact: true }).count(), 0);
  assert.equal(state.adminRequests.includes('GET /v1/admin/order-search'), false);
  assert.equal(await region.getByRole('button', { name: '現場收款與開票', exact: true }).count(), 0);
  assert.equal(await region.getByLabel('開票時機', { exact: true }).count(), 0);
  assert.equal(await region.getByLabel('取餐日期', { exact: true }).count(), 0);
  assert.equal(await region.getByRole('checkbox').count(), 0);
  assert.equal(await region.getByRole('button', { name: '確認', exact: true }).count(), 1);
  assert.equal(state.posts.length, 0, '填寫資料與確認前不得送出交易');
}

function assertImmediateSale(body) {
  assert.match(body.client_request_id, uuidPattern);
  assert.equal(body.invoice_handling, 'on_payment');
  assert.equal(Object.hasOwn(body, 'pickup_date'), false);
  assert.equal(body.confirm_received, true);
  assert.equal(body.confirm_not_invoiced_elsewhere, true);
}

async function assertClean(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不能橫向溢出');
}

async function capture(page, name) {
  if (!process.env.UI_ARTIFACT_DIR) return;
  await mkdir(process.env.UI_ARTIFACT_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.UI_ARTIFACT_DIR, `${name}.png`), fullPage: true, animations: 'disabled' });
}

async function refocus(page, at) {
  await page.clock.setFixedTime(new Date(at));
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    window.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    window.dispatchEvent(new Event('visibilitychange'));
  });
}

test('手機手動開票直接填表後確認，固定即時開票且不帶入店員載具與 Email', async t => {
  const { page, region, state } = await fixture(t, { width: 390 });
  const recent = page.locator('details.admin-manual-invoice-recent');
  await recent.locator('summary').click();
  await recent.getByText('尚無手動開票紀錄', { exact: true }).waitFor();
  assert.equal(await region.getByLabel('發票通知 Email', { exact: true }).inputValue(), '');
  assert.equal(await region.getByRole('checkbox', { name: '結帳時儲存此手機條碼供下次使用' }).count(), 0);
  await fillSale(region, { barcode: true });
  await assertDirectForm(page, region, state);
  await assertClean(page, state);
  await capture(page, 'manual-invoice-390-filled');
  await region.getByRole('button', { name: '確認', exact: true }).click();
  await assertRecorded(region);
  await recent.getByRole('link', { name: 'O2030010201', exact: true }).waitFor();
  assert.equal(state.posts.length, 1);
  const body = state.posts[0].body;
  assertImmediateSale(body);
  assert.deepEqual(body, {
    client_request_id: body.client_request_id, product_name: '合成餐盒', sale_category: 'meal', unit_price: 120, quantity: 2,
    tax_type: 'taxable', contact_email: 'customer@example.test',
    invoice_handling: 'on_payment',
    preferences: { invoice_buyer_type: 'personal', invoice_buyer_email: 'customer@example.test', invoice_carrier_type: 'mobile_barcode', invoice_carrier_value: customerBarcode },
    reason: '現場客人收款', confirm_received: true, confirm_not_invoiced_elsewhere: true,
  });
  assert.equal(state.posts[0].raw.includes(staffBarcode), false);
  await assertClean(page, state);
});

test('現場收款後更新原票狀態，只讀同一筆交易', async t => {
  const { page, region, state } = await fixture(t);
  await fillSale(region, { barcode: true });
  await region.getByRole('button', { name: '確認', exact: true }).click();
  await assertRecorded(region);
  const requestId = state.posts[0].body.client_request_id;
  const stored = state.sales.get(requestId).sale;
  Object.assign(stored, { invoice_status: 'issued', invoice_number: 'AB12345678' });

  const lookup = 'GET /v1/admin/walk-in-sales/isolated-walk-in-order';
  assert.equal(state.adminRequests.includes(lookup), false);
  await region.getByRole('button', { name: '更新發票狀態', exact: true }).click();
  await region.getByText('已開立 · AB12345678', { exact: true }).waitFor();
  const href = await region.getByRole('link', { name: '查看交易與發票狀態', exact: true }).getAttribute('href');
  assert.equal(new URL(href, base).searchParams.get('order_id'), 'isolated-walk-in-order');
  assert.equal(state.adminRequests.filter(entry => entry === lookup).length, 1);
  assert.equal(state.posts.length, 1);
  await assertClean(page, state);
});

for (const width of [390, 1280]) {
  for (const buyerType of ['personal', 'company']) {
    test(`${width}px ${buyerType} 手機載具無 Email 可確認，下一筆清空客人資料`, async t => {
      const { page, region, state } = await fixture(t, { width });
      assert.equal(await region.getByRole('radio', { name: '手機條碼載具', exact: true }).isChecked(), true);
      await fillSale(region, { barcode: true });
      await region.getByLabel('發票通知 Email', { exact: true }).fill('');
      if (buyerType === 'company') {
        await region.getByRole('radio', { name: '公司統編發票', exact: true }).click();
        await region.getByLabel('統一編號', { exact: true }).fill('15989995');
        assert.equal(await region.getByLabel('公司名稱', { exact: true }).evaluate(input => input.required), false);
      }
      assert.equal(await region.getByLabel('發票通知 Email', { exact: true }).evaluate(input => input.required), false);
      await assertClean(page, state);
      await capture(page, `manual-invoice-${width}-${buyerType}-mobile`);
      await region.getByRole('button', { name: '確認', exact: true }).click();
      await assertRecorded(region);
      const body = state.posts[0].body;
      assert.equal(body.contact_email, null);
      assert.equal(body.preferences.invoice_buyer_email, null);
      assert.equal(body.preferences.invoice_buyer_type, buyerType);
      assert.equal(body.preferences.invoice_carrier_value, customerBarcode);
      assert.equal(body.sale_category, 'meal');
      assert.equal(state.posts[0].raw.includes('staff@example.test'), false);
      assert.equal(state.posts[0].raw.includes(staffBarcode), false);
      if (buyerType === 'company') assert.equal(body.preferences.invoice_buyer_tax_id, '15989995');
      await region.getByRole('button', { name: '下一筆現場交易', exact: true }).click();
      await region.getByLabel('手機條碼', { exact: true }).waitFor();
      assert.equal(await region.getByLabel('手機條碼', { exact: true }).inputValue(), '');
      assert.equal(await region.getByLabel('發票通知 Email', { exact: true }).inputValue(), '');
      assert.equal(await region.getByRole('radio', { name: '個人電子發票', exact: true }).getAttribute('aria-checked'), 'true');
      assert.equal(await region.getByRole('button', { name: '確認', exact: true }).isDisabled(), true);
      assert.equal(state.posts.length, 1);
      await assertClean(page, state);
    });
  }
}

test('手機載具須有效條碼，選填 Email 仍驗格式，Email 會員載具仍必填 Email', async t => {
  const { page, region, state } = await fixture(t);
  assert.equal(await region.locator('.invoice-field-error').count(), 0, '初開空白不可報錯');
  await fillSale(region, { barcode: true });
  const confirm = region.getByRole('button', { name: '確認', exact: true });
  const email = region.getByLabel('發票通知 Email', { exact: true });
  await email.fill('');
  await region.getByLabel('手機條碼', { exact: true }).fill('/BAD');
  await region.getByLabel('手機條碼', { exact: true }).press('Tab');
  await region.getByText('請輸入以 / 開頭的 8 碼手機條碼', { exact: true }).waitFor();
  assert.equal(await region.getByLabel('手機條碼', { exact: true }).getAttribute('aria-invalid'), 'true');
  assert.equal(await region.getByLabel('手機條碼', { exact: true }).getAttribute('aria-describedby'), 'walk-in-barcode-error');
  assert.equal(await confirm.isDisabled(), true);
  await region.getByLabel('手機條碼', { exact: true }).fill(customerBarcode);
  await email.fill('invalid');
  await email.press('Tab');
  await region.getByText('請輸入正確的 Email 格式', { exact: true }).waitFor();
  assert.equal(await email.getAttribute('aria-invalid'), 'true');
  assert.equal(await confirm.isDisabled(), true);
  await email.fill('');
  assert.equal(await region.locator('#walk-in-email-error').count(), 0);
  assert.equal(await region.locator('#walk-in-barcode-error').count(), 0);
  assert.equal(await confirm.isEnabled(), true);
  await region.getByRole('radio', { name: 'Email 會員載具', exact: true }).check();
  assert.equal(await confirm.isDisabled(), true);
  assert.equal(await email.evaluate(input => input.required), true);
  await email.fill('customer@example.test');
  assert.equal(await confirm.isEnabled(), true);
  await region.getByRole('radio', { name: '公司統編發票', exact: true }).click();
  assert.equal(await confirm.isDisabled(), true);
  await region.getByLabel('統一編號', { exact: true }).fill('11111111');
  await region.getByLabel('統一編號', { exact: true }).press('Tab');
  await region.getByText('請輸入有效的 8 碼統一編號', { exact: true }).waitFor();
  assert.equal(await region.getByLabel('統一編號', { exact: true }).getAttribute('aria-invalid'), 'true');
  await region.getByLabel('公司名稱', { exact: true }).fill('合成公司');
  assert.equal(await confirm.isDisabled(), true);
  await region.getByLabel('統一編號', { exact: true }).fill('15989995');
  assert.equal(await region.locator('#walk-in-tax-id-error').count(), 0);
  assert.equal(await confirm.isEnabled(), true);
  assert.equal(state.posts.length, 0);
  await assertClean(page, state);
});

test('無 Email 手機載具交易回應不明仍鎖定資料，以原 payload 重試一次', async t => {
  const { page, region, state } = await fixture(t, { firstResponse: '503' });
  await fillSale(region, { barcode: true });
  await region.getByLabel('發票通知 Email', { exact: true }).fill('');
  await region.getByRole('button', { name: '確認', exact: true }).click();
  const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
  await retry.waitFor();
  assert.equal(await region.getByLabel('手機條碼', { exact: true }).isDisabled(), true);
  assert.equal(await region.getByRole('radio', { name: 'Email 會員載具', exact: true }).isDisabled(), true);
  await retry.click();
  await assertRecorded(region);
  assert.equal(state.posts[0].raw, state.posts[1].raw);
  assert.equal(state.posts[0].body.preferences.invoice_buyer_email, null);
  assert.equal(state.sales.size, 1);
  await assertClean(page, state);
});

for (const firstResponse of ['abort', '503']) {
  test(`現場收款 ${firstResponse} 後以相同 UUID 與完整原文重試`, async t => {
    const { page, region, state } = await fixture(t, { firstResponse });
    await fillSale(region);
    await assertDirectForm(page, region, state);
    await region.getByRole('button', { name: '確認', exact: true }).click();
    const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
    await retry.waitFor();
    await region.getByRole('alert').waitFor();
    assert.equal(await region.getByLabel('含稅單價', { exact: true }).isDisabled(), true);
    assert.equal(await region.getByLabel('發票通知 Email', { exact: true }).isDisabled(), true);
    assert.equal(state.posts.length, 1);
    assertImmediateSale(state.posts[0].body);
    await retry.click();
    await assertRecorded(region);
    assert.equal(state.posts.length, 2);
    assert.equal(state.posts[1].raw, state.posts[0].raw);
    assert.equal(state.posts[1].body.client_request_id, state.posts[0].body.client_request_id);
    assert.equal(state.sales.size, 1);
    await assertClean(page, state);
  });
}

test('收款回應中斷後重新整理，只用保存的 UUID 查回交易、不重送 POST', async t => {
  const { page, region, state } = await fixture(t, { firstResponse: 'abort', width: 390 });
  await fillSale(region, { barcode: true });
  await assertDirectForm(page, region, state);
  await region.getByRole('button', { name: '確認', exact: true }).click();
  await region.getByRole('button', { name: '重試同一筆交易', exact: true }).waitFor();
  const requestId = state.posts[0].body.client_request_id;
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), requestKey), requestId);
  const storage = await page.evaluate(() => JSON.stringify(Object.fromEntries(Object.entries(sessionStorage))));
  assert.equal(storage.includes('customer@example.test'), false);
  assert.equal(storage.includes(customerBarcode), false);
  await page.reload();
  await assertRecorded(region);
  assert.equal(state.posts.length, 1);
  assert.ok(state.recoveries.filter(id => id === requestId).length >= 2);
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), requestKey), requestId);
  assertImmediateSale(state.posts[0].body);
  assert.equal(state.adminRequests.includes('GET /v1/admin/order-search'), false);
  await assertClean(page, state);
});

for (const action of ['查核', '重送']) {
  test(`交易結果不明時先${action}，等回應完成再開下一筆表單`, async t => {
    const { page, region, state } = await fixture(t, { firstResponse: '503', width: 390 });
    await fillSale(region, { barcode: true });
    await region.getByRole('button', { name: '確認', exact: true }).click();
    const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
    await retry.waitFor();
    const check = region.getByRole('button', { name: '查核交易結果', exact: true });
    const firstId = state.posts[0].body.client_request_id;
    let release;
    const hold = new Promise(resolve => { release = resolve; });
    try {
      if (action === '查核') {
        state.holdRecovery = hold;
        await check.click();
        const checking = region.getByRole('button', { name: '查核中…', exact: true });
        await checking.waitFor();
        assert.equal(await checking.isDisabled(), true);
        assert.equal(await retry.isDisabled(), true);
        await retry.evaluate(button => button.form.requestSubmit());
        assert.equal(state.posts.length, 1);
      } else {
        state.holdRetry = hold;
        const recoveryCount = state.recoveries.length;
        await retry.click();
        await region.getByRole('button', { name: '記錄中…', exact: true }).waitFor();
        assert.equal(await check.isDisabled(), true);
        await check.evaluate(button => button.click());
        await refocus(page, '2030-01-02T10:01:01+08:00');
        assert.equal(state.recoveries.length, recoveryCount);
        assert.equal(state.posts.length, 2);
        assert.equal(state.posts[0].raw, state.posts[1].raw);
      }
      assert.equal(await region.getByRole('button', { name: '下一筆現場交易', exact: true }).count(), 0);
    } finally {
      release();
    }
    await assertRecorded(region);
    await region.getByRole('button', { name: '下一筆現場交易', exact: true }).click();
    const name = region.getByLabel('品名', { exact: true });
    await name.fill('新一筆的餐點');
    assert.equal(await name.inputValue(), '新一筆的餐點');
    assert.equal(await region.locator('.admin-walk-in-result').count(), 0);
    assert.notEqual(await page.evaluate(key => sessionStorage.getItem(key), requestKey), firstId);
    assert.equal(state.sales.size, 1);
    await assertClean(page, state);
  });
}

for (const width of [390, 1280]) {
  test(`${width}px 訂單頁導航到手動開票再返回訂單，沒有內嵌表單且不建立交易`, async t => {
    const { page, region, state } = await fixture(t, { width, section: 'orders' });
    await page.getByText('沒有符合條件的訂單。', { exact: true }).waitFor();
    assert.equal(await region.count(), 0);
    assert.equal(await page.getByLabel('含稅單價', { exact: true }).count(), 0);
    await capture(page, `orders-${width}`);
    const navigation = page.getByRole('navigation', { name: '訂單營運子頁面', exact: true });
    const manualInvoice = navigation.getByRole('link', { name: '手動開票', exact: true });
    assert.equal(new URL(await manualInvoice.getAttribute('href'), base).searchParams.get('section'), 'manual-invoice');
    const orderReads = state.adminRequests.filter(path => path === 'GET /v1/admin/order-search').length;
    await manualInvoice.click();
    await page.getByRole('heading', { name: '手動開票', level: 1, exact: true }).waitFor();
    await region.getByLabel('含稅單價', { exact: true }).waitFor();
    assert.equal(await manualInvoice.getAttribute('aria-current'), 'page');
    assert.equal(await page.getByLabel('搜尋訂單', { exact: true }).count(), 0);
    assert.equal(state.adminRequests.filter(path => path === 'GET /v1/admin/order-search').length, orderReads);
    await fillSale(region);
    assert.equal(state.posts.length, 0);
    await assertClean(page, state);
    await capture(page, `manual-invoice-${width}-filled`);
    await navigation.getByRole('link', { name: '訂單、付款與發票', exact: true }).click();
    await page.getByLabel('搜尋訂單', { exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('section'), 'orders');
    assert.equal(await region.count(), 0);
    assert.equal(await page.getByLabel('含稅單價', { exact: true }).count(), 0);
    assert.equal(state.posts.length, 0);
    await assertClean(page, state);
  });
}

test('確認處理中停用送出，重複點擊及表單提交只建立一筆現場交易', async t => {
  const { page, region, state } = await fixture(t, { firstResponse: 'hold' });
  await fillSale(region, { barcode: true });
  await assertDirectForm(page, region, state);
  await region.getByRole('button', { name: '確認', exact: true }).click();
  const pending = region.getByRole('button', { name: '記錄中…', exact: true });
  await pending.waitFor();
  assert.equal(await pending.isDisabled(), true);
  assert.equal(await region.getByRole('textbox', { name: '品名', exact: true }).isDisabled(), true);
  assert.equal(await region.getByLabel('含稅單價', { exact: true }).isDisabled(), true);
  await pending.evaluate(button => { button.click(); button.form.requestSubmit(); });
  assert.equal(state.posts.length, 1);
  assertImmediateSale(state.posts[0].body);
  assert.equal(state.posts[0].raw.includes(staffBarcode), false);
  state.releasePost();
  await assertRecorded(region);
  assert.equal(state.posts.length, 1);
  assert.equal(state.sales.size, 1);
  await assertClean(page, state);
});

for (const role of ['guest', 'customer']) {
  test(`${role === 'guest' ? '訪客' : '非管理員'}直接開啟手動開票不顯示表單，也不請求管理 API`, async t => {
    const { page, region, state } = await fixture(t, { role, width: 390 });
    assert.equal(await region.count(), 0);
    assert.equal(await page.getByRole('link', { name: '手動開票', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '確認', exact: true }).count(), 0);
    assert.deepEqual(state.adminRequests, []);
    assert.deepEqual(state.posts, []);
    assert.deepEqual(state.recoveries, []);
    await assertClean(page, state);
  });
}

test('切換分類不會清掉手填品名，重試保留原交易', async t => {
  const { page, region, state } = await fixture(t, { firstResponse: '503' });
  await fillSale(region);
  const product = region.getByRole('textbox', { name: '品名', exact: true });
  await region.getByRole('button', { name: '一般商品', exact: true }).click();
  assert.equal(await product.inputValue(), '合成餐盒');
  assert.equal(state.posts.length, 0);
  await region.getByRole('button', { name: '確認', exact: true }).click();
  const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
  await retry.waitFor();
  assert.equal(await product.isDisabled(), true);
  await retry.click();
  await assertRecorded(region);
  assert.equal(state.posts.length, 2);
  assert.equal(state.posts[1].raw, state.posts[0].raw);
  assert.equal(state.posts[1].body.product_name, '合成餐盒');
  assert.equal(state.posts[1].body.sale_category, 'product');
  await assertClean(page, state);
});
