# Reproducible runs, operator controls, and additional targets

The existing Python pipeline and 23 toolchains remain available. This revision
adds nine targets and four named profiles. Generated code still executes in an
offline, read-only Docker sandbox as uid 1000, with memory/CPU/PID/output limits.
Image construction and dependency downloads are separate trusted preparation steps.

## Build and exercise the targets

Use Python 3.12+, Docker, and the repository's existing installation instructions.
Run the builder from a checkout or the installed Forge directory:

```sh
bash forge/install-forge-extras.sh python-app wasm kotlin-jvm hdl cad desktop
bash forge/install-forge-toolchains.sh node:frontend python-app:database-service
bash forge/install-forge-toolchains.sh node:database-service go:database-service
bash forge/install-forge-extras.sh infra ros2
# Set this only after accepting https://developer.android.com/studio/terms:
ANDROID_SDK_LICENSE_ACCEPTED=yes bash forge/install-forge-extras.sh android
forge selftest python-app
forge selftest android
forge selftest node --profile frontend
forge selftest go --profile database-service
```

The Android license was explicitly accepted for this managed environment. Other
machines and CI runs require their own explicit acceptance. Image builders keep
TLS, artifact checksums, Maven/Go checks, and apt signatures enabled. The managed
proxy CA is mounted only during networked build steps; Java uses a temporary
trust store that is removed in the same layer. No session certificate is baked in.
Debian security libraries and Chromium follow the selected Debian security
repositories; compiler/framework versions are pinned in the recipes. Record and
retain the resulting immutable image IDs to reproduce a particular installation.

| Target | Tools and scope | Artifact |
|---|---|---|
| `python-app` | Python 3.12, pytest, mypy, setuptools; real ASGI/WSGI HTTP; FastAPI/Flask/Django available | wheel and sdist |
| `wasm` | Rust 1.90 / Clang 19, WASI Preview 1, Wasmtime 38; explicit preopens, fuel and memory bounds | `.wasm` |
| `kotlin-jvm` | JDK 21.0.13, Gradle 8.14.3, Kotlin 2.2.21, JUnit 5.11.4; offline build/cache | runnable application distribution |
| `hdl` | Icarus 12, Verilator 5.032, Yosys 0.52; cycles/reset/overflow and synthesis | HDL source and VCD |
| `cad` | OpenSCAD 2021.01, trimesh 4.9; dimensions, volume, watertightness and render | STL and PNG |
| `infra` | Helm 3.18.6, Kustomize 5.6, kubeconform 0.6.7; Kubernetes 1.32 strict offline schemas and policy | manifests and change report |
| `android` | SDK command-line tools 19, API/build-tools 35, AGP 8.9.2; JVM tests and debug APK | APK |
| `ros2` | Jazzy on Ubuntu 24.04, rclpy, colcon; local DDS replay and headless launch | installed ROS package tree |
| `desktop` | PySide6 6.9.3, PyInstaller 6.16; real Qt keyboard tests, captures, packaged launch | native app directory |

Each target has coder/test guidance, an executable positive and negative smoke
project, and an example spec in `examples/`. Helpers return `Result` (`ok`, `out`,
`diagnostics`) or `Checks` parsed from actual test reports. Empty test reports do
not pass. WASI is Preview 1, rather than browser WebAssembly or the component
model. HDL stops at simulation/synthesis. Android does not certify an emulator
or device. ROS does not deploy to robots or run Gazebo. Infra never applies to a
cluster; unknown Kubernetes kinds fail closed until a reviewed schema is added.

## Named profiles on existing targets

```markdown
Toolchain: node
Profile: frontend
```

`node:frontend` selects the existing `ux` image name, extended with pinned
Vite/React/Vue and their plugins. `game.frontend_build()` builds offline.
`game.browser_check(actions=[dict(role="button", name="Increment", key="Space", text="42")])`
uses a real Chromium page, keyboard interaction, axe, and desktop/mobile captures.
`game.frontend_package(out)` exports static assets. The smoke checks React and
Vue single-file components and rejects malformed JSX. npm download stages retain
`--ignore-scripts`; native addons require a separately reviewed image.

`Profile: database-service` is available for `python-app`, `node`, and `go`.
PostgreSQL 17 runs inside the same sandbox using Unix sockets. Existing
`game.pg()`, `game.pg_apply()`, `Postgres.psql/query`, plus `game.database_env()`
and `game.service_request()` provide a controlled service recipe. The smoke
tests migration ordering, a constraint error rolling back an open transaction,
HTTP success/404, and persistence after a PostgreSQL restart, using a service
in each selected language. The Go fixture uses the PostgreSQL CLI; driver-based
applications can use the existing locked Go module preparation.

Set image/limit overrides in `[sandbox.toolchains.<target>]`. Profile image and
limit overrides use a quoted table such as
`[sandbox.profiles."node:frontend"]`. Set `image`, `memory`, `timeout`, `tmpfs_mb`,
`pids`, or `cpus` there. No existing config key or toolchain name is removed.

## Offline dependencies

Python targets accept `requirements.lock` with exact `==` pins and SHA256 hashes,
or `uv.lock` exported with `uv export --offline --frozen`. Only wheels are
downloaded and installed; project build backends do not execute in the networked
download stage. Missing wheels or invalid hashes fail verification. The trusted
image's framework/tool packages are also available in the project venv.

Additional JVM jars use a data-only `forge-dependencies.lock.json`:

