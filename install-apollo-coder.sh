#!/usr/bin/env bash
#
# install-apollo-coder.sh
#
# APOLLO - the coding worker.
#   MSI PRO B550M-VC WIFI / Ryzen 7 5800XT / 32GB DDR4 (matched pair)
#   Radeon RX 9070 XT 16GB (Navi 48, gfx1201) - PCI_E1, Gen4 x16
#   ONE GPU. No integrated graphics on the 5800XT.
#   Kubuntu 26.04 LTS
#
# Runs ONE thing: llama-server with TielCoder, as a pure inference worker for
# Gemini's forge pipeline. No agent logic lives here - verification happens in
# exactly one place, on Gemini. Pi is installed for interactive pairing.
#
# MODEL CHOICE
# ------------
# TielCoder UD-IQ3_XXS (14.1GB) - the quant its authors call "the 16GB pick",
# and the one you already validated on the 4070 Ti Super. That card's ~672
# GB/s is within ~5% of the 9070 XT's, so your measured quality AND speed
# should carry over almost directly.
#
# TielCoder rather than CyberTiel: same authors, base, sizes and MTP head, but
# not abliterated. An unattended pipeline that auto-executes generated code is
# exactly where refusal behaviour earns its keep.
#
# WHAT THIS DOES NOT DISABLE
# --------------------------
# --ctx-checkpoints and --cache-ram are left at their DEFAULTS here. Apollo's
# old crashes were KV-state serialisation across a tensor split spanning two
# different GPU architectures. A single card cannot hit that path - and prompt
# caching matters here, because forge resends the same spec prefix on every
# retry.
#
# 5800XT NOTE: no integrated graphics. The board's video outputs are dead; the
# 9070 XT drives the console. Run headless (see the base phase) so no desktop
# session holds VRAM.
#
# Usage:  ./install-apollo-coder.sh [phase ...]
# Phases: base rocm verify build model server agents firewall

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
MODEL_DIR="${MODEL_DIR:-$HOME/models}"
STACK_DIR="${STACK_DIR:-$HOME/llm-stack}"

HF_REPO="peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP"
# Which quant, and how many layers' MoE experts to keep in system RAM.
# The two are a pair: anything larger than the 16GB card cannot load with
# every layer on the GPU, and llama-server fails at startup.
#
#   UD-IQ3_XXS  14.1GB  N_CPU_MOE=0    all on GPU; the quant you validated
#   UD-IQ4_XS   18.6GB  N_CPU_MOE=12   4-bit quality; recommended
#   UD-Q4_K_XL  23.3GB  N_CPU_MOE=24   the model's benchmarked tier; slower
#
# Each layer's experts are roughly 0.4GB at IQ4_XS. The N_CPU_MOE values
# above are deliberately safe starting points: once it loads, LOWER it two
# at a time, restarting each time, until about 1GB of VRAM stays free under
# load. Every layer kept on the GPU makes generation faster.
#
# Gemini needs no change for a quant swap: forge asks for the model by its
# alias ("tiel-coder"), not its filename. Only a change to CTX_SIZE has to be
# mirrored in forge's [coder] context.
HF_QUANT="UD-IQ4_XS"
N_CPU_MOE=12
DOWNLOAD_MMPROJ=false       # TielCoder can see, but coding does not need it

# GPU architecture target - one card.
GFX_TARGETS="gfx1201"

# Server tuning. Start conservative; raise CTX_SIZE once you have seen
# actual VRAM use in rocm-smi.
CTX_SIZE=32768              # 14.1GB model on 16GB; measure before raising
PARALLEL_SLOTS=1            # forge sends one chunk at a time; give it all
SPEC_DRAFT_N_MAX=3          # MoE models favour 3; dense favour 2
LLAMA_DEVICES="ROCm0"       # single GPU; the 5800XT has no iGPU

SERVER_PORT=8080


LAN_IP="192.168.50.XXX"      # <-- set. Gemini takes .96
LAN_SUBNET="192.168.50.0/24"

API_KEY_FILE="$HOME/.coder-api-key"  # copy to Gemini for forge
RUN_SCRIPT="$HOME/run_llm.sh"

