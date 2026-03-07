# Getting Started

## Requirements

- Python 3.9+ (tested on 3.10)
- [Ollama](https://ollama.com) running on a reachable host (local or remote)
- Linux recommended (Jetson, x86 Ubuntu, WSL2). macOS untested.
- `sentence-transformers` for Mini-RAG (optional but recommended)

## Install

**Option 1 — One-command bootstrap:**

```bash
curl -sL https://raw.githubusercontent.com/thehighnotes/open-interpreter/main/tools/hub/bootstrap.sh | bash
```

This clones the repo, installs OI, and launches the install wizard.

**Option 2 — Manual:**

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
python3 tools/hub/install.py          # hub (full install)
python3 tools/hub/install.py --node   # node (connects to existing hub)
```

The install wizard will:
1. Detect your hostname and username
2. Ask about remote hosts (GPU servers, workstations, etc.)
3. Configure Ollama location and GitHub username
4. Write `~/.config/hub/config.json`
5. Create symlinks from `~/` to `tools/hub/` for each tool
6. Detect your shell (bash/zsh) and set up aliases (`hub`, `repo`, `status`)
7. Generate SSH keys if none exist (`~/.ssh/id_ed25519`)
8. Set up `~/.ssh/config` entries for remote hosts
9. Copy SSH keys to remote hosts via `ssh-copy-id`

## Node Setup

A **node** is a machine that runs OI locally (for native file access and editing) but delegates hub state operations (project registry, session management, status queries) to the hub via SSH. This gives you the performance of local OI with the coordination of a central hub.

**Install:**

```bash
python3 tools/hub/install.py --node
```

The node wizard will:
1. Ask for the hub's IP address and username
2. Establish SSH connectivity (generate keys, `ssh-copy-id`)
3. Sync `config.json` and `projects.json` from the hub
4. Create SSH stub scripts that forward hub commands to the hub machine

**Usage:**

```bash
work <project> --oi    # launch OI session on the node
```

OI runs natively on the node for fast file access, while `hub --status`, `repo`, and session state are forwarded transparently to the hub.

## Updating

Pull the latest changes from origin:

```bash
interpreter --update
```

**Auto-update:** Set `"oi_auto_update": true` in `config.json` to pull automatically on startup.

When auto-update is off, OI checks the remote on launch and prints a notification if your local copy is behind origin.

## Quick Start with Ollama

Create a profile at `~/.config/open-interpreter/profiles/my-profile.py`:

```python
from interpreter import interpreter

interpreter.llm.model = "ollama/llama3:8b"         # any Ollama model
interpreter.llm.api_base = "http://localhost:11434" # Ollama host (local or remote IP)
interpreter.llm.api_key = "unused"
interpreter.llm.context_window = 44000              # num_ctx sent to Ollama
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

**Hub config** (full install):

```json
{
  "oi_auto_update": false,
  "hub": {
    "name": "My Dev Hub",
    "local_host": "nano",
    "role": "hub"
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
      "roles": ["wakeable", "node"], "wol_mac": "AA:BB:CC:DD:EE:FF"
    }
  },
  "ollama": { "host": "gpu", "port": 11434, "default_model": "llama3:8b", "num_ctx": 44000 },
  "code_assistant": { "host": "gpu", "port": 5002 },
  "backup": { "destination": "gpu:~/hub-backup" },
  "git": { "github_username": "myuser", "email": "me@example.com" },
  "session_persist": false
}
```

**Node config** (`--node` install):

```json
{
  "oi_auto_update": false,
  "hub": {
    "name": "My Workstation",
    "local_host": "ws",
    "role": "node",
    "hub_host": "nano"
  },
  "hosts": {
    "ws": {
      "name": "Workstation", "ip": "127.0.0.1", "user": "dev",
      "roles": ["local", "node"]
    },
    "nano": {
      "name": "Hub", "ip": "192.168.1.31", "user": "hubuser",
      "roles": ["hub"]
    }
  },
  "ollama": { "host": "nano", "port": 11434, "default_model": "llama3:8b", "num_ctx": 44000 },
  "git": { "github_username": "myuser", "email": "me@example.com" }
}
```

### Host roles

- `local` — the machine running the hub tools
- `ollama` — runs the Ollama LLM server
- `code_assistant` — runs the Code Assistant (semantic search/RAG)
- `backup_target` — receives rsync backups
- `wakeable` — supports Wake-on-LAN (requires `wol_mac`)
- `node` — a machine with OI installed locally, delegates hub state operations via SSH

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
