#!/usr/bin/env bash
#
# install-coder-helper.sh
#
# A SECOND JOB for the image servers: a TielCoder coding worker that forge
# uses alongside Apollo, loaded only while it is needed.
#
#   VULCAN   RTX 4070 Ti Super 16GB, 64GB RAM   -> llama.cpp with CUDA
#   MERCURY  RX 9060 XT 16GB, 32GB RAM          -> llama.cpp with Vulkan
#
# Run it on each machine AFTER its image installer (install-vulcan-imagegen.sh
# or install-mercury-imagegen.sh): it reuses the CUDA toolkit or Vulkan SDK
# those installed. It picks CUDA if nvidia-smi works, Vulkan otherwise.
#
# HOW IT SHARES THE GPU WITH sd-server
# ------------------------------------
# sd-server keeps its weights in system RAM (--offload-to-cpu) and takes VRAM
# only while rendering. This llama-server runs in ROUTER mode: it starts with
# NO model loaded, loads TielCoder on the first request, and unloads it again
# after SLEEP_IDLE_SECONDS without work. forge also unloads it explicitly
# (POST /models/unload) before it sends this machine an image, and gives each
# machine ONE role per run - images or code - so the two never hold the GPU
# at once. Do not render by hand while forge is coding on this machine.
#
# Usage:  ./install-coder-helper.sh [phase ...]
# Phases: check build model server firewall

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

HOST_NAME="${HOST_NAME:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
MODEL_DIR="${MODEL_DIR:-$HOME/models}"
STACK_DIR="${STACK_DIR:-$HOME/llm-stack}"

# Same model as Apollo, so forge can send any chunk to any coder. forge asks
# for it by the name "tiel-coder" - the preset section below.
HF_REPO="peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP"
HF_QUANT="UD-IQ4_XS"        # 18.6GB; needs N_CPU_MOE on a 16GB card
# Experts of the first N MoE layers stay in system RAM. Apollo runs this
# quant on its 16GB card at 8; start a little safer and lower it two at a
# time while ~1GB of VRAM stays free under load.
#   Mercury: sd-server keeps ~11GB of RAM even when idle, leaving ~21GB.
#   Each layer's experts are ~0.4GB, so 12 layers is ~5GB of RAM.
N_CPU_MOE="${N_CPU_MOE:-12}"

CTX_SIZE=65536              # must match this coder's `context` in forge's config;
                            # forge's prompt budget is context - thinking_max_tokens
                            # (32000) - 1024, so 32768 would leave no room
SPEC_DRAFT_N_MAX=3          # MTP drafting, as on Apollo
SLEEP_IDLE_SECONDS=600      # unload after 10 idle minutes: VRAM back for images

SERVER_PORT=8080            # sd-server keeps 7860
LAN_SUBNET="192.168.50.0/24"
API_KEY_FILE="$HOME/.coder-api-key"   # copy to Gemini as ~/.coder-api-key-$HOST_NAME
RUN_SCRIPT="$HOME/run_coder.sh"
PRESET_FILE="$STACK_DIR/coder-presets.ini"
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

backend() {
    if have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then echo cuda; else echo vulkan; fi
}

# The discrete GPU's Vulkan index. llvmpipe (CPU software rendering) is also
# a Vulkan device and must never be chosen - same rule as Mercury's sd-server.
discrete_vk_index() {
    local out
    out=$(vulkaninfo --summary 2>/dev/null || true)
    awk '
        /^GPU[0-9]+:/ { idx = substr($1, 4, length($1) - 4) }
        /deviceType/ && /DISCRETE/ && found == "" { found = idx }
        END { if (found != "") print found }' <<< "$out"
}

# ============================================================================
# PHASE: check
# ============================================================================

phase_check() {
    say "[check] $HOST_NAME - $(backend) backend"
    [ "$(id -u)" -ne 0 ] || die "Run as your normal user, not with sudo."
    case "$(backend)" in
        cuda)
            nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/    /'
            have nvcc || die "nvcc not found - run install-vulcan-imagegen.sh's nvidia phase first."
            ok "CUDA $(nvcc --version | sed -n 's/.*release \([0-9.]*\).*/\1/p')"
            ;;
        vulkan)
            have vulkaninfo || die "vulkaninfo missing - run install-mercury-imagegen.sh first."
            have glslc || die "glslc missing (needed to build llama.cpp's Vulkan shaders):
    sudo apt install -y glslc libvulkan-dev"
            local idx; idx=$(discrete_vk_index)
            [ -n "$idx" ] || die "No discrete Vulkan GPU found."
            ok "Discrete GPU is Vulkan device $idx"
            ;;
    esac
    local ram_gb; ram_gb=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
    info "System RAM: ${ram_gb}GB"
    if [ "$ram_gb" -lt 40 ]; then
        warn "With sd-server holding ~11GB of RAM even when idle, ${ram_gb}GB is tight."
        warn "N_CPU_MOE=$N_CPU_MOE puts ~$((N_CPU_MOE * 4 / 10))GB of experts in RAM - keep it at or below 14."
    fi
    if systemctl --user is-active --quiet sd-server 2>/dev/null; then
        ok "sd-server is running - the two will share the GPU by taking turns."
    fi
    sudo apt install -y build-essential cmake ninja-build pkg-config ccache git \
        libssl-dev python3-venv jq curl >/dev/null
    ok "Build tools present."
}

