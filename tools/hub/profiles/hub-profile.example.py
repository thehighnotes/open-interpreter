"""Hub Profile Template for Open Interpreter.

This profile configures OI to use an Ollama model from your hub config,
sets up Mini-RAG context retrieval, permissions, and hub tool auto-run.

Copy to ~/.config/open-interpreter/profiles/ and customize.
"""

import json as _json
import os
import re
import urllib.request
from pathlib import Path
from interpreter import interpreter

# ── Load hub config ──────────────────────────────────────────────────────────
_HUB_CONFIG_PATH = Path.home() / '.config' / 'hub' / 'config.json'
_HUB_CONFIG = {}
if _HUB_CONFIG_PATH.exists():
    try:
        with open(_HUB_CONFIG_PATH) as _f:
            _HUB_CONFIG = _json.load(_f)
    except Exception:
        pass

_OLLAMA_HOST_KEY = _HUB_CONFIG.get('ollama', {}).get('host', 'local')
_OLLAMA_PORT = _HUB_CONFIG.get('ollama', {}).get('port', 11434)
_DEFAULT_MODEL = _HUB_CONFIG.get('ollama', {}).get('default_model', 'llama3.2:3b')
_HOSTS = _HUB_CONFIG.get('hosts', {})
_OLLAMA_IP = _HOSTS.get(_OLLAMA_HOST_KEY, {}).get('ip', '127.0.0.1')
_OLLAMA_HOST_NAME = _HOSTS.get(_OLLAMA_HOST_KEY, {}).get('name', _OLLAMA_HOST_KEY)

# PTY terminal environment
os.environ["TERM"] = "xterm-256color"
os.environ["OI_EXECUTION_TIMEOUT"] = "120"
os.environ["OI_INFERENCE_HOST"] = _OLLAMA_HOST_KEY

# ── Model setup ──────────────────────────────────────────────────────────────
_OLLAMA_BASE = f"http://{_OLLAMA_IP}:{_OLLAMA_PORT}"
_MODEL_NAME = os.environ.get("OI_MODEL", _DEFAULT_MODEL)

interpreter.llm.model = f"ollama/{_MODEL_NAME}"
interpreter.llm.api_base = _OLLAMA_BASE
interpreter.llm.api_key = "unused"
interpreter.llm.context_window = 16000
interpreter.llm.max_tokens = 1200
interpreter.llm.supports_functions = False
interpreter.llm.supports_vision = False
interpreter.disable_telemetry = True
interpreter.offline = True

# ── Startup connection probe ────────────────────────────────────────────────
try:
    _req = urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=2)
    _tags = _json.loads(_req.read())
    _models = [m["name"] for m in _tags.get("models", [])]
    _found = any(_MODEL_NAME in m for m in _models)
    if _found:
        print(f"\033[32m✓\033[0m {_MODEL_NAME} on {_OLLAMA_HOST_NAME} — connected")
    else:
        print(f"\033[33m⚠\033[0m {_OLLAMA_HOST_NAME} connected but {_MODEL_NAME} not loaded — available: {', '.join(_models[:5])}")
except Exception:
    print(f"\033[31m✗\033[0m {_OLLAMA_HOST_NAME} ({_OLLAMA_BASE}) unreachable — model calls will fail")

# ── Mini-RAG context retrieval ──────────────────────────────────────────────
try:
    from interpreter.core.mini_rag import MiniRAG
    _rag = MiniRAG()
    _rag.load()
    print(f"\033[32m✓\033[0m Mini-RAG loaded — {_rag.entry_count} entries, {_rag.embedding_dim}d")
except Exception as e:
    _rag = None
    print(f"\033[33m⚠\033[0m Mini-RAG failed to load: {e}")


def _rag_instructions(interpreter_obj):
    """Return RAG context for the latest user message, or empty string."""
    if not _rag or not _rag.is_loaded:
        return ""
    for msg in reversed(interpreter_obj.messages):
        if msg.get("role") == "user" and msg.get("type") == "message":
            hits = _rag.query(msg["content"], threshold=0.25, top_k=3)
            if hits:
                return "REFERENCE (use if relevant, ignore if not):\n" + _rag.format_context(hits)
            break
    return ""


# ── Selective auto-run ──────────────────────────────────────────────────────
# Read-only commands run automatically. Anything else requires confirmation.

_SAFE_PREFIXES = (
    'cat ', 'head ', 'tail ', 'less ', 'more ',
    'ls', 'll ', 'la ',
    'pwd', 'whoami', 'hostname', 'uname ',
    'df ', 'du ', 'free ', 'uptime',
    'ps ', 'top ', 'htop',
    'which ', 'type ', 'file ', 'stat ',
    'wc ', 'sort ', 'grep ', 'find ', 'locate ',
    'date', 'cal',
    'ip ', 'ifconfig', 'ping ', 'ss ', 'netstat ',
    '~/hub',
    '~/overview', '~/research',
    '~/search ',
    '~/code ',
    '~/code\n',
    '~/git status', '~/git log',
    '~/backup --list', '~/backup --dry-run',
    'diff ', 'cmp ', 'md5sum ', 'sha256sum ',
)

_UNSAFE_PATTERNS = re.compile(
    r'sudo |rm |rmdir |mv |cp |chmod |chown |kill |pkill |'
    r'shutdown|reboot|systemctl |service |'
    r'pip |apt |dnf |yum |pacman |'
    r'>\s|>>|tee |dd |mkfs|fdisk|'
    r'curl .* -[dXP]|wget ',
    re.IGNORECASE
)


def _should_auto_run(code):
    """Return True for read-only commands, False for anything requiring confirmation."""
    code = code.strip()
    if '\n' in code:
        return False
    if '&&' in code or '||' in code or '; ' in code:
        return False
    if _UNSAFE_PATTERNS.search(code):
        return False
    if code in ('~/git',):
        return True
    for prefix in _SAFE_PREFIXES:
        if code.startswith(prefix):
            return True
    return False


interpreter.auto_run = _should_auto_run

# ── System message ──────────────────────────────────────────────────────────
interpreter.system_message = """You are a terminal assistant. Run one command per code block:
```bash
command here
```
Read files with cat. Edit files carefully. Ask before destructive operations.
"""

# ── Custom instructions (appended before each response) ────────────────────
interpreter.custom_instructions = _rag_instructions
