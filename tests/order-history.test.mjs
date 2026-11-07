import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { flattenHistoryPages, nextHistoryCursor, normalizeHistoryQuery, orderHistoryPath, orderNeedsRefresh, replaceHistoryOrder } from '../web/src/lib/order-history.ts';

test('本人一般歷史與餐點都明確限25，搜尋Unicode正規化且完整cursor同送', () => {
  const cursor = { createdAt: '2030-01-02T03:04:05.123456Z', id: 'same-time-b' };
  const uri = new URL(orderHistoryPath('general', ' ＡＢＣ\t商品　門市 ', 'paid', cursor), 'http://127.0.0.1');
  assert.deepEqual(Object.fromEntries(uri.searchParams), { limit: '25', own_only: 'true', history_scope: 'general', q: 'abc 商品 門市', payment_status: 'paid', before_created_at: cursor.createdAt, before_id: cursor.id });
  const meal = new URL(orderHistoryPath('meal', '', '', null), 'http://127.0.0.1');
  assert.equal(meal.pathname, '/v1/meal-orders');
  assert.deepEqual(Object.fromEntries(meal.searchParams), { limit: '25' });
  assert.equal(normalizeHistoryQuery(' ＡＢＣ　ΟΣ \n'), 'abc ος');
});

test('游標只取最後actual page最後一筆；不足25與忽略limit的legacy大array停止續頁', () => {
  const page = Array.from({ length: 25 }, (_, i) => ({ id: `id-${i}`, created_at: '2030-01-01T00:00:00Z' }));
  assert.deepEqual(nextHistoryCursor(page), { createdAt: '2030-01-01T00:00:00Z', id: 'id-24' });
  assert.equal(nextHistoryCursor(page.slice(0, 24)), undefined);
  assert.equal(nextHistoryCursor([]), undefined);
  assert.equal(nextHistoryCursor([...page, { id: 'legacy-extra', created_at: '2030-01-01T00:00:00Z' }]), undefined);
});

test('跨頁依id去重，不改動actual pages及完整cursor參數', () => {
  const a = { id: 'a' }, b = { id: 'b' }, c = { id: 'c' };
  const pages = [[a, b], [b, c]];
  assert.deepEqual(flattenHistoryPages(pages), [a, b, c]);
  assert.deepEqual(pages, [[a, b], [b, c]]);
});

const terminal = { id: 'a', payment_status: 'paid', fulfillment_status: 'picked_up', invoice_status: 'issued' };
test('收款／取餐／發票／退款／物流／核對未終結持續更新，終結才停止', () => {
  assert.equal(orderNeedsRefresh(terminal), false);
  for (const value of [
    { payment_status: 'pending' }, { payment_status: 'refund_pending' }, { payment_status: 'late_paid_refund_required' },
    { fulfillment_status: 'ready_for_pickup' }, { fulfillment_status: 'preparing' },
    { invoice_status: 'pending' }, { invoice_status: 'failed' }, { invoice_status: 'void_pending' },
    { group_payment_uncertain: true }, { cancellation_request: { status: 'pending' } }, { shipment: { status: 'in_transit' } },
  ]) assert.equal(orderNeedsRefresh({ ...terminal, ...value }), true, JSON.stringify(value));
  for (const invoice_status of ['issued', 'voided', 'not_eligible', 'external']) {
    assert.equal(orderNeedsRefresh({ ...terminal, invoice_status, payment_status: 'refunded', fulfillment_status: 'cancelled', shipment: { status: 'cancelled' } }), false);
  }
});

test('單筆更新只替換命中id，保留pageParams、頁長及actual末筆供下一游標', () => {
  const a = { ...terminal }, b = { ...terminal, id: 'b' };
  const data = { pages: [[a], [b]], pageParams: [null, { id: 'a', createdAt: 'original' }] };
  const changed = { ...b, invoice_status: 'void_pending' };
  const updated = replaceHistoryOrder(data, changed);
  assert.strictEqual(updated.pages[0][0], a);
  assert.strictEqual(updated.pages[1][0], changed);
  assert.strictEqual(updated.pageParams, data.pageParams);
  assert.strictEqual(replaceHistoryOrder(data, { ...terminal, id: 'absent' }), data);
  assert.strictEqual(data.pages[1][0], b);
});

test('改量回應能局部更新InfiniteData一般與餐點頁，不破壞其他頁與pageParams', async () => {
  const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
  const source = (await readFile(new URL('../web/src/lib/order-quantity-amendments.ts', import.meta.url), 'utf8')).replace('import { campaignTimestamp } from "./group-campaigns";', '');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
  const { replaceAmendedOrder } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
  const general = { id: 'a', items: [{ id: 'line-a', quantity: 1 }] };
  const meal = { id: 'b', meal_event_title: '午餐', items: [{ order_item_id: 'line-b', quantity: 1 }] };
  const other = { id: 'c', items: [] };
  const data = { pages: [[general], [meal, other]], pageParams: [null, { id: 'cursor' }] };
  const updatedMeal = { id: 'b', revision: 2, amount_total: 200, items: [{ id: 'line-b', quantity: 2, unit_price: 100, subtotal: 200 }] };
  const next = replaceAmendedOrder(data, updatedMeal, {});
  assert.equal(next.pages[1][0].items[0].quantity, 2);
  assert.equal(next.pages[1][0].amount_total, 200);
  assert.deepEqual(next.pages[0][0], general);
  assert.deepEqual(next.pages[1][1], other);
  assert.strictEqual(next.pageParams, data.pageParams);
});