INSTALL_PI=true             # interactive coding agent the model was benchmarked with
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

    # No iGPU on the 5800XT, so any desktop session renders on the 9070 XT and
    # holds VRAM the model needs. Boot to a text console instead; start a
    # desktop by hand with 'sudo systemctl start display-manager' if wanted.
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
    info "gfx1201 -> RX 9070 XT"
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
    say "[verify] Confirming the RX 9070 XT is visible to ROCm"

    info "PCIe-level view:"
    lspci | grep -iE "vga|display|3d" || true
    echo

    # PRIMARY check: the kernel's own GPU topology. World-readable, needs no
    # ROCm userspace and no group membership. gfx_target_version encodes the
    # target as major*10000 + minor*100 + stepping, so gfx1201 = 120001.
    local n_topo=0 f
    for f in /sys/class/kfd/kfd/topology/nodes/*/properties; do
        [ -r "$f" ] || continue
        grep -q '^gfx_target_version 120001$' "$f" && n_topo=$((n_topo + 1))
    done

    # rocminfo is CAPTURED, then searched. Piping it straight into `grep -q`
    # was a bug: grep exits on the first match, rocminfo is killed by SIGPIPE
    # while still writing, and under `set -o pipefail` the pipeline reports
    # failure - so the old check warned "no gfx1201 agent" precisely BECAUSE
    # it had found the card.
    local ri n_ri=0
    if have rocminfo; then
        ri=$(rocminfo 2>/dev/null || true)
        n_ri=$(grep -c 'Name:[[:space:]]*gfx1201' <<< "$ri" || true)
        info "ROCm agents:"
        grep -E "Marketing Name|Name:[[:space:]]*gfx" <<< "$ri" | sed 's/^/      /' || true
        echo
    else
        warn "rocminfo not on PATH - relying on the kernel topology alone."
    fi

    if [ "$n_topo" -eq 1 ] || [ "$n_ri" -ge 1 ]; then
        ok "RX 9070 XT (gfx1201) detected - kernel topology: $n_topo, rocminfo: $n_ri"
    elif [ "$n_topo" -gt 1 ]; then
        warn "More than one gfx1201 device found. Apollo should have exactly one."
    else
        warn "No gfx1201 device found by the kernel or by rocminfo."
        warn "Check the card is seated in PCI_E1 and that the amdgpu driver loaded:"
        warn "  lsmod | grep amdgpu ; ls /sys/class/kfd/kfd/topology/nodes/"
        confirm "Continue anyway?"
    fi
    if [ "$n_ri" -eq 0 ] && [ "$n_topo" -ge 1 ]; then
        warn "The kernel sees the card but rocminfo does not. That is almost always"
        warn "group membership: log out and back in, or reboot, after the rocm phase."
    fi

    if id -nG | grep -qw render; then
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

    say "Compiling (expect 10-20 minutes on a 5800XT)"
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
    say "Device enumeration"
    "$LLAMA_DIR/build/bin/llama-server" --list-devices || true
    echo
    info "Single GPU - expect exactly one ROCm device above."
    confirm
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
# PHASE: model
# ============================================================================

phase_model() {
    say "[model] Downloading $HF_REPO : $HF_QUANT"

    mkdir -p "$MODEL_DIR"
    local hf; hf=$(find_hf)
    info "Using: $hf"


    local target="$MODEL_DIR/${HF_REPO##*/}"
    hf_get "$hf" "$HF_REPO" --local-dir "$target" --include "*${HF_QUANT}*" \
        || die "Download failed after 3 attempts - check the network, then re-run this phase."
    if [ "$DOWNLOAD_MMPROJ" = true ]; then
        say "Downloading vision projector"
        hf_get "$hf" "$HF_REPO" --local-dir "$target" --include "*mmproj*" \
        || die "Download failed after 3 attempts - check the network, then re-run this phase."
    fi

    say "Verifying downloads against published SHA-256 hashes"
    hf_verify "$HF_REPO" "$target" || die "Checksum mismatch - see above.
    Delete the BAD file(s) and re-run: ./install-apollo-coder.sh model"

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
        --alias "tiel-coder"
        -c "$CTX_SIZE"
        --parallel "$PARALLEL_SLOTS"
        --host 0.0.0.0 --port "$SERVER_PORT"
        --api-key-file "$API_KEY_FILE"
        -ngl 999
        -fa on
        --cache-type-k q8_0
        --cache-type-v q8_0
        -b 2048 -ub 512
        # TielCoder carries its "Sharp" chat template inside the GGUF.
        --jinja
        # Thinking is NOT set server-side. forge requests it per chunk with
        # enable_thinking true/false - on for numerics and sign-convention
        # work, off for data models and plumbing, as in your sample prompts.
        --spec-type draft-mtp
        --spec-draft-n-max "$SPEC_DRAFT_N_MAX"
        # Your validated sampling: temperature 0.2 for code.
        --temp 0.2
        --metrics
    )
    [ -n "$mmproj" ] && args+=( --mmproj "$mmproj" )
    [ -n "$LLAMA_DEVICES" ] && args+=( --device "$LLAMA_DEVICES" )
    # Experts of the first N MoE layers live in system RAM; everything else
    # (attention, shared experts, the MTP head, the KV cache) stays on the GPU.
    if [ "${N_CPU_MOE:-0}" -gt 0 ]; then
        args+=( --n-cpu-moe "$N_CPU_MOE" )
    fi

    # Catch the pairing mistake before llama-server does, with a clearer
    # message than its out-of-memory failure.
    local gib vram_gib
    gib=$(( $(stat -c %s "$model") / 1073741824 ))
    vram_gib=$(( $(cat /sys/class/drm/card*/device/mem_info_vram_total 2>/dev/null | sort -n | tail -n1 || echo 0) / 1073741824 ))
    if [ "${N_CPU_MOE:-0}" -eq 0 ] && [ "$vram_gib" -gt 0 ] && [ "$gib" -ge "$vram_gib" ]; then
        warn "$(basename "$model") is ${gib}GiB but the card has ${vram_gib}GiB, and"
        warn "N_CPU_MOE=0 keeps every layer on the GPU - it will not load."
        warn "Set N_CPU_MOE (see the table in the config section) and re-run."
    fi

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
Description=Apollo coder - TielCoder $HF_QUANT on RX 9070 XT
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
# PHASE: agents
# ============================================================================

