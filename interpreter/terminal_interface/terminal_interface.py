# Modified by thehighnotes (2026) — Jetson hub fork
# See https://github.com/thehighnotes/open-interpreter
"""
The terminal interface is just a view. Just handles the very top layer.
If you were to build a frontend this would be a way to do it.
"""

try:
    import readline
except ImportError:
    pass


# Load project keys dynamically for tab completion
try:
    import json as _json
    from pathlib import Path as _Path
    _pj = _Path.home() / ".config" / "hub" / "projects.json"
    if _pj.exists():
        with open(_pj) as _f:
            _project_keys = list(_json.load(_f).get("projects", {}).keys())
    else:
        _project_keys = []
except Exception:
    _project_keys = []


def _oi_completer(text, state):
    MAGIC_COMMANDS = [
        "%help", "%reset", "%undo", "%view",
        "%verbose <true|false>", "%auto_run <true|false>",
        "%save_message <path>", "%load_message <path>",
        "%tokens <prompt>", "%markdown <path>",
        "%info", "%jupyter",
        "%context <llm|display|terminal|window|tokens> <value>",
        "%allow <pattern>", "%deny <number|all>", "%permissions",
        "%auto-edit", "%confirm-edit",
        "%status", "%next", "%projects",
        "%repo", "%checkpoint", "%backup",
        "%wake", "%research", "%health",
        "%services", "%switch", "%overview",
        "%image",
        "%%",
    ]
    # Sub-options for commands that take known arguments
    _SUB_OPTIONS = {
        "%context": ["llm", "display", "terminal", "window", "tokens"],
        "%verbose": ["true", "false"],
        "%auto_run": ["true", "false"],
        "%deny": ["all"],
        "%repo": ["status", "log", "commit", "push", "pull", "deploy", "checkpoint", "create", "fix"],
        "%backup": ["--dry-run", "--list"],
        "%research": ["--fetch", "--calibrate", "--stats"],
        "%switch": _project_keys,
        "%overview": _project_keys,
    }

    buf = readline.get_line_buffer().lstrip()
    # Check if we're completing a sub-option (user already typed a command + space)
    for cmd, opts in _SUB_OPTIONS.items():
        if buf.startswith(cmd + " "):
            after = buf[len(cmd)+1:]
            # Only complete the first sub-arg (the part being typed)
            if " " not in after:
                matches = [f"{cmd} {o}" for o in opts if o.startswith(after)]
                return matches[state] if state < len(matches) else None

    # Top-level command completion
    if text.startswith("%"):
        matches = [c for c in MAGIC_COMMANDS if c.startswith(text)]
    else:
        matches = []
    return matches[state] if state < len(matches) else None

import os
import platform
import random
import re
import select
import subprocess
import sys
import tempfile
import termios
import time
import tty

from ..core.utils.scan_code import scan_code
from ..core.utils.system_debug_info import system_info
from ..core.utils.truncate_output import truncate_output, collapse_for_display
from .components.code_block import CodeBlock
from .components.message_block import MessageBlock
from .magic_commands import handle_magic_command
from .utils.check_for_package import check_for_package
from .utils.cli_input import cli_input
from .utils.display_output import display_output
from .utils.find_image_path import find_image_path

