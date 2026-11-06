"""When only images block the review, forge re-renders them - as groups, with
the same new seed, on another server if one fails - and re-reviews just the
images, instead of writing a change request and re-planning."""
import json
import subprocess

import forge
from conftest import FakeServer
from test_forge import C1, C2_FIXED, SPEC, TWO_CHUNKS, contract_reply, e2e_cfg

PNG = forge.png_encode(2, 2, 3, b"\x10\x20\x30" * 4)
IMAGES = [{"id": "a_1x", "kind": "image", "title": "1x", "prompt": "a card", "seed": 7,
           "filename": "1x/j.png", "depends_on": []},
          {"id": "a_2x", "kind": "image", "title": "2x", "prompt": "a card", "seed": 7,
           "filename": "2x/j.png", "depends_on": [], "scale": 2},
          {"id": "a_logo", "kind": "image", "title": "logo", "prompt": "a logo", "seed": 9,
           "filename": "logo.png", "depends_on": []}]


def fake_renders(monkeypatch):
    calls = []

    def fake(run, cfg, chunk, reroll=0, srv=None):
        over = (run.state.get("image_prompts") or {}).get(forge.image_group_id(chunk)) or {}
        calls.append((chunk["id"], reroll, over.get("prompt", chunk["prompt"])))
        p = run.root / "assets" / chunk["filename"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(PNG)
        with forge.Run.lock:
            run.state["chunks"][chunk["id"]] = {"status": "verified", "asset": chunk["filename"]}
            run.save()
        return 1.0
    monkeypatch.setattr(forge, "run_image_chunk", fake)
    monkeypatch.setattr(forge, "pick_server", lambda icfg, chunk: {"name": "vulcan", "url": "x"})
    monkeypatch.setattr(forge, "image_servers", lambda icfg: [{"name": "vulcan", "url": "x"}])
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: True)
    monkeypatch.setattr(forge, "preflight_images", lambda run, chunks: None)
    return calls


def test_image_only_issues_rerender_and_rereview_images(tmp_path, monkeypatch):
    calls = fake_renders(monkeypatch)
    verdicts = [
        {"status": "ok", "approved": False, "issues": [
            {"severity": "blocking", "chunk": "a_2x", "description": "corner letters",
             "prompt": "a card, no corner indices"}]},
        {"status": "ok", "approved": True, "issues": []}]
    seen = []

    def fake_review(cfg, workdir, disputed=None, only_images=None):
        seen.append(only_images)
        return verdicts.pop(0)
    monkeypatch.setattr(forge, "claude_review", fake_review)
    monkeypatch.setattr(forge, "auto_change", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no change request for an image")))
    plan = dict(TWO_CHUNKS, chunks=TWO_CHUNKS["chunks"] + IMAGES, acceptance_ok=True, missing=[])
    srv = FakeServer([
        ("into a plan that", lambda u, b: json.dumps(plan)),
        ("INTERFACE CONTRACT for a module", contract_reply),
        ("precise Python engineer",
         lambda u, b: "```python\n" + (C1 if "YOUR TASK: c1" in u else C2_FIXED) + "```"),
        ("You review generated code", lambda u, b: '{"approved": true, "issues": []}'),
    ])
    try:
        cfg = e2e_cfg(tmp_path, srv.url)
        cfg["claude"] = {"enabled": True}
        cfg["images"] = {"enabled": True, "servers": [{"name": "vulcan", "url": "x"}]}
        spec = tmp_path / "boxer.md"
        spec.write_text(SPEC)
        run = forge.Run.create(tmp_path / "runs", spec)
        branch = forge.execute(run, cfg)
        assert branch.startswith("forge/boxer-")
    finally:
        srv.close()
    rerenders = [c for c in calls if c[1] > 0]
    # Both sizes of the rejected picture, same try number, with the revised prompt;
    # the unrelated logo is left alone.
    assert sorted(rerenders) == [("a_1x", 1, "a card, no corner indices"),
                                 ("a_2x", 1, "a card, no corner indices")]
    assert seen == [None, ["assets/1x/j.png", "assets/2x/j.png"]]
    files = subprocess.run(["git", "-C", str(tmp_path / "repo"), "show", "--stat", "--format=",
                            branch], capture_output=True, text=True).stdout
    assert "assets/1x/j.png" in files


def test_rerender_limit_and_failover(tmp_path, monkeypatch):
    root = tmp_path / "run"
    (root / "assets").mkdir(parents=True)
    (root / "SPEC.md").write_text("# x\n")
    (root / "plan.json").write_text(json.dumps({"project": "x", "chunks": IMAGES}))
    run = forge.Run(root)
    run.state = {"chunks": {}}
    run.save()
    servers = [{"name": "vulcan", "url": "v"}, {"name": "mercury", "url": "m"}]
    monkeypatch.setattr(forge, "image_servers", lambda icfg: servers)
    monkeypatch.setattr(forge, "pick_server", lambda icfg, chunk: servers[0])
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: True)
    used = []

    def flaky(run, cfg, chunk, reroll=0, srv=None):
        used.append((chunk["id"], srv["name"], reroll))
        if srv["name"] == "vulcan":
            raise forge.ForgeError("vulcan HTTP 500: generate_image returned no results")
        return 1.0
    monkeypatch.setattr(forge, "run_image_chunk", flaky)
    cfg = {"loop": {"image_rounds": 2}}
    issue = [{"chunk": "a_logo", "description": "blurry"}]
    assert forge.rerender_images(run, cfg, issue) == []
    assert used == [("a_logo", "vulcan", 1), ("a_logo", "mercury", 1)]
    assert forge.rerender_images(run, cfg, issue) == []
    assert forge.rerender_images(run, cfg, issue) == issue        # out of tries
    assert forge.image_group_id(IMAGES[0]) == forge.image_group_id(IMAGES[1])
    assert forge.image_group_id(IMAGES[0]) != forge.image_group_id(IMAGES[2])


def test_prompt_revision_reaches_the_render(tmp_path, monkeypatch):
    root = tmp_path / "run"
    (root / "assets").mkdir(parents=True)
    run = forge.Run(root)
    run.state = {"chunks": {}, "image_prompts": {forge.image_group_id(IMAGES[0]): {
        "prompt": "a tarot portrait", "negative_prompt": "corner indices"}}}
    got = {}
    monkeypatch.setattr(forge, "_run_image_chunk",
                        lambda run, cfg, chunk, srv, name, reroll: got.update(chunk) or 1.0)
    forge.run_image_chunk(run, {"images": {"enabled": True}}, IMAGES[1], srv={"name": "v", "url": "v"})
    assert got["prompt"] == "a tarot portrait" and got["negative_prompt"] == "corner indices"


def test_art_without_chroma_key_is_opaque():
    raw = forge.png_encode(2, 1, 4, bytes([255, 255, 255, 0, 10, 20, 30, 128]))
    w, h, ch, px = forge.png_decode(forge.postprocess_image({"palette": 4}, raw))
    assert px[3] == 255 and px[7] == 255
