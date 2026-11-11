import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
const ts = createRequire(new URL('../web/package.json', import.meta.url))('typescript');
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`;
const readSource = path => readFile(new URL(`../web/src/${path}`, import.meta.url), 'utf8');
const [canonicalSource, storageSource, groupSource, bootstrapSource] = await Promise.all([
  readSource('lib/canonical-entry.ts'), readSource('lib/browser-storage.ts'),
  readSource('lib/group-submission.ts'), readSource('bootstrap.ts'),
]);
const { canonicalEntryUrl } = await import(moduleUrl(canonicalSource));
const { hasGroupSubmissionForPath, prepareGroupSubmission } = await import(moduleUrl(
  groupSource.replace('from "./browser-storage"', `from "${moduleUrl(storageSource)}"`),
));
const source = compile(bootstrapSource
  .replace('import { canonicalEntryUrl } from "./lib/canonical-entry";', '')
  .replace('import { hasGroupSubmissionForPath } from "./lib/group-submission";', '')
  .replace('void import("./main");', 'window.loadedMain = true;')).replace(/export \{\};?/, '');
const legacyOrigin = 'https://shilifangyuan-web.onrender.com';
const canonicalOrigin = 'https://shilifangyuan.com';

function browserStorage(entries) {
  const storage = Object.fromEntries(entries);
  Object.defineProperty(storage, 'getItem', { value: key => storage[key] ?? null });
  return storage;
}

function bootstrap(entries, { sessionEntries = [], path = '/shop?tab=products' } = {}) {
  const { href, pathname } = new URL(legacyOrigin + path);
  const window = {
    localStorage: browserStorage(entries),
    sessionStorage: browserStorage(sessionEntries),
    location: { href, pathname, replace: url => { window.redirected = url; } },
  };
  vm.runInNewContext(source, { window, canonicalEntryUrl, hasGroupSubmissionForPath });
  return window;
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

function pendingGroupEntries() {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); return true; },
  };
  prepareGroupSubmission('buyer-a', {
    campaignId: 'campaign-a', quantity: 2, contactEmail: 'buyer@example.test',
    invoicePreference: { buyerType: 'personal', buyerEmail: 'buyer@example.test' },
    fulfillment: { kind: 'pickup', pickupLocationId: 'pickup-a' },
  }, {
    confirmation_fingerprint: 'a'.repeat(64), membership_type: 'member',
    quantity: 2, unit_price: 100, product_subtotal: 200, shipping_fee: 0, amount_total: 200,
  }, { title: '共同購買' }, storage, () => 'original-request');
  return [...values];
}

test('舊網址重整同一團購時保留尚待確認的送單', () => {
  for (const path of ['/groups/campaign-a', '/groups/%63ampaign-a/']) {
    const result = bootstrap([], { path, sessionEntries: pendingGroupEntries() });
    assert.equal(result.redirected, undefined);
    assert.equal(result.loadedMain, true);
  }
});

test('舊網址只有對應的團購詳細頁才會保留送單', () => {
  for (const path of ['/groups/campaign-b', '/groups', '/groups/campaign-a/other', '/groups/%E0%A4%A', '/groups/campaign-a%2Fother', '/shop?tab=products']) {
    const result = bootstrap([], { path, sessionEntries: pendingGroupEntries() });
    assert.equal(result.redirected, canonicalOrigin + path);
    assert.equal(result.loadedMain, undefined);
  }
});

test('鍵名、團購識別或送單內容無效時，舊網址仍導向主網域', () => {
  const [[key, saved]] = pendingGroupEntries();
  const wrongCampaign = JSON.parse(saved);
  wrongCampaign.input.campaignId = 'campaign-b';
  const path = '/groups/campaign-a';
  for (const entry of [
    ['slf_group_submission:buyer-a:other:campaign-a', saved],
    ['slf_group_submission::campaign-a', saved],
    [key, '{broken'],
    [key, JSON.stringify(wrongCampaign)],
  ]) {
    const result = bootstrap([], { path, sessionEntries: [entry] });
    assert.equal(result.redirected, canonicalOrigin + path);
    assert.equal(result.loadedMain, undefined);
  }
});
