"""Regressions at the model-to-host and model-server boundaries."""
import json
from pathlib import Path

import pytest

import forge
from conftest import make_run


def chunk(cid="c1"):
    return {"id": cid, "spec": "return one", "verify": "assert True"}


@pytest.mark.parametrize("cid", ["../../escaped", "/tmp/escaped", "a/b", "..", "x\n"])
def test_plan_rejects_path_ids(cid):
    assert forge.validate_plan({"project": "demo", "chunks": [chunk(cid)]})
    assert forge.validate_outline({"project": "demo", "chunks": [chunk(cid)]})


def test_verified_writer_rejects_escape(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(forge.ForgeError):
        run.save_verified("../../escaped", "host write")
    assert not (tmp_path / "escaped.py").exists()


@pytest.mark.parametrize("link", ["projects", "demo", "changes", "history-entry"])
def test_commit_never_follows_base_project_symlink(tmp_path, monkeypatch, link):
    run = make_run(tmp_path, plan={"project": "demo", "chunks": [chunk()]})
    (run.root / "module.py").write_text("VALUE = 1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("precious")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    def git(root, *args, **kw):
        if args[:2] == ("worktree", "add"):
            wt = Path(args[-2])
            if link == "projects":
                wt.mkdir()
                (wt / "projects").symlink_to(outside, target_is_directory=True)
            elif link == "demo":
                (wt / "projects").mkdir(parents=True)
                (wt / "projects" / "demo").symlink_to(outside, target_is_directory=True)
            else:
                dest = wt / "projects" / "demo"
                dest.mkdir(parents=True)
                if link == "changes":
                    (dest / "changes").symlink_to(outside, target_is_directory=True)
                else:
                    (dest / "changes").mkdir()
                    (dest / "changes" / "run.md").symlink_to(sentinel)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(forge, "git", git)
    with pytest.raises(forge.ForgeError, match="symlink|outside"):
        forge.commit(run, {"git": {"repo": str(repo)}}, "report")
    assert sentinel.read_text() == "precious"


@pytest.mark.parametrize("response", [{}, [], {"tokens": "123"}, {"tokens": None}])
def test_bad_tokenizer_reply_is_not_zero_tokens(monkeypatch, response):
    llm = forge.LLM("bad-server", {"url": "http://unused"})
    monkeypatch.setattr(llm, "_post_retrying", lambda *a, **kw: response)
    with pytest.raises(forge.ForgeError, match="token"):
        llm.tokens("a large prompt")


@pytest.mark.parametrize("response", [[], {"choices": "bad"}, {"choices": [None]},
                                      {"choices": [{"message": {"content": ["bad"]}}]}])
def test_bad_chat_reply_is_a_pipeline_error(monkeypatch, response):
    llm = forge.LLM("bad-server", {"url": "http://unused", "tokenize": False})
    monkeypatch.setattr(llm, "_post_retrying", lambda *a, **kw: response)
    with pytest.raises(forge.ForgeError):
        llm.chat("system", "user")


@pytest.mark.parametrize("plan", [[], None, {}, {"chunks": []}, {"chunks": {}}, {"chunks": "bad"}])
def test_malformed_plan_is_diagnostic(plan):
    assert forge.validate_plan(plan)


@pytest.mark.parametrize("field,value", [("filename", "../outside.png"),
                                         ("filename", "/tmp/outside.png"),
                                         ("shape", {"filename": "../outside.glb"})])
def test_model_supplied_asset_paths_are_checked(field, value):
    image = {"id": "a1", "kind": "image", "filename": "ok.png", field: value}
    assert forge.validate_plan({"project": "demo", "chunks": [image]})


def test_tokenizer_may_legitimately_return_an_empty_list(monkeypatch):
    llm = forge.LLM("server", {"url": "http://unused"})
    monkeypatch.setattr(llm, "_post_retrying", lambda *a, **kw: {"tokens": []})
    assert llm.tokens(" ") == 0


def test_config_error_is_actionable(tmp_path, capsys):
    missing = tmp_path / "missing.toml"
    assert forge.main(["--config", str(missing), "check"]) == 1
    assert "Config not found" in capsys.readouterr().out


def test_validate_plan_cli_needs_no_config_or_servers(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"project": "demo", "chunks": [chunk()]}))
    assert forge.main(["--config", "/missing", "validate-plan", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    path.write_text(json.dumps({"chunks": [chunk("../x")]}))
    assert forge.main(["validate-plan", str(path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"]


def test_log_reattach_closes_old_handle(tmp_path):
    log = forge.Log()
    log.attach(tmp_path / "first.log")
    old = log.fh
    try:
        log.attach(tmp_path / "second.log")
        assert old.closed
    finally:
        old.close()
        log.fh.close()
