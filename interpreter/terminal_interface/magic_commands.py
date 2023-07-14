# Modified by thehighnotes (2026) — Jetson hub fork
# See https://github.com/thehighnotes/open-interpreter
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from ..core.utils.system_debug_info import system_info
from .utils.count_tokens import count_messages_tokens
from .utils.export_to_markdown import export_to_markdown


def handle_undo(self, arguments):
    # Removes all messages after the most recent user entry (and the entry itself).
    # Therefore user can jump back to the latest point of conversation.
    # Also gives a visual representation of the messages removed.

    if len(self.messages) == 0:
        return
    # Find the index of the last 'role': 'user' entry
    last_user_index = None
    for i, message in enumerate(self.messages):
        if message.get("role") == "user":
            last_user_index = i

    removed_messages = []

    # Remove all messages after the last 'role': 'user'
    if last_user_index is not None:
        removed_messages = self.messages[last_user_index:]
        self.messages = self.messages[:last_user_index]

    print("")  # Aesthetics.

    # Print out a preview of what messages were removed.
    for message in removed_messages:
        if "content" in message and message["content"] != None:
            self.display_message(
                f"**Removed message:** `\"{message['content'][:30]}...\"`"
            )
        elif "function_call" in message:
            self.display_message(
                f"**Removed codeblock**"
            )  # TODO: Could add preview of code removed here.

    print("")  # Aesthetics.


def handle_help(self, arguments):
    commands_description = {
        "%% [commands]": "Run commands in system shell",
        "%verbose [true/false]": "Toggle verbose mode. Without arguments or with 'true', it enters verbose mode. With 'false', it exits verbose mode.",
        "%reset": "Resets the current session.",
        "%undo": "Remove previous messages and its response from the message history.",
        "%save_message [path]": "Saves messages to a specified JSON path. If no path is provided, it defaults to 'messages.json'.",
        "%load_message [path]": "Loads messages from a specified JSON path. If no path is provided, it defaults to 'messages.json'.",
        "%tokens [prompt]": "EXPERIMENTAL: Calculate the tokens used by the next request based on the current conversation's messages and estimate the cost of that request; optionally provide a prompt to also calculate the tokens used by that prompt and the total amount of tokens that will be sent with the next request",
        "%help": "Show this help message.",
        "%info": "Show system and interpreter information",
        "%jupyter": "Export the conversation to a Jupyter notebook file",
        "%markdown [path]": "Export the conversation to a specified Markdown path. If no path is provided, it will be saved to the Downloads folder with a generated conversation name.",
        "%context [setting value]": "Show or adjust context settings. Settings: llm, display, terminal, window, tokens.",
        "%model [14b|coder]": "Show or switch the active LLM model.",
        "%view": "Open last collapsed/truncated command output in less.",
        "%allow <pattern>": "Add a command prefix to persistent auto-run list.",
        "%deny <number|all>": "Remove an allowed pattern by number or clear all.",
        "%permissions": "Show persistent and built-in auto-run permissions.",
        "%auto-edit": "Enable auto-apply for ~~~edit blocks (no confirmation).",
        "%confirm-edit": "Require confirmation for ~~~edit blocks (default).",
        "%status": "Hub dashboard (hosts, projects, services).",
        "%next": "Prioritized hub action list.",
        "%projects": "List all registered projects.",
        "%repo [cmd]": "Git management (status/log/commit/push/deploy).",
        "%checkpoint [msg]": "Batch commit+push all dirty projects.",
        "%backup [flags]": "Rsync hub backup (--dry-run, --list).",
        "%wake": "Send Wake-on-LAN packet.",
        "%research [flags]": "Research digest (--fetch to score new items).",
        "%health": "Show health probe results.",
        "%services": "Service status across all projects.",
        "%switch [project]": "Switch project context (fuzzy match).",
        "%overview [project]": "Project overview (defaults to current project).",
        "%notify [flags]": "Notification history (--all, --clear, --count).",
        "%image [paths] [prompt]": "Send image to vision model (clipboard or file paths).",
    }

    base_message = ["> **Available Commands:**\n\n"]

    # Add each command and its description to the message
    for cmd, desc in commands_description.items():
        base_message.append(f"- `{cmd}`: {desc}\n")

    additional_info = [
        "\n\nFor further assistance, please join our community Discord or consider contributing to the project's development."
    ]

    # Combine the base message with the additional info
    full_message = base_message + additional_info

    self.display_message("".join(full_message))


