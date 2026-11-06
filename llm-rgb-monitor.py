#!/usr/bin/env python3
"""
llm-rgb-monitor.py - drive RGB lighting from local LLM / GPU activity.

    green   idle / light use
    blue    actively generating
    red     sustained heavy load (prompt prefill, batching, long generation)
    amber   llama-server unreachable

WHY NOT gpu_busy_percent
------------------------
It is the obvious signal and it is the wrong one. Token decode on an LLM is
memory-bandwidth bound, not compute bound, so amdgpu reports busy% near 100%
whenever the model is emitting anything at all - the lights would sit red
forever and tell you nothing.

Power draw separates the cases properly. An RX 9070 XT idles around 10-30 W,
runs mid-range during decode, and approaches its board cap during prefill or
batched generation. So the primary signal is:

    util = power1_average / power1_cap        (hwmon, preferred)
    util = gpu_busy_percent / 100 * 0.85      (fallback, capped below red)

Power ramps lag the workload by a second or so, so an in-flight request from
llama-server's /metrics floors the signal into "active" immediately rather
than waiting for the rail to climb.

SAFETY
------
Only DIRECT STATIC COLOUR writes are ever issued; hardware effect modes are
never set. The documented OpenRGB incidents that permanently bricked MSI RGB
controllers involved sending effect modes, not static colours.

No device is touched unless named explicitly via --devices. There is no
"light up everything found" default, deliberately.

Usage
-----
    llm-rgb-monitor.py --probe              show detected sensors, exit
    llm-rgb-monitor.py --list               list OpenRGB devices, exit
    llm-rgb-monitor.py --dry-run            compute colours, touch nothing
    llm-rgb-monitor.py --devices "MSI" --test
    llm-rgb-monitor.py --devices "MSI"
"""

from __future__ import annotations

import argparse
import collections
import glob
import math
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

STOPS = [
    (0.00, (0, 255, 40)),
    (0.22, (0, 205, 130)),
    (0.48, (0, 90, 255)),
    (0.76, (165, 55, 205)),
    (1.00, (255, 0, 0)),
]

# Clock-speed ramp for the secondary metric. Deliberately cool and
# non-overlapping with STOPS above, so the two readouts never show the same
# colour for different reasons: deep indigo at rest through cyan to white.
SCLK_STOPS = [
    (0.00, (20, 0, 90)),
    (0.35, (0, 80, 200)),
    (0.70, (0, 200, 220)),
    (1.00, (255, 255, 255)),
]

COLOR_OFFLINE = (255, 120, 0)
COLOR_OFF = (0, 0, 0)
CARD_RE = re.compile(r"^card\d+$")


# ---------------------------------------------------------------------------
# GPU sampling
# ---------------------------------------------------------------------------

