"""Unit testing for cmd2/utils.py module."""

import io
import math
import os
import pathlib
import signal
import sys
import time
from unittest import (
    mock,
)

import pytest

import cmd2.utils as cu

HELLO_WORLD = "Hello, world!"


def test_remove_duplicates_no_duplicates() -> None:
    no_dups = [5, 4, 3, 2, 1]
    assert cu.remove_duplicates(no_dups) == no_dups


def test_remove_duplicates_with_duplicates() -> None:
    duplicates = [1, 1, 2, 3, 9, 9, 7, 8]
    assert cu.remove_duplicates(duplicates) == [1, 2, 3, 9, 7, 8]


def test_alphabetical_sort() -> None:
    my_list = ["café", "µ", "A", "micro", "unity", "cafeteria"]
    assert cu.alphabetical_sort(my_list) == ["A", "cafeteria", "café", "micro", "unity", "µ"]
    my_list = ["a3", "a22", "A2", "A11", "a1"]
    assert cu.alphabetical_sort(my_list) == ["a1", "A11", "A2", "a22", "a3"]


def test_try_int_or_force_to_lower_case() -> None:
    str1 = "17"
    assert cu.try_int_or_force_to_lower_case(str1) == 17
    str1 = "ABC"
    assert cu.try_int_or_force_to_lower_case(str1) == "abc"
    str1 = "X19"
    assert cu.try_int_or_force_to_lower_case(str1) == "x19"
    str1 = ""
    assert cu.try_int_or_force_to_lower_case(str1) == ""


def test_natural_keys() -> None:
    my_list = ["café", "µ", "A", "micro", "unity", "x1", "X2", "X11", "X0", "x22"]
    my_list.sort(key=cu.natural_keys)
    assert my_list == ["A", "café", "micro", "unity", "X0", "x1", "X2", "X11", "x22", "µ"]
    my_list = ["a3", "a22", "A2", "A11", "a1"]
    my_list.sort(key=cu.natural_keys)
    assert my_list == ["a1", "A2", "a3", "A11", "a22"]


def test_natural_sort() -> None:
    my_list = ["café", "µ", "A", "micro", "unity", "x1", "X2", "X11", "X0", "x22"]
    assert cu.natural_sort(my_list) == ["A", "café", "micro", "unity", "X0", "x1", "X2", "X11", "x22", "µ"]
    my_list = ["a3", "a22", "A2", "A11", "a1"]
    assert cu.natural_sort(my_list) == ["a1", "A2", "a3", "A11", "a22"]


@pytest.fixture
def stdout_sim():
    return cu.StdSim(sys.stdout, echo=True)


def test_stdsim_write_str(stdout_sim) -> None:
    my_str = "Hello World"
    stdout_sim.write(my_str)
    assert stdout_sim.getvalue() == my_str


def test_stdsim_write_bytes(stdout_sim) -> None:
    b_str = b"Hello World"
    with pytest.raises(TypeError):
        stdout_sim.write(b_str)


def test_stdsim_buffer_write_bytes(stdout_sim) -> None:
    b_str = b"Hello World"
    stdout_sim.buffer.write(b_str)
    assert stdout_sim.getvalue() == b_str.decode()
    assert stdout_sim.getbytes() == b_str


def test_stdsim_buffer_write_str(stdout_sim) -> None:
    my_str = "Hello World"
    with pytest.raises(TypeError):
        stdout_sim.buffer.write(my_str)


def test_stdsim_read(stdout_sim) -> None:
    my_str = "Hello World"
    stdout_sim.write(my_str)
    # getvalue() returns the value and leaves it unaffected internally
    assert stdout_sim.getvalue() == my_str
    # read() returns the value and then clears the internal buffer
    assert stdout_sim.read() == my_str
    assert stdout_sim.getvalue() == ""

    stdout_sim.write(my_str)

    assert stdout_sim.getvalue() == my_str
    assert stdout_sim.read(2) == my_str[:2]
    assert stdout_sim.getvalue() == my_str[2:]


def test_stdsim_read_bytes(stdout_sim) -> None:
    b_str = b"Hello World"
    stdout_sim.buffer.write(b_str)
    # getbytes() returns the value and leaves it unaffected internally
    assert stdout_sim.getbytes() == b_str
    # read_bytes() returns the value and then clears the internal buffer
    assert stdout_sim.readbytes() == b_str
    assert stdout_sim.getbytes() == b""


def test_stdsim_clear(stdout_sim) -> None:
    my_str = "Hello World"
    stdout_sim.write(my_str)
    assert stdout_sim.getvalue() == my_str
    stdout_sim.clear()
    assert stdout_sim.getvalue() == ""


