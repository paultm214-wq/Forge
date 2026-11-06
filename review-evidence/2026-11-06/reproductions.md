# Reproductions against the received package (6 November review)

Each was run against the received code before any change. The regression
tests that encode them are listed; each fails on the received code and
passes on the revised code (see "Baseline proof" below).

## R1 - `forge pending` commits identical code on every tick (finding 2)

Fake Claude answering `API Error: 529 overloaded_error`, internet probes
reachable, three `forge pending run` ticks after the first deferred commit:

```
after first run: ['forge/boxer-20261106-025644-07d5d9']
tick 0: rc=0 branches=2 deferred=['claude_review'] claude_calls=4
tick 1: rc=0 branches=3 deferred=['claude_review'] claude_calls=6
tick 2: rc=0 branches=4 deferred=['claude_review'] claude_calls=8
```

Test: `forge/tests/test_review_hardening.py::test_pending_does_not_recommit_while_claude_stays_down`

## R2 - Claude review loop without a bound (finding 1)

`claude_review` stubbed to return `approved: false` with one issue of
severity `"critical"`; `max_review_rounds = 2`; a guard raised on the 7th call:

```
RESULT: RuntimeError still reviewing after 6 Claude calls calls: 7
```

Tests: `test_claude_rejection_without_a_blocking_issue_is_bounded[issues0|issues1]`

## R3 - A string issue crashes the run (finding 4)

`self_review` stubbed to return `{"approved": false, "issues": ["c2: box() is wrong"]}`:

```
RESULT: AttributeError 'str' object has no attribute 'get' is ForgeError: False
```

Test: `test_self_review_string_issues_do_not_crash`

## R4 - /out fills the host disk (finding 3)

Not a planned reproduction: running the new watchdog tests against the
received code (no watchdog) let two generated write loops run to their
60-second sandbox timeouts. They wrote about 30 GB under
`/tmp/pytest-of-root` and exhausted the session's disk allowance; later
tests in that run errored with "No space left on device". The space was
reclaimed by deleting those pytest temporary directories. The tests are
deselected from the baseline proof for that reason.

Tests: `test_runaway_output_is_stopped_and_cleared`, `test_docker_container_is_killed_on_overflow`

## R5 - Dashboard: DNS rebinding, PIN brute force, non-ASCII PIN (findings 5, 6, 9)

Real `make_server` handler, `pin = "4821"`, Host/Origin of an attacker domain:

```
rebinding (wrong pin): 401          <- passed the Origin/Host check, reached the PIN
brute force via rebinding host: PIN 4821 after 4822 tries in 2.8s; actions=1
non-ascii pin: RemoteDisconnected   <- TypeError: comparing strings with non-ASCII characters is not supported
```

Tests: `lab-dashboard/test_control_hardening.py`

## R6 - Alias with a trailing newline rewrites another model's preset (finding 7)

```
ALIAS_RE accepts 'evil\n': True
valid_job: None
[good]
model = /m/good.gguf
c = 32768

[evil
]
model = /m/evil.gguf
c = 4096

re-read: {'good': {'model': '/m/evil.gguf', 'c': '4096'}}
```

On the received code, `forge.validate_entry({"alias": "coder\n", ...})` went
on to query Hugging Face, i.e. the alias passed validation.

Tests: `coder-models/test_identifiers.py`, `test_catalog_alias_with_a_newline_is_refused`

## Baseline proof

New tests copied into a worktree of the received code:

- `forge/tests/test_review_hardening.py` (watchdog tests deselected, see R4):
  **24 failed, 2 passed**. The 2 passing are behaviour-preservation tests
  (`test_output_under_the_watch_is_kept`, `test_subprocess_is_still_used_without_out_dir`).
- `lab-dashboard/test_control_hardening.py` + `coder-models/test_identifiers.py`:
  **11 failed, 12 passed**. The passing ones check behaviour the received
  code already had (LAN-style hosts, the normal PIN flow, valid jobs) or are
  trivially true there (no lockout exists to reset; every Host was accepted).

On the revised code all 51 pass, five consecutive runs.
