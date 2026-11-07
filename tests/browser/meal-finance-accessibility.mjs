import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4189');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const date = '2030-01-03';
function meal(id, period) {
  return { id, title: `隔離${period}`, location: '合作社取餐點', status: 'published', service_date: date, meal_period: period,
    ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-03T00:00:00+08:00',
    pickup_starts_at: `${date}T${period === 'lunch' ? '11:00' : '16:30'}:00+08:00`,
    pickup_ends_at: `${date}T${period === 'lunch' ? '13:30' : '18:30'}:00+08:00`,
    offerings: [{ id: `dish-${id}`, meal_id: 'shared-dish', meal_name: '蔬食便當', price: 120, description: '', capacity: 50,
      available_quantity: 50, reserved_quantity: 0, paid_quantity: 0, position: 0, is_active: true, option_groups: [] }] };
}
const events = [meal('lunch', 'lunch'), meal('dinner', 'dinner')];
const sale = { id: 'sale-1', order_number: 'SLF-REPORT-001', sales_channel: 'regular', customer_name: '隔離買家', customer_email: 'buyer@example.com',
  membership_type: 'member', created_at: '2030-01-02T01:00:00Z', paid_at: '2030-01-02T01:01:00Z', order_amount: 320,
  tax_amount: 0, period_received: 320, period_refunded: 100, period_net: 220, payment_status: 'paid', fulfillment_status: 'pending',
  pickup_at: null, pickup_time_confirmed: false, invoice_status: 'issued', invoice_number: 'AB12345678',
  items: [{ name: '有機米', unit: '袋', quantity: 2, unit_price: 160, subtotal: 320, tax_type: 'tax_exempt', options: [] }],
  refunds: [{ amount: 100, status: 'completed', reason: '跨期退款', requested_at: '2030-01-01T01:00:00Z', completed_at: '2030-01-02T02:00:00Z' }] };
const report = { starts_on: '2030-01-01', ends_on: '2030-01-02', summary: { received: 320, refunded: 100, net: 220, receipt_count: 1, refund_count: 1, paid_meal_quantity: 2, pending_refund_amount: 0 },
  daily: [{ period: '2030-01-02', received: 320, refunded: 100, net: 220 }], weekly: [{ period: '2029-12-31', received: 320, refunded: 100, net: 220 }], monthly: [{ period: '2030-01', received: 320, refunded: 100, net: 220 }],
  products: [{ name: '有機米', unit: '袋', quantity: 2, sales_amount: 320, order_count: 1 }], orders: { items: [sale], total: 1, limit: 20, offset: 0 } };
async function fixture(t, width) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = { unknown: [], errors: [], quotes: [], reads: [], writes: [] };
  await context.route('**/*', (route) => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', email: 'buyer@example.com', display_name: '隔離買家', user_role: 'admin' } } });
    if (req.method() === 'GET') {
      if (path === '/v1/platform') return route.fulfill({json:{orders_open:true,closed_message:''}});
      if (['/v1/products', '/v1/group-campaigns'].includes(path)) return route.fulfill({ json: [] });
      if (path === '/v1/pickup-locations') return route.fulfill({ json: [] });
    if (path === '/v1/meal-events') return route.fulfill({ json: events });
      const event = events.find((row) => path === `/v1/meal-events/${row.id}`);
      if (event) return route.fulfill({ json: event });
      if (path === '/v1/admin/finance/meal-sales') { state.reads.push(Object.fromEntries(url.searchParams)); return route.fulfill({ json: report }); }
      if (path === '/v1/admin/finance/meal-sales.csv') { state.reads.push({ csv: true, ...Object.fromEntries(url.searchParams) }); return route.fulfill({ contentType: 'text/csv', body: '\ufeff訂單編號,銷售類型\nSLF-REPORT-001,日常選品' }); }
    }
    if (req.method() === 'POST' && /\/meal-events\/[^/]+\/quote$/.test(path)) {
      const body = req.postDataJSON(); state.quotes.push({ path, body });
      return route.fulfill({ json: { amount_total: body.items[0].quantity * 120, tax_amount: 0, member_tax_exempt: false,
        items: [{ offering_id: body.items[0].offering_id, meal_name: '蔬食便當', quantity: body.items[0].quantity, base_price: 120, option_price: 0, unit_price: 120, subtotal: body.items[0].quantity * 120, tax_type: 'taxable', selections: [] }],
        pickup: { location: '合作社取餐點', starts_at: events[0].pickup_starts_at, ends_at: events[0].pickup_ends_at } } });
    }
    state.unknown.push(`${req.method()} ${path}`); return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}