def _read_int(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class Gpu:
    """One discrete amdgpu card, sampled through sysfs + hwmon."""

    def __init__(self, card, devpath):
        self.card = card
        self.dev = devpath
        self.busy_file = os.path.join(devpath, "gpu_busy_percent")
        self.vram_used = os.path.join(devpath, "mem_info_vram_used")
        self.vram_total = os.path.join(devpath, "mem_info_vram_total")
        self.power_file = None
        self.power_cap = None
        self.sclk_file = None
        self._sclk_max = None
        dpm = os.path.join(devpath, "pp_dpm_sclk")
        self.dpm_file = dpm if os.path.exists(dpm) else None
        self._find_hwmon()

    def _find_hwmon(self):
        for hw in sorted(glob.glob(os.path.join(self.dev, "hwmon", "hwmon*"))):
            # power1_average is the smoothed board draw; power1_input is the
            # instantaneous reading some cards expose instead.
            for name in ("power1_average", "power1_input"):
                cand = os.path.join(hw, name)
                if _read_int(cand) is not None:
                    self.power_file = cand
                    break
            cand = os.path.join(hw, "freq1_input")
            if _read_int(cand) is not None:
                self.sclk_file = cand
            for name in ("power1_cap", "power1_cap_max"):
                cand = os.path.join(hw, name)
                val = _read_int(cand)
                if val:
                    self.power_cap = cand
                    break
            if self.power_file and self.power_cap:
                return

    @property
    def name(self):
        return self.card

    def vram_fraction(self):
        used = _read_int(self.vram_used)
        total = _read_int(self.vram_total)
        if not used or not total:
            return 0.0
        return max(0.0, min(1.0, used / total))

    def sclk_mhz(self):
        """Current shader clock in MHz, or None.

        hwmon freq1_input is in Hz and gives fine granularity. pp_dpm_sclk is
        the fallback but only reports which DPM *state* is active - often just
        three levels - which is too coarse to average meaningfully.
        """
        v = _read_int(self.sclk_file) if self.sclk_file else None
        if v:
            return v / 1e6
        if self.dpm_file:
            try:
                with open(self.dpm_file) as fh:
                    for line in fh:
                        if "*" in line:
                            return float(line.split(":")[1].strip().rstrip("Mhz *"))
            except (OSError, ValueError, IndexError):
                pass
        return None

    def sclk_max_mhz(self):
        """Ceiling from the DPM table's highest state. Cached after first read."""
        if self._sclk_max is not None:
            return self._sclk_max
        best = None
        if self.dpm_file:
            try:
                with open(self.dpm_file) as fh:
                    for line in fh:
                        try:
                            mhz = float(line.split(":")[1].strip()
                                        .replace("*", "").strip().rstrip("Mhz").strip())
                        except (ValueError, IndexError):
                            continue
                        best = mhz if best is None else max(best, mhz)
            except OSError:
                pass
        self._sclk_max = best or 3000.0   # sane RDNA4 fallback
        return self._sclk_max

    def util(self):
        """Return (utilisation 0-1, source string)."""
        if self.power_file and self.power_cap:
            draw = _read_int(self.power_file)
            cap = _read_int(self.power_cap)
            if draw is not None and cap:
                return max(0.0, min(1.0, draw / cap)), "power"
        busy = _read_int(self.busy_file)
        if busy is not None:
            # Capped at 0.85 so the fallback path cannot reach full red on
            # its own - busy% saturating is exactly the failure we are
            # working around, so it should not be able to scream "overload".
            return max(0.0, min(1.0, busy / 100.0)) * 0.85, "busy%"
        return 0.0, "none"

    def describe(self):
        draw = _read_int(self.power_file) if self.power_file else None
        cap = _read_int(self.power_cap) if self.power_cap else None
        total = _read_int(self.vram_total)
        parts = [self.card]
        if total:
            parts.append(f"{total / 1024**3:.1f} GiB VRAM")
        if draw is not None and cap:
            parts.append(f"{draw / 1e6:.1f}W / {cap / 1e6:.0f}W cap")
        else:
            parts.append("no hwmon power -> busy% fallback")
        return "  ".join(parts)


def discover_gpus(min_vram_gib=8.0):
    """Discrete amdgpu cards only.

    The 5700G's integrated Vega carves a small VRAM window out of system RAM,
    well under 8 GiB, so a size filter excludes it without hardcoding PCI IDs
    that would break on a hardware change.
    """
    out = []
    try:
        entries = sorted(os.listdir("/sys/class/drm"))
    except OSError:
        return out
    for entry in entries:
        if not CARD_RE.match(entry):
            continue
        dev = os.path.join("/sys/class/drm", entry, "device")
        total = _read_int(os.path.join(dev, "mem_info_vram_total"))
        if total is None or total < min_vram_gib * 1024**3:
            continue
        out.append(Gpu(entry, dev))
    return out


# ---------------------------------------------------------------------------
# llama-server probe
# ---------------------------------------------------------------------------

class ServerProbe:
    """Polls llama-server /metrics. Advisory only - never fatal.

    Metric names have shifted between llama.cpp releases, so lookups are
    substring matches and any parse failure degrades to zero rather than
    raising.
    """

    def __init__(self, base_url, api_key, interval=2.0):
        self.url = base_url.rstrip("/") + "/metrics"
        self.api_key = api_key
        self.interval = interval
        self._last = -1e9
        self.healthy = True
        self.processing = 0.0
        self.deferred = 0.0
        self._auth_backoff_until = 0.0
        self._auth_warned = False

    def poll(self):
        now = time.monotonic()
        if now - self._last < self.interval or now < self._auth_backoff_until:
            return
        self._last = now
        req = urllib.request.Request(self.url)
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # The server ANSWERED, so it is up. Treating this like an
            # unreachable server showed the amber "offline" pulse while
            # llama-server was running fine. Colour falls back to GPU power.
            self.healthy = True
            self.processing = self.deferred = 0.0
            if exc.code in (401, 403):
                # Our key is wrong. Retrying every couple of seconds just
                # fills the server's log with "Invalid API Key" - back off,
                # and say why once.
                self._auth_backoff_until = now + 300
                if not self._auth_warned:
                    self._auth_warned = True
                    sys.stderr.write(
                        f"llama-server rejected our API key (HTTP {exc.code}). "
                        "Point LLAMA_API_KEY_FILE in monitor.env at the key the "
                        "server uses (~/.coder-api-key on Apollo). Colour will "
                        "follow GPU power alone; retrying every 5 minutes.\n")
            return
        except (urllib.error.URLError, OSError, ValueError):
            self.healthy = False
            self.processing = self.deferred = 0.0
            return
        self.healthy = True
        self.processing = self._scrape(body, "requests_processing")
        self.deferred = self._scrape(body, "requests_deferred")

    @staticmethod
    def _scrape(body, needle):
        for line in body.splitlines():
            if line.startswith("#") or needle not in line:
                continue
            parts = line.rsplit(None, 1)
            if len(parts) == 2:
                try:
                    return float(parts[1])
                except ValueError:
                    continue
        return 0.0


class ComfyProbe:
    """Polls ComfyUI instead of llama-server, for Apollo's image-gen role.

    Same interface as ServerProbe (healthy / processing / deferred) so the
    main loop does not care which backend it is watching:

      healthy     /system_stats answers
      processing  a prompt is in queue_running
      deferred    prompts are waiting in queue_pending

    Diffusion is compute-bound rather than bandwidth-bound, so GPU power
    tracks real work closely here - the queue signal mostly just makes the
    colour react before the power rail has ramped.
    """

    def __init__(self, base_url, interval=2.0):
        self.base = base_url.rstrip("/")
        self.interval = interval
        self._last = -1e9
        self.healthy = True
        self.processing = 0.0
        self.deferred = 0.0

    def poll(self):
        now = time.monotonic()
        if now - self._last < self.interval:
            return
        self._last = now
        import json
        try:
            with urllib.request.urlopen(self.base + "/queue", timeout=2.0) as r:
                q = json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError):
            self.healthy = False
            self.processing = self.deferred = 0.0
            return
        self.healthy = True
        self.processing = float(len(q.get("queue_running") or []))
        self.deferred = float(len(q.get("queue_pending") or []))


