import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const compile = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText).toString('base64')}`;
const storageUrl = compile(await readFile(new URL('../web/src/lib/browser-storage.ts', import.meta.url), 'utf8'));
const helperSource = (await readFile(new URL('../web/src/lib/group-submission.ts', import.meta.url), 'utf8')).replace('from "./browser-storage"', `from "${storageUrl}"`);
const { prepareGroupSubmission, readGroupSubmission, clearGroupSubmission, groupSubmissionWasRejected } = await import(compile(helperSource));
const { createBrowserStorage } = await import(storageUrl);
const commerceSource = (await readFile(new URL('../web/src/lib/commerce.ts', import.meta.url), 'utf8')).replace('import { apiFetch } from "./api";', 'const apiFetch = () => { throw new Error("Unexpected real HTTP request"); };');
const { beginGroupCheckout } = await import(compile(commerceSource));
const quote = { confirmation_fingerprint: 'a'.repeat(64), membership_type: 'member', quantity: 2, unit_price: 100, product_subtotal: 200, shipping_fee: 0, amount_total: 200 };
const input = { campaignId: 'campaign-a', quantity: 2, contactEmail: 'buyer@example.com', invoicePreference: { buyerType: 'personal', buyerEmail: 'buyer@example.com', carrierType: 'cloud', saveCarrier: true }, fulfillment: { kind: 'pickup', pickupLocationId: 'pickup-a' } };
const display = { title: '原共同購買', pickupLocationName: '原取貨點' };
function memory(values = new Map()) { return createBrowserStorage(() => ({ getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) })); }

test('同user同campaign重整沿用完整凍結原單，其他user/campaign隔離且不保saveCarrier', () => {
  const values = new Map();
  const first = prepareGroupSubmission('a', input, quote, display, memory(values), () => 'original-key');
  const retry = prepareGroupSubmission('a', { ...input, quantity: 9, invoicePreference: { ...input.invoicePreference, buyerEmail: 'changed@example.com' } }, { ...quote, amount_total: 900 }, { title: '錯誤新標題' }, memory(values), () => 'must-not-create-new');
  assert.deepEqual(retry, first);
  assert.equal(retry.input.invoicePreference.saveCarrier, undefined);
  assert.equal(retry.input.expectedTotal, 200);
  assert.equal(retry.input.expectedQuoteFingerprint, quote.confirmation_fingerprint);
  assert.equal(readGroupSubmission('b', 'campaign-a', memory(values)), null);
  assert.equal(readGroupSubmission('a', 'campaign-b', memory(values)), null);
  clearGroupSubmission('a', 'campaign-a', 'wrong-key', memory(values));
  assert.deepEqual(readGroupSubmission('a', 'campaign-a', memory(values)), first);
  clearGroupSubmission('a', 'campaign-a', 'original-key', memory(values));
  assert.equal(readGroupSubmission('a', 'campaign-a', memory(values)), null);
});

test('blocked storage零持久化時阻擋，允許後仍是原UUID原完整shipping/invoice', () => {
  const values = new Map(); let blocked = true;
  const storage = createBrowserStorage(() => ({ getItem: key => values.get(key) ?? null, setItem: (key, value) => { if (blocked) throw new Error('Quota'); values.set(key, value); }, removeItem: key => values.delete(key) }));
  const shipping = { ...input, fulfillment: { kind: 'shipping', channel: 'home_delivery', temperature: 'ambient', recipientName: '原收件人', recipientPhone: '0900000000', shippingAddress: '原配送地址' } };
  const shippingQuote = { ...quote, shipping_fee: 60, amount_total: 260 };
  assert.throws(() => prepareGroupSubmission('a', shipping, shippingQuote, display, storage, () => 'stable-key'), /無法儲存共同購買送單識別/);
  assert.equal(values.size, 0);
  blocked = false;
  const pending = prepareGroupSubmission('a', { ...input, quantity: 8 }, quote, display, storage, () => 'wrong-new-key');
  assert.equal(pending.requestId, 'stable-key');
  assert.deepEqual(pending.input.fulfillment, shipping.fulfillment);
  assert.equal(pending.input.expectedTotal, 260);
  assert.deepEqual(pending.input.invoicePreference, { buyerType: 'personal', buyerEmail: 'buyer@example.com', carrierType: 'cloud' });
});

test('已有無法讀取的原送單或缺新quote fingerprint，不能悄覆寫成新請求', () => {
  const values = new Map([['slf_group_submission:a:campaign-a', '{broken']]);
  assert.throws(() => prepareGroupSubmission('a', input, quote, display, memory(values)), /無法讀取/);
  assert.equal(values.get('slf_group_submission:a:campaign-a'), '{broken');
  assert.throws(() => prepareGroupSubmission('a', input, { ...quote, confirmation_fingerprint: undefined }, display, memory()), /報價識別尚未確認/);
});

test('權威quote_changed即使之前unknown也可重新確認；plain409/422、key/identity衝突不丟unknown', () => {
  const error = (status, code = null) => Object.assign(new Error('不解析detail文字'), { status, code });
  assert.equal(groupSubmissionWasRejected(error(409, 'quote_changed')), true);
  assert.equal(groupSubmissionWasRejected(error(422), true), true);
  for (const problem of [error(409), error(409, 'idempotency_conflict'), error(422), error(401), error(429), error(503)]) assert.equal(groupSubmissionWasRejected(problem), false);
});

test('group join送三個保護欄位，正常pickup直接付款，shipping quote含運但初始order不含運仍可物流', async () => {
  for (const shipping of [false, true]) {
    const calls = [];
    const outcome = await beginGroupCheckout({ ...input, clientRequestId: 'uuid', expectedTotal: shipping ? 260 : 200, expectedQuoteFingerprint: quote.confirmation_fingerprint,
      fulfillment: shipping ? { kind: 'shipping', channel: 'home_delivery', temperature: 'ambient', recipientName: '原收件人', recipientPhone: '0900000000', shippingAddress: '原地址' } : input.fulfillment }, {
      joinCampaign: async (id, body) => { calls.push(['join', id, body]); return { id: 'original-order', amount_total: 200, payment_status: 'pending', revision: 1 }; },
      createPaymentAttempt: async id => { calls.push(['payment', id]); return { payment_url: 'https://payment.example.test' }; },
      createLogisticsSelection: async (id, body) => { calls.push(['logistics', id, body]); return { selection_url: 'https://logistics.example.test' }; },
    });
    assert.equal(outcome.kind, 'redirect');
    assert.equal(outcome.step, shipping ? 'logistics' : 'payment');
    assert.equal(calls[0][2].client_request_id, 'uuid');
    assert.equal(calls[0][2].expected_total, shipping ? 260 : 200);
    assert.equal(calls[0][2].expected_quote_fingerprint, quote.confirmation_fingerprint);
    assert.equal(calls.length, 2);
  }
});

test('group原單stale/uncertain/改量/已付/已取消先回Orders，不能再建立金流或物流', async () => {
  for (const change of [{ group_contract_changed: true }, { group_payment_uncertain: true }, { revision: 2 }, { payment_status: 'paid' }, { fulfillment_status: 'cancelled' }, { amount_total: 220 }]) {
    const calls = [];
    const outcome = await beginGroupCheckout({ ...input, expectedTotal: 200 }, {
      joinCampaign: async () => ({ id: 'known-order', amount_total: 200, payment_status: 'pending', revision: 1, ...change }),
      createPaymentAttempt: async () => { calls.push('payment'); throw new Error('must not pay'); },
      createLogisticsSelection: async () => { calls.push('logistics'); throw new Error('must not select'); },
    });
    assert.equal(outcome.kind, 'resume');
    assert.equal(outcome.order.id, 'known-order');
    assert.deepEqual(calls, []);
  }
});

test('group join後換身分不得用新帳號接付款，正常金流故障仍保留已知原單', async () => {
  let current = true;
  const gateway = {
    joinCampaign: async () => { current = false; return { id: 'a-order', amount_total: 200, payment_status: 'pending' }; },
    createPaymentAttempt: async () => { throw new Error('must not pay as B'); },
    createLogisticsSelection: async () => { throw new Error('must not select as B'); },
  };
  await assert.rejects(beginGroupCheckout(input, gateway, () => current), /登入身分已變更/);
  const result = await beginGroupCheckout(input, { ...gateway, joinCampaign: async () => ({ id: 'known-order', amount_total: 200, payment_status: 'pending' }), createPaymentAttempt: async () => { throw new Error('金流暫停'); } });
  assert.equal(result.kind, 'resume'); assert.equal(result.order.id, 'known-order');
});
