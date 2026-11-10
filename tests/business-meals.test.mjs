import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const helper = {};
vm.runInNewContext(compile(await readFile(new URL('../web/src/lib/business-meals.ts', import.meta.url), 'utf8')), { exports: helper });
const order = { meal_booking_type: 'business', payment_status: 'pending', available_actions: ['pay'], payment_opens_at: '2030-01-01T16:00:00Z', payment_closes_at: '2030-01-02T16:00:00Z' };

test('會餐付款同時核對台北日界與 API 權限，缺時段不得付款', () => {
  assert.equal(helper.businessMealPaymentState(order, Date.parse('2030-01-01T15:59:59Z')), 'not_open');
  assert.equal(helper.businessMealPaymentState(order, Date.parse(order.payment_opens_at)), 'open');
  assert.equal(helper.businessMealPaymentState(order, Date.parse(order.payment_closes_at)), 'overdue');
  assert.equal(helper.businessMealPaymentState({ ...order, available_actions: [] }, Date.parse(order.payment_opens_at)), 'unavailable');
  assert.equal(helper.businessMealPaymentState({ ...order, payment_opens_at: null }, Date.parse(order.payment_opens_at)), 'unavailable');
  assert.equal(helper.businessMealPaymentState({ ...order, cancelled_at: '2030-01-01T00:00:00Z' }, Date.parse(order.payment_opens_at)), null);
  assert.equal(helper.businessMealPaymentState({ ...order, payment_status: 'paid' }, Date.parse(order.payment_opens_at)), null);
  assert.equal(helper.businessMealPaymentState({ ...order, meal_booking_type: 'standard' }, Date.parse(order.payment_opens_at)), null);
});

test('會餐建單強制當日開票與線上付款，不立即建立金流', async () => {
  const source = await readFile(new URL('../web/src/lib/commerce.ts', import.meta.url), 'utf8');
  for (const storedType of ['business', undefined]) {
    const calls = [], module = {};
    const apiFetch = async (path, options) => {
      calls.push({ path, body: options?.body && JSON.parse(options.body) });
      return { id: 'synthetic-business-order', meal_booking_type: storedType };
    };
    vm.runInNewContext(compile(source), { exports: module, require: () => ({ apiFetch }) });
    const outcome = await module.beginMealCheckout({ eventId: 'event', mealBookingType: 'business', paymentMethod: 'cash_on_pickup', invoiceHandling: 'on_payment',
      pickupAt: '2030-01-02T03:00:00Z', items: [{ offering_id: 'meal', quantity: 2, option_ids: [] }], contactEmail: 'buyer@example.test',
      invoicePreference: { buyerType: 'company', buyerTaxId: '15989995', buyerName: '隔離單位', buyerEmail: 'buyer@example.test', carrierType: 'cloud' } });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].body.meal_booking_type, 'business');
    assert.equal(calls[0].body.payment_method, 'online');
    assert.equal(calls[0].body.invoice_handling, 'pickup_day');
    assert.equal(outcome.kind, storedType === 'business' ? 'reserved' : 'resume');
  }
});

test('未付款會餐只在明確勾選時帶入列印條件，一般訂單不受影響', async () => {
  const module = {};
  vm.runInNewContext(compile(await readFile(new URL('../web/src/lib/order-summary.ts', import.meta.url), 'utf8')), { exports: module });
  const filters = { startsOn: '2030-01-01', endsOn: '2030-01-02', dateBasis: 'pickup', salesChannel: 'all', orderCategory: 'meal', status: 'active', category: '', search: '' };
  assert.equal(Object.hasOwn(module.orderSummaryParameters(filters), 'include_unpaid_business'), false);
  assert.equal(module.orderSummaryParameters({ ...filters, includeUnpaidBusiness: true }).include_unpaid_business, 'true');
  assert.equal(Object.hasOwn(module.orderSummaryParameters({ ...filters, orderCategory: 'general', includeUnpaidBusiness: true }), 'include_unpaid_business'), false);
});