class NullProbe:
    """No backend to watch - GPU power alone drives the colour, and the
    offline amber pulse never fires."""
    healthy = True
    processing = 0.0
    deferred = 0.0

    def poll(self):
        pass


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------

def load_to_color(load, stops=None):
    stops = stops or STOPS
    load = max(0.0, min(1.0, load))
    for i in range(len(stops) - 1):
        lo_p, lo_c = stops[i]
        hi_p, hi_c = stops[i + 1]
        if lo_p <= load <= hi_p:
            span = hi_p - lo_p
            t = 0.0 if span == 0 else (load - lo_p) / span
            return tuple(int(round(lo_c[k] + (hi_c[k] - lo_c[k]) * t))
                         for k in range(3))
    return stops[-1][1]


class RollingMean:
    """Time-windowed mean. Keeps (timestamp, value) and drops anything older
    than `window` seconds, so the average reflects a real time span rather
    than a sample count - important when the poll interval changes."""

    def __init__(self, window=300.0):
        self.window = float(window)
        self.samples = collections.deque()

    def add(self, value):
        now = time.monotonic()
        self.samples.append((now, value))
        cutoff = now - self.window
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def mean(self):
        if not self.samples:
            return 0.0
        return sum(v for _, v in self.samples) / len(self.samples)

    def span(self):
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1][0] - self.samples[0][0]


def scale(rgb, factor):
    return tuple(max(0, min(255, int(round(c * factor)))) for c in rgb)


def distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


# ---------------------------------------------------------------------------
# OpenRGB sink
# ---------------------------------------------------------------------------

