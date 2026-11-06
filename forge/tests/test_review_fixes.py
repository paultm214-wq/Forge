"""Reviewer notes, October 2026: the test-audit guard, SystemExit in the
all-failures rerun, positional-only parameters, comments in assembled
modules, reference-image name collisions, the status/state writers, image
speeds and the helper back-off after a GPU clash."""
import ast
import json
import threading
import time

import pytest

import forge
from conftest import FakeLLM, make_run


# ---------------------------------------------------------------- #2 audit guard

@pytest.mark.parametrize("src,n", [
    ("assert f() == 1\nassert g()\n", 2),
    ("if f() != 1:\n    raise SystemExit('bad')\n", 1),
    ("import sys\nif f() != 1:\n    sys.exit(1)\n", 1),
    ("self.assertEqual(f(), 1)\nself.assertTrue(g())\n", 2),
    ("# assert this, assert that\nprint('assert')\n", 0),     # text, not checks
    ("def (:\n", 0),
])
def test_count_checks(src, n):
    assert forge.count_checks(src) == n


def test_audit_cannot_swap_a_raise_based_test_for_print_pass(tmp_path):
    test = ("r = f()\n"
            "if r != 1:\n    raise AssertionError(r)\n"
            "if g() is None:\n    raise AssertionError('g')\n"
            "import sys\nif h():\n    sys.exit(1)\n"
            "print('PASS')\n")
    plan = {"project": "p", "chunks": [{"id": "c1", "kind": "code", "spec": "f, g, h",
                                        "depends_on": [], "verify": test}]}
    run = make_run(tmp_path, plan=plan)
    planner = FakeLLM({"keeps failing one chunk's verification test": [json.dumps(
        {"verdict": "test", "reason": "too strict", "test": "print('PASS')"})]})
    assert forge.audit_test(run, planner, plan["chunks"][0], "def f(): pass", "boom") is None
    # The substring count saw no "assert" in the old test (0 checks) and let
    # this through; the old test has three ways to fail.
    assert forge.count_checks(test) == 3


# ------------------------------------------------------- #3 SystemExit in rerun

def test_all_failures_counts_nonzero_exit_and_stops_at_exit_zero(local_sandbox):
    module = "def f():\n    return 2\n"
    test = ("import sys\n"
            "if f() != 1:\n    sys.exit('f() should be 1')\n"
            "assert f() > 0\n"
            "sys.exit(0)\n"
            "assert False, 'never reached in the real test'\n")
    out = forge.all_failures(local_sandbox, module, test)
    assert "1 failing statement" in out and "f() should be 1" in out
    assert "never reached" not in out


# ------------------------------------------------------ #4 positional-only

@pytest.mark.parametrize("code,contract,ok", [
    ("def f(a, b, /): pass", "(a, b)", False),       # f(a=1, b=2) would break
    ("def f(a, /, b): pass", "(a, b)", False),
    ("def f(a, /, b): pass", "(a, /, b)", True),
    ("def f(a, b): pass", "(a, /, b)", True),        # accepts more calls: fine
    ("def f(a, b): pass", "(a, b)", True),
])
def test_positional_only_parameters(code, contract, ok):
    assert forge.signature_compatible(ast.parse(code).body[0], contract) is ok


# --------------------------------------------------- #5 comments kept

def test_assemble_keeps_comments_above_definitions():
    src = ("import os\n\n"
           "# Units are millimetres throughout.\n"
           "# Do not change without the migration.\n"
           "SCALE = 1000\n\n\n"
           "# The store keeps one row per jar.\n"
           "@staticmethod\n"
           "def g():\n    return 1\n"
           "# trailing note\n")
    imports, body = forge.split_imports(src)
    assert imports == ["import os"]
    assert body[0].startswith("# Units are millimetres throughout.\n# Do not change")
    assert body[1].startswith("# The store keeps one row per jar.\n@staticmethod")
    assert body[1].endswith("# trailing note")
    mod = forge.assemble({"c1": src}, ["c1"])
    assert "# Units are millimetres" in mod and "# The store keeps" in mod
    compile(mod, "m", "exec")


