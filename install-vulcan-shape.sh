#!/usr/bin/env bash
#
# install-vulcan-shape.sh
#
# VULCAN - a THIRD job for the RTX 4070 Ti Super: 3D models for forge.
# Hunyuan3D 2.1's shape model turns one rendered image into an untextured
# mesh (GLB). forge renders an image asset as usual, then sends it here when
# the asset has a "shape" ([shapes] in forge's config.toml).
#
# Run it on Vulcan (Kubuntu) as your normal user, after install-vulcan-imagegen.sh
# (it reuses that NVIDIA driver). It installs:
#   ~/Hunyuan3D-2.1          Tencent's code, shape part only, pinned commit
#   ~/.venvs/hunyuan3d       Python 3.10 (Tencent's tested version) via uv,
#                            PyTorch 2.5.1 + CUDA 12.4 and the shape model's
#                            dependencies at Tencent's pinned versions
#   ~/models/hunyuan3d       the shape weights (~8 GB; texture weights are
#                            NOT downloaded - texturing needs 21 GB of VRAM)
#   ~/forge-shape/           forge-shape-server.py and the licence NOTICE
#   forge-shape.service      systemd user service on port 7870
#
# Sharing the GPU: sd-server keeps its weights in RAM and the helper coder
# loads only on demand, so the shape model (~10 GB) fits while neither is
# working. forge never overlaps them; the server also checks free VRAM and
# makes room (unloads the coder, then stops sd-server until released).
#
# LICENCE - read before installing. Hunyuan3D 2.1 is under the "Tencent
# Hunyuan 3D 2.1 Community License Agreement": it does NOT apply in the EU,
# the UK or South Korea; products with over 1 million monthly active users
# need Tencent's permission; outputs may not be used to improve other AI
# models; distributing the model needs the licence and a Notice file.
# https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/blob/main/LICENSE
#
# Usage:  ./install-vulcan-shape.sh [phase ...]
# Phases: check repo python models server firewall verify

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

REPO_URL="https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git"
REPO_COMMIT="${REPO_COMMIT:-82920d643c0dc2f7bfd7255f45f62d386edfe60c}"   # main, 2025-10-17
REPO_DIR="${REPO_DIR:-$HOME/Hunyuan3D-2.1}"
VENV="${VENV:-$HOME/.venvs/hunyuan3d}"
UV_VENV="$HOME/.venvs/uv"
MODELS="${MODELS:-$HOME/models/hunyuan3d}"        # HY3DGEN_MODELS
HF_REPO="tencent/Hunyuan3D-2.1"
SERVER_DIR="$HOME/forge-shape"
PORT=7870
LAN_SUBNET="192.168.50.0/24"
CONFIGURE_UFW=true
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Tencent's requirements.txt pins, for the parts the shape model imports
# (hy3dshape: pipelines, postprocessors, preprocessors, rembg). Texture-only
# packages (Real-ESRGAN, bpy, open3d, cupy, deepspeed, gradio...) are left out.
# One change: onnxruntime 1.20.1 instead of Tencent's 1.16.3. 1.16.3's library
# asks for an executable stack, which glibc 2.41 and later (Kubuntu 26.04)
# refuse to load ("cannot enable executable stack"); 1.19 and later do not.
# rembg uses it for background removal only.
TORCH_PKGS=(torch==2.5.1 torchvision==0.20.1)
TORCH_INDEX="https://download.pytorch.org/whl/cu124"
PY_PKGS=(
    transformers==4.46.0 diffusers==0.30.0 accelerate==1.1.1 huggingface-hub==0.30.2
    safetensors==0.4.4 numpy==1.24.4 scipy==1.14.1 einops==0.8.0
    opencv-python-headless==4.10.0.84 scikit-image==0.24.0 rembg==2.0.65 onnxruntime==1.20.1
    trimesh==4.4.7 pymeshlab==2022.2.post3 pygltflib==1.16.3 omegaconf==2.3.0 pyyaml==6.0.2
    tqdm==4.66.5 timm pillow
)

# ============================================================================
# HELPERS
# ============================================================================

BOLD=$'\033[1m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GREEN" "$*" "$RESET"; }
have() { command -v "$1" &>/dev/null; }
PY="$VENV/bin/python"

# ============================================================================
# PHASES
# ============================================================================

