"""Redact values of sensitive argparse arguments from cmd2-managed diagnostics.

Commands declare sensitive arguments with ``add_argument(..., sensitive=True)``.
This module classifies the raw tokens of a parsed command line against the
command's parser, finds the tokens that feed sensitive actions (including those
in subparsers), and builds redacted copies of a [cmd2.parsing.Statement][]. The
original values are always passed to the command function; only cmd2-managed
history, echo output, and argparse diagnostics use the redacted form.
"""

import argparse
import contextlib
from collections import deque
from collections.abc import Iterable
from typing import cast

from . import constants
from . import string_utils as su
from .argparse_utils import Cmd2ArgumentParser
from .parsing import Statement

# Maps the index of an argument token to the character spans (start, end)
# within that token which hold sensitive values.
_SpanMap = dict[int, list[tuple[int, int]]]

# Attribute used to cache whether a parser (including its subparsers) has sensitive actions.
_HAS_SENSITIVE_ATTR = constants.cmd2_private_attr_name("has_sensitive_args")


def _is_sensitive(action: argparse.Action) -> bool:
    """Whether an argparse action was declared sensitive."""
    return action.get_sensitive() is True  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def _nargs_bounds(action: argparse.Action) -> tuple[int, float]:
    """Return the (minimum, maximum) number of values consumed by an action.

    Mirrors the nargs handling in ArgparseCompleter._ArgumentState.
    """
    nargs_range = action.get_nargs_range()  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    if nargs_range is not None:
        return cast("tuple[int, float]", nargs_range)

    if action.nargs is None:
        return 1, 1
    if action.nargs == argparse.OPTIONAL:
        return 0, 1
    if action.nargs in (argparse.ZERO_OR_MORE, argparse.REMAINDER):
        return 0, constants.INFINITY
    if action.nargs == argparse.ONE_OR_MORE:
        return 1, constants.INFINITY
    return int(action.nargs), int(action.nargs)


def _looks_like_flag(token: str, parser: Cmd2ArgumentParser) -> bool:
    """Determine if token can be treated as a flag, mirroring ArgparseCompleter.

    Negative numbers are not flags unless the parser defines negative-number options.
    """
    if len(token) < 2 or token[0] not in parser.prefix_chars:
        return False
    if parser._negative_number_matcher.match(token) and not parser._has_negative_number_optionals:
        return False
    return " " not in token


class _ConsumptionState:
    """State of an action currently consuming value tokens."""

    def __init__(self, action: argparse.Action) -> None:
        self.action = action
        self.min, self.max = _nargs_bounds(action)
        self.count = 0

    @property
    def satisfied(self) -> bool:
        """Whether this action has consumed all the values it accepts."""
        return self.count >= self.max

    def consumes(self, token: str, parser: Cmd2ArgumentParser) -> bool:
        """Whether *token* should be consumed as a value of this action.

        Tokens required to satisfy a minimum count are always consumed (even when
        they look like flags); further values of a variable-length action stop at
        the next flag-looking token.
        """
        if self.count >= self.max:
            return False
        if self.count < self.min:
            return True
        return not _looks_like_flag(token, parser)


def _resolve_flag(
    token: str,
    parser: Cmd2ArgumentParser,
    flag_to_action: dict[str, argparse.Action],
) -> tuple[argparse.Action, tuple[int, int] | None] | None:
    """Resolve a flag token to its action and an optional inline value span.

    The inline span is the (start, end) character range of the value when the
    token uses ``--flag=value`` or a short cluster like ``-fVALUE``.

    :return: (action, inline_span) or None if the token is not a recognized flag
    """
    # Long options use two prefix characters (e.g. --token); short options one (e.g. -t).
    is_long_style = len(token) > 1 and token[0] in parser.prefix_chars and token[1] in parser.prefix_chars

    if is_long_style:
        explicit_value: str | None = None
        flag_token = token
        equals_pos = token.find("=")
        if equals_pos != -1:
            flag_token = token[:equals_pos]
            explicit_value = token[equals_pos + 1 :]

        action: argparse.Action | None = None
        if flag_token in flag_to_action:
            action = flag_to_action[flag_token]
        elif parser.allow_abbrev:
            candidates = [flag for flag in flag_to_action if flag.startswith(flag_token)]
            if len(candidates) == 1:
                action = flag_to_action[candidates[0]]

        if action is None:
            return None

        if explicit_value is not None:
            return action, (equals_pos + 1, len(token))
        return action, None

    # Short option clusters (e.g. -abc for boolean -a -b -c, or -pVALUE)
    prefix = token[0]
    bool_action: argparse.Action | None = None
    for char_index in range(1, len(token)):
        option = prefix + token[char_index]
        cluster_action = flag_to_action.get(option)
        if cluster_action is None:
            # Unknown short option: argparse would error, so no value can be attributed.
            return None

        _min_count, max_count = _nargs_bounds(cluster_action)
        if max_count > 0:
            # Everything after this option character is its inline value (possibly empty).
            if char_index + 1 < len(token):
                return cluster_action, (char_index + 1, len(token))
            return cluster_action, None

        bool_action = cluster_action

    return (bool_action, None) if bool_action is not None else None


