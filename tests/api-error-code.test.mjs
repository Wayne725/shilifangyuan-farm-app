import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
test('ApiError保留HTTP錯誤code並與原兩參數caller相容', async () => {
  const source = (await readFile(new URL('../web/src/lib/api.ts', import.meta.url), 'utf8')).replace('import.meta.env.VITE_API_BASE_URL', '""');
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, {
    exports, Headers, FormData,
    require(name) {
      if (name === './browser-storage') return { localStore: { removeItem() {} } };
      assert.equal(name, './http');
      return { fetchWithTimeout: async () => Response.json({ detail: '報價已更新' }, { status: 409, headers: { 'X-Error-Code': 'quote_changed' } }), RequestTimeoutError: class extends Error {} };
    },
  });
  assert.equal(new exports.ApiError(503, '暫停').code, null);
  await assert.rejects(exports.apiFetch('/v1/group-campaigns/test/join', { method: 'POST' }), error => error.status === 409 && error.code === 'quote_changed' && error.message === '報價已更新');
});
