import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import test from 'node:test';
const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4261';
const root = resolve(process.env.BROWSER_DIST_ROOT || resolve(import.meta.dirname, '../../web/dist'));
const campaign = { id: 'recovery-group', title: '恢復測試共同購買', description: '', target_type: 'product', target_id: 'group-product', member_price: 100, nonmember_price: 100, min_paid_quantity: 1, paid_quantity: 0, reserved_quantity: 0, supply_cap: 20, per_user_cap: 10, available_quantity: 20, intake_status: 'open', decision_status: 'recruiting', deadline: '2030-01-02T00:00:00Z', estimated_pickup_start: '2030-01-03T00:00:00Z', estimated_pickup_end: '2030-01-04T00:00:00Z', can_ship: false, allowed_shipping_channels: [] };
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg' };
async function fixture(t, { loseResponse = false, blockedStorage = false, loseBeforeCreate = false, rejection = null, missingFingerprint = false, shipping = false } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: Number(process.env.BROWSER_WIDTH || 390), height: 1000 }, serviceWorkers: 'block' });
  await context.addInitScript(blocked => {
    if (!localStorage.getItem('slf_cart')) localStorage.setItem('slf_cart', JSON.stringify([{ product: { id: 'other-cart-product', name: '原購物車商品' }, quantity: 3 }]));
    window.__groupStorageBlocked = blocked;
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (this === sessionStorage && key.startsWith('slf_group_submission:') && window.__groupStorageBlocked) throw new DOMException('Quota', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  }, blockedStorage);
  const state = { loseResponse, loseBeforeCreate, rejection, price: 100, fingerprint: missingFingerprint ? undefined : 'a'.repeat(64), userId: 'group-buyer', open: true, campaignError: false, stale: false, uncertain: false, creates: [], orders: new Map(), payments: [], logistics: [], errors: [], unknown: [] };
  const quoteFingerprint = body => state.fingerprint === undefined ? undefined : body.fulfillment_method === 'ecpay_logistics' ? (body.invoice_buyer_type === 'company' ? 'd'.repeat(64) : 'c'.repeat(64)) : state.fingerprint;
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-group-payment') return route.fulfill({ contentType: 'text/html', body: '<h1>隔離共同購買付款</h1>' });
    if (path === '/isolated-group-logistics') return route.fulfill({ contentType: 'text/html', body: '<h1>隔離共同購買物流</h1>' });
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: types[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-group', user: { id: state.userId, email: 'buyer@example.com', display_name: state.userId, membership_type: 'nonmember', user_role: 'customer' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: state.open, closed_message: '暫停接單' } });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'group-pickup', name: '合作社取貨點', is_active: true }] });
    if (path === '/v1/group-campaigns/recovery-group') return state.campaignError ? route.fulfill({ status: 404, json: { detail: '共同購買暫時無法讀取' } }) : route.fulfill({ json: { ...campaign, ...(shipping ? { can_ship: true, shipping_temperature: 'ambient', allowed_shipping_channels: ['home_delivery'] } : {}), available_quantity: state.open ? 20 : 0, intake_status: state.open ? 'open' : 'closed' } });
    if (path === '/v1/group-campaigns/recovery-group/quote') { const body = request.postDataJSON(), quantity = body.quantity, fee = body.fulfillment_method === 'ecpay_logistics' ? 60 : 0; return route.fulfill({ json: { confirmation_fingerprint: quoteFingerprint(body), membership_type: 'nonmember', quantity, unit_price: state.price, product_subtotal: state.price * quantity, shipping_fee: fee, amount_total: state.price * quantity + fee, tax_type: 'taxable', tax_amount: fee ? 8 : 5 } }); }
    if (path === '/v1/group-campaigns/recovery-group/join') {
      const body = request.postDataJSON(); state.creates.push(body);
      if (state.rejection) { const problem = state.rejection; state.rejection = null; return route.fulfill({ status: problem.status, ...(problem.code ? { headers: { 'X-Error-Code': problem.code } } : {}), json: { detail: problem.message } }); }
      if (state.loseBeforeCreate) { state.loseBeforeCreate = false; return route.abort(); }
      const key = body.client_request_id || `missing-${state.creates.length}`;
      let order = state.orders.get(key);
      if (!order) {
        if (!state.open) return route.fulfill({ status: 409, json: { detail: '已停止收單' } });
        const fee = body.fulfillment_method === 'ecpay_logistics' ? 60 : 0;
        if (body.expected_total !== undefined && body.expected_total !== state.price * body.quantity + fee || body.expected_quote_fingerprint && body.expected_quote_fingerprint !== quoteFingerprint(body)) return route.fulfill({ status: 409, headers: { 'X-Error-Code': 'quote_changed' }, json: { detail: '共同購買報價已更新，請重新確認後再送單' } });
        order = { id: `group-order-${state.orders.size + 1}`, order_number: 'GROUP-RECOVERY', amount_total: state.price * body.quantity, payment_status: 'pending', revision: 1, group_contract_changed: false };
        state.orders.set(key, order);
      }
      if (state.loseResponse) { state.loseResponse = false; return route.abort(); }
      return route.fulfill({ status: 201, json: { ...order, group_contract_changed: state.stale, group_payment_uncertain: state.uncertain } });
    }
    if (/^\/v1\/orders\/group-order-\d+\/payment-attempts$/.test(path)) { state.payments.push(path); return route.fulfill({ status: 201, json: { id: 'isolated-attempt', payment_url: `${base}/isolated-group-payment` } }); }
    if (/^\/v1\/orders\/group-order-\d+\/logistics\/selection$/.test(path)) { state.logistics.push({ path, body: request.postDataJSON() }); return route.fulfill({ status: 201, json: { selection_url: `${base}/isolated-group-logistics` } }); }
    if (path === '/v1/orders') return route.fulfill({ json: [] });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(5000);
  page.on('pageerror', error => state.errors.push(error.message));
  t.after(() => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); });
  await page.goto(`${base}/groups/recovery-group`);
  await page.getByRole('heading', { name: campaign.title, exact: true }).waitFor();
  const submit = () => page.getByRole('button', { name: /^(參加並前往付款|參加並選擇物流|重試原共同購買訂單)$/ });
  await submit().waitFor();
  await page.locator('.offer-total').getByText('$100', { exact: true }).waitFor();
  await page.getByText('本次單價', { exact: true }).waitFor();
  assert.equal(await page.getByText('社員單價', { exact: true }).count(), 0);
  return { page, state, submit };
}

