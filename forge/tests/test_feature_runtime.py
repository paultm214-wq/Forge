"""Behavioral tests for reproducibility, health, controls, and offline targets."""

import hashlib
import importlib.util
import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest
import forge
import forge_control as control
import forge_health as health
import forge_provenance as provenance
import forge_targets as targets
import forge_game as game
from conftest import make_run
from test_general_toolchains import project, bindir  # noqa: F401


def ready(tmp_path):
    code = "from pathlib import Path\ndef build_outputs(out_dir):\n Path(out_dir,'answer.txt').write_text('42')\n"
    plan = dict(
        project="answer",
        chunks=[dict(id="c1", spec="answer", verify="assert callable(build_outputs)")],
    )
    run = make_run(tmp_path, plan=plan, verified={"c1": code})
    module = forge.assemble(run.verified(), ["c1"])
    (run.root / "module.py").write_text(module)
    (run.root / "outputs").mkdir()
    (run.root / "outputs/answer.txt").write_text("42")
    return run, module


def test_replay_runs_saved_verification_and_outputs_without_model_calls(tmp_path):
    run, module = ready(tmp_path)
    cfg = {"sandbox": {"unsafe_local_exec": True}}
    forge.record_provenance(run, cfg, module)
    assert forge.replay_run(run.root, cfg, verify=True)["verified"]
    record = json.loads((run.root / provenance.NAME).read_text())
    record["artifacts"]["outputs/answer.txt"]["sha256"] = "0" * 64
    (run.root / provenance.NAME).write_text(json.dumps(record))
    result = forge.replay_run(run.root, cfg, verify=True)
    assert (
        not result["verified"]
        and result["differences"][0]["path"] == "outputs/answer.txt"
    )


def test_provenance_selects_metadata_excludes_private_keys_and_prompt_contents(
    tmp_path,
):
    run, module = ready(tmp_path)
    (run.root / "private").mkdir()
    (run.root / "private/key").write_text("PRIVATE INPUT")
    attempt = run.root / "chunks/c1/attempt1"
    attempt.mkdir(parents=True)
    (attempt / "prompt.md").write_text("PRIVATE PROMPT")
    record = forge.record_provenance(
        run,
        {
            "sandbox": {"unsafe_local_exec": True},
            "coder": {
                "model": "local",
                "api_key": "SECRET",
                "api_key_file": "/secret/key",
            },
        },
        module,
    )
    text = json.dumps(record)
    assert all(
        secret not in text
        for secret in ["PRIVATE INPUT", "PRIVATE PROMPT", "SECRET", "/secret/key"]
    )
    assert "chunks/c1/attempt1/prompt.md" in record["attempt_references"]


def test_replay_refuses_drift_unpinned_images_and_arbitrary_input_paths(tmp_path):
    run, module = ready(tmp_path)
    forge.record_provenance(run, {"sandbox": {"unsafe_local_exec": True}}, module)
    with pytest.raises(forge.ForgeError, match="no image digest"):
        forge.replay_run(run.root, {}, verify=True)
    (run.root / "SPEC.md").write_text("drift")
    with pytest.raises(forge.ForgeError, match="inputs differ"):
        forge.replay_run(run.root, {})


def test_explicit_image_tolerance_leaves_exact_default(tmp_path):
    from PIL import Image

    root = tmp_path / "run"
    (root / "outputs").mkdir(parents=True)
    path = root / "outputs/view.png"
    Image.new("RGB", (32, 32), (100, 100, 100)).save(path)
    record = dict(schema_version=1, artifacts=provenance.artifacts(root))
    Image.new("RGB", (32, 32), (101, 100, 100)).save(path)
    assert not provenance.compare(record, root)["verified"]
    assert provenance.compare(record, root, image_tolerance=0.01)["verified"]
    Image.new("RGB", (32, 32), (200, 100, 100)).save(path)
    assert not provenance.compare(record, root, image_tolerance=0.01)["verified"]