class CliSink:
    """Drives OpenRGB through its command-line client, not the SDK.

    WHY NOT THE SDK
    ---------------
    OpenRGB 1.0 moved the SDK to protocol 6, which addresses controllers by a
    unique 32-bit ID instead of a list index. openrgb-python hardcodes
    OPENRGB_PROTOCOL_VERSION = 4 (still true at git HEAD), so reads succeed
    but every write desyncs the stream and times out after the client's
    10-second lock:

        openrgb.utils.OpenRGBDisconnected:
            SDK server did not respond to previous request

    The CLI with --noautoconnect talks to USB directly and works today. Swap
    back to the SDK path once openrgb-python supports protocol 6 - the colour
    logic above is unchanged either way.

    COST
    ----
    One process spawn per device per update, and openrgb re-enumerates
    hardware on each invocation. That is far heavier than an SDK write, which
    is why the default interval here is seconds rather than 0.25s and why
    --min-delta matters more: an update that would not visibly change the
    colour is skipped entirely rather than spawning two processes to repaint
    the same thing.
    """

    def mode_for(self, dev):
        """Mode name to pass for a device.

        The Wraith Prism exposes Direct but has been seen to ignore colours
        set that way from the CLI while accepting them under another mode.
        Override per device with RGB_MODE_<something> if needed.
        """
        return self.modes.get(dev, "direct")

    def __init__(self, devices, dry_run=False, binary="openrgb", timeout=10.0,
                 modes=None):
        self.devices = [d.strip() for d in devices if d.strip()]
        self.dry_run = dry_run
        self.binary = binary
        self.timeout = timeout
        self.modes = modes or {}
        self.last_ms = 0.0

    def connect(self):
        if self.dry_run:
            return True
        import shutil
        if not shutil.which(self.binary):
            sys.stderr.write(f"'{self.binary}' not on PATH.\n")
            return False
        for d in self.devices:
            print(f"  target: {d}")
        if not self.devices:
            sys.stderr.write("No device names given.\n")
            return False
        return True

    def set(self, rgb, load=None, brightness=1.0, colors=None):
        """One command per device.

        `colors` optionally maps a device name to its own RGB tuple, so the
        Wraith can show a different metric from the case lighting. Anything
        not in that map takes `rgb`.

        Zones not named are reset by openrgb, so every call must address the
        whole device at once - which is what we want for flat colour.
        """
        if self.dry_run:
            return True
        import os, subprocess
        colors = colors or {}
        # openrgb is a Qt application. Run from a systemd service there is no
        # display, and Qt aborts before doing any work - exit 255 with nothing
        # on stderr. The SDK server unit sets this for the same reason.
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        t0 = time.monotonic()
        ok = True
        procs = []
        for dev in self.devices:
            dev_rgb = colors.get(dev, rgb)
            hexcol = "%02X%02X%02X" % dev_rgb
            cmd = [self.binary, "--noautoconnect", "--device", dev,
                   "--mode", self.mode_for(dev), "--color", hexcol]
            try:
                # Capture stdout too: openrgb reports some failures there
                # rather than on stderr, which made exit 255 look silent.
                procs.append(subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=env))
            except OSError as exc:
                sys.stderr.write(f"spawn failed for {dev}: {exc}\n")
                ok = False
        for pr in procs:
            try:
                out, _ = pr.communicate(timeout=self.timeout)
                if pr.returncode != 0:
                    msg = out.decode("utf-8", "replace").strip()[:300]
                    sys.stderr.write(
                        f"openrgb exited {pr.returncode}: "
                        f"{msg or '(no output - usually Qt failing to start)'}\n")
                    ok = False
            except subprocess.TimeoutExpired:
                pr.kill()
                sys.stderr.write("openrgb timed out; killed\n")
                ok = False
        self.last_ms = (time.monotonic() - t0) * 1000.0
        return ok

    def close(self, final_rgb):
        if final_rgb is not None:
            try:
                self.set(final_rgb)
            except Exception:
                pass


# ---------------------------------------------------------------------------

_running = True


def _stop(_sig, _frm):
    global _running
    _running = False


