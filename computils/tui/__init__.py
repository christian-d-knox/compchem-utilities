"""The CompUtils TUI. It only builds an Intent; Main() dispatches it exactly as it would a CLI invocation."""


def RunTUI():
    # Imported here so the CLI never pays Textual's import cost
    from ..console import console
    from .app import CompUtilsApp
    # The extractors the TUI reuses print CLI messages; written to the terminal they would corrupt the screen
    console.quiet = True
    try:
        return CompUtilsApp().run()
    finally:
        console.quiet = False
