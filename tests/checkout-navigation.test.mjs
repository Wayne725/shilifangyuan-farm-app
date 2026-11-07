import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`;
const handoffSource = (await readFile(new URL('../web/src/lib/order-handoff.ts', import.meta.url), 'utf8'))
  .replace('import { subscribeSession } from "./api";', 'const subscribeSession = () => () => {};');
const handoffUrl = moduleUrl(handoffSource);
const { getOrderHandoff } = await import(handoffUrl);
const source = (await readFile(new URL('../web/src/lib/checkout-navigation.ts', import.meta.url), 'utf8'))
  .replace('import { router } from "../router";', 'const router = { navigate: (...args) => globalThis.handoffTest.navigate(...args), buildLocation: target => ({ href: target.to + "?" + new URLSearchParams(target.search) }) };')
  .replace('import { captureSessionIdentityGuard } from "./api";', 'const captureSessionIdentityGuard = () => { const identity = globalThis.handoffTest.identity; return () => globalThis.handoffTest.identity === identity; };')
  .replace('import { flushSync } from "react-dom";', 'const flushSync = callback => callback();')
  .replace('"./order-handoff"', JSON.stringify(handoffUrl));
const navigation = await import(moduleUrl(source));
const browserWindow = assign => Object.assign(new EventTarget(), { location: { assign } });

for (const name of ['openOrderHandoff', 'openMembershipHandoff', 'openActivityHandoff']) {
  test(`${name} 先建立返回歷史，身分穩定才開付款頁`, async () => {
    const calls = [];
    globalThis.handoffTest = { identity: 'user-a', navigate: async (...args) => calls.push({ kind: 'return', args }) };
    globalThis.window = browserWindow(url => calls.push({ kind: 'provider', url }));
    await navigation[name]('https://payment.example.test', 'subject', name === 'openActivityHandoff' ? 'attempt' : undefined);
    assert.deepEqual(calls.map(call => call.kind), ['return', 'provider']);
    assert.equal(calls[0].args[0].replace, true);
    if (name === 'openOrderHandoff') assert.equal(getOrderHandoff().returnHref, '/orders?order_id=subject');
    window.dispatchEvent(new Event('pageshow'));
  });
  test(`${name} 返回頁等待期間身分切換，不開上一帳號付款頁`, async () => {
    const urls = [];
    let release;
    globalThis.handoffTest = { identity: 'user-a', navigate: () => new Promise(resolve => { release = resolve; }) };
    globalThis.window = browserWindow(url => urls.push(url));
    const pending = navigation[name]('https://payment.example.test', 'subject', name === 'openActivityHandoff' ? 'attempt' : undefined);
    globalThis.handoffTest.identity = 'signed-out';
    release();
    await assert.rejects(pending, /登入身分已變更/);
    assert.deepEqual(urls, []);
    assert.equal(getOrderHandoff(), null);
  });
}

for (const phase of ['navigate', 'assign']) {
  test(`訂單交接${phase}同步失敗後恢復頁面，不留下永久等待`, async () => {
    globalThis.handoffTest = { identity: 'user-a', navigate: async () => {
      if (phase === 'navigate') throw new Error('navigation failed');
    } };
    globalThis.window = browserWindow(() => { throw new Error('navigation failed'); });
    await assert.rejects(navigation.openOrderHandoff('https://payment.example.test', 'subject'), /navigation failed/);
    assert.equal(getOrderHandoff(), null);
  });
}

test('訂單交接期間拒絕第二個付款回呼，保留原訂單返回位置', async () => {
  let release;
  const urls = [];
  globalThis.handoffTest = { identity: 'user-a', navigate: () => new Promise(resolve => { release = resolve; }) };
  globalThis.window = browserWindow(url => urls.push(url));
  const first = navigation.openOrderHandoff('https://payment.example.test/one', 'one');
  await assert.rejects(navigation.openOrderHandoff('https://payment.example.test/two', 'two'), /交接/);
  assert.equal(getOrderHandoff().returnHref, '/orders?order_id=one');
  release();
  await first;
  assert.deepEqual(urls, ['https://payment.example.test/one']);
  window.dispatchEvent(new Event('pageshow'));
});
