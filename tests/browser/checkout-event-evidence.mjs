import { mkdir, writeFile } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { resolve } from 'node:path';

const redact = value => String(value).replace(/([?&](?:access_token|refresh_token|token|code|state|password|secret|signature|authorization)=)[^&#\s)]+/gi, '$1[redacted]')
  .replace(/(https?:\/\/)[^/@\s]+@/g, '$1[redacted]@');
const errorData = error => ({ name: redact(error.name), message: redact(error.message), stack: redact(error.stack || '') });

export function createCheckoutEventEvidence(options) {
  if (!options.directory) return null;
  const started = process.hrtime.bigint(), events = [], nativeErrors = [], observerErrors = [], active = new Map(), ids = new WeakMap();
  const directory = resolve(options.directory);
  const file = resolve(directory, `checkout-${createHash('sha256').update(options.testName).digest('hex').slice(0, 12)}-${randomUUID()}.json`);
  let sequence = 0, page, originalErrors = () => [];
  const record = (event, detail = {}) => events.push({ received_ms: Number(process.hrtime.bigint() - started) / 1e6, event, ...detail });
  const observe = callback => {
    try { callback(); }
    catch (error) { observerErrors.push(error); record('observer_error', errorData(error)); }
  };
  const idFor = request => {
    if (!ids.has(request)) ids.set(request, ++sequence);
    return ids.get(request);
  };
  const requestData = request => ({ id: idFor(request), method: request.method(), url: redact(request.url()) });
  const snapshot = event => observe(() => record(event, {
    page_url: page ? redact(page.url()) : null,
    fixture_errors: originalErrors().map(redact),
    native_errors: nativeErrors.map(error => ({ ...error })),
    active_GETs: [...active.values()].filter(request => request.method === 'GET'),
  }));

  return {
    attach(target, readOriginalErrors) {
      page = target; originalErrors = readOriginalErrors;
      page.on('request', request => observe(() => {
        const value = requestData(request); active.set(value.id, value);
        record('request', { ...value, navigation: request.isNavigationRequest() });
      }));
      page.on('response', response => observe(() => record('response_headers', {
        ...requestData(response.request()), status: response.status(),
      })));
      page.on('requestfinished', request => observe(() => {
        const value = requestData(request); active.delete(value.id); record('request_finished', value);
      }));
      page.on('requestfailed', request => observe(() => {
        const value = requestData(request); active.delete(value.id);
        record('request_failed', { ...value, error: redact(request.failure()?.errorText || '') });
      }));
      page.on('framenavigated', frame => observe(() => {
        if (frame === page.mainFrame()) record('mainframe_navigated', { url: redact(frame.url()) });
      }));
      page.on('pageerror', error => observe(() => {
        const value = errorData(error); nativeErrors.push(value);
        record('pageerror', { ...value, page_url: redact(page.url()) });
      }));
      record('observers_attached');
    },
    async close(closeBrowser) {
      // Node starts the original first after hook after the body settles; TAP
      // remains authoritative about whether the body passed or threw.
      snapshot('before_browser_close');
      try { return await closeBrowser(); }
      catch (error) { record('browser_close_failed', errorData(error)); throw error; }
      finally { snapshot('after_browser_close'); }
    },
    async assertAndSave(assertOriginal) {
      const failures = [];
      let originalAssertionFailed = false;
      try {
        snapshot('before_original_error_assert');
        try { assertOriginal(); } catch (error) { originalAssertionFailed = true; failures.push(error); }
        if (observerErrors.length) failures.push(new AggregateError(observerErrors, 'Checkout event observer failed'));
      } finally {
        try {
          await mkdir(directory, { recursive: true });
          await writeFile(file, JSON.stringify({ schema_version: 1, test_name: options.testName, node: process.version,
            platform: process.platform, arch: process.arch, engine: options.engine, clock: 'Node process.hrtime receipt clock; not native error creation time',
            browser_fetch_or_history_injected: false, headers_cookies_or_POST_bodies_recorded: false,
            body_result_source: 'Original TAP; no body marker or extra UI wait',
            original_assertion_failed: originalAssertionFailed, observer_failed: observerErrors.length > 0, events,
          }, null, 2) + '\n', { flag: 'wx' });
        } catch (error) { failures.push(error); }
      }
      if (failures.length === 1) throw failures[0];
      if (failures.length > 1) throw new AggregateError(failures, 'Checkout assertion and/or evidence persistence failed');
    },
  };
}
