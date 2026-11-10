import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText).toString('base64')}`;
const quantity = dataUrl(await readFile(new URL('../web/src/lib/cart-quantity.ts', import.meta.url), 'utf8'));
const source = (await readFile(new URL('../web/src/lib/cart-account.ts', import.meta.url), 'utf8')).replace('"./cart-quantity"', JSON.stringify(quantity));
const { cartStorageKey, createAccountCartStore } = await import(dataUrl(source));
const { createBrowserStorage } = await import(dataUrl(await readFile(new URL('../web/src/lib/browser-storage.ts', import.meta.url), 'utf8')));
const product = id => ({ id, stock_quantity: 100, unit: '瓶' });
const item = (id, quantity) => ({ product: product(id), quantity });
function fixture(entries = []) {
  const values = new Map(entries);
  const native = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const storage = createBrowserStorage(() => native);
  return { values, native, storage, store: createAccountCartStore(storage), items: owner => JSON.parse(values.get(cartStorageKey(owner)) || '{"items":[]}').items };
}
const setItems = (store, items) => store.update(store.getSnapshot().scope, () => items);

test('A登出後訪客與B不顯示A購物車，重新登入A恢復原份數', () => {
  const { store } = fixture();
  store.setOwner('A'); setItems(store, [item('private-A', 2)]);
  store.setOwner(null); assert.deepEqual(store.getSnapshot().items, []);
  store.setOwner('B'); assert.deepEqual(store.getSnapshot().items, []);
  setItems(store, [item('private-B', 1)]);
  store.setOwner('A'); assert.deepEqual(store.getSnapshot().items, [item('private-A', 2)]);
  store.setOwner('B'); assert.deepEqual(store.getSnapshot().items, [item('private-B', 1)]);
});
test('新訪客加入商品後登入，與本人既有購物車合併且不超過份數上限', () => {
  const { store } = fixture([[cartStorageKey('A'), JSON.stringify({ items: [item('same', 998), item('account', 1)] })]]);
  store.setOwner(null); setItems(store, [item('same', 3), item('guest', 2)]);
  store.setOwner('A'); assert.deepEqual(store.getSnapshot().items, [item('same', 999), item('account', 1), item('guest', 2)]);
  store.setOwner(null); store.setOwner('A'); assert.deepEqual(store.getSnapshot().items, [item('same', 999), item('account', 1), item('guest', 2)]);
});
test('登入完整換頁後仍可接續新訪客cart，而非只有同一React生命週期可合併', () => {
  const { store, storage } = fixture();
  store.setOwner(null); setItems(store, [item('guest', 2)]);
  const loginPage = createAccountCartStore(storage); loginPage.setOwner('A');
  assert.deepEqual(loginPage.getSnapshot().items, [item('guest', 2)]);
  const reloaded = createAccountCartStore(storage); reloaded.setOwner('A');
  assert.deepEqual(reloaded.getSnapshot().items, [item('guest', 2)]);
});
test('前一個帳號與登出前的延遲callback均不可清掉目前cart，即使同商品同份數', () => {
  const { store } = fixture();
  store.setOwner('A'); setItems(store, [item('same', 2)]); const oldA = store.getSnapshot().scope;
  store.setOwner('B'); setItems(store, [item('same', 2)]);
  assert.equal(store.update(oldA, () => []), false); assert.deepEqual(store.getSnapshot().items, [item('same', 2)]);
  store.setOwner(null); store.setOwner('A');
  assert.equal(store.update(oldA, () => []), false); assert.deepEqual(store.getSnapshot().items, [item('same', 2)]);
});
test('同帳號另一分頁改量後，本頁新增使用最新數量，其他帳號事件不混入畫面', () => {
  const { store, storage } = fixture(); store.setOwner('A'); setItems(store, [item('same', 1)]);
  const another = createAccountCartStore(storage); another.setOwner('A'); setItems(another, [item('same', 4)]);
  store.update(store.getSnapshot().scope, current => [item('same', current[0].quantity + 1)]);
  assert.equal(store.getSnapshot().items[0].quantity, 5);
  another.setOwner('B'); setItems(another, [item('private-B', 2)]); store.syncStorage(cartStorageKey('B'));
  assert.deepEqual(store.getSnapshot().items, [item('same', 5)]);
});
test('storage事件僅同步目前帳號，不能用舊in-memory cart覆寫新分頁結果', () => {
  const { store, values } = fixture(); store.setOwner('A'); setItems(store, [item('same', 1)]);
  values.set(cartStorageKey('A'), JSON.stringify({ items: [item('same', 3)] })); store.syncStorage(cartStorageKey('A'));
  assert.deepEqual(store.getSnapshot().items, [item('same', 3)]);
});
test('舊無主slf_cart保留原值，不在訪客或任何帳號顯示或自動歸戶', () => {
  const legacy = JSON.stringify([item('unknown-owner', 3)]);
  const { store, values } = fixture([['slf_cart', legacy]]);
  for (const owner of [null, 'A', null, 'B']) { store.setOwner(owner); assert.deepEqual(store.getSnapshot().items, []); }
  assert.equal(values.get('slf_cart'), legacy);
});
test('訪客清除失敗仍能靠合併receipt防止重整重複加入', () => {
  const { store, storage, native, items } = fixture(); store.setOwner(null); setItems(store, [item('guest', 2)]);
  const write = native.setItem; native.setItem = (key, value) => { if (key === cartStorageKey(null)) throw new DOMException('Blocked', 'QuotaExceededError'); write(key, value); };
  store.setOwner('A'); assert.deepEqual(items('A'), [item('guest', 2)]);
  native.setItem = write;
  const reloadedStorage = createBrowserStorage(() => native), reload = createAccountCartStore(reloadedStorage); reload.setOwner('A');
  assert.deepEqual(reload.getSnapshot().items, [item('guest', 2)]);
});
test('localStorage不可寫時同頁仍可購物，重整不破壞原帳號cart', () => {
  const { store, native, values } = fixture(); store.setOwner('A'); setItems(store, [item('same', 2)]); const original = values.get(cartStorageKey('A'));
  native.setItem = () => { throw new DOMException('Blocked', 'QuotaExceededError'); };
  setItems(store, [item('same', 4)]); assert.deepEqual(store.getSnapshot().items, [item('same', 4)]); assert.equal(values.get(cartStorageKey('A')), original);
  store.update(store.getSnapshot().scope, current => [item('same', current[0].quantity + 1)]); assert.equal(store.getSnapshot().items[0].quantity, 5);
});
test('無效儲存紀錄不會變成可提交cart，合法紀錄仍保留', () => {
  const { store } = fixture([[cartStorageKey('A'), JSON.stringify({ items: [null, { product: null }, item('valid', 2), item('negative', -1), item('decimal', 1.5)] })]]);
  store.setOwner('A'); assert.deepEqual(store.getSnapshot().items, [item('valid', 2)]);
});

test('會員cart寫入失敗時保留訪客原值，重新載入後能重試轉移而不漏單', () => {
  const { store, native, values } = fixture(); store.setOwner(null); setItems(store, [item('guest', 2)]);
  const originalGuest = values.get(cartStorageKey(null)); const write = native.setItem;
  native.setItem = (key, value) => { if (key === cartStorageKey('A')) throw new DOMException('Blocked', 'QuotaExceededError'); write(key, value); };
  store.setOwner('A'); assert.deepEqual(store.getSnapshot().items, [item('guest', 2)]); assert.equal(values.get(cartStorageKey(null)), originalGuest);
  native.setItem = write; const reload = createAccountCartStore(createBrowserStorage(() => native)); reload.setOwner('A');
  assert.deepEqual(reload.getSnapshot().items, [item('guest', 2)]);
});
