import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4250');
if (!['localhost', '127.0.0.1'].includes(target.hostname)) throw new Error('Only isolated local fixtures');
const base = target.origin;
const proposal = { id: 'recovery-proposal', title: '隔離提案', body: '只供本機驗證', status: 'discussion', proposal_type: 'resolution', created_by_id: 'buyer', created_by_name: '隔離社員', created_at: '2030-01-01T00:00:00Z', tally: { yes: 0, no: 0, abstain: 0, total: 0 }, options: [], minimum_voters: 1 };
const platform = { version: 0, orders_open: true, support_phone: '0912345678', closed_message: '隔離暫停接單', printer_brand: '', printer_model: '', printer_connection: 'unknown', kitchen_sound: false, invoice_provider: 'fanyu', sales_scope: 'pickup' };
const storage_product = { id: 'storage-product', slug: 'storage-product', name: '儲存隔離白米', category: 'processed', description: '', unit: '包', member_price: 100, nonmember_price: 100, stock_quantity: 50, is_active: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
async function fixture(t, { kind = 'member', admin = false, width = 390, handler, storage } = {}) {
  const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' });
  if (storage) await context.addInitScript(storage);
  const state = { reads: [], writes: [], errors: [], unknown: [], consoleErrors: [], failedRequests: [] };
  const user = { id: 'buyer', email: 'buyer@example.test', display_name: '隔離社員', user_role: admin ? 'admin' : 'customer', membership_type: kind, membership_status: kind === 'member' ? 'active' : kind };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    (request.method() === 'GET' ? state.reads : state.writes).push({ path, method: request.method(), ...(request.postData() ? { body: request.postDataJSON() } : {}) });
    if (await handler?.(route, url, request, state)) return;
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-recovery', user } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: kind, membership: { status: kind === 'member' ? 'active' : kind, member_number: 'ISOLATED-MEMBER' }, directory: { is_public: false } } });
    if (path === '/v1/platform') return route.fulfill({ json: platform });
    if (path === '/v1/admin/platform') return route.fulfill({ json: platform });
    if (path === '/v1/admin/membership-education/settings') return route.fulfill({ json: { configured: true, version: 1, retry_limit: 2, retry_interval_minutes: 0, updated_at: null, updated_by_id: null } });
    if (path === '/v1/member-proposals/recovery-proposal') return route.fulfill({ json: proposal });
    if (['/v1/member-proposals', '/v1/member-proposals/recovery-proposal/comments', '/v1/member-proposals/recovery-proposal/votes', '/v1/members/directory', '/v1/wishes', '/v1/meetings', '/v1/meeting-categories', '/v1/activities', '/v1/products', '/v1/group-campaigns', '/v1/meal-events', '/v1/orders'].includes(path)) return route.fulfill({ json: [] });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(3500);
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') state.consoleErrors.push(message.text()); });
  page.on('requestfailed', request => state.failedRequests.push({ url: request.url(), reason: request.failure()?.errorText }));
  t.after(() => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); });
  return { page, state, context };
}

for (const kind of ['nonmember', 'trainee']) test(`${kind}直開提案明確顯示資格限制，不永久載入、不讀私密API`, async t => {
  const { page, state } = await fixture(t, { kind });
  await page.goto(`${base}/proposals/recovery-proposal`);
  await page.getByRole('heading', { name: '此功能開放給正式社員', exact: true }).waitFor();
  assert.equal(await page.locator('main .loading-lines').count(), 0);
  assert.equal(state.reads.some(({ path }) => path.startsWith('/v1/member-proposals/')), false);
  await page.getByRole('link', { name: '查看入社進度', exact: true }).waitFor();
});

test('正式社員正常查看提案與留言', async t => {
  const { page } = await fixture(t);
  await page.goto(`${base}/proposals/recovery-proposal`);
  await page.getByRole('heading', { name: '隔離提案', exact: true }).waitFor();
  await page.getByRole('textbox', { name: '留言內容', exact: true }).waitFor();
});

