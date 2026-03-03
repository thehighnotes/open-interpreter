"""hub_common — Shared infrastructure for all Orin Nano hub tools.

Contains ANSI colors, project/host registries, SSH helpers, terminal
formatting, Spinner, parallel runner, and project resolution.
"""

import atexit
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import uuid
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# ANSI Colors
# ─────────────────────────────────────────────────────────────────────────────
GREEN = '\033[0;32m'
BRIGHT_GREEN = '\033[1;32m'
DIM = '\033[2m'
YELLOW = '\033[1;33m'
RED = '\033[1;31m'
CYAN = '\033[1;36m'
WHITE = '\033[1;37m'
GRAY = '\033[0;90m'
RESET = '\033[0m'
BOLD = '\033[1m'

# ─────────────────────────────────────────────────────────────────────────────
# Hub Config — loaded from ~/.config/hub/config.json
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_JSON = Path.home() / '.config' / 'hub' / 'config.json'

# Defaults — minimal single-host setup. Override via ~/.config/hub/config.json
_DEFAULT_CONFIG = {
    'hub': {
        'name': 'Dev Hub',
        'local_host': 'local',
    },
    'hosts': {
        'local': {'name': 'Hub', 'ip': '127.0.0.1', 'user': os.environ.get('USER', 'user'), 'roles': ['local', 'ollama']},
    },
    'ollama': {
        'host': 'local',
        'port': 11434,
        'default_model': 'llama3.2:3b',
    },
    'code_assistant': {'host': 'local', 'port': 5002},
    'backup': {'destination': '~/hub-backup'},
    'git': {'github_username': '', 'email': ''},
    'research': {
        'threshold': 7,
        'arxiv_categories': ['eess.AS', 'cs.SD', 'cs.CL', 'cs.LG', 'cs.AI'],
        'arxiv_keywords': [
            'TTS', 'speech', 'synthesis', 'voice', 'vocoder',
            'transformer', 'memory', 'attention', 'inference', 'embedding',
            'knowledge graph', 'continual learning', 'curriculum',
            'interactive', 'tutorial',
        ],
        'arxiv_max_results': 30,
        'github_repos': [
            'NVIDIA/TensorRT', 'ollama/ollama', 'pytorch/pytorch', 'ggml-org/llama.cpp',
        ],
    },
}


def load_config():
    """Load hub config from ~/.config/hub/config.json, merged with defaults.

    Returns complete config dict. If config.json doesn't exist, returns defaults
    (backward-compatible with existing installations).
    """
    config = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy defaults

    if CONFIG_JSON.exists():
        try:
            with open(CONFIG_JSON) as f:
                user_config = json.load(f)
            # Deep merge: user overrides defaults
            # 'hosts' is replaced entirely (not merged) to avoid phantom default entries
            for section, values in user_config.items():
                if section == 'hosts':
                    config[section] = values
                elif isinstance(values, dict) and isinstance(config.get(section), dict):
                    config[section].update(values)
                else:
                    config[section] = values
        except (json.JSONDecodeError, OSError):
            pass  # Fall back to defaults on parse error

    return config


def save_config(config):
    """Write full config dict to ~/.config/hub/config.json."""
    CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_JSON, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')


def reload_config():
    """Re-read config from disk and update all module-level globals in-place."""
    global LOCAL_HOST, OLLAMA_HOST, OLLAMA_PORT, DEFAULT_MODEL
    global GITHUB_USERNAME, GIT_EMAIL, BACKUP_DEST
    global CODE_ASSISTANT_URL

    fresh = load_config()
    HUB_CONFIG.clear()
    HUB_CONFIG.update(fresh)

    LOCAL_HOST = HUB_CONFIG['hub']['local_host']
    OLLAMA_HOST = HUB_CONFIG['ollama']['host']
    OLLAMA_PORT = HUB_CONFIG['ollama']['port']
    DEFAULT_MODEL = HUB_CONFIG['ollama']['default_model']
    GITHUB_USERNAME = HUB_CONFIG['git']['github_username']
    GIT_EMAIL = HUB_CONFIG['git']['email']
    BACKUP_DEST = HUB_CONFIG['backup']['destination']

    HOSTS.clear()
    for _key, _host in HUB_CONFIG['hosts'].items():
        HOSTS[_key] = {
            'alias': _key,
            'name': _host.get('name', _key),
            'ip': _host.get('ip', '127.0.0.1'),
            'user': _host.get('user', 'user'),
            'roles': _host.get('roles', []),
            'wol_mac': _host.get('wol_mac', ''),
        }

    _ca_hk = HUB_CONFIG['code_assistant']['host']
    _ca_i = HOSTS.get(_ca_hk, {}).get('ip', '127.0.0.1')
    _ca_p = HUB_CONFIG['code_assistant']['port']
    CODE_ASSISTANT_URL = f'http://{_ca_i}:{_ca_p}/api/v1/assistant'


HUB_CONFIG = load_config()

# Config-derived convenience globals
LOCAL_HOST = HUB_CONFIG['hub']['local_host']
OLLAMA_HOST = HUB_CONFIG['ollama']['host']
OLLAMA_PORT = HUB_CONFIG['ollama']['port']
DEFAULT_MODEL = HUB_CONFIG['ollama']['default_model']
GITHUB_USERNAME = HUB_CONFIG['git']['github_username']
GIT_EMAIL = HUB_CONFIG['git']['email']
BACKUP_DEST = HUB_CONFIG['backup']['destination']

# ─────────────────────────────────────────────────────────────────────────────
# Host Registry — derived from config
# ─────────────────────────────────────────────────────────────────────────────
HOSTS = {}
for _key, _host in HUB_CONFIG['hosts'].items():
    HOSTS[_key] = {
        'alias': _key,
        'name': _host.get('name', _key),
        'ip': _host.get('ip', '127.0.0.1'),
        'user': _host.get('user', 'user'),
        'roles': _host.get('roles', []),
        'wol_mac': _host.get('wol_mac', ''),
    }


def hosts_with_role(role):
    """Return list of host keys that have a given role."""
    return [k for k, h in HOSTS.items() if role in h.get('roles', [])]


def find_wakeable_host(host_key):
    """Check if a host has 'wakeable' role and a WoL MAC. Returns MAC or None."""
    host = HOSTS.get(host_key, {})
    if 'wakeable' in host.get('roles', []) and host.get('wol_mac'):
        return host['wol_mac']
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Project Registry — loaded from ~/.config/hub/projects.json
# ─────────────────────────────────────────────────────────────────────────────
PROJECTS_JSON = Path.home() / '.config' / 'hub' / 'projects.json'



def derive_claude_dir(host_key, path):
    """Derive Claude Code project dir name from host + path."""
    user = HOSTS[host_key]['user']
    expanded = path.replace('~', f'/home/{user}')
    return '-' + expanded.lstrip('/').replace('/', '-')


def load_projects():
    """Load projects from JSON, derive computed fields.

    On first run, migrates seed data to projects.json.
    Returns (projects_dict, order_list, ignored_dict).
    """
    if not PROJECTS_JSON.exists():
        save_projects({}, [])

    with open(PROJECTS_JSON) as f:
        data = json.load(f)

    projects = {}
    for key, proj in data['projects'].items():
        proj.setdefault('services', [])
        proj.setdefault('dev_services', [])
        proj.setdefault('tagline', '')
        proj.setdefault('claude_md', 'CLAUDE.md')
        proj['claude_dir'] = derive_claude_dir(proj['host'], proj['path'])
        projects[key] = proj
    ignored = data.get('ignored', {})
    return projects, data.get('order', sorted(projects.keys())), ignored


