import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const ts = createRequire(new URL('../../web/package.json', import.meta.url))('typescript');
const source = await readFile(new URL('../../web/src/lib/membership-documents.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports });
const document = { id: 'isolated-document', document_type: 'id_front', status: 'confirmed', content_type: 'image/jpeg', size_bytes: 10 };

test('明確的示範附件旗標禁止下載', () => {
  assert.equal(exports.documentAvailable({ ...document, is_demo_placeholder: true }), false);
});

test('示範附件顯示當下不能下載的原因', () => {
  assert.equal(exports.documentRetentionLabel({ ...document, is_demo_placeholder: true }), '示範資料，無實際檔案');
});

test('一般與舊API缺少旗標的已確認附件保留調閱', () => {
  for (const value of [document, { ...document, is_demo_placeholder: false }]) {
    assert.equal(exports.documentAvailable(value), true);
    assert.equal(exports.documentRetentionLabel(value), '舊資料：留存期限待確認');
  }
});

test('一般到期或刪除附件仍沿用原本阻擋與說明', () => {
  const expired = { ...document, is_demo_placeholder: false, retention_expired: true };
  assert.equal(exports.documentAvailable(expired), false);
  assert.equal(exports.documentRetentionLabel(expired), '已到期停止調閱，等待清除');
  const deleted = { ...document, status: 'deleted' };
  assert.equal(exports.documentAvailable(deleted), false);
  assert.equal(exports.documentRetentionLabel(deleted), '證件檔案已刪除');
});