for (const path of ['/directory', '/governance', '/wishes', '/social', '/proposals/recovery-proposal', '/meetings']) test(`${path}資格503顯示可重試服務錯誤，恢復後沿用正式社員功能`, async t => {
  let failing = true;
  const { page, state } = await fixture(t, { handler: (route, url) => {
    if (url.pathname === '/v1/members/me' && failing) { void route.fulfill({ status: 503, json: { detail: '隔離資格服務暫時失敗' } }); return true; }
  } });
  await page.goto(`${base}${path}`);
  await page.getByRole('heading', { name: '社員資格暫時無法讀取', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '此功能開放給正式社員', exact: true }).count(), 0);
  assert.equal(await page.getByRole('heading', { name: '開始入社程序', exact: true }).count(), 0);
  assert.equal(state.reads.some(({ path }) => ['/v1/members/directory', '/v1/member-proposals', '/v1/wishes', '/v1/meetings'].includes(path)), false);
  failing = false;
  await page.getByRole('button', { name: '重新確認資格', exact: true }).click();
  if (path === '/directory') await page.getByRole('button', { name: '編輯我的資料', exact: true }).waitFor();
  else if (path === '/governance') await page.getByRole('button', { name: '提出提案', exact: true }).waitFor();
  else if (path === '/wishes') await page.getByRole('button', { name: '提出願望', exact: true }).waitFor();
  else if (path === '/social') await page.locator('.social-identity-strip').waitFor();
  else if (path === '/meetings') await page.getByRole('heading', { name: '目前沒有會議紀錄', exact: true }).waitFor();
  else await page.getByRole('heading', { name: '隔離提案', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '社員資格暫時無法讀取', exact: true }).count(), 0);
});

