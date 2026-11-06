"""forge: enhancements 2-6 of the review."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _load import HERE, FakeLLM, forge, make_run, unsafe_sandbox

f = forge


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="forge-test-"))


class Leases(unittest.TestCase):
    """Enhancement 2: work on a machine another process uses is skipped."""

    def test_other_process_holds_the_machine(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             f"import sys, time; sys.path.insert(0, {str(HERE)!r})\n"
             f"import os; os.environ['HOME'] = {str(Path.home())!r}\n"
             f"import _load\n"
             f"_load.forge.FORGE_HOME = _load.Path({str(f.FORGE_HOME)!r})\n"
             f"_load.forge.LEASES_DIR = _load.forge.FORGE_HOME / 'leases'\n"
             f"with _load.forge.machine_lease(['apollo'], 'run test-run'):\n"
             f"    print('held', flush=True); time.sleep(60)\n"],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.assertIn("run test-run", f.machine_busy("apollo") or "")
            self.assertIsNone(f.machine_busy("vulcan"))
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()
        self.assertIsNone(f.machine_busy("apollo"))       # a dead holder does not count

    def test_own_lease_is_not_busy(self):
        with f.machine_lease(["mercury"], "this process"):
            self.assertIsNone(f.machine_busy("mercury"))

    def test_prefetch_skips_a_busy_machine(self):
        m = mock.Mock()
        with mock.patch.object(f, "register_machines", lambda *a: None), \
                mock.patch.dict(f.MACHINES, {"vulcan": m}, clear=True), \
                mock.patch.object(f, "machine_busy", lambda name: "run x (pid 1)"):
            f.prefetch({"coder": {"url": "http://x"}, "models": {}})
        m.ssh.assert_not_called()

    def test_run_machine_names(self):
        self.assertEqual(f.run_machine_names({"coders": [{"name": "vulcan"}, {"name": "mercury",
                                                                              "enabled": False}]}),
                         ["apollo", "vulcan"])


class TestStrength(unittest.TestCase):
    """Enhancement 3: a weak test is strengthened from the spec."""

    def run_with(self, test, extra_reply):
        run = make_run(tmpdir() / "r", {"project": "demo", "chunks": [
            {"id": "c1", "kind": "code", "spec": "scale(x, k) returns x times k.",
             "depends_on": [], "verify": test}]},
            verified={"c1": "def scale(x, k):\n    return x * k\n"})
        planner = FakeLLM(replies=[f"```python\n{extra_reply}\n```"] * 3, name="planner")
        cfg = {"loop": {"test_strength": True, "min_kill": 0.75}}
        notes = f.test_strength(run, cfg, planner, unsafe_sandbox(), ["c1"])
        return run, notes

    def test_weak_test_gains_checks(self):
        run, _ = self.run_with("assert scale(2, 3) is not None\nprint('PASS')",
                               "assert scale(2, 3) == 6, 'scale multiplies'")
        res = run.state["test_strength"]["c1"]
        self.assertTrue(res.get("added"), res)
        self.assertGreater(res["after"], res["killed"])
        self.assertIn("scale(2, 3) == 6", run.plan["chunks"][0]["verify"])

    def test_check_failing_on_current_code_is_kept_out(self):
        run, notes = self.run_with("assert scale(2, 3) is not None\nprint('PASS')",
                                   "assert scale(2, 3) == 7")
        self.assertNotIn("== 7", run.plan["chunks"][0]["verify"])
        self.assertTrue(any("FAIL on the current code" in n for n in notes))
        self.assertTrue((run.root / "test-strength" / "c1.rejected.py").exists())

    def test_strong_test_is_left_alone(self):
        run, _ = self.run_with("assert scale(2, 3) == 6\nprint('PASS')", "assert True")
        res = run.state["test_strength"]["c1"]
        self.assertEqual(res["killed"], res["mutants"])
        self.assertFalse(res.get("added"))

    def test_mutants_are_deterministic_and_compile(self):
        src = "def clamp(x, lo=0, hi=10):\n    if x < lo:\n        return lo\n    return min(x, hi) + 0\n"
        a, b = f.make_mutants(src, 5), f.make_mutants(src, 5)
        self.assertEqual(a, b)
        for _, m in a:
            compile(m, "m", "exec")


class Ladder(unittest.TestCase):
    """Enhancement 4: past runs adjust the ladder."""

    def setUp(self):
        (f.FORGE_HOME / "ladder-stats.json").unlink(missing_ok=True)

    def test_nothing_learned_nothing_changes(self):
        lad = f.LADDERS["thinking"]
        self.assertEqual(f.learned_ladder(lad, "rust", {}), (lad, ""))

    def test_rarely_winning_step_is_replaced_and_best_goes_first(self):
        f.record_ladder("lua", {("think", "tiel-coder"): [30, 0],
                                ("direct", "tiel-coder"): [20, 10]})
        lad, why = f.learned_ladder(["think", "direct", "think_long", "planner"], "lua", {})
        self.assertEqual(lad, ["direct", "direct", "think_long", "planner"])
        self.assertIn("think -> direct", why)

    def test_planner_steps_keep_their_places(self):
        f.record_ladder("web", {("direct", "m"): [20, 15], ("direct_warm", "m"): [20, 2]})
        lad, _ = f.learned_ladder(["direct_warm", "planner", "direct"], "web", {})
        self.assertEqual(lad[1], "planner")
        self.assertEqual(lad[0], "direct")

    def test_counts_add_up_across_calls(self):
        f.record_ladder("sql", {("direct", "m"): [1, 1]})
        f.record_ladder("sql", {("direct", "m"): [2, 0]})
        self.assertEqual(f.step_rates("sql")["direct"], (2 / 5, 3))


class ModelEval(unittest.TestCase):
    """Enhancement 5: models graded on your own committed runs."""

    def test_scores_move_toward_measured_relative_to_tielcoder(self):
        cat = {"models": [
            {"alias": "tiel-coder", "baseline": True, "languages": {"python": 80},
             "measured": {"python": [2, 4]}},
            {"alias": "x", "languages": {"python": 70}, "measured": {"python": [4, 4]}}]}
        # x: (4+1)/6 vs TielCoder (2+1)/6 -> 80 * 5/3 = 133 -> 100; weight 4/(4+4).
        self.assertEqual(f.effective_scores(cat["models"][1], cat)["python"], 85)
        del cat["models"][0]["measured"]
        self.assertEqual(f.effective_scores(cat["models"][1], cat)["python"], 70)

    def committed_run(self, runs):
        run = make_run(runs / "demo-1", {"project": "demo", "summary": "s", "chunks": [
            {"id": "c1", "kind": "code", "spec": "add(a, b) returns a + b.", "depends_on": [],
             "verify": "assert add(2, 3) == 5\nprint('PASS')"}]},
            verified={"c1": "def add(a, b):\n    return a + b\n"},
            state={"phase": "committed", "chunks": {"c1": {"status": "verified"}}})
        return run

    def test_cases_and_grading(self):
        runs = tmpdir()
        self.committed_run(runs)
        cases = f.eval_cases({"paths": {"runs": str(runs)}}, 6)
        self.assertEqual([c["key"] for c in cases], ["demo-1/c1"])
        sb = unsafe_sandbox()
        self.assertTrue(f.reference_passes(cases[0], sb))
        good = FakeLLM(replies=["```python\ndef add(a, b):\n    return a + b\n```"])
        bad = FakeLLM(replies=["```python\ndef add(a, b):\n    return a - b\n```"])
        self.assertTrue(f.grade_case(cases[0], good, sb))
        self.assertFalse(f.grade_case(cases[0], bad, sb))
        self.assertIn("YOUR TASK: c1", good.calls[0][1])       # the prompt forge would build

    def test_measured_results_reach_the_catalog(self):
        md = f.models_dir()
        md.mkdir(parents=True, exist_ok=True)
        (md / "catalog.json").write_text(json.dumps({"models": [
            {"alias": "x", "revision": "abc", "languages": {"python": 70}}]}))
        (md / "evals.json").write_text(json.dumps({"results": {"x": {
            "r/c1": {"ok": True, "lang": "python", "rev": "abc"},
            "r/c2": {"ok": False, "lang": "python", "rev": "abc"},
            "r/c3": {"ok": True, "lang": "python", "rev": "old"}}}}))
        self.assertEqual(f.load_catalog()["models"][0]["measured"], {"python": [1, 2]})


class ParallelChunks(unittest.TestCase):
    """Enhancement 6: independent chunks build side by side."""

    def setUp(self):
        self.run = make_run(tmpdir() / "r", {"project": "demo", "chunks": [
            {"id": "c1", "kind": "code", "spec": "a", "verify": "print('PASS')", "depends_on": []},
            {"id": "c2", "kind": "code", "spec": "b", "verify": "print('PASS')", "depends_on": []},
            {"id": "c3", "kind": "code", "spec": "c", "verify": "print('PASS')",
             "depends_on": ["c1", "c2"]}]},
            state={"phase": "build", "chunks": {}, "reviews": []})
        self.events = []

    def build(self, sleep=0.3, fail=None):
        def run_code_chunk(run, cfg, planner, coder, sandbox, chunk):
            t0 = time.monotonic()
            time.sleep(sleep if chunk["id"] != "c1" or not fail else 0.05)
            self.events.append((chunk["id"], t0, time.monotonic()))
            if fail and chunk["id"] in fail:
                raise fail[chunk["id"]]
            with f.Run.lock:
                run.state["chunks"][chunk["id"]] = {"status": "verified"}
                run.save()
        return run_code_chunk

    def test_independent_chunks_overlap(self):
        with mock.patch.object(f, "run_code_chunk", self.build()):
            f.build_code_chunks(self.run, {"loop": {"parallel_chunks": 2}}, None, None, None, None)
        ev = {c: (a, b) for c, a, b in self.events}
        self.assertLess(max(ev["c1"][0], ev["c2"][0]), min(ev["c1"][1], ev["c2"][1]))   # overlap
        self.assertGreaterEqual(ev["c3"][0], max(ev["c1"][1], ev["c2"][1]))           # after both

    def test_failure_waits_for_the_running_chunk_then_raises(self):
        with mock.patch.object(f, "run_code_chunk", self.build(fail={"c1": f.ForgeError("c1 broke")})):
            with self.assertRaises(f.ForgeError) as cm:
                f.build_code_chunks(self.run, {"loop": {"parallel_chunks": 2}}, None, None, None, None)
        self.assertIn("c1 broke", str(cm.exception))
        self.assertEqual(sorted(c for c, _, _ in self.events), ["c1", "c2"])   # c2 finished, no c3
        self.assertEqual(self.run.state["chunks"]["c2"]["status"], "verified")

    def test_a_hard_error_wins_over_an_upstream_fault(self):
        fails = {"c1": f.UpstreamFault("c1", "c0", "x"), "c2": f.ForgeError("c2 broke")}
        with mock.patch.object(f, "run_code_chunk", self.build(fail=fails)):
            with self.assertRaises(f.ForgeError) as cm:
                f.build_code_chunks(self.run, {"loop": {"parallel_chunks": 2}}, None, None, None, None)
        self.assertNotIsInstance(cm.exception, f.UpstreamFault)

    def test_writers_are_claimed_once(self):
        self.assertTrue(f.claim_writer("vulcan"))
        self.assertFalse(f.claim_writer("vulcan"))
        f.release_writer("vulcan")
        self.assertTrue(f.claim_writer("vulcan"))
        f.release_writer("vulcan")


if __name__ == "__main__":
    unittest.main()
