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
| **Magic commands** | 12 `%commands` to operate your hub without leaving OI — check status, switch projects, commit code, run backups |
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
| `%switch <name>` | Switch project context — updates the system message so the LLM knows which codebase you're working on. Supports fuzzy matching (`%switch prom` → prometheus). Loads cached project overview for extra context |
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
