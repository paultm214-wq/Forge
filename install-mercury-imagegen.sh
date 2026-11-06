#!/usr/bin/env bash
#
# install-mercury-imagegen.sh
#
# MERCURY - the image-generation worker.
#   GIGABYTE B550M K / Ryzen 7 5700X / 32GB DDR4 (matched pair)
#   Radeon RX 9060 XT 16GB (Navi 44, gfx1200) - PCIe slot, Gen4 x16
#   ONE GPU. No integrated graphics on the 5700X.
#   Kubuntu 26.04 LTS
#
# Runs stable-diffusion.cpp's sd-server with Qwen-Image-2.1, exposing an
# OpenAI-compatible images API that forge on Gemini calls.
#
# WHY sd.cpp AND WHY VULKAN
# -------------------------
# Headless and orchestrator-driven, so ComfyUI's node ecosystem goes unused
# while its API makes a client POST an entire workflow graph per request.
# sd-server takes a prompt and returns an image.
#
# Vulkan means NO ROCm and NO PyTorch on this box at all - which sidesteps the
# gfx1200 PyTorch VAE crashes, and keeps this box's software stack as simple
# as it can be.
#
# BIOS - do these BEFORE installing (GIGABYTE B550M K, Ryzen 7 5700X)
# ------------------------------------------------------------------
#   * PLUG THE MONITOR INTO THE 9060 XT. The 5700X has no integrated
#     graphics, so the board's video outputs are dead - including for the
#     BIOS screen and the installer.
#   * Load optimized defaults for the new CPU, then enable Above 4G Decoding
#     and Resizable BAR.
#   * Memory: start at DDR4-3200 / FCLK 1600 to establish a clean baseline.
#     The 5700X's controller is far stronger than the 5700G's, and a single
#     matched pair is the easy case, so XMP 3600 / FCLK 1800 is realistic
#     once the box has proven stable.
#
# REINSTALL THE OS. This install was made while the 5700G was corrupting
# memory - the compiler crashed within minutes, so files written during the
# OS install may be damaged too. The system is barely configured, so a clean
# install is cheap insurance against chasing phantom bugs later. Downloads
# are checksum-verified by the models phase regardless.
#
# CAVEAT: sd.cpp added Qwen-Image-2.1 on 2026-09-20. The server flags below
# follow sd.cpp's existing Qwen-Image conventions; the build phase prints the
# upstream 2.1 docs from the exact commit you built, so reconcile against
# those if anything differs.
#
# Usage:  ./install-mercury-imagegen.sh [phase ...]
# Phases: base vulkan verify build models server firewall

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

SD_DIR="${SD_DIR:-$HOME/stable-diffusion.cpp}"
MODEL_DIR="${MODEL_DIR:-$HOME/models/qwen-image-2.1}"
SD_PORT=7860

# Diffusion model. Every Qwen-Image-2.1 GGUF is a days-old community upload.
# Pick a repo you trust and avoid the near-identically named "Uncensored"
# variants unless you specifically want an unfiltered model.
DIFF_REPO="${DIFF_REPO:-abenzerps/Qwen-Image-2.1-GGUF}"
DIFF_QUANT="Q6_K"        # ~6GB. Q8_0 has reported shape-mismatch problems.

# VAE from Comfy-Org's official repackaging.
VAE_REPO="Comfy-Org/Qwen-Image-2.1"
VAE_GLOB="*qwen_image_2.1_vae*"

# Text encoder: Qwen-Image-2.1 conditions on Qwen3-VL 8B. sd.cpp takes it as
# a GGUF via --llm. Q4_K_M (~5GB) keeps the whole pipeline on the GPU:
# ~6 diffusion + ~5 encoder + VAE. Q8_0 is better for prompt adherence but
# then needs --offload-to-cpu. Confirm the repo against the printed docs.
LLM_REPO="${LLM_REPO:-unsloth/Qwen3-VL-8B-Instruct-GGUF}"
LLM_QUANT="Q4_K_M"
# The encoder's vision half. Renders with reference images (a likeness, a
# style) run in sd.cpp's EDIT mode, which needs it: without it every such
# render fails with "Qwen Image 2.1 editing requires Qwen3-VL vision weights;
# provide --llm_vision". ~1.1GB at F16.
LLM_VISION_FILE="${LLM_VISION_FILE:-mmproj-F16.gguf}"