def save_projects(projects, order, ignored=None):
    """Write projects back to JSON (strips computed fields)."""
    stored_fields = ('name', 'tagline', 'host', 'path', 'claude_md', 'services', 'dev_services', 'code_index', 'git_remote', 'git_branch')
    data = {'projects': {}, 'order': list(order)}
    for key, proj in projects.items():
        data['projects'][key] = {k: v for k, v in proj.items() if k in stored_fields}
    if ignored:
        data['ignored'] = ignored
    PROJECTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(PROJECTS_JSON, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


PROJECTS, PROJECT_ORDER, IGNORED = load_projects()


def reload_projects():
    """Refresh all project-related globals from disk after save_projects()."""
    global PROJECTS, PROJECT_ORDER, IGNORED, CODE_ASSISTANT_PROJECTS
    PROJECTS, PROJECT_ORDER, IGNORED = load_projects()
    CODE_ASSISTANT_PROJECTS.clear()
    CODE_ASSISTANT_PROJECTS.update({k: p['code_index'] for k, p in PROJECTS.items() if p.get('code_index')})


def load_services(project_key):
    """Load service list for a project from the registry (projects.json).

    Returns list of service dicts or empty list.
    Each dict has at least: name, port. May also have: start_cmd, dir, ready_url.
    """
    proj = PROJECTS.get(project_key)
    if not proj:
        return []
    return proj.get('services', [])


def load_dev_services(project_key, enabled_only=False):
    """Load dev service list for a project from the registry (projects.json).

    Returns list of dev service dicts or empty list.
    Each dict has: name, port, dev_cmd, dir, enabled.
    If enabled_only=True, filters to only enabled services.
    """
    proj = PROJECTS.get(project_key)
    if not proj:
        return []
    devs = proj.get('dev_services', [])
    if enabled_only:
        return [d for d in devs if d.get('enabled')]
    return devs


# ─────────────────────────────────────────────────────────────────────────────
# Cache paths
# ─────────────────────────────────────────────────────────────────────────────
OVERVIEW_CACHE_DIR = Path.home() / '.cache' / 'overview'
RESEARCH_CACHE_DIR = Path.home() / '.cache' / 'research'
NOTES_CACHE_DIR = Path.home() / '.cache' / 'notes'
TIMELINE_CACHE_DIR = Path.home() / '.cache' / 'timeline'
CODE_CACHE_DIR = Path.home() / '.cache' / 'code'
OVERVIEW_HISTORY_DIR = Path.home() / '.cache' / 'overview' / 'history'
DECISIONS_CACHE_DIR = Path.home() / '.cache' / 'decisions'
DECISION_CATEGORIES = ('architecture', 'tooling', 'design', 'performance', 'abandoned')
DEPLOY_CACHE_DIR = Path.home() / '.cache' / 'deploy'
HEALTH_CACHE_DIR = Path.home() / '.cache' / 'health'
OI_SESSION_DIR = Path.home() / '.cache' / 'oi-sessions'
NOTIFY_LOG = Path.home() / '.cache' / 'hub' / 'notifications.jsonl'

# Hub project key → Code Assistant project key (built from code_index field in projects.json)
CODE_ASSISTANT_PROJECTS = {k: p['code_index'] for k, p in PROJECTS.items() if p.get('code_index')}
_ca_host_key = HUB_CONFIG['code_assistant']['host']
_ca_ip = HOSTS.get(_ca_host_key, {}).get('ip', '127.0.0.1')
_ca_port = HUB_CONFIG['code_assistant']['port']
CODE_ASSISTANT_URL = f'http://{_ca_ip}:{_ca_port}/api/v1/assistant'

# ─────────────────────────────────────────────────────────────────────────────
# Overview cache schema documentation
# ─────────────────────────────────────────────────────────────────────────────
# Cache JSON keys, their types, and consumers:
#
# Key                    Type           Consumers
# ─────────────────────  ─────────────  ─────────────────────────
# phase                  str            overview, begin, hub, research
# status_summary         str            overview, begin, hub
# current_focus          list[str]      overview, begin, hub, research
# blockers               list[str]      overview, begin, research
# key_metrics            dict           overview
# recent_breakthroughs   list[str]      overview, begin
# architecture_summary   str            overview
# tech_stack             list[str]      overview, research
# connections            list[str]      overview
# entry_points           dict           overview
#
# Cache dependency graph:
#   overview cache (~/.cache/overview/{project}.json)
#     → consumed by: begin (preamble), hub (status), research (scoring context)
#   research cache (~/.cache/research/{project}.json)
#     → consumed by: overview (deep view + LLM prompt), begin (preamble)
#   notes cache (~/.cache/notes/{project}.txt)
#     → consumed by: begin (preamble)
#   timeline journal (~/.cache/timeline/{project}.jsonl)
#     → written by: prepare (session_end + state diffs), begin (session_start),
#                   note (note events), autosummary (supplementary session_end)
#     → consumed by: timeline (events), overview (blocker ages), begin (trajectory)
#   code cache (~/.cache/code/{project}_pulse.json, {project}_last.json)
#     → written by: prepare (pulse), code tool (search/ask/impact results)
#     → consumed by: begin (preamble), code tool (cached results)

OVERVIEW_REQUIRED_KEYS = {
    'phase': str,
    'status_summary': str,
    'current_focus': list,
    'blockers': list,
}

# ─────────────────────────────────────────────────────────────────────────────
# SSH
# ─────────────────────────────────────────────────────────────────────────────

def ssh_cmd(host, cmd, timeout=10):
    """Run a command on a remote host via SSH (or locally for 'nano'). Returns (success, output)."""
    try:
        if host == LOCAL_HOST:
            result = subprocess.run(
                ['bash', '-c', cmd],
                capture_output=True, text=True, timeout=timeout
            )
        else:
            result = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes',
                 '-o', 'StrictHostKeyChecking=no', host, cmd],
                capture_output=True, text=True, timeout=timeout
            )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, ''
    except Exception as e:
        return False, str(e)


def check_host_reachable(host_alias):
    """Quick reachability check. Returns True/False."""
    ok, _ = ssh_cmd(host_alias, 'echo ok', timeout=5)
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Threading
# ─────────────────────────────────────────────────────────────────────────────

def run_parallel(tasks):
    """Run a dict of {name: callable} in parallel threads. Returns {name: result}."""
    results = {}
    def _run(name, fn):
        results[name] = fn()
    threads = []
    for name, fn in tasks.items():
        t = threading.Thread(target=_run, args=(name, fn), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=30)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Terminal helpers
# ─────────────────────────────────────────────────────────────────────────────

def cols():
    return shutil.get_terminal_size().columns

def divider():
    print(f"{DIM}{'─' * cols()}{RESET}")

def thin_divider():
    print(f"{GRAY}{'·' * cols()}{RESET}")

def header_bar():
    print(f"{BRIGHT_GREEN}{'═' * cols()}{RESET}")

ANSI_RE = re.compile(r'\033\[[0-9;]*m')

def strip_ansi(text):
    """Remove ANSI escape codes from text for accurate length measurement."""
    return ANSI_RE.sub('', text)

def print_right(left, right):
    """Print left-aligned text with right-aligned text on the same line."""
    padding = cols() - len(strip_ansi(left)) - len(strip_ansi(right))
    if padding < 2:
        padding = 2
    print(f"{left}{' ' * padding}{right}")


# ─────────────────────────────────────────────────────────────────────────────
# Spinner
# ─────────────────────────────────────────────────────────────────────────────

class Spinner:
    """Simple terminal spinner for long-running operations."""
    FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self, message):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stderr.write(f"\r{CYAN}{frame}{RESET} {self.message}")
            sys.stderr.flush()
            i += 1
            self._stop.wait(0.1)
        sys.stderr.write(f"\r{' ' * (len(self.message) + 4)}\r")
        sys.stderr.flush()

    def __enter__(self):
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(timeout=2)


# ─────────────────────────────────────────────────────────────────────────────
# Interactive TUI primitives
# ─────────────────────────────────────────────────────────────────────────────

# Key constants
KEY_UP = 'UP'
KEY_DOWN = 'DOWN'
KEY_ENTER = 'ENTER'
KEY_SPACE = 'SPACE'
KEY_QUIT = 'q'
KEY_ESCAPE = 'ESC'

HIGHLIGHT_BG = '\033[7m'    # Inverse video
HIGHLIGHT_OFF = '\033[27m'

# Terminal state for atexit safety net
_saved_termios = None
_saved_fd = None


def _read_key(fd):
    """Read a single keypress from raw fd. Handles arrow escape sequences."""
    ch = os.read(fd, 1)
    if not ch:
        return None
    c = ch[0]
    if c == 27:  # ESC — could be arrow key or standalone ESC
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            seq = os.read(fd, 2)
            if len(seq) == 2 and seq[0] == ord('['):
                if seq[1] == ord('A'):
                    return KEY_UP
                elif seq[1] == ord('B'):
                    return KEY_DOWN
                elif seq[1] == ord('C'):
                    return None  # right arrow — ignore
                elif seq[1] == ord('D'):
                    return None  # left arrow — ignore
        return KEY_ESCAPE
    elif c == 10 or c == 13:
        return KEY_ENTER
    elif c == 32:
        return KEY_SPACE
    elif c == 3:  # Ctrl-C
        raise KeyboardInterrupt
    else:
        ch_str = chr(c)
        # Vim keys
        if ch_str == 'k':
            return KEY_UP
        elif ch_str == 'j':
            return KEY_DOWN
        return ch_str


