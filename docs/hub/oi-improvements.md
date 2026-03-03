# OI Improvements

These are standalone improvements to Open Interpreter that work regardless of whether the hub tools are installed.

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

## Other improvements

| Feature | What it does |
|---------|--------------|
| **Sudo detection** | Intercepts `sudo` commands before execution and warns the user instead of running them silently |
| **Truncation fixes** | Large command outputs no longer lose their tail — spillover handling preserves the last N lines, and ANSI escape sequences are stripped cleanly |
| **Refresh throttle** | Fast streaming output (e.g. `pip install` with 200 lines/sec) no longer floods the scrollback buffer. Base and message blocks throttle refreshes to ~10/sec |
| **Rich output panels** | Final command output is rendered with structured Rich panels — diffs get colored, tables get aligned, errors get highlighted |
| **Vendored tokentrim** | Fixes a [double-subtraction bug](https://github.com/KillianLucas/tokentrim/issues/11) that silently loses ~400-600 tokens of usable context window per turn |
