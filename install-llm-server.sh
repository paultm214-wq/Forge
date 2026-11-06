#!/usr/bin/env bash
#
# install-llm-server.sh
#
# Builds an always-on local LLM server on:
#   MSI PRO B550M-VC WIFI / Ryzen 7 5700G / 64GB DDR4-3600
#   Radeon RX 9070 XT 16GB  (Navi 48, gfx1201)  - PCI_E1, Gen3 x16
#   Radeon RX 9060 XT 16GB  (Navi 44, gfx1200)  - Oculink in M2_1, Gen3 x4
#   Ubuntu / Kubuntu 26.04 LTS  (NOT 25.10 - that release is EOL and
#                                unsupported by ROCm 10)
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
# Phases: base rocm verify build model server services agents firewall
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

# GPU architecture targets. BOTH are required - the cards differ.
GFX_TARGETS="gfx1200;gfx1201"

# Server tuning. Start conservative; raise CTX_SIZE once you have seen
# actual VRAM use in rocm-smi.
CTX_SIZE=32768
PARALLEL_SLOTS=2            # CTX_SIZE is divided across slots
TENSOR_SPLIT="3,2"          # Weighted toward the 9070 XT (higher bandwidth).
                            # VERIFY ORDER FIRST with: llama-server --list-devices
                            # If the 9060 XT enumerates as device 0, use "2,3".
REASONING_EFFORT="low"      # xhigh | medium | low | none
SPEC_DRAFT_N_MAX=2          # MTP draft depth. 2 is the dense-model
                            # recommendation; MoE favours 3. Sweep 2-4 and
                            # watch the acceptance rate - too deep and the
                            # main model rejects drafts faster than they
                            # save work.
                            # Default is xhigh - far too slow for voice control.
LLAMA_DEVICES="ROCm0,ROCm1" # Pin to the two discrete cards. The 5700G's
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

LAN_IP="192.168.50.96"
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
    say "[rocm] Installing ROCm 10.0.0 for gfx1200 + gfx1201"

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
    info "gfx1201 -> RX 9070 XT"
    info "gfx1200 -> RX 9060 XT"
    info "The -dev packages are required to BUILD llama.cpp, not just run it."
    sudo apt install -y \
        amdrocm-core-dev10.0-gfx1201 \
        amdrocm-core-dev10.0-gfx1200

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

    local found1200 found1201
    found1201=$(rocminfo 2>/dev/null | grep -c "gfx1201" || true)
    found1200=$(rocminfo 2>/dev/null | grep -c "gfx1200" || true)

    if [ "$found1201" -gt 0 ] && [ "$found1200" -gt 0 ]; then
        ok "Both gfx1201 (9070 XT) and gfx1200 (9060 XT) detected."
    else
        warn "Expected BOTH gfx1201 and gfx1200."
        warn "  gfx1201 matches: $found1201   gfx1200 matches: $found1200"
        warn "If the 9060 XT is missing, check: Oculink seating, Above 4G"
        warn "Decoding in BIOS, and that M2_1 is set to PCIe (not SATA) mode."
        confirm "Continue anyway?"
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
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_CURL=ON

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
    info "TENSOR_SPLIT is currently '$TENSOR_SPLIT', weighting device 0 more"
    info "heavily. Device 0 should be the RX 9070 XT (gfx1201, ~640 GB/s)."
    info "If the 9060 XT is device 0 instead, edit TENSOR_SPLIT to '2,3'."
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
        # spanning two different GPU architectures. If a third entry point
        # turns up, stop patching and run single-GPU - see the runbook.
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
Description=llama.cpp server - Qwen3.8-27B $HF_QUANT on gfx1201 + gfx1200
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

ALL_PHASES=(base rocm verify build model server services agents firewall)

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
            base|rocm|verify|build|model|server|services|agents|firewall)
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
