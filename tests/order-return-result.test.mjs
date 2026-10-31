import assert from 'node:assert/strict';
import test from 'node:test';
import { attemptMatchesReturnedOrder, holdReturnedOrderActions, resolveOrderReturn } from '../web/src/lib/order-return-result.ts';

const search = { orderId: 'current-order', attemptId: 'attempt', payment: 'confirming' };
const order = { id: 'current-order', revision: 2, payment_status: 'pending', fulfillment_status: 'pending_confirmation' };
const attempt = { id: 'attempt', order_id: 'current-order', order_revision: 2, superseded: false, status: 'succeeded' };

test('URL只帶意圖，不能用paid/succeeded或任意message把pending單稱為付款完成', () => {
  assert.equal(resolveOrderReturn({ orderId: order.id }, order), null);
  for (const result of ['paid', 'succeeded', '99999']) {
    const notice = resolveOrderReturn({ orderId: order.id, result, message: '已付99999' }, order);
    assert.equal(notice.kind, 'pending'); assert.equal(notice.success, false);
    assert.equal(notice.message.includes('99999'), false);
  }
});

test('本人目前Order的paid/refund/late/expired/failed狀態優先，不借URL或舊attempt覆蓋', () => {
  for (const [status, expected, success] of [['paid','paid',true],['refunded','refunded',true],['refund_pending','refund_pending',false],['late_paid_refund_required','late_paid_refund_required',false],['expired','expired',false],['failed','failed',false]]) {
    const notice = resolveOrderReturn({ ...search, result: 'paid' }, { ...order, payment_status: status }, { attempt: { ...attempt, order_revision: 1, superseded: true } });
    assert.equal(notice.kind, expected); assert.equal(notice.success, success);
    assert.equal(notice.message.includes('金流查詢確認'), false);
  }
});

test('讀取中/失敗/缺單/非目標單不稱已保存或付款完成，已知原單resume仍可續辦', () => {
  const resume = { orderId: order.id, result: 'resume' };
  assert.equal(resolveOrderReturn(resume, order).kind, 'resume');
  assert.equal(resolveOrderReturn(resume, undefined).kind, 'unconfirmed');
  assert.equal(resolveOrderReturn(resume, { ...order, id: 'other' }).kind, 'unconfirmed');
  assert.equal(resolveOrderReturn({ result: 'paid' }, { ...order, payment_status: 'paid' }).success, false);
  for (const state of [{ orderReading: true }, { orderError: true }]) {
    const notice = resolveOrderReturn({ ...resume, result: 'paid' }, { ...order, payment_status: 'paid' }, state);
    assert.equal(notice.success, false);
  }
});

test('attempt必須id/owner目標/order revision/非superseded全符，legacy缺證據不符合', () => {
  assert.equal(attemptMatchesReturnedOrder(order, attempt, search.orderId, search.attemptId), true);
  for (const patch of [{ id: 'other' }, { order_id: 'other' }, { order_revision: 1 }, { superseded: true }, { order_revision: undefined }, { superseded: undefined }, { order_id: undefined }]) {
    assert.equal(attemptMatchesReturnedOrder(order, { ...attempt, ...patch }, search.orderId, search.attemptId), false);
  }
  assert.equal(attemptMatchesReturnedOrder({ ...order, revision: undefined }, attempt, search.orderId, search.attemptId), false);
});

test('匹配succeeded仍不能把pending單說成已付款，錯單/舊revision/legacy結果只顯未確認', () => {
  const matched = resolveOrderReturn(search, order, { attempt });
  assert.equal(matched.kind, 'confirming'); assert.equal(matched.success, false);
  for (const patch of [{ order_id: 'other' }, { order_revision: 1 }, { superseded: true }, { order_revision: undefined }, { superseded: undefined }]) {
    assert.equal(resolveOrderReturn(search, order, { attempt: { ...attempt, ...patch } }).kind, 'unconfirmed');
  }
  assert.equal(resolveOrderReturn(search, order, { attempt: { ...attempt, status: 'pending', order_revision: undefined, superseded: undefined } }).kind, 'confirming');
});

test('物流selected必有當前shipment實際確認資料，取消/未選不能由URL成功', () => {
  const logistics = { orderId: order.id, logistics: 'selected', message: '付款已完成' };
  assert.equal(resolveOrderReturn(logistics, order).kind, 'unconfirmed');
  for (const status of ['draft','selection_pending','cancelled']) assert.equal(resolveOrderReturn(logistics, { ...order, shipment: { status } }).success, false);
  for (const status of ['ready_to_create','created','in_transit','delivered']) assert.equal(resolveOrderReturn(logistics, { ...order, shipment: { status } }).kind, 'selected');
});

test('付款返回讀取失敗/查詢503/錯單/舊版/缺證據不解除等待鎖', () => {
  const states = [{ attemptError: true }, { orderError: true }, { attempt: { ...attempt, status: 'failed', order_id: 'other' } },
    { attempt: { ...attempt, status: 'failed', order_revision: 1, superseded: true } },
    { attempt: { id: 'attempt', order_id: order.id, status: 'failed' } },
    { attemptError: true, attempt: { ...attempt, status: 'failed' } }, { orderError: true, attempt: { ...attempt, status: 'expired' } }];
  assert.deepEqual(states.map(state => holdReturnedOrderActions(search, order, state)), [true, true, true, true, true, true, true]);
});

test('匹配failed/expired允許原pending單續付，succeeded仍等Order真入帳', () => {
  for (const status of ['failed','expired']) assert.equal(holdReturnedOrderActions(search, order, { attempt: { ...attempt, status } }), false);
  for (const status of ['pending','succeeded','paid']) assert.equal(holdReturnedOrderActions(search, order, { attempt: { ...attempt, status } }), true);
  assert.equal(holdReturnedOrderActions(search, { ...order, payment_status: 'paid' }, { attempt }), false);
  assert.equal(holdReturnedOrderActions({ orderId: order.id, result: 'resume' }, order), false);
  assert.equal(holdReturnedOrderActions(search, { ...order, payment_status: 'unknown' }, { attempt: { ...attempt, status: 'failed' } }), true);
});
