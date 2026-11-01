from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts.pytest_exit_diagnostics import ChildExit, ExitPolicy, run_pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/pytest_exit_diagnostics.py"


def control_environment():
    return {key: os.environ[key] for key in ["PATH", "LANG", "TMPDIR"] if key in os.environ}


@pytest.fixture
def isolated_control_environment():
    return control_environment()


def test_cli_runs_original_pytest_arguments_and_waits_for_exit(tmp_path):
    case = tmp_path / "test_control.py"
    arguments = ["-q", "--durations=20", str(case)]
    case.write_text(
        "import os\n"
        "def test_control(pytestconfig):\n"
        f"    assert list(pytestconfig.invocation_params.args) == {arguments!r}\n"
        "    assert os.environ['DIAGNOSTIC_CONTROL_ENV'] == 'synthetic-environment-control'\n"
    )
    evidence = tmp_path / "evidence"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence-dir", str(evidence), "--", *arguments],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        env={**control_environment(), "DIAGNOSTIC_CONTROL_ENV": "synthetic-environment-control"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads((evidence / "result.json").read_text())
    events = [json.loads(line)["event"] for line in (evidence / "phases.jsonl").read_text().splitlines()]
    assert value["child_exit_code"] == 0 and value["child_reaped"] is True
    assert value["exit_timeout"] is False and value["issues"] == []
    assert value["exit_observer"] == ("waitid_wnowait" if sys.platform == "linux" else "kqueue_note_exit")
    assert "1 passed" in result.stdout
    assert events == ["PYTEST_MAIN_ENTER", "SESSIONFINISH_ENTER", "TERMINAL_SUMMARY", "SESSIONFINISH_RETURN",
        "UNCONFIGURE_ENTER", "UNCONFIGURE_RETURN", "PYTEST_MAIN_RETURN", "PYTHON_ATEXIT"]


@pytest.mark.parametrize("preexisting_handler", [False, True])
def test_late_stack_request_after_handler_teardown_preserves_child_exit(tmp_path, preexisting_handler):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    case = tmp_path / "test_control.py"
    case.write_text("def test_control(): pass\n")
    runner = tmp_path / "late_stack.py"
    registration = "faulthandler.register(signal.SIGUSR1, file=sys.stderr)\n" if preexisting_handler else ""
    runner.write_text(
        "import faulthandler, os, signal, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(SCRIPT.parents[1])!r})\n"
        "from scripts.pytest_exit_diagnostics import run_child\n"
        f"directory = Path({str(evidence)!r})\n"
        f"{registration}"
        f"code = run_child(['-q', {str(case)!r}], directory)\n"
        "os.kill(os.getpid(), signal.SIGUSR1)\n"
        "assert (directory / 'tracebacks.log').stat().st_size > 0\n"
        "assert faulthandler.unregister(signal.SIGUSR1)\n"
        "os.kill(os.getpid(), signal.SIGUSR1)\n"
        "raise SystemExit(code)\n"
    )
    result = subprocess.run([sys.executable, str(runner)], cwd=tmp_path,
        env=control_environment(), capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line)["event"]
        for line in (evidence / "phases.jsonl").read_text().splitlines()]
    assert events[-2:] == ["PYTEST_MAIN_RETURN", "PYTHON_ATEXIT"]


def test_non_daemon_after_pytest_return_is_timed_out_and_reaped(tmp_path, isolated_control_environment):
    case = tmp_path / "test_control.py"
    case.write_text("import threading\ndef test_control():\n    threading.Thread(target=lambda: threading.Event().wait(1)).start()\n")
    evidence = tmp_path / "evidence"
    code = run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment,
        policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.2))
    value = json.loads((evidence / "result.json").read_text())
    events = [json.loads(line)["event"] for line in (evidence / "phases.jsonl").read_text().splitlines()]
    assert code == 124
    assert value["child_reaped"] is True and value["exit_timeout"] is True
    assert value["child_exit_code"] == -15
    assert "PYTEST_MAIN_RETURN" in events and "PYTHON_ATEXIT" not in events
    assert "_shutdown" in (evidence / "tracebacks.log").read_text()


