# Open Interpreter — Multi-Machine Dev Hub

**Manage projects, git, services, and LLM workflows across multiple machines from a single terminal.**

> Fork of [OpenInterpreter/open-interpreter](https://github.com/OpenInterpreter/open-interpreter) v0.4.3 (AGPL-3.0) — for upstream docs see the [original README](https://github.com/OpenInterpreter/open-interpreter/blob/main/README.md).

---

### Why this exists

Multi-machine dev setups mean scattered repos, different SSH configs, services that need coordinating, and context that lives in your head instead of your tools. This hub eliminates that friction — one terminal gives you git dashboards, service management, LLM-powered research, automated backups, and session workflows across every machine you own.

### Origin

Built over 50+ phases while developing [AIquest](https://aiquest.info) on a Jetson Orin Nano + AGX Orin setup. Born on ARM + NVIDIA but designed to be architecture-generic — everything is pure Python, SSH, and config files. If you can run Python 3.10+ and reach your machines over SSH, it works.

### Architecture

```
┌──────────────┐         SSH          ┌──────────────────┐
│   Hub (ARM)  │◄───────────────────► │   GPU Server     │
│              │                      │                  │
│  hub tools   │    ┌────────────┐    │  Ollama (LLM)    │
│  OI / Claude │    │ Workstation│    │  Code Assistant  │
│  cron jobs   │◄──►│            │    │  project repos   │
│  backups     │SSH │ gh CLI     │    │  backup storage  │
└──────────────┘    │ project    │    └──────────────────┘
       ▲            │ repos      │             ▲
       │            └────────────┘             │
       │              SSH ▲                    │
       └──────────────────┴────────────────────┘
                    all linked via
                  ~/.config/hub/config.json
```

---

## What it looks like

**Hub dashboard** — hosts, services, caches at a glance:
```
$ hub --status

  ══════════════════════════════════════════════════════
    HUB STATUS                              Sun 02 Mar
  ══════════════════════════════════════════════════════

  HOSTS
    Hub          127.0.0.1        up 12d   3.2/7.4 GB
    GPU Server   192.168.1.100    up 12d   6.1/30.4 GB  llama3:8b
    Workstation  192.168.1.50     suspended

  BACKGROUND
    autosummary  running (pid 1234)
    research     last fetch 2h ago
    Code Assist  5 projects indexed, watcher active

  CACHES
    myapp 4m        backend 1h     frontend 3h
    website 2d      docs 5h        api-server 1d
```

**Git dashboard** — every project's status in one table:
```
$ repo

  ══════════════════════════════════════════════════════
    GIT STATUS                              Sun 02 Mar
  ══════════════════════════════════════════════════════

  Project       Branch    Dirty  Untrk  Ahead  Remote
  ─────────────────────────────────────────────────────
  myapp         main        2      0      1    ✓ SSH
  backend       main        0      0      0    ✓ SSH
  frontend      main        5      3      0    ✓ SSH
  website       main        0      0      2    ✓ SSH
```

**Session launcher** — one command to wake hosts, warm the LLM, start services, and launch your editor with full context:
```
$ work myapp
  ✓ GPU Server online
  ✓ Ollama warm (llama3:8b loaded)
  ✓ 2 services started
  ✓ Overview cache refreshed
  Launching Claude Code with context preamble...
```

---

## Quick Start

```bash
git clone https://github.com/thehighnotes/open-interpreter.git
cd open-interpreter
pip install -e .
python3 tools/hub/install.py    # interactive setup wizard
```

The wizard detects your hostname, asks about remote hosts, configures Ollama, and creates symlinks for all tools. After setup:

```bash
hub --status          # see your machines
hub --scan gpu        # discover projects on a remote host
repo                  # git dashboard across all projects
work myapp            # full session: wake → warm → launch
```

See the [Getting Started guide](docs/hub/getting-started.md) for configuration details, Ollama profiles, and the full `config.json` schema.

---

## Hub Tools

15 CLI tools + a web interface, all driven by `~/.config/hub/config.json`:

| Tool | Alias | Purpose |
|------|-------|---------|
| `hub` | `hub`, `status` | Dashboard, priorities, services, config |
| `git` | `repo` | Git dashboard, commit, push, checkpoint, deploy |
| `work` | — | One-command session: prepare → overview → begin |
| `overview` | — | LLM-powered project briefings |
| `research` | — | Arxiv + GitHub release monitor |
| `backup` | — | Rsync hub ecosystem to backup target |
| `code` | — | Semantic search, RAG, dependency graphs |
| `edit` | — | Structured remote file editing via SSH |
| `health-probe` | — | Host & service health checker (cron) |
| `notify` | — | Notification history viewer |
| `hubgrep` | — | Cross-ecosystem search |
| `search` | — | DuckDuckGo web search |
| `oi-web` | — | Web UI — chat, hub tabs, image upload |

Full reference: **[Hub Tools documentation](docs/hub/hub-tools.md)**

---

## Web Interface

Browser-based OI with hub integration — 8 tabs (Chat, Status, Projects, Repo, Research, Notify, Help, Settings), streaming markdown, code approval, image upload. Accessible from any device on the network.

![WebUI — Chat tab with sidebar and suggestion chips](docs/hub/images/webui-chat.png)

```bash
oi-web                          # start on port 8585
open http://localhost:8585      # or http://<hub-ip>:8585
```

Full reference: **[WebUI documentation](docs/hub/webui.md)**

---

## OI Integration

30+ magic commands let you operate the hub from inside an OI session:

```
%status          Hub dashboard
%repo            Git dashboard
%switch myapp    Switch project context
%checkpoint      Batch commit+push all dirty projects
%research        Research digest
%image           Send clipboard/file image to vision model
%help            Show all commands
```

Full reference: **[OI Integration documentation](docs/hub/oi-integration.md)**

---

## OI Improvements

Standalone enhancements to Open Interpreter (work without hub tools):

- **Callable auto_run** — pass a function instead of a boolean to approve commands selectively
- **Callable custom_instructions** — dynamic system message that updates each turn (enables RAG injection)
- **Mini-RAG** — lightweight semantic retrieval engine (all-MiniLM-L6-v2, 384-dim)
- **Sudo detection** — intercepts `sudo` commands with a warning
- **Truncation fixes** — large outputs preserve their tail, ANSI stripped cleanly
- **Refresh throttle** — fast streaming output no longer floods the scrollback
- **Rich output panels** — colored diffs, aligned tables, highlighted errors
- **Vendored tokentrim** — fixes a [double-subtraction bug](https://github.com/KillianLucas/tokentrim/issues/11)

Full reference: **[OI Improvements documentation](docs/hub/oi-improvements.md)**

---

## Platform support

| Platform | Status | Notes |
|----------|--------|-------|
| Ubuntu x86_64 | Tested | Primary CI target. Bare metal and WSL2. |
| Jetson (ARM64) | Tested | Built on Orin Nano + AGX Orin. |
| Debian / RPi | Community | Pure Python + SSH — should work. Not CI-tested. |
| macOS | Untested | TUI menus fall back to numbered input. Core tools likely work. |
| Windows | Not supported | Requires WSL2. Uses `termios`, `tty`, Unix signals. |

---

## Documentation

| Page | Content |
|------|---------|
| [Getting Started](docs/hub/getting-started.md) | Install, config schema, Ollama profiles, host roles |
| [Hub Tools](docs/hub/hub-tools.md) | Full reference for all 15 CLI tools |
| [Web Interface](docs/hub/webui.md) | WebUI architecture, tabs, API endpoints, responsive layout |
| [OI Integration](docs/hub/oi-integration.md) | Magic commands — project, git, infra, vision |
| [OI Improvements](docs/hub/oi-improvements.md) | Callable auto_run, custom_instructions, Mini-RAG |
| [Reference](docs/hub/reference.md) | Modified files, new files, environment variables |

---

## License

AGPL-3.0, same as upstream. Modified files are marked with headers. See [LICENSE](LICENSE).
