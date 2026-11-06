#!/usr/bin/env bash
#
# install-gemini-llm.sh
#
# Builds GEMINI, the always-on LLM server:
#   ASRock X870 Taichi Creator / Ryzen 5 9600X / 64GB DDR5-6000 CL30
#   2x Radeon RX 9070 XT 16GB (Navi 48, gfx1201) - PCIE1 + PCIE2, Gen5 x8/x8
#   Kubuntu 26.04 LTS
#
# WHY THIS BUILD SHOULD BE MORE STABLE THAN APOLLO WAS
# ---------------------------------------------------
# Apollo's llama-server crashed twice with GPU page faults, both inside KV-
# state serialisation across a tensor split spanning TWO DIFFERENT GPU
# ARCHITECTURES (gfx1201 + gfx1200). Two identical cards is the common,
# well-tested configuration. The workarounds (--ctx-checkpoints 0 and
# --cache-ram 0) are still set below, but they are now candidates to remove
# rather than permanent fixtures - see the server phase.
#
# Also: identical cards mean identical memory bandwidth, so --tensor-split
# 1,1 is correct here. Apollo needed 3,2 because the 9060 XT has half the
# bandwidth of the 9070 XT.
#
# Installs: ROCm 10.0.0, llama.cpp (HIP, BOTH gfx targets), Qwen3.8-27B
# with vision projector, Open WebUI, SearXNG, Wyoming STT/TTS/wake-word
# for Home Assistant, and Hermes Agent.
#
# Usage:
#   chmod +x install-llm-server.sh
#   ./install-llm-server.sh              # all phases, in order
#   ./install-llm-server.sh rocm         # a single phase
#   ./install-llm-server.sh build model  # several phases
#
# Phases: base rocm verify build model server services agents
#         sandbox claude forge firewall
#
# Safe to re-run. Completed steps detect themselves and skip.

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
MODEL_DIR="${MODEL_DIR:-$HOME/models}"
STACK_DIR="${STACK_DIR:-$HOME/llm-stack}"

HF_REPO="unsloth/Qwen3.8-27B-GGUF"
HF_QUANT="UD-Q4_K_XL"       # ~17.9GB. Fits 32GB pool with room for KV cache,
                            # vision projector and compute buffers.
                            # UD-Q3_K_XL (~13.4GB) fits ONE 16GB card alone.
                            # UD-Q6_K_XL (~23-26GB) is the quality ceiling
                            # that still leaves usable context headroom.
                            # Q8_0 (~31GB) will NOT leave room for context.
DOWNLOAD_MMPROJ=true        # Vision projector. Qwen3.8-27B is natively
                            # multimodal; this is what lets the SAME server
                            # handle Frigate image analysis. Keep it on.

# GPU architecture target.
GFX_TARGETS="gfx1201"        # both cards are Navi 48 - one target only

# Server tuning. Start conservative; raise CTX_SIZE once you have seen
# actual VRAM use in rocm-smi.
CTX_SIZE=98304              # 32GB of equal VRAM; walk up from here
PARALLEL_SLOTS=2            # CTX_SIZE is divided across slots
TENSOR_SPLIT="1,1"          # Identical cards, identical bandwidth.
                            # VERIFY ORDER FIRST with: llama-server --list-devices
                            # If the 9060 XT enumerates as device 0, use "2,3".
REASONING_EFFORT="low"      # xhigh | medium | low | none
SPEC_DRAFT_N_MAX=2          # MTP draft depth. 2 is the dense-model
                            # recommendation; MoE favours 3. Sweep 2-4 and
                            # watch the acceptance rate - too deep and the
                            # main model rejects drafts faster than they
                            # save work.
                            # Default is xhigh - far too slow for voice control.
LLAMA_DEVICES="ROCm0,ROCm1" # Pin to the two discrete cards. The 9600X has an
                            # integrated Radeon (Raphael, gfx1036) that may
                            # enumerate as a ROCm agent - and it may enumerate
                            # FIRST. Confirm with: llama-server --list-devices
                            # (Carried over from Apollo, where the 5700G's
                            # integrated Vega also enumerates as a ROCm agent
                            # (device 2, DID 0x1638) and must never be
                            # selected. Verify ordering with:
                            #   llama-server --list-devices

SERVER_PORT=8080
OPEN_WEBUI_PORT=3000
SEARXNG_PORT=8888
WHISPER_PORT=10300
PIPER_PORT=10200
OWW_PORT=10400

WHISPER_MODEL="base-int8"          # tiny-int8 | base-int8 | small-int8 | medium-int8
PIPER_VOICE="en_US-lessac-medium"

LAN_IP="192.168.50.96"       # Gemini TAKES OVER .96. Move the DHCP reservation
                             # from Apollo's MAC to Gemini's, and every HA,
                             # Frigate and Open WebUI config keeps working.

# --- forge pipeline -----------------------------------------------------------
APOLLO_IP="192.168.50.8"     # coder (TielCoder router :8080) + primary images (:7860)
MERCURY_IP="192.168.50.129"  # Lightning images (:7860) + triage (router :8080)
VULCAN_IP="192.168.50.151"   # principal coder: Strata (:8081); 3D shapes (:7870)
FORGE_DIR="$HOME/forge"
FORGE_SRC="${FORGE_SRC:-$(dirname "$(readlink -f "$0")")/forge/forge.py}"
# Packages generated code may import. Verification runs with NO network, so
# anything a spec needs must be baked into the sandbox image. Your Frigate
# sample spec uses aiohttp - it could not be verified without it here.
SANDBOX_PIP="aiohttp"
LAN_SUBNET="192.168.50.0/24"

API_KEY_FILE="$HOME/.llama-api-key"
RUN_SCRIPT="$HOME/run_llm.sh"

