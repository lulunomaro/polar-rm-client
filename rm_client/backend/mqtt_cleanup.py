"""Retain Paho's native owners until the loop and every socket are closed.

Paho's socket-close callback runs before its native close, after `_sock` has
already been detached. Retaining that callback argument is therefore needed
when the vendor's native close raises. The private socket/thread attributes
are inspected only here, alongside the public socket callbacks.
"""
from __future__ import annotations

import threading
from typing import Any

from .resource_close import close_resource


class MqttClientResources:
    """One client, its one network loop, and its captured socket owners."""

    _SOCKET_ATTRIBUTES = ("_sock", "_sockpairR", "_sockpairW")

    def __init__(self, client: Any) -> None:
        self.client = client
        self._lock = threading.Lock()
        self._sockets: dict[int, Any] = {}
        self._network_threads: dict[int, Any] = {}

    def attach_socket_observers(self) -> None:
        self.client.on_socket_open = self.observe_socket
        self.client.on_socket_close = self.observe_socket

    def observe_socket(self, _client: Any, _userdata: Any, handle: Any) -> None:
        # These callbacks only retain ownership. They never call UI observers,
        # close a descriptor still used by the loop, or acquire backend locks.
        with self._lock:
            self._sockets = {key: owner for key, owner in self._sockets.items()
                             if not self._confirmed_closed(owner)}
            self._sockets[id(handle)] = handle

    @staticmethod
    def _confirmed_closed(handle: Any) -> bool:
        try:
            fileno = getattr(handle, "fileno", None)
            return callable(fileno) and fileno() == -1
        except Exception:
            # An observer must retain the new argument even if inspecting a
            # previous handle fails. Cleanup will report that failure later.
            return False

    def _capture_sockets(self) -> None:
        for name in self._SOCKET_ATTRIBUTES:
            handle = getattr(self.client, name, None)
            if handle is not None:
                self.observe_socket(self.client, None, handle)

    def _capture_network_thread(self) -> None:
        thread = getattr(self.client, "_thread", None)
        if thread is not None:
            self._network_threads[id(thread)] = thread

    def start_network_loop(self) -> Any:
        """Adapt Paho's partial loop_start ownership before cleanup can run."""
        try:
            result = self.client.loop_start()
        except Exception:
            # Paho creates its socket pair and publishes _thread before
            # Thread.start. Keep both sockets, but remove only a real Thread
            # whose failed admission never acquired an identity. A thread
            # that ran (including one already finished) must still be joined.
            self._capture_sockets()
            thread = getattr(self.client, "_thread", None)
            if isinstance(thread, threading.Thread) and thread.ident is None:
                if getattr(self.client, "_thread", None) is thread:
                    self.client._thread = None
            else:
                self._capture_network_thread()
            raise
        self._capture_network_thread()
        return result

    def close(self) -> None:
        """Attempt cleanup once; an exception retains this owner for retry.

        A disconnect error does not skip loop termination or socket cleanup.
        Conversely, a stopped loop alone does not certify a socket closed.
        A nonzero loop_stop result is not itself release evidence. It is
        accepted only when the client exposes thread ownership, every captured
        thread has ended, and all captured sockets close successfully. This
        includes Paho's MQTT_ERR_INVAL when no network thread exists; a live
        captured thread always prevents native socket finalization.
        """
        self._capture_sockets()
        self._capture_network_thread()
        disconnect_error = None
        try:
            self.client.disconnect()
        except Exception as exc:
            disconnect_error = exc
        result = self.client.loop_stop()
        self._capture_sockets()
        self._capture_network_thread()
        if any(thread.is_alive() for thread in self._network_threads.values()):
            raise OSError("MQTT network thread remained alive after loop_stop")
        self._network_threads.clear()
        if result not in (None, 0) and not hasattr(self.client, "_thread"):
            raise OSError(f"MQTT loop_stop unconfirmed (rc={result})")
        with self._lock:
            handles = tuple(self._sockets.values())
        failure = None
        for handle in handles:
            try:
                if not self._confirmed_closed(handle):
                    close_resource(handle)
                    fileno = getattr(handle, "fileno", None)
                    if callable(fileno) and fileno() != -1:
                        raise OSError("MQTT socket remained open after close")
                with self._lock:
                    self._sockets.pop(id(handle), None)
                for name in self._SOCKET_ATTRIBUTES:
                    if getattr(self.client, name, None) is handle:
                        setattr(self.client, name, None)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure
        if disconnect_error is not None:
            # Preserve the first failed attempt even if best-effort cleanup
            # released everything. Only an explicit retry clears this fault.
            raise disconnect_error
