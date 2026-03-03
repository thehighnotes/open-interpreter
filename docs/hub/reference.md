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
