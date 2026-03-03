"""OI Bridge — Interpreter wrapper for WebUI streaming and approval."""

import json
import os
import re
import queue
import threading
import urllib.request
from pathlib import Path

# ── Hub config ───────────────────────────────────────────────────────────────
_HUB_CONFIG_PATH = Path.home() / '.config' / 'hub' / 'config.json'
_HUB_CONFIG = {}
if _HUB_CONFIG_PATH.exists():
    try:
        with open(_HUB_CONFIG_PATH) as f:
            _HUB_CONFIG = json.load(f)
    except Exception:
        pass

_OLLAMA_HOST_KEY = _HUB_CONFIG.get('ollama', {}).get('host', 'local')
_OLLAMA_PORT = _HUB_CONFIG.get('ollama', {}).get('port', 11434)
_DEFAULT_MODEL = _HUB_CONFIG.get('ollama', {}).get('default_model', 'llama3.2:3b')
_HOSTS = _HUB_CONFIG.get('hosts', {})
_OLLAMA_IP = _HOSTS.get(_OLLAMA_HOST_KEY, {}).get('ip', '127.0.0.1')

_OLLAMA_BASE = f"http://{_OLLAMA_IP}:{_OLLAMA_PORT}"

# ── Safe command prefixes (mirrors hub-profile.example.py) ───────────────────
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