async function verify(page, state) {
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不能水平溢出');
  const overlaps = await page.locator('.site-header').evaluate((header) => {
    const visible = [...header.children].filter((e) => e.getBoundingClientRect().width > 0);
    return visible.some((left, i) => visible.slice(i + 1).some((right) => {
      const a = left.getBoundingClientRect(), b = right.getBoundingClientRect();
      return Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
    }));
  });
  assert.equal(overlaps, false, '標頭導覽不能重疊');
}
async function large(page) {
  await page.locator('.reading-menu > summary').click();
  await page.getByRole('group', { name: '文字大小' }).getByRole('button', { name: '放大', exact: true }).click();
  assert.equal(await page.locator('html').getAttribute('data-text-size'), 'large');
  await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px', null, { timeout: 3000 });
}
for (const width of [1440, 390]) {
  test(`${width}px 直接選時段帶入正確菜單，改時段與重整保留選餐，大字無溢出`, { timeout: 30000 }, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/shop`);
    await page.getByRole('link', { name: '合作社取餐點 11:00 取餐', exact: true }).waitFor();
    assert.equal(await page.getByRole('group', { name: '餐別', exact: true }).count(), 0);
    const times = page.locator('.meal-time-selection .meal-pickup-options a');
    assert.deepEqual(await times.allTextContents(), ['11:00','11:30','12:00','12:30','13:00','16:30','17:00','17:30','18:00']);
    assert.equal(await page.locator('.meal-booking-content .eyebrow').evaluate((e) => getComputedStyle(e).fontSize), '16px');
    await large(page); await verify(page, state);
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.meal-booking-content .eyebrow')).fontSize === '20px', null, { timeout: 3000 });
    assert.equal(await page.locator('.meal-booking-content .eyebrow').evaluate((e) => getComputedStyle(e).fontSize), '20px');
    await page.getByRole('link', { name: '合作社取餐點 18:00 取餐', exact: true }).click();
    await page.getByRole('heading', { name: '選餐點與份數', exact: true }).waitFor();
    assert.match(page.url(), /\/meals\/dinner\?pickup_at=/);
    await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
    await page.locator('.offer-total dd').getByText('$120', { exact: true }).waitFor();
    assert.equal(state.quotes.at(-1).body.pickup_at, '2030-01-03T10:00:00.000Z');
    await page.getByText('修改取餐時間', {exact:true}).click();
    await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).selectOption('2030-01-03T17:30');
    await page.waitForURL(/pickup_at=.*17%3A30/);
    await page.reload();
    await page.getByText('修改取餐時間', {exact:true}).click();
    await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).inputValue(), '2030-01-03T17:30');
    await page.locator('.offer-total dd').getByText('$120', { exact: true }).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-text-size'), 'large');
    await verify(page, state);
    if(process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.BROWSER_SCREENSHOT_DIR}/meal-compact-${width}.png`,fullPage:true});
    await page.getByRole('button', { name: '清空選餐與取餐時間', exact: true }).click();
    await page.reload();
    await page.getByRole('heading', { name: '選餐點與份數', exact: true }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).inputValue(), '');
    assert.equal(state.quotes.every((q) => q.body.items[0].offering_id === 'dish-dinner'), true);
  });
  test(`${width}px 財務明細可展開、統整與CSV共用篩選，大字保持原版頁籤`, { timeout: 30000 }, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin/finance`);
    await page.getByRole('tab', { name: '銷售明細（1 筆）', exact: true }).waitFor();
    assert.equal(state.reads[0].sales_channel, 'all');
    assert.equal(await page.locator('.meal-finance-sale').getAttribute('open'), null);
    await page.locator('.meal-finance-sale > summary').click();
    await page.getByText('預估稅額', { exact: true }).waitFor();
    assert.match(await page.locator('.meal-finance-order-totals').innerText(), /期間淨收款\s*\$220/);
    await page.getByText('查看退款紀錄（1 筆）', { exact: true }).click();
    await page.getByText(/原因：跨期退款/).waitFor();
    await large(page); await verify(page, state);
    await page.getByRole('tab', { name: '銷售統整', exact: true }).click();
      const tab = page.getByRole('tab', { name: '銷售統整', exact: true });
    assert.equal(await tab.evaluate((e) => getComputedStyle(e).backgroundColor), 'rgba(0, 0, 0, 0)');
    assert.equal(await tab.getAttribute('data-state'), 'active', await page.locator('.meal-finance-page').innerText());
    await page.getByRole('combobox', { name: '統整方式', exact: true }).selectOption('weekly');
    await page.getByRole('rowheader', { name: '2029-12-31 起', exact: true }).waitFor();
    await page.getByRole('combobox', { name: '訂單分類', exact: true }).selectOption('general');
    await page.getByRole('button', { name: '更新', exact: true }).waitFor();
    await page.getByRole('combobox', { name: '銷售類型', exact: true }).selectOption('regular');
    await page.getByLabel('訂單或買家', { exact: true }).fill('SLF-REPORT-001');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByRole('button', { name: '更新', exact: true }).waitFor();
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: '下載全部明細 CSV', exact: true }).click();
    await downloading;
    const csv = state.reads.findLast((q) => q.csv);
    assert.equal(csv.order_category, 'general'); assert.equal(csv.sales_channel, 'regular'); assert.equal(csv.search, 'SLF-REPORT-001');
    assert.equal(csv.starts_on, '2030-01-01'); assert.equal(csv.ends_on, '2030-01-02');
    await verify(page, state);
    if (process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/finance-${width}.png`, fullPage: true });
  });
}
test('非法與今日取餐時間不採用，不能直接送出付款', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/meals/dinner?pickup_at=2030-01-02T18%3A00`);
  await page.getByRole('heading', { name: '選餐點與份數', exact: true }).waitFor();
  await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '預訂並前往線上付款', exact: true }).isEnabled(), false);
  assert.equal(state.quotes.length, 0); await verify(page, state);
});
