import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/') throw new Error('Only isolated local checkout is allowed');
const base = target.origin;
const product = { id: 'barcode-product', name: '載具測試白米', unit: '包', member_price: 300, nonmember_price: 300, stock_quantity: 20, is_active: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
const campaign = { id: 'barcode-group', title: '載具測試共同購買', description: '', member_price: 300, nonmember_price: 300, target_type: 'product', target_id: product.id, status: 'open', intake_status: 'open', decision_status: 'recruiting', deadline: '2030-01-04T00:00:00Z', estimated_pickup_start: '2030-01-05T00:00:00Z', estimated_pickup_end: '2030-01-06T00:00:00Z', paid_quantity: 0, min_paid_quantity: 1, supply_cap: 50, per_user_cap: 10, available_quantity: 50, allowed_shipping_channels: [], can_ship: false };
const buyer = (id, saved_mobile_barcode = null) => ({ id, email: `${id}@example.test`, display_name: id, user_role: 'customer', membership_type: 'nonmember', saved_mobile_barcode });

async function fixture(t, { saved = null, failCount = 0, failStatus = 503, holdFirst = false, width = 390 } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' });
  await context.addInitScript((item) => localStorage.setItem('slf_cart', JSON.stringify([{ product: item, quantity: 1 }])), product);
  const state = { user: buyer('buyer-a', saved), patches: [], owners: [], writes: [], unknown: [], errors: [], failCount, rejectNextAccess: false, refreshAs: null };
  let releaseFirst;
  const gate = new Promise((resolve) => { releaseFirst = resolve; });
  t.after(() => releaseFirst());
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-barcode-payment') return route.fulfill({ contentType: 'text/html', body: '<h1>隔離付款</h1>' });
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') {
      if (state.refreshAs) state.user = state.refreshAs;
      return route.fulfill({ json: { access_token: `isolated-${state.user.id}`, user: state.user } });
    }
    if (path === '/v1/auth/preferences' && request.method() === 'PATCH') {
      const body = request.postDataJSON();
      state.patches.push(body);
      state.owners.push(request.headers()['x-expected-user-id']);
      if (state.rejectNextAccess) {
        state.rejectNextAccess = false;
        return route.fulfill({ status: 401, json: { detail: '登入過期' } });
      }
      if (request.headers()['x-expected-user-id'] !== state.user.id) {
        return route.fulfill({ status: 409, json: { detail: '登入帳號已變更，請重新整理後再試' } });
      }
      if (holdFirst && state.patches.length === 1) await gate;
      if (state.failCount > 0) {
        state.failCount--;
        return route.fulfill({ status: failStatus, json: { detail: failStatus === 422 ? '查無此手機條碼' : '手機條碼驗證服務目前無法使用' } });
      }
      state.user = { ...state.user, saved_mobile_barcode: body.saved_mobile_barcode };
      return route.fulfill({ json: state.user });
    }
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/orders' && request.method() === 'GET') return route.fulfill({ json: [] });
    if (path === '/v1/shipping-rates') return route.fulfill({ json: [] });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'pickup', name: '合作社門市', address: '測試門市', is_active: true }] });
    if (path === '/v1/group-campaigns/barcode-group') return route.fulfill({ json: campaign });
    if (path.endsWith('/quote')) return route.fulfill({ json: { amount_total: 300, membership_type: 'nonmember', quantity: 1, unit_price: 300, product_subtotal: 300, shipping_fee: 0, items: [{ product_id: product.id, product_name: product.name, quantity: 1, unit_price: 300, subtotal: 300, tax_type: 'tax_exempt' }] } });
    if (['/v1/orders', '/v1/group-campaigns/barcode-group/join'].includes(path) && request.method() === 'POST') {
      state.writes.push({ path, body: request.postDataJSON() });
      return route.fulfill({ status: 201, json: { id: 'barcode-order', order_number: 'SLF-BARCODE-TEST', payment_status: 'pending', amount_total: 300 } });
    }
    if (path === '/v1/orders/barcode-order/payment-attempts') {
      state.writes.push({ path });
      return route.fulfill({ status: 201, json: { id: 'barcode-payment', status: 'pending', payment_url: `${base}/isolated-barcode-payment` } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.goto(`${base}/checkout`);
  await page.locator('.summary-total dd').getByText('$300', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('.checkout-fulfillment select[required]')?.value === 'pickup');
  const barcodeInput = () => page.getByRole('textbox', { name: '手機條碼', exact: true });
  const pay = page.getByRole('button', { name: '前往線上付款', exact: true });
  t.after(() => {
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });
  return { page, state, barcodeInput, pay, releaseFirst };
}

async function chooseBarcode(page) {
  await page.getByRole('radio', { name: '手機條碼載具', exact: true }).check();
}

const storageOption = (page) => page.getByRole('checkbox', { name: '結帳時儲存此手機條碼供下次使用', exact: true });

async function completePayment(page, pay) {
  await pay.click();
  await page.waitForURL(`${base}/isolated-barcode-payment`);
}

test('輸入與切換發票身分不儲存，未勾選結帳仍用本次條碼而不改帳號', async (t) => {
  const { page, state, barcodeInput, pay } = await fixture(t);
  await chooseBarcode(page);
  await barcodeInput().fill('/ab12+-.');
  assert.equal(await storageOption(page).isChecked(), false);
  assert.equal(state.patches.length, 0);
  await page.getByRole('radio', { name: '公司統編發票', exact: true }).click();
  assert.equal(await barcodeInput().inputValue(), '/AB12+-.');
  await page.getByRole('radio', { name: '個人電子發票', exact: true }).click();
  assert.equal(await storageOption(page).isChecked(), false);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await completePayment(page, pay);
  assert.deepEqual(state.patches, []);
  assert.equal(state.user.saved_mobile_barcode, null);
  assert.equal(state.writes[0].body.invoice_carrier_value, '/AB12+-.');
  assert.equal('saveCarrier' in state.writes[0].body, false);
});

test('勾選後取消不儲存新條碼，也不刪除原載具', async (t) => {
  const { page, state, barcodeInput, pay } = await fixture(t, { saved: '/OLD1234' });
  await barcodeInput().fill('/NEW1234');
  await storageOption(page).check();
  await storageOption(page).uncheck();
  await completePayment(page, pay);
  assert.deepEqual(state.patches, []);
  assert.equal(state.user.saved_mobile_barcode, '/OLD1234');
  assert.equal(state.writes[0].body.invoice_carrier_value, '/NEW1234');
});

test('勾選後送出才保存，付款等待保存完成且訂單使用同一條碼', async (t) => {
  const { page, state, barcodeInput, pay, releaseFirst } = await fixture(t, { holdFirst: true });
  await chooseBarcode(page);
  await barcodeInput().fill('/NOW1234');
  await storageOption(page).check();
  await page.getByRole('radio', { name: '公司統編發票', exact: true }).click();
  await page.getByRole('radio', { name: '個人電子發票', exact: true }).click();
  assert.equal(await storageOption(page).isChecked(), true);
  assert.equal(state.patches.length, 0);
  const firstRequest = page.waitForRequest((request) => request.url().endsWith('/v1/auth/preferences'));
  await pay.click();
  await firstRequest;
  const preparing = page.getByRole('button', { name: '準備結帳中…', exact: true });
  await preparing.waitFor();
  assert.equal(await preparing.isEnabled(), false);
  assert.deepEqual(state.writes, []);
  releaseFirst();
  await page.waitForURL(`${base}/isolated-barcode-payment`);
  assert.equal(state.patches.length, 1);
  assert.equal(state.user.saved_mobile_barcode, '/NOW1234');
  assert.equal(state.writes[0].body.invoice_carrier_value, '/NOW1234');
  assert.deepEqual(state.owners, ['buyer-a']);
  assert.deepEqual(state.writes.map(({ path }) => path), ['/v1/orders', '/v1/orders/barcode-order/payment-attempts']);
});

test('無效或未填完的格式不寫入帳號且不能付款', async (t) => {
  const { page, state, barcodeInput, pay } = await fixture(t);
  await chooseBarcode(page);
  await storageOption(page).check();
  for (const value of ['/SHORT', '/ABC123?', '']) {
    await barcodeInput().fill(value);
    await page.getByRole('textbox', { name: '發票通知 Email', exact: true }).focus();
    assert.equal(await pay.isEnabled(), false);
    assert.equal(state.patches.length, 0);
  }
  assert.equal(state.user.saved_mobile_barcode, null);
});

for (const failStatus of [422, 503]) test(`勾選保存 ${failStatus} 時不建立訂單，重新送出可成功`, async (t) => {
  const { page, state, barcodeInput, pay } = await fixture(t, { saved: '/OLD1234', failCount: 1, failStatus });
  await barcodeInput().fill('/NEW1234');
  await storageOption(page).check();
  await pay.click();
  await page.locator('.order-summary .form-error').getByText(failStatus === 422 ? '查無此手機條碼' : '手機條碼驗證服務目前無法使用', { exact: true }).waitFor();
  assert.equal(state.user.saved_mobile_barcode, '/OLD1234');
  assert.deepEqual(state.writes, []);
  await completePayment(page, pay);
  assert.equal(state.patches.length, 2);
  assert.equal(state.user.saved_mobile_barcode, '/NEW1234');
});

test('切換 Email 載具後不儲存隱藏手機條碼', async (t) => {
  const { page, state, barcodeInput, pay } = await fixture(t, { saved: '/OLD1234' });
  await barcodeInput().fill('/NEW1234');
  await storageOption(page).check();
  await page.getByRole('radio', { name: 'Email 會員載具', exact: true }).check();
  await completePayment(page, pay);
  assert.deepEqual(state.patches, []);
  assert.equal(state.user.saved_mobile_barcode, '/OLD1234');
  assert.equal(state.writes[0].body.invoice_carrier_type, 'cloud');
  assert.equal('invoice_carrier_value' in state.writes[0].body, false);
});

test('已存載具預帶，未勾選不清除；明確移除保留本次輸入，換帳號只帶新帳號資料', async (t) => {
  const { page, state, barcodeInput } = await fixture(t, { saved: '/AAA1234' });
  assert.equal(await barcodeInput().inputValue(), '/AAA1234');
  assert.equal(await storageOption(page).isChecked(), false);
  await page.getByRole('button', { name: '移除已儲存載具', exact: true }).click();
  await page.getByText('已移除儲存載具', { exact: true }).waitFor();
  assert.equal(state.user.saved_mobile_barcode, null);
  assert.equal(await barcodeInput().inputValue(), '/AAA1234');
  assert.equal(await storageOption(page).isChecked(), false);
  await page.reload();
  await page.getByRole('radio', { name: 'Email 會員載具', exact: true }).waitFor();
  assert.equal(await page.getByRole('radio', { name: 'Email 會員載具', exact: true }).isChecked(), true);
  state.user = buyer('buyer-b', '/BBB1234');
  await page.reload();
  await barcodeInput().waitFor();
  assert.equal(await barcodeInput().inputValue(), '/BBB1234');
  assert.equal(await storageOption(page).isChecked(), false);
  assert.equal(await page.getByRole('textbox', { name: '發票通知 Email', exact: true }).inputValue(), 'buyer-b@example.test');
});

test('舊分頁過期後 refresh 到另一帳號，保存仍綁原帳號且拒絕改寫他人載具', async (t) => {
  const { page, state, barcodeInput, pay } = await fixture(t, { saved: '/AAA1234' });
  state.rejectNextAccess = true;
  state.refreshAs = buyer('buyer-b', '/BBB1234');
  await barcodeInput().fill('/NEW1234');
  await storageOption(page).check();
  await pay.click();
  await page.waitForFunction(() => document.querySelector('a[aria-label="會員中心：buyer-b"]'));
  await barcodeInput().waitFor();
  assert.deepEqual(state.owners, ['buyer-a']);
  assert.equal(state.user.id, 'buyer-b');
  assert.equal(state.user.saved_mobile_barcode, '/BBB1234');
  assert.equal(await barcodeInput().inputValue(), '/BBB1234');
  assert.equal(await page.getByRole('textbox', { name: '發票通知 Email', exact: true }).inputValue(), 'buyer-b@example.test');
  assert.equal(await storageOption(page).isChecked(), false);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), [{ product, quantity: 1 }]);
  assert.deepEqual(state.writes, []);
});

for (const save of [false, true]) test(`共同購買同樣尊重儲存選擇：${save}`, async (t) => {
  const { page, state, barcodeInput } = await fixture(t, { saved: '/OLD1234', width: 1280 });
  await page.goto(`${base}/groups/barcode-group`);
  await barcodeInput().waitFor();
  assert.equal(await barcodeInput().inputValue(), '/OLD1234');
  await barcodeInput().fill('/GRP1234');
  assert.equal(await storageOption(page).isChecked(), false);
  if (save) await storageOption(page).check();
  await completePayment(page, page.getByRole('button', { name: '參加並前往付款', exact: true }));
  assert.equal(state.patches.length, save ? 1 : 0);
  assert.equal(state.user.saved_mobile_barcode, save ? '/GRP1234' : '/OLD1234');
  assert.equal(state.writes[0].path, '/v1/group-campaigns/barcode-group/join');
  assert.equal(state.writes[0].body.invoice_carrier_value, '/GRP1234');
});
