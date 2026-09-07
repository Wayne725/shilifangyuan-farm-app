import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const { chromium, webkit } = createRequire(import.meta.url)("playwright");
const base = process.env.BROWSER_TEST_URL || "http://127.0.0.1:4185";
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
  throw new Error('Browser acceptance must use an isolated local website');
}
const engine = process.env.BROWSER_ENGINE === "webkit" ? webkit : chromium;

const order = {
  id: 'a78d9cc4-b305-4ed7-a880-dc24f385e255', order_number: 'SLF-RETURN-TEST',
  order_kind: 'regular', sales_channel: 'regular', fulfillment_method: 'cooperative_pickup',
  payment_status: 'pending', invoice_status: 'not_eligible', fulfillment_status: 'unfulfilled',
  amount_total: 10, tax_amount: 0, created_at: '2026-09-05T08:00:00Z',
  available_actions: ['pay', 'cancel'], items: [{ product_name: '隔離測試商品', quantity: 1, unit_price: 10, subtotal: 10, unit_label: '份' }],
};

for (const entry of ['orders', 'checkout']) {
test(`${entry} 付款上一頁、下一頁與重整都接續原訂單，不重複建單或要求登入`, async () => {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    if (entry === 'checkout') {
      await context.addInitScript(() => {
        if (!sessionStorage.getItem('test_cart_initialized')) {
          localStorage.setItem('slf_cart', JSON.stringify([{ quantity: 1, product: {
            id: 'product-test', name: '隔離測試商品', slug: 'test', description: '', category: '測試',
            unit: '份', member_price: 10, nonmember_price: 10, stock_quantity: 10,
            tax_type: 'tax_exempt', can_ship: false, allowed_shipping_channels: [], is_active: true,
          } }]));
          sessionStorage.setItem('test_cart_initialized', 'yes');
        }
      });
    }
    let paymentCreates = 0;
    let orderCreates = 0;
    const requests = [];
    await context.route('**/*', (route) => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push(`${request.method()} ${url.hostname}${url.pathname}`);
      if (url.origin === 'https://payment.example.test') {
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><meta charset="utf-8"><h1>隔離金流付款頁</h1>' });
      }
      if (url.pathname.startsWith('/v1/')) {
        let json = [];
        if (url.pathname === '/v1/auth/refresh') json = { access_token: 'isolated-token', user: { id: 'buyer', email: 'buyer@example.com', display_name: '測試買家', user_role: 'customer' } };
        if (url.pathname === '/v1/orders') {
          if (request.method() === 'POST') orderCreates++;
          json = request.method() === 'POST' ? order : [order];
        }
        if (url.pathname === '/v1/orders/quote') json = { items: [{ product_id: 'product-test', product_name: '隔離測試商品', quantity: 1, subtotal: 10 }], amount_total: 10, membership_type: 'nonmember' };
        if (url.pathname === '/v1/pickup-locations') json = [{ id: 'pickup-test', name: '隔離取貨點', address: '測試地址', is_active: true }];
        if (url.pathname.endsWith('/payment-attempts')) {
          paymentCreates++;
          json = { payment_url: 'https://payment.example.test/checkout' };
        }
        return route.fulfill({ json });
      }
      if (url.origin === base) return route.continue();
      return route.abort();
    });
    const page = await context.newPage();
    await page.goto(`${base}/${entry}`);
    await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
    await page.getByRole('heading', { name: '隔離金流付款頁' }).waitFor({ timeout: 10000 }).catch(async (error) => {
      throw new Error(`${error.message}\n${JSON.stringify(requests)}\n${await page.locator('body').innerText()}`);
    });
    await page.goBack();
    await page.getByRole('button', { name: '前往線上付款', exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('order_id'), order.id);
    await page.goForward();
    await page.getByRole('heading', { name: '隔離金流付款頁' }).waitFor();
    await page.goBack();
    await page.reload();
    await page.getByRole('button', { name: '前往線上付款', exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('order_id'), order.id);
    assert.equal(await page.getByRole('dialog').count(), 0);
    assert.equal(orderCreates, entry === 'checkout' ? 1 : 0);
    assert.equal(paymentCreates, 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally {
    await browser.close();
  }
});
}

test("舊購物車網址轉入新版結帳，不顯示 404", async () => {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext();
    await context.route('**/*', (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/v1/')) {
        return route.fulfill({ status: url.pathname === '/v1/auth/refresh' ? 401 : 200, json: [] });
      }
      if (url.origin === base) return route.continue();
      return route.abort();
    });
    const page = await context.newPage();
    await page.goto(`${base}/cart`);
    await page.waitForURL(`${base}/checkout`, { timeout: 10000 });
    await page.getByText('結帳清單還是空的', { exact: true }).waitFor();
    assert.equal(await page.getByRole('heading', { name: '這一頁不存在。' }).count(), 0);
  } finally {
    await browser.close();
  }
});

