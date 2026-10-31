import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const ts = createRequire(new URL('../../web/package.json', import.meta.url))('typescript');
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const session = (id = 'a', token = `${id}-token`) => ({ access_token: token,
  user: { id, user_role: 'customer', membership_type: 'nonmember' } });

function originLocks() {
  const resources = new Map();
  function pump(resource) {
    if (resource.held || !resource.queue.length) return;
    const item = resource.queue.shift();
    resource.held = true;
    item.signal?.removeEventListener('abort', item.abort);
    queueMicrotask(async () => {
      try { item.resolve(await item.callback({ name: item.name, mode: 'exclusive' })); }
      catch (error) { item.reject(error); }
      finally { resource.held = false; pump(resource); }
    });
  }
  return {
    request(name, options, callback) {
      return new Promise((resolve, reject) => {
        const resource = resources.get(name) || { held: false, queue: [] };
        resources.set(name, resource);
        const item = { name, signal: options.signal, callback, resolve, reject };
        item.abort = () => { const index = resource.queue.indexOf(item); if (index >= 0) resource.queue.splice(index, 1); reject(item.signal.reason); };
        if (item.signal?.aborted) return reject(item.signal.reason);
        item.signal?.addEventListener('abort', item.abort, { once: true });
        resource.queue.push(item); pump(resource);
      });
    },
  };
}

async function loadApi(fetcher, { navigator, timers = { setTimeout, clearTimeout }, storage, broadcast } = {}) {
  const compile = source => ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const storageExports = {};
  const browser = storage ? { window: { localStorage: storage } } : {};
  vm.runInNewContext(compile(await readFile(new URL('../../web/src/lib/browser-storage.ts', import.meta.url), 'utf8')), { exports: storageExports, ...browser });
  const source = await readFile(new URL('../../web/src/lib/api.ts', import.meta.url), 'utf8');
  const exports = {};
  vm.runInNewContext(compile(source.replace('import.meta.env.VITE_API_BASE_URL', '""')), {
    exports, navigator, Headers, FormData, AbortController, DOMException, ...timers, ...browser, BroadcastChannel: broadcast,
    require(name) {
      if (name === './browser-storage') return storageExports;
      assert.equal(name, './http');
      return { fetchWithTimeout: fetcher, RequestTimeoutError: class extends Error {} };
    },
  });
  return exports;
}

for (const firstTab of ['A', 'B']) test(`${firstTab}先發起時兩獨立模組refresh仍依最新cookie代次序列化`, async () => {
  const locks = originLocks(), response = deferred();
  let cookieGeneration = 0;
  const requests = [];
  const fetcher = async (path, init) => {
    assert.equal(path, '/v1/auth/refresh');
    assert.equal(init.credentials, 'include');
    const observed = cookieGeneration;
    requests.push(observed);
    await response.promise;
    if (observed !== cookieGeneration) return new Response(null, { status: 401 });
    cookieGeneration += 1;
    return Response.json(session('a', `access-${cookieGeneration}`));
  };
  const a = await loadApi(fetcher, { navigator: { locks } }), b = await loadApi(fetcher, { navigator: { locks } });
  const restoring = firstTab === 'A' ? [a.restoreSession(), b.restoreSession()] : [b.restoreSession(), a.restoreSession()];
  await tick();
  const requestsWhileFirstResponseHeld = requests.length;
  response.resolve();
  await Promise.all(restoring);
  assert.equal(requestsWhileFirstResponseHeld, 1);
  assert.deepEqual(requests, [0, 1]);
  assert.equal(a.getAccessToken(), firstTab === 'A' ? 'access-1' : 'access-2');
  assert.equal(b.getAccessToken(), firstTab === 'B' ? 'access-1' : 'access-2');
});

test('Web Locks缺席仍保原singleflight正常登入', async () => {
  const response = deferred();
  let calls = 0;
  const api = await loadApi(async () => { calls += 1; await response.promise; return Response.json(session()); });
  const restoring = [api.restoreSession(), api.restoreSession()];
  response.resolve();
  await Promise.all(restoring);
  assert.equal(calls, 1);
  assert.equal(api.getAccessToken(), 'a-token');
});