class _raw_terminal:
    """Context manager: sets terminal to raw mode, hides cursor, restores on exit."""

    def __init__(self, fd=None):
        self.fd = fd if fd is not None else sys.stdin.fileno()
        self.old_settings = None
        self._width = shutil.get_terminal_size().columns
        self._prev_sigwinch = None

    def __enter__(self):
        global _saved_termios, _saved_fd
        self.old_settings = termios.tcgetattr(self.fd)
        _saved_termios = self.old_settings
        _saved_fd = self.fd
        tty.setraw(self.fd)
        sys.stdout.write('\033[?25l')  # Hide cursor
        sys.stdout.flush()
        # SIGWINCH handler for terminal resize
        try:
            self._prev_sigwinch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, self._on_resize)
        except (ValueError, OSError):
            pass  # Not main thread or signal unavailable
        return self

    def __exit__(self, *args):
        global _saved_termios, _saved_fd
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
        sys.stdout.write('\033[?25h')  # Show cursor
        sys.stdout.flush()
        _saved_termios = None
        _saved_fd = None
        try:
            if self._prev_sigwinch is not None:
                signal.signal(signal.SIGWINCH, self._prev_sigwinch)
        except (ValueError, OSError):
            pass

    def _on_resize(self, signum, frame):
        self._width = shutil.get_terminal_size().columns

    @property
    def width(self):
        return self._width


def _atexit_restore():
    """Safety net: restore terminal if process exits while in raw mode."""
    if _saved_termios is not None and _saved_fd is not None:
        try:
            termios.tcsetattr(_saved_fd, termios.TCSADRAIN, _saved_termios)
            sys.stdout.write('\033[?25h')
            sys.stdout.flush()
        except Exception:
            pass

atexit.register(_atexit_restore)


def interactive_select(items, render_fn, title='', actions=None, footer=''):
    """Arrow-key driven selection menu.

    Args:
        items: list of items to display
        render_fn: callable(item, index, is_highlighted) → str (one line, no newline)
        title: header text (printed once above menu)
        actions: dict of {char: (name, description)} for custom action keys
        footer: text shown below the menu

    Returns:
        (action_char, index) when user presses an action key or Enter
        (None, None) on quit (q/ESC)

    Falls back to numbered input() when not a TTY.
    """
    if not items:
        return (None, None)

    if not sys.stdin.isatty():
        return _select_fallback(items, render_fn, title, actions, footer)

    actions = actions or {}
    cursor = 0
    num_lines = 0  # Track lines drawn for redraw

    try:
        with _raw_terminal() as term:
            while True:
                # Build frame
                lines = []
                if title:
                    lines.append(title)
                    lines.append('')
                for i, item in enumerate(items):
                    highlighted = (i == cursor)
                    lines.append(render_fn(item, i, highlighted))
                if footer:
                    lines.append('')
                    lines.append(footer)

                # Erase previous frame and redraw
                if num_lines > 0:
                    sys.stdout.write(f'\033[{num_lines}A')  # Move up
                for line in lines:
                    sys.stdout.write(f'\033[2K{line}\r\n')  # Clear line + write
                sys.stdout.flush()
                num_lines = len(lines)

                # Read key
                key = _read_key(term.fd)
                if key is None:
                    continue
                elif key == KEY_UP:
                    cursor = (cursor - 1) % len(items)
                elif key == KEY_DOWN:
                    cursor = (cursor + 1) % len(items)
                elif key == KEY_ENTER:
                    return (KEY_ENTER, cursor)
                elif key in (KEY_QUIT, KEY_ESCAPE):
                    return (None, None)
                elif key in actions:
                    return (key, cursor)
    except KeyboardInterrupt:
        return (None, None)
    finally:
        # Ensure newline after menu
        sys.stdout.write('\r\n')
        sys.stdout.flush()


