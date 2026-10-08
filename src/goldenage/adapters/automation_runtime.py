"""Lifecycle wrapper for the in-process automation scheduler."""

from __future__ import annotations

import threading
from collections.abc import Callable


class AutomationSchedulerWorker:
    """Poll timezone-aware schedules and stop cleanly during app shutdown."""

    def __init__(self, tick: Callable[[], None], *, interval_seconds: int = 30) -> None:
        self._tick = tick
        self._interval_seconds = max(interval_seconds, 5)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None

    def start(self) -> None:
        """Start one daemon polling thread if it is not already running."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._watch, daemon=True, name="goldenage-scheduler")
        self._thread.start()

    def stop(self) -> None:
        """Request prompt shutdown without blocking the web lifecycle."""
        self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _watch(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as error:  # scheduler errors are visible on the next health check
                self.last_error = error
            self._stop_event.wait(self._interval_seconds)
