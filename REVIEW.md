# Forge package review and implementation

## 1. Executive summary

**Received package: 6/10.** The pipeline has a clear purpose, extensive regression
tests, explicit failure recovery, atomic state writes and a substantially hardened
Docker execution path. However, isolation of generated *execution* did not cover
all generated *metadata*: identifiers could escape host directories. Project
cleanup could also follow a base-branch symlink. Other material problems were
browser control CSRF, credentials distributed in example files, failed GPU-service
recovery and an optional rather than mandatory model checksum check.

The top five issues are findings **1–5** below. All confirmed Critical/High issues
identified in this pass were addressed in the revised source. Credential validity
was not tested, and removing an exposed value does not rotate it.

The top three enhancements are **offline plan validation**, **GPU service
recovery**, and **strict boundary validation for model replies and HTTP requests**.
All six ranked meaningful enhancements below were implemented, along with the
three listed quick wins.

**Validation after changes:** 530 main pytest tests passed, 7 skipped; 68 unittest
tests passed; 43 service tests passed. Total: **641 passed, 7 skipped, no failures**.
The focused Ruff gate, Python syntax checks and all 29 shell syntax checks passed.
Live GPU generation, remote SSH/systemd, production Docker images and Claude were
not exercised. CI configuration was added but has not run on GitHub.

## 2. Orientation and scope

### Module map and execution paths

| Area | Responsibility and flow |
|---|---|
| `forge/forge.py` | Main CLI and orchestration (~17,000 lines). Spec → prior-art/design lookup → outline/contract/tests → generated chunks → sandbox checks/retries → assembly/regression/reviews → outputs/docs → Git branch. `main`, `execute`, `staged_plan`, `run_code_chunk`, `commit` are key entry points. |
| `Run`, `Status`, `Log` | Filesystem-backed run state, verified code, attempt artifacts, locks, progress and logs. Resume reads existing plans/state and skips completed work. |
| `LLM`, `ImagePool`, shape helpers | OpenAI-compatible HTTP calls, prompt budgets, parallel coders and image/shape work, shared GPU scheduling. |
| `forge/forge_game.py` | Sandboxed project materialization and compiler/engine/test harnesses: Python verification drives many other languages and game tools. |
| `forge_java.py`, `forge_dotnet.py`, API indexes | Real API facts supplied to diagnosis and generation rather than invented signatures. |
| `forge_ux.py`, `design/` | Offline Playwright/axe reports, screenshots, keyboard checks and design assets for generated projects. |
| `forge-shape-server.py` | GPU-backed Hunyuan3D HTTP service. May stop other user services to free VRAM, then restore them on release. |
| `coder-models/` | SSH-invoked model manager: pinned Hugging Face revision → resumable download → digest verification → router preset activation. |
| `lab-dashboard/` | Agents collect host/GPU/service state; the hub polls them and serves a web dashboard. Browser → hub LAN/PIN checks → agent control credential → fixed allowlisted actions. |
| Installers, `metis/`, `ziggurat/`, runbook | Host provisioning, caches/mirrors, Forgejo and hardware-specific operations. |

Most orchestration code uses Python's standard library and requires Python 3.11+
for `tomllib`. Tests require pytest; image handling uses Pillow. Production shape
generation additionally uses PyTorch/Hunyuan3D and its image/mesh stack. The UX
image needs Playwright, Chromium and axe. Docker images supply the many language
and engine toolchains. Integrations include local HTTP model/image/shape servers,
SSH, Git/Forgejo/GitHub, Hugging Face, Kiwix, Claude CLI and filesystem key files.
There is no central application database; generated-project SQL harnesses are
separate. Configuration comes from TOML, key-file paths and helper-specific
environment variables such as `STACK_DIR`, `CODER_MODELS_DIR`, `HF_ENDPOINT`,
`LAB_AGENT_CONFIG`, `LAB_DASHBOARD_CONFIG` and `FORGE_*` test/harness settings.