# ------------------------------------------- #6 reference image collisions

def test_reference_images_with_the_same_name_do_not_collide(tmp_path, monkeypatch):
    home = tmp_path / "forgehome"
    for who, data in (("wendy", b"W"), ("rue", b"R")):
        (home / "refs" / who).mkdir(parents=True)
        (home / "refs" / who / "portrait-1.jpg").write_bytes(data)
    monkeypatch.setattr(forge, "FORGE_HOME", home)
    run = make_run(tmp_path, plan={"project": "p", "chunks": []})
    w = forge.resolve_ref(run, "refs/wendy/portrait-1.jpg")
    r = forge.resolve_ref(run, "refs/rue/portrait-1.jpg")
    assert w != r and w.read_bytes() == b"W" and r.read_bytes() == b"R"
    # Absolute paths with the same file name, too.
    a, b = tmp_path / "x" / "p.jpg", tmp_path / "y" / "p.jpg"
    for f, d in ((a, b"A"), (b, b"B")):
        f.parent.mkdir()
        f.write_bytes(d)
    pa, pb = forge.resolve_ref(run, str(a)), forge.resolve_ref(run, str(b))
    assert pa != pb and pa.read_bytes() == b"A" and pb.read_bytes() == b"B"
    with pytest.raises(forge.JobError, match=r"'\.\.'"):
        forge.resolve_ref(run, "refs/../../etc/passwd")


# ------------------------------------------------ #7 status and state writes

def test_status_file_survives_concurrent_writers(tmp_path):
    st = forge.Status()
    st.path = tmp_path / "status.json"
    errors = []

    def hammer(i):
        try:
            for j in range(200):
                st.line(f"worker {i} line {j} " + "x" * 200)
                st.begin("renders", f"r{i}", {"n": j})
        except Exception as exc:          # pragma: no cover - the failure being tested
            errors.append(exc)

    ts = [threading.Thread(target=hammer, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    json.loads(st.path.read_text())                       # never half a document
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))


def test_run_save_is_atomic_and_file_locked(tmp_path):
    run = make_run(tmp_path, plan={"project": "p", "chunks": []})
    run.state["note"] = "x" * 10000

    def save_many():
        for _ in range(100):
            run.save()

    ts = [threading.Thread(target=save_many) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert json.loads(run.state_path.read_text())["note"] == "x" * 10000
    assert (run.root / ".state.lock").exists()
    assert not list(run.root.glob("*.tmp")) and not list(run.root.glob(".*.tmp"))


def test_image_speeds_written_atomically():
    import inspect
    src = inspect.getsource(forge)
    assert "SPEEDS_FILE.write_text(" not in src
    assert "atomic_write(SPEEDS_FILE" in src


# ------------------------------------------- GPU clash: helper back-off

class CountingLock:
    """A GPU lock a render holds the whole time."""

    def __init__(self):
        self.tries = 0

    def acquire(self, blocking=True):
        self.tries += 1
        return False

    def release(self):
        pass


def test_helper_rests_after_a_gpu_clash(tmp_path, local_sandbox, monkeypatch):
    plan = {"project": "p", "chunks": [{"id": "c1", "kind": "code", "spec": "f() returns 1",
                                        "depends_on": [],
                                        "verify": "assert f() == 1\nprint('PASS')"}]}
    run = make_run(tmp_path, plan=plan)

    def slow_right(user):
        time.sleep(1.5)
        return "```python\ndef f():\n    return 1\n```"

    coder = FakeLLM({"precise Python engineer": slow_right,
                     "You diagnose": lambda u: "x"})
    helper = forge.Helper("vulcan", FakeLLM({}), "vulcan", thinking=True)
    monkeypatch.setattr(forge.Helper, "up", lambda self: True)
    monkeypatch.setattr(forge, "HELPERS", [helper])
    lock = CountingLock()
    monkeypatch.setattr(forge, "gpu_lock", lambda machine: lock)
    forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                         local_sandbox, plan["chunks"][0])
    assert "return 1" in (run.root / "verified" / "c1.py").read_text()
    # Before: the helper retried the lock in a tight loop for the whole 1.5 s.
    assert 1 <= lock.tries <= 2, lock.tries
    assert helper.gpu_busy_until > time.monotonic()


