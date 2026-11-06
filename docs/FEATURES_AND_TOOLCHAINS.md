# Forge and suite: feature and toolchain recommendations

These proposals follow a review of the updated package, its toolchain registry,
installer, helpers, dependency download adapters, examples and tests. They are
recommendations, not claims that additional toolchains have been implemented.
Effort estimates cover focused engineering and tests; hardware procurement,
licence approval and provisioning are additional.

## What exists today

The source declares **23 project toolchains**, including default Python. The
installer builds 22 corresponding non-Python images plus the auxiliary `ux`
browser image. `ux` is a post-build checking service, not a 24th project toolchain.
Declared versions below describe source configuration, not locally verified
installed runtimes.

| Area | Existing toolchains | Capabilities and assessment |
|---|---|---|
| General software | `python`, `node`, `go`, `rust`, `dotnet`, `java`, `c-cpp`, `shell` | Strong breadth: Python orchestration/tests; TypeScript with tsc/Vitest; Go build/test/race checks; Cargo; .NET 8 and Java 21 targets; C17/C++20 with sanitizers/static tools; shellcheck/Bats/systemd/YAML. General Java/.NET are not yet the equivalent of unrestricted Maven/Gradle/NuGet project resolution. |
| Games and mods | `godot`, `c-sdl`, `lua-love`, `sbox`, `minecraft`, `gmod`, `fo4` | Good fit for the existing image/shape workflow. Godot scenes and GDScript; SDL/CMake; LOVE/Lua; s&box C#; Paper/MockBukkit Java; GLua; Fallout Papyrus/Mutagen. Several need specific engine installs or licensing prerequisites; fake compiler tests do not establish game compatibility. |
| Data | `sql` | PostgreSQL/pgTAP plus MariaDB, SQLite and DuckDB. Migration and query-plan helpers already make this more useful than merely generating SQL. No reason to add a duplicate generic SQL target. |
| Devices and automation | `mcu`, `esp32`, `devices`, `raspberrypi`, `homeassistant` | Embedded compilers/emulators, ESP-IDF, fake ADB/serial/USB interfaces, Pi GPIO/config tooling, HA integration and ESPHome checks. Simulation is useful; it must remain distinct from validation on real hardware. |
| Assets and analysis | `blender`, `re` | Blender/bpy asset creation and geometry checks; Ghidra/rizin/binutils analysis and emulator-equivalence helpers. Broader CAD and HDL verification would complement these rather than duplicate them. |

Evidence: [`TOOLCHAINS`, `SELFTEST_FILES`, `SELFTEST_STEPS`, `PACKAGED`,
`DEPS_TOOLCHAINS`](../forge/forge.py), [sandbox helpers](../forge/forge_game.py),
[image installer](../forge/install-forge-toolchains.sh), and
[the extension guide](../forge/ADDING_A_TOOLCHAIN.md).

### Strengthen the existing coverage before adding many targets

1. **Complete the named smoke-test matrix.** Fifteen registry entries have a
   `SELFTEST_FILES` project. The other eight are `python`, `godot`, `c-sdl`,
   `rust`, `lua-love`, `dotnet`, `java`, and `re`. They have varying other tests
   and checks; this is specifically a gap in the uniform `forge selftest NAME`
   path, not proof that their code is untested. Add a successful sample and a
   deliberate compiler/runtime failure to each, then run them on real images.
2. **Bring packaging to more app targets.** Dedicated `PACKAGED` handling names
   `fo4`, `blender`, `minecraft`, and `go`. Add standard executable/distribution
   recipes for Rust, .NET, Java and Node projects, while preserving ordinary
   source/output export for other workflows. Include runtime requirements and
   an offline startup check in each generated package.
3. **Make dependency support explicit.** Project-specific download adapters exist
   for npm, Go modules and HA wheels, with Rust handled separately by Cargo
   vendoring. Define what Java and .NET projects can use from the baked image
   and add locked dependency materialization before promising arbitrary builds.
4. **Track real compatibility.** An image tag or declared version is weaker than
   a tested compiler/engine/API tuple. Record image digest, actual versions,
   host architecture and negative smoke-test results. Keep Windows/engine-only
   checks separate from Linux headless results.

## Ranked features for Forge and the surrounding suite

