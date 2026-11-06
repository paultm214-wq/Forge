"""3D shapes: an image asset with a "shape" becomes a GLB from the shape
server (Hunyuan3D 2.1 on Vulcan) once its image has rendered - after that
machine's renders, under its GPU lock, released at the end. A fake shape
server over real HTTP stands in for Vulcan."""
import base64
import http.server
import json
import threading
import time

import pytest

import forge
from conftest import make_run

GLB = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 12


class FakeShapeServer:
    def __init__(self, glb=GLB, delay=0.0):
        self.jobs, self.releases, self.times = [], 0, []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, {"status": "ok", "model": "hunyuan3d-2.1-shape",
                                            "loaded": False, "stops": ["sd-server"]})
                self._send(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if self.path == "/v1/release":
                    server.releases += 1
                    return self._send(200, {"released": True, "restarted": []})
                if self.path == "/v1/shape":
                    server.jobs.append(body)
                    server.times.append(time.monotonic())
                    time.sleep(delay)
                    return self._send(200, {"glb": base64.b64encode(glb).decode(), "faces": 1234,
                                            "vertices": 620, "seconds": 1.0})
                self._send(404, {})

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def shape_server():
    srv = FakeShapeServer()
    yield srv
    srv.close()


def cfg_for(shape_url, image_names=("vulcan",)):
    return {"images": {"enabled": True,
                       "servers": [{"name": n, "url": f"http://{n}.invalid"} for n in image_names]},
            "shapes": {"enabled": True, "release_wait": 0,
                       "servers": [{"name": "vulcan", "url": shape_url, "machine": "vulcan"}]}}


def fake_renders(monkeypatch, run, log, delay=0.0):
    """run_image_chunk as the real one leaves things: the PNG in assets/ and
    the state "rendered" for an asset with a shape."""
    def render(run_, cfg, job, srv=None, reroll=0):
        time.sleep(delay)
        f = run_.root / "assets" / job["filename"]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        with forge.Run.lock:
            run_.state["chunks"][job["id"]] = {"status": "rendered" if job.get("shape") else
                                               "verified", "asset": job["filename"]}
        log.append(("render", job["id"], srv["name"], time.monotonic()))
        return 1.0
    monkeypatch.setattr(forge, "run_image_chunk", render)
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: True)


def barrel(i=""):
    return {"id": f"barrel{i}", "kind": "image", "filename": f"refs/barrel{i}.png",
            "prompt": "an oil barrel", "shape": {"filename": f"models/barrel{i}.glb",
                                                 "max_faces": 8000, "steps": 20}}


def test_spec_assets_accepts_and_checks_shapes():
    ok = ('## Assets\n```json\n[{"id": "b", "filename": "b.png", "prompt": "p", '
          '"shape": {"filename": "models/b.glb", "max_faces": 5000}}]\n```\n')
    assert forge.spec_assets(ok)[0]["shape"] == {"filename": "models/b.glb", "max_faces": 5000}
    for shape, msg in [('{"max_faces": 1}', "shape needs"),
                       ('{"filename": "../b.glb"}', "relative .glb"),
                       ('{"filename": "b.obj"}', "relative .glb"),
                       ('{"filename": "b.glb", "texture": true}', "unknown shape keys")]:
        spec = (f'## Assets\n```json\n[{{"id": "b", "filename": "b.png", "prompt": "p", '
                f'"shape": {shape}}}]\n```\n')
        with pytest.raises(forge.ForgeError, match=msg):
            forge.spec_assets(spec)
    sheet = ('## Assets\n```json\n[{"id": "b", "filename": "b.png", "prompt": "p", "frames": 4, '
             '"shape": {"filename": "b.glb"}}]\n```\n')
    with pytest.raises(forge.ForgeError, match="sprite sheet"):
        forge.spec_assets(sheet)


