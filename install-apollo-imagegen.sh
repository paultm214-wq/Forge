#!/usr/bin/env bash
#
# install-apollo-imagegen.sh
#
# Rebuilds APOLLO as a dedicated image-generation box:
#   MSI PRO B550M-VC WIFI / Ryzen 7 5700G / 32GB DDR4 (matched pair)
#   Radeon RX 9060 XT 16GB (Navi 44, gfx1200) - PCI_E1, Gen3 x16
#   NVMe in M2_1 (CPU-attached, now that the OCuLink adapter is gone)
#   Kubuntu 26.04 LTS
#
# Runs: ComfyUI + Qwen-Image-2.1 (GGUF), served on :8188.
#
# ===========================================================================
# READ BEFORE RUNNING - ComfyUI on RDNA4 is the riskiest part of this build
# ===========================================================================
# PyTorch-on-ROCm for gfx1200 is newer and rougher than llama.cpp's HIP path.
# Reports specific to the 9060 XT include VAE-stage driver crashes, and
# ComfyUI users on ROCm report "Memory access fault by GPU node-1" after
# repeated generations - the same error family Apollo's llama-server hit.
# The mitigations below are the documented ones, not guesses:
#
#   --disable-pinned-memory   pinned memory causes HIP mismatch errors when
#                             ComfyUI moves from text encoding to sampling -
#                             which is exactly the handoff this setup makes,
#                             since the text encoder lives in system RAM
#   AOTriton experimental     enables attention kernels on gfx1200
#
# If it still faults, see the ALTERNATIVE note at the bottom of this header.
#
# ===========================================================================
# MODEL LAYOUT - why the text encoder goes to system RAM
# ===========================================================================
# Qwen-Image-2.1's diffusion model is ~14.2GB at BF16. Its text encoder is a
# full Qwen3-VL 8B. Both on a 16GB card does not fit with room to sample.
#
#   diffusion model  GGUF Q6_K on GPU   (~6GB)  - runs every sampling step
#   text encoder     int8 in system RAM (~8GB)  - runs ONCE per prompt
#   VAE              BF16 on GPU        (small)
#
# Encoding once per prompt means offloading it costs almost nothing in speed
# and frees 9-17GB of VRAM. 32GB of system RAM holds it comfortably.
#
# Why Q6_K and not Q8_0: a Q8_0 release is currently reported to hit a shape
# mismatch ([136] vs [128]) in some environments. Q4_K_M/Q5_K_M/Q6_K are the
# stable recommendations; Q6_K is the best of those that fits well.
#
# ===========================================================================
# ALTERNATIVE: stable-diffusion.cpp
# ===========================================================================
# If ComfyUI's PyTorch stack keeps faulting, stable-diffusion.cpp runs the
# same GGUF files with NO PyTorch at all, and can use Vulkan instead of ROCm.
# Its author maintains the ComfyUI-GGUF fork that added native Qwen-Image 2.1
# support, so it is the natural fallback. Given Apollo's history with ROCm
# memory faults, a Vulkan path that sidesteps ROCm entirely is worth having
# in reserve. Check it supports 2.1 before switching - the model is days old.
#
# Usage:
#   ./install-apollo-imagegen.sh              # all phases
#   ./install-apollo-imagegen.sh comfyui      # one phase
#
# Phases: base rocm verify comfyui models server firewall

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

COMFY_DIR="${COMFY_DIR:-$HOME/ComfyUI}"
COMFY_VENV="${COMFY_VENV:-$HOME/.venvs/comfyui}"
COMFY_PORT=8188

# PyTorch wheels built by AMD's TheRock for the gfx120X family (RDNA4).
TORCH_INDEX="https://rocm.nightlies.amd.com/v2/gfx120X-all/"

# --- Model sources -----------------------------------------------------------
# Text encoder and VAE: official Comfy-Org repackaging.
COMFY_ORG_REPO="Comfy-Org/Qwen-Image-2.1"
TEXT_ENCODER_GLOB="*qwen3vl_8b_int8_convrot*"   # int8 = lower memory; runs on CPU
VAE_GLOB="*qwen_image_2.1_vae*"

