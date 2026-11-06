#!/usr/bin/env python3
"""
mercury-hires-probe - find the largest hires setting Mercury can finish.

Sends the wallpaper hero request at very few steps, so each try takes minutes
instead of the full render's ten-plus. Same resolution, upscaler and VAE work
as the real thing; only the step counts are cut. The first setting that
succeeds is the one to put in the run's plan.json.

    python3 mercury-hires-probe.py            # stop at the first success
    python3 mercury-hires-probe.py --all      # try every setting
    python3 mercury-hires-probe.py 1.5        # try only these scales
    python3 mercury-hires-probe.py --server vulcan 2.0   # probe another server

A failed hires pass can leave sd-server unable to run anything until it is
restarted (seen on Mercury: every later request failed in 2 s). After any
failure the probe sends a tiny health-check image; if that fails too, it
stops and tells you to restart sd-server rather than report false failures.

Reads the server from ~/forge/config.toml: the [[images.servers]] entry
named by --server (default "mercury"), or the older single [images] url.
"""
import base64, json, sys, time, tomllib, urllib.error, urllib.request
from pathlib import Path

cfg = tomllib.loads(Path("~/forge/config.toml").expanduser().read_text())
icfg = cfg.get("images", {})
want = sys.argv[sys.argv.index("--server") + 1] if "--server" in sys.argv else "mercury"
match = [s for s in icfg.get("servers", []) if s.get("name") == want]
if match:
    base = match[0]["url"]
elif icfg.get("url") and want == "mercury":
    base = icfg["url"]
else:
    sys.exit(f"No image server named {want!r} in ~/forge/config.toml")
URL = base.rstrip("/") + "/sdapi/v1/txt2img"
print(f"probing {want} at {base}")

BASE = {"prompt": "Two hexagonal cores of sky-blue light orbiting each other, "
                  "dark cinematic digital art, deep charcoal background.",
        "negative_prompt": "text, letters, watermark",
        "width": 1920, "height": 1088, "steps": 4, "cfg_scale": 3.5,
        "seed": 2102, "batch_size": 1, "n_iter": 1}

# Best quality first. (label, extra params)
# The upscaler is not the limit - Mercury's log showed RealESRGAN finishing and
# the hires SECOND PASS failing: one transformer block at 3840x2176 needs
# ~9.8 GB of working memory, and only ~4.5 GB was free next to the resident
# weights. So the sizes step down, the upscaler stays.
ESR = {"enable_hr": True, "hr_upscaler": "RealESRGAN_x4plus"}
TRIES = [
    ("base only, 1920x1088 (sanity)", {}),
    ("x2.0 -> 3840x2176", dict(ESR, hr_scale=2.0)),
    ("x1.5 -> 2880x1632", dict(ESR, hr_scale=1.5)),
    ("x1.25 -> 2400x1360", dict(ESR, hr_scale=1.25)),
]


def png_size(raw):
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def attempt(label, extra):
    body = dict(BASE, **extra)
    if body.get("enable_hr"):
        body.update(denoising_strength=0.35, hr_second_pass_steps=4, hr_steps=4)
    print(f"-> {label} ...", flush=True)
    t0 = time.monotonic()
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"   FAIL after {time.monotonic()-t0:.0f}s: HTTP {e.code} "
              f"{e.read().decode('utf-8', 'replace')[:200]}")
        return False
    except OSError as e:
        print(f"   FAIL after {time.monotonic()-t0:.0f}s: {e} (did sd-server crash?)")
        return False
    raw = base64.b64decode((data.get("images") or [""])[0] or b"")
    size = png_size(raw)
    secs = time.monotonic() - t0
    if not size:
        print(f"   FAIL after {secs:.0f}s: no image returned")
        return False
    out = Path(f"/tmp/probe-{size[0]}x{size[1]}.png")
    out.write_bytes(raw)
    print(f"   OK   {size[0]}x{size[1]} in {secs:.0f}s  ({out})")
    return True


def healthy():
    body = dict(BASE, width=512, height=512, steps=1)
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return bool(json.loads(r.read()).get("images"))
    except (urllib.error.HTTPError, OSError):
        return False


run_all = "--all" in sys.argv
only = {float(a) for a in sys.argv[1:] if a.replace(".", "", 1).isdigit()}  # e.g. 2.0
tries = [t for t in TRIES if not only or t[0].startswith("base")
         or t[1].get("hr_scale") in only]
for label, extra in tries:
    ok = attempt(label, extra)
    if label.startswith("base") and not ok:
        print("\nEven the base image failed - the problem is not hires. "
              "Restart sd-server and check its log.")
        sys.exit(1)
    if not ok:
        print("   checking sd-server still works ...", flush=True)
        if not healthy():
            print("\nsd-server can no longer make even a tiny image - the failed "
                  "pass left it broken, so further results would be false.\n"
                  f"On {want}:  systemctl --user restart sd-server\n"
                  "Then rerun with the next scale, e.g.:  "
                  f"python3 mercury-hires-probe.py --server {want} 1.5")
            sys.exit(2)
    if ok and not label.startswith("base") and not run_all:
        print(f"\nUse this one: {label}")
        break
