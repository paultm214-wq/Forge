"""amend_contract must survive whatever shape the planner uses (glassinv: a
rebuild answered with plain strings and the run crashed)."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import forge


def make_run(tmp_path):
    for d in ("chunks", "verified"):
        (tmp_path / d).mkdir()
    (tmp_path / "SPEC.md").write_text("spec")
    plan = {"project": "p", "contract": {"names": [
        {"name": "Store", "owner": "c2", "signature": "(self, data_dir)"},
        {"name": "Store.delete", "owner": "c2", "signature": "(self, kind, id)"}]},
        "chunks": [{"id": c, "kind": "code", "spec": "x", "depends_on": [], "verify": "pass"}
                   for c in ("c2", "c8", "c10")]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    run = forge.Run(tmp_path)
    run.state = {"chunks": {}}
    run.save()
    return run


def test_string_names_from_a_rebuild(tmp_path):
    run = make_run(tmp_path)
    ok = forge.amend_contract(run, {"names": ["Store.delete(self, kind, id) - raises KeyError",
                                              "_supply_detail(store, kind, id)"],
                                    "remove": ["Nope"], "routes": ["GET /supplies/<kind>/<id>"]},
                              "rebuild of c8", default_owner="c8")
    assert ok
    names = forge.contract_names(run.plan)
    assert names["Store.delete"]["owner"] == "c2"          # existing owner kept
    assert names["_supply_detail"]["owner"] == "c8"        # new name: the rebuilt chunk
    assert names["_supply_detail"]["signature"] == "(store, kind, id)"
    assert run.plan["contract"]["routes"] == [{"method": "GET", "path": "/supplies/<kind>/<id>"}]


def test_odd_shapes_never_crash(tmp_path):
    run = make_run(tmp_path)
    for bad in ("change everything", 42, ["Store.close(self)"], {"names": "Store.open(self)"},
                {"names": [{"signature": "()"}, 7, None]}, {"schema_sql": ["x"]}):
        forge.amend_contract(run, bad, "test", default_owner="c10")
    assert "Store.close" in forge.contract_names(run.plan)
    assert "Store.open" in forge.contract_names(run.plan)