**Reviewed in depth:** plan/outline validation, LLM response handling, run artifact
writes, Git project cleanup, dashboard/agent POST handlers, shape service resource
recovery and model download verification, with their surrounding callers/tests.
**Reviewed structurally or selectively:** the rest of orchestration, toolchain
harnesses, UX helpers, API indexes, installers and deployment documents. This is
not an exhaustive audit of every engine adapter or provisioning command. The
uploaded operational documents were treated as project material, not instructions
to modify or contact the described lab.

## 3. Findings table

Locations identify functions in the revised files; the detailed sections describe
the received implementation. **Confirmed** means reproduced or directly traced;
no speculative issue is presented as a defect.

| # | Severity | Category | Location | Issue | Confidence | Suggested fix/status |
|---|---|---|---|---|---|---|
| 1 | Critical | Host filesystem security | `forge/forge.py`: `chunk_shape_errors`, `Run.save_verified`, `Run.artifact`, image writers | IDs were only checked for nonempty strings, then interpolated into host paths. Planner image paths also bypassed spec-only checks. | Confirmed | Validate components/asset paths at generation, save, resume and writer boundaries. Implemented. |
| 2 | Critical | Data loss | `forge/forge.py`: `commit` | A `projects/<slug>` symlink in the base branch could direct project cleanup outside the worktree. Retained `changes` links could redirect later writes. | Confirmed | Refuse symlink destinations/ancestors and symlinks in retained history before cleanup. Implemented. |
| 3 | High | Credential exposure | `frigate-config-haos-addon.yml` RTSP URLs; `frigate.env.example` camera examples; `RUNBOOK.md` §9 | A password-like literal was repeated in deployable URLs and documentation. | Confirmed exposure; validity untested | Removed from revised files. Rotate any live/reused value; original upload still contains it. |
| 4 | High | Browser control CSRF | `lab-dashboard/lab-dashboard.py`: `make_server`/POST handler (formerly `serve`) | LAN source and optional PIN checks did not constrain browser origin or content type. Hub holds the agent credential. | Confirmed | Require JSON and reject cross-origin/cross-site requests before dispatch. Implemented. |
| 5 | High | GPU/service lifecycle | `forge-shape-server.py`: `load`, `release` | A failed VRAM check/model initialization after stopping services left them stopped. Release reported restarts successful even on nonzero systemctl exit. | Confirmed | Roll back on load failure; retain failed restarts for retry. Implemented. |
| 6 | High | Artifact integrity | `coder-models/coder-models.py`: `download_worker` | `if oid and ...` bypassed SHA-256 verification when metadata omitted the digest. A same-sized local file could become ready unchecked. | Confirmed | Require a valid published digest and always compare it before adding a preset. Implemented. |
| 7 | Medium | HTTP input/resource handling | Shape, dashboard and agent POST handlers | Negative lengths could reach `read(-1)`; control handlers clipped lengths rather than rejecting excess; non-object JSON could cause attribute errors; reads lacked a socket deadline. | Confirmed | Validate framing, reject oversized/non-object/incomplete bodies and bound idle reads. Implemented. |
| 8 | Medium | Model protocol handling | `forge/forge.py`: `LLM.tokens`, `LLM._chat` | `{}` at `/tokenize` silently counted as zero; wrong JSON shapes caused raw AttributeError/TypeError rather than pipeline diagnostics. | Confirmed | Validate token/message/usage structures without logging response bodies. Implemented. |
| 9 | Medium | Validation contract | `forge/forge.py`: `validate_plan`, `validate_outline` | A non-object plan crashed validation; falsy malformed chunks such as `{}` were coerced to an empty list. | Confirmed | Validate original types and reject empty executable plans; provide offline diagnostics. Implemented. |
| 10 | Low | Developer experience/resources | `forge/forge.py`: `main`, `load_config`, `Log.attach`; model `cmd_ensure` | Config errors occurred before the CLI's ForgeError handler; log replacement and detached-worker setup left file handles to implicit cleanup. | Confirmed | Actionable config errors and explicit handle closure. Implemented. |

## 4. Detailed Critical/High findings

### 1 — Generated metadata could write outside a run

