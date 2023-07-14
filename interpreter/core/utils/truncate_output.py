# Modified by thehighnotes (2026) — Jetson hub fork
# See https://github.com/thehighnotes/open-interpreter
import re

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_SPILLOVER_PATH = "/tmp/oi-output-latest.txt"


def truncate_output(data, max_output_chars=2800, add_scrollbars=False):
    # Strip ANSI escape codes before counting (belt-and-suspenders)
    data = _ANSI_RE.sub("", data)

    needs_truncation = False

    message = f"[▼ {max_output_chars}/{len(data)} chars — press v after output]\n\n"

    # Remove previous truncation message if it exists (regex match for varying numbers)
    if data.startswith("[▼ ") or data.startswith("[Output truncated"):
        newline_pos = data.find("\n\n")
        if newline_pos != -1:
            data = data[newline_pos + 2:]
            needs_truncation = True

    # If data exceeds max length, truncate it and add message
    if len(data) > max_output_chars or needs_truncation:
        # Save full output to spillover file before truncating
        try:
            with open(_SPILLOVER_PATH, 'w') as fh:
                fh.write(data)
        except OSError:
            pass
        data = message + data[-max_output_chars:]

    return data


def collapse_for_display(data, collapse_threshold=15, max_display_chars=2800,
                         preview_head=5, preview_tail=3):
    """Collapse long output into a preview for terminal display.

    - If output > collapse_threshold lines: show first/last lines with hidden count
    - If under line threshold but over char limit: old-style char truncation
    - Otherwise: return as-is
    Full output is always saved to _SPILLOVER_PATH.
    """
    data = _ANSI_RE.sub("", data)

    # Strip any previous collapse/truncation markers
    if data.startswith("[collapsed:") or data.startswith("[▼ ") or data.startswith("[Output truncated"):
        newline_pos = data.find("\n\n")
        if newline_pos != -1:
            data = data[newline_pos + 2:]
    # Also strip the preview separator if re-collapsing
    _sep_marker = "  ··· "
    if _sep_marker in data:
        # Already collapsed — don't re-collapse. Return as-is.
        return data

    lines = data.split("\n")
    char_count = len(data)

    if len(lines) > collapse_threshold:
        try:
            with open(_SPILLOVER_PATH, 'w') as fh:
                fh.write(data)
        except OSError:
            pass
        hidden = len(lines) - preview_head - preview_tail
        head = "\n".join(lines[:preview_head])
        tail = "\n".join(lines[-preview_tail:])
        sep = f"  ··· {hidden} more lines ({char_count} chars) — %view for full output ···"
        return f"{head}\n{sep}\n{tail}"

    if char_count > max_display_chars:
        try:
            with open(_SPILLOVER_PATH, 'w') as fh:
                fh.write(data)
        except OSError:
            pass
        message = f"[▼ {max_display_chars}/{char_count} chars — %view for full output]\n\n"
        return message + data[-max_display_chars:]

    return data