# Diffusion model GGUF. The model is DAYS old and every GGUF repo right now
# is a community upload. Pick one whose provenance you trust, and avoid the
# "Uncensored" variants unless you specifically want an unfiltered model -
# several exist with near-identical names. Prefer an unsloth or Comfy-Org
# GGUF if one appears; they have done Qwen-Image GGUFs before.
GGUF_REPO="${GGUF_REPO:-abenzerps/Qwen-Image-2.1-GGUF}"
GGUF_QUANT="Q6_K"

# ComfyUI-GGUF: the leejet fork has native Qwen-Image 2.1 support. The older
# city96 original raises "Unknown model architecture" on this model.
COMFY_GGUF_REPO="https://github.com/leejet/ComfyUI-GGUF"

LAN_SUBNET="192.168.50.0/24"
CONFIGURE_UFW=true

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

    sudo apt update && sudo apt full-upgrade -y
    sudo apt install -y \
        build-essential cmake ninja-build git curl wget pciutils \
        libcurl4-openssl-dev libatomic1 libquadmath0 \
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
# PHASE: rocm
# ============================================================================

phase_rocm() {
    say "[rocm] Installing ROCm 10.0.0 runtime for gfx1200"

    if have rocminfo && [ -d "$(rocm_path)" ]; then
        ok "ROCm already present at $(rocm_path); skipping install."
        return 0
    fi

    warn "Before continuing, confirm 'Above 4G Decoding' is ENABLED in BIOS."
    warn "AMD requires it, and it matters more with two discrete GPUs."
    confirm

    say "Registering AMD ROCm repository"
    sudo mkdir --parents --mode=0755 /etc/apt/keyrings
    wget -qO- https://stable.repo.amd.com/rocm/gpg/packages.gpg \
        | gpg --dearmor | sudo tee /etc/apt/keyrings/amdrocm.gpg > /dev/null

    sudo tee /etc/apt/sources.list.d/amdrocm-stable.sources > /dev/null <<'EOF'
X-Repo-Id: amdrocm-stable
Types: deb
URIs: https://stable.repo.amd.com/rocm/core/packages/ubuntu2604/
Suites: stable
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/amdrocm.gpg
Enabled: yes
EOF

    sudo apt update

    say "Installing ROCm dev packages for BOTH GPU targets"
    info "gfx1200 -> RX 9060 XT (the only GPU in Apollo now)"
    info "The -dev packages are required to BUILD llama.cpp, not just run it."
    # Runtime + tools only. PyTorch's ROCm wheels bundle their own ROCm
    # userspace, so the -dev packages (needed to BUILD llama.cpp) are not
    # needed here. rocminfo / rocm-smi remain useful for diagnosis.
    sudo apt install -y amdrocm10.0-gfx1200

    say "Granting GPU access to $USER"
    sudo usermod -a -G render,video "$USER"

    cat <<EOF

${BOLD}${YELLOW}    REBOOT REQUIRED.${RESET}
    The kernel driver and your new 'render'/'video' group membership both
    need a full reboot - not just a new terminal, and not just a KDE logout.

        sudo reboot

    Then re-run this script to continue:

        $0

EOF
    exit 0
}

# ============================================================================
# PHASE: verify
# ============================================================================

phase_verify() {
    say "[verify] Confirming the RX 9060 XT is visible"
    have rocminfo || die "rocminfo not found. Run the 'rocm' phase first."

    rocminfo | grep -E "Marketing Name|gfx" || true
    echo

    local n1200
    n1200=$(rocminfo 2>/dev/null | grep -c "Name:.*gfx1200" || true)
    if [ "$n1200" -ge 1 ]; then
        ok "gfx1200 (RX 9060 XT) detected."
    else
        warn "No gfx1200 agent found. Check the card is seated in PCI_E1."
        confirm "Continue anyway?"
    fi

    # The 5700G's integrated Vega also enumerates as a ROCm agent. PyTorch
    # would count it, and ComfyUI uses device 0 by default - so pin explicitly
    # rather than trusting enumeration order.
    info "ROCm agent order (the server pins the discrete card by index):"
    rocminfo 2>/dev/null | grep -E "^\s+Name:\s+gfx" | nl -v0 | sed 's/^/      /'

    groups | grep -qE '\brender\b' || die "User not in 'render' group. Reboot and re-run."
    ok "User is in the 'render' group."
}

