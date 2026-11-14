"""GitHub-compatible forge entrypoint.

Binds an ephemeral port, POSTs it to $MAKEFUN_ENV_URL/register, and polls
GET $MAKEFUN_ENV_URL/done until the suite answers {"done": true}. The HTTP
server is a ThreadingHTTPServer speaking HTTP/1.1 keep-alive. Git smart HTTP,
GraphQL, and MCP are wired in beside the REST routes.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gitstore  # noqa: E402
import gql  # noqa: E402
import mcp  # noqa: E402
import rest  # noqa: E402
from store import get_store  # noqa: E402

REQUEST_TIMEOUT = 30
RETRY_INITIAL = 0.25
RETRY_MAX = 1.0

REASONS = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
}


def _retry_delay(attempt: int) -> float:
    return min(RETRY_MAX, RETRY_INITIAL * (2 ** min(attempt, 8)))


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        payload = err.read()
        return err.code, payload


def register(port: int) -> None:
    env = os.environ.get("MAKEFUN_ENV_URL", "").rstrip("/")
    url = f"{env}/register"
    for attempt in range(10**9):
        try:
            status, payload = _request("POST", url, {"port": port})
            if status in (200, 409):
                return
            if status in (400, 405):
                raise RuntimeError(f"register answered HTTP {status}: {payload.decode('utf-8', 'replace')}")
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(_retry_delay(attempt))


def wait_for_done() -> None:
    env = os.environ.get("MAKEFUN_ENV_URL", "").rstrip("/")
    url = f"{env}/done"
    for attempt in range(10**9):
        try:
            status, payload = _request("GET", url)
            if status == 200:
                body = json.loads(payload)
                if isinstance(body, dict) and body.get("done") is True:
                    return
        except Exception:
            pass
        time.sleep(_retry_delay(attempt))


class ForgeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256
    allow_reuse_address = True

    def process_request(self, request, client_address):
        try:
            request.setsockopt(6, 1, 1)  # IPPROTO_TCP, TCP_NODELAY
        except OSError:
            pass
        super().process_request(request, client_address)

    def handle_error(self, request, client_address):
        # Keep-alive clients often reset the socket; that is not a forge fault.
        return


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 120
    server_version = "forge"

    def log_message(self, fmt, *args):
        return

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            # The client closed a keep-alive socket between requests.
            return

    def handle_expect_100(self):
        self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        self.wfile.flush()
        return True

    def send_error(self, code, message=None, explain=None):
        status = int(code)
        text = message or REASONS.get(status, "Error")
        self._emit(status, json.dumps({"message": str(text)}).encode(), rest.JSON, ())

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            length = 0
        if length > 64 * 1024 * 1024:
            return b""
        data = self.rfile.read(length) if length else b""
        encoding = (self.headers.get("Content-Encoding") or "").lower()
        if encoding == "gzip" and data:
            try:
                data = gzip.decompress(data)
            except OSError:
                return data
        return data

    def _authorized(self) -> bool:
        expected = os.environ.get("MAKEFUN_SHOP_TOKEN", "")
        header = self.headers.get("Authorization", "")
        if not expected or not header:
            return False
        got = ""
        if header.startswith("Bearer ") or header.startswith("bearer "):
            got = header[7:]
        elif header[:6].lower() == "basic ":
            import base64

            try:
                decoded = base64.b64decode(header[6:].strip()).decode()
                got = decoded.split(":", 1)[1] if ":" in decoded else ""
            except Exception:
                return False
        else:
            return False
        if len(got) != len(expected):
            return False
        result = 0
        for a, b in zip(got.encode(), expected.encode()):
            result |= a ^ b
        return result == 0

    def _emit(self, status: int, body: bytes, content_type: str, extra) -> None:
        if getattr(self, "_head", False) or status == 204:
            body = b""
        reason = REASONS.get(status, "OK")
        version = self.request_version if self.request_version in ("HTTP/1.0", "HTTP/1.1") else "HTTP/1.1"
        conn = "close" if self.close_connection else "keep-alive"
        lines = [
            f"{version} {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "X-GitHub-Request-Id: gf",
            f"Connection: {conn}",
        ]
        for key, value in extra:
            if value is None:
                continue
            lines.append(f"{key}: {value}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")
        self.wfile.write(raw)
        if body:
            self.wfile.write(body)

    def _dispatch(self) -> None:
        path = self.path or "/"
        mark = path.find("?")
        if mark >= 0:
            pth, query = path[:mark], path[mark + 1 :]
        else:
            pth, query = path, ""
        if len(pth) > 1 and pth.endswith("/"):
            pth = pth[:-1]
        method = self.command
        # Always consume the body so a keep-alive connection stays aligned.
        body = self._read_body()
        if pth == "/healthz" and method in ("GET", "HEAD"):
            self._emit(200, b'{"ok":true}', rest.JSON, ())
            return
        if not self._authorized():
            self._emit(401, rest.UNAUTH, rest.JSON, ())
            return
        host = self.headers.get("Host", "localhost")
        try:
            if pth == "/api/graphql":
                result = gql.handle(body) if method == "POST" else (404, rest.NOT_FOUND, rest.JSON, ())
            elif pth == "/mcp":
                result = mcp.handle(body) if method == "POST" else (405, rest.NOT_FOUND, rest.JSON, ())
            elif gitstore.parse_git_path(pth) is not None:
                result = gitstore.handle_git(method, pth, query, body, self.headers.get("Git-Protocol", ""))
            else:
                result = rest.handle_rest(method, pth, query, body, host)
        except Exception:
            self._emit(500, json.dumps({"message": "Internal Server Error"}).encode(), rest.JSON, ())
            return
        if result is None:
            self._emit(404, rest.NOT_FOUND, rest.JSON, ())
            return
        status, payload, content_type, extra = result
        self._emit(status, payload, content_type, extra)

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._head = True
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()


def build_server() -> ForgeServer:
    return ForgeServer(("0.0.0.0", 0), Handler)


def main() -> None:
    env = os.environ.get("MAKEFUN_ENV_URL", "").strip()
    if not env:
        sys.stderr.write("[entry] MAKEFUN_ENV_URL is not set: there is no env to register with\n")
        sys.exit(1)
    # Touch the store before serving so login and trial time are fixed.
    get_store()
    server = build_server()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    port = server.server_address[1]
    sys.stdout.write(f"[entry] listening on port {port}\n")
    sys.stdout.flush()
    try:
        register(port)
        sys.stdout.write("[entry] registered; waiting for the env to say done\n")
        sys.stdout.flush()
        wait_for_done()
    finally:
        server.shutdown()
        server.server_close()
    sys.stdout.write("[entry] done\n")
    sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        sys.stderr.write(f"[entry] {err}\n")
        sys.exit(1)