phase_check() {
    say "[check] GPU, disk, tools"
    [ "$(id -u)" -ne 0 ] || die "Run as your normal user, not with sudo (it installs a user service)."
    have nvidia-smi || die "nvidia-smi missing - run install-vulcan-imagegen.sh (nvidia phase) first."
    nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader \
        | sed 's/^/    /'
    local free_gb
    free_gb=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
    [ "${free_gb:-0}" -ge 25 ] || die "Only ${free_gb} GB free in $HOME; ~20 GB needed (weights, PyTorch, CUDA libraries)."
    ok "${free_gb} GB free"
    local missing=()
    for t in git curl python3; do have "$t" || missing+=("$t"); done
    python3 -c 'import venv' 2>/dev/null || missing+=(python3-venv)
    if [ ${#missing[@]} -gt 0 ]; then
        info "Installing: ${missing[*]}"
        sudo apt-get install -y "${missing[@]}"
    fi
    [ -f "$HERE/forge-shape-server.py" ] || die "forge-shape-server.py must be beside this script."
    ok "tools present"
}

phase_repo() {
    say "[repo] Hunyuan3D 2.1, shape code only, at $REPO_COMMIT"
    if [ ! -d "$REPO_DIR/.git" ]; then
        git clone --filter=blob:none --no-checkout "$REPO_URL" "$REPO_DIR"
    fi
    git -C "$REPO_DIR" sparse-checkout set --no-cone '/hy3dshape/' '/LICENSE' '/Notice.txt' '/README.md'
    git -C "$REPO_DIR" fetch --quiet origin "$REPO_COMMIT" 2>/dev/null || git -C "$REPO_DIR" fetch --quiet origin
    git -C "$REPO_DIR" -c advice.detachedHead=false checkout --quiet "$REPO_COMMIT"
    [ -f "$REPO_DIR/hy3dshape/hy3dshape/pipelines.py" ] || die "hy3dshape/pipelines.py missing after checkout"
    ok "$(git -C "$REPO_DIR" log -1 --format='%h %cs %s')"
}

phase_python() {
    say "[python] Python 3.10 venv with PyTorch 2.5.1 (CUDA 12.4)"
    # uv provides Python 3.10 (Kubuntu's own Python is too new for
    # numpy 1.24 and torch 2.5); uv itself lives in its own small venv.
    if [ ! -x "$UV_VENV/bin/uv" ]; then
        python3 -m venv "$UV_VENV"
        "$UV_VENV/bin/pip" install -q -U uv
    fi
    local UV="$UV_VENV/bin/uv"
    "$UV" python install 3.10
    [ -x "$PY" ] || "$UV" venv --python 3.10 "$VENV"
    "$UV" pip install --python "$PY" "${TORCH_PKGS[@]}" --index-url "$TORCH_INDEX"
    "$UV" pip install --python "$PY" "${PY_PKGS[@]}"
    # A compiled library that still wants an executable stack fails to load
    # on this glibc: list any now rather than at the first job.
    "$PY" - "$VENV" <<'EOF'
import os, struct, sys
bad = []
for root, _, files in os.walk(sys.argv[1]):
    for f in files:
        if ".so" not in f:
            continue
        p = os.path.join(root, f)
        try:
            with open(p, "rb") as fh:
                h = fh.read(64)
                if h[:4] != b"\x7fELF" or h[4] != 2:
                    continue
                phoff, = struct.unpack_from("<Q", h, 32)
                phentsize, phnum = struct.unpack_from("<HH", h, 54)
                fh.seek(phoff)
                ph = fh.read(phentsize * phnum)
        except (OSError, struct.error):
            continue
        for i in range(phnum):
            ptype, pflags = struct.unpack_from("<II", ph, i * phentsize)
            if ptype == 0x6474E551 and pflags & 1:      # PT_GNU_STACK, executable
                bad.append(os.path.relpath(p, sys.argv[1]))
if bad:
    print("    These libraries ask for an executable stack, which this glibc refuses:")
    print("\n".join("      " + b for b in bad))
    sys.exit(1)
print("    no library needs an executable stack")
EOF
    "$PY" - "$REPO_DIR" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1] + "/hy3dshape")
import torch, rembg, onnxruntime, pymeshlab, cv2, skimage, trimesh
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
from hy3dshape.postprocessors import FaceReducer, FloaterRemover, DegenerateFaceRemover
assert torch.cuda.is_available(), "PyTorch cannot see the GPU"
print("    torch", torch.__version__, "CUDA", torch.version.cuda, "-", torch.cuda.get_device_name(0))
print("    onnxruntime", onnxruntime.__version__, "- rembg, pymeshlab, OpenCV and hy3dshape import")
EOF
    ok "venv $VENV"
}

phase_models() {
    say "[models] shape weights into $MODELS (DiT + VAE, ~8 GB)"
    mkdir -p "$MODELS"
    HF_HUB_DISABLE_TELEMETRY=1 "$PY" - "$MODELS/$HF_REPO" "$HF_REPO" <<'EOF'
import sys
from huggingface_hub import snapshot_download
dest, repo = sys.argv[1], sys.argv[2]
snapshot_download(repo_id=repo, local_dir=dest,
                  allow_patterns=["hunyuan3d-dit-v2-1/*", "hunyuan3d-vae-v2-1/*",
                                  "LICENSE", "Notice.txt"])
EOF
    for f in hunyuan3d-dit-v2-1/model.fp16.ckpt hunyuan3d-dit-v2-1/config.yaml \
             hunyuan3d-vae-v2-1/model.fp16.ckpt; do
        [ -s "$MODELS/$HF_REPO/$f" ] || die "missing after download: $MODELS/$HF_REPO/$f"
    done
    ok "$(du -sh "$MODELS/$HF_REPO" | cut -f1) in $MODELS/$HF_REPO"
    # rembg fetches its background-removal model (u2net) on first use: now,
    # while there is network, not during forge's first job.
    "$PY" -c 'from rembg import new_session; new_session(); print("    rembg model ready")'
}

