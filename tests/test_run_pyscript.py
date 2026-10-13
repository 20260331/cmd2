"""Unit/functional testing for run_pytest in cmd2"""

import os
import pathlib
import sys
from typing import (
    TextIO,
    cast,
)
from unittest import (
    mock,
)

import pytest

import cmd2
from cmd2 import rich_utils as ru
from cmd2.py_bridge import PyBridge
from cmd2.string_utils import quote
from cmd2.utils import StdSim

from .conftest import (
    odd_file_names,
    run_cmd,
    with_ansi_style,
)


def test_run_pyscript(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "script.py")
    expected = "This is a python script running ..."

    out, _err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert expected in out
    assert base_app.last_result is True


def test_run_pyscript_recursive_not_allowed(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "recursive.py")
    expected = "Recursively entering interactive Python shells is not allowed"

    _out, err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert err[0] == expected
    assert base_app.last_result is False


def test_run_pyscript_with_nonexist_file(base_app) -> None:
    python_script = "does_not_exist.py"
    _out, err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert "Error reading script file" in err[0]
    assert base_app.last_result is False


def test_run_pyscript_with_non_python_file(base_app, request, monkeypatch) -> None:
    # Mock out the read_input call so we don't actually wait for a user's response on stdin
    read_input_mock = mock.MagicMock(name="read_input", return_value="2")
    monkeypatch.setattr("cmd2.Cmd.read_input", read_input_mock)

    test_dir = os.path.dirname(request.module.__file__)
    filename = os.path.join(test_dir, "scripts", "help.txt")
    _out, err = run_cmd(base_app, f"run_pyscript {filename}")
    assert "does not have a .py extension" in err[0]
    assert base_app.last_result is False


@pytest.mark.parametrize("python_script", odd_file_names)
def test_run_pyscript_with_odd_file_names(base_app, python_script, monkeypatch) -> None:
    """Pass in file names with various patterns. Since these files don't exist, we will rely
    on the error text to make sure the file names were processed correctly.
    """
    # Mock read_input to get us passed the warning about not ending in .py
    read_input_mock = mock.MagicMock(name="read_input", return_value="1")
    monkeypatch.setattr("cmd2.Cmd.read_input", read_input_mock)

    _out, err = run_cmd(base_app, f"run_pyscript {quote(python_script)}")
    err = "".join(err)
    assert f"Error reading script file '{python_script}'" in err
    assert base_app.last_result is False


def test_run_pyscript_with_exception(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "raises_exception.py")
    _out, err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert err[0].startswith("Traceback")
    assert "TypeError: unsupported operand type(s) for +: 'int' and 'str'" in err[-1]
    assert base_app.last_result is True


def test_run_pyscript_requires_an_argument(base_app) -> None:
    _out, err = run_cmd(base_app, "run_pyscript")
    assert "the following arguments are required: script_path" in err[1]
    assert base_app.last_result is None


def test_run_pyscript_help(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "help.py")
    out1, _err1 = run_cmd(base_app, "help")
    out2, _err2 = run_cmd(base_app, f"run_pyscript {python_script}")
    assert out1
    assert out1 == out2


def test_scripts_add_to_history(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "help.py")
    command = f"run_pyscript {python_script}"

    # Add to history
    base_app.scripts_add_to_history = True
    base_app.history.clear()
    run_cmd(base_app, command)
    assert len(base_app.history) == 2
    assert base_app.history.get(1).raw == command
    assert base_app.history.get(2).raw == "help"

    # Do not add to history
    base_app.scripts_add_to_history = False
    base_app.history.clear()
    run_cmd(base_app, command)
    assert len(base_app.history) == 1
    assert base_app.history.get(1).raw == command


def test_run_pyscript_dir(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "pyscript_dir.py")

    out, _err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert out[0] == "['cmd_echo']"


