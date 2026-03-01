# Copyright (c) 2026 thehighnotes — AGPL-3.0
"""
Rich output renderer for OI's console output panel.

Converts plain-text command output into Rich renderables at the display
layer. The raw string stays unchanged through the entire pipeline (collapse,
truncation, LLM storage). Only CodeBlock.refresh() calls render_output_panel()
at the final render step.

Integration point (code_block.py line 98):
    Before: Panel(self.output, box=MINIMAL, style="#FFFFFF on #3b3b37")
    After:  Panel(render_output_panel(self.output), box=MINIMAL, style="on #1e1e1e")
"""

import re
from rich.box import ROUNDED
from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

# ── Pattern detection ─────────────────────────────────────────────────────────

# Edit operations: "✓ filepath updated (lines N-M):" or "✓ filepath created (N lines):"
_EDIT_SUCCESS = re.compile(
    r'^✓ (.+?) (?:updated|created) \((?:lines? )?(.+?)\):\n((?:  .+\n?)+)',
    re.MULTILINE
)
_ERROR_LINE = re.compile(r'^ERROR:\s*(.+)', re.MULTILINE)
_NO_CHANGE = re.compile(r'^\(no changes.+\)$', re.MULTILINE)

# ~/edit --show and --map header: "  filepath  (N lines...)"
_SHOW_HEADER = re.compile(r'^  (.+?)  \((\d+ lines.*)\)$', re.MULTILINE)
# --map structural lines footer: "N structural lines / M total"
_MAP_FOOTER = re.compile(r'^\s*(\d+ structural lines.*)$', re.MULTILINE)

# ~/code search tool activity
_TOOL_ACTIVITY = re.compile(r'^\s*\[(?:searching|reading|indexing):.+\]$', re.MULTILINE)
_FILES_LINE = re.compile(r'^Files:\s*(.+)$', re.MULTILINE)
_STATS_LINE = re.compile(r'^\[(\d+ tool calls?, .+)\]$', re.MULTILINE)

# Edit diff: "INSERT N lines after line M ..." or "REPLACE line(s) N-M: ..."
_EDIT_DIFF = re.compile(
    r'^(INSERT|REPLACE)\s+.+?\((\d+)\u2192(\d+) lines\)\s+(.+)$'
)

# Collapse marker from truncate_output.py (already-collapsed output)
_COLLAPSE_MARKER = re.compile(r'^\s*···\s+\d+ more lines')

# Numbered line: "  37  code here" (2+ spaces, digits, 2 spaces, content)
_NUMBERED_LINE_RE = re.compile(r'^(\s*\d+)(  (.*))?$')

# ── Language detection ────────────────────────────────────────────────────────

_EXT_MAP = {
    '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
    '.jsx': 'jsx', '.tsx': 'tsx', '.rs': 'rust', '.go': 'go',
    '.rb': 'ruby', '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
    '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp',
    '.java': 'java', '.kt': 'kotlin', '.swift': 'swift',
    '.html': 'html', '.css': 'css', '.scss': 'scss',
    '.yml': 'yaml', '.yaml': 'yaml', '.toml': 'toml',
    '.json': 'json', '.xml': 'xml', '.md': 'markdown',
    '.sql': 'sql', '.lua': 'lua', '.r': 'r',
    '.env': 'bash', '.conf': 'ini', '.ini': 'ini',
    '.dockerfile': 'docker', '.makefile': 'makefile',
}


def _detect_lang(path):
    """Extract language hint from file path."""
    path_lower = path.lower()
    # Handle host:path format
    if ':' in path_lower:
        path_lower = path_lower.split(':', 1)[1]
    for ext, lang in _EXT_MAP.items():
        if path_lower.endswith(ext):
            return lang
    # Special filenames
    base = path_lower.rsplit('/', 1)[-1] if '/' in path_lower else path_lower
    if base in ('dockerfile',):
        return 'docker'
    if base in ('makefile', 'gnumakefile'):
        return 'makefile'
    return None


def _parse_numbered_lines(body_text):
    """Parse numbered lines into (num_str, code_str) pairs.
    Handles empty lines, continuation hints, and map depth markers.
    """
    parsed = []
    for line in body_text.split('\n'):
        if not line.strip():
            parsed.append(('', ''))
            continue
        m = _NUMBERED_LINE_RE.match(line)
        if m:
            code_part = m.group(3) if m.group(3) is not None else ''
            parsed.append((m.group(1).strip(), code_part))
        elif line.strip().startswith('...'):
            parsed.append(('·', line.strip()))
        else:
            parsed.append(('', line))
    return parsed