def test_the_shape_key_never_reaches_sd_server():
    assert "shape" in forge.FORGE_ASSET_KEYS and "shape" in forge.ASSET_KEYS


def test_pool_makes_the_model_after_the_render_and_releases_once(tmp_path, monkeypatch,
                                                                shape_server):
    run = make_run(tmp_path, plan={"chunks": []})
    log = []
    fake_renders(monkeypatch, run, log)
    pool = forge.ImagePool(run, cfg_for(shape_server.url), [barrel()])
    pool.start()
    pool.join()
    assert not pool.failed
    assert (run.root / "assets" / "models" / "barrel.glb").read_bytes() == GLB
    st = run.state["chunks"]["barrel"]
    assert st["status"] == "verified" and st["model"] == "models/barrel.glb" and st["faces"] == 1234
    job = shape_server.jobs[0]
    assert base64.b64decode(job["image"]).startswith(b"\x89PNG")
    assert (job["max_faces"], job["steps"], job["octree_resolution"]) == (8000, 20, 256)
    assert shape_server.releases == 1


def test_models_wait_until_the_machine_has_no_renders_left(tmp_path, monkeypatch, shape_server):
    run = make_run(tmp_path, plan={"chunks": []})
    log = []
    fake_renders(monkeypatch, run, log, delay=0.3)
    jobs = [barrel(1), barrel(2), dict(barrel(3), shape=None, id="flat", filename="flat.png")]
    jobs[2].pop("shape")
    pool = forge.ImagePool(run, cfg_for(shape_server.url), jobs)
    pool.start()
    pool.join()
    renders = [t for kind, _, _, t in log if kind == "render"]
    assert len(renders) == 3 and len(shape_server.jobs) == 2
    assert min(shape_server.times) >= max(renders)          # all of Vulcan's renders first
    assert shape_server.releases == 1


def test_models_start_beside_another_machines_renders(tmp_path, monkeypatch, shape_server):
    """Images rendered on Mercury: Vulcan's shape server need not wait."""
    run = make_run(tmp_path, plan={"chunks": []})
    log = []
    fake_renders(monkeypatch, run, log, delay=0.4)
    jobs = [dict(barrel(1), server="mercury"), dict(barrel(2), server="mercury"),
            dict(barrel(3), server="mercury")]
    pool = forge.ImagePool(run, cfg_for(shape_server.url, ("mercury",)), jobs)
    pool.start()
    pool.join()
    renders = sorted(t for kind, _, _, t in log if kind == "render")
    assert len(shape_server.jobs) == 3
    assert min(shape_server.times) < renders[-1]            # the first model came before the last render


def test_unfinished_counts_a_pending_model(tmp_path, monkeypatch):
    slow = FakeShapeServer(delay=1.0)
    try:
        run = make_run(tmp_path, plan={"chunks": []})
        fake_renders(monkeypatch, run, [])
        pool = forge.ImagePool(run, cfg_for(slow.url), [barrel()])
        pool.start()
        deadline = time.monotonic() + 5
        while not slow.jobs and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pool.unfinished(["barrel"]) == ["barrel"]   # rendered, model not made yet
        pool.join()
        assert pool.unfinished(["barrel"]) == []
    finally:
        slow.close()


def test_a_resume_makes_the_model_without_rendering_again(tmp_path, monkeypatch, shape_server):
    run = make_run(tmp_path, plan={"chunks": []},
                   state={"chunks": {"barrel": {"status": "rendered", "asset": "refs/barrel.png"}}})
    (run.root / "assets" / "refs").mkdir(parents=True)
    (run.root / "assets" / "refs" / "barrel.png").write_bytes(b"\x89PNG\r\n\x1a\nold")
    monkeypatch.setattr(forge, "run_image_chunk",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rendered again")))
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: False)   # images all down
    pool = forge.ImagePool(run, cfg_for(shape_server.url), [barrel()])
    assert pool.queue == [] and [c["id"] for c in pool.shape_queue] == ["barrel"]
    pool.start()
    pool.join()
    assert not pool.failed and run.state["chunks"]["barrel"]["status"] == "verified"