def test_py_bridge_capture_isolation() -> None:
    """Verify that PyBridge captures poutput but not raw print from within a command."""
    import cmd2
    from cmd2.py_bridge import PyBridge

    class App(cmd2.Cmd):
        def do_test_capture(self, _):
            print("process_stdout")
            self.poutput("app_stdout")

    app = App()
    bridge = PyBridge(app)
    result = bridge("test_capture")

    # Verify isolation: only the application stream should be in the result
    assert result.stdout == "app_stdout\n"
    assert "process_stdout" not in result.stdout


class BudgetApp(cmd2.Cmd):
    """App used by the PyBridge capture-budget tests."""

    @staticmethod
    def big_output(count: int = 100) -> str:
        return "".join(f"line-{i:03d}\n" for i in range(count))

    @staticmethod
    def big_error(count: int = 100) -> str:
        return "".join(f"err-{i:03d}\n" for i in range(count))

    def do_big(self, args: str) -> None:
        count = int(args) if args.strip() else 100
        for i in range(count):
            self.poutput(f"line-{i:03d}")

    def do_bigerr(self, _: str) -> None:
        for i in range(100):
            sys.stderr.write(f"err-{i:03d}\n")

    def do_same(self, args: str) -> None:
        count = int(args) if args.strip() else 50
        for _ in range(count):
            self.poutput("same-line")

    def do_emit(self, _: str) -> None:
        self.poutput("fixed-output")

    def do_nested(self, _: str) -> None:
        # A nested PyBridge call gets its own independent capture budget. Overflow that it does
        # not retain must pass through to the capture of the enclosing PyBridge call.
        result = PyBridge(self)("big 100", stdout_budget=20)
        self.poutput("NESTED_PREFIX:" + result.stdout.replace("\n", "|"))


def test_py_bridge_default_captures_everything() -> None:
    app = BudgetApp()
    result = PyBridge(app)("big 100")

    assert result.stdout == BudgetApp.big_output()
    assert not result.stdout_truncated
    assert not result.stderr_truncated
    assert result.stdout_log is None
    assert result.stderr_log is None


def test_py_bridge_stdout_budget_prefix_and_passthrough() -> None:
    app = BudgetApp()

    # Stand in for the original target of app.stdout, as an outer capture would provide.
    original_target = StdSim(sys.stdout)
    app.stdout = cast(TextIO, original_target)

    result = PyBridge(app)("big 100", stdout_budget=36)
    full_output = BudgetApp.big_output()

    # The result retains only the beginning of the output.
    assert result.stdout == full_output[:36]
    assert result.stdout_truncated
    assert result.stdout_log is None

    # Everything beyond the budget continued to the original target.
    assert original_target.getvalue() == full_output[36:]


def test_py_bridge_zero_budget_passes_all_through() -> None:
    app = BudgetApp()
    original_target = StdSim(sys.stdout)
    app.stdout = cast(TextIO, original_target)

    result = PyBridge(app)("emit", stdout_budget=0)

    assert result.stdout == ""
    assert result.stdout_truncated
    assert original_target.getvalue() == "fixed-output\n"


def test_py_bridge_echo_with_budget_not_duplicated() -> None:
    app = BudgetApp()
    original_target = StdSim(sys.stdout)
    app.stdout = cast(TextIO, original_target)

    result = PyBridge(app)("emit", echo=True, stdout_budget=3)

    assert result.stdout == "fix"
    assert result.stdout_truncated
    # Echo delivers the complete output once; budget pass-through must not write it again.
    assert original_target.getvalue() == "fixed-output\n"


def test_py_bridge_spill_stdout_to_directory(tmp_path: pathlib.Path) -> None:
    log_dir = tmp_path / "created" / "on" / "demand"
    app = BudgetApp()

    result = PyBridge(app)("big 100", stdout_budget=64, overflow_dir=log_dir)
    full_output = BudgetApp.big_output()

    assert result.stdout == full_output[:64]
    assert result.stdout_truncated
    assert result.stderr_log is None

    # The spill file is the complete log: retained prefix followed by all overflow.
    assert result.stdout_log is not None
    spill_path = pathlib.Path(result.stdout_log)
    assert spill_path.parent == log_dir
    assert spill_path.read_text(encoding="utf-8") == full_output


