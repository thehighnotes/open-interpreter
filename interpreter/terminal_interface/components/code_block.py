# Modified by thehighnotes (2026) — Jetson hub fork
# See https://github.com/thehighnotes/open-interpreter
import sys
from rich.box import MINIMAL
from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .base_block import BaseBlock

# Rich output renderer — converts plain output strings to Rich renderables
try:
    from ..rich_output import render_output_panel as _render_output
    _HAS_RICH_OUTPUT = True
except ImportError:
    _HAS_RICH_OUTPUT = False


class CodeBlock(BaseBlock):
    """
    Code Blocks display code and outputs in different languages. You can also set the active_line!
    """

    def __init__(self, interpreter=None):
        super().__init__()

        self.type = "code"
        self.highlight_active_line = (
            interpreter.highlight_active_line if interpreter else None
        )

        # Define these for IDE auto-completion
        self.language = ""
        self.output = ""
        self.code = ""
        self.active_line = None
        self.margin_top = True
        self.output_only = False  # When True, only render the output panel (code already shown)

    def end(self):
        self.active_line = None
        self.refresh(cursor=False)
        super().end()

    def refresh(self, cursor=True):
        if not self.code and not self.output:
            return

        # In output_only mode, skip rendering until we have output
        if self.output_only and (self.output == "" or self.output == "None"):
            return

        # Throttle streaming refreshes to prevent scrollback pollution
        if cursor and not self._should_refresh():
            return

        # Build code panel (unless output_only — code was already shown in the previous block)
        if not self.output_only:
            # Get code
            code = self.code

            # Create a table for the code
            code_table = Table(
                show_header=False, show_footer=False, box=None, padding=0, expand=True
            )
            code_table.add_column()

            # Add cursor only if active line highliting is true
            if cursor and (
                self.highlight_active_line
                if self.highlight_active_line is not None
                else True
            ):
                code += "●"

            # Add each line of code to the table
            code_lines = code.strip().split("\n")
            for i, line in enumerate(code_lines, start=1):
                if i == self.active_line and (
                    self.highlight_active_line
                    if self.highlight_active_line is not None
                    else True
                ):
                    # This is the active line, print it with a white background
                    syntax = Syntax(
                        line, self.language, theme="bw", line_numbers=False, word_wrap=True
                    )
                    code_table.add_row(syntax, style="black on white")
                else:
                    # This is not the active line, print it normally
                    syntax = Syntax(
                        line,
                        self.language,
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=True,
                    )
                    code_table.add_row(syntax)

            # Create a panel for the code
            code_panel = Panel(code_table, box=MINIMAL, style="on #272722")

        # Create a panel for the output (if there is any)
        if self.output == "" or self.output == "None":
            output_panel = ""
        elif _HAS_RICH_OUTPUT and not cursor:
            # Final render: use Rich output renderer for structured display
            output_panel = Panel(
                _render_output(self.output),
                box=MINIMAL, style="on #1e1e1e",
            )
        else:
            # Streaming (cursor=True): plain text for speed
            output_panel = Panel(self.output, box=MINIMAL, style="#FFFFFF on #1e1e1e")

        # Create a group with the code table and output panel
        if self.output_only:
            group_items = [output_panel]
        else:
            group_items = [code_panel, output_panel]
        if self.margin_top:
            # This adds some space at the top. Just looks good!
            group_items = [""] + group_items
        group = Group(*group_items)

        # Update the live display
        self.live.update(group)
        self.live.refresh()
