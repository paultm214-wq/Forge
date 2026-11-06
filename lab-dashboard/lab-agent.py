#!/usr/bin/env python3
"""
lab-agent.py - one machine's live readings for the lab dashboard.

Runs on every machine (Gemini, Apollo, Vulcan, Mercury, optionally Metis) as
a user service and answers GET /api/node with one JSON object:

    host      CPU %, CPU temperature and clock, load, RAM, swap, disks, uptime
    gpus      per GPU: VRAM used/total and WHO holds it (llama-server,
              sd-server, games), busy %, memory-bus %, core and memory clock,
              edge / hotspot / memory temperature, power and cap, fan
    llm       per llama-server: up, model, loaded / sleeping (router mode),
              generating or reading a prompt, live tokens/s, last response's
              generation and prompt speed, context size
    sd        sd-server: up, and whether it is rendering
    gaming    Vulcan: the Steam game running, or the vulcan-play flag
    services  systemd units: active / inactive / failed

Standard library only. API keys stay on this machine; prompt text from
/slots is never passed on. Settings: ~/.config/lab-agent.toml (or the file
LAB_AGENT_CONFIG names - Metis's system service uses /etc/lab-agent.toml),
see the installer, or the defaults below.

CONTROLS (POST /api/action) - only with `control_token` set, and only with
that token in X-Lab-Control (the hub on Gemini sends it). Nothing else can
be run: the actions are a fixed list built from this file -
    unload:<llm>     unload every model a llama-server router has loaded
    clear_vram       unload every router's models, and restart an sd-server
                     that holds VRAM while it is not rendering
    restart:<unit>   restart a user service named in `llm`/`sd` (`unit`) or
                     in `control_units`
    stop:<unit>, start:<unit>   the same units (Strata, forge-shape)
Each action is logged to ~/.local/state/lab-agent-actions.log and shown in
the snapshot's "actions" (the last 10).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG = Path(os.environ.get("LAB_AGENT_CONFIG") or Path.home() / ".config" / "lab-agent.toml")
SYS = os.environ.get("LAB_SYS", "/sys")      # tests point this at a fake tree
DEFAULTS = {
    "port": 9101,
    "token": "",                       # if set, the hub must send X-Lab-Token
    "llm": [],                         # [{name, url, key_file}]
    "sd": [],                          # [{name, url}]
    "user_units": [],                  # systemctl --user units to report
    "system_units": [],                # system units to report
    "disks": ["/"],
    "gpu_names": {"0x7550": "RX 9070 XT", "0x7590": "RX 9060 XT",
                  "0x7551": "RX 9070", "0x7591": "RX 9060"},
    "control_token": "",               # empty: no controls on this machine
    "control_units": [],               # extra user units the dashboard may stop/start/restart
}
# Process names that matter on the GPU, for the VRAM breakdown.
ROLE = [(re.compile(r"llama-server|llama_server"), "llm"),
        (re.compile(r"sd-server|sd_server|stable-diffusion"), "sd"),
        (re.compile(r"plasmashell|kwin|Xwayland|Xorg|gnome-shell|sunshine", re.I), "desktop")]


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        with open(CONFIG, "rb") as fh:
            cfg.update(tomllib.load(fh))
    return cfg


def read(path, default=None):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def num(path, scale=1.0, default=None):
    v = read(path)
    try:
        return float(v) * scale if v is not None else default
    except ValueError:
        return default


# --------------------------------------------------------------------- host

class CPU:
    def __init__(self):
        self.prev = None

    def percent(self) -> float | None:
        line = read("/proc/stat", "").splitlines()[0].split()[1:]
        vals = [int(x) for x in line]
        idle, total = vals[3] + vals[4], sum(vals)
        out = None
        if self.prev:
            di, dt = idle - self.prev[0], total - self.prev[1]
            out = round(100.0 * (1 - di / dt), 1) if dt > 0 else None
        self.prev = (idle, total)
        return out


def hwmon_by_name(name: str) -> list[Path]:
    return [Path(p).parent for p in glob.glob(f"{SYS}/class/hwmon/hwmon*/name")
            if read(p) == name]


def cpu_temp() -> float | None:
    for d in hwmon_by_name("k10temp") + hwmon_by_name("coretemp"):
        for t in sorted(d.glob("temp*_input")):
            label = read(str(t).replace("_input", "_label"), "")
            if label in ("Tctl", "Tdie", "Package id 0") or not label:
                return num(t, 0.001)
    return None


def cpu_mhz() -> float | None:
    vals = [num(p, 0.001) for p in glob.glob(f"{SYS}/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")]
    vals = [v for v in vals if v]
    return round(max(vals)) if vals else None


def host(cpu: CPU, disks: list[str]) -> dict:
    mem = {}
    for line in read("/proc/meminfo", "").splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.split()[0]) * 1024 if v.split() else 0
    load = read("/proc/loadavg", "0 0 0").split()[:3]
    out = {"hostname": socket.gethostname(), "cpu_pct": cpu.percent(),
           "cpu_temp": cpu_temp(), "cpu_mhz": cpu_mhz(), "cores": os.cpu_count(),
           "load": [float(x) for x in load],
           "ram_total": mem.get("MemTotal"), "ram_used": mem.get("MemTotal", 0) - mem.get("MemAvailable", 0),
           "swap_total": mem.get("SwapTotal"), "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0),
           "uptime": float(read("/proc/uptime", "0").split()[0]), "disks": []}
    for d in disks:
        try:
            u = shutil.disk_usage(os.path.expanduser(d))
            out["disks"].append({"path": d, "total": u.total, "used": u.used})
        except OSError:
            pass
    return out


# --------------------------------------------------------------- processes

def comm(pid: str) -> str:
    return read(f"/proc/{pid}/comm", "") or ""


def role_of(name: str) -> str:
    for rx, role in ROLE:
        if rx.search(name):
            return role
    return "other"


def amd_vram_by_process() -> dict[str, dict[str, int]]:
    """{pci address: {process name: bytes}} from DRM fdinfo - which process
    holds how much VRAM, without root for our own processes."""
    out: dict[str, dict[str, int]] = {}
    seen = set()
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = fd_dir.split("/")[2]
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if not os.readlink(f"{fd_dir}/{fd}").startswith("/dev/dri/"):
                    continue
                info = read(f"/proc/{pid}/fdinfo/{fd}", "")
            except OSError:
                continue
            pdev = re.search(r"^drm-pdev:\s*(\S+)", info, re.M)
            cid = re.search(r"^drm-client-id:\s*(\d+)", info, re.M)
            vram = re.search(r"^drm-memory-vram:\s*(\d+)\s*(\w+)", info, re.M)
            if not (pdev and vram):
                continue
            key = (pdev.group(1), cid.group(1) if cid else f"{pid}/{fd}")
            if key in seen:
                continue
            seen.add(key)
            mult = {"KiB": 1024, "MiB": 1 << 20, "GiB": 1 << 30}.get(vram.group(2), 1)
            name = comm(pid)
            procs = out.setdefault(pdev.group(1), {})
            procs[name] = procs.get(name, 0) + int(vram.group(1)) * mult
    return out


# --------------------------------------------------------------------- GPUs

def amd_gpus(names: dict) -> list[dict]:
    gpus, by_proc = [], None
    for card in sorted(glob.glob(f"{SYS}/class/drm/card[0-9]*")):
        if "-" in os.path.basename(card):
            continue
        dev = Path(card) / "device"
        if read(dev / "vendor") != "0x1002":
            continue
        total = num(dev / "mem_info_vram_total")
        if not total or total < 4 * (1 << 30):        # the CPU's iGPU
            continue
        if by_proc is None:
            by_proc = amd_vram_by_process()
        pci = os.path.basename(os.path.realpath(dev))
        hw = next(iter(sorted(dev.glob("hwmon/hwmon*"))), None)
        temps = {}
        if hw:
            for t in hw.glob("temp*_input"):
                label = read(str(t).replace("_input", "_label"), t.name)
                temps[{"edge": "edge", "junction": "hotspot", "mem": "mem"}.get(label, label)] = num(t, 0.001)
        def clock(name, hwfile):
            v = num(hw / hwfile, 1e-6) if hw else None
            if v:
                return round(v)
            cur = re.search(r"(\d+)Mhz \*", read(dev / name, "") or "")
            return int(cur.group(1)) if cur else None
        power = (num(hw / "power1_average", 1e-6) or num(hw / "power1_input", 1e-6)) if hw else None
        did = read(dev / "device", "")
        gpus.append({
            "vendor": "amd", "name": names.get(did, f"AMD {did}"), "pci": pci,
            "vram_total": total, "vram_used": num(dev / "mem_info_vram_used"),
            "busy": num(dev / "gpu_busy_percent"), "mem_busy": num(dev / "mem_busy_percent"),
            "sclk": clock("pp_dpm_sclk", "freq1_input"), "mclk": clock("pp_dpm_mclk", "freq2_input"),
            "temps": temps, "power": power,
            "power_cap": num(hw / "power1_cap", 1e-6) if hw else None,
            "fan_rpm": num(hw / "fan1_input") if hw else None,
            "procs": [{"name": n, "role": role_of(n), "vram": b}
                      for n, b in sorted((by_proc or {}).get(pci, {}).items(), key=lambda kv: -kv[1])],
        })
    return gpus


def intel_gpus(names: dict) -> list[dict]:
    """Intel Arc (Hades' B580) on the xe driver: name (gpu_names, else the
    PCI id), VRAM size and power where the kernel reports them. Cards with no
    VRAM of their own (iGPUs) are skipped."""
    gpus = []
    for card in sorted(glob.glob(f"{SYS}/class/drm/card[0-9]*")):
        if "-" in os.path.basename(card):
            continue
        dev = Path(card) / "device"
        if read(dev / "vendor") != "0x8086":
            continue
        did = read(dev / "device", "")
        total = num(dev / "tile0" / "vram0" / "io_size") or num(dev / "mem_info_vram_total")
        if not total or total < 4 * (1 << 30):        # an iGPU: no VRAM of its own
            continue
        hw = next(iter(sorted(dev.glob("hwmon/hwmon*"))), None)
        power = num(hw / "power1_input", 1e-6) if hw else None
        gpus.append({"vendor": "intel", "name": names.get(did, f"Intel Arc {did}"),
                     "pci": os.path.basename(os.path.realpath(dev)),
                     "vram_total": total, "vram_used": num(dev / "mem_info_vram_used"),
                     "busy": None, "temps": {}, "power": power, "procs": []})
    return gpus


def nvidia_gpus() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    q = ("index,name,memory.used,memory.total,utilization.gpu,utilization.memory,clocks.sm,"
         "clocks.mem,temperature.gpu,temperature.memory,power.draw,power.limit,fan.speed,pstate,pci.bus_id")
    try:
        rows = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=gpu_bus_id,pid,process_name,used_memory",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return []
    f = lambda v: None if v.strip() in ("[N/A]", "N/A", "[Not Supported]", "") else float(v)
    procs: dict[str, list] = {}
    for line in apps:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            name = os.path.basename(parts[2]) or comm(parts[1])
            procs.setdefault(parts[0], []).append(
                {"name": name, "role": role_of(name), "vram": int((f(parts[3]) or 0) * (1 << 20))})
    gpus = []
    for line in rows:
        p = [x.strip() for x in line.split(",")]
        if len(p) < 15:
            continue
        gpus.append({
            "vendor": "nvidia", "name": p[1].replace("NVIDIA GeForce ", ""), "pci": p[14],
            "vram_used": (f(p[2]) or 0) * (1 << 20), "vram_total": (f(p[3]) or 0) * (1 << 20),
            "busy": f(p[4]), "mem_busy": f(p[5]), "sclk": f(p[6]), "mclk": f(p[7]),
            "temps": {k: v for k, v in (("edge", f(p[8])), ("mem", f(p[9]))) if v is not None},
            "power": f(p[10]), "power_cap": f(p[11]), "fan_pct": f(p[12]), "pstate": p[13],
            "procs": sorted(procs.get(p[14], []), key=lambda x: -x["vram"]),
        })
    return gpus


# ---------------------------------------------------------- model servers

class LlamaWatch:
    """One llama-server: what it is doing and how fast. Live speed is counted
    from /slots between polls, so it moves while a response is generated."""

    def __init__(self, spec: dict):
        self.name = spec.get("name", "llm")
        self.url = spec["url"].rstrip("/")
        kf = os.path.expanduser(spec.get("key_file", ""))
        self.key = read(kf, "") if kf else ""
        self.prev: dict = {}
        self.router = None

    def get(self, path, timeout=2.0, accept=None):
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(self.url + path, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        return json.loads(body) if body.lstrip()[:1] in "{[" else body

    def poll(self) -> dict:
        out = {"name": self.name, "url": self.url, "up": False, "state": "down"}
        try:
            urllib.request.urlopen(self.url + "/health", timeout=2).read()
            out["up"] = True
        except urllib.error.HTTPError as e:
            out["state"] = "loading" if e.code == 503 else f"error {e.code}"
            return out
        except (OSError, ValueError):
            return out
        model_q = ""
        # Router mode (Vulcan and Mercury's helper coders): /models says what
        # is loaded; a model that is not loaded uses no VRAM.
        try:
            models = self.get("/models")
            data = models.get("data", models) if isinstance(models, dict) else models
            if isinstance(data, list) and data and isinstance(data[0], dict) and "status" in data[0]:
                self.router = True
                loaded = None
                for m in data:
                    st = m.get("status")
                    st = st.get("value") if isinstance(st, dict) else st
                    if str(st).lower() in ("loaded", "loading"):
                        loaded = (m.get("id") or m.get("name"), str(st).lower())
                if not loaded:
                    out.update(state="unloaded", model=data[0].get("id"))
                    return out
                out["model"], model_q = loaded[0], f"?model={loaded[0]}"
                if loaded[1] == "loading":
                    out["state"] = "loading"
                    return out
        except Exception:
            pass
        try:
            props = self.get("/props" + model_q)
            if isinstance(props, dict):
                out["model"] = out.get("model") or props.get("model_alias") or \
                    os.path.basename(str(props.get("model_path", ""))).replace(".gguf", "")
                gs = props.get("default_generation_settings") or {}
                out["ctx"] = gs.get("n_ctx") or props.get("n_ctx")
                out["slots_total"] = props.get("total_slots")
                if props.get("is_sleeping"):
                    out["state"] = "sleeping"
                    return out
        except Exception:
            pass
        now = time.monotonic()
        out["state"], out["tps"] = "idle", 0.0
        try:
            slots = self.get("/slots" + model_q)
            busy = gen = 0
            tps = 0.0
            for s in slots if isinstance(slots, list) else []:
                sid = s.get("id")
                processing = bool(s.get("is_processing"))
                nt = s.get("next_token") or [{}]
                nt = nt[0] if isinstance(nt, list) and nt else nt if isinstance(nt, dict) else {}
                n_dec = nt.get("n_decoded", 0) or 0
                if processing:
                    busy += 1
                    if n_dec > 0:
                        gen += 1
                    prev = self.prev.get(sid)
                    if prev and n_dec >= prev[0] and now > prev[1]:
                        tps += (n_dec - prev[0]) / (now - prev[1])
                self.prev[sid] = (n_dec, now)
            out.update(slots_busy=busy, tps=round(tps, 1))
            out["state"] = "generating" if gen else "prompt" if busy else "idle"
        except Exception:
            pass
        try:
            # Prometheus text: llama-server sends it anyway; Strata sends
            # JSON unless asked for text/plain.
            text = self.get("/metrics" + model_q, accept="text/plain")
            if isinstance(text, str):
                metric = lambda key: re.search(
                    rf"^{re.escape(key)}(?:{{[^}}]*}})?\s+([0-9.eE+-]+)", text, re.M)
                for key, field in (("llamacpp:predicted_tokens_seconds", "last_gen_tps"),
                                   ("llamacpp:prompt_tokens_seconds", "last_prompt_tps"),
                                   ("llamacpp:tokens_predicted_total", "tokens_total"),
                                   ("llamacpp:requests_deferred", "queued"),
                                   # Strata (docs/DETAILS.md, "strata:" names)
                                   ("strata:last_decode_tok_s", "last_gen_tps"),
                                   ("strata:live_prefill_tok_s_mean", "last_prompt_tps"),
                                   ("vllm:num_requests_waiting", "queued")):
                    m = metric(key)
                    if m:
                        out[field] = round(float(m.group(1)), 1)
                if "strata:" in text:
                    self.strata_live(text, out, metric)
        except Exception:
            pass
        return out

    @staticmethod
    def strata_live(text: str, out: dict, metric) -> None:
        """Strata has no llama.cpp-style per-slot token counts, so its live
        speed and state come from its own metrics: strata:live_tok_s and
        strata:live_state{state="..."} (the state whose value is 1, or the
        only one listed)."""
        m = metric("strata:live_tok_s")
        live = float(m.group(1)) if m else 0.0
        states = re.findall(r'^strata:live_state\{[^}]*state="([^"]*)"[^}]*\}\s+([0-9.eE+-]+)', text, re.M)
        on = [s for s, v in states if float(v) >= 1] or ([states[0][0]] if len(states) == 1 else [])
        s = (on[0] if on else "").lower()
        if any(w in s for w in ("answer", "decod", "generat")) or live > 0:
            state = "generating"
        elif any(w in s for w in ("prompt", "prefill", "read")):
            state = "prompt"
        elif "load" in s:
            state = "loading"
        else:
            state = "idle"
        if out.get("state") in (None, "idle", "prompt", "generating"):
            out["state"] = state
        out["tps"] = round(live, 1) if state == "generating" else 0.0
        if state != "idle":
            out["slots_busy"] = max(out.get("slots_busy") or 0, 1)


def sd_status(spec: dict, gpus: list[dict]) -> dict:
    """sd-server holds its weights in RAM and takes the VRAM only while it
    renders, so its VRAM share is the clearest sign of a render. It also
    answers slowly or not at all mid-render."""
    url = spec["url"].rstrip("/")
    out = {"name": spec.get("name", "sd-server"), "url": url, "up": False, "state": "down"}
    try:
        urllib.request.urlopen(url + "/sdapi/v1/loras", timeout=1.5).read()
        out.update(up=True, state="idle")
    except urllib.error.HTTPError:
        out.update(up=True, state="idle")
    except (TimeoutError, socket.timeout):
        out.update(up=True, state="busy")
    except OSError as e:
        if "timed out" in str(e):
            out.update(up=True, state="busy")
        else:
            return out
    vram = sum(p["vram"] for g in gpus for p in g.get("procs", []) if p["role"] == "sd")
    out["vram"] = vram
    if vram > 3 * (1 << 30) and any((g.get("busy") or 0) > 30 for g in gpus):
        out["state"] = "rendering"
    return out


def gaming() -> dict | None:
    uid = os.getuid()
    try:
        flag = Path(f"/run/user/{uid}/vulcan-play").exists()
    except OSError:          # no /run/user for this account (Metis's system service)
        flag = False
    for cmd in glob.glob("/proc/[0-9]*/cmdline"):
        c = read(cmd, "") or ""
        if "SteamLaunch" in c:
            m = re.search(r"AppId=(\d+)", c.replace("\x00", " "))
            appid = m.group(1) if m else None
            name = None
            if appid:
                for lib in (Path.home() / ".steam/steam/steamapps", Path.home() / ".local/share/Steam/steamapps"):
                    acf = read(lib / f"appmanifest_{appid}.acf", "")
                    nm = re.search(r'"name"\s+"([^"]+)"', acf or "")
                    if nm:
                        name = nm.group(1)
                        break
            return {"appid": appid, "name": name or (f"Steam app {appid}" if appid else "Steam game")}
    return {"appid": None, "name": "Gaming mode (vulcan-mode play)"} if flag else None


def model_downloads() -> list[dict]:
    """Coding models this machine is fetching for forge (coder-models.py)."""
    p = Path.home() / "llm-stack" / "coder-models-state.json"
    try:
        st = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for alias, m in (st.get("models") or {}).items():
        if m.get("state") in ("queued", "downloading", "verifying", "failed"):
            total = m.get("total") or 0
            out.append({"alias": alias, "state": m["state"],
                        "pct": round(100 * (m.get("bytes") or 0) / total) if total else None,
                        "error": (m.get("error") or "")[:120] or None})
    return out


def units(names: list[str], user: bool) -> dict[str, str]:
    if not names:
        return {}
    cmd = ["systemctl"] + (["--user"] if user else []) + ["is-active"] + names
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return dict(zip(names, res))


# ------------------------------------------------------------------ server

# ------------------------------------------------------------------ controls

ACTIONS_LOG = Path.home() / ".local" / "state" / "lab-agent-actions.log"


class Controls:
    """The fixed list of things the dashboard may do on this machine."""

    def __init__(self, cfg: dict, llms: list):
        self.cfg, self.llms = cfg, llms
        self.lock = threading.Lock()
        self.running: dict | None = None
        self.recent: list[dict] = []

    def units(self) -> list[str]:
        out = [s.get("unit") for s in self.cfg["llm"] + self.cfg["sd"] if s.get("unit")]
        out += list(self.cfg.get("control_units") or [])
        return list(dict.fromkeys(u for u in out if u and re.fullmatch(r"[\w@.-]+", u)))

    def available(self) -> list[dict]:
        if not self.cfg.get("control_token"):
            return []
        out = []
        routers = [w for w in self.llms if w.router]
        if routers or self.cfg["sd"]:
            out.append({"id": "clear_vram", "label": "Clear VRAM",
                        "detail": "unload coding models; restart an idle sd-server holding VRAM"})
        for w in routers:
            out.append({"id": f"unload:{w.name}", "label": f"Unload {w.name}",
                        "detail": "the router frees the GPU; the next request loads again"})
        sd_units = {s.get("unit") for s in self.cfg["sd"]}
        for u in self.units():
            kind = "sd" if u in sd_units else "llm" if any(s.get("unit") == u for s in self.cfg["llm"]) else "unit"
            out.append({"id": f"restart:{u}", "label": f"Restart {u}", "kind": kind,
                        "detail": {"sd": "stops a render in progress (forge retries it)",
                                   "llm": "cuts off a request in progress"}.get(kind, "")})
            if u in (self.cfg.get("control_units") or []):
                out.append({"id": f"stop:{u}", "label": f"Stop {u}", "kind": kind})
                out.append({"id": f"start:{u}", "label": f"Start {u}", "kind": kind})
        return out

    def start(self, action: str) -> tuple[int, dict]:
        if action not in {a["id"] for a in self.available()}:
            return 400, {"ok": False, "message": f"not an action here: {action}"}
        with self.lock:
            if self.running:
                return 409, {"ok": False, "message": f"busy with {self.running['action']}"}
            self.running = {"action": action, "since": time.time()}
        threading.Thread(target=self._run, args=(action,), daemon=True).start()
        return 202, {"ok": True, "message": f"{action} started"}

    def _run(self, action: str):
        t0 = time.time()
        try:
            ok, msg = self._do(action)
        except Exception as exc:                    # report, never die
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        rec = {"action": action, "ok": ok, "message": msg[:300], "at": t0,
               "seconds": round(time.time() - t0, 1)}
        with self.lock:
            self.running = None
            self.recent = (self.recent + [rec])[-10:]
        try:
            ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ACTIONS_LOG, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    def snapshot(self) -> dict:
        with self.lock:
            return {"available": self.available(), "running": self.running,
                    "recent": list(self.recent)}

    # -- the actions ------------------------------------------------------
    def _systemctl(self, verb: str, unit: str) -> tuple[bool, str]:
        p = subprocess.run(["systemctl", "--user", verb, unit], capture_output=True, text=True,
                           timeout=180)
        return p.returncode == 0, (p.stderr or p.stdout).strip() or f"{verb} {unit}: done"

    def _unload(self, w) -> list[str]:
        done = []
        try:
            data = w.get("/models", timeout=5)
        except Exception:
            return done
        data = data.get("data", data) if isinstance(data, dict) else data
        for m in data if isinstance(data, list) else []:
            st = m.get("status")
            st = st.get("value") if isinstance(st, dict) else st
            if str(st).lower() not in ("loaded", "loading"):
                continue
            mid = m.get("id") or m.get("name")
            req = urllib.request.Request(
                w.url + "/models/unload", method="POST", data=json.dumps({"model": mid}).encode(),
                headers={"Content-Type": "application/json",
                         **({"Authorization": f"Bearer {w.key}"} if w.key else {})})
            try:
                urllib.request.urlopen(req, timeout=15).close()
                done.append(f"{w.name}: unloaded {mid}")
            except Exception as exc:
                done.append(f"{w.name}: could not unload {mid} ({exc})")
        return done

    def _do(self, action: str) -> tuple[bool, str]:
        verb, _, arg = action.partition(":")
        if verb in ("restart", "stop", "start"):
            return self._systemctl(verb, arg)
        if verb == "unload":
            w = next(w for w in self.llms if w.name == arg)
            msgs = self._unload(w)
            return True, "; ".join(msgs) or f"{arg}: nothing was loaded"
        if verb == "clear_vram":
            msgs = []
            for w in self.llms:
                if w.router:
                    msgs += self._unload(w)
            time.sleep(3)
            gpus = amd_gpus(self.cfg["gpu_names"]) + nvidia_gpus()
            for s in self.cfg["sd"]:
                st = sd_status(s, gpus)
                held = st.get("vram") or 0
                if st["state"] != "rendering" and held > (1 << 30) and s.get("unit"):
                    ok, m = self._systemctl("restart", s["unit"])
                    msgs.append(f"{s['unit']}: held {held / 2**30:.1f} GB while idle - "
                                f"{'restarted' if ok else 'restart failed: ' + m}")
            return True, "; ".join(msgs) or "nothing held VRAM"
        return False, f"unknown action {action}"


def optional(fn, default=None):
    """A reading that only some machines have: a failure leaves it out
    instead of blanking the whole snapshot."""
    try:
        return fn()
    except OSError:
        return default


class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cpu = CPU()
        self.llms = [LlamaWatch(s) for s in cfg["llm"]]
        self.controls = Controls(cfg, self.llms)
        self.lock = threading.Lock()
        self.snapshot: dict = {}
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            t0 = time.monotonic()
            try:
                gpus = amd_gpus(self.cfg["gpu_names"]) + nvidia_gpus() + \
                    optional(lambda: intel_gpus(self.cfg["gpu_names"]), [])
                snap = {"time": time.time(), "host": host(self.cpu, self.cfg["disks"]),
                        "gpus": gpus,
                        "llm": [w.poll() for w in self.llms],
                        "sd": [sd_status(s, gpus) for s in self.cfg["sd"]],
                        "gaming": optional(gaming),
                        "downloads": optional(model_downloads, []),
                        "services": {**units(self.cfg["system_units"], False),
                                     **units(self.cfg["user_units"], True),
                                     **units([u for u in self.controls.units()
                                              if u not in self.cfg["user_units"]], True)},
                        "controls": self.controls.snapshot()}
            except Exception as exc:                 # never die; report instead
                snap = {"time": time.time(), "error": f"{type(exc).__name__}: {exc}"}
            with self.lock:
                self.snapshot = snap
            time.sleep(max(0.2, 2.0 - (time.monotonic() - t0)))


def same_secret(given: str, secret: str) -> bool:
    """Constant-time, and safe for any header text: hmac.compare_digest
    raises TypeError on non-ASCII str, which dropped the connection."""
    import hmac
    return hmac.compare_digest(str(given).encode("utf-8", "surrogatepass"),
                               str(secret).encode("utf-8", "surrogatepass"))


def make_server(cfg: dict):
    agent = Agent(cfg)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def do_GET(self):
            if cfg["token"] and not same_secret(self.headers.get("X-Lab-Token", ""), cfg["token"]):
                return self.send_error(401)
            if self.path.split("?")[0] == "/health":
                body, ctype = b"ok", "text/plain"
            elif self.path.split("?")[0] == "/api/node":
                with agent.lock:
                    body = json.dumps(agent.snapshot).encode()
                ctype = "application/json"
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            secret = cfg.get("control_token") or ""
            if not secret or not same_secret(self.headers.get("X-Lab-Control", ""), secret):
                return self.send_error(403)
            if self.path.split("?")[0] != "/api/action":
                return self.send_error(404)
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdecimal():
                return self.send_error(400)
            n = int(lengths[0])
            if n > 4096:
                return self.send_error(413)
            try:
                raw = self.rfile.read(n)
                if len(raw) != n:
                    raise ValueError("incomplete body")
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected object")
                action = str(body.get("action", ""))
            except (ValueError, OSError):
                return self.send_error(400)
            code, out = agent.controls.start(action)
            body = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer(("0.0.0.0", int(cfg["port"])), Handler)


def serve(cfg: dict):
    with make_server(cfg) as server:
        server.serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="print one reading and exit")
    args = ap.parse_args()
    cfg = load_config()
    if args.once:
        cpu = CPU()
        cpu.percent()
        time.sleep(1)
        gpus = amd_gpus(cfg["gpu_names"]) + nvidia_gpus()
        print(json.dumps({"host": host(cpu, cfg["disks"]), "gpus": gpus,
                          "llm": [LlamaWatch(s).poll() for s in cfg["llm"]],
                          "sd": [sd_status(s, gpus) for s in cfg["sd"]], "gaming": optional(gaming),
                          "services": {**units(cfg["system_units"], False),
                                       **units(cfg["user_units"], True)}}, indent=2))
        return
    serve(cfg)


if __name__ == "__main__":
    main()