test('共同購買建單回應遺失後重整，以同UUID同body恢復一單並正常付款', async t => {
  const { page, state, submit } = await fixture(t, { loseResponse: true });
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.locator('.offer-total').getByText('$200', { exact: true }).waitFor();
  await submit().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  await page.reload();
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates.length, 2);
  assert.match(state.creates[0].client_request_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(state.creates[1], state.creates[0]);
  assert.equal(state.creates[0].expected_total, 200);
  assert.equal(state.creates[0].expected_quote_fingerprint, 'a'.repeat(64));
  assert.deepEqual(state.payments, ['/v1/orders/group-order-1/payment-attempts']);
});

test('共同購買舊報價被明確拒絕後先顯示新價，需再次確認才以新UUID送單', async t => {
  const { page, state, submit } = await fixture(t);
  state.price = 120; state.fingerprint = 'b'.repeat(64);
  await submit().click();
  await page.getByText('共同購買報價已更新，請重新確認後再送單', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0);
  assert.equal(state.payments.length, 0);
  await page.locator('.offer-total').getByText('$120', { exact: true }).waitFor();
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.orders.size, 1);
  assert.notEqual(state.creates[1].client_request_id, state.creates[0].client_request_id);
});

for (const hidden of [false, true]) test(`已接受的未知原單在關店/關團/售罄${hidden ? '/campaign404' : ''}後仍能同key恢復，不清購物車`, async t => {
  const { page, state, submit } = await fixture(t, { loseResponse: true });
  await submit().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  state.open = false; state.campaignError = hidden;
  await page.reload();
  await page.getByRole('heading', { name: '確認原共同購買訂單結果', exact: true }).waitFor();
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.orders.size, 1);
  assert.deepEqual(state.creates[1], state.creates[0]);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), [{ product: { id: 'other-cart-product', name: '原購物車商品' }, quantity: 3 }]);
});

test('同總額但campaign契約fingerprint變動也須重新確認新key', async t => {
  const { page, state, submit } = await fixture(t);
  state.fingerprint = 'b'.repeat(64);
  await submit().click();
  await page.getByText('共同購買報價已更新，請重新確認後再送單', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0); assert.equal(state.payments.length, 0);
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.orders.size, 1);
  assert.equal(state.creates[0].expected_total, state.creates[1].expected_total);
  assert.notEqual(state.creates[0].expected_quote_fingerprint, state.creates[1].expected_quote_fingerprint);
  assert.notEqual(state.creates[0].client_request_id, state.creates[1].client_request_id);
});

