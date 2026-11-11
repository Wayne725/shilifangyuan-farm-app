import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Only isolated local checkout is allowed');
const base = target.origin;
const product = { id: 'line-product', slug: 'line-product', name: '隔離測試白米', unit: '包', description: '', category: 'processed',
  member_price: 120, nonmember_price: 120, stock_quantity: 50, is_active: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'taxable' };
const event = { id: 'line-meal', title: '隔離便當場次', location: '合作社', status: 'published', service_date: '2030-01-03', meal_period: 'lunch',
  ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-03T00:00:00+08:00',
  pickup_starts_at: '2030-01-03T11:00:00+08:00', pickup_ends_at: '2030-01-03T13:30:00+08:00',
  offerings: [{ id: 'line-dish', meal_id: 'line-dish', meal_name: '隔離便當', price: 120, description: '', capacity: 50, available_quantity: 50, reserved_quantity: 0, paid_quantity: 0, is_active: true, position: 0, option_groups: [] }] };
const campaign = { id: 'line-group', title: '隔離團購', description: '', member_price: 120, nonmember_price: 120, target_type: 'product', target_id: product.id,
  status: 'open', intake_status: 'open', decision_status: 'recruiting', deadline: '2030-01-04T00:00:00Z', estimated_pickup_start: '2030-01-05T00:00:00Z', estimated_pickup_end: '2030-01-06T00:00:00Z',
  paid_quantity: 0, min_paid_quantity: 1, supply_cap: 50, per_user_cap: 10, available_quantity: 50, allowed_shipping_channels: [], can_ship: false };

