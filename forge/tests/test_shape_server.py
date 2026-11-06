"""forge-shape-server.py (Vulcan) without a GPU: its HTTP API, background
detection and how it makes room in VRAM. Skipped where the server file is
not beside the forge folder (it lives with the machine installers)."""
import base64
import importlib.util
import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent.parent / "forge-shape-server.py"
pytestmark = pytest.mark.skipif(not SERVER.exists(), reason="forge-shape-server.py not here")


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("forge_shape_server", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.ARGS = type("A", (), {"need_gb": 11.0, "stop_unit": ["sd-server"], "coder_url": "",
                            "coder_key_file": "/nonexistent", "idle": 600})()
    m.STATE.update(pipeline=None, stopped=[], jobs=0)
    return m


@pytest.fixture
def http(mod):
    srv = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"

    def call(path, body=None):
        req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    yield call
    srv.shutdown()
    srv.server_close()


def png(mode, color):
    from PIL import Image
    b = io.BytesIO()
    Image.new(mode, (8, 8), color).save(b, "PNG")
    return b.getvalue()


def test_background_removal_only_without_transparency(mod):
    from PIL import Image
    assert mod.needs_background_removal(Image.open(io.BytesIO(png("RGB", (255, 0, 0)))))
    assert mod.needs_background_removal(Image.open(io.BytesIO(png("RGBA", (255, 0, 0, 255)))))
    assert not mod.needs_background_removal(Image.open(io.BytesIO(png("RGBA", (255, 0, 0, 0)))))


def test_health_models_and_release(mod, http, monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "systemctl", lambda *a: calls.append(a) or 0)
    code, h = http("/health")
    assert code == 200 and h["status"] == "ok" and h["model"] == mod.MODEL_ID
    assert h["loaded"] is False and h["stops"] == ["sd-server"]
    code, m = http("/v1/models")
    assert m["data"][0]["status"]["value"] == "unloaded"
    mod.STATE["stopped"] = ["sd-server"]
    code, r = http("/v1/release", {})
    assert code == 200 and r == {"released": True, "restarted": ["sd-server"]}
    assert calls == [("start", "sd-server")] and mod.STATE["stopped"] == []


def test_shape_without_an_image_is_a_400(mod, http):
    code, r = http("/v1/shape", {})
    assert code == 400 and r["error"] == "no image"
    code, r = http("/v1/shape", {"image": base64.b64encode(b"not an image").decode()})
    assert code == 400 and "not a readable PNG" in r["error"]
    code, r = http("/nope", {})
    assert code == 404


@pytest.mark.parametrize("body", [[], None, 7, "text"])
def test_shape_requires_a_json_object(mod, body):
    # None means GET in the fixture, so use a raw request for JSON null.
    import http.client
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    worker = threading.Thread(target=srv.serve_forever, daemon=True)
    worker.start()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=2)
    try:
        conn.request("POST", "/v1/shape", json.dumps(body))
        response = conn.getresponse()
        assert response.status == 400
        assert "JSON object" in json.loads(response.read())["error"]
    finally:
        conn.close()
        srv.shutdown()
        srv.server_close()
        worker.join(timeout=2)


def test_make_room_unloads_the_coder_then_stops_sd_server(mod, monkeypatch):
    free = iter([4.0, 5.0, 12.5])                           # before, after the coder, after sd-server
    monkeypatch.setattr(mod, "vram_free_gb", lambda: next(free))
    monkeypatch.setattr(mod, "wait_for_vram", lambda need, s: mod.vram_free_gb())
    unloaded = []
    monkeypatch.setattr(mod, "unload_coder", lambda: unloaded.append(1))
    calls = []
    monkeypatch.setattr(mod, "systemctl", lambda *a: calls.append(a) or 0)
    mod.make_room()
    assert unloaded == [1]
    assert ("stop", "sd-server") in calls and mod.STATE["stopped"] == ["sd-server"]


