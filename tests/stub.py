import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class StubResponse:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b'{"ok":true}'
    delay: float = 0.0


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        # Truncating a capped response closes the socket early, which is the point
        pass


class StubServer:
    """A real HTTP server on loopback. Replay tests use sockets, never mocks."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.script: list[StubResponse] = []
        self.default = StubResponse()
        self._server = QuietServer(("127.0.0.1", 0), self._handler())
        self.port = self._server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.url = f"http://{self.host}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def enqueue(
        self,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        delay: float = 0.0,
    ) -> None:
        self.script.append(StubResponse(status, headers or {}, body, delay))

    def redirect(self, location: str, status: int = 302) -> None:
        self.enqueue(status, {"Location": location})

    def _handler(stub) -> type[BaseHTTPRequestHandler]:  # noqa: N805
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _respond(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                stub.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(length) if length else b"",
                    }
                )
                reply = stub.script.pop(0) if stub.script else stub.default
                if reply.delay:
                    time.sleep(reply.delay)
                self.send_response(reply.status)
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                if reply.body:
                    self.wfile.write(reply.body)

            do_GET = _respond
            do_POST = _respond
            do_PUT = _respond
            do_PATCH = _respond
            do_DELETE = _respond

        return Handler