# ------------------------------- #1 class statements that would replace a class

DC = "from dataclasses import dataclass\n"
CLASS_CASES = {
    # name: (c1, c2, check c2 flags, check c1 flags)
    "plain extension": ("class S:\n    def a(self):\n        return 1\n",
                        "class S:\n    def b(self):\n        return 2\n", False, False),
    "repeated bases": ("class B: pass\nclass S(B):\n    def a(self):\n        return 1\n",
                       "class S(B):\n    def b(self):\n        return 2\n", False, False),
    "decorated redefinition": ("class S:\n    def a(self):\n        return 1\n",
                               DC + "@dataclass\nclass S:\n    x: int = 0\n", True, True),
    "extending a decorated class": (DC + "@dataclass\nclass S:\n    x: int = 1\n",
                                    "class S:\n    def a(self):\n        return self.x\n",
                                    True, True),
    "different bases": ("class B: pass\nclass C: pass\nclass S(B):\n    def a(self):\n        return 1\n",
                        "class S(C):\n    def b(self):\n        return 2\n", True, True),
}


@pytest.mark.parametrize("case", CLASS_CASES)
def test_class_extension_conflicts_match_assemble(case):
    c1, c2, later_flag, earlier_flag = CLASS_CASES[case]
    chunks, order = {"c1": c1, "c2": c2}, ["c1", "c2"]
    assert bool(forge.class_extension_conflicts(chunks, order, "c2")) is later_flag
    # Rebuilding c1 is reported only when the pair really breaks, and then
    # the message names the later chunk.
    msgs = forge.class_extension_conflicts(chunks, order, "c1")
    assert bool(msgs) is earlier_flag
    assert all(m.startswith("c2 extends class `S`") for m in msgs)
    # The check agrees with what assemble() actually does.
    ns = {}
    exec(forge.assemble(chunks, order), ns)
    merged = sum(hasattr(ns["S"], m) for m in ("a", "b", "x")) >= 2
    assert merged is not later_flag, case


def test_original_definer_is_not_blamed_for_a_safe_extension():
    chunks = {"c1": DC + "class Base: pass\nclass Store(Base):\n    x = 0\n",
              "c2": "class Store(Base):\n    def y(self):\n        return 1\n"}
    assert forge.class_extension_conflicts(chunks, ["c1", "c2"], "c1") == []
    assert forge.class_extension_conflicts(chunks, ["c1", "c2"], "c2") == []


def test_class_conflict_rejects_the_attempt_before_the_sandbox(tmp_path, local_sandbox):
    plan = {"project": "p", "chunks": [
        {"id": "c1", "kind": "code", "spec": "Config", "depends_on": [],
         "verify": "assert Config().a == 1\nprint('PASS')"},
        {"id": "c2", "kind": "code", "spec": "Config.double()", "depends_on": ["c1"],
         "verify": "assert Config().double() == 2\nprint('PASS')"}]}
    run = make_run(tmp_path, plan=plan, verified={
        "c1": DC + "@dataclass\nclass Config:\n    a: int = 1\n"})
    replies = ["```python\nclass Config:\n    def double(self):\n        return 2\n```",
               "```python\ndef _double(self):\n    return self.a * 2\nConfig.double = _double\n```"]
    coder = FakeLLM({"precise Python engineer": replies,
                     "You diagnose": lambda u: "x"})
    forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                         local_sandbox, plan["chunks"][1])
    assert "Config.double = _double" in (run.root / "verified" / "c2.py").read_text()
    assert "is decorated" in coder.calls[1][1]