# Lightning LoRA (lightx2v/Qwen-Image-2.1-Lightning, released 2026-10-13):
# a 4-step DMD distillation of Qwen-Image-2.1 - the same base model this
# server runs. Rank 16, alpha 16 (strength 1.0 is neutral), fp32, 67MB, on
# the attention projections of all 32 blocks. Its card's settings: 4 steps,
# CFG 1.0 (off - negative prompts do nothing), sigmas [1, .75, .5, .25]
# with a resolution-dependent shift.
#
# It is opt-in per request; renders that do not ask for it are unchanged.
# sd-server deliberately IGNORES <lora:...> prompt tags, so a request names
# it in its "lora" list:
#   "lora": [{"path": "qwen-image-2.1-lightning/pytorch_lora_weights.safetensors", "multiplier": 1.0}]
# plus "steps": 4, "cfg_scale": 1.0 and the card's sigmas, which sd.cpp's
# fixed flow shift (3) does not reproduce - ~/sd-lightning-test.py shows how.
LORA_REPO="${LORA_REPO:-lightx2v/Qwen-Image-2.1-Lightning}"
LORA_FILE="pytorch_lora_weights.safetensors"
LORA_DIR="${LORA_DIR:-$MODEL_DIR/lora}"
LORA_SUB="qwen-image-2.1-lightning"      # request path: $LORA_SUB/$LORA_FILE

# Upscalers for the hires fix. sd-server lists whatever is in this folder
# (forge check shows the names); RealESRGAN_x4plus is the one forge uses.
UPSCALER_DIR="${UPSCALER_DIR:-$HOME/models/upscalers}"
ESRGAN_URL="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"

LAN_SUBNET="192.168.50.0/24"
CONFIGURE_UFW=true
RUN_SCRIPT="$HOME/run_sd.sh"

# ============================================================================
# HELPERS
# ============================================================================

BOLD=$'\033[1m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; GREEN=$'\033[32m'; RESET=$'\033[0m'

say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GREEN" "$*" "$RESET"; }

confirm() {
    local prompt="${1:-Press Enter to continue, or Ctrl+C to abort}"
    if [ -t 0 ]; then
        read -rp "    $prompt... "
    else
        info "(non-interactive; continuing)"
    fi
}

have() { command -v "$1" &>/dev/null; }

rocm_path() {
    if [ -n "${ROCM_PATH:-}" ]; then echo "$ROCM_PATH"; return; fi
    local p
    # ROCm 10 moved to a nested layout: /opt/rocm/core-10.0/, not /opt/rocm/.
    # Check the nested form FIRST - /opt/rocm exists as a parent directory and
    # would otherwise match while containing no lib/ of its own, leaving
    # libhipblas.so.3 off the linker path.
    for p in /opt/rocm/core-* /opt/rocm-*/core-* /opt/rocm /opt/rocm-*; do
        [ -d "$p/lib" ] && { echo "$p"; return; }
    done
    for p in /opt/rocm /opt/rocm-*; do
        [ -d "$p" ] && { echo "$p"; return; }
    done
    echo "/opt/rocm"
}

# ============================================================================
# PHASE: base
# ============================================================================

phase_base() {
    say "[base] OS check and base packages"

    # Running the whole script under sudo puts llama.cpp, the model, the API
    # key and the systemd USER service under /root, where the login session
    # cannot reach them. The script calls sudo itself where root is needed.
    if [ "$(id -u)" -eq 0 ]; then
        die "Do not run this with sudo. Run it as your normal user - it
    escalates only where necessary. If you already ran it as root, move
    \$HOME/models aside, delete /root/llama.cpp and /root/.venvs, and start
    again as yourself."
    fi

    local ver
    ver=$(. /etc/os-release && echo "$VERSION_ID")
    info "Detected Ubuntu $ver"

    case "$ver" in
        26.04) ok "Supported by ROCm 10.0.0." ;;
        24.04|22.04)
            warn "ROCm supports this, but 26.04 LTS is recommended for RDNA4."
            confirm "Continue anyway?"
            ;;
        25.10|25.04)
            die "Ubuntu $ver is END OF LIFE and is NOT supported by ROCm 10.
    Upgrade to 26.04 LTS first:
        sudo apt update && sudo apt full-upgrade -y && sudo reboot
        sudo do-release-upgrade
    Or reinstall from a 26.04 LTS ISO (faster on a fresh box)."
            ;;
        *)
            warn "Unrecognised Ubuntu version '$ver'. Proceeding at your own risk."
            confirm
            ;;
    esac

    # Several packages below (nodejs, glslc, libfuse2t64, rasdaemon, ...) live
    # in the universe component. Kubuntu enables it by default, but if it is
    # off, apt fails with a misleading "Unable to locate package".
    if ! grep -rqsE '^(Components:.*\buniverse\b|deb .* universe)' \
            /etc/apt/sources.list /etc/apt/sources.list.d/; then
        warn "The 'universe' component is not enabled - enabling it."
        sudo apt install -y software-properties-common
        sudo add-apt-repository -y universe
    fi

    sudo apt update && sudo apt full-upgrade -y
    # Every package here was checked against Ubuntu 26.04's package index.
    #   libssl-dev  - llama.cpp dropped libcurl in January 2026 and now
    #                 uses OpenSSL for HTTPS; libcurl4-openssl-dev and
    #                 -DLLAMA_CURL=ON are no longer used.
    #   gpg, openssl, ca-certificates - repo signing keys and API-key
    #                 generation. Standard on Kubuntu, but the installer's
    #                 "minimal installation" option can omit them.
    #   pkg-config  - used by CMake to find libraries.
    #   ccache      - faster rebuilds; also silences the CMake hint.
    sudo apt install -y \
        build-essential cmake ninja-build pkg-config ccache git \
        curl wget ca-certificates gpg openssl pciutils \
        libssl-dev libatomic1 libquadmath0 \
        python3-pip python3-venv jq \
        smartmontools nvme-cli lm-sensors rasdaemon

    # A volatile journal loses everything on reboot, so a machine that dies
    # every few hours erases its own evidence each time. Make it persistent
    # BEFORE anything can crash.
    if [ ! -d /var/log/journal ]; then
        sudo mkdir -p /var/log/journal
        sudo systemd-tmpfiles --create --prefix /var/log/journal
        sudo systemctl restart systemd-journald
        ok "Journal is now persistent across reboots."
    else
        ok "Journal already persistent."
    fi

    # No iGPU on the 5700X, so a desktop session renders on the 9060 XT and
    # holds VRAM the image model needs. Boot to a text console instead; start
    # a desktop by hand with 'sudo systemctl start display-manager' if wanted.
    if [ "$(systemctl get-default)" != "multi-user.target" ]; then
        sudo systemctl set-default multi-user.target
        warn "Default boot target is now text-only (multi-user.target)."
    fi

    # Logs corrected memory errors as they happen. On a box running DDR4
    # above its controller's rated speed, accumulating corrected errors are
    # the early warning that precedes an unexplained crash.
    sudo systemctl enable --now rasdaemon >/dev/null 2>&1 || \
        warn "rasdaemon did not start; check: systemctl status rasdaemon"

    say "Storage health baseline"
    # Capture this now, while everything is healthy, so a later reading has
    # something to be compared against.
    for d in /dev/nvme?n1; do
        [ -e "$d" ] || continue
        info "$d"
        sudo smartctl -a "$d" 2>/dev/null \
            | grep -iE 'Model Number|Firmware|Percentage Used|Temperature:|Critical Warning|Media and Data Integrity|Unsafe Shutdowns' \
            | sed 's/^/       /'
    done

    ok "Base packages installed."
}

