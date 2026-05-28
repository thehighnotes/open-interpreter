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
    '3': 'ansi-italic',
    '4': 'ansi-underline',
    '9': 'ansi-strikethrough',
    '22': None,          # normal intensity (reset bold/dim)
    '23': None,          # reset italic
    '24': None,          # reset underline
    '29': None,          # reset strikethrough
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

    exec_mode = body.get("exec_mode")
    bridge = get_bridge()
    return StreamingResponse(
        bridge.chat_stream(message, exec_mode=exec_mode),
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


async def truncate_session(request):
    body = await _json_body(request)
    turn = body.get("turn")
    if not isinstance(turn, int) or turn < 1:
        return JSONResponse({"ok": False, "error": "Valid turn number required"}, status_code=400)
    bridge = get_bridge()
    ok = bridge.truncate(turn)
    if not ok:
        return JSONResponse({"ok": False, "error": "Truncation failed (generation in progress?)"}, status_code=409)
    return JSONResponse({"ok": True})


async def set_exec_mode(request):
    """Set execution mode: ask, safe, or auto."""
    data = await request.json()
    mode = data.get("mode", "safe")
    bridge = get_bridge()
    if bridge.set_exec_mode(mode):
        _save_webui_config({"exec_mode": mode})
        return JSONResponse({"ok": True, "mode": mode})
    return JSONResponse({"ok": False, "error": f"Invalid mode: {mode}"}, status_code=400)


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


async def create_project(request):
    """Create a new project. Body: { key, data: { name, host, path, code_index, tagline } }"""
    if not hub_common:
        return JSONResponse({"ok": False}, status_code=500)
    body = await _json_body(request)
    key = body.get("key", "").strip()
    data = body.get("data", {})

    if not key:
        return JSONResponse({"ok": False, "error": "Missing key"}, status_code=400)

    projects, order, ignored = hub_common.load_projects()
    if key in projects:
        return JSONResponse({"ok": False, "error": "Already exists"}, status_code=409)

    projects[key] = {
        "name": data.get("name", key),
        "host": data.get("host", "ws"),
        "path": data.get("path", ""),
        "code_index": data.get("code_index", ""),
        "tagline": data.get("tagline", ""),
        "claude_md": data.get("claude_md", "CLAUDE.md"),
        "services": [],
        "dev_services": [],
        "related_repos": [],
    }
    order.append(key)
    hub_common.save_projects(projects, order, ignored)
    hub_common.reload_projects()
    return JSONResponse({"ok": True, "key": key})


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


# ── Apps API ──────────────────────────────────────────────────────────────

async def get_apps(request):
    """Return app list with status and tunnel info."""
    if not hub_common:
        return JSONResponse({"apps": [], "tunnels": []})

    app_list = hub_common.get_app_list()

    # Probe hosts + ports in parallel
    hosts_needed = set(a['host'] for a in app_list)
    host_reachable = {}
    for h in hosts_needed:
        alias = hub_common.HOSTS.get(h, {}).get('alias', h)
        host_reachable[h] = hub_common.check_host_reachable(alias) if h != hub_common.LOCAL_HOST else True

    probe_tasks = {}
    for app in app_list:
        if host_reachable.get(app['host']) and app.get('port'):
            alias = hub_common.HOSTS.get(app['host'], {}).get('alias', app['host'])
            key = f"{app['host']}:{app['port']}"
            if key not in probe_tasks:
                probe_tasks[key] = lambda a=alias, p=app['port']: hub_common.probe_service(a, p, timeout=3)

    probe_results = hub_common.run_parallel(probe_tasks) if probe_tasks else {}

    tunnels = hub_common.list_tunnels()
    tunnel_map = {(t['host'], t['remote_port']): t for t in tunnels}

    result = []
    for app in app_list:
        host = app['host']
        port = app.get('port')
        probe_key = f"{host}:{port}" if port else None
        reachable = host_reachable.get(host, False)

        if not reachable:
            status = 'offline'
        elif probe_key and probe_key in probe_results:
            status = 'running' if probe_results[probe_key] else 'stopped'
        else:
            status = 'unknown'

        tun = tunnel_map.get((host, port))
        host_name = hub_common.HOSTS.get(host, {}).get('name', host)

        result.append({
            'project_key': app['project_key'],
            'project_name': app['project_name'],
            'host': host,
            'host_name': host_name,
            'app_name': app['app_name'],
            'type': app['type'],
            'port': port,
            'status': status,
            'source': app['source'],
            'tunnel': {'local_port': tun['local_port']} if tun else None,
        })

    return JSONResponse({
        "apps": result,
        "tunnels": [{"host": t['host'], "remote_port": t['remote_port'],
                      "local_port": t['local_port'], "label": t.get('label', '')}
                     for t in tunnels],
    })


async def app_tunnel(request):
    """Create or remove a tunnel. Body: { host, port, action: "start"|"stop", label }"""
    if not hub_common:
        return JSONResponse({"ok": False, "message": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    host = body.get("host", "").strip()
    port = body.get("port")
    action = body.get("action", "start").strip()
    label = body.get("label", "")

    if not host or not port:
        return JSONResponse({"ok": False, "message": "host and port required"}, status_code=400)

    alias = hub_common.HOSTS.get(host, {}).get('alias', host)

    if action == "stop":
        ok, msg = hub_common.stop_tunnel(alias, int(port))
        return JSONResponse({"ok": ok, "message": msg})
    else:
        ok, local_port, msg = hub_common.start_tunnel(alias, int(port), label=label)
        return JSONResponse({"ok": ok, "local_port": local_port, "message": msg})


async def app_tunnel_cleanup(request):
    """Kill all active tunnels."""
    if not hub_common:
        return JSONResponse({"ok": False}, status_code=500)
    count = hub_common.cleanup_tunnels()
    return JSONResponse({"ok": True, "stopped": count})


async def app_service_action(request):
    """Start or stop a backing service. Body: { project, port, action: "start"|"stop" }"""
    if not hub_common:
        return JSONResponse({"ok": False, "message": "hub_common not available"}, status_code=500)
    body = await _json_body(request)
    project_key = body.get("project", "").strip()
    port = body.get("port")
    action = body.get("action", "start").strip()

    projects, _, _ = hub_common.load_projects()
    proj = projects.get(project_key)
    if not proj:
        return JSONResponse({"ok": False, "message": f"Project not found: {project_key}"}, status_code=404)

    host = proj['host']
    alias = hub_common.HOSTS.get(host, {}).get('alias', host)

    # Find matching service or dev_service
    svc = next((s for s in proj.get('services', []) if s.get('port') == port), None)
    dev_svc = next((d for d in proj.get('dev_services', []) if d.get('port') == port), None)

    if action == "start":
        if dev_svc:
            hub_common.start_dev_service(alias, proj['path'], dev_svc)
        elif svc:
            hub_common.start_service(alias, proj['path'], svc)
        else:
            return JSONResponse({"ok": False, "message": "Service not found"}, status_code=404)
        return JSONResponse({"ok": True, "message": f"Start command sent"})
    elif action == "stop":
        if dev_svc:
            hub_common.stop_dev_service(alias, dev_svc)
        elif svc:
            hub_common.stop_service(alias, svc)
        else:
            return JSONResponse({"ok": False, "message": "Service not found"}, status_code=404)
        return JSONResponse({"ok": True, "message": f"Stop command sent"})

    return JSONResponse({"ok": False, "message": f"Invalid action: {action}"}, status_code=400)


# ── Research fetch ────────────────────────────────────────────────────────

async def research_fetch(request):
    """Trigger research --fetch (long-running)."""
    output = _run_tool("research", "--fetch", timeout=300)
    return JSONResponse({"output": output})


# ── Mail API ─────────────────────────────────────────────────────────────────

async def get_mail_overview(request):
    """GET /api/mail — overview stats + rules + recent actions."""
    try:
        from mail_api import get_overview
        return JSONResponse(get_overview())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def get_mail_rules(request):
    """GET /api/mail/rules — list all rules."""
    from mail_api import load_rules
    return JSONResponse({"rules": load_rules()})


async def add_mail_rule(request):
    """POST /api/mail/rules/add — add a filter rule."""
    body = await _json_body(request)
    rule = body.get("rule")
    if not rule:
        return JSONResponse({"error": "rule required"}, status_code=400)
    from mail_api import add_rule
    created = add_rule(rule)
    return JSONResponse({"ok": True, "rule": created})


async def update_mail_rule(request):
    """POST /api/mail/rules/update — update a rule."""
    body = await _json_body(request)
    rule_id = body.get("id")
    updates = body.get("updates")
    if not rule_id or not updates:
        return JSONResponse({"error": "id and updates required"}, status_code=400)
    from mail_api import update_rule
    return JSONResponse({"ok": update_rule(rule_id, updates)})


async def delete_mail_rule(request):
    """POST /api/mail/rules/delete — delete a rule."""
    body = await _json_body(request)
    rule_id = body.get("id")
    if not rule_id:
        return JSONResponse({"error": "id required"}, status_code=400)
    from mail_api import delete_rule
    return JSONResponse({"ok": delete_rule(rule_id)})


async def mail_scan(request):
    """POST /api/mail/scan — scan inbox with LLM (no actions taken)."""
    try:
        from mail_api import scan
        result = scan()
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def mail_apply(request):
    """POST /api/mail/apply — execute approved actions. {approvals: {msg_id: action}}"""
    body = await _json_body(request)
    approvals = body.get("approvals", {})
    if not approvals:
        return JSONResponse({"error": "approvals required"}, status_code=400)
    try:
        from mail_api import apply_actions
        result = apply_actions(approvals)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def mail_auth_status(request):
    """GET /api/mail/auth/status — check auth state."""
    from mail_api import get_auth_status
    return JSONResponse(get_auth_status())


async def mail_auth_save_creds(request):
    """POST /api/mail/auth/save-creds — save client_id/secret to .env."""
    body = await _json_body(request)
    client_id = body.get("client_id", "").strip()
    client_secret = body.get("client_secret", "").strip()
    if not client_id or not client_secret:
        return JSONResponse({"error": "client_id and client_secret required"}, status_code=400)
    from mail_api import save_credentials
    save_credentials(client_id, client_secret)
    return JSONResponse({"ok": True})


async def mail_auth_start(request):
    """GET /api/mail/auth/start — begin OAuth flow, returns authorize URL."""
    try:
        from mail_api import start_auth_flow
        result = start_auth_flow("https://oi.aiquest.info/auth/gmail/callback")
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def mail_auth_callback(request):
    """GET /auth/gmail/callback — OAuth redirect handler."""
    code = request.query_params.get("code")
    if not code:
        error = request.query_params.get("error", "no authorization code")
        return HTMLResponse(f"<h3>Authorization failed: {error}</h3>")
    state = request.query_params.get("state", "")
    try:
        from mail_api import complete_auth_flow
        complete_auth_flow(code, state)
        return HTMLResponse(
            "<html><body style='font-family:system-ui;text-align:center;padding:60px'>"
            "<h2>Gmail connected!</h2>"
            "<p>You can close this tab and return to the Mail tab.</p>"
            "<script>setTimeout(()=>window.close(),2000)</script>"
            "</body></html>"
        )
    except Exception as e:
        return HTMLResponse(f"<h3>Authorization failed: {e}</h3>")


async def mail_reset(request):
    """POST /api/mail/reset — reset Gmail settings. {scope: 'token'|'all'}"""
    body = await _json_body(request)
    scope = body.get("scope", "token")
    try:
        from mail_api import reset
        reset(scope)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def get_mail_config(request):
    """GET /api/mail/config — get system prompt and config."""
    from mail_api import load_mail_config
    return JSONResponse(load_mail_config())


async def update_mail_config(request):
    """POST /api/mail/config — update settings. null on a prompt resets it to default."""
    body = await _json_body(request)
    from mail_api import save_mail_config, load_mail_config, DEFAULT_CONFIG
    config = load_mail_config()
    for key in ("system_prompt", "suggestion_prompt", "mode", "scope_read", "scope_label", "batch_size"):
        if key in body:
            if body[key] is None and key in DEFAULT_CONFIG:
                config[key] = DEFAULT_CONFIG[key]
            else:
                config[key] = body[key]
    save_mail_config(config)
    return JSONResponse({"ok": True})


async def mail_unsubscribe(request):
    """POST /api/mail/unsubscribe — get unsubscribe link for a sender."""
    body = await _json_body(request)
    email = body.get("email")
    if not email:
        return JSONResponse({"error": "email required"}, status_code=400)
    from mail_api import get_unsubscribe_link
    return JSONResponse(get_unsubscribe_link(email))


async def mail_sender_stats(request):
    """GET /api/mail/sender?email=x — get triage stats for a sender."""
    email = request.query_params.get("email")
    if not email:
        return JSONResponse({"error": "email required"}, status_code=400)
    from mail_api import get_sender_summary
    return JSONResponse(get_sender_summary(email))


async def mail_refresh_advice(request):
    """POST /api/mail/advice/refresh — re-run insights analysis."""
    from mail_api import _load_sender_stats, _refresh_suggestions
    stats = _load_sender_stats()
    if not stats:
        return JSONResponse({"ok": False, "error": "No sender history yet"})
    _refresh_suggestions(stats, [], {})
    from mail_api import get_advice
    return JSONResponse({"ok": True, **get_advice()})


# ── Updates API ──────────────────────────────────────────────────────────────


async def get_updates_overview(request):
    """GET /api/updates — overview stats + scan results + rules."""
    try:
        from updates_api import get_overview
        return JSONResponse(get_overview())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_scan(request):
    """POST /api/updates/scan — scan for updates across dimensions."""
    try:
        body = await _json_body(request)
        dimensions = body.get("dimensions") if body else None
        hosts = body.get("hosts") if body else None
        from updates_api import scan
        result = scan(dimensions=dimensions, hosts=hosts)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_apply(request):
    """POST /api/updates/apply — generate commands for approved updates."""
    try:
        body = await _json_body(request)
        approvals = body.get("approvals", {})
        from updates_api import apply_actions
        result = apply_actions(approvals)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def get_updates_rules(request):
    """GET /api/updates/rules — list all rules."""
    from updates_api import load_rules
    return JSONResponse({"rules": load_rules()})


async def add_updates_rule(request):
    """POST /api/updates/rules/add — add a rule."""
    try:
        body = await _json_body(request)
        from updates_api import add_rule
        rule = add_rule(body.get("rule", {}))
        return JSONResponse({"ok": True, "rule": rule})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def update_updates_rule(request):
    """POST /api/updates/rules/update — update a rule."""
    try:
        body = await _json_body(request)
        from updates_api import update_rule
        ok = update_rule(body.get("id"), body.get("updates", {}))
        return JSONResponse({"ok": ok})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def delete_updates_rule(request):
    """POST /api/updates/rules/delete — delete a rule."""
    try:
        body = await _json_body(request)
        from updates_api import delete_rule
        ok = delete_rule(body.get("id"))
        return JSONResponse({"ok": ok})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def get_updates_config(request):
    """GET /api/updates/config — get update config."""
    from updates_api import load_updates_config
    return JSONResponse(load_updates_config())


async def update_updates_config(request):
    """POST /api/updates/config — update config."""
    try:
        body = await _json_body(request)
        from updates_api import load_updates_config, save_updates_config
        config = load_updates_config()
        config.update(body)
        save_updates_config(config)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_export(request):
    """POST /api/updates/export — export approved updates as shell script."""
    try:
        body = await _json_body(request)
        from updates_api import export_script
        result = export_script(body.get("host"), body.get("item_ids"))
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_refresh_insights(request):
    """POST /api/updates/insights/refresh — re-run cross-reference analysis."""
    try:
        from updates_api import refresh_insights
        result = refresh_insights()
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_analyze(request):
    """POST /api/updates/analyze — trigger LLM analysis per cluster."""
    try:
        body = await _json_body(request)
        cluster_ids = body.get("cluster_ids") if body else None
        from updates_api import analyze_clusters
        result = analyze_clusters(cluster_ids=cluster_ids)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_analyze_status(request):
    """GET /api/updates/analyze/status — poll analysis progress."""
    from updates_api import get_analyze_status
    return JSONResponse(get_analyze_status())


async def updates_enrich(request):
    """POST /api/updates/enrich — on-demand intelligence for a cluster."""
    try:
        body = await _json_body(request)
        cluster_id = body.get("cluster_id")
        if not cluster_id:
            return JSONResponse({"ok": False, "error": "cluster_id required"}, status_code=400)
        from updates_api import enrich_cluster
        result = enrich_cluster(cluster_id)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_deploy(request):
    """POST /api/updates/deploy — deploy update script to target host."""
    try:
        body = await _json_body(request)
        cluster_id = body.get("cluster_id")
        if not cluster_id:
            return JSONResponse({"ok": False, "error": "cluster_id required"}, status_code=400)
        from updates_api import deploy_cluster_script
        excluded = body.get("excluded", [])
        result = deploy_cluster_script(cluster_id, excluded=excluded)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_deploy_bulk(request):
    """POST /api/updates/deploy/bulk — deploy multiple clusters as one script per host."""
    try:
        body = await _json_body(request)
        cluster_ids = body.get("cluster_ids", [])
        if not cluster_ids:
            return JSONResponse({"ok": False, "error": "cluster_ids required"}, status_code=400)
        from updates_api import deploy_bulk
        excluded = body.get("excluded", [])
        result = deploy_bulk(cluster_ids, excluded=excluded)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def updates_ask(request):
    """POST /api/updates/ask — ask a question about a cluster."""
    try:
        body = await _json_body(request)
        cluster_id = body.get("cluster_id")
        question = body.get("question")
        if not cluster_id or not question:
            return JSONResponse({"ok": False, "error": "cluster_id and question required"}, status_code=400)
        from updates_api import ask_cluster
        result = ask_cluster(cluster_id, question)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── App assembly ─────────────────────────────────────────────────────────────

_TAB_ROUTES = ["chat", "status", "projects", "apps", "repo", "research", "notify", "mail", "updates", "help", "settings"]

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
    Route("/api/session/truncate", truncate_session, methods=["POST"]),
    Route("/api/session/exec-mode", set_exec_mode, methods=["POST"]),
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
    Route("/api/projects/create", create_project, methods=["POST"]),
    Route("/api/projects/delete", delete_project, methods=["POST"]),
    # Research fetch
    Route("/api/research/fetch", research_fetch, methods=["POST"]),
    # Mail API
    Route("/api/mail", get_mail_overview),
    Route("/api/mail/rules", get_mail_rules),
    Route("/api/mail/rules/add", add_mail_rule, methods=["POST"]),
    Route("/api/mail/rules/update", update_mail_rule, methods=["POST"]),
    Route("/api/mail/rules/delete", delete_mail_rule, methods=["POST"]),
    Route("/api/mail/scan", mail_scan, methods=["POST"]),
    Route("/api/mail/apply", mail_apply, methods=["POST"]),
    Route("/api/mail/auth/status", mail_auth_status),
    Route("/api/mail/auth/start", mail_auth_start),
    Route("/api/mail/auth/save-creds", mail_auth_save_creds, methods=["POST"]),
    Route("/api/mail/reset", mail_reset, methods=["POST"]),
    Route("/api/mail/config", get_mail_config),
    Route("/api/mail/config", update_mail_config, methods=["POST"]),
    Route("/api/mail/unsubscribe", mail_unsubscribe, methods=["POST"]),
    Route("/api/mail/sender", mail_sender_stats),
    Route("/api/mail/advice/refresh", mail_refresh_advice, methods=["POST"]),
    Route("/auth/gmail/callback", mail_auth_callback),
    # Updates API
    Route("/api/updates", get_updates_overview),
    Route("/api/updates/scan", updates_scan, methods=["POST"]),
    Route("/api/updates/apply", updates_apply, methods=["POST"]),
    Route("/api/updates/rules", get_updates_rules),
    Route("/api/updates/rules/add", add_updates_rule, methods=["POST"]),
    Route("/api/updates/rules/update", update_updates_rule, methods=["POST"]),
    Route("/api/updates/rules/delete", delete_updates_rule, methods=["POST"]),
    Route("/api/updates/config", get_updates_config),
    Route("/api/updates/config", update_updates_config, methods=["POST"]),
    Route("/api/updates/export", updates_export, methods=["POST"]),
    Route("/api/updates/insights/refresh", updates_refresh_insights, methods=["POST"]),
    Route("/api/updates/analyze", updates_analyze, methods=["POST"]),
    Route("/api/updates/analyze/status", updates_analyze_status),
    Route("/api/updates/enrich", updates_enrich, methods=["POST"]),
    Route("/api/updates/deploy", updates_deploy, methods=["POST"]),
    Route("/api/updates/deploy/bulk", updates_deploy_bulk, methods=["POST"]),
    Route("/api/updates/ask", updates_ask, methods=["POST"]),
    # Apps
    Route("/api/apps", get_apps),
    Route("/api/apps/tunnel", app_tunnel, methods=["POST"]),
    Route("/api/apps/tunnel/cleanup", app_tunnel_cleanup, methods=["POST"]),
    Route("/api/apps/service", app_service_action, methods=["POST"]),
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
