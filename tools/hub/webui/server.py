#!/usr/bin/env python3
"""OI WebUI — Starlette server for Open Interpreter web interface."""

import html as html_mod
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

# ── Paths ────────────────────────────────────────────────────────────────────
WEBUI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WEBUI_DIR))  # for oi_bridge import
sys.path.insert(0, str(Path.home()))  # for hub_common import
try:
    import hub_common
except ImportError:
    hub_common = None

# ── WebUI config ─────────────────────────────────────────────────────────────
WEBUI_CONFIG = WEBUI_DIR / "config.json"
STATIC_DIR = WEBUI_DIR / "static"

_webui_cfg = {}
if WEBUI_CONFIG.exists():
    try:
        with open(WEBUI_CONFIG) as f:
            _webui_cfg = json.load(f)
    except Exception:
        pass

PORT = _webui_cfg.get("port", 8585)


def _load_webui_config():
    """Load webui config from disk."""
    if WEBUI_CONFIG.exists():
        try:
            with open(WEBUI_CONFIG) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_webui_config(updates):
    """Merge updates into webui config and save to disk."""
    cfg = _load_webui_config()
    cfg.update(updates)
    try:
        with open(WEBUI_CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
    return cfg


# ── ANSI-to-HTML conversion ───────────────────────────────────────────────
_SGR_RE = re.compile(r'\x1b\[([0-9;]*)m')
_ALL_ESC_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

_SGR_CLASS_MAP = {
    '0': None,           # reset
    '1': 'ansi-bold',
    '2': 'ansi-dim',
    '22': None,          # normal intensity (reset bold/dim)
    '30': 'ansi-black',
    '31': 'ansi-red',
    '32': 'ansi-green',
    '33': 'ansi-yellow',
    '34': 'ansi-blue',
    '35': 'ansi-magenta',
    '36': 'ansi-cyan',
    '37': 'ansi-white',
    '39': None,          # default fg
    '90': 'ansi-gray',
    '91': 'ansi-bright-red',
    '92': 'ansi-bright-green',
    '93': 'ansi-bright-yellow',
    '94': 'ansi-bright-blue',
    '95': 'ansi-bright-magenta',
    '96': 'ansi-bright-cyan',
    '97': 'ansi-white',
    # Combined codes (e.g. \x1b[1;32m)
    '0;32': 'ansi-green',
    '0;33': 'ansi-yellow',
    '0;31': 'ansi-red',
    '0;36': 'ansi-cyan',
    '1;32': 'ansi-bold ansi-green',
    '1;33': 'ansi-bold ansi-yellow',
    '1;31': 'ansi-bold ansi-red',
    '1;36': 'ansi-bold ansi-cyan',
    '1;34': 'ansi-bold ansi-blue',
    '1;35': 'ansi-bold ansi-magenta',
    '1;37': 'ansi-bold ansi-white',
}


_SPINNER_RE = re.compile(r'^[\s\x1b\[\d;]*[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]')

def strip_spinner_frames(text):
    """Clean up terminal output for web display:
    - Simulate \\r behavior (keep last segment per line)
    - Drop braille spinner lines (captured as separate \\n lines in subprocess)
    - Collapse runs of 2+ blank lines down to 1"""
    lines = text.split('\n')
    result = []
    for line in lines:
        if '\r' in line:
            segments = line.split('\r')
            # Last non-empty segment wins (terminal overwrites from col 0)
            final = ''
            for seg in segments:
                if seg:
                    final = seg
            if final.strip():
                result.append(final)
            # else: line was fully cleared by spinner, drop it
        else:
            result.append(line)
    # Drop spinner frame lines (braille chars used by hub Spinner class)
    result = [l for l in result if not _SPINNER_RE.match(_ALL_ESC_RE.sub('', l))]
    # Collapse consecutive blank lines to at most one
    collapsed = []
    prev_blank = False
    for line in result:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank
    return '\n'.join(collapsed)


def ansi_to_html(text):
    """Convert ANSI SGR escape codes to <span class="ansi-*"> HTML."""
    # First strip non-SGR escapes (cursor movement, clear line, etc.)
    text = re.sub(r'\x1b\[[0-9;]*[A-HJKSTfhln]', '', text)
    text = re.sub(r'\x1b\[\?[0-9;]*[a-zA-Z]', '', text)

    parts = _SGR_RE.split(text)
    # parts alternates: text, sgr_params, text, sgr_params, ...
    out = []
    span_open = False

    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Text content — HTML-escape it
            out.append(html_mod.escape(part))
        else:
            # SGR parameter string
            css_class = _SGR_CLASS_MAP.get(part)
            if css_class is None and part in ('', '0', '00', '39'):
                # Reset
                if span_open:
                    out.append('</span>')
                    span_open = False
            elif css_class is None:
                # Try individual codes for compound sequences not in map
                classes = []
                for code in part.split(';'):
                    c = _SGR_CLASS_MAP.get(code)
                    if c:
                        classes.extend(c.split())
                if classes:
                    if span_open:
                        out.append('</span>')
                    out.append(f'<span class="{" ".join(classes)}">')
                    span_open = True
                elif span_open and (part.startswith('0') or part == ''):
                    out.append('</span>')
                    span_open = False
            else:
                if span_open:
                    out.append('</span>')
                out.append(f'<span class="{css_class}">')
                span_open = True

    if span_open:
        out.append('</span>')

    return ''.join(out)


# ── OI Bridge (lazy init) ───────────────────────────────────────────────────
_bridge = None


def get_bridge():
    global _bridge
    if _bridge is None:
        from oi_bridge import bridge
        bridge.initialize()
        _bridge = bridge
    return _bridge


# ── Helper: parse JSON body ─────────────────────────────────────────────────
async def _json_body(request):
    body = await request.body()
    return json.loads(body) if body else {}


# ── Page routes ──────────────────────────────────────────────────────────────

async def index(request):
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text())