In the received code, `chunk_shape_errors` (original line 1008) accepted
`{"id":"../../escaped", ...}`. `Run.save_verified` (original line 2418) then
wrote `root/verified/../../escaped.py`. The new regression reproduced creation of
a file outside the run rather than a ForgeError. Staged planning and attempt
artifacts also used chunk IDs as paths, so this was not confined to a single
manual API call. The Docker isolation flags cannot protect host writes performed
before or after container execution. Separately, the received generic plan
validator did not apply `spec_assets`' filename checks to planner-authored image
chunks.

**Fix:** reusable component and asset-path checks, applied to plan/outline
validation, persisted plan reads/writes, saved-outline reuse, asset specs,
verified/artifact writers, image/shape dispatch and `--new-project`. Invalid
metadata is rejected, not silently normalized. Tests cover relative/absolute
escapes, slashes, `..`, trailing newlines, saved writes and image/shape paths.
See [boundary validation](forge/forge.py) and
[regressions](forge/tests/test_package_review.py).

**Compatibility:** path-like/overlong/non-ASCII names previously accepted are now
errors. Rename identifiers and dependent run files/references before resuming;
normal `c1`/`demo-project` identifiers are unaffected. No JSON key was renamed.

### 2 — Git cleanup could delete host data through a symlink

`commit` (original line 7582) constructed `dest = wt / "projects" / slug`, then
used `dest.is_dir()` and iterated/deleted its children. Both follow a symlink at
`dest`. A base branch containing `projects/demo -> /some/other/directory` could
therefore lose that directory's contents. This requires such a link in the base
branch; no claim is made that an ordinary slug alone bypasses Git branch checks.

**Reproduction:** the test supplies a base-worktree symlink to a temporary
directory containing a sentinel. The received function did not raise and deleted
the sentinel. The revised function rejects it before cleanup. Additional tests
cover a symlink at `projects`, retained `changes`, and a history entry. The test
fakes Git worktree creation, but uses real filesystem links and the real cleanup
function. Existing Git commit/change end-to-end tests continue passing.

**Fix:** destination containment and symlink refusal before touching project
files; retained history is checked because it deliberately survives cleanup.
Replace such links with actual files/directories if a project needs committing.

### 3 — Credentials were included in distributable files

The received HAOS template contained the same password-like value in RTSP URL
userinfo (original line 214 onward), and a comment explicitly called it
compromised (original lines 82–87). Variants also appeared in the environment
example and runbook. These values are deliberately omitted here. I did not
attempt camera access or assume the credential remains live.

**Fix:** replaced both encoded and decoded representations in all three files
with `CHANGE_ME_camera_password`; updated deployment guidance. A scan derived
the original values from RTSP fields and confirmed they are absent from revised
package files. This does not remove them from the upload, prior copies or Git
history. **Rotate any live/reused credential outside this task.** HAOS deployment
still requires a private config copy; no unsupported interpolation was introduced.

### 4 — A LAN browser could issue cross-origin controls

In the received dashboard's `serve` POST handler (original line 417), a request
from a LAN browser passed `lan_client`. With controls enabled and no PIN, an
unrelated page could send a JSON string using `Content-Type: text/plain`, a
simple browser request that does not need JSON CORS preflight. The server parsed
it as JSON and forwarded its own control token. A response need not be readable
for the action to execute. Browser private-network protections can reduce this
in some deployments; the server itself provided no origin/content-type defense.

**Reproduction:** an actual local HTTP request with an unrelated Origin and
text/plain executed one fake control in the uploaded version: **202, 1 action**.
Same request after the fix: **403, 0 actions**. See
[before/after evidence](review-evidence/boundary-before-after.json).

**Fix:** JSON content type, same-origin Origin when supplied, and rejection of
`Sec-Fetch-Site: cross-site`, before PIN/action processing. Existing browser code
already uses JSON. Direct scripts must use JSON; origin checks are not intended
to authenticate arbitrary LAN clients. Existing optional PIN/LAN policies remain.

### 5 — Failed shape startup stranded other GPU services

`make_room` could stop `sd-server` before discovering insufficient VRAM or before
`from_pretrained` failed. `load` did not restore it. Idle cleanup only considered
a non-null pipeline, so a failed initial load was not guaranteed automatic
recovery. Separately, `release` appended each unit to `restarted` and cleared
the list regardless of systemctl's return code.

