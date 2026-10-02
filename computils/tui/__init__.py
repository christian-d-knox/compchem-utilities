"""The CompUtils TUI. Job actions submit inside the app; the others (FormChk, GoodVibes) return their Intent for Main() to
dispatch exactly as it would a CLI invocation."""


def RunTUI():
    # Imported here so the CLI never pays Textual's import cost
    from ..console import console
    from .. import prompts
    from .app import CompUtilsApp
    from .common import CAPTURE
    # The extractors the TUI reuses print CLI messages; written to the terminal they would corrupt the screen. Only an
    # in-app submission's messages are kept (CAPTURE), to be shown in the app. Nothing may prompt in a worker either
    terminal = console.file
    console.file, prompts.interactive = CAPTURE, False
    try:
        return CompUtilsApp().run()
    finally:
        console.file, prompts.interactive = terminal, True