def test_stdsim_getattr_exist(stdout_sim) -> None:
    # Here the StdSim getattr is allowing us to access methods within StdSim
    my_str = "Hello World"
    stdout_sim.write(my_str)
    val_func = stdout_sim.getvalue
    assert val_func() == my_str


def test_stdsim_getattr_noexist(stdout_sim) -> None:
    # Here the StdSim getattr is allowing us to access methods defined by the inner stream
    assert not stdout_sim.isatty()


def test_stdsim_pause_storage(stdout_sim) -> None:
    # Test pausing storage for string data
    my_str = "Hello World"

    stdout_sim.pause_storage = False
    stdout_sim.write(my_str)
    assert stdout_sim.read() == my_str

    stdout_sim.pause_storage = True
    stdout_sim.write(my_str)
    assert stdout_sim.read() == ""

    # Test pausing storage for binary data
    b_str = b"Hello World"

    stdout_sim.pause_storage = False
    stdout_sim.buffer.write(b_str)
    assert stdout_sim.readbytes() == b_str

    stdout_sim.pause_storage = True
    stdout_sim.buffer.write(b_str)
    assert stdout_sim.getbytes() == b""


def test_stdsim_line_buffering(base_app) -> None:
    # This exercises the case of writing binary data that contains new lines/carriage returns to a StdSim
    # when line buffering is on. The output should immediately be flushed to the underlying stream.
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="wt") as file:
        file.line_buffering = True

        stdsim = cu.StdSim(file, echo=True)
        saved_size = os.path.getsize(file.name)

        bytes_to_write = b"hello\n"
        stdsim.buffer.write(bytes_to_write)
        assert os.path.getsize(file.name) == saved_size + len(bytes_to_write)
        saved_size = os.path.getsize(file.name)

        bytes_to_write = b"hello\r"
        stdsim.buffer.write(bytes_to_write)
        assert os.path.getsize(file.name) == saved_size + len(bytes_to_write)


def test_stdsim_budget_negative_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        cu.StdSim(io.StringIO(), max_bytes=-1)


def test_stdsim_budget_retains_prefix_and_passes_through() -> None:
    # No echo and no spill directory: the prefix stays in the buffer, overflow continues
    # to the original target. The boundary may fall in the middle of a write.
    sink = io.StringIO()
    stdsim = cu.StdSim(sink, max_bytes=5)

    stdsim.write("Hel")
    stdsim.write("lo,")
    stdsim.write(" World")

    assert stdsim.getvalue() == "Hello"
    assert stdsim.truncated
    assert stdsim.total_bytes == len("Hello, World")
    assert sink.getvalue() == ", World"


def test_stdsim_budget_zero_passes_everything_through() -> None:
    sink = io.StringIO()
    stdsim = cu.StdSim(sink, max_bytes=0)
    stdsim.write("Hello")
    assert stdsim.getvalue() == ""
    assert stdsim.truncated
    assert sink.getvalue() == "Hello"


def test_stdsim_budget_not_exceeded() -> None:
    sink = io.StringIO()
    stdsim = cu.StdSim(sink, max_bytes=100)
    stdsim.write("Hello")
    assert stdsim.getvalue() == "Hello"
    assert not stdsim.truncated
    assert sink.getvalue() == ""


def test_stdsim_budget_paused_writes_do_not_count() -> None:
    sink = io.StringIO()
    stdsim = cu.StdSim(sink, max_bytes=5)

    stdsim.pause_storage = True
    stdsim.write("a much longer string written while storage is paused")
    assert stdsim.getvalue() == ""
    assert not stdsim.truncated

    stdsim.pause_storage = False
    stdsim.write("123456")
    assert stdsim.getvalue() == "12345"
    assert stdsim.truncated


def test_stdsim_budget_binary_passes_through_nested_stdsim() -> None:
    # Overflow written through ByteBuf must reach a wrapping (outer) StdSim, where it is
    # accounted against that outer StdSim's own settings.
    outer = cu.StdSim(io.StringIO())
    inner = cu.StdSim(outer, max_bytes=3)

    inner.buffer.write(b"abcdef")

    assert inner.getvalue() == "abc"
    assert inner.truncated
    assert outer.getbytes() == b"def"


def test_stdsim_budget_echo_does_not_duplicate_overflow() -> None:
    sink = io.StringIO()
    stdsim = cu.StdSim(sink, echo=True, max_bytes=3)
    stdsim.write("abcdef")

    # Echo sends the complete write to the sink exactly once; overflow is not written again.
    assert stdsim.getvalue() == "abc"
    assert sink.getvalue() == "abcdef"


