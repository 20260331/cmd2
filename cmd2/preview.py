"""Read-only preview of what cmd2 would execute after alias, macro, and redirection expansion.

The classes in this module describe an execution plan produced by
[Cmd.preview][cmd2.Cmd.preview]. A plan is purely descriptive: building it never runs a
command function, hook, argparse conversion, shell process, or clipboard operation, and it never
opens a redirection target for writing. Text scripts referenced by ``run_script`` and
``_relative_run_script`` are read and expanded in place using the same inclusion rules the
application uses at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

from . import constants
from . import string_utils as su

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .parsing import Statement

# Discriminator values for PlannedRedirection.kind
REDIRECT_KIND_FILE = "file"
REDIRECT_KIND_SHELL = "shell"
REDIRECT_KIND_CLIPBOARD = "clipboard"


@dataclass(frozen=True, slots=True)
class PlannedRedirection:
    """Describes the output redirection (``>``, ``>>``, or ``|``) attached to a planned step.

    This describes intent only. The plan builder never opens the target file, starts the pipe
    process, or touches the clipboard.
    """

    # The redirection operator, one of constants.REDIRECTION_OVERWRITE, REDIRECTION_APPEND,
    # or REDIRECTION_PIPE.
    operator: str

    # What the output would be redirected to:
    #   - a resolved file path for > and >>
    #   - the shell command line for |
    #   - an empty string for clipboard redirection (>, >> with no file argument)
    target: str

    # One of the REDIRECT_KIND_* constants: "file", "shell", or "clipboard".
    kind: str

    # File open mode for kind == REDIRECT_KIND_FILE ("w" for >, "a" for >>); otherwise None.
    mode: str | None = None

    # False when redirection is disabled (Cmd.allow_redirection is False), in which case the
    # application would ignore this redirection at runtime.
    active: bool = True

    # An error the application would raise before running the command while setting up this
    # redirection (e.g. clipboard access not allowed). None means no such error was detected.
    error: str | None = None

    def describe(self) -> str:
        """Return a short human-readable description of this redirection."""
        if not self.active:
            return "redirection disabled"
        if self.kind == REDIRECT_KIND_FILE:
            action = "append to file" if self.mode == "a" else "write file"
            description = f"{action} {self.target}"
        elif self.kind == REDIRECT_KIND_SHELL:
            description = f"pipe to shell: {self.target}"
        else:
            description = "copy to clipboard"
        if self.error:
            description += f" [error: {self.error}]"
        return description


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    """A single cmd2 command that would be executed, with all expansions resolved."""

    # The line as it was authored before shortcut, alias, and macro expansion.
    raw: str

    # The resolved command name (after shortcut/alias/macro expansion).
    command: str

    # The arguments to the command as a string with quotes preserved.
    args: str

    # The command and its arguments a-la sys.argv, with quotes removed.
    argv: tuple[str, ...]

    # The fully expanded command line, including any terminator and redirection clauses.
    expanded: str

    # The Statement produced by the application's parser.
    statement: Statement

    # Path of the script file this line came from, or None if it came from the preview caller.
    source: str | None

    # 1-based line number of this line within its source (the caller input or a script file).
    line_number: int

    # The parsed output redirection attached to the command, or None if there is none.
    redirection: PlannedRedirection | None = None

    # True when a do_* method exists for the resolved command. False means the application would
    # route this line to default() ("not a recognized command, alias, or macro").
    recognized: bool = True

    # For the built-in shell command (or ! shortcut), the OS shell command line that would run,
    # with quotes preserved and user paths expanded. None for all other commands.
    shell_command: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedPyscript:
    """A ``run_pyscript`` invocation.

    Python scripts can contain arbitrary logic and invoke commands through the Python bridge,
    so their contents cannot be expanded without executing them. The plan records the
    invocation but never runs the script.
    """

    # The line as it was authored before shortcut, alias, and macro expansion.
    raw: str

    # The path to the Python script, with a leading ~ expanded.
    path: str

    # The command-line arguments that would be passed to the Python script.
    arguments: tuple[str, ...]

    # Path of the script file this line came from, or None if it came from the preview caller.
    source: str | None

    # 1-based line number of this line within its source.
    line_number: int

    # The parsed output redirection attached to the invocation, or None if there is none.
    redirection: PlannedRedirection | None = None

    @property
    def expanded(self) -> str:
        """The fully expanded command line."""
        parts = ["run_pyscript", self.path, *self.arguments]
        line = " ".join(parts)
        if self.redirection:
            line += f" {self.redirection.operator}"
            if self.redirection.target:
                line += f" {self.redirection.target}"
        return line


@dataclass(frozen=True, slots=True)
class PlannedScript:
    """A ``run_script`` / ``_relative_run_script`` inclusion, with the script's own plan nested.

    Children preserve the order of the lines in the script file. Blank lines and comments
    produce no children.
    """

    # The line as it was authored before shortcut, alias, and macro expansion.
    raw: str

    # The resolved command name: "run_script" or "_relative_run_script".
    command: str

    # The resolved absolute path of the script file.
    path: str

    # True for _relative_run_script, where the path was resolved relative to the including
    # script's directory.
    relative: bool

    # Path of the script file this line came from, or None if it came from the preview caller.
    source: str | None

    # 1-based line number of this line within its source.
    line_number: int

    # The planned steps produced from the included script's lines, in order.
    children: tuple[PlanStep, ...]

    # The parsed output redirection attached to the run_script line, or None if there is none.
    redirection: PlannedRedirection | None = None

    # Non-fatal notes about the inclusion (e.g. the file looks like a Python file).
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def expanded(self) -> str:
        """The fully expanded command line."""
        line = f"{self.command} {self.path}"
        if self.redirection:
            line += f" {self.redirection.operator}"
            if self.redirection.target:
                line += f" {self.redirection.target}"
        return line


@dataclass(frozen=True, slots=True)
class PlanError:
    """A line that could not be turned into an executable plan.

    Examples include invalid quoting, a macro invoked with too few arguments, an incomplete
    multiline command, or a script file that cannot be read. The application would report a
    matching error if the same input were run.
    """

    # The line that could not be planned.
    raw: str

    # Path of the script file this line came from, or None if it came from the preview caller.
    source: str | None

    # 1-based line number of this line within its source.
    line_number: int

    # The error message.
    message: str


#: A single node in a preview plan.
PlanStep: TypeAlias = PlannedCommand | PlannedPyscript | PlannedScript | PlanError


@dataclass(frozen=True, slots=True)
class PreviewPlan:
    """The read-only execution plan produced by [Cmd.preview][cmd2.Cmd.preview].

    Iterating over the plan yields its top-level steps; steps produced by included text scripts
    are nested inside their [PlannedScript][cmd2.preview.PlannedScript] nodes. Use
    [walk][cmd2.preview.PreviewPlan.walk] for a depth-first traversal or
    [commands][cmd2.preview.PreviewPlan.commands] to iterate every planned command regardless
    of script nesting.
    """

    # The top-level planned steps, in the order the caller provided them.
    steps: tuple[PlanStep, ...] = field(default_factory=tuple)

    def __iter__(self) -> Iterator[PlanStep]:
        """Iterate over the top-level steps."""
        return iter(self.steps)

    def __len__(self) -> int:
        """Return the number of top-level steps."""
        return len(self.steps)

    def walk(self) -> Iterator[tuple[int, PlanStep]]:
        """Yield every plan step in execution order as ``(depth, step)`` pairs.

        A [PlannedScript][cmd2.preview.PlannedScript] is yielded before its children, and its
        children are yielded at depth + 1.
        """

        def _walk(steps: tuple[PlanStep, ...], depth: int) -> Iterator[tuple[int, PlanStep]]:
            for step in steps:
                yield depth, step
                if isinstance(step, PlannedScript):
                    yield from _walk(step.children, depth + 1)

        return _walk(self.steps, 0)

    def commands(self) -> Iterator[PlannedCommand]:
        """Yield every planned command in execution order, including those nested in scripts."""
        for _depth, step in self.walk():
            if isinstance(step, PlannedCommand):
                yield step

    @property
    def errors(self) -> tuple[PlanError, ...]:
        """All plan errors in execution order, including those nested in scripts."""
        return tuple(step for _depth, step in self.walk() if isinstance(step, PlanError))

    @property
    def ok(self) -> bool:
        """True when the plan contains no errors."""
        return not self.errors

    def render(self) -> str:
        """Render the plan as an indented, human-readable text block."""
        lines: list[str] = []

        def add(depth: int, text: str) -> None:
            lines.append(f"{'  ' * depth}{text}")

        for depth, step in self.walk():
            if isinstance(step, PlannedCommand):
                text = step.expanded
                annotations: list[str] = []
                if step.shell_command is not None:
                    annotations.append(f"shell: {step.shell_command}")
                if not step.recognized:
                    annotations.append("unrecognized command")
                if step.redirection is not None:
                    annotations.append(step.redirection.describe())
                if annotations:
                    text += f"  # {' | '.join(annotations)}"
                add(depth, text)

            elif isinstance(step, PlannedPyscript):
                text = step.expanded + "  # Python script; contents are not expanded"
                add(depth, text)

            elif isinstance(step, PlannedScript):
                text = f"{step.command} {step.path}"
                script_notes: list[str] = []
                if step.relative:
                    script_notes.append("relative include")
                if step.warnings:
                    script_notes.extend(step.warnings)
                if step.redirection is not None:
                    script_notes.append(step.redirection.describe())
                if script_notes:
                    text += f"  # {' | '.join(script_notes)}"
                add(depth, text + ":")

            else:
                location = f"{step.source}:{step.line_number}: " if step.source else ""
                add(depth, f"# ERROR: {location}{step.message}")

        return "\n".join(lines)


def _redirection_from_statement(
    statement: Statement,
    *,
    allow_redirection: bool,
    allow_clipboard: bool,
) -> PlannedRedirection | None:
    """Build a [PlannedRedirection][cmd2.preview.PlannedRedirection] from a parsed Statement.

    This only reads parsed data and application settings; it performs no file, process, or
    clipboard operations.

    :param statement: the parsed statement
    :param allow_redirection: value of Cmd.allow_redirection
    :param allow_clipboard: value of Cmd.allow_clipboard
    :return: a PlannedRedirection, or None when the statement has no redirection
    """
    operator = statement.redirector
    if not operator:
        return None

    target = statement.redirect_to

    if operator == constants.REDIRECTION_PIPE:
        kind = REDIRECT_KIND_SHELL
        mode: str | None = None
    elif target:
        kind = REDIRECT_KIND_FILE
        mode = "a" if operator == constants.REDIRECTION_APPEND else "w"
        target = su.strip_quotes(target)
    else:
        kind = REDIRECT_KIND_CLIPBOARD
        mode = None
        target = ""

    error: str | None = None
    if not allow_redirection:
        # _redirect_output() leaves stdout untouched when redirection is disabled.
        return PlannedRedirection(operator, target, kind, mode=mode, active=False)

    if kind == REDIRECT_KIND_CLIPBOARD and not allow_clipboard:
        # _redirect_output() raises RedirectionError before the command runs.
        error = "Clipboard access not allowed"

    return PlannedRedirection(operator, target, kind, mode=mode, error=error)
