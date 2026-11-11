import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const source = await readFile(new URL('../web/src/lib/walkInCatalog.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { walkInCatalogItems } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('手動開票帶入便當售價與一般商品一般價，保留目錄稅別', () => {
  assert.deepEqual(walkInCatalogItems([{ id: 'meal-1', name: '雞胸便當', is_active: true, price: 149, tax_type: 'taxable' }], 'meal'),
    [{ id: 'meal-1', name: '雞胸便當', label: '雞胸便當', unitPrice: '149', memberPrice: undefined, taxType: 'taxable' }]);
  assert.deepEqual(walkInCatalogItems([{ id: 'rice-1', name: '白米', is_active: true, member_price: 90, nonmember_price: 100, tax_type: 'tax_exempt' }], 'regular'),
    [{ id: 'rice-1', name: '白米', label: '白米', unitPrice: '100', memberPrice: 90, taxType: 'tax_exempt' }]);
});

test('手動開票保留一元商品，缺價或不合法價格交由管理員輸入', () => {
  for (const price of [undefined, null, 0, -1, 1.5, 1_000_001, NaN, Infinity, '120']) {
    assert.equal(walkInCatalogItems([{ id: 'meal-1', name: '缺價便當', is_active: true, price }], 'meal')[0].unitPrice, '');
  }
  assert.equal(walkInCatalogItems([{ id: 'meal-2', name: '一元商品', is_active: true, price: 1 }], 'meal')[0].unitPrice, '1');
});

test('停用品項不提供選擇，啟用的同名商品各自保留識別碼、售價與目錄順序', () => {
  const items = walkInCatalogItems([
    { id: 'disabled', name: '停用', is_active: false, price: 10 },
    { id: 'meal-1', slug: 'chicken-a', name: '雞胸便當', is_active: true, price: 149 },
    { id: 'meal-2', slug: 'chicken-b', name: '雞胸便當', is_active: true, price: 159 },
    { id: 'meal-3', name: '蔬食便當', is_active: true, price: 120 },
  ], 'meal');
  assert.deepEqual(items.map(item => [item.id, item.label, item.unitPrice]), [
    ['meal-1', '雞胸便當（chicken-a）', '149'],
    ['meal-2', '雞胸便當（chicken-b）', '159'],
    ['meal-3', '蔬食便當', '120'],
  ]);
});