def test_make_room_gives_up_with_how_much_is_free(mod, monkeypatch):
    monkeypatch.setattr(mod, "vram_free_gb", lambda: 3.0)
    monkeypatch.setattr(mod, "wait_for_vram", lambda need, s: 3.0)
    monkeypatch.setattr(mod, "unload_coder", lambda: None)
    monkeypatch.setattr(mod, "systemctl", lambda *a: 1)    # nothing active to stop
    with pytest.raises(RuntimeError, match="only 3.0 GB of VRAM free"):
        mod.make_room()


def test_make_room_does_nothing_when_there_is_room(mod, monkeypatch):
    monkeypatch.setattr(mod, "vram_free_gb", lambda: 14.0)
    monkeypatch.setattr(mod, "unload_coder", lambda: (_ for _ in ()).throw(AssertionError()))
    mod.make_room()


def test_failed_model_load_restores_services(mod, monkeypatch):
    def fail():
        mod.STATE["stopped"].append("sd-server")
        raise RuntimeError("still no VRAM")
    monkeypatch.setattr(mod, "make_room", fail)
    calls = []
    monkeypatch.setattr(mod, "systemctl", lambda *a: calls.append(a) or 0)
    with pytest.raises(RuntimeError, match="no VRAM"):
        mod.load()
    assert calls == [("start", "sd-server")]
    assert mod.STATE["stopped"] == []


def test_failed_service_restart_is_retained_for_retry(mod, monkeypatch):
    mod.STATE["stopped"] = ["sd-server"]
    monkeypatch.setattr(mod, "systemctl", lambda *a: 1)
    assert mod.release() == []
    assert mod.STATE["stopped"] == ["sd-server"]
    monkeypatch.setattr(mod, "systemctl", lambda *a: 0)
    assert mod.release() == ["sd-server"]
    assert mod.STATE["stopped"] == []


def test_generate_errors_become_http_errors(mod, http, monkeypatch):
    def boom(req):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(mod, "generate", boom)
    code, r = http("/v1/shape", {"image": base64.b64encode(png("RGB", (1, 2, 3))).decode()})
    assert code == 500 and "CUDA out of memory" in r["error"]


def test_a_release_restarts_the_server_to_give_back_its_gpu_memory(mod, monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(empty_cache=lambda: None)))
    monkeypatch.setattr(mod, "systemctl", lambda *a: 0)
    restarts = []
    monkeypatch.setattr(mod, "schedule_restart", lambda delay=2.0: restarts.append(delay))
    mod.ARGS.keep_alive = False
    mod.STATE["pipeline"] = object()
    mod.release()
    assert restarts == [2.0] and mod.STATE["pipeline"] is None
    mod.release()                                  # nothing loaded: no restart
    assert restarts == [2.0]


def test_its_own_leftover_memory_restarts_it_and_the_error_names_the_holders(mod, monkeypatch):
    monkeypatch.setattr(mod, "vram_free_gb", lambda: 8.4)
    monkeypatch.setattr(mod, "wait_for_vram", lambda need, s: 8.4)
    monkeypatch.setattr(mod, "unload_coder", lambda: None)
    monkeypatch.setattr(mod, "systemctl", lambda *a: 1)
    import os
    monkeypatch.setattr(mod, "gpu_holders",
                        lambda: f"{os.getpid()} python3 (this server) 7650 MiB, 4242 strata 900 MiB")
    restarts = []
    monkeypatch.setattr(mod, "schedule_restart", lambda delay=2.0: restarts.append(delay))
    with pytest.raises(RuntimeError, match=r"(?s)only 8.4 GB.*this server\) 7650 MiB.*strata"):
        mod.make_room()
    assert restarts == [2.0]


def test_alpha_noise_still_gets_its_background_removed(mod):
    from PIL import Image
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(0, 100, 7):
        img.putpixel((x, 0), (255, 255, 255, 253))
    assert mod.needs_background_removal(img)
    for y in range(20):
        for x in range(100):
            img.putpixel((x, y), (0, 0, 0, 0))
    assert not mod.needs_background_removal(img)
