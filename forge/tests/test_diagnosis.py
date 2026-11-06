"""Diagnosis beside the build (a slow planner no longer stops new attempts)
and fix hints straight from the error message (no model call when they
cover every failure)."""
import time

import pytest

import forge
from conftest import FakeLLM, make_run


def fails(sandbox, module, test):
    ok, out = sandbox.run(forge.verify_files(module, test), "verify.py")
    assert not ok
    return out, forge.all_failures(sandbox, module, test)


def chunk(spec="", cid="c2"):
    return {"id": cid, "spec": spec, "verify": ""}


# ------------------------------------------------------------- the hints

def test_missing_stdlib_import_is_covered(local_sandbox):
    mod = "def now():\n    return time.time()\n"
    out, every = fails(local_sandbox, mod, "assert now() > 0\nassert now() > 1\n")
    hints, covered = forge.mechanical_hints({}, chunk(), mod, out, every)
    assert covered and hints == ["`time` is used (module.py line 2, in `now`) but never "
                                 "imported: add `import time` to this chunk."]


def test_assertion_failures_still_need_the_model(local_sandbox):
    mod = "import json\ndef f():\n    return 2\ndef g():\n    return dataclass\n"
    out, every = fails(local_sandbox, mod, "assert f() == 1\nassert g()\n")
    hints, covered = forge.mechanical_hints({}, chunk(), mod, out, every)
    assert not covered
    assert any("from dataclasses import dataclass" in h for h in hints)


def test_name_the_test_expects(local_sandbox):
    mod = "SCHEMAS = {}\n"
    out, every = fails(local_sandbox, mod, "assert SCHEMA_SQL\n")
    # The spec says this chunk provides it: covered, with the close match named.
    hints, covered = forge.mechanical_hints({}, chunk("Define SCHEMA_SQL."), mod, out, every)
    assert covered and "closest existing name: `SCHEMAS`" in hints[0]
    assert "define `SCHEMA_SQL`" in hints[0]
    # Nothing says so (it may be a helper the test forgot): the model decides.
    assert not forge.mechanical_hints({}, chunk("Something else."), mod, out, every)[1]
    # The contract gives it to another chunk: never "define it here".
    plan = {"contract": {"names": [{"name": "SCHEMA_SQL", "owner": "c1"}]}}
    hints, covered = forge.mechanical_hints(plan, chunk("Define SCHEMA_SQL."), mod, out, every)
    assert not covered and "Do not define it here" in hints[0]


SCHEMA_MOD = '''import sqlite3
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    plant_id INTEGER REFERENCES plants(id),
    path TEXT NOT NULL,
    taken_at TEXT CHECK (length(taken_at) > 0),
    UNIQUE (plant_id, path)
);
CREATE TABLE plants (id INTEGER PRIMARY KEY, name TEXT);
"""
def add(db, kind):
    db.execute("INSERT INTO photos (plant_id, path, uploaded_at) VALUES (1, 'a', 'x')")
def count(db, kind):
    return db.execute("SELECT count(*) FROM {kind}").fetchone()
'''


def test_schema_tables():
    assert forge.schema_tables(SCHEMA_MOD) == {
        "photos": ["id", "plant_id", "path", "taken_at"], "plants": ["id", "name"]}


def test_sqlite_column_and_fstring_table(local_sandbox):
    test = ("import sqlite3\ndb = sqlite3.connect(':memory:')\ndb.executescript(SCHEMA_SQL)\n"
            "add(db, 'photos')\nassert count(db, 'photos') == (1,)\n")
    out, every = fails(local_sandbox, SCHEMA_MOD, test)
    plan = {"contract": {"names": [{"name": "SCHEMA_SQL", "owner": "c1"}]}}
    hints, covered = forge.mechanical_hints(plan, chunk(), SCHEMA_MOD, out, every)
    assert covered, hints
    assert "photos(id, plant_id, path, taken_at)" in hints[0]
    assert "belongs to c1" in hints[0]
    assert "missing its `f` prefix" in hints[1]
    # the plain traceback names it sqlite3.OperationalError, the full list does not
    assert forge.mechanical_hints(plan, chunk(), SCHEMA_MOD, out, "")[1]


def test_signature_mismatch_gives_facts_but_asks_the_model(local_sandbox):
    mod = "def claude_client(api_key, post=None):\n    return api_key\n"
    out, every = fails(local_sandbox, mod, "assert claude_client(key_file='k')\n")
    hints, covered = forge.mechanical_hints({}, chunk(), mod, out, every)
    assert not covered
    assert hints == ["`claude_client` is defined as `claude_client(api_key, post=None)`, and a "
                     "call in the test does not match it (got an unexpected keyword argument "
                     "'key_file')."]


# --------------------------------------------- in the build loop

