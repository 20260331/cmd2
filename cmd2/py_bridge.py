"""Bridges calls made inside of a Python environment to the Cmd2 host app.

Maintains a reasonable degree of isolation between the two.
"""

import os
import sys
from contextlib import redirect_stderr
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    NamedTuple,
    TextIO,
    cast,
)

from .utils import StdSim  # namedtuple_with_defaults,

if TYPE_CHECKING:  # pragma: no cover
    from .cmd2 import Cmd


class CommandResult(NamedTuple):
    """Encapsulates the results from a cmd2 app command.

    :stdout: str - output captured from stdout while this command is executing
    :stderr: str - output captured from stderr while this command is executing
    :stop: bool - return value of onecmd_plus_hooks after it runs the given
           command line.
    :data: possible data populated by the command.
    :stdout_truncated: bool - True if stdout exceeded its capture budget; in that case stdout
           holds only the beginning of the output.
    :stderr_truncated: bool - True if stderr exceeded its capture budget; in that case stderr
           holds only the beginning of the output.
    :stdout_log: path of a file containing the complete stdout when a capture budget was exceeded
           and an overflow directory was provided, otherwise None.
    :stderr_log: path of a file containing the complete stderr when a capture budget was exceeded
           and an overflow directory was provided, otherwise None.

    Any combination of these fields can be used when developing a scripting API
    for a given command. By default stdout, stderr, and stop will be captured
    for you. If there is additional command specific data, then write that to
    cmd2's last_result member. That becomes the data member of this tuple.

    In some cases, the data member may contain everything needed for a command
    and storing stdout and stderr might just be a duplication of data that
    wastes memory. In that case, the StdSim can be told not to store output
    with its pause_storage member. While this member is True, any output sent
    to StdSim won't be saved in its buffer.

    The code would look like this::

        if isinstance(self.stdout, StdSim):
            self.stdout.pause_storage = True

        if isinstance(sys.stderr, StdSim):
            sys.stderr.pause_storage = True

    See [cmd2.utils.StdSim][] for more information.

    .. note::

       Named tuples are immutable. The contents are there for access,
       not for modification.
    """

    stdout: str = ""
    stderr: str = ""
    stop: bool = False
    data: Any = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_log: str | None = None
    stderr_log: str | None = None

    def __bool__(self) -> bool:
        """Return True if the command succeeded, otherwise False."""
        # If data was set, then use it to determine success
        if self.data is not None:
            return bool(self.data)

        # Otherwise check if stderr was filled out. A truncated stderr still means the command
        # wrote errors, even when a capture budget left its retained value empty.
        return not self.stderr and not self.stderr_truncated


class PyBridge:
    """Provides a Python API wrapper for application commands.

    :param cmd_app: app being controlled by this PyBridge.
    :param add_to_history: If True, then add all commands run by this PyBridge to history.
                           Defaults to True.
    """

    def __init__(self, cmd_app: "Cmd", *, add_to_history: bool = True) -> None:
        """Initialize PyBridge instances."""
        self._cmd_app = cmd_app
        self._add_to_history = add_to_history
        self.cmd_echo = False

        # Tells if any of the commands run via __call__ returned True for stop
        self.stop = False

    def __dir__(self) -> list[str]:
        """Return a custom set of attribute names."""
        attributes: list[str] = []
        attributes.insert(0, "cmd_echo")
        return attributes

    def __call__(
        self,
        command: str,
        *,
        echo: bool | None = None,
        stdout_budget: int | None = None,
        stderr_budget: int | None = None,
        overflow_dir: str | os.PathLike[str] | None = None,
    ) -> CommandResult:
        """Provide functionality to call application commands by calling PyBridge.

        ex: app('help')
        :param command: command line being run
        :param echo: If provided, this temporarily overrides the value of self.cmd_echo
                     while the command runs. If True, output will be echoed to _cmd_app.stdout
                     and sys.stderr. (Defaults to None)
        :param stdout_budget: Maximum number of stdout bytes retained in CommandResult.stdout.
                     Output beyond the budget keeps only its beginning in the result. None (the
                     default) captures all stdout in memory.
        :param stderr_budget: Maximum number of stderr bytes retained in CommandResult.stderr.
                     None (the default) captures all stderr in memory.
        :param overflow_dir: When a budget is exceeded, stream the complete output (beginning
                     included) to a uniquely named log file in this directory, which is created if
                     it does not exist. The file path is returned in CommandResult.stdout_log or
                     CommandResult.stderr_log. When None (the default), output beyond the budget
                     continues to the stream's original target instead. Nested PyBridge calls keep
                     their own independent budgets either way.
        :raises ValueError: if a budget is negative
        """
        if echo is None:
            echo = self.cmd_echo

        self._validate_budget(stdout_budget, "stdout_budget")
        self._validate_budget(stderr_budget, "stderr_budget")

        spill_dir: str | None = None if overflow_dir is None else os.fspath(overflow_dir)
        if spill_dir is not None and (stdout_budget is not None or stderr_budget is not None):
            # Fail early, before the command runs, if the complete log cannot be written.
            os.makedirs(spill_dir, exist_ok=True)

        # This will be used to capture _cmd_app.stdout
        copy_cmd_stdout = StdSim(
            cast(TextIO | StdSim, self._cmd_app.stdout),
            echo=echo,
            max_bytes=stdout_budget,
            spill_dir=spill_dir,
            spill_prefix="cmd2-stdout-",
        )

        # Pause the storing of stdout until onecmd_plus_hooks enables it
        copy_cmd_stdout.pause_storage = True

        # This will be used to capture sys.stderr
        copy_stderr = StdSim(
            sys.stderr,
            echo=echo,
            max_bytes=stderr_budget,
            spill_dir=spill_dir,
            spill_prefix="cmd2-stderr-",
        )

        self._cmd_app.last_result = None

        stop = False
        try:
            self._cmd_app.stdout = cast(TextIO, copy_cmd_stdout)

            with redirect_stderr(cast(IO[str], copy_stderr)):
                stop = self._cmd_app.onecmd_plus_hooks(
                    command,
                    add_to_history=self._add_to_history,
                    py_bridge_call=True,
                )
        finally:
            with self._cmd_app.sigint_protection:
                self._cmd_app.stdout = cast(TextIO, copy_cmd_stdout.inner_stream)
                self.stop = stop or self.stop
            # Make sure the complete logs are flushed even if the command raised.
            copy_cmd_stdout.close_spill_file()
            copy_stderr.close_spill_file()

        # Save the result
        return CommandResult(
            stdout=copy_cmd_stdout.getvalue(),
            stderr=copy_stderr.getvalue(),
            stop=stop,
            data=self._cmd_app.last_result,
            stdout_truncated=copy_cmd_stdout.truncated,
            stderr_truncated=copy_stderr.truncated,
            stdout_log=copy_cmd_stdout.spill_path,
            stderr_log=copy_stderr.spill_path,
        )

    @staticmethod
    def _validate_budget(budget: int | None, name: str) -> None:
        """Validate a per-call capture budget."""
        if budget is not None and budget < 0:
            raise ValueError(f"{name} must be non-negative")