phase_server() {
    say "[server] forge-shape as a systemd user service (port $PORT)"
    mkdir -p "$SERVER_DIR"
    install -m 0755 "$HERE/forge-shape-server.py" "$SERVER_DIR/forge-shape-server.py"
    for f in LICENSE Notice.txt; do
        [ -f "$MODELS/$HF_REPO/$f" ] && cp "$MODELS/$HF_REPO/$f" "$SERVER_DIR/$f"
    done
    cat > "$SERVER_DIR/NOTICE" <<'EOF'
Tencent Hunyuan 3D 2.1 is licensed under the Tencent Hunyuan 3D 2.1 Community
License Agreement, Copyright (C) 2025 Tencent. All Rights Reserved. The
trademark rights of "Tencent Hunyuan" are owned by Tencent or its affiliate.
The licence (LICENSE in this folder) does not apply in the European Union,
the United Kingdom or South Korea. forge-shape-server.py calls Tencent's
unmodified hy3dshape code from ~/Hunyuan3D-2.1.
EOF
    # While the shape model holds the GPU it stops these (and starts them
    # again on release): Strata, Vulcan's principal coder from 30 Oct, and
    # sd-server where it still runs.
    local STOP_UNITS=" --stop-unit sd-server"
    [ -f "$HOME/.config/systemd/user/strata.service" ] && STOP_UNITS=" --stop-unit strata --stop-unit sd-server"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/forge-shape.service" <<EOF
[Unit]
Description=forge shape server - Hunyuan3D 2.1 image-to-3D (RTX 4070 Ti Super)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HY3DGEN_MODELS=$MODELS
Environment=HF_HUB_OFFLINE=1
Environment=HF_HUB_DISABLE_TELEMETRY=1
Environment=CUDA_VISIBLE_DEVICES=0
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY $SERVER_DIR/forge-shape-server.py --repo $REPO_DIR --port $PORT$STOP_UNITS
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; the service will stop at logout."
    systemctl --user enable --now forge-shape
    local _
    for _ in $(seq 1 30); do
        curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
        sleep 1
    done
    curl -fsS "http://127.0.0.1:$PORT/health" | sed 's/^/    /' && echo \
        || die "forge-shape did not answer - journalctl --user -u forge-shape -n 50"
    ok "forge-shape running (the model loads on the first job)"
}

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || return 0
    say "[firewall] shape API, LAN only"
    have ufw || sudo apt-get install -y ufw
    sudo ufw allow from "$LAN_SUBNET" to any port "$PORT" proto tcp comment "forge-shape" >/dev/null
    ok "Allowed $PORT from $LAN_SUBNET"
    info "forge-shape has no API key, like sd-server. Restrict it to Gemini's address if"
    info "other LAN devices should not be able to use the GPU."
}

phase_verify() {
    say "[verify] one model from Tencent's demo image"
    local demo
    demo=$(find "$REPO_DIR/hy3dshape" -maxdepth 3 -iname 'demo.png' | head -1)
    [ -n "$demo" ] || demo=$(find "$REPO_DIR/hy3dshape/demos" -maxdepth 2 -iname '*.png' | head -1)
    [ -n "$demo" ] || { warn "no demo image in the checkout; run 'forge selftest shapes' on Gemini instead"; return 0; }
    info "image: $demo (the first job loads the model - a minute or two)"
    "$PY" - "$demo" "$PORT" <<'EOF'
import base64, json, sys, time, urllib.request
img, port = sys.argv[1], sys.argv[2]
body = {"image": base64.b64encode(open(img, "rb").read()).decode(), "steps": 30,
        "octree_resolution": 256, "max_faces": 20000}
t0 = time.time()
req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/shape", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=1200) as r:
    out = json.loads(r.read())
glb = base64.b64decode(out["glb"])
assert glb[:4] == b"glTF", out
open("/tmp/forge-shape-demo.glb", "wb").write(glb)
print(f"    {out['faces']} faces, {len(glb) // 1024} KB -> /tmp/forge-shape-demo.glb "
      f"({out['seconds']} s generating, {time.time() - t0:.0f} s in all)")
urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/release",
                                              data=b"{}"), timeout=120).close()
print("    released the GPU")
EOF
    ok "shape server works"
}

ALL_PHASES=(check repo python models server firewall verify)
main() {
    local -a phases
    if [ $# -gt 0 ]; then phases=("$@"); else phases=("${ALL_PHASES[@]}"); fi
    local p
    for p in "${phases[@]}"; do
        case "$p" in
            check|repo|python|models|server|firewall|verify) "phase_$p"; echo ;;
            *) die "Unknown phase '$p'. Valid: ${ALL_PHASES[*]}" ;;
        esac
    done
    say "Done. Shape API: http://192.168.50.151:$PORT/  - in forge's config.toml set"
    info "[shapes] enabled = true (its [[shapes.servers]] entry points here), then"
    info "on Gemini: forge selftest shapes"
}
main "$@"