INSTALL_HERMES=true
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
    say "[rocm] Installing ROCm 10.0.0 for gfx1201"

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
    info "gfx1201 -> both RX 9070 XT cards"
    info "The -dev packages are required to BUILD llama.cpp, not just run it."
    sudo apt install -y amdrocm-core-dev10.0-gfx1201

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
    say "[verify] Confirming both GPUs are visible to ROCm"

    have rocminfo || die "rocminfo not found. Run the 'rocm' phase first."

    info "PCIe-level view:"
    lspci | grep -iE "vga|display|3d" || true
    echo

    info "ROCm agents:"
    rocminfo | grep -E "Marketing Name|gfx" || true
    echo

    rocm-smi --showproductname 2>/dev/null || amd-smi static 2>/dev/null || true
    echo

    local n1201 nigpu
    n1201=$(rocminfo 2>/dev/null | grep -c "Name:.*gfx1201" || true)
    nigpu=$(rocminfo 2>/dev/null | grep -cE "Name:.*gfx103[0-9]" || true)

    if [ "$n1201" -ge 2 ]; then
        ok "Two gfx1201 agents found - both RX 9070 XTs."
    else
        warn "Expected TWO gfx1201 agents, found $n1201."
        warn "Check both cards are seated in PCIE1 and PCIE2, that PCIE bifurcation"
        warn "is set to x8/x8 in BIOS, and that Above 4G Decoding + ReBAR are on."
        confirm "Continue anyway?"
    fi
    if [ "$nigpu" -gt 0 ]; then
        warn "The 9600X's integrated GPU is visible to ROCm ($nigpu agent)."
        warn "LLAMA_DEVICES pins the two discrete cards, but VERIFY the order with"
        warn "  llama-server --list-devices"
        warn "If the iGPU is ROCm0, the discrete cards are ROCm1,ROCm2."
    fi

    if groups | grep -qE '\brender\b'; then
        ok "User is in the 'render' group."
    else
        die "User is not in the 'render' group yet. Reboot and re-run."
    fi
}

# ============================================================================
# PHASE: build
# ============================================================================

phase_build() {
    say "[build] Building llama.cpp with HIP for $GFX_TARGETS"

    local rp; rp=$(rocm_path)
    export PATH="$rp/bin:$PATH"
    export ROCM_PATH="$rp"

    if [ ! -d "$LLAMA_DIR/.git" ]; then
        git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    else
        info "Updating existing checkout at $LLAMA_DIR"
        git -C "$LLAMA_DIR" pull --ff-only
    fi

    info "Qwen3.8-27B uses the 'qwen3_5' architecture, registered in llama.cpp"
    info "from its mid-2026 release onward. A stale checkout will fail with"
    info "'unknown model architecture', hence the pull above."

    local hipcxx hip_root
    if have hipconfig; then
        hipcxx="$(hipconfig -l 2>/dev/null)/clang"
        hip_root="$(hipconfig -R 2>/dev/null)"
    else
        hipcxx="$rp/llvm/bin/clang"
        hip_root="$rp"
    fi
    [ -x "$hipcxx" ] || die "HIP compiler not found at $hipcxx"

    say "Configuring (this is where the dual-target fix lives)"
    HIPCXX="$hipcxx" HIP_PATH="$hip_root" \
    cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" \
        -G Ninja \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS="$GFX_TARGETS" \
        -DCMAKE_BUILD_TYPE=Release

    say "Compiling (expect 10-25 minutes on a 5700G)"
    cmake --build "$LLAMA_DIR/build" --config Release -j"$(nproc)" \
        --target llama-cli llama-server llama-mtmd-cli llama-bench llama-gguf-split

    ok "Built. Binaries in $LLAMA_DIR/build/bin"

    say "Registering ROCm libraries with the dynamic linker"
    # ROCm 10 does not always drop a file into /etc/ld.so.conf.d, so binaries
    # linked against hipBLAS/rocBLAS resolve only when LD_LIBRARY_PATH happens
    # to be set. That works in an interactive shell and fails under systemd.
    # Fixing it system-wide means every tool benefits, not just llama-server.
    if [ -z "$(ldconfig -p 2>/dev/null | grep -m1 libhipblas)" ]; then
        printf '%s\n%s\n' "$rp/lib" "$rp/lib64" \
            | sudo tee /etc/ld.so.conf.d/rocm.conf >/dev/null
        sudo ldconfig
        if ldconfig -p | grep -q libhipblas; then
            ok "ROCm libraries registered ($(ldconfig -p | grep -c rocm) entries)"
        else
            warn "libhipblas still not on the linker path. Locate it with:"
            warn "    find / -name 'libhipblas.so*' 2>/dev/null"
            warn "then add its directory to /etc/ld.so.conf.d/rocm.conf"
        fi
    else
        ok "ROCm libraries already on the linker path."
    fi

    echo
    say "Device enumeration - CHECK THIS against your TENSOR_SPLIT setting"
    "$LLAMA_DIR/build/bin/llama-server" --list-devices || true
    echo
    info "TENSOR_SPLIT is '$TENSOR_SPLIT' - even, since the cards are identical."
    info "What matters here is that LLAMA_DEVICES names the two 9070 XTs and"
    info "NOT the integrated GPU. Check the list above."
    confirm
}

# ============================================================================
# PHASE: model
# ============================================================================

phase_model() {
    say "[model] Downloading $HF_REPO : $HF_QUANT"

    mkdir -p "$MODEL_DIR"
    local venv="$HOME/.venvs/hf"
    [ -d "$venv" ] || python3 -m venv "$venv"
    # shellcheck disable=SC1091
    source "$venv/bin/activate"
    # No [cli] extra: huggingface-hub 1.x bundles the `hf` command in the base
    # package, and asking for the extra just prints
    #   WARNING: huggingface-hub 1.31.0 does not provide the extra 'cli'
    pip install -q -U huggingface_hub

    # Xet is HF's chunked content-addressed transfer layer. It is faster when
    # it works, but more fragile on a connection that hiccups - a dropped
    # chunk surfaces as:
    #   RuntimeError: Task error: File reconstruction error: CAS Client Error
    # Set HF_HUB_DISABLE_XET=1 before running this phase to fall back to plain
    # HTTP, which resumes more reliably on a long transfer.
    if [ "${HF_HUB_DISABLE_XET:-0}" = "1" ]; then
        info "Xet disabled; using plain HTTP transfer."
        export HF_HUB_DISABLE_XET=1
    fi

    local target="$MODEL_DIR/${HF_REPO##*/}"

    hf download "$HF_REPO" --local-dir "$target" --include "*${HF_QUANT}*"

    if [ "$DOWNLOAD_MMPROJ" = true ]; then
        say "Downloading vision projector (enables Frigate image analysis)"
        hf download "$HF_REPO" --local-dir "$target" --include "*mmproj*"
    fi

    deactivate

    MODEL_PATH=$(find "$target" -iname "*${HF_QUANT}*.gguf" ! -iname "*mmproj*" | sort | head -n1)
    [ -n "$MODEL_PATH" ] || die "No .gguf matching '$HF_QUANT' under $target"

    MMPROJ_PATH=""
    if [ "$DOWNLOAD_MMPROJ" = true ]; then
        MMPROJ_PATH=$(find "$target" -iname "*mmproj*.gguf" | sort | head -n1)
        [ -n "$MMPROJ_PATH" ] || warn "mmproj requested but not found; vision disabled."
    fi

    mkdir -p "$STACK_DIR"
    printf '%s\n' "$MODEL_PATH"  > "$STACK_DIR/.model_path"
    printf '%s\n' "$MMPROJ_PATH" > "$STACK_DIR/.mmproj_path"

    ok "Model:  $MODEL_PATH"
    # An if, not `[ ] && ok`: as a function's LAST statement, a false test
    # becomes its return value, and under set -e that silently killed the
    # script right after a successful download.
    if [ -n "$MMPROJ_PATH" ]; then ok "mmproj: $MMPROJ_PATH"; fi
}

