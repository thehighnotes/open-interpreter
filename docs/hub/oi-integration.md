# OI Integration — Magic Commands

The hub tools connect to Open Interpreter through **magic commands** — type them directly in an OI session to operate your hub without leaving the conversation. Without the hub tools installed, the magic commands print a "tool not found" error and return gracefully — the core OI improvements still work fine.

## Typical session

```
%switch myapp          → switch context to your project
%status                → check which hosts are up
%repo                  → see if anything needs committing
  ... do some coding ...
%repo commit           → commit current project (LLM writes the message)
%checkpoint            → batch commit+push everything dirty
%research              → check if any relevant papers dropped overnight
```

Commands chain naturally — `%switch` loads project context into the system message, so the LLM knows your codebase when you ask questions. `%repo commit` uses the same LLM to generate commit messages from your diff.

## Project Management

| Command | What it does |
|---------|--------------|
| `%projects` | List all registered projects with host, name, and service count |
| `%switch <name>` | Switch project context — updates the system message so the LLM knows which codebase you're working on. Supports fuzzy matching (`%switch my` → myapp). Loads cached project overview for extra context |
| `%overview [name]` | Show a project briefing — calls `~/overview` which queries the LLM to summarize project state, recent changes, and next steps |

## Git Operations

| Command | What it does |
|---------|--------------|
| `%repo` | Show git dashboard — branch, dirty files, unpushed commits for every registered project |
| `%repo status` | Detailed status for the current project |
| `%repo commit` | Interactive commit for the current project |
| `%repo push` | Push current project |
| `%checkpoint [msg]` | Batch commit+push **all** dirty projects in one shot (with optional message) |

## Infrastructure

| Command | What it does |
|---------|--------------|
| `%status` | Hub dashboard — shows which hosts are up, which services are running, project states |
| `%health` | Health probe results — reads from `~/.cache/health/latest.json` |
| `%services` | Service status across all projects — which ports are listening, which are down |
| `%wake` | Send a Wake-on-LAN packet to bring a suspended machine online |

## Utilities

| Command | What it does |
|---------|--------------|
| `%backup [--dry-run]` | Run the backup script — rsyncs hub config to a remote host |
| `%research [--fetch]` | Research digest — shows scored arxiv papers and GitHub releases relevant to your projects |
| `%notify [--all\|--clear]` | Notification history — shows desktop notifications logged during the session |

## Vision

| Command | What it does |
|---------|--------------|
| `%image` | Send the clipboard image to the LLM (grabs via xclip, saves to `/tmp/oi-images/`) |
| `%image <prompt>` | Send clipboard image with a text prompt |
| `%image /path/to/file.png` | Send a specific image file |
| `%image /a.png /b.png <prompt>` | Send multiple images with a prompt |

Requires a vision-capable model and `xclip` installed for clipboard mode. Images must be at least 32x32 pixels. In the WebUI, use the image upload button instead — it handles the `%image` message automatically.

## Node Mode

When OI runs on a node (via `work <project> --oi`), files are **local** — no `host:` prefix needed for `~/edit`. Hub tools that need state (overview, research, timeline) are available as SSH stubs that delegate to the hub automatically.

In the OI system message, you'll see `PROJECT MODE: myproject on local:/path` instead of `on host:/path`, confirming local file access.

## Context Injection

When launching with `--oi`, `begin` enriches the OI session with project context at three levels:

1. **System message** (always present): session preamble (phase, focus, blockers, commits) + project directory tree (depth 2) + distilled project reference (~2.5K chars, structured)
2. **Project mode block** (appended to system message): tells the LLM the project name, host, file paths, and how to access the reference file
3. **Reference file** (`/tmp/_oi_reference.txt`): distilled CLAUDE.md available for re-reading via `~/edit /tmp/_oi_reference.txt --show` after tokentrim drops it from context

### Project reference distillation

Instead of injecting a raw truncated CLAUDE.md, `begin` distills each project's CLAUDE.md into a structured OI reference via vLLM (~28s). The output covers tech stack, architecture, key files, conventions, and current state — optimized for a 35B model's context window.

Results are cached by content hash in `~/.cache/oi-ref/`. Only re-distills when CLAUDE.md actually changes (instant cache hit on most launches).

### Inline edit blocks

OI's profile includes an execution interceptor (`_clean_run`) that catches multi-line `~/edit` blocks before they reach the shell. This means content with special characters (parentheses, brackets, quotes) is handled safely in Python, not interpreted by bash.

The interceptor also provides:
- **Grep safety** — injects `--exclude-dir` for `.git`, `node_modules`, `__pycache__`, etc. on recursive grep
- **Interactive SSH blocking** — prevents bare `ssh agx` (would hang the PTY)
- **Hub state indicators** — prints a subtle `hub: git state updated` after state-changing commands

### vLLM self-awareness

The system message tells OI that vLLM on AGX runs its own inference. This prevents the LLM from attempting to kill or restart vLLM processes when investigating memory usage — it would terminate itself.

OI's shell subprocess also starts in the project directory (via `OI_PROJECT_PATH`), so commands like `ls`, `cat`, and `git` operate on the project immediately.

Hub tool commands still work from inside OI on a node:
```
ssh hub "~/overview myproject"    # explicit hub call
~/hub --status                    # SSH stub, delegates automatically
~/edit src/app.tsx --show          # local file, no prefix needed
```

## Built-in OI Commands

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
