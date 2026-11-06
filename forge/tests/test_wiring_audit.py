"""A wiring test that is itself wrong is corrected before anything is 'fixed'
(glassinv: "record-form has method= attribute" matched data-method=)."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import forge

MODULE = "def page():\n    return '<form class=\"record-form\" data-method=\"POST\"></form>'\n"
BAD = ("fails = []\nif 'method=' in page():\n    fails.append(('c8', 'record-form has method= attribute'))\n"
       "for c, m in fails: print(f'WIRING FAIL [{c}] {m}')\nassert not fails\nprint('PASS')\n")
GOOD = ("import re\nfails = []\nif re.search(r'\\smethod=', page()):\n    fails.append(('c8', 'record-form has method= attribute'))\n"
        "for c, m in fails: print(f'WIRING FAIL [{c}] {m}')\nassert not fails\nprint('PASS')\n")


class Planner:
    name, context, prompt_budget, thinking_max_tokens, temperature, last = "planner", 10**6, 10**6, 4000, 0.2, {}
    def __init__(self): self.calls = 0
    def tokens(self, t): return 1
    def chat(self, system, user, **k):
        self.calls += 1
        assert system == forge.WIRING_AUDIT_SYSTEM
        return json.dumps({"false": ["record-form has method= attribute"],
                           "reason": "data-method is not method", "test": GOOD})


def test_false_wiring_problem_is_audited_away(tmp_path):
    for d in ("chunks", "verified", "plan-tests"):
        (tmp_path / d).mkdir()
    (tmp_path / "SPEC.md").write_text("spec")
    con = {"names": [], "routes": [{"method": "GET", "path": "/x", "router": "c8"}]}
    (tmp_path / "plan.json").write_text(json.dumps({"project": "p", "contract": con, "chunks": [
        {"id": "c8", "kind": "code", "spec": "x", "depends_on": [], "verify": "pass"}]}))
    run = forge.Run(tmp_path)
    run.state = {"chunks": {}}
    run.save()
    stamp = "# contract " + forge.hashlib.sha256(json.dumps(con, sort_keys=True).encode()).hexdigest()[:16]
    (tmp_path / "plan-tests" / "wiring.py").write_text(f"{stamp}\n{BAD}")
    sb = forge.Sandbox({"unsafe_local_exec": True})
    p = Planner()
    assert forge.wiring_check(run, p, sb, MODULE) == [{"severity": "blocking", "chunk": "c8",
        "description": "Wiring: record-form has method= attribute",
        "fix": "Make this agree with the interface contract's routes / page elements / JSON shapes."}]
    assert forge.wiring_check(run, p, sb, MODULE, audit=True) == []
    assert p.calls == 1 and (tmp_path / "plan-tests" / "wiring.before-audit.py").exists()
