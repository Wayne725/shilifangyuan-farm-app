import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const source = await readFile(new URL('../web/src/components/StorefrontStatus.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports, require: () => ({}) });
const { storefrontCapabilities, storefrontOrderBlockReason } = exports;

const pickup = { orders_open: true, sales_scope: 'pickup', regular_orders_enabled: true, group_orders_enabled: true, shipping_enabled: false };
const mealsOnly = { orders_open: true, sales_scope: 'meals_only', regular_orders_enabled: false, group_orders_enabled: false, shipping_enabled: false };

test('自取範圍開放一般商品與共同購買，配送保持停用', () => {
  assert.equal(storefrontOrderBlockReason(pickup, 'regular'), '');
  assert.equal(storefrontOrderBlockReason(pickup, 'group'), '');
  assert.equal(storefrontCapabilities(pickup).shipping, false);
});

test('舊 API 與初次載入沒有能力欄位時不誤封鎖購物', () => {
  for (const status of [undefined, { orders_open: true }]) {
    assert.equal(storefrontOrderBlockReason(status, 'regular'), '');
    assert.equal(storefrontOrderBlockReason(status, 'group'), '');
    assert.equal(storefrontCapabilities(status).shipping, true);
  }
});

test('便當限定與平台暫停會提供當下阻擋原因', () => {
  assert.equal(storefrontOrderBlockReason(mealsOnly, 'regular'), '目前未開放一般商品訂購');
  assert.equal(storefrontOrderBlockReason(mealsOnly, 'group'), '目前未開放共同購買訂購');
  assert.equal(storefrontOrderBlockReason({ ...pickup, orders_open: false, closed_message: '盤點暫停' }, 'regular'), '盤點暫停');
});

test('便當限定驗收品只允許白名單中單一商品一件，暫停接單仍生效', () => {
  const status = { ...mealsOnly, limited_product_ids: ['test-product-a', 'test-product-b'] };
  assert.equal(storefrontOrderBlockReason(status, 'regular', [{ product_id: 'test-product-a', quantity: 1 }]), '');
  assert.equal(storefrontOrderBlockReason(status, 'regular', [{ product_id: 'test-product-b', quantity: 1 }]), '');
  for (const items of [[], [{ product_id: 'test-product-a', quantity: 2 }], [{ product_id: 'ordinary', quantity: 1 }], [{ product_id: 'test-product-a', quantity: 1 }, { product_id: 'test-product-b', quantity: 1 }]]) {
    assert.equal(storefrontOrderBlockReason(status, 'regular', items), '目前未開放一般商品訂購');
  }
  assert.equal(storefrontOrderBlockReason(status, 'group', [{ product_id: 'test-product-a', quantity: 1 }]), '目前未開放共同購買訂購');
  assert.equal(storefrontOrderBlockReason({ ...status, orders_open: false, closed_message: '暫停接單' }, 'regular', [{ product_id: 'test-product-a', quantity: 1 }]), '暫停接單');
});