def _mark_span(
    spans: _SpanMap,
    values: set[str],
    action: argparse.Action,
    token_index: int,
    token: str,
    span: tuple[int, int] | None = None,
) -> None:
    """Record a sensitive value occurrence and collect the raw value for diagnostic scrubbing."""
    if not _is_sensitive(action):
        return

    start, end = span if span is not None else (0, len(token))
    spans.setdefault(token_index, []).append((start, end))

    value = token[start:end]
    values.add(value)
    unquoted = su.strip_quotes(value)
    if unquoted:
        values.add(unquoted)


def _classify(
    parser: Cmd2ArgumentParser,
    tokens: list[str],
    token_offset: int,
    spans: _SpanMap,
    values: set[str],
) -> None:
    """Classify *tokens* for this parser level, recording sensitive spans.

    :param token_offset: index offset of *tokens* within the full command's token list
                         (non-zero when recursing into a subparser)
    """
    flag_to_action: dict[str, argparse.Action] = {}
    positionals: deque[argparse.Action] = deque()
    for action in parser._actions:
        if action.option_strings:
            for option in action.option_strings:
                flag_to_action[option] = action
        else:
            positionals.append(action)

    flag_state: _ConsumptionState | None = None
    pos_state: _ConsumptionState | None = None
    skip_remaining_flags = False

    index = 0
    while index < len(tokens):
        token = tokens[index]
        absolute_index = token_offset + index

        # A REMAINDER flag consumes everything until a double dash
        if flag_state is not None and flag_state.action.nargs == argparse.REMAINDER:
            if token == "--":  # noqa: S105
                flag_state = None
            else:
                _mark_span(spans, values, flag_state.action, absolute_index, token)
                flag_state.count += 1
            index += 1
            continue

        # A double dash ends flag processing for argparse
        if token == "--" and not skip_remaining_flags:  # noqa: S105
            flag_state = None
            skip_remaining_flags = True
            index += 1
            continue

        resolved: tuple[argparse.Action, tuple[int, int] | None] | None = None
        if not skip_remaining_flags and _looks_like_flag(token, parser):
            resolved = _resolve_flag(token, parser, flag_to_action)

        if resolved is not None:
            action, inline_span = resolved
            flag_state = None

            if inline_span is not None:
                _mark_span(spans, values, action, absolute_index, token, inline_span)
            elif _nargs_bounds(action)[1] > 0:
                flag_state = _ConsumptionState(action)

            index += 1
            continue

        # A value token for an option still expecting values
        if flag_state is not None and flag_state.consumes(token, parser):
            _mark_span(spans, values, flag_state.action, absolute_index, token)
            flag_state.count += 1
            if flag_state.satisfied:
                flag_state = None
            index += 1
            continue

        # An option ended (e.g. variable-length action met a flag token); fall through to positionals
        flag_state = None

        # Obtain the next positional action
        if pos_state is None:
            subparser_action: argparse._SubParsersAction[Cmd2ArgumentParser] | None = None
            while positionals:
                next_action = positionals.popleft()
                if isinstance(next_action, argparse._SubParsersAction):
                    subparser_action = cast(
                        "argparse._SubParsersAction[Cmd2ArgumentParser]",
                        next_action,
                    )
                    break
                pos_state = _ConsumptionState(next_action)
                break

            if subparser_action is not None:
                subparser = subparser_action.choices.get(token)
                if subparser is not None:
                    # The subcommand name itself is not sensitive; classify the rest with its parser
                    _classify(subparser, tokens[index + 1 :], absolute_index + 1, spans, values)
                return

            if pos_state is None:
                # No positional can accept this token (argparse would report an error)
                index += 1
                continue

        if pos_state.action.nargs == argparse.REMAINDER:
            # A positional REMAINDER swallows every remaining token, flags included
            for remainder_index in range(index, len(tokens)):
                _mark_span(
                    spans,
                    values,
                    pos_state.action,
                    token_offset + remainder_index,
                    tokens[remainder_index],
                )
            return

        if pos_state.consumes(token, parser):
            _mark_span(spans, values, pos_state.action, absolute_index, token)
            pos_state.count += 1
            if pos_state.satisfied:
                pos_state = None

        index += 1