class OIBridge:
    """Singleton wrapper around Open Interpreter for WebUI use."""

    def __init__(self):
        self._interpreter = None
        self._lock = threading.Lock()
        self._approval_event = threading.Event()
        self._approval_result = False
        self._stop_event = threading.Event()
        self._current_thread = None
        self._pending_approval = False
        # Load saved settings from webui config
        self._saved_cfg = self._load_saved_config()
        self._model_name = self._saved_cfg.get(
            "model", os.environ.get("OI_MODEL", _DEFAULT_MODEL))

    @staticmethod
    def _load_saved_config():
        """Load persisted settings from webui/config.json."""
        cfg_path = Path(__file__).resolve().parent / "config.json"
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def initialize(self):
        """Set up the interpreter instance (call once at startup)."""
        from interpreter import interpreter

        self._interpreter = interpreter

        interpreter.llm.model = f"ollama/{self._model_name}"
        interpreter.llm.api_base = _OLLAMA_BASE
        interpreter.llm.api_key = "unused"
        interpreter.llm.context_window = self._saved_cfg.get(
            "context_window", int(os.environ.get("OI_CTX", 16000)))
        interpreter.llm.max_tokens = self._saved_cfg.get("max_tokens", 1200)
        interpreter.llm.supports_functions = False
        interpreter.llm.supports_vision = True
        interpreter.disable_telemetry = True
        interpreter.offline = True

        interpreter.system_message = (
            "You are a terminal assistant. Run one command per code block:\n"
            "```bash\ncommand here\n```\n"
            "Read files with cat. Edit files carefully. Ask before destructive operations.\n"
        )

        # Load Mini-RAG if available
        self._rag = None
        try:
            from interpreter.core.mini_rag import MiniRAG
            self._rag = MiniRAG()
            self._rag.load()
        except Exception:
            pass

        if self._rag and self._rag.is_loaded:
            interpreter.custom_instructions = self._rag_instructions

        # Auto-run callable — blocks on approval for unsafe commands
        interpreter.auto_run = self._webui_auto_run

        # Probe connection
        self._connected = False
        try:
            req = urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3)
            tags = json.loads(req.read())
            models = [m["name"] for m in tags.get("models", [])]
            self._connected = any(self._model_name in m for m in models)
        except Exception:
            pass

    def _rag_instructions(self, interpreter_obj):
        if not self._rag or not self._rag.is_loaded:
            return ""
        for msg in reversed(interpreter_obj.messages):
            if msg.get("role") == "user" and msg.get("type") == "message":
                hits = self._rag.query(msg["content"], threshold=0.25, top_k=3)
                if hits:
                    return "REFERENCE (use if relevant, ignore if not):\n" + self._rag.format_context(hits)
                break
        return ""

    def _should_auto_run(self, code):
        """Check if a command is safe to auto-run."""
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

    def _webui_auto_run(self, code):
        """Auto-run callable: safe commands pass, unsafe ones block for approval."""
        if self._should_auto_run(code):
            return True

        # Need approval — signal the SSE stream and wait
        self._pending_approval = True
        self._approval_event.clear()
        self._approval_event.wait()  # blocks until approve/skip
        self._pending_approval = False
        return self._approval_result

    def chat_stream(self, message):
        """Stream OI response as SSE events. Yields 'data: {...}\n\n' strings."""
        if not self._interpreter:
            yield f"data: {json.dumps({'type': 'error', 'content': 'Interpreter not initialized'})}\n\n"
            return

        if not self._lock.acquire(blocking=False):
            yield f"data: {json.dumps({'type': 'error', 'content': 'Another request is in progress'})}\n\n"
            return

        try:
            self._stop_event.clear()
            q = queue.Queue()
            error_holder = [None]

            def _run():
                try:
                    for chunk in self._interpreter.chat(message, display=False, stream=True):
                        if self._stop_event.is_set():
                            break
                        normalized = self._normalize_chunk(chunk)
                        if normalized:
                            q.put(normalized)
                except Exception as e:
                    error_holder[0] = str(e)
                finally:
                    q.put(None)  # sentinel

            t = threading.Thread(target=_run, daemon=True)
            self._current_thread = t
            t.start()

            while True:
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    if not t.is_alive():
                        break
                    # Check for pending approval
                    if self._pending_approval:
                        yield f"data: {json.dumps({'type': 'confirmation', 'language': 'bash', 'code': self._get_pending_code()})}\n\n"
                        # Wait for approval response
                        while self._pending_approval and not self._stop_event.is_set():
                            try:
                                item = q.get(timeout=0.2)
                                if item is None:
                                    break
                                yield f"data: {json.dumps(item)}\n\n"
                            except queue.Empty:
                                continue
                        if item is None:
                            break
                    continue

                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"

            if error_holder[0]:
                yield f"data: {json.dumps({'type': 'error', 'content': error_holder[0]})}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        finally:
            self._current_thread = None
            self._lock.release()

    def _get_pending_code(self):
        """Extract the last code block from messages for the confirmation UI."""
        if not self._interpreter:
            return ""
        for msg in reversed(self._interpreter.messages):
            if msg.get("type") == "code":
                return msg.get("content", "")
        return ""

    def _normalize_chunk(self, chunk):
        """Map OI chunk format to clean event dict."""
        if not isinstance(chunk, dict):
            return None

        role = chunk.get("role", "")
        msg_type = chunk.get("type", "")
        content = chunk.get("content", "")
        start = chunk.get("start", False)
        end = chunk.get("end", False)

        if role == "assistant":
            if msg_type == "message":
                if start:
                    return {"type": "text_start"}
                if end:
                    return {"type": "text_end"}
                if content:
                    return {"type": "text", "content": content}

            elif msg_type == "code":
                lang = chunk.get("format", "bash")
                if start:
                    return {"type": "code_start", "language": lang}
                if end:
                    return {"type": "code_end"}
                if content:
                    return {"type": "code", "language": lang, "content": content}

        elif role == "computer":
            if msg_type == "console":
                if start:
                    return {"type": "output_start"}
                if end:
                    return {"type": "output_end"}
                if content:
                    return {"type": "output", "content": content}

        return None

    def approve(self, approved):
        """Respond to a pending code approval."""
        self._approval_result = approved
        self._approval_event.set()

    def stop(self):
        """Abort current generation."""
        self._stop_event.set()
        if self._pending_approval:
            self._approval_result = False
            self._approval_event.set()

    def reset(self):
        """Clear conversation history."""
        if self._interpreter:
            self._interpreter.messages = []

    def get_messages(self):
        """Return current message history."""
        if not self._interpreter:
            return []
        return list(self._interpreter.messages)

    def get_session_info(self):
        """Return session metadata."""
        msgs = self.get_messages()
        return {
            "message_count": len(msgs),
            "model": self._model_name,
            "connected": self._connected,
            "rag_loaded": bool(self._rag and self._rag.is_loaded),
            "rag_entries": self._rag.entry_count if self._rag and self._rag.is_loaded else 0,
        }

    def get_oi_config(self):
        """Return current OI interpreter configuration."""
        i = self._interpreter
        if not i:
            return {}
        return {
            "model": self._model_name,
            "api_base": i.llm.api_base,
            "context_window": i.llm.context_window,
            "max_tokens": i.llm.max_tokens,
            "supports_vision": i.llm.supports_vision,
            "supports_functions": i.llm.supports_functions,
            "offline": i.offline,
            "auto_run": "callable" if callable(i.auto_run) else bool(i.auto_run),
            "custom_instructions": "callable" if callable(i.custom_instructions) else ("set" if i.custom_instructions else "none"),
            "system_message_preview": (i.system_message or "")[:200],
        }

    def update_model(self, model_name):
        """Switch to a different model."""
        if self._interpreter:
            self._model_name = model_name
            self._interpreter.llm.model = f"ollama/{model_name}"

    def update_context_window(self, ctx):
        """Update context window size."""
        if self._interpreter:
            self._interpreter.llm.context_window = ctx

    def update_api_base(self, url):
        """Update the LLM API base URL (for custom endpoint support)."""
        if self._interpreter:
            self._interpreter.llm.api_base = url


# Singleton
bridge = OIBridge()
