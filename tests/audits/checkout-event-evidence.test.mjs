import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createCheckoutEventEvidence } from '../browser/checkout-event-evidence.mjs';

async function ownedDirectory(t) {
  const directory = await mkdtemp(join(tmpdir(), 'checkout-events-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

function fakePage() {
  const page = new EventEmitter();
  const frame = { url: () => 'http://127.0.0.1/orders?order_id=recovery-1' };
  page.mainFrame = () => frame;
  page.url = frame.url;
  return { page, frame };
}

test('checkout evidence disabled returns synchronously without touching metadata or IO', () => {
  const options = new Proxy({}, { get: (_target, name) => {
    if (name === 'directory') return '';
    throw new Error('Disabled observer touched metadata');
  } });
  assert.equal(createCheckoutEventEvidence(options), null);
});

test('events preserve native errors, active GETs and close order without reading secrets or bodies', async t => {
  const directory = await ownedDirectory(t), { page, frame } = fakePage(), messages = [];
  const evidence = createCheckoutEventEvidence({ directory, testName: '合成checkout', engine: 'webkit' });
  evidence.attach(page, () => messages);
  const forbidden = () => { throw new Error('Read forbidden headers/body'); };
  const request = { url: () => 'http://127.0.0.1/v1/orders/recovery-1?own_only=true&access_token=private-marker',
    method: () => 'GET', isNavigationRequest: () => false, failure: () => ({ errorText: 'cancelled' }), headers: forbidden, postData: forbidden };
  page.emit('request', request);
  page.emit('response', { request: () => request, status: () => 200, headers: forbidden, body: forbidden });
  page.emit('framenavigated', frame);
  await evidence.close(async () => {
    const error = new Error('native access-control message'); error.name = 'Fetch API cannot load http';
    messages.push(error.message); page.emit('pageerror', error);
    page.emit('requestfailed', request);
  });
  let assertions = 0;
  await evidence.assertAndSave(() => { assertions += 1; });
  assert.equal(assertions, 1);
  const files = await readdir(directory); assert.equal(files.length, 1);
  const text = await readFile(join(directory, files[0]), 'utf8'), value = JSON.parse(text);
  assert.ok(!text.includes('private-marker'));
  const before = value.events.find(event => event.event === 'before_browser_close');
  const after = value.events.find(event => event.event === 'after_browser_close');
  assert.equal(before.fixture_errors.length, 0); assert.equal(before.active_GETs.length, 1);
  assert.equal(after.fixture_errors.length, 1); assert.equal(after.active_GETs.length, 0);
  assert.equal(after.native_errors[0].name, 'Fetch API cannot load http');
  assert.match(after.native_errors[0].stack, /native access-control message/);
  assert.ok(value.events.findIndex(event => event.event === 'pageerror') < value.events.indexOf(after));
  assert.equal(value.browser_fetch_or_history_injected, false);
});

test('original assertion failure is persisted and rethrown by identity', async t => {
  const directory = await ownedDirectory(t);
  const evidence = createCheckoutEventEvidence({ directory, testName: 'original failure' });
  const original = new assert.AssertionError({ message: 'strict errors[] remains red' });
  await assert.rejects(evidence.assertAndSave(() => { throw original; }), error => error === original);
  const [file] = await readdir(directory);
  assert.equal(JSON.parse(await readFile(join(directory, file), 'utf8')).original_assertion_failed, true);
});

test('save failure preserves original assertion and both remain visible', async t => {
  const directory = await ownedDirectory(t), blocked = join(directory, 'not-a-directory');
  await writeFile(blocked, 'synthetic');
  const evidence = createCheckoutEventEvidence({ directory: blocked, testName: 'save failure' });
  const original = new assert.AssertionError({ message: 'original strict failure' });
  await assert.rejects(evidence.assertAndSave(() => { throw original; }), error => {
    assert.ok(error instanceof AggregateError);
    assert.equal(error.errors[0], original); assert.equal(error.errors.length, 2);
    assert.ok(error.errors[1] instanceof Error); return true;
  });
});

test('observer callback failure cannot prevent close or silently pass', async t => {
  const directory = await ownedDirectory(t), { page } = fakePage();
  const evidence = createCheckoutEventEvidence({ directory, testName: 'observer failure' });
  evidence.attach(page, () => []);
  page.emit('request', { method: () => 'GET', url: () => { throw new Error('synthetic closed request'); } });
  let closed = false;
  await evidence.close(async () => { closed = true; });
  assert.equal(closed, true);
  await assert.rejects(evidence.assertAndSave(() => {}), AggregateError);
  const [file] = await readdir(directory);
  const value = JSON.parse(await readFile(join(directory, file), 'utf8'));
  assert.ok(value.events.some(event => event.event === 'observer_error'));
  assert.equal(value.original_assertion_failed, false);
  assert.equal(value.observer_failed, true);
});