def test_atexit_phase_write_failure_cannot_turn_into_success(tmp_path, isolated_control_environment):
    case = tmp_path / "test_control.py"
    case.write_text(
        "import atexit\nfrom pathlib import Path\n"
        "def test_control():\n"
        "    original = Path.open\n"
        "    def broken(path, *args, **kwargs):\n"
        "        if path.name == 'phases.jsonl': raise OSError('synthetic phase write failure')\n"
        "        return original(path, *args, **kwargs)\n"
        "    atexit.register(lambda: setattr(Path, 'open', broken))\n"
    )
    evidence = tmp_path / "evidence"
    code = run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment)
    value = json.loads((evidence / "result.json").read_text())
    assert value["child_exit_code"] == 0
    assert code != 0
    assert value["issues"] == [{"type": "diagnostics_incomplete"}]


def test_wrapper_signal_reaps_only_its_owned_child_session(tmp_path):
    case = tmp_path / "test_control.py"
    case.write_text("import time\ndef test_control():\n    time.sleep(2)\n")
    evidence = tmp_path / "evidence"
    child_pid = None
    with (tmp_path / "wrapper.log").open("w") as output:
        wrapper = subprocess.Popen([sys.executable, str(SCRIPT), "--evidence-dir", str(evidence), "--", "-q", str(case)],
            cwd=tmp_path, env=control_environment(), stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 5
            while child_pid is None and time.monotonic() < deadline:
                path = evidence / "phases.jsonl"
                if path.exists():
                    lines = path.read_text().splitlines()
                    if lines:
                        child_pid = json.loads(lines[0])["pid"]
                time.sleep(.01)
            assert child_pid is not None
            wrapper.send_signal(signal.SIGTERM)
            assert wrapper.wait(timeout=5) == 143
            value = json.loads((evidence / "result.json").read_text())
            assert value["interrupted_signal"] == 15 and value["child_reaped"] is True
            assert value["child_exit_code"] == -15
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=3)
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_unconfigure_hang_is_distinct_from_python_thread_shutdown(tmp_path, isolated_control_environment):
    case = tmp_path / "test_control.py"
    case.write_text("def test_control(): pass\n")
    (tmp_path / "conftest.py").write_text("import time\ndef pytest_unconfigure(config):\n    time.sleep(2)\n")
    evidence = tmp_path / "evidence"
    code = run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment,
        policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.2))
    value = json.loads((evidence / "result.json").read_text())
    assert code == 124 and value["child_reaped"] is True
    assert value["phases"][-1] == "UNCONFIGURE_ENTER"
    assert "PYTEST_MAIN_RETURN" not in value["phases"]
    assert "pytest_unconfigure" in (evidence / "tracebacks.log").read_text()
    assert value["after_summary_seconds"] >= .2


def test_daemon_thread_does_not_block_normal_exit(tmp_path, isolated_control_environment):
    case = tmp_path / "test_control.py"
    case.write_text("import threading\ndef test_control():\n    threading.Thread(target=lambda: threading.Event().wait(2), daemon=True).start()\n")
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment) == 0
    value = json.loads((evidence / "result.json").read_text())
    events = [json.loads(line) for line in (evidence / "phases.jsonl").read_text().splitlines()]
    returned = next(event for event in events if event["event"] == "PYTEST_MAIN_RETURN")
    assert any(thread["daemon"] and thread["alive"] for thread in returned["threads"])
    assert value["exit_timeout"] is False and value["child_reaped"] is True


def test_exit_deadline_never_becomes_a_test_runtime_deadline(tmp_path, isolated_control_environment):
    case = tmp_path / "test_control.py"
    case.write_text("import time\ndef test_control():\n    time.sleep(.4)\n")
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment,
        policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.2)) == 0
    value = json.loads((evidence / "result.json").read_text())
    assert value["wall_seconds"] >= .4 and value["exit_timeout"] is False


@pytest.mark.parametrize("source,arguments,expected", [
    ("def test_control(): assert False\n", [], 1),
    ("raise RuntimeError('synthetic collection failure')\n", [], 2),
    ("def test_control(): pass\n", ["--unrecognised-exit-control"], 4),
    ("def helper(): pass\n", [], 5),
    ("import os, signal\ndef test_control(): os.kill(os.getpid(), signal.SIGTERM)\n", [], 143),
])
def test_actual_pytest_failure_or_signal_code_is_preserved(tmp_path, isolated_control_environment, source, arguments, expected):
    case = tmp_path / "test_control.py"
    case.write_text(source)
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", *arguments, str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment) == expected
    value = json.loads((evidence / "result.json").read_text())
    assert value["child_exit_code"] == (-15 if expected == 143 else expected)
    assert value["child_reaped"] is True and value["exit_timeout"] is False