# ============================================================================
# PHASE: vulkan
# ============================================================================

phase_vulkan() {
    say "[vulkan] Mesa RADV driver and build tooling"
    # RADV (Mesa's Vulkan driver) supports RDNA4. No ROCm needed on this box.
    # spirv-headers: ggml's Vulkan backend requires SPIR-V headers
    # (spirv/unified1/spirv.hpp), and libvulkan-dev does not always pull them
    # in. llama.cpp's own Ubuntu 26.04 Vulkan build installs all three.
    sudo apt install -y mesa-vulkan-drivers vulkan-tools libvulkan-dev glslc spirv-headers
    sudo usermod -a -G render,video "$USER"
    ok "Vulkan installed."
    warn "Log out and back in for the render/video groups to apply."
}

# ============================================================================
# PHASE: verify
# ============================================================================

# Picks the discrete GPU's Vulkan index. Mesa's llvmpipe (lavapipe) - a
# software renderer that runs on the CPU - is ALSO listed as a Vulkan device,
# so pinning still matters with a single real GPU.
#
# vulkaninfo's output is captured before it is parsed. Piping it into an awk
# that exits on the first match was a bug: vulkaninfo was killed by SIGPIPE
# mid-write, `pipefail` turned that into a failure, and because the result is
# assigned under `set -e` the WHOLE SCRIPT exited silently.
discrete_vk_index() {
    local out
    out=$(vulkaninfo --summary 2>/dev/null || true)
    awk '
        /^GPU[0-9]+:/ { idx = substr($1, 4, length($1) - 4) }
        /deviceType/ && /DISCRETE/ && found == "" { found = idx }
        END { if (found != "") print found }' <<< "$out"
}

