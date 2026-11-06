# go-gpu-lease

Toolchain: go
License: MIT
Prior art: none

<!--
A small Go service for the lab: GPU leases, so forge, the image servers
and the shape servers can agree who is using which machine's card (today
forge's gpu_lock does this from Gemini alone). It tries the go toolchain:
a library with an injectable clock, an HTTP API tested with
net/http/httptest, the race detector, a persisted state file, and a
command built into outputs/build by forge's packaging.
-->

## Goal

`leased` serves leases over HTTP. A holder asks for a machine's GPU for a
time; it gets it if nobody else holds it, can renew it, and releases it.
Expired leases are free again. State survives a restart through a JSON
file.

## Constraints

- Module `example.com/gpulease`, `go 1.27`; standard library only.
- Package `lease`: the rules, with a `Clock` interface (`Now()
  time.Time`) so tests control time; safe for concurrent use (a mutex).
- Package `api`: `http.Handler` built on `net/http`'s ServeMux patterns
  (`"POST /leases/{machine}"`...). JSON in and out, `Content-Type:
  application/json`.
- Command `cmd/leased`: flags `-addr` (default `127.0.0.1:7811`) and
  `-state` (default `leases.json`); writes the state file after every
  change (write to a temp file in the same folder, then rename).
- gofmt-clean; `go vet` clean; exported names documented.

## Interfaces

`lease`:
- `type Lease struct { Machine, Holder string; Expires time.Time }`
  (JSON keys machine, holder, expires).
- `New(clock Clock, maxTTL time.Duration) *Table` (maxTTL 0 = 1 hour).
- `(*Table) Acquire(machine, holder string, ttl time.Duration) (Lease,
  error)`: ttl must be > 0 and <= maxTTL (`ErrBadTTL`); machine and holder
  non-empty, at most 64 characters, `[a-zA-Z0-9._-]` (`ErrBadName`). Free
  or expired -> granted. Held by the same holder -> renewed (new expiry).
  Held by someone else -> `ErrHeld` (wrapped with the holder's name:
  `errors.Is(err, ErrHeld)`, message contains it).
- `Release(machine, holder string) error`: `ErrNotHolder` when someone
  else (or nobody) holds it.
- `List() []Lease`: live leases sorted by machine.
- `Snapshot() ([]byte, error)` / `Restore([]byte) error`: JSON of the live
  leases; Restore drops expired ones.

`api.New(t *lease.Table) http.Handler`:
- `POST /leases/{machine}` body `{"holder": "...", "ttl_seconds": n}` ->
  200 + the lease; 409 + `{"error": "...", "holder": "<current>"}` when
  held; 400 + `{"error": ...}` for bad JSON, names or TTL.
- `DELETE /leases/{machine}?holder=x` -> 204; 409 when not the holder.
- `GET /leases` -> 200 + `[...]` (an empty array, never null).
- An `OnChange func()` field on the handler's options is called after
  every successful POST or DELETE (the command saves the state there).

## Acceptance

1. `game.go_build().ok` and `game.gofmt_files() == []`.
2. `lease` tests with a fake clock:
   - acquire a free machine; a second holder gets ErrHeld naming the
     first; the first renews and the expiry moves;
   - after the clock passes the expiry, the second holder gets it;
   - ttl 0, ttl > maxTTL and a name with a space are rejected with the
     right errors;
   - Release by a non-holder -> ErrNotHolder; by the holder -> free;
   - Snapshot -> Restore into a new table at a later time keeps only the
     live leases;
   - 50 goroutines acquiring the same machine: exactly one succeeds.
3. `api` tests with `httptest.NewRecorder` / `httptest.NewServer`: every
   status code above, the JSON bodies, `GET /leases` returning `[]` when
   empty, and OnChange called once per successful change and not on
   failures.
4. `game.go_test(race=True).assert_ok()` with at least 12 checks.
5. `game.go("run", "./cmd/leased", "-h")` exits non-zero (flag's usage)
   and its output names `-addr` and `-state`.
6. forge's packaging builds `outputs/build/leased` (static linux/amd64).
