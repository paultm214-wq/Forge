# Adding a toolchain to forge

A toolchain is everything forge needs to build and test one kind of
project offline: a sandbox image, helpers the tests call, notes for the
coders, and a self-test. This is the checklist the November toolchains
(minecraft, shell, node, go, homeassistant, c-cpp, sql, mcu, devices,
raspberrypi) followed. Copy the nearest
existing one; `shell` is the smallest and `minecraft` the fullest.

## 1. The image (`install-forge-toolchains.sh`)

- Add the name to `ALL=(...)` and a `name)` case that calls
  `build name 'Dockerfile text' [context files...]`.
- Start `FROM forge-sandbox-base:latest` (Python 3.12, uid 1000, /work)
  unless the tools need another Python (homeassistant starts from
  python:3.14-slim-trixie and adds the `runner` user itself).
- Tools come in at BUILD time, with the network. At test time the
  container has no network, a read-only root, uid 1000 and a tmpfs /tmp.
- Prefer Debian packages; for a language runtime, `COPY --from=` the
  official image (node, golang) rather than piping an installer to sh.
- Pin versions with an environment default at the top (`GO_VERSION=1.27`),
  and write what was installed to a versions.json in the image so a
  helper (`game.node_versions()`) and the self-test can report it.
- Anything with its own licence the user must accept (Mojang's EULA) is
  gated on an explicit variable the user sets (`MINECRAFT_EULA=accept`).
  forge never accepts it for them.
- Add a smoke command to the `case "$t"` list near the end and a line to
  the header comment (disk use, what it needs).
- Behind a proxy that listens only on localhost:
  `DOCKER_BUILD_OPTS="--network host"`.
- Package mirrors on Ziggurat (ziggurat/README.md) reach every build as
  build arguments (`http_proxy` for apt, `PIP_INDEX_URL`,
  `npm_config_registry`, `GOPROXY`), declared after each `FROM` by build()
  - nothing to add to a new Dockerfile.
- Files the image needs from forge (the MicroPython fakes) are build
  context: pass them after the Dockerfile text and `COPY` them.
- Hardware the sandbox cannot have is replaced by a fake the real tools
  talk to (a fake adb server, a folder gphoto2 sees as a camera, a pty pair
  for serial, QEMU/simavr for firmware, gpiozero's MockFactory, umockdev
  recordings).

## 2. Dependencies the project declares

Tests never reach the network. A project's own packages are fetched
BEFORE the sandbox starts, by a container WITH network that only
downloads, into the run's `deps/` folder, mounted read-only at `/deps`:

| toolchain | what | how (no package code runs) |
|---|---|---|
| rust | crates | `cargo vendor` (`ensure_cargo_deps`) |
| node | npm packages | `npm ci` / `npm install --ignore-scripts` |
| go | modules | `go mod tidy && go mod download` (+ go.mod/go.sum) |
| homeassistant | manifest requirements | `pip download --only-binary :all:` |

Downloads go through the Ziggurat mirror for that kind when `[mirrors]`
names one that answers (`mirror_env()`). For a new kind, add a branch to
`_deps_plan()` in forge.py (files the
container sees, a cache key, the script, its offline variant, a cache
folder under `~/forge/cache/`) and the name to `DEPS_TOOLCHAINS`. The
download cache makes runs work when the internet is down.
`FORGE_DEPS_NETWORK=host` runs the download container on the host network.

## 3. Helpers (`forge_game.py`)

Tests are Python and call `game.<helper>()`. A helper:

- runs the tool from `project_dir()` with `run(...)` (offline env, caches
  under `TMP`);
- returns a `Result` (`.ok`, `.out`) with parsed `.errors` in the tool's
  own `file:line:col: message` form, or a `Checks` (one dict per test:
  name, ok, detail) with `.assert_ok()`;
- raises AssertionError with the tool's output tail when a build the
  test depends on fails.

Parse machine-readable output where the tool has it (`go test -json`,
vitest's JSON reporter, JUnit XML via `junit_results`, `bats --tap`,
`check_config --json`). If the project builds into something to install,
add a branch to `package()` and the name to `PACKAGED` in forge.py.

## 4. The toolchain entry (`forge.py`)

- `TOOLCHAINS[name]`: `lang`, `fence`, `memory`, `tmpfs_mb`, `timeout`,
  `pids`, `test_help` (what tests can call - every `game.x()` named there
  must exist; a test checks), `coder_help` (a `*_CODER_RULES` block: layout,
  idioms, what is installed, "no network").
- `TOOLCHAIN_LANG[name]`: a key of `LANGS` (it picks the coder model per
  language; add a language to LANGS only when none fits).
- `KIWIX_TOOLCHAIN_BOOKS[name]`: offline reference books (prefixes;
  `forge check` lists the ones the library lacks).
- Failure facts: when a common failure has a fixed cause (a package not
  in package.json, a Go import nothing provides), add it to
  `harness_facts()` so the next attempt is told.
- Library API facts (optional, for big APIs coders guess at): an index made
  by `tools/java_api.py` / `tools/dotnet_api.py`, and the module in
  `API_FACT_MODULES`.

## 5. Self-test and tests

- `SELFTEST_FILES[name]`: a small real project. `SELFTEST_STEPS[name]`:
  `step("what", fn)` calls that exercise every helper, including one
  deliberate error that must come back parsed.
  `forge selftest <name>` runs it in the real image.
- Unit tests in `tests/` run the helpers against fake programs on PATH
  (see tests/test_general_toolchains.py) - no Docker needed.
- An example spec in `examples/` that a run can take end to end.
- A CHANGES.md entry.
