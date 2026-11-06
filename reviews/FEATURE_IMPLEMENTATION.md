# Forge feature implementation and validation

This pass implements the three ranked runtime features, all nine selected new
targets (including Android, ROS 2, and native desktop), and the browser/database
profiles. Existing review findings and the original roadmap remain in the
earlier review documents. This report describes the additional implementation.

## Orientation and implementation map

`forge.py` remains the CLI and spec-to-commit orchestrator. It loads the additive
modules after its existing definitions, preserving the project's test injection
and custom module-loader conventions:

| Module | Responsibility |
|---|---|
| `forge_operations.py` | provenance/replay, health/control CLI integration, lifecycle checkpoints |
| `forge_provenance.py` | selected metadata, streaming file hashes, bounded PNG/GLB comparison |
| `forge_control.py` | process-start identities, file-locked FIFO GPU ownership, control mailbox |
| `forge_recovery.py` | recorded service handoffs and configuration-constrained restoration |
| `forge_health.py` | Docker/image/version/cache observations and independent smoke age |
| `forge_targets.py` | nine registrations, language routing, profiles, executable smoke fixtures |
| `forge_dependencies.py` | data-only dependency plans; no project build code in download stages |
| `forge_extra_helpers.py` | sandbox-side compilers, test/report parsers, HTTP, browser, CAD/HDL/ROS helpers |
| `toolchain-images/` | pinned build recipes, verified SDK/Gradle/schema downloads, trusted prewarm templates |
| `tools/desktop_worker.py` | native GUI build workers and separate hash-approved signing action |

Critical flow: spec/plan selects target/profile; trusted preparation materializes
locked dependencies; generated source executes offline in `Sandbox`; verification
and build outputs are collected; provenance is written before commit and exported
with the project. Replay consumes saved inputs and an immutable image ID without
models, download stages, or a commit. Dashboard operations use the existing auth
boundary and the offline cooperative-control CLI.

## Confirmed problems corrected before delivery

| Severity | Location | Failure mechanism and scenario | Evidence and correction |
|---|---|---|---|
| High | `forge.py: release_shape_server` | The existing implementation dispatched `restore_cmd` before posting `/v1/release`. A GPU service could restart while the shape model still occupied VRAM. | **Confirmed:** traced ordering and added a deterministic dispatch-order regression. Restoration now follows the release response; managed unit handoffs additionally require `released: true`. |
| High | `toolchain-images/wasi_runner.py` | Wasmtime raises `ExitTrap` for an explicit WASI `proc_exit(0)`. An unhandled exception made successful CLI exits, including common help paths, appear to fail. | **Confirmed:** reproduced the zero exit as a Python traceback inside the real image. The runner now preserves the WASI exit code. Real Rust smoke cases assert exits 0 and 7, alongside normal stdout, a trap and denied filesystem access. |
| High | `forge_extra_helpers.py: Helpers.gradle` | Android's Gradle plugin used Java's `/home/runner` home in the read-only sandbox, despite the writable shell HOME. APK verification failed creating `.android`. | **Confirmed:** real offline Android smoke failed with `Read-only file system`. Explicit writable Java/Android homes fixed the APK/JVM smoke; Kotlin was rechecked as well. |

Other explicit contracts covered by regression tests include stale health passes,
PID reuse/dead owners, FIFO order across waiting threads, parallel pause drainage,
cancelled render dispatch, failed-recovery exit codes, filenames matching new CLI
commands, unsigned native artifact approval hashes, empty JUnit reports,
dependency-stage script isolation, stale download stamps, topology-preserving
mesh comparisons, and rejection of unsupported external GLB geometry.

## Validation evidence

The full CPU runner is `python tools/check.py --lint`. It executes pytest,
unittest, service/dashboard tests, Python error checks and shell syntax checks in
their required separate processes. Exact final counts are recorded in
[`validation/feature-checks.txt`](validation/feature-checks.txt). The new tests are
in `forge/tests/test_feature_runtime.py` and the dashboard suite. No repository
wide static type-check configuration was present; the Python-app real smoke
executes its mypy helper against the sample package.