def _build_code_table(parsed_lines, lang):
    """Build a Rich Table with dim line numbers and syntax-highlighted code."""
    num_width = max((len(p[0]) for p in parsed_lines if p[0]), default=3) + 1
    table = Table(
        show_header=False, show_footer=False, box=None,
        padding=(0, 0), expand=True, show_edge=False,
    )
    table.add_column(style="dim", width=num_width, no_wrap=True)
    table.add_column(ratio=1)

    for num, code in parsed_lines:
        if lang:
            code_cell = Syntax(code, lang, theme="monokai",
                               line_numbers=False, word_wrap=True)
        else:
            code_cell = Text(code)
        table.add_row(num, code_cell)
    return table


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_output(text):
    """Parse plain-text output into a list of Rich renderables."""
    if not text or not text.strip():
        return [Text("")]

    remaining = text.rstrip()

    # ── Collapsed output (already processed by collapse_for_display) ──
    # Pass through as plain text — don't try to parse collapsed fragments
    if _COLLAPSE_MARKER.search(remaining):
        return [Text(remaining)]

    # ── Edit diff: "INSERT ..." or "REPLACE ..." ──
    first_line = remaining.split('\n', 1)[0]
    diff_match = _EDIT_DIFF.match(first_line)
    if diff_match:
        op_word = diff_match.group(1)
        filepath = diff_match.group(4).strip()
        lang = _detect_lang(filepath)

        if op_word == 'INSERT':
            op_text, op_color, border = 'INSERT', 'green', 'green'
        else:
            op_text, op_color, border = 'REPLACE', 'yellow', 'yellow'

        # Parse body lines into styled diff entries
        body_lines = remaining.split('\n')[1:]
        diff_table = Table(
            show_header=False, show_footer=False, box=None,
            padding=(0, 0), expand=True, show_edge=False,
        )
        diff_table.add_column(width=2, no_wrap=True)  # prefix (-/+/ /!)
        diff_table.add_column(style="dim", width=5, no_wrap=True)  # line number
        diff_table.add_column(ratio=1)  # content

        warnings = []
        for line in body_lines:
            if line.startswith('! '):
                warnings.append(line)
                continue
            if line.startswith('- '):
                prefix = Text("-", style="bold red")
                rest = line[2:]
            elif line.startswith('+ '):
                prefix = Text("+", style="bold green")
                rest = line[2:]
            else:
                prefix = Text(" ", style="dim")
                rest = line[2:] if line.startswith('  ') else line

            # Split "  NUM  content" into number and code
            m = _NUMBERED_LINE_RE.match(f"  {rest}" if not rest.startswith(' ') else rest)
            if m:
                num_str = m.group(1).strip()
                code_str = m.group(3) if m.group(3) is not None else ''
            elif rest.strip().startswith('...'):
                num_str = ''
                code_str = rest.strip()
            else:
                num_str = ''
                code_str = rest

            if lang:
                code_cell = Syntax(code_str, lang, theme="monokai",
                                   line_numbers=False, word_wrap=True)
            else:
                code_cell = Text(code_str)
            diff_table.add_row(prefix, num_str, code_cell)

        title = Text()
        title.append(f" {op_text} ", style=f"bold {op_color}")
        title.append(f" {filepath} ", style="bold cyan")

        content_parts = [diff_table]
        if warnings:
            warn_text = Text()
            for w in warnings:
                warn_text.append(w + '\n', style="bold yellow")
            content_parts.append(warn_text)

        content = Group(*content_parts) if len(content_parts) > 1 else content_parts[0]
        return [Panel(
            content, title=title, title_align="left",
            border_style=border, box=ROUNDED, padding=(0, 1),
        )]

    # ── Edit operation: "✓ file created (N lines):" ──
    edit_match = _EDIT_SUCCESS.match(remaining)
    if edit_match:
        filepath, detail, body = edit_match.groups()
        lang = _detect_lang(filepath)

        first_line_c = remaining[:remaining.find('\n')]
        if 'created' in first_line_c:
            op_text, op_color = '+ CREATE', 'green'
        else:
            op_text, op_color = '✓ EDIT', 'green'

        parsed_lines = _parse_numbered_lines(body.strip())
        code_table = _build_code_table(parsed_lines, lang)

        title = Text()
        title.append(f" {op_text} ", style=f"bold {op_color}")
        title.append(f" {filepath} ", style="bold cyan")
        title.append(f" {detail} ", style="dim")

        renderables = [Panel(
            code_table, title=title, title_align="left",
            border_style="green", box=ROUNDED, padding=(0, 1),
        )]

        # Trailing content (e.g. "(no changes — content identical)")
        after = remaining[edit_match.end():]
        if after.strip():
            renderables.extend(parse_output(after))
        return renderables

    # ── Error ──
    err_match = _ERROR_LINE.match(remaining.strip())
    if err_match:
        error_text = Text()
        error_text.append("✗ ", style="bold red")
        error_text.append(err_match.group(1), style="red")
        renderables = [Panel(
            error_text, border_style="red", box=ROUNDED, padding=(0, 1),
        )]
        after = remaining.strip()[err_match.end():]
        if after.strip():
            renderables.extend(parse_output(after))
        return renderables

    # ── No change ──
    nc_match = _NO_CHANGE.match(remaining.strip())
    if nc_match:
        nc_text = Text()
        nc_text.append("— ", style="bold yellow")
        nc_text.append("no changes — content identical", style="yellow")
        return [Panel(
            nc_text, border_style="yellow", box=ROUNDED, padding=(0, 1),
        )]

    # ── File view: --show / --map output ──
    show_match = _SHOW_HEADER.match(remaining)
    if show_match:
        filepath = show_match.group(1).strip()
        meta = show_match.group(2)
        lang = _detect_lang(filepath)

        lines = remaining.split('\n')
        body_lines = lines[1:]

        # Detect --map by checking for "structural lines" footer or depth markers (··)
        is_map = bool(_MAP_FOOTER.search(remaining)) or any(
            '··' in l and _NUMBERED_LINE_RE.match(l) for l in body_lines[:5]
        )

        # Separate body from footer (continuation hints, map footer)
        content_lines = []
        footer_lines = []
        for line in body_lines:
            if line.strip().startswith('...') or _MAP_FOOTER.match(line):
                footer_lines.append(line.strip())
            else:
                content_lines.append(line)

        parsed_lines = _parse_numbered_lines('\n'.join(content_lines))
        code_table = _build_code_table(parsed_lines, lang)

        title = Text()
        title.append(f" {filepath} ", style="bold cyan")
        title.append(f" ({meta}) ", style="dim")
        if is_map:
            title.append(" MAP ", style="bold magenta")

        border_color = "magenta" if is_map else "cyan"

        content = code_table
        if footer_lines:
            footer = Text('\n'.join(footer_lines), style="dim")
            content = Group(code_table, Text(''), footer)

        return [Panel(
            content, title=title, title_align="left",
            border_style=border_color, box=ROUNDED, padding=(0, 1),
        )]

    # ── Code search with tool activity ──
    stripped_lines = remaining.split('\n')
    activity_lines = []
    answer_lines = []
    files_ref = None
    stats_ref = None
    in_activity = True

    for line in stripped_lines:
        if in_activity and _TOOL_ACTIVITY.match(line):
            activity_lines.append(line.strip())
        elif _STATS_LINE.match(line):
            stats_ref = _STATS_LINE.match(line).group(1)
        elif _FILES_LINE.match(line):
            files_ref = _FILES_LINE.match(line).group(1)
        else:
            in_activity = False
            answer_lines.append(line)

    if activity_lines and answer_lines:
        renderables = []
        activity = Text()
        for al in activity_lines:
            activity.append("⟳ ", style="dim cyan")
            activity.append(al.strip('[] ') + '\n', style="dim")
        renderables.append(activity)

        answer = '\n'.join(answer_lines).strip()
        if answer:
            renderables.append(Text(answer))

        if files_ref or stats_ref:
            footer = Text()
            if files_ref:
                footer.append("Files: ", style="cyan")
                footer.append(files_ref, style="dim")
            if stats_ref:
                if files_ref:
                    footer.append("  ")
                footer.append(f"[{stats_ref}]", style="dim")
            renderables.append(footer)

        return renderables

    # ── Fallback: plain text ──
    return [Text(remaining)]


def render_output_panel(output_text):
    """Convert output text to a Rich renderable for the CodeBlock output panel."""
    renderables = parse_output(output_text)
    if len(renderables) == 1:
        return renderables[0]
    return Group(*renderables)
