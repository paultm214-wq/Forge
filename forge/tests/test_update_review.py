"""Regressions found while reviewing the supplied incremental update."""
import json
import subprocess

import pytest

import forge
from test_offline import boxer_server, branches, claude, e2e  # noqa: F401


@pytest.mark.parametrize("exit_code", [0, 3])
def test_output_watch_checks_fast_process_exit(tmp_path, exit_code):
    sandbox = forge.Sandbox({"unsafe_local_exec": True, "timeout": 10, "out_watch_mb": 1})
    out = tmp_path / "out"
    code = ("import pathlib, sys\n"
            "pathlib.Path(sys.argv[1], 'large.bin').write_bytes(b'x' * (2 * 2**20))\n"
            f"sys.exit({exit_code})\n")
    ok, diagnostic = sandbox.run({"writer.py": code}, "writer.py", ["OUT"], out_dir=out)
    assert not ok and "OUTPUT LIMIT" in diagnostic
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("file", ["state.json", "plan.json"])
@pytest.mark.parametrize("value", [[], None, 1, "invalid"])
def test_status_rejects_nonobject_run_documents(tmp_path, capsys, file, value):
    (tmp_path / "state.json").write_text(json.dumps({"chunks": {}}))
    (tmp_path / "plan.json").write_text(json.dumps({"chunks": []}))
    (tmp_path / file).write_text(json.dumps(value))
    assert forge.main(["status", str(tmp_path), "--json"]) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert file in error and "object" in error


@pytest.mark.parametrize("tree", ["assets", "outputs"])
def test_pending_commits_changed_deliverables_even_if_code_is_identical(tmp_path, monkeypatch,
                                                                       claude, tree):  # noqa: F811
    server = boxer_server()
    try:
        claude("overloaded")
        cfg, run = e2e(tmp_path, server)
        if tree == "outputs":
            # A build output changes between attempts, without changing the
            # generated module (e.g. regenerated fixture or rendered result).
            original = forge.build_outputs
            payload = [b"original"]

            def build_outputs(run, sandbox, module):
                original(run, sandbox, module)
                (run.root / "outputs").mkdir(exist_ok=True)
                (run.root / "outputs" / "result.bin").write_bytes(payload[0])

            monkeypatch.setattr(forge, "build_outputs", build_outputs)
            rel = "outputs/result.bin"
        else:
            rel = "assets/result.bin"
            (run.root / rel).write_bytes(b"original")
        first = forge.execute(run, cfg)
        if tree == "outputs":
            payload[0] = b"updated"
        else:
            (run.root / rel).write_bytes(b"updated")
        assert forge.cmd_pending(cfg, run.root.parent, "run", run.root.name) == 0
        state = forge.Run(run.root).state
        assert state["branch"] != first
        assert len(branches(tmp_path)) == 2
        result = subprocess.run(["git", "-C", cfg["git"]["repo"], "show",
                                 f"{state['branch']}:projects/boxer/{rel}"],
                                capture_output=True, check=True)
        assert result.stdout == b"updated"
    finally:
        server.close()
