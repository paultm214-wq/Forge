# Follow-up review of the supplied update

- The `/out` watchdog checks file size when the process exits as well as while
  it runs. Fast successful or failed writers exceeding the limit are rejected
  and their outputs cleared. This remains a polling watchdog, not a disk quota.
- Deferred-commit deduplication includes assets, outputs, spec/plan, vendored
  files, design and docs, not only module code. `deferred_content_hash` is an
  additive state key; legacy deferred runs establish it on their next commit.
- `forge status` reports non-object state/plan JSON through its normal error
  response instead of raising AttributeError.
- Regressions: `tests/test_update_review.py`.

# 6 November: review hardening - verdicts, `forge pending`, /out, `forge status`

From the 6 November code review. Tests: tests/test_review_hardening.py,
lab-dashboard/test_control_hardening.py, coder-models/test_identifiers.py.

- **Review verdicts are normalized** (`normalize_verdict`). An issue given as
  a plain string crashed the run; "Blocking", "critical", "high" or "major"
  now block as "blocking" does. A rejection with no blocking issue carries
  one unroutable blocking issue, so it uses up a round: Claude's review used
  to loop on it without limit, one paid review per round.
- **`forge pending` no longer commits the same code again** while Claude stays
  unavailable with the internet up (each 30-minute tick made a new branch).
  It backs off between tries - 30 min, 1 h, 2 h, 4 h, then every 8 h
  (`[claude] pending_backoff_minutes`); `forge pending run RUN` tries one now,
  and `forge pending list` shows the next try.
- **`[claude] max_cost_usd`**: stop reviewing a run once its Claude reviews cost
  this much (default: no limit). Raise it and `forge resume`.
- **/out is watched while code runs.** It is a folder on the host's disk, and
  only the copy afterwards checked `outputs_max_mb`: a generated loop writing
  frames filled the disk first. Over `[sandbox] out_watch_mb` (default
  max(4 x outputs_max_mb, 2048)) the code is stopped and /out emptied.
- **The recheck on resume tries a failing test once more** before rebuilding the
  chunk (a busy machine timed out once and lost a chunk its code), and keeps
  the old verified code in chunks/<id>-stale-<time>/verified.py.
- **`forge status RUN [--json]`**: phase, chunks, reviews, waiting work, cost and
  whether a process holds the run. Read-only; no config or servers.