test("管理端分頁搜尋、重查及重送必填原因，不使用確認視窗", async () => {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const searches = [];
    const submissions = [];
    let retried = false;
    await context.route('**/*', (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.startsWith('/v1/')) {
        let json = [];
        if (url.pathname === '/v1/auth/refresh') json = { access_token: 'isolated-admin-token', user: { id: 'admin', email: 'admin@example.com', display_name: '測試管理員', user_role: 'admin' } };
        if (url.pathname === '/v1/admin/order-search') {
          searches.push(url.searchParams.toString());
          const second = url.searchParams.get('offset') === '12' || url.searchParams.get('q') === 'SECOND';
          json = { items: [{ ...order, id: second ? 'f7b0d41f-b849-4890-979b-fc560a5e50a4' : order.id, order_number: second ? 'SLF-SECOND' : order.order_number }], total: url.searchParams.get('q') ? 1 : 24 };
        }
        if (url.pathname === '/v1/admin/failed-jobs') json = { items: retried ? [] : [{ id: 'job-test', event_type: 'send_email', attempts: 8, error: '外部服務回應逾時', retryable: true }], total: retried ? 0 : 1 };
        if (url.pathname.endsWith('/retry')) {
          retried = true;
          submissions.push(request.postDataJSON());
          json = { status: 'pending' };
        }
        if (url.pathname.endsWith('/payment/query')) {
          submissions.push(request.postDataJSON());
          json = { status: 'pending', message: '已查詢最新一次付款紀錄' };
        }
        return route.fulfill({ json });
      }
      if (url.origin === base) return route.continue();
      return route.abort();
    });
    const page = await context.newPage();
    let dialogs = 0;
    page.on('dialog', async (dialog) => { dialogs++; await dialog.dismiss(); });
    await page.goto(`${base}/admin`);
    const retry = page.getByRole('button', { name: '排入重試', exact: true });
    await retry.waitFor();
    assert.equal(await retry.isDisabled(), true);
    await page.getByLabel('處理原因', { exact: true }).fill('已修正寄信設定');
    await retry.click();
    await page.getByText('目前沒有失敗工作。', { exact: true }).waitFor();
    await page.getByRole('navigation', { name: '訂單分頁' }).getByRole('button', { name: '下一頁', exact: true }).click();
    await page.getByText('SLF-SECOND', { exact: true }).waitFor();
    await page.getByLabel('搜尋訂單', { exact: true }).fill('SECOND');
    await page.getByRole('button', { name: '搜尋', exact: true }).click();
    await page.getByText('共 1 筆 · 第 1 頁', { exact: true }).waitFor();
    const query = page.getByRole('button', { name: '重新查詢雷門付款', exact: true });
    assert.equal(await query.isDisabled(), true);
    await page.getByLabel('金流查詢原因', { exact: true }).fill('核對付款未更新');
    await query.click();
    await page.getByText('已查詢最新一次付款紀錄', { exact: true }).waitFor();
    assert.equal(dialogs, 0);
    assert.deepEqual(submissions, [{ reason: '已修正寄信設定' }, { reason: '核對付款未更新' }]);
    assert(searches.some((query) => new URLSearchParams(query).get('offset') === '12'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally {
    await browser.close();
  }
});