def make_glb(points, indices=(0, 1, 2), external=False):
    import struct

    positions = b"".join(struct.pack("<3f", *p) for p in points)
    binary = positions + struct.pack("<3H", *indices) + bytes(2)
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": 6},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(points),
                "type": "VEC3",
            },
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
    }
    if external:
        doc["buffers"][0]["uri"] = "external.bin"
    metadata = json.dumps(doc).encode()
    metadata += b" " * (-len(metadata) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(metadata) + len(binary))
        + struct.pack("<II", len(metadata), 0x4E4F534A)
        + metadata
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def test_mesh_tolerance_requires_matching_topology_and_vertex_correspondence(tmp_path):
    root = tmp_path / "run"
    (root / "outputs").mkdir(parents=True)
    path = root / "outputs/model.glb"
    points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    path.write_bytes(make_glb(points))
    record = dict(schema_version=1, artifacts=provenance.artifacts(root))
    path.write_bytes(make_glb([(0.001, 0, 0), *points[1:]]))
    assert not provenance.compare(record, root)["verified"]
    assert provenance.compare(record, root, mesh_tolerance=0.01)["verified"]
    path.write_bytes(make_glb(points, (2, 1, 0)))
    assert not provenance.compare(record, root, mesh_tolerance=0.01)["verified"]
    path.write_bytes(make_glb([points[1], points[0], points[2]]))
    assert not provenance.compare(record, root, mesh_tolerance=0.01)["verified"]


def test_external_geometry_is_never_claimed_as_a_supported_glb_signature(tmp_path):
    path = tmp_path / "external.glb"
    path.write_bytes(make_glb([(0, 0, 0), (1, 0, 0), (0, 1, 0)], external=True))
    assert provenance.mesh_signature(path) is None


def fake_docker(args):
    return (
        (0, json.dumps([{"Id": "sha256:" + "a" * 64}]), "")
        if args[1:3] == ["image", "inspect"]
        else (0, '{"compiler":"example"}', "")
    )


def test_health_never_confuses_installed_unavailable_failed_and_stale(tmp_path):
    path = tmp_path / "health.json"
    profiles = {"python": {"image": "test"}}
    result = health.report(path, profiles, run=fake_docker)
    assert (
        result["toolchains"][0]["installed"]
        and result["toolchains"][0]["smoke"]["status"] == "unrun"
    )
    result = health.report(
        path, profiles, run=fake_docker, smoke=lambda name: ("failed", "assertion")
    )
    assert result["toolchains"][0]["smoke"]["status"] == "failed"
    result["toolchains"][0]["smoke"] = dict(
        status="passed", validated_at="2000-01-01T00:00:00+00:00"
    )
    path.write_text(json.dumps(result))
    result = health.report(path, profiles, run=fake_docker)
    assert result["toolchains"][0]["smoke"]["stale"]
    result["host"]["hostname"] = "different"
    path.write_text(json.dumps(result))
    assert health.cached(path)["stale"]
    result = health.report(path, profiles, run=lambda args: (None, "", "daemon down"))
    assert (
        result["toolchains"][0]["installed"] is None
        and result["toolchains"][0]["status"] == "unavailable"
    )


def hold(root, ready):
    with control.GpuLock(Path(root), "gpu"):
        ready.set()
        time.sleep(60)