def build_parser():
    e = os.environ.get
    p = argparse.ArgumentParser(
        description="Drive RGB from local LLM / GPU activity.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--devices", default=e("RGB_DEVICES", ""),
                   help="Comma-separated substrings of OpenRGB device names.")
    p.add_argument("--list", action="store_true", help="List OpenRGB devices, exit.")
    p.add_argument("--probe", action="store_true", help="Show detected sensors, exit.")
    p.add_argument("--test", action="store_true",
                   help="Cycle green/blue/red/amber once, exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute colours without writing to hardware.")
    p.add_argument("--host", default=e("RGB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(e("RGB_PORT", "6742")))
    p.add_argument("--backend", choices=["llama", "comfyui", "none"],
                   default=e("RGB_BACKEND", "llama"),
                   help="What to watch for activity and health. 'comfyui' for "
                        "Apollo's image-gen role, 'llama' for Gemini, 'none' "
                        "to use GPU power alone. Default llama.")
    p.add_argument("--server-url", default=e("LLAMA_URL", "http://127.0.0.1:8080"))
    p.add_argument("--comfy-url", default=e("COMFY_URL", "http://127.0.0.1:8188"))
    p.add_argument("--api-key-file",
                   default=e("LLAMA_API_KEY_FILE",
                             os.path.expanduser("~/.llama-api-key")))
    p.add_argument("--interval", type=float, default=float(e("RGB_INTERVAL", "2.0")))
    p.add_argument("--smoothing", type=float, default=float(e("RGB_SMOOTHING", "0.35")),
                   help="EMA factor 0-1. Lower is smoother. Default 0.2.")
    p.add_argument("--brightness", type=float,
                   default=float(e("RGB_BRIGHTNESS", "1.0")))
    p.add_argument("--combine", choices=["max", "mean"], default=e("RGB_COMBINE", "max"))
    p.add_argument("--idle-floor", type=float, default=float(e("RGB_IDLE_FLOOR", "0.12")),
                   help="Utilisation at or below this reads as fully idle. "
                        "Absorbs the cards' resting power draw. Default 0.12.")
    p.add_argument("--active-floor", type=float,
                   default=float(e("RGB_ACTIVE_FLOOR", "0.45")),
                   help="Signal is floored here whenever llama-server reports a "
                        "request in flight, so colour changes immediately "
                        "instead of waiting for the power rail. Default 0.45.")
    p.add_argument("--min-delta", type=int, default=int(e("RGB_MIN_DELTA", "18")),
                   help="Skip the write if colour moved less than this.")
    p.add_argument("--openrgb", default=e("RGB_OPENRGB_BIN", "openrgb"),
                   help="Path to the openrgb binary.")
    p.add_argument("--sclk-devices", default=e("RGB_SCLK_DEVICES", ""),
                   help="Comma-separated device names driven by the clock "
                        "metric instead of the load metric. Uses a cool "
                        "indigo/cyan/white ramp for contrast.")
    p.add_argument("--sclk-window", type=float,
                   default=float(e("RGB_SCLK_WINDOW", "300")),
                   help="Rolling average window in seconds. Default 300 "
                        "(5 minutes).")
    p.add_argument("--sclk-floor", type=float,
                   default=float(e("RGB_SCLK_FLOOR", "0.05")),
                   help="Clock fraction at or below this reads as fully idle.")
    p.add_argument("--modes", default=e("RGB_MODES", ""),
                   help="Per-device mode overrides, e.g. "
                        "'AMD Wraith Prism=static'. Comma-separated.")
    p.add_argument("--off-on-exit", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def do_probe():
    gpus = discover_gpus()
    if not gpus:
        print("No discrete amdgpu cards found with >=8 GiB VRAM.")
        print("Check: lsmod | grep amdgpu ; ls /sys/class/drm/")
        return 1
    print(f"{len(gpus)} discrete GPU(s):\n")
    for g in gpus:
        print("  " + g.describe())
        u, src = g.util()
        print(f"      util now: {u:.3f}  (source: {src})  "
              f"VRAM {g.vram_fraction() * 100:.0f}%")
    print()
    any_power = any(g.power_file for g in gpus)
    if not any_power:
        print("WARNING: no hwmon power sensor readable on any card.")
        print("Falling back to gpu_busy_percent, which saturates during token")
        print("decode - expect less useful colour separation.")
    return 0


def do_list(binary):
    """List devices through the openrgb command line - the same path the
    daemon uses to drive them, so anything listed here is controllable.

    Earlier versions listed through the OpenRGB SDK server. OpenRGB 1.0 speaks
    SDK protocol 6, which openrgb-python cannot write to, so the daemon moved
    to the CLI - and listing was the last thing still needing the server.
    """
    import os, re, shutil, subprocess
    if not shutil.which(binary):
        sys.stderr.write(f"'{binary}' not on PATH. Install the OpenRGB AppImage "
                         f"as /usr/local/bin/openrgb.\n")
        return 1
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")   # Qt aborts without a display
    try:
        p = subprocess.run([binary, "--noautoconnect", "--list-devices"],
                           capture_output=True, text=True, timeout=90, env=env)
    except subprocess.TimeoutExpired:
        sys.stderr.write("openrgb --list-devices timed out.\n")
        return 1
    out = p.stdout or ""
    print(out.rstrip() or "(no output)")
    # Device headers are unindented "N: Name"; zones and properties are indented.
    names = re.findall(r"^(\d+):\s+(.+?)\s*$", out, re.M)
    if not names:
        if p.stderr.strip():
            sys.stderr.write(p.stderr.strip()[:600] + "\n")
        sys.stderr.write("No devices listed. Check the controllers appear in lsusb,\n"
                         "and that the udev phase ran for each one.\n")
        return 2
    print("\nNames for RGB_DEVICES - exact, comma-separated:")
    for _, name in names:
        flag = "   <- NEVER add this one" if re.search(r"\bMSI\b|MYSTIC", name, re.I) else ""
        print(f"  {name}{flag}")
    return 0


def do_test(sink, brightness):
    seq = [("green / idle", STOPS[0][1]),
           ("teal / light", STOPS[1][1]),
           ("blue / active", STOPS[2][1]),
           ("violet / heavy", STOPS[3][1]),
           ("red / loaded", STOPS[-1][1]),
           ("amber / offline", COLOR_OFFLINE)]
    for label, rgb in seq:
        c = scale(rgb, brightness)
        sink.set(c, brightness=brightness)
        print(f"  {label:<17} {c}   ({sink.last_ms:.0f} ms)")
        time.sleep(1.2)
    sink.set(scale(STOPS[0][1], brightness), brightness=brightness)
    print("\n  If the round-trip above is slower than your --interval, raise")
    print("  the interval - openrgb re-enumerates hardware on every call.")
    return 0


def main():
    args = build_parser().parse_args()

    if args.probe:
        return do_probe()
    if args.list:
        return do_list(args.openrgb)

    patterns = [s for s in args.devices.split(",") if s.strip()]
    if not patterns and not args.dry_run:
        sys.stderr.write(
            "Refusing to run without --devices. Discover them with --list.\n"
            "This is deliberate: writing blindly to every RGB controller\n"
            "OpenRGB happens to find is how people break hardware.\n")
        return 2

    modes = {}
    for pair in args.modes.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            modes[k.strip()] = v.strip()

    sclk_devices = [d.strip() for d in args.sclk_devices.split(",") if d.strip()]

    sink = CliSink(patterns, args.dry_run, binary=args.openrgb, modes=modes)
    if not sink.connect():
        return 1

    if args.test:
        rc = do_test(sink, args.brightness)
        sink.close(None)
        return rc

    gpus = discover_gpus()
    if not gpus:
        sys.stderr.write("No discrete amdgpu cards found. Try --probe.\n")
        return 1
    print(f"Monitoring {len(gpus)} GPU(s): {', '.join(g.name for g in gpus)}")
    for g in gpus:
        print("  " + g.describe())

    api_key = None
    if args.backend == "llama" and args.api_key_file and not os.path.exists(args.api_key_file):
        sys.stderr.write(f"WARNING: API key file {args.api_key_file} not found - "
                         "requests to llama-server will carry no key and be "
                         "rejected if it requires one.\n")
    if args.api_key_file and os.path.exists(args.api_key_file):
        try:
            with open(args.api_key_file) as fh:
                api_key = fh.read().strip() or None
        except OSError:
            pass

    if args.backend == "comfyui":
        probe = ComfyProbe(args.comfy_url)
        print(f"Watching ComfyUI at {args.comfy_url}")
    elif args.backend == "none":
        probe = NullProbe()
        print("No backend probe - GPU power only.")
    else:
        probe = ServerProbe(args.server_url, api_key)
        print(f"Watching llama-server at {args.server_url}")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    ema = 0.0
    last = None
    last_colors = {}
    retry_at = 0.0
    idle_floor = max(0.0, min(0.9, args.idle_floor))
    sclk_avg = RollingMean(args.sclk_window)
    if sclk_devices:
        print(f"Clock metric on: {', '.join(sclk_devices)} "
              f"({args.sclk_window:.0f}s rolling average)")
        for g in gpus:
            print(f"  {g.card} ceiling {g.sclk_max_mhz():.0f} MHz")

    while _running:
        probe.poll()

        samples = [g.util()[0] for g in gpus]
        raw = max(samples) if args.combine == "max" else sum(samples) / len(samples)

        # Rescale so resting draw reads as true idle rather than pale green.
        raw = max(0.0, (raw - idle_floor) / max(1e-6, 1.0 - idle_floor))

        if probe.healthy and probe.processing > 0:
            raw = max(raw, args.active_floor)
        if probe.deferred > 0:
            raw = max(raw, 0.9)

        a = max(0.01, min(1.0, args.smoothing))
        ema = ema * (1.0 - a) + raw * a

        # Clock metric: mean of both cards as a fraction of their DPM ceiling,
        # fed into a time-windowed average. Deliberately NOT smoothed by the
        # EMA above - the whole point is a slow-moving readout that shows how
        # hard the machine has been working lately, not right now.
        sclk_now = [g.sclk_mhz() for g in gpus]
        sclk_now = [v for v in sclk_now if v is not None]
        if sclk_now:
            frac = sum(v / g.sclk_max_mhz() for v, g in zip(sclk_now, gpus)) / len(sclk_now)
            sclk_avg.add(max(0.0, min(1.0, frac)))

        sc = sclk_avg.mean()
        sc = max(0.0, (sc - args.sclk_floor) / max(1e-6, 1.0 - args.sclk_floor))
        sclk_rgb = scale(load_to_color(sc, SCLK_STOPS), args.brightness)
        dev_colors = {d: sclk_rgb for d in sclk_devices}

        if not probe.healthy:
            # Slow breathing amber so an unreachable server is obvious at a
            # glance and distinguishable from any steady colour.
            phase = (math.sin(time.monotonic() * 1.6) + 1.0) / 2.0
            target = scale(COLOR_OFFLINE, args.brightness * (0.25 + 0.75 * phase))
            dev_colors = {}          # offline pulse applies to everything
            force = True
        else:
            target = scale(load_to_color(ema), args.brightness)
            force = False

        if args.verbose:
            print(f"util={[f'{s:.2f}' for s in samples]} ema={ema:.3f} "
                  f"proc={probe.processing:.0f} defer={probe.deferred:.0f} "
                  f"up={probe.healthy} rgb={target} "
                  f"sclk={sc:.3f}/{sclk_avg.span():.0f}s rgb2={sclk_rgb} "
                  f"({sink.last_ms:.0f}ms)", flush=True)

        moved = (last is None or distance(target, last) >= args.min_delta
                 or any(distance(v, last_colors.get(k, (-99, -99, -99)))
                        >= args.min_delta for k, v in dev_colors.items()))
        if force or moved:
            if args.dry_run:
                print(f"  would set {target}", flush=True)
                last = target
            elif time.monotonic() >= retry_at:
                if sink.set(target, load=ema, brightness=args.brightness,
                            colors=dev_colors):
                    last = target
                    last_colors = dict(dev_colors)
                else:
                    sys.stderr.write("Lost OpenRGB connection; retry in 5s.\n")
                    retry_at = time.monotonic() + 5.0
                    last = None
                    sink.close(None)
                    sink = CliSink(patterns, args.dry_run,
                                   binary=args.openrgb, modes=modes)
                    sink.connect()

        time.sleep(args.interval)

    print("Shutting down.")
    sink.close(COLOR_OFF if args.off_on_exit
               else scale(STOPS[0][1], args.brightness))
    return 0


if __name__ == "__main__":
    sys.exit(main())
