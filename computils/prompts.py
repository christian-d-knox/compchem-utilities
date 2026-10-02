from rich.markup import escape
from .console import console

# False while the TUI runs: its workers own the terminal, so a prompt would hang there. Code that can be reached from the
# TUI checks this before asking (project.PromptCreateProject); any other Ask* raises, so a stray prompt fails loudly
interactive = True

def _CheckInteractive(prompt: str) -> None:
    if not interactive:
        raise RuntimeError(f"Prompt reached inside the TUI: {prompt}")

def AskBool(prompt: str, default: str = "y", style: str = "prompt") -> bool:
    """Ask a y/n question with a default. Returns True for yes."""
    _CheckInteractive(prompt)
    default = default.lower()
    if default not in ("y", "n"):
        raise ValueError(f"default must be 'y' or 'n', got {default!r}")

    # Format the prompt with the default capitalized: (Y/n) or (y/N)
    if default == "y":
        suffix = " (Y/n): "
    else:
        suffix = " (y/N): "

    while True:
        response = console.input(f"[{style}]{prompt}{suffix}[/{style}]").strip().lower()
        if response == "":
            response = default
        if response in ("y", "n"):
            return response == "y"
        console.print("[warning]Please enter Y or N.[/warning]")

def AskFloat(prompt: str, default = None, style: str = "prompt") -> float:
    """Ask for a number, and return it as a float"""
    while True:
        response = AskStr(prompt, default, style)
        try:
            return float(response)
        except ValueError:
            console.print(f"[warning]{escape(response)} is not a number.[/warning]")

def AskStr(prompt: str, default = None, style: str = "prompt") -> str:
    """Ask for a non-empty string (or accept the default, which may be "" to make the answer optional), and return it"""
    _CheckInteractive(prompt)
    if default:
        suffix = f" ({default}): "
    else:
        suffix = ": "
    while True:
        response = console.input(f"[{style}]{prompt}{suffix}[/{style}]").strip()
        if response == "":
            if default is not None:
                return default
            console.print("[warning]Please provide a value.[/warning]")
            continue
        return response

def AskChoice(prompt: str, options: list[str], default: int = 0, style: str = "prompt") -> int | None:
    """Ask the user to pick one option from a numbered list. Returns its index, or None if they enter q."""
    _CheckInteractive(prompt)
    for index, option in enumerate(options):
        console.print(f"  [{style}]\\[{index}][/{style}] {escape(option)}")
    while True:
        response = console.input(f"[{style}]{prompt} ({default}, q to cancel): [/{style}]").strip().lower()
        if response == "":
            return default
        if response == "q":
            return None
        if response.isdigit() and int(response) < len(options):
            return int(response)
        console.print(f"[warning]Please enter a number from 0 to {len(options) - 1}, or q.[/warning]")