test('排隊15秒timeout保原登入、回可重試503、不繞鎖；釋放後可恢復', async () => {
  const locks = originLocks(), hold = deferred();
  const holder = locks.request('slf-auth-refresh-v1', {}, () => hold.promise);
  await tick();
  const pendingTimers = new Map(), delays = [];
  const timers = { setTimeout(fn, delay) { const id = {}; pendingTimers.set(id, fn); delays.push(delay); return id; }, clearTimeout(id) { pendingTimers.delete(id); } };
  let calls = 0;
  const api = await loadApi(async () => { calls += 1; return Response.json(session('a', 'renewed')); }, { navigator: { locks }, timers });
  api.saveSession(session());
  const restoring = api.restoreSession();
  const rejected = assert.rejects(restoring, error => error.status === 503);
  await tick();
  assert.equal(calls, 0);
  assert.deepEqual(delays, [15000]);
  [...pendingTimers.values()].forEach(callback => callback());
  await rejected;
  assert.equal(calls, 0);
  assert.equal(api.getAccessToken(), 'a-token');
  hold.resolve(); await holder;
  await api.restoreSession();
  assert.equal(calls, 1);
  assert.equal(api.getAccessToken(), 'renewed');
});

test('已授鎖但callback排程延後時，15秒等待仍有界且晚到callback不送refresh', async () => {
  let callback, rejectLock, timerCallback, outcome;
  let calls = 0;
  const navigator = { locks: { request(name, options, granted) {
    callback = granted;
    return new Promise((resolve, reject) => { rejectLock = reject; });
  } } };
  const timers = { setTimeout(fn) { timerCallback = fn; return 1; }, clearTimeout() {} };
  const api = await loadApi(async () => { calls += 1; return Response.json(session()); }, { navigator, timers });
  api.saveSession(session());
  const restoring = api.restoreSession().catch(error => { outcome = error.status; });
  timerCallback();
  await tick();
  const resultAtDeadline = outcome;
  try { await callback(); } catch (error) { rejectLock(error); }
  await restoring;
  assert.equal(resultAtDeadline, 503);
  assert.equal(calls, 0);
  assert.equal(api.getAccessToken(), 'a-token');
});

for (const name of ['SecurityError', 'NotSupportedError']) {
  test(`原生${name}發生在callback前才保一次fallback登入`, async () => {
    const locks = { request() { throw new DOMException('native unavailable', name); } };
    let calls = 0;
    const api = await loadApi(async () => { calls += 1; return Response.json(session()); }, { navigator: { locks } });
    await api.restoreSession();
    assert.equal(calls, 1); assert.equal(api.getAccessToken(), 'a-token');
  });
}

test('原生getter SecurityError仍可fallback；普通程式錯誤不得冒充原生拒絕', async () => {
  let calls = 0;
  const blockedNavigator = { get locks() { throw new DOMException('native unavailable', 'SecurityError'); } };
  const api = await loadApi(async () => { calls += 1; return Response.json(session()); }, { navigator: blockedNavigator });
  await api.restoreSession();
  assert.equal(calls, 1);
  const broken = await loadApi(async () => { throw new Error('must not fetch'); }, {
    navigator: { locks: { request() { const error = new Error('programming error'); error.name = 'SecurityError'; throw error; } } },
  });
  await assert.rejects(broken.restoreSession(), /programming error/);
});

for (const status of [401, 503]) {
  test(`callback已執行的HTTP${status}不fallback重送且保原登入語意`, async () => {
    let calls = 0;
    const api = await loadApi(async () => { calls += 1; return Response.json({ detail: 'controlled' }, { status }); }, { navigator: { locks: originLocks() } });
    api.saveSession(session());
    if (status === 401) { assert.equal(await api.restoreSession(), null); assert.equal(api.getAccessToken(), null); }
    else { await assert.rejects(api.restoreSession(), error => error.status === 503); assert.equal(api.getAccessToken(), 'a-token'); }
    assert.equal(calls, 1);
  });
}

test('callback內network錯誤即使原始型別像SecurityError也不得重送', async () => {
  let calls = 0;
  const api = await loadApi(async () => { calls += 1; throw new DOMException('network denied', 'SecurityError'); }, { navigator: { locks: originLocks() } });
  api.saveSession(session());
  await assert.rejects(api.restoreSession(), error => error.status === 503);
  assert.equal(calls, 1); assert.equal(api.getAccessToken(), 'a-token');
});