| Rank | Feature and concrete benefit | Who benefits | Effort | Dependencies and trade-offs |
|---|---|---|---|---|
| 1 | **Run provenance and reproducible replay.** Emit `BUILD_PROVENANCE.json`: toolchain image digests, dependency locks, model revisions, prompts/attempt references, asset seeds, output hashes and review state. Add `forge replay RUN --verify` to compare a rerun with its manifest. | Anyone investigating a model/engine upgrade or restoring the lab. | 3–5 days for manifest/export; about a week more for replay. | Reuse existing state, pinned prior-art and digest checks. Exclude key contents/private inputs. GPU nondeterminism needs tolerance-based image/mesh comparisons, not a promise of identical pixels. |
| 2 | **A toolchain health report backed by real smoke runs.** Extend existing `check`, `status` and `selftest` with a JSON capability matrix: installed/buildable, versions, dependency cache readiness, smoke pass/fail, last validation. Surface it in the dashboard. | Operators and users selecting a toolchain for a spec. | 3–5 days plus real image test time. | Existing commands are useful foundations; this consolidates them rather than introducing another unrelated status system. Results need age/host identifiers and must distinguish unavailable from failed. |
| 3 | **Shared-GPU recovery and operator controls.** Reconcile the existing leases and service state after process failure; queue GPU claims, show why a job waits, and provide pause-after-chunk/cancel-with-checkpoint controls. | Users sharing inference, image generation and Hunyuan3D GPUs. | 1–2 weeks. | Build on `machine_lease`, GPU locks, status and shape restoration. Requires explicit ownership/cancellation rules; generic service stop/start buttons are not sufficient job recovery. |
| 4 | **Model-server capability profiles.** Probe and store chat/tokenizer/model-list/unload/cancellation support, context, request schema and health semantics for llama.cpp/Strata and other OpenAI-compatible local servers. Contract-test each profile with recordings/fake servers. | People replacing or mixing local model servers. | 3–5 days for profiles and checks, then per-server testing. | Reuse LLM transport and tokenizer fallback. Server names alone do not prove compatibility. Proxy/TLS behavior must match ordinary and cancellable requests. |
| 5 | **Deliverable-aware review approvals.** Extend the new deferred-content fingerprint to saved review approval records: a review should identify the code, images/meshes, spec and relevant build artifacts it actually inspected. Show what changed since approval. | Users relying on deferred reviews or rerendering assets. | 2–4 days. | Reuse the follow-up's `deliverable_hash`. Existing `claude_approved` is module-based; adding a versioned approval manifest avoids silently changing the meaning of old state. Hashing large assets adds I/O. |
| 6 | **A dashboard plan/diff workflow.** Show the plan, acceptance coverage, changed source/artifacts, pending reviews and cost before resuming. Add an explicit approval action for configured workflows, with keyboard and mobile support. | Users managing projects away from the terminal. | About a week. | Reuse `pause_after_plan`, status, existing slider keyboard confirmation and control policy. Requires per-run action authorization and protection of private input paths/logs. Approval should not imply deployment. |
| 7 | **Failure bundles and targeted regression promotion.** Export the minimal failed chunk, fixture, toolchain identity and redacted server facts; offer an explicit action to promote a useful reproduction into an existing suite. | Maintainers diagnosing local-model and toolchain failures. | 3–5 days. | Reuse attempt artifacts, lessons and test-strength tools. Redact credentials/private files and avoid automatically trusting model-written tests. |

The best initial sequence is **provenance → health/smoke matrix → GPU recovery**.
These make new toolchains easier to support and make local-LLM development more
predictable without increasing model complexity.

## Additional toolchains worth implementing

### Highest-value additions

