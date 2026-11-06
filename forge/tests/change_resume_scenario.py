import sys, json, tempfile, pathlib, shutil
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent)); import forge
forge.fit = lambda llm, fixed, blocks: "\n".join(fixed)
runs = pathlib.Path(tempfile.mkdtemp())
src = runs / "p-1"
for d in ("chunks", "verified", "plan-tests"): (src / d).mkdir(parents=True)
(src / "SPEC.md").write_text("# p\n\nStore and make_app.\n\n## Acceptance\n\n1. it works\n")
T1 = "s = Store()\nassert s.n == 0\nprint('PASS')\n"
T2 = "app = make_app()\nassert app() == 0\nprint('PASS')\n"
plan = {"project": "p", "summary": "s", "contract": {"names": [
    {"name": "Store", "owner": "c1", "signature": "(self)"},
    {"name": "make_app", "owner": "c2", "signature": "()"}]},
    "chunks": [{"id": "c1", "kind": "code", "spec": "Store", "depends_on": [], "verify": T1, "checks": ["1"]},
               {"id": "c2", "kind": "code", "spec": "make_app", "depends_on": ["c1"], "verify": T2, "checks": []}]}
(src / "plan.json").write_text(json.dumps(plan))
for c, t in (("c1", T1), ("c2", T2)): (src / "plan-tests" / f"{c}.py").write_text(t)
(src / "verified" / "c1.py").write_text("class Store:\n    n = 0\n    def delete(self, i):\n        pass\n")
(src / "verified" / "c2.py").write_text("def make_app():\n    return lambda: 0\n")
r = forge.Run(src); r.state = {"phase": "build", "chunks": {"c1": {"status": "verified"}, "c2": {"status": "verified"}}, "reviews": []}; r.save()
EXTRA = ("s2 = Store()\ntry:\n    s2.delete(99)\n    raise AssertionError('no KeyError')\nexcept KeyError:\n    pass\n"
         "app2 = make_app()\nst = app2()\nassert st == 404, st\n")
class P:
    name, context, thinking_max_tokens, temperature, prompt_budget, last = "planner", 10**7, 4000, 0.2, 10**7, {}
    def __init__(s): s.calls = []
    def tokens(s, t): return 1
    def chat(s, system, user, **k):
        s.calls.append(system[:25])
        if system.startswith(forge.CHANGE_SYSTEM[:30]):
            return json.dumps({"title": "Deletes 404", "summary": "x", "spec_addendum": "Deleting a missing id is 404.\n\nCH1-1. delete 99 is 404",
                               "modify": [{"id": "c1", "spec": "Store; delete raises KeyError", "test": "extend", "checks": ["CH1-1"]}],
                               "add": [], "contract": {"routes": ["DELETE /api/<plural>/<id> - 404 if missing", "GET /glass"],
                                                       "dom_hooks": ["#intake-form"]}})
        if system.startswith(forge.EXTEND_TEST_SYSTEM[:30]): return f"```python\n{EXTRA}```"
        return json.dumps({"assign": {}})
chg = forge.start_change(runs, str(src), "Make deletes of missing ids 404.")
p = P(); sb = forge.Sandbox({"unsafe_local_exec": True})
forge.plan_change(chg, p, sb)
pl = chg.plan; by = {c["id"]: c for c in pl["chunks"]}
print("routes normalized:", pl["contract"]["routes"])
print("hooks:", pl["contract"]["dom_hooks"])
print("c1 test keeps KeyError check, no make_app:", "KeyError" in by["c1"]["verify"], "make_app" not in by["c1"]["verify"])
print("c2 test got the moved check with its setup:", "checks moved from c1" in by["c2"]["verify"], "app2 = make_app()" in by["c2"]["verify"])
print("c2 pending, seeded:", chg.state["chunks"]["c2"], (chg.root / "seeds/c2.py").exists())
# resume: planning again must not double anything nor ask the planner again
n_calls = len(p.calls)
forge.plan_change(forge.Run(chg.root), p, sb)
spec = (chg.root / "SPEC.md").read_text(); pl2 = forge.Run(chg.root).plan; by2 = {c["id"]: c for c in pl2["chunks"]}
print("after re-entry: one change in spec:", spec.count("## Change:") == 1,
      "| c1 test extended once:", by2["c1"]["verify"].count("# --- change:") == 1,
      "| c2 moved once:", by2["c2"]["verify"].count("checks moved from") == 1,
      "| no new planner calls:", len(p.calls) == n_calls, p.calls[n_calls:])
# --- the user's case: a crash late in planning, by a forge without baselines
import time; time.sleep(1.1)
chg = forge.start_change(runs, str(src), "Make deletes of missing ids 404.")
orig_amend, orig_base = forge.amend_contract, forge.change_baseline
forge.change_baseline = lambda run: None            # the old forge had none
def boom(*a, **k): raise AttributeError("'str' object has no attribute 'get'")
forge.amend_contract = boom
p = P()
try: forge.plan_change(chg, p, sb)
except AttributeError as e: print("crashed like before:", e)
print("  state after crash - spec has change:", "## Change:" in (chg.root / "SPEC.md").read_text(),
      "| c1 verified file gone:", not (chg.root / "verified/c1.py").exists())
forge.amend_contract, forge.change_baseline = orig_amend, orig_base
n = len(p.calls)
r2 = forge.Run(chg.root); forge.plan_change(r2, p, sb)
spec = (chg.root / "SPEC.md").read_text(); by3 = {c["id"]: c for c in forge.Run(chg.root).plan["chunks"]}
print("resume: one change in spec:", spec.count("## Change:") == 1, "| c1 extended once:", by3["c1"]["verify"].count("# --- change:") == 1,
      "| reused test, extend not re-asked:", not any(c.startswith(forge.EXTEND_TEST_SYSTEM[:25]) for c in p.calls[n:]),
      "| c1 seed kept:", (chg.root / "seeds/c1.py").exists())
