"""Coder models per language: catalog validation against a fake Hugging Face,
the planner's assignment, and switching at chunk boundaries."""
import http.server
import json
import threading

import pytest

import forge
from conftest import FakeLLM

GB = 2**30
SHA = "b" * 40


class FakeHF(http.server.BaseHTTPRequestHandler):
    repos = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        repo = path[len("/api/models/"):]
        if repo in self.repos:
            body = json.dumps(self.repos[repo]).encode()
            self.send_response(200)
        else:
            body = b'{"error": "not found"}'
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def repo(files, license="apache-2.0", **kw):
    return {"sha": SHA, "cardData": {"license": license},
            "siblings": [{"rfilename": f, "size": int(s * GB)} for f, s in files.items()], **kw}


@pytest.fixture
def hf(monkeypatch):
    FakeHF.repos = {
        "unsloth/JSCoder-30B-A3B-GGUF": repo({"JSCoder-UD-Q4_K_XL.gguf": 18.0,
                                               "JSCoder-UD-IQ3_XXS.gguf": 12.5}),
        "bartowski/Dense-14B-GGUF": repo({"Dense-14B-Q4_K_M.gguf": 9.0,
                                          "Dense-14B-Q8_0.gguf": 15.5}, license="mit"),
        "someone/Closed-GGUF": repo({"c.gguf": 5.0}, license="other"),
        "someone/Gated-GGUF": repo({"g.gguf": 5.0}, gated="auto"),
    }
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHF)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HF_ENDPOINT", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("HF_TOKEN", "")
    yield
    srv.shutdown()


REPLY = {"models": [
    {"alias": "jscoder", "repo": "unsloth/JSCoder-30B-A3B-GGUF", "kind": "qwen38", "moe": True,
     "languages": {"web": 90, "python": 70, "klingon": 99}, "context": 65536,
     "quants": {"apollo": {"files": ["JSCoder-UD-Q4_K_XL.gguf"], "n_cpu_moe": 10},
                "vulcan": {"files": ["JSCoder-UD-Q4_K_XL.gguf"]},
                "mercury": {"files": ["JSCoder-UD-Q4_K_XL.gguf"]},
                "nowhere": {"files": ["JSCoder-UD-Q4_K_XL.gguf"]}}},
    {"alias": "dense14", "repo": "bartowski/Dense-14B-GGUF", "kind": "plain", "moe": False,
     "languages": {"rust": 85, "c-cpp": 80},
     "quants": {"apollo": {"files": ["Dense-14B-Q8_0.gguf"]},
                "vulcan": {"files": ["Dense-14B-Q4_K_M.gguf"]},
                "mercury": {"files": ["missing.gguf"]}}},
    {"alias": "closed", "repo": "someone/Closed-GGUF", "languages": {"web": 99},
     "quants": {"apollo": {"files": ["c.gguf"]}}},
    {"alias": "gated", "repo": "someone/Gated-GGUF", "languages": {"web": 99},
     "quants": {"apollo": {"files": ["g.gguf"]}}},
    {"alias": "freecoder", "repo": "x/Coder-abliterated-GGUF", "languages": {"web": 99},
     "quants": {"apollo": {"files": ["a.gguf"]}}},
    {"alias": "tiel-coder", "repo": "x/y", "languages": {"python": 1}},
]}


def test_refresh_validates_against_hugging_face(hf):
    cat = forge.refresh_catalog({}, json.dumps(REPLY))
    by = {m["alias"]: m for m in cat["models"]}
    assert set(by) == {"tiel-coder", "jscoder", "dense14"}
    js = by["jscoder"]
    assert js["revision"] == SHA and js["languages"] == {"web": 90, "python": 70}
    assert set(js["quants"]) == {"apollo", "vulcan", "mercury"}  # MoE: experts in RAM
    assert js["quants"]["apollo"] == {"files": ["JSCoder-UD-Q4_K_XL.gguf"], "size_gb": 18.0,
                                      "n_cpu_moe": 10}
    assert js["quants"]["vulcan"]["n_cpu_moe"] > 0              # estimated
    d = by["dense14"]
    assert set(d["quants"]) == {"vulcan"}                       # Q8 dense too big; file missing
    notes = "\n".join(cat["notes"])
    assert "licence other" in notes and "gated" in notes and "abliterated" in notes
    assert (forge.models_dir() / "catalog.json").exists()
    # A refresh where nothing passes keeps the old catalog.
    with pytest.raises(forge.ForgeError, match="keeping the previous"):
        forge.refresh_catalog({}, json.dumps({"models": [REPLY["models"][2]]}))
    assert len(forge.load_catalog()["models"]) == 3


class FakeMachine(forge.Machine):
    def __init__(self, name, llm, models=None):
        super().__init__(name, llm, {**forge.MACHINE_DEFAULTS[name], "context": llm.context})
        self.models = models or {}
        self.calls = []
        self.changed = False

    def ssh(self, *args, stdin=None, timeout=60):
        self.calls.append(args)
        if args[0] == "status":
            return {"models": self.models, "presets_changed": self.changed}
        if args[0] == "ensure":
            job = json.loads(stdin)
            self.models[job["alias"]] = {"state": "downloading", "bytes": 1, "total": 4}
            return {"state": "downloading"}
        if args[0] == "activate":
            self.changed = False
            return {"restarted": True}
        return {}


def coder_llm(name):
    return forge.LLM(name, {"url": "http://127.0.0.1:9", "model": "tiel-coder", "kind": "tiel",
                            "context": 65536, "max_tokens": 28000, "thinking_max_tokens": 32000})


