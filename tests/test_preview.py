"""Tests for the read-only command preview API: Cmd.preview() and cmd2.preview types."""

import os
import subprocess

import cmd2
from cmd2 import plugin
from cmd2.preview import (
    REDIRECT_KIND_CLIPBOARD,
    REDIRECT_KIND_FILE,
    REDIRECT_KIND_SHELL,
    PlanError,
    PlannedCommand,
    PlannedPyscript,
    PlannedScript,
)

from .conftest import run_cmd


def test_preview_single_string_is_one_command(base_app) -> None:
    plan = base_app.preview("help history")

    assert isinstance(plan.steps[0], PlannedCommand)
    step = plan.steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.command == "help"
    assert step.args == "history"
    assert step.argv == ("help", "history")
    assert step.expanded == "help history"
    assert step.raw == "help history"
    assert step.source is None
    assert step.line_number == 1
    assert step.recognized is True
    assert step.redirection is None
    assert step.shell_command is None
    assert plan.ok is True
    assert plan.errors == ()


def test_preview_does_not_execute_commands(base_app, monkeypatch) -> None:
    def fail_command(_statement):
        raise AssertionError("preview must not execute command functions")

    monkeypatch.setattr(base_app, "do_help", fail_command)
    monkeypatch.setattr(base_app, "default", fail_command)

    base_app.preview(["help", "not_a_real_command"])

    # History is only appended when a command actually runs
    assert len(base_app.history) == 0


def test_preview_does_not_run_hooks(base_app) -> None:
    def fail_postparsing(data: plugin.PostparsingData) -> plugin.PostparsingData:
        raise AssertionError("preview must not run postparsing hooks")

    def fail_precmd(data: plugin.PrecommandData) -> plugin.PrecommandData:
        raise AssertionError("preview must not run precmd hooks")

    def fail_postcmd(data: plugin.PostcommandData) -> plugin.PostcommandData:
        raise AssertionError("preview must not run postcmd hooks")

    def fail_finalization(data: plugin.CommandFinalizationData) -> plugin.CommandFinalizationData:
        raise AssertionError("preview must not run command finalization hooks")

    base_app.register_postparsing_hook(fail_postparsing)
    base_app.register_precmd_hook(fail_precmd)
    base_app.register_postcmd_hook(fail_postcmd)
    base_app.register_cmdfinalization_hook(fail_finalization)

    step = base_app.preview("help").steps[0]
    assert isinstance(step, PlannedCommand)


def test_preview_does_not_mutate_app_state(base_app, request) -> None:
    script_path = os.path.join(os.path.dirname(request.module.__file__), "scripts", "nested.txt")

    base_app.preview(f"run_script {script_path}")

    assert base_app._script_dir == []
    assert base_app._current_script_dir is None
    assert len(base_app.history) == 0


def test_preview_alias_expansion(base_app) -> None:
    run_cmd(base_app, "alias create hlp help")

    step = base_app.preview("hlp history").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.raw == "hlp history"
    assert step.command == "help"
    assert step.args == "history"
    assert step.argv == ("help", "history")
    assert step.expanded == "help history"


def test_preview_nested_alias_expansion(base_app) -> None:
    run_cmd(base_app, "alias create nested help")
    run_cmd(base_app, "alias create wrapper nested")

    step = base_app.preview("wrapper").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.command == "help"


def test_preview_shortcut_expansion(base_app) -> None:
    step = base_app.preview("? history").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.raw == "? history"
    assert step.command == "help"
    assert step.args == "history"


def test_preview_macro_expansion(base_app) -> None:
    run_cmd(base_app, "macro create fake help {1}")

    step = base_app.preview("fake alias").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.raw == "fake alias"
    assert step.command == "help"
    assert step.args == "alias"
    assert step.argv == ("help", "alias")


def test_preview_macro_extra_args_are_appended(base_app) -> None:
    run_cmd(base_app, "macro create fake help {1}")

    step = base_app.preview("fake alias create").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.command == "help"
    assert step.args == "alias create"
    assert step.argv == ("help", "alias", "create")


