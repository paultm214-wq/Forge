# node-lab-status

Toolchain: node
License: MIT
Prior art: none

<!--
A TypeScript library and CLI that asks the lab's llama-server instances
how they are and renders a status page. It tries the node toolchain:
strict tsc, vitest with a fake fetch (no network), a jsdom test of the
page, tsx running the CLI, and one npm package (zod) that forge fetches
in its download-only container. llama-server's GET /health answers 200
with {"status": "ok"} when ready and 503 while the model loads - that is
the only endpoint used, and the tests fake it.
-->

## Goal

`src/status.ts` polls a list of servers concurrently with a timeout and
classifies each as ready, loading, down or error. `src/page.ts` renders
the result into an HTML table. `src/cli.ts` reads a JSON config, checks,
prints a table and exits 1 when anything is not ready.

## Constraints

- ES modules, strict TypeScript (tsconfig: strict, target es2024,
  module/moduleResolution nodenext, noEmit, types node). `.js` endings
  on relative imports.
- Dependencies: `zod` (^4) for the config schema; dev: typescript,
  vitest. Nothing else.
- No global fetch in the library: `checkAll` takes the fetch function as a
  parameter, so tests pass a fake. The CLI passes `globalThis.fetch`.
- Concurrency limit 4 (never more than 4 requests in flight); per-request
  timeout with `AbortSignal.timeout(ms)` (default 3000).

## Interfaces

`src/config.ts`: `ConfigSchema` (zod): `{ servers: { name: string (1-32
chars), url: string (a URL) }[] (1-32 items), timeoutMs?: number
(100-60000) }`. `loadConfig(text: string): Config` parses JSON and
validates; throws an Error whose message starts with "config:".

`src/status.ts`:
- `type State = "ready" | "loading" | "down" | "error"`;
- `interface Result { name: string; url: string; state: State; ms: number;
  detail: string }`;
- `checkOne(fetchFn, server, timeoutMs, now = () => performance.now())`:
  GET `<url>/health` (a single slash between; the url may end in `/`).
  200 with JSON `status === "ok"` -> ready; 503 -> loading; any other
  status -> error with detail `HTTP <code>`; a thrown error or timeout ->
  down with the error's name (`TimeoutError`, `TypeError`...) as detail.
  `ms` is the elapsed time, rounded.
- `checkAll(fetchFn, servers, timeoutMs, limit = 4): Promise<Result[]>`
  in the servers' order.
- `summary(results): string`: `"<ready>/<total> ready"` plus
  `", <n> loading"` / `", <n> down"` / `", <n> error"` for each non-zero
  count, in that order.

`src/page.ts`: `renderPage(doc: Document, results): HTMLTableElement`:
a `<table id="lab-status">` with a header row (Server, State, Time) and
one row per result whose `<tr>` has `data-state="<state>"`; the time cell
reads `<ms> ms`. It replaces an existing table with the same id.

`src/cli.ts`: `tsx src/cli.ts CONFIG.json` prints one line per server,
`<name padded to 12> <state padded to 8> <ms> ms <detail>`, then the
summary; exit 0 when all ready, 1 otherwise, 2 for a config error (its
message on stderr).

## Acceptance

1. `game.tsc().ok`.
2. vitest, all with fake fetch functions:
   - a ready, a loading (503), an error (500) and a down (throws
     TypeError) server: states in order, detail `HTTP 500` and
     `TypeError`;
   - a fake that never resolves until aborted, timeoutMs 50: down,
     `TimeoutError`;
   - 10 servers with a fake that counts in-flight requests: the maximum
     seen is 4 and the results keep the input order;
   - url `http://h:1/` requests `http://h:1/health`;
   - summary of 2 ready, 1 loading, 1 down: "2/4 ready, 1 loading, 1 down";
   - loadConfig: a valid config parses; an empty servers list and a bad
     url each throw starting with "config:".
3. A jsdom test (`// @vitest-environment jsdom`): renderPage twice into
   the same document leaves one table with 1 + n rows and the right
   `data-state` values.
4. `game.vitest().assert_ok()` with at least 9 tests.
5. `game.node_run("src/cli.ts", ["missing.json"])` exits 2; with a config
   file whose `servers` is empty it exits 2 and stderr starts with
   "config:".