def make_run(tmp_path):
    root = tmp_path / "run"
    (root / "chunks").mkdir(parents=True)
    (root / "SPEC.md").write_text("# app\n")
    plan = {"project": "app", "chunks": [
        {"id": "c1", "kind": "code", "title": "store", "spec": "sqlite store", "verify": "pass"},
        {"id": "c2", "kind": "code", "title": "ui js", "spec": "the page's script", "verify": "pass"},
        {"id": "c3", "kind": "code", "title": "more js", "spec": "charts in JS", "verify": "pass"}]}
    (root / "plan.json").write_text(json.dumps(plan))
    run = forge.Run(root)
    run.state = {"chunks": {}}
    run.save()
    return run


def test_assign_and_route(hf, tmp_path, monkeypatch):
    forge.refresh_catalog({}, json.dumps(REPLY))
    run = make_run(tmp_path)
    ap, vu = FakeMachine("apollo", coder_llm("coder")), FakeMachine("vulcan", coder_llm("v"))
    monkeypatch.setattr(forge, "MACHINES", {"apollo": ap, "vulcan": vu})
    planner = FakeLLM({"which coding model each coder machine": [json.dumps({
        "chunk_languages": {"c1": "python", "c2": "web", "c3": "web"},
        "assign": {"apollo": {"default": "tiel-coder", "by_language": {"web": "jscoder"}},
                   "vulcan": {"default": "nonsense-model"}},
        "why": "JS chunks to the JS model"})]})
    forge.assign_coders(run, {}, planner)
    a = run.state["coder_models"]["assign"]
    assert a["apollo"] == {"default": "tiel-coder", "by_language": {"web": "jscoder"}}
    assert a["vulcan"]["default"] == "jscoder"      # invalid pick -> best for "web"
    assert any(c[0] == "ensure" for c in ap.calls)  # downloads start at once
    # c1 (python): Apollo keeps TielCoder.
    plan = run.plan
    forge.route_coders(run, plan["chunks"][0])
    assert ap.llm.model == "tiel-coder"
    # c2 (web) while still downloading: keeps TielCoder.
    forge.route_coders(run, plan["chunks"][1])
    assert ap.llm.model == "tiel-coder"
    # Download done, presets changed: router restarted, probe, switch.
    ap.models["jscoder"] = {"state": "ready"}
    ap.changed = True
    ap._at = 0
    monkeypatch.setattr(forge, "probe", lambda m, timeout=900: None)
    forge.route_coders(run, plan["chunks"][1])
    assert ap.llm.model == "jscoder" and ap.llm.kind == "qwen38"
    assert ("activate",) in ap.calls
    # Back to a python chunk: base settings restored.
    forge.route_coders(run, plan["chunks"][0])
    assert ap.llm.model == "tiel-coder" and ap.llm.kind == "tiel"
    assert ap.llm.prompt_budget == ap.base["prompt_budget"]


def test_failed_load_falls_back_and_blocks(hf, tmp_path, monkeypatch):
    forge.refresh_catalog({}, json.dumps(REPLY))
    run = make_run(tmp_path)
    ap = FakeMachine("apollo", coder_llm("coder"), {"jscoder": {"state": "ready"}})
    monkeypatch.setattr(forge, "MACHINES", {"apollo": ap})
    run.state["coder_models"] = {"chunk_languages": {"c2": "web"},
                                 "assign": {"apollo": {"default": "jscoder", "by_language": {}}}}
    monkeypatch.setattr(forge, "probe", lambda m, timeout=900: "HTTP 500: out of memory")
    forge.route_coders(run, run.plan["chunks"][1])
    assert ap.llm.model == "tiel-coder"
    assert ("revert", "jscoder") in ap.calls
    assert "jscoder" in forge.blocked()["apollo"]
    assert not forge.fits({a["alias"]: a for a in forge.load_catalog()["models"]}["jscoder"], "apollo")


def test_no_catalog_means_tielcoder(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setattr(forge, "MACHINES", {"apollo": FakeMachine("apollo", coder_llm("coder"))})
    forge.assign_coders(run, {}, FakeLLM({}))
    assert "coder_models" not in run.state


def test_ensure_job_and_plain_kind():
    e = {"alias": "dense14", "repo": "a/b", "revision": SHA, "context": 32768, "mtp": True,
         "temperature": 0.3, "quants": {"vulcan": {"files": ["x.gguf"], "size_gb": 9.0, "n_cpu_moe": 0}}}
    j = forge.ensure_job(e, "vulcan")
    assert j["preset"] == {"c": 32768, "temp": 0.3, "jinja": "true", "spec-type": "draft-mtp",
                           "spec-draft-n-max": 3}
    llm = coder_llm("x")
    llm.kind = "plain"
    assert llm.template_kwargs(True) == {}


def test_language_keys_are_normalised_and_estimates_discounted(hf):
    reply = {"models": [
        {"alias": "tiel-coder", "languages": {"python": 82, "javascript": 70, "gdscript": 50},
         "estimated": ["gdscript"]},
        dict(REPLY["models"][0], languages={"Python": 75, "JavaScript": 88, "TypeScript": 91,
                                            "C++": 60, "bash": 55, "GDScript": 70},
             estimated=["GDScript"])]}
    cat = forge.refresh_catalog({}, json.dumps(reply))
    by = {m["alias"]: m for m in cat["models"]}
    assert by["tiel-coder"]["languages"] == {"python": 82, "web": 70, "gdscript": 40}
    assert by["jscoder"]["languages"] == {"python": 75, "web": 91, "c-cpp": 60, "shell": 55,
                                          "gdscript": 60}
    assert forge.best_for(cat, "apollo", "web")["alias"] == "jscoder"
    assert forge.best_for(cat, "apollo", "python")["alias"] == "tiel-coder"
    assert forge.best_for(cat, "apollo", "rust")["alias"] == "tiel-coder"   # nobody scored rust
    assert (forge.models_dir() / "last-search-reply.txt").exists()