for (const width of [1440, 390]) for (const channel of ['regular', 'meal', 'group']) for (const saved of [false, true]) {
  test(`${width}px ${channel} LINE無登入Email，${saved ? '預填已存通知信箱' : '結帳才填信箱'}並保留線上付款`, async (t) => {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    t.after(() => browser.close());
    const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
    if (channel === 'regular') await context.addInitScript((item) => localStorage.setItem('slf_cart_v2:user:line-buyer', JSON.stringify({ items: [{ product: item, quantity: 1 }] })), product);
    const writes = [], unknown = [], errors = [];
    const orderPath = channel === 'regular' ? '/v1/orders' : channel === 'meal' ? '/v1/meal-events/line-meal/orders' : '/v1/group-campaigns/line-group/join';
    await context.route('**/*', (route) => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      if (url.origin !== base) return route.abort();
      if (path === '/isolated-line-payment') return route.fulfill({ contentType: 'text/html', body: '<h1>隔離線上付款</h1>' });
      if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
      if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'line-buyer', display_name: 'LINE顧客', email: null, notification_email: saved ? 'saved@example.com' : null, membership_type: 'nonmember', user_role: 'customer' } } });
      if (request.method() === 'GET') {
        if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
        if (path === '/v1/orders') return route.fulfill({ json: [] });
        if (path === '/v1/orders/line-order' && writes.some(entry => entry.path === orderPath)) {
          assert.equal(url.searchParams.get('own_only'), 'true');
          return route.fulfill({ json: {
          id: 'line-order', order_number: 'SLF-LINE-ISOLATED', sales_channel: channel === 'meal' ? 'meal_preorder' : channel === 'group' ? 'group' : 'regular',
          fulfillment_method: channel === 'meal' ? 'event_pickup' : 'cooperative_pickup', payment_status: 'pending', amount_total: 120,
          created_at: '2030-01-02T01:00:00Z', invoice_status: 'not_eligible', fulfillment_status: 'pending_confirmation', available_actions: [],
          items: [{ id: 'line-order-item', product_name: product.name, quantity: 1, unit_price: 120, subtotal: 120, unit_label: '包', tax_type: 'taxable' }],
          } });
        }
        if (path === '/v1/meal-orders/line-order' && channel === 'meal' && writes.some(entry => entry.path === orderPath)) return route.fulfill({ json: {
          id: 'line-order', order_number: 'SLF-LINE-ISOLATED', sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
          meal_event_title: event.title, venue_name: event.location, pickup_start: event.pickup_starts_at, pickup_end: event.pickup_ends_at,
          created_at: '2030-01-02T01:00:00Z', payment_status: 'pending', invoice_status: 'not_eligible', fulfillment_status: 'pending_confirmation',
          amount_total: 120, available_actions: [], items: [{ offering_id: 'line-dish', meal_name: '隔離便當', quantity: 1, unit_price: 120, subtotal: 120, selections: [] }],
        } });
        if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'pickup', name: '合作社', address: '測試取貨點', is_active: true }, { id: 'pickup-campus', name: '教育學院大樓', address: '另一測試取貨點', is_active: true }] });
        if (path === '/v1/meal-events/line-meal') return route.fulfill({ json: event });
        if (path === '/v1/group-campaigns/line-group') return route.fulfill({ json: campaign });
      }
      if (path.endsWith('/quote')) return route.fulfill({ json: { ...(path.startsWith('/v1/group-campaigns/') ? { confirmation_fingerprint: 'a'.repeat(64) } : {}), amount_total: 120, membership_type: 'nonmember', quantity: 1, unit_price: 120, product_subtotal: 120, shipping_fee: 0,
        items: [{ product_id: product.id, product_name: product.name, offering_id: 'line-dish', meal_name: '隔離便當', quantity: 1, base_price: 120, option_price: 0, unit_price: 120, subtotal: 120, selections: [], tax_type: 'taxable' }], pickup: { location: '合作社', starts_at: event.pickup_starts_at, ends_at: event.pickup_ends_at } } });
      if (request.method() === 'POST' && path === orderPath) {
        writes.push({ path, body: request.postDataJSON() });
        return route.fulfill({ status: 201, json: { id: 'line-order', order_number: 'SLF-LINE-ISOLATED', payment_status: 'pending', amount_total: 120 } });
      }
      if (request.method() === 'POST' && path === '/v1/orders/line-order/payment-attempts') {
        writes.push({ path });
        return route.fulfill({ status: 201, json: { id: 'isolated-attempt', status: 'pending', payment_url: `${base}/isolated-line-payment` } });
      }
      unknown.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 404, json: { detail: 'Undefined local route' } });
    });
    const page = await context.newPage();
    page.setDefaultTimeout(6000);
    page.on('pageerror', (error) => errors.push(error.message));
    await page.clock.install({ time: new Date('2030-01-02T01:00:00Z') });
    await page.goto(`${base}${channel === 'regular' ? '/checkout' : channel === 'meal' ? '/meals/line-meal?pickup_at=2030-01-03T11%3A00' : '/groups/line-group'}`);
    if (channel === 'meal') await page.getByRole('button', { name: '增加隔離便當', exact: true }).click();
    const email = page.getByRole('textbox', { name: '發票通知 Email', exact: true });
    if (channel === 'meal' && await page.getByRole('button', { name: '修改發票設定', exact: true }).count()) await page.getByRole('button', { name: '修改發票設定', exact: true }).click();
    const pay = page.getByRole('button', { name: /^(前往線上付款|預訂並前往線上付款|參加並前往付款)$/ });
    await email.waitFor();
    if (saved) await page.waitForFunction(() => document.querySelector('input[type=email]')?.value === 'saved@example.com');
    else {
      assert.equal(await email.inputValue(), '');
      assert.equal(writes.length, 0);
      assert.equal(await pay.isEnabled(), false);
      await email.fill('checkout@example.com');
    }
    if (channel === 'regular') {
      await page.getByRole('combobox', { name: '取貨地點', exact: true }).selectOption('pickup-campus');
      assert.equal(await page.locator('.summary-lines').getByText('教育學院大樓', { exact: true }).count(), 1);
    }
    await pay.click();
    await page.waitForURL(`${base}/isolated-line-payment`);
    assert.deepEqual(writes.map((entry) => entry.path), [orderPath, '/v1/orders/line-order/payment-attempts']);
    assert.equal(writes[0].body.contact_email, saved ? 'saved@example.com' : 'checkout@example.com');
    assert.equal(writes[0].body.invoice_buyer_email, writes[0].body.contact_email);
    if (channel === 'regular') assert.equal(writes[0].body.pickup_location_id, 'pickup-campus');
    assert.deepEqual(unknown, []);
    assert.deepEqual(errors, []);
  });
}