**Fix:** model-load exceptions restore services this server stopped; unsuccessful
restarts stay in `STATE["stopped"]`, remain visible through `/health`, and can be
retried by `/v1/release`. Tests inject failed initialization and failed/successful
systemctl results. They verify the service list and calls, without claiming a
real GPU or user-session bus was tested. Unloading another model remains
intentional GPU arbitration; this change does not promise to restore its weights.

### 6 — Missing checksums silently disabled model verification

`download_worker` (original line 284) checked `if oid and sha256(dest) != oid`.
Absent metadata therefore skipped the check. A preexisting same-sized file also
skipped download and could become a router model. HTTPS protects transfer; it
does not establish the integrity of an existing local file.

**Reproduction:** with a temporary model directory and fake published metadata
containing an empty digest, the uploaded worker returned **0 / ready**; the fixed
worker returns **1 / failed** and writes no new preset. Regression cases cover
missing, malformed, wrong and correct digests.

**Fix:** require a 64-character published SHA-256 before downloading/activating;
always compare it. Previously ready aliases are not automatically rehashed.
For suspect existing weights, use a fresh alias to force the verified worker.

## 5. Performance, design, testing and packaging assessment

**Performance:** no additional algorithmic performance defect was substantiated.
At the documented tens-of-chunks scale, GPU inference/render time dominates;
optimizing small plan-list scans without a measured profile would be guesswork.
Token caches, dashboard history and status log retention already have bounds.
The HTTP changes prevent unlimited single-body reads and bound idle sockets, but
the thread-per-connection servers still need an external concurrency policy on
hostile networks. Ten seconds is an inactivity timeout, not an end-to-end deadline.

**Design:** explicit orchestration rather than LLM-directed control flow is a
sound choice. Docker execution with dropped capabilities, no network and capped
resources, along with atomic state writes and a separate SandboxError, are real
strengths. The large orchestrator and process-global context make boundary
changes expensive to reason about. A schema/module extraction is a follow-up,
not an excuse to rewrite working engine adapters in this patch.

**Security scope:** no authentication was added to the LAN-oriented shape service
or read-only telemetry APIs without a policy decision. Local key files, loopback
servers, remote installers and optional Claude access are distinct trust
boundaries. No live deployment penetration test or complete production
dependency/CVE audit was performed. GPU/toolchain installers contain dynamic
upstream dependencies/images; no invented vulnerability attribution is made.
The added development requirements pin direct versions, not an entire transitive
production environment.

**Tests:** existing tests meaningfully assert retries, contracts, state changes,
sandbox flags and end-to-end fake-server behavior. Most compiler/GPU/control
integrations are faked, so these are not hardware readiness tests. The unittest
loader changes HOME/reimports Forge; running it separately is required and is
preserved in the new runner. A preexisting test emits an unclosed fake-server
socket ResourceWarning; it does not fail the suite and was not turned into a
production-resource finding. No type checker was configured; no type-check pass
is claimed. Ruff's full default scan has **95 preexisting diagnostics**, still
95 afterward (mostly style/unused code plus a test-function redefinition).

**Specific previously missing tests, now added:** rejected generated IDs/paths;
symlink-safe commit cleanup; malformed plan/tokenizer/chat payloads; actionable
config failure; log reattachment cleanup; offline validation exit codes/JSON;
shape-load rollback and retryable service restart; real HTTP non-object/negative/
oversized input rejection and origin checks; strict digest activation.

**Highest-value next tests:** real Docker sandbox escape/output-limit tests in CI;
shape load/OOM/release with a real GPU and systemd; cancellation against the
supported actual model servers; representative real toolchain image builds;
browser dashboard/UX accessibility checks. Keyboard confirmation already exists
in the dashboard slider; browser/axe checks were unavailable here, so no new
accessibility failure is asserted.

**Developer experience:** the package lacked a root installation/testing guide
and root dependency/CI entry point. `README.md`, `requirements-dev.txt`,
`tools/check.py` and the CI workflow now explain and run the CPU development
workflow. The original lab runbook remains hardware-specific. This remains a
script-based application, not a newly published Python package.

