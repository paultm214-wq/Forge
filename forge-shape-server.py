#!/usr/bin/env python3
"""
forge-shape-server - Hunyuan3D 2.1 shape generation for forge, on Vulcan.

One rendered image in, one untextured 3D model (GLB) out. forge sends an
image asset's render here when the asset has a "shape" (see [shapes] in
forge's config.toml). install-vulcan-shape.sh installs this as the systemd
user service forge-shape on port 7870.

    POST /v1/shape     {"image": base64 PNG/JPEG, "seed": 0, "steps": 30,
                        "guidance": 5.0, "octree_resolution": 256,
                        "num_chunks": 8000, "max_faces": 20000,
                        "remove_background": true|false (default: when the
                        image has no transparency)}
                    -> {"glb": base64, "faces": n, "vertices": n,
                        "seconds": s, "seed": n, "background_removed": bool}
    POST /v1/release   unload the model, give the GPU back (restarting any
                       service this server stopped)
    GET  /health       {"status": "ok", "model", "loaded", "stops", "jobs"}
    GET  /v1/models    the model and whether it is loaded

THE GPU
-------
The shape model needs about 10 GB of VRAM (Tencent's figure). Vulcan's other
GPU users: sd-server keeps its weights in RAM (--offload-to-cpu) and takes
VRAM only while rendering, and the helper coder (llama-server in router
mode) loads its model only on demand. forge never sends a shape while it is
rendering or coding on Vulcan, and it unloads the helper coder first. Here,
before loading, the server checks free VRAM; if it is short it unloads the
local coder's models, and if still short it stops the --stop-unit services
(default: sd-server) until it is released. The model stays loaded between
jobs and is released when forge says so or after --idle seconds.

Licence: Hunyuan3D 2.1 is under the Tencent Hunyuan 3D 2.1 Community License
Agreement (see NOTICE beside this file) - it does not apply in the EU, the UK
or South Korea.
"""

import argparse
import base64
import gc
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "hunyuan3d-2.1-shape"
MAX_BODY = 64 * 2**20
ARGS = None
GPU = threading.Lock()          # one job (or load/release) at a time
STATE = {"pipeline": None, "rembg": None, "post": None, "last_used": 0.0,
         "stopped": [], "jobs": 0}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- the GPU

def vram_free_gb() -> float:
    import torch
    free, _ = torch.cuda.mem_get_info()
    return free / 2**30