phase_verify() {
    say "[verify] Vulkan devices"
    have vulkaninfo || die "vulkaninfo missing - run the vulkan phase."
    vulkaninfo --summary 2>/dev/null | grep -E "^GPU|deviceName|deviceType" | sed 's/^/    /'
    echo
    local idx; idx=$(discrete_vk_index)
    if [ -n "$idx" ]; then
        ok "Discrete GPU is Vulkan device $idx - the server will be pinned to it."
    else
        warn "No DISCRETE Vulkan device found. Check the 9060 XT is seated."
        confirm "Continue anyway?"
    fi

    # Gigabyte B550 boards can expose RGB Fusion on the chipset SMBus - where
    # OpenRGB's historical bricking reports sit. Your RGB goes through the
    # Airgoo over USB, so this is only a warning to keep RGB_DEVICES scoped.
    if grep -qi piix4 /sys/class/i2c-dev/*/name 2>/dev/null; then
        warn "Chipset SMBus (PIIX4) is exposed. OpenRGB may detect the board's"
        warn "RGB Fusion controller over SMBus. Keep RGB_DEVICES set to the Airgoo"
        warn "only, and never set effect modes on the motherboard device."
    else
        ok "No chipset SMBus exposed - OpenRGB cannot reach the board's RGB."
    fi
}

# ============================================================================
# PHASE: build
# ============================================================================

phase_build() {
    say "[build] stable-diffusion.cpp with Vulkan"
    if [ ! -d "$SD_DIR/.git" ]; then
        git clone --recursive https://github.com/leejet/stable-diffusion.cpp "$SD_DIR"
    else
        git -C "$SD_DIR" pull --ff-only
        git -C "$SD_DIR" submodule update --init --recursive
    fi
    # sd-server's built-in web UI is compiled into the binary ONLY if pnpm is
    # on PATH at configure time. Without it CMake prints "pnpm not found;
    # frontend build disabled" and the server is API-only. User-level npm
    # prefix: no sudo for global installs.
    say "Web UI toolchain (Node.js + pnpm)"
    have npm || sudo apt install -y nodejs npm
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    grep -q 'npm-global/bin' "$HOME/.profile" 2>/dev/null || \
        echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.profile"
    export PATH="$HOME/.npm-global/bin:$PATH"
    have pnpm || npm install -g pnpm
    ok "pnpm $(pnpm --version)"

    # Captured, then searched - not piped into grep -q (see the SIGPIPE note
    # on discrete_vk_index). A cached "pnpm not found" is re-searched by
    # CMake automatically, so a reconfigure is enough to pick pnpm up.
    local cfg
    cfg=$(cmake -S "$SD_DIR" -B "$SD_DIR/build" -G Ninja \
              -DSD_VULKAN=ON -DCMAKE_BUILD_TYPE=Release 2>&1) \
        || { echo "$cfg"; die "CMake configure failed."; }
    echo "$cfg" > "$SD_DIR/build/configure.log"
    if grep -q "frontend build disabled" <<< "$cfg"; then
        warn "Web UI still disabled - the server will be API-only. See"
        warn "$SD_DIR/build/configure.log for why."
    else
        ok "Web UI enabled"
    fi

    # The frontend step fetches npm packages during the build, so this
    # phase needs internet access.
    cmake --build "$SD_DIR/build" -j"$(nproc)" --target sd-server sd-cli
    [ -x "$SD_DIR/build/bin/sd-server" ] || die "sd-server did not build."
    ok "Built $SD_DIR/build/bin/sd-server"

    say "Upstream Qwen-Image-2.1 docs from the commit you just built"
    info "2.1 support is days old - these are authoritative over this script."
    local d
    d=$(grep -ril -E "qwen.?image.?2\.1" "$SD_DIR/docs" 2>/dev/null | head -n1 || true)
    if [ -n "$d" ]; then
        info "  $d"
        sed -n '1,60p' "$d" | sed 's/^/      /'
    else
        warn "No 2.1-specific doc found under $SD_DIR/docs. Check the README and"
        warn "docs/qwen_image.md, and confirm the text encoder requirement."
    fi
}

# ============================================================================
# Hugging Face helpers
# ============================================================================

# Pick an `hf` CLI, most-trusted first:
#   1. ~/.local/bin/hf - Hugging Face's standalone installer (current)
#   2. ~/.venvs/hf     - this script's own private copy (current)
#   3. whatever is on PATH - LAST, because it may be Ubuntu's
#      python3-huggingface-hub package, which lags upstream
# Each candidate must actually support `hf download`; an older CLI that
# lacks it is skipped rather than failing halfway through a download.
# Diagnostics go to stderr so the path this prints can be captured cleanly.
find_hf() {
    local c
    for c in "$HOME/.local/bin/hf" "$HOME/.venvs/hf/bin/hf" \
             "$(command -v hf 2>/dev/null || true)"; do
        if [ -n "$c" ] && [ -x "$c" ] && "$c" download --help >/dev/null 2>&1; then
            echo "$c"; return 0
        fi
    done
    echo "    No hf CLI found - installing huggingface_hub into ~/.venvs/hf" >&2
    python3 -m venv "$HOME/.venvs/hf" >&2
    "$HOME/.venvs/hf/bin/pip" install -q -U huggingface_hub >&2
    echo "$HOME/.venvs/hf/bin/hf"
}

# Download with Hugging Face's plain HTTP transfer by DEFAULT. Xet, HF's
# chunked transfer layer, failed mid-download on this network every time it
# was tried, with:
#   RuntimeError: ... CAS Client Error: ... error decoding response body
# Plain HTTP resumes interrupted files, so a retry loop makes it robust.
# Opt back into Xet with HF_HUB_DISABLE_XET=0.
#   hf_get HF_BINARY <hf download arguments...>
hf_get() {
    local hf="$1"; shift
    local try xet="${HF_HUB_DISABLE_XET:-1}"
    [ "$xet" = "1" ] && echo "    transfer: plain HTTP (Xet off)" >&2 \
                     || echo "    transfer: Xet" >&2
    for try in 1 2 3; do
        if HF_HUB_DISABLE_XET="$xet" "$hf" download "$@"; then
            return 0
        fi
        if [ "$try" -lt 3 ]; then
            echo "    download attempt $try failed; retrying - partial files resume" >&2
            sleep $((try * 10))
        fi
    done
    return 1
}

# Check downloaded files against the SHA-256 Hugging Face publishes for each
# LFS file. A file written through bad RAM - as on the 5700G - looks normal
# until it fails to load or, worse, loads and misbehaves. Uses only the
# Python standard library, so it needs no venv.
#   hf_verify REPO LOCAL_DIR
hf_verify() {
    python3 - "$1" "$2" <<'PY'
import hashlib, json, os, sys, urllib.request
repo, root = sys.argv[1], sys.argv[2]
base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
url = f"{base}/api/models/{repo}/tree/main?recursive=true"
# Same token the hf CLI uses: HF_TOKEN if set, else the file `hf auth login`
# writes. Needed for gated repos; also raises rate limits.
token = os.environ.get("HF_TOKEN", "").strip()
if not token:
    home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    try:
        token = open(os.path.join(home, "token")).read().strip()
    except OSError:
        pass
req = urllib.request.Request(url)
if token:
    req.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        entries = json.load(r)
except Exception as exc:
    print(f"    WARNING: could not fetch published hashes ({exc}); NOT verified")
    sys.exit(0)
want = {e["path"]: e["lfs"]["oid"] for e in entries
        if e.get("type") == "file" and isinstance(e.get("lfs"), dict)}
# A file downloaded by hand may sit at a different path than in the repo
# (e.g. the repo keeps it in a subfolder). Fall back to its filename - but
# only when that name is unique in the repo, so a match is never a guess.
by_name = {}
for path, oid in want.items():
    by_name.setdefault(os.path.basename(path), []).append(oid)
bad = checked = 0
for dirpath, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d != ".cache"]      # hf's own metadata
    for name in files:
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, root)
        if rel in want:
            expected = want[rel]
        elif len(by_name.get(name, [])) == 1:
            expected = by_name[name][0]
        else:
            continue
        h = hashlib.sha256()
        with open(full, "rb") as fh:
            for chunk in iter(lambda: fh.read(8 << 20), b""):
                h.update(chunk)
        checked += 1
        if h.hexdigest() == expected:
            print(f"    OK   sha256  {rel}")
        else:
            bad += 1
            print(f"    BAD  sha256  {rel}  <- delete it and download again")
if not checked:
    print("    WARNING: no downloaded file matched a published hash; NOT verified")
sys.exit(1 if bad else 0)
PY
}

# ============================================================================
# PHASE: models
# ============================================================================

phase_models() {
    say "[models] Qwen-Image-2.1 components"
    mkdir -p "$MODEL_DIR"/{diffusion,vae,llm} "$LORA_DIR/$LORA_SUB"
    # An earlier version of this script saved the LoRA loose in $LORA_DIR with
    # a symlink. Move it into its folder rather than download it again.
    [ -L "$LORA_DIR/$LORA_SUB.safetensors" ] && rm -f "$LORA_DIR/$LORA_SUB.safetensors"
    [ -f "$LORA_DIR/$LORA_FILE" ] && [ ! -f "$LORA_DIR/$LORA_SUB/$LORA_FILE" ] && \
        mv "$LORA_DIR/$LORA_FILE" "$LORA_DIR/$LORA_SUB/"

    # SKIP_DOWNLOAD=1: use files already in place - downloaded by hand, or
    # copied from another machine - and go straight to checksum verification
    # and recording their paths. Files can sit anywhere under each folder.
    if [ "${SKIP_DOWNLOAD:-0}" = "1" ]; then
        info "SKIP_DOWNLOAD=1 - not downloading. Expecting:"
        info "  $MODEL_DIR/diffusion/  *${DIFF_QUANT}*.gguf      from $DIFF_REPO"
        info "  $MODEL_DIR/vae/        *.safetensors             from $VAE_REPO"
        info "  $MODEL_DIR/llm/        *${LLM_QUANT}*.gguf (not mmproj) from $LLM_REPO"
        info "  $LORA_DIR/$LORA_SUB/  $LORA_FILE  from $LORA_REPO"
    else
        local hf; hf=$(find_hf)
        info "Using: $hf"
        warn "Diffusion model from $DIFF_REPO - a community upload. Confirm you trust it."
        confirm
        local fail="Download failed after 3 attempts. Retry, or download by hand and run:
    SKIP_DOWNLOAD=1 $0 models"
        hf_get "$hf" "$DIFF_REPO" --local-dir "$MODEL_DIR/diffusion" --include "*${DIFF_QUANT}*.gguf" || die "$fail"
        hf_get "$hf" "$VAE_REPO"  --local-dir "$MODEL_DIR/vae" --include "$VAE_GLOB"               || die "$fail"
        hf_get "$hf" "$LLM_REPO"  --local-dir "$MODEL_DIR/llm" --include "*${LLM_QUANT}*.gguf"     || die "$fail"
        hf_get "$hf" "$LLM_REPO"  --local-dir "$MODEL_DIR/llm" --include "$LLM_VISION_FILE"        || die "$fail"
        hf_get "$hf" "$LORA_REPO" --local-dir "$LORA_DIR/$LORA_SUB" --include "$LORA_FILE"         || die "$fail"
    fi

    say "Verifying downloads against published SHA-256 hashes"
    local bad=0
    hf_verify "$DIFF_REPO" "$MODEL_DIR/diffusion" || bad=1
    hf_verify "$VAE_REPO"  "$MODEL_DIR/vae"       || bad=1
    hf_verify "$LLM_REPO"  "$MODEL_DIR/llm"       || bad=1
    hf_verify "$LORA_REPO" "$LORA_DIR/$LORA_SUB"  || bad=1
    [ "$bad" -eq 0 ] || die "Checksum mismatch - delete the BAD file(s) and re-run the models phase."

    # Record what was found so the server phase does not guess at paths.
    local diff vae llm
    # hf download keeps a hidden .cache/ folder inside each download dir, full
    # of .lock and .metadata files NAMED AFTER the real ones. Skip it
    # explicitly and match real model extensions. The first version of this
    # used `find -type f` for the VAE, and ".cache" sorts first - so sd-server
    # was handed a .lock file and failed with hundreds of "VAE tensor ... not
    # in model metadata" errors.
    diff=$(find "$MODEL_DIR/diffusion" -path '*/.cache' -prune -o \
               -type f -name "*${DIFF_QUANT}*.gguf" -print | sort | head -n1)
    vae=$(find "$MODEL_DIR/vae" -path '*/.cache' -prune -o \
               -type f -name '*.safetensors' -print | sort | head -n1)
    llm=$(find "$MODEL_DIR/llm" -path '*/.cache' -prune -o \
               -type f -name "*${LLM_QUANT}*.gguf" ! -iname "*mmproj*" -print | sort | head -n1)
    local vision
    vision=$(find "$MODEL_DIR/llm" -path '*/.cache' -prune -o \
               -type f -name "$LLM_VISION_FILE" -print | sort | head -n1)
    [ -n "$vision" ] && ok "vision: $vision" || warn "No $LLM_VISION_FILE - renders with reference images will fail"
    for v in diff vae llm; do
        [ -n "${!v}" ] || die "Could not find the $v file under $MODEL_DIR"
        ok "$v: ${!v}"
    done
    printf 'DIFF=%q\nVAE=%q\nLLM=%q\nLLM_VISION=%q\n' "$diff" "$vae" "$llm" "$vision" > "$MODEL_DIR/paths.env"
    if [ -s "$LORA_DIR/$LORA_SUB/$LORA_FILE" ]; then
        ok "lightning LoRA: $LORA_DIR/$LORA_SUB/$LORA_FILE"
    else
        warn "No $LORA_DIR/$LORA_SUB/$LORA_FILE - copy it in (SKIP_DOWNLOAD=1) or"
        warn "rerun this phase. Only Lightning requests need it."
    fi


    say "Hires upscaler"
    mkdir -p "$UPSCALER_DIR"
    if [ -s "$UPSCALER_DIR/RealESRGAN_x4plus.pth" ]; then
        ok "RealESRGAN_x4plus.pth already present"
    elif [ "${SKIP_DOWNLOAD:-0}" = "1" ]; then
        warn "No $UPSCALER_DIR/RealESRGAN_x4plus.pth - copy it in, or hires renders"
        warn "will have only Lanczos/Nearest."
    else
        # From the official Real-ESRGAN release (~64MB).
        curl -fL --retry 3 -o "$UPSCALER_DIR/RealESRGAN_x4plus.pth.part" "$ESRGAN_URL" \
            && mv "$UPSCALER_DIR/RealESRGAN_x4plus.pth.part" "$UPSCALER_DIR/RealESRGAN_x4plus.pth" \
            || die "Upscaler download failed - retry, or copy the file into $UPSCALER_DIR"
        ok "RealESRGAN_x4plus.pth ($(du -h "$UPSCALER_DIR/RealESRGAN_x4plus.pth" | cut -f1))"
    fi
}

