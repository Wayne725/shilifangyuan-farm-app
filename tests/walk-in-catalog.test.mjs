import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const source = await readFile(new URL('../web/src/lib/walkInCatalog.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { walkInCatalogItems } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('手動開票帶入便當售價與一般商品一般價，保留目錄稅別', () => {
  assert.deepEqual(walkInCatalogItems([{ name: '雞胸便當', is_active: true, price: 149, tax_type: 'taxable' }], 'meal'),
    [{ name: '雞胸便當', unitPrice: '149', taxType: 'taxable' }]);
  assert.deepEqual(walkInCatalogItems([{ name: '白米', is_active: true, member_price: 90, nonmember_price: 100, tax_type: 'tax_exempt' }], 'regular'),
    [{ name: '白米', unitPrice: '100', taxType: 'tax_exempt' }]);
});

test('手動開票保留一元商品，缺價或不合法價格交由管理員輸入', () => {
  for (const price of [undefined, null, 0, -1, 1.5, 1_000_001, NaN, Infinity, '120']) {
    assert.equal(walkInCatalogItems([{ name: '缺價便當', is_active: true, price }], 'meal')[0].unitPrice, '');
  }
  assert.equal(walkInCatalogItems([{ name: '一元商品', is_active: true, price: 1 }], 'meal')[0].unitPrice, '1');
});

test('停用品項不提供手動開票選擇，同名商品沿用第一筆且保留目錄順序', () => {
  const items = walkInCatalogItems([
    { name: '停用', is_active: false, price: 10 },
    { name: '雞胸便當', is_active: true, price: 149 },
    { name: '雞胸便當', is_active: true, price: 159 },
    { name: '蔬食便當', is_active: true, price: 120 },
  ], 'meal');
  assert.deepEqual(items.map(item => [item.name, item.unitPrice]), [['雞胸便當', '149'], ['蔬食便當', '120']]);
});
