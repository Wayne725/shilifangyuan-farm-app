import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

const sha = value => createHash('sha256').update(String(value)).digest('hex');
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,100}$/.test(value) ? value : null;
const knownPaths = new Set(['/checkout', '/orders', '/isolated-recovery-payment', '/v1/auth/refresh', '/v1/auth/logout', '/v1/platform', '/v1/pickup-locations', '/v1/orders', '/v1/orders/quote', '/v1/shipping-rates']);
const methods = new Set(['Network.requestWillBeSent', 'Network.requestIntercepted', 'Network.responseReceived', 'Network.loadingFinished', 'Network.loadingFailed', 'Page.frameNavigated', 'Page.frameDetached', 'Page.frameStartedLoading', 'Page.frameStoppedLoading', 'Page.loadEventFired', 'Page.domContentEventFired', 'Console.messageAdded', 'Target.targetCreated', 'Target.targetDestroyed', 'Target.didCommitProvisionalTarget', 'Playwright.pageProxyCreated', 'Playwright.pageProxyDestroyed', 'Playwright.provisionalLoadFailed']);
const enumValue = (value, allowed) => allowed.includes(value) ? value : 'other';

export function safeUrl(value, assets = new Set()) {
  if (value === 'about:blank') return { path: 'about:blank' };
  if (typeof value !== 'string' || value.length > 4096) return { url_allowed: false };
  let parsed;
  try { parsed = new URL(value); } catch { return { url_allowed: false, url_sha256: sha(value) }; }
  const path = parsed.pathname;
  if (parsed.origin !== 'http://127.0.0.1:4239' || parsed.username || parsed.password
    || !(knownPaths.has(path) || /^\/v1\/orders\/recovery-\d{1,3}(?:\/payment-attempts)?$/.test(path) || assets.has(path))) {
    return { url_allowed: false, url_sha256: sha(value) };
  }
  return { path, ...(parsed.searchParams.get('own_only') === 'true' ? { own_only: true } : {}) };
}

export function safeText(value, assets = new Set()) {
  const text = String(value || '');
  const result = { text_sha256: sha(text), text_bytes: Buffer.byteLength(text) };
  const match = text.match(/^Fetch API cannot load (http:\/\/127\.0\.0\.1:4239\/\S+) due to access control checks\.$/);
  if (match) {
    const url = safeUrl(match[1], assets);
    return { ...result, classification: 'fetch_access_control', ...url, text: url.path ? `Fetch API cannot load [loopback]${url.path} due to access control checks.` : undefined };
  }
  if (text.includes('due to access control checks.') && /\/v1\/orders\/recovery-\d{1,3}\?own_only=true/.test(text)) {
    return { ...result, classification: 'fetch_access_control_split_pageerror', text: 'Fetch API detail request rejected due to access control checks.' };
  }
  if (['Load request cancelled', 'Load request canceled', 'The operation was aborted.', 'TypeError: Load failed', 'Load failed'].includes(text)) {
    return { ...result, classification: 'known_load_or_cancel', text };
  }
  if (/^Origin http:\/\/127\.0\.0\.1:4239 is not allowed by Access-Control-Allow-Origin\. Status code: \d{3}\.$/.test(text)) {
    return { ...result, classification: 'known_loopback_cors_origin', text: text.replace('http://127.0.0.1:4239', '[loopback]') };
  }
  for (const [pattern, classification] of [[/content security policy|connect-src/i, 'security_csp'], [/Access-Control-Allow-Origin|cross[- ]origin/i, 'security_cors'], [/mixed content|insecure content/i, 'security_mixed_content'], [/not allowed to (?:load|request)/i, 'security_not_allowed_resource']]) {
    if (pattern.test(text)) return { ...result, classification };
  }
  return { ...result, classification: 'other_hashed_text' };
}

export function filterProtocol(direction, object, assets = new Set(), envelope = {}, depth = 0) {
  if (direction !== 'receive' || !object || typeof object !== 'object' || depth > 2) return null;
  const scope = { ...envelope };
  for (const key of ['pageProxyId', 'sessionId', 'targetId']) {
    const value = identifier(object[key]);
    if (value) scope[key] = value;
  }
  if (object.method === 'Target.dispatchMessageFromTarget') {
    const data = object.params;
    if (!data || typeof data.message !== 'string') return null;
    if (data.message.length > 2 * 1024 * 1024) return { kind: 'observer_gap', reason: 'nested_packet_size_limit' };
    const targetId = identifier(data.targetId);
    let nested;
    try { nested = JSON.parse(data.message); } catch { return { kind: 'observer_fault', fault: 'invalid_nested_protocol_json' }; }
    return filterProtocol(direction, nested, assets, { ...scope, ...(targetId ? { targetId } : {}) }, depth + 1);
  }
  if (!methods.has(object.method)) return null;
  const params = object.params || {};
  const result = { kind: 'protocol', method: object.method, ...scope };
  for (const key of ['requestId', 'frameId', 'loaderId', 'targetId', 'oldTargetId', 'newTargetId']) {
    const value = identifier(params[key]);
    if (value) result[key] = value;
  }
  if (object.method === 'Console.messageAdded') {
    const message = params.message || {};
    if (message.level !== 'error') return null;
    return { ...result, level: 'error', source: enumValue(message.source, ['javascript', 'security', 'network', 'console-api', 'xml', 'other']), ...safeText(message.text, assets), location: safeUrl(message.url, assets) };
  }
  if (params.request) {
    Object.assign(result, safeUrl(params.request.url, assets));
    result.http_method = enumValue(params.request.method, ['GET', 'POST', 'OPTIONS', 'HEAD']);
  } else if (params.response) {
    Object.assign(result, safeUrl(params.response.url, assets));
    if (Number.isInteger(params.response.status) && params.response.status >= 100 && params.response.status <= 599) result.status = params.response.status;
  } else if (params.url) Object.assign(result, safeUrl(params.url, assets));
  if (params.frame) {
    for (const key of ['id', 'parentId', 'loaderId']) {
      const value = identifier(params.frame[key]);
      if (value) result[`frame_${key}`] = value;
    }
    Object.assign(result, safeUrl(params.frame.url, assets));
  }
  if (params.targetInfo) {
    const value = identifier(params.targetInfo.targetId);
    if (value) result.targetId = value;
    if (typeof params.targetInfo.isProvisional === 'boolean') result.isProvisional = params.targetInfo.isProvisional;
    result.target_type = enumValue(params.targetInfo.type, ['page', 'worker', 'service-worker']);
  }
  if (params.errorText) result.failure = safeText(params.errorText, assets);
  if (typeof params.canceled === 'boolean') result.canceled = params.canceled;
  if (params.errorType) result.error_type = enumValue(params.errorType, ['Cancellation', 'AccessControl', 'General', 'Timeout']);
  if (params.type) result.resource_type = enumValue(params.type, ['Document', 'XHR', 'Fetch', 'Script', 'Stylesheet', 'StyleSheet', 'Image', 'Font', 'Other']);
  return result;
}