| Target/profile | Real proof | Result |
|---|---|---|
| `python-app` | pytest, mypy, wheel/sdist, actual WSGI HTTP; failing test and missing wheel rejected | Passed locally |
| `wasm` | C and Rust execution; explicit zero/nonzero exits, filesystem denial and trap | Passed locally |
| `kotlin-jvm` | offline Gradle build, parsed JUnit tests, real application output, compiler-error rejection | Passed locally |
| `hdl` | clock/reset/wraparound assertions, Verilator, Yosys, malformed HDL rejection | Passed locally |
| `cad` | exported STL dimensions/volume/watertightness and rendered PNG; invalid SCAD rejection | Passed locally |
| `infra` | Helm and Kustomize, strict offline schemas, policy checks, schema/policy negatives | Passed on GitHub runner |
| `android` | real APK build/inspection, JVM tests, invalid Kotlin rejection | Passed locally; no emulator claim |
| `ros2` | colcon, pytest, headless launch, real local DDS Float64 replay, failing-test rejection | Passed on GitHub runner |
| `desktop` | real Qt keyboard/accessibility test, screenshot, PyInstaller package and executable launch, negative test | Passed locally |
| `node:frontend` | actual React and Vue SFC builds, Playwright keyboard interaction, axe, responsive captures, static export, broken JSX rejection | Passed locally |
| `python-app:database-service` | migration, rollback, HTTP/404, restart persistence and bad migration | Passed locally |
| `node:database-service` | same contract through a real Node service | Passed locally |
| `go:database-service` | same contract through a real Go service | Passed locally |

External evidence is independently inspectable:

- [ROS 2 and infra real-image workflow](https://github.com/paultm214-wq/Forge/actions/runs/42258689662), successful for source `52db6bb`. Its Android job was skipped without CI license acceptance; Android was tested separately in the licensed local environment.
- [Native Linux, Windows and macOS Qt workers](https://github.com/paultm214-wq/Forge/actions/runs/42258689629), successful for source `52db6bb`. These run GUI tests, package, launch the native executable, and upload unsigned artifacts. The worker and GUI fixture remain unchanged by subsequent runtime fixes.
- [Earlier independent native run](https://github.com/paultm214-wq/Forge/actions/runs/42256665151), also successful on all three platforms.

The local ROS/infra image builds were denied access to `packages.ros.org` and
`storage.googleapis.com`. Their configuration additions are saved in the cloud
draft but inactive. No TLS or apt-signature checks were disabled. GitHub runner
validation proves the targets while the local health matrix correctly retains
`not_installed`, rather than importing an external pass as a local installation.

Additional real checks reproduced a saved build in Docker using its recorded
image ID and matched artifact hashes; downloaded a SHA256-locked Python wheel,
installed it offline and ran pytest; then rejected a bad hash and verified that
the completion stamp was removed. These results and image IDs are summarized in
[`validation/feature-validation.json`](validation/feature-validation.json).

## Compatibility, deployment and remaining limits

Follow [the runtime guide](../forge/FEATURES.md) for commands, config tables,
dependency locks and examples. Deploy the complete directory or updated installer:
the new sibling modules are required. Existing commands and config keys remain;
new target names, profiles, manifests and controls are additive. Service restore
ordering is an intentional behavior correction. Resource aliases now have a
validated filename-safe character set. No project data format was migrated.

The GPU queue coordinates Linux processes on one controller, not independent
controller hosts. Service recovery was tested against constrained fake SSH/HTTP
endpoints and dead processes, not a live fault injected into the user's GPU lab.
Arbitrary legacy shell commands cannot be inferred into safe automatic recovery.

Native signing code requires a configured certificate-store identity and an
approved artifact hash. No signing identity was supplied, so no signed or
notarized distributable is claimed. Android emulator/device instrumentation,
physical FPGA timing/deployment, Gazebo, robot hardware and cluster application
remain outside the selected initial toolchain contracts.

Replay reports drift; it cannot promise deterministic model/GPU output or archive
timestamps. Unsupported/large image and mesh formats fall back to exact hashes.
Cache presence does not prove dependencies for every future spec are available.
Existing older targets were covered by the full CPU suites, with real-image
validation in this pass focused on the new targets and profiles.

## Next additions, after this implementation

1. Native signing/notarization workers connected to the operator's certificate
   stores: turns the validated desktop builds into distributable releases;
   requires platform identities and release approval policy.
2. Android emulator instrumentation on a KVM-capable runner: tests actual UI and
   lifecycle behavior beyond the APK/JVM contract; requires additional SDK images
   and runner capacity.
3. Reviewed CAD STEP/assembly and OpenTofu plan adapters: extend the mechanical
   and infrastructure suite with new artifact types and trust boundaries, using
   the same smoke/lock/provenance framework rather than a general privileged runner.

These are follow-ups, not capabilities claimed by the current implementation.