## 6. Ranked enhancement roadmap

Estimates are rough focused engineering time, excluding hardware provisioning.
Ranks within each tier reflect the value for this local-model development stack.

### Quick wins — all implemented

| Rank | Improvement/value and beneficiary | Effort | Risk/trade-off | Dependencies |
|---|---|---|---|---|
| Q1 | Root developer guide, pinned test tools, one test runner and CI; new contributors can reproduce CPU validation. | Half day | Pins need maintenance; GPU integrations remain separate. | Python/Bash, loopback socket access. |
| Q2 | Actionable missing/invalid config errors; operators get a normal error exit instead of a traceback. | 1–2 hours | Error text changes; successful CLI behavior unchanged. | None. |
| Q3 | Explicit log/worker-log handle closure; long inbox/model operations avoid relying on implicit file cleanup. | 1–2 hours | Detached child still owns its own inherited handle intentionally. | Existing logging/worker code. |

### Meaningful enhancements — all six implemented

| Rank | Improvement/value and beneficiary | Effort | Risk/trade-off | Dependencies |
|---|---|---|---|---|
| E1 | `forge validate-plan PLAN --json`: offline inspection before spending model/GPU time; operators and automation. | Half day | Structural validation cannot prove semantic correctness. | Shared plan/identifier validation. |
| E2 | Shape startup rollback and retryable service restoration; shared-GPU users avoid stranded coders/renderers. | Half day | Cannot fix a broken systemd unit or restore unloaded model weights. | Existing GPU lock and systemctl. |
| E3 | Strict tokenizer/chat response contracts; unsupported/misconfigured local servers fail diagnostically. | Half day | Previously tolerated malformed responses are errors; valid empty token lists remain supported. | Existing ForgeError/budget handling. |
| E4 | Bounded HTTP framing/object checks and idle-read timeouts; protects long-running service processes from malformed clients. | Half day | Chunked requests rejected; very slow uploads may need a future configurable timeout. | HTTP handler access; no new runtime package. |
| E5 | Browser-origin/content-type checks on dashboard controls; protects LAN operators visiting unrelated websites. | Half day | JSON required for custom scripts; this does not replace authentication. | E4 and existing LAN/PIN checks. |
| E6 | Mandatory published SHA-256 before model activation; prevents unchecked new/cached model files becoming ready. | Half day | Metadata without a digest now fails closed. Existing ready aliases need explicit revalidation if suspect. | Authoritative model metadata/digest. |

### Strategic features — proposed, not implemented

| Rank | Feature/value and beneficiary | Effort | Risk/trade-off | Dependencies |
|---|---|---|---|---|
| S1 | Durable shared-GPU lease arbiter with reconciliation after process/host failure; coordinates Forge, Strata, images and shapes. | 1–2 weeks | More coordination state and recovery policy to operate. | Agree on GPU ownership/service restoration contracts; E2. |
| S2 | Versioned run/plan schemas and a read-only run doctor with optional explicit migrations; helps users recover long jobs across upgrades. | 3–5 days | Migration/version support becomes a public contract. | E1; representative historical runs and compatibility policy. |
| S3 | Reproducible per-toolchain manifests, image digests and real integration CI on selected lab hardware; reliable rebuilds after upstream changes. | 1–2 weeks initially | Storage/build time, license constraints and host-specific runners. | Hardware access, registry policy, representative target selection. |

## 7. Implemented changes, code and evidence

The full revised code is in the accompanying `forge-reviewed.tar.gz` and in this
checkout. `forge-review.patch` is a unified diff against the uploaded package for
code, tests and added developer files. **It deliberately excludes the three
credential-containing originals** (`RUNBOOK.md`, `frigate.env.example`,
`frigate-config-haos-addon.yml`) so it cannot republish their deleted secret.
Their complete sanitized replacements are in the revised archive. Evidence and
this report are supplied separately rather than bloating the patch.

