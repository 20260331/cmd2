"""Tests for sensitive argparse argument redaction.

An argument declared with ``sensitive=True`` keeps its real value inside the
command function, but cmd2-managed history, echo output, and argparse diagnostics
receive a redaction placeholder. Redacted history records can't be replayed.
"""

import contextlib
import io
import os
import tempfile
from typing import Annotated

import pytest

import cmd2
from cmd2 import (
    constants,
    redaction,
)
from cmd2.annotated import (
    Argument,
    Option,
    build_parser_from_function,
    with_annotated,
)
from cmd2.history import History
from cmd2.pt_utils import Cmd2History

from .conftest import run_cmd

SECRET = "s3cr3t-token"
PLACEHOLDER = constants.SENSITIVE_ARG_PLACEHOLDER


class SensitiveArgsApp(cmd2.Cmd):
    """App with a sensitive option, a sensitive positional, and a choice argument."""

    def __init__(self, *args, **kwargs) -> None:
        self.login_count = 0
        self.last_token = None
        self.last_password = None
        super().__init__(*args, **kwargs)

    login_parser = cmd2.Cmd2ArgumentParser()
    login_parser.add_argument("--user", help="user name")
    login_parser.add_argument("--token", sensitive=True, help="API token")
    login_parser.add_argument("--codes", nargs="+", type=int, sensitive=True, help="one-time codes")
    login_parser.add_argument("password", sensitive=True, help="account password")

    @cmd2.with_argparser(login_parser)
    def do_login(self, args) -> None:
        """Log in with a token and password."""
        self.login_count += 1
        self.last_token = args.token
        self.last_password = args.password
        self.poutput(f"logged in with token {args.token}")

    plain_parser = cmd2.Cmd2ArgumentParser()
    plain_parser.add_argument("--value")

    @cmd2.with_argparser(plain_parser)
    def do_plain(self, args) -> None:
        """A command with no sensitive arguments."""
        self.poutput(args.value)

    select_parser = cmd2.Cmd2ArgumentParser()
    select_parser.add_argument("--mode", choices=["a", "b"], sensitive=True)

    @cmd2.with_argparser(select_parser)
    def do_select(self, args) -> None:
        """Select a mode."""
        self.poutput(args.mode)


@pytest.fixture
def app() -> SensitiveArgsApp:
    return SensitiveArgsApp()


# ---------------------------------------------------------------------------
# Declaration / registration
# ---------------------------------------------------------------------------


def test_sensitive_action_attribute() -> None:
    parser = cmd2.Cmd2ArgumentParser()
    action = parser.add_argument("--token", sensitive=True)
    assert action.get_sensitive() is True  # type: ignore[attr-defined]

    default_action = parser.add_argument("--user")
    assert default_action.get_sensitive() is False  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "action_kwargs",
    [
        {"action": "store_true"},
        {"action": "store_false"},
        {"action": "store_const", "const": "x"},
        {"action": "count"},
    ],
)
def test_sensitive_rejected_for_value_less_actions(action_kwargs) -> None:
    parser = cmd2.Cmd2ArgumentParser()
    with pytest.raises(ValueError, match="sensitive"):
        parser.add_argument("--flag", sensitive=True, **action_kwargs)


# ---------------------------------------------------------------------------
# Command receives the real value, history gets a redacted copy
# ---------------------------------------------------------------------------


def test_command_receives_real_value(app) -> None:
    out, err = run_cmd(app, f"login --token {SECRET} my-password")
    assert not err
    assert SECRET in " ".join(out)
    assert app.last_token == SECRET
    assert app.last_password == "my-password"
    assert app.login_count == 1


def test_history_stores_redacted_statement(app) -> None:
    run_cmd(app, f"login --user alice --token {SECRET} my-password")

    item = app.history[-1]
    assert item.statement.redacted is True
    assert SECRET not in item.raw
    assert "my-password" not in item.raw
    assert item.raw == f"login --user alice --token {PLACEHOLDER} {PLACEHOLDER}"
    # The command still ran against the original values
    assert app.last_token == SECRET


def test_nonsensitive_command_not_marked(app) -> None:
    run_cmd(app, f"plain --value {SECRET}")
    item = app.history[-1]
    assert item.statement.redacted is False
    assert SECRET in item.raw


