import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const source = (await readFile(new URL('../web/src/lib/checkout-navigation.ts', import.meta.url), 'utf8'))
  .replace('import { router } from "../router";', 'const router = { navigate: (...args) => globalThis.handoffTest.navigate(...args) };')
  .replace('import { captureSessionIdentityGuard } from "./api";', 'const captureSessionIdentityGuard = () => { const identity = globalThis.handoffTest.identity; return () => globalThis.handoffTest.identity === identity; };');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const navigation = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

for (const name of ['openOrderHandoff', 'openMembershipHandoff', 'openActivityHandoff']) {
  test(`${name} 先建立返回歷史，身分穩定才開付款頁`, async () => {
    const calls = [];
    globalThis.handoffTest = { identity: 'user-a', navigate: async (...args) => calls.push({ kind: 'return', args }) };
    globalThis.window = { location: { assign: url => calls.push({ kind: 'provider', url }) } };
    await navigation[name]('https://payment.example.test', 'subject', name === 'openActivityHandoff' ? 'attempt' : undefined);
    assert.deepEqual(calls.map(call => call.kind), ['return', 'provider']);
    assert.equal(calls[0].args[0].replace, true);
  });
  test(`${name} 返回頁等待期間身分切換，不開上一帳號付款頁`, async () => {
    const urls = [];
    let release;
    globalThis.handoffTest = { identity: 'user-a', navigate: () => new Promise(resolve => { release = resolve; }) };
    globalThis.window = { location: { assign: url => urls.push(url) } };
    const pending = navigation[name]('https://payment.example.test', 'subject', name === 'openActivityHandoff' ? 'attempt' : undefined);
    globalThis.handoffTest.identity = 'signed-out';
    release();
    await assert.rejects(pending, /登入身分已變更/);
    assert.deepEqual(urls, []);
  });
}