# ============================================================================
# PHASE: comfyui
# ============================================================================

phase_comfyui() {
    say "[comfyui] ComfyUI with TheRock PyTorch for gfx120X"

    if [ ! -d "$COMFY_DIR/.git" ]; then
        git clone https://github.com/comfyanonymous/ComfyUI "$COMFY_DIR"
    else
        git -C "$COMFY_DIR" pull --ff-only
    fi

    [ -d "$COMFY_VENV" ] || python3 -m venv "$COMFY_VENV"
    local py="$COMFY_VENV/bin/python" pip="$COMFY_VENV/bin/pip"
    "$pip" install -q -U pip wheel

    say "Installing PyTorch from TheRock's gfx120X index"
    info "These wheels BUNDLE their own ROCm userspace - they do not depend on"
    info "the system ROCm version, which avoids a whole class of mismatch."
    "$pip" install --index-url "$TORCH_INDEX" torch torchvision torchaudio

    say "Checking PyTorch can see the GPU"
    HIP_VISIBLE_DEVICES=0 "$py" - <<'PYEOF' || warn "PyTorch GPU check failed - see above."
import torch
print("  torch      ", torch.__version__)
print("  hip        ", getattr(torch.version, "hip", None))
print("  available  ", torch.cuda.is_available())
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        print(f"  device {i}   ", torch.cuda.get_device_name(i))
PYEOF

    "$pip" install -r "$COMFY_DIR/requirements.txt"

    say "Custom nodes"
    local nodes="$COMFY_DIR/custom_nodes"
    if [ -d "$nodes/ComfyUI-GGUF" ] && \
       ! git -C "$nodes/ComfyUI-GGUF" remote -v | grep -q leejet; then
        warn "An older ComfyUI-GGUF is installed. Replacing it with the leejet fork,"
        warn "which has native Qwen-Image 2.1 support."
        rm -rf "$nodes/ComfyUI-GGUF"
    fi
    [ -d "$nodes/ComfyUI-GGUF" ] || git clone "$COMFY_GGUF_REPO" "$nodes/ComfyUI-GGUF"
    [ -f "$nodes/ComfyUI-GGUF/requirements.txt" ] && \
        "$pip" install -q -r "$nodes/ComfyUI-GGUF/requirements.txt"

    [ -d "$nodes/ComfyUI-Manager" ] || \
        git clone https://github.com/ltdrdata/ComfyUI-Manager "$nodes/ComfyUI-Manager"

    ok "ComfyUI installed at $COMFY_DIR"
}

# ============================================================================
# PHASE: models
# ============================================================================

phase_models() {
    say "[models] Qwen-Image-2.1"

    local hfv="$HOME/.venvs/hf"
    [ -d "$hfv" ] || python3 -m venv "$hfv"
    "$hfv/bin/pip" install -q -U huggingface_hub
    local hf="$hfv/bin/hf"
    local stage="$HOME/models/qwen-image-2.1"
    mkdir -p "$stage" \
        "$COMFY_DIR/models/diffusion_models" \
        "$COMFY_DIR/models/text_encoders" \
        "$COMFY_DIR/models/vae"

    say "Diffusion model: $GGUF_REPO ($GGUF_QUANT)"
    warn "Community upload - confirm you trust $GGUF_REPO before continuing."
    confirm
    "$hf" download "$GGUF_REPO" --local-dir "$stage/gguf" --include "*${GGUF_QUANT}*.gguf"

    say "Text encoder + VAE from $COMFY_ORG_REPO"
    "$hf" download "$COMFY_ORG_REPO" --local-dir "$stage/official" \
        --include "$TEXT_ENCODER_GLOB" --include "$VAE_GLOB"

    # Repos lay files out differently, so find them by pattern rather than
    # assuming a path, then symlink into ComfyUI's model folders. Symlinks
    # keep ComfyUI updates from touching multi-GB files.
    link_one() {
        local pattern="$1" dest="$2" f
        f=$(find "$stage" -iname "$pattern" -type f | sort | head -n1)
        if [ -z "$f" ]; then
            warn "No file matching '$pattern' - check the repo layout."
            return
        fi
        ln -sf "$f" "$dest/$(basename "$f")"
        ok "$(basename "$f") -> $dest"
    }
    link_one "*${GGUF_QUANT}*.gguf"    "$COMFY_DIR/models/diffusion_models"
    link_one "$TEXT_ENCODER_GLOB"      "$COMFY_DIR/models/text_encoders"
    link_one "$VAE_GLOB"               "$COMFY_DIR/models/vae"

    cat <<EOF

    In ComfyUI, start from the Qwen-Image 2.1 template and change:
      - UNETLoader       -> Unet Loader (GGUF), pick the ${GGUF_QUANT} file
      - CLIPLoader       -> type: qwen_image, pick the int8 text encoder,
                            and set its DEVICE to cpu (this is the VRAM saving)
      - VAELoader        -> the qwen_image_2.1 VAE

EOF
}