def test_py_bridge_spill_stderr_to_directory(tmp_path: pathlib.Path) -> None:
    app = BudgetApp()

    result = PyBridge(app)("bigerr", stderr_budget=18, overflow_dir=tmp_path)
    full_error = BudgetApp.big_error()

    assert result.stderr == full_error[:18]
    assert result.stderr_truncated
    assert result.stdout_log is None

    assert result.stderr_log is not None
    assert pathlib.Path(result.stderr_log).read_text(encoding="utf-8") == full_error


def test_py_bridge_no_spill_file_within_budget(tmp_path: pathlib.Path) -> None:
    app = BudgetApp()
    result = PyBridge(app)("emit", stdout_budget=1024, overflow_dir=tmp_path)

    assert result.stdout == "fixed-output\n"
    assert not result.stdout_truncated
    assert result.stdout_log is None
    assert list(tmp_path.iterdir()) == []


def test_py_bridge_truncated_stderr_counts_as_failure() -> None:
    # A budget that leaves stderr empty must not make the result look successful.
    app = BudgetApp()
    result = PyBridge(app)("bigerr", stderr_budget=0)

    assert result.stderr == ""
    assert result.stderr_truncated
    assert not bool(result)


def test_py_bridge_negative_budget_raises() -> None:
    app = BudgetApp()
    with pytest.raises(ValueError, match="non-negative"):
        PyBridge(app)("big 100", stdout_budget=-1)


def test_py_bridge_budget_preserves_file_redirection(tmp_path: pathlib.Path) -> None:
    app = BudgetApp()
    out_file = tmp_path / "out.txt"

    # File redirection replaces app.stdout, so the command output bypasses capture exactly
    # as it does without a budget.
    result = PyBridge(app)(f"emit > {out_file}", stdout_budget=5)

    assert result.stdout == ""
    assert not result.stdout_truncated
    assert out_file.read_text(encoding="utf-8") == "fixed-output\n"


def test_py_bridge_budget_preserves_pipe(running_pipe_process, tmp_path: pathlib.Path) -> None:
    app = BudgetApp()

    # Piped shell output comes back through StdSim.buffer and must honor the budget as well.
    result = PyBridge(app)("same 50 | sort", stdout_budget=10, overflow_dir=tmp_path)

    assert result.stdout_truncated
    assert result.stdout_log is not None

    # The retained prefix is exactly the first 10 bytes of the complete piped output. Compare
    # against the spill bytes rather than a literal so line endings stay platform-independent.
    raw_log = pathlib.Path(result.stdout_log).read_bytes()
    assert result.stdout == raw_log[:10].decode("utf-8", errors="replace")

    lines = raw_log.decode("utf-8").splitlines()
    assert len(lines) == 50
    assert all(line == "same-line" for line in lines)


def test_py_bridge_nested_call_has_independent_budget() -> None:
    app = BudgetApp()
    full_output = BudgetApp.big_output()

    # The outer call has no budget. The nested call inside do_nested has a 20 byte budget.
    result = PyBridge(app)("nested")

    assert not result.stdout_truncated

    # The nested call retained only the first 20 bytes, surfaced through NESTED_PREFIX.
    assert "NESTED_PREFIX:" + full_output[:20].replace("\n", "|") in result.stdout

    # Its overflow reached the enclosing capture, starting exactly at the budget boundary.
    assert result.stdout.startswith(full_output[20:])
    assert "line-099" in result.stdout
    # The nested prefix itself did not leak into the enclosing capture except via NESTED_PREFIX.
    assert "line-000\n" not in result.stdout