# ============================================================================
# PHASE: server
# ============================================================================

phase_server() {
    say "[server] sd-server as a systemd user service"
    [ -f "$MODEL_DIR/paths.env" ] || die "Run the models phase first."
    local idx; idx=$(discrete_vk_index)
    [ -n "$idx" ] || { warn "Could not detect the discrete GPU; defaulting to 0."; idx=0; }

    cat > "$RUN_SCRIPT" <<EOF
#!/usr/bin/env bash
# Auto-generated by install-mercury-imagegen.sh
set -euo pipefail
source "$MODEL_DIR/paths.env"

# Pin to the discrete 9060 XT. llvmpipe (a CPU software renderer) is also a
# Vulkan device and must never be chosen.
export GGML_VK_VISIBLE_DEVICES=$idx

ARGS=(
  --listen-ip 0.0.0.0 --listen-port $SD_PORT
  # Hires-fix upscalers (forge's hr_upscaler names one of these).
  --hires-upscalers-dir "$UPSCALER_DIR"
  # LoRAs a request can name in its "lora" list (the Lightning LoRA).
  # On the GGUF model sd.cpp applies them at run time, so a request without
  # one renders exactly as before.
  --lora-model-dir "$LORA_DIR"
  --diffusion-model "\$DIFF"
  --vae "\$VAE"
  --llm "\$LLM"
  # Qwen-Image conventions in sd.cpp. Reconcile with the printed 2.1 docs.
  --cfg-scale 2.5
  --sampling-method euler
  --flow-shift 3
  # Flash attention in the diffusion model only: less VRAM at no quality
  # cost. (Was --diffusion-fa, which current sd.cpp deprecates as this.)
  --flash-attn off,diffusion=on
  # Decode the image in tiles. With ~9.9GB of weights resident, a full
  # 1024x1024 decode needs 7.5GB more than the ~5.4GB left free: without
  # this, every image first fails its decode, logs a burst of ERRORs, then
  # retries tiled anyway. Tiled decode took ~9s in testing.
  --vae-tiling
  # Keep weights in system RAM and move each block to the GPU only while it
  # runs. Needed for 4K hires: one transformer block at 3840x2176 needs
  # ~9.8GB of working memory, and with ~10.9GB of weights resident only
  # ~4.5GB was free ("cannot make enough memory available on Vulkan0").
  # Costs roughly 0.5s per step in PCIe transfers.
  --offload-to-cpu
)
# The encoder's vision weights, for renders with reference images (EDIT mode).
[ -n "\${LLM_VISION:-}" ] && ARGS+=(--llm_vision "\$LLM_VISION")
exec "$SD_DIR/build/bin/sd-server" "\${ARGS[@]}" "\$@"
EOF
    chmod +x "$RUN_SCRIPT"

    # Renders one image with the Lightning LoRA using the model card's
    # schedule, and one normal render to compare quality and time.
    cat > "$HOME/sd-lightning-test.py" <<'PYTEST'
#!/usr/bin/env python3
"""
sd-lightning-test - render one image with the Qwen-Image-2.1 Lightning LoRA,
using the schedule on its model card, and time it next to a normal render.

    python3 ~/sd-lightning-test.py                  # 1024x1024, both renders
    python3 ~/sd-lightning-test.py 1920 1088        # another size
    python3 ~/sd-lightning-test.py --lightning-only

Model card (lightx2v/Qwen-Image-2.1-Lightning): 4 steps, CFG off
(true_cfg_scale 1.0), sigmas [1.0, 0.75, 0.5, 0.25], FlowMatch Euler with
dynamic exponential shift (base_shift 0.5, max_shift 0.9 over 256..8192
image tokens), no terminal stretch. sd.cpp's own flow schedule is a fixed
shift of 3, so the shifted sigmas are computed here and sent as
custom_sigmas. sd-server ignores <lora:...> prompt tags; the LoRA goes in
the request's "lora" list.
"""
import base64, json, math, sys, time, urllib.request

URL = "http://127.0.0.1:7860/sdapi/v1/txt2img"
LORA = "qwen-image-2.1-lightning/pytorch_lora_weights.safetensors"
PROMPT = ("A lighthouse on a rocky headland at dusk, warm lamp light, "
          "stormy violet sky, crashing surf, detailed digital painting")


def lightning_sigmas(width, height):
    tokens = (width // 16) * (height // 16)     # Qwen-Image-2.1: 16x VAE, patch 1
    mu = 0.5 + (0.9 - 0.5) / (8192 - 256) * (tokens - 256)
    e = math.exp(mu)
    return [round(e / (e + (1 / s - 1)), 5) for s in (1.0, 0.75, 0.5, 0.25)] + [0.0]


def render(body, out):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=3600) as r:
        data = json.loads(r.read())
    secs = time.monotonic() - t0
    open(out, "wb").write(base64.b64decode(data["images"][0]))
    print(f"   {secs:6.1f}s  -> {out}")


nums = [int(a) for a in sys.argv[1:] if a.isdigit()]
w, h = (nums + [1024, 1024])[:2]
sig = lightning_sigmas(w, h)
print(f"{w}x{h}: lightning sigmas {sig}")
base = {"prompt": PROMPT, "width": w, "height": h, "seed": 42, "sampler_name": "euler"}
print("Lightning (4 steps, CFG 1):")
render(dict(base, steps=4, cfg_scale=1.0,
            prompt=PROMPT + "<sd_cpp_extra_args>"
            + json.dumps({"sample_params": {"custom_sigmas": sig}}) + "</sd_cpp_extra_args>",
            lora=[{"path": LORA, "multiplier": 1.0}]),
       f"/tmp/lightning-{w}x{h}.png")
if "--lightning-only" not in sys.argv:
    print("Normal (30 steps, CFG 2.5, no LoRA):")
    render(dict(base, steps=30, cfg_scale=2.5), f"/tmp/normal-{w}x{h}.png")
print("Check the sd-server log for LoRA warnings: journalctl --user -u sd-server -n 80")
PYTEST
    chmod +x "$HOME/sd-lightning-test.py"
    bash -n "$RUN_SCRIPT" || die "Generated $RUN_SCRIPT failed syntax check."
    ok "Wrote $RUN_SCRIPT (Vulkan device $idx)"

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/sd-server.service" <<EOF
[Unit]
Description=Mercury sd-server - Qwen-Image-2.1 on RX 9060 XT (Vulkan)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$RUN_SCRIPT
Restart=on-failure
RestartSec=15
TimeoutStartSec=900

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; the service will stop at logout."

    cat <<EOF

    Start:  systemctl --user enable --now sd-server
    Logs:   journalctl --user -u sd-server -f
    LoRA:   python3 ~/sd-lightning-test.py    (Lightning vs normal render, timed)
    Test:   curl -s http://127.0.0.1:$SD_PORT/v1/images/generations \\
              -H 'Content-Type: application/json' \\
              -d '{"prompt":"a lighthouse at dusk","n":1,"size":"1024x1024","response_format":"b64_json"}' \\
              | python3 -c "import sys,json,base64; open('/tmp/t.png','wb').write(base64.b64decode(json.load(sys.stdin)['data'][0]['b64_json']))" \\
              && echo "wrote /tmp/t.png"

EOF
    # Only claim a web UI if one was actually compiled in.
    if grep -q "frontend build disabled" "$SD_DIR/build/configure.log" 2>/dev/null; then
        info "No web UI in this build (pnpm was missing) - API only."
        info "Re-run the build phase to add it: ./install-mercury-imagegen.sh build server"
    else
        info "Web UI: http://<mercury>:$SD_PORT/  - for trying prompts by hand."
    fi
}

