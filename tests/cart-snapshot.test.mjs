import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const source = await readFile(new URL('../web/src/lib/cart-snapshot.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { cartMatchesSubmission } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString('base64')}`);

test('送單snapshot比對合併重複商品、忽略排列及產品展示欄位', () => {
  assert.equal(cartMatchesSubmission([{ product: { id: 'b' }, quantity: 2 }, { product: { id: 'a' }, quantity: 1 }, { product: { id: 'a' }, quantity: 2 }], [{ product_id: 'a', quantity: 3 }, { product_id: 'b', quantity: 2 }]), true);
});
test('送出後新增數量或商品，不可由舊成功callback清除', () => {
  assert.equal(cartMatchesSubmission([{ product: { id: 'a' }, quantity: 3 }], [{ product_id: 'a', quantity: 2 }]), false);
  assert.equal(cartMatchesSubmission([{ product: { id: 'a' }, quantity: 2 }, { product: { id: 'b' }, quantity: 1 }], [{ product_id: 'a', quantity: 2 }]), false);
});
test('空snapshot只匹配真正空購物車', () => {
  assert.equal(cartMatchesSubmission([], []), true);
  assert.equal(cartMatchesSubmission([{ product: { id: 'a' }, quantity: 1 }], []), false);
});
