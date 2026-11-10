import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const canonicalSource = compile(await readFile(new URL('../web/src/lib/canonical-entry.ts', import.meta.url), 'utf8'));
const { canonicalEntryUrl } = await import(`data:text/javascript;base64,${Buffer.from(canonicalSource).toString('base64')}`);
const source = compile((await readFile(new URL('../web/src/bootstrap.ts', import.meta.url), 'utf8')).replace('import { canonicalEntryUrl } from "./lib/canonical-entry";', '').replace('void import("./main");', 'window.loadedMain = true;')).replace(/export \{\};?/, '');
function bootstrap(entries) {
  const localStorage = Object.fromEntries(entries); Object.defineProperty(localStorage, 'getItem', { value: key => localStorage[key] ?? null });
  const window = { localStorage, sessionStorage: {}, location: { href: 'https://shilifangyuan-web.onrender.com/shop?tab=products', replace: url => { window.redirected = url; } } };
  vm.runInNewContext(source, { window, canonicalEntryUrl }); return window;
}
for (const [key, value] of [
  ['slf_cart', [{ product: { id: 'legacy' }, quantity: 2 }]],
  ['slf_cart_v2:guest', { items: [{ product: { id: 'guest' }, quantity: 2 }] }],
  ['slf_cart_v2:user:A', { items: [{ product: { id: 'account' }, quantity: 2 }] }],
]) test(`${key}仍有草稿時保留原origin，不因換域造成cart遺失`, () => {
  const result = bootstrap([[key, JSON.stringify(value)]]); assert.equal(result.loadedMain, true); assert.equal(result.redirected, undefined);
});
test('所有帳號cart空白時仍依原規則導向主網域', () => {
  const result = bootstrap([['slf_cart_v2:user:A', '{"items":[]}'], ['slf_cart_v2:guest', '{"items":[]}']]);
  assert.equal(result.redirected, 'https://shilifangyuan.com/shop?tab=products'); assert.equal(result.loadedMain, undefined);
});
