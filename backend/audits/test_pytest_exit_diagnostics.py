from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts.pytest_exit_diagnostics import ExitPolicy, run_pytest


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
    assert "1 passed" in result.stdout
    assert events == ["PYTEST_MAIN_ENTER", "SESSIONFINISH_ENTER", "TERMINAL_SUMMARY", "SESSIONFINISH_RETURN",
        "UNCONFIGURE_ENTER", "UNCONFIGURE_RETURN", "PYTEST_MAIN_RETURN", "PYTHON_ATEXIT"]


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