def handle_verbose(self, arguments=None):
    if arguments == "" or arguments == "true":
        self.display_message("> Entered verbose mode")
        print("\n\nCurrent messages:\n")
        for message in self.messages:
            message = message.copy()
            if message["type"] == "image" and message.get("format") not in [
                "path",
                "description",
            ]:
                message["content"] = (
                    message["content"][:30] + "..." + message["content"][-30:]
                )
            print(message, "\n")
        print("\n")
        self.verbose = True
    elif arguments == "false":
        self.display_message("> Exited verbose mode")
        self.verbose = False
    else:
        self.display_message("> Unknown argument to verbose command.")


def handle_debug(self, arguments=None):
    if arguments == "" or arguments == "true":
        self.display_message("> Entered debug mode")
        print("\n\nCurrent messages:\n")
        for message in self.messages:
            message = message.copy()
            if message["type"] == "image" and message.get("format") not in [
                "path",
                "description",
            ]:
                message["content"] = (
                    message["content"][:30] + "..." + message["content"][-30:]
                )
            print(message, "\n")
        print("\n")
        self.debug = True
    elif arguments == "false":
        self.display_message("> Exited verbose mode")
        self.debug = False
    else:
        self.display_message("> Unknown argument to debug command.")


def handle_auto_run(self, arguments=None):
    if arguments == "" or arguments == "true":
        self.display_message("> Entered auto_run mode")
        self.auto_run = True
    elif arguments == "false":
        self.display_message("> Exited auto_run mode")
        self.auto_run = False
    else:
        self.display_message("> Unknown argument to auto_run command.")


def handle_info(self, arguments):
    system_info(self)


def handle_reset(self, arguments):
    self.reset()
    # Delete saved session file so next launch starts fresh
    _session_key = os.environ.get("OI_PROJECT", "_global")
    _session_file = os.path.expanduser(f"~/.cache/oi-sessions/{_session_key}.json")
    try:
        if os.path.exists(_session_file):
            os.remove(_session_file)
    except OSError:
        pass
    # Clear terminal screen
    os.system('clear')
    # Show confirmation with context info
    print(f"\n  \033[32m✓\033[0m Session reset — messages cleared, saved session deleted\n")


def default_handle(self, arguments):
    self.display_message("> Unknown command")
    handle_help(self, arguments)


def handle_save_message(self, json_path):
    if json_path == "":
        json_path = "messages.json"
    if not json_path.endswith(".json"):
        json_path += ".json"
    with open(json_path, "w") as f:
        json.dump(self.messages, f, indent=2)

    self.display_message(f"> messages json export to {os.path.abspath(json_path)}")


def handle_load_message(self, json_path):
    if json_path == "":
        json_path = "messages.json"
    if not json_path.endswith(".json"):
        json_path += ".json"
    with open(json_path, "r") as f:
        self.messages = json.load(f)

    self.display_message(f"> messages json loaded from {os.path.abspath(json_path)}")


def handle_count_tokens(self, prompt):
    messages = [{"role": "system", "message": self.system_message}] + self.messages

    outputs = []

    if len(self.messages) == 0:
        (conversation_tokens, conversation_cost) = count_messages_tokens(
            messages=messages, model=self.llm.model
        )
    else:
        (conversation_tokens, conversation_cost) = count_messages_tokens(
            messages=messages, model=self.llm.model
        )

    outputs.append(
        (
            f"> Tokens sent with next request as context: {conversation_tokens} (Estimated Cost: ${conversation_cost})"
        )
    )

    if prompt:
        (prompt_tokens, prompt_cost) = count_messages_tokens(
            messages=[prompt], model=self.llm.model
        )
        outputs.append(
            f"> Tokens used by this prompt: {prompt_tokens} (Estimated Cost: ${prompt_cost})"
        )

        total_tokens = conversation_tokens + prompt_tokens
        total_cost = conversation_cost + prompt_cost

        outputs.append(
            f"> Total tokens for next request with this prompt: {total_tokens} (Estimated Cost: ${total_cost})"
        )

    outputs.append(
        f"**Note**: This functionality is currently experimental and may not be accurate. Please report any issues you find to the [Open Interpreter GitHub repository](https://github.com/OpenInterpreter/open-interpreter)."
    )

    self.display_message("\n".join(outputs))


