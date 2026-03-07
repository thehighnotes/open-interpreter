# Hub Tools Reference

The `tools/hub/` directory contains a suite of CLI tools that manage a multi-machine development environment. Each tool is a standalone Python script that can be run directly from the terminal or through OI's [magic commands](oi-integration.md).

| Tool | Alias | Purpose |
|------|-------|---------|
| `hub` | `hub`, `status` | Meta-tool: dashboard, priorities, services, config, architecture |
| `git` | `repo` | Git management: dashboard, commit, push, checkpoint, deploy |
| `overview` | — | LLM-powered project briefings |
| `prepare` | — | Session setup: wake hosts, warm Ollama, start services |
| `begin` | — | Session bootstrap: build preamble, launch Claude Code, OI, or OI on remote node |
| `work` | — | One-command workflow: prepare → overview → begin (hub or node) |
| `backup` | — | Rsync hub ecosystem to backup target |
| `research` | — | Monitor arxiv + GitHub releases, score by relevance |
| `health-probe` | — | Probe hosts and services, track transitions |
| `code` | — | Code Assistant client: semantic search, RAG, dependency graphs |
| `notify` | — | Notification history viewer |
| `hubgrep` | — | Search across all hub files (tools, config, cache, memory) |
| `edit` | — | Structured remote file editing via SSH |
| `search` | — | DuckDuckGo web search helper |
| `oi-web` | — | Web UI for OI — chat, hub tabs, image upload (port 8585) |
| `autosummary` | — | Post-session daemon: journaling, cache refresh |

---

## `hub` — Meta-Tool

The central entry point for the hub ecosystem. Provides a live dashboard, prioritized action list, service management, and interactive project configuration.

### Quick Reference

```
hub                    Full reference with live system status
hub --status           Compact dashboard (hosts, services, caches, hints)
hub --next             Prioritized action list — what needs attention now
hub --services         Live service status across all projects
hub --dev              Show/toggle npm dev services (interactive TUI)
hub --manage           Edit, delete, or reorder projects (interactive TUI)
hub --scan <host>      Discover new projects on a host
```

### Dashboard (`--status`)

Shows four sections in a single fast probe (no LLM calls):

1. **HOSTS** — online/offline, uptime, RAM usage, loaded Ollama model
2. **BACKGROUND** — autosummary, research freshness, Code Assistant, enabled dev services
3. **CACHES** — overview cache ages per project (grouped)
4. **Hints** — contextual suggestions (dirty repos, stale caches, offline hosts)

### Priorities (`--next`)

Scans every project and surfaces issues sorted by severity:

- **P1** (red) — Data loss risk: uncommitted changes with no remote, unpushed commits
- **P2** (yellow) — Production out of sync: deployed HEAD differs from git HEAD
- **P3** (yellow) — Degraded: hosts offline, autosummary not running, Code Assistant down

### Dev Services (`--dev`)

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

### Scanning (`--scan`)

```bash
hub --scan gpu       # discover projects on GPU server
hub --scan ws        # discover projects on workstation
```

Discovers project directories via SSH, detects `package.json` scripts and framework ports (React→3000, Next→3000, Vite→5173, Azure Functions→7071), and adds them to `projects.json`.

---

## `repo` — Git Management Tool

The `repo` tool (aliased from `~/git`) provides unified git management across all registered projects. It works over SSH, so your projects can live on any configured host.

### Quick Reference

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

### Dashboard

Running `repo` with no arguments shows a compact table of every registered project. The dashboard probes each host via SSH in parallel (one SSH call per host for all projects on that host), so it returns in ~2 seconds regardless of how many projects you have.

Column meanings:
- **Dirty** — modified tracked files
- **Untrk** — untracked files
- **Ahead** — commits not yet pushed to remote
- **Remote** — `✓ SSH` (secure), `⚠ PAT` (token in URL — run `repo fix`), or `✗ none`

### Committing

```bash
# Commit with a message you write:
repo commit myapp "Add session expiry logic"

# Let the LLM write the commit message (uses Ollama):
repo commit myapp
```

When you omit the message, `repo` sends the `git diff --staged` output to Ollama and gets back a conventional commit message. The diff is sent via SCP + curl (not shell escaping) to handle large diffs cleanly.

The tool stages all changes (`git add -A`), shows you the diff summary, generates the message, and asks for confirmation before committing.

### Creating GitHub Repos

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

### Batch Checkpoint

```bash
# Interactive — shows what will be committed, asks for confirmation:
repo checkpoint

# With a shared commit message:
repo checkpoint "Weekly sync"

# Skip confirmation (for scripts/cron):
repo checkpoint --yes
```

