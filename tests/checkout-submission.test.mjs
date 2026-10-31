import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const compile = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText).toString('base64')}`;
const storageUrl = compile(await readFile(new URL('../web/src/lib/browser-storage.ts', import.meta.url), 'utf8'));
const source = (await readFile(new URL('../web/src/lib/checkout-submission.ts', import.meta.url), 'utf8')).replace('from "./browser-storage"', `from "${storageUrl}"`);
const { prepareCheckoutSubmission, readCheckoutSubmission, clearCheckoutSubmission, checkoutWasRejected } = await import(compile(source));
const { createBrowserStorage } = await import(storageUrl);
const commerceSource = (await readFile(new URL('../web/src/lib/commerce.ts', import.meta.url), 'utf8')).replace('import { apiFetch } from "./api";', 'const apiFetch = () => { throw new Error("Unexpected HTTP request"); };');
const { beginCheckout } = await import(compile(commerceSource));
const input = { items: [{ product_id: 'rice', quantity: 1 }], expectedTotal: 100, contactEmail: 'buyer@example.com', invoicePreference: { buyerType: 'personal', buyerEmail: 'buyer@example.com', carrierType: 'cloud', saveCarrier: true }, fulfillment: { kind: 'pickup', pickupLocationId: 'store' } };
const quote = { amount_total: 100, items: [{ product_id: 'rice', product_name: '白米', quantity: 1, subtotal: 100 }] };

test('未知結果跨重整沿用相同帳號的原始請求，不被新表單覆寫', () => {
  const values = new Map();
  const storage = () => createBrowserStorage(() => ({ getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }));
  const first = prepareCheckoutSubmission('buyer-a', input, quote, storage(), () => 'request-a');
  const retry = prepareCheckoutSubmission('buyer-a', { ...input, expectedTotal: 200, items: [{ product_id: 'rice', quantity: 2 }] }, { ...quote, amount_total: 200 }, storage(), () => 'wrong-new-id');
  assert.deepEqual(retry, first);
  assert.equal(retry.input.invoicePreference.saveCarrier, undefined);
  assert.equal(readCheckoutSubmission('buyer-b', storage()), null);
  clearCheckoutSubmission('buyer-a', 'wrong-id', storage());
  assert.deepEqual(readCheckoutSubmission('buyer-a', storage()), first);
  clearCheckoutSubmission('buyer-a', 'request-a', storage());
  assert.equal(readCheckoutSubmission('buyer-a', storage()), null);
});

test('無法持久化時先阻擋送單，恢復儲存權限後沿用同一識別', () => {
  const values = new Map();
  let blocked = true;
  const storage = createBrowserStorage(() => ({
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => { if (blocked) throw new Error('Blocked'); values.set(key, value); },
    removeItem: key => values.delete(key),
  }));
  assert.throws(() => prepareCheckoutSubmission('buyer', input, quote, storage, () => 'stable-id'), /瀏覽器無法儲存送單識別/);
  assert.throws(() => prepareCheckoutSubmission('buyer', input, quote, storage, () => 'wrong-id'), /瀏覽器無法儲存送單識別/);
  blocked = false;
  assert.equal(prepareCheckoutSubmission('buyer', input, quote, storage, () => 'another-wrong-id').requestId, 'stable-id');
  assert.ok(values.get('slf_checkout_submission:buyer'));
});

test('逾時、伺服器錯誤及身分／識別衝突保留原送單，明確價格拒絕可重新確認', () => {
  const error = (status, message = '失敗') => Object.assign(new Error(message), { status });
  for (const reason of [error(500), error(503), error(401), error(429), error(409, '此送單識別碼已使用，請重新確認訂單'), error(409, '登入身分已變更，請確認目前帳號後重新操作。')]) assert.equal(checkoutWasRejected(reason), false);
  assert.equal(checkoutWasRejected(error(409, '價格已變動，請重新確認訂單明細')), true);
  assert.equal(checkoutWasRejected(error(422)), true);
});

test('恢復已改量改價或已付款原單，不以舊摘要直接進入金流', async () => {
  for (const changes of [{ amount_total: 150 }, { revision: 2 }, { payment_status: 'paid' }, { fulfillment_status: 'cancelled' }]) {
    const calls = [];
    const order = { id: 'original-order', amount_total: 100, revision: 1, payment_status: 'pending', fulfillment_status: 'unfulfilled', ...changes };
    const gateway = {
      createOrder: async body => { calls.push(body); return order; },
      createPaymentAttempt: async () => { throw new Error('Must not create a payment'); },
      createLogisticsSelection: async () => { throw new Error('Must not recreate logistics'); },
    };
    const outcome = await beginCheckout({ ...input, clientRequestId: 'same-request' }, gateway);
    assert.equal(outcome.kind, 'resume');
    assert.equal(outcome.order.id, order.id);
    assert.equal(calls[0].client_request_id, 'same-request');
    assert.equal(calls[0].expected_total, 100);
  }
});

test('正常新單與舊版呼叫仍可付款；付款服務中斷回同一單續辦', async () => {
  for (const guarded of [false, true]) for (const failPayment of [false, true]) {
    const calls = [];
    const gateway = {
      createOrder: async body => { calls.push(body); return { id: 'created-order', amount_total: 100, revision: 1, payment_status: 'pending' }; },
      createPaymentAttempt: async id => { assert.equal(id, 'created-order'); if (failPayment) throw new Error('金流暫停'); return { payment_url: 'https://payment.example.test' }; },
    };
    const legacy = { ...input };
    delete legacy.expectedTotal;
    const outcome = await beginCheckout(guarded ? { ...input, clientRequestId: 'request' } : legacy, gateway);
    assert.equal(outcome.kind, failPayment ? 'resume' : 'redirect');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].expected_total, guarded ? 100 : undefined);
  }
});