```json
{"artifacts":[{"coordinate":"org.example:library:1.2.3","sha256":"<64 lowercase hex digits>"}]}
```

Replace the example with a real Maven Central coordinate and verified checksum.
Snapshots, custom repository URLs, and uploaded Gradle scripts are not accepted
by this downloader. Verified jars are materialized under `/deps/jars`; Gradle
can reference `implementation(fileTree("/deps/jars") { include("*.jar") })`.
The cache is verified again before offline reuse. Existing Node/Go preparation
and Cargo vendoring remain available; WASI Rust reuses Cargo preparation.
Preparation failures invalidate the dependency completion stamp.

## Provenance and replay

Every committed build exports `BUILD_PROVENANCE.json` with schema version 1:
toolchain/profile, inspected image ID/digests, available model revisions,
dependency-lock hashes, attempt path/hash references, seeds, artifact hashes,
and review dispositions. Unknown revisions remain unknown. Config dictionaries,
key contents, private inputs, prompt/log contents, and reviewer free text are
excluded. This is an audit record, not a signed attestation.

```sh
forge replay RUN                         # inspect without executing code
forge replay RUN --verify                # saved checks/build, pinned image ID
forge replay RUN --verify --image-tolerance 0.01 --mesh-tolerance 0.001
```

Verification copies saved code/dependencies/assets into a temporary run, makes
no model calls, launches no dependency downloads, reruns saved checks and build
outputs, and compares artifacts. It never commits or changes the original run.
Missing saved dependencies fail offline. Private input mounts must still be
available at their configured paths. Symlink input trees and input/code drift
are rejected. An uninspected image cannot be verified in Docker.

Hashes are exact by default. Explicit PNG tolerance uses mean RGBA error on a
32-by-32 thumbnail at matching dimensions. GLB tolerance compares corresponding
float VEC3 positions with exact structure, topology, transforms and other bytes;
the tolerance is in model-coordinate units. Large/unsupported images and meshes
remain hash-only (PNG limit 16 million pixels; GLB limit 64 MiB/100,000 positions).
These metrics do not promise universal visual equivalence or GPU determinism.
Archive timestamps and other nondeterministic outputs can still produce drift.

## Health and operator controls

```sh
forge check --toolchains --json
forge check --toolchains python-app node:frontend android --smoke --json
forge status RUN --json
forge control RUN pause
forge control RUN cancel
forge resume RUN
forge recover
forge recover --apply
```

The capability matrix distinguishes buildable, installed, unrun, failed and
unavailable. It records image/tool versions, host/architecture, validation time,
cache presence, and independent smoke age. Cache presence does not prove all
dependencies for an arbitrary spec are ready. Observations become stale after
24 hours or a host change. The dashboard shows health even before a run exists.

GPU claims use a file-locked FIFO, including waiting threads in the same
process. Boot/process-start identity and native thread liveness prevent PID
reuse or dead workers from retaining claims. Live owners are not preempted.
Claims coordinate processes on one Forge host sharing the same `FORGE_HOME`.
The local `/proc` ownership checks do not coordinate independent controller
hosts; use one Forge controller for a shared GPU.

Pause/cancel stop new chunk dispatch, drain current chunks, keep verified work,
write a checkpoint and `CHECKPOINT_PROVENANCE.json`, and stop before commit.
Resume clears the operator request. Dashboard controls use the existing
authenticated confirmation slider. They are cooperative, rather than immediate
termination of an in-flight model call or render.

Simple configured `systemctl --user stop/start UNIT...` GPU handoffs record only
previously active units before stopping them. Recovery requires a dead owner,
matching current host/server/unit configuration, a GPU claim, and confirmed
shape-server GPU release. Failed restoration retains its journal. Arbitrary
legacy shell handoff commands continue to work but cannot be inferred into a
safe automatic service-recovery recipe.

Compatibility: restoration is now dispatched after the shape server's release
response, including legacy shell handoffs. Failed managed recovery returns a
nonzero CLI status and stops the run before model work. GPU resource aliases
must use letters, numbers, underscores, hyphens or dots (maximum 128 characters,
no `..`); rename unusual existing aliases before using the FIFO.

Deploy the complete Forge directory or run the updated installer: the new
sibling runtime modules are required. Copying only `forge.py` or `forge_game.py`
is no longer a complete installation. Existing config keys and CLI commands
remain supported; the new commands, profiles and artifact formats are additive.

## Native desktop workers

```sh
python forge/tools/desktop_worker.py build PROJECT --platform windows --output native-dist
python forge/tools/desktop_worker.py sign ARTIFACT --expected-sha256 HASH --identity CERTIFICATE_STORE_SELECTOR --output signed-dist
```

Build on a disposable worker matching Linux, Windows, or macOS. The worker runs
actual GUI tests, packages with PyInstaller, launches the packaged selftest and
records artifact SHA256. The separate signing action requires an approved hash,
uses the worker's existing certificate store, verifies the signature, and never
runs application code. It does not notarize macOS builds. Build workers receive
no signing keys; environment filtering is not an OS sandbox.

`desktop-native.yml` exercises the reviewed static fixture on three native
GitHub runners. `toolchains.yml` provides real-image smoke jobs, with a separate
explicit Android-license input. Native CI/signing and the environment's blocked
ROS/infra builds must be reported separately from local passes. See the release
validation report for evidence and remaining limits.