def _select_fallback(items, render_fn, title, actions, footer):
    """Non-TTY fallback: numbered input for interactive_select."""
    if title:
        print(title)
    for i, item in enumerate(items):
        print(f"  {i+1}. {render_fn(item, i, False)}")
    if footer:
        print(footer)

    action_help = ', '.join(f'{k}={v[0]}' for k, v in (actions or {}).items())
    prompt_parts = [f'1-{len(items)}']
    if action_help:
        prompt_parts.append(action_help)
    prompt_parts.append('q=quit')

    try:
        raw = input(f"  [{', '.join(prompt_parts)}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return (None, None)

    if raw == 'q':
        return (None, None)
    if raw in (actions or {}):
        # Need an index too — ask for it
        try:
            idx = int(input(f"  Which # (1-{len(items)}): ").strip()) - 1
        except (ValueError, EOFError, KeyboardInterrupt):
            print()
            return (None, None)
        if 0 <= idx < len(items):
            return (raw, idx)
        return (None, None)
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            return (KEY_ENTER, idx)
    except ValueError:
        pass
    return (None, None)


def interactive_toggle(items, render_fn, get_state, set_state, title='',
                       footer='', on_save=None):
    """Arrow-key + space-to-toggle menu.

    Args:
        items: list of items to display
        render_fn: callable(item, index, is_highlighted) → str
        get_state: callable(item) → bool (current toggle state) or None (non-toggleable)
        set_state: callable(item, bool) → None (set toggle state)
        title: header text
        footer: text shown below menu
        on_save: callable() invoked when user presses Enter to save

    Returns:
        True if saved (Enter), False if discarded (q/ESC).

    Falls back to space-separated numbers when not a TTY.
    """
    if not items:
        return False

    if not sys.stdin.isatty():
        return _toggle_fallback(items, render_fn, get_state, set_state, title, footer, on_save)

    cursor = 0
    # Skip to first toggleable item
    while cursor < len(items) and get_state(items[cursor]) is None:
        cursor += 1
    if cursor >= len(items):
        cursor = 0  # No toggleable items, just allow viewing
    num_lines = 0

    try:
        with _raw_terminal() as term:
            while True:
                lines = []
                if title:
                    lines.append(title)
                    lines.append('')
                for i, item in enumerate(items):
                    highlighted = (i == cursor)
                    lines.append(render_fn(item, i, highlighted))
                if footer:
                    lines.append('')
                    lines.append(footer)

                if num_lines > 0:
                    sys.stdout.write(f'\033[{num_lines}A')
                for line in lines:
                    sys.stdout.write(f'\033[2K{line}\r\n')
                sys.stdout.flush()
                num_lines = len(lines)

                key = _read_key(term.fd)
                if key is None:
                    continue
                elif key == KEY_UP:
                    # Skip non-toggleable items
                    start = cursor
                    cursor = (cursor - 1) % len(items)
                    while get_state(items[cursor]) is None and cursor != start:
                        cursor = (cursor - 1) % len(items)
                elif key == KEY_DOWN:
                    start = cursor
                    cursor = (cursor + 1) % len(items)
                    while get_state(items[cursor]) is None and cursor != start:
                        cursor = (cursor + 1) % len(items)
                elif key == KEY_SPACE:
                    state = get_state(items[cursor])
                    if state is not None:
                        set_state(items[cursor], not state)
                elif key == KEY_ENTER:
                    if on_save:
                        on_save()
                    return True
                elif key in (KEY_QUIT, KEY_ESCAPE):
                    return False
    except KeyboardInterrupt:
        return False
    finally:
        sys.stdout.write('\r\n')
        sys.stdout.flush()


def _toggle_fallback(items, render_fn, get_state, set_state, title, footer, on_save):
    """Non-TTY fallback: space-separated numbers for interactive_toggle."""
    if title:
        print(title)
    toggleable = []
    for i, item in enumerate(items):
        state = get_state(item)
        line = render_fn(item, i, False)
        if state is not None:
            toggleable.append((i, item))
            print(f"  {len(toggleable)}. {line}")
        else:
            print(f"     {line}")
    if footer:
        print(footer)

    try:
        raw = input(f"  Toggle which? (space-sep #s, Enter=save, q=discard): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if raw.lower() == 'q':
        return False

    if raw:
        for part in raw.split():
            try:
                idx = int(part) - 1
                if 0 <= idx < len(toggleable):
                    _, item = toggleable[idx]
                    state = get_state(item)
                    if state is not None:
                        set_state(item, not state)
            except ValueError:
                pass

    if on_save:
        on_save()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Project resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_project(query):
    """Fuzzy match a project key. Returns key or None.

    Matches against: exact key, substring of key, substring of name,
    and path basenames (e.g. 'jetson' → 'pipeline', 'localprogrammingtuts' → 'learnlocal').
    Offers "did you mean?" for close misses via edit distance.
    """
    q = query.lower()
    if q in PROJECTS:
        return q

    # Match against key, name, and path basename
    matches = []
    for k, p in PROJECTS.items():
        basename = p['path'].rsplit('/', 1)[-1].lower()
        if q in k or q in p['name'].lower() or q in basename:
            matches.append(k)

    if len(matches) == 1:
        return matches[0]
    if matches:
        print(f"{YELLOW}Ambiguous name '{query}'. Did you mean:{RESET}")
        for m in matches:
            print(f"  {WHITE}{m}{RESET}  ({PROJECTS[m]['name']})")
        return None

    # "Did you mean?" via simple edit distance
    def _edit_dist(a, b):
        if len(a) > len(b):
            a, b = b, a
        dists = range(len(a) + 1)
        for j, cb in enumerate(b):
            new_dists = [j + 1]
            for i, ca in enumerate(a):
                cost = 0 if ca == cb else 1
                new_dists.append(min(new_dists[i] + 1, dists[i + 1] + 1, dists[i] + cost))
            dists = new_dists
        return dists[-1]

    closest = None
    closest_dist = 999
    for k in PROJECTS:
        d = _edit_dist(q, k)
        if d < closest_dist:
            closest_dist = d
            closest = k

    if closest and closest_dist <= 3:
        print(f"{RED}Unknown project '{query}'.{RESET} Did you mean {WHITE}{closest}{RESET}?")
    else:
        print(f"{RED}Unknown project '{query}'.{RESET}")
    print(f"{GRAY}Available: {', '.join(PROJECT_ORDER)}{RESET}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def ensure_dir(path):
    """Ensure a directory exists."""
    Path(path).mkdir(parents=True, exist_ok=True)


def hub_notify(title, body='', icon='dialog-information', source='hub'):
    """Fire-and-forget desktop notification via notify-send (DISPLAY=:0).

    Also appends a JSONL entry to NOTIFY_LOG for persistent history.
    """
    try:
        subprocess.Popen(
            ['notify-send', '-i', icon, title, body],
            env={**__import__('os').environ, 'DISPLAY': ':0'},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    # Persist to JSONL log
    try:
        NOTIFY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            'ts': time.time(),
            'title': title,
            'body': body,
            'source': source,
            'read': False,
        })
        with open(NOTIFY_LOG, 'a') as f:
            f.write(entry + '\n')
    except Exception:
        pass


def host_offline_msg(host_key, project_key=None):
    """Return a consistent offline message for a host."""
    host_name = HOSTS[host_key]['name']
    if host_key == 'ws':
        hint = f"run {WHITE}prepare {project_key}{YELLOW}" if project_key else f"run {WHITE}wake-ws{YELLOW}"
        return f"{YELLOW}● {host_name} SUSPENDED{RESET} — {hint} to wake"
    return f"{RED}● {host_name} OFFLINE{RESET} — must be powered on manually"


# ─────────────────────────────────────────────────────────────────────────────
# Service probing
# ─────────────────────────────────────────────────────────────────────────────

def probe_service(host_alias, port, timeout=3):
    """Check if a service responds on a port. Returns True/False."""
    cmd = f'curl -s -o /dev/null -w "%{{http_code}}" --connect-timeout {timeout} http://localhost:{port}/ 2>/dev/null || echo "000"'
    ok, output = ssh_cmd(host_alias, cmd, timeout=timeout + 3)
    if ok and output:
        try:
            code = int(output.strip().split('\n')[-1])
            return code > 0 and code != 000
        except ValueError:
            pass
    return False


def check_service_health(host_alias, service):
    """Check service health using ready_url if present, else port probe.

    Returns True if healthy.
    """
    ready_url = service.get('ready_url')
    port = service.get('port')
    if ready_url and port:
        cmd = f'curl -sf --connect-timeout 3 http://localhost:{port}{ready_url} >/dev/null 2>&1'
        ok, _ = ssh_cmd(host_alias, cmd, timeout=6)
        return ok
    if port:
        return probe_service(host_alias, port, timeout=3)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# LLM JSON parsing and validation
# ─────────────────────────────────────────────────────────────────────────────

def parse_llm_json(text):
    """Try to parse JSON from LLM output, handling markdown fences and other issues."""
    text = text.strip()

    # Strip markdown code fences
    if text.startswith('```'):
        lines = text.split('\n')
        lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def validate_overview_analysis(data):
    """Check that LLM analysis has the required keys with correct types.

    Returns True if valid, False otherwise.
    """
    if not isinstance(data, dict):
        return False
    for key, expected_type in OVERVIEW_REQUIRED_KEYS.items():
        val = data.get(key)
        if val is None:
            return False
        if not isinstance(val, expected_type):
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Ollama query (unified — used by overview + research)
# ─────────────────────────────────────────────────────────────────────────────

def ollama_query(model, system_msg, user_msg, timeout=120, temperature=0.3, num_predict=2048):
    """Send a query to Ollama via SCP+SSH (or locally). Returns parsed JSON dict or None.

    Uses UUID-based temp paths to avoid collisions when running
    overview and research concurrently.
    """
    import os as _os

    payload = json.dumps({
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg},
        ],
        'stream': False,
        'think': False,
        'format': 'json',
        'options': {
            'temperature': temperature,
            'num_predict': num_predict,
        }
    })

    uid = uuid.uuid4().hex[:8]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(payload)
        tmp_local = f.name

    tmp_remote = f'/tmp/_hub_ollama_{uid}.json'
    try:
        if OLLAMA_HOST == LOCAL_HOST:
            # Ollama runs locally — no SCP/SSH needed
            cmd = f'curl -s --max-time {timeout - 30} http://localhost:{OLLAMA_PORT}/api/chat -d @{tmp_local}'
            try:
                result = subprocess.run(
                    ['bash', '-c', cmd],
                    capture_output=True, text=True, timeout=timeout
                )
                if result.returncode != 0 or not result.stdout.strip():
                    return None
                output = result.stdout.strip()
            except (subprocess.TimeoutExpired, Exception):
                return None
        else:
            # Ollama runs on a remote host — SCP payload, SSH curl
            scp = subprocess.run(
                ['scp', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes',
                 tmp_local, f'{OLLAMA_HOST}:{tmp_remote}'],
                capture_output=True, text=True, timeout=10
            )
            if scp.returncode != 0:
                return None

            cmd = f'curl -s --max-time {timeout - 30} http://localhost:{OLLAMA_PORT}/api/chat -d @{tmp_remote}'
            ok, output = ssh_cmd(OLLAMA_HOST, cmd, timeout=timeout)

            if not ok or not output:
                return None

        try:
            response = json.loads(output)
            text = response.get('message', {}).get('content', '')
        except json.JSONDecodeError:
            return None

        return parse_llm_json(text)
    finally:
        _os.unlink(tmp_local)
        if OLLAMA_HOST != LOCAL_HOST:
            ssh_cmd(OLLAMA_HOST, f'rm -f {tmp_remote}', timeout=5)


