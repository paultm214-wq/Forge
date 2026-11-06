"""Shared fixtures for the forge test suite.

Nothing here needs a GPU, Docker or the network: model servers are faked
(FakeLLM in-process, FakeServer over real HTTP for the LLM client), the
sandbox runs with unsafe_local_exec, and Docker itself is replaced by a
script on PATH when a test needs to see what forge does with its output.
"""

import http.server
import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forge  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Keep every test away from ~/forge and from the previous test's globals."""
    monkeypatch.setattr(forge, "SPEEDS_FILE", tmp_path / "image-speeds.json")
    monkeypatch.setattr(forge, "FORGE_HOME", tmp_path / "forge-home")
    monkeypatch.setattr(forge, "HELPERS", [])
    monkeypatch.setattr(forge, "TRIAGE", None)
    monkeypatch.setattr(forge, "LESSON_RUN", None)
    monkeypatch.setattr(forge, "CURRENT_POOL", None)
    monkeypatch.setattr(forge, "TEST_PRELUDE", "")
    monkeypatch.setattr(forge, "PROJECT_MODULE", None)
    monkeypatch.setattr(forge, "TOOLCHAIN", "python")
    monkeypatch.setattr(forge, "TOOLCHAIN_PROFILE", "")
    monkeypatch.setattr(forge, "PRIOR_ART_TEXT", "")
    monkeypatch.setattr(forge, "SANDBOX_MOUNTS", [])
    monkeypatch.setattr(forge, "SANDBOX_ENV", {})
    # Prior art's F-Droid and web searches would reach the real internet.
    monkeypatch.setattr(forge, "fdroid_search", lambda q, **kw: [])
    monkeypatch.setattr(forge, "sdk_web_search", lambda q, **kw: [])
    monkeypatch.setattr(forge, "RUN_ROOT", None)
    # No probe of the real internet, no waits between retries, and no
    # offline library unless a test sets one up.
    net = forge.Net()
    net.cfg = {"probe_urls": [], "retries": 0}
    monkeypatch.setattr(forge, "NET", net)
    monkeypatch.setattr(forge, "CLAUDE_WAITS", (0.0,))
    monkeypatch.setattr(forge, "_KIWIX", {})
    monkeypatch.setattr(forge, "KIWIX_TEXT", "")
    monkeypatch.setattr(forge, "RUN_SPEC_TEXT", "")
    yield
    if forge.log.fh:
        forge.log.fh.close()
        forge.log.fh = None


@pytest.fixture
def local_sandbox():
    return forge.Sandbox({"unsafe_local_exec": True, "timeout": 60})


class FakeLLM:
    """Duck-typed stand-in for forge.LLM. `script` maps a phrase of the system
    prompt to a list of replies (popped in order) or a function of the user
    prompt. A reply that is an exception is raised."""

    def __init__(self, script, name="fake", context=400_000):
        self.script = script
        self.name = name
        self.context = context
        self.prompt_budget = context // 2
        self.max_tokens = self.thinking_max_tokens = self.review_max_tokens = 4000
        self.temperature = 0.2
        self.calls = []
        self.last = {}

    def tokens(self, text):
        return len(text) // 4

    def chat(self, system, user, **kw):
        for phrase, replies in self.script.items():
            if phrase in system:
                self.calls.append((phrase, user, kw))
                r = replies.pop(0) if isinstance(replies, list) else replies(user)
                if isinstance(r, BaseException):
                    raise r
                return r
        raise AssertionError(f"unexpected system prompt: {system[:100]!r}")

    def count(self, phrase):
        return sum(1 for p, _, _ in self.calls if p == phrase)


class FakeServer:
    """An OpenAI-compatible llama-server over real HTTP, for the LLM client.
    /tokenize counts one token per 4 characters; /v1/chat/completions answers
    with the first responder whose phrase is in the system prompt."""

    def __init__(self, responders):
        self.responders = responders          # [(phrase, fn(user, body) -> str)]
        self.calls = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/tokenize":
                    return self._send(200, {"tokens": list(range(len(body["content"]) // 4))})
                system, user = (m["content"] for m in body["messages"])
                for phrase, fn in server.responders:
                    if phrase in system:
                        server.calls.append((phrase, body))
                        content = fn(user, body)
                        return self._send(200, {
                            "choices": [{"message": {"content": content},
                                         "finish_reason": "stop"}],
                            "usage": {"completion_tokens": len(content) // 4}})
                self._send(500, {"error": f"no responder for {system[:80]!r}"})

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def count(self, phrase):
        return sum(1 for p, _ in self.calls if p == phrase)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """Put a `docker` on PATH that records its arguments and behaves as told:
    fake_docker(exit_code, stdout="", stderr="")."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    argfile = tmp_path / "docker-args.json"

    def make(code, stdout="", stderr=""):
        script = bindir / "docker"
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"json.dump(sys.argv[1:], open({str(argfile)!r}, 'w'))\n"
            f"sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r})\n"
            f"sys.exit({code})\n")
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
        return argfile

    return make


def make_run(tmp_path, spec="# P\n", plan=None, verified=None, state=None):
    """A run directory with the given spec, plan, verified chunks and state."""
    root = tmp_path / "run"
    for d in ("chunks", "verified", "assets"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "SPEC.md").write_text(spec)
    if plan is not None:
        (root / "plan.json").write_text(json.dumps(plan))
    for cid, code in (verified or {}).items():
        (root / "verified" / f"{cid}.py").write_text(code)
    st = {"phase": "build", "reviews": [], "claude_cost_usd": 0.0,
          "chunks": {cid: {"status": "verified"} for cid in (verified or {})}}
    st.update(state or {})
    (root / "state.json").write_text(json.dumps(st))
    return forge.Run(root)
