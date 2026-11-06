"""Control hardening on top of test_http_controls.py: DNS-rebinding hosts,
PIN lockout, and secrets compared without crashing on non-ASCII input."""
import http.client
import importlib.util
import json
import threading
import types
from pathlib import Path

import pytest


def load(file):
    spec = importlib.util.spec_from_file_location(file, Path(__file__).with_name(file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hub(monkeypatch):
    mod = load("lab-dashboard.py")
    actions = []

    def action(*args):
        actions.append(args)
        return 202, {"ok": True}

    monkeypatch.setattr(mod, "Hub", lambda cfg: types.SimpleNamespace(node_action=action))
    servers = []

    def start(**cfg_extra):
        cfg = {**mod.DEFAULTS, "port": 0, "control_token": "test-only", **cfg_extra}
        server = mod.make_server(cfg)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)

        def call(pin=None, headers=None, raw=None):
            body = {"node": "Apollo", "action": "clear_vram"}
            if pin is not None:
                body["pin"] = pin
            data = raw if raw is not None else json.dumps(body).encode()
            c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                c.request("POST", "/api/action", data,
                          {"Content-Type": "application/json", **(headers or {})})
                r = c.getresponse()
                return r.status, json.loads(r.read() or b"{}")
            finally:
                c.close()
        return call

    yield start, actions, mod
    for s in servers:
        s.shutdown()
        s.server_close()


REBIND = {"Host": "rebind.attacker.example:8095", "Origin": "http://rebind.attacker.example:8095"}


def test_rebinding_host_is_refused_even_with_matching_origin(hub):
    start, actions, _ = hub
    call = start()
    status, body = call(headers=REBIND)
    assert status == 403 and "allowed_hosts" in body["message"]
    assert actions == []


@pytest.mark.parametrize("host", ["192.168.50.96:8095", "[::1]:8095", "gemini:8095",
                                  "gemini", "lab.local", "lab.lan:8095", "gemini.home.arpa",
                                  "localhost:8095"])
def test_lan_style_hosts_still_work(hub, host):
    start, actions, _ = hub
    call = start()
    assert call(headers={"Host": host, "Origin": f"http://{host}"})[0] == 202
    assert len(actions) == 1


def test_allowed_hosts_adds_names(hub):
    start, actions, _ = hub
    assert start(allowed_hosts=["Lab.Example.org"])(
        headers={"Host": "lab.example.org:8095"})[0] == 202
    assert start(allowed_hosts=["*"])(headers=REBIND)[0] == 202
    assert len(actions) == 2


def test_wrong_pins_lock_out_the_address(hub):
    start, actions, _ = hub
    call = start(pin="4821", pin_attempts=3, pin_lockout_seconds=600)
    assert [call(pin="0000")[0] for _ in range(3)] == [401, 401, 401]
    status, body = call(pin="4821")          # right PIN, but locked out now
    assert status == 429 and "too many wrong PINs" in body["message"]
    assert actions == []


def test_a_right_pin_resets_the_count(hub):
    start, actions, _ = hub
    call = start(pin="4821", pin_attempts=3)
    assert call(pin="0000")[0] == 401 and call(pin="1111")[0] == 401
    assert call(pin="4821")[0] == 202
    assert call(pin="0000")[0] == 401 and call(pin="1111")[0] == 401
    assert call(pin="4821")[0] == 202        # two failures again: not locked
    assert len(actions) == 2


def test_lockout_expires(hub):
    start, actions, mod = hub
    guard = mod.PinGuard(2, 0.05)
    guard.failed("a")
    guard.failed("a")
    assert guard.locked_for("a") > 0 and guard.locked_for("b") == 0
    import time
    time.sleep(0.06)
    assert guard.locked_for("a") == 0


def test_non_ascii_pin_is_just_wrong(hub):
    start, actions, _ = hub
    call = start(pin="4821")
    assert call(pin="éé")[0] == 401
    assert call(raw=b'{"node":"Apollo","action":"clear_vram","pin":"\\ud800"}')[0] == 401
    assert actions == []


def test_control_host_parsing(hub):
    _, _, mod = hub
    cfg = {"allowed_hosts": []}
    assert mod.control_host(cfg, "10.0.0.5:80") and mod.control_host(cfg, "[fe80::1]:80")
    assert not mod.control_host(cfg, "") and not mod.control_host(cfg, "evil.com")
    assert not mod.control_host(cfg, "evil.com:80") and not mod.control_host(cfg, "x.local.evil.com")


@pytest.fixture
def agent(monkeypatch):
    mod = load("lab-agent.py")
    actions = []
    monkeypatch.setattr(mod, "Agent", lambda cfg: types.SimpleNamespace(
        controls=types.SimpleNamespace(start=lambda *a: (actions.append(a), (202, {"ok": True}))[1]),
        lock=threading.Lock(), snapshot={"ok": 1}))
    cfg = {**mod.DEFAULTS, "port": 0, "control_token": "test-only", "token": "read-token"}
    server = mod.make_server(cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, path, headers, body=b""):
        c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            c.putrequest(method, path)
            for k, v in headers.items():
                c.putheader(k, v.encode("latin-1") if isinstance(v, str) else v)
            c.putheader("Content-Length", str(len(body)))
            c.endheaders(body)
            return c.getresponse().status
        finally:
            c.close()
    yield call, actions
    server.shutdown()
    server.server_close()


def test_agent_non_ascii_tokens_are_refused_not_dropped(agent):
    call, actions = agent
    body = b'{"action":"clear_vram"}'
    assert call("POST", "/api/action", {"X-Lab-Control": "tést",
                                        "Content-Type": "application/json"}, body) == 403
    assert call("GET", "/api/node", {"X-Lab-Token": "réad"}) == 401
    assert call("GET", "/api/node", {"X-Lab-Token": "read-token"}) == 200
    assert call("POST", "/api/action", {"X-Lab-Control": "test-only",
                                        "Content-Type": "application/json"}, body) == 202
    assert len(actions) == 1
