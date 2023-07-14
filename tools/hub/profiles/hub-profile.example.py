"""Hub Profile Template for Open Interpreter.

This profile configures OI to use the LLM backend from your hub config
(vLLM or Ollama), sets up Mini-RAG context retrieval, permissions, and
hub tool auto-run.

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

_HOSTS = _HUB_CONFIG.get('hosts', {})

# ── LLM config (vllm or ollama, with backward compat) ───────────────────────
if 'llm' in _HUB_CONFIG:
    _LLM = _HUB_CONFIG['llm']
    _LLM_BACKEND = _LLM.get('backend', 'vllm')
    _LLM_HOST_KEY = _LLM.get('host', 'local')
    _LLM_PORT = _LLM.get('port', 8000)
    _DEFAULT_MODEL = _LLM.get('model', 'llama3.2:3b')
    _CONTEXT_WINDOW = _LLM.get('context_window', 44000)
else:
    _LLM_BACKEND = 'ollama'
    _LLM_HOST_KEY = _HUB_CONFIG.get('ollama', {}).get('host', 'local')
    _LLM_PORT = _HUB_CONFIG.get('ollama', {}).get('port', 11434)
    _DEFAULT_MODEL = _HUB_CONFIG.get('ollama', {}).get('default_model', 'llama3.2:3b')
    _CONTEXT_WINDOW = _HUB_CONFIG.get('ollama', {}).get('num_ctx', 44000)

_LLM_IP = _HOSTS.get(_LLM_HOST_KEY, {}).get('ip', '127.0.0.1')
_LLM_HOST_NAME = _HOSTS.get(_LLM_HOST_KEY, {}).get('name', _LLM_HOST_KEY)

# PTY terminal environment
os.environ["TERM"] = "xterm-256color"
os.environ["OI_EXECUTION_TIMEOUT"] = "120"
os.environ["OI_INFERENCE_HOST"] = _LLM_HOST_KEY

# ── Model setup ──────────────────────────────────────────────────────────────
_MODEL_NAME = os.environ.get("OI_MODEL", _DEFAULT_MODEL)

if _LLM_BACKEND == 'vllm':
    _LLM_BASE = f"http://{_LLM_IP}:{_LLM_PORT}/v1"
    interpreter.llm.model = f"openai/{_MODEL_NAME}"
    interpreter.llm.api_base = _LLM_BASE
else:
    _LLM_BASE = f"http://{_LLM_IP}:{_LLM_PORT}"
    interpreter.llm.model = f"ollama/{_MODEL_NAME}"
    interpreter.llm.api_base = _LLM_BASE

interpreter.llm.api_key = "unused"
interpreter.llm.context_window = _CONTEXT_WINDOW
interpreter.llm.max_tokens = 1200
interpreter.llm.supports_functions = False
interpreter.llm.supports_vision = False
interpreter.disable_telemetry = True
interpreter.offline = True

# ── Startup connection probe ────────────────────────────────────────────────
try:
    if _LLM_BACKEND == 'vllm':
        _req = urllib.request.urlopen(f"{_LLM_BASE}/models", timeout=3)
        _data = _json.loads(_req.read())
        _models = [m["id"] for m in _data.get("data", [])]
    else:
        _req = urllib.request.urlopen(f"{_LLM_BASE}/api/tags", timeout=2)
        _data = _json.loads(_req.read())
        _models = [m["name"] for m in _data.get("models", [])]
    _found = any(_MODEL_NAME in m for m in _models)
    if _found:
        print(f"\033[32m✓\033[0m {_MODEL_NAME} on {_LLM_HOST_NAME} ({_LLM_BACKEND}) — connected")
    else:
        print(f"\033[33m⚠\033[0m {_LLM_HOST_NAME} connected but {_MODEL_NAME} not found — available: {', '.join(_models[:5])}")
except Exception:
    print(f"\033[31m✗\033[0m {_LLM_HOST_NAME} ({_LLM_BASE}) unreachable — model calls will fail")

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

# ── Session preamble (injected by begin/work) ────────────────────────────────
_PREAMBLE = ""
_PREAMBLE_PATH = Path("/tmp/_oi_preamble.txt")
if _PREAMBLE_PATH.exists():
    try:
        _PREAMBLE = _PREAMBLE_PATH.read_text().strip()
    except OSError:
        pass

# ── System message ──────────────────────────────────────────────────────────
_SYS_BASE = """You are a terminal assistant. Run one command per code block:
```bash
command here
```
Read files with cat. Edit files carefully. Ask before destructive operations.
"""

interpreter.system_message = f"{_PREAMBLE}\n\n{_SYS_BASE}" if _PREAMBLE else _SYS_BASE

# ── Silent context injection (full CLAUDE.md as pre-seeded conversation) ──
_CONTEXT_PATH = Path("/tmp/_oi_context.txt")
if _CONTEXT_PATH.exists():
    try:
        _full_context = _CONTEXT_PATH.read_text().strip()
        if _full_context:
            _project_name = os.environ.get("OI_PROJECT", "project")
            interpreter.messages = [
                {"role": "user", "type": "message",
                 "content": f"Here is the full CLAUDE.md for {_project_name}. Use this as your reference for project conventions, architecture, and guidelines:\n\n{_full_context}"},
                {"role": "assistant", "type": "message",
                 "content": f"Understood. I've loaded the full CLAUDE.md ({len(_full_context)} chars) for {_project_name}. I'll follow these conventions and use this as reference throughout our session."},
            ]
    except OSError:
        pass

# ── Custom instructions (appended before each response) ────────────────────
interpreter.custom_instructions = _rag_instructions
