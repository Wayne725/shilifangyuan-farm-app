import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4198');
if (!['localhost', '127.0.0.1'].includes(target.hostname)) throw new Error('Local fixtures only');
const base = target.origin;
const pickupAt = '2030-01-03T11:30';
const sample = {
  id: 'sample', title: '清華大學午餐', location: '清華大學', status: 'published', service_date: '2030-01-03',
  ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-02T20:00:00+08:00',
  pickup_starts_at: '2030-01-03T11:00:00+08:00', pickup_ends_at: '2030-01-03T13:30:00+08:00',
  offerings: [
    ['chicken', '嫩煮雞胸', 'sous-vide-chicken-breast', 149],
    ['salad', '三明治沙拉', 'sandwich-salad', 139],
  ].map(([id, name, image, price]) => ({ id, meal_id: id, meal_name: name, price,
    image_url: `/meals/${image}.webp`, description: '新鮮食材，搭配當季蔬菜。',
    capacity: 20, available_quantity: 20, reserved_quantity: 0, paid_quantity: 0, position: 0, is_active: true, option_groups: [] })),
};

async function fixture(t, width, email = 'buyer@example.test') {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 720 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', reducedMotion: 'reduce', serviceWorkers: 'block' });
  const state = { quotes: [], orders: [], unknown: [], errors: [] };
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', display_name: '測試管理員', email, user_role: 'admin' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (['/v1/products', '/v1/group-campaigns', '/v1/orders'].includes(path)) return route.fulfill({ json: [] });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [] });
    if (path === '/v1/meal-events') return route.fulfill({ json: [sample] });
    if (path === '/v1/meal-events/sample') return route.fulfill({ json: sample });
    if (path === '/v1/meal-events/sample/quote') {
      const body = request.postDataJSON(); state.quotes.push(body);
      const quantity = body.items[0].quantity, subtotal = quantity * 149;
      const discount = body.coupon_code === 'FARM10' ? 10 : 0;
      return route.fulfill({ json: { amount_total: subtotal - discount, items: [{ offering_id: 'chicken', meal_name: '嫩煮雞胸', quantity, subtotal, selections: [] }],
        ...(discount ? { promotion: { name: '便當優惠', discount } } : {}), pickup: { location: sample.location, starts_at: sample.pickup_starts_at, ends_at: sample.pickup_ends_at } } });
    }
    if (path === '/v1/meal-events/sample/orders') { state.orders.push(request.postDataJSON()); return route.fulfill({ json: { id: 'isolated-order' } }); }
    if (path === '/v1/orders/isolated-order/payment-attempts') return route.fulfill({ json: { payment_url: `${base}/isolated-payment` } });
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { total: 2, items: [
      { id: 'pending', order_number: 'SLF-UI-001', payment_status: 'pending', fulfillment_status: 'pending_confirmation', invoice_status: 'not_eligible' },
      { id: 'error', order_number: 'SLF-UI-002', payment_status: 'refunded', fulfillment_status: 'cancelled', invoice_status: 'failed' },
    ].map(order => ({ ...order, amount_total: 149, created_at: '2030-01-02T03:00:00Z', sales_channel: 'regular', available_actions: [], items: [] })) } });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