def test_redacted_option_forms(app) -> None:
    cases = {
        f"login --token={SECRET} pw": f"login --token={PLACEHOLDER} {PLACEHOLDER}",
        f"login --token {SECRET} pw": f"login --token {PLACEHOLDER} {PLACEHOLDER}",
    }
    for command, expected in cases.items():
        run_cmd(app, command)
        assert app.history[-1].raw == expected
        assert app.history[-1].statement.redacted is True


def test_short_option_and_cluster_redaction() -> None:
    parser = cmd2.Cmd2ArgumentParser(prog="c")
    parser.add_argument("-p", "--pin", sensitive=True)
    parser.add_argument("-v", action="store_true")

    def redacted(line: str) -> str:
        statement = cmd2.parsing.StatementParser().parse(line)
        result = redaction.redact_statement(parser, statement)
        assert result is not None
        return result[0].raw

    assert redacted("c -p1234 -v") == "c -p**** -v"
    assert redacted("c -vp1234") == "c -vp****"
    assert redacted("c -p 1234") == "c -p ****"


def test_multiple_sensitive_values_redacted(app) -> None:
    run_cmd(app, f"login --codes 11 22 33 --token {SECRET} pw")
    raw = app.history[-1].raw
    assert "11" not in raw
    assert "22" not in raw
    assert "33" not in raw
    assert raw.count(PLACEHOLDER) == 5
    # Real values still reached the command
    assert app.last_token == SECRET
    assert app.last_password == "pw"


def test_quoted_secret_redacted(app) -> None:
    run_cmd(app, 'login --token "my secret token" pw')
    raw = app.history[-1].raw
    assert "my secret token" not in raw
    assert raw == f"login --token {PLACEHOLDER} {PLACEHOLDER}"
    assert app.last_token == "my secret token"


