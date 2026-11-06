#!/usr/bin/env python3
"""
lab-dashboard.py - every machine in the lab, and forge, on one page.

Runs on Gemini. Every 2 seconds it asks each machine's lab-agent for its
readings, reads forge's status file, works out what each machine is doing
(coding, rendering, planning, 3D, chatting, idle) and keeps an hour of
history. The page at http://<gemini>:8095 redraws itself from /api/state;
open it in a browser, or add it to the home screen on the iPad or iPhone
(it runs full screen as a web app).

Tap a machine for its detail page: dials, an hour of graphs, its model
servers and services, and its controls - restart the model or image
server, clear the VRAM, stop or start Strata or the shape server. When a
forge run has stopped, the forge card offers to resume it or to rebuild
its failed chunks with new tests. Every control is a slider: drag it all
the way across to confirm, so a stray tap does nothing.

Controls need `control_token` here and in each machine's lab-agent.toml
(`install-lab-dashboard.sh controls` sets both); without it the page is
read-only, as before. They are accepted only from the LAN, and with
`pin` set, only with the PIN (asked once per device; five wrong PINs from
one address lock it out for 15 minutes). A control request must name this
server by an IP address, a one-word or .local/.lan/.home.arpa name, or a
name in `allowed_hosts` - a web page cannot then reach the controls by
pointing its own domain name at this server (DNS rebinding). Standard
library only. Settings: ~/.config/lab-dashboard.toml (see the installer).
"""

from __future__ import annotations

import argparse
import collections
import hmac
import ipaddress
import json
import os
import datetime as dt
import platform
import socket
import shutil
import struct
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CONFIG = Path(os.environ.get("LAB_DASHBOARD_CONFIG") or Path.home() / ".config" / "lab-dashboard.toml")
DEFAULTS = {
    "port": 8095,
    "token": "",                                   # sent to the agents if they need one
    "control_token": "",                           # lets the page use the agents' controls
    "pin": "",                                     # optional PIN for every control
    "lan": "192.168.50.0/24",                      # controls only from here (and this machine)
    "allowed_hosts": [],                           # more names controls may use ("*": any)
    "pin_attempts": 5,                             # wrong PINs per address before a lockout
    "pin_lockout_seconds": 900,
    "forge": "~/forge/forge.py",
    "forge_config": "~/forge/config.toml",
    "forge_status": "~/forge/runs/.forge-status.json",
    "nodes": [
        {"name": "Gemini", "host": "127.0.0.1", "lan": "192.168.50.96",
         "role": "Planner · reviewer · chat · Home Assistant"},
        {"name": "Vulcan", "host": "192.168.50.151", "role": "Principal coder (Strata) · 3D models"},
        {"name": "Apollo", "host": "192.168.50.8", "role": "Images (full steps) · coder"},
        {"name": "Mercury", "host": "192.168.50.129", "role": "Images (Lightning) · triage"},
        {"name": "Hades", "host": "hades", "role": "Gaming · spare GPU", "optional": True},
        {"name": "Metis", "host": "192.168.50.126", "role": "Backups · caches · git", "optional": True},
    ],
    "agent_port": 9101,
    "history_seconds": 3600,
}
POLL = 2.0
CARD_POINTS = 300          # the overview's sparklines: the last 10 minutes


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        with open(CONFIG, "rb") as fh:
            cfg.update(tomllib.load(fh))
    return cfg