def test_preview_macro_missing_args_is_plan_error(base_app) -> None:
    run_cmd(base_app, "macro create fake help {1} {2}")

    plan = base_app.preview("fake only_one_arg")
    error = plan.errors[0]
    assert isinstance(error, PlanError)
    assert "expects at least 2 arguments" in error.message
    assert error.raw == "fake only_one_arg"
    assert error.line_number == 1
    assert plan.ok is False


def test_preview_shell_command_does_not_spawn_process(base_app, monkeypatch) -> None:
    def fail_popen(*_args, **_kwargs):
        raise AssertionError("preview must not start shell processes")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    step = base_app.preview("!ls -l /tmp").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.command == "shell"
    assert step.shell_command == "ls -l /tmp"
    assert step.argv == ("shell", "ls", "-l", "/tmp")


def test_preview_shell_preserves_quotes(base_app, monkeypatch) -> None:
    def fail_popen(*_args, **_kwargs):
        raise AssertionError("preview must not start shell processes")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    step = base_app.preview('shell echo "hello world"').steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.shell_command == 'echo "hello world"'


def test_preview_pipe_redirection(base_app, monkeypatch) -> None:
    def fail_popen(*_args, **_kwargs):
        raise AssertionError("preview must not start pipe processes")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    step = base_app.preview("help | grep history").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.redirection is not None
    assert step.redirection.operator == "|"
    assert step.redirection.kind == REDIRECT_KIND_SHELL
    assert step.redirection.target == "grep history"
    assert step.redirection.mode is None


