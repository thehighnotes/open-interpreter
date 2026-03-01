# Open Interpreter — Jetson Hub Fork

> Fork of [OpenInterpreter/open-interpreter](https://github.com/OpenInterpreter/open-interpreter) v0.4.3 — AGPL-3.0

A modified Open Interpreter for **Jetson** (and other Linux ARM64/x86) devices running local LLMs via [Ollama](https://ollama.com). Designed for multi-machine development hubs where the LLM runs on one device and OI runs on another.

For upstream docs see the [original README](https://github.com/OpenInterpreter/open-interpreter/blob/main/README.md).

---

## What's Different

This fork adds two layers on top of stock OI:

1. **Core OI improvements** — standalone fixes that make OI better regardless of your setup
2. **Hub integration** — tools and commands for managing a multi-machine dev environment from inside OI

### Core Improvements (work standalone)

| Feature | What it does |
|---------|--------------|
| **Callable auto_run** | Pass a function instead of `True/False` — approve/deny each command individually based on its content |
| **Callable custom_instructions** | System message built dynamically at each turn, so you can inject live context (RAG results, project state, etc.) |
| **Sudo detection** | Intercepts `sudo` commands before execution and warns the user instead of running them silently |
| **Truncation fixes** | Large command outputs no longer lose their tail — spillover handling preserves the last N lines, and ANSI escape sequences are stripped cleanly |
| **Refresh throttle** | Fast streaming output (e.g. `pip install` with 200 lines/sec) no longer floods the scrollback buffer. Base and message blocks throttle refreshes to ~10/sec |
| **Rich output panels** | Final command output is rendered with structured Rich panels — diffs get colored, tables get aligned, errors get highlighted |
| **Vendored tokentrim** | Fixes a [double-subtraction bug](https://github.com/KillianLucas/tokentrim/issues/11) that silently loses ~400-600 tokens of usable context window per turn |

### Hub Integration (requires hub tools)

| Feature | What it does |
|---------|--------------|
| **Mini-RAG** | Semantic search over a local knowledge base (your projects, tools, commands) — injects the most relevant entries into each prompt automatically |
| **Magic commands** | 13 `%commands` to operate your hub without leaving OI — check status, switch projects, commit code, run backups |
| **Project switching** | `%switch` changes the LLM's project context (system message, env vars) so it knows which codebase you're working on |
| **Status bar** | Shows context window usage and optional remote host memory usage after each response |

---

## Requirements

- Python 3.9+ (tested on 3.10)
- [Ollama](https://ollama.com) running on a reachable host (local or remote)
- `sentence-transformers` for Mini-RAG (optional but recommended)
- Linux recommended (Jetson, x86 Ubuntu, WSL2). macOS untested.

## Install

```bash
git clone https://github.com/thehighnotes/open-interpreter.git
cd open-interpreter
git checkout hub-integration
pip install -e .

# For Mini-RAG support:
pip install sentence-transformers
```

> **Jetson note:** If `pip install` fails on `poetry-core`, run `pip install poetry-core` first.
> On Jetson devices, prefer `pip3` and ensure you're using the system Python (not a venv) if you want OI available system-wide.

---

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

---

## Callable auto_run

Stock OI's `auto_run` is a boolean — either every command runs automatically, or every command needs approval. This fork lets you pass a **function** that decides per-command:

```python
# In your profile:
def should_auto_run(code: str) -> bool:
    """Approve read-only commands, block everything else."""
    safe_prefixes = ['ls', 'cat', 'echo', 'pwd', 'git status', 'git log', 'git diff']
    return any(code.strip().startswith(p) for p in safe_prefixes)

interpreter.auto_run = should_auto_run
```

Now `ls`, `cat`, and `git status` run instantly, while `rm`, `pip install`, and other commands still ask for confirmation. The function receives the raw code string and returns `True` (auto-run) or `False` (ask user).

---

## Callable custom_instructions

Stock OI's `custom_instructions` is a static string appended to the system message. This fork also accepts a **callable** — a function called at each conversation turn that returns a string:

```python
def build_instructions() -> str:
    """Dynamic system instructions — called before each LLM request."""
    base = "You are a Jetson development assistant."

    # Inject RAG context based on recent conversation
    if rag and rag.is_loaded:
        recent = interpreter.messages[-1]["content"] if interpreter.messages else ""
        hits = rag.query(recent, top_k=3)
        if hits:
            context = rag.format_context(hits)
            base += f"\n\nRelevant context:\n{context}"

    return base

interpreter.custom_instructions = build_instructions
```

This means the LLM always gets fresh, relevant context — not a stale blob of text from when the session started.

---

## Mini-RAG

A lightweight semantic retrieval engine that makes the LLM aware of your specific setup. It loads knowledge entries from a local JSON file, embeds them with [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) (384-dim, ~45MB), and injects the best matches into each prompt.

### Setup

```bash
# Copy the example entries and customize for your setup:
mkdir -p ~/.config/hub
cp rag-entries.example.json ~/.config/hub/rag-entries.json
```

### Entry format

```json
{
  "topic": "ollama api",
  "description": "running LLM inference, Ollama API, chat completions, sending prompts",
  "content": "Use /api/chat not /api/generate. Add format:json for structured output.",
  "source": "hub",
  "category": "ollama"
}
```

- **`description`** — semantic search matches against this field (add synonyms and related phrases)
- **`content`** — the actual text injected into the prompt when matched
- **`topic`** / **`source`** / **`category`** — metadata for organizing and filtering

### Usage in a profile

```python
from interpreter.core.mini_rag import MiniRAG

rag = MiniRAG()
rag.load()  # loads model + embeds all entries (~2s first time)
print(f"RAG: {rag.entry_count} entries, {rag.embedding_dim}d")

# Use with callable custom_instructions (see above)
```

### Custom location

Set `RAG_ENTRIES_PATH` to load entries from a different path:

```bash
export RAG_ENTRIES_PATH=/path/to/my-entries.json
```

---

## Hub Integration & Magic Commands

The magic commands connect OI to an ecosystem of hub tools — small scripts in `~/` that manage projects, services, git repos, backups, and monitoring across multiple machines. They're designed for a setup where:

- **OI runs on one machine** (the "hub" — e.g. a Jetson Nano)
- **Ollama runs on another** (e.g. a Jetson with more GPU memory)
- **Projects live on various hosts** (accessed via SSH)

The magic commands let you operate the entire environment without leaving the OI session.

### Project management

| Command | What it does |
|---------|--------------|
| `%projects` | List all registered projects with host, name, and service count. Reads from `~/.config/hub/projects.json` |
| `%switch <name>` | Switch project context — updates the system message so the LLM knows which codebase you're working on. Supports fuzzy matching (`%switch my` → myapp). Loads cached project overview for extra context |
| `%overview [name]` | Show a project briefing — calls `~/overview` which queries the LLM to summarize project state, recent changes, and next steps |

### Git operations

| Command | What it does |
|---------|--------------|
| `%repo` | Show git dashboard — branch, dirty files, unpushed commits for every registered project |
| `%repo status` | Detailed status for the current project |
| `%repo commit` | Interactive commit for the current project |
| `%repo push` | Push current project |
| `%checkpoint [msg]` | Batch commit+push **all** dirty projects in one shot (with optional message) |

### Infrastructure

| Command | What it does |
|---------|--------------|
| `%status` | Hub dashboard — shows which hosts are up, which services are running, project states |
| `%health` | Health probe results — reads from `~/.cache/health/latest.json` (written by a cron-based health probe) |
| `%services` | Service status across all projects — which ports are listening, which are down |
| `%wake` | Send a Wake-on-LAN packet to bring a suspended machine online |

### Utilities

| Command | What it does |
|---------|--------------|
| `%backup [--dry-run]` | Run the backup script (`~/backup`) — rsyncs hub config to a remote host |
| `%research [--fetch]` | Research digest — shows scored arxiv papers and GitHub releases relevant to your projects |
| `%notify [--all\|--clear]` | Notification history — shows desktop notifications logged during the session |

### Setting up the hub tools

The magic commands expect these scripts in `~/`:

| Script | Purpose |
|--------|---------|
| `~/hub` | Meta-tool — `--status`, `--next`, `--services`, `--explain` |
| `~/git` | Git management — dashboard, commit, push, checkpoint |
| `~/overview` | Project briefings via Ollama |
| `~/research` | Arxiv + GitHub release monitoring |
| `~/backup` | Rsync-based backup |
| `~/notify` | Notification history viewer |
| `~/wol.sh` | Wake-on-LAN script (path configurable via `OI_WOL_SCRIPT` env var) |
| `~/hub_common.py` | Shared module — `HOSTS` dict, `ssh_cmd()`, project registry helpers |

The project registry lives at `~/.config/hub/projects.json`. The health probe writes to `~/.cache/health/latest.json`. Overview cache lives in `~/.cache/overview/`.

Without these tools installed, the magic commands will print a "tool not found" error and return gracefully — the core OI improvements still work fine.

---

## Hub Tools

The `tools/hub/` directory contains a suite of CLI tools that manage a multi-machine development environment. Each tool is a standalone Python script that can be run directly from the terminal or through OI's magic commands.

### Installation

```bash
cd open-interpreter
python3 tools/hub/install.py
```

The install wizard will:
1. Detect your hostname and username
2. Ask about remote hosts (GPU servers, workstations, etc.)
3. Configure Ollama location and GitHub username
4. Write `~/.config/hub/config.json`
5. Create symlinks from `~/` to `tools/hub/` for each tool
6. Set up bash aliases (`hub`, `repo`, `status`)

### Configuration

All hub tools read from two files:

| File | Purpose | Modified by |
|------|---------|-------------|
| `~/.config/hub/config.json` | Infrastructure config (hosts, Ollama, GitHub, backup) | `install.py` only |
| `~/.config/hub/projects.json` | Project registry (names, paths, services, git remotes) | `hub --scan`, `hub --manage` |

#### config.json schema

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

Host roles determine behavior:
- `local` — the machine running the hub tools
- `ollama` — runs the Ollama LLM server
- `code_assistant` — runs the Code Assistant (semantic search/RAG)
- `backup_target` — receives rsync backups
- `wakeable` — supports Wake-on-LAN (requires `wol_mac`)

### Tool Overview

| Tool | Alias | Purpose |
|------|-------|---------|
| `hub` | `hub`, `status` | Meta-tool: dashboard, priorities, services, config, architecture |
| `git` | `repo` | Git management: dashboard, commit, push, checkpoint, deploy |
| `overview` | — | LLM-powered project briefings |
| `prepare` | — | Session setup: wake hosts, warm Ollama, start services |
| `begin` | — | Session bootstrap: build preamble, launch Claude Code or OI |
| `work` | — | One-command workflow: prepare → overview → begin |
| `backup` | — | Rsync hub ecosystem to backup target |
| `research` | — | Monitor arxiv + GitHub releases, score by relevance |
| `health-probe` | — | Probe hosts and services, track transitions |
| `code` | — | Code Assistant client: semantic search, RAG, dependency graphs |
| `notify` | — | Notification history viewer |
| `hubgrep` | — | Search across all hub files (tools, config, cache, memory) |
| `edit` | — | Structured remote file editing via SSH |
| `search` | — | DuckDuckGo web search helper |
| `autosummary` | — | Post-session daemon: journaling, cache refresh |

---

### `hub` — Meta-Tool

The central entry point for the hub ecosystem. Provides a live dashboard, prioritized action list, service management, and interactive project configuration.

#### Quick Reference

```
hub                    Full reference with live system status
hub --status           Compact dashboard (hosts, services, caches, hints)
hub --next             Prioritized action list — what needs attention now
hub --services         Live service status across all projects
hub --dev              Show/toggle npm dev services (interactive TUI)
hub --manage           Edit, delete, or reorder projects (interactive TUI)
hub --scan <host>      Discover new projects on a host
```

#### Dashboard (`--status`)

Shows four sections in a single fast probe (no LLM calls):

1. **HOSTS** — online/offline, uptime, RAM usage, loaded Ollama model
2. **BACKGROUND** — autosummary, research freshness, Code Assistant, enabled dev services
3. **CACHES** — overview cache ages per project (grouped)
4. **Hints** — contextual suggestions (dirty repos, stale caches, offline hosts)

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

#### Priorities (`--next`)

Scans every project and surfaces issues sorted by severity:

- **P1** (red) — Data loss risk: uncommitted changes with no remote, unpushed commits
- **P2** (yellow) — Production out of sync: deployed HEAD differs from git HEAD
- **P3** (yellow) — Degraded: hosts offline, autosummary not running, Code Assistant down

#### Dev Services (`--dev`)

Interactive toggle menu for ephemeral npm dev servers (React, Next.js, Vite, Azure Functions, etc.):

```
$ hub --dev

  Dev Services
  ─────────────────────────────
  [ ] frontend / React          :3000
  [ ] backend / Express         :4000
  [ ] website / Next.js         :3001

  space=toggle  enter=save  q=discard
```

Enabled services are started by `prepare` and stopped by `autosummary` at session end.

#### Scanning (`--scan`)

```bash
hub --scan gpu       # discover projects on GPU server
hub --scan ws        # discover projects on workstation
```

Discovers project directories via SSH, detects `package.json` scripts and framework ports (React→3000, Next→3000, Vite→5173, Azure Functions→7071), and adds them to `projects.json`.

---

### `repo` — Git Management Tool

The `repo` tool (aliased from `~/git`) provides unified git management across all registered projects. It works over SSH, so your projects can live on any configured host.

#### Quick Reference

```
repo                           Dashboard — all projects at a glance
repo status <project>          Detailed status for one project
repo init <project>            git init + .gitignore + first commit
repo commit <project> ["msg"]  Stage all + commit (LLM-generated message if omitted)
repo push <project>            Push to remote
repo pull <project>            Pull from remote
repo log <project>             Recent commit log (last 10)
repo create <project>          Create private GitHub repo + configure SSH remote
repo checkpoint ["msg"]        Batch commit+push ALL dirty projects
repo deploy <project>          Commit + push + restart services + health check
repo fix <project>             Audit and fix git issues (PAT tokens, user config, .gitignore)
```

#### Dashboard

Running `repo` with no arguments shows a compact table of every registered project:

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
  docs          main        0      1      0    ✓ SSH
  api-server    main        0      0      0    ✓ SSH
```

The dashboard probes each host via SSH in parallel (one SSH call per host for all projects on that host), so it returns in ~2 seconds regardless of how many projects you have.

Column meanings:
- **Dirty** — modified tracked files
- **Untrk** — untracked files
- **Ahead** — commits not yet pushed to remote
- **Remote** — `✓ SSH` (secure), `⚠ PAT` (token in URL — run `repo fix`), or `✗ none`

#### Committing

```bash
# Commit with a message you write:
repo commit myapp "Add session expiry logic"

# Let the LLM write the commit message (uses Ollama):
repo commit myapp
```

When you omit the message, `repo` sends the `git diff --staged` output to Ollama and gets back a conventional commit message. The diff is sent via SCP + curl (not shell escaping) to handle large diffs cleanly.

The tool stages all changes (`git add -A`), shows you the diff summary, generates the message, and asks for confirmation before committing.

#### Creating GitHub Repos

```bash
repo create myapp
```

This will:
1. Check the project is already a git repo (run `repo init` first if not)
2. Prompt for a repo name (defaults to the project key)
3. Prompt for a description (optional)
4. Create a **private** GitHub repo via the `gh` CLI (runs on the host that has `gh` authenticated)
5. Set the remote to use **SSH** (`git@github.com:user/repo.git`) — not HTTPS
6. Push the current branch
7. Save the remote to `projects.json`

> **Note:** `gh` CLI must be authenticated on one of your configured hosts. The tool auto-detects which host has it.

#### Batch Checkpoint

```bash
# Interactive — shows what will be committed, asks for confirmation:
repo checkpoint

# With a shared commit message:
repo checkpoint "Weekly sync"

# Skip confirmation (for scripts/cron):
repo checkpoint --yes
```

`checkpoint` finds every project with uncommitted changes or unpushed commits and processes them all in sequence. For each dirty project it stages, generates/uses a commit message, commits, and pushes. Projects that are clean but have unpushed commits just get pushed.

#### Deploy

```bash
repo deploy myapp
```

Deploy is a full release pipeline for a single project:
1. `git add -A` + commit (with LLM-generated message if needed)
2. `git push`
3. Restart the project's registered services (via tmux on the remote host)
4. Wait for services to become healthy (HTTP probe)
5. Update deploy state cache

If any step fails, the pipeline stops and reports the issue.

#### Fix — Audit and Repair

```bash
repo fix myapp
```

Checks for and fixes common git issues:
- **PAT token in remote URL** — rewrites HTTPS remote from `https://token@github.com/...` to `git@github.com:...` (SSH)
- **Wrong user.name / user.email** — sets them to the values from `config.json`
- **Missing .gitignore** — creates one with sensible defaults for the project's tech stack

#### Init

```bash
repo init myapp
```

For projects that aren't git repos yet:
1. `git init`
2. Creates a `.gitignore` (detects Python, Node, Rust, etc.)
3. Sets `user.name` and `user.email` from config
4. `git add -A && git commit -m "Initial commit"`

#### Pre-flight Safety

Every git operation runs pre-flight checks:
- **Index lock** — detects and clears stale `.git/index.lock` files (with confirmation)
- **Large files** — warns about files >10MB before staging
- **Remote auth** — validates SSH remote is accessible before pushing
- **Stderr capture** — git warnings and errors are shown to the user, not swallowed

---

### Session Flow: `work` → `prepare` → `begin` → `autosummary`

These four tools form a session lifecycle. You typically run `work <project>` which chains the others automatically — but each can be used standalone.

#### `work` — One-Command Session Launcher

```
work <project>                  Full flow: prepare → overview → begin
work <project> --continue       Continue previous session
work <project> --dry-run        Run prepare + overview, print preamble, don't launch
work <project> --oi             Launch Open Interpreter instead of Claude Code
```

Runs `prepare`, then `overview`, then shows a 5-second countdown (Ctrl+C to cancel) before launching `begin`. Passes flags through to downstream tools.

#### `prepare` — Session Setup

```
prepare <project>               Wake host, warm Ollama, start services, refresh cache
prepare <project> --no-wake     Skip Wake-on-LAN, just report if host is suspended
```

Four-step flow:
1. **Check host** — SSH probe; sends Wake-on-LAN if the host has the `wakeable` role (waits up to 120s)
2. **Warm Ollama** — background curl to load the model into GPU memory
3. **Start services** — launches persistent services (from `projects.json`) + enabled dev services in tmux
4. **Refresh cache** — runs `overview --refresh`, fetches code pulse from Code Assistant, extracts decisions from session journal

#### `begin` — Session Bootstrap

```
begin <project>                 Launch Claude Code with context preamble
begin <project> --continue      Continue previous session with updated context
begin <project> --dry-run       Print preamble without launching
begin <project> --oi            Launch Open Interpreter instead
```

Gathers context from five sources:
1. Host reachability check
2. Local notes + remote git log
3. Overview cache (populated by `prepare`)
4. Service status (which ports are up)
5. Research digest (recent relevant papers/releases)

Builds a structured preamble (~3000 chars for Claude Code, ~1500 for OI) containing phase, focus, blockers, breakthroughs, trajectory, recent decisions, cross-project context, and Code Assistant API reference. Delivers via `--append-system-prompt`.

#### `autosummary` — Post-Session Daemon

```
autosummary                     Start polling daemon (foreground)
autosummary --interval 30       Custom poll interval in seconds (default: 60)
autosummary --idle 15           Minutes of inactivity before session ends (default: 10)
autosummary --status            Check if daemon is running + recent log
```

Polls transcript mtimes across all projects. When activity stops for the idle threshold:
1. Closes session in the timeline journal
2. Refreshes overview cache (captures what changed)
3. Stops enabled dev services
4. Sends a desktop notification

Typically runs in a tmux window or via cron `@reboot`.

---

### `overview` — Project Briefings

LLM-powered project overviews that extract phase, status, blockers, and focus from CLAUDE.md and git history.

```
overview                        Compact summary of all projects
overview <project>              Deep view of a single project
overview <project> --refresh    Force re-analysis (bypass cache)
overview <project> --raw        Show raw LLM JSON (debug)
```

Deep views include:
- Current phase, status, and active focus
- Blockers and breakthroughs
- Architecture diagrams (loaded from `~/.config/hub/diagrams.json` with ANSI support)
- Recent decisions and cross-project dependencies

Results are cached per-project in `~/.cache/overview/` for fast re-access. The cache is refreshed by `prepare` and `autosummary`.

---

### `research` — Arxiv & GitHub Monitor

Continuous research monitoring that fetches papers and releases, scores them against your projects via Ollama.

```
research                        Show digests for all projects
research <project>              Detailed digest for one project
research --fetch                Fetch + score new items (cron mode)
research --calibrate            Score test items against threshold matrix
research --status               Fetch state, item counts, source health
```

**Sources:**
- **Arxiv** — cs.AS, cs.SD, cs.CL, cs.LG, cs.AI categories
- **GitHub** — releases from TensorRT, Ollama, PyTorch, llama.cpp (configurable)

Items are scored 1-10 for relevance against each project. Threshold is 7 (configurable). Items older than 30 days are pruned. State is deduplicated by arxiv ID.

**Cron:** `0 */6 * * * ~/research --fetch >> ~/.cache/research/research.log 2>&1`

---

### `backup` — Hub Ecosystem Backup

Rsyncs the entire hub ecosystem to a remote host in ~0.5 seconds.

```
backup                          Run full backup
backup --dry-run                Show what would transfer
backup --list                   Full manifest with file sizes
```

**11 backup categories:**

| Category | What's included |
|----------|----------------|
| Hub Tools | All 15 executables + hub_common.py |
| Hub Config | projects.json, config.json, .bashrc, .tmux.conf |
| SSH | config, authorized_keys, key pairs |
| Open Interpreter | Profile + patched library files |
| RustDesk | User-side config |
| XFCE + Display | Panel, keyboard, workspace, devilspie2 configs |
| VNC | xstartup, passwd |
| Tab Completions | Bash completion scripts for all tools |
| Timeline | Session journals (irreplaceable) |
| Claude Code | CLAUDE.md + memory directory |
| Desktop | Setup guide + shortcuts |

System files (EDID, xorg.conf, lightdm.conf, WireGuard) require sudo — `backup` prints the commands for you to run manually.

**Destination:** configured in `config.json` (default: backup target host's `~/hub-backup/`)

---

### `code` — Code Assistant Client

Interactive code exploration and index management for the Code Assistant service (semantic search, RAG, dependency graphs).

```
code                            List indexed projects
code search <project> "query"   Find files matching a concept
code ask <project> "question"   RAG-powered architecture question
code impact <project> "file"    What depends on this file
code graph <project>            Dependency graph overview
code scan <host>                Discover + index new projects on a host
code reindex <project>          Trigger reindex
code manage                     Interactive index management (TUI)
```

#### Search & Ask

```bash
code search myapp "authentication flow"
# → ranked file list with relevance scores

code ask myapp "how does the session store work?"
# → LLM-generated answer with source citations
```

`search` returns matching files by semantic similarity. `ask` feeds the matches to the LLM and returns a synthesized answer with citations.

#### Impact & Graph

```bash
code impact myapp "core/auth.py"
# → files that import/depend on auth.py

code graph myapp
# → dependency graph stats (nodes, edges, clusters)
```

#### Managing Indexes

`code manage` opens an interactive TUI (arrow keys to navigate, enter to reindex, s to sync+reindex, w to toggle file watcher). Falls back to numbered input when not running in a terminal (e.g. inside OI).

`code scan <host>` discovers project directories via SSH, detects file types (Python, JS/TS, HTML, CSS, Vue), and adds them to the Code Assistant index.

---

### `edit` — Structured File Editing

Structured find-and-replace editing for local and remote files. Designed for use by OI in project mode but works standalone.

```
edit <file> --find "old" --replace "new"      Unique string replacement
edit <file> --after "anchor" --insert "text"   Insert after anchor line
edit <file> --line N --replace "text"          Replace specific line
edit <file> --append "text"                    Append to end
edit <file> --write-from /tmp/file.txt         Overwrite from temp file
edit <file> --patch /tmp/edit.json             Apply batch edits from JSON
edit <file> --show [N]                         Show file with line numbers
edit <file> --show N-M                         Show line range
edit <file> --map                              Structural skeleton (class/function defs)
edit <file> --create                           Create empty file (with parent dirs)
```

**Remote files:** use `host:path` syntax — `edit gpu:~/projects/myapp/main.py --show`

**Safety:**
- `--find` requires exactly 1 match (fails on 0 or 2+ matches with closest-line hints)
- Atomic writes (temp file + rename)
- `.bak` created on first edit per process
- Colored unified diff shown after every edit

**JSON patch format** (one operation per line):
```json
{"find": "old_text", "replace": "new_text"}
{"after": "anchor_line", "insert": "new_line"}
{"line": 42, "replace": "new content"}
```

**Structural map** detects language-specific patterns for Python, JS/TS, Go, Rust, Ruby, C/C++, HTML, CSS, Markdown, YAML, JSON, Dockerfile, and Makefile.

---

### `health-probe` — Host & Service Monitor

Lightweight health checker that probes hosts and services, tracks state transitions, and fires desktop notifications on changes.

```
health-probe                    Run probe (designed for cron)
health-probe --show             Print current state without re-probing
```

**What it probes:**
1. All remote hosts (SSH reachability)
2. Ollama service (port check)
3. Code Assistant (API endpoint)
4. Autosummary daemon (process check)

Writes state to `~/.cache/health/latest.json`. Compares with previous state and sends a desktop notification when something goes UP or DOWN.

**Cron:** `*/15 * * * * ~/health-probe`

---

### `notify` — Notification History

Review desktop notifications logged by hub tools (health transitions, session events, backup results, research scores).

```
notify                          Show unread notifications
notify --all                    Show all (last 7 days)
notify --clear                  Mark all as read
notify --count                  Print unread count (integer, for dashboards)
```

Output is grouped by day, color-coded by source (health, session, backup, research), with relative timestamps. Unread items are marked with a yellow asterisk.

Notifications are generated by `hub_notify()` calls throughout the ecosystem and stored in `~/.cache/hub/notifications.jsonl`.

---

### `hubgrep` — Cross-Ecosystem Search

Search across all hub files — tools, config, cache, memory, shell config.

```
hubgrep <pattern>               Search hub ecosystem
hubgrep --all <pattern>         Also search OI patches + installed interpreter
```

**Search scope:**
- Hub tools (all executables + hub_common.py)
- Config (`~/.config/hub/` files)
- Cache (overview, research, notes, timeline, decisions, code, deploy, health, OI sessions)
- Claude memory (CLAUDE.md + memory directories)
- Shell & config (.bashrc, .tmux.conf, SSH config)
- Tab completions

Results are grouped by category with highlighted matches. Max 5 matches shown per file.

---

### `search` — Web Search

DuckDuckGo search wrapper that returns the top 5 results as plain text. Designed for use inside OI sessions.

```
search "query"
```

Requires the `ddgs` Python package (`pip install ddgs`).

---

## Other Built-in Commands

These are OI's original commands plus a few additions:

| Command | Description |
|---------|-------------|
| `%help` | Show all available commands |
| `%reset` | Clear conversation history |
| `%undo` | Remove last exchange |
| `%view` | Open last truncated output in a pager |
| `%model [name]` | Show or switch LLM model |
| `%context [setting]` | Show or adjust context settings |
| `%allow <pattern>` | Add a command prefix to persistent auto-run list |
| `%deny <n\|all>` | Remove an auto-run pattern |
| `%permissions` | Show auto-run permission list |
| `%auto-edit` | Auto-apply `~~~edit` blocks without confirmation |
| `%confirm-edit` | Require confirmation for `~~~edit` blocks (default) |

---

## File Reference

### Modified files

| File | Changes |
|------|---------|
| `interpreter/core/core.py` | Callable auto_run, callable custom_instructions |
| `interpreter/core/respond.py` | Callable custom_instructions support |
| `interpreter/core/llm/llm.py` | Vendored tokentrim import |
| `interpreter/core/utils/truncate_output.py` | Spillover handling + ANSI strip |
| `interpreter/core/computer/terminal/languages/subprocess_language.py` | Sudo detection |
| `interpreter/terminal_interface/terminal_interface.py` | Auto-run flow, state indicators, configurable inference host probe |
| `interpreter/terminal_interface/magic_commands.py` | Hub magic commands |
| `interpreter/terminal_interface/components/code_block.py` | Rich output integration |
| `interpreter/terminal_interface/components/base_block.py` | Refresh throttle |
| `interpreter/terminal_interface/components/message_block.py` | Refresh throttle |

### New files

| File | Description |
|------|-------------|
| `interpreter/core/mini_rag.py` | RAG engine — loads entries from external JSON, embeds with all-MiniLM-L6-v2 |
| `interpreter/terminal_interface/rich_output.py` | Rich diff panels + structured output renderer |
| `interpreter/vendor/tokentrim/` | Vendored tokentrim with [double-subtraction fix](https://github.com/KillianLucas/tokentrim/issues/11) |
| `rag-entries.example.json` | Sample RAG entries to get started |
| `tools/hub/` | Hub tools ecosystem — 15 scripts + shared module, config templates, install wizard |
| `tools/hub/hub_common.py` | Shared module — config loading, SSH helpers, project registry, terminal UI primitives |
| `tools/hub/install.py` | Interactive setup wizard — generates config.json and creates symlinks |
| `tools/hub/config.example.json` | Minimal single-host config template |
| `tools/hub/profiles/hub-profile.example.py` | OI profile template that reads from config.json |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_ENTRIES_PATH` | `~/.config/hub/rag-entries.json` | Path to RAG knowledge base |
| `OI_INFERENCE_HOST` | *(none)* | SSH alias for remote inference host (shows memory usage in status bar) |
| `OI_WOL_SCRIPT` | `~/wol.sh` | Script called by `%wake` |
| `OI_PROJECT` | *(none)* | Current project key (set by `%switch`) |

---

## License

AGPL-3.0, same as upstream. Modified files are marked with headers. See [LICENSE](LICENSE).