def test_stdsim_budget_split_multibyte_char_does_not_raise() -> None:
    sink = io.StringIO()
    stdsim = cu.StdSim(sink, max_bytes=1)
    stdsim.write("é")
    # The default errors="replace" policy must keep prefix decoding working at the split byte.
    assert stdsim.getvalue() == "\ufffd"
    assert sink.getvalue()


def test_stdsim_spill_file_holds_complete_output(tmp_path: pathlib.Path) -> None:
    log_dir = tmp_path / "nested" / "logs"
    stdsim = cu.StdSim(io.StringIO(), max_bytes=5, spill_dir=log_dir, spill_prefix="test-")

    stdsim.write("Hello, ")
    stdsim.write("World")

    assert stdsim.getvalue() == "Hello"
    assert stdsim.truncated
    assert stdsim.spill_path is not None

    spill_path = pathlib.Path(stdsim.spill_path)
    assert spill_path.parent == log_dir
    assert spill_path.name.startswith("test-")
    assert spill_path.read_text(encoding="utf-8") == "Hello, World"

    stdsim.close_spill_file()


def test_stdsim_spill_binary_path(tmp_path: pathlib.Path) -> None:
    stdsim = cu.StdSim(io.StringIO(), max_bytes=3, spill_dir=tmp_path)
    stdsim.buffer.write(b"abcdef")

    assert stdsim.getbytes() == b"abc"
    assert stdsim.spill_path is not None
    assert pathlib.Path(stdsim.spill_path).read_bytes() == b"abcdef"
    stdsim.close_spill_file()


def test_stdsim_no_spill_file_within_budget(tmp_path: pathlib.Path) -> None:
    stdsim = cu.StdSim(io.StringIO(), max_bytes=100, spill_dir=tmp_path)
    stdsim.write("hi")
    assert not stdsim.truncated
    assert stdsim.spill_path is None
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def pr_none():
    import subprocess

    # Start a long running process so we have time to run tests on it before it finishes
    # Put the new process into a separate group so its signal are isolated from ours
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    # The child restores the default SIGINT action so that, like `sleep`, it dies with -SIGINT on POSIX
    child_code = "import signal, time; signal.signal(signal.SIGINT, signal.SIG_DFL); print('ready', flush=True); time.sleep(5)"
    proc = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, **kwargs)

    # Wait for the child to report that it is running before returning it to a test. Signaling a Windows console
    # process before it has finished initializing makes it fail with STATUS_DLL_INIT_FAILED (0xc0000142) and pop up
    # an "Application Error" dialog that blocks the test run until someone clicks OK.
    assert proc.stdout.readline().strip() == b"ready"
    return cu.ProcReader(proc, sys.stdout, sys.stderr)


def test_proc_reader_send_sigint(pr_none) -> None:
    assert pr_none._proc.poll() is None
    pr_none.send_sigint()
    pr_none.wait()
    ret_code = pr_none._proc.poll()

    # Make sure a SIGINT killed the process
    if sys.platform.startswith("win"):
        assert ret_code is not None
    else:
        assert ret_code == -signal.SIGINT


def test_proc_reader_terminate(pr_none) -> None:
    assert pr_none._proc.poll() is None
    pr_none.terminate()

    wait_start = time.monotonic()
    pr_none.wait()
    wait_finish = time.monotonic()

    # Make sure the process exited before sleep of 5 seconds finished
    # 3 seconds accounts for some delay but is long enough for the process to exit
    assert wait_finish - wait_start < 3

    ret_code = pr_none._proc.poll()
    if sys.platform.startswith("win"):
        assert ret_code is not None
    else:
        assert ret_code == -signal.SIGTERM


@pytest.fixture
def context_flag():
    return cu.ContextFlag()


def test_context_flag_bool(context_flag) -> None:
    assert not context_flag
    with context_flag:
        assert context_flag


def test_context_flag_exit_err(context_flag) -> None:
    with pytest.raises(ValueError, match="count has gone below 0"):
        context_flag.__exit__()


def test_to_bool_str_true() -> None:
    assert cu.to_bool("true")
    assert cu.to_bool("True")
    assert cu.to_bool("TRUE")
    assert cu.to_bool("tRuE")


def test_to_bool_str_false() -> None:
    assert not cu.to_bool("false")
    assert not cu.to_bool("False")
    assert not cu.to_bool("FALSE")
    assert not cu.to_bool("fAlSe")


def test_to_bool_str_invalid() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        cu.to_bool("other")


def test_to_bool_bool() -> None:
    assert cu.to_bool(True)
    assert not cu.to_bool(False)


def test_to_bool_int() -> None:
    assert cu.to_bool(1)
    assert cu.to_bool(-1)
    assert not cu.to_bool(0)