`checkpoint` finds every project with uncommitted changes or unpushed commits and processes them all in sequence. For each dirty project it stages, generates/uses a commit message, commits, and pushes. Projects that are clean but have unpushed commits just get pushed.

### Deploy

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

### Fix — Audit and Repair

```bash
repo fix myapp
```

Checks for and fixes common git issues:
- **PAT token in remote URL** — rewrites HTTPS remote from `https://token@github.com/...` to `git@github.com:...` (SSH)
- **Wrong user.name / user.email** — sets them to the values from `config.json`
- **Missing .gitignore** — creates one with sensible defaults for the project's tech stack

### Init

```bash
repo init myapp
```

For projects that aren't git repos yet:
1. `git init`
2. Creates a `.gitignore` (detects Python, Node, Rust, etc.)
3. Sets `user.name` and `user.email` from config
4. `git add -A && git commit -m "Initial commit"`

### Pre-flight Safety

Every git operation runs pre-flight checks:
- **Index lock** — detects and clears stale `.git/index.lock` files (with confirmation)
- **Large files** — warns about files >10MB before staging
- **Remote auth** — validates SSH remote is accessible before pushing
- **Stderr capture** — git warnings and errors are shown to the user, not swallowed

---

## Session Flow: `work` → `prepare` → `begin` → `autosummary`

These four tools form a session lifecycle. You typically run `work <project>` which chains the others automatically — but each can be used standalone.

### `work` — One-Command Session Launcher

```
work <project>                  Full flow: prepare → overview → begin
work <project> --continue       Continue previous session
work <project> --dry-run        Run prepare + overview, print preamble, don't launch
work <project> --oi             Launch Open Interpreter instead of Claude Code
```

Runs `prepare`, then `overview`, then shows a 5-second countdown (Ctrl+C to cancel) before launching `begin`. Passes flags through to downstream tools.

**Node behavior:** When run on a machine with `role: "node"` in `config.json`, `work` delegates `prepare` and `overview` to the hub via SSH (since the hub owns caches, services, and Ollama access). The OI session itself launches locally on the node, giving it native file access to the project directory without SSH round-trips.

### `prepare` — Session Setup

```
prepare <project>               Wake host, warm Ollama, start services, refresh cache
prepare <project> --no-wake     Skip Wake-on-LAN, just report if host is suspended
```

Four-step flow:
1. **Check host** — SSH probe; sends Wake-on-LAN if the host has the `wakeable` role (waits up to 120s)
2. **Warm Ollama** — background curl to load the model into GPU memory
3. **Start services** — launches persistent services (from `projects.json`) + enabled dev services in tmux
4. **Refresh cache** — runs `overview --refresh`, fetches code pulse from Code Assistant, extracts decisions from session journal, extracts repo references for the research tool

### `begin` — Session Bootstrap

```
begin <project>                 Launch Claude Code with context preamble
begin <project> --continue      Continue previous session with updated context
begin <project> --dry-run       Print preamble without launching
begin <project> --oi            Launch Open Interpreter instead
begin <project> --preamble-only Output raw preamble text to stdout (no UI chrome)
```

**`--preamble-only`:** Outputs the raw preamble text to stdout without any UI chrome (no banners, countdowns, or color), and records the session start in the timeline. This is used internally by nodes to fetch context from the hub over SSH.

**Hub behavior with `--oi`:** If the project's host is a remote machine, `begin --oi` SSHes into that host and launches OI there, so it has native file access to the project directory.

**Node behavior:** On a node, `begin` calls `begin --preamble-only` on the hub via SSH to fetch the context preamble, then launches OI locally. This gives the session native file access to the project on the node while still pulling centralized context (overview cache, research digest, service status) from the hub.

Gathers context from five sources:
1. Host reachability check
2. Local notes + remote git log
3. Overview cache (populated by `prepare`)
4. Service status (which ports are up)
5. Research digest (recent relevant papers/releases)

Builds a structured preamble (~3000 chars for Claude Code, ~1500 for OI) containing phase, focus, blockers, breakthroughs, trajectory, recent decisions, cross-project context, and Code Assistant API reference. Delivers via `--append-system-prompt`.

### `autosummary` — Post-Session Daemon

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

## `overview` — Project Briefings

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

## `research` — Arxiv & GitHub Monitor

Continuous research monitoring that fetches papers and releases, scores them against your projects via Ollama. Automatically discovers relevant repos from your development sessions.

```
research                        Show digests for all projects
research <project>              Detailed digest for one project
research --fetch                Fetch + score new items (cron mode)
research --calibrate            Score test items against threshold matrix
research --status               Fetch state, item counts, source health
```

### Sources

