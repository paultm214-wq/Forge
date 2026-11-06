"""Regressions for the November 6 review: review verdicts, `forge pending`
while Claude stays down, the Claude budget, the recheck on resume, the /out
watch, `forge status`, and host-side path checks."""
import json
import os
import subprocess
import sys
import time

import pytest

import forge
from conftest import make_run
from test_forge import C1, C2_FIXED, TWO_CHUNKS
from test_offline import boxer_server, branches, claude, e2e  # noqa: F401  (claude: fixture)


# ------------------------------------------------------------ verdicts

def test_well_formed_verdicts_are_unchanged():
    v = {"approved": True, "issues": [{"severity": "minor", "chunk": "c1", "description": "x",
                                      "fix": "y"}], "status": "ok", "cost_usd": 0.1}
    assert forge.normalize_verdict(v, {"c1"}) == v
    v = {"approved": False, "issues": [{"severity": "blocking", "chunk": "c2", "description": "d"}]}
    assert forge.normalize_verdict(v) == v


@pytest.mark.parametrize("sev", ["Blocking", "BLOCKING", "critical", "High", " major "])
def test_blocking_synonyms_block(sev):
    v = forge.normalize_verdict({"approved": False, "issues": [
        {"severity": sev, "chunk": "c1", "description": "d"}]})
    assert [i["severity"] for i in v["issues"]] == ["blocking"]


def test_malformed_verdicts_are_normalized_not_fatal():
    v = forge.normalize_verdict({"approved": "false", "issues": ["c2: box() is wrong", 7, None]},
                                {"c1", "c2"})
    assert v["approved"] is False
    assert v["issues"][0] == {"severity": "blocking", "chunk": "c2", "description": "box() is wrong"}
    assert all(isinstance(i, dict) and isinstance(i["description"], str) for i in v["issues"])
    assert forge.normalize_verdict({"approved": True, "issues": None}) == {"approved": True,
                                                                           "issues": []}
    assert forge.normalize_verdict({"approved": "yes", "issues": "looks fine"})["approved"] is True


def test_a_rejection_naming_no_blocking_issue_gets_one():
    v = forge.normalize_verdict({"approved": False, "issues": [
        {"severity": "minor", "chunk": "c1", "description": "naming"}]})
    blocking = [i for i in v["issues"] if i["severity"] == "blocking"]
    assert len(blocking) == 1 and blocking[0]["chunk"] is None and "naming" in blocking[0]["description"]
    assert forge.normalize_verdict({"approved": False})["issues"][0]["severity"] == "blocking"


def _no_fixes(monkeypatch):
    """Review findings are left unfixed (fixing them would ask the models for
    regression tests); the build's own regression repair still runs."""
    real = forge.apply_fixes

    def apply(run, cfg, planner, coder, sandbox, issues, source):
        if source == "regression":
            return real(run, cfg, planner, coder, sandbox, issues, source)
        return list(issues)
    monkeypatch.setattr(forge, "apply_fixes", apply)


@pytest.mark.parametrize("issues", [[], [{"severity": "critical", "chunk": "c2", "description": "x"}]])
def test_claude_rejection_without_a_blocking_issue_is_bounded(tmp_path, monkeypatch, issues):
    """It looped with no limit (one paid review per round): the image-only
    branch took every rejection whose issues were not spelled "blocking"."""
    srv = boxer_server()
    calls = []

    def review(cfg, workdir, disputed=None, only_images=None):
        calls.append(only_images)
        assert len(calls) <= 4, "Claude review loop is unbounded"
        return {"status": "ok", "approved": False, "cost_usd": 0.0, "issues": list(issues)}
    monkeypatch.setattr(forge, "claude_review", review)
    _no_fixes(monkeypatch)
    try:
        cfg, run = e2e(tmp_path, srv)
        cfg["loop"].update(max_review_rounds=2, auto_change=0)
        with pytest.raises(forge.ForgeError, match="blocking issue"):
            forge.execute(run, cfg)
        assert len(calls) == 2
    finally:
        srv.close()


