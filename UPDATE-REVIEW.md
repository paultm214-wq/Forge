# Review of the updated Forge package

## Scope and conclusion

Reviewed the supplied `forge-paultm214-reviewed-2026-11-06.tar.gz` against the
previously reviewed source archive. Input SHA-256:
`f5b18db4e59a42847e60c5b2e62ec91c1baddc28d3556b0eed9f0b998f28a1bd`.

The supplied update changes **15 files**. Its principal additions are review
verdict normalization, review cost checks, pending-work backoff/deduplication,
the output-size watchdog, retained stale chunk code, offline run status, stricter
identifier matching, dashboard Host/PIN controls and regression tests. I read
the changed production code and its calling paths, rather than treating the
included review documents or prior test logs as independent proof.

**Conclusion:** merge with the three focused fixes below. All three were
reproduced against the supplied update and are covered by new regressions.
Existing behavior tests continue passing. No new toolchain or strategic feature
was implemented in this merge; those are proposed in
[FEATURES_AND_TOOLCHAINS.md](docs/FEATURES_AND_TOOLCHAINS.md).

## Confirmed findings and fixes

| Severity | Location | Failure mechanism and evidence | Fix |
|---|---|---|---|
| High | `forge/forge.py`: `_execute` deferred-review shortcut | Deduplication compared only `deferred_hash == module_hash(module)`. With Claude still unavailable, changing an asset or generated output returned the previous branch as committed without committing the changed deliverable. Both asset and output end-to-end regressions reproduced this using real temporary Git repositories and a fake model server. | Require a matching `deferred_content_hash` over code and deliverables before skipping the commit. Stream file hashing to bound memory. Retain the existing module hash for compatibility. |
| Medium | `forge/forge.py`: `_run_watched` | A writer that finished inside the first polling interval returned from `communicate()` directly. A 2 MiB output with a 1 MiB watchdog returned success and left the oversized file in place. A nonzero fast exit also missed the size-limit diagnostic and cleanup. | Check directory size after process completion as well as on timeout/poll ticks; reuse the existing overflow cleanup. Tests cover both zero and nonzero process exits. |
| Medium | `forge/forge.py`: `run_status` | Valid JSON with a non-object top level (`[]`, `null`, a number or string) reached `.get`, raising AttributeError. `forge status --json` then produced a traceback instead of its documented error object. | Reject non-object state/plan documents with ForgeError. Eight parameterized cases verify JSON error output, file identification and exit 1. |

All confidence labels are **Confirmed**. The red regression run had **12 failing
cases**, matching these three findings. This does not mean the supplied package's
existing tests failed: all existing suites passed independently.

### Behavior and migration notes

- `deferred_content_hash` is additive. A legacy deferred run without it can make
  one extra commit to establish a trustworthy content baseline. No manual JSON
  migration or rename is required. The existing test still proves that unchanged
  retries back off and avoid duplicate branches.
- The fingerprint includes module code, spec/plan/change request, assets,
  outputs, vendored files, design and docs. Logs, state and generated reports
  are excluded because they change on every attempt. Symbolic links in those
  deliverable trees are rejected rather than followed by the hash reader.
- A process that exceeds the output watchdog now fails even if it exits before
  the next tick. This closes a validation gap; it does **not** turn the polling
  watchdog into an enforceable filesystem quota. Burst writes can occur between
  polls, and the existing output-copy cap remains a separate check.
- The status fix concerns top-level JSON object validation. It does not claim
  exhaustive schema validation/migration of every historical nested state field.
- The supplied cost setting is a guard against starting another review after
  the reported total reaches the threshold. One final call can exceed it; it
  is not a prepaid hard spending ceiling. The existing test intentionally
  demonstrates a 1.0 threshold with 1.2 reported spend.
- No existing CLI flag or configuration key was removed. No lab credentials,
  live GPU services or environment publication settings were changed.

## Validation performed here

Python 3.12.14, with the package's pinned development requirements in an isolated
workspace environment. Tests using fake servers ran with loopback socket access.

| Check | Supplied update | After the fixes |
|---|---:|---:|
| Main pytest suite | 558 passed, 7 skipped | **570 passed, 7 skipped** |
| Separate unittest suite | 68 passed | **68 passed** |
| Model-manager/dashboard pytest suites | 66 passed | **66 passed** |
| Total executed passing tests | **692** | **704** |
| Focused new + existing hardening regressions | 12 new cases failed before fixes | **40 passed** |
| `python tools/check.py --lint` | Exit 0 | **Exit 0** |
| Python source compilation | — | **65 files passed** |
| Shell syntax | 29 passed via runner | **29 passed via runner** |
| Focused Ruff error gate | Passed | **Passed** |
| Full default Ruff scan | Historical diagnostics remain | **95 diagnostics remain; not a clean-lint claim** |

The seven skips are optional checks requiring additional compiler/GUI/disk tools,
live Kiwix or Playwright/browser prerequisites. No skipped test was represented
as a pass. A pre-existing fake-server socket ResourceWarning in unittest remains.
No type-checker configuration was added or static type-check pass claimed.

Logs from this review:

- [Supplied-update full validation](review-evidence/update-review/baseline-checks.log)
- [New regressions before fixes](review-evidence/update-review/reproductions.log)
- [Focused regressions after fixes](review-evidence/update-review/focused-checks.log)
- [Final full validation](review-evidence/update-review/final-checks.log)

No real GPU inference, remote user-service session, real toolchain Docker image,
Claude service or lab network was available for integration validation. No claim
is made that the included historical reports' hardware observations were rerun.
The newly added GitHub workflow will run on push; these logs are local validation,
not a claim about its remote result.

## Existing toolchains and recommended expansion

The registry contains **23 project toolchains**. The image installer also has an
auxiliary `ux` browser image. Fifteen registry targets have dedicated
`SELFTEST_FILES` examples; eight do not use that uniform named path. Dedicated
`PACKAGED` handling currently names four targets. Project dependency adapters
cover npm, Go modules and HA wheels, with Rust vendoring handled separately.

Before adding many targets, complete the uniform real-image smoke-test matrix,
add runtime/build provenance and extend packaging/dependency support where the
existing targets need it. The tests here mostly exercise fakes and protocol
contracts; source declarations do not establish installed-image readiness.

The detailed inventory, ranked suite features, six new target proposals,
implementation/testing paths, effort estimates and trade-offs are in
[FEATURES_AND_TOOLCHAINS.md](docs/FEATURES_AND_TOOLCHAINS.md). Recommended suite
priorities are provenance/replay, toolchain health and shared-GPU recovery.
Recommended new targets are `python-app`, WASI/WebAssembly, Kotlin/JVM, HDL,
OpenSCAD/CAD and offline infrastructure validation. Frontend web applications
should first extend existing `node` and `ux` support.

## GitHub integration

The repository previously held a reviewed download archive and checksum rather
than the extracted source. This merge imports the supplied source at the
repository root, preserves that history, then layers these focused fixes, tests,
reports and the refreshed download. The source import and follow-up changes are
separate commits so reviewers can inspect the actual corrections independently.
No force push or history replacement is required.
