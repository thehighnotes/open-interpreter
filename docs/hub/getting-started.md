# Getting Started

## Requirements

- Python 3.9+ (tested on 3.10)
- [Ollama](https://ollama.com) running on a reachable host (local or remote)
- Linux recommended (Jetson, x86 Ubuntu, WSL2). macOS untested.
- `sentence-transformers` for Mini-RAG (optional but recommended)

## Install

```bash
git clone https://github.com/thehighnotes/open-interpreter.git
cd open-interpreter
pip install -e .

# For Mini-RAG support:
pip install sentence-transformers
```

> **Jetson note:** If `pip install` fails on `poetry-core`, run `pip install poetry-core` first.
> On Jetson devices, prefer `pip3` and ensure you're using the system Python (not a venv) if you want OI available system-wide.

## Hub tools setup

```bash
python3 tools/hub/install.py
```

The install wizard will:
1. Detect your hostname and username
2. Ask about remote hosts (GPU servers, workstations, etc.)
3. Configure Ollama location and GitHub username
4. Write `~/.config/hub/config.json`
5. Create symlinks from `~/` to `tools/hub/` for each tool
6. Set up bash aliases (`hub`, `repo`, `status`)

## Quick Start with Ollama

Create a profile at `~/.config/open-interpreter/profiles/my-profile.py`:

```python
from interpreter import interpreter

interpreter.llm.model = "ollama/llama3:8b"         # any Ollama model
interpreter.llm.api_base = "http://localhost:11434" # Ollama host (local or remote IP)
interpreter.llm.api_key = "unused"
interpreter.llm.context_window = 8000
interpreter.llm.max_tokens = 1000
interpreter.llm.supports_functions = False
interpreter.llm.supports_vision = False
interpreter.offline = True
```

Launch with:

```bash
interpreter --profile my-profile.py
```

For remote Ollama (running on a separate machine), change `api_base` to `http://<ollama-host>:11434`.

## Configuration

All hub tools read from two files:

| File | Purpose | Modified by |
|------|---------|-------------|
| `~/.config/hub/config.json` | Infrastructure config (hosts, Ollama, GitHub, backup) | `install.py` only |
| `~/.config/hub/projects.json` | Project registry (names, paths, services, git remotes) | `hub --scan`, `hub --manage` |

### config.json schema

```json
{
  "hub": {
    "name": "My Dev Hub",
    "local_host": "nano"
  },
  "hosts": {
    "nano": {
      "name": "Hub", "ip": "127.0.0.1", "user": "myuser",
      "roles": ["local"]
    },
    "gpu": {
      "name": "GPU Server", "ip": "192.168.1.100", "user": "mluser",
      "roles": ["ollama", "code_assistant", "backup_target"]
    },
    "ws": {
      "name": "Workstation", "ip": "192.168.1.50", "user": "dev",
      "roles": ["wakeable"], "wol_mac": "AA:BB:CC:DD:EE:FF"
    }
  },
  "ollama": { "host": "gpu", "port": 11434, "default_model": "llama3:8b" },
  "code_assistant": { "host": "gpu", "port": 5002 },
  "backup": { "destination": "gpu:~/hub-backup" },
  "git": { "github_username": "myuser", "email": "me@example.com" }
}
```

### Host roles

- `local` — the machine running the hub tools
- `ollama` — runs the Ollama LLM server
- `code_assistant` — runs the Code Assistant (semantic search/RAG)
- `backup_target` — receives rsync backups
- `wakeable` — supports Wake-on-LAN (requires `wol_mac`)

## Platform support

| Platform | Status | Notes |
|----------|--------|-------|
| Ubuntu x86_64 | Tested | Primary CI target. Bare metal and WSL2. |
| Jetson (ARM64) | Tested | Built on Orin Nano + AGX Orin. |
| Debian / RPi | Community | Pure Python + SSH — should work. Not CI-tested. |
| macOS | Untested | TUI menus fall back to numbered input. Core tools likely work. |
| Windows | Not supported | Requires WSL2. Uses `termios`, `tty`, Unix signals. |

## First steps after install

After running `install.py`, try these commands in order:

1. **`hub --status`** — verify your hosts are reachable and services are detected
2. **`hub --scan <host>`** — discover projects on remote machines and add them to the registry
3. **`repo`** — see git status across all registered projects
4. **`work <project>`** — launch a full session (wake hosts, warm LLM, start services, open editor)

From inside an OI session, type `%help` to see all magic commands.

If something isn't working, see [Troubleshooting](troubleshooting.md).