# ============================================================================
# PHASE: build
# ============================================================================

phase_build() {
    local be; be=$(backend)
    say "[build] llama.cpp with $be"
    if [ ! -d "$LLAMA_DIR/.git" ]; then
        git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    else
        git -C "$LLAMA_DIR" pull --ff-only
    fi
    local -a flags=(-G Ninja -DCMAKE_BUILD_TYPE=Release)
    if [ "$be" = cuda ]; then
        # sm_89 = Ada (RTX 40-series). Building for one architecture is far
        # quicker than CUDA's default list.
        flags+=(-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89)
    else
        flags+=(-DGGML_VULKAN=ON)
    fi
    cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" "${flags[@]}"
    say "Compiling (10-25 minutes)"
    cmake --build "$LLAMA_DIR/build" --config Release -j"$(nproc)" \
        --target llama-server llama-bench
    ok "Built $LLAMA_DIR/build/bin/llama-server"
    if [ "$be" = vulkan ]; then
        GGML_VK_VISIBLE_DEVICES="$(discrete_vk_index)" \
            "$LLAMA_DIR/build/bin/llama-server" --list-devices || true
    else
        "$LLAMA_DIR/build/bin/llama-server" --list-devices || true
    fi
    info "Expect exactly one device above - the discrete GPU."
}

# ============================================================================
# PHASE: model
# ============================================================================

phase_model() {
    say "[model] $HF_REPO : $HF_QUANT"
    mkdir -p "$MODEL_DIR" "$STACK_DIR"
    local target="$MODEL_DIR/${HF_REPO##*/}"
    if [ "${SKIP_DOWNLOAD:-0}" = "1" ]; then
        info "SKIP_DOWNLOAD=1 - expecting *${HF_QUANT}*.gguf under $target"
        info "(e.g. copied from Apollo: rsync -a apollo:models/${HF_REPO##*/}/ $target/)"
    else
        local hf; hf=$(find_hf)
        hf_get "$hf" "$HF_REPO" --local-dir "$target" --include "*${HF_QUANT}*" \
            || die "Download failed after 3 attempts - retry, or copy it from Apollo and use SKIP_DOWNLOAD=1."
    fi
    say "Verifying against published SHA-256 hashes"
    hf_verify "$HF_REPO" "$target" || die "Checksum mismatch - delete the BAD file and re-run this phase."
    local model
    model=$(find "$target" -path '*/.cache' -prune -o -type f -iname "*${HF_QUANT}*.gguf" \
            ! -iname "*mmproj*" -print | sort | head -n1)
    [ -n "$model" ] || die "No .gguf matching '$HF_QUANT' under $target"
    printf '%s\n' "$model" > "$STACK_DIR/.coder_model_path"
    ok "Model: $model"
}

# ============================================================================
# PHASE: server
# ============================================================================

