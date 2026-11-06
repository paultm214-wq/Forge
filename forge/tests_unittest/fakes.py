"""Fake servers for the tests: an sd-server that draws flat PNGs, and an
OpenAI-style llama-server whose replies a test scripts by system prompt."""

from __future__ import annotations

import base64
import http.server
import json
import threading
import time

from _load import forge


class _Quiet(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                       # a cancelled attempt hangs up mid-reply: expected


class _Server:
    def __init__(self, handler_cls):
        self.httpd = _Quiet(("127.0.0.1", 0), handler_cls)
        self.httpd.owner = self
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def reply(self, obj, code: int = 200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def flat_png(w: int, h: int, rgb=(255, 0, 255)) -> bytes:
    """A key-colour background with a white square in the middle."""
    px = bytearray()
    for y in range(h):
        for x in range(w):
            inside = w // 4 <= x < 3 * w // 4 and h // 4 <= y < 3 * h // 4
            px += bytes((255, 255, 255) if inside else rgb)
    return forge.png_encode(w, h, 3, bytes(px))


class FakeSD(_Server):
    """sd-server: /sdapi/v1/txt2img answers a PNG of the asked size after
    `delay` seconds; every request body is kept in .requests."""

    def __init__(self, delay: float = 0.0):
        self.delay, self.requests, self.lock = delay, [], threading.Lock()

        class H(_Handler):
            def do_GET(self):
                self.reply({"data": []})

            def do_POST(self):
                b = self.body()
                srv = self.server.owner
                with srv.lock:
                    srv.requests.append(b)
                time.sleep(srv.delay)
                png = flat_png(int(b.get("width", 64)), int(b.get("height", 64)))
                self.reply({"images": [base64.b64encode(png).decode()]})

        super().__init__(H)


class FakeLLMServer(_Server):
    """llama-server: /tokenize counts 4 characters as a token; chat replies
    come from `route(system, user, body) -> str`. Keeps (system, user) of
    every chat call in .calls."""

    def __init__(self, route):
        self.route, self.calls, self.lock = route, [], threading.Lock()

        class H(_Handler):
            def do_GET(self):
                self.reply({"data": []} if self.path.startswith("/models") else {"status": "ok"})

            def do_POST(self):
                b = self.body()
                srv = self.server.owner
                if self.path.startswith("/tokenize"):
                    return self.reply({"tokens": [0] * (len(b.get("content", "")) // 4 + 1)})
                if self.path.startswith("/models"):
                    return self.reply({})
                msgs = b.get("messages") or []
                system = next((m["content"] for m in msgs if m["role"] == "system"), "")
                user = next((m["content"] for m in msgs if m["role"] == "user"), "")
                with srv.lock:
                    srv.calls.append((system, user))
                text = srv.route(system, user, b)
                self.reply({"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                            "usage": {"completion_tokens": len(text) // 4}})

        super().__init__(H)