def test_self_review_string_issues_do_not_crash(tmp_path, monkeypatch):
    srv = boxer_server()
    monkeypatch.setattr(forge, "self_review", lambda planner, run, module, rnd=1: {
        "approved": False, "issues": ["c2: box() is wrong"]})
    _no_fixes(monkeypatch)
    try:
        cfg, run = e2e(tmp_path, srv)
        cfg["claude"]["enabled"] = False
        with pytest.raises(forge.ForgeError, match="self review still has 1 blocking"):
            forge.execute(run, cfg)
        last = forge.Run(run.root).state["reviews"][-1]
        assert last["issues"][0]["chunk"] == "c2" and last["issues"][0]["severity"] == "blocking"
    finally:
        srv.close()


def test_claude_budget_stops_the_review_loop(tmp_path, monkeypatch):
    srv = boxer_server()
    calls = []

    def review(cfg, workdir, disputed=None, only_images=None):
        calls.append(1)
        return {"status": "ok", "approved": False, "cost_usd": 0.6,
                "issues": [{"severity": "blocking", "chunk": "c2", "description": "x"}]}
    monkeypatch.setattr(forge, "claude_review", review)
    _no_fixes(monkeypatch)
    try:
        cfg, run = e2e(tmp_path, srv)
        cfg["loop"].update(max_review_rounds=5, auto_change=0)
        cfg["claude"]["max_cost_usd"] = 1.0
        with pytest.raises(forge.ForgeError, match="budget reached"):
            forge.execute(run, cfg)
        assert len(calls) == 2 and forge.Run(run.root).state["claude_cost_usd"] == pytest.approx(1.2)
    finally:
        srv.close()


# ------------------------------------------------------------ forge pending

def test_pending_does_not_recommit_while_claude_stays_down(tmp_path, claude):  # noqa: F811
    """Every timer tick with the internet up but Claude still failing made a
    new branch of the same code (and re-ran the regression)."""
    srv = boxer_server()
    try:
        claude("overloaded")
        cfg, run = e2e(tmp_path, srv)
        first = forge.execute(run, cfg)
        runs = run.root.parent
        st = forge.Run(run.root).state
        assert st["deferred_hash"] and branches(tmp_path) == [first]

        assert forge.cmd_pending(cfg, runs, "run") == 0          # Claude still overloaded
        st = forge.Run(run.root).state
        assert branches(tmp_path) == [first]
        assert st["phase"] == "committed" and st["branch"] == first and "claude_review" in st["deferred"]
        assert st["deferred_tries"] == 1 and st["deferred_retry_at"] > time.time() + 25 * 60

        n = claude.count()
        assert forge.cmd_pending(cfg, runs, "run") == 0          # backing off: not even tried
        assert claude.count() == n and branches(tmp_path) == [first]

        claude("ok")                                              # Claude is back
        assert forge.cmd_pending(cfg, runs, "run", run.root.name) == 0   # now, despite the backoff
        st = forge.Run(run.root).state
        assert "deferred" not in st and st["branch"] != first and st["deferred_from"] == first
        assert not {"deferred_hash", "deferred_tries", "deferred_retry_at"} & set(st)
        assert branches(tmp_path) == sorted([first, st["branch"]])
    finally:
        srv.close()


def test_pending_list_shows_the_backoff(tmp_path, capsys):
    r = make_run(tmp_path, state={"phase": "committed", "branch": "b",
                                  "deferred": {"claude_review": {"since": "2026-11-06 10:00"}},
                                  "deferred_tries": 2, "deferred_retry_at": time.time() + 3600})
    forge.cmd_pending({}, r.root.parent, "list")
    assert "next try in 60 min" in capsys.readouterr().out
    with pytest.raises(forge.ForgeError):
        forge.cmd_pending({}, r.root.parent, "run", "no-such-run")


# ------------------------------------------------------------ recheck on resume

class Scripted:
    """A sandbox whose results are given in order."""

    def __init__(self, results):
        self.results, self.calls = list(results), 0

    def run(self, files, entry, *a, **kw):
        self.calls += 1
        return self.results.pop(0)


