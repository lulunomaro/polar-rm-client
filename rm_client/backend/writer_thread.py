"""Retained thread ownership for the DT7 and CC single-writer adapters.

This owner knows admission and joining, not neutral frames or native release.
Adapters serialize reservation/retry decisions with their existing condition;
initial admission runs outside that lock so cancellation stays nonblocking.
"""
from __future__ import annotations

import threading


class WriterThread:
    def __init__(self, condition):
        self._condition = condition
        self.thread = None
        self.starting = False

    @property
    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def never_admitted(self):
        return self.thread is None or self.thread.ident is None

    def reserve(self):
        """Called under the adapter condition before releasing its lock."""
        self.starting = True

    def start_reserved(self, target, name):
        """Initial admission; release the reservation even on constructor error."""
        try:
            self.start(target, name)
        finally:
            with self._condition:
                self.starting = False

    def start(self, target, name):
        """Retain before admission, including a start wrapper's late failure.

        For cleanup retries the caller holds the condition and has confirmed
        no reserved/live worker. Failed construction preserves the old owner;
        failed admission retains the new one for inspection and explicit retry.
        """
        # Retry admission also needs a reservation: the condition is reentrant,
        # so a defensive constructor/start wrapper may call close() on this
        # same owner before the new worker becomes alive. Preserve an initial
        # reservation until start_reserved releases it outside this boundary.
        with self._condition:
            already_reserved = self.starting
            self.starting = True
        try:
            self.thread = threading.Thread(target=target, name=name, daemon=True)
            self.thread.start()
        finally:
            with self._condition:
                self.starting = already_reserved

    def join(self, timeout):
        """Only background/test callers wait; rejected workers cannot be joined."""
        thread = self.thread
        if (thread is not None and thread.ident is not None
                and thread is not threading.current_thread()):
            thread.join(None if timeout is None else max(0.0, float(timeout)))