def ollama_query_text(model, system_msg, user_msg, timeout=60, temperature=0.3, num_predict=256):
    """Send a query to Ollama via SCP+SSH (or locally). Returns raw text string or None.

    Same transport as ollama_query() but omits format='json' for free-text output.
    """
    import os as _os

    payload = json.dumps({
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg},
        ],
        'stream': False,
        'think': False,
        'options': {
            'temperature': temperature,
            'num_predict': num_predict,
        }
    })

    uid = uuid.uuid4().hex[:8]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(payload)
        tmp_local = f.name

    tmp_remote = f'/tmp/_hub_ollama_{uid}.json'
    try:
        if OLLAMA_HOST == LOCAL_HOST:
            cmd = f'curl -s --max-time {timeout - 10} http://localhost:{OLLAMA_PORT}/api/chat -d @{tmp_local}'
            try:
                result = subprocess.run(
                    ['bash', '-c', cmd],
                    capture_output=True, text=True, timeout=timeout
                )
                if result.returncode != 0 or not result.stdout.strip():
                    return None
                output = result.stdout.strip()
            except (subprocess.TimeoutExpired, Exception):
                return None
        else:
            scp = subprocess.run(
                ['scp', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes',
                 tmp_local, f'{OLLAMA_HOST}:{tmp_remote}'],
                capture_output=True, text=True, timeout=10
            )
            if scp.returncode != 0:
                return None

            cmd = f'curl -s --max-time {timeout - 10} http://localhost:{OLLAMA_PORT}/api/chat -d @{tmp_remote}'
            ok, output = ssh_cmd(OLLAMA_HOST, cmd, timeout=timeout)

            if not ok or not output:
                return None

        try:
            response = json.loads(output)
            return response.get('message', {}).get('content', '').strip() or None
        except json.JSONDecodeError:
            return None
    finally:
        _os.unlink(tmp_local)
        if OLLAMA_HOST != LOCAL_HOST:
            ssh_cmd(OLLAMA_HOST, f'rm -f {tmp_remote}', timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# Overview cache refresh (used by prepare + autosummary)
# ─────────────────────────────────────────────────────────────────────────────

def refresh_overview_cache(project_key, log_fn=None):
    """Run overview --refresh for a project. Returns True on success."""
    import time as _time
    overview_path = str(Path.home() / 'overview')
    try:
        start = _time.time()
        result = subprocess.run(
            [overview_path, project_key, '--refresh'],
            capture_output=True, text=True, timeout=180
        )
        elapsed = int(_time.time() - start)
        if result.returncode == 0:
            if log_fn:
                log_fn(f"[{project_key}] cache refresh complete ({elapsed}s)")
            return True
        else:
            if log_fn:
                log_fn(f"[{project_key}] cache refresh failed ({elapsed}s)")
            return False
    except subprocess.TimeoutExpired:
        if log_fn:
            log_fn(f"[{project_key}] cache refresh timed out")
        return False
    except Exception as e:
        if log_fn:
            log_fn(f"[{project_key}] cache refresh error: {e}")
        return False


def append_overview_history(project_key, analysis_data):
    """Append timestamped overview snapshot to history JSONL."""
    import time as _time
    ensure_dir(OVERVIEW_HISTORY_DIR)
    history_file = OVERVIEW_HISTORY_DIR / f'{project_key}.jsonl'
    entry = dict(analysis_data)
    entry['ts'] = _time.time()
    try:
        with open(history_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Transcript mtime probing (shared by prepare, begin, autosummary)
# ─────────────────────────────────────────────────────────────────────────────

def get_transcript_mtime(host_alias, claude_dir):
    """Get the mtime of the newest transcript file. Returns float or None."""
    cmd = (
        f'find ~/.claude/projects/{claude_dir}/ -maxdepth 1 -name "*.jsonl" '
        f'-printf "%T@\\n" 2>/dev/null | sort -rn | head -1'
    )
    ok, output = ssh_cmd(host_alias, cmd, timeout=8)
    if ok and output.strip():
        try:
            return float(output.strip())
        except ValueError:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Git stats (shared by prepare, begin)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_git_stats(host_alias, project_path, since_ref=None):
    """Fetch git stats from a remote project. Returns dict or None.

    If since_ref is provided, stats are computed since that commit.
    Returns {commits, head, changed_files, insertions, deletions}.
    """
    if since_ref:
        cmd = (
            f'cd {project_path} 2>/dev/null && '
            f'echo "HEAD:$(git rev-parse --short HEAD 2>/dev/null)" && '
            f'echo "COMMITS:$(git rev-list --count {since_ref}..HEAD 2>/dev/null)" && '
            f'git diff --stat {since_ref}..HEAD 2>/dev/null | tail -1'
        )
    else:
        cmd = (
            f'cd {project_path} 2>/dev/null && '
            f'echo "HEAD:$(git rev-parse --short HEAD 2>/dev/null)" && '
            f'echo "COMMITS:0"'
        )

    ok, output = ssh_cmd(host_alias, cmd, timeout=10)
    if not ok:
        return None

    result = {'commits': 0, 'head': None, 'changed_files': [], 'insertions': 0, 'deletions': 0}
    for line in output.strip().split('\n'):
        if line.startswith('HEAD:'):
            result['head'] = line[5:].strip() or None
        elif line.startswith('COMMITS:'):
            try:
                result['commits'] = int(line[8:].strip())
            except ValueError:
                pass
        elif 'changed' in line or 'insertion' in line or 'deletion' in line:
            # Parse git diff --stat summary line: "N files changed, N insertions(+), N deletions(-)"
            import re as _re
            m = _re.search(r'(\d+) file', line)
            if m:
                # Get actual changed file list
                if since_ref:
                    ok2, files_out = ssh_cmd(host_alias,
                        f'cd {project_path} && git diff --name-only {since_ref}..HEAD 2>/dev/null',
                        timeout=10)
                    if ok2 and files_out.strip():
                        result['changed_files'] = files_out.strip().split('\n')
            m_ins = _re.search(r'(\d+) insertion', line)
            m_del = _re.search(r'(\d+) deletion', line)
            if m_ins:
                result['insertions'] = int(m_ins.group(1))
            if m_del:
                result['deletions'] = int(m_del.group(1))

    return result


def fetch_git_head(host_alias, project_path):
    """Get current git HEAD short hash. Returns string or None."""
    ok, output = ssh_cmd(host_alias, f'cd {project_path} 2>/dev/null && git rev-parse --short HEAD 2>/dev/null', timeout=8)
    if ok and output.strip():
        return output.strip()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Timeline journal — state transition tracking
# ─────────────────────────────────────────────────────────────────────────────

def normalize_blocker(text):
    """Normalize blocker text for fuzzy comparison: lowercase, strip punctuation."""
    import re as _re
    return set(_re.sub(r'[^\w\s]', '', text.lower()).split())


def blocker_similarity(a, b):
    """Compute Jaccard word overlap between two blocker strings. Returns float 0-1."""
    words_a = normalize_blocker(a)
    words_b = normalize_blocker(b)
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


BLOCKER_SIMILARITY_THRESHOLD = 0.6


def _fuzzy_match_blockers(old_blockers, new_blockers):
    """Classify blockers as added, removed, or persisted using fuzzy matching.

    Returns (added, removed, persisted) where persisted maps new text → old text.
    """
    old_list = list(old_blockers)
    new_list = list(new_blockers)
    persisted = {}  # new_text → old_text
    matched_old = set()
    matched_new = set()

    # First pass: exact matches
    for n in new_list:
        if n in old_blockers:
            persisted[n] = n
            matched_old.add(n)
            matched_new.add(n)

    # Second pass: fuzzy matches for unmatched
    for n in new_list:
        if n in matched_new:
            continue
        best_score = 0
        best_old = None
        for o in old_list:
            if o in matched_old:
                continue
            score = blocker_similarity(n, o)
            if score > best_score:
                best_score = score
                best_old = o
        if best_score >= BLOCKER_SIMILARITY_THRESHOLD and best_old:
            persisted[n] = best_old
            matched_old.add(best_old)
            matched_new.add(n)

    added = [n for n in new_list if n not in matched_new]
    removed = [o for o in old_list if o not in matched_old]
    return added, removed, persisted


def diff_overview_states(before, after):
    """Diff two overview cache dicts. Returns list of event dicts for the journal.

    Pure code — no LLM involved. Compares blockers, focus, phase, metrics.
    Uses fuzzy matching for blockers to handle LLM rephrasing.
    """
    import time as _time
    if not before or not after:
        return []

    ts = _time.time()
    events = []

    # Phase change
    old_phase = before.get('phase', '')
    new_phase = after.get('phase', '')
    if old_phase != new_phase and (old_phase or new_phase):
        events.append({
            'ts': ts, 'type': 'state_change', 'field': 'phase',
            'before': old_phase, 'after': new_phase,
        })

    # Blockers diff (fuzzy matching)
    old_blockers = set(before.get('blockers', []))
    new_blockers = set(after.get('blockers', []))
    added, removed, persisted_map = _fuzzy_match_blockers(old_blockers, new_blockers)
    persisted = list(persisted_map.keys())
    if added or removed:
        events.append({
            'ts': ts, 'type': 'state_change', 'field': 'blockers',
            'added': added, 'removed': removed, 'persisted': persisted,
        })

    # Focus diff
    old_focus = set(before.get('current_focus', []))
    new_focus = set(after.get('current_focus', []))
    if old_focus != new_focus:
        events.append({
            'ts': ts, 'type': 'state_change', 'field': 'focus',
            'before': list(old_focus), 'after': list(new_focus),
        })

    # Metrics diff
    old_metrics = before.get('key_metrics', {})
    new_metrics = after.get('key_metrics', {})
    for key in set(list(old_metrics.keys()) + list(new_metrics.keys())):
        old_val = old_metrics.get(key)
        new_val = new_metrics.get(key)
        if old_val != new_val and old_val is not None and new_val is not None:
            events.append({
                'ts': ts, 'type': 'metric_change', 'key': key,
                'before': str(old_val), 'after': str(new_val),
            })

    return events


def append_journal(project_key, events):
    """Append events to the project's timeline journal (JSONL)."""
    if not events:
        return
    ensure_dir(TIMELINE_CACHE_DIR)
    journal_file = TIMELINE_CACHE_DIR / f'{project_key}.jsonl'
    with open(journal_file, 'a') as f:
        for event in events:
            f.write(json.dumps(event) + '\n')


def read_journal(project_key, since_ts=None):
    """Read journal entries for a project. Optionally filter by timestamp.

    Returns list of event dicts, oldest first.
    """
    journal_file = TIMELINE_CACHE_DIR / f'{project_key}.jsonl'
    if not journal_file.exists():
        return []
    events = []
    try:
        for line in journal_file.read_text().strip().split('\n'):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if since_ts and event.get('ts', 0) < since_ts:
                    continue
                events.append(event)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return events


def get_blocker_ages(project_key):
    """Analyze journal to determine how long each current blocker has persisted.

    Uses fuzzy matching to track blocker identity across sessions even when
    the LLM rephrases them slightly.

    Returns dict mapping blocker text → {'sessions': N, 'first_seen_ts': float}.
    """
    journal = read_journal(project_key)
    if not journal:
        return {}

    # Track blocker appearances across state_change events
    # canonical_text → {first_seen_ts, sessions}
    blocker_info = {}
    current_blockers = set()  # set of canonical texts

    for event in journal:
        if event.get('type') != 'state_change' or event.get('field') != 'blockers':
            continue
        ts = event.get('ts', 0)
        added = event.get('added', [])
        removed = event.get('removed', [])
        persisted = event.get('persisted', [])

        for b in added:
            # Check if this fuzzy-matches an existing blocker
            matched = False
            for existing in list(current_blockers):
                if blocker_similarity(b, existing) >= BLOCKER_SIMILARITY_THRESHOLD:
                    # Same blocker, new phrasing — keep the canonical text, bump count
                    blocker_info[existing]['sessions'] += 1
                    matched = True
                    break
            if not matched:
                blocker_info[b] = {'first_seen_ts': ts, 'sessions': 1}
                current_blockers.add(b)

        for b in persisted:
            # Find the canonical version
            for existing in list(current_blockers):
                if b == existing or blocker_similarity(b, existing) >= BLOCKER_SIMILARITY_THRESHOLD:
                    blocker_info[existing]['sessions'] += 1
                    break

        for b in removed:
            # Find and remove the canonical version
            for existing in list(current_blockers):
                if b == existing or blocker_similarity(b, existing) >= BLOCKER_SIMILARITY_THRESHOLD:
                    current_blockers.discard(existing)
                    break

    # Return ages for current blockers only
    result = {}
    for b in current_blockers:
        if b in blocker_info:
            result[b] = blocker_info[b]
    return result


def get_last_open_session(project_key):
    """Find the most recent session_start without a matching session_end.

    Walks journal backwards. Returns event dict or None.
    """
    journal = read_journal(project_key)
    if not journal:
        return None

    # Walk backwards: if we hit session_start before session_end, it's open
    for event in reversed(journal):
        etype = event.get('type', '')
        if etype == 'session_end':
            return None  # Most recent boundary is an end — no open session
        if etype == 'session_start':
            return event  # Found an open session

    return None


def close_previous_session(project_key, log_fn=None):
    """Close the previous open session if one exists.

    Writes session_end event with duration and git stats.
    Returns True if a session was closed, False otherwise.
    """
    import time as _time

    open_session = get_last_open_session(project_key)
    if not open_session:
        return False

    proj = PROJECTS[project_key]
    host_alias = HOSTS[proj['host']]['alias']

    # Compute end timestamp from transcript mtime
    end_ts = get_transcript_mtime(host_alias, proj['claude_dir'])
    if not end_ts:
        end_ts = _time.time()

    # Compute duration
    start_ts = open_session.get('ts', end_ts)
    duration_minutes = max(0, int((end_ts - start_ts) / 60))

    # Get git stats since session start
    git_head_at_start = open_session.get('git_head')
    git_stats = None
    if git_head_at_start:
        git_stats = fetch_git_stats(host_alias, proj['path'], since_ref=git_head_at_start)

    event = {
        'ts': end_ts,
        'type': 'session_end',
        'project': project_key,
        'duration_minutes': duration_minutes,
    }
    if git_stats:
        event['git_stats'] = git_stats

    append_journal(project_key, [event])

    if log_fn:
        commits = git_stats.get('commits', 0) if git_stats else 0
        log_fn(f"[{project_key}] session closed ({duration_minutes}m, {commits} commits)")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Decision log
# ─────────────────────────────────────────────────────────────────────────────

def append_decision(project_key, description, category='design'):
    """Append to ~/.cache/decisions/{project}.jsonl. Returns event dict."""
    import time as _time
    ensure_dir(DECISIONS_CACHE_DIR)
    event = {
        'ts': _time.time(),
        'project': project_key,
        'description': description,
        'category': category,
    }
    decision_file = DECISIONS_CACHE_DIR / f'{project_key}.jsonl'
    try:
        with open(decision_file, 'a') as f:
            f.write(json.dumps(event) + '\n')
    except OSError:
        pass
    return event


def extract_decisions_from_transcript(project_key):
    """Extract design decisions from the latest transcript via Ollama.

    Reads the transcript, sends a focused Ollama query to find decisions,
    and appends any new ones to the decision log. Returns list of new decisions.
    """
    proj = PROJECTS[project_key]
    host_alias = HOSTS[proj['host']]['alias']
    claude_dir = proj.get('claude_dir')
    if not claude_dir:
        return []

    # Get transcript — reuse the overview probe pattern
    cmd = (
        f'find ~/.claude/projects/{claude_dir}/ -maxdepth 1 -name "*.jsonl" '
        f'-printf "%T@ %p\\n" 2>/dev/null | sort -rn | head -1'
    )
    ok, output = ssh_cmd(host_alias, cmd, timeout=5)
    if not ok or not output.strip():
        return []

    parts = output.strip().split(' ', 1)
    if len(parts) != 2:
        return []
    jsonl_path = parts[1]

    ok, raw = ssh_cmd(host_alias, f'cat {jsonl_path}', timeout=15)
    if not ok or not raw:
        return []

    # Condense transcript (user + assistant text only)
    lines = []
    for jline in raw.split('\n'):
        jline = jline.strip()
        if not jline:
            continue
        try:
            obj = json.loads(jline)
        except json.JSONDecodeError:
            continue
        typ = obj.get('type', '')
        if typ not in ('user', 'assistant'):
            continue
        msg = obj.get('message', {})
        content = msg.get('content', '')
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if block.get('type') == 'text' and block.get('text', '').strip():
                    texts.append(block['text'])
        for t in texts:
            t = t.strip()
            if not t or len(t) < 20:
                continue
            if '<command-name>' in t or '<task-notification>' in t:
                continue
            if len(t) > 400:
                t = t[:400]
            lines.append(t)

    if not lines:
        return []

    # Take last ~4000 chars of transcript (most recent context)
    transcript = '\n'.join(lines)
    if len(transcript) > 4000:
        transcript = transcript[-4000:]

    # Load existing decisions to avoid duplicates
    existing = read_decisions(project_key, limit=20)
    existing_texts = {d.get('description', '').lower() for d in existing}

    # Ollama query
    system_msg = """/no_think
You extract design decisions from development conversations.
Return JSON: {"decisions": [{"description": "...", "category": "..."}]}
Categories: architecture, tooling, design, performance, abandoned
Rules:
- Only extract EXPLICIT decisions — choices made between alternatives, or deliberate rejections
- "Chose X over Y because Z" = decision. "Fixed a bug" = NOT a decision.
- "Abandoned approach X" or "tried X, didn't work" = abandoned category
- Each description should be self-contained (understandable without the conversation)
- Max 3 decisions per session. Return {"decisions": []} if none found."""

    user_msg = f"Extract decisions from this {proj['name']} development session:\n\n{transcript}"

    result = ollama_query(
        model=DEFAULT_MODEL,
        system_msg=system_msg,
        user_msg=user_msg,
        timeout=45,
        temperature=0.2,
        num_predict=512,
    )

    if not result or not isinstance(result.get('decisions'), list):
        return []

    new_decisions = []
    for d in result['decisions'][:3]:
        desc = d.get('description', '').strip()
        cat = d.get('category', 'design')
        if not desc or len(desc) < 10:
            continue
        if cat not in DECISION_CATEGORIES:
            cat = 'design'
        # Skip if too similar to existing
        if desc.lower() in existing_texts:
            continue
        # Fuzzy duplicate check — skip if >60% word overlap with any existing
        desc_words = set(desc.lower().split())
        is_dup = False
        for ex_text in existing_texts:
            ex_words = set(ex_text.split())
            if desc_words and ex_words:
                overlap = len(desc_words & ex_words) / len(desc_words | ex_words)
                if overlap > 0.6:
                    is_dup = True
                    break
        if is_dup:
            continue

        event = append_decision(project_key, desc, cat)
        new_decisions.append(event)
        existing_texts.add(desc.lower())

    return new_decisions


def read_decisions(project_key, limit=None, category=None):
    """Read decisions, newest first. Optional category filter."""
    decision_file = DECISIONS_CACHE_DIR / f'{project_key}.jsonl'
    if not decision_file.exists():
        return []
    events = []
    try:
        for line in decision_file.read_text().strip().split('\n'):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if category and event.get('category') != category:
                    continue
                events.append(event)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    events.reverse()  # newest first
    if limit:
        events = events[:limit]
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Cross-project context
# ─────────────────────────────────────────────────────────────────────────────

def summarize_cross_project_sessions(current_project_key):
    """Today's work on other projects, LLM-summarized.

    Returns list of "  - PROJECT_NAME: summary" strings.
    """
    from datetime import datetime as _dt
    import time as _time

    now = _dt.now()
    today_midnight = _dt(now.year, now.month, now.day).timestamp()

    results = []
    for pk in PROJECT_ORDER:
        if pk == current_project_key:
            continue
        journal = read_journal(pk, since_ts=today_midnight)
        session_ends = [e for e in journal if e.get('type') == 'session_end']
        if not session_ends:
            continue

        proj = PROJECTS[pk]
        host_alias = HOSTS[proj['host']]['alias']

        # Get today's commit messages
        date_str = now.strftime('%Y-%m-%d')
        ok, log_output = ssh_cmd(
            host_alias,
            f'cd {proj["path"]} 2>/dev/null && git log --oneline --since="{date_str}" 2>/dev/null | head -5',
            timeout=10
        )
        commit_msgs = []
        if ok and log_output.strip():
            commit_msgs = [line.split(' ', 1)[-1] for line in log_output.strip().split('\n') if ' ' in line]

        if not commit_msgs:
            # Fallback: use git_stats from session_end events
            for se in session_ends:
                gs = se.get('git_stats', {})
                if gs.get('commits', 0) > 0:
                    commit_msgs.append(f"{gs['commits']} commit(s), +{gs.get('insertions', 0)}/-{gs.get('deletions', 0)} lines")

        if not commit_msgs:
            results.append(f"  - {proj['name']}: session recorded (no commits)")
            continue

        # Try Ollama summarization
        summary = None
        try:
            raw_text = '; '.join(commit_msgs[:5])
            llm_result = ollama_query(
                model=DEFAULT_MODEL,
                system_msg='/no_think\nReturn JSON: {"summary": "one sentence summary of the work done"}',
                user_msg=f'Summarize this work on {proj["name"]}: {raw_text}',
                timeout=30,
                temperature=0.3,
                num_predict=128,
            )
            if llm_result and llm_result.get('summary'):
                summary = llm_result['summary']
        except Exception:
            pass

        if not summary:
            summary = commit_msgs[0] if commit_msgs else 'session recorded'

        results.append(f"  - {proj['name']}: {summary}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Code Assistant — direct HTTP (config-derived host + port)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_code_project(hub_key):
    """Map hub project key to Code Assistant key. Returns key or None."""
    return CODE_ASSISTANT_PROJECTS.get(hub_key)


def code_assistant_get(endpoint, params=None, timeout=15):
    """GET request to Code Assistant. Returns parsed JSON or None."""
    import urllib.request, urllib.parse
    url = f'{CODE_ASSISTANT_URL}/{endpoint}'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def code_assistant_post(endpoint, data, timeout=120):
    """POST JSON to Code Assistant. Returns parsed JSON or None."""
    import urllib.request
    url = f'{CODE_ASSISTANT_URL}/{endpoint}'
    body = json.dumps(data).encode()
    try:
        req = urllib.request.Request(url, data=body,
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def check_code_assistant():
    """Check if Code Assistant is reachable. Returns True/False."""
    result = code_assistant_get('health', timeout=3)
    return result is not None and result.get('healthy', False)


def check_agx_ws_ssh():
    """Check if CA host can SSH to source hosts. Returns True/False."""
    ca_host = HUB_CONFIG['code_assistant']['host']
    ok, output = ssh_cmd(ca_host, 'ssh -o BatchMode=yes -o ConnectTimeout=3 ws echo ok', timeout=10)
    return ok and 'ok' in output


def rsync_to_ca_host(source_host, source_path, mirror_path, dry_run=False):
    """Rsync a project from source_host to CA host mirror directory.

    Runs rsync ON the CA host pulling FROM source_host.

    Args:
        source_host: SSH alias of source host (e.g. 'ws')
        source_path: Path on source host (e.g. ~/projects/ai-reference-hub)
        mirror_path: Target path on CA host (e.g. ~/projects/aiquest)
        dry_run: If True, only show what would transfer

    Returns (success, output_text).
    """
    ca_host = HUB_CONFIG['code_assistant']['host']
    excludes = ' '.join(
        f'--exclude={x}' for x in
        ('.git', 'node_modules', '__pycache__', '.venv', 'dist', 'build', '.next', '.cache', '.tox')
    )
    dry = '--dry-run ' if dry_run else ''
    cmd = f'rsync -az --delete {dry}{excludes} {source_host}:{source_path}/ {mirror_path}/'
    ok, output = ssh_cmd(ca_host, cmd, timeout=300)
    return ok, output


# Backward-compatible alias
rsync_to_agx = lambda ws_path, agx_mirror_path, dry_run=False: rsync_to_ca_host('ws', ws_path, agx_mirror_path, dry_run)


# ─────────────────────────────────────────────────────────────────────────────
# Git helpers (used by ~/git tool)
# ─────────────────────────────────────────────────────────────────────────────

def get_backup_age():
    """Get age of last backup. Returns (age_str, age_seconds).

    age_str is like '2h', '3d', or 'never'. age_seconds is float or None.
    """
    import time as _time
    backup_host = BACKUP_DEST.split(':')[0] if ':' in BACKUP_DEST else LOCAL_HOST
    backup_path = BACKUP_DEST.split(':', 1)[1] if ':' in BACKUP_DEST else BACKUP_DEST
    ok, output = ssh_cmd(backup_host, f'stat -c %Y {backup_path}/ 2>/dev/null', timeout=8)
    if not ok or not output.strip():
        return 'never', None
    try:
        mtime = float(output.strip())
        age = _time.time() - mtime
        if age < 3600:
            return f"{int(age / 60)}m", age
        elif age < 86400:
            return f"{int(age / 3600)}h", age
        else:
            return f"{int(age / 86400)}d", age
    except (ValueError, OSError):
        return 'never', None


def sanitize_remote_url(url):
    """Mask PAT tokens for display: https://ghp_xxx@github.com/... → https://github.com/..."""
    if not url:
        return ''
    return re.sub(r'https://[^@]+@', 'https://', url)


def extract_github_repo(url):
    """Extract owner/repo from any GitHub URL format (HTTPS, SSH, PAT).

    Returns 'owner/repo' or None.
    """
    if not url:
        return None
    # SSH: git@github.com:owner/repo.git
    m = re.match(r'git@github\.com:(.+?)(?:\.git)?$', url)
    if m:
        return m.group(1)
    # HTTPS (possibly with PAT): https://...github.com/owner/repo.git
    m = re.match(r'https?://(?:[^@]+@)?github\.com/(.+?)(?:\.git)?$', url)
    if m:
        return m.group(1)
    return None


def fetch_git_status_batch(host_alias, project_entries):
    """One SSH call per host → git state for all projects.

    Args:
        host_alias: SSH alias ('agx' or 'ws')
        project_entries: list of (project_key, project_dict) tuples

    Returns dict: project_key → {is_git, branch, dirty, untracked, commits,
                                   remote_url, head, ahead, behind, has_pat}
    """
    if not project_entries:
        return {}

    # Build compound command — one block per project, delimited
    parts = []
    for key, proj in project_entries:
        path = proj['path']
        parts.append(f'''echo "===PROJ:{key}==="
cd {path} 2>/dev/null || {{ echo "NO_DIR"; continue; }}
if [ ! -d .git ]; then echo "NOT_GIT"; continue; fi
echo "IS_GIT"
echo "BRANCH:$(git symbolic-ref --short HEAD 2>/dev/null || echo 'detached')"
echo "HEAD:$(git rev-parse --short HEAD 2>/dev/null || echo 'none')"
echo "DIRTY:$(git status --porcelain 2>/dev/null | grep -v '^?' | wc -l | tr -d ' ')"
echo "UNTRACKED:$(git status --porcelain 2>/dev/null | grep '^?' | wc -l | tr -d ' ')"
echo "COMMITS:$(git rev-list --count HEAD 2>/dev/null || echo '0')"
REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
echo "REMOTE:$REMOTE"
if [ -n "$REMOTE" ]; then
    UPSTREAM=$(git rev-parse --abbrev-ref @{{upstream}} 2>/dev/null)
    if [ -n "$UPSTREAM" ]; then
        echo "AHEAD:$(git rev-list --count $UPSTREAM..HEAD 2>/dev/null || echo '0')"
        echo "BEHIND:$(git rev-list --count HEAD..$UPSTREAM 2>/dev/null || echo '0')"
    else
        echo "AHEAD:?"
        echo "BEHIND:?"
    fi
fi''')

    cmd = '\n'.join(parts)
    ok, output = ssh_cmd(host_alias, cmd, timeout=20)
    if not ok:
        return {}

    # Parse output into per-project blocks
    results = {}
    current_key = None
    current = None

    for line in output.split('\n'):
        line = line.strip()
        if line.startswith('===PROJ:') and line.endswith('==='):
            if current_key and current:
                results[current_key] = current
            current_key = line[8:-3]
            current = {
                'is_git': False, 'branch': None, 'dirty': 0, 'untracked': 0,
                'commits': 0, 'remote_url': '', 'head': None,
                'ahead': 0, 'behind': 0, 'has_pat': False,
            }
        elif current is not None:
            if line == 'NOT_GIT' or line == 'NO_DIR':
                current['is_git'] = False
            elif line == 'IS_GIT':
                current['is_git'] = True
            elif line.startswith('BRANCH:'):
                current['branch'] = line[7:]
            elif line.startswith('HEAD:'):
                current['head'] = line[5:] if line[5:] != 'none' else None
            elif line.startswith('DIRTY:'):
                try:
                    current['dirty'] = int(line[6:])
                except ValueError:
                    pass
            elif line.startswith('UNTRACKED:'):
                try:
                    current['untracked'] = int(line[10:])
                except ValueError:
                    pass
            elif line.startswith('COMMITS:'):
                try:
                    current['commits'] = int(line[8:])
                except ValueError:
                    pass
            elif line.startswith('REMOTE:'):
                url = line[7:]
                current['remote_url'] = url
                if url and re.search(r'https://[^@]+@', url):
                    current['has_pat'] = True
            elif line.startswith('AHEAD:'):
                val = line[6:]
                current['ahead'] = int(val) if val.isdigit() else val
            elif line.startswith('BEHIND:'):
                val = line[7:]
                current['behind'] = int(val) if val.isdigit() else val

    if current_key and current:
        results[current_key] = current

    return results


# ─────────────────────────────────────────────────────────────────────────────
# .hub-env parser and service management
# ─────────────────────────────────────────────────────────────────────────────

def parse_hub_env(content):
    """Parse .hub-env file content using configparser.

    Format:
        [service: Display Name]
        port = 3456
        start = npm run dev
        dir = .
        ready = http://localhost:3456

    Returns list of service dicts or empty list.
    """
    import configparser
    import io

    parser = configparser.ConfigParser()
    parser.read_string(content)

    services = []
    for section in parser.sections():
        if not section.startswith('service:'):
            continue
        name = section.split(':', 1)[1].strip()
        svc = {'name': name}
        svc['port'] = parser.getint(section, 'port', fallback=None)
        svc['start'] = parser.get(section, 'start', fallback=None)
        svc['dir'] = parser.get(section, 'dir', fallback='.')
        svc['ready'] = parser.get(section, 'ready', fallback=None)
        if svc['port']:
            services.append(svc)
    return services


def load_hub_env(host_alias, project_path, hub_env_file='.hub-env'):
    """Load and parse .hub-env from a remote project. Returns list of service dicts or None."""
    filepath = f"{project_path}/{hub_env_file}"
    ok, output = ssh_cmd(host_alias, f'cat {filepath} 2>/dev/null', timeout=8)
    if ok and output.strip():
        services = parse_hub_env(output)
        return services if services else None
    return None


def start_service(host_alias, project_path, service):
    """Start a service in a dedicated tmux 'services' session on the remote host.

    Creates the session if it doesn't exist. Uses a named window per service.
    Idempotent: skips if the window already exists.
    """
    import re as _re
    # Sanitize window name
    win_name = _re.sub(r'[^a-zA-Z0-9_-]', '-', service['name'].lower())

    # Check if window already exists
    ok, output = ssh_cmd(host_alias, f'tmux list-windows -t services -F "#{{window_name}}" 2>/dev/null', timeout=5)
    if ok and win_name in output.split('\n'):
        return True  # Already running

    # Ensure services session exists
    ssh_cmd(host_alias, 'tmux has-session -t services 2>/dev/null || tmux new-session -d -s services', timeout=5)

    # Create window and send start command
    work_dir = service.get('dir', '.')
    start_cmd = service.get('start_cmd') or service.get('start', '')
    if not start_cmd:
        return False

    full_cmd = f'cd {project_path}/{work_dir} && {start_cmd}'
    ssh_cmd(host_alias, f'tmux new-window -t services -n {win_name}', timeout=5)
    ssh_cmd(host_alias, f'tmux send-keys -t services:{win_name} {repr(full_cmd)} Enter', timeout=5)
    return True


def wait_for_ready(host_alias, port, timeout=30, interval=2):
    """Poll probe_service() until port responds or timeout. Returns True/False."""
    import time as _time
    elapsed = 0
    while elapsed < timeout:
        if probe_service(host_alias, port, timeout=3):
            return True
        _time.sleep(interval)
        elapsed += interval
    return False


def stop_service(host_alias, service, timeout=10):
    """Send Ctrl+C to tmux window, wait for port to stop responding."""
    import re as _re
    import time as _time

    win_name = _re.sub(r'[^a-zA-Z0-9_-]', '-', service['name'].lower())
    port = service.get('port')

    # Check if window exists
    ok, output = ssh_cmd(host_alias, f'tmux list-windows -t services -F "#{{window_name}}" 2>/dev/null', timeout=5)
    if not ok or win_name not in output.split('\n'):
        return True  # Not running

    # Send Ctrl+C
    ssh_cmd(host_alias, f'tmux send-keys -t services:{win_name} C-c', timeout=5)

    if port:
        elapsed = 0
        while elapsed < timeout:
            if not probe_service(host_alias, port, timeout=2):
                return True
            _time.sleep(1)
            elapsed += 1

    return True


def restart_service(host_alias, project_path, service, ready_timeout=30):
    """Stop -> kill tmux window -> start_service() fresh -> wait_for_ready().

    Returns True if service is healthy after restart.
    """
    import re as _re
    import time as _time

    win_name = _re.sub(r'[^a-zA-Z0-9_-]', '-', service['name'].lower())
    port = service.get('port')

    # Stop gracefully
    stop_service(host_alias, service, timeout=8)
    _time.sleep(1)

    # Kill the tmux window
    ssh_cmd(host_alias, f'tmux kill-window -t services:{win_name} 2>/dev/null', timeout=5)
    _time.sleep(0.5)

    # Start fresh
    start_service(host_alias, project_path, service)

    # Wait for ready
    if port:
        return wait_for_ready(host_alias, port, timeout=ready_timeout)
    return True


def start_dev_service(host_alias, project_path, dev_service):
    """Start an npm dev service in a tmux 'dev' session on the remote host.

    Uses a separate 'dev' tmux session (not 'services') so dev servers
    can be managed independently. Window names prefixed with 'dev-'.
    """
    import re as _re
    win_name = 'dev-' + _re.sub(r'[^a-zA-Z0-9_-]', '-', dev_service['name'].lower())

    # Check if window already exists
    ok, output = ssh_cmd(host_alias, 'tmux list-windows -t dev -F "#{window_name}" 2>/dev/null', timeout=5)
    if ok and win_name in output.split('\n'):
        return True  # Already running

    # Ensure dev session exists
    ssh_cmd(host_alias, 'tmux has-session -t dev 2>/dev/null || tmux new-session -d -s dev', timeout=5)

    # Create window and send dev command
    work_dir = dev_service.get('dir', '.')
    dev_cmd = dev_service.get('dev_cmd', '')
    if not dev_cmd:
        return False

    full_cmd = f'cd {project_path}/{work_dir} && {dev_cmd}'
    ssh_cmd(host_alias, f'tmux new-window -t dev -n {win_name}', timeout=5)
    ssh_cmd(host_alias, f'tmux send-keys -t dev:{win_name} {repr(full_cmd)} Enter', timeout=5)
    return True


def stop_dev_service(host_alias, dev_service, timeout=10):
    """Send Ctrl+C to dev tmux window, wait for port to stop responding."""
    import re as _re
    import time as _time

    win_name = 'dev-' + _re.sub(r'[^a-zA-Z0-9_-]', '-', dev_service['name'].lower())
    port = dev_service.get('port')

    # Check if window exists
    ok, output = ssh_cmd(host_alias, 'tmux list-windows -t dev -F "#{window_name}" 2>/dev/null', timeout=5)
    if not ok or win_name not in output.split('\n'):
        return True  # Not running

    # Send Ctrl+C
    ssh_cmd(host_alias, f'tmux send-keys -t dev:{win_name} C-c', timeout=5)

    if port:
        elapsed = 0
        while elapsed < timeout:
            if not probe_service(host_alias, port, timeout=2):
                # Kill the window after process exits
                ssh_cmd(host_alias, f'tmux kill-window -t dev:{win_name} 2>/dev/null', timeout=5)
                return True
            _time.sleep(1)
            elapsed += 1

    # Force kill the window
    ssh_cmd(host_alias, f'tmux kill-window -t dev:{win_name} 2>/dev/null', timeout=5)
    return True


def read_deploy_state(project_key):
    """Read ~/.cache/deploy/{project}.json. Returns dict or None."""
    deploy_file = DEPLOY_CACHE_DIR / f'{project_key}.json'
    if not deploy_file.exists():
        return None
    try:
        return json.loads(deploy_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_deploy_state(project_key, head, services_restarted=None):
    """Write deploy state {head, ts, services_restarted}."""
    import time as _time
    ensure_dir(DEPLOY_CACHE_DIR)
    state = {
        'head': head,
        'ts': _time.time(),
    }
    if services_restarted:
        state['services_restarted'] = services_restarted
    deploy_file = DEPLOY_CACHE_DIR / f'{project_key}.json'
    try:
        deploy_file.write_text(json.dumps(state, indent=2) + '\n')
    except OSError:
        pass