def test_recheck_retries_once_and_keeps_the_old_code(tmp_path):
    run = make_run(tmp_path, plan=TWO_CHUNKS, verified={"c1": C1, "c2": C2_FIXED})
    (run.root / "chunks" / "c2" / "attempt_1").mkdir(parents=True)
    sb = Scripted([(True, "ok"),                          # the sandbox works
                   (False, "TIMEOUT after 60s"), (True, "PASS"),   # c1: flaky, kept
                   (False, "AssertionError: x"), (False, "AssertionError: x")])  # c2: stale
    assert forge.recheck_verified(run, sb) == ["c2"] and sb.calls == 5
    assert (run.root / "verified" / "c1.py").exists()
    assert not (run.root / "verified" / "c2.py").exists()
    kept = list((run.root / "chunks").glob("c2-stale-*/verified.py"))
    assert len(kept) == 1 and kept[0].read_text() == C2_FIXED
    assert (kept[0].parent / "attempt_1").is_dir()
    st = forge.Run(run.root).state["chunks"]
    assert st["c1"]["status"] == "verified" and st["c2"]["status"] == "pending"


# ------------------------------------------------------------ the /out watch

WRITER = "import sys, os\nd = sys.argv[1]\ni = 0\nwhile True:\n" \
         "    open(os.path.join(d, f'f{i}.bin'), 'wb').write(b'x' * 262144)\n    i += 1\n"


def test_runaway_output_is_stopped_and_cleared(tmp_path):
    sb = forge.Sandbox({"unsafe_local_exec": True, "timeout": 60, "out_watch_mb": 2})
    out = tmp_path / "out"
    t0 = time.monotonic()
    ok, msg = sb.run({"w.py": WRITER}, "w.py", ["OUT"], out_dir=out)
    assert not ok and "OUTPUT LIMIT" in msg and time.monotonic() - t0 < 30
    assert list(out.iterdir()) == []


def test_output_under_the_watch_is_kept(tmp_path):
    sb = forge.Sandbox({"unsafe_local_exec": True, "timeout": 60, "out_watch_mb": 2})
    out = tmp_path / "out"
    ok, msg = sb.run({"w.py": "import sys\nopen(sys.argv[1] + '/a.txt', 'w').write('hi')\n"
                             "print('done')"}, "w.py", ["OUT"], out_dir=out)
    assert ok and msg.strip() == "done" and (out / "a.txt").read_text() == "hi"
    sb = forge.Sandbox({"unsafe_local_exec": True, "timeout": 1, "out_watch_mb": 2})
    ok, msg = sb.run({"s.py": "import time\ntime.sleep(30)"}, "s.py", ["OUT"], out_dir=out)
    assert not ok and msg.startswith("TIMEOUT")


def test_watch_default_and_off():
    assert forge.Sandbox({}).out_watch_bytes == 2048 * 2**20
    assert forge.Sandbox({"outputs_max_mb": 1000}).out_watch_bytes == 4000 * 2**20
    assert forge.Sandbox({"out_watch_mb": 0}).out_watch_bytes == 0