phase_server() {
    say "[server] router-mode llama-server, loads TielCoder on demand"
    local model; model=$(cat "$STACK_DIR/.coder_model_path" 2>/dev/null || true)
    [ -n "$model" ] || die "No model recorded. Run the 'model' phase first."
    local be; be=$(backend)
    local dev; [ "$be" = cuda ] && dev="CUDA0" || dev="Vulkan0"

    if [ ! -f "$API_KEY_FILE" ]; then
        openssl rand -hex 24 > "$API_KEY_FILE"
        chmod 600 "$API_KEY_FILE"
        ok "Generated API key at $API_KEY_FILE"
    fi

    # Models forge fetched (coder-models.py) are extra sections: keep them.
    local extra=""
    if [ -f "$PRESET_FILE" ]; then
        extra=$(awk '/^\[/{keep = ($0 != "[tiel-coder]")} keep' "$PRESET_FILE")
    fi
    # One preset = Apollo's tuned arguments. The section name is the model
    # name requests use; the router overrides host, port and API key.
    cat > "$PRESET_FILE" <<EOF
version = 1

[tiel-coder]
model = $model
c = $CTX_SIZE
parallel = 1
n-gpu-layers = 999
n-cpu-moe = $N_CPU_MOE
device = $dev
flash-attn = on
cache-type-k = q8_0
cache-type-v = q8_0
batch-size = 2048
ubatch-size = 512
jinja = true
spec-type = draft-mtp
spec-draft-n-max = $SPEC_DRAFT_N_MAX
temp = 0.2
EOF
    [ -n "$extra" ] && printf '\n%s\n' "$extra" >> "$PRESET_FILE"
    ok "Wrote $PRESET_FILE"

    {
        echo '#!/usr/bin/env bash'
        echo "# Auto-generated by install-coder-helper.sh on $HOST_NAME. Edit freely."
        echo 'set -euo pipefail'
        if [ "$be" = vulkan ]; then
            echo '# Pin to the discrete GPU; llvmpipe is also a Vulkan device.'
            echo "export GGML_VK_VISIBLE_DEVICES=$(discrete_vk_index)"
        fi
        echo 'ARGS=('
        echo "  --models-preset $(printf '%q' "$PRESET_FILE")"
        echo '  # One model at a time; this GPU also renders images.'
        echo '  --models-max 1'
        echo '  # Free the VRAM after this long without a request.'
        echo "  --sleep-idle-seconds $SLEEP_IDLE_SECONDS"
        echo "  --host 0.0.0.0 --port $SERVER_PORT"
        echo "  --api-key-file $(printf '%q' "$API_KEY_FILE")"
        echo '  --metrics'
        echo ')'
        printf 'exec %q "${ARGS[@]}" "$@"\n' "$LLAMA_DIR/build/bin/llama-server"
    } > "$RUN_SCRIPT"
    chmod +x "$RUN_SCRIPT"
    bash -n "$RUN_SCRIPT" || die "Generated $RUN_SCRIPT failed syntax check."
    ok "Wrote $RUN_SCRIPT"

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/coder.service" <<EOF
[Unit]
Description=$HOST_NAME coder - TielCoder $HF_QUANT on demand (llama-server router)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$RUN_SCRIPT
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; the service will stop at logout."

    cat <<EOF

    Start:   systemctl --user enable --now coder
    Logs:    journalctl --user -u coder -f
    Status:  curl -s -H "Authorization: Bearer \$(cat $API_KEY_FILE)" http://127.0.0.1:$SERVER_PORT/models | jq '.data[] | {id, status}'
    Test:    curl -s -H "Authorization: Bearer \$(cat $API_KEY_FILE)" -H 'Content-Type: application/json' \\
               http://127.0.0.1:$SERVER_PORT/v1/chat/completions \\
               -d '{"model":"tiel-coder","messages":[{"role":"user","content":"Write a Python one-liner that reverses a string."}],"chat_template_kwargs":{"enable_thinking":false}}' | jq -r '.choices[0].message.content'
             (the first request loads the model - expect a minute or two)
    Unload:  curl -s -H "Authorization: Bearer \$(cat $API_KEY_FILE)" -H 'Content-Type: application/json' \\
               http://127.0.0.1:$SERVER_PORT/models/unload -d '{"model":"tiel-coder"}'

    Tune N_CPU_MOE: load it with the Test command, check VRAM
    ($([ "$be" = cuda ] && echo "nvidia-smi" || echo "watch -n 0.5 'cat /sys/class/drm/card*/device/mem_info_vram_used'")),
    lower n-cpu-moe in $PRESET_FILE two at a time while ~1GB stays free, and
    restart the service each time.

EOF
}

# ============================================================================
# PHASE: firewall
# ============================================================================

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || { info "Firewall configuration disabled."; return 0; }
    say "[firewall] coder API, LAN only"
    have ufw || sudo apt install -y ufw
    sudo ufw allow from "$LAN_SUBNET" to any port "$SERVER_PORT" proto tcp comment "coder" >/dev/null
    ok "Allowed $SERVER_PORT from $LAN_SUBNET (requests also need the API key)"
}

# ============================================================================
# MAIN
# ============================================================================

ALL_PHASES=(check build model server firewall)

main() {
    mkdir -p "$STACK_DIR"
    local -a phases
    if [ $# -gt 0 ]; then phases=("$@"); else phases=("${ALL_PHASES[@]}"); fi
    local p
    for p in "${phases[@]}"; do
        case "$p" in
            check|build|model|server|firewall) "phase_$p"; echo ;;
            *) die "Unknown phase '$p'. Valid: ${ALL_PHASES[*]}" ;;
        esac
    done
    say "Done."
    cat <<EOF
    Coder endpoint:  http://<this machine's IP>:${SERVER_PORT}/v1   (model: tiel-coder)
    On Gemini:       scp $HOST_NAME:$API_KEY_FILE ~/.coder-api-key-$HOST_NAME
                     then add a [[coders]] entry for $HOST_NAME to ~/forge/config.toml
EOF
}

main "$@"
