"""Offline application and engineering helpers, bound to forge_game's namespace."""

import json
import math
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path


class Helpers:
    def __init__(self, namespace):
        self.g = namespace

    def project(self):
        return self.g["project_dir"]()

    def path(self, name):
        root = self.project().resolve()
        result = (root / name).resolve()
        if not result.is_relative_to(root):
            raise ValueError("path must stay inside the project")
        return result

    def run(self, command, **kwargs):
        result = self.g["run"](command, cwd=self.project(), **kwargs)
        result.diagnostics = [
            line.strip()
            for line in result.out.splitlines()
            if re.search(
                r"(^e: |\b(?:error|ERROR|FATAL|Error):|syntax error|Parser error)", line
            )
        ]
        return result

    def python_env(self):
        root = self.project() / ".forge-venv"
        python = root / "bin/python"
        if not python.exists():
            self.run(
                [sys.executable, "-m", "venv", "--system-site-packages", str(root)],
                check=True,
            )
            import site

            next((root / "lib").glob("python*/site-packages")).joinpath(
                "forge-tools.pth"
            ).write_text("\n".join(site.getsitepackages()))
            lock = self.g["DEPS"] / "requirements.lock"
            if not lock.is_file():
                lock = self.project() / "requirements.lock"
            if lock.is_file():
                result = self.run(
                    [
                        str(python),
                        "-m",
                        "pip",
                        "install",
                        "--no-index",
                        "--only-binary",
                        ":all:",
                        "--require-hashes",
                        "--find-links",
                        str(self.g["DEPS"] / "wheels"),
                        "-r",
                        str(lock),
                    ]
                )
                if not result.ok:
                    shutil.rmtree(root)
                    raise AssertionError(
                        "offline wheels missing or invalid:\n" + result.out[-3000:]
                    )
        return str(python)

    def pytest_project(self, args=None):
        reports = self.project() / ".forge-reports"
        shutil.rmtree(reports, ignore_errors=True)
        reports.mkdir()
        result = self.run(
            [
                self.python_env(),
                "-m",
                "pytest",
                "-q",
                "--import-mode=importlib",
                "--junitxml",
                str(reports / "tests.xml"),
                *(args or ["tests"]),
            ],
            timeout=600,
        )
        return self.g["Checks"](
            self.g["junit_results"](reports),
            errors=[] if result.ok else [result.out[-3000:]],
            out=result.out,
        )

    def python_typecheck(self, args=None):
        return self.run(
            [
                self.python_env(),
                "-m",
                "mypy",
                "--ignore-missing-imports",
                *(args or ["."]),
            ]
        )

    def python_build(self):
        return self.run(
            [self.python_env(), "-m", "build", "--no-isolation"], timeout=600
        )

    def service_request(self, command, path="/", env=None, method="GET", data=None):
        import signal
        import socket
        import subprocess
        import tempfile
        import time
        import urllib.request
        import urllib.error

        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("local absolute request path required")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(
                command,
                cwd=self.project(),
                stdout=log,
                stderr=log,
                env={**os.environ, **(env or {}), "PORT": str(port)},
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        if proc.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            raise AssertionError(
                                "service did not become ready: "
                                + log.read(4000).decode(errors="replace")
                            )
                        time.sleep(0.05)
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}" + path,
                    method=method,
                    data=data.encode() if isinstance(data, str) else data,
                )
                try:
                    response = urllib.request.urlopen(req, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return dict(
                        status=response.status,
                        body=response.read(2**20).decode(),
                        headers=dict(response.headers),
                    )
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                except ProcessLookupError:
                    proc.wait(timeout=5)

    def python_request(self, module, factory="app", path="/", **kwargs):
        # Server wrapper runs in the same offline venv as the application's tests.
        script = self.project() / ".forge-service.py"
        script.write_text(
            """import importlib,inspect,os
from wsgiref.simple_server import make_server
import uvicorn
app=getattr(importlib.import_module(%r),%r)
if callable(app) and not inspect.signature(app).parameters:app=app()
if inspect.iscoroutinefunction(app) or inspect.iscoroutinefunction(getattr(app,'__call__',None)):
 uvicorn.run(app,host='127.0.0.1',port=int(os.environ['PORT']),log_level='error')
else:make_server('127.0.0.1',int(os.environ['PORT']),app).serve_forever()
"""
            % (module, factory)
        )
        return self.service_request([self.python_env(), str(script)], path, **kwargs)

    def database_env(self, db="app"):
        srv = self.g["pg"]()
        return dict(PGHOST=str(srv.sock), PGUSER=srv.user, PGDATABASE=db, PGPORT="5432")

    def wasi_build(self, source, output="app.wasm"):
        return self.run(
            [
                "clang-19",
                "--target=wasm32-wasi",
                "--sysroot=/usr",
                "-Wl,-z,stack-size=1048576",
                str(self.path(source)),
                "-o",
                str(self.path(output)),
            ],
            timeout=300,
        )

    def wasi_rust(self, source, output="app.wasm"):
        return self.run(
            [
                "rustc",
                "--target",
                "wasm32-wasip1",
                "-C",
                "opt-level=2",
                str(self.path(source)),
                "-o",
                str(self.path(output)),
            ],
            timeout=300,
        )

    def wasi_run(self, path, args=None, preopens=None, fuel=10000000):
        grants = {
            str(guest): str(self.path(host)) for guest, host in (preopens or {}).items()
        }
        return self.run(
            [
                sys.executable,
                "/opt/forge-tools/wasi_runner.py",
                str(self.path(path)),
                json.dumps(args or []),
                json.dumps(grants),
                str(int(fuel)),
            ],
            timeout=120,
        )

    def gradle(self, *args, android=False):
        cache = "android-gradle" if android else "gradle"
        home = self.project() / ("." + cache + "-home")
        if not home.exists():
            shutil.copytree("/opt/forge-" + cache + "-home", home)
        java_home = self.project() / ".java-home"
        java_home.mkdir(exist_ok=True)
        return self.run(
            [
                "gradle",
                "--offline",
                "--no-daemon",
                "--console=plain",
                "--max-workers=2",
                "--gradle-user-home",
                str(home),
                *args,
            ],
            timeout=1500 if android else 900,
            env={
                "GRADLE_OPTS": "-Xmx768m -Dorg.gradle.daemon=false",
                "JAVA_TOOL_OPTIONS": "-Duser.home=" + str(java_home),
                "ANDROID_USER_HOME": str(java_home / ".android"),
                "ANDROID_PREFS_ROOT": str(java_home),
            },
        )

    def kotlin_build(self):
        return self.gradle("assemble")

    def kotlin_run(self, args=None):
        import shlex

        return self.gradle("run", *(["--args=" + shlex.join(args)] if args else []))

    def kotlin_test(self):
        reports = self.project() / "build/test-results"
        shutil.rmtree(reports, ignore_errors=True)
        result = self.gradle("test", "--rerun-tasks")
        return self.g["Checks"](
            self.g["junit_results"](reports),
            errors=[] if result.ok else [result.out[-3000:]],
            out=result.out,
        )

    def android_build(self):
        return self.gradle("assembleDebug", android=True)

    def android_test(self):
        for reports in self.project().glob("*/build/test-results"):
            shutil.rmtree(reports)
        result = self.gradle("testDebugUnitTest", "--rerun-tasks", android=True)
        checks = [
            c
            for r in self.project().glob("*/build/test-results")
            for c in self.g["junit_results"](r)
        ]
        return self.g["Checks"](
            checks, errors=[] if result.ok else [result.out[-3000:]], out=result.out
        )

    def android_apks(self):
        paths = sorted(self.project().glob("*/build/outputs/apk/**/*.apk"))
        for path in paths:
            with zipfile.ZipFile(path) as archive:
                assert "AndroidManifest.xml" in archive.namelist(), "invalid APK"
        return paths

    def hdl_sources(self, sources):
        return [
            str(self.path(s))
            for s in (
                sources
                or sorted(
                    str(p.relative_to(self.project()))
                    for p in self.project().rglob("*")
                    if p.suffix in (".v", ".sv")
                )
            )
        ]

    def hdl_sim(self, top, sources=None):
        output = self.project() / ".forge-sim.vvp"
        result = self.run(
            [
                "iverilog",
                "-g2012",
                "-s",
                top,
                "-o",
                str(output),
                *self.hdl_sources(sources),
            ]
        )
        return self.run(["vvp", str(output)], timeout=120) if result.ok else result

    def hdl_lint(self, sources=None):
        return self.run(
            [
                "verilator",
                "--lint-only",
                "--timing",
                "-Wno-fatal",
                *self.hdl_sources(sources),
            ]
        )

    def hdl_synth(self, top, sources=None):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
            raise ValueError("invalid HDL top")
        script = "read_verilog -sv " + " ".join(
            json.dumps(s) for s in self.hdl_sources(sources)
        )
        return self.run(
            [
                "yosys",
                "-p",
                script + f"; hierarchy -check -top {top}; synth -top {top}; stat",
            ],
            timeout=300,
        )

    def cad_export(self, source="model.scad", output="model.stl"):
        path = self.path(output)
        path.unlink(missing_ok=True)
        result = self.run(
            ["openscad", "-o", str(path), str(self.path(source))], timeout=300
        )
        return (
            result
            if not result.ok or path.is_file()
            else self.g["Result"](1, result.out + "\nno mesh exported")
        )

    def cad_render(self, source="model.scad", output="preview.png"):
        return self.run(
            [
                "xvfb-run",
                "-a",
                "openscad",
                "--render",
                "--autocenter",
                "--viewall",
                "--imgsize=640,480",
                "-o",
                str(self.path(output)),
                str(self.path(source)),
            ],
            timeout=300,
            env={"LIBGL_ALWAYS_SOFTWARE": "1"},
        )

    def cad_mesh(self, path):
        import trimesh

        mesh = trimesh.load_mesh(self.path(path), force="mesh")
        return dict(
            bounds=mesh.bounds.tolist(),
            size=mesh.extents.tolist(),
            volume=float(mesh.volume),
            watertight=bool(mesh.is_watertight),
            faces=len(mesh.faces),
        )

    def helm_render(self, chart="chart", values=None):
        return self.run(
            [
                "helm",
                "template",
                "forge",
                str(self.path(chart)),
                *(["--values", str(self.path(values))] if values else []),
            ]
        )

    def kustomize_render(self, path="."):
        return self.run(
            [
                "kustomize",
                "build",
                str(self.path(path)),
                "--load-restrictor=LoadRestrictionsRootOnly",
            ]
        )

    def infra_validate(self, text):
        path = self.project() / ".forge-manifest.yaml"
        path.write_text(text)
        return self.run(
            [
                "kubeconform",
                "-strict",
                "-summary",
                "-kubernetes-version",
                "1.32.0",
                "-schema-location",
                "/opt/forge-schemas/{{.ResourceKind}}.json",
                str(path),
            ]
        )

    def infra_policy(self, text):
        import yaml

        checks = []
        for doc in yaml.safe_load_all(text):
            if not doc or doc.get("kind") not in (
                "Pod",
                "Deployment",
                "StatefulSet",
                "DaemonSet",
                "Job",
            ):
                continue
            spec = doc.get("spec", {})
            pod = (
                spec
                if doc["kind"] == "Pod"
                else spec.get("template", {}).get("spec", {})
            )
            for c in pod.get("containers", []):
                sc = c.get("securityContext", {})
                image = c.get("image", "")
                facts = {
                    "pinned image": bool(
                        "@sha256:" in image
                        or ":" in image.rsplit("/", 1)[-1]
                        and not image.endswith(":latest")
                    ),
                    "nonroot": sc.get(
                        "runAsNonRoot",
                        pod.get("securityContext", {}).get("runAsNonRoot"),
                    )
                    is True,
                    "no privilege escalation": sc.get("allowPrivilegeEscalation")
                    is False,
                    "readonly root": sc.get("readOnlyRootFilesystem") is True,
                    "no privileged mode": not sc.get("privileged", False),
                    "resources": bool(
                        c.get("resources", {}).get("requests")
                        and c.get("resources", {}).get("limits")
                    ),
                    "ports": all(
                        type(p.get("containerPort")) is int
                        and 1 <= p["containerPort"] <= 65535
                        for p in c.get("ports", [])
                    ),
                }
                checks.extend(
                    dict(
                        name=doc["metadata"]["name"] + "/" + c["name"] + ": " + k, ok=v
                    )
                    for k, v in facts.items()
                )
        return self.g["Checks"](checks)

    def ros_env(self):
        return dict(
            ROS_LOCALHOST_ONLY="1",
            ROS_DOMAIN_ID=str(os.getpid() % 180 + 20),
            ROS_LOG_DIR=str(self.project() / ".ros-logs"),
        )

    def ros_build(self):
        return self.run(
            [
                "colcon",
                "--log-base",
                str(self.project() / ".colcon-logs"),
                "build",
                "--base-paths",
                str(self.project()),
            ],
            timeout=600,
            env=self.ros_env(),
        )

    def ros_test(self):
        return self.pytest_project()

    def ros_launch(self, path):
        return self.run(
            ["ros2", "launch", str(self.path(path))], timeout=60, env=self.ros_env()
        )

    def ros_replay(self, module, recording, function="transform"):
        samples = json.loads(self.path(recording).read_text())
        if (
            not isinstance(samples, list)
            or not samples
            or len(samples) > 10000
            or not all(type(v) in (int, float) and math.isfinite(v) for v in samples)
        ):
            raise ValueError(
                "recording must be a bounded nonempty finite numeric array"
            )
        script = """import importlib,json,sys,time
import rclpy
from std_msgs.msg import Float64
fn=getattr(importlib.import_module(sys.argv[1]),sys.argv[2]);samples=json.loads(sys.argv[3]);received=[];index=0
rclpy.init();node=rclpy.create_node('forge_recording');pub=node.create_publisher(Float64,'samples',10)
sub=node.create_subscription(Float64,'samples',lambda msg:received.append(fn(msg.data)),10)
deadline=time.monotonic()+15
try:
 while len(received)<len(samples) and time.monotonic()<deadline:
  if pub.get_subscription_count()>0 and index==len(received):
   msg=Float64();msg.data=float(samples[index]);pub.publish(msg);index+=1
  rclpy.spin_once(node,timeout_sec=.02)
 if len(received)!=len(samples):raise RuntimeError('ROS replay timeout')
 print('FORGE_ROS='+json.dumps(received))
finally:node.destroy_node();rclpy.shutdown()
"""
        result = self.run(
            [sys.executable, "-c", script, module, function, json.dumps(samples)],
            timeout=30,
            env=self.ros_env(),
        )
        assert result.ok, result.out
        return json.loads(
            next(
                line[10:]
                for line in result.out.splitlines()
                if line.startswith("FORGE_ROS=")
            )
        )

    def desktop_test(self):
        return self.pytest_project()

    def desktop_build(self, entry="app.py"):
        return self.run(
            [
                self.python_env(),
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onedir",
                "--name",
                "app",
                "--distpath",
                str(self.project() / ".desktop-dist"),
                "--workpath",
                str(self.project() / ".desktop-build"),
                str(self.path(entry)),
            ],
            timeout=900,
        )

    def desktop_run(self, args=None):
        return self.run(
            [str(self.project() / ".desktop-dist/app/app"), *(args or [])],
            timeout=60,
            env={"QT_QPA_PLATFORM": "offscreen"},
        )

    def desktop_capture(
        self, module="app", factory="make_window", output="desktop.png"
    ):
        script = "import importlib,sys;from PySide6.QtWidgets import QApplication;app=QApplication([]);w=getattr(importlib.import_module(sys.argv[1]),sys.argv[2])();w.show();app.processEvents();assert w.grab().save(sys.argv[3]);w.close()"
        result = self.run(
            [self.python_env(), "-c", script, module, factory, str(self.path(output))],
            env={"QT_QPA_PLATFORM": "offscreen"},
        )
        assert result.ok, result.out
        return self.path(output)

    def frontend_build(self):
        self.g["node_modules"]()
        return self.run(
            [str(self.project() / "node_modules/.bin/vite"), "build"],
            env=self.g["_node_env"](),
            timeout=300,
        )

    def browser_check(self, folder="dist", actions=None):
        import http.server
        import threading
        import forge_ux
        from playwright.sync_api import sync_playwright

        root = self.path(folder)
        if not (root / "index.html").is_file():
            raise ValueError("build index.html first")

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(root), **kw)

            def log_message(self, *a):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    executable_path=os.environ.get("FORGE_CHROMIUM"),
                    chromium_sandbox=False,
                    args=["--disable-dev-shm-usage"],
                )
                try:
                    page = browser.new_page(
                        viewport={"width": 1280, "height": 800}, reduced_motion="reduce"
                    )
                    page.goto(
                        f"http://127.0.0.1:{server.server_port}",
                        wait_until="networkidle",
                    )
                    for action in actions or []:
                        loc = page.get_by_role(
                            action["role"], name=action["name"], exact=True
                        )
                        loc.focus()
                        loc.press(action.get("key", "Enter"))
                        if "text" in action:
                            page.get_by_text(action["text"], exact=True).wait_for()
                    report = dict(
                        violations=forge_ux.run_axe(page),
                        keyboard=forge_ux.keyboard_walk(page),
                    )
                    page.screenshot(path=str(self.project() / "browser-desktop.png"))
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=str(self.project() / "browser-mobile.png"))
                    report["mobile_overflow"] = page.evaluate(forge_ux.FACTS_JS)[
                        "scroll"
                    ]
                    (self.project() / "browser-report.json").write_text(
                        json.dumps(report, indent=2)
                    )
                    return report
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def frontend_package(self, out):
        source = self.path("dist")
        if not (source / "index.html").is_file() or any(
            p.is_symlink() for p in source.rglob("*")
        ):
            raise ValueError("static build must exist and contain no symlinks")
        shutil.copytree(source, out, dirs_exist_ok=True)
        return [str(p.relative_to(out)) for p in Path(out).rglob("*") if p.is_file()]

    def extra_package(self, toolchain, out):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        if toolchain == "python-app":
            result = self.python_build()
            assert result.ok, result.out
            candidates = list((self.project() / "dist").glob("*"))
        elif toolchain == "wasm":
            candidates = list(self.project().glob("*.wasm"))
            if not candidates:
                for p in self.project().glob("*.c"):
                    assert self.wasi_build(str(p.relative_to(self.project()))).ok
                for p in self.project().glob("*.rs"):
                    assert self.wasi_rust(str(p.relative_to(self.project()))).ok
                candidates = list(self.project().glob("*.wasm"))
        elif toolchain == "kotlin-jvm":
            result = self.gradle("installDist")
            assert result.ok, result.out
            candidates = list((self.project() / "build/install").glob("*"))
        elif toolchain == "android":
            result = self.android_build()
            assert result.ok, result.out
            candidates = self.android_apks()
        elif toolchain == "desktop":
            result = self.desktop_build()
            assert result.ok, result.out
            candidates = [self.project() / ".desktop-dist/app"]
        elif toolchain == "ros2":
            result = self.ros_build()
            assert result.ok, result.out
            candidates = [self.project() / "install"]
        elif toolchain == "cad":
            result = self.cad_export()
            assert result.ok, result.out
            candidates = [self.project() / "model.stl"]
        elif toolchain == "hdl":
            candidates = [
                p
                for p in self.project().rglob("*")
                if p.suffix in (".sv", ".v", ".vcd")
            ]
        elif toolchain == "infra":
            result = (
                self.helm_render()
                if (self.project() / "chart").is_dir()
                else self.kustomize_render()
            )
            assert result.ok, result.out
            valid = self.infra_validate(result.out)
            assert valid.ok, valid.out
            (out / "manifests.yaml").write_text(result.out)
            (out / "CHANGE_REPORT.md").write_text(
                "Rendered and schema-validated offline. No cluster or live diff was requested.\n"
            )
            return ["manifests.yaml", "CHANGE_REPORT.md"]
        else:
            return []
        made = []
        for p in candidates:
            if (
                p.is_symlink()
                or p.is_dir()
                and any(
                    c.is_symlink() and not c.resolve().is_relative_to(p.resolve())
                    for c in p.rglob("*")
                )
            ):
                raise ValueError("artifact symlink leaves package tree")
            target = out / p.name
            if p.is_dir():
                shutil.copytree(p, target, dirs_exist_ok=True)
            else:
                shutil.copy2(p, target)
            made.append(p.name)
        assert made, "no " + toolchain + " artifacts produced"
        return made


def install(namespace):
    obj = Helpers(namespace)
    return {
        name: getattr(obj, name)
        for name in dir(Helpers)
        if not name.startswith("_")
        and name
        not in (
            "project",
            "path",
            "run",
            "python_env",
            "hdl_sources",
            "ros_env",
            "gradle",
        )
    }
