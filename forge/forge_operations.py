"""Runtime integration for provenance, health and cooperative controls.

Installed after forge's definitions, including when loaded by custom test loaders.
The namespace stays live so existing dependency injection and test doubles work.
"""

import argparse
import contextlib
import copy
import io
import json
import re
import shutil
import tempfile
from pathlib import Path

import forge_control as control
import forge_health as health
import forge_provenance as provenance


class Operations:
    def __init__(self, namespace):
        self.g = namespace

    @property
    def home(self):
        return self.g["FORGE_HOME"]

    def checkpoint(self, run):
        action = control.requested(run.root)
        if action in ("pause", "cancel"):
            raise self.g["CheckpointStop"](action)

    def record(self, run, cfg, module, checkpoint_phase=""):
        profile = self.g["Sandbox"](cfg.get("sandbox", {})).profile()
        image = dict(reference=profile["image"], id=None, repo_digests=[])
        if not cfg.get("sandbox", {}).get("unsafe_local_exec"):
            code, text, _ = health.command(
                ["docker", "image", "inspect", profile["image"]]
            )
            if code == 0:
                item = json.loads(text)[0]
                image.update(id=item["Id"], repo_digests=item.get("RepoDigests", []))
        models = [
            dict(
                role=role,
                **{
                    k: value[k]
                    for k in ("model", "kind", "revision", "repo")
                    if k in value
                },
            )
            for role in ("planner", "coder", "triage")
            if isinstance(value := cfg.get(role), dict)
        ]
        assignments = run.state.get("coder_models", {}).get("assign", {})
        catalog = {
            m.get("alias"): m for m in self.g["load_catalog"]().get("models", [])
        }
        for machine, assignment in assignments.items():
            aliases = {
                assignment.get("default"),
                *assignment.get("by_language", {}).values(),
            } - {None}
            for alias in aliases:
                models.append(
                    {
                        "role": machine,
                        "model": alias,
                        **{
                            k: catalog.get(alias, {})[k]
                            for k in ("revision", "repo")
                            if k in catalog.get(alias, {})
                        },
                    }
                )
        record = provenance.build(
            run.root,
            toolchain=self.g["TOOLCHAIN"],
            image=image,
            models=models,
            project_files=self.g["python_to_files"](module)
            if self.g["files_mode"]()
            else {},
        )
        record.update(
            profile=self.g.get("TOOLCHAIN_PROFILE") or None,
            checkpoint_phase=checkpoint_phase or None,
        )
        control.atomic(
            run.root
            / ("CHECKPOINT_PROVENANCE.json" if checkpoint_phase else provenance.NAME),
            record,
        )
        return record

    def replay(self, root, cfg, verify=False, image_tolerance=0, mesh_tolerance=0):
        g = self.g
        source = g["Run"](root.expanduser().resolve())
        expected = json.loads((source.root / provenance.NAME).read_text())
        if expected.get("schema_version") != 1:
            raise g["ForgeError"]("unsupported provenance version")
        if set(expected.get("inputs", {})) - {"SPEC.md", "plan.json", "module.py"}:
            raise g["ForgeError"]("unsupported input references")
        if any(
            not (source.root / n).is_file() or provenance.digest(source.root / n) != h
            for n, h in expected.get("inputs", {}).items()
        ):
            raise g["ForgeError"]("saved inputs differ from provenance")
        if not verify:
            return dict(
                verified=None,
                toolchain=expected["toolchain"],
                artifacts=len(expected["artifacts"]),
            )
        with tempfile.TemporaryDirectory(prefix="forge-replay-") as temporary:
            target = Path(temporary)
            try:
                for name in (
                    "SPEC.md",
                    "plan.json",
                    "state.json",
                    "verified",
                    "vendor",
                    "deps",
                    "assets",
                    "design",
                ):
                    path = source.root / name
                    if (
                        path.is_symlink()
                        or path.is_dir()
                        and any(p.is_symlink() for p in path.rglob("*"))
                    ):
                        raise g["ForgeError"]("symlink replay input: " + name)
                    if path.is_dir():
                        shutil.copytree(path, target / name)
                    elif path.is_file():
                        shutil.copy2(path, target / name)
                run = g["Run"](target)
                plan = run.plan
                g["set_sandbox_context"](run)
                module = g["assemble"](run.verified(), g["topo_order"](plan["chunks"]))
                import hashlib

                if hashlib.sha256(module.encode()).hexdigest() != expected[
                    "inputs"
                ].get("module.py"):
                    raise g["ForgeError"](
                        "saved chunks no longer reproduce the recorded module"
                    )
                sandbox_cfg = copy.deepcopy(cfg.get("sandbox", {}))
                image_id = expected.get("image", {}).get("id")
                if image_id:
                    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                        raise g["ForgeError"]("invalid image digest")
                    sandbox_cfg["image"] = image_id
                    sandbox_cfg.setdefault("toolchains", {}).setdefault(
                        g["TOOLCHAIN"], {}
                    )["image"] = image_id
                    sandbox_cfg.setdefault("profiles", {}).setdefault(
                        g["TOOLCHAIN"] + ":" + g.get("TOOLCHAIN_PROFILE", ""), {}
                    )["image"] = image_id
                elif not sandbox_cfg.get("unsafe_local_exec"):
                    raise g["ForgeError"]("provenance has no image digest")
                sandbox = g["Sandbox"](sandbox_cfg)
                sandbox.replay = (
                    True  # consume saved dependencies; never launch download stages
                )
                for chunk in plan["chunks"]:
                    if chunk.get("kind", "code") == "code":
                        ok, out = sandbox.run(
                            g["verify_files"](module, chunk["verify"]), "verify.py"
                        )
                        if not ok:
                            return dict(
                                verified=False,
                                error="saved verification failed",
                                chunk=chunk["id"],
                                diagnostic=out[-2000:],
                            )
                g["build_outputs"](run, sandbox, module)
                return provenance.compare(
                    expected,
                    target,
                    image_tolerance=image_tolerance,
                    mesh_tolerance=mesh_tolerance,
                )
            finally:
                g["set_sandbox_context"](source)

    def toolchain_health(self, cfg, names=(), smoke=False):
        g = self.g
        known = set(g["TOOLCHAINS"]) | set(g.get("PROFILES", {}))
        names = list(names) or sorted(known)
        if set(names) - known:
            raise g["ForgeError"](
                "unknown toolchain or profile: " + ", ".join(sorted(set(names) - known))
            )
        old, profiles = (g["TOOLCHAIN"], g.get("TOOLCHAIN_PROFILE", "")), {}
        try:
            for name in names:
                g["set_toolchain"](*name.split(":", 1))
                profiles[name] = g["Sandbox"](cfg.get("sandbox", {})).profile()

            def check(name):
                stream = io.StringIO()
                try:
                    with contextlib.redirect_stdout(stream):
                        result = g["cmd_selftest"](cfg, *name.split(":", 1))
                    return ("passed" if result == 0 else "failed"), stream.getvalue()
                except g["SandboxError"] as error:
                    return "unavailable", str(error)
                except (g["ForgeError"], OSError) as error:
                    return "failed", str(error)

            cache_names = {
                "node": ["npm"],
                "go": ["gomod"],
                "rust": ["cargo"],
                "wasm": ["cargo"],
                "python-app": ["wheels"],
                "desktop": ["wheels"],
                "kotlin-jvm": ["maven"],
                "android": ["maven"],
            }
            caches = {
                n: [
                    g["cache_dir"](cfg) / p
                    for p in cache_names.get(n.split(":")[0], [])
                ]
                for n in names
            }
            data = health.report(
                self.home / "toolchain-health.json",
                profiles,
                smoke=check if smoke else None,
                caches=caches,
            )
            control.atomic(self.home / "toolchain-health.json", data)
            return data
        finally:
            g["set_toolchain"](*old)


