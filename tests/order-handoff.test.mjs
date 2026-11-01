import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
globalThis.handoffSessionListeners = new Set();
globalThis.window = new EventTarget();
const source = (await readFile(new URL('../web/src/lib/order-handoff.ts', import.meta.url), 'utf8'))
  .replace('import { subscribeSession } from "./api";', `const subscribeSession = listener => {
    globalThis.handoffSessionListeners.add(listener);
    return () => globalThis.handoffSessionListeners.delete(listener);
  };`);
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { beginOrderHandoff, getOrderHandoff, subscribeOrderHandoff } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('付款返回解除交接，移除舊身份監聽', () => {
  const snapshots = [];
  const unsubscribe = subscribeOrderHandoff(() => snapshots.push(getOrderHandoff()));
  const current = beginOrderHandoff('/orders?order_id=one');
  assert.equal(getOrderHandoff().returnHref, '/orders?order_id=one');
  assert.equal(current.isCurrent(), true);
  window.dispatchEvent(new Event('pageshow'));
  assert.equal(getOrderHandoff(), null);
  assert.equal(current.isCurrent(), false);
  assert.equal(handoffSessionListeners.size, 0);
  assert.equal(snapshots.length, 2);
  unsubscribe();
});

test('拒絕重疊交接，保留先前已取得的付款頁', () => {
  const first = beginOrderHandoff('/orders?order_id=one');
  assert.throws(() => beginOrderHandoff('/orders?order_id=two'), /交接/);
  assert.equal(getOrderHandoff().returnHref, '/orders?order_id=one');
  assert.equal(first.isCurrent(), true);
  first.release();
});

test('舊交接的清理不解除下一次交接', () => {
  const first = beginOrderHandoff('/orders?order_id=one');
  const oldIdentityListener = [...handoffSessionListeners][0];
  first.release();
  const second = beginOrderHandoff('/orders?order_id=two');
  first.release();
  oldIdentityListener(null, true);
  assert.equal(getOrderHandoff().returnHref, '/orders?order_id=two');
  assert.equal(second.isCurrent(), true);
  second.release();
  assert.equal(handoffSessionListeners.size, 0);
});

test('憑證續期保留交接，登出或換身份解除交接', () => {
  const current = beginOrderHandoff('/orders?order_id=one');
  for (const listener of handoffSessionListeners) listener({ id: 'buyer' }, false);
  assert.equal(current.isCurrent(), true);
  for (const listener of [...handoffSessionListeners]) listener(null, true);
  assert.equal(getOrderHandoff(), null);
  assert.equal(handoffSessionListeners.size, 0);
});
