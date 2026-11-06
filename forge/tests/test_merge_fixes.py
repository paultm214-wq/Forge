"""Fixes made while merging the October 27 review changes: an attempt that
fails before its body still reports (no hung build), the ladder's skip rule
on the plain pass rate, salvaged thinking attempts, and model eval giving a
machine back when a run starts using it."""
import json
import threading

import pytest

import forge
from conftest import FakeLLM, make_run


def one_chunk(tmp_path, **extra):
    plan = {"project": "p", "chunks": [{"id": "c1", "kind": "code", "spec": "f() returns 1",
                                        "depends_on": [], "verify": "assert f() == 1\nprint('PASS')",
                                        **extra}]}
    return make_run(tmp_path, plan=plan), plan


GOOD = "```python\ndef f():\n    return 1\n```"


def test_an_attempt_that_fails_early_reports_instead_of_hanging(tmp_path, local_sandbox,
                                                                monkeypatch):
    run, plan = one_chunk(tmp_path)
    monkeypatch.setattr(forge.STATUS, "doing", lambda what: (_ for _ in ()).throw(
        RuntimeError("status broke")))
    coder = FakeLLM({"precise Python engineer": [GOOD]})
    done = {}

    def go():
        try:
            forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                                 local_sandbox, plan["chunks"][0])
        except RuntimeError as exc:
            done["exc"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive(), "the build loop hung waiting for the attempt"
    assert "status broke" in str(done.get("exc"))
    assert not forge._WRITERS_BUSY                      # the writer was given back


def test_ladder_skip_rule_uses_the_plain_rate():
    # 0 passes in 12: smoothed that is 1/14 = 7%, which never fell under 5%.
    forge.record_ladder("rust", {("think", "m"): [12, 0], ("direct", "m"): [12, 6]})
    lad, why = forge.learned_ladder(["think", "direct", "planner"], "rust", {})
    assert lad == ["direct", "direct", "planner"] and "think -> direct" in why
    # Ranking still uses the smoothed rate.
    assert forge.step_rates("rust")["think"] == (1 / 14, 12)


def test_a_salvaged_thinking_attempt_counts_for_direct(tmp_path, local_sandbox):
    run, plan = one_chunk(tmp_path, thinking=True)

    def coder_reply(user):
        return GOOD
    calls = []

    class Coder(FakeLLM):
        def chat(self, system, user, **kw):
            calls.append(kw.get("thinking"))
            if kw.get("thinking"):
                raise forge.Truncated("thinking ran out")
            return GOOD
    coder = Coder({})
    forge.run_code_chunk(run, {"loop": {"test_audit": False}}, coder, coder,
                         local_sandbox, plan["chunks"][0])
    stats = json.loads((forge.FORGE_HOME / "ladder-stats.json").read_text())["python"]
    assert stats["think"]["?"] == [1, 0]                # cut off: no pass for thinking
    assert stats["direct"]["?"] == [1, 1]               # the salvage write passed


class FakeMachine:
    def __init__(self):
        self.llm = FakeLLM({})
        self.helper = None

    def status(self, fresh=False):
        return {"models": {}}

    def use(self, entry):
        pass


def test_eval_stops_when_a_run_takes_the_machine(tmp_path, monkeypatch):
    cases = [{"run": "r", "chunk": f"c{i}", "lang": "python", "key": f"r/c{i}"} for i in range(5)]
    monkeypatch.setattr(forge, "eval_cases", lambda cfg, n: cases)
    monkeypatch.setattr(forge, "reference_passes", lambda case, sb: True)
    monkeypatch.setattr(forge, "register_machines",
                        lambda cfg, coder, helpers: forge.MACHINES.update(apollo=FakeMachine()))
    monkeypatch.setattr(forge, "MACHINES", {})
    graded = []

    def grade(case, llm, sb):
        graded.append(case["key"])
        return True
    monkeypatch.setattr(forge, "grade_case", grade)
    # Free for the first two cases, then a run's lease appears.
    monkeypatch.setattr(forge, "machine_busy",
                        lambda name: "run demo (pid 1)" if len(graded) >= 2 else None)
    cfg = {"coder": {"url": "http://127.0.0.1:9", "model": "tiel-coder"},
           "sandbox": {"unsafe_local_exec": True}, "models": {"eval_minutes": 5}}
    forge.eval_models(cfg)
    assert graded == ["r/c0", "r/c1"]