# ── Chat API ─────────────────────────────────────────────────────────────────

async def chat(request):
    body = await _json_body(request)
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    bridge = get_bridge()
    return StreamingResponse(
        bridge.chat_stream(message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def chat_approve(request):
    body = await _json_body(request)
    approved = body.get("approved", False)
    bridge = get_bridge()
    bridge.approve(approved)
    return JSONResponse({"ok": True})


async def chat_stop(request):
    bridge = get_bridge()
    bridge.stop()
    return JSONResponse({"ok": True})


# ── Magic commands ───────────────────────────────────────────────────────────

async def magic_command(request):
    body = await _json_body(request)
    cmd = body.get("command", "").strip()
    if not cmd:
        return JSONResponse({"error": "Empty command"}, status_code=400)

    magic_map = {
        "%status": ["hub", "--status"],
        "%next": ["hub", "--next"],
        "%projects": ["hub", "--scan"],
        "%services": ["hub", "--services"],
        "%health": ["health-probe"],
        "%repo": ["git"],
        "%research": ["research"],
        "%notify": ["notify"],
        "%overview": ["overview"],
        "%backup": ["backup", "--list"],
        "%vllm": ["hub", "--vllm"],
        "%dev": ["hub", "--dev"],
        "%prepare": ["prepare"],
        "%begin": ["begin"],
        "%work": ["work"],
    }

    timeout_map = {"%prepare": 120, "%work": 180, "%begin": 120, "%vllm": 30}

    parts = cmd.split(None, 1)
    base = parts[0].lower()

    columns = body.get("columns")

    if base in magic_map:
        tool_cmd = magic_map[base]
        tool_path = str(Path.home() / tool_cmd[0])
        args = tool_cmd[1:] + (parts[1:] if len(parts) > 1 else [])
        env = dict(os.environ)
        if columns:
            env["COLUMNS"] = str(max(40, min(int(columns), 300)))
        try:
            result = subprocess.run(
                [tool_path] + args,
                capture_output=True, text=True, timeout=timeout_map.get(base, 30),
                env=env,
            )
            raw = (result.stdout + result.stderr).strip()
            output = ansi_to_html(strip_spinner_frames(raw))
        except subprocess.TimeoutExpired:
            output = html_mod.escape(f"Command timed out: {cmd}")
        except Exception as e:
            output = html_mod.escape(f"Error: {e}")
    elif base == "%reset":
        bridge = get_bridge()
        bridge.reset()
        output = "Session reset."
    elif base == "%model":
        if len(parts) > 1:
            bridge = get_bridge()
            bridge.update_model(parts[1])
            output = f"Model switched to: {parts[1]}"
        else:
            bridge = get_bridge()
            info = bridge.get_session_info()
            output = f"Current model: {info['model']}"
    else:
        output = f"Unknown magic command: {base}\nAvailable: {', '.join(sorted(magic_map.keys()))}, %reset, %model"

    return JSONResponse({"output": output, "command": cmd})


# ── Config API ───────────────────────────────────────────────────────────────

async def get_config(request):
    if hub_common:
        cfg = hub_common.HUB_CONFIG
        hosts = {}
        for key, host in hub_common.HOSTS.items():
            hosts[key] = {"name": host["name"], "roles": host.get("roles", [])}
        return JSONResponse({
            "hub_name": cfg.get("hub", {}).get("name", "Dev Hub"),
            "hosts": hosts,
            "model": cfg.get("llm", cfg.get("ollama", {})).get("model", cfg.get("ollama", {}).get("default_model", "unknown")),
            "local_host": cfg.get("hub", {}).get("local_host", "local"),
        })
    return JSONResponse({"hub_name": "Dev Hub", "hosts": {}, "model": "unknown", "local_host": "local"})


# ── Session API ──────────────────────────────────────────────────────────────

async def get_session(request):
    bridge = get_bridge()
    return JSONResponse(bridge.get_session_info())


async def get_messages(request):
    bridge = get_bridge()
    msgs = bridge.get_messages()
    simplified = []
    for m in msgs:
        simplified.append({
            "role": m.get("role", ""),
            "type": m.get("type", ""),
            "content": m.get("content", ""),
            "format": m.get("format", ""),
        })
    return JSONResponse({"messages": simplified})


async def reset_session(request):
    bridge = get_bridge()
    bridge.reset()
    return JSONResponse({"ok": True})


# ── Hub data endpoints ───────────────────────────────────────────────────────

def _run_tool(name, *args, timeout=30, columns=None):
    """Run a hub tool and return HTML-rendered output with ANSI colors."""
    tool_path = str(Path.home() / name)
    env = dict(os.environ)
    if columns:
        env["COLUMNS"] = str(max(40, min(int(columns), 300)))
    try:
        result = subprocess.run(
            [tool_path] + list(args),
            capture_output=True, text=True, timeout=timeout,
            env=env,
        )
        raw = (result.stdout + result.stderr).strip()
        return ansi_to_html(strip_spinner_frames(raw))
    except subprocess.TimeoutExpired:
        return html_mod.escape(f"Timeout running {name}")
    except Exception as e:
        return html_mod.escape(f"Error: {e}")


async def get_status(request):
    cols = request.query_params.get("columns")
    output = _run_tool("hub", "--status", columns=cols)
    return JSONResponse({"output": output})


async def get_projects(request):
    if hub_common:
        projects, order, _ = hub_common.load_projects()
        result = []
        for key in order:
            if key in projects:
                p = projects[key]
                result.append({
                    "key": key,
                    "name": p.get("name", key),
                    "tagline": p.get("tagline", ""),
                    "host": p.get("host", ""),
                    "path": p.get("path", ""),
                    "services": p.get("services", []),
                    "dev_services": p.get("dev_services", []),
                    "git_remote": p.get("git_remote", ""),
                })
        return JSONResponse({"projects": result})
    return JSONResponse({"projects": []})


async def switch_project(request):
    body = await _json_body(request)
    project_key = body.get("project", "").strip()
    if not project_key or not hub_common:
        return JSONResponse({"error": "Invalid project"}, status_code=400)

    projects, order, _ = hub_common.load_projects()
    if project_key not in projects:
        resolved = hub_common.resolve_project(project_key)
        if resolved:
            project_key = resolved
        else:
            return JSONResponse({"error": f"Project not found: {project_key}"}, status_code=404)

    p = projects[project_key]
    os.environ["OI_PROJECT"] = project_key
    os.environ["OI_PROJECT_NAME"] = p.get("name", project_key)
    os.environ["OI_PROJECT_HOST"] = p.get("host", "")
    os.environ["OI_PROJECT_PATH"] = p.get("path", "")

    return JSONResponse({"ok": True, "project": project_key, "name": p.get("name", project_key)})


async def get_repo(request):
    cols = request.query_params.get("columns")
    output = _run_tool("git", columns=cols)
    return JSONResponse({"output": output})


async def get_research(request):
    cols = request.query_params.get("columns")
    output = _run_tool("research", timeout=45, columns=cols)
    return JSONResponse({"output": output})


async def get_notifications(request):
    cols = request.query_params.get("columns")
    output = _run_tool("notify", "--all", columns=cols)
    return JSONResponse({"output": output})


async def clear_notifications(request):
    output = _run_tool("notify", "--clear")
    return JSONResponse({"output": output})


# ── Settings API ─────────────────────────────────────────────────────────────

async def get_settings(request):
    bridge = get_bridge()
    info = bridge.get_session_info()
    result = {
        "model": info["model"],
        "context_window": bridge._interpreter.llm.context_window if bridge._interpreter else 16000,
        "max_tokens": bridge._interpreter.llm.max_tokens if bridge._interpreter else 1200,
        "connected": info["connected"],
        "rag_loaded": info["rag_loaded"],
        "rag_entries": info["rag_entries"],
        "message_count": info["message_count"],
    }
    # Add hub infrastructure info
    if hub_common:
        cfg = hub_common.HUB_CONFIG
        llm_host_obj = hub_common.HOSTS.get(hub_common.LLM_HOST, {})
        result["llm_backend"] = hub_common.LLM_BACKEND
        result["llm_host"] = llm_host_obj.get("name", hub_common.LLM_HOST)
        result["llm_ip"] = llm_host_obj.get("ip", "127.0.0.1")
        result["llm_port"] = hub_common.LLM_PORT
        result["hub_name"] = cfg.get("hub", {}).get("name", "Dev Hub")
        result["host_count"] = len(hub_common.HOSTS)
    return JSONResponse(result)


async def update_settings(request):
    body = await _json_body(request)
    bridge = get_bridge()
    persist = {}

    if "model" in body:
        bridge.update_model(body["model"])
        persist["model"] = body["model"]
    if "context_window" in body:
        ctx = int(body["context_window"])
        bridge.update_context_window(ctx)
        persist["context_window"] = ctx
    if "max_tokens" in body and bridge._interpreter:
        tok = int(body["max_tokens"])
        bridge._interpreter.llm.max_tokens = tok
        persist["max_tokens"] = tok

    if persist:
        _save_webui_config(persist)

    return JSONResponse({"ok": True})


async def get_oi_config(request):
    bridge = get_bridge()
    return JSONResponse(bridge.get_oi_config())


# ── Hub Config API ────────────────────────────────────────────────────────────

async def get_hub_config(request):
    """Return full hub config dict."""
    if hub_common:
        return JSONResponse(hub_common.HUB_CONFIG)
    return JSONResponse({})


async def update_hub_config(request):
    """Update a section of hub config. Body: { section: "git", data: {...} }"""
    if not hub_common:
        return JSONResponse({"error": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    section = body.get("section", "").strip()
    data = body.get("data")
    if not section or not isinstance(data, dict):
        return JSONResponse({"error": "section and data required"}, status_code=400)

    config = hub_common.HUB_CONFIG
    if section == "hosts":
        config["hosts"] = data
    elif section in config and isinstance(config[section], dict):
        config[section].update(data)
    else:
        config[section] = data

    hub_common.save_config(config)
    hub_common.reload_config()
    return JSONResponse({"ok": True})


async def probe_backup(request):
    """Test backup destination reachability. Body: { destination: "agx:~/..." }"""
    if not hub_common:
        return JSONResponse({"error": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    dest = body.get("destination", "").strip()
    if not dest:
        return JSONResponse({"reachable": False, "error": "No destination"})

    if ":" in dest:
        host_alias = dest.split(":")[0]
        reachable = hub_common.check_host_reachable(host_alias)
        return JSONResponse({"reachable": reachable, "host": host_alias})
    else:
        # Local path
        from pathlib import Path as P
        expanded = P(os.path.expanduser(dest))
        return JSONResponse({"reachable": True, "local": True, "exists": expanded.parent.exists()})


# ── Hosts API ─────────────────────────────────────────────────────────────────

async def probe_host(request):
    """Test SSH reachability of a host. Body: { alias: "agx" }"""
    if not hub_common:
        return JSONResponse({"error": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    alias = body.get("alias", "").strip()
    if not alias:
        return JSONResponse({"reachable": False, "error": "No alias"})

    host = hub_common.HOSTS.get(alias)
    if not host:
        return JSONResponse({"reachable": False, "error": f"Unknown host: {alias}"})

    ip = host.get("ip", "127.0.0.1")
    user = host.get("user", "user")
    reachable = hub_common.check_host_reachable(alias)
    guide = ""
    if not reachable:
        guide = (
            f"SSH connection to {alias} ({ip}) failed.\n\n"
            f"Setup steps:\n"
            f"  1. Ensure the host is powered on and reachable: ping {ip}\n"
            f"  2. Generate an SSH key (if needed): ssh-keygen -t ed25519\n"
            f"  3. Copy your key: ssh-copy-id {user}@{ip}\n"
            f"  4. Add to ~/.ssh/config:\n"
            f"     Host {alias}\n"
            f"       HostName {ip}\n"
            f"       User {user}\n"
            f"       IdentityFile ~/.ssh/id_ed25519\n"
            f"  5. Test: ssh {alias} echo ok"
        )
    return JSONResponse({"reachable": reachable, "alias": alias, "guide": guide})


async def save_hosts(request):
    """Replace entire hosts section. Body: { hosts: {...} }"""
    if not hub_common:
        return JSONResponse({"error": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    hosts = body.get("hosts")
    if not isinstance(hosts, dict):
        return JSONResponse({"error": "hosts dict required"}, status_code=400)

    config = hub_common.HUB_CONFIG
    config["hosts"] = hosts
    hub_common.save_config(config)
    hub_common.reload_config()
    return JSONResponse({"ok": True})


# ── LLM API (vLLM / Ollama) ───────────────────────────────────────────────────

async def get_llm_models(request):
    """Fetch model list from LLM backend."""
    if not hub_common:
        return JSONResponse({"models": []})
    ip = hub_common.HOSTS.get(hub_common.LLM_HOST, {}).get("ip", "127.0.0.1")
    port = hub_common.LLM_PORT
    try:
        import urllib.request
        if hub_common.LLM_BACKEND == 'vllm':
            req = urllib.request.urlopen(f"http://{ip}:{port}/v1/models", timeout=5)
            data = json.loads(req.read())
            models = [m["id"] for m in data.get("data", [])]
        else:
            req = urllib.request.urlopen(f"http://{ip}:{port}/api/tags", timeout=5)
            data = json.loads(req.read())
            models = [m["name"] for m in data.get("models", [])]
        return JSONResponse({"models": models, "backend": hub_common.LLM_BACKEND})
    except Exception as e:
        return JSONResponse({"models": [], "error": str(e), "backend": hub_common.LLM_BACKEND})

# Keep old route as alias
get_ollama_models = get_llm_models


async def probe_llm(request):
    """Test LLM backend connectivity."""
    if not hub_common:
        return JSONResponse({"reachable": False})
    ip = hub_common.HOSTS.get(hub_common.LLM_HOST, {}).get("ip", "127.0.0.1")
    port = hub_common.LLM_PORT
    try:
        import urllib.request
        if hub_common.LLM_BACKEND == 'vllm':
            req = urllib.request.urlopen(f"http://{ip}:{port}/v1/models", timeout=5)
            data = json.loads(req.read())
            model_count = len(data.get("data", []))
        else:
            req = urllib.request.urlopen(f"http://{ip}:{port}/api/tags", timeout=5)
            data = json.loads(req.read())
            model_count = len(data.get("models", []))
        return JSONResponse({"reachable": True, "model_count": model_count, "backend": hub_common.LLM_BACKEND})
    except Exception as e:
        return JSONResponse({"reachable": False, "error": str(e), "backend": hub_common.LLM_BACKEND})

# Keep old route as alias
probe_ollama = probe_llm


# ── RAG API ───────────────────────────────────────────────────────────────────

_RAG_FILE = Path.home() / '.config' / 'hub' / 'rag-entries.json'


def _load_rag():
    if _RAG_FILE.exists():
        try:
            with open(_RAG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_rag(entries):
    _RAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RAG_FILE, 'w') as f:
        json.dump(entries, f, indent=2)
        f.write('\n')


async def get_rag(request):
    entries = _load_rag()
    categories = sorted(set(e.get("category", "") for e in entries if e.get("category")))
    return JSONResponse({"entries": entries, "count": len(entries), "categories": categories})


async def add_rag(request):
    body = await _json_body(request)
    entry = body.get("entry")
    if not entry or not entry.get("topic"):
        return JSONResponse({"error": "entry with topic required"}, status_code=400)
    entries = _load_rag()
    entries.append(entry)
    _save_rag(entries)
    return JSONResponse({"ok": True, "count": len(entries)})


async def update_rag(request):
    body = await _json_body(request)
    index = body.get("index")
    entry = body.get("entry")
    entries = _load_rag()
    if index is None or not entry or index < 0 or index >= len(entries):
        return JSONResponse({"error": "valid index and entry required"}, status_code=400)
    entries[index] = entry
    _save_rag(entries)
    return JSONResponse({"ok": True})


async def delete_rag(request):
    body = await _json_body(request)
    index = body.get("index")
    entries = _load_rag()
    if index is None or index < 0 or index >= len(entries):
        return JSONResponse({"error": "valid index required"}, status_code=400)
    entries.pop(index)
    _save_rag(entries)
    return JSONResponse({"ok": True, "count": len(entries)})


# ── Code Assistant probe ─────────────────────────────────────────────────────

async def probe_code_assistant(request):
    if not hub_common:
        return JSONResponse({"healthy": False})
    try:
        healthy = hub_common.check_code_assistant()
        return JSONResponse({"healthy": healthy})
    except Exception as e:
        return JSONResponse({"healthy": False, "error": str(e)})


# ── Image upload ─────────────────────────────────────────────────────────────

async def upload_image(request):
    form = await request.form()
    upload = form.get("file")
    if not upload:
        return JSONResponse({"error": "No file"}, status_code=400)

    img_dir = Path("/tmp/oi-images")
    img_dir.mkdir(exist_ok=True)

    ext = Path(upload.filename).suffix or ".png"
    filename = f"upload_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest = img_dir / filename

    content = await upload.read()
    with open(dest, "wb") as f:
        f.write(content)

    return JSONResponse({"path": str(dest), "filename": filename})


# ── vLLM server controls ──────────────────────────────────────────────────

async def get_vllm_status(request):
    """Return vLLM server state, model, and endpoint info."""
    if not hub_common:
        return JSONResponse({"state": "unknown", "model": None})
    llm_host = hub_common.LLM_HOST
    ip = hub_common.HOSTS.get(llm_host, {}).get("ip", "127.0.0.1")
    port = hub_common.LLM_PORT
    ctx = hub_common.LLM_CONTEXT_WINDOW

    # Check systemd state
    try:
        state_out = hub_common.ssh_cmd(llm_host, 'systemctl is-active vllm-server')
        state = state_out.strip() if state_out else "unknown"
    except Exception:
        state = "unknown"

    # Probe for model name
    model = None
    if state == "active":
        try:
            import urllib.request
            req = urllib.request.urlopen(f"http://{ip}:{port}/v1/models", timeout=5)
            data = json.loads(req.read())
            models = data.get("data", [])
            if models:
                model = models[0].get("id")
        except Exception:
            pass

    return JSONResponse({
        "state": state,
        "model": model,
        "endpoint": f"http://{ip}:{port}",
        "context_window": ctx,
    })


async def vllm_action(request):
    """Start/stop/restart vLLM server. Body: { action: "start"|"stop"|"restart" }"""
    if not hub_common:
        return JSONResponse({"ok": False, "message": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    action = body.get("action", "").strip()
    if action not in ("start", "stop", "restart"):
        return JSONResponse({"ok": False, "message": f"Invalid action: {action}"}, status_code=400)

    llm_host = hub_common.LLM_HOST
    try:
        out = hub_common.ssh_cmd(llm_host, f'sudo systemctl {action} vllm-server', timeout=20)
        return JSONResponse({"ok": True, "message": f"vLLM {action} sent", "output": out or ""})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)})


# ── Dev service toggle ────────────────────────────────────────────────────

async def toggle_dev_service(request):
    """Toggle a dev service enabled state. Body: { project, service, enabled }"""
    if not hub_common:
        return JSONResponse({"ok": False}, status_code=500)
    body = await _json_body(request)
    project_key = body.get("project", "").strip()
    service_name = body.get("service", "").strip()
    enabled = body.get("enabled", True)

    projects, order, ignored = hub_common.load_projects()
    if project_key not in projects:
        return JSONResponse({"ok": False, "error": f"Project not found: {project_key}"}, status_code=404)

    dev_services = projects[project_key].get("dev_services", [])
    found = False
    for svc in dev_services:
        if svc.get("name") == service_name:
            svc["enabled"] = bool(enabled)
            found = True
            break

    if not found:
        return JSONResponse({"ok": False, "error": f"Service not found: {service_name}"}, status_code=404)

    hub_common.save_projects(projects, order, ignored)
    return JSONResponse({"ok": True, "service": service_name, "enabled": bool(enabled)})


# ── Project CRUD ──────────────────────────────────────────────────────────

async def update_project(request):
    """Update project fields. Body: { key, data: { name, tagline } }"""
    if not hub_common:
        return JSONResponse({"ok": False}, status_code=500)
    body = await _json_body(request)
    key = body.get("key", "").strip()
    data = body.get("data", {})

    projects, order, ignored = hub_common.load_projects()
    if key not in projects:
        return JSONResponse({"ok": False, "error": f"Project not found: {key}"}, status_code=404)

    if "name" in data:
        projects[key]["name"] = data["name"]
    if "tagline" in data:
        projects[key]["tagline"] = data["tagline"]

    hub_common.save_projects(projects, order, ignored)
    return JSONResponse({"ok": True})


async def delete_project(request):
    """Delete a project. Body: { key }"""
    if not hub_common:
        return JSONResponse({"ok": False}, status_code=500)
    body = await _json_body(request)
    key = body.get("key", "").strip()

    projects, order, ignored = hub_common.load_projects()
    if key not in projects:
        return JSONResponse({"ok": False, "error": f"Project not found: {key}"}, status_code=404)

    del projects[key]
    if key in order:
        order.remove(key)

    hub_common.save_projects(projects, order, ignored)
    return JSONResponse({"ok": True})


# ── Code Assistant projects ───────────────────────────────────────────────

async def get_ca_projects(request):
    """Fetch indexed projects from Code Assistant."""
    if not hub_common:
        return JSONResponse({"projects": []})
    try:
        data = hub_common.code_assistant_get('projects')
        return JSONResponse({"projects": data if isinstance(data, list) else []})
    except Exception as e:
        return JSONResponse({"projects": [], "error": str(e)})


async def ca_reindex(request):
    """Trigger reindex for a Code Assistant project. Body: { project }"""
    if not hub_common:
        return JSONResponse({"ok": False}, status_code=500)
    body = await _json_body(request)
    project = body.get("project", "").strip()
    if not project:
        return JSONResponse({"ok": False, "error": "Project name required"}, status_code=400)
    try:
        result = hub_common.code_assistant_post(f'projects/{project}/reindex', {}, timeout=300)
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ── Research fetch ────────────────────────────────────────────────────────

async def research_fetch(request):
    """Trigger research --fetch (long-running)."""
    output = _run_tool("research", "--fetch", timeout=300)
    return JSONResponse({"output": output})


# ── App assembly ─────────────────────────────────────────────────────────────

_TAB_ROUTES = ["chat", "status", "projects", "repo", "research", "notify", "help", "settings"]

routes = [
    Route("/", index),
    *[Route(f"/{tab}", index) for tab in _TAB_ROUTES],
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/chat/approve", chat_approve, methods=["POST"]),
    Route("/api/chat/stop", chat_stop, methods=["POST"]),
    Route("/api/magic", magic_command, methods=["POST"]),
    Route("/api/config", get_config),
    Route("/api/session", get_session),
    Route("/api/session/messages", get_messages),
    Route("/api/session/reset", reset_session, methods=["POST"]),
    Route("/api/status", get_status),
    Route("/api/projects", get_projects),
    Route("/api/projects/switch", switch_project, methods=["POST"]),
    Route("/api/repo", get_repo),
    Route("/api/research", get_research),
    Route("/api/notifications", get_notifications),
    Route("/api/notifications/clear", clear_notifications, methods=["POST"]),
    Route("/api/settings", get_settings),
    Route("/api/settings/update", update_settings, methods=["POST"]),
    Route("/api/settings/oi", get_oi_config),
    # Hub config API
    Route("/api/settings/hub", get_hub_config),
    Route("/api/settings/hub/update", update_hub_config, methods=["POST"]),
    Route("/api/settings/backup/probe", probe_backup, methods=["POST"]),
    # Hosts API
    Route("/api/settings/hosts/probe", probe_host, methods=["POST"]),
    Route("/api/settings/hosts/save", save_hosts, methods=["POST"]),
    # Ollama API
    Route("/api/settings/ollama/models", get_ollama_models),
    Route("/api/settings/ollama/probe", probe_ollama, methods=["POST"]),
    # RAG API
    Route("/api/settings/rag", get_rag),
    Route("/api/settings/rag/add", add_rag, methods=["POST"]),
    Route("/api/settings/rag/update", update_rag, methods=["POST"]),
    Route("/api/settings/rag/delete", delete_rag, methods=["POST"]),
    # Code Assistant
    Route("/api/settings/ca/probe", probe_code_assistant, methods=["POST"]),
    Route("/api/ca/projects", get_ca_projects),
    Route("/api/ca/reindex", ca_reindex, methods=["POST"]),
    # vLLM controls
    Route("/api/vllm/status", get_vllm_status),
    Route("/api/vllm/action", vllm_action, methods=["POST"]),
    # Dev service toggle
    Route("/api/projects/dev-toggle", toggle_dev_service, methods=["POST"]),
    # Project CRUD
    Route("/api/projects/update", update_project, methods=["POST"]),
    Route("/api/projects/delete", delete_project, methods=["POST"]),
    # Research fetch
    Route("/api/research/fetch", research_fetch, methods=["POST"]),
    Route("/api/image", upload_image, methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
]

app = Starlette(routes=routes)


# ── Process management ───────────────────────────────────────────────────────

_PID_FILE = Path.home() / '.cache' / 'hub' / 'oi-web.pid'


def _find_running_pid():
    """Return PID of running oi-web server, or None."""
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # check if alive
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        _PID_FILE.unlink(missing_ok=True)
        return None


def _write_pid():
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))


def _stop_server():
    """Stop a running oi-web server."""
    pid = _find_running_pid()
    if pid:
        print(f"Stopping oi-web (pid {pid})")
        import signal as _sig
        os.kill(pid, _sig.SIGTERM)
        _PID_FILE.unlink(missing_ok=True)
        return True
    else:
        print("oi-web is not running")
        return False


def _status():
    """Print server status."""
    pid = _find_running_pid()
    if pid:
        print(f"\033[32m●\033[0m oi-web running (pid {pid}) on port {PORT}")
    else:
        print(f"\033[31m●\033[0m oi-web not running")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import threading
    import uvicorn

    args = sys.argv[1:]

    if '--help' in args or '-h' in args:
        print(f"""oi-web — Open Interpreter WebUI server

Usage:
  oi-web                Start the server (port {PORT})
  oi-web --stop         Stop the running server
  oi-web --restart      Restart the server
  oi-web --status       Show if server is running
  oi-web --port N       Start on a specific port""")
        sys.exit(0)

    if '--status' in args:
        _status()
        sys.exit(0)

    if '--stop' in args:
        _stop_server()
        sys.exit(0)

    if '--restart' in args:
        _stop_server()
        import time as _time
        _time.sleep(1)

    # Parse --port
    for i, a in enumerate(args):
        if a == '--port' and i + 1 < len(args):
            PORT = int(args[i + 1])
            break

    # Check for already running instance
    existing = _find_running_pid()
    if existing:
        print(f"\033[33m!\033[0m oi-web already running (pid {existing}). Use --restart or --stop.")
        sys.exit(1)

    _write_pid()

    print(f"\033[1;36mOI WebUI\033[0m starting on port {PORT}")

    # Pre-init bridge in background
    def _init():
        try:
            get_bridge()
            print(f"\033[32m✓\033[0m Interpreter ready")
        except Exception as e:
            print(f"\033[31m✗\033[0m Interpreter init failed: {e}")
    threading.Thread(target=_init, daemon=True).start()

    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    finally:
        _PID_FILE.unlink(missing_ok=True)
