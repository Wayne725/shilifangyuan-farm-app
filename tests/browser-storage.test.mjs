import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const source = await readFile(new URL('../web/src/lib/browser-storage.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { createBrowserStorage, readStoredJson, writeStoredJson } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString('base64')}`);

test('storage getter封鎖時，同頁購物草稿仍能讀寫與清理', () => {
  const storage = createBrowserStorage(() => { throw new DOMException('Blocked', 'SecurityError'); });
  assert.equal(writeStoredJson(storage, 'checkout-draft', { quantity: 2 }), false);
  assert.deepEqual(readStoredJson(storage, 'checkout-draft', null), { quantity: 2 });
  assert.equal(storage.removeItem('checkout-draft'), false);
  assert.equal(readStoredJson(storage, 'checkout-draft', null), null);
});

test('部分寫入／移除失敗不覆蓋記憶體新值，也不清掉其他既有cart與Email', () => {
  const values = new Map([['cart', '[{"quantity":2}]'], ['email', 'remembered@example.test'], ['old-token', 'old']]);
  const storage = createBrowserStorage(() => ({
    getItem: key => values.get(key) ?? null,
    setItem: () => { throw new Error('Quota'); },
    removeItem: () => { throw new Error('Blocked'); },
  }));
  storage.removeItem('old-token');
  assert.equal(storage.getItem('old-token'), null);
  assert.deepEqual(readStoredJson(storage, 'cart', []), [{ quantity: 2 }]);
  assert.equal(storage.getItem('email'), 'remembered@example.test');
  assert.equal(writeStoredJson(storage, 'cart', [{ quantity: 3 }]), false);
  assert.deepEqual(readStoredJson(storage, 'cart', []), [{ quantity: 3 }]);
  assert.equal(values.get('cart'), '[{"quantity":2}]');
});

test('可用storage照常持久化；讀取暫時失敗時沿用最後已讀的同頁值', () => {
  const values = new Map();
  let blocked = false;
  const storage = createBrowserStorage(() => ({
    getItem: key => { if (blocked) throw new Error('Blocked'); return values.get(key) ?? null; },
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  }));
  assert.equal(writeStoredJson(storage, 'draft', { id: 'same-request' }), true);
  assert.deepEqual(readStoredJson(storage, 'draft', null), { id: 'same-request' });
  blocked = true;
  assert.deepEqual(readStoredJson(storage, 'draft', null), { id: 'same-request' });
  assert.equal(storage.removeItem('draft'), true);
  assert.equal(readStoredJson(storage, 'draft', null), null);
});

test('無法讀取既有值時不得用空白記憶體草稿覆寫原購物車', () => {
  const values = new Map([['cart', '[{"quantity":2}]']]);
  let blocked = true;
  const storage = createBrowserStorage(() => ({
    getItem: key => { if (blocked) throw new Error('Blocked'); return values.get(key) ?? null; },
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  }));
  assert.equal(storage.getItem('cart'), null);
  assert.equal(writeStoredJson(storage, 'cart', [{ quantity: 1 }]), false);
  assert.equal(values.get('cart'), '[{"quantity":2}]');
  blocked = false;
  assert.equal(writeStoredJson(storage, 'cart', [{ quantity: 1 }]), false);
  assert.equal(values.get('cart'), '[{"quantity":2}]');
  assert.equal(writeStoredJson(storage, 'new-request', { id: 'stable-request' }), true);
});