# ============================================================================
# PHASE: firewall
# ============================================================================

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || return 0
    say "[firewall] image API and SSH, LAN only"
    have ufw || sudo apt install -y ufw
    sudo ufw allow from "$LAN_SUBNET" to any port "$SD_PORT" proto tcp comment "sd-server" >/dev/null
    sudo ufw allow from "$LAN_SUBNET" to any port 22 proto tcp comment "ssh" >/dev/null
    ok "Allowed $SD_PORT and 22 from $LAN_SUBNET"
    info "sd-server has no API key. Restrict it to Gemini's address if other"
    info "LAN devices should not be able to generate images."
    sudo ufw status | grep -q "Status: active" || \
        warn "ufw inactive. Enable from a LOCAL session: sudo ufw enable"
}

# ============================================================================
# MAIN
# ============================================================================

ALL_PHASES=(base vulkan verify build models server firewall)
main() {
    local -a phases
    if [ $# -gt 0 ]; then phases=("$@"); else phases=("${ALL_PHASES[@]}"); fi
    local p
    for p in "${phases[@]}"; do
        case "$p" in
            base|vulkan|verify|build|models|server|firewall) "phase_$p"; echo ;;
            *) die "Unknown phase '$p'. Valid: ${ALL_PHASES[*]}" ;;
        esac
    done
    say "Done. Images API: http://<mercury>:$SD_PORT/v1/images/generations"
}
main "$@"
