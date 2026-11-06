"""Checks that need a later chunk move there before the build; a stuck
sd-server is restarted and the render retried; reference images are capped."""
import json

import forge


def make_run(tmp_path, plan):
    root = tmp_path / "run"
    for d in ("chunks", "verified", "plan-tests"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "SPEC.md").write_text("# app\nmake_app(data_dir) builds the WSGI app.\n")
    (root / "plan.json").write_text(json.dumps(plan))
    run = forge.Run(root)
    run.state = {"chunks": {}}
    run.save()
    return run


PLAN = {"project": "app", "contract": {"names": [
            {"name": "APP_CSS", "owner": "c9", "signature": ""},
            {"name": "make_app", "owner": "c10", "signature": "(data_dir)"}]},
        "chunks": [
            {"id": "c9", "kind": "code", "spec": "APP_CSS", "depends_on": [],
             "verify": "assert 'body' in APP_CSS\nd = '/tmp/x'\napp = make_app(d)\n"
                       "assert app is not None\nprint('PASS')\n"},
            {"id": "c10", "kind": "code", "spec": "make_app", "depends_on": ["c9"],
             "verify": "assert callable(make_app)\nprint('PASS')\n"}]}


def test_checks_needing_a_later_chunk_move_there(tmp_path):
    run = make_run(tmp_path, PLAN)
    assert forge.move_later_checks(run, "c9")
    plan = run.plan
    c9, c10 = plan["chunks"]
    assert "make_app" not in c9["verify"] and "APP_CSS" in c9["verify"]
    assert "app = make_app(d)" in c10["verify"] and "d = '/tmp/x'" in c10["verify"]
    assert c10["verify"].rstrip().endswith("print('PASS')")
    assert (run.root / "plan-tests" / "c10.py").read_text() == c10["verify"]
    assert not forge.move_later_checks(run, "c9")          # nothing left to move
    compile(c9["verify"], "c9", "exec"), compile(c10["verify"], "c10", "exec")


def test_stuck_sd_server_is_restarted_and_render_retried(tmp_path, monkeypatch):
    calls = {"render": 0, "ssh": []}

    def render(run, cfg, chunk, srv, name, reroll):
        calls["render"] += 1
        if calls["render"] == 1:
            raise forge.ForgeError('[a] vulcan HTTP 500: {"error":"generate_image returned no results"}')
        return 2.0

    class P:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(forge, "_run_image_chunk", render)
    monkeypatch.setattr(forge.subprocess, "run", lambda cmd, **kw: calls["ssh"].append(cmd) or P())
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: True)
    monkeypatch.setattr(forge.time, "sleep", lambda s: None)
    monkeypatch.setattr(forge, "_SD_RESTARTS", {})
    run = forge.Run(tmp_path)
    run.state = {"chunks": {}}
    cfg = {"images": {"enabled": True}}
    srv = {"name": "vulcan", "url": "http://v"}
    assert forge.run_image_chunk(run, cfg, {"id": "a", "prompt": "p"}, srv=srv) == 2.0
    assert calls["render"] == 2
    assert calls["ssh"][0][:4] == ["ssh", "-o", "BatchMode=yes", "-o"]
    assert "systemctl --user restart sd-server" in calls["ssh"][0][-1]
    # Restarts are limited per server.
    calls["render"] = 0
    forge._SD_RESTARTS["vulcan"] = 2
    try:
        forge.run_image_chunk(run, cfg, {"id": "a", "prompt": "p"}, srv=srv)
        raise AssertionError("expected the error once restarts are used up")
    except forge.ForgeError:
        pass


def test_reference_images_are_capped_and_spread():
    refs = [f"w{i}" for i in range(22)] + ["rue-1", "rue-2"]
    picked = forge.pick_references(refs, 3)
    assert picked == ["w0", "w12", "rue-2"]
    assert forge.pick_references(refs[:2], 3) == refs[:2]
    assert forge.stuck_render("HTTP 500: generate_image returned no results")
    assert not forge.stuck_render("HTTP 400: bad size")


def test_reference_images_rejected_is_the_jobs_fault(tmp_path, monkeypatch):
    seen = []

    def render(run, cfg, chunk, srv, name, reroll):
        seen.append(bool(chunk.get("reference_images")))
        if chunk.get("reference_images"):
            raise forge.ForgeError('[a] vulcan HTTP 500: {"error":"generate_image returned no results"}')
        return 1.0
    restarts = []
    monkeypatch.setattr(forge, "_run_image_chunk", render)
    monkeypatch.setattr(forge, "plain_render_works", lambda srv, timeout=240: True)
    monkeypatch.setattr(forge, "restart_sd_server", lambda cfg, srv: restarts.append(1) or True)
    run = forge.Run(tmp_path)
    run.state = {"chunks": {}}
    job = {"id": "a", "prompt": "p", "reference_images": ["refs/w1.jpg"]}
    srv = {"name": "vulcan", "url": "http://v"}
    try:
        forge.run_image_chunk(run, {"images": {"enabled": True}}, job, srv=srv)
        raise AssertionError("expected a JobError")
    except forge.JobError as exc:
        assert "reference images" in str(exc) and "journalctl" in str(exc)
    assert restarts == []                       # the server is fine: no restart
    seen.clear()
    cfg = {"images": {"enabled": True, "refs_fallback": "drop"}}
    assert forge.run_image_chunk(run, cfg, job, srv=srv) == 1.0
    assert seen == [True, False]
