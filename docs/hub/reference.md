# Reference

## Modified files

| File | Changes |
|------|---------|
| `interpreter/core/core.py` | Callable auto_run, callable custom_instructions |
| `interpreter/core/respond.py` | Callable custom_instructions support |
| `interpreter/core/llm/llm.py` | Vendored tokentrim import |
| `interpreter/core/utils/truncate_output.py` | Spillover handling + ANSI strip |
| `interpreter/core/computer/terminal/languages/subprocess_language.py` | Sudo detection |
| `interpreter/terminal_interface/terminal_interface.py` | Auto-run flow, state indicators, configurable inference host probe |
| `interpreter/terminal_interface/magic_commands.py` | Hub magic commands, `%image` vision support |
| `interpreter/terminal_interface/components/code_block.py` | Rich output integration |
| `interpreter/terminal_interface/components/base_block.py` | Refresh throttle |
| `interpreter/terminal_interface/components/message_block.py` | Refresh throttle |

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
| `tools/hub/hub_common.py` | Shared module — config loading, SSH helpers, project registry, terminal UI primitives |
| `tools/hub/install.py` | Interactive setup wizard — generates config.json and creates symlinks |
| `tools/hub/config.example.json` | Minimal single-host config template |
| `tools/hub/profiles/hub-profile.example.py` | OI profile template that reads from config.json |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_ENTRIES_PATH` | `~/.config/hub/rag-entries.json` | Path to RAG knowledge base |
| `OI_INFERENCE_HOST` | *(none)* | SSH alias for remote inference host (shows memory usage in status bar) |
| `OI_WOL_SCRIPT` | `~/wol.sh` | Script called by `%wake` |
| `OI_PROJECT` | *(none)* | Current project key (set by `%switch`) |

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