def install(g):
    ops = Operations(g)
    g.update(
        checkpoint=ops.checkpoint,
        record_provenance=ops.record,
        replay_run=ops.replay,
        toolchain_health=ops.toolchain_health,
        forge_health=health,
        forge_control=control,
    )

    class CheckpointStop(g["ForgeError"]):
        pass

    g["CheckpointStop"] = CheckpointStop
    original_execute = g["execute"]

    def execute(run, cfg):
        try:
            return original_execute(run, cfg)
        except CheckpointStop as error:
            phase = "paused" if str(error) == "pause" else "cancelled"
            with run.edit() as state:
                state.update(phase=phase, checkpoint_at=health.now())
            if (run.root / "plan.json").is_file():
                try:
                    ops.record(run, cfg, "", checkpoint_phase=phase)
                except (OSError, ValueError, g["ForgeError"]) as error:
                    g["log"](
                        "checkpoint saved; provenance unavailable: " + str(error),
                        "WARN",
                    )
            g["STATUS"].set(active=False, phase=phase, error=None)
            return None

    g["execute"] = execute
    original_inner = g["_execute"]

    def inner(run, cfg):
        ops.checkpoint(run)
        recovery = g["recover_services"](cfg, True)
        if recovery:
            g["STATUS"].set(service_recovery=recovery)
        if any(row.get("state") == "failed" for row in recovery):
            raise g["ForgeError"](
                "service recovery failed; inspect forge recover before resuming"
            )
        return original_inner(run, cfg)

    g["_execute"] = inner
    original_commit = g["commit"]

    def commit(run, cfg, report):
        ops.checkpoint(run)
        ops.record(run, cfg, (run.root / "module.py").read_text())
        return original_commit(run, cfg, report)

    g["commit"] = commit
    original_chat, original_init = g["LLM"].chat, g["LLM"].__init__

    def init(self, name, cfg):
        original_init(self, name, cfg)
        self.machine = cfg.get("machine")

    def chat(self, *args, **kwargs):
        lock = g["gpu_lock"](self.machine) if self.machine else None
        with lock if lock and not lock.owned() else contextlib.nullcontext():
            return original_chat(self, *args, **kwargs)

    g["LLM"].__init__, g["LLM"].chat = init, chat
    locks = {}

    def gpu_lock(machine):
        key = (str(ops.home), machine)
        with g["_GPU_LOCKS_GUARD"]:
            if key not in locks:

                def waiting(name, ahead):
                    g["STATUS"].set(
                        gpu_waiting={
                            name: dict(
                                reason="waiting for GPU" if ahead else "acquired",
                                ahead=ahead,
                            )
                        }
                    )

                locks[key] = control.GpuLock(ops.home / "gpu-queue", machine, waiting)
            return locks[key]

    g["gpu_lock"] = gpu_lock
    original_status = g["run_status"]

    def status(*args, **kwargs):
        return {
            **original_status(*args, **kwargs),
            "toolchain_health": health.cached(ops.home / "toolchain-health.json"),
        }

    g["run_status"] = status
    original_selftest = g["cmd_selftest"]

    def selftest(cfg, name, profile=""):
        old = (g["TOOLCHAIN"], g.get("TOOLCHAIN_PROFILE", ""))
        g["set_toolchain"](name, profile)
        sandbox = g["Sandbox"](cfg.get("sandbox", {}))
        try:
            if profile:
                fixture, steps = g["profile_smoke"](name, profile)
                ok, output = sandbox.run(
                    {
                        "module.py": g["files_to_python"](fixture),
                        "selftest.py": g["SELFTEST_SCRIPT"] % steps,
                    },
                    "selftest.py",
                    max_out=60000,
                )
                print(output)
                code = 0 if ok else 1
            else:
                code = original_selftest(cfg, name)
                output = "selftest exit " + str(code)
            control.atomic(
                ops.home / "toolchain-health.json",
                health.record_smoke(
                    ops.home / "toolchain-health.json",
                    name + (":" + profile if profile else ""),
                    sandbox.profile(),
                    "passed" if code == 0 else "failed",
                    output,
                ),
            )
            return code
        finally:
            g["set_toolchain"](*old)

    g["cmd_selftest"] = selftest
    original_main = g["main"]

    def main(argv=None):
        import sys

        argv = list(sys.argv[1:] if argv is None else argv)
        index = 0
        while index < len(argv):
            if argv[index] == "--config":
                index += 2
            elif argv[index].startswith("--config="):
                index += 1
            else:
                break
        command = argv[index] if index < len(argv) else None
        has_flag = lambda flag: any(
            x == flag or x.startswith(flag + "=") for x in argv[index + 1 :]
        )
        new = (
            command in ("replay", "control", "recover")
            or command == "check"
            and has_flag("--toolchains")
            or command == "selftest"
            and has_flag("--profile")
        )
        if not new:
            if command == "resume":
                i = index
                if len(argv) > i + 1 and control.requested(
                    Path(argv[i + 1]).expanduser()
                ) in ("pause", "cancel"):
                    control.request(Path(argv[i + 1]).expanduser(), "resume")
            return original_main(argv)
        parser = argparse.ArgumentParser(prog="forge")
        parser.add_argument("--config", type=Path, default=g["DEFAULT_CONFIG"])
        sub = parser.add_subparsers(dest="cmd", required=True)
        replay = sub.add_parser("replay")
        replay.add_argument("run_dir", type=Path)
        replay.add_argument("--verify", action="store_true")
        replay.add_argument("--image-tolerance", type=float, default=0)
        replay.add_argument("--mesh-tolerance", type=float, default=0)
        ctl = sub.add_parser("control")
        ctl.add_argument("run_dir", type=Path)
        ctl.add_argument("action", choices=["pause", "cancel"])
        recover = sub.add_parser("recover")
        recover.add_argument("--apply", action="store_true")
        check = sub.add_parser("check")
        check.add_argument("--toolchains", nargs="*", default=[])
        check.add_argument("--smoke", action="store_true")
        check.add_argument("--json", action="store_true")
        test = sub.add_parser("selftest")
        test.add_argument("toolchain")
        test.add_argument("--profile", required=True)
        args = parser.parse_args(argv)
        try:
            cfg = g["load_config"](args.config) if args.config.exists() else {}
            if args.cmd == "control":
                data = control.request(args.run_dir.expanduser().resolve(), args.action)
            elif args.cmd == "recover":
                data = dict(
                    gpu_queues=control.reconcile(ops.home / "gpu-queue"),
                    services=g["recover_services"](cfg, args.apply),
                )
            elif args.cmd == "replay":
                data = ops.replay(
                    args.run_dir,
                    cfg,
                    args.verify,
                    args.image_tolerance,
                    args.mesh_tolerance,
                )
            elif args.cmd == "selftest":
                return selftest(cfg, args.toolchain, args.profile)
            else:
                data = ops.toolchain_health(cfg, args.toolchains, args.smoke)
            print(json.dumps(data, indent=2))
            return int(
                data.get("verified") is False
                or any(
                    r["smoke"]["status"] == "failed" for r in data.get("toolchains", [])
                )
                or any(r.get("state") == "failed" for r in data.get("services", []))
            )
        except (g["ForgeError"], OSError, ValueError, KeyError) as error:
            print(json.dumps(dict(error=str(error))))
            return 1

    g["main"] = main