def one_chunk_run(tmp_path, verify="assert f() == 1\nprint('PASS')"):
    plan = {"project": "p", "chunks": [{"id": "c1", "kind": "code", "spec": "f() returns 1",
                                        "depends_on": [], "verify": verify}]}
    return make_run(tmp_path, plan=plan), plan


GOOD = "```python\ndef f():\n    return 1\n```"


def test_return_outside_function_is_caught_before_the_sandbox(tmp_path, local_sandbox):
    run, plan = one_chunk_run(tmp_path)
    coder = FakeLLM({"precise Python engineer": [
        "```python\ndef f():\n    pass\nreturn 1\n```", GOOD],
        "You diagnose": lambda u: pytest.fail("no diagnosis for a syntax error")})
    forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                         local_sandbox, plan["chunks"][0])
    assert not (run.root / "chunks" / "c1" / "attempt_1" / "result.txt").exists()
    assert "placed at module level" in coder.calls[1][1]


def test_mechanical_failure_needs_no_model(tmp_path, local_sandbox):
    run, plan = one_chunk_run(tmp_path, "assert f() > 0\nprint('PASS')")
    coder = FakeLLM({"precise Python engineer": [
        "```python\ndef f():\n    return time.time()\n```",
        "```python\nimport time\ndef f():\n    return time.time()\n```"],
        "You diagnose": lambda u: pytest.fail("the error message said it all")})
    forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                         local_sandbox, plan["chunks"][0])
    assert "add `import time`" in coder.calls[1][1]


def test_slow_diagnosis_does_not_hold_the_next_attempt(tmp_path, local_sandbox):
    run, plan = one_chunk_run(tmp_path)

    def slow(user):
        time.sleep(4)
        return "SLOW DIAGNOSIS"
    coder = FakeLLM({"precise Python engineer": ["```python\ndef f():\n    return 2\n```", GOOD],
                     "You diagnose": slow})
    t0 = time.monotonic()
    forge.run_code_chunk(run, {"loop": {"test_audit": False, "diagnosis_wait": 0.5}},
                         coder, coder, local_sandbox, plan["chunks"][0])
    assert time.monotonic() - t0 < 3.5
    second = [u for p, u, _ in coder.calls if p == "precise Python engineer"][1]
    assert "SLOW DIAGNOSIS" not in second and "AssertionError" in second


def test_quick_diagnosis_reaches_the_next_attempt(tmp_path, local_sandbox):
    run, plan = one_chunk_run(tmp_path)
    coder = FakeLLM({"precise Python engineer": ["```python\ndef f():\n    return 2\n```", GOOD],
                     "You diagnose": lambda u: "f returns 2; return 1"})
    forge.run_code_chunk(run, {"loop": {"test_audit": False, "diagnosis_wait": 10}},
                         coder, coder, local_sandbox, plan["chunks"][0])
    second = [u for p, u, _ in coder.calls if p == "precise Python engineer"][1]
    assert "Diagnosis:\nf returns 2; return 1" in second


def test_a_failing_planner_does_not_fail_the_chunk(tmp_path, local_sandbox):
    run, plan = one_chunk_run(tmp_path)
    coder = FakeLLM({"precise Python engineer": ["```python\ndef f():\n    return 2\n```", GOOD],
                     "You diagnose": [forge.ForgeError("planner unreachable")]})
    assert "return 1" in forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                                              local_sandbox, plan["chunks"][0])


def test_a_mechanical_first_failure_covers_its_consequences(local_sandbox):
    mod = ("import sqlite3\nSCHEMA_SQL = 'CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT);'\n"
           "def add(db):\n    db.execute(\"INSERT INTO photos (path, taken) VALUES ('a', 1)\")\n")
    test = ("import sqlite3\ndb = sqlite3.connect(':memory:')\ndb.executescript(SCHEMA_SQL)\n"
            "add(db)\nrows = db.execute('SELECT * FROM photos').fetchall()\n"
            "assert len(rows) == 1, 'Expected 1 photo'\nassert rows[0][1] == 'a'\n")
    out, every = fails(local_sandbox, mod, test)
    hints, covered = forge.mechanical_hints({}, chunk(), mod, out, every)
    assert covered and "photos(id, path)" in hints[0]


def test_schema_written_as_adjacent_literals():
    mod = ("SCHEMA_SQL = (\n"
           "    'CREATE TABLE IF NOT EXISTS \"photos\" (\\n'\n"
           "    '    \"id\" INTEGER PRIMARY KEY AUTOINCREMENT,\\n'\n"
           "    '    \"record_id\" INTEGER NOT NULL,\\n'\n"
           "    '    \"caption\" TEXT\\n'\n"
           "    ');\\n'\n)\n")
    assert forge.schema_tables(forge.module_sql(mod)) == {"photos": ["id", "record_id", "caption"]}