def test_docker_container_is_killed_on_overflow(tmp_path, monkeypatch):
    """With Docker the CLI process is not the container: it is killed by name."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker-calls"
    (bindir / "docker").write_text(
        f"#!{sys.executable}\nimport sys, os\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:3]) + '\\n')\n"
        "if sys.argv[1] == 'run':\n"
        "    out = next(a.split(':')[0] for a in sys.argv if a.endswith(':/out:rw'))\n"
        "    i = 0\n"
        "    while True:\n"
        "        open(os.path.join(out, f'f{i}'), 'wb').write(b'x' * 262144); i += 1\n")
    (bindir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    sb = forge.Sandbox({"timeout": 60, "out_watch_mb": 2})
    ok, msg = sb.run({"w.py": "pass"}, "w.py", ["OUT"], out_dir=tmp_path / "out")
    assert not ok and "OUTPUT LIMIT" in msg
    assert [ln.split()[0] for ln in log.read_text().splitlines()] == ["run", "kill"]


# ------------------------------------------------------------ forge status

def test_status_reads_a_run_without_config(tmp_path, capsys):
    run = make_run(tmp_path, plan=TWO_CHUNKS, verified={"c1": C1},
                   state={"phase": "committed", "branch": "forge/boxer-x",
                          "chunks": {"c1": {"status": "verified"}, "c2": {"status": "failed"}},
                          "claude_cost_usd": 0.42,
                          "reviews": [{"source": "self", "round": 1, "approved": True, "issues": []},
                                      {"source": "claude", "round": 2, "approved": False,
                                       "issues": [{"severity": "blocking"}]}],
                          "deferred": {"push": {"since": "2026-11-06 09:00", "reason": "down"}}})
    before = sorted(p.name for p in run.root.iterdir())
    assert forge.main(["--config", str(tmp_path / "none.toml"), "status", str(run.root), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["phase"] == "committed" and info["project"] == "boxer"
    assert info["chunks"]["verified"] == ["c1"] and info["chunks"]["failed"] == ["c2"]
    assert info["reviews"]["claude"] == {"round": 2, "approved": False, "blocking": 1}
    assert info["claude_cost_usd"] == 0.42 and "push" in info["deferred"] and info["busy"] is None
    assert forge.main(["status", str(run.root)]) == 0
    text = capsys.readouterr().out
    assert "phase:    committed" in text and "failed:   c2" in text and "push to the git host" in text
    assert sorted(p.name for p in run.root.iterdir()) == before        # read-only


def test_status_shows_a_run_in_use(tmp_path, capsys):
    run = make_run(tmp_path, plan=TWO_CHUNKS)
    with forge.run_lock(run):
        info = forge.run_status(run.root)
    assert info["busy"] and "pid" in info["busy"]


def test_status_of_a_non_run(tmp_path, capsys):
    assert forge.main(["status", str(tmp_path), "--json"]) == 1
    assert "not a forge run" in json.loads(capsys.readouterr().out)["error"]


# ------------------------------------------------------------ host-side paths

def test_safe_path_refuses_a_trailing_newline():
    assert forge.SAFE_PATH.match("scripts/a.gd")
    assert not forge.SAFE_PATH.match("scripts/a.gd\n")


def test_catalog_alias_with_a_newline_is_refused():
    entry, why = forge.validate_entry({"alias": "coder\n", "repo": "a/b"}, {}, {})
    assert entry is None and "bad alias" in why
    entry, why = forge.validate_entry({"alias": "coder", "repo": "a/b\n"}, {}, {})
    assert entry is None and "bad repo" in why


def test_project_files_outside_the_project_are_refused(tmp_path, monkeypatch):
    run = make_run(tmp_path, plan=TWO_CHUNKS)
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    module = forge.files_to_python({"main.gd": "x"}) + "FILES['../escape.txt'] = 'y'\n"
    with pytest.raises(forge.ForgeError, match="unsafe project file path"):
        forge.build_files_outputs(run, forge.Sandbox({"unsafe_local_exec": True}), module)
    assert not (run.root / "outputs" / "escape.txt").exists()


def test_atomic_write_leaves_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text("old")

    def fail(self, other):
        raise OSError("No space left on device")
    monkeypatch.setattr(forge.Path, "replace", fail)
    with pytest.raises(OSError):
        forge.atomic_write(target, "new")
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"] and target.read_text() == "old"


def test_sandbox_memory_message_names_the_profile_limit(tmp_path, fake_docker, monkeypatch):
    fake_docker(137)
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")        # a toolchain with its own memory
    sb = forge.Sandbox({"memory": "1g"})
    ok, msg = sb.run({"v.py": "pass"}, "v.py")
    assert not ok and f"({sb.profile()['memory']})" in msg and sb.profile()["memory"] != "1g"


def test_subprocess_is_still_used_without_out_dir(monkeypatch, tmp_path):
    seen = []

    class P:
        returncode, stdout, stderr = 0, "PASS", ""
    monkeypatch.setattr(forge.subprocess, "run", lambda cmd, **kw: seen.append(kw) or P())
    ok, _ = forge.Sandbox({}).run({"v.py": "pass"}, "v.py")
    assert ok and seen and seen[0]["timeout"] == 120 and seen[0]["capture_output"]
    assert subprocess.run is not None