test('quota未保存識別時零POST，原摘要凍結，允許storage後正常同資料送單', async t => {
  const { page, state, submit } = await fixture(t, { blockedStorage: true });
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.locator('.offer-total').getByText('$200', { exact: true }).waitFor();
  await submit().click();
  await page.getByText('瀏覽器無法儲存共同購買送單識別，請允許網站儲存資料後重試', { exact: true }).waitFor();
  assert.equal(state.creates.length, 0);
  assert.equal(await page.getByRole('button', { name: '增加數量', exact: true }).count(), 0);
  await page.locator('.group-recovery .offer-total').getByText('$200', { exact: true }).waitFor();
  await page.evaluate(() => { window.__groupStorageBlocked = false; });
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.creates.length, 1);
  assert.equal(state.creates[0].quantity, 2);
  assert.equal(state.creates[0].expected_total, 200);
  assert.match(state.creates[0].client_request_id, /^[0-9a-f-]{36}$/);
});

test('idempotency conflict保留原key原稿，不暗自建立新訂單', async t => {
  const problem = { status: 409, code: 'idempotency_conflict', message: '原送單識別衝突' };
  const { page, state, submit } = await fixture(t, { rejection: problem });
  await submit().click();
  await page.getByText(problem.message, { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '增加數量', exact: true }).count(), 0);
  state.rejection = problem;
  await submit().click();
  await page.getByText(problem.message, { exact: true }).waitFor();
  assert.equal(state.orders.size, 0); assert.equal(state.payments.length, 0);
  assert.deepEqual(state.creates[1], state.creates[0]);
});

test('先unknown未建單再收到權威quote_changed，可重新確認但不自動送新單', async t => {
  const { page, state, submit } = await fixture(t, { loseBeforeCreate: true });
  await submit().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  state.price = 120; state.fingerprint = 'b'.repeat(64);
  await submit().click();
  await page.getByText('共同購買報價已更新，請重新確認後再送單', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0); assert.equal(state.creates.length, 2);
  assert.equal(state.creates[1].client_request_id, state.creates[0].client_request_id);
  await page.locator('.offer-total').getByText('$120', { exact: true }).waitFor();
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.orders.size, 1);
  assert.notEqual(state.creates[2].client_request_id, state.creates[1].client_request_id);
});

for (const flag of ['stale', 'uncertain']) test(`已知原單${flag}回Orders確認，不再建立金流`, async t => {
  const { page, state, submit } = await fixture(t, { loseResponse: true });
  await submit().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  state[flag] = true;
  await submit().click();
  await page.waitForURL(url => url.origin === base && url.pathname === '/orders');
  assert.equal(state.orders.size, 1); assert.equal(state.payments.length, 0);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('slf_group_submission:group-buyer:recovery-group')), null);
});

for (const status of [409, 422]) test(`先unknown再plain${status}仍保原snapshot，不依錯誤文字換新key`, async t => {
  const { page, state, submit } = await fixture(t, { loseBeforeCreate: true });
  await submit().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  state.rejection = { status, message: '報價可能改變但沒有權威code' };
  await submit().click();
  await page.getByText('報價可能改變但沒有權威code', { exact: true }).waitFor();
  await page.getByRole('heading', { name: '確認原共同購買訂單結果', exact: true }).waitFor();
  assert.equal(state.orders.size, 0);
  assert.deepEqual(state.creates[1], state.creates[0]);
  assert.equal(await page.getByRole('button', { name: '增加數量', exact: true }).count(), 0);
});

test('首次明確422沒有建單時保留可修正表單，再確認才新key', async t => {
  const { page, state, submit } = await fixture(t, { rejection: { status: 422, message: '隔離發票資料需修正' } });
  await submit().click();
  await page.getByText('隔離發票資料需修正', { exact: true }).waitFor();
  assert.equal(state.orders.size, 0);
  await page.getByRole('button', { name: '增加數量', exact: true }).click();
  await page.locator('.offer-total').getByText('$200', { exact: true }).waitFor();
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.orders.size, 1);
  assert.notEqual(state.creates[1].client_request_id, state.creates[0].client_request_id);
});