- Smaller: identifiers end with \Z, not $ (a trailing newline passed: an alias
  "x\n" rewrote another model's router preset); FILES paths are checked again
  before outputs/project is written on the host, and in `cargo vendor`'s copy;
  a failed atomic write leaves no .tmp file; exit 137 names the memory limit
  the run really had.
- **Lab dashboard**: controls need a Host that is an IP address, a one-word or
  .local/.lan/.home.arpa name, or one in `allowed_hosts` (DNS rebinding passed
  the Origin check); five wrong PINs from one address lock it out for 15
  minutes (a four-digit PIN fell in under three seconds); a non-ASCII PIN or
  token is refused instead of dropping the connection.

# 5 November (evening): hardware, C/C++ and SQL toolchains; Ziggurat as the offline store

Six more toolchains. Each has an image, helpers, coder notes, a self-test
that passes here, unit tests (tests/test_hw_toolchains.py) and an example
spec.

- **c-cpp: general C and C++.**
  - Compilers and build tools: GCC 14, Clang 19 (with its sanitizer
    runtimes), CMake, Ninja and Meson.
  - Test frameworks: GoogleTest, Catch2, doctest, cmocka and Check.
  - Libraries, as Debian packages: fmt, spdlog, nlohmann_json, CLI11,
    Boost, Eigen, curl, OpenSSL, SQLite, libpq and others.
  - Helpers:
    - `cc_build` (CMake, then Meson, then make; `sanitize=`, `compiler=`,
      `coverage=`, `werror=`) returns compiler and linker errors;
    - `ctest` gives one check per test, and a sanitizer finding fails its
      test;
    - also `cc_run`, `valgrind`, `clang_tidy`, `cppcheck`,
      `clang_format_files` and `coverage` (gcovr).
- **sql.** PostgreSQL 17 with pgTAP and pg_prove, MariaDB 11, SQLite,
  DuckDB, sqlfluff and sqlglot.
  - Helpers:
    - `pg()` starts a throwaway server in /tmp;
    - `pg_apply` applies numbered migrations, and an error names the file
      and line;
    - `pg_test` gives one check per pgTAP assertion;
    - `explain` / `plan_nodes` check index use;
    - also `mariadb()`, `sqlfluff` (with the noisy style rules off) and
      `sql_parse_errors`.
- **mcu: microcontrollers.**
  - Compilers: Arm Cortex-M (newlib and picolibc), AVR and RISC-V GCC.
  - The Pico SDK 2.3.1, with picotool and pioasm built into the image, so a
    Pico build fetches nothing.
  - Also Unity, QEMU, simavr and the MicroPython unix port.
  - Firmware runs, not just builds:
    - Cortex-M0/M3/M4/M7 and RV32 run on QEMU boards with semihosting
      (printf output, and main's return value is the exit code);
    - AVR (atmega328p) runs under simavr.
  - Helpers:
    - `unity_test` (on the host with ASan/UBSan, or on an emulated core)
      generates the runner;
    - `pico_build` for pico, pico_w, pico2 and pico2_w;
    - `uf2_info`;
    - `mpy_test`, which runs MicroPython tests against a fake `machine`
      module (Pin, ADC, PWM, I2C, SPI, UART, Timer, WDT) driven by
      `forge_hw`.
- **esp32: ESP-IDF 6.1.** It uses Espressif's own image, which already has
  QEMU.
  - Helpers: `esp32_build` for any chip, and `esp32_run`, which runs ESP32
    and ESP32-C3 firmware in QEMU until a marker line appears.
  - The self-test passed here against that image directly. The thin layer
    the installer adds on top could not be built here, because there was no
    disk space for a second 13 GB copy.
  - The first version reused the name `esp_build` and silently replaced
    Fallout 4's plugin builder. It is now `esp32_*`, and a test checks
    forge_game for duplicate names.
- **devices: ADB, MTP, PTP, USB mass storage and serial.**
  - Tools in the image:
    - adb and fastboot;
    - mtp-tools and gphoto2;
    - libimobiledevice;
    - mtools, sfdisk, mkfs.*, squashfs and 7z;
    - umockdev, socat, sg3-utils and exiftool;
    - pyusb, pyudev, pyserial and adb-shell.
  - The sandbox has no USB, so tests run against fakes that the real tools
    talk to:
    - **`adb_fake`** is a fake adb *server* speaking the client's protocol:
      devices, shell v2 with exit codes, push, pull, ls, reboot. The
      unmodified `adb` client works against it, checked with 15 commands.
    - **`camera_dir`** makes a folder that gphoto2 imports from as a PTP
      camera.
    - **`disk_image`** builds partitioned USB-stick or SD-card images. FAT
      is written with mtools and ext4 with `mkfs.ext4 -d`; nothing is
      mounted.
    - **`serial_fake`** gives a pty pair with a scripted responder.
    - **`umockdev_run`** replays USB devices recorded on real hardware.
- **raspberrypi.**
  - Cross-compilers for arm64 and armhf. The arm64 libgpiod and i2c
    libraries are installed multiarch.
  - Helpers:
    - `rpi_build` cross-compiles;
    - `rpi_run` runs the result under qemu-user;
    - `dt_overlay` / `dt_decompile`;
    - `cloud_init_schema`;
    - `pi_config_problems`.
  - Also in the image: gpiozero (tests use its MockFactory), smbus2 and
    spidev. The SD-card image helpers work here too.
- **Ziggurat as the offline store** (`ziggurat/`).
  - Package caches:
    - `compose.yaml` runs caching proxies on Ziggurat: apt-cacher-ng,
      proxpi (PyPI), Verdaccio (npm), Athens (Go) and a Docker Hub
      pull-through registry.
    - `install-forge-toolchains.sh` reads `~/forge/mirrors.env` and uses
      each mirror that answers, as build arguments, so nothing is baked
      into the images.
    - Forge's per-run download containers read `[mirrors]` in config.toml
      and fall back to the internet.
    - `forge check` lists the mirrors.
  - Built images: `install-forge-toolchains.sh --save DIR` / `--load DIR`
    keeps them as zstd archives on Ziggurat, so they can be restored
    without the internet.
  - Kiwix: `kiwix-forge-library.py` has two new groups, `hardware-qa` and
    `wikis`, and more devdocs. Every name was checked against the Kiwix
    catalog.
- **Installer fixes.**
  - Toolchains without a smoke test stopped the smoke loop with "cmd:
    unbound variable".
  - CTest's JUnit output leaves out test output, so sanitizer findings now
    come from the run's log.
  - JUnit names no longer repeat the class name when it equals the test
    name.
- **Example specs:**
  - cpp-log-digest;
  - sql-lab-inventory;
  - mcu-plant-monitor;
  - devices-photo-ingest;
  - raspberrypi-door-sensor.

# 5 November: new toolchains - minecraft, shell, node, go, homeassistant

forge can now build and test five more kinds of project offline. Each has
an image, test helpers, coder notes, a self-test that passes here, unit
tests and an example spec. `ADDING_A_TOOLCHAIN.md` is the checklist for
the next one.

- **minecraft (Paper plugins, Java 25).**
  - The image has Paper 26.3 (latest stable build), the Paper API,
    MockBukkit and JUnit.
  - Helpers: `mc_build`, `mc_compile` (javac errors), `mc_test` (JUnit +
    MockBukkit), and `mc_run`, which starts a real headless Paper server
    with the plugin and datapacks and runs Java checks on the server
    thread.
  - javac errors get API facts from indexes of the Paper and MockBukkit
    jars (`forge_java.py`, `tools/java_api.py`), the same way Mutagen's
    did for fo4.
  - Building the image needs `MINECRAFT_EULA=accept` (Mojang's EULA,
    yours to accept) and `MC_CONTACT=<email or URL>` (PaperMC's download
    API asks for contact details).
  - The plugin's jar and datapacks are packaged into outputs/build.
- **shell.** Bash scripts and the systemd units and YAML they install.
  - Helpers: `shellcheck` (info level, so SC2086 counts), `bats` with
    bats-assert/support/file, and `sh_run`, which runs a script with
    commands such as systemctl, docker or apt-get replaced by stubs that
    record their calls. Also `systemd_verify`, `yamllint` and
    `shfmt_diff`.
- **node.** TypeScript/JavaScript on Node 26 LTS.
  - TypeScript 7, vitest 5, tsx, jsdom and @types/node are in the image.
  - Helpers: `tsc`, `vitest`, `node_run`.
- **go.** Go 1.27 with gcc, so the race detector works.
  - Helpers: `go_build` (build + vet), `go_test` (`-json`: one check per
    test and subtest), `gofmt_files`, `go(...)`.
  - A module's commands are packaged as static binaries in outputs/build.
- **homeassistant.** Custom integrations, Home Assistant YAML and ESPHome
  device YAML.
  - The image has Home Assistant core with
    pytest-homeassistant-custom-component (pinned together), on
    Python 3.14.
  - Helpers: `ha_test`, which imports the project's custom_components
    first, otherwise HA's loader finds the test package's own folder.
  - `ha_check_config` runs HA's own config check (`--json`) with the
    project's integrations.
  - `esphome_config` validates device YAML without compiling it.
- **Dependencies are fetched per run.** npm packages, Go modules and HA
  manifest requirements come from a download-only container, as Rust
  crates already did.
  - No package code runs there: `npm --ignore-scripts`, `go mod tidy`,
    and `pip download --only-binary`.
  - The result is mounted read-only into the sandbox.
  - Downloads are cached in ~/forge/cache, so runs still work offline.
    This was checked online and offline for all three.
- **New failure facts.**
  - A package that is not in package.json.
  - A Go import that nothing provides.
  - An HA integration the loader cannot find.
- **Other changes.**
  - Go is a language for coder-model choice.
  - `forge selftest` accepts every toolchain that has a self-test.
  - `package()` creates outputs/build itself; the minecraft branch
    failed without it.
- **Example specs:**
  - minecraft-crest-tower: the Council Crest rules and numbers again;
  - shell-lab-health;
  - node-lab-status;
  - go-gpu-lease;
  - homeassistant-lab-monitor.

# 4 November (evening): pdx-slice-a-terrain

c1, c2, c7 and c8 verified. c3 (the GeoTIFF reader) failed 30 attempts in
three rounds.

- **The contract check missed `self.width = ...`.** It counted only
  class-level names, so GeoTiff.width set in `__init__` was "not defined".
  That cost two attempts per round. Attributes assigned on `self` in any
  method now count.
- **The planner's chunk spec had the format facts wrong.**
  - It said GTModelTypeGeoKey 1 = geographic and 2 = projected (it is the
    other way round).
  - It gave FLOAT and DOUBLE 1 and 2 bytes, and read the tiepoint as LONGs.
  - Coders see only the chunk spec, so every one followed it, and the
    audits kept saying "the test is right".
  - forge now passes a spec's `## Reference facts` section to every coder
    word for word, and tells them it wins where the task text disagrees.
  - The terrain spec now has that section: TIFF field types, tags, strips
    and tiles, GeoKeys, LZW early change, and predictors 2 and 3. It was
    checked against tifffile's tables and a reader that decodes the
    fixtures.
- **c2 (LZW) took 17 attempts,** partly because the test writer
  hand-encoded LZW bits wrongly twice.
  - The spec now ships three LZW streams made by imagecodecs, one of which
    crosses the 9-to-10 and 10-to-11-bit steps.
  - `lzw_decode` is now a named interface.
  - Tests are told to use only the shipped streams.
- Acceptance 1 now gives 3235.294117647059 instead of "3235.2941...".

# 4 November (noon): the c3 retest and a third plugin run

- **The retest's attempt 8 had the plugin right.** Its masters were
  `["Fallout4.esm"]`, and the test failed only on `b"ControllerGlobal\x00"`
  in the VMAD.
  - xEdit's wbVMAD shows VMAD names are uint16-length-prefixed, with no
    null terminator.
  - The audit called the test right and invented a lesson: "set the
    property's Type (and Access) field". Mutagen has neither field, and
    attempt 10 then failed on `ScriptProperty.Access`.
  - forge_game now decodes the VMAD as `rec['scripts']`: each script's name
    and properties, with objects as `{formid, alias}` and arrays as lists.
  - A failing check that looks for `\x00` in VMAD bytes now comes back
    "the test is wrong".
  - A lesson that claims "its X field" for a Mutagen type without that
    member is dropped.
- **The third run's c3:** 7 of 10 attempts read Fallout4.esm with
  `CreateFromBinaryOverlay` and tried to add records to it.
  - That gives a read-only getter, hence `IFallout4GroupGetter ... AddNew`.
  - The spec said Main "reads masters from args[1]", which invited this.
    The spec now says the plugin is a new Fallout4Mod with Fallout4.esm
    added to its master list, and never needs to read Fallout4.esm.
  - The coder notes now say the same.
  - CS1929 (a read-only receiver) and CS0104 (`Activator` is both
    Mutagen's and System's) now come back with facts.
- **c2's test (Papyrus):** two slips, both caught by the audit.
  - A local was named `quest`.
  - The test expected type names in the error. The compiler reports a
    wrong-type call as "is not a function or does not exist".
  - The fo4 test writer is now told both.

# 4 November (morning): pdx-slice-a-plugin, second run

c1 (Papyrus Factions) and c2 (CrestBroadcast) verified within a minute.
c3 (Program.cs) failed, and mostly on its test:

- **The plugin was right in attempts 2 and 7. The test was wrong.**
  - The test read `rec['subs']['MODL']` as bytes; forge_game gives a list,
    one item per subrecord.
  - Its GLOB and QUST checks used DATA subrecords that Fallout 4 does not
    have. xEdit's wbDefinitionsFO4 shows GLOB keeps FNAM ('s') and FLTV
    (a float), and QUST keeps its flags in DNAM (0x0001 Start Game Enabled).
  - Its last check looked in outputs/build, which forge only makes after
    every chunk test has passed.
  - forge_game now decodes these fields: `rec['model']`, `glob_type` and
    `value` for GLOB, `quest_flags` for QUST.
  - `game.build_preview('fo4')` packages into a scratch folder now, so a
    test can check what outputs/build will hold.
  - The fo4 test writer is told all of this.
  - When a test misreads forge_game this way, the failure comes back with
    a FACT saying the test is wrong, so the audit sees it.
- **The masters write works.** Attempts 2 and 7 used the notes'
  `MastersListContentOption.NoCheck` and got `["Fallout4.esm"]`. Attempts
  4 and 8 used the plain write and got `[]`.
- **`mod.Factions[i]`:** a group is keyed by FormKey. A "cannot convert"
  error now comes back with the offending source line, the indexers that
  take that type, and how to get one (`record.FormKey`).
- An invented `FormKeyFactory` lesson was dropped, as intended.
- The spec's acceptance 6-9 now use the decoded fields and `build_preview`.

# 4 November: pdx-slice-a-plugin - `import game`, and Mutagen's real API

- **All three plugin chunks failed on "No module named 'game'".**
  - The planner began every test with `import game`. The sandbox only had
    `game` as a ready-made name, so 40 attempts died on the first line.
  - forge's hint then told the coder to stop using the missing package, and
    the audits could only answer "the test is right".
  - Tests can now `import game` (it is registered as a module).
- **Plugin/Program.cs failed 28 attempts on Mutagen names that do not exist.**
  - Invented names: Quest.VMAD, QuestScriptData, StatRecord,
    ScriptProperty.Value, FormKey.Value, Mutagen.Bethesda.Skyrim and
    .Synthesis. Eleven attempts also missed the `using` for IModGetter.
  - The audits "corrected" these with other invented names.
  - forge now ships an index of Mutagen 0.54.4, read from the NuGet
    package's DLLs: every public type, its properties and types,
    constructors and enum values (`fo4/mutagen-0.54.4-api.json.gz`, made by
    `tools/dotnet_api.py`).
  - Each C# compile error comes back with API FACTS from that index: the
    real members of the type, the namespace the type lives in (with the
    `using`), the real constructors, and close real names. The next attempt,
    the diagnosis and the test audit all see them, and a `using` fix needs
    no model call.
  - The fo4 coder gets the members of the records the spec names (FACT,
    WRLD, STAT, GLOB, QUST...) plus Mutagen notes: groups and AddNew,
    GlobalShort, Model, QuestData flags, scripts and properties in VMAD,
    and masters with MastersListContentOption.NoCheck. A test checks that
    every name in those notes is in the index.
  - A lesson that names Mutagen members the index does not have is dropped,
    not held (e.g. "Fallout4Mod.CreateEmpty",
    "Quest.Data.DataFlags |= QuestDataFlags...").
- **Papyrus tests:** test writers now learn that
  - `r.errors` items are dicts (`e['message']`, not `e.message`);
  - the compiler writes type names in lower case;
  - a scratch script must sit where its ScriptName says;
  - Papyrus has no EndScript.
- **Blender (pdx-slice-a-props):** new coder notes on
  - `face.verts`;
  - deleting loose parts by their vertices;
  - `decimate_type` (a modifier's `.type` is read-only);
  - unlinking from `obj.users_collection`;
  - measuring and grounding after decimation. ReclaimerWaterStill shipped
    1 mm off the ground before Claude's review caught it.
- **Not verified here:** the masters write call (NoCheck) is built only from
  names in the DLLs; no .NET could run in this workspace. `forge selftest
  fo4` still checks the plain write.

# 3 November (afternoon): props committed without a build; plugin's Papyrus

- **pdx-slice-a-props was committed with no outputs/build.**
  - After the bubbler was re-made, build.py ran into its 900 s timeout. The
    capture run failed with it, and Claude's "re-rendered images only"
    round approved what it could not see.
  - A failed package or capture run is now a blocking finding for the chunk
    that owns the build (build.py, Plugin/Program.cs), and no review runs
    on a build that failed.
  - forge refuses to commit when the final build fails.
  - After re-rendering assets in a FILES project, the chunk tests run again
    (and are repaired), because the project reads those assets.
- **Backdrop planes and floor discs in the models, again.** The renders
  carry alpha noise (252-254 on a few thousand pixels). Both forge and the
  shape server read that as "already transparent", so the background was
  never removed. Real transparency now means at least 1% of the image
  see-through.
- **Papyrus:** the coders wrote C-style casts and invented ways to send a
  custom event (SendEvent, ControllerChanged.Send), so all 18 attempts at
  the Crest Signal script failed to compile. fo4 coders now get Papyrus
  notes: `as` casts, CustomEvent plus SendCustomEvent with a Var array,
  and how a receiver registers. Blender coders get notes too: loops toward
  a target must stop when nothing changes, joined objects are freed, and
  bmesh.ops is module-level. fo4 test writers learn that r.pex holds
  absolute paths.

# 3 November: pdx-slice-a-props, second run

- **Vulcan refused every 3D model ("only 8.4 GB of VRAM free"), even with
  Strata stopped.** The holder was the shape server itself: after its first
  release, about 7.6 GB stayed with the process (PyTorch / onnxruntime do
  not give it all back). On 1 Nov the first batch of models worked, and
  everything after the first release failed. The server now restarts
  itself after each release (systemd starts a fresh process; `--keep-alive`
  turns this off). It also restarts when it finds its own leftovers while
  short of VRAM, and its error names who holds the GPU (from nvidia-smi).
  forge waits for a restarting shape server instead of failing the model.
- **Coding requests reached Vulcan while it made 3D models.**
  - Strata was stopped for the shape model in the middle of a coding
    attempt that was already running. forge now lets that attempt finish
    first; no new one starts meanwhile.
  - After the shape server let go of the GPU, the next attempt went to
    Vulcan in the roughly 40 seconds before Strata was back, and waited on
    "connection refused". The principal coder now stays paused until
    Strata's /health reports the model loaded (at most 30 minutes).
  - The dashboard shows "3D model" on Vulcan while a shape is made.
- **"No module named 'pdxprops'" for two chunks.** A Blender project's own
  packages were importable neither in the test nor inside
  `game.blender_eval`; five wrong lessons and forty attempts went to it.
  The project folder is now on sys.path in both. Test writers are told that
  a module importing bpy is tested through blender_eval, and that paths go
  in as `str(path)`.

# 2 November: pdx-slice-a-props

- **Mercury's triage model held the VRAM.** Mercury is no longer a helper
  coder, so forge neither unloaded its router before a render nor locked
  the GPU between triage calls and renders. The first render failed and
  Mercury was retired for the run. Triage now counts as a tenant of its
  machine, like the helper coders: renders wait for a triage call and
  unload the model first, and so do run start, run stop and stuck-server
  restarts.
- **Apollo never rendered.** Its image worker quit in the first second,
  because Mercury was the quicker choice for every job at that moment.
  When Mercury failed, nobody rendered. A server now stays while any queued
  job could still come to it.
- **The build chunk ran before the 3D models existed.** Its task named its
  inputs as `assets/models/<id>.glb`, a folder pattern, so forge did not
  wait for Vulcan. A task or test that names an asset folder with `<...>`,
  `{...}`, `*` or `[...]` now waits for every asset in that folder.
- **Wrong lessons:** three were invented to explain that missing file. A
  lesson is now held until the chunk it came from passes, and is dropped
  if it never does.
- **Floor slabs under every model:** the renders' backdrop gradient and
  floor shadow became a 2 x 2 slab. Before the shape is made, forge keys
  out the light background connected to the image border (with the soft
  shadow) and sends the object on transparency. Per asset:
  `"key_background": false` to skip, `"background"`,
  `"background_tolerance"`.
- **Vulcan: "only 8.4 GB of VRAM free" for every re-made model.** Strata
  held the GPU. `[[shapes.servers]]` now takes `free_cmd` / `restore_cmd`
  (run over ssh): roles.toml stops Strata before the first model and starts
  it again after the shape server lets go.
- **A failed 3D model no longer counts as a failed image server.** It used
  to throw away two good renders and report "every image server failed".

# 1 November: rose-city on the new roles - what its run taught

- **Strata was thinking through every "direct" attempt** - 28000 tokens of
  reasoning, no code, four minutes each, all of c10's rebuilds. forge sent
  `reasoning_effort` inside chat_template_kwargs ("low"), where Strata does
  not read it. Now `reasoning_top_level = true` and `no_think_effort =
  "none"` in [coder] (roles.toml); Gemini's planner is unchanged.
- **Triage audits never finished**: TielCoder thought 32000 tokens at
  "high" and again at "medium" (it has no effort levels) - about 20 minutes
  per audit, every audit. Triage now audits without thinking, once; no
  verdict, or a prompt too big for its window (which stopped the whole run
  once), goes straight to the planner. Triage's output caps are 16000, so
  its prompts get more room.
- **A chunk's own parse error was blamed on the test**: the eval backtrace
  names __forge_eval.gd, so inventory_bar.gd's error came with "THE TEST'S
  OWN GDSCRIPT does not parse" and an immediate audit. Only an error located
  in the eval script counts now, and the failure headline is the first
  parse/compile error, not the "Nonexistent function 'new'" it causes.
- **Boot check (Godot)**: after the regression, forge starts the finished
  game (import, then the main scene for 180 frames) and turns every script
  error into a finding for the chunk that wrote the file - two fix rounds,
  then Claude rules. This run passed all 51 chunk tests while
  room_view.gd did not compile; Claude found it twice, after the local
  reviews.
- **Game art checked before any review**: chroma keying measures the
  background the model actually painted and clears only what is connected
  to the border (a pink doughnut box is no longer keyed away; an off-shade
  magenta background is); keyed sprites that are empty or still mostly key
  colour, and scenes that are nearly black, are rendered again with a new
  seed (`[images] art_rerolls`, 2), then kept for the review to judge.
- **Harness**: add_child/get_node/has_node work in godot_eval; `var x :=`
  in test code is relaxed to `=` (projects that treat warnings as errors);
  more rules for test writers (godot_eval returns the value; Script
  resources vs instances; inherited methods) and for coders (`:=` on untyped
  calls, properties that do not exist, draw order, Controls under Node2D).
- **Image entries in FILES**: one log line instead of 35 per test; chunks
  verified before 30 Oct lose theirs for good.
- **Finding tests** for a whole Godot project get 24000 / 12000 tokens (12000
  ran out three times).
- **Lessons**:
  - an audit's lesson is for "tests" or "code"; coders get the code lessons;
  - test writers mid-run (`forge retest`, rebuilds, finding tests) get this
    run's lessons, not only earlier runs';
  - before a chunk's first attempt, its test is checked against lessons
    learned since it was written, and corrected once if it breaks one (it
    must keep its checks) - `[loop] lesson_check`;
  - a lesson counts once per run (a resumed run no longer counts twice),
    leaves the prompts after 120 days unseen, and `forge lessons list |
    drop N | clear` shows and switches them off.

# 30 October: new machine roles, triage, and rose-city's third run

## Roles (forge/roles.toml, `python3 ~/forge/apply-roles.py` on Gemini)
- **Vulcan** is the principal coder ([coder]): Strata serving
  Qwen3.8-Flash-Next on :8081 (`install-vulcan-strata.sh`: retires the
  image/gaming jobs - disabled, not removed - installs Strata after showing
  you its installer, a `strata` user service, the API key, firewall). It
  also makes the 3D models: the shape server stops Strata while it holds
  the GPU and starts it again after, forge waits until every image that
  needs a model is rendered so that happens once per run, and the principal
  coder pauses meanwhile (the helpers carry on). `switch_models = false`
  keeps forge's per-language model switching away from Strata; a server
  with no /tokenize is sized by estimate. Optional `gpu-unlock` phase
  (Zagitta/gpu-unlock, experimental on Ada) with an off switch.
- **Apollo** codes (helper) and is the primary image server, full steps
  (`install-apollo-images.sh`, Mercury's sd.cpp/Vulkan setup without its OS
  phase). Clamped to x1.5 hires until x2 is proven on its Vulkan driver.
- **Mercury** keeps Lightning renders and becomes **triage** ([triage]):
  the first diagnosis and the first test audit of a failing chunk run on
  its router instead of the planner; the planner takes the second audit.
  While Mercury renders, the planner does it. Its coder seat is off by
  default (10 attempts in the last run, none won) - `enabled = true` brings
  it back.
- Assets pinned to an image server that no longer renders (`"server":
  "vulcan"`) go to any server instead of failing the run.

## Fixes from rose-city-signal-20261030-101513
- **VRAM after a failure:** forge unloaded only the model it expected
  (tiel-coder), while the earlier run had left qwen3.8-27b loaded on
  Mercury - the first render failed ("generate_image returned no results")
  and Mercury was retired. Now every loaded model on the router is
  unloaded, at the start of a run with renders, when a build stops, and
  before restarting a stuck sd-server.
- **Placeholder images:** c2 wrote `FILES['assets/rooms/*.png'] =
  'placeholder'`, which overwrote the real renders in every later test and
  the package. Images and other binaries in FILES are dropped (with a
  note to the coder) and never written over a render.
- **"No FILE block":** Vulcan's coder wrote ```` ```FILE: ui/a.tscn ````,
  a label-only fence, or FILE lines with no fences - five of c5/c6's
  attempts were thrown away. All three forms parse now.
- **The tests' own GDScript:** most "failures" were the tests (in the three
  runs, 15 of 24 chunk verifications came only after an audit fixed the test). The
  godot_eval harness now runs at the first frame (root in the tree, so
  `_ready()` runs on add_child and autoload nodes exist), with
  `get_tree()`, and mends Python-style asserts, lambdas called without
  `.call`, `func` inside the statements and a redeclared `result`. Image
  helpers take the PNG paths godot_frames returns; `"assets/x.png" in
  game.files()` is true once rendered; `script_errors()` leaves out errors
  only about an autoload or main scene a later chunk writes. A test whose
  own GDScript does not parse is audited on the first failure. The
  failure text shows script errors first and drops image/main-scene noise.
  Test writers and coders get the Godot rules seen in these runs.
- **Run lessons:** when an audit finds the TEST wrong in a way that can
  repeat, it states the rule; it goes to every later diagnosis, audit and
  test of the run, and (after it has come up twice) to later runs with the
  same toolchain (`~/forge/lessons/<toolchain>.json`).

## Lab dashboard
- Tap a machine: dials (GPU load, VRAM, temperatures, power, CPU, RAM,
  tokens/s), an hour of graphs, VRAM by process, model servers, services.
- Controls, each a slide-to-confirm: Clear VRAM, unload a router's model,
  restart the model or image server, stop/start Strata and forge-shape.
  The agents accept only that fixed list, only with the hub's token
  (`install-lab-dashboard.sh controls`); the hub only from the LAN;
  optional PIN.
- forge card: when a run has stopped, "Resume the run" and "Rebuild <failed
  chunks>" (new tests, `forge retest`); this run's lessons.
- Strata's tokens/s, state and queue come from its own metrics (`strata:live_tok_s`,
  `strata:live_state`, `strata:last_decode_tok_s`), asked for as Prometheus text.
- Home-screen web app on iPad and iPhone (manifest, icon, safe areas);
  Hades and Metis as optional machines; Intel Arc readings.

# Forgejo on Metis as forge's git host

- `metis/forgejo-setup.sh` (in the Forgejo container): finds Forgejo
  (systemd binary install or Docker), backs up app.ini and sets the lab
  settings - its LAN address as ROOT_URL, offline mode, no sign-up, sign-in
  required to see anything, private repositories by default, no OpenID, no
  update checks; creates an admin if there is none; and a nightly
  `forgejo dump` pushed to MetisOMV like the machines' backups.
- `metis/forgejo-link.sh` (on Gemini): checks a Forgejo token, creates the
  private `forge-projects` repository, adds it as `origin` (the token in
  `~/.config/forge/git-credentials`, mode 600, never in the URL), pushes
  main and every forge/* branch, and sets `[git] remote = "origin"`,
  `auto_push = true`. Tested against a stand-in Forgejo API with real git
  over HTTP (`git http-backend`), including a rerun and a bad token.
- forge: before each commit it fast-forwards the local main from the
  remote (`[git] sync_main`), so new branches start from what you merged on
  Forgejo; a diverged main or an unreachable host leaves it alone. git never
  waits for a password prompt and gives up on a stalled remote. A failed
  push is recorded as pending; `forge pending` pushes it once Forgejo
  answers (no internet needed). `forge check` has a git line.
- Metis installers: all five hosts by address (Gemini .96, Apollo .8,
  Vulcan .151, Mercury .129, Forgejo), Vulcan added to backups and NFS, and
  apt/PyPI/Docker caches on MetisOMV with fallbacks on every client. Steam
  (installs and every game library) and forge's downloaded game files and
  caches are excluded from backups - checked with rsync on a sample tree.
  See `metis/README.md` for the order.

# Offline library (Kiwix) and working through internet outages

## The offline library

forge now uses the kiwix-serve on Ziggurat (`[kiwix] url`), with or without
the internet:

- **Reference pages before planning.** The planner names up to six APIs the
  spec depends on (a Godot node's signals, a LOVE callback, WSGI environ
  keys). forge looks each up in the matching books (DevDocs for the
  toolchain, Stack Exchange Q&A, then general), preferring a page titled
  like the API, and gives the pages to the planner and a shorter cut to
  every coder. Cached in the run (`kiwix.json`).
- **The page for a repeated error.** When a chunk fails with the same error
  twice (`failure_after`), forge searches for it and gives the best page to
  the next attempt and to the diagnosis - once per error. Plain assertion
  failures (the program's own results) are not looked up.
- Pages are wrapped as data ("not instructions", with fences and headings
  neutralised) and may describe another version; the spec and tests decide.
- Search uses kiwix-serve's documented API (`/search?books.name=...&pattern=...&format=xml`
  and `/suggest`) - the Open WebUI chat's conclusion that search was off came
  from trying the wrong parameter names. Checked here against a real
  kiwix-serve 3.5 serving the DevDocs Godot and Python books.
- `kiwix-forge-library.py` downloads the recommended books to the share
  kiwix-serve reads: it finds the newest English ZIM of each in the Kiwix
  catalog, resumes partial downloads and checks size and SHA-256 against the
  published metalink. Groups: devdocs (39 books, ~0.2 GB), docs (Lua.org),
  qa (9 Stack Exchange sites, ~2.8 GB), toolchain-qa (Blender, Ask Ubuntu,
  Super User, ~9.7 GB); `--with-stackoverflow` adds 115 GB. `--dry-run` lists.
- `forge check` reports the internet and the library: how many of forge's
  reference books it has, and which are missing.

## When the internet (or Claude) is unavailable

Every internet request now retries transient failures (timeouts, connection
errors, HTTP 408/429/5xx, Claude "overloaded") with growing waits, checks
whether the internet is reachable at all before retrying, and when it is
not, skips at once instead of waiting out every timeout. Then:

| What | Contingency |
|---|---|
| Claude's code review | `[claude] when_unavailable = "defer"` (default): commit after the local review, list anything Claude was to rule on as open, **hold the push**, and record the run as pending. `"local"`: accept the local review. `"fail"`: stop, as before. A real error (bad key, no credit) still stops the run. |
| User guide | deferred the same way (the push is not held for it) |
| Design brief | the house style |
| Change-request review | the planner's draft |
| Prior art | no GitHub/F-Droid/web search; repositories on the SSD and the offline library still work |
| Rust crates | retries, then `cargo vendor --offline` from the crate cache |
| Weekly model search | the catalog stays; retried when the internet is back |

`forge pending` (new timer `forge-pending.timer`, every 30 minutes) finishes
deferred work once the internet is back: it resumes each pending run at
Claude's review (code Claude already approved is not reviewed again),
commits the result to a new branch (REPORT.md says which branch it
completes) and pushes it. It does nothing while the internet is still down
or another forge process is building. `forge pending list` shows what is
waiting; `forge pending drop RUN` forgets a run's deferred work.

## Deploying

1. Copy `forge.py` to `~/forge/` (or rerun the forge phase of
   `install-gemini-llm.sh`, which now also writes and enables
   `forge-pending.timer`).
2. Add `[network]` and `[kiwix]`, and the new `[claude]` keys (`retries`,
   `retry_waits`, `when_unavailable`), from this `config.toml` to yours.
3. On a machine with the Ziggurat share mounted:
   `python3 kiwix-forge-library.py <share>/Media/Books/Zim --dry-run`, then
   without `--dry-run`. Restart the kiwix-serve container, then `forge check`.

## Checked here

- 29 new tests (tests/test_offline.py) against a fake kiwix-serve and a fake
  `claude`: catalog, title-first look-ups, page text, reference and failure
  pages reaching the planner, coders and diagnosis; reachability, retries and
  Offline; prior art, crates and the model search offline; Claude retries,
  real errors, and whole runs with Claude down under defer/local/fail,
  including `forge pending` finishing the deferred review on a new branch.
  Full suite 367 passed (2 skipped: the real-browser and real-kiwix tests
  need FORGE_AXE / FORGE_TEST_KIWIX_URL); unittest suite 68.
- The downloader against the live catalog (dry run of all 53 books) and two
  real downloads, one resumed from a partial file, SHA-256 checked.
- Not run on Gemini or against Ziggurat's kiwix-serve (its version is
  unknown; the search API used is in kiwix-serve 3.5 and later).

# Design, UX checks and user documentation for projects with an interface

forge now designs before it builds, checks what it built the way a user
would meet it, and ships a user guide. Your choices: the house style by
default, adapted per project by Claude; Claude writes the docs; UX findings
are reported, never commit-blocking.

## What happens in a run

1. **Which projects.** A spec line `UI: web|gui|game|none` (or `auto`, the
   default): Godot, LOVE, C/SDL, Rust and s&box projects are games; a
   Python spec that mentions HTML, WSGI, a browser, a dashboard and so on is
   web; tkinter/Qt/GTK is a desktop GUI. Everything else is unchanged.
2. **Design brief, before planning.** forge's house style lives in
   `~/forge/design/` (yours to edit): `house-style.md` (layout, type,
   colour, components, WCAG 2.2 AA, help), `tokens.css` (colours, type,
   spacing, components in light and dark - every text pair checked at
   4.5:1 or better) and, for games, `game-ui.md` (the full menu set: title,
   pause, settings for audio/display/controls with rebinding/accessibility,
   save/load, confirmations, How to play; keyboard, mouse and gamepad each
   alone; text size from the Xbox Accessibility Guidelines). Claude reads
   the spec and adapts it into `design/DESIGN.md` - screens, layout, labels,
   states, interactions - plus project tokens and short rules. Without
   Claude (or `brief = "house"`) the house style is the brief.
3. **Planner and coders.** The planner sees the brief and plans its screens
   and menus; every coder and fixer gets its rules; `design/tokens.css` is
   in the project. Prior art also looks for an accessible UI or menu kit
   for the toolchain on GitHub.
4. **UX check after each build (report only).** Web apps are served in a new
   `forge-sandbox-ux` image (no network, as always) and visited in headless
   Chromium: screenshots at 1280 px and 390 px, axe-core 4.14 (WCAG 2.0-2.2
   A/AA plus best practice), a Tab walk (reachable controls, missing focus
   rings, traps, skip link), sideways scrolling on a phone, console errors.
   It starts at the spec's `## Screens` and follows same-site links (never
   logout/delete-style links). Godot projects get an input and menu audit:
   actions without a gamepad or key binding, settings not saved, no menu
   setting focus, the smallest font size. Results: `outputs/ux/`, a section
   in REPORT.md, and Claude's review, which also judges the UX against the
   brief and the screenshots - as `minor`, `ux` findings that never block.
5. **User guide at commit.** Claude reads the spec, the brief, the code and
   the screenshots and writes the guide as structured sections; forge checks
   it (unique ids, only images that exist) and renders `docs/index.html`
   (contents, live search with a match count, `/` to search, `?q=` links,
   print styles, the project's tokens - axe-clean), `docs/USER_GUIDE.md` and
   `docs/images/`. When the app has in-app help (`Help: yes`, or the brief
   says it needs one), the planner was told to build a help screen with a
   searchable index that reads `help/help.json`, and forge puts the guide
   there at commit. If Claude is unavailable the commit goes ahead with a
   note in REPORT.md.

## Deploying

1. Copy `forge.py`, `forge_game.py`, `forge_ux.py` and the `design/`
   folder to `~/forge/` - or rerun the forge phase of
   `install-gemini-llm.sh`, which now installs them (and keeps your edits
   to `~/forge/design/*`, putting forge's new version beside as `.new`).
2. Add `[design]`, `[ux]` and `[docs]` from this `config.toml` to yours.
3. `./install-forge-toolchains.sh ux` builds `forge-sandbox-ux` (Playwright
   1.64.0 with its Chromium; axe-core 4.14.0 from the npm registry, checked
   against npm's sha512 integrity; about 0.6 GB). Without it web projects
   still build; they just get no screenshots or accessibility check.
4. Spec templates: `UI:`, `Serve:` (the WSGI app, e.g. `make_app(tmp)`),
   `Help:` and `## Screens` are documented in SPEC_TEMPLATE.md, and
   `UI:`/`Help:` in GAME_SPEC_TEMPLATE.md.

## Checked here

- 46 new tests (tests/test_ux_docs.py): detection, the brief with a fake
  `claude` CLI (and its fallbacks), planner/coder prompts, the sandbox's
  image override, serving, the UX check, the Godot audit, the guide's
  Markdown, rendering and search (in real Chromium), docs at commit, the
  report, a whole web run, and forge_ux.py itself against a sample app in
  real Chromium with axe-core. Full suite: 339 passed (the forge_ux test
  skips unless FORGE_AXE points at axe.min.js); unittest suite: 68 passed.
- The `ux` image's smoke test (Chromium plus axe in a page) ran here
  outside Docker. The image build itself and the Claude calls are not yet
  run on Gemini.

# Fixes from the rose-city-signal runs (2026-10-28 and -29)

Both Godot runs stalled on forge problems, not just hard chunks:

1. **godot_eval and space-indented test code.** Tests write GDScript inside
   Python strings, indented with spaces; godot_eval put a tab in front, and
   Godot rejected the script ("Mixed use of tabs and spaces"). Every such
   eval returned nothing, so c3's walkthrough checks failed for a reason no
   coder could fix. Code is now re-indented with tabs (`game.gd_indent`).
2. **A script that fails to load left Godot running** until the 120 s
   timeout, and the timeout dropped stderr, where Godot prints its errors:
   the coder saw only the banner. godot_eval now passes `--quit-after`, and
   a timeout keeps stderr.
3. **godot_eval hid script errors.** A test sees only the returned value, so
   "Trying to assign an array of type Array to Array[String]" (c3's empty
   inventory, attempt after attempt) never reached the coder. Godot's script
   errors are now printed with the result.
4. **`res://` paths.** The c2 test looked up `files()["res://data/world.json"]`;
   the audit then told the coder to write `FILE: res://data/world.json`,
   which forge refused as unsafe and that error stopped the whole build.
   `game.files()` accepts res:// keys, replies' res:// paths are made
   project-relative, and a bad path is now a failed attempt with a message.
5. **A reply with one unlabelled code block** ("no FILE block for
   scripts/adventure.gd", 10+ times) is taken as the chunk's file when the
   chunk owns exactly one.
6. **Failure headlines** named Godot's C++ source line ("at: _load
   (core/io/resource_loader.cpp:317)"); they now give the script error.
8. **A main scene that does not exist yet.** c1's project.godot names
   res://main.tscn, which a later chunk writes. Godot loads the main scene
   even for godot_eval's script, fails ("Failed loading resource:
   res://main.tscn") and exits before the script runs, so every c3 eval came
   back empty. godot_eval now switches a missing main scene off for its own
   run (an override.cfg it removes afterwards).
7. **Test writers are told**: paths are project-relative; an early chunk's
   test sees only its own and its dependencies' files (c1's import check
   failed on main.tscn and game.gd that later chunks write, which led to
   rebuilds of c1 that flip-flopped on main_scene); GDScript/Lua/C# inside a
   test goes in plain strings, not f-strings (a `{...}` dictionary broke one).

Not changed: c3 (the whole game logic) is big enough that thinking ran out
at 32-44k tokens on nearly every attempt, and the planner's high-effort
outline and audits hit `thinking_max_tokens = 36000` several times, each
costing a retry at medium. Raising the planner's thinking_max_tokens to
about 60000 would avoid those retries.

# forge: game toolchains (sbox, gmod, fo4, blender) and 3D shapes

## Deploying

1. Copy `forge.py` and `forge_game.py` to `~/forge/`, and add the `[shapes]`
   and `[games]` sections from this `config.toml` to yours (the Gemini
   installer only writes a config when there is none).
2. On Gemini, build the images:
   `./install-forge-toolchains.sh sbox gmod fo4 blender`
   - gmod: downloads the dedicated server (app 4020, anonymous) to
     `~/forge/games/gmod-server`, moving `~/gmod-probe/server` there if it
     exists, and removes the probe's test addon.
   - fo4: extracts the Papyrus sources (Base.zip, DLC zips) to
     `~/forge/games/fo4/scripts`; builds Mono + Wine + a Wine prefix +
     .NET 10 + Mutagen 0.54.4 into the image.
   - blender: Blender 5.2.3, downloaded on the host from download.blender.org
     or, when its Cloudflare front refuses curl (403), mirror.clarkson.edu
     (where blender.org's own download page points), then the Berkeley and
     NLUUG mirrors. It must match the published SHA-256, and is kept in
     `~/forge/toolchain-cache` (`BLENDER_VERSION=`, `BLENDER_MIRROR=`,
     `BLENDER_SHA256=` to override).
3. Check each one end to end: `forge selftest sbox` (then gmod, fo4,
   blender). Each runs a small sample project through the real sandbox
   with the real mounts and limits, and prints PASS/FAIL per step.
4. On Vulcan: copy `install-vulcan-shape.sh` and `forge-shape-server.py`
   (same folder), run `./install-vulcan-shape.sh`, then on Gemini set
   `[shapes] enabled = true` and run `forge selftest shapes`.

## Toolchains

- **sbox** - C# against the s&box install (mounted read-only at /sbox),
  MSTest, the engine started headless for scene tests (the probes' stand-in
  engine folder and SDL offscreen driver). `game.sbox_eval` runs C# snippets
  in a Scene and returns plain data; several snippets share one build.
  `game.sbox_test` runs the project's UnitTests/, `game.sbox_build` compiles.
  NuGet is offline: MSTest comes from the image's cache.
- **gmod** - the project is an addon; `game.gmod_run(lua)` starts the
  headless dedicated server on gm_construct, runs the Lua once the map has
  loaded, collects `FORGE.check`/`FORGE.value` results from data/, then
  kills the server (it ignores stop signals). Lua errors on the console
  fail the run. `game.glualint()` checks GLua syntax, including client code.
- **fo4** - Papyrus compiled by the Creation Kit's compiler under Mono, its
  assembler under Wine (probe 5's setup); errors come back with file, line
  and column. The plugin is built by `Plugin/Program.cs` with Mutagen
  (forge writes the .csproj). `game.esp_read/esp_records/esp_record` read
  plugins in plain Python (groups, compressed records, XXXX, localized and
  light flags, overrides). outputs/build is the Data folder to install.
- **blender** - Python in headless Blender: `game.blender_eval`,
  `game.blender` (scripts), `game.mesh_info` (counts, manifold checks,
  dimensions, materials), `game.blender_render` (Cycles CPU, auto camera),
  `game.blender_build` (a project's build.py; packaged to outputs/build).
  Captures can render models: `{"name", "model", "views"}`, with
  `{build}/x.glb` for what build.py made.
- Per toolchain: game folders mounted read-only from `[games]`, and limits
  for `pids` and `cpus` as well as memory, tmpfs and timeout
  (`[sandbox.toolchains.<name>]`). `forge check` lists the game folders.

## 3D shapes (Hunyuan3D 2.1 on Vulcan)

- An image asset with `"shape": {"filename": "models/x.glb", ...}` is
  rendered as usual, then sent to the shape server, which returns an
  untextured GLB in assets/. Chunks that name the model file wait for it.
- Shape jobs on a machine start once that machine has no renders left, run
  under its GPU lock with the helper coder unloaded, and end with a release
  that gives the GPU back. A resume makes missing models without
  re-rendering; a re-rendered image gets its model straight away.
- `forge-shape-server.py`: shape model only (texturing needs 21 GB), loads
  on the first job, checks free VRAM first (unloads the local coder, then
  stops sd-server until released if still short), releases after 10 idle
  minutes. onnxruntime is 1.20.1, not Tencent's 1.16.3: 1.16.3 asks for an
  executable stack, which Kubuntu 26.04's glibc refuses to load. The
  installer now checks every library in the venv for that, and that the
  shape code imports. Licence: Tencent Hunyuan 3D 2.1 Community License - not valid in
  the EU, UK or South Korea; NOTICE installed beside the server.

## Not verified here

This workspace has no Docker daemon, and running Blender was blocked, so
none of the images, Dockerfiles or in-engine code paths have run. What
the tests do cover, with fake programs: every helper's command lines,
files, parsing and process handling, the shape pipeline over real HTTP,
and the shape server's API. The first `forge selftest` of each toolchain
is the real check. Things most likely to need a fix there:
- s&box: whether NuGet restores offline from the cache mirror, and whether
  s&box code compiles with forge's global usings.
- gmod: whether srcds runs from the read-only server mirror under /tmp, and
  the exact console format of Lua errors.
- fo4: whether Wine runs from the mirrored prefix, and the compiler's error
  format under Mono.
- blender: the bpy calls in the render and measurement scripts.

## Tests

- `python3 -m pytest -q tests`: 293 tests (new: test_rose_city_fixes, test_game_helpers,
  test_game_toolchains, test_shapes, test_shape_server - the last one is
  skipped where forge-shape-server.py is not beside the forge folder).
- `python3 -m unittest discover -s tests_unittest`: 68, unchanged.

# Earlier: forge review fixes and enhancements

## Deploying

- `forge6.py` goes to `~/forge/forge.py` as before, now with **`forge_game.py`
  next to it**. Game, emulator, mod and RE projects need that file, and
  `forge check` warns if it is missing.
- `coder-models.py`: run `./install-coder-models.sh` again. It copies the new
  version to each machine and changes nothing else there.
- `config.toml`: the new keys are in `[loop]` (`parallel_chunks`,
  `test_strength`, `mutants`, `min_kill`, `learn_ladder`) and `[models]`
  (`eval`, `eval_cases`, `eval_minutes`). They are all switched on in this
  copy. In the code they default to off, so a config without them behaves
  as before.
- Tests, from `~/forge` (neither needs servers, Docker or GPUs):
  - `python3 -m pytest -q tests`: the main suite, 208 tests, about 80 s
    (coder-models.py has 12 more in its own folder).
  - `python3 -m unittest discover -s tests_unittest`: these 68 tests, about
    15 s. They use a temporary HOME, so your `~/forge` is not touched, and
    pass on Python 3.11, 3.12 and 3.13. They live in their own folder because
    their loader re-imports forge, which would undo the main suite's
    isolation in one pytest session (`pytest.ini` keeps pytest out of
    them).
- `install-gemini-llm.sh` now installs `forge_game.py` beside forge.py.

## Fixes (numbered as in the review)

1. coder-models.py: a download in progress is recognised as the same job
   (`same_job`), and a worker is started and recorded under the state lock.
   Before this, every `ensure` during a download started a second worker on
   the same `.part` file. A different revision for the same alias is refused
   until the running download ends.
2. coder-models.py: a `.part` larger than the published file is deleted and
   the download starts over. Before, it failed every later `ensure` the same way.
3. coder-models.py: `convert-router` reads `--seed -1`, `-n -1` and
   `--flag=value` correctly (`parse_server_args`).
4. `Sandbox.run(max_out=...)`: the all-failures report and the wiring test
   keep up to 24,000 characters (`REPORT_MAX_OUT`). Before, the first failures
   were cut, and those are the ones `mechanical_hints` relies on.
5. A code chunk waits for every image it depends on or names (by file name)
   before it starts, and other chunks build in the meantime
   (`chunk_images`, `build_code_chunks`).
6. Sprite sheets: frame 0's render from before the game-art steps
   (`*.frame0.raw.png`) is the reference image for the later frames.
7. `image_cost` counts each sprite-sheet frame and the tiling pass, so
   `image-speeds.json` is no longer skewed.
8. An attempt that passes its own test but breaks a verified dependent no
   longer triggers the repeat audit.
9. An `UpstreamFault` raised during fixes, rebuilds or regression repair
   rebuilds the culprit (`build_or_fix_upstream`) instead of ending the run.
10. `run_lock`: one forge process per run. A second one stops with
    "in use by pid N on host, since ...". The lock is re-entrant within a
    process and also held around `forge rebuild` and `forge retest`.
11. `commit()` empties the project folder in the worktree before copying,
    except for `changes/`. Outputs and assets the new version no longer
    produces are deleted in the commit.
12. `split_later_checks` moves decorated statements together with their
    decorators.

## Enhancements

1. **Tests for forge itself**, in `tests/`:
   - regression tests for the incidents described in the comments;
   - a test for each fix, all six of the forge ones checked to fail on the
     original;
   - two end-to-end `forge run`s against a fake planner/coder and a fake
     sd-server.

   `FORGE_GAME_PY` is now `forge_game.py`; linting it found an unused import.
2. **Machine leases** (`~/forge/leases/`). A run holds its machines. Prefetch
   and prune, router restarts and model switches (`route_coders`), and model
   eval all skip a machine that another live forge process holds.
3. **Test-strength check** (`[loop] test_strength`). After the regression
   check, each verified chunk gets up to `mutants` one-site mutations. If its
   test catches fewer than `min_kill` of them, the planner adds checks from the
   spec. The new checks are kept only if they pass on the current code and
   catch at least one more mutant. Checks that fail on the current code are
   saved to `test-strength/<chunk>.rejected.py` and listed in REPORT.md for a
   person to judge. REPORT.md gets a "Test strength" section.
4. **Learned ladder** (`[loop] learn_ladder`). Each attempt's outcome is
   recorded per language, step and model in `~/forge/ladder-stats.json`. A
   coder step with at least 10 attempts and under 5% passes is replaced by the
   best step, and the best step goes first. Planner steps keep their places.
5. **Model eval on your own runs** (`forge models eval`; also nightly and
   weekly when `[models] eval = true`).
   - Code chunks of committed runs are given, as a direct attempt, to each
     model a machine already holds. They are graded against the chunk's final
     test and the run's final code.
   - Results go to `~/forge/models/evals.json`, keyed by model commit, so only
     new models or new chunks get graded.
   - The measured pass rate, taken relative to TielCoder's, moves the
     catalog's scores (`effective_scores`), and those scores pick the models.
6. **Parallel chunks** (`[loop] parallel_chunks`). Independent chunks build
   side by side.
   - Writers (Apollo, the helpers, the planner) are claimed across chunks, and
     each chunk gets a fair share of them.
   - A failure lets running chunks finish, then stops the build.
   - plan.json read-modify-writes and `cargo vendor` are locked.

## Fixed while merging

1. `attempt_job` read `writer.model` before its `try`. Any error there
   killed the attempt thread without a result, and the build loop waited
   forever (with `parallel_chunks`, so did every chunk after it). Everything
   now runs inside the `try`, so the result is always reported and the
   writer always released.
2. `forge models eval` stops when a run starts using its machine. The run's
   lease came after eval's, and each eval request made the router swap
   models back (about 18 GB each way) for the rest of `eval_minutes`.
3. The learned ladder's skip rule uses the plain pass rate, as documented.
   With the smoothed rate, 0 passes in 10 is 8%, and no step could fall
   under 5% before 19 attempts. Ranking still uses the smoothed rate.
4. A thinking attempt that ran out and was salvaged by a direct write
   (`think+direct`) counts as a failed `think` and a `direct` attempt. Before,
   the salvage's pass was credited to `think`.

## Not changed

The review's "smaller bugs" and its template, config and installer notes are
still open. The exception is the template's image line, rewritten for fix 5.
