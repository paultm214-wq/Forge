"""Tests for the forge.py changes described in CHANGES.md, section by section.

Run from the forge directory:   python3 -m pytest -q tests
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

import forge
from conftest import FakeLLM, FakeServer, make_run


# =============================================================================
# 1. Sandbox: outputs copy, Docker failures, exit 137, swap
# =============================================================================

def test_copy_outputs_refuses_a_symlink_to_a_host_file(tmp_path):
    secret = tmp_path / "api-key"
    secret.write_text("sk-SECRET")
    src, dst = tmp_path / "raw", tmp_path / "outputs"
    src.mkdir()
    os.symlink(secret, src / "harmless.txt")
    with pytest.raises(forge.ForgeError, match="symlink"):
        forge.copy_outputs(src, dst, 10**6)
    assert not any(p.is_file() and p.read_text() == "sk-SECRET" for p in dst.rglob("*"))


def test_copy_outputs_refuses_a_symlinked_directory(tmp_path):
    (tmp_path / "home").mkdir()
    src = tmp_path / "raw"
    src.mkdir()
    os.symlink(tmp_path / "home", src / "dir")
    with pytest.raises(forge.ForgeError, match="symlink"):
        forge.copy_outputs(src, tmp_path / "out", 10**6)


def test_copy_outputs_copies_files_and_folders(tmp_path):
    src = tmp_path / "raw"
    (src / "sub").mkdir(parents=True)
    (src / "a.svg").write_text("<svg/>")
    (src / "sub" / "b.txt").write_text("b")
    forge.copy_outputs(src, tmp_path / "out", 10**6)
    assert (tmp_path / "out" / "a.svg").read_text() == "<svg/>"
    assert (tmp_path / "out" / "sub" / "b.txt").read_text() == "b"


def test_copy_outputs_enforces_the_size_cap(tmp_path):
    src = tmp_path / "raw"
    src.mkdir()
    (src / "big.bin").write_bytes(b"x" * 2000)
    with pytest.raises(forge.ForgeError, match="outputs_max_mb"):
        forge.copy_outputs(src, tmp_path / "out", 1000)


def test_build_outputs_never_copies_what_a_symlink_points_at(tmp_path, local_sandbox):
    secret = tmp_path / "coder-api-key"
    secret.write_text("sk-SECRET")
    run = make_run(tmp_path)
    module = ("import os\n"
              "def build_outputs(out_dir):\n"
              f"    os.symlink({str(secret)!r}, os.path.join(out_dir, 'x.txt'))\n")
    with pytest.raises(forge.ForgeError, match="symlink"):
        forge.build_outputs(run, local_sandbox, module)
    leaked = [p for p in run.root.rglob("*") if p.is_file() and not p.is_symlink()
              and p.read_bytes() == b"sk-SECRET"]
    assert leaked == []


@pytest.mark.parametrize("code,stderr", [
    (1, "failed to connect to the docker API at unix:///var/run/docker.sock; check if "
        "the path is correct and if the daemon is running"),
    (1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the "
        "docker daemon running?"),
    (125, "docker: Error response from daemon: No such image: forge-sandbox:latest."),
])
def test_docker_failures_raise_instead_of_failing_the_test(fake_docker, code, stderr):
    fake_docker(code, stderr=stderr)
    with pytest.raises(forge.SandboxError, match="not a test result"):
        forge.Sandbox({}).run({"t.py": "print(1)"}, "t.py")


def test_a_failing_test_is_still_a_test_result(fake_docker):
    fake_docker(1, stdout="", stderr="Traceback (most recent call last):\n"
                                     "AssertionError: expected 5, got 4\n")
    ok, out = forge.Sandbox({}).run({"t.py": "x"}, "t.py")
    assert ok is False and "expected 5" in out


def test_exit_137_says_memory(fake_docker):
    fake_docker(137)
    ok, out = forge.Sandbox({"memory": "1g"}).run({"t.py": "x"}, "t.py")
    assert not ok and "memory limit (1g)" in out


def test_missing_docker_binary_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(forge.SandboxError):
        forge.Sandbox({}).run({"t.py": "x"}, "t.py")


def test_swap_is_capped_at_the_memory_limit(fake_docker):
    args = fake_docker(0, stdout="PASS")
    forge.Sandbox({"memory": "768m"}).run({"t.py": "x"}, "t.py")
    argv = json.loads(args.read_text())
    assert argv[argv.index("--memory-swap") + 1] == "768m"


def test_sandbox_error_stops_a_chunk_build_and_keeps_verified_code(tmp_path, fake_docker):
    """The case that used to delete work: Docker dies mid-run."""
    fake_docker(1, stderr="failed to connect to the docker API at unix:///x")
    plan = {"project": "p", "chunks": [
        {"id": "c1", "kind": "code", "spec": "x", "verify": "print('PASS')", "depends_on": []},
        {"id": "c2", "kind": "code", "spec": "y", "verify": "print('PASS')", "depends_on": ["c1"]}]}
    run = make_run(tmp_path, plan=plan, verified={"c1": "X = 1\n"})
    coder = FakeLLM({"precise Python engineer": lambda u: "```python\nY = 2\n```"})
    with pytest.raises(forge.SandboxError):
        forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                             forge.Sandbox({}), plan["chunks"][1])
    assert (run.root / "verified" / "c1.py").read_text() == "X = 1\n"


# =============================================================================
# 2. LLM client: 503 retry, max_tokens clamp, token cache, per-thread `last`
# =============================================================================

class StatusServer:
    """Answers /tokenize with the given statuses in turn, then 200."""

    def __init__(self, statuses):
        import http.server
        self.statuses, self.hits = list(statuses), 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                outer.hits += 1
                code = outer.statuses.pop(0) if outer.statuses else 200
                raw = (json.dumps({"tokens": [1, 2, 3]}) if code == 200
                       else '{"error": "Loading model"}').encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(forge.time, "sleep", lambda s: None)


def test_503_while_loading_is_retried(no_sleep):
    srv = StatusServer([503, 503])
    try:
        llm = forge.LLM("coder", {"url": srv.url, "retry_minutes": 1})
        assert llm.tokens("hello") == 3
        assert srv.hits == 3
    finally:
        srv.close()


def test_400_is_not_retried(no_sleep):
    srv = StatusServer([400])
    try:
        llm = forge.LLM("coder", {"url": srv.url, "retry_minutes": 1})
        with pytest.raises(forge.ForgeError, match="HTTP 400"):
            llm.tokens("hello")
        assert srv.hits == 1
    finally:
        srv.close()


def test_503_gives_up_at_the_deadline(no_sleep):
    srv = StatusServer([503] * 10)
    try:
        llm = forge.LLM("coder", {"url": srv.url, "retry_minutes": 0})
        with pytest.raises(forge.ForgeError, match="HTTP 503"):
            llm.tokens("hello")
    finally:
        srv.close()


def test_chat_never_asks_for_more_than_the_context_has_left():
    srv = FakeServer([("SYS", lambda u, b: "ok")])
    try:
        llm = forge.LLM("planner", {"url": srv.url, "context": 10_000, "max_tokens": 120_000,
                                    "prompt_budget": 5000})
        assert llm.review_max_tokens == 240_000          # the old default, unchanged
        llm.chat("SYS", "x" * 8000, max_tokens=llm.review_max_tokens)
        asked = srv.calls[-1][1]["max_tokens"]
        # 10000 context - 2000 prompt tokens - 0 system - 512 template margin
        assert asked == 10_000 - 2000 - forge.LLM.TEMPLATE_MARGIN
    finally:
        srv.close()


def test_chat_refuses_a_prompt_that_leaves_no_room():
    srv = FakeServer([("SYS", lambda u, b: "ok")])
    try:
        llm = forge.LLM("planner", {"url": srv.url, "context": 2000, "prompt_budget": 1000})
        with pytest.raises(forge.BudgetExceeded):
            llm.chat("SYS", "x" * 7000)
        assert srv.calls == []
    finally:
        srv.close()


def test_token_cache_is_bounded(monkeypatch):
    srv = FakeServer([])
    try:
        llm = forge.LLM("coder", {"url": srv.url})
        monkeypatch.setattr(forge.LLM, "TOKCACHE_SIZE", 3)
        for i in range(10):
            llm.tokens("word " * i)
        assert len(llm._tokcache) == 3
        assert all(len(k) == 64 for k in llm._tokcache)       # hashes, not prompts
    finally:
        srv.close()


def test_last_is_per_thread():
    srv = FakeServer([("A", lambda u, b: "from A"), ("B", lambda u, b: "from B")])
    try:
        llm = forge.LLM("planner", {"url": srv.url, "context": 50_000})
        llm.chat("A", "q")
        t = threading.Thread(target=lambda: llm.chat("B", "q"))
        t.start()
        t.join()
        assert llm.last["content"] == "from A"
    finally:
        srv.close()


def test_config_sets_review_max_tokens_inside_the_context():
    import tomllib
    cfg = tomllib.loads((Path(__file__).parent.parent / "config.toml").read_text())
    p = forge.LLM("planner", cfg["planner"])
    assert p.review_max_tokens + p.prompt_budget < p.context


# =============================================================================
# 3. run.state: changes under the lock image threads save under
# =============================================================================

def test_state_changes_and_saves_do_not_race(tmp_path, monkeypatch):
    import json.encoder
    monkeypatch.setattr(json.encoder, "c_make_encoder", None)   # Python 3.12's path
    run = make_run(tmp_path, state={"chunks": {f"c{i}": {"status": "verified"}
                                                for i in range(50)}})
    errors, stop = [], threading.Event()

    def image_thread():                      # what run_image_chunk does
        while not stop.is_set():
            try:
                with forge.Run.lock:
                    run.save()
            except Exception as exc:         # pragma: no cover - the bug
                errors.append(exc)

    t = threading.Thread(target=image_thread, daemon=True)
    t.start()
    try:
        for i in range(3000):
            with run.edit() as st:
                st["chunks"][f"x{i % 300}"] = {"status": "pending"}
                st["chunks"].pop(f"x{(i + 150) % 300}", None)
    finally:
        stop.set()
        t.join()
    assert errors == []
    assert json.loads(run.state_path.read_text())["chunks"]


# =============================================================================
# 4. Smaller confirmed bugs
# =============================================================================

@pytest.mark.parametrize("heading,fixture", [
    ("Latest rates (copy verbatim)", False),
    ("Testimonials (copy verbatim)", False),
    ("Contest rules (copy verbatim)", False),
    ("Test fixture and helpers (copy verbatim)", True),
    ("Testing helpers (copy verbatim)", True),
    ("Fixtures (copy verbatim)", True),
])
def test_verbatim_heading_words(heading, fixture):
    spec = f"# P\n## {heading}\n```python\nX = 1\n```\n"
    assert (forge.verbatim_code(spec, tests=True) is not None) == fixture
    assert (forge.verbatim_code(spec, tests=False) is not None) == (not fixture)


@pytest.mark.parametrize("fence", ["```python", "```py", "```Python", "```python3"])
def test_verbatim_accepts_python_fence_spellings(fence):
    assert forge.verbatim_code(f"## Vocab (copy verbatim)\n{fence}\nV = 1\n```\n") == "V = 1\n"


def test_verbatim_problems_reports_bad_and_missing_blocks():
    errors, warnings = forge.verbatim_problems(
        "## A (copy verbatim)\n```python\ndef f(:\n```\n## B (copy verbatim)\nprose only\n")
    assert len(errors) == 1 and "'A (copy verbatim)'" in errors[0]
    assert len(warnings) == 1 and "'B (copy verbatim)'" in warnings[0]


def test_ensure_verbatim_chunk_stops_on_a_broken_block(tmp_path, local_sandbox):
    run = make_run(tmp_path, spec="## Vocab (copy verbatim)\n```python\nX = (\n```\n",
                   plan={"project": "p", "chunks": []})
    with pytest.raises(forge.ForgeError, match="does not parse"):
        forge.ensure_verbatim_chunk(run, local_sandbox)


CONTRACT = {"contract": {"names": [
    {"name": "Store", "owner": "c2", "signature": "(self)"},
    {"name": "Store.add_lot", "owner": "c3", "signature": "(self)"},
    {"name": "f", "owner": "c4", "signature": "(a, b)"}]}}


def test_enforce_contract_keeps_an_emptied_class_parseable():
    code = "class Store:\n\n    def add_lot(self):\n        pass\n"
    new, removed, _ = forge.enforce_contract(code, "c4", CONTRACT)
    compile(new, "x", "exec")
    assert removed == ["Store.add_lot (owned by c3)"]


def test_enforce_contract_keeps_decorated_members_parseable():
    code = "class Store:\n    @property\n    def add_lot(self):\n        pass\n"
    new, _, _ = forge.enforce_contract(code, "c4", CONTRACT)
    compile(new, "x", "exec")
    assert "add_lot" not in new


@pytest.mark.parametrize("sig_code,ok", [
    ("def f(a, b): pass", True),
    ("def f(a, b, *, strict=False): pass", True),
    ("def f(a, b, c=None): pass", True),
    ("def f(a, b, **kw): pass", True),
    ("def f(a, b, c): pass", False),          # requires an argument callers never pass
    ("def f(a): pass", False),
    ("def f(b, a): pass", False),
    ("def f(a, b, *, strict): pass", False),
])
def test_contract_signature_compatibility(sig_code, ok):
    assert forge.signature_compatible(__import__("ast").parse(sig_code).body[0], "(a, b)") is ok
    problems = forge.enforce_contract(sig_code + "\n", "c4", CONTRACT)[2]
    assert (problems == []) is ok


def test_contract_optional_parameter_must_stay_optional():
    import ast
    node = ast.parse("def f(a, limit): pass").body[0]
    assert not forge.signature_compatible(node, "(a, limit=10)")
    assert forge.signature_compatible(ast.parse("def f(a, limit=5): pass").body[0],
                                      "(a, limit=10)")


def test_import_clash_between_chunks_is_reported():
    chunks = {"c1": "from datetime import datetime\ndef now():\n    return datetime.now()\n",
              "c4": "import datetime\ndef today():\n    return datetime.date.today()\n"}
    clashes = forge.binding_conflicts(chunks, ["c1", "c4"], "c4")
    assert len(clashes) == 1 and "datetime" in clashes[0] and "c1" in clashes[0]


def test_definition_shadowing_an_import_is_reported():
    chunks = {"c1": "import json\ndef dump(x):\n    return json.dumps(x)\n",
              "c2": "def json(x):\n    return x\n"}
    assert forge.binding_conflicts(chunks, ["c1", "c2"], "c2")


@pytest.mark.parametrize("a,b", [
    ("import os", "import os.path"),
    ("from os import path", "import os.path as path"),
    ("import json", "import json"),
    ("import datetime as dt", "import datetime"),
])
def test_compatible_imports_are_not_clashes(a, b):
    assert forge.binding_conflicts({"c1": a + "\n", "c2": b + "\n"}, ["c1", "c2"], "c2") == []


def test_clashing_attempt_is_rejected_with_feedback(tmp_path, local_sandbox):
    plan = {"project": "p", "chunks": [
        {"id": "c1", "kind": "code", "spec": "now()", "depends_on": [],
         "verify": "assert now().year > 2000\nprint('PASS')"},
        {"id": "c2", "kind": "code", "spec": "today()", "depends_on": [],
         "verify": "assert today().year > 2000\nprint('PASS')"}]}
    run = make_run(tmp_path, plan=plan, verified={
        "c1": "from datetime import datetime\ndef now():\n    return datetime.now()\n"})
    replies = ["```python\nimport datetime\ndef today():\n    return datetime.date.today()\n```",
               "```python\nimport datetime as _dt\ndef today():\n    return _dt.date.today()\n```"]
    coder = FakeLLM({"precise Python engineer": replies,
                     "You diagnose": lambda u: "use an alias"})
    forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                         local_sandbox, plan["chunks"][1])
    assert "_dt" in (run.root / "verified" / "c2.py").read_text()
    second_prompt = coder.calls[1][1]
    assert "clashes with module-level names" in second_prompt


def test_class_extension_with_repeated_bases_is_merged():
    mod = forge.assemble({
        "c1": "class Base:\n    pass\nclass Store(Base):\n    def a(self):\n        return 1\n",
        "c2": "class Store(Base):\n    def b(self):\n        return 2\n"}, ["c1", "c2"])
    ns = {}
    exec(mod, ns)
    assert ns["Store"]().a() == 1 and ns["Store"]().b() == 2


def test_class_with_different_bases_still_replaces():
    mod = forge.assemble({
        "c1": "class A:\n    pass\nclass Store(A):\n    def a(self):\n        return 1\n",
        "c2": "class B:\n    pass\nclass Store(B):\n    def b(self):\n        return 2\n"},
        ["c1", "c2"])
    ns = {}
    exec(mod, ns)
    assert not hasattr(ns["Store"], "a") and ns["Store"]().b() == 2


@pytest.mark.parametrize("text,want", [
    ('{"a": 1}\nNote: use {x} later', {"a": 1}),
    ('Here:\n```json\n{"a": 2}\n```\nDone.', {"a": 2}),
    ('{"verify": "x = 1\nprint(x)"}', {"verify": "x = 1\nprint(x)"}),   # raw newline
    ('prose {not json} then {"a": 3}', {"a": 3}),
    ('{"a": "```python\\ncode\\n```"}', {"a": "```python\ncode\n```"}),
])
def test_extract_json_variants(text, want):
    assert forge.extract_json(text) == want


@pytest.mark.parametrize("text", ['{"a": }', "no json here", "[1, 2]"])
def test_extract_json_raises_a_forge_error(text):
    with pytest.raises(forge.BadJSON):
        forge.extract_json(text)
    assert issubclass(forge.BadJSON, forge.ForgeError)


@pytest.mark.parametrize("chunks,needle", [
    ([{"kind": "code", "spec": "x"}], "has no id"),
    (["c1"], "not an object"),
    ([{"id": "c1", "spec": "x", "depends_on": "c0"}], "depends_on"),
    ([{"id": "c1", "spec": "x", "verify": ["a"]}], "verify must be a string"),
    ([{"id": "c1", "spec": "x", "checks": "1, 2"}], "checks must be a list"),
])
def test_malformed_chunks_are_reported_not_crashed_on(chunks, needle):
    assert any(needle in e for e in forge.validate_outline({"chunks": chunks}))
    assert any(needle in e for e in forge.validate_plan({"chunks": chunks}))


def test_run_expands_a_tilde_path(tmp_path, monkeypatch):
    run = make_run(tmp_path, state={"phase": "build"})
    monkeypatch.setenv("HOME", str(run.root.parent))
    again = forge.Run(Path("~") / run.root.name)
    assert again.state_path == run.root / "state.json"
    assert again.state["phase"] == "build"


def test_resume_of_a_committed_run_does_nothing(tmp_path):
    run = make_run(tmp_path, state={"phase": "committed", "branch": "forge/p-1"})
    cfg = {"planner": {"url": "http://127.0.0.1:9"}, "coder": {"url": "http://127.0.0.1:9"}}
    assert forge.execute(run, cfg) == "forge/p-1"


def test_reopen_lets_rebuild_continue_a_committed_run(tmp_path):
    run = make_run(tmp_path, state={"phase": "committed", "branch": "forge/p-1"})
    forge.reopen(run)
    assert run.state["phase"] == "build"
    assert forge.Run(run.root).state["reopened_from"] == "forge/p-1"


def test_git_errors_are_forge_errors_with_git_s_message(tmp_path):
    with pytest.raises(forge.ForgeError, match="not a git repository"):
        forge.git(tmp_path, "log")


def test_failed_push_is_a_warning_and_is_recorded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    forge.git(repo, "init", "-b", "main")
    run = make_run(tmp_path, state={"phase": "committed"})
    cfg = {"git": {"repo": str(repo), "remote": "nowhere", "auto_push": True}}
    forge.push_branch(run, cfg, "main")                 # must not raise
    assert "push" in forge.Run(run.root).state["push_error"]


def test_atomic_write_leaves_no_temp_files(tmp_path):
    forge.write_plan(tmp_path, {"chunks": []})
    forge.atomic_write(tmp_path / "plan.json", json.dumps({"chunks": [1]}))
    assert json.loads((tmp_path / "plan.json").read_text()) == {"chunks": [1]}
    assert [p.name for p in tmp_path.iterdir()] == ["plan.json"]


# =============================================================================
# 5. Repair paths
# =============================================================================

TWO_CHUNKS = {"project": "boxer", "summary": "s", "chunks": [
    {"id": "c1", "kind": "code", "title": "show", "spec": "show(x) returns '<x>'",
     "depends_on": [], "verify": "assert show(1) == '<1>', show(1)\nprint('PASS')"},
    {"id": "c2", "kind": "code", "title": "box", "spec": "box(x) returns '[x]'",
     "depends_on": [], "verify": "assert box(2) == '[2]', box(2)\nprint('PASS')"}]}
C1 = "def _fmt(x):\n    return f'<{x}>'\n\ndef show(x):\n    return _fmt(x)\n"
C2_BREAKS_C1 = "def _fmt(x):\n    return f'[{x}]'\n\ndef box(x):\n    return _fmt(x)\n"
C2_FIXED = "def _box_fmt(x):\n    return f'[{x}]'\n\ndef box(x):\n    return _box_fmt(x)\n"


def test_find_breaker_names_the_chunk_that_breaks_another(tmp_path, local_sandbox):
    run = make_run(tmp_path, plan=TWO_CHUNKS, verified={"c1": C1, "c2": C2_BREAKS_C1})
    assert forge.find_breaker(run, local_sandbox, "c1", ["c1", "c2"]) == "c2"


def test_regression_failure_is_repaired_not_fatal(tmp_path, local_sandbox):
    run = make_run(tmp_path, plan=TWO_CHUNKS, verified={"c1": C1, "c2": C2_BREAKS_C1})
    coder = FakeLLM({"You are REVISING": [f"```python\n{C2_FIXED}```"]})
    module = forge.repair_regression(run, {}, coder, coder, local_sandbox, ["c1", "c2"], "test")
    assert "_box_fmt" in module
    assert forge.regression_failures(run, local_sandbox, module) == []
    prompt = coder.calls[0][1]
    assert "verified test of c1 fails" in prompt


def test_review_fix_that_breaks_another_chunk_is_rejected(tmp_path, local_sandbox):
    run = make_run(tmp_path, plan=TWO_CHUNKS,
                   verified={"c1": C1, "c2": "def box(x):\n    return '[' + str(x) + ']'\n"})
    coder = FakeLLM({"You are REVISING": [f"```python\n{C2_BREAKS_C1}```",
                                          f"```python\n{C2_FIXED}```"]})
    planner = FakeLLM({"regression test for one defect": ["```python\nprint('PASS')\n```"] * 2})
    issues = [{"severity": "blocking", "chunk": "c2", "description": "use a helper",
               "fix": "factor out the formatting"}]
    left = forge.apply_fixes(run, {}, planner, coder, local_sandbox, issues, "self")
    assert left == []
    assert (run.root / "verified" / "c2.py").read_text().strip() == C2_FIXED.strip()
    assert "these chunks' tests now fail" in coder.calls[1][1]


def test_review_fix_must_keep_the_contract(tmp_path, local_sandbox):
    plan = dict(TWO_CHUNKS, contract={"names": [
        {"name": "show", "owner": "c1", "signature": "(x)"},
        {"name": "box", "owner": "c2", "signature": "(x)"}]})
    run = make_run(tmp_path, plan=plan, verified={"c1": C1, "c2": C2_FIXED})
    renamed = "def boxed(x):\n    return f'[{x}]'\n\ndef box(x, y):\n    return boxed(x)\n"
    coder = FakeLLM({"You are REVISING": [f"```python\n{renamed}```", f"```python\n{C2_FIXED}```"]})
    forge.apply_fixes(run, {}, coder, coder, local_sandbox,
                      [{"chunk": "c2", "description": "d"}], "regression")
    assert "must take (x)" in coder.calls[1][1]


def test_image_job_error_does_not_retire_the_server(tmp_path, monkeypatch):
    run = make_run(tmp_path, plan={"chunks": []})
    monkeypatch.setattr(forge, "server_up", lambda srv, timeout=5: True)
    rendered = []

    def fake_render(run_, cfg, job, srv=None, reroll=0):
        if job["id"] == "bad":
            raise forge.JobError("reference image 'missing.jpg' not found")
        rendered.append((job["id"], srv["name"]))
        return 1.0

    monkeypatch.setattr(forge, "run_image_chunk", fake_render)
    jobs = [{"id": "bad", "kind": "image", "prompt": "p"},
            {"id": "good", "kind": "image", "prompt": "p"}]
    cfg = {"images": {"enabled": True, "servers": [{"name": "s1", "url": "http://x"},
                                                    {"name": "s2", "url": "http://y"}]}}
    pool = forge.ImagePool(run, cfg, jobs)
    pool.start()
    pool.join()
    assert [j for j, _ in rendered] == ["good"]
    assert pool.alive == {"s1", "s2"}
    assert len(pool.failed) == 1 and pool.failed[0].startswith("bad:")


def test_preflight_finds_a_missing_reference_image_before_rendering(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(forge.ForgeError, match="missing.jpg"):
        forge.preflight_images(run, [{"id": "hero", "reference_images": ["missing.jpg"]}])


def test_preflight_and_spec_reject_a_tile_without_size(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(forge.ForgeError, match="tileable needs width and height"):
        forge.preflight_images(run, [{"id": "bg", "tileable": True}])
    spec = ('## Assets\n```json\n[{"id": "bg", "filename": "bg.png", "prompt": "p", '
            '"tileable": true}]\n```\n')
    with pytest.raises(forge.ForgeError, match="tileable needs width and height"):
        forge.spec_assets(spec)


SPEC = """# Boxer