phase_agents() {
    say "[agents] Pi - interactive coding agent"
    [ "$INSTALL_PI" = true ] || { info "Pi disabled."; return 0; }

    # User-level npm prefix: no sudo for global installs, nothing written into
    # /usr that an OS upgrade could clobber.
    have npm || sudo apt install -y nodejs npm
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    grep -q 'npm-global/bin' "$HOME/.profile" 2>/dev/null || \
        echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME/.profile"
    export PATH="$HOME/.npm-global/bin:$PATH"

    npm install -g @mariozechner/pi-coding-agent
    mkdir -p "$HOME/.pi/agent"
    local key; key=$(cat "$API_KEY_FILE" 2>/dev/null || echo none)
    cat > "$HOME/.pi/agent/models.json" <<EOF
{
  "providers": {
    "llama-cpp": {
      "baseUrl": "http://127.0.0.1:${SERVER_PORT}/v1",
      "api": "openai-completions",
      "apiKey": "${key}",
      "models": [ { "id": "tiel-coder" } ]
    }
  }
}
EOF
    chmod 600 "$HOME/.pi/agent/models.json"
    ok "Pi configured against the local coder. Run 'pi' in a project directory."
    info "Pi is for YOU. forge on Gemini calls llama-server directly and does"
    info "not go through Pi."
}

# ============================================================================
# PHASE: firewall
# ============================================================================

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || { info "Firewall configuration disabled."; return 0; }
    say "[firewall] coder API and SSH, LAN only"
    have ufw || sudo apt install -y ufw
    sudo ufw allow from "$LAN_SUBNET" to any port "$SERVER_PORT" proto tcp comment "tiel-coder" >/dev/null
    sudo ufw allow from "$LAN_SUBNET" to any port 22 proto tcp comment "ssh" >/dev/null
    ok "Allowed $SERVER_PORT and 22 from $LAN_SUBNET"
    info "The coder is also protected by its API key. To restrict it to Gemini"
    info "alone, replace the subnet rule with Gemini's address."
    sudo ufw status | grep -q "Status: active" || \
        warn "ufw inactive. Enable from a LOCAL session: sudo ufw enable"
}

# ============================================================================
# MAIN
# ============================================================================

ALL_PHASES=(base rocm verify build model server agents firewall)

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
            base|rocm|verify|build|model|server|agents|firewall)
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
    Coder endpoint:  http://${LAN_IP}:${SERVER_PORT}/v1   (model: tiel-coder)
    API key:         $API_KEY_FILE  <- copy to Gemini as ~/.coder-api-key
    Start:           systemctl --user enable --now llama-server
    Interactive:     pi   (in any project directory)

    Thinking is requested per call with chat_template_kwargs
    {"enable_thinking": true|false}. forge does this per chunk.
EOF
}

main "$@"
