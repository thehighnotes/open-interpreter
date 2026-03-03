# OI Integration — Magic Commands

The hub tools connect to Open Interpreter through **magic commands** — type them directly in an OI session to operate your hub without leaving the conversation. Without the hub tools installed, the magic commands print a "tool not found" error and return gracefully — the core OI improvements still work fine.

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
