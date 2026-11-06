import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'run_wk01_native.py'
spec = importlib.util.spec_from_file_location('wk01_control_runner', SOURCE)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def row(pid, parent, group=None, session=None, start=10, state='S'):
    return runner.Process(pid, parent, group or pid, session or group or pid, os.getuid(), start, state)


def guardian(records):
    value = runner.OwnedProcesses.__new__(runner.OwnedProcesses)
    value.known = {r.pid: r for r in records}
    value.fds = {r.pid: 1000 + r.pid for r in records}
    value.groups = {r.pgid for r in records}
    value.sessions = {r.sid for r in records}
    value.browser_binding = None
    value.actions = {'TERM': 0, 'KILL': 0}
    return value


class Controls(unittest.TestCase):
    def evidence(self, folder):
        work = Path(folder)
        (work / 'public').mkdir()
        events = [
            {'kind': 'protocol', 'method': 'Network.requestWillBeSent', 'received_ms': 1, 'path': '/checkout'},
            {'kind': 'protocol', 'method': 'Page.frameNavigated', 'received_ms': 2, 'path': '/checkout'},
            {'kind': 'public', 'event': 'request', 'received_ms': 3, 'path': '/checkout'},
            {'kind': 'public', 'event': 'mainframe_navigated', 'received_ms': 4, 'path': '/checkout'},
        ]
        protocol = {'kind': 'farm-wk01-protocol-observation-v1', 'platform': 'linux', 'arch': 'x64',
            'node': 'v22.23.4', 'installed': True, 'launchCount': 1, 'publicAttached': 1, 'issues': [],
            'ownership': [{'browser_pid': 30, 'owner_node_pid': 20, 'launcher_exit_observed': True,
                           'exit_code': 0, 'signal': None}], 'events': events}
        public = {'test_name': runner.CASE, 'platform': 'linux', 'arch': 'x64', 'node': 'v22.23.4',
            'engine': 'webkit', 'observer_failed': False, 'original_assertion_failed': False,
            'events': [{'event': 'observers_attached'}, {'event': 'before_browser_close'},
                       {'event': 'after_browser_close'}, {'event': 'before_original_error_assert', 'fixture_errors': []}]}
        (work / 'protocol.json').write_text(json.dumps(protocol))
        (work / 'public/evidence.json').write_text(json.dumps(public))
        (work / 'ownership.json').write_text(json.dumps({'kind': 'farm-wk01-launch-ownership-v1',
                                                       'browser_pid': 30, 'owner_node_pid': 20}))
        (work / 'tap.log').write_text(f'ok 1 - {runner.CASE}\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 0\n')
        return work, protocol, public

    def test_instrumentation_exact_reverse_and_original_hooks(self):
        source = runner.FIXTURE.read_bytes()
        result = runner.instrument(source, {'/index.html'})
        self.assertEqual(source.count(b't.after('), result.count(b't.after('))
        self.assertEqual(result.count(b'wk01Observer.noteBrowser(browser);'), 1)
        self.assertEqual(result.count(b'wk01Observer.attachPublic(page);'), 1)
        with self.assertRaises(runner.Refusal):
            runner.instrument(source.replace(b'  const page = await context.newPage();', b''), set())

    def test_observer_is_byte_identical(self):
        self.assertEqual(runner.sha(runner.OBSERVER.read_bytes()), runner.PINS[runner.OBSERVER])

    def test_safe_projection_hashes_identifiers_and_discards_unknown_text(self):
        value = runner.safe_event({'kind': 'public', 'event': 'pageerror', 'sessionId': 'private-session',
            'text': 'private raw text token=example', 'classification': 'other_hashed_text',
            'text_sha256': 'a' * 64, 'text_bytes': 35}, set())
        self.assertNotIn('private', json.dumps(value))
        self.assertIn('sessionId_sha256', value)
        for value in [{'headers': {'Cookie': 'private'}}, {'path': '/checkout?token=private'}, {'path': 'https://example.invalid'}]:
            with self.subTest(value=value), self.assertRaises(runner.Refusal):
                runner.safe_event(value, set())

    def test_complete_projection_is_inconclusive_not_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            work, _, _ = self.evidence(folder)
            timeline, facts = runner.evidence_projection(work, set(), 0, {'browser_pid': 30, 'owner_node_pid': 20})
            self.assertTrue(timeline['available'])
            self.assertEqual(facts['diagnosis'], 'inconclusive_not_reproduced')
            self.assertEqual(facts['launcher_exit_code'], 0)
            self.assertEqual(facts['strict_hooks']['unknown_assert'], 'passed')

    def test_empty_or_only_hook_evidence_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            work, protocol, public = self.evidence(folder)
            for events in [[], [{'kind': 'public', 'event': 'pageerror', 'received_ms': 1}]]:
                protocol['events'] = events
                (work / 'protocol.json').write_text(json.dumps(protocol))
                with self.assertRaises(runner.Refusal):
                    runner.evidence_projection(work, set(), 0, {'browser_pid': 30, 'owner_node_pid': 20})
            protocol['events'] = [{'kind': 'protocol', 'method': 'Network.requestWillBeSent'},
                                  {'kind': 'protocol', 'method': 'Page.frameNavigated'},
                                  {'kind': 'public', 'event': 'request'}, {'kind': 'public', 'event': 'mainframe_navigated'}]
            (work / 'protocol.json').write_text(json.dumps(protocol))
            public['events'] = [public['events'][-1]]
            (work / 'public/evidence.json').write_text(json.dumps(public))
            with self.assertRaises(runner.Refusal):
                runner.evidence_projection(work, set(), 0, {'browser_pid': 30, 'owner_node_pid': 20})

    def test_unobserved_or_wrong_browser_binding_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            work, _, _ = self.evidence(folder)
            for binding in [None, {'browser_pid': 31, 'owner_node_pid': 20}]:
                with self.assertRaises(runner.Refusal):
                    runner.evidence_projection(work, set(), 0, binding)
            owned = guardian([row(10, 1), row(20, 10, 10, 10)])
            with patch.object(owned, 'capture', return_value=({}, [], [])), self.assertRaises(runner.Refusal):
                owned.bind_browser(work / 'ownership.json')

    def test_browser_requires_observed_worker_parent_and_private_group(self):
        owner, browser = row(20, 10, 10, 10), row(30, 20)
        owned = guardian([row(10, 1), owner, browser])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ownership.json'
            path.write_text(json.dumps({'kind': 'farm-wk01-launch-ownership-v1', 'browser_pid': 30, 'owner_node_pid': 20}))
            with patch.object(owned, 'capture', return_value=({}, [], [])):
                owned.bind_browser(path)
            self.assertEqual(owned.browser_binding['owner_start'], owner.start)
            owned.known[30] = row(30, 1)
            with patch.object(owned, 'capture', return_value=({}, [], [])), self.assertRaises(runner.Refusal):
                owned.bind_browser(path)

    def test_unknown_reparented_browser_group_or_session_is_not_clean(self):
        owned = guardian([row(10, 1), row(30, 20)])
        rows = {31: row(31, 1, 31, 30)}
        with patch.object(runner, 'process_table', return_value=rows):
            _, live, unknown = owned.capture()
        self.assertEqual([r.pid for r in live], [31])
        self.assertEqual(unknown, live)

    def test_stale_numeric_identity_never_signals_pidfd_for_new_process(self):
        owned = guardian([row(30, 20)])
        with patch.object(signal, 'pidfd_send_signal', create=True) as send:
            errors = []
            owned.signal_known(signal.SIGTERM, 'TERM', errors, {30: row(30, 20, start=99)})
        send.assert_not_called()
        self.assertEqual(errors, ['stale_process_identity'])

    def test_cleanup_drain_or_proc_fault_still_sends_known_KILL(self):
        for fault in ('drain', 'proc'):
            owned = guardian([row(30, 20)])
            capture = Mock(return_value=({30: owned.known[30]}, [owned.known[30]], []))
            if fault == 'proc':
                capture.side_effect = OSError('private error')
            drain = Mock(side_effect=OSError('private error'))
            with patch.object(owned, 'capture', capture), patch.object(signal, 'pidfd_send_signal', create=True) as send:
                result = owned.cleanup(time.monotonic() + 1, drain)
            self.assertTrue(any(call.args[1] == signal.SIGKILL for call in send.call_args_list))
            self.assertTrue(result['errors'])
            self.assertNotIn('private', json.dumps(result))

    def test_constructor_failure_reaps_own_Popen_leader_bounded(self):
        child = Mock(pid=123)
        child.wait.side_effect = [subprocess.TimeoutExpired('owned', 1), -signal.SIGKILL]
        with patch.object(os, 'waitid', return_value=None, create=True):
            self.assertEqual(runner.reap_child(child, time.monotonic() + 2, False), -signal.SIGKILL)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_run_constructor_fault_reaps_leader_and_publishes_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder).resolve() / 'safe'
            read_fd, write_fd = os.pipe()
            os.close(write_fd)
            child = Mock(pid=123, stdout=os.fdopen(read_fd, 'rb'))
            child.wait.return_value = 0
            revision = b'a' * 40
            outputs = [SimpleNamespace(stdout=b'v22.23.4\n', stderr=b''), SimpleNamespace(stdout=revision)]
            with patch.dict(os.environ, {'RUNNER_TEMP': folder, 'GITHUB_SHA': revision.decode()}), \
                    patch.object(runner.sys, 'platform', 'linux'), patch.object(runner.platform, 'machine', return_value='x86_64'), \
                    patch.object(runner, 'source_manifest', return_value=({'fixed': 'a' * 64}, set())), \
                    patch.object(runner, 'process_table', return_value={os.getpid(): row(os.getpid(), 1)}), \
                    patch.object(os, 'pidfd_open', side_effect=lambda pid: os.open(os.devnull, os.O_RDONLY), create=True), \
                    patch.object(signal, 'pidfd_send_signal', create=True), patch.object(os, 'WNOWAIT', 0, create=True), \
                    patch.object(os, 'waitid', return_value=None, create=True), \
                    patch.object(runner.shutil, 'which', return_value=sys.executable), \
                    patch.object(runner.subprocess, 'run', side_effect=outputs), \
                    patch.object(runner.subprocess, 'Popen', return_value=child), \
                    patch.object(runner, 'OwnedProcesses', side_effect=OSError('private constructor failure')):
                self.assertEqual(runner.run(evidence), 1)
            child.terminate.assert_called_once()
            child.wait.assert_called_once()
            summary = json.loads((evidence / 'summary.json').read_bytes())
            self.assertFalse(summary['ok'])
            self.assertEqual(summary['ownership_coverage'], 'unknown')
            self.assertNotIn('private', json.dumps(summary))

    def test_browser_exit_and_original_assertion_contradictions(self):
        with tempfile.TemporaryDirectory() as folder:
            work, protocol, public = self.evidence(folder)
            public['events'][-1]['fixture_errors'] = ['private page error']
            (work / 'public/evidence.json').write_text(json.dumps(public))
            with self.assertRaises(runner.Refusal):
                runner.evidence_projection(work, set(), 0, {'browser_pid': 30, 'owner_node_pid': 20})
            public['events'][-1]['fixture_errors'] = []
            (work / 'public/evidence.json').write_text(json.dumps(public))
            protocol['ownership'][0].update(exit_code=None, signal='SIGSEGV')
            (work / 'protocol.json').write_text(json.dumps(protocol))
            _, facts = runner.evidence_projection(work, set(), 0, {'browser_pid': 30, 'owner_node_pid': 20})
            self.assertEqual(facts['launcher_signal'], 'SIGSEGV')
            self.assertIsNone(facts['launcher_exit_code'])

    def test_artifact_exact_three_files_and_payload_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            runner.publish(path, {'ok': True, 'issues': []}, {'available': True, 'events': []}, {})
            self.assertEqual({p.name for p in path.iterdir()}, runner.SAFE_FILES)
            manifest = json.loads((path / 'manifest.json').read_bytes())
            for name, metadata in manifest['safe_files'].items():
                self.assertEqual(runner.sha((path / name).read_bytes()), metadata['sha256'])
            (path / 'foreign.json').write_text('{}')
            with self.assertRaises((runner.Refusal, FileExistsError)):
                runner.publish(path, {'ok': True}, {}, {})


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'),
                     'Linux-only real pidfd evidence; mocked controls are not kernel evidence')
class LinuxKernelControls(unittest.TestCase):
    def test_short_lived_python_natural_exit_and_reap(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.15)'], start_new_session=True)
        owned = None
        try:
            owned = runner.OwnedProcesses(child.pid)
            current = owned.known[child.pid]
            stale = runner.Process(current.pid, current.ppid, current.pgid, current.sid,
                                   current.uid, current.start + 1, current.state)
            with patch.object(signal, 'pidfd_send_signal', wraps=signal.pidfd_send_signal) as send:
                owned.signal_known(signal.SIGTERM, 'TERM', [], {child.pid: stale})
            send.assert_not_called()
            deadline = time.monotonic() + 2
            while os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertEqual(runner.reap_child(child, deadline, True), 0)
            result = owned.cleanup(deadline, lambda: None)
            self.assertEqual(result['live_members'], 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=1)
            if owned is not None:
                owned.close()


if __name__ == '__main__':
    unittest.main()