# UUIDs of the discrete RX 9070 XTs (gfx1201), comma-separated, from
# rocminfo. Matches each agent's FIRST Name line exactly, so the
# "amdgcn-amd-amdhsa--gfx1201" ISA line inside the same block cannot count
# twice. rocminfo's output is captured, then parsed - never piped into an
# early-exiting reader (that SIGPIPE bug produced Apollo's false
# "no gfx1201 agent" warning).
discrete_uuids() {
    local ri
    ri=$(rocminfo 2>/dev/null || true)
    awk '
        function emit() {
            if (name == "gfx1201" && uuid ~ /^GPU-/ && uuid != "GPU-XX")
                out = out (out == "" ? "" : ",") uuid
        }
        /^Agent [0-9]+/             { emit(); name = ""; uuid = ""; next }
        /^[ \t]*Name:/ && name == "" { name = $2 }
        /^[ \t]*Uuid:/ && uuid == "" { uuid = $2 }
        END { emit(); print out }' <<< "$ri"
}

# ============================================================================
# PHASE: server
# ============================================================================

phase_server() {
    say "[server] Writing run script and systemd unit"

    mkdir -p "$STACK_DIR"
    local model mmproj
    model=$(cat "$STACK_DIR/.model_path" 2>/dev/null || true)
    mmproj=$(cat "$STACK_DIR/.mmproj_path" 2>/dev/null || true)
    [ -n "$model" ] || die "No model recorded. Run the 'model' phase first."

    if [ ! -f "$API_KEY_FILE" ]; then
        openssl rand -hex 24 > "$API_KEY_FILE"
        chmod 600 "$API_KEY_FILE"
        ok "Generated API key at $API_KEY_FILE"
    else
        info "Reusing existing API key at $API_KEY_FILE"
    fi

    # Build the argument list as an ARRAY. The original draft interpolated an
    # optional --mmproj flag inside a backslash-continued heredoc; when the
    # variable was empty it emitted a blank line mid-command, silently
    # truncating every flag that followed. Arrays cannot do that.
    # NOTE: --no-mmap was removed from this list. Recent llama.cpp converted
    # several boolean flags to value-taking form (--flash-attn became -fa on),
    # and this build rejects the bare version:
    #     error: invalid argument: --no-mmap
    # It was only ever a minor optimisation - with -ngl 999 the weights live
    # in VRAM, so page-cache behaviour on the host barely matters. Check
    # `llama-server --help | grep -i mmap` if you want the current spelling.
    #
    # llama.cpp stops at the FIRST bad argument, so a rejection means every
    # flag before it parsed fine and everything after is still untested.
    local -a args=(
        -m "$model"
        --alias "qwen3.8-27b"
        -c "$CTX_SIZE"
        --parallel "$PARALLEL_SLOTS"
        --host 0.0.0.0 --port "$SERVER_PORT"
        --api-key-file "$API_KEY_FILE"
        -ngl 999
        --tensor-split "$TENSOR_SPLIT"
        -fa on
        --cache-type-k q8_0
        --cache-type-v q8_0
        -b 2048 -ub 512
        --jinja
        --chat-template-kwargs "{\"reasoning_effort\":\"$REASONING_EFFORT\"}"
        # Self-speculative decoding using the MTP head already inside the
        # GGUF - Unsloth kept Qwen's nextn tensors, llama.cpp loads them and
        # ignores them without --spec-type. No extra download, no extra
        # weights in VRAM beyond a small draft KV context.
        --spec-type draft-mtp
        --spec-draft-n-max "$SPEC_DRAFT_N_MAX"
        # Context checkpoints faulted the GPU on this dual-architecture
        # tensor-split (gfx1201 + gfx1200). The trace was a page fault in
        # state_seq_get_data pulling KV state off the second device, after
        # ~1.7 hours of normal service. Disabling the feature fixed it.
        # Costs prefix reuse across similar prompts; keeps the server alive.
        # Retry the default (32) after a llama.cpp update and see.
        --ctx-checkpoints 0
        # One shared KV pool across slots rather than fixed halves.
        --kv-unified
        # Reasoning traces are otherwise resent each turn, eating context.
        --no-reasoning-preserve
        # Server warns Qwen-VL needs >=1024 image tokens for grounding tasks;
        # affects Frigate caption quality.
        --image-min-tokens 1024
        # Second entry point into the same bug. --ctx-checkpoints 0 closed the
        # state_seq_get_data path (reading KV state OUT); this closes
        # state_seq_set_data via server_prompt_cache::load (writing it back
        # IN), which fires when a slot is reused by LRU:
        #     server_prompt_cache::load -> llama_context::state_seq_set_data
        #       -> ggml_backend_cuda_buffer_set_tensor -> hipMemcpyAsync
        #         -> Memory access fault by GPU node-1
        # Default is 8192 MiB. Setting 0 also disables --cache-idle-slots,
        # which requires cache-ram.
        #
        # BOTH crashes were KV state serialisation across a tensor split
        # spanning two different GPU architectures. Gemini's cards are
        # identical, which is the common, tested path - so these two flags
        # are now CANDIDATES FOR REMOVAL. After a week of stability, remove
        # --cache-ram 0 first (restores prompt caching, the bigger win for
        # Home Assistant's repeated system prompt). If that holds for another
        # week, remove --ctx-checkpoints 0. One at a time.
        --cache-ram 0
        --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0
        --metrics
    )
    [ -n "$mmproj" ] && args+=( --mmproj "$mmproj" )
    [ -n "$LLAMA_DEVICES" ] && args+=( --device "$LLAMA_DEVICES" )

    # Emit as a bash array rather than a backslash-continued command. Arrays
    # survive empty/optional values, quote safely, and are far easier to edit
    # by hand later. Grouping: start a new line at each flag, keep its value
    # on the same line.
    local vis; vis=$(discrete_uuids)
    if [ "$(tr ',' '\n' <<< "$vis" | grep -c '^GPU-' || true)" -eq 2 ]; then
        ok "Exposing only the two RX 9070 XTs to ROCm: $vis"
    else
        warn "Could not identify both RX 9070 XTs by UUID (found: '${vis:-none}')."
        warn "Not hiding the iGPU - relying on --device $LLAMA_DEVICES alone. Check"
        warn "  llama-server --list-devices"
        warn "and re-run this phase after a reboot if rocminfo was not yet readable."
        vis=""
    fi

    local a line=""
    {
        echo '#!/usr/bin/env bash'
        echo '# Auto-generated by install-llm-server.sh. Edit freely.'
        echo 'set -euo pipefail'
        echo
        echo "export ROCM_PATH=\"$(rocm_path)\""
        echo 'export PATH="$ROCM_PATH/bin:$PATH"'
        echo '# systemd starts services with a minimal environment - it does not'
        echo '# read your shell profile. Without this the binary loads fine by'
        echo '# hand and dies under systemd with:'
        echo '#   error while loading shared libraries: libhipblas.so.3'
        echo 'export LD_LIBRARY_PATH="$ROCM_PATH/lib:$ROCM_PATH/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"'
        if [ -n "$vis" ]; then
            echo '# Hide the 9600X'"'"'s integrated GPU from ROCm entirely, exposing only'
            echo '# the two RX 9070 XTs - by unique ID, not index, since the iGPU can'
            echo '# enumerate first and shift every index. With this, ROCm0 and ROCm1'
            echo '# in --device are always the two cards.'
            echo "export ROCR_VISIBLE_DEVICES=\"$vis\""
        fi
        echo
        echo 'ARGS=('
        for a in "${args[@]}"; do
            if [[ "$a" == -* ]]; then
                [ -n "$line" ] && printf '  %s\n' "$line"
                line=$(printf '%q' "$a")
            else
                line="$line $(printf '%q' "$a")"
            fi
        done
        [ -n "$line" ] && printf '  %s\n' "$line"
        echo ')'
        echo
        printf 'exec %q "${ARGS[@]}" "$@"\n' "$LLAMA_DIR/build/bin/llama-server"
    } > "$RUN_SCRIPT"

    chmod +x "$RUN_SCRIPT"
    bash -n "$RUN_SCRIPT" || die "Generated $RUN_SCRIPT failed syntax check."
    ok "Wrote $RUN_SCRIPT (syntax verified)"

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/llama-server.service" <<EOF
[Unit]
Description=llama.cpp server on Gemini - Qwen3.8-27B $HF_QUANT on 2x gfx1201
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$RUN_SCRIPT
Restart=on-failure
RestartSec=10
# A cold load of ~18GB from a chipset-attached Gen3 x4 NVMe took 5.5
# minutes with a cold page cache. Ten minutes was uncomfortably close to
# that; systemd killing a load mid-flight looks exactly like a crash loop.
TimeoutStartSec=1800

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; the service will not start until you log in."

    cat <<EOF

    Start it:   systemctl --user enable --now llama-server.service
    Logs:       journalctl --user -u llama-server.service -f
    VRAM:       watch -n 0.5 rocm-smi
    API key:    cat $API_KEY_FILE

EOF
}