def test_result_write_failure_is_red_after_child_was_reaped(tmp_path, isolated_control_environment, capsys):
    evidence = tmp_path / "evidence"
    case = tmp_path / "test_control.py"
    case.write_text(f"from pathlib import Path\ndef test_control():\n    Path({str(evidence / 'result.json')!r}).mkdir()\n")
    assert run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment) == 1
    events = [json.loads(line) for line in (evidence / "phases.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "PYTHON_ATEXIT"
    with pytest.raises(ProcessLookupError):
        os.kill(events[0]["pid"], 0)
    assert "evidence write failed" in capsys.readouterr().err


def test_traceback_does_not_dump_thread_locals_or_environment(tmp_path, isolated_control_environment):
    marker = "private-value-must-never-appear-in-diagnostic"
    case = tmp_path / "test_control.py"
    case.write_text(
        "import threading, os\n"
        "def blocked():\n    private_value = os.environ['SYNTHETIC_PRIVATE_VALUE']\n    threading.Event().wait(2)\n"
        "def test_control(): threading.Thread(target=blocked).start()\n"
    )
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", str(case)], evidence, cwd=tmp_path,
        environment={**isolated_control_environment, "SYNTHETIC_PRIVATE_VALUE": marker},
        policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.2)) == 124
    for path in evidence.iterdir():
        if path.is_file():
            assert marker not in path.read_text()


def test_cli_has_no_timeout_or_pytest_selection_override(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--evidence-dir", str(tmp_path / 'evidence'),
        "--summary-grace", "1", "--", "-q"], env=control_environment(), capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr


@pytest.mark.parametrize("trigger,expected_code", [("normal", 1), ("interrupt", 143), ("timeout", 124)])
def test_owned_term_ignoring_grandchild_cannot_hold_output_or_harm_unrelated_process(tmp_path, trigger, expected_code):
    ready = tmp_path / "grandchild.json"
    holder = tmp_path / "holder.py"
    holder.write_text(
        "import json, os, signal, time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(ready)!r}).write_text(json.dumps({{'pid': os.getpid(), 'pgid': os.getpgrp(), 'sid': os.getsid(0)}}))\n"
        "time.sleep(10)\n"
    )
    case = tmp_path / "test_control.py"
    tail = ""
    if trigger == "interrupt":
        tail = "    time.sleep(10)\n"
    if trigger == "timeout":
        tail = "    threading.Thread(target=lambda: threading.Event().wait(10)).start()\n"
    case.write_text(
        "import subprocess, sys, threading, time\nfrom pathlib import Path\n"
        "def test_control():\n"
        f"    subprocess.Popen([sys.executable, {str(holder)!r}])\n"
        "    deadline = time.monotonic() + 5\n"
        f"    while not Path({str(ready)!r}).exists() and time.monotonic() < deadline: time.sleep(.01)\n"
        f"    assert Path({str(ready)!r}).exists()\n" + tail
    )
    evidence = tmp_path / "evidence"
    runner = tmp_path / "run_control.py"
    runner.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(SCRIPT.parents[1])!r})\n"
        "from scripts.pytest_exit_diagnostics import ExitPolicy, run_pytest\n"
        f"raise SystemExit(run_pytest(['-q', '-s', {str(case)!r}], Path({str(evidence)!r}), "
        f"cwd={str(tmp_path)!r}, policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.1)))\n"
    )
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
        env=control_environment(), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wrapper = subprocess.Popen([sys.executable, str(runner)], cwd=tmp_path, env=control_environment(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    identity = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        identity = json.loads(ready.read_text())
        leader = json.loads((evidence / "phases.jsonl").read_text().splitlines()[0])["pid"]
        assert identity["pgid"] == identity["sid"] == leader
        assert os.getpgid(sentinel.pid) != leader
        if trigger == "interrupt":
            wrapper.send_signal(signal.SIGTERM)
        output, _ = wrapper.communicate(timeout=3)
        assert wrapper.returncode == expected_code, output
        assert sentinel.poll() is None
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(identity["pid"])],
            capture_output=True, text=True, timeout=2).stdout.strip()
        assert not state or state.startswith("Z"), "Owned grandchild remained alive after cleanup"
        value = json.loads((evidence / "result.json").read_text())
        assert value["child_exit_code"] == (0 if trigger == "normal" else -15)
        assert value["child_reaped"] is True
        (tmp_path / "ownership-control.json").write_text(json.dumps({
            "trigger": trigger, "grandchild_identity": identity, "owned_leader_pid": leader,
            "unrelated_pid": sentinel.pid, "unrelated_pgid": os.getpgid(sentinel.pid),
            "unrelated_alive_after_owned_cleanup": sentinel.poll() is None,
            "stdout_pipe_reached_eof": True, "grandchild_final_state": state,
            "wrapper_exit_code": wrapper.returncode, "child_exit_code": value["child_exit_code"],
            "group_cleanup": value["group_cleanup"],
        }, indent=2) + "\n")
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=3)
        if identity is not None:
            try:
                if os.getpgid(identity["pid"]) == identity["pgid"] and os.getsid(identity["pid"]) == identity["sid"]:
                    os.kill(identity["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if wrapper.stdout is not None:
            wrapper.stdout.close()
        sentinel.terminate()
        sentinel.wait(timeout=3)


def test_observation_registration_failure_still_reaps_owned_child(tmp_path, isolated_control_environment, monkeypatch):
    def unavailable(*args):
        raise OSError("synthetic process exit registration failure")
    monkeypatch.setattr(ChildExit, "attach", unavailable)
    case = tmp_path / "test_control.py"
    case.write_text("import time\ndef test_control(): time.sleep(2)\n")
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment,
        policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.2)) != 0
    value = json.loads((evidence / "result.json").read_text())
    assert value["child_reaped"] is True
    assert value["group_cleanup"]["remaining_live_members"] == []
    with pytest.raises(ProcessLookupError):
        os.kill(value["owned_child_pid"], 0)


