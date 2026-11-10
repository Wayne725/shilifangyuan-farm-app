"""One unchanged WK01 case on native Linux; publish only three safe JSON files."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
OBSERVER = Path(__file__).with_name('wk01_protocol_observer.mjs')
FIXTURE = ROOT / 'tests/browser/checkout-recovery.mjs'
PUBLIC_HELPER = ROOT / 'tests/browser/checkout-event-evidence.mjs'
CASE = '總額相同但品項單價或稅別已變，仍須重新確認'
PINS = {
    FIXTURE: 'd807ab1166ac56a7a06d53c7235e057843dc01682ce3636f913668b0f6d9d7ed',
    PUBLIC_HELPER: '23116b4bf74c393895f98638a9da75927132a02ec5c8235fe94522258d968af7',
    OBSERVER: '1a0ad5d7767ec39322ea4df0180f5e23e5e0690fec285e555c1b1ca582a7ce4c',
}
CORE_SHA = '9393fa79e1c67c74edc26b610d65a4f7ed73d345a762465cc88340a33a2454ac'
SAFE_FILES = {'summary.json', 'timeline.json', 'manifest.json'}
KNOWN_PATHS = {'about:blank', '/checkout', '/orders', '/isolated-recovery-payment',
    '/v1/auth/refresh', '/v1/auth/logout', '/v1/platform', '/v1/pickup-locations',
    '/v1/orders', '/v1/orders/quote', '/v1/shipping-rates'}
METHODS = {'Network.requestWillBeSent', 'Network.requestIntercepted', 'Network.responseReceived',
    'Network.loadingFinished', 'Network.loadingFailed', 'Page.frameNavigated', 'Page.frameDetached',
    'Page.frameStartedLoading', 'Page.frameStoppedLoading', 'Page.loadEventFired',
    'Page.domContentEventFired', 'Console.messageAdded', 'Target.targetCreated',
    'Target.targetDestroyed', 'Target.didCommitProvisionalTarget', 'Playwright.pageProxyCreated',
    'Playwright.pageProxyDestroyed', 'Playwright.provisionalLoadFailed'}
PUBLIC_EVENTS = {'request', 'response', 'requestfailed', 'mainframe_navigated', 'console_error', 'pageerror'}
CLASSIFICATIONS = {'fetch_access_control', 'fetch_access_control_split_pageerror', 'known_load_or_cancel',
    'known_loopback_cors_origin', 'security_csp', 'security_cors', 'security_mixed_content',
    'security_not_allowed_resource', 'other_hashed_text'}
IDENTIFIERS = {'pageProxyId', 'sessionId', 'targetId', 'requestId', 'frameId', 'loaderId',
    'oldTargetId', 'newTargetId', 'frame_id', 'frame_parentId', 'frame_loaderId'}
ENUMS = {'kind': {'protocol', 'public', 'observer_gap', 'observer_fault'}, 'method': METHODS,
    'event': PUBLIC_EVENTS, 'classification': CLASSIFICATIONS, 'level': {'error'},
    'source': {'javascript', 'security', 'network', 'console-api', 'xml', 'other'},
    'http_method': {'GET', 'POST', 'OPTIONS', 'HEAD', 'other'},
    'target_type': {'page', 'worker', 'service-worker', 'other'},
    'resource_type': {'Document', 'XHR', 'Fetch', 'Script', 'Stylesheet', 'StyleSheet', 'Image', 'Font', 'Other', 'other'},
    'error_type': {'Cancellation', 'AccessControl', 'General', 'Timeout', 'other'},
    'reason': {'nested_packet_size_limit'}, 'fault': {'invalid_nested_protocol_json'}}


class Refusal(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refusal(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_time(deadline):
    require(time.monotonic() < deadline, 'deadline')


def private_write(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(data)


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16 * 1024**2, 'raw_file_unavailable')
    return json.loads(path.read_bytes())


def instrument(source, assets):
    prefix = f"import {{ createObserver }} from {json.dumps(OBSERVER.as_uri())};\n"
    changes = [
        ("const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;",
         "const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;\n"
         "const wk01Observer = createObserver({ directory: process.env.WK01_PROTOCOL_DIRECTORY, assets: "
         + json.dumps(sorted(assets)) + " });\nwk01Observer.install(webkit);"),
        ('  t.after(evidence ? () => evidence.close(() => browser.close()) : () => browser.close());',
         '  t.after(evidence ? () => evidence.close(() => browser.close()) : () => browser.close());\n  wk01Observer.noteBrowser(browser);'),
        ('  const page = await context.newPage();',
         '  const page = await context.newPage();\n  wk01Observer.attachPublic(page);'),
    ]
    text = source.decode('utf-8')
    for old, new in changes:
        require(text.count(old) == 1, 'instrumentation_anchor')
        text = text.replace(old, new, 1)
    generated = prefix + text
    recovered = generated.removeprefix(prefix)
    for old, new in reversed(changes):
        require(recovered.count(new) == 1, 'instrumentation_reverse_anchor')
        recovered = recovered.replace(new, old, 1)
    require(recovered.encode() == source, 'instrumentation_reverse_bytes')
    return generated.encode()


def source_manifest(deadline):
    entries = {}
    for path, expected in PINS.items():
        check_time(deadline)
        require(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == expected, 'source_identity')
        entries[path.relative_to(ROOT).as_posix()] = expected
    dist = ROOT / 'web/dist'
    require(dist.is_dir() and not dist.is_symlink(), 'dist_missing')
    paths = sorted(dist.rglob('*'))
    require(len(paths) <= 1500 and all(not p.is_symlink() for p in paths), 'dist_inventory')
    for path in paths:
        check_time(deadline)
        if path.is_file():
            name = path.relative_to(ROOT).as_posix()
            require(path.stat().st_size <= 32 * 1024**2 and re.fullmatch(r'[A-Za-z0-9_./-]{1,240}', name), 'dist_file')
            entries[name] = sha(path.read_bytes())
    require('web/dist/index.html' in entries, 'dist_index')
    package = (ROOT / 'node_modules/playwright').resolve(strict=True)
    core = (package.parent / 'playwright-core').resolve(strict=True)
    for folder in (package, core):
        require(folder.is_relative_to((ROOT / 'node_modules').resolve())
                and read_json(folder / 'package.json')['version'] == '1.62.1', 'SDK_version')
    require(sha((core / 'lib/coreBundle.js').read_bytes()) == CORE_SHA, 'SDK_core_identity')
    browsers = read_json(core / 'browsers.json')['browsers']
    require([b['revision'] for b in browsers if b['name'] == 'webkit'] == ['2336'], 'WebKit_revision')
    entries['playwright-core/lib/coreBundle.js'] = CORE_SHA
    return entries, {'/' + n.removeprefix('web/dist/') for n in entries if n.startswith('web/dist/')}


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    pgid: int
    sid: int
    uid: int
    start: int
    state: str

    def identity(self):
        return self.pid, self.pgid, self.sid, self.uid, self.start


def process_table():
    rows = {}
    directories = list(Path('/proc').iterdir())
    require(len(directories) <= 8192, 'proc_inventory_limit')
    for folder in directories:
        if not folder.name.isdecimal():
            continue
        try:
            fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
            row = Process(int(folder.name), int(fields[1]), int(fields[2]), int(fields[3]),
                          folder.stat().st_uid, int(fields[19]), fields[0])
            rows[row.pid] = row
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rows


class OwnedProcesses:
    """Hold pidfds before signalling; never send a numeric PID/group signal."""
    def __init__(self, node_pid):
        row = process_table().get(node_pid)
        require(row is not None and row.pgid == row.sid == node_pid and row.uid == os.getuid(), 'node_ownership')
        self.known, self.fds, self.groups, self.sessions = {}, {}, set(), set()
        self.browser_binding = None
        self.actions = {'TERM': 0, 'KILL': 0}
        self.add(row)

    def add(self, row, anchor=None):
        require(row.uid == os.getuid() and len(self.known) < 256, 'owned_process_limit')
        fd = os.pidfd_open(row.pid)
        fresh = process_table()
        current = fresh.get(row.pid)
        if (current is None or current.identity() != row.identity()
                or (anchor is not None and (anchor.pid not in fresh or fresh[anchor.pid].identity() != anchor.identity()))):
            os.close(fd)
            raise Refusal('process_identity_changed')
        self.known[row.pid], self.fds[row.pid] = row, fd
        self.groups.add(row.pgid)
        self.sessions.add(row.sid)

    def capture(self):
        rows = process_table()
        for _ in range(16):
            anchors = {pid for pid, saved in self.known.items() if pid in rows
                       and rows[pid].identity() == saved.identity()}
            parents = {pid for pid in anchors if rows[pid].state not in {'Z', 'X'}}
            sessions = {rows[pid].sid for pid in anchors}
            new = [r for r in rows.values() if r.pid not in self.known and r.state not in {'Z', 'X'}
                   and (r.ppid in parents or r.sid in sessions)]
            if not new:
                break
            for row in new:
                try:
                    parent = row.ppid if row.ppid in parents else next(pid for pid in anchors if rows[pid].sid == row.sid)
                    self.add(row, self.known[parent])
                except ProcessLookupError:
                    pass
        live = [r for r in rows.values() if (r.pgid in self.groups or r.sid in self.sessions)
                and r.state not in {'Z', 'X'}]
        unknown = [r for r in live if r.pid not in self.known or r.identity() != self.known[r.pid].identity()]
        return rows, live, unknown

    def bind_browser(self, path):
        if not path.exists():
            return
        value = read_json(path)
        require(value.get('kind') == 'farm-wk01-launch-ownership-v1'
                and all(type(value.get(k)) is int and value[k] > 1
                        for k in ('browser_pid', 'owner_node_pid')), 'browser_ownership_shape')
        rows, _, _ = self.capture()
        owner = self.known.get(value['owner_node_pid'])
        browser = self.known.get(value['browser_pid'])
        require(owner is not None, 'browser_owner_not_observed')
        require(browser is not None and browser.ppid == owner.pid and browser.state not in {'Z', 'X'}
                and browser.uid == owner.uid == os.getuid() and browser.pgid == browser.sid == browser.pid,
                'browser_live_ancestry_not_observed')
        binding = {'browser_pid': browser.pid, 'owner_node_pid': owner.pid,
                   'browser_start': browser.start, 'owner_start': owner.start}
        require(self.browser_binding is None or self.browser_binding == binding, 'browser_binding_changed')
        self.browser_binding = binding

    def cleanup(self, deadline, drain):
        errors = []
        for number, label in ((signal.SIGTERM, 'TERM'), (signal.SIGKILL, 'KILL')):
            try:
                rows, live, unknown = self.capture()
                if not live:
                    return {'live_members': 0, 'unknown_members': len(unknown), 'signal_counts': self.actions, 'errors': errors}
            except BaseException:
                errors.append('cleanup_process_observation_failed')
                rows = None
            self.signal_known(number, label, errors, rows)
            until = min(deadline, time.monotonic() + 2)
            while time.monotonic() < until:
                try:
                    drain()
                except BaseException:
                    errors.append('cleanup_drain_failed')
                    break
                try:
                    _, live, unknown = self.capture()
                except BaseException:
                    errors.append('cleanup_process_observation_failed')
                    break
                if not live:
                    return {'live_members': 0, 'unknown_members': len(unknown), 'signal_counts': self.actions, 'errors': errors}
                time.sleep(0.05)
        try:
            _, live, unknown = self.capture()
            counts = {'live_members': len(live), 'unknown_members': len(unknown)}
        except BaseException:
            errors.append('cleanup_final_observation_failed')
            counts = {'live_members': None, 'unknown_members': None}
        return {**counts, 'signal_counts': self.actions, 'errors': errors}

    def signal_known(self, number, label, errors, rows=None):
        for pid, fd in self.fds.items():
            if rows is not None:
                current = rows.get(pid)
                if current is None or current.state in {'Z', 'X'}:
                    continue
                if current.identity() != self.known[pid].identity():
                    errors.append('stale_process_identity')
                    continue
            try:
                signal.pidfd_send_signal(fd, number)
                self.actions[label] += 1
            except ProcessLookupError:
                pass
            except OSError:
                errors.append('owned_pidfd_signal_failed')

    def close(self):
        for fd in self.fds.values():
            os.close(fd)


def safe_event(value, assets):
    require(type(value) is dict, 'event_shape')
    result = {}
    for key, item in value.items():
        if key == 'text':
            continue
        if key in IDENTIFIERS:
            require(type(item) is str and len(item) <= 100, 'technical_identifier')
            result[key + '_sha256'] = sha(item.encode())
        elif key in ENUMS:
            require(type(item) is str and item in ENUMS[key], 'event_enum')
            result[key] = item
        elif key in {'location', 'failure'}:
            result[key] = safe_event(item, assets)
        elif key == 'path':
            require(type(item) is str and (item in KNOWN_PATHS or item in assets
                    or re.fullmatch(r'/v1/orders/recovery-\d{1,3}(?:/payment-attempts)?', item)), 'event_path')
            result[key] = item
        elif key in {'text_sha256', 'url_sha256'}:
            require(type(item) is str and re.fullmatch(r'[0-9a-f]{64}', item), 'event_hash')
            result[key] = item
        elif key in {'own_only', 'url_allowed', 'navigation', 'canceled', 'isProvisional'}:
            require(type(item) is bool, 'event_boolean')
            result[key] = item
        elif key in {'status', 'text_bytes', 'public_request_id'}:
            require(type(item) is int and 0 <= item <= 16 * 1024**2, 'event_integer')
            result[key] = item
        elif key == 'received_ms':
            require(type(item) in {int, float} and math.isfinite(item) and 0 <= item <= 70000, 'event_clock')
            result[key] = item
        else:
            raise Refusal('unknown_event_field')
    return result


def tap_counts(data):
    text = data.decode('utf-8', errors='replace')
    require(len(re.findall(r'^(?:not )?ok 1 - ' + re.escape(CASE) + r'$', text, re.M)) == 1, 'TAP_case_name')
    counts = {}
    for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped'):
        values = re.findall(r'^# ' + key + r' (\d+)$', text, re.M)
        require(len(values) == 1, 'TAP_summary_missing_or_ambiguous')
        counts[key] = int(values[0])
    require(counts['tests'] == 1 and counts['pass'] + counts['fail'] == 1
            and counts['cancelled'] == counts['skipped'] == 0, 'TAP_case_inventory')
    return counts


def reap_child(child, deadline, ownership_available):
    # Until wait/poll reaps this direct child, its PID cannot be reused.
    if not ownership_available and os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
        child.terminate()
    try:
        return child.wait(timeout=max(0.01, min(1, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait(timeout=max(0.01, deadline - time.monotonic()))


def evidence_projection(work, assets, returncode, browser_binding):
    protocol = read_json(work / 'protocol.json')
    require(protocol['kind'] == 'farm-wk01-protocol-observation-v1' and protocol['platform'] == 'linux'
            and protocol['arch'] == 'x64' and protocol['node'] == 'v22.23.4', 'observer_runtime')
    require(protocol['installed'] is True and protocol['launchCount'] == protocol['publicAttached'] == 1
            and protocol['issues'] == [] and len(protocol['events']) <= 12000, 'observer_coverage_gap')
    protocol_methods = {e.get('method') for e in protocol['events'] if type(e) is dict and e.get('kind') == 'protocol'}
    public_events = {e.get('event') for e in protocol['events'] if type(e) is dict and e.get('kind') == 'public'}
    require(bool(protocol_methods & {'Network.requestWillBeSent', 'Network.requestIntercepted'})
            and 'Page.frameNavigated' in protocol_methods
            and {'request', 'mainframe_navigated'} <= public_events, 'protocol_or_public_baseline_missing')
    ownership = read_json(work / 'ownership.json')
    require(browser_binding is not None and all(ownership[k] == browser_binding[k]
            for k in ('browser_pid', 'owner_node_pid')), 'browser_ownership_coverage_unknown')
    owners = protocol['ownership']
    require(len(owners) == 1 and owners[0]['browser_pid'] == ownership['browser_pid']
            and owners[0]['owner_node_pid'] == ownership['owner_node_pid'], 'observer_ownership_gap')
    require(owners[0]['launcher_exit_observed'] is True, 'launcher_exit_missing')
    code, sig = owners[0].get('exit_code'), owners[0].get('signal')
    require((type(code) is int and 0 <= code <= 255 and sig is None)
            or (code is None and sig in {'SIGTERM', 'SIGKILL', 'SIGABRT', 'SIGSEGV', 'SIGBUS',
                'SIGILL', 'SIGFPE', 'SIGTRAP', 'SIGQUIT', 'SIGHUP', 'SIGINT', 'SIGPIPE', 'SIGSYS'}), 'launcher_exit_shape')
    public_files = list((work / 'public').glob('*.json'))
    require(len(public_files) == 1, 'public_evidence_missing')
    public = read_json(public_files[0])
    require(public['test_name'] == CASE and public['node'] == 'v22.23.4' and public['platform'] == 'linux'
            and public['arch'] == 'x64' and public['engine'] == 'webkit'
            and public['observer_failed'] is False, 'public_runtime_or_observer')
    markers = [e.get('event') for e in public['events']]
    expected_markers = ['observers_attached', 'before_browser_close', 'after_browser_close', 'before_original_error_assert']
    require(all(markers.count(m) == 1 for m in expected_markers)
            and [markers.index(m) for m in expected_markers] == sorted(markers.index(m) for m in expected_markers),
            'public_FIFO_markers_missing')
    assertions = [e for e in public['events'] if e.get('event') == 'before_original_error_assert']
    require(len(assertions) == 1 and type(assertions[0]['fixture_errors']) is list
            and type(public['original_assertion_failed']) is bool, 'strict_hook_evidence')
    counts = tap_counts((work / 'tap.log').read_bytes())
    require(returncode == (1 if counts['fail'] else 0), 'Node_exit_TAP_mismatch')
    events = [safe_event(e, assets) for e in protocol['events']]
    failed = public['original_assertion_failed']
    errors = bool(assertions[0]['fixture_errors'])
    require(not errors or failed, 'strict_hook_contradiction')
    require(not counts['pass'] or not failed, 'TAP_strict_contradiction')
    strict = {'original_assertion_failed': failed,
        'errors_assert': 'failed' if failed and errors else 'passed',
        'unknown_assert': 'not_reached' if failed and errors else 'failed_inferred' if failed else 'passed'}
    return {'available': True, 'clock': 'Node receive time; not wire time', 'events': events}, {
        'TAP': counts, 'launch_completed': True, 'new_page_completed': True,
        'body_result': 'passed' if counts['pass'] else 'unknown_on_failure', 'strict_hooks': strict,
        'launcher_exit_observed': True, 'launcher_exit_code': code, 'launcher_signal': sig,
        'pageerror_count': sum(e.get('event') == 'pageerror' for e in events),
        'diagnosis': 'natural_pageerror_observed' if any(e.get('event') == 'pageerror' for e in events) else 'inconclusive_not_reproduced'}


def publish(directory, summary, timeline, manifest):
    encode = lambda value: (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()
    if len(encode(timeline)) > 2 * 1024**2:
        summary['ok'] = False
        summary['issues'].append('safe_timeline_limit')
        timeline = {'available': False, 'events': []}
    payloads = {'summary.json': encode(summary), 'timeline.json': encode(timeline)}
    manifest['safe_files'] = {name: {'bytes': len(data), 'sha256': sha(data)} for name, data in payloads.items()}
    manifest['expected_files'] = sorted(SAFE_FILES)
    payloads['manifest.json'] = encode(manifest)
    require(all(len(data) <= 2 * 1024**2 for data in payloads.values()), 'safe_artifact_size')
    for name, data in payloads.items():
        private_write(directory / name, data)
    require({p.name for p in directory.iterdir()} == SAFE_FILES, 'artifact_whitelist')
    for name in SAFE_FILES:
        require(type(read_json(directory / name)) is dict, 'artifact_JSON')
    require(all(sha((directory / name).read_bytes()) == meta['sha256']
                for name, meta in manifest['safe_files'].items()), 'artifact_hash')


def run(directory):
    started = time.monotonic()
    work_end, total_end = started + 60, started + 70
    work, generated, child, owned, tap = None, None, None, None, None
    summary = {'kind': 'farm-wk01-native-diagnostic-v1', 'ok': False, 'issues': [],
        'diagnosis': 'unknown', 'WK01_fixed': False, 'original_78_case_suite_changed': False,
        'node_returncode': None, 'pipe_EOF': False, 'ownership_coverage': 'unknown', 'launch_completed': 'unknown',
        'new_page_completed': 'unknown', 'body_result': 'unknown', 'strict_hooks': 'unknown'}
    timeline, manifest = {'available': False, 'events': []}, {'kind': 'farm-wk01-native-manifest-v1'}
    previous = {}
    artifact_created = False
    eof = False

    def drain():
        nonlocal eof
        if child is None or child.stdout is None or eof:
            return
        for _ in range(16):
            try:
                data = os.read(child.stdout.fileno(), 65536)
            except BlockingIOError:
                return
            if not data:
                eof = True
                return
            require(tap.tell() + len(data) <= 4 * 1024**2, 'raw_TAP_limit')
            tap.write(data)

    def interrupted(_number, _frame):
        raise Refusal('interrupted')

    try:
        require(sys.platform == 'linux' and platform.machine() == 'x86_64', 'native_linux_x64_required')
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal') and hasattr(os, 'WNOWAIT'), 'ownership_API_unavailable')
        require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'SIGCHLD_handler')
        require(os.getpid() in process_table(), 'proc_unavailable')
        probe = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(probe, 0)
        finally:
            os.close(probe)
        require(not directory.exists() and not any(p.is_symlink() for p in directory.parents), 'new_artifact_directory_required')
        directory.mkdir(parents=True, mode=0o700)
        artifact_created = True
        temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
        work = Path(tempfile.mkdtemp(prefix='wk01-native-', dir=temp))
        (work / 'public').mkdir(mode=0o700)
        (work / 'tmp').mkdir(mode=0o700)
        sources, assets = source_manifest(work_end)
        node = shutil.which('node')
        require(node is not None, 'node_unavailable')
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'PLAYWRIGHT_BROWSERS_PATH') if k in os.environ}
        env.update(CI='true', LANG='C.UTF-8', TZ='UTC', TMPDIR=str(work / 'tmp'), DEBUG='', PWDEBUG='', NODE_OPTIONS='',
            BROWSER_ENGINE='webkit', CHECKOUT_EVENT_EVIDENCE_DIR=str(work / 'public'), WK01_PROTOCOL_DIRECTORY=str(work))
        version = subprocess.run([node, '--version'], env=env, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=min(2, work_end - time.monotonic()), check=True)
        require(version.stdout.strip() == b'v22.23.4' and not version.stderr, 'node_version')
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=min(2, work_end - time.monotonic()), check=True).stdout.strip().decode()
        require(re.fullmatch(r'[0-9a-f]{40}', revision), 'checkout_identity')
        if 'GITHUB_SHA' in os.environ:
            require(os.environ['GITHUB_SHA'] == revision, 'checkout_SHA_mismatch')
        manifest.update(source_sha256=sources, checkout_SHA=revision, node_sha256=sha(Path(node).read_bytes()),
            python_version=list(sys.version_info[:3]), OS='Linux', arch='x64', source_recovery_byte_exact=True)
        for key, pattern in [('RUNNER_OS', r'Linux'), ('RUNNER_ARCH', r'X64'), ('ImageOS', r'ubuntu[0-9]{2}'), ('ImageVersion', r'[0-9][0-9.]{1,40}')]:
            value = os.environ.get(key)
            if value is not None:
                require(re.fullmatch(pattern, value), 'runner_metadata_format')
                manifest[key] = value
        generated = FIXTURE.with_name('.wk01-native-' + uuid4().hex + '.mjs')
        generated_bytes = instrument(FIXTURE.read_bytes(), assets)
        private_write(generated, generated_bytes)
        manifest['generated_fixture_sha256'] = sha(generated_bytes)
        tap = os.fdopen(os.open(work / 'tap.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb', buffering=0)
        previous = {n: signal.signal(n, interrupted) for n in (signal.SIGINT, signal.SIGTERM)}
        check_time(work_end)
        child = subprocess.Popen([node, '--test', '--test-concurrency=1', '--test-timeout=45000',
            '--test-name-pattern=^' + re.escape(CASE) + '$', str(generated)], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        os.set_blocking(child.stdout.fileno(), False)
        owned = OwnedProcesses(child.pid)
        while time.monotonic() < work_end:
            owned.capture()
            owned.bind_browser(work / 'ownership.json')
            drain()
            ended = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if ended is not None and eof:
                break
            time.sleep(0.05)
        else:
            raise Refusal('work_deadline')
    except BaseException as error:
        summary['issues'].append(error.args[0] if type(error) is Refusal else type(error).__name__)
    finally:
        cleanup_end = min(total_end, time.monotonic() + 10)
        for number in previous:
            signal.signal(number, signal.SIG_IGN)
        try:
            if owned is not None:
                try:
                    owned.bind_browser(work / 'ownership.json')
                except BaseException:
                    summary['issues'].append('browser_binding_unverified')
                summary['cleanup'] = owned.cleanup(cleanup_end - 2, drain)
                require(summary['cleanup']['live_members'] == summary['cleanup']['unknown_members'] == 0, 'live_or_unknown_processes')
                require(not summary['cleanup']['errors'], 'cleanup_observation_failed')
            elif child is not None:
                summary['issues'].append('cleanup_ownership_unavailable')
        except BaseException as error:
            summary['issues'].append(error.args[0] if type(error) is Refusal else type(error).__name__)
            if owned is not None:
                owned.signal_known(signal.SIGKILL, 'KILL', [])
        try:
            if child is not None:
                summary['node_returncode'] = reap_child(child, cleanup_end - 2, owned is not None)
                drain()
                require(eof, 'pipe_EOF_missing')
                summary['pipe_EOF'] = True
                require(owned is not None, 'cleanup_descendant_coverage_unknown')
                _, live, unknown = owned.capture()
                require(not live and not unknown, 'post_reap_live_or_unknown_processes')
                summary['ownership_coverage'] = 'verified' if owned.browser_binding else 'unknown'
                require(owned.browser_binding is not None, 'browser_ownership_coverage_unknown')
        except BaseException as error:
            summary['issues'].append(error.args[0] if type(error) is Refusal else type(error).__name__)
        finally:
            if tap is not None:
                tap.close()
            if child is not None and child.stdout is not None:
                child.stdout.close()
            if owned is not None:
                owned.close()
            for number, handler in previous.items():
                signal.signal(number, handler)
        try:
            if work is not None and child is not None:
                check_time(total_end)
                timeline, facts = evidence_projection(work, assets, summary['node_returncode'], owned.browser_binding if owned else None)
                summary.update(facts)
                require(summary['node_returncode'] == 0, 'original_test_failed')
                require(summary['launcher_exit_code'] == 0 and summary['launcher_signal'] is None, 'launcher_exit_nonzero')
            if 'sources' in locals():
                after, _ = source_manifest(total_end)
                require(after == sources, 'source_or_dist_changed')
                manifest['source_and_dist_unchanged'] = True
            summary['ok'] = not summary['issues'] and summary['pipe_EOF'] and timeline['available']
        except BaseException as error:
            summary['issues'].append(error.args[0] if type(error) is Refusal else type(error).__name__)
        if generated is not None:
            generated.unlink(missing_ok=True)
        summary['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if artifact_created:
            publish(directory, summary, timeline, manifest)
    return 0 if summary['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        return run(args.evidence_dir.absolute())
    except BaseException as error:
        print(json.dumps({'ok': False, 'safe_publication_failed': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
