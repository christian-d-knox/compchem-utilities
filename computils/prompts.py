from rich.markup import escape
from .console import console

def AskBool(prompt: str, default: str = "y", style: str = "prompt") -> bool:
    """Ask a y/n question with a default. Returns True for yes."""
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
    """Ask for an integer input, and returns it"""
    if default is not None:
        suffix = f" ({default}): "
    else:
        suffix = ": "
    while True:
        response = console.input(f"[{style}]{prompt}{suffix}[/{style}]").strip()
        if response == "":
            if default is not None:
                return default
            console.print("[warning]Please provide an integer.[/warning]")
            continue
        try:
            value = float(response)
        except ValueError:
            console.print(f"[warning]{escape(response)} is not an integer.[/warning]")
            continue
        return value

def AskStr(prompt: str, default = None, style: str = "prompt") -> str:
    """Ask for an integer input, and returns it"""
    if default is not None:
        suffix = f" ({default}): "
    else:
        suffix = ": "
    while True:
        response = console.input(f"[{style}]{prompt}{suffix}[/{style}]").strip()
        if response == "":
            if default is not None:
                return default
            console.print("[warning]Please provide an integer.[/warning]")
            continue
        return response