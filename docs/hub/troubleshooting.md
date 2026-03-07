# Troubleshooting

Hub-specific issues and their fixes. For general SSH or git problems, the error messages are usually self-explanatory.

---

## Ollama not responding

**`hub --status` shows no model or Ollama unreachable**

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

## Port reference

| Port | Service |
|------|---------|
| 8585 | OI WebUI |
| 5002 | Code Assistant |
| 11434 | Ollama |
| 3000-5173 | Dev services (React, Next, Vite) |
