# Forge

Forge turns a development spec into independently generated and verified code
chunks, assembles them, runs regression and review passes, and commits a project
branch. Local OpenAI-compatible LLM servers do the generation; Docker isolates
generated code. Image and 3D generation, Claude review, offline documentation,
and remote lab monitoring are integrated workflows.

## Develop and test without the lab

Use Python 3.11+ (validated here on 3.12) and Bash. From the package root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/check.py --lint
.venv/bin/python forge/forge.py --help
```

The runner executes pytest, unittest, and service tests in separate processes.
Do not combine `forge/tests_unittest` with pytest: its loader changes HOME and
reimports Forge. Tests use fake HTTP/model servers and temporary directories;
they need permission to open loopback sockets, but not GPUs or a running lab.
Some optional browser, Kiwix and container checks skip when prerequisites are
absent; a skip does not verify that integration. Ruff's CI gate covers likely
execution errors; historical style diagnostics are not made a new requirement.
There is no configured static type checker.

## Configure and operate the pipeline

`forge/config.toml` is a **lab-specific example**, not a portable default.
Copy it to a local configuration and review its endpoints, paths, enabled
features, SSH hosts and API-key **file paths**. Store credentials locally; do
not commit them. Standard-library code requires Python 3.11 (`tomllib`).
The shape service additionally needs the GPU stack installed by
`install-vulcan-shape.sh`; Pillow is enough for its CPU-only tests.

The per-machine installers configure Docker, model servers, toolchain images
and user services on their respective hosts. Review their hardware assumptions
before running them; they are not required for the test workflow above.
`RUNBOOK.md` documents the original hardware deployment. Game and other
language images are built by `forge/install-forge-toolchains.sh`.

With the lab provisioned:

```sh
python3 forge/forge.py --config /path/to/local-config.toml check
python3 forge/forge.py --config /path/to/local-config.toml plan SPEC.md
python3 forge/forge.py validate-plan /path/to/run/plan.json --json
python3 forge/forge.py --config /path/to/local-config.toml resume /path/to/run
```

`forge status RUN [--json]` shows a run's phase, chunk states, last reviews,
Claude cost, deferred work and whether another process holds it. Like
`validate-plan`, it reads files only and needs no config or model server.

`validate-plan` is offline and does not execute generated verification code.
It checks plan structure, identifiers, dependency cycles, and test syntax;
it does not prove that generated code is correct or safe. Exit 0 means valid,
exit 1 invalid/unreadable. It requires neither a config nor a model server.

## Compatibility and security changes in this revision

- Project names, chunk IDs, asset IDs and artifact names must be single safe
  ASCII file components, 1–128 characters; start with a letter, digit or `_`,
  then letters, digits, `_`, `.` or `-`; no `..`. Previously accepted path-like
  names are rejected. Rename those IDs **and their dependency references** in
  local plans, outlines and associated run files before resuming. Ordinary
  `c1`/`demo-project` plans need no migration. Commits refuse symlinks in project
  destinations and retained change history; replace such links with local files.
- Dashboard browser controls require `Content-Type: application/json` and a
  same-origin request. Existing dashboard JavaScript already sends JSON.
  Scripts using `text/plain` must switch to JSON. LAN and optional PIN checks
  remain in effect; JSON/origin checks are CSRF protection, not user login.
- Control endpoints accept at most 4096 bytes; the shape endpoint retains its
  64 MiB limit. Requests require a single valid Content-Length and a JSON
  object. Oversized bodies return 413, malformed bodies 400. Reads time out
  after 10 seconds without socket activity. Chunked requests are not supported.
- Malformed tokenizer/chat payloads raise a ForgeError with the endpoint name,
  rather than silently undercounting tokens or leaking Python type errors.
- Shape initialization failures restart services this server stopped. Failed
  restarts remain visible in `/health` and can be retried with `/v1/release`.
- New model activations require the published SHA-256. Missing/invalid digests
  fail the worker; they cannot be bypassed by a same-sized cached file. Existing
  ready models are not automatically rehashed; use a fresh alias to revalidate
  suspect weights. Publish a valid upstream digest rather than disabling checks.
- Camera credentials in the supplied examples were replaced with
  `CHANGE_ME_camera_password`. The original upload still contains them: rotate
  any live credential, including reused values. For HAOS, fill a private local
  deployment copy using URL-encoded values; this does not add unsupported config
  interpolation to the add-on. Never deploy the placeholder unchanged.

These changes do not alter state/plan JSON formats or existing CLI flags.
`validate-plan` is an additive subcommand. No live services or credentials were
changed by the code review.

## Changes in the 6 November review

Behaviour changes (see `forge/CHANGES.md` for the full list):

- Review verdicts: issue severities "Blocking", "critical", "high" and "major"
  now block. A reviewer rejection that names no blocking issue now uses up a
  review round and stops after `max_review_rounds`, instead of looping.
- `forge pending` does not re-commit identical code while Claude stays
  unavailable, and backs off between tries (`[claude] pending_backoff_minutes`).
  `forge pending run RUN` forces one run. New state keys: `deferred_hash`,
  `deferred_tries`, `deferred_retry_at` (additive; older runs need no migration).
- Sandbox runs with a writable `/out` are stopped when `/out` exceeds
  `[sandbox] out_watch_mb` (default max(4 x `outputs_max_mb`, 2048) MB; `0`
  disables). The folder is emptied when that happens.
- On resume, a verified chunk whose test fails is retried once; if it still
  fails its code moves to `chunks/<id>-stale-<YYYYmmdd-HHMMSS>/verified.py`
  (the folder suffix used to be `HHMMSS` only).
- Lab dashboard controls: the request's Host must be an IP address, a one-word
  name, a `.local`/`.lan`/`.home`/`.internal`/`.home.arpa` name, or listed in the
  new `allowed_hosts` setting (`["*"]` restores the old behaviour). Wrong PINs
  are limited per address (`pin_attempts`, `pin_lockout_seconds`; HTTP 429).
- Model identifiers (alias, repo, revision, file, digest) and project paths
  with a trailing newline are now rejected.

New, additive: `forge status RUN [--json]` and `[claude] max_cost_usd`.
