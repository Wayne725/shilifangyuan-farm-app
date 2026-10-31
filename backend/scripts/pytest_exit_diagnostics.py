"""Run unchanged pytest arguments and observe its actual process exit."""
from __future__ import annotations

import argparse
import atexit
from dataclasses import dataclass
from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


@dataclass(frozen=True)
class ExitPolicy:
    summary_grace: float = 60
    dump_after: float = 5
    terminate_grace: float = 3

    def __post_init__(self):
        if not 0 < self.dump_after < self.summary_grace <= 60 or not 0 < self.terminate_grace <= 3:
            raise ValueError("Exit diagnostics must remain bounded")


class ExitInterrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


def read_phases(directory):
    path = directory / "phases.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]


def phase(directory, event, **extra):
    value = {"event": event, "monotonic": time.monotonic(), "utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
        "threads": [{"ident": thread.ident, "native_id": thread.native_id, "daemon": thread.daemon,
            "alive": thread.is_alive()} for thread in threading.enumerate()], **extra}
    with (directory / "phases.jsonl").open("a") as output:
        output.write(json.dumps(value) + "\n")


def run_child(arguments, directory):
    import pytest

    # Keep this descriptor alive through Python's thread shutdown and atexit.
    stacks = (directory / "tracebacks.log").open("w")
    faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True, chain=False)
    def at_exit():
        try:
            phase(directory, "PYTHON_ATEXIT")
        except OSError as error:
            print(f"pytest exit phase write failed: {type(error).__name__}", file=sys.stderr)
    atexit.register(at_exit)

    class ObserveExit:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_sessionfinish(self, session, exitstatus):
            phase(directory, "SESSIONFINISH_ENTER", pytest_exit_code=int(exitstatus))
            value = yield
            phase(directory, "SESSIONFINISH_RETURN")
            return value

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
            value = yield
            phase(directory, "TERMINAL_SUMMARY")
            return value

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_unconfigure(self, config):
            phase(directory, "UNCONFIGURE_ENTER")
            value = yield
            phase(directory, "UNCONFIGURE_RETURN")
            return value

    sys.path.insert(0, str(Path.cwd()))
    sys.argv = ["pytest", *arguments]
    phase(directory, "PYTEST_MAIN_ENTER")
    code = int(pytest.main(arguments, plugins=[ObserveExit()]))
    phase(directory, "PYTEST_MAIN_RETURN", pytest_exit_code=code)
    # atexit retains stacks until the process genuinely exits.
    atexit.register(lambda: stacks.flush())
    return code


def run_pytest(arguments, directory, *, cwd=None, environment=None, policy=ExitPolicy()):
    """The CLI uses the fixed policy; short policies are for subprocess controls."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True)
    result = {"child_exit_code": None, "child_reaped": False, "exit_timeout": False,
        "summary_grace_seconds": policy.summary_grace, "dump_after_seconds": policy.dump_after,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "issues": [], "traceback_contains_locals": False}
    child, summary, next_dump, previous_handlers = None, None, None, {}
    started = time.monotonic()
    def interrupted(signum, frame):
        raise ExitInterrupted(signum)
    try:
        for signum in [signal.SIGINT, signal.SIGTERM]:
            previous_handlers[signum] = signal.signal(signum, interrupted)
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", "--evidence-dir", str(directory), "--", *arguments],
            cwd=cwd, env=environment, start_new_session=True)
        result["owned_child_pid"] = child.pid
        result["owned_child_session_id"] = child.pid
        while child.poll() is None:
            events = read_phases(directory)
            completed = [event for event in events if event["event"] == "TERMINAL_SUMMARY"]
            if completed and summary is None:
                summary = completed[0]["monotonic"]
                next_dump = summary + policy.dump_after
            now = time.monotonic()
            if next_dump is not None and now >= next_dump:
                child.send_signal(signal.SIGUSR1)
                result["stack_requests"] = result.get("stack_requests", 0) + 1
                next_dump = now + 10
            if summary is not None and now - summary >= policy.summary_grace:
                result["exit_timeout"] = True
                result["issues"].append({"type": "post_summary_exit_timeout"})
                break
            time.sleep(.02)
        if not result["exit_timeout"]:
            result["child_exit_code"] = child.wait()
    except ExitInterrupted as error:
        result["interrupted_signal"] = error.signum
        result["issues"].append({"type": "wrapper_interrupted"})
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, error.signum)
                child.wait(timeout=policy.terminate_grace)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
    except BaseException as error:
        result["issues"].append({"type": type(error).__name__})
    finally:
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=policy.terminate_grace)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=policy.terminate_grace)
                except subprocess.TimeoutExpired:
                    result["issues"].append({"type": "owned_child_not_reaped"})
        if child is not None:
            result["child_exit_code"] = child.returncode
            result["child_reaped"] = child.poll() is not None
        result["wall_seconds"] = time.monotonic() - started
        result["after_summary_seconds"] = time.monotonic() - summary if summary is not None else None
        raw_code = result["child_exit_code"]
        try:
            events = read_phases(directory)
            result["phases"] = [event["event"] for event in events]
            if raw_code is not None and raw_code >= 0 and not result["exit_timeout"] and not result.get("interrupted_signal"):
                if not {"PYTEST_MAIN_RETURN", "PYTHON_ATEXIT"}.issubset(result["phases"]):
                    result["issues"].append({"type": "diagnostics_incomplete"})
        except (OSError, ValueError) as error:
            result["issues"].append({"type": type(error).__name__})
        code = 1 if raw_code is None else raw_code if raw_code >= 0 else 128 - raw_code
        if result["issues"] and not code:
            code = 1
        if result["exit_timeout"]:
            code = 124
        if result.get("interrupted_signal"):
            code = 128 + result["interrupted_signal"]
        result["wrapper_exit_code"] = code
        try:
            (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        except OSError as error:
            print(f"pytest exit evidence write failed: {type(error).__name__}", file=sys.stderr)
            code = code or 1
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
    return code


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    if "--" not in values:
        parser.error("Separate the unchanged pytest arguments with --")
    separator = values.index("--")
    args = parser.parse_args(values[:separator])
    arguments = values[separator + 1:]
    if args.child:
        return run_child(arguments, args.evidence_dir)
    return run_pytest(arguments, args.evidence_dir)


if __name__ == "__main__":
    raise SystemExit(main())