def test_to_bool_float() -> None:
    assert cu.to_bool(2.35)
    assert cu.to_bool(0.25)
    assert cu.to_bool(-math.pi)
    assert not cu.to_bool(0)


def test_optional_int_none() -> None:
    assert cu.optional_int(None) is None
    assert cu.optional_int("none") is None
    assert cu.optional_int("None") is None
    assert cu.optional_int("nOnE") is None


def test_optional_int_int() -> None:
    assert cu.optional_int(5) == 5
    assert cu.optional_int("5") == 5
    assert cu.optional_int("-10") == -10


def test_optional_int_invalid() -> None:
    with pytest.raises(ValueError, match="must be an integer or None"):
        cu.optional_int("abc")
    with pytest.raises(ValueError, match="must be an integer or None"):
        cu.optional_int("3.14")
    with pytest.raises(ValueError, match="must be an integer or None"):
        cu.optional_int([])


def test_find_editor_specified() -> None:
    expected_editor = os.path.join("fake_dir", "editor")
    with mock.patch.dict(os.environ, {"EDITOR": expected_editor}):
        editor = cu.find_editor()
    assert editor == expected_editor


def test_find_editor_not_specified() -> None:
    # Use existing path env setting. Something in the editor list should be found.
    editor = cu.find_editor()
    assert editor

    # Overwrite path env setting with invalid path, clear all other env vars so no editor should be found.
    with mock.patch.dict(os.environ, {"PATH": "fake_dir"}, clear=True):
        editor = cu.find_editor()
    assert editor is None


def test_similarity() -> None:
    suggested_command = cu.suggest_similar("comand", ["command", "UNRELATED", "NOT_SIMILAR"])
    assert suggested_command == "command"
    suggested_command = cu.suggest_similar("command", ["COMMAND", "acommands"])
    assert suggested_command == "COMMAND"


def test_similarity_without_good_canididates() -> None:
    suggested_command = cu.suggest_similar("comand", ["UNRELATED", "NOT_SIMILAR"])
    assert suggested_command is None
    suggested_command = cu.suggest_similar("comand", [])
    assert suggested_command is None


def test_similarity_overwrite_function() -> None:
    options = ["history", "test"]
    suggested_command = cu.suggest_similar("test", options)
    assert suggested_command == "test"

    def custom_similarity_function(s1, s2) -> float:
        return 1.0 if "history" in (s1, s2) else 0.0

    suggested_command = cu.suggest_similar("test", options, similarity_function_to_use=custom_similarity_function)
    assert suggested_command == "history"

    suggested_command = cu.suggest_similar("history", options, similarity_function_to_use=custom_similarity_function)
    assert suggested_command == "history"

    suggested_command = cu.suggest_similar("test", ["test"], similarity_function_to_use=custom_similarity_function)
    assert suggested_command is None


def test_get_types_invalid_input() -> None:
    x = 1
    with pytest.raises(ValueError, match="Argument passed to get_types should be a function or method"):
        cu.get_types(x)


def test_get_types_empty() -> None:
    def a(b):
        print(b)

    param_ann, ret_ann = cu.get_types(a)
    assert ret_ann is None
    assert param_ann == {}


def test_get_types_non_empty() -> None:
    def foo(x: int) -> str:
        return f"{x * x}"

    param_ann, ret_ann = cu.get_types(foo)
    assert ret_ann is str
    param_name, param_value = next(iter(param_ann.items()))
    assert param_name == "x"
    assert param_value is int


def test_get_types_method() -> None:
    class Foo:
        def bar(self, x: bool) -> None:
            print(x)

    f = Foo()

    param_ann, ret_ann = cu.get_types(f.bar)
    assert ret_ann is None
    assert len(param_ann) == 1
    param_name, param_value = next(iter(param_ann.items()))
    assert param_name == "x"
    assert param_value is bool


def test_categorize() -> None:
    from cmd2 import constants

    category = "Test Category"
    attr_name = constants.COMMAND_ATTR_HELP_CATEGORY

    # Test single function
    def func1() -> None:
        pass

    cu.categorize(func1, category)
    assert getattr(func1, attr_name) == category

    # Test single method
    class Foo:
        def foo_method(self) -> None:
            pass

    f = Foo()
    cu.categorize(f.foo_method, category)
    assert getattr(Foo.foo_method, attr_name) == category

    # Test iterable
    def func2() -> None:
        pass

    class Bar:
        def bar_method(self) -> None:
            pass

    b = Bar()
    cu.categorize([func2, b.bar_method], category)
    assert getattr(func2, attr_name) == category
    assert getattr(Bar.bar_method, attr_name) == category