# ============================================================================
# PHASE: services
# ============================================================================

phase_services() {
    say "[services] Docker stack: Open WebUI, SearXNG, Wyoming STT/TTS/wake-word"

    if ! have docker; then
        say "Installing Docker Engine"
        sudo install -m 0755 -d /etc/apt/keyrings
        sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            -o /etc/apt/keyrings/docker.asc
        sudo chmod a+r /etc/apt/keyrings/docker.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
            | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt update
        sudo apt install -y docker-ce docker-ce-cli containerd.io \
            docker-buildx-plugin docker-compose-plugin
        sudo usermod -aG docker "$USER"
        warn "You were added to the 'docker' group. Log out and back in, or"
        warn "prefix the compose commands below with sudo for now."
    fi

    mkdir -p "$STACK_DIR"/{searxng,whisper,piper,openwakeword}

    local api_key; api_key=$(cat "$API_KEY_FILE" 2>/dev/null || echo "")

    # SearXNG needs the JSON output format enabled for Open WebUI to query it.
    if [ ! -f "$STACK_DIR/searxng/settings.yml" ]; then
        cat > "$STACK_DIR/searxng/settings.yml" <<EOF
use_default_settings: true
server:
  secret_key: "$(openssl rand -hex 32)"
  limiter: false
search:
  formats:
    - html
    - json
EOF
        ok "Wrote SearXNG settings with JSON output enabled."
    fi

    cat > "$STACK_DIR/docker-compose.yml" <<EOF
# Auto-generated by install-llm-server.sh
# llama-server runs on the HOST (systemd user unit), not in Docker, so
# containers reach it over the LAN address rather than a compose network.

services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "${OPEN_WEBUI_PORT}:8080"
    environment:
      - OPENAI_API_BASE_URL=http://${LAN_IP}:${SERVER_PORT}/v1
      - OPENAI_API_KEY=${api_key}
      - ENABLE_WEB_SEARCH=true
      - WEB_SEARCH_ENGINE=searxng
      - SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>
    volumes:
      - open-webui-data:/app/backend/data
    depends_on:
      - searxng

  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    ports:
      - "${SEARXNG_PORT}:8080"
    volumes:
      - ./searxng:/etc/searxng:rw
    environment:
      - SEARXNG_BASE_URL=http://${LAN_IP}:${SEARXNG_PORT}/

  wyoming-whisper:
    image: rhasspy/wyoming-whisper:latest
    container_name: wyoming-whisper
    restart: unless-stopped
    ports:
      - "${WHISPER_PORT}:10300"
    volumes:
      - ./whisper:/data
    command: --model ${WHISPER_MODEL} --language en

  wyoming-piper:
    image: rhasspy/wyoming-piper:latest
    container_name: wyoming-piper
    restart: unless-stopped
    ports:
      - "${PIPER_PORT}:10200"
    volumes:
      - ./piper:/data
    command: --voice ${PIPER_VOICE}

  wyoming-openwakeword:
    image: rhasspy/wyoming-openwakeword:latest
    container_name: wyoming-openwakeword
    restart: unless-stopped
    ports:
      - "${OWW_PORT}:10400"
    volumes:
      - ./openwakeword:/data
    command: --preload-model ok_nabu

volumes:
  open-webui-data:
EOF

    ok "Wrote $STACK_DIR/docker-compose.yml"

    cat <<EOF

    Start the stack:
        cd $STACK_DIR && docker compose up -d

    Then:
        Open WebUI   http://${LAN_IP}:${OPEN_WEBUI_PORT}
        SearXNG      http://${LAN_IP}:${SEARXNG_PORT}

    Open WebUI should auto-detect the model from the OPENAI_API_BASE_URL
    environment variable. If it does not, add it manually under
    Admin Settings > Connections > OpenAI with the URL and key above.

    Speech services run on CPU deliberately - they are fast enough on a
    5700G and every GB they do not take is a GB of context for the 27B.

EOF
}

# ============================================================================
# PHASE: agents
# ============================================================================