test('慢JSON不佔cookie鎖，第二個分頁仍可完成自己的refresh', async () => {
  const locks = originLocks(), body = deferred();
  let secondFetched = false;
  const a = await loadApi(async () => ({ ok: true, status: 200, json: () => body.promise }), { navigator: { locks } });
  const b = await loadApi(async () => { secondFetched = true; return Response.json(session('a', 'second')); }, { navigator: { locks } });
  const first = a.restoreSession();
  await tick();
  const second = b.restoreSession();
  await tick();
  const progressedBeforeFirstJson = secondFetched;
  body.resolve(session('a', 'first'));
  await Promise.all([first, second]);
  assert.equal(progressedBeforeFirstJson, true);
  assert.equal(a.getAccessToken(), 'first'); assert.equal(b.getAccessToken(), 'second');
});

for (const change of ['logout', 'switch-account', 'same-user-new-credential', 'identity-update']) {
  test(`排隊時${change}後不得發起舊refresh或覆蓋新身分`, async () => {
    const locks = originLocks(), hold = deferred();
    const holder = locks.request('slf-auth-refresh-v1', {}, () => hold.promise);
    let calls = 0;
    const api = await loadApi(async () => { calls += 1; return Response.json(session('a', 'stale')); }, { navigator: { locks } });
    api.saveSession(session());
    const restoring = api.restoreSession();
    await tick();
    if (change === 'logout') api.clearSession();
    if (change === 'switch-account') api.saveSession(session('b'));
    if (change === 'same-user-new-credential') api.saveSession(session('a', 'new-login'));
    if (change === 'identity-update') api.updateSessionUser({ ...session().user, user_role: 'admin' });
    hold.resolve(); await holder;
    const result = await restoring;
    assert.equal(calls, 0);
    if (change === 'logout') { assert.equal(result, null); assert.equal(api.getAccessToken(), null); }
    if (change === 'switch-account') { assert.equal(result.user.id, 'b'); assert.equal(api.getAccessToken(), 'b-token'); }
    if (change === 'same-user-new-credential') assert.equal(api.getAccessToken(), 'new-login');
    if (change === 'identity-update') assert.equal(result.user.user_role, 'admin');
  });
}

test('A的POST在refresh排隊時切成B，原操作不會用B憑證重送', async () => {
  const locks = originLocks(), hold = deferred();
  const holder = locks.request('slf-auth-refresh-v1', {}, () => hold.promise);
  const requests = [];
  const api = await loadApi(async (path, init) => { requests.push({ path, authorization: init.headers?.get('Authorization') }); return new Response(null, { status: 401 }); }, { navigator: { locks } });
  api.saveSession(session());
  const operation = api.apiFetch('/v1/orders', { method: 'POST', body: '{}' });
  const rejection = assert.rejects(operation, error => error.status === 409);
  await tick();
  api.saveSession(session('b'));
  hold.resolve(); await holder;
  await rejection;
  assert.deepEqual(requests, [{ path: '/v1/orders', authorization: 'Bearer a-token' }]);
  assert.equal(api.getAccessToken(), 'b-token');
});

test('refresh/clearSession不把token寫入儲存或廣播，原購物車保持完整', async () => {
  const cart = '[{"product":{"id":"cart"},"quantity":2}]';
  const values = new Map([['slf_cart', cart]]), writes = [], messages = [];
  const storage = { getItem: key => values.get(key) ?? null, setItem(key, value) { writes.push(key); values.set(key, value); }, removeItem(key) { values.delete(key); } };
  class Broadcast { postMessage(message) { messages.push(message); } }
  const api = await loadApi(async () => Response.json(session('a', 'sensitive-test-token')), { navigator: { locks: originLocks() }, storage, broadcast: Broadcast });
  await api.restoreSession();
  api.clearSession();
  assert.deepEqual(writes, []);
  assert.deepEqual(messages, []);
  assert.equal(values.get('slf_cart'), cart);
  assert.equal([...values.values()].some(value => value.includes('sensitive-test-token')), false);
});