# ============================================================================
# PHASE: server
# ============================================================================

phase_server() {
    say "[server] ComfyUI as a systemd user service"

    local run="$HOME/run_comfyui.sh"
    cat > "$run" <<EOF
#!/usr/bin/env bash
# Auto-generated by install-apollo-imagegen.sh
set -euo pipefail

# Pin the discrete card. The 5700G's integrated Vega also enumerates, and
# ComfyUI uses device 0 by default. If verify showed the iGPU at index 0,
# change this to 1.
export HIP_VISIBLE_DEVICES=0

# Attention kernels on gfx1200 are still flagged experimental in AOTriton.
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1

# Skip MIOpen's exhaustive kernel search on first use of each shape.
export MIOPEN_FIND_MODE=FAST

cd "$COMFY_DIR"
exec "$COMFY_VENV/bin/python" main.py \\
    --listen 0.0.0.0 --port $COMFY_PORT \\
    --disable-pinned-memory \\
    --use-pytorch-cross-attention \\
    "\$@"
EOF
    chmod +x "$run"
    bash -n "$run" || die "Generated $run failed syntax check."
    ok "Wrote $run"

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/comfyui.service" <<EOF
[Unit]
Description=ComfyUI on Apollo - Qwen-Image-2.1 on RX 9060 XT
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$run
Restart=on-failure
RestartSec=15
TimeoutStartSec=600

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; the service will stop at logout."

    cat <<EOF

    Start:   systemctl --user enable --now comfyui
    Logs:    journalctl --user -u comfyui -f
    UI:      http://$(hostname -I | awk '{print $1}'):$COMFY_PORT

    If generations fault with "Memory access fault by GPU node-1", add
    --lowvram to the ExecStart flags first. If the VAE stage specifically
    crashes, use the tiled VAE decode node.

EOF
}

# ============================================================================
# PHASE: firewall
# ============================================================================

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || return 0
    say "[firewall] ComfyUI to LAN only"
    have ufw || sudo apt install -y ufw
    sudo ufw allow from "$LAN_SUBNET" to any port "$COMFY_PORT" proto tcp comment "comfyui" >/dev/null
    sudo ufw allow from "$LAN_SUBNET" to any port 22 proto tcp comment "ssh" >/dev/null
    ok "Allowed $COMFY_PORT and 22 from $LAN_SUBNET"
    sudo ufw status | grep -q "Status: active" || \
        warn "ufw inactive. Enable from a LOCAL session: sudo ufw enable"
}

# ============================================================================
# MAIN
# ============================================================================

ALL_PHASES=(base rocm verify comfyui models server firewall)
main() {
    local -a phases
    if [ $# -gt 0 ]; then phases=("$@"); else phases=("${ALL_PHASES[@]}"); fi
    local p
    for p in "${phases[@]}"; do
        case "$p" in
            base|rocm|verify|comfyui|models|server|firewall) "phase_$p"; echo ;;
            *) die "Unknown phase '$p'. Valid: ${ALL_PHASES[*]}" ;;
        esac
    done
    say "Done."
}
main "$@"
