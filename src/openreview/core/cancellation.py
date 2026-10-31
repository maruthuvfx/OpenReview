"""
Cooperative Cancellation Token for High-Performance VFX Review Tasks.
Enables fine-grained, cooperative abort of in-flight decoding and prefetch tasks.
"""
from __future__ import annotations

import threading
from typing import Optional


class CancellationToken:
    """
    Thread-safe cooperative cancellation token modeled on VFX review engine specifications.
    Allows worker threads to frequently evaluate whether an in-flight operation has been
    superseded by user interaction (e.g., timeline scrubbing, playhead jumps).
    """

    def __init__(self, generation: int = 0):
        self._generation = generation
        self._cancelled = False
        self._lock = threading.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    def request_cancellation(self) -> None:
        """Signal that the operation associated with this token should abort."""
        with self._lock:
            self._cancelled = True

    cancel = request_cancellation

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        with self._lock:
            return self._cancelled

    def reset(self, new_generation: Optional[int] = None) -> None:
        """Reset the cancellation state for a new operation cycle."""
        with self._lock:
            self._cancelled = False
            if new_generation is not None:
                self._generation = new_generation


class CancellationScopeGuard:
    """
    RAII-style helper for evaluating cancellation status inside tight worker loops
    (e.g., scanline by scanline or block by block image decoding).
    """

    def __init__(self, token: Optional[CancellationToken] = None):
        self._token = token

    def aborted(self) -> bool:
        """Return True if an attached cancellation token has been aborted."""
        return self._token is not None and self._token.is_cancelled()