## Goal
Formatting helpers.

## Interfaces
- `show(x) -> str`
- `box(x) -> str`

## Acceptance
1. show(1) -> "<1>"
2. box(2) -> "[2]"
"""

OUTLINE = {"project": "boxer", "summary": "s", "acceptance_ok": True, "missing": [],
           "chunks": [{"id": "c1", "kind": "code", "title": "show", "spec": "show(x) -> '<x>'",
                       "depends_on": [], "thinking": False, "checks": ["1"]},
                      {"id": "c2", "kind": "code", "title": "box", "spec": "box(x) -> '[x]'",
                       "depends_on": ["c1"], "thinking": False, "checks": ["2"]}]}


def test_invalid_outline_gets_one_repair_call():
    bad = dict(OUTLINE, chunks=[dict(OUTLINE["chunks"][0], depends_on=["c9"]),
                                OUTLINE["chunks"][1]])
    tests = {"c1": "assert show(1) == '<1>'\nprint('PASS')",
             "c2": "assert box(2) == '[2]'\nprint('PASS')"}
    planner = FakeLLM({
        "into an OUTLINE": [json.dumps(bad), json.dumps(OUTLINE)],
        "verification test for ONE chunk":
            lambda u: "```python\n" + tests["c1" if "THIS CHUNK: c1" in u else "c2"] + "\n```"})
    plan = forge.staged_plan(planner, SPEC, None, contract=False)
    assert planner.count("into an OUTLINE") == 2
    assert [c["depends_on"] for c in plan["chunks"]] == [[], ["c1"]]


def test_uncovered_acceptance_item_gets_a_repair_call():
    gap = dict(OUTLINE, chunks=[OUTLINE["chunks"][0], dict(OUTLINE["chunks"][1], checks=[])])
    planner = FakeLLM({"into an OUTLINE": [json.dumps(OUTLINE)]})
    out = forge.check_coverage(planner, SPEC, gap)
    assert "coverage" not in out and out["chunks"][1]["checks"] == ["2"]
    assert "acceptance item(s) 2" in planner.calls[0][1]


def test_still_uncovered_items_are_recorded_for_the_report(tmp_path):
    gap = dict(OUTLINE, chunks=[OUTLINE["chunks"][0], dict(OUTLINE["chunks"][1], checks=[])])
    planner = FakeLLM({"into an OUTLINE": [json.dumps(gap)]})
    out = forge.check_coverage(planner, SPEC, json.loads(json.dumps(gap)))
    assert out["coverage"] == {"missing": ["2"], "duplicated": []}
    run = make_run(tmp_path, plan=out)
    assert "not covered: 2" in forge.build_report(run, "x = 1\n", [])


def test_extend_test_retries_after_a_syntax_error():
    planner = FakeLLM({"ADDITIONAL test code": ["```python\nassert (\n```",
                                                "```python\nassert box(3) == '[3]'\n```"]})
    chunk = {"id": "c2", "spec": "box(x)"}
    test = forge.extend_test(planner, SPEC, chunk, "assert box(2) == '[2]'\nprint('PASS')\n",
                             "change", ["CH1-1"], OUTLINE)
    compile(test, "t", "exec")
    assert "box(3)" in test and test.rstrip().endswith("print('PASS')")
    assert "CH1-1" in planner.calls[0][1]


# =============================================================================
# 6. Acceptance coverage and test lint
# =============================================================================

def test_acceptance_items_parsing():
    spec = ("## Acceptance\n1. a\n2) b\n10 items per page is the default\n- R1: c\n"
            "**CH2-3** d\n```python\n4. not an item\n```\n### Edge cases\n5. still acceptance\n"
            "## Next\n6. not acceptance\n")
    assert forge.acceptance_items(spec) == ["1", "2", "R1", "CH2-3", "5"]


def test_coverage_problems():
    chunks = [{"id": "c1", "checks": [1, "#2"]}, {"id": "c2", "checks": ["item 2"]}]
    assert forge.coverage_problems(["1", "2", "3"], chunks) == (["3"], ["2 (c1, c2)"])


LINT_PLAN = {"chunks": [{"id": "c1", "spec": "show(x) returns a string"},
                        {"id": "c2", "spec": "box(x)", "depends_on": ["c1"]}],
             "contract": {"names": [{"name": "show", "owner": "c1", "signature": "(x)"},
                                    {"name": "box", "owner": "c2", "signature": "(x)"}]}}


def test_lint_flags_a_name_from_a_later_chunk():
    problems = forge.test_name_problems("assert box(1) == '[1]'\n", SPEC, LINT_PLAN, "c1")
    assert len(problems) == 1 and "built AFTER c1" in problems[0]


def test_lint_flags_a_name_nothing_defines():
    problems = forge.test_name_problems("assert shw(1)\n", SPEC, LINT_PLAN, "c1")
    assert len(problems) == 1 and "`shw` is defined nowhere" in problems[0]


def test_lint_accepts_locals_builtins_stdlib_and_the_fixture():
    spec = SPEC + "\n## Test fixture (copy verbatim)\n```python\ndef load_fixture():\n    return 1\n```\n"
    test = ("import tempfile\nd = tempfile.mkdtemp()\nrows = [show(i) for i in range(3)]\n"
            "def helper(v):\n    return len(v)\nassert helper(rows) == 3, json\n"
            "assert load_fixture() == 1\ntry:\n    pass\nexcept ValueError as e:\n    print(e)\n")
    assert forge.test_name_problems(test, spec, LINT_PLAN, "c1") == []


def test_write_chunk_test_retries_a_test_that_uses_a_later_name():
    planner = FakeLLM({"verification test for ONE chunk": [
        "```python\nassert box(show(1))\nprint('PASS')\n```",
        "```python\nassert show(1) == '<1>'\nprint('PASS')\n```"]})
    chunk = dict(LINT_PLAN["chunks"][0], checks=["1"])
    test = forge.write_chunk_test(planner, SPEC, LINT_PLAN, chunk)
    assert "box" not in test
    assert "NAMES IT CANNOT HAVE" in planner.calls[1][1]


def test_write_chunk_test_keeps_the_best_test_when_lint_never_clears():
    planner = FakeLLM({"verification test for ONE chunk": [
        "```python\nassert box(show(zz))\n```", "```python\nassert box(show(1))\n```"]})
    test = forge.write_chunk_test(planner, SPEC, LINT_PLAN, LINT_PLAN["chunks"][0])
    assert "zz" not in test


def test_change_checks_are_assigned_to_chunks():
    ch = {"spec_addendum": "### Acceptance\nCH1-1. a\nCH1-2. b\n",
          "modify": [{"id": "c1", "spec": "x", "checks": ["CH1-1"]}],
          "add": [{"id": "c3", "spec": "y", "title": "new"}]}
    planner = FakeLLM({"assign acceptance checks": ['{"assign": {"CH1-2": "c3"}}']})
    assert forge.change_coverage(planner, ch, "CH1-") == []
    assert ch["add"][0]["checks"] == ["CH1-2"]


# =============================================================================
# 7. End to end: plan -> contract -> build -> regression repair -> review ->
#    commit, against a fake llama-server and a local sandbox
# =============================================================================

def e2e_cfg(tmp_path, url, **loop):
    return {"planner": {"url": url, "context": 200_000, "max_tokens": 8000},
            "coder": {"url": url, "context": 100_000, "max_tokens": 8000},
            "sandbox": {"unsafe_local_exec": True, "timeout": 60},
            "loop": {"test_audit": False, **loop},
            "claude": {"enabled": False},
            "git": {"repo": str(tmp_path / "repo"), "remote": "nowhere", "auto_push": True},
            "paths": {"runs": str(tmp_path / "runs")}}


def contract_reply(u, b):
    return json.dumps({"names": [{"name": "show", "owner": "c1", "signature": "(x)"},
                                 {"name": "box", "owner": "c2", "signature": "(x)"}],
                       "schema_sql": "", "rules": [], "routes": [], "dom_hooks": [],
                       "payloads": []})


def coder_reply(u, b):
    return "```python\n" + (C1 if "YOUR TASK: c1" in u else C2_BREAKS_C1) + "```"


def test_end_to_end_single_plan_with_regression_repair(tmp_path):
    plan = dict(TWO_CHUNKS, acceptance_ok=True, missing=[])
    srv = FakeServer([
        ("into a plan that", lambda u, b: json.dumps(plan)),
        ("INTERFACE CONTRACT for a module", contract_reply),
        ("You are REVISING", lambda u, b: f"```python\n{C2_FIXED}```"),
        ("precise Python engineer", coder_reply),
        ("You review generated code", lambda u, b: '{"approved": true, "issues": []}'),
    ])
    try:
        cfg = e2e_cfg(tmp_path, srv.url)
        spec = tmp_path / "boxer.md"
        spec.write_text(SPEC)
        run = forge.Run.create(tmp_path / "runs", spec)
        branch = forge.execute(run, cfg)
        assert branch.startswith("forge/boxer-")
        state = forge.Run(run.root).state
        assert state["phase"] == "committed" and "push_error" in state
        files = subprocess.run(["git", "-C", str(tmp_path / "repo"), "show", "--stat", branch],
                               capture_output=True, text=True).stdout
        assert "projects/boxer/boxer.py" in files
        assert "_box_fmt" in (run.root / "module.py").read_text()
        assert srv.count("You are REVISING") == 1
        # Resume of the committed run: nothing happens, no model call.
        n = len(srv.calls)
        assert forge.execute(forge.Run(run.root), cfg) == branch
        assert len(srv.calls) == n
    finally:
        srv.close()


def test_end_to_end_staged_plan_with_coverage_and_lint(tmp_path):
    gap = dict(OUTLINE, chunks=[OUTLINE["chunks"][0], dict(OUTLINE["chunks"][1], checks=[])])
    outlines = [gap, OUTLINE]
    tests = {"c1": ["assert box(show(1))\nprint('PASS')",              # uses c2's name
                    "assert show(1) == '<1>', show(1)\nprint('PASS')"],
             "c2": ["assert box(2) == '[2]', box(2)\nprint('PASS')"]}
    srv = FakeServer([
        ("into an OUTLINE", lambda u, b: json.dumps(outlines.pop(0))),
        ("INTERFACE CONTRACT for a module", contract_reply),
        ("verification test for ONE chunk",
         lambda u, b: "```python\n" + tests["c1" if "THIS CHUNK: c1" in u else "c2"].pop(0)
         + "\n```"),
        ("precise Python engineer",
         lambda u, b: "```python\n" + (C1 if "YOUR TASK: c1" in u else C2_FIXED) + "```"),
        ("You review generated code", lambda u, b: '{"approved": true, "issues": []}'),
    ])
    try:
        cfg = e2e_cfg(tmp_path, srv.url, staged_plan=True)
        spec = tmp_path / "boxer.md"
        spec.write_text(SPEC)
        run = forge.Run.create(tmp_path / "runs", spec)
        assert forge.execute(run, cfg)
        plan = json.loads((run.root / "plan.json").read_text())
        assert "coverage" not in plan
        assert [c.get("checks") for c in plan["chunks"] if c["id"] in ("c1", "c2")] == [["1"], ["2"]]
        c1 = next(c for c in plan["chunks"] if c["id"] == "c1")
        assert "box" not in c1["verify"]
        assert srv.count("into an OUTLINE") == 2
    finally:
        srv.close()


def test_extract_json_never_returns_an_inner_object_of_a_broken_reply():
    with pytest.raises(forge.BadJSON):
        forge.extract_json('{"chunks": [{"id": "c1"}, {broken}]}')