def test_run_pyscript_stop(base_app, request) -> None:
    # Verify onecmd_plus_hooks() returns True if any commands in a pyscript return True for stop
    test_dir = os.path.dirname(request.module.__file__)

    # help.py doesn't run any commands that return True for stop
    python_script = os.path.join(test_dir, "pyscript", "help.py")
    stop = base_app.onecmd_plus_hooks(f"run_pyscript {python_script}")
    assert not stop

    # stop.py runs the quit command which does return True for stop
    python_script = os.path.join(test_dir, "pyscript", "stop.py")
    stop = base_app.onecmd_plus_hooks(f"run_pyscript {python_script}")
    assert stop


def test_run_pyscript_environment(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "environment.py")
    out, _err = run_cmd(base_app, f"run_pyscript {python_script}")

    assert out[0] == "PASSED"


def test_run_pyscript_self_in_py(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "self_in_py.py")

    # Set self_in_py to True and make sure we see self
    base_app.self_in_py = True
    out, _err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert "I see self" in out[0]

    # Set self_in_py to False and make sure we can't see self
    base_app.self_in_py = False
    out, _err = run_cmd(base_app, f"run_pyscript {python_script}")
    assert "I do not see self" in out[0]


def test_run_pyscript_py_locals(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "py_locals.py")

    # Make sure pyscripts can't edit Cmd.py_locals. It used to be that cmd2 was passing its py_locals
    # dictionary to the py environment instead of a shallow copy.
    base_app.py_locals["test_var"] = 5

    # Place an editable object in py_locals. Since we make a shallow copy of py_locals,
    # this object should be editable from the py environment.
    base_app.py_locals["my_list"] = []

    run_cmd(base_app, f"run_pyscript {python_script}")

    # test_var should still exist
    assert base_app.py_locals["test_var"] == 5

    # my_list should be edited
    assert base_app.py_locals["my_list"][0] == 2


def test_run_pyscript_app_echo(base_app, request) -> None:
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "echo.py")
    out, _err = run_cmd(base_app, f"run_pyscript {python_script}")

    # Only the edit help text should have been echoed to pytest's stdout
    assert out[0] == "Usage: edit [-h] [file_path]"


@with_ansi_style(ru.AllowStyle.ALWAYS)
def test_run_pyscript_print(base_app, request, capsys) -> None:
    """Verify that py_print() (the print() replacement in pyscripts) works correctly."""
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "test_print.py")
    out, err = run_cmd(base_app, f"run_pyscript {python_script}")

    # Verify contents of self.stdout
    assert len(out) == 4
    assert out[0] == "hello-world"
    assert out[1] == "no newline here"
    assert out[2] == "1:2:3."
    assert out[3] == "\x1b[34mI am Rich Text\x1b[0m"

    # Verify contents of sys.stderr
    assert len(err) == 1
    assert err[0] == "this goes to sys.stderr"

    # Verify contents of sys.stdout
    stdout, _ = capsys.readouterr()
    assert "this goes to sys.stdout" in stdout


def test_run_pyscript_print_redirection(base_app, request, tmp_path, capsys) -> None:
    """Verify that py_print() (the print() replacement in pyscripts) respects cmd2 redirection."""
    test_dir = os.path.dirname(request.module.__file__)
    python_script = os.path.join(test_dir, "pyscript", "test_print.py")
    out_file = tmp_path / "out.txt"

    # Run the pyscript with redirection
    base_app.onecmd_plus_hooks(f"run_pyscript {python_script} > {out_file}")
    out, err = capsys.readouterr()

    # Verify the output file contains what we expect from print()
    content = pathlib.Path(out_file).read_text(encoding="utf-8")

    # Look for everything written to self.stdout
    assert len(content.splitlines()) == 4
    assert "hello-world\n" in content
    assert "no newline here\n" in content
    assert "1:2:3.\n" in content
    assert "I am Rich Text\n" in content

    # Nothing else should have been redirected
    assert "this goes to sys.stdout" not in content
    assert "this goes to sys.stderr" not in content

    # Verify the remaining output when to the correct stream
    assert "this goes to sys.stdout" in out
    assert "this goes to sys.stderr" in err
