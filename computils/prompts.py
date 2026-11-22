import os
import sys

from rich.markup import escape
from .console import console
from . import agent

# Every question has a key (kebab-case, unique per question: `python -m computils.agentskill` checks), so a run with
# nobody at the keyboard can be given its answer in advance: --answer KEY=VALUE. An answer is checked exactly as a typed
# one, and a bad one is an error, never a fallback. A question with no answer and nobody to ask (no TTY, --json, the
# TUI, a Neva-Touch Scheduler hook) raises agent.QuestionNeeded: the run stops before acting (every question comes
# before its command acts), and an agent re-runs it with the answer.

# False when nobody can type: the TUI (its workers own the terminal), Neva-Touch Scheduler hooks, and runs without a
# TTY or with --json (Main sets it)
interactive = True


def _Answer(key: str, prompt: str, kind: str, options: list[str] | None = None, default=None) -> str | None:
    """The answer given in advance (--answer), or None to ask on the terminal. Raises QuestionNeeded when nobody can."""
    if key in agent.answers:
        return agent.answers[key]
    if not interactive:
        raise agent.QuestionNeeded(key, prompt, kind, options, default)
    return None


def StdinIsTerminal() -> bool:
    """Whether someone can type answers: stdin is a terminal. On Windows, a stdin redirected from NUL (Git Bash's
    /dev/null, an agent's command runner) also reads as a TTY, so a real console is told by its console mode."""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        if os.name != "nt":
            return True
        import ctypes
        import msvcrt
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(mode)))
    except (AttributeError, ValueError, OSError):
        return False


def _Input(text: str, key: str, prompt: str, kind: str, options: list[str] | None = None, default=None) -> str:
    """A typed answer. Input that ends (nobody there after all: a closed or empty stdin) stops the run as an unanswered
    question, exactly as a run nobody can answer does, never with a traceback."""
    try:
        return console.input(text)
    except EOFError:
        raise agent.QuestionNeeded(key, prompt, kind, options, default) from None


def _Invalid(key: str, value: str, expected: str) -> agent.UsageError:
    return agent.UsageError(f"--answer {key}={value} isn't valid: {expected}.")


def AskBool(prompt: str, default: str = "y", style: str = "prompt", *, key: str) -> bool:
    """Ask a y/n question with a default. Returns True for yes."""
    default = default.lower()
    if default not in ("y", "n"):
        raise ValueError(f"default must be 'y' or 'n', got {default!r}")
    answer = _Answer(key, prompt, "bool", ["y", "n"], default)
    if answer is not None:
        value = answer.lower()
        if value in ("y", "yes", "true"):
            return True
        if value in ("n", "no", "false"):
            return False
        raise _Invalid(key, answer, "expected y or n")

    # Format the prompt with the default capitalized: (Y/n) or (y/N)
    suffix = " (Y/n): " if default == "y" else " (y/N): "
    while True:
        response = _Input(f"[{style}]{prompt}{suffix}[/{style}]", key, prompt, "bool", ["y", "n"], default).strip().lower()
        if response == "":
            response = default
        if response in ("y", "n"):
            return response == "y"
        console.print("[warning]Please enter Y or N.[/warning]")


def AskFloat(prompt: str, default = None, style: str = "prompt", *, key: str) -> float:
    """Ask for a number, and return it as a float"""
    answer = _Answer(key, prompt, "float", None, default)
    if answer is not None:
        try:
            return float(answer if answer != "" or default is None else default)
        except ValueError:
            raise _Invalid(key, answer, "expected a number") from None
    while True:
        response = _Typed(prompt, default, style, key, "float")
        try:
            return float(response)
        except ValueError:
            console.print(f"[warning]{escape(response)} is not a number.[/warning]")


def AskStr(prompt: str, default = None, style: str = "prompt", *, key: str) -> str:
    """Ask for a non-empty string (or accept the default, which may be "" to make the answer optional), and return it"""
    answer = _Answer(key, prompt, "str", None, default)
    if answer is not None:
        if answer == "":
            if default is None:
                raise _Invalid(key, answer, "a value is required")
            return default
        return answer
    return _Typed(prompt, default, style, key, "str")


def _Typed(prompt: str, default, style: str, key: str, kind: str) -> str:
    suffix = f" ({default}): " if default else ": "
    while True:
        response = _Input(f"[{style}]{prompt}{suffix}[/{style}]", key, prompt, kind, None, default).strip()
        if response == "":
            if default is not None:
                return default
            console.print("[warning]Please provide a value.[/warning]")
            continue
        return response


def AskChoice(prompt: str, options: list[str], default: int = 0, style: str = "prompt", *, key: str) -> int | None:
    """Ask the user to pick one option from a numbered list. Returns its index, or None if they enter q. An answer
    given in advance may be the option's text (any case) or its index."""
    answer = _Answer(key, prompt, "choice", list(options), options[default] if options else None)
    if answer is not None:
        if answer.lower() == "q":
            return None
        # The option's text first, so an option that is itself a number is never read as an index
        match = [index for index, option in enumerate(options) if option.lower() == answer.lower()]
        if match:
            return match[0]
        if answer.isdigit() and int(answer) < len(options):
            return int(answer)
        raise _Invalid(key, answer, f"expected one of {', '.join(options)}")
    for index, option in enumerate(options):
        console.print(f"  [{style}]\\[{index}][/{style}] {escape(option)}")
    while True:
        response = _Input(f"[{style}]{prompt} ({default}, q to cancel): [/{style}]", key, prompt, "choice",
                          list(options), options[default] if options else None).strip().lower()
        if response == "":
            return default
        if response == "q":
            return None
        if response.isdigit() and int(response) < len(options):
            return int(response)
        console.print(f"[warning]Please enter a number from 0 to {len(options) - 1}, or q.[/warning]")
