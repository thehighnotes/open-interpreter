# Modified by thehighnotes (2026) — Jetson hub fork
# See https://github.com/thehighnotes/open-interpreter
import time

from rich.console import Console
from rich.live import Live


class BaseBlock:
    """
    a visual "block" on the terminal.
    """

    def __init__(self):
        self.live = Live(
            auto_refresh=False, console=Console(), vertical_overflow="ellipsis"
        )
        self.live.start()
        self._last_refresh = 0

    def update_from_message(self, message):
        raise NotImplementedError("Subclasses must implement this method")

    def end(self):
        self.refresh(cursor=False)
        self.live.stop()

    def refresh(self, cursor=True):
        raise NotImplementedError("Subclasses must implement this method")

    def _should_refresh(self, interval=0.08):
        """Throttle refresh calls to prevent scrollback pollution. Returns True if enough time has passed."""
        now = time.time()
        if now - self._last_refresh >= interval:
            self._last_refresh = now
            return True
        return False