def fetch(url: str, token: str, timeout: float = 1.8):
    req = urllib.request.Request(url, headers={"X-Lab-Token": token} if token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def mmss(secs: float) -> str:
    secs = int(max(0, secs))
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}" if secs >= 3600 else f"{secs // 60}:{secs % 60:02d}"


class Hub:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        n = int(cfg["history_seconds"] / POLL)
        self.hist = {nd["name"]: collections.deque(maxlen=n) for nd in cfg["nodes"]}
        self.nodes: dict[str, dict] = {}
        self.last_ok: dict[str, float] = {}
        self.forge: dict = {}
        self.forge_action: dict | None = None
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=len(cfg["nodes"]) + 1)
        threading.Thread(target=self.loop, daemon=True).start()

    # ------------------------------------------------------------ polling
    def agent_url(self, nd: dict, path: str) -> str:
        return f"http://{nd['host']}:{nd.get('port', self.cfg['agent_port'])}{path}"

    def poll_node(self, nd: dict):
        try:
            return nd["name"], fetch(self.agent_url(nd, "/api/node"), self.cfg["token"]), None
        except Exception as exc:
            return nd["name"], None, f"{type(exc).__name__}: {exc}"[:160]

    def read_forge(self) -> dict:
        path = Path(os.path.expanduser(self.cfg["forge_status"]))
        try:
            st = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            st = {"present": False}
        else:
            st["present"] = True
        try:
            health = json.loads(Path(os.path.expanduser(self.cfg.get("toolchain_health", "~/forge/toolchain-health.json"))).read_text())
            age = time.time() - dt.datetime.fromisoformat(health["observed_at"]).timestamp()
            host = {"hostname": socket.gethostname(), "architecture": platform.machine()}
            health.update(age_seconds=max(0, age), stale=age > 86400 or health.get("host") != host)
            for row in health.get("toolchains", []):
                smoke = row.setdefault("smoke", {})
                stamp = smoke.get("validated_at")
                smoke["stale"] = not stamp or time.time() - dt.datetime.fromisoformat(stamp).timestamp() > 86400
            st["toolchain_health"] = health
        except (OSError, ValueError, TypeError, KeyError):
            st["toolchain_health"] = {"toolchains": [], "stale": True}
        if not st["present"]:
            return st
        st["alive"] = bool(st.get("active")) and pid_alive(st.get("pid"))
        run_dir = Path(st.get("run_dir") or "")
        try:
            state = json.loads((run_dir / "state.json").read_text())
            plan = json.loads((run_dir / "plan.json").read_text())
            code = [c["id"] for c in plan.get("chunks", []) if c.get("kind", "code") == "code"]
            chunks = state.get("chunks", {})
            st["chunks_total"] = len(code)
            st["chunks_done"] = sum(1 for c in code if chunks.get(c, {}).get("status") == "verified")
            st["chunks_failed"] = [c for c in code if chunks.get(c, {}).get("status") == "failed"]
            st["images_total"] = sum(1 for c in plan.get("chunks", []) if c.get("kind") == "image")
            st["images_done"] = sum(1 for c in plan.get("chunks", []) if c.get("kind") == "image"
                                    and chunks.get(c["id"], {}).get("status") in ("rendered", "verified"))
            st["run_phase"] = state.get("review_stage") and f"review ({state['review_stage']})" or state.get("phase")
            st["claude_cost"] = state.get("claude_cost_usd")
            st["project"] = st.get("project") or plan.get("project")
            st["lessons"] = [l.get("text") for l in state.get("lessons") or []][-5:]
        except (OSError, json.JSONDecodeError):
            pass
        now = time.time()
        for kind in ("calls", "renders"):
            for v in (st.get(kind) or {}).values():
                v["for"] = mmss(now - v.get("since", now))
        if not st["alive"]:
            st["calls"], st["renders"] = {}, {}
        return st

    def loop(self):
        while True:
            t0 = time.monotonic()
            futures = [self.pool.submit(self.poll_node, nd) for nd in self.cfg["nodes"]]
            forge = self.read_forge()
            results = [f.result() for f in futures]
            now = time.time()
            with self.lock:
                self.forge = forge
                for name, data, err in results:
                    entry = self.nodes.setdefault(name, {})
                    if data is not None and "error" not in data:
                        entry.update(data=data, error=None)
                        self.last_ok[name] = now
                        self.hist[name].append(self.sample(data, now))
                    else:
                        entry["error"] = err or (data or {}).get("error")
                        if now - self.last_ok.get(name, 0) > 10:
                            entry["data"] = None
            time.sleep(max(0.2, POLL - (time.monotonic() - t0)))

    @staticmethod
    def sample(d: dict, now: float) -> list:
        """[time, GPU busy %, tok/s, hottest GPU °C, CPU %, VRAM %, GPU W,
        RAM %, CPU °C] - one point of a machine's history."""
        gpus = d.get("gpus") or []
        busy = max([g.get("busy") or 0 for g in gpus], default=None)
        hot = max([max((g.get("temps") or {}).values(), default=0) for g in gpus], default=None)
        tps = sum((l.get("tps") or 0) for l in d.get("llm") or [])
        h = d.get("host") or {}
        vt = sum(g.get("vram_total") or 0 for g in gpus)
        vu = sum(g.get("vram_used") or 0 for g in gpus)
        power = sum(g.get("power") or 0 for g in gpus) if gpus else None
        ram = 100 * h["ram_used"] / h["ram_total"] if h.get("ram_total") else None
        r = lambda x, k=1: None if x is None else round(x, k)
        return [round(now), r(busy, 0), r(tps), r(hot, 0), r(h.get("cpu_pct"), 0),
                r(100 * vu / vt if vt else None, 0), r(power, 0), r(ram, 0), r(h.get("cpu_temp"), 0)]

    # --------------------------------------------------------- activity
    def node_host_set(self, nd: dict) -> set:
        hosts = {nd["host"], nd.get("lan")}
        if nd["host"] in ("127.0.0.1", "localhost"):
            hosts |= {"127.0.0.1", "localhost"}
        return {h for h in hosts if h}

    def activity(self, nd: dict, entry: dict, forge: dict) -> dict:
        d = entry.get("data")
        if not d:
            return {"kind": "offline", "label": "Offline", "detail": entry.get("error") or ""}
        hosts = self.node_host_set(nd)
        on_me = lambda v: urlparse(v.get("url") or "").hostname in hosts
        renders = [v for v in (forge.get("renders") or {}).values() if on_me(v)]
        calls = [v for v in (forge.get("calls") or {}).values() if on_me(v)]
        if d.get("gaming"):
            return {"kind": "gaming", "label": "Gaming", "detail": d["gaming"].get("name") or ""}
        if renders:
            r = renders[0]
            if r.get("kind") == "shape":
                return {"kind": "shape", "label": "3D model", "detail": f"{r.get('asset')} · {r['for']}"}
            return {"kind": "render", "label": "Rendering", "detail": f"{r.get('asset')} {r.get('size', '')} · {r['for']}"}
        # forge's own record beats any guess from the GPU readings
        if any(s.get("state") == "rendering" for s in d.get("sd") or []) and not calls:
            return {"kind": "render", "label": "Rendering", "detail": "sd-server (not forge)"}
        if calls:
            c = calls[0]
            planner = c.get("llm") == "planner"
            triage = c.get("llm") == "triage"
            what = c.get("what") or ("planning" if planner else "")
            think = c.get("thinking")
            return {"kind": "plan" if planner or triage else "code",
                    "label": "Triage" if triage else ("Thinking" if think else "Working") if planner else "Coding",
                    "detail": f"{what} · {c['for']}" + (f" · thinking {think}" if isinstance(think, str) else
                                                         " · thinking" if think else "")}
        if any(p.get("name", "").startswith("python") and (p.get("vram") or 0) > 4 * 2**30
               for g in d.get("gpus") or [] for p in g.get("procs") or []) \
                and (d.get("services") or {}).get("forge-shape") == "active":
            return {"kind": "shape", "label": "3D model", "detail": "forge-shape holds the GPU"}
        llms = d.get("llm") or []
        if any(l.get("state") in ("generating", "prompt") for l in llms):
            return {"kind": "chat", "label": "Generating", "detail": "not forge (chat, Home Assistant, ...)"}
        if any(l.get("state") == "loading" for l in llms):
            return {"kind": "load", "label": "Loading model", "detail": ""}
        return {"kind": "idle", "label": "Idle", "detail": ""}

    def state(self) -> dict:
        with self.lock:
            forge = dict(self.forge)
            out = []
            for nd in self.cfg["nodes"]:
                entry = dict(self.nodes.get(nd["name"], {}))
                if nd.get("optional") and not entry.get("data") and not self.last_ok.get(nd["name"]):
                    out.append({**nd, "absent": True})
                    continue
                out.append({**nd, "data": entry.get("data"), "error": entry.get("error"),
                            "activity": self.activity(nd, entry, forge),
                            "history": list(self.hist[nd["name"]])[-CARD_POINTS:]})
            forge_action = dict(self.forge_action) if self.forge_action else None
        # Name the machine behind each forge call and render.
        for kind in ("calls", "renders"):
            for v in (forge.get(kind) or {}).values():
                host = urlparse(v.get("url") or "").hostname
                v["node"] = next((nd["name"] for nd in self.cfg["nodes"]
                                  if host in self.node_host_set(nd)), host)
        forge["action"] = forge_action
        forge["can_act"] = bool(self.cfg.get("control_token")) and shutil.which("python3") is not None
        return {"time": time.time(), "nodes": out, "forge": forge,
                "controls": bool(self.cfg.get("control_token")), "pin": bool(self.cfg.get("pin"))}

    def history(self, name: str) -> list:
        with self.lock:
            return list(self.hist.get(name, []))

    # ----------------------------------------------------------- controls
    def node_action(self, name: str, action: str) -> tuple[int, dict]:
        nd = next((n for n in self.cfg["nodes"] if n["name"] == name), None)
        if nd is None:
            return 404, {"ok": False, "message": f"no machine {name}"}
        with self.lock:
            data = (self.nodes.get(name) or {}).get("data") or {}
        allowed = {a["id"] for a in ((data.get("controls") or {}).get("available") or [])}
        if action not in allowed:
            return 400, {"ok": False, "message": f"{name} does not offer {action}"}
        req = urllib.request.Request(self.agent_url(nd, "/api/action"), method="POST",
                                     data=json.dumps({"action": action}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Lab-Control": self.cfg["control_token"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {"ok": False, "message": f"{name} answered HTTP {exc.code}"}
        except OSError as exc:
            return 502, {"ok": False, "message": f"{name} did not answer: {exc}"}

    def forge_command(self, action: str) -> tuple[int, dict]:
        """Resume the latest run, or rebuild its failed chunks with new tests
        (`forge retest`), as a transient user service so it outlives this
        page and logs to the journal."""
        f = self.read_forge()
        if not f.get("present") or not f.get("run_dir"):
            return 400, {"ok": False, "message": "forge has no run yet"}
        if action in ("pause", "cancel"):
            if not f.get("alive"):
                return 409, {"ok": False, "message": "forge has no active run"}
            try:
                result = subprocess.run(["python3", os.path.expanduser(self.cfg["forge"]), "control", f["run_dir"], action],
                                        capture_output=True, text=True, timeout=10)
                return (200 if result.returncode == 0 else 500), {"ok": result.returncode == 0,
                    "message": f"{action} requested; current chunks finish before the checkpoint" if result.returncode == 0 else result.stdout[-1000:]}
            except (OSError, subprocess.TimeoutExpired) as error:
                return 500, {"ok": False, "message": str(error)}
        if f.get("alive"):
            return 409, {"ok": False, "message": f"{f.get('run')} is still running"}
        forge = os.path.expanduser(self.cfg["forge"])
        conf = os.path.expanduser(self.cfg["forge_config"])
        run_dir = f["run_dir"]
        if action == "resume":
            args = [forge, "--config", conf, "resume", run_dir]
            label = f"resume {f.get('run')}"
        elif action == "retest_failed":
            failed = f.get("chunks_failed") or []
            if not failed:
                return 400, {"ok": False, "message": "no failed chunks in the latest run"}
            args = [forge, "--config", conf, "retest", run_dir, *failed]
            label = f"rebuild {', '.join(failed)} with new tests"
        else:
            return 400, {"ok": False, "message": f"unknown forge action {action}"}
        unit = f"forge-dashboard-{action.replace('_', '-')}-{int(time.time())}"
        ok, msg, where = False, "", ""
        if shutil.which("systemd-run"):
            try:
                p = subprocess.run(["systemd-run", "--user", "--collect", f"--unit={unit}",
                                    "python3", *args], capture_output=True, text=True, timeout=20)
                ok, msg = p.returncode == 0, (p.stderr or p.stdout).strip()
                where = f"journalctl --user -u {unit}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                msg = str(exc)
        if not ok:
            # No user session bus (or no systemd-run): a detached process,
            # its output in a log beside the run.
            logf = Path(run_dir) / f"dashboard-{action}.log"
            try:
                with open(logf, "ab") as fh:
                    subprocess.Popen(["python3", *args], start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=fh, stderr=fh)
                ok, where = True, str(logf)
            except OSError as exc:
                msg = f"{msg}; {exc}" if msg else str(exc)
        rec = {"action": action, "label": label, "ok": ok, "at": time.time(),
               "message": (f"started: {label} (log: {where})" if ok
                           else f"could not start {label}: {msg[:200]}")}
        with self.lock:
            self.forge_action = rec
        return (202 if ok else 500), {"ok": ok, "message": rec["message"]}


def lan_client(cfg: dict, addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    try:
        return ip in ipaddress.ip_network(cfg.get("lan") or "192.168.50.0/24", strict=False)
    except ValueError:
        return False


LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain",
                  ".localhost")


def control_host(cfg: dict, host_header: str) -> bool:
    """Whether a control request's Host names this server the way a LAN
    browser does. The Origin check alone compares Origin with Host, and a
    DNS-rebinding page sets both to its own domain; it cannot use an IP
    address, a one-word name or an mDNS/.lan name of yours."""
    host = (host_header or "").strip().lower()
    if not host:
        return False
    if host.startswith("["):                        # [v6]:port
        name = host[1:host.find("]")] if "]" in host else ""
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.rstrip(".")
    allowed = [str(h).strip().lower().rstrip(".") for h in cfg.get("allowed_hosts") or []]
    if "*" in allowed or name in allowed:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return bool(name) and ("." not in name or name.endswith(LOCAL_SUFFIXES))


class PinGuard:
    """Wrong PINs per client address: a four-digit PIN took under three
    seconds to find by trying them all."""

    def __init__(self, attempts: int, lockout: float):
        self.attempts, self.lockout = max(1, int(attempts)), float(lockout)
        self.lock = threading.Lock()
        self.failures: dict[str, list[float]] = {}

    def locked_for(self, addr: str) -> float:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.failures.get(addr, []) if now - t < self.lockout]
            if recent:
                self.failures[addr] = recent
            else:
                self.failures.pop(addr, None)
            if len(recent) < self.attempts:
                return 0.0
            return self.lockout - (now - recent[-self.attempts])

    def failed(self, addr: str) -> None:
        with self.lock:
            self.failures.setdefault(addr, []).append(time.monotonic())

    def passed(self, addr: str) -> None:
        with self.lock:
            self.failures.pop(addr, None)


def same_secret(given, secret: str) -> bool:
    """Constant-time comparison that also takes non-ASCII input:
    hmac.compare_digest raises TypeError on non-ASCII str."""
    return hmac.compare_digest(str(given).encode("utf-8", "surrogatepass"),
                               secret.encode("utf-8", "surrogatepass"))


def png_icon(size: int = 180) -> bytes:
    """The home-screen icon: a dark tile with four bars (the machines)."""
    bg, fg = (14, 17, 22), [(88, 166, 255), (188, 140, 255), (57, 197, 207), (63, 185, 80)]
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            c = bg
            for i, col in enumerate(fg):
                x0 = int(size * (0.18 + 0.17 * i))
                top = int(size * (0.70 - 0.12 * ((i * 7) % 4)))
                if x0 <= x < x0 + int(size * 0.11) and top <= y < int(size * 0.80):
                    c = col
            row += bytes(c)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


MANIFEST = json.dumps({"name": "Lab", "short_name": "Lab", "start_url": "/", "display": "standalone",
                       "background_color": "#0e1116", "theme_color": "#0e1116",
                       "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}]})


def make_server(cfg: dict):
    hub = Hub(cfg)
    icon = png_icon()
    guard = PinGuard(cfg.get("pin_attempts", 5), cfg.get("pin_lockout_seconds", 900))

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self.send(200, json.dumps(hub.state()).encode(), "application/json")
            if u.path == "/api/history":
                name = (parse_qs(u.query).get("node") or [""])[0]
                return self.send(200, json.dumps(hub.history(name)).encode(), "application/json")
            if u.path == "/manifest.webmanifest":
                return self.send(200, MANIFEST.encode(), "application/manifest+json", "max-age=86400")
            if u.path in ("/icon.png", "/apple-touch-icon.png", "/favicon.ico"):
                return self.send(200, icon, "image/png", "max-age=86400")
            if u.path == "/health":
                return self.send(200, b"ok", "text/plain")
            self.send_error(404)

        def do_POST(self):
            u = urlparse(self.path)
            if not lan_client(cfg, self.client_address[0]):
                return self.send(403, b'{"ok":false,"message":"controls only from the LAN"}',
                                 "application/json")
            if not cfg.get("control_token"):
                return self.send(403, b'{"ok":false,"message":"controls are off: no control_token"}',
                                 "application/json")
            # JSON requires a browser preflight, which this server does not
            # grant. Reject simple cross-site form/fetch requests explicitly.
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if self.headers.get("Sec-Fetch-Site") == "cross-site" or (origin is not None and origin not in (f"http://{host}", f"https://{host}")):
                return self.send(403, b'{"ok":false,"message":"cross-origin controls refused"}', "application/json")
            if not control_host(cfg, host):
                return self.send(403, json.dumps({
                    "ok": False, "message": f"controls refused for host {host[:80]!r}: open the page "
                    "by IP address or LAN name, or add the name to allowed_hosts"}).encode(),
                    "application/json")
            if self.headers.get_content_type() != "application/json":
                return self.send(415, b'{"ok":false,"message":"application/json required"}', "application/json")
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdecimal():
                return self.send(400, b'{"ok":false,"message":"invalid Content-Length"}', "application/json")
            n = int(lengths[0])
            if n > 4096:
                return self.send(413, b'{"ok":false,"message":"request over 4096 bytes"}', "application/json")
            try:
                raw = self.rfile.read(n)
                if len(raw) != n:
                    raise ValueError("incomplete body")
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected object")
            except (ValueError, OSError):
                return self.send(400, b'{"ok":false,"message":"bad request"}', "application/json")
            pin = str(cfg.get("pin") or "")
            if pin:
                addr = self.client_address[0]
                wait = guard.locked_for(addr)
                if wait > 0:
                    return self.send(429, json.dumps({
                        "ok": False, "message": f"too many wrong PINs - try again in "
                        f"{max(1, round(wait / 60))} min"}).encode(), "application/json")
                if not same_secret(body.get("pin", ""), pin):
                    guard.failed(addr)
                    return self.send(401, b'{"ok":false,"message":"wrong PIN","pin":true}',
                                     "application/json")
                guard.passed(addr)
            if u.path == "/api/action":
                code, out = hub.node_action(str(body.get("node", "")), str(body.get("action", "")))
            elif u.path == "/api/forge":
                code, out = hub.forge_command(str(body.get("action", "")))
            else:
                return self.send_error(404)
            self.send(code, json.dumps(out).encode(), "application/json")

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer(("0.0.0.0", int(cfg["port"])), Handler)


def serve(cfg: dict):
    with make_server(cfg) as server:
        server.serve_forever()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Lab">
<meta name="theme-color" content="#0e1116">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Lab</title>
<style>
:root{--bg:#0e1116;--card:#161b22;--card2:#1b2129;--line:#262d36;--ink:#e6edf3;--dim:#8b949e;--faint:#5c6570;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--code:#58a6ff;--render:#bc8cff;--plan:#39c5cf;--chat:#e3b341;
--game:#ff7b72;--shape:#f0883e;--idle:#5c6570;--llm:#58a6ff;--sd:#bc8cff;--desk:#8b949e;--other:#6e7681;
--track:#21262d;--safe-t:env(safe-area-inset-top,0px);--safe-b:env(safe-area-inset-bottom,0px)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-text-size-adjust:100%}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;
  padding:calc(10px + var(--safe-t)) 16px 10px;background:rgba(14,17,22,.92);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0;font-weight:650;letter-spacing:.2px}
#clock{color:var(--dim);font-variant-numeric:tabular-nums;margin-left:auto}
#stale{color:var(--bad);display:none;font-size:12px}
.back{display:none;background:none;border:1px solid var(--line);color:var(--ink);border-radius:8px;
  padding:6px 12px;font:inherit;cursor:pointer}
body.detail .back{display:inline-block}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;
  padding:12px 16px calc(16px + var(--safe-b))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;min-width:0}
.card.tap{cursor:pointer;transition:border-color .15s,transform .15s}
.card.tap:active{transform:scale(.99)}
@media (hover:hover){.card.tap:hover{border-color:#3a4552}}
.card.offline{opacity:.55}.wide{grid-column:1/-1}
.top{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
.name{font-size:17px;font-weight:650}.role{color:var(--dim);font-size:12px}
.pill{border-radius:999px;padding:3px 10px;font-weight:650;font-size:12px;white-space:nowrap;color:#0e1116}
.detailtxt{font-size:12px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;font-variant-numeric:tabular-nums}
.k{color:var(--dim);font-size:12px;margin-right:3px}.v{font-weight:600}
.bar{height:8px;border-radius:4px;background:var(--track);overflow:hidden;display:flex;margin-top:4px}
.bar>span{height:100%}
.sub{color:var(--dim);font-size:12px}.gname{font-weight:600}
.legend{display:flex;gap:10px;font-size:11px;color:var(--dim);margin-top:3px;flex-wrap:wrap}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}
.dials{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:8px}
.dial{display:flex;flex-direction:column;align-items:center;min-width:0}
.dial svg{width:100%;max-width:120px;height:auto;display:block}
.dial .lab{font-size:11px;color:var(--dim);margin-top:-4px;text-align:center;white-space:nowrap}
canvas.spark{width:100%;height:42px;display:block;margin-top:8px}
.svc{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--dim)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
.hot{color:var(--warn)}.vhot{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:3px 6px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500}
pre{margin:8px 0 0;font:12px/1.4 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word;
  max-height:240px;overflow:auto;background:#0b0e13;border-radius:8px;padding:8px}
.WARN{color:var(--warn)}.ERROR{color:var(--bad)}
.progress{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.more{color:var(--dim);font-size:12px;margin-top:8px;text-align:right}
/* detail page */
#detail{display:none;padding:12px 16px calc(24px + var(--safe-b))}
body.detail #grid{display:none}body.detail #detail{display:block}
.dhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.dhead .name{font-size:24px}
.bigdials{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;margin-top:12px}
.bigdials .dial{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 6px 12px}
.bigdials .dial svg{max-width:150px}
.bigdials .dial .lab{font-size:12px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px;margin-top:12px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.chart h3,.panel h3{margin:0;font-size:13px;font-weight:600;color:var(--dim);display:flex;justify-content:space-between}
.chart canvas{width:100%;height:120px;display:block;margin-top:6px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:12px}
.item{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
.item:first-of-type{border-top:0}
.tps{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
.tps small{font-size:12px;color:var(--dim);font-weight:400}
/* slide to confirm */
.ctl{margin-top:10px;display:flex;flex-direction:column;justify-content:flex-end}
.ctl .what{font-size:12px;color:var(--dim);margin:0 0 4px 2px}
.slider{position:relative;height:52px;border-radius:26px;background:var(--track);border:1px solid var(--line);
  overflow:hidden;user-select:none;-webkit-user-select:none;touch-action:none}
.slider .fill{position:absolute;inset:0 auto 0 0;width:52px;border-radius:26px;background:rgba(88,166,255,.18)}
.slider .label{position:absolute;inset:0;display:block;line-height:52px;text-align:center;font-weight:600;
  color:var(--dim);pointer-events:none;padding:0 16px 0 58px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slider .knob{position:absolute;top:3px;left:3px;width:46px;height:46px;border-radius:50%;background:var(--code);
  display:flex;align-items:center;justify-content:center;color:#0e1116;font-weight:800;font-size:20px;cursor:grab;
  box-shadow:0 2px 8px rgba(0,0,0,.4)}
.slider.danger .knob{background:var(--warn)}.slider.danger .fill{background:rgba(210,153,34,.18)}
.slider.busy{opacity:.6;pointer-events:none}
.slider.done .knob{background:var(--ok)}
.result{font-size:12px;margin-top:4px;min-height:1em}
.result.ok{color:var(--ok)}.result.bad{color:var(--bad)}
.ctlgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:4px 16px}
.note{font-size:12px;color:var(--dim);margin-top:6px}
#pinbox{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:20}
#pinbox form{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;width:min(320px,90vw)}
#pinbox input{width:100%;font:20px/1.2 system-ui;padding:10px;border-radius:8px;border:1px solid var(--line);
  background:#0b0e13;color:var(--ink);letter-spacing:4px;text-align:center;margin:10px 0}
#pinbox button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card2);color:var(--ink)}
@media (max-width:600px){main{grid-template-columns:1fr;padding:8px 8px calc(12px + var(--safe-b))}
  #detail{padding:8px 8px calc(16px + var(--safe-b))}.cols{grid-template-columns:1fr}
  .bigdials{grid-template-columns:repeat(3,1fr);gap:6px}.bigdials .dial{padding:6px 2px 8px}
  .dhead .name{font-size:20px}}
</style></head><body>
<header><button class="back" id="back" aria-label="Back to all machines">‹ All</button>
<h1 id="title">Lab</h1><span id="stale">not updating</span><span id="clock"></span></header>
<main id="grid"></main>
<section id="detail"></section>
<div id="pinbox"><form id="pinform"><div class="name">Control PIN</div>
<div class="sub">Asked once on this device.</div><input id="pin" inputmode="numeric" autocomplete="off" type="password">
<div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" id="pincancel">Cancel</button>
<button type="submit">OK</button></div></form></div>
<script>
"use strict";
const COLORS={code:"--code",render:"--render",plan:"--plan",chat:"--chat",gaming:"--game",idle:"--idle",
  offline:"--bad",load:"--warn",shape:"--shape"};
const ROLE_COLOR={llm:"--llm",sd:"--sd",desktop:"--desk",other:"--other"};
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function kv(k,v,cls){const s=el("span");s.append(el("span","k",k),el("span","v "+(cls||""),v));return s}
const GB=b=>b==null?"–":(b/2**30).toFixed(1);
const n0=x=>x==null?"–":Math.round(x).toString();
function tempCls(t,warn,bad){return t==null?"":t>=bad?"vhot":t>=warn?"hot":""}
function ago(s){s=Math.max(0,s|0);const d=s/86400|0,h=s%86400/3600|0,m=s%3600/60|0;return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`}
const store={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
let STATE=null,lastOk=0;

function bar(parts,total){const b=el("div","bar");for(const[p,c]of parts){if(!p||!total)continue;
  const s=el("span");s.style.width=Math.min(100,100*p/total)+"%";s.style.background=css(c);b.append(s)}return b}

/* ---- dials: a 240° arc with the value in the middle ---- */
function dial(label,value,max,unit,opts={}){
  const d=el("div","dial");const NS="http://www.w3.org/2000/svg";
  const svg=document.createElementNS(NS,"svg");svg.setAttribute("viewBox","0 0 120 100");
  const r=46,cx=60,cy=56,a0=150,a1=390;
  const pt=a=>[cx+r*Math.cos(a*Math.PI/180),cy+r*Math.sin(a*Math.PI/180)];
  const arc=(from,to)=>{const[x0,y0]=pt(from),[x1,y1]=pt(to);const large=(to-from)>180?1:0;
    return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`};
  const track=document.createElementNS(NS,"path");track.setAttribute("d",arc(a0,a1));
  track.setAttribute("stroke",css("--track"));track.setAttribute("stroke-width","9");track.setAttribute("fill","none");
  track.setAttribute("stroke-linecap","round");svg.append(track);
  const f=value==null?0:Math.max(0,Math.min(1,value/max));
  let color=css(opts.color||"--code");
  if(opts.warn!=null&&value!=null&&value>=opts.warn)color=css("--warn");
  if(opts.bad!=null&&value!=null&&value>=opts.bad)color=css("--bad");
  if(f>0.002){const p=document.createElementNS(NS,"path");p.setAttribute("d",arc(a0,a0+(a1-a0)*f));
    p.setAttribute("stroke",color);p.setAttribute("stroke-width","9");p.setAttribute("fill","none");
    p.setAttribute("stroke-linecap","round");svg.append(p)}
  if(opts.mark!=null){const[x,y]=pt(a0+(a1-a0)*Math.min(1,opts.mark/max));const m=document.createElementNS(NS,"circle");
    m.setAttribute("cx",x);m.setAttribute("cy",y);m.setAttribute("r","2.5");m.setAttribute("fill",css("--dim"));svg.append(m)}
  const t=document.createElementNS(NS,"text");t.setAttribute("x","60");t.setAttribute("y","60");t.setAttribute("text-anchor","middle");
  t.setAttribute("fill",value==null?css("--faint"):css("--ink"));t.setAttribute("font-size","22");t.setAttribute("font-weight","700");
  t.setAttribute("font-family","system-ui,-apple-system,sans-serif");
  t.textContent=value==null?"–":(opts.fmt?opts.fmt(value):Math.round(value));svg.append(t);
  const u=document.createElementNS(NS,"text");u.setAttribute("x","60");u.setAttribute("y","78");u.setAttribute("text-anchor","middle");
  u.setAttribute("fill",css("--dim"));u.setAttribute("font-size","10");u.setAttribute("font-family","system-ui,sans-serif");
  u.textContent=unit||"";svg.append(u);
  d.append(svg,el("div","lab",label));return d}

function nodeDials(d,big){const g=(d.gpus||[]);const h=d.host||{};
  const vt=g.reduce((a,x)=>a+(x.vram_total||0),0),vu=g.reduce((a,x)=>a+(x.vram_used||0),0);
  const busy=g.length?Math.max(...g.map(x=>x.busy||0)):null;
  const hot=g.length?Math.max(...g.map(x=>Math.max(0,...Object.values(x.temps||{})))):null;
  const pw=g.length&&g.some(x=>x.power!=null)?g.reduce((a,x)=>a+(x.power||0),0):null;
  const cap=g.length&&g.some(x=>x.power_cap)?g.reduce((a,x)=>a+(x.power_cap||0),0):null;
  const tps=(d.llm||[]).reduce((a,l)=>a+(l.tps||0),0);
  const out=[];
  if(g.length){out.push(dial("GPU load",busy,100,"%"),
    dial("VRAM",vt?vu/2**30:null,vt?vt/2**30:16,`of ${GB(vt)} GB`,{fmt:v=>v.toFixed(1),warn:vt?(vt-2**29)/2**30:null}),
    dial("GPU temp",hot||null,110,"°C",{warn:85,bad:100,color:"--plan"}))}
  else out.push(dial("CPU",h.cpu_pct,100,"%"),dial("RAM",h.ram_total?100*h.ram_used/h.ram_total:null,100,"%",{warn:90}),
    dial("CPU temp",h.cpu_temp,100,"°C",{warn:80,bad:90,color:"--plan"}));
  if(big){if(g.length)out.push(dial("GPU power",pw,cap||400,cap?`of ${n0(cap)} W`:"W",{color:"--chat"}),
      dial("CPU",h.cpu_pct,100,"%"),dial("RAM",h.ram_total?100*h.ram_used/h.ram_total:null,100,`% of ${GB(h.ram_total)} GB`,{warn:90}));
    if((d.llm||[]).length)out.push(dial("Tokens/s",tps,Math.max(60,...(d.llm||[]).map(l=>l.last_gen_tps||0))*1.2,"tok/s",{color:"--chat",fmt:v=>v.toFixed(v<10?1:0)}));
    if(g.length)out.push(dial("CPU temp",h.cpu_temp,100,"°C",{warn:80,bad:90,color:"--plan"}))}
  return out}

/* ---- graphs ---- */
function drawChart(c,hist,series,opts={}){requestAnimationFrame(()=>{
  const r=c.getBoundingClientRect(),dpr=window.devicePixelRatio||1;if(!r.width)return;
  c.width=r.width*dpr;c.height=r.height*dpr;const g=c.getContext("2d");g.scale(dpr,dpr);
  const W=r.width,H=r.height,pad=opts.axis?14:2;g.clearRect(0,0,W,H);
  if(opts.axis){g.strokeStyle=css("--line");g.lineWidth=1;for(const f of[.25,.5,.75]){g.beginPath();
    g.moveTo(0,pad+(H-2*pad)*f);g.lineTo(W,pad+(H-2*pad)*f);g.stroke()}}
  if(hist.length<2)return;const t0=hist[0][0],t1=hist[hist.length-1][0],span=Math.max(1,t1-t0);
  for(const s of series){const max=s.max||Math.max(1,...hist.map(p=>p[s.i]||0))*1.15;
    g.beginPath();let started=false,lastX=0;
    for(const p of hist){const v=p[s.i];if(v==null)continue;const x=(p[0]-t0)/span*W,y=H-pad-(H-2*pad)*Math.min(1,v/max);
      if(!started){g.moveTo(x,y);started=true}else g.lineTo(x,y);lastX=x}
    g.strokeStyle=css(s.c);g.lineWidth=1.6;g.stroke();
    if(s.fill&&started){g.lineTo(lastX,H);g.lineTo((hist.find(p=>p[s.i]!=null)[0]-t0)/span*W,H);g.closePath();
      g.globalAlpha=.14;g.fillStyle=css(s.c);g.fill();g.globalAlpha=1}}
  if(opts.axis){g.fillStyle=css("--faint");g.font="10px system-ui";const mins=Math.round(span/60);
    g.fillText(mins>=1?`${mins} min ago`:"now",2,H-2);g.textAlign="right";g.fillText("now",W-2,H-2)}})}

function spark(hist){const wrap=el("div");const c=el("canvas","spark");wrap.append(c);
  drawChart(c,hist,[{i:1,max:100,c:"--code",fill:true},{i:5,max:100,c:"--sd"},{i:2,c:"--chat"}]);
  wrap.append(el("div","legend","GPU load (blue) · VRAM % (violet) · tok/s (yellow) · 10 min"));return wrap}

/* ---- slide to confirm ---- */
function slider(text,onConfirm,opts={}){
  const s=el("div","slider"+(opts.danger?" danger":""));s.setAttribute("role","button");s.setAttribute("tabindex","0");
  s.setAttribute("aria-label",text+" - slide right, or press Enter twice");
  const fill=el("div","fill"),lab=el("div","label",text+"  ›››"),knob=el("div","knob","›");s.append(fill,lab,knob);
  let x0=null,dx=0,armed=false;const maxX=()=>s.clientWidth-52;
  const set=v=>{dx=Math.max(0,Math.min(maxX(),v));knob.style.transform=`translateX(${dx}px)`;fill.style.width=(dx+52)+"px";
    lab.style.opacity=String(1-dx/maxX()*.9)};
  const reset=()=>{knob.style.transition=fill.style.transition="transform .25s, width .25s";set(0);
    setTimeout(()=>{knob.style.transition=fill.style.transition=""},260)};
  knob.addEventListener("pointerdown",e=>{if(s.classList.contains("busy"))return;x0=e.clientX-dx;knob.setPointerCapture(e.pointerId);e.preventDefault()});
  knob.addEventListener("pointermove",e=>{if(x0==null)return;set(e.clientX-x0)});
  const up=()=>{if(x0==null)return;x0=null;if(dx>=maxX()*0.92){set(maxX());fire()}else reset()};
  knob.addEventListener("pointerup",up);knob.addEventListener("pointercancel",()=>{x0=null;reset()});
  s.addEventListener("keydown",e=>{if(e.key!=="Enter"&&e.key!==" ")return;e.preventDefault();
    if(armed){armed=false;fire()}else{armed=true;lab.textContent="Press again to confirm";setTimeout(()=>{armed=false;lab.textContent=text+"  ›››"},3000)}});
  async function fire(){s.classList.add("busy");lab.textContent="Working…";
    try{const ok=await onConfirm();s.classList.toggle("done",!!ok)}finally{
      setTimeout(()=>{s.classList.remove("busy","done");lab.textContent=text+"  ›››";reset()},1500)}}
  return s}

/* ---- talking to the hub ---- */
function askPin(){return new Promise(res=>{const box=document.getElementById("pinbox"),inp=document.getElementById("pin");
  box.style.display="flex";inp.value="";setTimeout(()=>inp.focus(),50);
  const done=v=>{box.style.display="none";form.onsubmit=null;cancel.onclick=null;res(v)};
  const form=document.getElementById("pinform"),cancel=document.getElementById("pincancel");
  form.onsubmit=e=>{e.preventDefault();done(inp.value)};cancel.onclick=()=>done(null)})}
async function post(path,body){for(let i=0;i<2;i++){
  const pin=store.get("lab-pin")||"";
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...body,pin})});
  let j={};try{j=await r.json()}catch(e){}
  if(r.status===401&&j.pin){const p=await askPin();if(p==null)return{ok:false,message:"cancelled"};store.set("lab-pin",p);continue}
  return{ok:r.ok&&j.ok!==false,message:j.message||`HTTP ${r.status}`}}return{ok:false,message:"wrong PIN"}}

function control(title,what,danger,run){const box=el("div","ctl");if(what)box.append(el("div","what",what));
  const res=el("div","result");box.append(slider(title,async()=>{res.className="result";res.textContent="";
    const out=await run();res.className="result "+(out.ok?"ok":"bad");res.textContent=out.message||"";
    setTimeout(tick,1200);return out.ok},{danger}),res);return box}

/* ---- overview cards ---- */
function nodeCard(n){const card=el("section","card tap");const a=n.activity||{kind:"offline",label:"Offline"};
  card.addEventListener("click",()=>{location.hash="#/n/"+encodeURIComponent(n.name)});
  if(a.kind==="offline")card.classList.add("offline");
  const top=el("div","top");const p=el("span","pill",a.label);p.style.background=css(COLORS[a.kind]||"--idle");
  top.append(el("div","name",n.name),p);card.append(top,el("div","role",n.role||""));
  if(a.detail){const dt=el("div","detailtxt",a.detail);dt.style.color=css(COLORS[a.kind]||"--dim");dt.title=a.detail;card.append(dt)}
  const d=n.data;if(!d){card.append(el("div","sub",n.error?`No answer from lab-agent: ${n.error}`:"Waiting for lab-agent…"));return card}
  const dl=el("div","dials");dl.append(...nodeDials(d,false));card.append(dl);
  const h=d.host||{};const row=el("div","row");
  row.append(kv("CPU",`${n0(h.cpu_pct)}%`),kv("RAM",`${GB(h.ram_used)} / ${GB(h.ram_total)} GB`,
    h.ram_total&&h.ram_used/h.ram_total>.9?"hot":""),kv("up",ago(h.uptime)));card.append(row);
  for(const lm of d.llm||[]){const x=el("div","sub");x.style.marginTop="4px";
    x.textContent=`${lm.name}: ${lm.model||""} · ${lm.state}`+(lm.state==="generating"&&lm.tps?` · ${lm.tps.toFixed(1)} tok/s`:"");card.append(x)}
  for(const s of d.sd||[]){const x=el("div","sub");x.style.marginTop="2px";x.textContent=`${s.name}: ${s.state}`;card.append(x)}
  if((d.gpus||[]).length)card.append(spark(n.history||[]));
  const svc=el("div","svc");for(const[name,st]of Object.entries(d.services||{})){const s=el("span");const dot=el("span","dot");
    dot.style.background=css(st==="active"?"--ok":st==="failed"?"--bad":"--faint");s.append(dot,name);svc.append(s)}
  for(const dk of h.disks||[]){if(dk.total&&dk.used/dk.total>.85)svc.append(el("span","hot",`${dk.path} ${Math.round(100*dk.used/dk.total)}% full`))}
  card.append(svc);const ctl=(d.controls||{}).available||[];
  card.append(el("div","more",ctl.length?`Details & ${ctl.length} control${ctl.length===1?"":"s"} ›`:"Details ›"));return card}

function forgeCard(f,controls){const card=el("section","card wide");const top=el("div","top");const l=el("div");
  l.append(el("div","name","forge"),el("div","role",f.present?`${f.project||""} · ${f.run||""}`:"no run yet"));
  const r=el("div");const p=el("span","pill",f.alive?(f.run_phase||"running"):(f.phase||"not running"));
  p.style.background=css(f.alive?"--code":f.phase==="failed"?"--bad":f.phase==="committed"?"--ok":"--idle");r.append(p);
  top.append(l,r);card.append(top);
  const health=f.toolchain_health||{};
  if((health.toolchains||[]).length){const box=el("details");box.append(el("summary",null,"Toolchain health"+(health.stale?" (stale)":"")));
    for(const t of health.toolchains)box.append(el("div","note",`${t.name}: ${t.status}; smoke ${(t.smoke||{}).status||"unrun"}${(t.smoke||{}).stale?" (stale)":""}`));card.append(box)}
  if(!f.present)return card;
  for(const [name,w] of Object.entries(f.gpu_waiting||{}))card.append(el("div","note",`${name}: ${w.reason}; ${(w.ahead||[]).length} claim(s) ahead`));
  if(f.alive&&controls){const group=el("div","ctlgrid");for(const action of ["pause","cancel"])group.append(control(action==="pause"?"Pause after current chunks":"Cancel with checkpoint",
    "Current work finishes and verified chunks remain available to resume.",false,()=>post("/api/forge",{action})));card.append(group)}
  const pr=el("div","row progress");if(f.chunks_total)pr.append(kv("chunks verified",`${f.chunks_done} / ${f.chunks_total}`));
  if(f.images_total)pr.append(kv("images",`${f.images_done} / ${f.images_total}`));
  if(f.chunks_failed&&f.chunks_failed.length)pr.append(kv("failed",f.chunks_failed.join(", "),"vhot"));
  if(f.claude_cost)pr.append(kv("Claude",`$${f.claude_cost.toFixed(2)}`));if(f.error&&!f.alive)pr.append(kv("stopped",f.error,"vhot"));
  card.append(pr);if(f.chunks_total)card.append(bar([[f.chunks_done,"--ok"]],f.chunks_total));
  const jobs=[...Object.values(f.calls||{}).map(c=>[c.node||c.llm,c.llm==="planner"?"planner":c.llm==="triage"?"triage":"coder",c.what||"",
      c.thinking?("thinking "+(typeof c.thinking==="string"?c.thinking:"")):"",c.for]),
    ...Object.values(f.renders||{}).map(x=>[x.node||"",'image',x.asset+" "+(x.size||""),"",x.for])];
  if(jobs.length){const t=el("table");const hr=el("tr");for(const h of["machine","as","doing","","for"])hr.append(el("th",null,h));t.append(hr);
    for(const j of jobs){const tr=el("tr");for(const c of j)tr.append(el("td",null,c));t.append(tr)}card.append(t)}
  if(f.lessons&&f.lessons.length){const ls=el("div","note","Lessons this run: "+f.lessons.join(" · "));card.append(ls)}
  if(!f.alive&&controls){const g=el("div","ctlgrid");
    g.append(control("Resume the run",`${f.run||""}: carries on where it stopped; failed chunks get fresh attempts.`,false,
      ()=>post("/api/forge",{action:"resume"})));
    if(f.chunks_failed&&f.chunks_failed.length)g.append(control(`Rebuild ${f.chunks_failed.join(", ")}`,
      "Writes new tests for the failed chunks from the spec, then builds them again.",false,
      ()=>post("/api/forge",{action:"retest_failed"})));
    card.append(g)}
  if(f.action){const a=el("div","result "+(f.action.ok?"ok":"bad"),f.action.message);card.append(a)}
  const pre=el("pre");for(const line of(f.log||[]).slice(-14)){const lv=(line.split(/\s+/)[1]||"");pre.append(el("div",lv,line))}
  card.append(pre);requestAnimationFrame(()=>{pre.scrollTop=pre.scrollHeight});return card}

/* ---- detail page ---- */
let HIST={name:null,data:[],at:0};
async function loadHistory(name){if(HIST.name===name&&Date.now()-HIST.at<10000)return HIST.data;
  try{const r=await fetch("/api/history?node="+encodeURIComponent(name),{cache:"no-store"});HIST={name,data:await r.json(),at:Date.now()}}catch(e){}
  return HIST.data}

function chartBox(title,hist,series,legend){const b=el("div","chart");const h=el("h3");h.append(el("span",null,title),el("span",null,legend||""));
  const c=el("canvas");b.append(h,c);drawChart(c,hist,series,{axis:true});return b}

async function renderDetail(name){const n=(STATE.nodes||[]).find(x=>x.name===name);const box=document.getElementById("detail");
  document.getElementById("title").textContent=name;
  if(!n){box.replaceChildren(el("div","sub","No such machine."));return}
  const frag=document.createDocumentFragment();const a=n.activity||{kind:"offline",label:"Offline"};
  const head=el("div","dhead");const left=el("div");left.append(el("div","name",n.name),el("div","role",n.role||""));
  if(a.detail)left.append(el("div","detailtxt",a.detail));
  const pill=el("span","pill",a.label);pill.style.background=css(COLORS[a.kind]||"--idle");head.append(left,pill);frag.append(head);
  const d=n.data;if(!d){frag.append(el("div","panel",n.error?`No answer from lab-agent: ${n.error}`:"Waiting for lab-agent…"));box.replaceChildren(frag);return}
  const dl=el("div","bigdials");dl.append(...nodeDials(d,true));frag.append(dl);
  const hist=await loadHistory(name);const cols=el("div","cols");
  const mins=hist.length>1?Math.round((hist[hist.length-1][0]-hist[0][0])/60):0;
  if((d.gpus||[]).length){cols.append(chartBox("GPU load and VRAM",hist,[{i:1,max:100,c:"--code",fill:true},{i:5,max:100,c:"--sd"}],"load · VRAM %"),
    chartBox("Temperatures",hist,[{i:3,max:110,c:"--plan"},{i:8,max:100,c:"--game"}],"GPU · CPU °C"),
    chartBox("GPU power",hist,[{i:6,c:"--chat",fill:true}],"W"))}
  if((d.llm||[]).length)cols.append(chartBox("Tokens per second",hist,[{i:2,c:"--chat",fill:true}],"tok/s"));
  cols.append(chartBox("CPU and RAM",hist,[{i:4,max:100,c:"--code",fill:true},{i:7,max:100,c:"--sd"}],"CPU · RAM %"));
  frag.append(cols);frag.append(el("div","note",`Last ${mins} min · a point every 2 s`));
  // GPUs
  for(const gp of d.gpus||[]){const pnl=el("div","panel");const h3=el("h3");h3.append(el("span",null,gp.name),el("span",null,`${n0(gp.sclk)} / ${n0(gp.mclk)} MHz`));pnl.append(h3);
    const used=gp.vram_used||0,total=gp.vram_total||1;const by={llm:0,sd:0,desktop:0,other:0};
    for(const p of gp.procs||[])by[p.role]=(by[p.role]||0)+p.vram;const known=by.llm+by.sd+by.desktop+by.other;if(used>known)by.other+=used-known;
    const r=el("div","row");r.append(kv("VRAM",`${GB(used)} / ${GB(total)} GB`),kv("load",`${n0(gp.busy)}%`),kv("mem bus",`${n0(gp.mem_busy)}%`));
    const t=gp.temps||{};for(const[k,v]of Object.entries(t))r.append(kv(k,`${n0(v)}°`,tempCls(v,85,100)));
    if(gp.power!=null)r.append(kv("power",`${n0(gp.power)}${gp.power_cap?" / "+n0(gp.power_cap):""} W`));
    if(gp.fan_rpm!=null)r.append(kv("fan",`${n0(gp.fan_rpm)} rpm`));if(gp.fan_pct!=null)r.append(kv("fan",`${n0(gp.fan_pct)}%`));
    pnl.append(r,bar([[by.llm,ROLE_COLOR.llm],[by.sd,ROLE_COLOR.sd],[by.desktop,ROLE_COLOR.desktop],[by.other,ROLE_COLOR.other]],total));
    const lg=el("div","legend");for(const p of gp.procs||[]){const s=el("span");const i=el("i");i.style.background=css(ROLE_COLOR[p.role]||"--other");s.append(i,`${p.name} ${GB(p.vram)} GB`);lg.append(s)}
    pnl.append(lg);frag.append(pnl)}
  // model servers
  if((d.llm||[]).length||(d.sd||[]).length){const pnl=el("div","panel");pnl.append(el("h3",null,"Model and image servers"));
    for(const l of d.llm||[]){const it=el("div","item");const L=el("div");L.append(el("div","gname",l.model||l.name),
        el("div","sub",`${l.name} · ${l.state}`+(l.ctx?` · ctx ${l.ctx.toLocaleString()}`:"")+(l.slots_total?` · slots ${l.slots_busy||0}/${l.slots_total}`:"")+(l.queued?` · queued ${l.queued}`:"")));
      const R=el("div");R.style.textAlign="right";const live=l.state==="generating"&&l.tps>0;
      const big=el("div","tps",live?l.tps.toFixed(1):(l.last_gen_tps??"–").toString());big.append(el("small",null,live?" tok/s":" tok/s last"));
      if(!live)big.style.opacity=.55;R.append(big);if(l.last_prompt_tps)R.append(el("div","sub",`prompt ${n0(l.last_prompt_tps)} tok/s`));
      it.append(L,R);pnl.append(it)}
    for(const s of d.sd||[]){const it=el("div","item");it.append(el("div","gname",s.name),el("div","sub",`${s.state}${s.vram?` · ${GB(s.vram)} GB VRAM`:""}`));pnl.append(it)}
    for(const x of d.downloads||[])pnl.append(el("div","sub",`fetching ${x.alias}${x.pct!=null?" · "+x.pct+"%":""} · ${x.state}`));
    frag.append(pnl)}
  // controls
  const C=d.controls||{};const pnl=el("div","panel");pnl.append(el("h3",null,"Controls"));
  if(!STATE.controls)pnl.append(el("div","note","Read-only: set up controls on Gemini with  ./install-lab-dashboard.sh controls"));
  else if(!(C.available||[]).length)pnl.append(el("div","note","This machine offers no controls (no control_token in its lab-agent.toml)."));
  else{const g=el("div","ctlgrid");for(const c of C.available){const danger=/^stop:|^restart:/.test(c.id);
      g.append(control(c.label,c.detail||"",danger,()=>post("/api/action",{node:n.name,action:c.id})))}pnl.append(g)}
  if(C.running)pnl.append(el("div","note",`Running: ${C.running.action}…`));
  for(const r of(C.recent||[]).slice(-5).reverse())pnl.append(el("div","result "+(r.ok?"ok":"bad"),
    `${new Date(r.at*1000).toLocaleTimeString()} ${r.action}: ${r.message}`));
  frag.append(pnl);
  // services and disks
  const sp=el("div","panel");sp.append(el("h3",null,"Services and disks"));const svc=el("div","svc");
  for(const[nm,st]of Object.entries(d.services||{})){const s=el("span");const dot=el("span","dot");
    dot.style.background=css(st==="active"?"--ok":st==="failed"?"--bad":"--faint");s.append(dot,`${nm} (${st})`);svc.append(s)}
  sp.append(svc);for(const dk of (d.host||{}).disks||[]){const r=el("div","row");r.append(kv(dk.path,`${GB(dk.used)} / ${GB(dk.total)} GB`));
    sp.append(r,bar([[dk.used,dk.used/dk.total>.85?"--warn":"--code"]],dk.total))}
  frag.append(sp);box.replaceChildren(frag)}

/* ---- routing and refresh ---- */
function route(){const m=location.hash.match(/^#\/n\/(.+)$/);return m?decodeURIComponent(m[1]):null}
async function tick(){try{const r=await fetch("/api/state",{cache:"no-store"});STATE=await r.json();lastOk=Date.now()}catch(e){}
  document.getElementById("clock").textContent=new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
  document.getElementById("stale").style.display=Date.now()-lastOk>10000?"inline":"none";
  if(!STATE)return;const name=route();document.body.classList.toggle("detail",!!name);
  if(name){if(!document.querySelector(".slider.busy")&&!dragging())renderDetail(name);return}
  document.getElementById("title").textContent="Lab";
  if(document.querySelector(".slider.busy")||dragging())return;
  const g=document.getElementById("grid");const frag=document.createDocumentFragment();frag.append(forgeCard(STATE.forge||{},STATE.controls));
  for(const n of STATE.nodes){if(n.absent)continue;frag.append(nodeCard(n))}g.replaceChildren(frag)}
let DRAG=false;document.addEventListener("pointerdown",e=>{if(e.target.closest(".slider"))DRAG=true});
document.addEventListener("pointerup",()=>{setTimeout(()=>{DRAG=false},50)});
document.addEventListener("pointercancel",()=>{DRAG=false});
const dragging=()=>DRAG;
document.getElementById("back").addEventListener("click",()=>{location.hash="#/"});
window.addEventListener("hashchange",()=>{HIST.at=0;window.scrollTo(0,0);tick()});
tick();setInterval(tick,2000);
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    serve(cfg)


if __name__ == "__main__":
    main()