def test_missing_group_inspection_capability_fails_before_spawning(tmp_path, isolated_control_environment, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda command: None)
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", "not-used.py"], evidence, cwd=tmp_path, environment=isolated_control_environment) == 1
    value = json.loads((evidence / "result.json").read_text())
    assert "owned_child_pid" not in value and value["child_exit_code"] is None
    assert value["issues"] == [{"type": "RuntimeError"}]


@pytest.mark.parametrize("handler", [signal.SIG_IGN, lambda signum, frame: None])
def test_caller_sigchld_handler_is_rejected_without_changing_it_or_spawning(tmp_path, isolated_control_environment, monkeypatch, handler):
    original = signal.getsignal
    monkeypatch.setattr(signal, "getsignal", lambda signum: handler if signum == signal.SIGCHLD else original(signum))
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", "not-used.py"], evidence, cwd=tmp_path, environment=isolated_control_environment) == 1
    value = json.loads((evidence / "result.json").read_text())
    assert "owned_child_pid" not in value and value["child_exit_code"] is None
    assert signal.getsignal(signal.SIGCHLD) is handler


def test_cleanup_diagnostic_failure_still_terminates_pinned_owned_child(tmp_path, isolated_control_environment, monkeypatch):
    def unavailable(*args):
        raise OSError("synthetic registration failure")
    def broken_cleanup(*args):
        raise RuntimeError("synthetic cleanup diagnostic failure")
    monkeypatch.setattr(ChildExit, "attach", unavailable)
    monkeypatch.setattr("scripts.pytest_exit_diagnostics.cleanup_group", broken_cleanup)
    case = tmp_path / "test_control.py"
    case.write_text("import time\ndef test_control(): time.sleep(2)\n")
    evidence = tmp_path / "evidence"
    assert run_pytest(["-q", str(case)], evidence, cwd=tmp_path, environment=isolated_control_environment,
        policy=ExitPolicy(summary_grace=.2, dump_after=.05, terminate_grace=.1)) != 0
    value = json.loads((evidence / "result.json").read_text())
    assert value["child_reaped"] is True
    assert value["group_cleanup_fallback"]["verification"] == "unavailable"
    with pytest.raises(ProcessLookupError):
        os.kill(value["owned_child_pid"], 0)
