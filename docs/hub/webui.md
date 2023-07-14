# Web Interface (oi-web)

A browser-based UI for Open Interpreter, served from the hub machine. Provides the full OI chat experience plus hub dashboard tabs — accessible from any device on the network (designed for tablet use via RustDesk or direct browser).

```
oi-web                          Start the WebUI server on port 8585
oi-web --stop                   Stop the server
oi-web --restart                Restart the server
oi-web --status                 Check if server is running
oi-web --port N                 Start on a custom port
```

Open `http://<hub-ip>:8585` in a browser.

## Architecture

```
Browser  ──HTTP──▶  Hub:8585 (Starlette)  ──Python──▶  OI Interpreter
                           │                                  │
                           ├── hub_common.py (config, projects)
                           ├── hub tools (subprocess)         ▼
                           └── static files (HTML/CSS/JS)   LLM (vLLM/Ollama)
```

A single Starlette server hosts the API and serves static files. The interpreter runs in-process as a singleton — chat responses stream via SSE (Server-Sent Events). Hub tool endpoints (`/api/status`, `/api/repo`, etc.) run the corresponding tool as a subprocess and return stripped output.

## Tabs

| Tab | Content |
|-----|---------|
| **Chat** | Full OI conversation with streaming markdown, syntax-highlighted code blocks, Run/Skip approval buttons, image upload |
| **Status** | Hub dashboard (`hub --status` output) |
| **Projects** | Project list with switch buttons — changes the interpreter's project context |
| **Repo** | Git dashboard (`repo` output) |
| **Research** | Research digest |
| **Notify** | Notification history with mark-read |
| **Help** | In-app documentation with searchable command reference |
| **Settings** | Model switcher, context window, max tokens, connection status, session reset |

## Chat Features

- **Streaming** — LLM responses stream token-by-token via SSE, rendered as markdown with syntax highlighting (vendored marked.js + highlight.js)
- **Code approval** — unsafe commands show a code block with Run/Skip buttons; safe commands (read-only, hub tools) auto-run using the same `_SAFE_PREFIXES` list as the terminal OI profile
- **Magic commands** — lines starting with `%` are detected client-side and routed to `POST /api/magic`, which runs the corresponding hub tool and returns output in a terminal-style block
- **Image upload** — button next to the input opens a file picker (with camera capture on mobile); uploads to `/tmp/oi-images/` and inserts an `%image /path` message
- **Context stats** — after each response, a `ctx 3.4K / 44K (8%)` footer shows real token usage (prompt tokens vs context window)
- **Session restore** — on page load, previous messages are fetched from the interpreter's in-memory history
- **Welcome screen** — suggestion chips for common actions (Hub Status, My Projects, Research Digest, Git Activity)

### Code approval flow

When the LLM generates a command to run, the WebUI evaluates it against `_SAFE_PREFIXES` — a list of read-only and hub tool commands (`ls`, `cat`, `git status`, `hub`, `repo`, `overview`, etc.). Safe commands auto-execute and show their output inline. Unsafe commands pause and display a code block with **Run** and **Skip** buttons:

```
┌─────────────────────────────────────┐
│ ⚠ OI wants to run:                 │
│                                     │
│   rm -rf node_modules && npm install│
│                                     │
│            [ Run ]    [ Skip ]      │
└─────────────────────────────────────┘
```

Clicking **Run** sends a POST to `/api/chat/approve` with `{"approved": true}`, and the command executes. **Skip** sends `{"approved": false}` and the LLM continues without running it. This is the same approval model as the terminal OI — the WebUI just renders it as buttons instead of a y/n prompt.

### Chat interaction example

A typical exchange looks like this:

```
You:  What's the git status of the backend project?

OI:   I'll check that for you.
      ┌──────────────────────────┐
      │ repo status backend      │  ← auto-runs (safe command)
      └──────────────────────────┘

      The backend project is on branch `main` with 2 modified files
      (src/api.py, src/auth.py) and 1 unpushed commit. No untracked files.

You:  Commit those changes

OI:   I'll stage and commit with an LLM-generated message.
      ┌──────────────────────────┐
      │ repo commit backend      │  ← auto-runs (safe command)
      └──────────────────────────┘

      Committed: "Fix session expiry and add token refresh to auth flow"
      1 commit ready to push.
```

Magic commands (lines starting with `%`) are detected client-side and routed through `/api/magic` — they bypass the LLM entirely and return tool output directly.

### Screenshots

![Chat tab — streaming conversation with code approval and suggestion chips](images/webui-chat.png)

![Status tab — live host and service dashboard](images/webui-status.png)

![Help tab — searchable command reference with 70+ entries](images/webui-help.png)

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/chat` | SSE streaming chat |
| POST | `/api/chat/approve` | Approve/skip pending code execution |
| POST | `/api/chat/stop` | Abort current generation |
| POST | `/api/magic` | Execute magic command |
| GET | `/api/config` | Hub config (name, hosts, model) |
| GET | `/api/session` | Session info (message count, model, connection) |
| GET | `/api/session/messages` | Message history for restore |
| POST | `/api/session/reset` | Clear conversation |
| GET | `/api/status` | Hub status output |
| GET | `/api/projects` | Project list from registry |
| POST | `/api/projects/switch` | Switch project context |
| GET | `/api/repo` | Git dashboard output |
| GET | `/api/research` | Research digest output |
| GET | `/api/notifications` | Notification history |
| POST | `/api/notifications/clear` | Mark notifications read |
| GET | `/api/settings` | Current model, context, connection |
| POST | `/api/settings/update` | Update model/context/max tokens |
| POST | `/api/image` | Upload image file |

## Responsive Layout

- **Desktop (>1024px)** — 220px sidebar with labels + content area
- **Tablet (768–1024px)** — Icon-only sidebar (56px) + content
- **Mobile (<768px)** — Sidebar hidden, horizontal tab bar at bottom

The UI is optimized for tablet viewing at 1600x900 (18px root font, 44px touch targets).

## Configuration

The server reads hub config from `~/.config/hub/config.json` (via `hub_common.load_config()`). An optional `webui/config.json` can override the port:

```json
{ "port": 8585 }
```

`webui/config.json` is gitignored. The server, frontend, and vendored dependencies are all in the repo at `tools/hub/webui/`.

## Service Registration

The WebUI is registered as a service in `projects.json` and appears in `hub --services` and `hub --status`:

```json
{ "port": 8585, "name": "OI WebUI", "start_cmd": "python3 tools/hub/webui/server.py", "dir": "." }
```