phase_agents() {
    say "[agents] Hermes Agent and coding-agent endpoints"

    if [ "$INSTALL_HERMES" = true ]; then
        if have hermes; then
            info "hermes already installed; skipping."
        else
            curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
        fi

        cat <<EOF

    Hermes is configured INTERACTIVELY - it does not take the
    'hermes config set model.provider ...' commands the earlier draft used.
    Run:

        hermes setup

    Choose a custom / OpenAI-compatible provider and give it:
        Base URL:  http://127.0.0.1:${SERVER_PORT}/v1
        API key:   $(cat "$API_KEY_FILE" 2>/dev/null || echo '<see '"$API_KEY_FILE"'>')
        Model:     qwen3.8-27b

    Switch models later with:  hermes model
    Check wiring with:         hermes doctor

EOF
    fi

    cat <<EOF
    Any OpenAI-compatible coding agent points at the same endpoint:

        export OPENAI_API_BASE=http://127.0.0.1:${SERVER_PORT}/v1
        export OPENAI_API_KEY=\$(cat $API_KEY_FILE)

        aider --model openai/qwen3.8-27b
        # Cline / Roo Code / Continue.dev: set a custom OpenAI base URL
        #   in extension settings to the same value.

    For Home Assistant automation authoring, run the agent against a git
    checkout of your HA config rather than editing YAML through a chat box.

EOF
}

# ============================================================================
# PHASE: sandbox
# ============================================================================

phase_sandbox() {
    say "[sandbox] forge-sandbox image for executing generated code"
    have docker || die "Docker missing - run the services phase first."
    local tmp; tmp=$(mktemp -d)
    cat > "$tmp/Dockerfile" <<EOF
FROM python:3.12-slim
# Installed as root at BUILD time; the container then runs as uid 1000 with
# no network, a read-only root, and dropped capabilities (see forge.py).
RUN pip install --no-cache-dir ${SANDBOX_PIP}
RUN useradd -u 1000 -m runner
USER 1000:1000
WORKDIR /work
EOF
    # sudo: docker group membership from the services phase needs a re-login
    # before it applies to this shell.
    sudo docker build -q -t forge-sandbox:latest "$tmp" >/dev/null
    rm -rf "$tmp"
    ok "Built forge-sandbox:latest (python 3.12 + ${SANDBOX_PIP:-nothing extra})"
    warn "forge runs docker as $USER. Docker group membership is root-equivalent"
    warn "on this host - the sandbox confines GENERATED code, not forge itself."
}

# ============================================================================
# PHASE: claude
# ============================================================================

phase_claude() {
    say "[claude] Claude Code CLI for the final review gate"
    have npm || sudo apt install -y nodejs npm
    # forge renders SVG outputs to PNG so Claude's review can see them.
    have rsvg-convert || sudo apt install -y librsvg2-bin
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    grep -q 'npm-global/bin' "$HOME/.profile" 2>/dev/null || \
        echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.profile"
    export PATH="$HOME/.npm-global/bin:$PATH"
    npm install -g @anthropic-ai/claude-code
    ok "claude: $(command -v claude)"

    mkdir -p "$FORGE_DIR"
    if [ ! -f "$FORGE_DIR/env" ]; then
        cat > "$FORGE_DIR/env" <<'EOF'
# Read by the forge systemd units. Mode 600.
#
# Claude Code needs credentials. EITHER log in once interactively by running
# `claude` as this user (headless runs reuse that login - and count against
# your Pro plan's limits), OR set an API key here for separate, metered
# billing. For an unattended pipeline the API key is the cleaner fit.
#ANTHROPIC_API_KEY=
EOF
        chmod 600 "$FORGE_DIR/env"
    fi
    cat <<EOF

    Authenticate ONE of these ways before the first run:
      a) run 'claude' once as $USER and log in, or
      b) put ANTHROPIC_API_KEY in $FORGE_DIR/env

    forge calls it read-only: --permission-mode plan, tools Read/Grep/Glob.
    It can inspect the generated project but never change it.
EOF
}

# ============================================================================
# PHASE: forge
# ============================================================================

