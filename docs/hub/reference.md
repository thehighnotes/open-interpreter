# Reference

## Modified files

| File | Changes |
|------|---------|
| `interpreter/terminal_interface/utils/check_for_update.py` | (rewritten) Git-based update checker, replaces PyPI version check |
| `interpreter/core/core.py` | Callable auto_run, callable custom_instructions |
| `interpreter/core/respond.py` | Callable custom_instructions support, prompt token counting |
| `interpreter/core/llm/llm.py` | Vendored tokentrim import, `num_ctx` pass-through for Ollama, `reasoning_effort` for local models |
| `interpreter/core/utils/truncate_output.py` | Spillover handling + ANSI strip |
| `interpreter/core/computer/terminal/languages/subprocess_language.py` | Sudo detection, streaming output, inactivity timeout, fork-time stderr capture, Escape interrupt |
| `interpreter/terminal_interface/start_terminal_interface.py` | `--update` CLI flag |
| `interpreter/terminal_interface/terminal_interface.py` | Auto-run flow, state indicators, configurable inference host probe, context token stats, Escape key interrupt, snapshot output handling |
| `interpreter/terminal_interface/magic_commands.py` | Hub magic commands, `%image` vision support |
| `interpreter/terminal_interface/components/code_block.py` | Rich output integration |
| `interpreter/terminal_interface/components/base_block.py` | Refresh throttle |
| `interpreter/terminal_interface/components/message_block.py` | Refresh throttle |
| `tools/hub/begin` | `--preamble-only` flag, hub/node-aware launch, OI context enrichment, project reference distillation |
| `tools/hub/prepare` | Smart skip (staleness check), `--force` flag |
| `tools/hub/work` | Node-aware delegation to hub, `--force` pass-through |

## New files