def test_preview_file_redirection_is_described_not_written(base_app, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "out.txt"

    step = base_app.preview("help > out.txt").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.redirection is not None
    assert step.redirection.kind == REDIRECT_KIND_FILE
    assert step.redirection.operator == ">"
    assert step.redirection.mode == "w"
    assert step.redirection.target == "out.txt"
    assert not target.exists()

    append_step = base_app.preview("help >> out.txt").steps[0]
    assert isinstance(append_step, PlannedCommand)
    assert append_step.redirection is not None
    assert append_step.redirection.operator == ">>"
    assert append_step.redirection.mode == "a"
    assert not target.exists()


def test_preview_clipboard_redirection_does_not_touch_clipboard(base_app, monkeypatch) -> None:
    def fail_clipboard(*_args, **_kwargs):
        raise AssertionError("preview must not access the clipboard")

    monkeypatch.setattr("cmd2.cmd2.get_paste_buffer", fail_clipboard)
    monkeypatch.setattr("cmd2.cmd2.write_to_paste_buffer", fail_clipboard)

    step = base_app.preview("help >").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.redirection is not None
    assert step.redirection.kind == REDIRECT_KIND_CLIPBOARD
    assert step.redirection.target == ""
    assert step.redirection.error is None


def test_preview_clipboard_disabled_is_reported(base_app) -> None:
    base_app.allow_clipboard = False

    step = base_app.preview("help >").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.redirection is not None
    assert step.redirection.kind == REDIRECT_KIND_CLIPBOARD
    assert step.redirection.error == "Clipboard access not allowed"


def test_preview_redirection_disabled_is_marked_inactive(base_app) -> None:
    base_app.allow_redirection = False

    step = base_app.preview("help > out.txt").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.redirection is not None
    assert step.redirection.active is False


def test_preview_run_script_expands_nested_scripts(base_app, request) -> None:
    scripts_dir = os.path.join(os.path.dirname(request.module.__file__), "scripts")
    top = os.path.join(scripts_dir, "nested.txt")

    root = base_app.preview(f"run_script {top}").steps[0]
    assert isinstance(root, PlannedScript)
    assert root.command == "run_script"
    assert root.path == os.path.abspath(top)
    assert root.relative is False
    assert root.source is None

    pre, help_step, shortcuts_step, post = root.children
    assert isinstance(pre, PlannedScript)
    assert pre.relative is True
    assert pre.path == os.path.join(scripts_dir, "precmds.txt")
    assert pre.source == root.path
    pre_command = pre.children[0]
    assert isinstance(pre_command, PlannedCommand)
    assert pre_command.command == "set"
    assert pre_command.args == "allow_style Always"
    assert pre_command.source == pre.path
    assert pre_command.line_number == 1

    assert isinstance(help_step, PlannedCommand)
    assert help_step.command == "help"
    assert help_step.line_number == 2
    assert isinstance(shortcuts_step, PlannedCommand)
    assert shortcuts_step.command == "shortcuts"

    assert isinstance(post, PlannedScript)
    assert post.path == os.path.join(scripts_dir, "postcmds.txt")
    assert isinstance(post.children[0], PlannedCommand)
    assert post.children[0].args == "allow_style Terminal"

    flattened = [(c.command, c.args) for c in base_app.preview(f"run_script {top}").commands()]
    assert flattened == [
        ("set", "allow_style Always"),
        ("help", ""),
        ("shortcuts", ""),
        ("set", "allow_style Terminal"),
    ]


def test_preview_run_script_shortcut(base_app, request) -> None:
    script_path = os.path.join(os.path.dirname(request.module.__file__), "scripts", "precmds.txt")

    root = base_app.preview(f"@{script_path}").steps[0]
    assert isinstance(root, PlannedScript)
    assert root.path == os.path.abspath(script_path)


def test_preview_relative_run_script_shortcut_uses_script_dir(base_app, request, tmp_path) -> None:
    # A script in tmp_path which includes by relative path; the include must resolve beside it
    included = tmp_path / "inner.txt"
    included.write_text("help\n", encoding="utf-8")
    outer = tmp_path / "outer.txt"
    outer.write_text("@@ inner.txt\n", encoding="utf-8")

    root = base_app.preview(f"run_script {outer}").steps[0]
    assert isinstance(root, PlannedScript)
    nested = root.children[0]
    assert isinstance(nested, PlannedScript)
    assert nested.path == str(included)
    assert isinstance(nested.children[0], PlannedCommand)
    assert nested.children[0].command == "help"


def test_preview_run_script_redirection_is_on_node(base_app, request) -> None:
    script_path = os.path.join(os.path.dirname(request.module.__file__), "scripts", "precmds.txt")

    root = base_app.preview(f"run_script {script_path} > out.txt").steps[0]
    assert isinstance(root, PlannedScript)
    assert root.redirection is not None
    assert root.redirection.kind == REDIRECT_KIND_FILE
    assert root.redirection.target == "out.txt"


def test_preview_missing_script_is_plan_error(base_app) -> None:
    root = base_app.preview("run_script /does/not/exist.txt").steps[0]
    assert isinstance(root, PlanError)
    assert "Problem accessing script" in root.message
    assert base_app.preview("run_script /does/not/exist.txt").ok is False


def test_preview_run_script_without_path_is_plan_error(base_app) -> None:
    root = base_app.preview("run_script").steps[0]
    assert isinstance(root, PlanError)
    assert "requires a script path" in root.message


def test_preview_binary_script_is_plan_error(base_app, request) -> None:
    script_path = os.path.join(os.path.dirname(request.module.__file__), "scripts", "binary.bin")

    root = base_app.preview(f"run_script {script_path}").steps[0]
    assert isinstance(root, PlanError)
    assert "not an ASCII or UTF-8 encoded text file" in root.message


def test_preview_empty_script_has_no_children_or_errors(base_app, request) -> None:
    script_path = os.path.join(os.path.dirname(request.module.__file__), "scripts", "empty.txt")

    root = base_app.preview(f"run_script {script_path}").steps[0]
    assert isinstance(root, PlannedScript)
    assert root.children == ()
    assert base_app.preview(f"run_script {script_path}").ok is True


def test_preview_recursive_script_inclusion_is_cut_off(base_app, tmp_path) -> None:
    script_a = tmp_path / "a.txt"
    script_b = tmp_path / "b.txt"
    script_a.write_text(f"_relative_run_script {script_b}\nhelp\n", encoding="utf-8")
    script_b.write_text(f"_relative_run_script {script_a}\n", encoding="utf-8")

    # Building the plan must terminate despite the include cycle
    root = base_app.preview(f"run_script {script_a}").steps[0]
    assert isinstance(root, PlannedScript)
    nested_b = root.children[0]
    assert isinstance(nested_b, PlannedScript)
    nested_a = nested_b.children[0]
    assert isinstance(nested_a, PlannedScript)
    cycle_error = nested_a.children[0]
    assert isinstance(cycle_error, PlanError)
    assert "never terminate" in cycle_error.message
    # The command after the cycle in a.txt is still planned
    assert isinstance(root.children[1], PlannedCommand)


def test_preview_python_script_is_opaque(base_app, monkeypatch) -> None:
    def fail_run_python(*_args, **_kwargs):
        raise AssertionError("preview must not execute Python scripts")

    monkeypatch.setattr(base_app, "_run_python", fail_run_python)

    step = base_app.preview("run_pyscript my_script.py arg1 arg2 > out.txt").steps[0]
    assert isinstance(step, PlannedPyscript)
    assert step.path == "my_script.py"
    assert step.arguments == ("arg1", "arg2")
    assert step.redirection is not None
    assert step.redirection.kind == REDIRECT_KIND_FILE
    assert "run_pyscript my_script.py arg1 arg2 > out.txt" in step.expanded


def test_preview_python_script_without_path_is_plan_error(base_app) -> None:
    root = base_app.preview("run_pyscript").steps[0]
    assert isinstance(root, PlanError)


def test_preview_comments_and_blank_lines_produce_no_steps(base_app) -> None:
    plan = base_app.preview(["# this is a comment", "", "   ", "help"])
    assert len(plan.steps) == 1
    assert isinstance(plan.steps[0], PlannedCommand)
    assert plan.steps[0].command == "help"


def test_preview_batch_line_numbers(base_app) -> None:
    plan = base_app.preview(["help", "shortcuts", "quit"])
    assert [step.line_number for step in plan.steps] == [1, 2, 3]
    assert [step.command for step in plan.steps if isinstance(step, PlannedCommand)] == [
        "help",
        "shortcuts",
        "quit",
    ]


def test_preview_accepts_history_items(base_app) -> None:
    run_cmd(base_app, "help")
    run_cmd(base_app, "shortcuts")

    plan = base_app.preview(list(base_app.history))
    commands = [step.command for step in plan.steps if isinstance(step, PlannedCommand)]
    assert commands == ["help", "shortcuts"]
    # Previewing history must not add the previewed items again
    assert len(base_app.history) == 2


def test_preview_syntax_error_is_plan_error(base_app) -> None:
    root = base_app.preview('help "unclosed quote').steps[0]
    assert isinstance(root, PlanError)
    assert "Invalid syntax" in root.message


def test_preview_incomplete_multiline_command_is_plan_error() -> None:
    app = cmd2.Cmd(multiline_commands=["multiline"])
    root = app.preview("multiline some args").steps[0]
    assert isinstance(root, PlanError)
    assert "Incomplete multiline command" in root.message


def test_preview_unrecognized_command_is_flagged(base_app, monkeypatch) -> None:
    def fail_default(_statement):
        raise AssertionError("preview must not call default()")

    monkeypatch.setattr(base_app, "default", fail_default)

    step = base_app.preview("frobnicate the_thing").steps[0]
    assert isinstance(step, PlannedCommand)
    assert step.command == "frobnicate"
    assert step.recognized is False


def test_preview_walk_depth_and_render(base_app, tmp_path) -> None:
    included = tmp_path / "inner.txt"
    included.write_text("shortcuts\n", encoding="utf-8")
    outer = tmp_path / "outer.txt"
    outer.write_text("help\n@@ inner.txt\n", encoding="utf-8")

    plan = base_app.preview([f"run_script {outer}", "!ls"])
    walked = [(depth, type(step).__name__) for depth, step in plan.walk()]
    assert walked == [
        (0, "PlannedScript"),
        (1, "PlannedCommand"),
        (1, "PlannedScript"),
        (2, "PlannedCommand"),
        (0, "PlannedCommand"),
    ]

    rendered = plan.render()
    assert "run_script" in rendered
    assert str(outer) in rendered
    assert "help" in rendered
    assert "shortcuts" in rendered
    assert "shell: ls" in rendered