phase_forge() {
    say "[forge] the pipeline"
    [ -f "$FORGE_SRC" ] || die "forge.py not found at $FORGE_SRC"
    mkdir -p "$FORGE_DIR/inbox" "$FORGE_DIR/runs" "$HOME/.local/bin"
    install -m 0755 "$FORGE_SRC" "$FORGE_DIR/forge.py"
    for extension in forge_control.py forge_health.py forge_provenance.py forge_operations.py forge_recovery.py forge_targets.py forge_dependencies.py forge_extra_helpers.py; do
        install -m 0644 "$(dirname "$FORGE_SRC")/$extension" "$FORGE_DIR/$extension"
    done
    install -m 0755 "$(dirname "$FORGE_SRC")/install-forge-extras.sh" "$FORGE_DIR/install-forge-extras.sh"
    cp -a "$(dirname "$FORGE_SRC")/toolchain-images" "$FORGE_DIR/"
    # forge_game.py: the `game` helper of game, emulator, mod and RE test
    # sandboxes - forge reads it from beside forge.py.
    if [ -f "$(dirname "$FORGE_SRC")/forge_game.py" ]; then
        install -m 0644 "$(dirname "$FORGE_SRC")/forge_game.py" "$FORGE_DIR/forge_game.py"
    else
        warn "forge_game.py is not beside $FORGE_SRC - game and mod projects need it in $FORGE_DIR"
    fi
    # forge_ux.py: the UX and accessibility check for web projects (runs in
    # forge-sandbox-ux, from install-forge-toolchains.sh ux).
    if [ -f "$(dirname "$FORGE_SRC")/forge_ux.py" ]; then
        install -m 0644 "$(dirname "$FORGE_SRC")/forge_ux.py" "$FORGE_DIR/forge_ux.py"
    else
        warn "forge_ux.py is not beside $FORGE_SRC - web projects get no UX check without it"
    fi
    # forge_dotnet.py + fo4/mutagen-*-api.json.gz: the real Mutagen API (read
    # from its DLLs) for C# compile errors and the fo4 coder notes.
    if [ -f "$(dirname "$FORGE_SRC")/forge_dotnet.py" ]; then
        install -m 0644 "$(dirname "$FORGE_SRC")/forge_dotnet.py" "$FORGE_DIR/forge_dotnet.py"
        mkdir -p "$FORGE_DIR/fo4" "$FORGE_DIR/tools"
        for f in "$(dirname "$FORGE_SRC")"/fo4/*-api.json.gz; do
            [ -f "$f" ] && install -m 0644 "$f" "$FORGE_DIR/fo4/"
        done
        [ -f "$(dirname "$FORGE_SRC")/tools/dotnet_api.py" ] && \
            install -m 0644 "$(dirname "$FORGE_SRC")/tools/dotnet_api.py" "$FORGE_DIR/tools/"
    fi
    # design/: the house style every project with a UI starts from. Yours to
    # edit in $FORGE_DIR/design - an update replaces a file only if you have
    # not changed it; otherwise the new version lands beside it as <file>.new.
    local dsrc="$(dirname "$FORGE_SRC")/design" ddst="$FORGE_DIR/design" f
    if [ -d "$dsrc" ]; then
        mkdir -p "$ddst/.shipped"
        for f in house-style.md game-ui.md tokens.css; do
            [ -f "$dsrc/$f" ] || continue
            if [ ! -f "$ddst/$f" ] || { [ -f "$ddst/.shipped/$f" ] && cmp -s "$ddst/$f" "$ddst/.shipped/$f"; }; then
                install -m 0644 "$dsrc/$f" "$ddst/$f"
            elif ! cmp -s "$ddst/$f" "$dsrc/$f"; then
                install -m 0644 "$dsrc/$f" "$ddst/$f.new"
                warn "kept your edited $ddst/$f; forge's new version is $ddst/$f.new"
            fi
            install -m 0644 "$dsrc/$f" "$ddst/.shipped/$f"
        done
    else
        warn "design/ is not beside $FORGE_SRC - projects with a UI fall back to no house style"
    fi
    ln -sf "$FORGE_DIR/forge.py" "$HOME/.local/bin/forge"

    if [ ! -f "$HOME/.coder-api-key" ]; then
        warn "No ~/.coder-api-key. Copy it from Apollo:"
        warn "  scp apollo:~/.coder-api-key ~/.coder-api-key && chmod 600 ~/.coder-api-key"
    fi

    # Planner budget: capped well below the unified window so Home Assistant
    # voice, which shares this server, always keeps headroom.
    local budget=$(( CTX_SIZE * 45 / 100 ))
    if [ ! -f "$FORGE_DIR/config.toml" ]; then
        cat > "$FORGE_DIR/config.toml" <<EOF
# forge configuration - generated by install-gemini-llm.sh

[planner]                 # Gemini: plans, diagnoses, self-reviews
url = "http://127.0.0.1:${SERVER_PORT}"
model = "qwen3.8-27b"
kind = "qwen38"           # thinking via reasoning_effort
api_key_file = "~/.llama-api-key"
context = ${CTX_SIZE}
prompt_budget = ${budget} # ~45% of the window; the rest stays free for HA
max_tokens = 12000
thinking_max_tokens = 32000  # outline of a large spec; reviews use
                          # review_max_tokens. Raising this does not rescue
                          # a planner that never converges - staging does.
                          # A call that runs out at high effort is asked
                          # again at medium, then low.
# review_max_tokens = 24000  # self-review cap; default 2 x max_tokens. forge
                          # also caps every request to what the context has left
temperature = 0.6

[coder]                   # Vulcan: the principal coder - Strata
machine = "vulcan"        # its GPU pauses for the 3D shape model, when a spec has one
url = "http://${VULCAN_IP}:8081"
model = "strata"          # Strata answers to any model name
kind = "qwen38"           # Qwen3.8 family: thinking via reasoning_effort
reasoning_top_level = true   # Strata reads reasoning_effort from the request itself
no_think_effort = "none"  # "direct" attempts must not think (they did: 28000 tokens each)
api_key_file = "~/.coder-api-key-strata"   # from 'strata launch --show-key' on Vulcan
switch_models = false     # forge's per-language models do not apply to Strata
context = 65536           # must match Strata's --context on Vulcan
max_tokens = 28000
thinking_max_tokens = 32000
temperature = 0.2
thinking_temperature = 0.6
retry_minutes = 15        # Strata restarting (after 3D models): wait, don't fail the run

# Helper coders run the chunk's retry ladder beside the principal coder; a
# machine that also renders codes only when its renders are done.
[[coders]]
name = "apollo"
url = "http://${APOLLO_IP}:8080"
model = "tiel-coder"
kind = "tiel"             # thinking via enable_thinking
api_key_file = "~/.coder-api-key"
image_server = "apollo"   # the [[images.servers]] entry sharing this GPU
context = 65536           # matches c = 65536 in Apollo's coder-presets.ini

[[coders]]
name = "mercury"
enabled = false           # Mercury triages now (below); its coder never won an attempt
url = "http://${MERCURY_IP}:8080"
model = "tiel-coder"
kind = "tiel"
api_key_file = "~/.coder-api-key-mercury"
image_server = "mercury"
context = 65536
thinking = false

[triage]                  # first diagnosis and first test audit of a failing chunk
enabled = true            # off: the planner does both, as before
url = "http://${MERCURY_IP}:8080"   # Mercury's router (loads TielCoder on demand)
model = "tiel-coder"
kind = "tiel"
api_key_file = "~/.coder-api-key-mercury"
machine = "mercury"       # waits for nothing: while Mercury renders, the planner does it
context = 65536
max_tokens = 16000        # audits and diagnoses answer without thinking; more room for the prompt
thinking_max_tokens = 16000
uses = ["diagnose", "audit"]

[[images.servers]]
name = "apollo"
url = "http://${APOLLO_IP}:7860"
speed = 2                 # a starting guess (9070 XT vs Mercury's 9060 XT) until measured
max_hr_scale = 1.5        # Vulkan: a 2x 4K hires pass crashed Mercury's GPU; assume the same

[[images.servers]]
name = "mercury"
url = "http://${MERCURY_IP}:7860"
max_hr_scale = 1.5
speed = 1
lightning = true          # every job with the 4-step Lightning LoRA

[[shapes.servers]]
name = "vulcan"
url = "http://${VULCAN_IP}:7870"
machine = "vulcan"        # stops Strata while it holds the GPU, starts it again after
free_cmd = "systemctl --user stop strata"     # before the first model of a run (ssh vulcan)
restore_cmd = "systemctl --user start strata" # once the shape server lets go of the GPU

[images]
enabled = true
timeout = 900             # default; a spec asset can set its own (4K: 5400)
hr_upscaler = "RealESRGAN_x4plus"   # `forge check` lists each server's upscalers

[shapes]                  # 3D models from image assets with a "shape" (Hunyuan3D 2.1)
enabled = false           # true once install-vulcan-shape.sh has set up Vulcan
timeout = 900
[games]                   # game files the sbox, gmod and fo4 sandboxes read (read-only)
sbox = "~/.steam/debian-installation/steamapps/common/sbox"
fallout4 = "~/.steam/debian-installation/steamapps/common/Fallout 4"
gmod_server = "~/forge/games/gmod-server"   # install-forge-toolchains.sh gmod
fo4_scripts = "~/forge/games/fo4/scripts"   # install-forge-toolchains.sh fo4

[design]                  # projects with a UI (spec line `UI: web|gui|game`, or inferred)
enabled = true
brief = "claude"          # "claude": Claude adapts the house style (~/forge/design) to
                          # each project; "house": the house style unchanged

[ux]                      # report-only UX and accessibility checks after each build
enabled = true            # web: needs forge-sandbox-ux (install-forge-toolchains.sh ux)
image = "forge-sandbox-ux:latest"
max_pages = 12            # pages the browser visits per check
timeout = 600
memory = "2g"

[docs]                    # illustrated end-user guide and in-app help at commit (Claude)
enabled = true
max_images = 24           # screenshots Claude may use
max_turns = 40
timeout = 1500

[sandbox]
image = "forge-sandbox:latest"
timeout = 120
memory = "1g"
cpus = "2"
outputs_max_mb = 200      # most build_outputs may hand back; regular files only
# Game, emulator, mod and RE toolchains (spec line `Toolchain: godot`) run in
# forge-sandbox-<name>:latest from install-forge-toolchains.sh, with larger
# limits of their own. Override per toolchain, e.g.:
# [sandbox.toolchains.rust]
# memory = "6g"
# timeout = 1200

[loop]
# Each chunk climbs a ladder of strategies, one per attempt, changing
# approach instead of repeating it:
#   think        coder with thinking (skipped once a think was cut off)
#   direct       coder writes the code without thinking
#   direct_warm  same, temperature +0.3, to shake it off a fixed wrong answer
#   think_long   coder thinking with up to 2x thinking_max_tokens, as far
#                as Apollo's context allows
#   planner      the planner writes the chunk itself (medium thinking)
#   planner_think  the planner writes it with high thinking
# Every attempt after the first gets the failure output and a diagnosis.
max_attempts = 6          # the whole ladder; a lower number cuts it short
# ladder_thinking = ["think", "direct", "direct_warm", "think_long", "planner", "planner_think"]
# ladder_direct   = ["direct", "direct_warm", "think", "think_long", "planner", "planner_think"]
test_audit = true         # same check failing the same way twice: the planner
                          # rereads the spec and decides whether the TEST is
                          # wrong. A corrected test must keep >=70% of the
                          # asserts, and is listed in the run report.
adaptive_thinking = true  # a thinking step that has run out of tokens twice
                          # this run, and never produced code, is skipped
upstream_rebuilds = 3     # the audit may blame an EARLIER verified chunk; forge
upstream_per_chunk = 2    # rebuilds it (at most this many per run / per chunk)
                          # and re-checks everything that depends on it
parallel_attempts = true  # with [[coders]]: run ladder steps side by side,
                          # on a longer ladder with more varied direct steps
# parallel_extra_attempts = 4   # added to max_attempts when helpers exist
                                # (default: 2 per helper coder)
contract = true           # the planner writes an interface contract - which
                          # chunk owns which name, signatures, the database
                          # schema, and for web apps every URL, page element
                          # scripts use and JSON shape - shown to every chunk
wiring_check = true       # web apps: after regression, a wiring test follows
                          # every link and checks routes and page elements
                          # against the scripts; problems get fixed first
all_failures = true       # a failed test stops at its first bad assert; forge
                          # reruns it statement by statement so the retry sees
                          # EVERY failing check, not one bug per attempt
max_review_rounds = 2
image_rerolls = 2         # Claude rejects a generated image: re-render
                          # with a new seed, at most this many times
unresolved_self_review = "claude"  # local review still objecting after the
                          # last round: "claude" lets Claude rule on it,
                          # "fail" stops the run
staged_plan = "auto"      # large specs (>5000 tokens): outline first,
                          # then one test per chunk. true/false to force.
auto_rebuilds = 1         # patching a chunk failed: rewrite its task
                          # and regenerate it, at most this many times
pause_after_plan = false  # true = stop after planning so you can read plan.json
regression_repairs = 2    # a chunk test fails on the assembled module: find
                          # the chunk that broke it and fix that, this many rounds
auto_change = 1           # Claude's final review still blocks: forge writes a
                          # change request, Claude reviews/revises it, and a
                          # change run starts. Levels deep; 0 = off
pause_before_auto_change = false  # true = write auto-change.md and stop

[claude]
enabled = true
require = true            # refuse to commit if Claude could not review (a real error)
max_turns = 12
timeout = 900
retries = 3               # Claude unreachable, overloaded or timed out: try again ...
retry_waits = [30, 120, 300]   # ... after these many seconds
when_unavailable = "defer"     # still unavailable after the retries:
                          # "defer" - commit after the local review, hold the push, and
                          #           `forge pending` (timer) has Claude review it later
                          # "local" - accept the local review; "fail" - stop the run
review_change = true      # Claude checks and revises forge's own change requests
change_review_rounds = 2

[git]
repo = "~/forge-projects"
remote = ""               # set once your git host exists, e.g. "origin"
auto_push = false         # HUMAN GATE. metis/forgejo-link.sh turns it on for Forgejo.
sync_main = true          # fast-forward the local main from the remote before each commit

[network]                   # the internet: retries, and how forge notices an outage
retries = 3                 # transient failures (timeouts, 5xx, 429) of GitHub, F-Droid,
retry_waits = [10, 30, 90]  # web search, Hugging Face and crates are retried
probe_urls = ["https://api.anthropic.com/", "https://api.github.com/"]
recheck = 60                # seconds a reachability check is trusted
# offline = true            # work offline on purpose (no internet requests at all)

[kiwix]                     # the offline library on Ziggurat (kiwix-serve)
url = "http://${KIWIX_IP:-192.168.50.143}:4242"
reference = true            # reference pages for the APIs a spec depends on, before planning
on_failure = true           # the page for an error a chunk keeps hitting, for the next attempt
failure_after = 2           # ... once the same error has come back this many times
max_lookups = 6
# books = []                # extra books (ZIM names or prefixes) to search for every project

[cache]                     # repositories kept on Gemini's SSD (forge cache list)
enabled = true
dir = "~/forge/cache"       # GitHub tarballs, file trees, READMEs; Rust crates in cargo/
max_gb = 20                 # least used repositories go first beyond this
min_uses = 2                # used this often in `days` = fetched ahead nightly
days = 90
repos = []                  # always kept current, e.g. ["godotengine/godot-demo-projects"]

[models]                    # coding model per language on each coder machine
enabled = true              # false = TielCoder everywhere, as before
prefetch = ["python", "web"]  # after the weekly refresh, fetch each machine's best for these
max_gb = 120                # disk for fetched models per machine; least used go first
keep_frequent = 3           # each machine keeps (and updates ahead) its 3 most used models
frequent_days = 60
# licenses = [...]          # default: open-weight licences that allow personal use
# Machines (defaults shown). ram_gb = system RAM the coder may use for MoE experts.
# [models.machines.apollo]
# ssh = "apollo"
# vram_gb = 16
# ram_gb = 20
# [models.machines.vulcan]
# ram_gb = 40
# [models.machines.mercury]
# ram_gb = 14               # sd-server keeps ~11 GB resident

[prior_art]                 # GitHub search before planning a new project
enabled = true              # a spec line `Prior art: none` skips it for that spec
max_repos = 3               # repositories the planner may build on
max_mb = 50                 # largest repository considered, and most vendored
# token_file = "~/.github-token"   # else GITHUB_TOKEN, else `gh auth token`
# licenses = ["MIT", "BSD-3-Clause", "Apache-2.0", ...]  # default: permissive only;
#                             GPL only for a spec that says `License: GPL-3.0`

[playtest]                  # forge playtest <project>
host = "vulcan"             # ssh host that gets the build (key login)
dir = "~/playtest"

[paths]
runs = "~/forge/runs"
inbox = "~/forge/inbox"
EOF
        ok "Wrote $FORGE_DIR/config.toml"
    else
        info "Keeping existing $FORGE_DIR/config.toml"
    fi

    # Drop a SPEC.md into the inbox and it runs. PathExistsGlob fires while
    # any *.md is present; forge always moves each spec to done/ or failed/,
    # so a finished spec never re-triggers.
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/forge-inbox.path" <<EOF
[Unit]
Description=Watch the forge inbox for new specs

[Path]
PathExistsGlob=%h/forge/inbox/*.md
MakeDirectory=yes

[Install]
WantedBy=default.target
EOF
    cat > "$HOME/.config/systemd/user/forge-inbox.service" <<EOF
[Unit]
Description=forge - process specs in the inbox
After=network-online.target llama-server.service

[Service]
Type=oneshot
EnvironmentFile=-%h/forge/env
Environment=PATH=%h/.npm-global/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/forge/forge.py inbox
EOF
    # Work that waited for the internet (Claude's review, user guides, the
    # model search): every 30 minutes. It does nothing when nothing waits or
    # the internet is still down, and stays out of the way of a running build.
    cat > "$HOME/.config/systemd/user/forge-pending.service" <<UNIT
[Unit]
Description=forge - finish work that waited for the internet
After=network-online.target llama-server.service

[Service]
Type=oneshot
EnvironmentFile=-%h/forge/env
Environment=PATH=%h/.npm-global/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/forge/forge.py pending run
TimeoutStartSec=6h
UNIT
    cat > "$HOME/.config/systemd/user/forge-pending.timer" <<UNIT
[Unit]
Description=forge - check for work waiting on the internet

[Timer]
OnBootSec=10min
OnUnitInactiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now forge-pending.timer >/dev/null 2>&1 || \
        warn "could not enable forge-pending.timer: systemctl --user enable --now forge-pending.timer"
    ok "Units written. Enable once 'forge check' passes:"
    info "  systemctl --user enable --now forge-inbox.path"

    say "forge check"
    "$FORGE_DIR/forge.py" --config "$FORGE_DIR/config.toml" check || \
        warn "Some checks failed - fix them before enabling the inbox."
}

# ============================================================================
# PHASE: firewall
# ============================================================================

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || { info "Firewall configuration disabled."; return 0; }

    say "[firewall] Restricting service ports to $LAN_SUBNET"

    have ufw || sudo apt install -y ufw

    local p
    for p in "$SERVER_PORT" "$OPEN_WEBUI_PORT" "$SEARXNG_PORT" \
             "$WHISPER_PORT" "$PIPER_PORT" "$OWW_PORT"; do
        sudo ufw allow from "$LAN_SUBNET" to any port "$p" proto tcp \
            comment "llm-stack" >/dev/null
    done
    ok "Rules added for ports: $SERVER_PORT $OPEN_WEBUI_PORT $SEARXNG_PORT $WHISPER_PORT $PIPER_PORT $OWW_PORT"

    # Containers reach the host over the Docker bridge (172.16.0.0/12), NOT
    # from the LAN subnet, so the rules above do not cover them. Without this,
    # Open WebUI sits there showing an empty model list while curl from the
    # host works perfectly - the request never reaches llama-server at all.
    say "Allowing Docker containers to reach llama-server on the host"
    sudo ufw allow from 172.16.0.0/12 to any port "$SERVER_PORT" proto tcp \
        comment "docker->llama-server" >/dev/null
    ok "Docker bridge allowed to port $SERVER_PORT"

    if sudo ufw status | grep -q "Status: active"; then
        ok "ufw is active."
    else
        warn "ufw rules are staged but ufw is INACTIVE. Enable with:"
        warn "    sudo ufw enable"
        warn "Do this from a local session, not SSH, in case you lock yourself out."
    fi

    warn "Docker publishes ports by manipulating iptables directly and can"
    warn "bypass ufw rules. If strict isolation matters, bind the compose"
    warn "services to 127.0.0.1 and put a reverse proxy in front."
}

# ============================================================================
# MAIN
# ============================================================================

ALL_PHASES=(base rocm verify build model server services agents sandbox claude forge firewall)

main() {
    mkdir -p "$STACK_DIR"

    local -a phases
    if [ $# -gt 0 ]; then
        phases=("$@")
    else
        phases=("${ALL_PHASES[@]}")
    fi

    local p
    for p in "${phases[@]}"; do
        case "$p" in
            base|rocm|verify|build|model|server|services|agents|sandbox|claude|forge|firewall)
                "phase_$p"
                echo
                ;;
            *)
                die "Unknown phase '$p'. Valid: ${ALL_PHASES[*]}"
                ;;
        esac
    done

    say "Done."
    cat <<EOF
    API endpoint (LAN):    http://${LAN_IP}:${SERVER_PORT}/v1
    API endpoint (local):  http://127.0.0.1:${SERVER_PORT}/v1
    API key:               cat $API_KEY_FILE
    Open WebUI:            http://${LAN_IP}:${OPEN_WEBUI_PORT}
    Watch GPUs:            watch -n 0.5 rocm-smi
    Benchmark the split:   $LLAMA_DIR/build/bin/llama-bench -m \$(cat $STACK_DIR/.model_path) -ts 1,1 -ts 3,2

    Home Assistant:
      Settings > Devices & Services > Add Integration > llama.cpp
        URL:     http://${LAN_IP}:${SERVER_PORT}/v1
        API key: (above)
      Then Wyoming Protocol x3 for ports ${WHISPER_PORT} / ${PIPER_PORT} / ${OWW_PORT}.

EOF
}

main "$@"
