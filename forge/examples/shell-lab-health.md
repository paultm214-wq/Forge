# shell-lab-health

Toolchain: shell
License: MIT
Prior art: none

<!--
A tool the lab can actually use: one Bash script that checks the lab's
HTTP services (llama-server instances, Forgejo, the dashboard) from a
config file and prints a status table, installed as a systemd timer by an
idempotent install script. It tries the shell toolchain: ShellCheck at
info level, bats tests of the library functions with curl replaced by a
stub, the installer run against stubbed systemctl, and systemd-analyze on
the unit files. No machine is contacted by the tests - every network call
goes through `curl`, which the tests stub.
-->

## Goal

`bin/lab-health` reads a list of services and checks each one over HTTP,
printing one line per service and a summary, and exiting non-zero when
any service is down. `install.sh` installs it with a systemd service and
timer that run it every 5 minutes and log to the journal.

## Constraints

- Bash 5, `set -euo pipefail` in the scripts; ShellCheck clean at the
  default (info) level with no disable comments except SC1091 on the
  `source` line if needed.
- Logic in `lib/lab-health.sh` (functions only, nothing runs when it is
  sourced); `bin/lab-health` sources it from `../lib` relative to the
  script's real path.
- Config file: `${LAB_HEALTH_CONF:-/etc/lab-health.conf}`. One service
  per line, `name url [expect]`, whitespace separated; `expect` is the
  HTTP status that means up (default 200). Blank lines and lines starting
  with `#` are ignored. A line with fewer than 2 fields is an error that
  names the line number.
- HTTP only through `curl -sS -o /dev/null -w '%{http_code}' --max-time
  "$LAB_HEALTH_TIMEOUT" "$url"` (LAB_HEALTH_TIMEOUT default 5). curl's
  exit status non-zero = down with code 000.
- `install.sh` writes only under `${DESTDIR:-}`: the script to
  `$DESTDIR/usr/local/bin/lab-health` (0755), the library to
  `$DESTDIR/usr/local/lib/lab-health/lab-health.sh` (0644), units to
  `$DESTDIR/etc/systemd/system/`, and a sample config to
  `$DESTDIR/etc/lab-health.conf` ONLY if none exists (never overwritten).
  Then `systemctl daemon-reload` and `systemctl enable --now
  lab-health.timer`. Running it twice gives the same files and the same
  systemctl calls.
- No real addresses in the sample config: example lines are commented
  out, e.g. `# coder http://<host>:8081/health`.

## Interfaces

`lib/lab-health.sh`:
- `lh_parse_conf FILE`: prints `name<TAB>url<TAB>expect` per service;
  exit 2 with `lab-health: FILE:N: expected "name url [expect]"` on
  stderr for a bad line.
- `lh_check URL EXPECT`: prints the HTTP code; exit 0 if it equals
  EXPECT, else 1.
- `lh_line NAME CODE OK`: prints `%-12s %-4s %s` of name, `up`/`DOWN`,
  code.
- `lh_main [FILE]`: checks every service in order, prints a line each,
  then `N up, M down`; exit 0 if all up, 1 if any down, 2 on a config
  error.

`bin/lab-health [--conf FILE]`: calls lh_main; `--help` prints usage and
exits 0; an unknown option prints usage to stderr and exits 2.

Units: `lab-health.service` (Type=oneshot,
ExecStart=/usr/local/bin/lab-health, a non-root `DynamicUser=yes`) and
`lab-health.timer` (OnBootSec=2min, OnUnitActiveSec=5min,
WantedBy=timers.target).

## Acceptance

1. `game.shellcheck().ok` and `game.shfmt_diff().ok` (4-space indents).
2. `game.systemd_verify() == []`.
3. bats (`tests/*.bats`, using bats-assert), with a stub `curl` put first
   on PATH by the test's setup that answers from a table in the test:
   - lh_parse_conf on a file with a comment, a blank line, `a http://x/h`
     and `b http://y/ 204` prints exactly two lines with 200 and 204;
   - a line `oops` on line 3 exits 2 with "conf:3:" in stderr;
   - lh_check for a URL the stub answers 200 prints 200 and succeeds;
     503 prints 503 and fails; a stub exit 7 prints 000 and fails;
   - lh_main with two up and one down prints three lines, then
     "2 up, 1 down", and exits 1;
   - `bin/lab-health --help` exits 0; `bin/lab-health --bogus` exits 2.
4. `game.bats().assert_ok()` with at least 8 tests.
5. The installer through `game.sh_run(["bash", "install.sh"],
   env={"DESTDIR": d}, stubs={"systemctl": ""})`: exit 0; the five files
   exist with modes 0755/0644; the calls to systemctl are exactly
   `["daemon-reload"]`, `["enable", "--now", "lab-health.timer"]`.
   A config written into DESTDIR before a second run is unchanged after
   it, and the second run makes the same systemctl calls.
6. `game.sh_run(["bash", "bin/lab-health", "--conf", conf],
   stubs={"curl": "echo 200"})` on a 2-service config: exit 0 and the
   output ends with "2 up, 0 down".