def test_gpu_recovers_dead_owner_but_never_steals_live_claim(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    event = ctx.Event()
    child = ctx.Process(target=hold, args=(str(tmp_path), event))
    child.start()
    try:
        assert event.wait(3)
        lock = control.GpuLock(tmp_path, "gpu")
        assert not lock.acquire(timeout=0.05)
        child.terminate()
        child.join(3)
        assert lock.acquire(timeout=2)
        lock.release()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(3)


def test_fifo_includes_threads_waiting_on_the_same_lock(tmp_path):
    holder = control.GpuLock(tmp_path, "gpu")
    holder.acquire()
    shared = control.GpuLock(tmp_path, "gpu")
    threads = []
    order = []
    try:
        for number in range(3):

            def claim(n=number):
                with shared:
                    order.append(n)

            thread = threading.Thread(target=claim)
            threads.append(thread)
            thread.start()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with control.entries(tmp_path, "gpu") as rows:
                    if len(rows) == number + 2:
                        break
                time.sleep(0.01)
            else:
                pytest.fail("waiting thread never entered FIFO")
    finally:
        holder.release()
        for thread in threads:
            thread.join(3)
    assert order == [0, 1, 2] and not any(t.is_alive() for t in threads)


@pytest.mark.parametrize("action,phase", [("pause", "paused"), ("cancel", "cancelled")])
def test_controls_checkpoint_without_deleting_verified_work(tmp_path, action, phase):
    run = make_run(
        tmp_path, plan=dict(project="p", chunks=[]), verified={"c1": "VALUE=42"}
    )
    control.request(run.root, action)
    assert forge.execute(run, {"paths": {"runs": str(run.root.parent)}}) is None
    assert run.state["phase"] == phase and run.verified()["c1"] == "VALUE=42"
    assert (
        json.loads((run.root / "CHECKPOINT_PROVENANCE.json").read_text())[
            "checkpoint_phase"
        ]
        == phase
    )


@pytest.mark.parametrize("target", list(targets.DETAILS))
def test_new_targets_route_languages_and_have_real_helper_contracts(target):
    import re

    assert forge.TOOLCHAIN_LANG[target] in forge.LANGS
    for helper in re.findall(r"game\.(\w+)\(", forge.TOOLCHAINS[target]["test_help"]):
        assert callable(getattr(game, helper, None)), (target, helper)
    compile(forge.SELFTEST_SCRIPT % forge.SELFTEST_STEPS[target], target, "exec")
    assert forge.SELFTEST_FILES[target]


@pytest.mark.parametrize("key", list(targets.PROFILES))
def test_profiles_reuse_toolchain_and_select_separate_image(key):
    target, profile = key.split(":")
    assert key not in forge.TOOLCHAINS
    assert targets.spec_profile("Profile: " + profile, target) == profile
    forge.set_toolchain(target, profile)
    assert forge.Sandbox({}).profile()["image"] == targets.PROFILES[key]["image"]
    files, steps = targets.profile_smoke(target, profile)
    assert files
    compile(forge.SELFTEST_SCRIPT % steps, key, "exec")
    forge.set_toolchain(target)
    assert forge.Sandbox({}).profile()["image"] == f"forge-sandbox-{target}:latest"


def test_missing_tests_do_not_pass(project, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        game._extra_helpers["pytest_project"].__self__,
        "python_env",
        lambda: sys.executable,
    )
    monkeypatch.setattr(game, "run", lambda *a, **k: game.Result(0, "no tests"))
    assert not game.pytest_project().ok


def test_compile_failure_does_not_execute_a_stale_simulation(project, bindir):  # noqa: F811
    project.FILES = {"bad.sv": "invalid"}
    bindir("iverilog", "import sys;print('bad.sv:1: syntax error');sys.exit(2)")
    bindir("vvp", "raise Exception('must not execute stale output')")
    result = game.hdl_sim("top")
    assert not result.ok and "syntax error" in result.out
    with pytest.raises(ValueError, match="invalid HDL"):
        game.hdl_synth("top; shell command")


def test_parallel_pause_drains_running_chunks_and_stops_dispatch(tmp_path, monkeypatch):
    chunks = [
        dict(id="c" + str(i), spec="value", verify="assert True") for i in range(3)
    ]
    run = make_run(tmp_path, plan=dict(project="p", chunks=chunks))
    monkeypatch.setattr(forge, "infer_dependencies", lambda run: None)
    monkeypatch.setattr(forge, "later_names", lambda *a: [])
    monkeypatch.setattr(forge, "move_later_checks", lambda *a: False)
    monkeypatch.setattr(forge, "route_coders", lambda *a: None)
    started = []
    finish = threading.Event()
    exceptions = []

    def work(run, cfg, planner, coder, sandbox, chunk):
        started.append(chunk["id"])
        assert finish.wait(3)
        with run.edit() as state:
            state["chunks"][chunk["id"]] = {"status": "verified"}

    monkeypatch.setattr(forge, "run_code_chunk", work)

    def build():
        try:
            forge.build_code_chunks(
                run, {"loop": {"parallel_chunks": 2}}, None, None, None, None
            )
        except BaseException as error:
            exceptions.append(error)

    thread = threading.Thread(target=build)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while len(started) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(started) == 2
        control.request(run.root, "pause")
        finish.set()
        thread.join(3)
    finally:
        finish.set()
        thread.join(3)
    assert sorted(started) == ["c0", "c1"] and not thread.is_alive()
    assert len(exceptions) == 1 and isinstance(exceptions[0], forge.CheckpointStop)


def test_recovery_never_restores_live_or_unconfigured_service(tmp_path, monkeypatch):
    home = forge.FORGE_HOME
    home.mkdir(parents=True)
    row = dict(name="shapes", host="host", units=["strata"], owner=control.owner())
    path = home / "service-handoffs.json"
    path.write_text(json.dumps([row]))
    assert forge.recover_services({}, True)[0]["state"] == "owner is alive"
    row["owner"]["identity"] = "dead process identity"
    path.write_text(json.dumps([row]))
    assert forge.recover_services({}, True)[0]["state"] == "configuration mismatch"
    calls = []
    monkeypatch.setattr(forge, "_shape_ssh", lambda *a: calls.append(a) or True)
    import forge_recovery

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b'{"released":true}'

    monkeypatch.setattr(
        forge_recovery.urllib.request, "urlopen", lambda *a, **k: Reply()
    )
    cfg = {
        "shapes": {
            "servers": [
                dict(
                    name="shapes",
                    ssh="host",
                    url="http://shape",
                    free_cmd="systemctl --user stop strata",
                    restore_cmd="systemctl --user start strata",
                )
            ]
        }
    }
    assert forge.recover_services(cfg, True)[0]["state"] == "restored"
    assert (
        calls[0][1] == "systemctl --user start strata"
        and json.loads(path.read_text()) == []
    )


def load_dependencies():
    path = Path(forge.__file__).parent / "toolchain-images/locked_dependencies.py"
    spec = importlib.util.spec_from_file_location("locked_dependencies", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "text",
    [
        "a>=1",
        "a==1",
        "a @ https://example.com/a.whl",
        "--index-url https://example.com",
    ],
)
def test_lock_rejects_unpinned_or_unhashed_network_instructions(text):
    with pytest.raises(ValueError):
        load_dependencies().requirements(text)


def test_maven_offline_cache_requires_verified_bytes(tmp_path, monkeypatch):
    tool = load_dependencies()
    payload = b"jar"
    digest = hashlib.sha256(payload).hexdigest()
    lock = dict(artifacts=[dict(coordinate="org.example:demo:1.2", sha256=digest)])
    cache = tmp_path / "cache"
    cache.mkdir()
    path = cache / f"{digest[:16]}-demo-1.2.jar"
    path.write_bytes(payload)
    monkeypatch.setattr(
        tool.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("offline network attempt"),
    )
    tool.maven(lock, tmp_path / "deps", cache, True)
    assert (tmp_path / "deps" / path.name).read_bytes() == payload
    path.write_bytes(b"bad")
    with pytest.raises(ValueError, match="unavailable offline"):
        tool.maven(lock, tmp_path / "deps", cache, True)


def test_dependency_stage_cannot_execute_uploaded_gradle_or_setup_scripts():
    lock = "a==1 --hash=sha256:" + "a" * 64
    plan = forge._deps_plan(
        "python-app",
        {
            "requirements.lock": lock,
            "setup.py": "raise Exception()",
            "app.py": "secret",
        },
    )
    assert set(plan["files"]) == {"requirements.lock"} and "--offline" in plan[
        "offline"
    ](plan["script"])
    plan = forge._deps_plan(
        "kotlin-jvm",
        {"forge-dependencies.lock.json": "{}", "build.gradle.kts": "untrusted"},
    )
    assert set(plan["files"]) == {"forge-dependencies.lock.json"}


def test_cancel_stops_new_image_and_shape_dispatch(tmp_path, monkeypatch):
    run = make_run(tmp_path, plan=dict(project="p", chunks=[]))
    control.request(run.root, "cancel")
    pool = forge.ImagePool.__new__(forge.ImagePool)
    pool.run = run
    pool.cv = threading.Condition()
    pool.no_new = False
    pool.alive = {"server"}
    pool.shape_holding = set()
    monkeypatch.setattr(
        forge, "run_image_chunk", lambda *a, **k: pytest.fail("new image dispatched")
    )
    monkeypatch.setattr(
        forge, "run_shape", lambda *a, **k: pytest.fail("new shape dispatched")
    )
    pool._work({"name": "server"})
    pool._shape_work({"name": "server"})


def test_trusted_prewarm_templates_match_smoke_sources():
    root = Path(forge.__file__).parent / "toolchain-images/templates"
    for target, files in targets.SELFTEST_FILES.items():
        for name, content in files.items():
            assert (root / target / name).read_text() == content, (target, name)


def test_native_worker_rejects_wrong_platform_and_unapproved_bytes(tmp_path):
    path = Path(forge.__file__).parent / "tools/desktop_worker.py"
    spec = importlib.util.spec_from_file_location("desktop_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("pass")
    wrong = "windows" if worker.native_platform() != "windows" else "linux"
    with pytest.raises(ValueError, match="native worker"):
        worker.build(source, tmp_path / "out", wrong)
    artifact = tmp_path / "app.tar.gz"
    artifact.write_bytes(b"unapproved")
    with pytest.raises(ValueError, match="approved build"):
        worker.sign(artifact, tmp_path / "out", "0" * 64, "certificate selector")


def test_legacy_spec_filename_is_not_treated_as_a_new_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "control").write_text("# A project named control\n")
    cfg = tmp_path / "config.toml"
    cfg.write_text('[paths]\nruns="' + str(tmp_path / "runs") + '"\n')
    seen = []
    monkeypatch.setattr(forge, "execute", lambda run, cfg: seen.append(run.spec))
    assert forge.main(["--config", str(cfg), "run", "control"]) == 0
    assert seen == ["# A project named control\n"]


def test_recovery_failure_has_nonzero_cli_status_and_stops_model_work(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        forge, "recover_services", lambda *a: [{"state": "failed", "name": "shapes"}]
    )
    assert forge.main(["recover", "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["services"][0]["state"] == "failed"
    run = make_run(tmp_path, plan=dict(project="p", chunks=[]))
    with pytest.raises(forge.ForgeError, match="service recovery failed"):
        forge.execute(run, {"paths": {"runs": str(run.root.parent)}})


def test_legacy_service_restore_is_dispatched_after_gpu_release(monkeypatch):
    import types

    events = []

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    class Threads:
        Thread = ImmediateThread

        def __getattr__(self, name):
            return getattr(threading, name)

    monkeypatch.setattr(forge, "threading", Threads())
    monkeypatch.setattr(forge, "_SHAPE_FREED", {"shape"})
    monkeypatch.setattr(
        forge, "_shape_ssh", lambda *args: events.append("restore") or True
    )

    def released(*args, **kwargs):
        events.append("release")
        return types.SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(forge.urllib.request, "urlopen", released)
    forge.release_shape_server(
        {},
        {"name": "shape", "url": "http://shape", "restore_cmd": "custom restore"},
        wait=False,
    )
    assert events == ["release", "restore"]


def test_profile_limit_overrides_preserve_docker_argument_types():
    forge.set_toolchain("node", "frontend")
    cfg = {
        "profiles": {
            "node:frontend": {"cpus": 0.5, "timeout": 42, "pids": 128, "tmpfs_mb": 256}
        }
    }
    profile = forge.Sandbox(cfg).profile()
    assert profile["cpus"] == "0.5"
    assert (
        profile["timeout"] == 42
        and profile["pids"] == 128
        and profile["tmpfs_mb"] == 256
    )


def test_oversized_png_header_remains_hash_only_without_decoding(tmp_path):
    import struct
    import zlib

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    root = tmp_path / "run"
    (root / "outputs").mkdir(parents=True)
    path = root / "outputs/large.png"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 50000, 50000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )
    record = provenance.artifacts(root)["outputs/large.png"]
    assert record["sha256"] == provenance.digest(path) and "image" not in record