export function createObserver({ directory, assets }) {
  const allowedAssets = new Set(assets), events = [], issues = [], ownership = [];
  const started = process.hrtime.bigint();
  let launchCount = 0, installed = false, publicAttached = 0;
  const record = data => {
    if (events.length >= 12000) { if (!issues.includes('event_limit')) issues.push('event_limit'); return; }
    events.push({ received_ms: Number(process.hrtime.bigint() - started) / 1e6, ...data });
  };
  const observe = callback => { try { callback(); } catch (error) { issues.push(`observer_${error?.constructor?.name || 'Error'}`); } };
  const save = () => writeFileSync(`${directory}/protocol.json`, JSON.stringify({ kind: 'farm-wk01-protocol-observation-v1', platform: process.platform, arch: process.arch, node: process.version, clock: 'Node hrtime receipt; not native creation or wire time', launchCount, installed, publicAttached, issues, ownership, headers_cookies_post_bodies_recorded: false, browser_fetch_history_or_waits_injected: false, raw_debug_logs_recorded: false, events }, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return {
    install(webkit) {
      if (installed || typeof webkit?._connection?.toImpl !== 'function') throw new Error('WK01 unavailable in-process seam');
      const server = webkit._connection.toImpl(webkit), original = server?.launch;
      if (typeof original !== 'function' || !Function.prototype.toString.call(original).includes('helper.debugProtocolLogger(protocolLogger)')) throw new Error('WK01 incompatible server launch seam');
      server.launch = function(progress, options, existingLogger) {
        launchCount += 1;
        if (launchCount !== 1 || existingLogger) throw new Error('WK01 requires exactly one unmodified launch logger');
        return original.call(this, progress, options, (direction, message) => observe(() => {
          const data = filterProtocol(direction, message, allowedAssets);
          if (data) {
            if (data.kind === 'observer_fault' || data.kind === 'observer_gap') issues.push(data.fault || data.reason);
            record(data);
          }
        }));
      };
      installed = true;
      process.once('exit', () => { try { save(); } catch { process.exitCode = 1; } });
    },
    noteBrowser(browser) {
      const impl = browser?._connection?.toImpl(browser);
      const child = impl?.options?.browserProcess?.process;
      if (!Number.isInteger(child?.pid) || child.pid <= 1) throw new Error('WK01 browser process ownership unavailable');
      const entry = { browser_pid: child.pid, owner_node_pid: process.pid, launcher_exit_observed: false };
      ownership.push(entry);
      writeFileSync(`${directory}/ownership.json`, JSON.stringify({ browser_pid: child.pid, owner_node_pid: process.pid, kind: 'farm-wk01-launch-ownership-v1' }) + '\n', { flag: 'wx', mode: 0o600 });
      child.once('exit', (code, signal) => { entry.launcher_exit_observed = true; entry.exit_code = code; entry.signal = signal; });
    },
    attachPublic(page) {
      publicAttached += 1;
      const ids = new WeakMap(); let next = 0;
      const requestData = request => {
        if (!ids.has(request)) ids.set(request, ++next);
        return { public_request_id: ids.get(request), http_method: enumValue(request.method(), ['GET', 'POST', 'OPTIONS', 'HEAD']), ...safeUrl(request.url(), allowedAssets) };
      };
      page.on('request', request => observe(() => record({ kind: 'public', event: 'request', ...requestData(request), navigation: request.isNavigationRequest() })));
      page.on('response', response => observe(() => record({ kind: 'public', event: 'response', ...requestData(response.request()), status: response.status() })));
      page.on('requestfailed', request => observe(() => record({ kind: 'public', event: 'requestfailed', ...requestData(request), failure: safeText(request.failure()?.errorText, allowedAssets) })));
      page.on('framenavigated', frame => observe(() => { if (frame === page.mainFrame()) record({ kind: 'public', event: 'mainframe_navigated', ...safeUrl(frame.url(), allowedAssets) }); }));
      page.on('console', message => observe(() => { if (message.type() === 'error') record({ kind: 'public', event: 'console_error', ...safeText(message.text(), allowedAssets), location: safeUrl(message.location()?.url, allowedAssets) }); }));
      page.on('pageerror', error => observe(() => record({ kind: 'public', event: 'pageerror', ...safeText(`${error.name}: ${error.message}`, allowedAssets) })));
    },
  };
}