def _handle_image_command(interpreter, message):
    """Handle %image command: grab clipboard image and/or file paths, inject into conversation.

    Returns the text prompt string to send to interpreter.chat(), or None on error.
    Images are appended to interpreter.messages before returning so the LLM sees them.
    The returned prompt flows through interpreter.chat() which adds it as the final
    user message — giving the model: [..., image1, image2, text_prompt].

    Usage:
        %image                          clipboard + default prompt
        %image what is this?            clipboard + custom prompt
        %image /path/to/img.png         file path(s) + default prompt
        %image /a.png /b.jpg compare    file path(s) + custom prompt
    """
    _IMAGE_DIR = os.path.join(tempfile.gettempdir(), "oi-images")
    _IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff'}
    _CLIP_TARGETS = ['image/png', 'image/jpeg', 'image/bmp']

    # Strip %image prefix
    args = message[len("%image"):].strip()

    # Parse: separate file paths from prompt text
    tokens = args.split() if args else []
    file_paths = []
    prompt_words = []
    for tok in tokens:
        expanded = os.path.expanduser(tok)
        if os.path.isfile(expanded) and os.path.splitext(expanded)[1].lower() in _IMAGE_EXTS:
            file_paths.append(expanded)
        else:
            prompt_words.append(tok)

    prompt = " ".join(prompt_words).strip() if prompt_words else ""
    use_clipboard = len(file_paths) == 0  # grab clipboard only if no files given

    if use_clipboard:
        # Check clipboard for image content
        clip_path = None
        try:
            targets_result = subprocess.run(
                ['xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o'],
                capture_output=True, text=True, timeout=3,
            )
            available = targets_result.stdout.strip().split('\n') if targets_result.returncode == 0 else []

            # Find a supported image MIME type
            clip_mime = None
            for mime in _CLIP_TARGETS:
                if mime in available:
                    clip_mime = mime
                    break

            if clip_mime:
                ext = clip_mime.split('/')[-1]
                if ext == 'jpeg':
                    ext = 'jpg'
                os.makedirs(_IMAGE_DIR, exist_ok=True)
                ts = int(time.time() * 1000)
                clip_path = os.path.join(_IMAGE_DIR, f"clip_{ts}.{ext}")

                grab = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-t', clip_mime, '-o'],
                    capture_output=True, timeout=5,
                )
                if grab.returncode == 0 and len(grab.stdout) > 100:
                    with open(clip_path, 'wb') as f:
                        f.write(grab.stdout)
                    file_paths.append(clip_path)
                else:
                    clip_path = None
        except FileNotFoundError:
            print("\n  xclip not installed. Install with: sudo apt install xclip\n")
            return None
        except subprocess.TimeoutExpired:
            pass

        if not clip_path:
            print("\n  No image in clipboard. Copy an image first, or provide file path(s):")
            print("  %image /path/to/image.png [prompt]")
            print("  %image /a.png /b.jpg compare these\n")
            return None

    # Validate all paths exist
    for p in file_paths:
        if not os.path.isfile(p):
            print(f"\n  File not found: {p}\n")
            return None

    # Default prompt if none given
    if not prompt:
        if len(file_paths) == 1:
            prompt = "Describe this image."
        else:
            prompt = f"Describe these {len(file_paths)} images."

    # Inject images into interpreter.messages — they'll be seen by the LLM.
    # The prompt text is returned and flows through interpreter.chat() which
    # appends it as the final user message after these images.
    for p in file_paths:
        interpreter.messages.append({
            "role": "user",
            "type": "image",
            "format": "path",
            "content": p,
        })

    # Show confirmation
    names = [os.path.basename(p) for p in file_paths]
    size_kb = sum(os.path.getsize(p) for p in file_paths) // 1024
    print(f"\n  \033[36m◆\033[0m {len(file_paths)} image{'s' if len(file_paths) != 1 else ''} ({size_kb}KB): {', '.join(names)}")
    if prompt != "Describe this image." and prompt != f"Describe these {len(file_paths)} images.":
        print(f"  \033[2m\"{prompt}\"\033[0m")
    print()

    return prompt


# Add examples to the readline history
examples = [
    "check the status of all services",
    "show GPU memory usage",
    "list files in the current directory",
    "search for recent transformer architecture papers",
    "what changed in the project recently?",
]
random.shuffle(examples)
try:
    for example in examples:
        readline.add_history(example)
    readline.set_completer(_oi_completer)
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")
    readline.parse_and_bind("set enable-bracketed-paste on")
except:
    # If they don't have readline, that's fine
    pass


