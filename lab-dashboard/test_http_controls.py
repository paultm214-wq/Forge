"""Exercise the actual HTTP handlers; hardware and controls are faked."""
import http.client
import importlib.util
import threading
import types
from pathlib import Path

import pytest


def load(file):
    spec = importlib.util.spec_from_file_location(file, Path(__file__).with_name(file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=["lab-dashboard.py", "lab-agent.py"])
def api(request, monkeypatch):
    mod = load(request.param)
    actions = []

    def action(*args):
        actions.append(args)
        return 202, {"ok": True}

    if request.param == "lab-dashboard.py":
        monkeypatch.setattr(mod, "Hub", lambda cfg: types.SimpleNamespace(node_action=action))
    else:
        monkeypatch.setattr(mod, "Agent", lambda cfg: types.SimpleNamespace(
            controls=types.SimpleNamespace(start=action)))
    cfg = {**mod.DEFAULTS, "port": 0, "control_token": "test-only"}
    server = mod.make_server(cfg)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def call(body=b'{"node":"Apollo","action":"clear_vram"}', headers=None):
        c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            c.request("POST", "/api/action", body, {
                "Content-Type": "application/json", "X-Lab-Control": "test-only", **(headers or {})})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    yield call, actions, request.param
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


@pytest.mark.parametrize("body", [b"[]", b"null", b"42", b'"x"', b"{broken"])
def test_nonobject_rejected_before_action(api, body):
    call, actions, _ = api
    assert call(body)[0] == 400
    assert actions == []


@pytest.mark.parametrize("length,expected", [("-1", 400), ("bad", 400), ("4097", 413)])
def test_invalid_or_large_lengths_rejected_before_read(api, length, expected):
    call, actions, _ = api
    assert call(b"", {"Content-Length": length})[0] == expected
    assert actions == []


def test_valid_control_still_works(api):
    call, actions, _ = api
    assert call(headers={"Host": "lab.local", "Origin": "http://lab.local"})[0] == 202
    assert len(actions) == 1


@pytest.mark.parametrize("api", ["lab-dashboard.py"], indirect=True)
def test_dashboard_rejects_cross_origin_and_plaintext(api):
    call, actions, kind = api
    assert call(headers={"Origin": "https://untrusted.invalid"})[0] == 403
    assert call(headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(headers={"Content-Type": "text/plain"})[0] == 415
    assert actions == []


@pytest.mark.parametrize("api", ["lab-agent.py"], indirect=True)
def test_agent_wrong_control_token_rejected(api):
    call, actions, kind = api
    assert call(headers={"X-Lab-Control": "wrong"})[0] == 403
    assert actions == []
