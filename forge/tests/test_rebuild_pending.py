"""After a review-phase fix marks a dependent chunk for rebuilding, that chunk
is built again before the review goes on (glassinv: c10 was left out, and
everything that needed make_app failed)."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import forge


class NoModel:
    name, context, prompt_budget, thinking_max_tokens, temperature, last = "x", 10**6, 10**6, 1000, 0.2, {}
    def tokens(self, t): return 1
    def chat(self, *a, **k): raise AssertionError("no model call expected")


def test_pending_dependent_is_rebuilt_from_its_seed(tmp_path):
    for d in ("chunks", "verified", "seeds"):
        (tmp_path / d).mkdir()
    (tmp_path / "SPEC.md").write_text("spec")
    plan = {"project": "p", "chunks": [
        {"id": "c1", "kind": "code", "spec": "f", "depends_on": [], "verify": "assert f() == 1\nprint('PASS')"},
        {"id": "c2", "kind": "code", "spec": "g", "depends_on": ["c1"], "verify": "assert g() == 2\nprint('PASS')"}]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "verified" / "c1.py").write_text("def f():\n    return 1\n")
    (tmp_path / "seeds" / "c2.py").write_text("def g():\n    return f() + 1\n")
    run = forge.Run(tmp_path)
    run.state = {"chunks": {"c1": {"status": "verified"}, "c2": {"status": "pending"}}, "reviews": []}
    run.save()
    sb = forge.Sandbox({"unsafe_local_exec": True})
    forge.rebuild_pending(run, {"loop": {}}, NoModel(), NoModel(), sb)
    assert run.state["chunks"]["c2"]["status"] == "verified"
    assert (tmp_path / "verified" / "c2.py").exists()


def test_code_warnings_reports_bad_escapes():
    assert forge.code_warnings('x = b"p\\u00e9"') and not forge.code_warnings('x = "p\\u00e9"')