def get_downloads_path():
    if os.name == "nt":
        # For Windows
        downloads = os.path.join(os.environ["USERPROFILE"], "Downloads")
    else:
        # For MacOS and Linux
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        # For some GNU/Linux distros, there's no '~/Downloads' dir by default
        if not os.path.exists(downloads):
            os.makedirs(downloads)
    return downloads


def install_and_import(package):
    try:
        module = __import__(package)
    except ImportError:
        try:
            # Install the package silently with pip
            print("")
            print(f"Installing {package}...")
            print("")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            module = __import__(package)
        except subprocess.CalledProcessError:
            # If pip fails, try pip3
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip3", "install", package],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError:
                print(f"Failed to install package {package}.")
                return
    finally:
        globals()[package] = module
    return module


def jupyter(self, arguments):
    # Dynamically install nbformat if not already installed
    nbformat = install_and_import("nbformat")
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

    downloads = get_downloads_path()
    current_time = datetime.now()
    formatted_time = current_time.strftime("%m-%d-%y-%I%M%p")
    filename = f"open-interpreter-{formatted_time}.ipynb"
    notebook_path = os.path.join(downloads, filename)
    nb = new_notebook()
    cells = []

    for msg in self.messages:
        if msg["role"] == "user" and msg["type"] == "message":
            # Prefix user messages with '>' to render them as block quotes, so they stand out
            content = f"> {msg['content']}"
            cells.append(new_markdown_cell(content))
        elif msg["role"] == "assistant" and msg["type"] == "message":
            cells.append(new_markdown_cell(msg["content"]))
        elif msg["type"] == "code":
            # Handle the language of the code cell
            if "format" in msg and msg["format"]:
                language = msg["format"]
            else:
                language = "python"  # Default to Python if no format specified
            code_cell = new_code_cell(msg["content"])
            code_cell.metadata.update({"language": language})
            cells.append(code_cell)

    nb["cells"] = cells

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)

    print("")
    self.display_message(
        f"Jupyter notebook file exported to {os.path.abspath(notebook_path)}"
    )


def markdown(self, export_path: str):
    # If it's an empty conversations
    if len(self.messages) == 0:
        print("No messages to export.")
        return

    # If user doesn't specify the export path, then save the exported PDF in '~/Downloads'
    if not export_path:
        export_path = get_downloads_path() + f"/{self.conversation_filename[:-4]}md"

    export_to_markdown(self.messages, export_path)


# ── Persistent permissions (%allow / %deny / %permissions) ───────────────
_PERMISSIONS_FILE = os.path.expanduser("~/.config/open-interpreter/permissions.json")

