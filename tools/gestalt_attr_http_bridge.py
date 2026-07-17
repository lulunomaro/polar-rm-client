#!/usr/bin/env python3
"""接收 Workshop Telemetry Relay 的 HTTP POST，转发为 UDP polar.gestalt.attrs.v1。

游戏 iframe 无法直接 UDP，故：
  HUD fetch → http://127.0.0.1:9841/ingest → 本进程 → UDP 127.0.0.1:9840 → rm_client

用法:
  python tools/gestalt_attr_http_bridge.py
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.gestalt_attrs import DEFAULT_RELAY_PORT, PROTOCOL  # noqa: E402

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 9841
UDP_HOST = "127.0.0.1"
UDP_PORT = DEFAULT_RELAY_PORT


class Handler(BaseHTTPRequestHandler):
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    count = 0

    def log_message(self, fmt: str, *args) -> None:  # quieter
        if self.count % 50 == 0:
            super().log_message(fmt, *args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/ingest", "/"):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(max(0, length))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"bad json")
            return
        if not isinstance(payload, dict):
            self.send_response(400)
            self._cors()
            self.end_headers()
            return
        payload.setdefault("protocol", PROTOCOL)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            Handler.udp.sendto(data, (UDP_HOST, UDP_PORT))
            Handler.count += 1
        except OSError as exc:
            self.send_response(500)
            self._cors()
            self.end_headers()
            self.wfile.write(str(exc).encode("utf-8"))
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        body = (
            f"gestalt_attr_http_bridge ok count={Handler.count} "
            f"forward udp://{UDP_HOST}:{UDP_PORT}\n"
        ).encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"Attr HTTP bridge http://{HTTP_HOST}:{HTTP_PORT}/ingest -> udp://{UDP_HOST}:{UDP_PORT}")
    print("Load workshop_telemetry_relay HUD in Gestalt, then start rm_custom_client.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