def gpu_holders() -> str:
    """Who holds the GPU, from nvidia-smi: "pid name MiB, ..." ("" if it
    cannot tell). Put in the out-of-VRAM error, so the log says it."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    rows = []
    for ln in out.strip().splitlines():
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) == 3:
            me = " (this server)" if parts[0] == str(os.getpid()) else ""
            rows.append(f"{parts[0]} {os.path.basename(parts[1])}{me} {parts[2]} MiB")
    return ", ".join(rows)


def own_gpu_mib() -> int:
    """What this process itself holds on the GPU, per nvidia-smi."""
    holders = gpu_holders()
    for part in holders.split(", "):
        if "(this server)" in part:
            try:
                return int(part.rsplit(" ", 2)[-2])
            except ValueError:
                return 0
    return 0


def coder_key() -> str:
    try:
        return open(os.path.expanduser(ARGS.coder_key_file)).read().strip()
    except OSError:
        return ""


def unload_coder() -> None:
    """Unload whatever the local coder (llama-server, router mode) holds."""
    if not ARGS.coder_url:
        return
    headers = {"Content-Type": "application/json"}
    key = coder_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(ARGS.coder_url.rstrip("/") + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            models = json.loads(r.read()).get("data", [])
    except Exception as exc:
        log(f"coder: not reachable ({exc}) - nothing to unload")
        return
    for m in models:
        if (m.get("status") or {}).get("value") in ("loaded", "loading"):
            try:
                req = urllib.request.Request(ARGS.coder_url.rstrip("/") + "/models/unload",
                                             data=json.dumps({"model": m.get("id")}).encode(),
                                             headers=headers, method="POST")
                urllib.request.urlopen(req, timeout=30).close()
                log(f"coder: unloaded {m.get('id')}")
            except Exception as exc:
                log(f"coder: could not unload {m.get('id')} ({exc})")


def systemctl(*args) -> int:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True).returncode


def wait_for_vram(need: float, seconds: float) -> float:
    deadline = time.monotonic() + seconds
    free = vram_free_gb()
    while free < need and time.monotonic() < deadline:
        time.sleep(2)
        free = vram_free_gb()
    return free


def make_room() -> None:
    need = ARGS.need_gb
    free = vram_free_gb()
    if free >= need:
        return
    log(f"VRAM: {free:.1f} GB free, {need} GB needed - unloading the coder")
    unload_coder()
    free = wait_for_vram(need, 60)
    if free < need:
        for unit in ARGS.stop_unit:
            if systemctl("is-active", "--quiet", unit) == 0:
                log(f"VRAM: still {free:.1f} GB free - stopping {unit} until released")
                systemctl("stop", unit)
                STATE["stopped"].append(unit)
        free = wait_for_vram(need, 60)
    if free < need:
        mine = own_gpu_mib()
        if mine > 1024 and STATE["pipeline"] is None:
            # Its own leftovers: a released model PyTorch / onnxruntime did
            # not give back (1-3 Nov: 7.6 GB held for two days, every model
            # refused). Start clean - systemd restarts the service.
            log(f"VRAM: this server still holds {mine} MiB with no model loaded - restarting")
            schedule_restart()
        raise RuntimeError(f"only {free:.1f} GB of VRAM free; the shape model needs about "
                           f"{need} GB. Holding the GPU: {gpu_holders() or 'see nvidia-smi'}")


def load() -> None:
    if STATE["pipeline"] is not None:
        return
    try:
        _load()
    except Exception:
        # make_room may have stopped other GPU services before either the
        # VRAM check or model initialization failed. Roll them back too.
        restore_services()
        raise


def _load() -> None:
    make_room()
    t0 = time.monotonic()
    sys.path.insert(0, os.path.join(ARGS.repo, "hy3dshape"))
    from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
    from hy3dshape.postprocessors import DegenerateFaceRemover, FaceReducer, FloaterRemover
    from hy3dshape.rembg import BackgroundRemover
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(ARGS.model_path,
                                                            subfolder=ARGS.subfolder)
    STATE.update(pipeline=pipe, rembg=BackgroundRemover(),
                 post=(FloaterRemover(), DegenerateFaceRemover(), FaceReducer()))
    log(f"loaded {ARGS.model_path}/{ARGS.subfolder} in {time.monotonic() - t0:.0f} s "
        f"({vram_free_gb():.1f} GB VRAM left)")


def schedule_restart(delay: float = 2.0) -> None:
    """Exit after `delay` seconds (the reply goes out first); systemd's
    Restart=on-failure starts a fresh process that holds no GPU memory."""
    def go():
        time.sleep(delay)
        log("restarting to give back all GPU memory")
        os._exit(75)
    threading.Thread(target=go, daemon=True).start()


def restore_services() -> list:
    """Restart only services we stopped; retain failures for a later retry.

    Caller holds GPU (load and release both do).
    """
    restarted, pending = [], []
    for unit in STATE["stopped"]:
        if systemctl("start", unit) == 0:
            restarted.append(unit)
            log(f"started {unit} again")
        else:
            pending.append(unit)
            log(f"could not restart {unit}; will retry on release")
    STATE["stopped"] = pending
    return restarted


def release() -> list:
    """Unload the model and restart what make_room stopped. A process that
    has loaded the model once keeps GPU memory PyTorch and onnxruntime never
    return, so after a release the server also restarts itself (--keep-alive
    turns that off)."""
    with GPU:
        was = STATE["pipeline"] is not None
        STATE.update(pipeline=None, rembg=None, post=None)
        gc.collect()
        if was:
            import torch
            torch.cuda.empty_cache()
            log("released the model")
        restarted = restore_services()
        if was and not ARGS.keep_alive:
            schedule_restart()
        return restarted


def idle_watch() -> None:
    while True:
        time.sleep(30)
        if STATE["pipeline"] is not None and not GPU.locked() and \
                time.monotonic() - STATE["last_used"] > ARGS.idle:
            log(f"idle for {ARGS.idle} s")
            release()


# ------------------------------------------------------------- generation

def needs_background_removal(img) -> bool:
    """True unless the image already has real transparency: at least 1% of
    it see-through. Sampled renders come back with alpha noise (252-254 on a
    few thousand pixels), which counted as "transparent" - background
    removal was skipped, and the backdrop and floor shadow became planes
    and discs in every pdx-slice-a-props model."""
    if img.mode not in ("RGBA", "LA") and "transparency" not in img.info:
        return True
    hist = img.convert("RGBA").getchannel("A").histogram()
    see_through = sum(hist[:128])
    return see_through < 0.01 * img.width * img.height


def generate(req: dict) -> dict:
    from PIL import Image
    if not req.get("image"):
        raise ValueError("no image")
    try:
        img = Image.open(io.BytesIO(base64.b64decode(req["image"])))
        img.load()
    except Exception as exc:
        raise ValueError(f"image is not a readable PNG or JPEG ({exc})")
    import torch
    rb = req.get("remove_background")
    if rb is None:
        rb = needs_background_removal(img)
    with GPU:
        load()
        STATE["last_used"] = time.monotonic()
        t0 = time.monotonic()
        img = STATE["rembg"](img.convert("RGB")) if rb else img.convert("RGBA")
        seed = int(req.get("seed", 0))
        gen = torch.Generator(device="cuda").manual_seed(seed)
        meshes = STATE["pipeline"](
            image=img, num_inference_steps=int(req.get("steps", 30)),
            guidance_scale=float(req.get("guidance", 5.0)), generator=gen,
            octree_resolution=int(req.get("octree_resolution", 256)),
            num_chunks=int(req.get("num_chunks", 8000)), output_type="trimesh")
        mesh = meshes[0] if meshes else None
        if mesh is None or len(mesh.faces) == 0:
            raise ValueError("no surface found - the image should show one whole object "
                             "on a plain background")
        floater, degenerate, reducer = STATE["post"]
        mesh = degenerate(floater(mesh))
        cap = int(req.get("max_faces", 20000) or 0)
        if cap and len(mesh.faces) > cap:
            mesh = reducer(mesh, max_facenum=cap)
        glb = mesh.export(file_type="glb")
        STATE["jobs"] += 1
        STATE["last_used"] = time.monotonic()
        secs = time.monotonic() - t0
    log(f"shape {STATE['jobs']}: {len(mesh.faces)} faces in {secs:.0f} s "
        f"(background {'removed' if rb else 'kept'})")
    return {"glb": base64.b64encode(glb).decode(), "faces": int(len(mesh.faces)),
            "vertices": int(len(mesh.vertices)), "seconds": round(secs, 1), "seed": seed,
            "background_removed": bool(rb)}


# ------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "forge-shape/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        pass                                    # one log line per job instead

    def reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        loaded = STATE["pipeline"] is not None
        if self.path == "/health":
            self.reply(200, {"status": "ok", "model": MODEL_ID, "loaded": loaded,
                             "busy": GPU.locked(), "stops": list(ARGS.stop_unit),
                             "stopped": list(STATE["stopped"]), "jobs": STATE["jobs"]})
        elif self.path == "/v1/models":
            self.reply(200, {"data": [{"id": MODEL_ID, "status": {
                "value": "loaded" if loaded else "unloaded"}}]})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdecimal():
            self.reply(400, {"error": "one nonnegative Content-Length is required"})
            return
        n = int(lengths[0])
        if n > MAX_BODY:
            self.reply(413, {"error": f"request over {MAX_BODY // 2**20} MB"})
            return
        try:
            raw = self.rfile.read(n)
            if len(raw) != n:
                raise ValueError("incomplete body")
            req = json.loads(raw or b"{}")
            if not isinstance(req, dict):
                raise ValueError("body must be an object")
        except (ValueError, OSError):
            self.reply(400, {"error": "body must be a complete JSON object"})
            return
        if self.path == "/v1/release":
            self.reply(200, {"released": True, "restarted": release()})
        elif self.path == "/v1/shape":
            try:
                self.reply(200, generate(req))
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
            except Exception as exc:
                log("shape failed:\n" + traceback.format_exc())
                self.reply(500, {"error": f"{type(exc).__name__}: {exc}",
                                 "trace": traceback.format_exc()[-1500:]})
        else:
            self.reply(404, {"error": "not found"})


def main() -> int:
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, help="the Hunyuan3D-2.1 checkout")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7870)
    ap.add_argument("--model-path", default="tencent/Hunyuan3D-2.1",
                    help="under $HY3DGEN_MODELS (the installer puts the weights there)")
    ap.add_argument("--subfolder", default="hunyuan3d-dit-v2-1")
    ap.add_argument("--need-gb", type=float, default=11.0,
                    help="free VRAM wanted before loading the model")
    ap.add_argument("--stop-unit", action="append", default=None,
                    help="user service to stop while VRAM is short (repeatable; "
                         "default sd-server; 'none' for none)")
    ap.add_argument("--coder-url", default="http://127.0.0.1:8080")
    ap.add_argument("--coder-key-file", default="~/.coder-api-key")
    ap.add_argument("--keep-alive", action="store_true",
                    help="do not restart the process after a release (it then keeps some GPU "
                         "memory until the service restarts)")
    ap.add_argument("--idle", type=float, default=600,
                    help="seconds without a job before the model is released")
    ARGS = ap.parse_args()
    if ARGS.stop_unit is None:
        ARGS.stop_unit = ["sd-server"]
    ARGS.stop_unit = [u for u in ARGS.stop_unit if u and u != "none"]
    threading.Thread(target=idle_watch, daemon=True).start()
    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    log(f"forge-shape on {ARGS.host}:{ARGS.port} - {MODEL_ID} loads on the first job")
    try:
        srv.serve_forever()
    finally:
        release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