def _perm_load_json():
    """Read full permissions dict from JSON. Returns dict."""
    try:
        with open(_PERMISSIONS_FILE) as f:
            return json.loads(f.read())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _perm_save_json(data):
    """Write full permissions dict to JSON."""
    os.makedirs(os.path.dirname(_PERMISSIONS_FILE), exist_ok=True)
    with open(_PERMISSIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

def _perm_load():
    """Read allowed patterns from JSON. Returns list."""
    return _perm_load_json().get("allowed", [])

def _perm_save(patterns):
    """Write allowed patterns to JSON, preserving other keys."""
    data = _perm_load_json()
    data["allowed"] = patterns
    _perm_save_json(data)

def handle_allow(self, arguments):
    """Add a command prefix to persistent allowed list."""
    pattern = arguments.strip()
    if not pattern:
        print("\n  Usage: %allow <command prefix>")
        print("  Example: %allow curl")
        print("           %allow sudo apt\n")
        return
    patterns = _perm_load()
    if pattern in patterns:
        print(f"\n  Already allowed: {pattern}\n")
        return
    patterns.append(pattern)
    _perm_save(patterns)
    print(f"\n  \033[32m✓\033[0m Added: \033[1m{pattern}\033[0m — commands starting with this will auto-run\n")

def handle_deny(self, arguments):
    """Remove an allowed pattern by number or clear all."""
    arg = arguments.strip()
    if not arg:
        print("\n  Usage: %deny <number>  or  %deny all\n")
        return
    patterns = _perm_load()
    if arg.lower() == "all":
        if not patterns:
            print("\n  No user-allowed patterns to clear.\n")
            return
        _perm_save([])
        print(f"\n  \033[33m✓\033[0m Cleared all {len(patterns)} user-allowed pattern(s)\n")
        return
    try:
        idx = int(arg)
    except ValueError:
        print(f"\n  Expected a number or 'all', got: {arg}\n")
        return
    if idx < 1 or idx > len(patterns):
        print(f"\n  Invalid index: {idx} (valid: 1–{len(patterns)})\n")
        return
    removed = patterns.pop(idx - 1)
    _perm_save(patterns)
    print(f"\n  \033[33m✓\033[0m Removed: \033[1m{removed}\033[0m\n")

def handle_permissions(self, arguments):
    """Show current permission settings."""
    patterns = _perm_load()
    print("\n  \033[1mPersistent allowed commands\033[0m", end="")
    print(f"  \033[2m({_PERMISSIONS_FILE})\033[0m")
    if patterns:
        for i, p in enumerate(patterns, 1):
            print(f"    {i}. {p}")
    else:
        print("    (none)")

    # Show built-in safe prefixes from profile
    _builtin = [
        'cat', 'head', 'tail', 'less', 'more', 'ls', 'pwd', 'whoami',
        'hostname', 'uname', 'df', 'du', 'free', 'uptime', 'ps', 'top',
        'which', 'type', 'file', 'stat', 'wc', 'sort', 'grep', 'find',
        'date', 'cal', 'ip', 'ifconfig', 'ping', 'ss', 'netstat',
        'nvidia-smi', '~/hub', '~/overview', '~/research', '~/search',
        '~/code', '~/git status', '~/git log', 'diff', 'cmp',
    ]
    print("\n  \033[1mBuilt-in safe prefixes\033[0m  \033[2m(always auto-run)\033[0m")
    # Display in compact columns
    line = "    "
    for p in _builtin:
        if len(line) + len(p) + 2 > 78:
            print(line)
            line = "    "
        line += p + ", "
    if line.strip():
        print(line.rstrip(", "))

    # Show auto_edit status
    _auto_edit = _perm_load_json().get("auto_edit", False)
    _ae_status = "\033[32mON\033[0m" if _auto_edit else "\033[33mOFF\033[0m (default)"
    print(f"\n  \033[1mEdit block auto-apply\033[0m  {_ae_status}")
    print(f"    Toggle: %auto-edit / %confirm-edit")

    print(f"\n  Usage: %allow <pattern>  |  %deny <number>  |  %deny all\n")


def handle_auto_edit(self, arguments):
    """Enable auto-apply for ~~~edit blocks."""
    data = _perm_load_json()
    data["auto_edit"] = True
    _perm_save_json(data)
    print(f"\n  \033[32m✓\033[0m Edit blocks will now auto-apply without confirmation\n")

def handle_confirm_edit(self, arguments):
    """Disable auto-apply for ~~~edit blocks (require confirmation)."""
    data = _perm_load_json()
    data["auto_edit"] = False
    _perm_save_json(data)
    print(f"\n  \033[33m✓\033[0m Edit blocks will now require confirmation (default)\n")


def _looks_like_markdown(text):
    """Heuristic: does this text contain enough markdown to warrant rendering?"""
    import re as _re
    indicators = 0
    if _re.search(r'^#{1,6}\s', text, _re.MULTILINE):  indicators += 2  # headings
    if _re.search(r'^[-*]\s', text, _re.MULTILINE):     indicators += 1  # unordered list
    if _re.search(r'^\d+\.\s', text, _re.MULTILINE):    indicators += 1  # ordered list
    if '```' in text:                                    indicators += 2  # code fence
    if _re.search(r'\*\*.+?\*\*', text):                 indicators += 1  # bold
    if _re.search(r'^\s*>\s', text, _re.MULTILINE):     indicators += 1  # blockquote
    if _re.search(r'\[.+?\]\(.+?\)', text):              indicators += 1  # link
    return indicators >= 2


def handle_view(self, arguments):
    """Open the last collapsed/truncated output. Renders markdown if detected. Press q to return."""
    spillover = "/tmp/oi-output-latest.txt"
    if not os.path.exists(spillover):
        print("\n  No saved output to view.\n")
        return
    with open(spillover) as f:
        content = f.read()
    lines = content.count('\n') + 1
    size = len(content)

    if _looks_like_markdown(content):
        # Render markdown with Rich, pipe through less
        from rich.console import Console as _RCon
        from rich.markdown import Markdown as _RMd
        import io
        buf = io.StringIO()
        _con = _RCon(file=buf, width=100, force_terminal=True)
        _con.print(_RMd(content))
        rendered = buf.getvalue()
        # Write rendered output to temp file for less
        _rendered_path = "/tmp/oi-output-rendered.txt"
        with open(_rendered_path, 'w') as f:
            f.write(rendered)
        print(f"\n  {lines} lines, {size} chars (markdown) — press q to return\n")
        subprocess.call(["less", "-R", _rendered_path])
    else:
        print(f"\n  {lines} lines, {size} chars — press q to return\n")
        subprocess.call(["less", spillover])


# Auto-detect profile path from --profile CLI arg, or fall back to default
_PROFILE_PATH = None
for _i, _arg in enumerate(sys.argv):
    if _arg == '--profile' and _i + 1 < len(sys.argv):
        _pname = sys.argv[_i + 1]
        _candidate = os.path.expanduser(f"~/.config/open-interpreter/profiles/{_pname}")
        if os.path.exists(_candidate):
            _PROFILE_PATH = _candidate
        elif os.path.exists(_pname):
            _PROFILE_PATH = _pname
        break
if _PROFILE_PATH is None:
    # Fall back to any .py file in profiles dir
    _profiles_dir = os.path.expanduser("~/.config/open-interpreter/profiles")
    if os.path.isdir(_profiles_dir):
        _pfiles = [f for f in os.listdir(_profiles_dir) if f.endswith('.py')]
        if len(_pfiles) == 1:
            _PROFILE_PATH = os.path.join(_profiles_dir, _pfiles[0])

# Maps %context setting names to (target, attr, label, profile regex pattern)
# target: "self" = interpreter, "llm" = interpreter.llm
import re as _re
_CONTEXT_SETTINGS = {
    "llm":      ("self", "max_llm_output",         "LLM output limit",           _re.compile(r'^(interpreter\.max_llm_output\s*=\s*)\d+', _re.MULTILINE)),
    "display":  ("self", "display_collapse_lines",  "Display collapse threshold", _re.compile(r'^(interpreter\.display_collapse_lines\s*=\s*)\d+', _re.MULTILINE)),
    "terminal": ("self", "max_output",              "Terminal char limit",        _re.compile(r'^(interpreter\.max_output\s*=\s*)\d+', _re.MULTILINE)),
    "window":   ("llm",  "context_window",          "Context window",            _re.compile(r'^(interpreter\.llm\.context_window\s*=\s*)\d+', _re.MULTILINE)),
    "tokens":   ("llm",  "max_tokens",              "Max response tokens",       _re.compile(r'^(interpreter\.llm\.max_tokens\s*=\s*)\d+', _re.MULTILINE)),
}


def _persist_to_profile(pattern, value):
    """Update a setting in the profile file. Returns True on success."""
    try:
        text = open(_PROFILE_PATH).read()
        new_text, count = pattern.subn(rf'\g<1>{value}', text, count=1)
        if count == 0:
            return False
        with open(_PROFILE_PATH, 'w') as f:
            f.write(new_text)
        return True
    except OSError:
        return False


def handle_model(self, arguments):
    """Show or switch the active LLM model. Usage: %model [ollama/model-name]"""
    current = self.llm.model.split("/")[-1] if "/" in self.llm.model else self.llm.model

    if not arguments:
        print(f"\n  Current model: \033[1m{current}\033[0m")
        print(f"  Context window: {self.llm.context_window}")
        print(f"  Max tokens: {self.llm.max_tokens}")
        print(f"  API base: {getattr(self.llm, 'api_base', 'default')}")
        print(f"\n  Usage: %model <model-name>  (e.g. %model llama3:8b)\n")
        return

    model_name = arguments.strip()
    if model_name == current:
        print(f"\n  Already using {model_name}\n")
        return

    prefix = "ollama/" if not model_name.startswith("ollama/") else ""
    self.llm.model = f"{prefix}{model_name}"
    print(f"\n  Switched to \033[1m{model_name}\033[0m (ctx={self.llm.context_window}, max_tok={self.llm.max_tokens})")
    print(f"  Conversation history preserved ({len(self.messages)} messages)\n")


def handle_context(self, arguments):
    """Show or adjust LLM/display context settings. Changes are saved to profile."""
    parts = arguments.split() if arguments else []

    if not parts:
        # Show current settings
        _llm = getattr(self, 'max_llm_output', self.max_output)
        _collapse = getattr(self, 'display_collapse_lines', 15)
        _terminal = self.max_output
        _ctx_win = getattr(self.llm, 'context_window', 0)
        _max_tok = getattr(self.llm, 'max_tokens', 0)
        _msgs = len(self.messages)
        # Context fill estimate (~4 chars per token)
        _sys_chars = len(getattr(self, 'system_message', '') or '')
        _msg_chars = sum(len(str(m.get('content', ''))) for m in self.messages)
        _est_tokens = (_sys_chars + _msg_chars) // 4
        _ctx_pct = min(int(_est_tokens / _ctx_win * 100), 100) if _ctx_win > 0 else 0
        print(f"\n  Context settings:")
        print(f"    LLM output limit:      {_llm} chars")
        print(f"    Display collapse:       {_collapse} lines")
        print(f"    Terminal char limit:    {_terminal} chars")
        print(f"    Context window:         {_ctx_win} tokens")
        print(f"    Max response tokens:    {_max_tok}")
        print(f"    Messages in history:    {_msgs}")
        print(f"    Est. context fill:      ~{_est_tokens} tokens ({_ctx_pct}%)")
        print()
        return

    if len(parts) != 2:
        print("\n  Usage: %context <setting> <value>")
        print("  Settings: llm, display, terminal, window, tokens")
        print("  No args = show current settings\n")
        return

    setting, value = parts
    try:
        value = int(value)
    except ValueError:
        print(f"\n  Value must be an integer, got: {value}\n")
        return

    if setting not in _CONTEXT_SETTINGS:
        print(f"\n  Unknown setting: {setting}")
        print("  Available: llm, display, terminal, window, tokens\n")
        return

    target, attr, label, pattern = _CONTEXT_SETTINGS[setting]
    obj = self.llm if target == "llm" else self
    setattr(obj, attr, value)
    saved = _persist_to_profile(pattern, value)
    suffix = " (saved)" if saved else " (session only — profile write failed)"
    print(f"\n  {label} → {value}{suffix}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Hub magic commands — bypass LLM, run tools directly
# ─────────────────────────────────────────────────────────────────────────────

def _run_hub_tool(args, timeout=30):
    """Run a hub tool and print its output. Returns exit code."""
    # Check if the tool exists before running
    tool_path = args[1] if len(args) > 1 and args[0] == sys.executable else args[0]
    tool_path = os.path.expanduser(tool_path)
    if not os.path.exists(tool_path):
        tool_name = os.path.basename(tool_path)
        print(f"\n  ⚠ Hub tool '{tool_name}' not found at {tool_path}")
        print(f"  Run: python3 tools/hub/install.py (from OI repo) to set up hub tools")
        return 1
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'},
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        if output.strip():
            print(output.rstrip())
        return result.returncode
    except subprocess.TimeoutExpired:
        print(f"\n  ⚠ Command timed out after {timeout}s")
        return 1
    except Exception as e:
        print(f"\n  ⚠ Error: {e}")
        return 1


def handle_status(self, arguments):
    _run_hub_tool([sys.executable, os.path.expanduser('~/hub'), '--status'], timeout=20)


def handle_next(self, arguments):
    _run_hub_tool([sys.executable, os.path.expanduser('~/hub'), '--next'], timeout=20)


def handle_projects(self, arguments):
    """List all registered projects from projects.json."""
    try:
        with open(os.path.expanduser('~/.config/hub/projects.json')) as f:
            data = json.load(f)
        order = data.get('order', sorted(data['projects'].keys()))
        print()
        for key in order:
            proj = data['projects'].get(key, {})
            host = proj.get('host', '?')
            name = proj.get('name', key.upper())
            tagline = proj.get('tagline', '')
            svcs = len(proj.get('services', []))
            svc_str = f"  ({svcs} svc)" if svcs else ""
            print(f"  {key:<18} {host:<5} {name}{svc_str}")
            if tagline:
                print(f"  {'':18} \033[0;90m{tagline}\033[0m")
        print()
    except Exception as e:
        print(f"\n  ⚠ Error reading projects: {e}\n")


def handle_hub_repo(self, arguments):
    cmd = [sys.executable, os.path.expanduser('~/git')]
    if arguments:
        cmd.extend(arguments.split())
    _run_hub_tool(cmd, timeout=30)


def handle_checkpoint(self, arguments):
    cmd = [sys.executable, os.path.expanduser('~/git'), 'checkpoint']
    if arguments:
        cmd.append(arguments)
    cmd.append('--yes')
    _run_hub_tool(cmd, timeout=60)


def handle_hub_backup(self, arguments):
    cmd = [sys.executable, os.path.expanduser('~/backup')]
    if arguments:
        cmd.extend(arguments.split())
    _run_hub_tool(cmd, timeout=60)


def handle_wake(self, arguments):
    wol_script = os.environ.get("OI_WOL_SCRIPT", os.path.expanduser("~/wol.sh"))
    _run_hub_tool([wol_script], timeout=10)


def handle_hub_research(self, arguments):
    cmd = [sys.executable, os.path.expanduser('~/research')]
    if arguments:
        cmd.extend(arguments.split())
    _run_hub_tool(cmd, timeout=120)


def handle_hub_health(self, arguments):
    """Show health probe results from cache."""
    state_file = os.path.expanduser('~/.cache/health/latest.json')
    if not os.path.exists(state_file):
        print("\n  No health data — run health-probe first\n")
        return
    try:
        with open(state_file) as f:
            state = json.load(f)
        ts = state.get('ts', 0)
        age_min = int((time.time() - ts) / 60)
        # Build checks from state keys (host labels come from hub config)
        _host_labels = {}
        try:
            from hub_common import HOSTS
            _host_labels = {k: v['name'] for k, v in HOSTS.items()}
        except Exception:
            pass
        _service_keys = [('ollama', 'Ollama'), ('code_assistant', 'Code Assistant'), ('autosummary', 'Autosummary')]
        checks = [(_host_labels.get(k, k.upper()), state.get(k)) for k in _host_labels]
        checks += [(label, state.get(key)) for key, label in _service_keys]
        print(f"\n  Health Status  ({age_min}m ago)\n")
        for label, up in checks:
            dot = "\033[0;32m●\033[0m" if up else "\033[1;31m●\033[0m"
            status = "\033[0;32mup\033[0m" if up else "\033[1;31mdown\033[0m"
            print(f"    {dot} {label:<20} {status}")
        print()
    except Exception as e:
        print(f"\n  ⚠ Error: {e}\n")


def handle_services(self, arguments):
    _run_hub_tool([sys.executable, os.path.expanduser('~/hub'), '--services'], timeout=20)


def handle_switch(self, arguments):
    """Switch OI project context."""
    projects_file = os.path.expanduser('~/.config/hub/projects.json')
    try:
        with open(projects_file) as f:
            data = json.load(f)
    except Exception as e:
        print(f"\n  ⚠ Error reading projects: {e}\n")
        return

    projects = data['projects']
    order = data.get('order', sorted(projects.keys()))
    current = os.environ.get('OI_PROJECT', '')

    if not arguments:
        # Show current project
        if current:
            proj = projects.get(current, {})
            print(f"\n  Current project: \033[1;37m{current}\033[0m ({proj.get('name', '?')})")
            print(f"  Host: {proj.get('host', '?')}  Path: {proj.get('path', '?')}\n")
        else:
            print("\n  No project active. Use %switch <project>\n")
        print(f"  Available: {', '.join(order)}")
        print()
        return

    # Fuzzy match
    query = arguments.strip().lower()
    match = None
    for key in order:
        if key == query:
            match = key
            break
    if not match:
        for key in order:
            if key.startswith(query):
                match = key
                break
    if not match:
        print(f"\n  Unknown project '{arguments}'. Available: {', '.join(order)}\n")
        return

    proj = projects[match]
    host_key = proj.get('host', '')

    # Load HOSTS to get alias
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("hub_common", os.path.expanduser("~/hub_common.py"))
        hc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hc)
        host_alias = hc.HOSTS.get(host_key, {}).get('alias', host_key)
    except Exception:
        host_alias = host_key

    # Update env vars
    os.environ['OI_PROJECT'] = match
    os.environ['OI_PROJECT_HOST'] = host_alias
    os.environ['OI_PROJECT_PATH'] = proj.get('path', '')

    # Strip old PROJECT MODE block from system message and append new one
    sys_msg = self.system_message
    # Remove everything from "PROJECT MODE:" to end of that block
    import re as _re
    sys_msg = _re.sub(r'\nPROJECT MODE:.*', '', sys_msg, flags=_re.DOTALL)

    # Read overview cache for context
    cache_line = ""
    cache_file = os.path.expanduser(f'~/.cache/overview/{match}.json')
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                cache = json.load(f)
            phase = cache.get('phase', '')
            focus = cache.get('current_focus', [])
            if phase:
                cache_line = f"\nPhase: {phase}"
            if focus:
                cache_line += f"\nFocus: {'; '.join(focus[:3])}"
        except Exception:
            pass

    project_path = proj.get('path', '')
    sys_msg += f"""
PROJECT MODE: {match} on {host_alias}:{project_path}
Files are REMOTE. ~/edit {host_alias}:{project_path}/FILE --show to view.
To edit: ~/edit {host_alias}:{project_path}/FILE <range> then content lines.
{cache_line}"""

    self.system_message = sys_msg

    print(f"\n  \033[36m◆\033[0m Switched to \033[1;37m{match}\033[0m ({proj.get('name', '?')}) on {host_alias}")
    if cache_line:
        print(f"  {cache_line.strip()}")
    print()


def handle_overview(self, arguments):
    cmd = [sys.executable, os.path.expanduser('~/overview')]
    if arguments:
        cmd.append(arguments)
    elif os.environ.get('OI_PROJECT'):
        cmd.append(os.environ['OI_PROJECT'])
    _run_hub_tool(cmd, timeout=30)


def handle_notify(self, arguments):
    cmd = [sys.executable, os.path.expanduser('~/notify')]
    if arguments:
        cmd.extend(arguments.split())
    _run_hub_tool(cmd, timeout=10)


def handle_magic_command(self, user_input):
    # Handle shell
    if user_input.startswith("%%"):
        code = user_input[2:].strip()
        self.computer.run("shell", code, stream=False, display=True)
        print("")
        return

    # split the command into the command and the arguments, by the first whitespace
    switch = {
        "help": handle_help,
        "verbose": handle_verbose,
        "debug": handle_debug,
        "auto_run": handle_auto_run,
        "reset": handle_reset,
        "save_message": handle_save_message,
        "load_message": handle_load_message,
        "undo": handle_undo,
        "tokens": handle_count_tokens,
        "info": handle_info,
        "jupyter": jupyter,
        "markdown": markdown,
        "context": handle_context,
        "model": handle_model,
        "view": handle_view,
        "allow": handle_allow,
        "deny": handle_deny,
        "permissions": handle_permissions,
        "auto-edit": handle_auto_edit,
        "confirm-edit": handle_confirm_edit,
        "status": handle_status,
        "next": handle_next,
        "projects": handle_projects,
        "repo": handle_hub_repo,
        "checkpoint": handle_checkpoint,
        "backup": handle_hub_backup,
        "wake": handle_wake,
        "research": handle_hub_research,
        "health": handle_hub_health,
        "services": handle_services,
        "switch": handle_switch,
        "overview": handle_overview,
        "notify": handle_notify,
    }

    user_input = user_input[1:].strip()  # Capture the part after the `%`
    command = user_input.split(" ")[0]
    arguments = user_input[len(command) :].strip()

    if command == "debug":
        print(
            "\n`%debug` / `--debug_mode` has been renamed to `%verbose` / `--verbose`.\n"
        )
        time.sleep(1.5)
        command = "verbose"

    action = switch.get(
        command, default_handle
    )  # Get the function from the dictionary, or default_handle if not found
    action(self, arguments)  # Execute the function