async function verify(page, state) {
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '整頁無水平溢出');
}
async function capture(page, name) {
  if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/${name}.png`, fullPage: false });
}

for (const width of [1280, 816, 390]) test(`${width}px 訂餐首屏、收合設定與付款資料保持一致`, async t => {
  const { page, state } = await fixture(t, width);
  await page.goto(`${base}/shop`);
  const time = page.getByRole('link', { name: '清華大學 11:30 取餐', exact: true });
  await time.waitFor();
  assert.ok((await time.boundingBox()).y < 720, '取餐時段出現在首屏');
  await capture(page, `shop-${width}`);
  await time.click();
  const add = page.getByRole('button', { name: '增加嫩煮雞胸', exact: true });
  await add.waitFor();
  const box = await add.boundingBox();
  assert.ok(box.y + box.height < (width === 390 ? 760 : 720), `首道餐點可直接加份數：${JSON.stringify(box)}`);
  assert.ok(box.width >= 48 && box.height >= 48);
  assert.equal(await page.getByRole('heading', { name: '選擇餐點與份數', exact: true }).count(), 0);
  await capture(page, `meal-${width}`);
  await add.click();
  await page.locator('.offer-total dd').getByText('$149', { exact: true }).waitFor();
  const invoice = page.getByRole('region', { name: '電子發票設定', exact: true });
  assert.equal(await invoice.getByLabel('發票通知 Email', { exact: true }).isVisible(), false);
  await invoice.getByRole('button', { name: '修改發票設定', exact: true }).click();
  const input = invoice.getByLabel('發票通知 Email', { exact: true });
  await input.fill('invalid');
  assert.equal(await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).isEnabled(), false);
  await invoice.getByText('請輸入正確的 Email 格式', { exact: true }).waitFor();
  await input.fill('updated@example.test');
  assert.equal(await input.isVisible(), true, '輸入變有效時不能自動隱藏');
  await invoice.getByRole('button', { name: '完成發票設定', exact: true }).click();
  await invoice.getByText('updated@example.test', { exact: true }).waitFor();
  assert.equal(await input.isVisible(), false);
  await page.getByText('使用優惠碼', { exact: true }).click();
  await page.getByLabel('優惠碼', { exact: true }).fill('farm10');
  await page.getByRole('button', { name: '套用優惠碼', exact: true }).click();
  await page.locator('.offer-total dd').getByText('$139', { exact: true }).waitFor();
  await page.locator('.reading-menu > summary').click();
  await page.getByRole('button', { name: '放大', exact: true }).click();
  await verify(page, state);
  const pay = page.getByRole('button', { name: '預訂並前往線上付款', exact: true });
  await pay.scrollIntoViewIfNeeded();
  await capture(page, `summary-large-${width}`);
  await pay.click();
  await page.waitForURL(`${base}/isolated-payment`);
  assert.equal(state.orders.length, 1);
  assert.equal(state.orders[0].expected_total, 139);
  assert.equal(state.orders[0].contact_email, 'updated@example.test');
  assert.equal(state.orders[0].coupon_code, 'FARM10');
  assert.equal(state.orders[0].pickup_at, '2030-01-03T03:30:00.000Z');
  await verify(page, state);
});

test('缺少 Email 時直接展開發票設定，輸入有效後仍留在原欄位', async t => {
  const { page, state } = await fixture(t, 390, '');
  await page.goto(`${base}/meals/sample?pickup_at=${encodeURIComponent(pickupAt)}`);
  const input = page.getByLabel('發票通知 Email', { exact: true });
  await input.waitFor();
  await input.fill('new@example.test');
  assert.equal(await input.isVisible(), true);
  assert.equal(state.orders.length, 0);
  await verify(page, state);
});

for (const width of [1280, 390]) test(`${width}px 後台首屏可處理、主分類不換行、狀態與文字對比清楚`, async t => {
  const { page, state } = await fixture(t, width);
  await page.goto(`${base}/admin?section=orders`);
  await page.getByText('SLF-UI-001', { exact: true }).waitFor();
  if (width === 1280) assert.ok((await page.locator('.operation-state').first().boundingBox()).y < 650);
  const nav = page.getByRole('navigation', { name: '管理工作台分類', exact: true });
  const rows = await nav.getByRole('link').evaluateAll(elements => elements.map(element => Math.round(element.getBoundingClientRect().top)));
  assert.equal(new Set(rows).size, 1, '五大分類保持同一列');
  const badgeStyles = await page.locator('.order-status-badge').evaluateAll(elements => elements.map(element => ({text: element.textContent, color: getComputedStyle(element).color, background: getComputedStyle(element).backgroundColor})));
  assert.notEqual(badgeStyles[0].background, badgeStyles[2].background);
  assert.notEqual(badgeStyles[5].background, badgeStyles[2].background);
  const contrast = await page.locator('.eyebrow').first().evaluate(element => {
    const rgb = text => text.match(/[\d.]+/g).slice(0, 3).map(Number);
    const luminance = channels => channels.map(channel => { const v = channel / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    const fg = luminance(rgb(getComputedStyle(element).color));
    let parent = element;
    while (parent && getComputedStyle(parent).backgroundColor === 'rgba(0, 0, 0, 0)') parent = parent.parentElement;
    const bg = luminance(rgb(getComputedStyle(parent).backgroundColor));
    return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
  });
  assert.ok(contrast >= 4.5, `金色文字對比：${contrast}`);
  await capture(page, `admin-${width}`);
  await verify(page, state);
});