| Rank | Proposed target | Why it fits / what would be new | Minimum implementation and proof | Effort / main risks |
|---|---|---|---|---|
| T1 | **`python-app`** | Default Python is already present, but a general isolated Python package/web-app workflow is missing. Its base image currently adds `aiohttp`; it is not a universal dependency environment. This target would cover packaged libraries, FastAPI/Flask/Django services and CLIs. | Locked requirements or uv lock materialization into a wheelhouse before sandbox execution; offline installation into writable temp space; pytest and type-check helpers; wheel/sdist packaging; a real request to a sample service. Include missing-wheel and failing-test samples. | 3–5 days. Start with wheels-only support, reusing HA's download pattern. Source builds need a separately designed isolated build stage. Framework templates should be profiles, not separate toolchains. |
| T2 | **`wasm` / WASI** | Portable CLI tools and plugins complement existing Rust/C/C++ and avoid coupling generated utilities to a lab host. | Pinned WASI SDK/Rust target and Wasmtime; compile/run helpers with explicit filesystem preopens and resource limits; fixtures checking stdout, traps and denied access; reproducible `.wasm` output. | 3–5 days for one WASI version. Preview 1 vs component-model/Preview 2 must be explicit. Browser/WASM testing is a later profile, not an assumed capability of WASI tests. |
| T3 | **`kotlin-jvm`** | Java already exists, but Gradle/Kotlin project support and locked JVM dependency resolution would add a different ecosystem and improve lab service development. | Pinned JDK/Kotlin/Gradle, checksum-verified distribution and dependency locks, offline cache materialization, Kotlin diagnostics, JUnit XML parsing and runnable JAR packaging. | 4–7 days. Gradle scripts execute code: a dependency stage must be isolated and not run arbitrary uploaded build scripts with host credentials/network. Do not market this as Android support. |
| T4 | **`hdl`** | FPGA/digital-logic development is a natural companion to MCU, ESP32 and electronics work. It adds simulation and synthesis checks rather than another firmware compiler. | Verilator/Icarus plus Yosys; optionally a pinned cocotb environment; simulate a UART/FIFO, assert cycles/reset/overflow, export waveforms, parse synthesis diagnostics. | 4–7 days. Tool dialect differences and timing fidelity matter. Initial scope is simulation/synthesis; FPGA place-and-route, bitstream deployment and physical timing need device-specific support. |
| T5 | **`cad` (OpenSCAD first)** | Parametric mechanical parts, enclosures and mounts complement Blender's generated visual assets and the hardware suite. | Pinned OpenSCAD plus mesh inspection; helpers for render/export, bounding boxes, volume, watertightness and dimension assertions; a device enclosure sample with a deliberately invalid model. | 3–5 days. Rendering/geometry can vary with engine versions; verify dimensions/manifoldness instead of exact STL bytes. STEP/FreeCAD and assemblies are later extensions. |
| T6 | **`infra` (Helm/Kustomize first)** | The suite already manages services, caches and lab hosts. Verified deployment manifests add value without granting generated code permission to deploy. | Helm/Kustomize rendering, pinned offline schemas, kubeconform and policy assertions; tests on ports/resources/security contexts; negative invalid-schema/policy examples. Export manifests and a change report. | 3–5 days. Keep the initial target offline with no cluster credentials or apply step. Kubernetes version/schema selection is part of the contract. OpenTofu provider/plan support is a separate extension. |

### Extend existing targets before creating redundant ones

- **Browser web apps:** extend `node` with a named frontend profile and reuse the
  `ux` image. Vite/React/Vue build, real Playwright interactions, axe, keyboard
  navigation and static packaging are valuable. They do not require claiming
  Node or browser support is absent. Allow 3–5 days for one supported stack;
  handle native npm dependencies and disabled lifecycle scripts explicitly.
- **Database-backed services:** compose existing Python/Node/Go and `sql` harness
  capabilities inside one controlled sandbox/test recipe before introducing a
  network-enabled general Docker Compose runner. A sample should prove migration,
  transaction/error paths and restart behavior, not only that a port opened.
- **Android:** a later SDK/Gradle/Kotlin target could build APKs and run JVM tests;
  emulator instrumentation is a separate hardware/runtime capability. Allow
  1–2 weeks for build support and additional work for reliable emulator runners.
  SDK licences, substantial image size and KVM availability are prerequisites.
- **ROS 2:** attractive if the device suite grows into robotics. Start with
  headless node/message/launch tests and deterministic recorded sensor input;
  Gazebo and GPU simulation are a later phase. Approximately 1–2 weeks for a
  focused distro/profile, with pinned apt/rosdep materialization.
- **Cross-platform desktop:** Tauri/Electron or Qt profiles could reuse existing
  Rust/Node/C++ and browser tooling. Signed Windows/macOS distributables need
  platform-specific workers and signing configuration; a Linux build cannot
  certify those targets.

## Definition of done for a new target

Follow [ADDING_A_TOOLCHAIN.md](../forge/ADDING_A_TOOLCHAIN.md): registry and language
routing, pinned image/tools and a version manifest, offline dependency strategy,
helpers with parsed diagnostics, coder/test guidance, an example spec, packaging
where relevant, fake-program tests and a **real-image** smoke test with deliberate
failure cases. Download/build preparation and project execution must preserve
their separate network/trust boundaries. A successful unit suite or a newly
registered name alone is not proof that the toolchain works.

None of these proposed targets was implemented or validated on real images in
this update review. The suggestions describe a scoped implementation path rather
than presumed support.