def _analyze(
    parser: Cmd2ArgumentParser,
    tokens: list[str],
) -> tuple[_SpanMap, tuple[str, ...]]:
    """Classify tokens and return their sensitive spans and raw sensitive values."""
    spans: _SpanMap = {}
    values: set[str] = set()
    _classify(parser, tokens, token_offset=0, spans=spans, values=values)
    # Longest values first so diagnostic scrubbing can't leave part of a secret behind
    ordered_values = tuple(sorted(values, key=len, reverse=True))
    return spans, ordered_values


def parser_has_sensitive_arguments(parser: Cmd2ArgumentParser) -> bool:
    """Whether the parser or any of its subparsers declares a sensitive argument."""
    cached = getattr(parser, _HAS_SENSITIVE_ATTR, None)
    if isinstance(cached, bool):
        return cached

    has_sensitive = False
    for action in parser._actions:
        if _is_sensitive(action):
            has_sensitive = True
            break
        if isinstance(action, argparse._SubParsersAction) and any(
            parser_has_sensitive_arguments(subparser) for subparser in action.choices.values()
        ):
            has_sensitive = True
            break

    with contextlib.suppress(AttributeError, TypeError):
        # Caching is an optimization; never fail if the parser rejects the attribute.
        setattr(parser, _HAS_SENSITIVE_ATTR, has_sensitive)
    return has_sensitive


def sensitive_values(parser: Cmd2ArgumentParser, tokens: Iterable[str]) -> tuple[str, ...]:
    """Return raw values of sensitive arguments found among *tokens*.

    Both quote-preserved and unquoted forms are included so the values can be
    scrubbed from argparse error messages regardless of quote handling.
    """
    if not parser_has_sensitive_arguments(parser):
        return ()
    _spans, values = _analyze(parser, list(tokens))
    return values


def _rebuild_raw(statement: Statement, new_args: str) -> str:
    """Rebuild a statement's raw line from the redacted arguments."""
    command_and_args = f"{statement.command} {new_args}" if statement.command and new_args else statement.command

    post_command = statement.post_command
    separator = " " if post_command and not statement.terminator else ""
    raw = f"{command_and_args}{separator}{post_command}"

    # Preserve a trailing line feed (statements parsed from a line ending in '\n')
    if statement.raw.endswith("\n") and not raw.endswith("\n"):
        raw += "\n"
    return raw


def redact_statement(
    parser: Cmd2ArgumentParser,
    statement: Statement,
) -> tuple[Statement, tuple[str, ...]] | None:
    """Build a redacted copy of *statement* for cmd2-managed diagnostics.

    :param parser: the parser associated with the statement's command
    :param statement: the original statement (never modified)
    :return: a (redacted statement, sensitive values) tuple, or None if the parser
             declares no sensitive arguments or no sensitive value was present
    """
    if not parser_has_sensitive_arguments(parser):
        return None

    try:
        tokens = statement.arg_list
    except ValueError:
        # Malformed quoting: cmd2 normally rejects the line before this point.
        return None

    spans, values = _analyze(parser, tokens)
    if not spans:
        return None

    new_tokens: list[str] = []
    for index, token in enumerate(tokens):
        token_spans = spans.get(index)
        if not token_spans:
            new_tokens.append(token)
            continue

        redacted_token = token
        for start, end in sorted(token_spans, reverse=True):
            redacted_token = redacted_token[:start] + constants.SENSITIVE_ARG_PLACEHOLDER + redacted_token[end:]
        new_tokens.append(redacted_token)

    new_args = " ".join(new_tokens)
    new_raw = _rebuild_raw(statement, new_args)

    redacted = Statement(
        new_args,
        raw=new_raw,
        command=statement.command,
        multiline_command=statement.multiline_command,
        terminator=statement.terminator,
        suffix=statement.suffix,
        redirector=statement.redirector,
        redirect_to=statement.redirect_to,
        redacted=True,
    )
    return redacted, values
