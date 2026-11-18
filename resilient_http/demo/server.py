"""Servidor do dashboard (apenas biblioteca padrão).

    python -m resilient_http.demo [--port 8765] [--no-browser]
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import urlsplit

from .simulator import Scenario, simulate

MAX_BODY = 64 * 1024


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ResilientHttpDemo/0.1"

    @property
    def route(self) -> str:
        """Caminho sem query string (``/?theme=dark`` -> ``/``)."""
        return urlsplit(self.path).path

    def do_GET(self) -> None:
        if self.route in ("/", "/index.html"):
            body = resources.files(__package__).joinpath("index.html").read_bytes()
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
        elif self.route == "/api/defaults":
            self._json(HTTPStatus.OK, Scenario().__dict__)
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "não encontrado"})

    def do_POST(self) -> None:
        if self.route != "/api/simulate":
            self._json(HTTPStatus.NOT_FOUND, {"error": "não encontrado"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "corpo grande demais"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = simulate(Scenario.from_dict(payload))
        except (ValueError, TypeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json(HTTPStatus.OK, result)

    def _json(self, status: HTTPStatus, data: object) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # silencioso
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Dashboard do resilient_http")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Dashboard em {url}  (Ctrl+C para sair)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