def test_sensitive_subcommand_argument() -> None:
    parser = cmd2.Cmd2ArgumentParser(prog="admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    child = subparsers.add_parser("reset")
    child.add_argument("--secret", sensitive=True)
    child.add_argument("name")

    statement = cmd2.parsing.StatementParser().parse("admin reset --secret hush thing")
    result = redaction.redact_statement(parser, statement)
    assert result is not None
    assert result[0].raw == "admin reset --secret **** thing"
    assert "hush" not in result[0].raw
    assert result[0].redacted is True


def test_redaction_leaves_non_secret_lines_unchanged(app) -> None:
    # Sensitive flag omitted, but the sensitive positional still carries a value.
    run_cmd(app, "login pw")
    item = app.history[-1]
    assert item.statement.redacted is True
    assert item.raw == f"login {PLACEHOLDER}"


def test_redirection_preserved(app) -> None:
    # Redact at the statement level so the redirection target isn't actually opened.
    statement = cmd2.parsing.StatementParser().parse(f"login --token {SECRET} pw > out.txt")
    parser = app.command_parsers.get(app.do_login)
    assert parser is not None
    result = redaction.redact_statement(parser, statement)
    assert result is not None
    redacted = result[0]
    assert redacted.redirect_to == "out.txt"
    assert SECRET not in redacted.raw
    assert redacted.raw == "login --token **** **** > out.txt"


# ---------------------------------------------------------------------------
# Persistent history
# ---------------------------------------------------------------------------


def test_persistent_history_roundtrip(app) -> None:
    run_cmd(app, f"login --token {SECRET} pw")
    history_json = app.history.to_json()
    assert SECRET not in history_json

    restored = History.from_json(history_json)
    item = restored[-1]
    assert item.statement.redacted is True
    assert SECRET not in item.raw


# ---------------------------------------------------------------------------
# Replay guards
# ---------------------------------------------------------------------------


def test_history_run_skips_redacted_item(app) -> None:
    run_cmd(app, f"login --token {SECRET} pw")
    assert app.login_count == 1

    out, err = run_cmd(app, "history -r 1")
    assert app.login_count == 1
    joined_err = "\n".join(err)
    assert "Skipping history item 1" in joined_err
    assert "redacted sensitive arguments" in joined_err
    assert not out


def test_runcmds_never_replays_redacted_history_item(app) -> None:
    run_cmd(app, f"login --token {SECRET} pw")
    assert app.login_count == 1

    stderr = io.StringIO()
    original_stdout = app.stdout
    try:
        app.stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            app.runcmds_plus_hooks([app.history[-1]])
    finally:
        app.stdout = original_stdout

    assert app.login_count == 1
    assert "redacted sensitive arguments" in stderr.getvalue()


def test_history_output_file_comments_redacted(app) -> None:
    run_cmd(app, f"login --token {SECRET} pw")
    fd, fname = tempfile.mkstemp(prefix="", suffix=".txt")
    os.close(fd)
    try:
        run_cmd(app, f'history -o "{fname}"')
        with open(fname, encoding="utf-8") as fobj:
            content = fobj.read()
        assert SECRET not in content
        assert content.startswith(f"# login --token {PLACEHOLDER}")

        # Running the saved script must not execute the commented, redacted command
        run_cmd(app, f'run_script "{fname}"')
        assert app.login_count == 1
    finally:
        os.remove(fname)


# ---------------------------------------------------------------------------
# Echo
# ---------------------------------------------------------------------------


def test_runcmds_echo_redacts_secret(capsys) -> None:
    app = SensitiveArgsApp()
    app.echo = True
    app.runcmds_plus_hooks([f"login --token {SECRET} pw"], add_to_history=False)

    captured = capsys.readouterr()
    echo_line = captured.out.splitlines()[0]
    assert SECRET not in echo_line
    assert "pw" not in echo_line
    assert echo_line == f"{app.prompt}login --token {PLACEHOLDER} {PLACEHOLDER}"
    # The command itself ran with real values
    assert app.last_token == SECRET
    app.echo = False


def test_piped_input_echo_redacts_secret(capsys) -> None:
    prompt = "prompt> "
    app = SensitiveArgsApp(stdin=io.StringIO(f"login --token {SECRET} pw\n"))
    app.interactive_pipe = False
    app.echo = True

    result = app._read_raw_input(prompt, app.main_session)
    assert result == f"login --token {SECRET} pw"

    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert "pw" not in captured.out
    assert captured.out.startswith(f"{prompt}login --token")
    assert PLACEHOLDER in captured.out


# ---------------------------------------------------------------------------
# Argparse diagnostics
# ---------------------------------------------------------------------------


def test_argparse_error_scrubs_sensitive_value(app) -> None:
    out, err = run_cmd(app, f"login --token {SECRET} pw --codes notanint")
    combined = "\n".join(out + err)
    assert SECRET not in combined
    assert "invalid int value" in combined


def test_argparse_choice_error_scrubs_sensitive_value(app) -> None:
    out, err = run_cmd(app, "select --mode SECRET-MODE")
    combined = "\n".join(out + err)
    assert "SECRET-MODE" not in combined
    assert PLACEHOLDER in combined


# ---------------------------------------------------------------------------
# prompt-toolkit history
# ---------------------------------------------------------------------------


def test_prompt_history_entry_replaced(app) -> None:
    prompt_history = app.main_session.history
    assert isinstance(prompt_history, Cmd2History)
    prompt_history.append_string(f"login --token {SECRET} pw")

    run_cmd(app, f"login --token {SECRET} pw")

    newest = prompt_history._loaded_strings[0]
    assert SECRET not in newest
    assert newest == f"login --token {PLACEHOLDER} {PLACEHOLDER}"


def test_prompt_history_replace_only_matching() -> None:
    prompt_history = Cmd2History()
    prompt_history.append_string("other command")
    replaced = prompt_history.replace_matching_string("login --token secret pw", "login --token **** ****")
    assert replaced is False
    assert prompt_history._loaded_strings[0] == "other command"


# ---------------------------------------------------------------------------
# @with_annotated
# ---------------------------------------------------------------------------


def test_annotated_sensitive_argument() -> None:
    received = {}

    class AnnotatedApp(cmd2.Cmd):
        @with_annotated
        def do_login(
            self,
            token: Annotated[str, Option("--token", sensitive=True)],
            password: Annotated[str, Argument(sensitive=True)],
            user: Annotated[str | None, Option("--user")] = None,
        ) -> None:
            received["token"] = token
            received["password"] = password
            self.poutput(token)

    app = AnnotatedApp()
    out, err = run_cmd(app, f"login --token {SECRET} secret-password --user bob")
    assert not err
    assert received == {"token": SECRET, "password": "secret-password"}
    assert SECRET in " ".join(out)

    item = app.history[-1]
    assert item.statement.redacted is True
    assert SECRET not in item.raw
    assert "secret-password" not in item.raw


def test_annotated_sensitive_rejected_for_value_less_action() -> None:
    def func(
        self,
        flag: Annotated[bool, Option("--flag", action="store_true", sensitive=True)] = False,
    ) -> None:
        pass

    with pytest.raises(TypeError, match="sensitive"):
        build_parser_from_function(func)