def test_a_bad_reply_fails_the_asset(tmp_path, monkeypatch):
    bad = FakeShapeServer(glb=b"nope")
    try:
        run = make_run(tmp_path, plan={"chunks": []})
        fake_renders(monkeypatch, run, [])
        pool = forge.ImagePool(run, cfg_for(bad.url), [barrel()])
        pool.start()
        pool.join()
        assert len(pool.failed) == 1 and "returned no GLB" in pool.failed[0]
        assert run.state["chunks"]["barrel"]["status"] == "rendered"
        assert bad.releases == 1
    finally:
        bad.close()


def test_shapes_need_the_config_and_a_reachable_server(tmp_path, monkeypatch):
    run = make_run(tmp_path, plan={"chunks": []})
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: True)
    cfg = cfg_for("http://127.0.0.1:9")
    cfg["shapes"]["enabled"] = False
    with pytest.raises(forge.ForgeError, match=r"\[shapes\] is not enabled"):
        forge.ImagePool(run, cfg, [barrel()])
    with pytest.raises(forge.ForgeError, match="no shape server is reachable"):
        forge.ImagePool(run, cfg_for("http://127.0.0.1:9"), [barrel()])


def test_helper_coder_waits_for_its_machines_models(tmp_path, monkeypatch):
    slow = FakeShapeServer(delay=1.0)
    try:
        run = make_run(tmp_path, plan={"chunks": []})
        fake_renders(monkeypatch, run, [])
        pool = forge.ImagePool(run, cfg_for(slow.url, ("mercury",)), [dict(barrel(), server="mercury")])
        monkeypatch.setattr(forge, "CURRENT_POOL", pool)
        helper = forge.Helper("vulcan", forge.LLM("coder@vulcan", {"url": "http://x"}), "vulcan", True)
        other = forge.Helper("mercury", forge.LLM("coder@mercury", {"url": "http://y"}), "mercury", True)
        pool.start()
        deadline = time.monotonic() + 5
        while not slow.jobs and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not forge.helper_free_of_images(helper)      # Vulcan is making a model
        pool.join()
        assert forge.helper_free_of_images(helper) and forge.helper_free_of_images(other)
    finally:
        slow.close()


def test_code_chunks_wait_for_a_model_they_name():
    plan = {"chunks": [barrel(), {"id": "c1", "kind": "code", "depends_on": [],
                                  "spec": "Load assets/models/barrel.glb and decimate it."}]}
    assert forge.chunk_images(plan, plan["chunks"][1]) == ["barrel"]


def test_a_rerender_makes_its_model_straight_away(tmp_path, monkeypatch, shape_server):
    run = make_run(tmp_path, plan={"chunks": []})
    fake_renders(monkeypatch, run, [])
    cfg = cfg_for(shape_server.url)
    forge.render_failover(run, cfg, barrel(), reroll=1)
    assert run.state["chunks"]["barrel"]["status"] == "verified"
    assert len(shape_server.jobs) == 1 and shape_server.releases == 1


def test_run_shape_holds_the_machines_gpu_lock(tmp_path, monkeypatch, shape_server):
    run = make_run(tmp_path, plan={"chunks": []})
    (run.root / "assets" / "refs").mkdir(parents=True)
    (run.root / "assets" / "refs" / "barrel.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    released = []
    monkeypatch.setattr(forge, "release_gpu", lambda m: released.append(
        (m, forge.gpu_lock(m).locked())))
    forge.run_shape(run, cfg_for(shape_server.url), barrel(),
                    {"name": "vulcan", "url": shape_server.url, "machine": "vulcan"})
    assert released == [("vulcan", True)]                   # coder unloaded under the lock
    assert not forge.gpu_lock("vulcan").locked()
