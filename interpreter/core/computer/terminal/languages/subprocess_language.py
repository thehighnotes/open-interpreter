# Modified by thehighnotes (2026) — Jetson hub fork
# See https://github.com/thehighnotes/open-interpreter
import os
import pty
import queue
import re
import select
import struct
import subprocess
import fcntl
import termios
import threading
import time
import traceback

import pyte

from ..base_language import BaseLanguage


class SubprocessLanguage(BaseLanguage):
    def __init__(self):
        self.start_cmd = []
        self.process = None
        self.verbose = False
        self.output_queue = queue.Queue()
        self.done = threading.Event()
        self.master_fd = None
        self.screen = None
        self.pyte_stream = None
        self._init_done = threading.Event()

    def detect_active_line(self, line):
        return None

    def detect_end_of_execution(self, line):
        return None

    def line_postprocessor(self, line):
        return line

    def preprocess_code(self, code):
        """
        This needs to insert an end_of_execution marker of some kind,
        which can be detected by detect_end_of_execution.

        Optionally, add active line markers for detect_active_line.
        """
        return code

    def terminate(self):
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.process:
            try:
                self.process.terminate()
            except OSError:
                pass
            self.process = None
        self.screen = None
        self.pyte_stream = None

    def start_process(self):
        if self.process:
            self.terminate()

        # Detect terminal width — match the real terminal so programs
        # format output at the correct width. Fall back to 80 if unknown.
        try:
            cols = os.get_terminal_size().columns
        except (AttributeError, ValueError, OSError):
            cols = 80
        cols = max(cols, 40)  # floor at 40 to avoid degenerate wrapping
        rows = 50

        # Create PTY pair
        master_fd, slave_fd = pty.openpty()

        # Disable echo on slave so input isn't reflected back
        attrs = termios.tcgetattr(slave_fd)
        attrs[3] = attrs[3] & ~termios.ECHO  # lflags
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

        # Set terminal size to match the real terminal
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                     struct.pack('HHHH', rows, cols, 0, 0))

        # Build environment
        my_env = os.environ.copy()
        my_env["PYTHONIOENCODING"] = "utf-8"
        my_env["TERM"] = "xterm-256color"
        my_env["COLUMNS"] = str(cols)
        my_env["LINES"] = str(rows)

        self.process = subprocess.Popen(
            self.start_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=my_env,
            preexec_fn=os.setsid,
        )

        # Close slave in parent — child owns it now
        os.close(slave_fd)

        self.master_fd = master_fd

        # Set up pyte virtual terminal with scrollback history
        # (dimensions must match PTY for correct terminal emulation)
        self.screen = pyte.HistoryScreen(cols, rows, history=50000)
        self.pyte_stream = pyte.ByteStream(self.screen)

        # Start reader thread (initially in init mode)
        self._init_done.clear()
        threading.Thread(
            target=self.handle_stream_output,
            args=(master_fd,),
            daemon=True,
        ).start()

        # Send shell initialization: suppress prompt and bracketed paste
        init_cmd = (
            "PS1=''; PS2=''; "
            "bind 'set enable-bracketed-paste off' 2>/dev/null; "
            "echo '##shell_init_done##'\n"
        )
        os.write(master_fd, init_cmd.encode("utf-8"))

        # Wait for init to complete (reader thread sets _init_done)
        self._init_done.wait(timeout=5.0)

        # Reset screen after init so no init output leaks
        self.screen.reset()

        # Drain any init output from the queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    def run(self, code):
        retry_count = 0
        max_retries = 3

        # Setup
        try:
            code = self.preprocess_code(code)
            # For PTY mode: collapse all commands onto a single line using
            # semicolons. PTY bash processes input lines independently — if
            # a command is on a separate line, the previous command could
            # consume PTY stdin and eat it. Single-line ensures atomic execution.
            lines = []
            for l in code.split('\n'):
                l = l.strip().strip(';').strip()
                if l:
                    lines.append(l)
            code = '; '.join(lines)
            if not self.process:
                self.start_process()
        except:
            yield {
                "type": "console",
                "format": "output",
                "content": traceback.format_exc(),
            }
            return

        while retry_count <= max_retries:
            if self.verbose:
                print(f"(after processing) Running processed code:\n{code}\n---")

            self.done.clear()

            # Reset screen for fresh command output (clears history too)
            if self.screen:
                self.screen.reset()

            try:
                os.write(self.master_fd, (code + "\n").encode("utf-8"))
                break
            except:
                if retry_count != 0:
                    yield {
                        "type": "console",
                        "format": "output",
                        "content": f"{traceback.format_exc()}\nRetrying... ({retry_count}/{max_retries})\nRestarting process.",
                    }

                self.start_process()

                retry_count += 1
                if retry_count > max_retries:
                    yield {
                        "type": "console",
                        "format": "output",
                        "content": "Maximum retries reached. Could not execute code.",
                    }
                    return

        execution_timeout = float(os.environ.get("OI_EXECUTION_TIMEOUT", "120"))
        execution_start = time.time()

        while True:
            # Check timeout
            if time.time() - execution_start > execution_timeout:
                yield {
                    "type": "console",
                    "format": "output",
                    "content": f"\n[Execution timed out after {int(execution_timeout)}s]\n",
                }
                self.done.set()
                break

            if not self.output_queue.empty():
                yield self.output_queue.get()
            else:
                time.sleep(0.1)
            try:
                output = self.output_queue.get(timeout=0.3)
                yield output
            except queue.Empty:
                if self.done.is_set():
                    # Drain remaining items
                    for _ in range(3):
                        if not self.output_queue.empty():
                            yield self.output_queue.get()
                        time.sleep(0.2)
                    break

    def handle_stream_output(self, master_fd):
        raw_buffer = b""
        END_MARKER = b"##end_of_execution##"
        INIT_MARKER = b"##shell_init_done##"
        ACTIVE_LINE_RE = re.compile(rb"##active_line(\d+)##")
        SUDO_PROMPT_RE = re.compile(rb"\[sudo\] password for \w+")
        init_phase = not self._init_done.is_set()

        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
            except (ValueError, OSError):
                break

            if not ready:
                continue

            try:
                data = os.read(master_fd, 4096)
            except OSError:
                # EIO = PTY closed (process exited)
                break

            if not data:
                break

            raw_buffer += data

            # During init phase, just look for init marker
            if init_phase:
                if INIT_MARKER in raw_buffer:
                    # Init complete — discard everything
                    marker_end = raw_buffer.find(INIT_MARKER) + len(INIT_MARKER)
                    raw_buffer = raw_buffer[marker_end:]
                    init_phase = False
                    self._init_done.set()
                continue

            # Detect sudo password prompt — kill it immediately instead of
            # hanging for 120s. Send Ctrl+C to cancel, emit helpful message.
            if SUDO_PROMPT_RE.search(raw_buffer):
                try:
                    os.write(master_fd, b"\x03")  # Ctrl+C
                except OSError:
                    pass
                self.output_queue.put({
                    "type": "console",
                    "format": "output",
                    "content": "[sudo detected — command requires elevated privileges. Run it manually in your terminal.]\n",
                })
                self.done.set()
                raw_buffer = b""
                continue

            # Strip active_line markers so they don't leak into pyte output.
            # We don't emit them as events — final-state capture doesn't need
            # real-time line tracking, and each event triggers a Rich Live
            # refresh which causes visual duplication on some terminals.
            raw_buffer = ACTIVE_LINE_RE.sub(b"", raw_buffer)

            # Scan raw buffer for end marker (before pyte eats it)
            if END_MARKER in raw_buffer:
                marker_pos = raw_buffer.find(END_MARKER)
                # Feed everything before the marker to pyte
                before = raw_buffer[:marker_pos]
                if before:
                    try:
                        self.pyte_stream.feed(before)
                    except Exception:
                        pass
                # Emit complete terminal content now that command is done
                self._emit_screen_content()
                self.done.set()
                raw_buffer = raw_buffer[marker_pos + len(END_MARKER):]
                continue

            # Feed to pyte (no output emitted — wait for end marker)
            if raw_buffer:
                try:
                    self.pyte_stream.feed(raw_buffer)
                except Exception:
                    pass

            raw_buffer = b""

    def _emit_screen_content(self):
        """Read complete terminal state (history + visible screen) and queue as output."""
        if not self.screen:
            return

        output_lines = []

        # 1. History lines (scrolled off the top)
        for row in self.screen.history.top:
            if not row:
                output_lines.append("")
                continue
            max_col = max(row.keys()) if row else -1
            chars = []
            for col in range(max_col + 1):
                if col in row:
                    chars.append(row[col].data)
                else:
                    chars.append(' ')
            output_lines.append(''.join(chars).rstrip())

        # 2. Visible screen lines
        for line in self.screen.display:
            output_lines.append(line.rstrip())

        # Trim leading and trailing empty lines
        while output_lines and not output_lines[0]:
            output_lines.pop(0)
        while output_lines and not output_lines[-1]:
            output_lines.pop()

        if output_lines:
            content = '\n'.join(output_lines) + '\n'
            self.output_queue.put({
                "type": "console",
                "format": "output",
                "content": content,
            })