class _EscapeWatcher:
    """Watch for Escape key in a background thread during streaming.

    Puts stdin into raw mode to detect single keypresses, then restores
    it when stopped.  Sets `self.pressed` when Escape is detected.
    """

    def __init__(self):
        self.pressed = False
        self._stop = False
        self._thread = None
        self._old_settings = None

    def start(self):
        """Begin watching for Escape on stdin."""
        if not sys.stdin.isatty():
            return
        self.pressed = False
        self._stop = False
        try:
            self._old_settings = termios.tcgetattr(sys.stdin)
        except termios.error:
            return
        tty.setcbreak(sys.stdin.fileno())
        import threading
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        while not self._stop:
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                try:
                    ch = sys.stdin.read(1)
                except Exception:
                    break
                if ch == '\x1b':  # Escape
                    self.pressed = True
                    break

    def stop(self):
        """Stop watching and restore terminal settings."""
        self._stop = True
        if self._old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)
            except termios.error:
                pass
            self._old_settings = None


def terminal_interface(interpreter, message):
    # Auto run and offline (this.. this isn't right) don't display messages.
    # Probably worth abstracting this to something like "debug_cli" at some point.
    # If (len(interpreter.messages) == 1), they probably used the advanced "i {command}" entry, so no message should be displayed.
    if (
        not interpreter.auto_run
        and not interpreter.offline
        and not (len(interpreter.messages) == 1)
    ):
        interpreter_intro_message = [
            "**Open Interpreter** will require approval before running code."
        ]

        if interpreter.safe_mode == "ask" or interpreter.safe_mode == "auto":
            if not check_for_package("semgrep"):
                interpreter_intro_message.append(
                    f"**Safe Mode**: {interpreter.safe_mode}\n\n>Note: **Safe Mode** requires `semgrep` (`pip install semgrep`)"
                )
        else:
            interpreter_intro_message.append("Use `interpreter -y` to bypass this.")

        if (
            not interpreter.plain_text_display
        ):  # A proxy/heuristic for standard in mode, which isn't tracked (but prob should be)
            interpreter_intro_message.append("Press `CTRL-C` to exit.")

        interpreter.display_message("\n\n".join(interpreter_intro_message) + "\n")

    if message:
        interactive = False
    else:
        interactive = True

    active_block = None
    voice_subprocess = None
    _esc_watcher = _EscapeWatcher()

    while True:
        if interactive:
            if (
                len(interpreter.messages) == 1
                and interpreter.messages[-1]["role"] == "user"
                and interpreter.messages[-1]["type"] == "message"
            ):
                # They passed in a message already, probably via "i {command}"!
                message = interpreter.messages[-1]["content"]
                interpreter.messages = interpreter.messages[:-1]
            else:
                ### This is the primary input for Open Interpreter.
                try:
                    message = (
                        cli_input("> ").strip()
                        if interpreter.multi_line
                        else input("> ").strip()
                    )
                except (KeyboardInterrupt, EOFError):
                    # Treat Ctrl-D on an empty line the same as Ctrl-C by exiting gracefully
                    interpreter.display_message("\n\n`Exiting...`")
                    raise KeyboardInterrupt

            try:
                # This lets users hit the up arrow key for past messages
                readline.add_history(message)
            except:
                # If the user doesn't have readline (may be the case on windows), that's fine
                pass

        if isinstance(message, str):
            # This is for the terminal interface being used as a CLI — messages are strings.
            # This won't fire if they're in the python package, display=True, and they passed in an array of messages (for example).

            if message == "":
                # Ignore empty messages when user presses enter without typing anything
                continue

            # ── %image: clipboard/file vision ────────────────────────────
            if (message == "%image" or message.startswith("%image ")) and interactive:
                _img_result = _handle_image_command(interpreter, message)
                if _img_result is None:
                    # Error already printed, go back to prompt
                    continue
                # _img_result is the text prompt to send (images already in interpreter.messages)
                message = _img_result

            elif message.startswith("%") and interactive:
                handle_magic_command(interpreter, message)
                continue

            # Many users do this
            if message.strip() == "interpreter --local":
                print("Please exit this conversation, then run `interpreter --local`.")
                continue
            if message.strip() == "pip install --upgrade open-interpreter":
                print(
                    "Please exit this conversation, then run `pip install --upgrade open-interpreter`."
                )
                continue

            if (
                interpreter.llm.supports_vision
                or interpreter.llm.vision_renderer != None
            ):
                # Is the input a path to an image? Like they just dragged it into the terminal?
                image_path = find_image_path(message)

                ## If we found an image, add it to the message
                if image_path:
                    # Add the text interpreter's message history
                    interpreter.messages.append(
                        {
                            "role": "user",
                            "type": "message",
                            "content": message,
                        }
                    )

                    # Pass in the image to interpreter in a moment
                    message = {
                        "role": "user",
                        "type": "image",
                        "format": "path",
                        "content": image_path,
                    }

        try:
            # LLM inference tracking
            _llm_wait_start = time.time()
            _llm_first_token = None
            _llm_token_count = 0
            _llm_waiting = False
            _llm_spinner = None

            # Escape key watcher — press Esc to interrupt LLM or command
            _esc_watcher.start()

            # Show thinking indicator for initial LLM call
            if not interpreter.plain_text_display:
                from rich.console import Console as _RCon
                _llm_spinner = _RCon().status("  [dim]Thinking...[/dim]", spinner="dots")
                _llm_spinner.start()
                _llm_waiting = True

            for chunk in interpreter.chat(message, display=False, stream=True):
                yield chunk

                # Escape pressed — interrupt current operation
                if _esc_watcher.pressed:
                    _esc_watcher.stop()
                    if _llm_spinner:
                        _llm_spinner.stop()
                        _llm_spinner = None
                    if active_block:
                        active_block.refresh(cursor=False)
                        active_block.end()
                        active_block = None
                    if not interpreter.plain_text_display:
                        from rich.console import Console as _RCon
                        _RCon().print("  [dim]Interrupted[/dim]")
                    break

                # Is this for thine eyes?
                if "recipient" in chunk and chunk["recipient"] != "user":
                    continue

                if interpreter.verbose:
                    print("Chunk in `terminal_interface`:", chunk)

                # Comply with PyAutoGUI fail-safe for OS mode
                # so people can turn it off by moving their mouse to a corner
                if interpreter.os:
                    if (
                        chunk.get("format") == "output"
                        and "failsafeexception" in chunk["content"].lower()
                    ):
                        print("Fail-safe triggered (mouse in one of the four corners).")
                        break

                if chunk["type"] == "review" and chunk.get("content"):
                    # Specialized models can emit a code review.
                    print(chunk.get("content"), end="", flush=True)

                # Execution notice
                if chunk["type"] == "confirmation":
                    # Support callable auto_run for selective auto-approval
                    _code_content = chunk["content"]["content"]
                    _should_auto_run = (
                        interpreter.auto_run(_code_content)
                        if callable(interpreter.auto_run)
                        else interpreter.auto_run
                    )
                    if not _should_auto_run:
                        # OI is about to execute code. The user wants to approve this

                        # End the active code block so you can run input() below it
                        if active_block and not interpreter.plain_text_display:
                            active_block.refresh(cursor=False)
                            active_block.end()
                            active_block = None


                        code_to_run = chunk["content"]
                        language = code_to_run["format"]
                        code = code_to_run["content"]

                        should_scan_code = False

                        if not interpreter.safe_mode == "off":
                            if interpreter.safe_mode == "auto":
                                should_scan_code = True
                            elif interpreter.safe_mode == "ask":
                                response = input(
                                    "  Would you like to scan this code? (y/n)\n\n  "
                                )
                                print("")  # <- Aesthetic choice

                                if response.strip().lower() == "y":
                                    should_scan_code = True

                        if should_scan_code:
                            scan_code(code, language, interpreter)

                        if interpreter.plain_text_display:
                            response = input(
                                "Run? y/n/e(dit)\n\n"
                            )
                        else:
                            response = input(
                                "  Run? y/n/e(dit)\n\n  "
                            )
                        print("")  # <- Aesthetic choice

                        if response.strip().lower() == "y":
                            # Create a new block for output only — code was already shown above
                            active_block = CodeBlock(interpreter)
                            active_block.margin_top = False  # <- Aesthetic choice
                            active_block.language = language
                            active_block.code = code
                            active_block.output_only = True  # Don't re-render the code panel

                        elif response.strip().lower() == "e":
                            # Edit

                            # Create a temporary file
                            with tempfile.NamedTemporaryFile(
                                suffix=".tmp", delete=False
                            ) as tf:
                                tf.write(code.encode())
                                tf.flush()

                            # Open the temporary file with the default editor
                            subprocess.call([os.environ.get("EDITOR", "vim"), tf.name])

                            # Read the modified code
                            with open(tf.name, "r") as tf:
                                code = tf.read()

                            interpreter.messages[-1]["content"] = code  # Give it code

                            # Delete the temporary file
                            os.unlink(tf.name)
                            active_block = CodeBlock()
                            active_block.margin_top = False  # <- Aesthetic choice
                            active_block.language = language
                            active_block.code = code

                        else:
                            # User declined — ask for redirect context
                            _decline_msg = "I have declined to run this code."
                            try:
                                if interpreter.plain_text_display:
                                    _reason = input("Why? (or Enter to skip)\n\n").strip()
                                else:
                                    _reason = input("  Why? (or Enter to skip)\n\n  ").strip()
                                if _reason:
                                    _decline_msg = f"I declined. {_reason}"
                            except (KeyboardInterrupt, EOFError):
                                pass
                            interpreter.messages.append(
                                {
                                    "role": "user",
                                    "type": "message",
                                    "content": _decline_msg,
                                }
                            )
                            break

                # Plain text mode
                if interpreter.plain_text_display:
                    if "start" in chunk or "end" in chunk:
                        print("")
                    if chunk["type"] in ["code", "console"] and "format" in chunk:
                        if "start" in chunk:
                            print("```" + chunk["format"], flush=True)
                        if "end" in chunk:
                            print("```", flush=True)
                    if chunk.get("format") != "active_line":
                        print(chunk.get("content", ""), end="", flush=True)
                    continue

                if "end" in chunk and active_block:
                    active_block.refresh(cursor=False)

                    if chunk["type"] in [
                        "message",
                        "console",
                    ]:  # We don't stop on code's end — code + console output are actually one block.
                        if chunk["type"] == "console":
                            active_block.end()
                            active_block = None

                        else:
                            active_block.end()
                            active_block = None


                        # Message ended → show inference stats + context fill
                        if chunk["type"] == "message" and _llm_first_token and not interpreter.plain_text_display:
                            _now = time.time()
                            _wait = _llm_first_token - _llm_wait_start
                            _gen = _now - _llm_first_token
                            _tps = _llm_token_count / _gen if _gen > 0.1 else 0
                            from rich.console import Console as _RCon
                            _stats_parts = []
                            if _wait > 0.5:
                                _stats_parts.append(f"prompt {_wait:.1f}s")
                            _stats_parts.append(f"gen {_gen:.1f}s")
                            if _tps > 0:
                                _stats_parts.append(f"~{_tps:.0f} tok/s")
                            # Context fill — use real token count from respond.py
                            _ctx_win = getattr(interpreter.llm, 'context_window', 0)
                            _prompt_tok = getattr(interpreter, '_last_prompt_tokens', 0)
                            if _ctx_win > 0 and _prompt_tok > 0:
                                _ctx_pct = min(int(_prompt_tok / _ctx_win * 100), 100)
                                _ctx_color = "green" if _ctx_pct < 60 else "yellow" if _ctx_pct < 80 else "red"
                                # Format token counts: 1234 → "1.2K", 44000 → "44K"
                                def _fmt_k(n):
                                    if n >= 1000:
                                        v = n / 1000
                                        return f"{v:.1f}K" if v < 10 else f"{v:.0f}K"
                                    return str(n)
                                _stats_parts.append(f"[{_ctx_color}]ctx {_fmt_k(_prompt_tok)}/{_fmt_k(_ctx_win)} ({_ctx_pct}%)[/{_ctx_color}]")
                            # Remote inference host memory usage (quick SSH probe)
                            _inf_host = os.environ.get("OI_INFERENCE_HOST", "")
                            if _inf_host:
                                try:
                                    _mem_result = subprocess.run(
                                        ["ssh", "-o", "ConnectTimeout=1", _inf_host,
                                         "awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf \"%d %d\", (t-a)/1024, t/1024}' /proc/meminfo"],
                                        capture_output=True, text=True, timeout=2
                                    )
                                    if _mem_result.returncode == 0 and _mem_result.stdout.strip():
                                        _used_mb, _total_mb = _mem_result.stdout.strip().split()
                                        _mem_pct = int(int(_used_mb) / int(_total_mb) * 100)
                                        _mem_color = "green" if _mem_pct < 60 else "yellow" if _mem_pct < 80 else "red"
                                        _stats_parts.append(f"[{_mem_color}]{_inf_host} {_used_mb}M/{_total_mb}M[/{_mem_color}]")
                                except (subprocess.TimeoutExpired, OSError):
                                    pass
                            _RCon().print(f"  [dim]{' | '.join(_stats_parts)}[/dim]")
                            _llm_first_token = None

                        # Console ended → LLM will be called next. Start waiting indicator.
                        if chunk["type"] == "console" and not interpreter.plain_text_display:
                            _llm_wait_start = time.time()
                            from rich.console import Console as _RCon
                            _llm_spinner = _RCon().status("  [dim]Thinking...[/dim]", spinner="dots")
                            _llm_spinner.start()
                            _llm_waiting = True

                # Assistant message blocks
                if chunk["type"] == "message":
                    if "start" in chunk:
                        # Stop waiting indicator
                        if _llm_spinner:
                            _llm_spinner.stop()
                            _llm_spinner = None
                            _llm_waiting = False
                        _llm_first_token = time.time()
                        _llm_token_count = 0
                        active_block = MessageBlock()

                        render_cursor = True

                    if "content" in chunk:
                        _llm_token_count += 1
                        active_block.message += chunk["content"]

                    if "end" in chunk and interpreter.os:
                        last_message = interpreter.messages[-1]["content"]

                        # Remove markdown lists and the line above markdown lists
                        lines = last_message.split("\n")
                        i = 0
                        while i < len(lines):
                            # Match markdown lists starting with hyphen, asterisk or number
                            if re.match(r"^\s*([-*]|\d+\.)\s", lines[i]):
                                del lines[i]
                                if i > 0:
                                    del lines[i - 1]
                                    i -= 1
                            else:
                                i += 1
                        message = "\n".join(lines)
                        # Replace newlines with spaces, escape double quotes and backslashes
                        sanitized_message = (
                            message.replace("\\", "\\\\")
                            .replace("\n", " ")
                            .replace('"', '\\"')
                        )

                        # Display notification in OS mode
                        interpreter.computer.os.notify(sanitized_message)

                        # Speak message aloud
                        if platform.system() == "Darwin" and interpreter.speak_messages:
                            if voice_subprocess:
                                voice_subprocess.terminate()
                            voice_subprocess = subprocess.Popen(
                                [
                                    "osascript",
                                    "-e",
                                    f'say "{sanitized_message}" using "Fred"',
                                ]
                            )
                        else:
                            pass
                            # User isn't on a Mac, so we can't do this. You should tell them something about that when they first set this up.
                            # Or use a universal TTS library.

                # Assistant code blocks
                elif chunk["role"] == "assistant" and chunk["type"] == "code":
                    if "start" in chunk:
                        # Stop waiting indicator if LLM went straight to code
                        if _llm_spinner:
                            _llm_spinner.stop()
                            _llm_spinner = None
                            _llm_waiting = False
                        active_block = CodeBlock()
                        active_block.language = chunk["format"]

                        render_cursor = True

                    if "content" in chunk:
                        active_block.code += chunk["content"]

                # Computer can display visual types to user,
                # Which sometimes creates more computer output (e.g. HTML errors, eventually)
                if (
                    chunk["role"] == "computer"
                    and "content" in chunk
                    and (
                        chunk["type"] == "image"
                        or ("format" in chunk and chunk["format"] == "html")
                        or ("format" in chunk and chunk["format"] == "javascript")
                    )
                ):
                    if (interpreter.os == True) and (interpreter.verbose == False):
                        # We don't display things to the user in OS control mode, since we use vision to communicate the screen to the LLM so much.
                        # But if verbose is true, we do display it!
                        continue

                    assistant_code_blocks = [
                        m
                        for m in interpreter.messages
                        if m.get("role") == "assistant" and m.get("type") == "code"
                    ]
                    if assistant_code_blocks:
                        code = assistant_code_blocks[-1].get("content")
                        if any(
                            text in code
                            for text in [
                                "computer.display.view",
                                "computer.display.screenshot",
                                "computer.view",
                                "computer.screenshot",
                            ]
                        ):
                            # If the last line of the code is a computer.view command, don't display it.
                            # The LLM is going to see it, the user doesn't need to.
                            continue

                    # Display and give extra output back to the LLM
                    extra_computer_output = display_output(chunk)

                    # We're going to just add it to the messages directly, not changing `recipient` here.
                    # Mind you, the way we're doing this, this would make it appear to the user if they look at their conversation history,
                    # because we're not adding "recipient: assistant" to this block. But this is a good simple solution IMO.
                    # we just might want to change it in the future, once we're sure that a bunch of adjacent type:console blocks will be rendered normally to text-only LLMs
                    # and that if we made a new block here with "recipient: assistant" it wouldn't add new console outputs to that block (thus hiding them from the user)

                    if (
                        interpreter.messages[-1].get("format") != "output"
                        or interpreter.messages[-1]["role"] != "computer"
                        or interpreter.messages[-1]["type"] != "console"
                    ):
                        # If the last message isn't a console output, make a new block
                        interpreter.messages.append(
                            {
                                "role": "computer",
                                "type": "console",
                                "format": "output",
                                "content": extra_computer_output,
                            }
                        )
                    else:
                        # If the last message is a console output, simply append the extra output to it
                        interpreter.messages[-1]["content"] += (
                            "\n" + extra_computer_output
                        )
                        interpreter.messages[-1]["content"] = interpreter.messages[-1][
                            "content"
                        ].strip()

                # Console
                if chunk["type"] == "console":
                    render_cursor = False
                    if "format" in chunk and chunk["format"] == "output":
                        # Track raw output separately so collapse always
                        # operates on unmodified data (streaming sends
                        # multiple chunks — collapsing already-collapsed
                        # output would hit the short-circuit and fail).
                        if not hasattr(active_block, '_raw_output'):
                            active_block._raw_output = ""
                        if chunk.get("snapshot"):
                            # Full screen snapshot — replace (handles \r spinners)
                            active_block._raw_output = chunk["content"].strip()
                        else:
                            # Delta output — append
                            active_block._raw_output += "\n" + chunk["content"]
                            active_block._raw_output = (
                                active_block._raw_output.strip()
                            )

                        # Collapse or truncate output for terminal display
                        _threshold = getattr(interpreter, 'display_collapse_lines', 15)
                        active_block.output = collapse_for_display(
                            active_block._raw_output,
                            collapse_threshold=_threshold,
                            max_display_chars=interpreter.max_output,
                        )
                    if "format" in chunk and chunk["format"] == "active_line":
                        active_block.active_line = chunk["content"]

                        # Display action notifications if we're in OS mode
                        if interpreter.os and active_block.active_line != None:
                            action = ""

                            code_lines = active_block.code.split("\n")
                            if active_block.active_line < len(code_lines):
                                action = code_lines[active_block.active_line].strip()

                            if action.startswith("computer"):
                                description = None

                                # Extract arguments from the action
                                start_index = action.find("(")
                                end_index = action.rfind(")")
                                if start_index != -1 and end_index != -1:
                                    # (If we found both)
                                    arguments = action[start_index + 1 : end_index]
                                else:
                                    arguments = None

                                # NOTE: Do not put the text you're clicking on screen
                                # (unless we figure out how to do this AFTER taking the screenshot)
                                # otherwise it will try to click this notification!

                                if any(
                                    action.startswith(text)
                                    for text in [
                                        "computer.screenshot",
                                        "computer.display.screenshot",
                                        "computer.display.view",
                                        "computer.view",
                                    ]
                                ):
                                    description = "Viewing screen..."
                                elif action == "computer.mouse.click()":
                                    description = "Clicking..."
                                elif action.startswith("computer.mouse.click("):
                                    if "icon=" in arguments:
                                        text_or_icon = "icon"
                                    else:
                                        text_or_icon = "text"
                                    description = f"Clicking {text_or_icon}..."
                                elif action.startswith("computer.mouse.move("):
                                    if "icon=" in arguments:
                                        text_or_icon = "icon"
                                    else:
                                        text_or_icon = "text"
                                    if (
                                        "click" in active_block.code
                                    ):  # This could be better
                                        description = f"Clicking {text_or_icon}..."
                                    else:
                                        description = f"Mousing over {text_or_icon}..."
                                elif action.startswith("computer.keyboard.write("):
                                    description = f"Typing {arguments}."
                                elif action.startswith("computer.keyboard.hotkey("):
                                    description = f"Pressing {arguments}."
                                elif action.startswith("computer.keyboard.press("):
                                    description = f"Pressing {arguments}."
                                elif action == "computer.os.get_selected_text()":
                                    description = f"Getting selected text."

                                if description:
                                    interpreter.computer.os.notify(description)

                    if "start" in chunk:
                        # We need to make a code block if we pushed out an HTML block first, which would have closed our code block.
                        if not isinstance(active_block, CodeBlock):
                            if active_block:
                                active_block.end()
                            active_block = CodeBlock()


                if active_block:
                    active_block.refresh(cursor=render_cursor)

            # Clean up spinner and escape watcher
            if _llm_spinner:
                _llm_spinner.stop()
                _llm_spinner = None
            _esc_watcher.stop()

            # (Sometimes -- like if they CTRL-C quickly -- active_block is still None here)
            if "active_block" in locals():
                if active_block:
                    active_block.end()
                    active_block = None
                    time.sleep(0.1)

            if not interactive:
                # Don't loop
                break

        except KeyboardInterrupt:
            # Exit gracefully
            _esc_watcher.stop()
            if _llm_spinner:
                _llm_spinner.stop()
                _llm_spinner = None
            if "active_block" in locals() and active_block:
                active_block.end()
                active_block = None

            if interactive:
                # (this cancels LLM, returns to the interactive "> " input)
                continue
            else:
                break
        except:
            _esc_watcher.stop()
            if interpreter.debug:
                system_info(interpreter)
            raise