test('缺少新quote fingerprint不送join，恢復報價後才能正常付款', async t => {
  const { page, state, submit } = await fixture(t, { missingFingerprint: true });
  await page.getByText('報價識別尚未確認，請重新取得報價', { exact: true }).waitFor();
  assert.equal(await submit().isDisabled(), true);
  assert.equal(state.creates.length, 0);
  state.fingerprint = 'a'.repeat(64);
  await page.getByRole('button', { name: '重新取得報價', exact: true }).click();
  await submit().click();
  await page.waitForURL(`${base}/isolated-group-payment`);
  assert.equal(state.creates.length, 1);
});

test('未知A單不在B登入後的共同購買表單顯示或自動重送，原購物車保留', async t => {
  const { page, state, submit } = await fixture(t, { loseResponse: true });
  await submit().click();
  await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
  state.userId = 'other-buyer';
  await page.reload();
  await page.locator('.account-button[aria-label="會員中心：other-buyer"]').waitFor({ state: 'attached' });
  await page.getByRole('button', { name: '增加數量', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '確認原共同購買訂單結果', exact: true }).count(), 0);
  assert.equal(state.creates.length, 1);
  assert.notEqual(await page.evaluate(() => sessionStorage.getItem('slf_group_submission:group-buyer:recovery-group')), null);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('slf_group_submission:other-buyer:recovery-group')), null);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 3);
});

async function fillShippingInvoice(page) {
  await page.getByRole('radio', { name: /宅配／超商取貨/ }).check();
  await page.getByRole('textbox', { name: '收件人姓名', exact: true }).fill('原始收件人');
  await page.getByRole('textbox', { name: '聯絡電話', exact: true }).fill('0900000000');
  await page.getByRole('textbox', { name: '配送地址', exact: true }).fill('原始配送地址一號');
  await page.getByRole('radio', { name: '公司統編發票', exact: true }).click();
  await page.getByRole('textbox', { name: '統一編號', exact: true }).fill('24536806');
  await page.getByRole('textbox', { name: '公司名稱', exact: true }).fill('原始發票公司');
  await page.getByRole('textbox', { name: '發票通知 Email', exact: true }).fill('original-invoice@example.com');
  await page.locator('.offer-total').getByText('$160', { exact: true }).waitFor();
}

if (process.env.BROWSER_LOGISTICS_TESTS === 'true') {
  for (const lose of [false, true]) test(`shipping ${lose ? 'unknown+重整' : '正常'}完整地址與公司發票凍結，quote含運160但初始order100仍能物流`, async t => {
    const { page, state, submit } = await fixture(t, { shipping: true, loseResponse: lose });
    await fillShippingInvoice(page);
    await submit().click();
    if (lose) {
      await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
      await page.reload();
      await page.getByRole('heading', { name: '確認原共同購買訂單結果', exact: true }).waitFor();
      await page.getByText('原始配送地址一號', { exact: true }).waitFor();
      await page.getByText('原始發票公司', { exact: true }).waitFor();
      await submit().click();
    }
    await page.waitForURL(`${base}/isolated-group-logistics`);
    assert.equal(state.orders.size, 1);
    assert.equal([...state.orders.values()][0].amount_total, 100);
    assert.equal(state.creates[0].expected_total, 160);
    assert.equal(state.creates[0].expected_quote_fingerprint, 'd'.repeat(64));
    assert.equal(state.creates[0].invoice_buyer_type, 'company');
    assert.equal(state.creates[0].invoice_buyer_tax_id, '24536806');
    assert.equal(state.creates[0].invoice_buyer_name, '原始發票公司');
    assert.equal(state.creates[0].invoice_buyer_email, 'original-invoice@example.com');
    if (lose) assert.deepEqual(state.creates[1], state.creates[0]);
    assert.deepEqual(state.logistics, [{ path: '/v1/orders/group-order-1/logistics/selection', body: { channel: 'home_delivery', temperature: 'ambient', recipient_name: '原始收件人', recipient_phone: '0900000000', shipping_address: '原始配送地址一號' } }]);
    assert.equal(state.payments.length, 0);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 3);
  });

  test('shipping unknown恢復已變更契約原單只回Orders，不再送物流或付款', async t => {
    const { page, state, submit } = await fixture(t, { shipping: true, loseResponse: true });
    await fillShippingInvoice(page);
    await submit().click();
    await page.getByText('目前無法連上後端服務，請稍後重試。', { exact: true }).waitFor();
    state.stale = true;
    await submit().click();
    await page.waitForURL(url => url.origin === base && url.pathname === '/orders');
    assert.equal(state.orders.size, 1);
    assert.equal(state.logistics.length, 0); assert.equal(state.payments.length, 0);
  });
}
