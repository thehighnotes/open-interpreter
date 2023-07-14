# Troubleshooting

Hub-specific issues and their fixes. For general SSH or git problems, the error messages are usually self-explanatory.

---

## LLM backend not responding

**`hub --status` shows no model or backend unreachable**

The hub supports two LLM backends: **vLLM** (recommended) and **Ollama** (legacy). Only one can run at a time — they conflict by design.

### vLLM

Check status and available models:
```bash
hub --vllm status
curl -s localhost:8000/v1/models | python3 -m json.tool
```

**vLLM won't start or times out**

vLLM takes 2-3 minutes to start (model loading + compilation). Wait, then check again:
```bash
hub --vllm start
# wait 2-3 min...
hub --vllm status
```

If it still fails, check the systemd journal:
```bash
journalctl -u vllm-server.service --no-pager -n 30
```

**vLLM conflicts with Ollama**

The services have `Conflicts=` set — starting one stops the other. If you see connection refused after switching backends, verify the right service is running:
```bash
hub --vllm status       # should show active
systemctl is-active ollama.service   # should show inactive
```

### Ollama (legacy)

Check that `config.json` points to the right host, then verify Ollama is actually running there:
```bash
ssh <ollama-host> "curl -s localhost:11434/api/tags | head -c 200"
```

**"model not found" errors**

The model name in `config.json` doesn't match what's pulled. Check with `ssh <ollama-host> "ollama list"`.

---

## Code Assistant not starting

`hub --status` shows it offline. Three things to check:
1. The host has the `code_assistant` role in `config.json`
2. The port (default 5002) matches between config and the actual service
3. `prepare <project>` starts it — run that if it's down

---

## Reasoning artifacts in output

Models like Qwen and DeepSeek produce `<think>` tags by default. The hub profile suppresses this with `think: false` in API payloads. If you see thinking artifacts, your OI profile isn't loading the hub integration — check your profile imports.

---

## WebUI won't start

```bash
oi-web --status        # already running?
oi-web --stop          # kill existing
oi-web                 # restart
```

Usually a port conflict (8585). Use `oi-web --port <other>` or check what's listening with `ss -tlnp | grep 8585`.

---

## Command execution timeout

**`[Execution timed out after 120s of silence]`**

The timeout is based on **inactivity**, not total runtime. It resets every time the command produces output. If a command is actively printing (spinners, progress, logs), it will never time out.

If you're running commands that go silent for long periods (large builds, slow network calls), increase the timeout:

```python
# In your profile:
os.environ["OI_EXECUTION_TIMEOUT"] = "300"  # 5 minutes
```

**Command still running after Escape/Ctrl+C?**

Pressing Escape sends Ctrl+C to the subprocess and returns to the `>` prompt. If the process ignores SIGINT, it may continue in the background. Use `kill` or `pkill` to clean it up.

---

## Tokenizer fork warnings

**`huggingface/tokenizers: The current process just got forked...`**

This warning is captured during subprocess creation and rendered inside the code block's output panel. If you see it raw in the terminal instead, the stderr capture may not be active — ensure you're running the latest fork.

---

## Commands hang in OI on zsh hosts

If OI commands appear to freeze (no output, no timeout), the likely cause is **zsh bracketed paste mode** interfering with the PTY. The subprocess init disables this for both bash and zsh, but if you're running an older version of the fork, update to get the fix:

```bash
cd ~/projects/open-interpreter && git pull
```

The init sequence now sends `printf '\e[?2004l'` (terminal-level bracketed paste disable) and `unset zle_bracketed_paste` (zsh-specific) alongside the existing bash `bind` command.

---

## `interpreter` not found on node

**`zsh:1: command not found: interpreter`** when running `work <project> --oi`

The SSH session doesn't source `.zshrc`, so `~/.local/bin` isn't in PATH. The hub's `begin` script adds `export PATH="$HOME/.local/bin:$PATH"` to the remote command automatically. If you see this error, update the hub's `begin` script.

---

## Node can't reach hub

**`work <project> --oi` fails with "Hub unreachable"**

Check SSH connectivity from the node to the hub:
```bash
ssh nano "echo ok"
```

If this fails:
1. Verify `hub_host` in `~/.config/hub/config.json` matches your SSH alias
2. Check `~/.ssh/config` has an entry for the hub
3. Re-run `ssh-copy-id <hub-user>@<hub-ip>` to authorize your key

**Node shows "No cache on hub"**

The hub needs to have run `prepare <project>` at least once. From the hub:
```bash
prepare <project>
```

---

## Update issues

**`interpreter --update` fails with "Pull failed"**

Usually means local changes conflict with upstream. Options:
1. `git stash && git pull && git stash pop` — save your changes, update, restore
2. `git diff` — review what's different before deciding

**Auto-update not working**

Ensure `oi_auto_update` is set in config.json (not the hub section — top level):
```json
{
  "oi_auto_update": true,
  ...
}
```

Also: the check runs once every 6 hours (cached in `~/.cache/oi-update-check.json`). Delete the cache file to force a fresh check.

---

## Inline edit blocks fail with syntax errors

**`bash: syntax error near unexpected token '('`** or **`SyntaxError: invalid decimal literal`**

This means the inline edit block interceptor is not loaded. The OI profile needs the `_clean_run` wrapper that catches multi-line `~/edit` blocks before they reach bash. Without it, content containing parentheses or other shell metacharacters is interpreted by the shell.

Check that your profile (`~/.config/open-interpreter/profiles/linux-admin.py`) includes:
- The `~/edit` module import (`_edit_mod`)
- The `_is_inline_edit` / `_handle_edit_block` functions
- The `_clean_run` wrapper assigned to `interpreter.computer.run`

If you're using a profile generated by `install.py` before this fix, update the profile from `tools/hub/profiles/hub-profile.example.py` or re-run `install.py --update`.

---

## OI project reference not generating

**`⚠ OI reference generation failed — using truncated CLAUDE.md`**

The distillation step calls vLLM to summarize CLAUDE.md. If vLLM is down or the request times out (90s), it falls back to truncated raw CLAUDE.md. Check:
```bash
hub --vllm status
```

To clear the cache and force regeneration:
```bash
rm ~/.cache/oi-ref/<project>-*.txt
```

---

## Port reference

| Port | Service |
|------|---------|
| 8585 | OI WebUI |
| 8000 | vLLM |
| 5002 | Code Assistant |
| 11434 | Ollama (legacy) |
| 3000-5173 | Dev services (React, Next, Vite) |