| File | Description |
|------|-------------|
| `interpreter/core/mini_rag.py` | RAG engine — loads entries from external JSON, embeds with all-MiniLM-L6-v2 |
| `interpreter/terminal_interface/rich_output.py` | Rich diff panels + structured output renderer |
| `interpreter/vendor/tokentrim/` | Vendored tokentrim with [double-subtraction fix](https://github.com/KillianLucas/tokentrim/issues/11) |
| `rag-entries.example.json` | Sample RAG entries to get started |
| `tools/hub/` | Hub tools ecosystem — 15 scripts + shared module, config templates, install wizard |
| `tools/hub/webui/server.py` | Starlette server — 20 API routes, static file serving, hub tool subprocess runner |
| `tools/hub/webui/oi_bridge.py` | Interpreter wrapper — singleton, SSE streaming via thread+queue, approval flow |
| `tools/hub/webui/static/` | Frontend — HTML, CSS (dark theme), JS (chat, tabs, help), vendored marked.js + highlight.js |
| `tools/hub/hub_common.py` | Shared module — config loading, SSH helpers, project registry, terminal UI primitives, hub/node role support (`ROLE`, `HUB_HOST`, `is_hub()`, `is_node()`) |
| `tools/hub/install.py` | (rewritten) Hub/node setup wizard with SSH key exchange, `--update` command |
| `tools/hub/config.example.json` | Minimal single-host config template |
| `tools/hub/profiles/hub-profile.example.py` | OI profile template that reads from config.json |
| `tools/hub/bootstrap.sh` | One-command installer for hub or node setup |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_ENTRIES_PATH` | `~/.config/hub/rag-entries.json` | Path to RAG knowledge base |
| `OI_EXECUTION_TIMEOUT` | `120` | Inactivity timeout in seconds — resets on any output, only fires after silence |
| `OI_INFERENCE_HOST` | *(none)* | SSH alias for remote inference host (shows memory usage in status bar) |
| `OI_CTX` | `44000` | Context window size (tokens) — used by WebUI bridge when no saved config exists |
| `OI_WOL_SCRIPT` | `~/wol.sh` | Script called by `%wake` |
| `OI_PROJECT` | *(none)* | Current project key (set by `%switch`) |
| `OI_HUB_HOST` | *(none)* | SSH alias of hub machine (set by begin for OI profile) |
| `OI_PROJECT_HOST` | *(none)* | Empty string = local files, host alias = remote files via SSH |
| `OI_PROJECT_PATH` | *(none)* | Absolute path to project directory — OI's shell subprocess starts here |
| `OI_SESSION` | *(none)* | Set to `1` by `begin --oi` — gates preamble loading to prevent stale context in bare `interpreter` sessions |

## Config keys (config.json)

| Key | Default | Description |
|-----|---------|-------------|
| `session_persist` | `false` | Save/restore OI conversation across sessions (stored in `~/.cache/oi-sessions/`) |
| `hub.role` | `"hub"` | Machine role: `"hub"` (owns state) or `"node"` (delegates to hub) |
| `hub.hub_host` | *(none)* | SSH alias of the hub (required for nodes) |
| `oi_auto_update` | `false` (hub) / `true` (node) | Auto-pull latest OI changes from origin on startup (cached 6h) |

## projects.json schema

Each project entry in `projects.json` supports these fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name |
| `tagline` | string | Short description |
| `host` | string | Host key from config.json |
| `path` | string | Project path on the host (supports `~`) |
| `claude_md` | string | CLAUDE.md filename (empty to skip) |
| `services` | array | Persistent services (port, name, start_cmd, dir) |
| `dev_services` | array | Ephemeral dev servers (port, name, dev_cmd, dir, enabled) |
| `code_index` | string | Code Assistant project key (empty to skip) |
| `git_remote` | string | GitHub org/repo (e.g. `thehighnotes/myapp`) |
| `git_branch` | string | Default branch |
| `related_repos` | array | GitHub repos of interest for research monitoring |

### `related_repos`

A list of GitHub `org/repo` strings representing dependencies and libraries relevant to the project. Used by the `research` tool to fetch releases and score them with project-specific context.

Repos are populated two ways:
- **Automatically** — `prepare` extracts repo references from the previous session's Claude Code transcript using regex (GitHub URLs, `git clone`, `pip install git+`) and LLM analysis (package name → repo mapping)
- **Manually** — edit `projects.json` directly

Example:
```json
"related_repos": ["tiangolo/fastapi", "encode/httpx", "ratatui/ratatui"]
```

Repos shared across projects (e.g. `tiangolo/fastapi` used by three projects) are fetched once but scored for all owning projects. The project's own repo (`git_remote`) is automatically excluded from extraction.

## Modification conventions

All modified OI source files carry a `# Modified by hub-integration` header comment so changes are easy to find. Modifications are minimal and surgical — we avoid rewriting functions, preferring to add hooks and callables that leave the original logic intact.

For example, `core.py` wasn't refactored to support callable `auto_run` — instead, the existing `if self.auto_run:` check was extended to also call the function when it's a callable. This keeps the diff small and makes rebasing against upstream realistic.

## Adding a new hub tool

1. Create the script in `tools/hub/` (standalone Python, imports `hub_common` for config access)
2. Add it to `install.py` so the wizard creates a `~/` symlink
3. Add tab completion in `tools/hub/completions/` and register it in the install step
4. If the tool needs config values, add them to `hub_common.py`'s `load_config()` and document the schema in `config.example.json`
5. If it should be accessible from OI, add a magic command handler in `magic_commands.py`
6. Add a section to `hub-tools.md`

## Adding RAG entries

RAG entries live in `~/.config/hub/rag-entries.json`. Each entry has:

```json
{
  "title": "Short name",
  "description": "What this is and when to use it. Include synonyms and related terms — the embedding model matches on semantic similarity, so 'authentication login session token' catches more queries than just 'auth'.",
  "content": "The actual knowledge to inject into the prompt."
}
```

Tips:
- **Description drives retrieval** — pack it with the words someone would use when asking about this topic
- **Content drives answers** — include code examples, file paths, and exact command syntax
- **Keep entries focused** — one concept per entry retrieves better than a wall of text
- See `rag-entries.example.json` in the repo root for the format