Two layers of GitHub monitoring plus arxiv:

- **Arxiv** — cs.AS, cs.SD, cs.CL, cs.LG, cs.AI categories, keyword-filtered
- **Global GitHub repos** — TensorRT, Ollama, PyTorch, llama.cpp (configurable in `config.json`)
- **Per-project GitHub repos** — tracked in `projects.json` under `related_repos` per project

Per-project repos are discovered automatically: when `prepare` runs at the start of a session, it scans the previous session's transcript for GitHub URLs, `git clone` commands, `pip install git+` references, and notable package names. New repos are merged into `projects.json` and picked up by the next `research --fetch`. You can also add them manually by editing `projects.json`.

Repos shared across projects (e.g. `tiangolo/fastapi` used by three projects) are fetched once but scored for all owning projects.

### Scoring

Items are scored 0-10 for relevance against each project simultaneously via a single Ollama call per item:

| Score | Meaning |
|-------|---------|
| 0 | Completely unrelated |
| 3 | Tangentially related (same broad field) |
| 5 | Loosely related (shares some technology) |
| 7 | Relevant (addresses a technology the project uses) |
| 8 | Highly relevant (directly applicable to current work) |
| 10 | Critical (solves an active blocker or is a direct dependency update) |

The scoring prompt enforces strict rules: items only score ≥6 if they directly mention a project's technology, address a listed blocker, or are a tracked dependency release. Items from per-project repos include a hint to the model identifying which projects track that dependency.

Each score includes:
- **reason** — brief justification for the score
- **actionable** — how the item could be applied (for scores ≥7)

### Display

The **compact digest** (`research`) shows the top 3 papers and 2 releases per project with score, age, and title.

The **detailed view** (`research <project>`) adds URLs, reasons, and actionable insights highlighted in cyan:

```
  [10] ↑ ratatui/ratatui ratatui-v0.30.0
       https://github.com/ratatui/ratatui/releases/tag/v0.30.0  (12d ago)
       Direct dependency — ratatui is the TUI framework for LearnLocal
       → Update ratatui dependency to v0.30.0 for new widget APIs
```

### Status

`research --status` shows fetch timestamps, cache stats, age distribution, per-project digest summaries, per-project tracked repos, and Ollama health.

### Configuration

- **Threshold:** `config.json` → `research.threshold` (default: 7)
- **Arxiv categories:** `config.json` → `research.arxiv_categories`
- **Arxiv keywords:** `config.json` → `research.arxiv_keywords`
- **Global repos:** `config.json` → `research.github_repos`
- **Per-project repos:** `projects.json` → `<project>.related_repos`
- **Pruning:** items older than 30 days are removed on each fetch

**Cron:** `0 */6 * * * ~/research --fetch >> ~/.cache/research/research.log 2>&1`

---

## `backup` — Hub Ecosystem Backup

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

## `code` — Code Assistant Client

The `code` tool talks to the Code Assistant service, which runs on the host with the `code_assistant` role (typically your GPU server). The service provides semantic indexing and dependency analysis across your projects. It's started automatically by `prepare` or can be checked with `hub --status` (BACKGROUND section). The endpoint is configured in `config.json` under `code_assistant` — default is `http://<host>:5002/api/...`.

Interactive code exploration and index management:

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

### Search & Ask

```bash
code search myapp "authentication flow"
# → ranked file list with relevance scores

code ask myapp "how does the session store work?"
# → LLM-generated answer with source citations
```

`search` returns matching files by semantic similarity. `ask` feeds the matches to the LLM and returns a synthesized answer with citations.

### Impact & Graph

```bash
code impact myapp "core/auth.py"
# → files that import/depend on auth.py

code graph myapp
# → dependency graph stats (nodes, edges, clusters)
```

### Managing Indexes

`code manage` opens an interactive TUI (arrow keys to navigate, enter to reindex, s to sync+reindex, w to toggle file watcher). Falls back to numbered input when not running in a terminal (e.g. inside OI).

`code scan <host>` discovers project directories via SSH, detects file types (Python, JS/TS, HTML, CSS, Vue), and adds them to the Code Assistant index.

---

## `edit` — Structured File Editing

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

**On a node:** Since OI runs locally with native file access, no `host:` prefix is needed. OI just uses `~/edit path/to/file` with a plain local path.

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

## `health-probe` — Host & Service Monitor

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

## `notify` — Notification History

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

## `hubgrep` — Cross-Ecosystem Search

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

## `search` — Web Search

DuckDuckGo search wrapper that returns the top 5 results as plain text. Designed for use inside OI sessions.

```
search "query"
```

Requires the `ddgs` Python package (`pip install ddgs`).