for (const background of [true, false]) test(`平台設定${background ? '背景更新' : '保存409'}保留草稿，明確重讀後以新版本正常儲存`, async t => {
  let current = { ...platform };
  const { page, state } = await fixture(t, { admin: true, width: 1280, handler: async (route, url, request) => {
    if (url.pathname !== '/v1/admin/platform') return false;
    if (request.method() === 'GET') { await route.fulfill({ json: current }); return true; }
    const body = request.postDataJSON();
    if (body.expected_version !== current.version) { await route.fulfill({ status: 409, json: { detail: '隔離設定版本已更新，請重新讀取' } }); return true; }
    current = { ...current, ...body, version: current.version + 1 };
    await route.fulfill({ json: current }); return true;
  } });
  await page.clock.install();
  await page.goto(`${base}/admin/settings`);
  const phone = page.getByRole('textbox', { name: '客服電話（退單聯絡）', exact: true });
  await phone.fill('0987654321');
  current = { ...current, version: 1, printer_model: '另一管理員的新設備' };
  if (background) {
    const refresh = page.waitForResponse(response => new URL(response.url()).pathname === '/v1/admin/platform');
    await page.clock.runFor(31000);
    await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
    await refresh;
    await page.getByText('設定已更新。重新讀取會捨棄未儲存變更。', { exact: true }).waitFor();
    assert.equal(state.writes.filter(({ path }) => path === '/v1/admin/platform').length, 0);
  } else {
    await page.getByRole('button', { name: '儲存平台設定', exact: true }).click();
    await page.getByText('隔離設定版本已更新，請重新讀取', { exact: true }).waitFor();
    assert.equal(state.writes.find(({ path }) => path === '/v1/admin/platform').body.expected_version, 0);
  }
  assert.equal(await phone.inputValue(), '0987654321');
  assert.equal(await page.getByRole('button', { name: '儲存平台設定', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '重新讀取最新設定', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('input[type=tel]')?.value === '0912345678');
  assert.equal(await page.getByRole('textbox', { name: '印表機型號', exact: true }).count(), 0);
  await phone.fill('0999999999');
  await page.getByRole('button', { name: '儲存平台設定', exact: true }).click();
  await page.getByText('設定已儲存', { exact: true }).waitFor();
  assert.equal(current.support_phone, '0999999999');
  assert.equal(current.printer_model, '另一管理員的新設備');
  assert.equal(current.version, 2);
});

test('廚房與平台各自儲存同一版本的設定，保留另一頁的欄位', async t => {
  const public_information = { published: false, legal_name: '隔離合作社', tax_id: '', address: '', phone: '', email: '', policy_version: '', privacy: '', terms: '', returns: '' };
  let current = {
    ...platform,
    orders_open: false,
    printer_brand: '飛鵝',
    printer_model: 'FP-V58-W',
    printer_connection: 'network',
    invoice_provider: 'ecpay',
    public_information,
  };
  const { page, state } = await fixture(t, { admin: true, handler: async (route, url, request) => {
    const path = url.pathname;
    if (path === '/v1/admin/meal-kitchen') { await route.fulfill({ json: { items: [], total: 0, meal_quantity: 0, products: [] } }); return true; }
    if (path === '/v1/admin/printers/status') { await route.fulfill({ json: { status: 'disabled' } }); return true; }
    if (path === '/v1/admin/printers/jobs') { await route.fulfill({ json: { items: [], total: 0 } }); return true; }
    if (path !== '/v1/admin/platform') return false;
    if (request.method() === 'GET') { await route.fulfill({ json: current }); return true; }
    const { expected_version, reason: _reason, ...fields } = request.postDataJSON();
    if (expected_version !== current.version) { await route.fulfill({ status: 409, json: { detail: '隔離設定版本已更新' } }); return true; }
    const saved = { ...fields, version: current.version + 1, sales_scope: current.sales_scope };
    current = { ...saved, invoice_provider: current.invoice_provider };
    await route.fulfill({ json: saved });
    return true;
  } });
  await page.goto(`${base}/admin/kitchen`);
  await page.getByText('印表機與提示音', { exact: true }).click();
  const model = page.getByRole('textbox', { name: '印表機型號', exact: true });
  await model.waitFor();
  assert.equal(await model.inputValue(), 'FP-V58-W');
  assert.equal(await page.getByLabel('連接方式', { exact: true }).inputValue(), 'network');
  await model.fill('隔離更新型號');
  await page.getByRole('checkbox', { name: '啟用廚房新訂單提示音', exact: true }).check();
  assert.equal(await page.getByRole('button', { name: '開啟本頁新訂單提示音', exact: true }).count(), 0);
  await page.getByRole('button', { name: '儲存出單設定', exact: true }).click();
  await page.getByText('出單設定已儲存', { exact: true }).waitFor();
  await page.getByRole('button', { name: '開啟本頁新訂單提示音', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.equal(current.orders_open, false);
  assert.equal(current.support_phone, platform.support_phone);
  assert.equal(current.closed_message, platform.closed_message);
  assert.deepEqual(current.public_information, public_information);

  await page.goto(`${base}/admin/settings`);
  await page.getByText('綠界科技電子發票', { exact: true }).waitFor();
  assert.equal(await page.getByText('印表機與提示音', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('heading', { name: '測驗重考設定', exact: true }).count(), 0);
  await page.getByRole('textbox', { name: '客服電話（退單聯絡）', exact: true }).fill('0999999999');
  await page.getByRole('button', { name: '儲存平台設定', exact: true }).click();
  await page.getByText('設定已儲存', { exact: true }).waitFor();
  await page.getByText('綠界科技電子發票', { exact: true }).waitFor();
  assert.equal(current.printer_brand, '飛鵝');
  assert.equal(current.printer_model, '隔離更新型號');
  assert.equal(current.printer_connection, 'network');
  assert.equal(current.kitchen_sound, true);
  assert.deepEqual(current.public_information, public_information);
  assert.deepEqual(state.writes.filter(write => write.path !== '/v1/auth/refresh').map(write => [write.method, write.path, write.body.expected_version]), [
    ['PUT', '/v1/admin/platform', 0],
    ['PUT', '/v1/admin/platform', 1],
  ]);
});

for (const failure of ['submit-503', 'submit-response-lost', 'create-response-lost']) test(`提案${failure}重試/重整沿用同一request及草稿，正常送審只有一案`, async t => {
  let stored = null, create_requests = 0, submit_requests = 0;
  const request_bodies = [];
  const { page } = await fixture(t, { width: 1280, handler: async (route, url, request) => {
    const path = url.pathname;
    if (path === '/v1/member-proposals' && request.method() === 'GET') { await route.fulfill({ json: stored ? [stored] : [] }); return true; }
    if (path === '/v1/member-proposals' && request.method() === 'POST') {
      create_requests++;
      const body = request.postDataJSON(); request_bodies.push(body);
      if (!stored) stored = { ...proposal, ...body, id: 'recovery-proposal', status: 'draft' };
      else assert.equal(body.client_request_id, stored.client_request_id);
      if (failure === 'create-response-lost' && create_requests === 1) await route.abort();
      else await route.fulfill({ status: 201, json: stored });
      return true;
    }
    if (path === '/v1/member-proposals/recovery-proposal') { await route.fulfill({ json: stored }); return true; }
    if (path === '/v1/member-proposals/recovery-proposal/submit') {
      submit_requests++;
      if (failure === 'submit-503' && submit_requests === 1) { await route.fulfill({ status: 503, json: { detail: '隔離送審暫時失敗' } }); return true; }
      stored = { ...stored, status: 'pending_review' };
      if (failure === 'submit-response-lost' && submit_requests === 1) await route.abort();
      else await route.fulfill({ json: stored });
      return true;
    }
  } });
  await page.goto(`${base}/governance`);
  await page.getByRole('button', { name: '提出提案', exact: true }).click();
  await page.getByRole('textbox', { name: '提案主旨', exact: true }).fill('不重複的隔離提案');
  await page.getByRole('textbox', { name: '提案內容', exact: true }).fill('建立或送審回應遺失後，只能繼續原提案。');
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  if (failure !== 'submit-response-lost') {
    await page.locator('.social-form .form-error').waitFor();
    if (failure === 'create-response-lost') {
      await page.reload();
      await page.getByRole('textbox', { name: '提案主旨', exact: true }).waitFor();
      assert.equal(await page.getByRole('textbox', { name: '提案主旨', exact: true }).inputValue(), '不重複的隔離提案');
    }
    await page.getByRole('button', { name: '送出審核', exact: true }).click();
  }
  await page.locator('.proposal-list').getByRole('heading', { name: '不重複的隔離提案', exact: true }).waitFor();
  await page.locator('.proposal-list').getByText('待審核', { exact: true }).waitFor();
  assert.equal(create_requests, failure === 'create-response-lost' ? 2 : 1);
  assert.equal(submit_requests, failure === 'submit-503' ? 2 : 1);
  assert.match(request_bodies[0].client_request_id, /^[0-9a-f-]{36}$/i);
  if (request_bodies.length > 1) assert.deepEqual(request_bodies[1], request_bodies[0]);
});

test('正常儲存控制組：初次登入還原在商品GET回覆之前完成，商品仍可載入並選購', async t => {
  const refresh_requested = Promise.withResolvers(), product_requested = Promise.withResolvers();
  let refreshing = true, product_reads = 0;
  const { page } = await fixture(t, { handler: async (route, url) => {
    if (url.pathname === '/v1/auth/refresh' && refreshing) { refreshing = false; refresh_requested.resolve(route); return true; }
    if (url.pathname === '/v1/products/storage-product') {
      product_reads++;
      if (product_reads === 1) product_requested.resolve(route);
      else await route.fulfill({ json: storage_product });
      return true;
    }
  } });
  await page.goto(`${base}/products/storage-product`);
  const [refresh, product] = await Promise.all([refresh_requested.promise, product_requested.promise]);
  await refresh.fulfill({ json: { access_token: 'isolated-recovery', user: { id: 'buyer', email: 'buyer@example.test', display_name: '隔離社員', user_role: 'customer', membership_type: 'member', membership_status: 'active' } } });
  await page.locator('.account-button[aria-label="會員中心：隔離社員"]').waitFor({ state: 'attached' });
  await product.fulfill({ json: storage_product });
  await page.getByRole('heading', { name: storage_product.name, exact: true }).waitFor();
  assert.equal(product_reads, 2, '登入身分確定後應重新讀取商品，不使用舊匿名請求的晚到回覆');
  await page.getByRole('button', { name: `加入購物車：${storage_product.name}`, exact: true }).click();
  await page.getByText(`${storage_product.name}已加入，清單共 1 ${storage_product.unit}`, { exact: true }).waitFor();
});

for (const mode of ['removeItem', 'getItem', 'setItem', 'getter']) test(`storage ${mode}受限仍可瀏覽及同頁購物，不清舊cart/記住的Email`, async t => {
  const { page, state } = await fixture(t, { storage: { content: `(() => {
    const original = window.localStorage, get = Storage.prototype.getItem;
    original.setItem('slf_cart_v2:user:buyer', ${JSON.stringify(JSON.stringify({ items: [{ product: storage_product, quantity: 2 }] }))});
    original.setItem('shilifangyuan.remembered-login-email', 'remembered@example.test');
    window.__auditReadNative = key => get.call(original, key);
    if (${JSON.stringify(mode)} === 'getter') Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('Storage blocked', 'SecurityError'); } });
    else Storage.prototype[${JSON.stringify(mode)}] = function() { throw new DOMException('Storage blocked', 'SecurityError'); };
  })();` }, handler: async (route, url, request) => {
    if (url.pathname === '/v1/products/storage-product') { await route.fulfill({ json: storage_product }); return true; }
    if (url.pathname === '/v1/pickup-locations') { await route.fulfill({ json: [{ id: 'pickup', name: '隔離取貨點', is_active: true }] }); return true; }
    if (url.pathname === '/v1/orders/quote') { const quantity = request.postDataJSON().items[0].quantity; await route.fulfill({ json: { items: [{ product_id: storage_product.id, product_name: storage_product.name, quantity, subtotal: quantity * 100 }], amount_total: quantity * 100, membership_type: 'member' } }); return true; }
  } });
  await page.goto(`${base}/products/storage-product`);
  await page.getByRole('heading', { name: storage_product.name, exact: true }).waitFor().catch(async error => {
    t.diagnostic(JSON.stringify({ state, url: page.url(), body: await page.locator('body').innerText(), main: await page.locator('main').innerHTML(), account: await page.locator('.account-button').getAttribute('aria-label') }));
    throw error;
  });
  await page.getByRole('button', { name: `加入購物車：${storage_product.name}`, exact: true }).click();
  const count = ['getItem', 'getter'].includes(mode) ? 1 : 3;
  await page.getByText(`${storage_product.name}已加入，清單共 ${count} ${storage_product.unit}`, { exact: true }).waitFor();
  await page.getByText('瀏覽器儲存受限，部分資料只在本頁暫存，重整後可能無法保留。', { exact: true }).waitFor();
  const native = await page.evaluate(() => ({ cart: JSON.parse(window.__auditReadNative('slf_cart_v2:user:buyer')).items, email: window.__auditReadNative('shilifangyuan.remembered-login-email') }));
  assert.equal(native.cart[0].quantity, mode === 'removeItem' ? 3 : 2);
  assert.equal(native.email, 'remembered@example.test');
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('link', { name: `結帳清單 (${count})`, exact: true }).click();
  await page.getByRole('heading', { name: storage_product.name, exact: true }).waitFor();
  assert.match(await page.locator('.cart-link').innerText(), new RegExp(String(count)));
});

for (const invalid_options of ['唯一選項', '相同選項\n相同選項']) test(`提案首次已知422保留可編輯內容，修正選項後正常送審：${invalid_options}`, async t => {
  let stored = null;
  const ids = [];
  const { page } = await fixture(t, { width: 1280, handler: async (route, url, request) => {
    if (url.pathname === '/v1/member-proposals' && request.method() === 'GET') { await route.fulfill({ json: stored ? [stored] : [] }); return true; }
    if (url.pathname === '/v1/member-proposals' && request.method() === 'POST') {
      const body = request.postDataJSON(); ids.push(body.client_request_id);
      if (new Set(body.options.map(option => option.label)).size < 2) await route.fulfill({ status: 422, json: { detail: '至少提供兩個不同選項' } });
      else { stored = { ...proposal, ...body, status: 'draft' }; await route.fulfill({ status: 201, json: stored }); }
      return true;
    }
    if (url.pathname === '/v1/member-proposals/recovery-proposal/submit') { stored = { ...stored, status: 'pending_review' }; await route.fulfill({ json: stored }); return true; }
  } });
  await page.goto(`${base}/governance`);
  await page.getByRole('button', { name: '提出提案', exact: true }).click();
  await page.getByRole('textbox', { name: '提案主旨', exact: true }).fill('可以修正的選項提案');
  await page.getByRole('textbox', { name: '提案內容', exact: true }).fill('首次明確422沒有建案，應保留可編輯內容。');
  await page.getByRole('combobox', { name: '表決方式', exact: true }).selectOption('multiple_choice');
  const options = page.getByRole('textbox', { name: '選項（每行一個）', exact: true });
  await options.fill(invalid_options);
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.getByText('至少提供兩個不同選項', { exact: true }).waitFor();
  assert.equal(await options.isEditable(), true);
  assert.equal(await page.getByRole('textbox', { name: '提案主旨', exact: true }).inputValue(), '可以修正的選項提案');
  await options.fill('修正選項一\n修正選項二');
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.locator('.proposal-list').getByText('待審核', { exact: true }).waitFor();
  assert.equal(ids.length, 2); assert.notEqual(ids[0], ids[1]);
});

test('提案snapshot儲存quota失敗時不送POST，畫面凍結原資料；允許儲存後重試同一提案', async t => {
  let stored = null;
  const { page, state } = await fixture(t, { storage: () => {
    const original = Storage.prototype.setItem;
    window.__auditSessionBlocked = true;
    Storage.prototype.setItem = function(key, value) {
      if (this === window.sessionStorage && window.__auditSessionBlocked) throw new DOMException('Quota', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  }, handler: async (route, url, request) => {
    if (url.pathname === '/v1/member-proposals' && request.method() === 'GET') { await route.fulfill({ json: stored ? [stored] : [] }); return true; }
    if (url.pathname === '/v1/member-proposals' && request.method() === 'POST') { stored = { ...proposal, ...request.postDataJSON(), status: 'draft' }; await route.fulfill({ status: 201, json: stored }); return true; }
    if (url.pathname === '/v1/member-proposals/recovery-proposal/submit') { stored = { ...stored, status: 'pending_review' }; await route.fulfill({ json: stored }); return true; }
  } });
  await page.goto(`${base}/governance`);
  await page.getByRole('button', { name: '提出提案', exact: true }).click();
  const title = page.getByRole('textbox', { name: '提案主旨', exact: true });
  await title.fill('quota前的原提案');
  await page.getByRole('textbox', { name: '提案內容', exact: true }).fill('畫面與實際送出的內容必須一致。');
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.getByText('瀏覽器無法儲存提案識別，請允許網站儲存資料後重試', { exact: true }).waitFor();
  assert.equal(await title.isEditable(), false);
  assert.equal(await title.inputValue(), 'quota前的原提案');
  assert.equal(state.writes.some(({ path }) => path === '/v1/member-proposals'), false);
  await page.evaluate(() => { window.__auditSessionBlocked = false; });
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.locator('.proposal-list').getByRole('heading', { name: 'quota前的原提案', exact: true }).waitFor();
  assert.equal(state.writes.filter(({ path }) => path === '/v1/member-proposals').length, 1);
});

test('提案create先unknown再422不得丟棄原snapshot或悄悄換新key', async t => {
  let attempts = 0;
  const ids = [];
  const { page } = await fixture(t, { handler: async (route, url, request) => {
    if (url.pathname !== '/v1/member-proposals' || request.method() !== 'POST') return false;
    attempts++; ids.push(request.postDataJSON().client_request_id);
    await route.fulfill({ status: attempts === 1 ? 503 : 422, json: { detail: attempts === 1 ? '建立結果仍待確認' : '先前結果未確認，保留原識別' } }); return true;
  } });
  await page.goto(`${base}/governance`);
  await page.getByRole('button', { name: '提出提案', exact: true }).click();
  const title = page.getByRole('textbox', { name: '提案主旨', exact: true });
  await title.fill('不可丟棄的未知提案');
  await page.getByRole('textbox', { name: '提案內容', exact: true }).fill('未知結果後不得自動建立另一份提案。');
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.getByText('建立結果仍待確認', { exact: true }).waitFor();
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.getByText('先前結果未確認，保留原識別', { exact: true }).waitFor();
  assert.equal(await title.isEditable(), false);
  assert.equal(ids.length, 2); assert.equal(ids[0], ids[1]);
  assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('slf_proposal_submission:buyer')).createUncertain), true);
});

test('提案重整先顯示訪客後才完成登入還原，仍恢復並鎖住原送審snapshot', async t => {
  const saved = { requestId: '77f78d6a-c4a5-4aec-a605-72121049412b', input: { title: '登入還原前已送出的原提案', body: '未知結果只能恢復同一提案，不能讓新表單遮住原內容。', proposal_type: 'resolution', options: [] }, createUncertain: true };
  const refresh_requested = Promise.withResolvers();
  let held = true, stored = null;
  const { page, state } = await fixture(t, { storage: { content: `sessionStorage.setItem('slf_proposal_submission:buyer', ${JSON.stringify(JSON.stringify(saved))});` }, handler: async (route, url, request) => {
    if (url.pathname === '/v1/auth/refresh' && held) { held = false; refresh_requested.resolve(route); return true; }
    if (url.pathname === '/v1/member-proposals' && request.method() === 'GET') { await route.fulfill({ json: stored ? [stored] : [] }); return true; }
    if (url.pathname === '/v1/member-proposals' && request.method() === 'POST') { stored = { ...proposal, ...request.postDataJSON(), status: 'draft' }; await route.fulfill({ status: 201, json: stored }); return true; }
    if (url.pathname === '/v1/member-proposals/recovery-proposal/submit') { stored = { ...stored, status: 'pending_review' }; await route.fulfill({ json: stored }); return true; }
  } });
  await page.goto(`${base}/governance`);
  await page.getByRole('heading', { name: '登入後查看社員提案', exact: true }).waitFor();
  await (await refresh_requested.promise).fulfill({ json: { access_token: 'isolated-recovery', user: { id: 'buyer', email: 'buyer@example.test', display_name: '隔離社員', user_role: 'customer', membership_type: 'member', membership_status: 'active' } } });
  const title = page.getByRole('textbox', { name: '提案主旨', exact: true });
  await title.waitFor();
  assert.equal(await title.inputValue(), saved.input.title);
  assert.equal(await title.isEditable(), false);
  await page.getByRole('button', { name: '送出審核', exact: true }).click();
  await page.locator('.proposal-list').getByText('待審核', { exact: true }).waitFor();
  const creates = state.writes.filter(({ path }) => path === '/v1/member-proposals');
  assert.equal(creates.length, 1);
  assert.deepEqual(creates[0].body, { ...saved.input, client_request_id: saved.requestId });
});