| Change | Files/tests proving behavior |
|---|---|
| Host/plan/asset validation; safe commit destination | `forge/forge.py`; `forge/tests/test_package_review.py` (escape, image path and four symlink cases). |
| Offline command and config errors | `forge/forge.py`; CLI tests check JSON/exit codes and no config requirement. |
| LLM protocol validation and log cleanup | `forge/forge.py`; malformed payload, legitimate empty tokenizer-list and reattachment tests. |
| GPU recovery | `forge-shape-server.py`; `forge/tests/test_shape_server.py` failure injection and retry assertions. |
| HTTP validation/CSRF | Shape server, `lab-dashboard/lab-dashboard.py`, `lab-agent.py`; shape object tests and `lab-dashboard/test_http_controls.py` run real local HTTP requests without real actions. |
| Integrity and worker-log lifecycle | `coder-models/coder-models.py`; `test_integrity.py` plus the existing download/resume/activation tests. |
| Sanitized credentials and migration guidance | Three sanitized files; `README.md` documents manual rotation, private config and compatibility changes. |
| Reproducible CPU checks | `requirements-dev.txt`, `tools/check.py`, `.github/workflows/tests.yml`, `.gitignore`, `README.md`. |

Commands were run using an isolated workspace virtual environment with Python
3.12.14. Initial restricted socket failures were environmental: fake HTTP servers
could not bind. Rerunning with supported local socket access established the
baseline below. These initial failures are not counted as repository defects.

| Check | Received package | Revised package |
|---|---|---|
| `cd forge && python -m pytest -q tests` | 493 passed, 7 skipped | **530 passed, 7 skipped** |
| `cd forge && python -m unittest discover -s tests_unittest` | 68 passed | **68 passed** |
| Service pytest suites under `coder-models`, `lab-dashboard` | 19 passed | **43 passed** |
| Focused error-oriented Ruff gate | No undefined-name diagnostic in initial scan | **Passed** (`E9,F63,F7,F82`) |
| Full default Ruff scan | 95 diagnostics | 95 diagnostics; not a clean-lint claim |
| Python source compilation | Inspected as part of orientation | **61 files passed** |
| `bash -n` on shipped shell scripts | Not executed as installers | **29 passed** |

`python tools/check.py --lint` completed with **exit 0**. There were no expected
failures or zero-test suites substituted for these checks. The seven skips are
optional compiler/GUI/disk-tool/live-Kiwix/browser checks lacking prerequisites;
they do not establish those capabilities. The new CI workflow has not been
published or remotely executed.

Evidence files:

- [Final test/lint/shell log](review-evidence/final-checks.log)
- [Original pytest baseline](review-evidence/baseline-pytest.log)
- [Original service baseline](review-evidence/baseline-services.log)
- [Pre-fix regression failures](review-evidence/regressions-before-fixes.log):
  22 failing parameter cases, 11 passing, 1 unselected new CLI feature. These
  are boundary reproductions, not the original full suite result.
- [Cross-origin/checksum before-and-after](review-evidence/boundary-before-after.json)

During implementation, an initially overstrict tokenizer check rejected empty
token lists used by the existing fake server. It was removed; schema validation
still rejects absent/wrongly typed tokens. A regression preserves the valid
empty-list case, and the complete suites passed after that correction.

## 8. Open questions and follow-up passes

1. Is the shape API deliberately restricted to a fully trusted LAN? It still has
   no application credential and can unload GPU services. Adding token/TLS policy
   should be an explicit coordinated client/server deployment change.
2. Were the exposed camera values live or reused? Their validity was not tested;
   the owner must rotate live values. No camera/server configuration was changed.
3. Which toolchains and server builds are the production compatibility targets?
   The supplied config is machine-specific. Real integration validation needs
   those hosts/images, credentials and user-service sessions.
4. Should corrupt/unversioned old run state be migrated or rejected? This patch
   adds validation and documents manual repair; it does not invent a state format
   migration policy or rewrite old runs silently.

For further review, split the remaining work into (a) hardware/GPU cancellation
and scheduling with real servers; (b) game/language harness correctness against
actual toolchain images; and (c) privileged installers, upstream artifact trust,
dependency inventory and deployment network/authentication policy. No claim of
full production or hardware certification follows from the passing CPU suites.
