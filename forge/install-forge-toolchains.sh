#!/usr/bin/env bash
#
# install-forge-toolchains.sh - sandbox images for forge's game, emulator,
# mod and reverse-engineering toolchains. Run on Gemini (where forge runs).
#
#   ./install-forge-toolchains.sh                # every toolchain
#   ./install-forge-toolchains.sh godot c-sdl    # just these
#   ./install-forge-toolchains.sh --list
#   ./install-forge-toolchains.sh --save /mnt/ziggurat/forge/images   # keep the
#   ./install-forge-toolchains.sh --load /mnt/ziggurat/forge/images   # built images
#
# With package mirrors on Ziggurat (ziggurat/README.md) in ~/forge/mirrors.env,
# builds fetch through them when they answer.
#
# Each image is forge-sandbox-<toolchain>:latest. A spec picks one with a line
# `Toolchain: godot`. The images get their tools at BUILD time (with network);
# forge then runs them with no network, a read-only root and uid 1000, the
# same as the Python sandbox.
#
# Godot and Ghidra come from their official GitHub releases - the latest
# stable one unless you pin it:
#   GODOT_VERSION=4.6.2 GHIDRA_VERSION=12.0.4 ./install-forge-toolchains.sh godot re
#
# Disk: base ~0.6 GB; godot +0.2; c-sdl +0.4; lua-love +0.1; java +0.3;
# rust +1.5; dotnet +0.9; re +1.2 (Ghidra and a JDK); sbox +1.2 (.NET 10
# and the libraries s&box's engine loads); gmod 0.5 + the dedicated server
# (~6.5 GB in ~/forge/games/gmod-server); fo4 ~3.5 (Mono, Wine and a Wine
# prefix, .NET 10, Mutagen) + the extracted script sources; blender +1;
# esp32 ~13 (Espressif's ESP-IDF image); minecraft ~1.1 (a JDK, the Paper
# server with its world, the Paper API and
# MockBukkit); shell +0.2; node +0.4; go +0.7; homeassistant ~1.5 (its own
# Python 3.14 image); ux +0.6 (Chromium and its libraries).
#
# Game toolchains read the game's own files, mounted read-only - [games] in
# ~/forge/config.toml (defaults below):
#   sbox     your s&box install (SBOX_DIR)
#   gmod     the Garry's Mod DEDICATED server (Steam app 4020, free,
#            anonymous login) - installed here into ~/forge/games/gmod-server
#   fo4      your Fallout 4 install with the Creation Kit (FO4_DIR); its
#            Papyrus sources are extracted to ~/forge/games/fo4/scripts
#   minecraft nothing - a Paper server is in the image; it needs
#            MINECRAFT_EULA=accept (Mojang's EULA, yours to accept) and
#            MC_CONTACT=<email or URL> (PaperMC's download service asks for
#            contact details); MC_VERSION=26.3 by default
#   blender  nothing (Blender is in the image; BLENDER_VERSION=5.2.3; the
#            tarball is kept in ~/forge/toolchain-cache for rebuilds)
#   shell, node, go, homeassistant  nothing (general-purpose: admin scripts
#            and systemd units; Node/TypeScript; Go; Home Assistant
#            integrations, config and ESPHome YAML)
#   ux       nothing - not a project toolchain: the browser image forge's UX
#            and accessibility check uses on web projects (Playwright's
#            Chromium and axe-core; PLAYWRIGHT_VERSION=1.64.0, AXE_VERSION=4.14.0)
# Then `forge selftest <name>` runs a sample project through each.
#
set -euo pipefail

ALL=(godot c-sdl rust lua-love dotnet java re sbox gmod fo4 blender minecraft shell node go
     homeassistant c-cpp sql mcu esp32 devices raspberrypi ux)
SBOX_DIR="${SBOX_DIR:-$HOME/.steam/debian-installation/steamapps/common/sbox}"
FO4_DIR="${FO4_DIR:-$HOME/.steam/debian-installation/steamapps/common/Fallout 4}"
GAMES="${GAMES:-$HOME/forge/games}"
BLENDER_VERSION="${BLENDER_VERSION:-5.2.3}"
NODE_VERSION="${NODE_VERSION:-26}"            # Node's LTS line (nodejs.org/dist/index.json)
TS_VERSION="${TS_VERSION:-7}"
VITEST_VERSION="${VITEST_VERSION:-5}"
GO_VERSION="${GO_VERSION:-1.27}"
PHACC_VERSION="${PHACC_VERSION:-0.13.375}"    # pins homeassistant 2026.10.4
ESPHOME_VERSION="${ESPHOME_VERSION:-2026.10.2}"
PICO_SDK_VERSION="${PICO_SDK_VERSION:-2.3.1}"
UNITY_VERSION="${UNITY_VERSION:-v2.7.0}"
PICOTOOL_VERSION="${PICOTOOL_VERSION:-2.3.1}"
ESP_IDF_VERSION="${ESP_IDF_VERSION:-v6.1}"     # espressif/idf image tag   # the SDK wants this version, installed
PLAYWRIGHT_VERSION="${PLAYWRIGHT_VERSION:-1.64.0}"
AXE_VERSION="${AXE_VERSION:-4.14.0}"
# npm's published integrity for axe-core 4.14.0 (registry.npmjs.org/axe-core).
AXE_INTEGRITY="${AXE_INTEGRITY:-sha512-9WTZxEjsZ7b13TH8JPmbV2z8CHbl80/2hm3XPEG4JgNdQLK81IBRXmSxHfMAOkSqQeRxT/0dwNDz2GOm3zzpcQ==}"
STEAMCMD_IMAGE="${STEAMCMD_IMAGE:-steamcmd/steamcmd:latest}"
# forge_game.py (forge's sandbox helpers) makes the template projects the
# .NET images restore, so their package caches match what forge asks for.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORGE_GAME="${FORGE_GAME:-}"
for c in "$HOME/forge/forge_game.py" "$HERE/forge_game.py"; do
    [ -z "$FORGE_GAME" ] && [ -f "$c" ] && FORGE_GAME="$c"
done
say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--list" ]; then
    printf '%s\n' "${ALL[@]}"
    exit 0
fi
command -v docker >/dev/null || die "Docker is missing (install-gemini-llm.sh services phase)."
DOCKER=docker
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

# --save DIR [names]: write the built images to DIR (one .tar.zst each, e.g.
# on Ziggurat), so they can be restored without the internet;
# --load DIR [names]: load them back (a new Gemini, a wiped Docker, no net).
if [ "${1:-}" = "--save" ] || [ "${1:-}" = "--load" ]; then
    mode="$1"; dir="${2:?usage: $0 $1 DIR [toolchain...]}"; shift 2
    names=("$@"); [ ${#names[@]} -eq 0 ] && names=(base "${ALL[@]}")
    command -v zstd >/dev/null || die "zstd is missing (sudo apt install zstd)"
    mkdir -p "$dir"
    rc=0
    for n in "${names[@]}"; do
        img="forge-sandbox-$n:latest" f="$dir/forge-sandbox-$n.tar.zst"
        if [ "$mode" = "--save" ]; then
            $DOCKER image inspect "$img" >/dev/null 2>&1 || { warn "$img is not built - skipped"; continue; }
            say "saving $img -> $f"
            if $DOCKER save "$img" | zstd -T0 -q -o "$f.part" && mv "$f.part" "$f"; then
                $DOCKER image inspect --format '{{.Id}}' "$img" > "$f.id"
                ok "$n: $(du -h "$f" | cut -f1)"
            else
                rm -f "$f.part"; warn "$n: save failed"; rc=1
            fi
        else
            [ -f "$f" ] || { warn "no $f - skipped"; continue; }
            say "loading $f"
            zstd -dc "$f" | $DOCKER load -q && ok "$n" || { warn "$n: load failed"; rc=1; }
        fi
    done
    exit $rc
fi

# Package mirrors on Ziggurat (ziggurat/README.md), from ~/forge/mirrors.env
# or the environment: MIRROR_APT (apt-cacher-ng, http://host:3142),
# MIRROR_PYPI (a PyPI simple index, http://host:3141/index/), MIRROR_NPM
# (http://host:4873/), MIRROR_GO (http://host:3300). Each is used only when it
# answers now; otherwise that build goes to the internet as before.
[ -f "$HOME/forge/mirrors.env" ] && . "$HOME/forge/mirrors.env"
MIRROR_ARGS=()
mirror_up() { curl -fsS -o /dev/null --max-time 5 "$1"; }
if [ -n "${MIRROR_APT:-}" ]; then
    # apt-cacher-ng answers 406 on its root page; any HTTP answer means it is up.
    if curl -s -o /dev/null --max-time 5 -w '%{http_code}' "$MIRROR_APT" | grep -qv '^000$'; then
        MIRROR_ARGS+=(--build-arg "http_proxy=$MIRROR_APT")
        ok "apt through $MIRROR_APT"
    else warn "MIRROR_APT $MIRROR_APT does not answer - apt goes to the internet"; fi
fi
if [ -n "${MIRROR_PYPI:-}" ]; then
    if mirror_up "${MIRROR_PYPI%/}/pip/"; then
        MIRROR_ARGS+=(--build-arg "PIP_INDEX_URL=$MIRROR_PYPI"
                      --build-arg "PIP_TRUSTED_HOST=$(echo "$MIRROR_PYPI" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')")
        ok "pip through $MIRROR_PYPI"
    else warn "MIRROR_PYPI $MIRROR_PYPI does not answer - pip goes to PyPI"; fi
fi
if [ -n "${MIRROR_NPM:-}" ]; then
    if mirror_up "${MIRROR_NPM%/}/-/ping"; then
        MIRROR_ARGS+=(--build-arg "npm_config_registry=$MIRROR_NPM")
        ok "npm through $MIRROR_NPM"
    else warn "MIRROR_NPM $MIRROR_NPM does not answer - npm goes to the registry"; fi
fi
if [ -n "${MIRROR_GO:-}" ]; then
    if mirror_up "${MIRROR_GO%/}/healthz"; then
        MIRROR_ARGS+=(--build-arg "GOPROXY=$MIRROR_GO|https://proxy.golang.org|direct")
        ok "Go modules through $MIRROR_GO"
    else warn "MIRROR_GO $MIRROR_GO does not answer - Go uses proxy.golang.org"; fi
fi

WANT=("$@")
[ ${#WANT[@]} -eq 0 ] && WANT=("${ALL[@]}")
for t in "${WANT[@]}"; do
    [[ " ${ALL[*]} " == *" $t "* ]] || die "unknown toolchain '$t' (one of: ${ALL[*]})"
done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# A release asset URL from GitHub: latest stable, or the pinned version.
#   gh_asset owner/repo tag-or-"" regex
gh_asset() {
    local api="https://api.github.com/repos/$1/releases"
    local url="$api/latest"
    [ -n "$2" ] && url="$api/tags/$2"
    curl -fsSL ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$url" |
        python3 -c 'import json, re, sys
rel = json.load(sys.stdin)
pat = re.compile(sys.argv[1])
for a in rel.get("assets", []):
    if pat.search(a["name"]):
        print(a["browser_download_url"]); break
else:
    sys.exit("no asset matching %s in %s" % (sys.argv[1], rel.get("tag_name")))' "$3"
}

LOGDIR="$HOME/forge/toolchain-logs"
mkdir -p "$LOGDIR"
FAILED=()
BUILT=()

# Build one image. The full build output goes to a log; on failure its last
# lines (where apt or curl says what went wrong) are shown and the other
# toolchains still build. Files after the Dockerfile text go into the build
# context (COPY them by their base name).
build() {   # name dockerfile-text [context files...]
    local dir="$TMP/$1" log="$LOGDIR/$1.log"
    mkdir -p "$dir"
    # Build args are visible to RUN steps but not kept in the image; each
    # stage needs them declared after its FROM (http_proxy is predefined).
    printf '%s\n' "$2" | sed -E '/^FROM /a ARG PIP_INDEX_URL\nARG PIP_TRUSTED_HOST\nARG npm_config_registry\nARG GOPROXY' \
        > "$dir/Dockerfile"
    local f
    for f in "${@:3}"; do cp "$f" "$dir/"; done
    say "building forge-sandbox-$1 (this can take a while; log: $log)"
    # DOCKER_BUILD_OPTS: extra `docker build` options, e.g. "--network host"
    # behind a proxy that only listens on localhost.
    # shellcheck disable=SC2086
    if $DOCKER build ${DOCKER_BUILD_OPTS:-} ${MIRROR_ARGS[@]+"${MIRROR_ARGS[@]}"} --progress=plain \
            -t "forge-sandbox-$1:latest" "$dir" >"$log" 2>&1; then
        ok "forge-sandbox-$1:latest"
        BUILT+=("$1")
        return 0
    fi
    warn "forge-sandbox-$1 failed. The end of its log:"
    grep -E "^#[0-9]+ [0-9.]+ (E:|W:|ERROR|error|Unable|Package|dpkg|curl)|^ERROR" "$log" \
        | tail -n 25 | sed 's/^/      /'
    echo "      (whole log: $log)"
    FAILED+=("$1")
    return 0          # keep going: the other toolchains can still build
}

# ---------------------------------------------------------------- base
# (every toolchain image starts from this one, so it must build)
# Python (tests always run in Python), a virtual display for frame capture,
# software OpenGL, ImageMagick for screenshots, and the uid-1000 user.
build base '
FROM python:3.12-slim-trixie
ENV DEBIAN_FRONTEND=noninteractive LIBGL_ALWAYS_SOFTWARE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb xauth x11-utils imagemagick libgl1 libgl1-mesa-dri libegl1 libgles2 \
        libvulkan1 mesa-vulkan-drivers libasound2t64 libpulse0 libxcursor1 libxinerama1 \
        libxrandr2 libxi6 fontconfig fonts-dejavu-core ca-certificates curl unzip xz-utils \
        file git \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -u 1000 -m runner
USER 1000:1000
WORKDIR /work'

[[ " ${BUILT[*]} " == *" base "* ]] || die "the base image did not build - see $LOGDIR/base.log"
for t in "${WANT[@]}"; do
case "$t" in
godot)
    url=$(gh_asset godotengine/godot "${GODOT_VERSION:+${GODOT_VERSION}-stable}" '_linux\.x86_64\.zip$') || {
        warn "godot: could not find the release on GitHub (rate limit? set GITHUB_TOKEN)"
        FAILED+=("godot"); continue; }
    say "Godot: $url"
    build godot "
FROM forge-sandbox-base:latest
USER root
RUN curl -fsSL -o /tmp/godot.zip '$url' && unzip -q /tmp/godot.zip -d /opt/godot \
    && ln -s /opt/godot/Godot_*_linux.x86_64 /usr/local/bin/godot && rm /tmp/godot.zip \
    && godot --headless --version
USER 1000:1000" ;;
c-sdl)
    build c-sdl '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential clang cmake ninja-build pkg-config gdb \
        libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
        libglew-dev libglfw3-dev \
    && rm -rf /var/lib/apt/lists/*
USER 1000:1000' ;;
rust)
    build rust '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libsdl2-dev libasound2-dev libudev-dev libx11-dev \
        libxkbcommon-dev libwayland-dev \
    && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path \
    && chmod -R a+rX /opt/rustup /opt/cargo && cargo --version
USER 1000:1000' ;;
lua-love)
    build lua-love '
FROM forge-sandbox-base:latest
USER root
# The slim base image drops man pages (dpkg path-exclude), and love'"'"'s install
# script registers its man page as an alternative - "alternative path
# /usr/share/man/man6/love-11.5.6.gz doesn'"'"'t exist" stopped the install.
# Let man6 pages back in for this package.
RUN printf "path-include=/usr/share/man/man6/*\n" > /etc/dpkg/dpkg.cfg.d/zz-man6 \
    && apt-get update && apt-get install -y --no-install-recommends love luajit lua5.4 \
    && rm -rf /var/lib/apt/lists/* && luajit -v && lua5.4 -v && command -v love
USER 1000:1000' ;;
dotnet)
    build dotnet '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libicu76 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/di.sh \
    && bash /tmp/di.sh --channel 8.0 --install-dir /opt/dotnet && rm /tmp/di.sh \
    && ln -s /opt/dotnet/dotnet /usr/local/bin/dotnet && dotnet --info >/dev/null
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
USER 1000:1000' ;;
java)
    build java '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*
USER 1000:1000' ;;
re)
    url=$(gh_asset NationalSecurityAgency/ghidra "${GHIDRA_VERSION:+Ghidra_${GHIDRA_VERSION}_build}" '^ghidra_.*_PUBLIC_.*\.zip$') || {
        warn "re: could not find the release on GitHub (rate limit? set GITHUB_TOKEN)"
        FAILED+=("re"); continue; }
    say "Ghidra: $url"
    # rizin is not packaged in Debian (trixie or later): use its official
    # static Linux build from GitHub instead.
    rz=$(gh_asset rizinorg/rizin "${RIZIN_VERSION:+v${RIZIN_VERSION}}" '^rizin-v.*-static-x86_64\.tar\.xz$') || {
        warn "re: could not find rizin's static build on GitHub (rate limit? set GITHUB_TOKEN)"
        FAILED+=("re"); continue; }
    say "rizin: $rz"
    # REA (github.com/morluto/rea, MIT): reverse engineering for agents -
    # JavaScript/Electron, .NET, Go, Rust, Godot PCK, IL2CPP metadata, and
    # native binaries through the Ghidra below. Needs Node >= 22 (Debian has
    # 20), so Node comes from nodejs.org. Pinned: REA's README warns of
    # look-alike packages, and an image must rebuild the same way.
    REA_VERSION="${REA_VERSION:-6.7.0}"
    node_ver=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c '
import json, sys
print(next(r["version"] for r in json.load(sys.stdin) if r["version"].startswith("v24.") and r.get("lts")))') || {
        warn "re: could not read the Node.js release list from nodejs.org"; FAILED+=("re"); continue; }
    say "Node.js $node_ver, rea-agents $REA_VERSION"
    build re "
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless binutils build-essential clang gcc-multilib nasm xxd \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/ghidra.zip '$url' && unzip -q /tmp/ghidra.zip -d /opt \
    && mv /opt/ghidra_* /opt/ghidra && rm /tmp/ghidra.zip
RUN mkdir -p /tmp/rz && curl -fsSL '$rz' | tar -xJ -C /tmp/rz \
    && bin=\$(find /tmp/rz -type f -name rizin -path '*/bin/*' | head -n1) \
    && test -n \"\$bin\" && mv \"\$(dirname \"\$(dirname \"\$bin\")\")\" /opt/rizin \
    && ln -sf /opt/rizin/bin/* /usr/local/bin/ && rm -rf /tmp/rz && rizin -v
RUN apt-get update && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://nodejs.org/dist/$node_ver/node-$node_ver-linux-x64.tar.xz \
       | tar -xJ -C /opt && mv /opt/node-$node_ver-linux-x64 /opt/node \
    && ln -sf /opt/node/bin/node /opt/node/bin/npm /opt/node/bin/npx /usr/local/bin/ \
    && npm install --global --no-fund --no-audit --prefix /opt/rea rea-agents@$REA_VERSION \
    && ln -sf /opt/rea/bin/rea /usr/local/bin/rea && rea --help > /opt/rea-help.txt 2>&1 \
    && test -s /opt/rea-help.txt
ENV GHIDRA_INSTALL_DIR=/opt/ghidra PATH=/opt/ghidra/support:\$PATH
USER 1000:1000" ;;
sbox)
    [ -n "$FORGE_GAME" ] || { warn "sbox: forge_game.py not found (set FORGE_GAME)"; FAILED+=("sbox"); continue; }
    [ -d "$SBOX_DIR/bin/managed" ] || warn "sbox: no s&box install at $SBOX_DIR (the image builds; set [games] sbox before using it)"
    # .NET 10 from Microsoft's SDK image; the libraries s&box's Linux engine
    # loads (probes 2 and 3: GLib, Qt's X11/xcb plugins, fonts, GL/Vulkan
    # loaders); MSTest restored into /opt/forge-nuget, where forge's offline
    # test projects find it.
    build sbox '
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS sdk
FROM forge-sandbox-base:latest
USER root
COPY --from=sdk /usr/share/dotnet /usr/share/dotnet
RUN ln -sf /usr/share/dotnet/dotnet /usr/local/bin/dotnet
RUN apt-get update && apt-get install -y --no-install-recommends libicu76 \
        libglib2.0-0t64 libpng16-16t64 libpcre2-16-0 libdbus-1-3 libharfbuzz0b libfontconfig1 \
        libfreetype6 libx11-6 libx11-xcb1 libxext6 libxcursor1 libxrandr2 libxi6 libxfixes3 \
        libxss1 libxkbcommon0 libxkbcommon-x11-0 libwayland-client0 libdecor-0-0 libgl1 libegl1 \
        libvulkan1 libasound2t64 libpulse0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-xinerama0 libxcb-xinput0 \
    && rm -rf /var/lib/apt/lists/*
ENV DOTNET_ROOT=/usr/share/dotnet DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
COPY forge_game.py /tmp/forge_game.py
RUN mkdir -p /opt/forge-nuget && python3 /tmp/forge_game.py sbox-template /tmp/tpl \
    && cd /tmp/tpl && export HOME=/tmp/h DOTNET_CLI_HOME=/tmp/h NUGET_PACKAGES=/opt/forge-nuget \
    && dotnet restore && dotnet build -nodeReuse:false -p:UseSharedCompilation=false \
    && dotnet test --no-build \
    && chmod -R a+rX /opt/forge-nuget && rm -rf /tmp/tpl /tmp/h /tmp/forge_game.py
USER 1000:1000' "$FORGE_GAME" ;;
gmod)
    # The dedicated server (app 4020) on the host, read-only in the sandbox.
    # A server left by gmod-probe.sh is moved here rather than downloaded again.
    SRV="$GAMES/gmod-server"
    mkdir -p "$GAMES"
    if [ ! -d "$SRV/garrysmod" ] && [ -d "$HOME/gmod-probe/server/garrysmod" ]; then
        say "gmod: moving the probe's server from ~/gmod-probe/server to $SRV"
        mv "$HOME/gmod-probe/server" "$SRV"
    fi
    mkdir -p "$SRV"
    say "gmod: installing/updating the dedicated server (app 4020, anonymous) in $SRV"
    if ! $DOCKER run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp/home -v "$SRV":/server \
            "$STEAMCMD_IMAGE" +force_install_dir /server +login anonymous +app_update 4020 validate +quit \
            > "$LOGDIR/gmod-server.log" 2>&1 || [ ! -f "$SRV/srcds_run" ]; then
        warn "gmod: SteamCMD did not install the server - see $LOGDIR/gmod-server.log"
        FAILED+=("gmod"); continue
    fi
    # What gmod-probe.sh added to the server.
    rm -rf "${SRV:?}/garrysmod/addons/forge_probe"
    rm -f "${SRV:?}/garrysmod/data/forge_probe.txt"
    ok "gmod: server $(grep -h -m1 -i PatchVersion "$SRV/garrysmod/steam.inf" 2>/dev/null) in $SRV ($(du -sh "$SRV" | cut -f1))"
    # glualint (GLuaFixer, LGPL-2.1): its x86-64 Linux build.
    gl=$(gh_asset FPtje/GLuaFixer "${GLUALINT_VERSION:-}" 'x86_64-linux\.zip$') || {
        warn "gmod: could not find glualint on GitHub (rate limit? set GITHUB_TOKEN)"
        FAILED+=("gmod"); continue; }
    say "glualint: $gl"
    mkdir -p "$TMP/gl" && curl -fsSL -o "$TMP/gl/gl.zip" "$gl" && (cd "$TMP/gl" && unzip -oq gl.zip)
    glbin=$(find "$TMP/gl" -type f -name 'glualint' | head -1)
    [ -n "$glbin" ] || { warn "gmod: no glualint binary in $gl"; FAILED+=("gmod"); continue; }
    # srcds is 32-bit: SteamCMD's image (what gmod-probe.sh ran it in) has
    # the i386 libraries. Its ENTRYPOINT is steamcmd - cleared for forge.
    build gmod "
FROM $STEAMCMD_IMAGE
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
RUN (userdel -r ubuntu 2>/dev/null || true) && useradd -u 1000 -m runner
COPY glualint /usr/local/bin/glualint
RUN chmod 755 /usr/local/bin/glualint \
    && (glualint --version >/dev/null 2>&1 || glualint --help >/dev/null 2>&1)
ENV HOME=/tmp/home
USER 1000:1000
WORKDIR /work
ENTRYPOINT []
CMD []" "$glbin" ;;
fo4)
    [ -n "$FORGE_GAME" ] || { warn "fo4: forge_game.py not found (set FORGE_GAME)"; FAILED+=("fo4"); continue; }
    if [ -d "$FO4_DIR/Data" ]; then
        # The Papyrus sources ship zipped (Base.zip, DLC01.zip, ...): one
        # folder each, extracted again only when the zip is newer.
        SCR="$GAMES/fo4/scripts"
        mkdir -p "$SCR"
        n=0
        while IFS= read -r -d '' z; do
            python3 - "$z" "$SCR" <<'PY'
import os, shutil, sys, zipfile
src, root = sys.argv[1], sys.argv[2]
name = os.path.splitext(os.path.basename(src))[0]
dst = os.path.join(root, name)
if os.path.isdir(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
    sys.exit(0)
shutil.rmtree(dst, ignore_errors=True)
zipfile.ZipFile(src).extractall(dst)
# A zip holding one folder: its contents become the folder.
items = os.listdir(dst)
if len(items) == 1 and os.path.isdir(os.path.join(dst, items[0])):
    inner = os.path.join(dst, items[0])
    for f in os.listdir(inner):
        shutil.move(os.path.join(inner, f), dst)
    os.rmdir(inner)
PY
            n=$((n + 1))
        done < <(find "$FO4_DIR/Data/Scripts/Source" -maxdepth 2 -iname '*.zip' -print0 2>/dev/null)
        ok "fo4: $n script source archive(s) in $SCR ($(find "$SCR" -iname '*.psc' | wc -l) .psc)"
        find "$SCR" -iname 'Institute_Papyrus_Flags.flg' | grep -q . \
            || warn "fo4: Institute_Papyrus_Flags.flg not found in the sources - Papyrus will not compile"
        find "$FO4_DIR" -maxdepth 3 -iname 'PapyrusCompiler.exe' | grep -q . \
            || warn "fo4: no PapyrusCompiler.exe under $FO4_DIR - install the Creation Kit for scripts"
    else
        warn "fo4: no Fallout 4 install at $FO4_DIR (set FO4_DIR; the image still builds)"
    fi
    # Probe 5's combination: Ubuntu 24.04's Mono runs the compiler, Wine runs
    # its native PapyrusAssembler.exe. The Wine prefix is made once here (as
    # uid 1000); forge mirrors it under /tmp per run. Mutagen (GPL-3.0) is
    # restored into /opt/forge-nuget for the plugin builds.
    build fo4 '
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS sdk
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
        python3 ca-certificates mono-runtime mono-devel wine wine64 wine32:i386 libicu74 \
        unzip file procps \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sdk /usr/share/dotnet /usr/share/dotnet
RUN ln -sf /usr/share/dotnet/dotnet /usr/local/bin/dotnet
ENV DOTNET_ROOT=/usr/share/dotnet DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
RUN (userdel -r ubuntu 2>/dev/null || true) && useradd -u 1000 -m runner \
    && mkdir -p /opt/wineprefix /opt/forge-nuget && chown 1000:1000 /opt/wineprefix /opt/forge-nuget
COPY forge_game.py /tmp/forge_game.py
USER 1000:1000
RUN export HOME=/tmp/h WINEPREFIX=/opt/wineprefix WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=" \
    && wineboot -i && wineserver -w && test -f /opt/wineprefix/system.reg && rm -rf /tmp/h
RUN python3 /tmp/forge_game.py fo4-template /tmp/tpl && cd /tmp/tpl \
    && export HOME=/tmp/h DOTNET_CLI_HOME=/tmp/h NUGET_PACKAGES=/opt/forge-nuget \
    && dotnet restore && dotnet build -nodeReuse:false -p:UseSharedCompilation=false \
    && rm -rf /tmp/tpl /tmp/h
USER root
RUN rm -f /tmp/forge_game.py && chmod -R a+rX /opt/forge-nuget
ENV HOME=/tmp/home
USER 1000:1000
WORKDIR /work' "$FORGE_GAME" ;;
blender)
    # download.blender.org is behind Cloudflare, which can refuse curl from a
    # home connection (403); blender.org's own download page sends people to
    # mirror.clarkson.edu. Each source is tried in turn, on this machine,
    # and the tarball must match the published SHA-256 before Docker sees it.
    # BLENDER_MIRROR=<base url> puts one first; BLENDER_SHA256 pins the sum.
    major=$(echo "$BLENDER_VERSION" | cut -d. -f1-2)
    tarball="blender-$BLENDER_VERSION-linux-x64.tar.xz"
    sources=(${BLENDER_MIRROR:+"$BLENDER_MIRROR"} https://download.blender.org/release
             https://mirror.clarkson.edu/blender/release https://mirrors.ocf.berkeley.edu/blender/release
             https://ftp.nluug.nl/pub/graphics/blender/release)
    bsha="${BLENDER_SHA256:-}"
    if [ -z "$bsha" ]; then
        for base in "${sources[@]}"; do
            bsha=$(curl -fsSL -A "forge-install (curl)" "$base/Blender$major/blender-$BLENDER_VERSION.sha256" 2>/dev/null \
                   | awk -v f="$tarball" '$2 == f {print $1}') && [ -n "$bsha" ] && { say "Blender checksum from $base"; break; }
            bsha=""
        done
    fi
    [ -n "$bsha" ] || { warn "blender: no checksum for $BLENDER_VERSION from any source (set BLENDER_SHA256)"; FAILED+=("blender"); continue; }
    cache="$HOME/forge/toolchain-cache"
    mkdir -p "$cache"
    if ! echo "$bsha  $cache/$tarball" | sha256sum -c --quiet - 2>/dev/null; then
        got=""
        for base in "${sources[@]}"; do
            say "Blender: $base/Blender$major/$tarball"
            if curl -fL --progress-bar -A "forge-install (curl)" -o "$cache/$tarball.part" "$base/Blender$major/$tarball" \
                    && echo "$bsha  $cache/$tarball.part" | sha256sum -c --quiet -; then
                mv "$cache/$tarball.part" "$cache/$tarball"
                got=1
                break
            fi
            warn "blender: $base failed or the checksum did not match - trying the next source"
        done
        rm -f "$cache/$tarball.part"
        [ -n "$got" ] || { warn "blender: could not download $tarball"; FAILED+=("blender"); continue; }
    fi
    ok "blender: $tarball (sha256 ok)"
    build blender "
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 libx11-6 libxext6 \
        libgl1 libegl1 libgomp1 libwayland-client0 libwayland-cursor0 libwayland-egl1 libdecor-0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY $tarball /tmp/blender.tar.xz
RUN echo '$bsha  /tmp/blender.tar.xz' | sha256sum -c - \
    && mkdir -p /opt/blender && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz && ln -s /opt/blender/blender /usr/local/bin/blender \
    && HOME=/tmp/h blender -b --factory-startup -noaudio --python-expr 'import bpy; print(bpy.app.version_string)' \
    && rm -rf /tmp/h
USER 1000:1000" "$cache/$tarball" ;;
shell)
    # Shell scripts, installers and systemd units: ShellCheck, shfmt, bats
    # (with bats-support, bats-assert and bats-file), systemd-analyze for
    # unit files, yamllint and jq - Debian's packages.
    build shell '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        shellcheck shfmt bats bats-support bats-assert bats-file systemd yamllint jq \
        make gawk rsync procps bsdextrautils \
    && rm -rf /var/lib/apt/lists/*
USER 1000:1000' ;;
minecraft)
    # A Paper server (PaperMC's own build of the Minecraft server, for
    # plugins) and what forge compiles and tests Paper plugins with: a JDK
    # (Eclipse Temurin), the Paper API and MockBukkit with every library they
    # need, and JUnit's console launcher - all fetched now, so a run needs no
    # network.
    #   MINECRAFT_EULA=accept  required: the server is Mojang's software under
    #                          the Minecraft EULA (https://aka.ms/MinecraftEULA),
    #                          and only you can accept it. Nothing is accepted
    #                          for you.
    #   MC_CONTACT=<email or URL>  required by PaperMC's download service,
    #                          which wants a User-Agent with contact details.
    #   MC_VERSION=26.3 (default), PAPER_BUILD (default: latest STABLE),
    #   MOCKBUKKIT_VERSION, JUNIT_VERSION (default: MockBukkit's).
    if [ "${MINECRAFT_EULA:-}" != "accept" ]; then
        warn "minecraft: the server needs the Minecraft EULA (https://aka.ms/MinecraftEULA) - read it, then run with MINECRAFT_EULA=accept"
        FAILED+=("minecraft"); continue
    fi
    if [ -z "${MC_CONTACT:-}" ]; then
        warn "minecraft: set MC_CONTACT to an email or URL - PaperMC's download service needs contact details in the User-Agent"
        FAILED+=("minecraft"); continue
    fi
    MC_VERSION="${MC_VERSION:-26.3}"
    ua="forge-toolchains/1.0 ($MC_CONTACT)"
    fill="https://fill.papermc.io/v3/projects/paper/versions/$MC_VERSION/builds"
    cache="$HOME/forge/toolchain-cache"
    mkdir -p "$cache" "$TMP/mc"
    if ! curl -fsSL -A "$ua" "$fill" -o "$TMP/mc/builds.json"; then
        warn "minecraft: PaperMC has no builds list for $MC_VERSION ($fill)"
        FAILED+=("minecraft"); continue
    fi
    # The chosen build (PAPER_BUILD, or the newest STABLE one): its jar's
    # name, URL and SHA-256 as PaperMC publishes them.
    if ! read -r pbuild pjar purl psha < <(python3 - "$TMP/mc/builds.json" "${PAPER_BUILD:-}" <<'PY'
import json, sys
builds = json.load(open(sys.argv[1]))
want = sys.argv[2]
pick = [b for b in builds if (str(b["id"]) == want if want else b.get("channel") == "STABLE")]
if not pick:
    sys.exit(1)
b = max(pick, key=lambda b: b["id"])
d = b["downloads"]["server:default"]
print(b["id"], d["name"], d["url"], d["checksums"]["sha256"])
PY
    ); then
        warn "minecraft: no ${PAPER_BUILD:+build $PAPER_BUILD}${PAPER_BUILD:-STABLE build} of Paper $MC_VERSION"
        FAILED+=("minecraft"); continue
    fi
    if ! echo "$psha  $cache/$pjar" | sha256sum -c --quiet - 2>/dev/null; then
        say "Paper: $pjar"
        if ! curl -fL --progress-bar -A "$ua" -o "$cache/$pjar.part" "$purl" \
                || ! echo "$psha  $cache/$pjar.part" | sha256sum -c --quiet -; then
            rm -f "$cache/$pjar.part"
            warn "minecraft: could not download $pjar, or its SHA-256 did not match"
            FAILED+=("minecraft"); continue
        fi
        mv "$cache/$pjar.part" "$cache/$pjar"
    fi
    ok "minecraft: Paper $MC_VERSION build $pbuild (sha256 ok)"
    cp "$cache/$pjar" "$TMP/mc/paper.jar"
    # The Paper API for this build, and MockBukkit for this Minecraft version
    # (Maven Central publishes one artifact per version: mockbukkit-v26.3).
    papi="$MC_VERSION.build.$pbuild-stable"
    if ! curl -fsI "https://repo.papermc.io/repository/maven-public/io/papermc/paper/paper-api/$papi/paper-api-$papi.pom" >/dev/null; then
        warn "minecraft: PaperMC's Maven repository has no paper-api $papi"
        FAILED+=("minecraft"); continue
    fi
    mbv="${MOCKBUKKIT_VERSION:-$(curl -fsSL "https://repo1.maven.org/maven2/org/mockbukkit/mockbukkit/mockbukkit-v$MC_VERSION/maven-metadata.xml" 2>/dev/null \
          | sed -n 's:.*<release>\(.*\)</release>.*:\1:p' | head -1)}"
    if [ -z "$mbv" ]; then
        warn "minecraft: no MockBukkit for Minecraft $MC_VERSION on Maven Central (mockbukkit-v$MC_VERSION) - set MC_VERSION to one it has"
        FAILED+=("minecraft"); continue
    fi
    jv="${JUNIT_VERSION:-$(curl -fsSL "https://repo1.maven.org/maven2/org/mockbukkit/mockbukkit/mockbukkit-v$MC_VERSION/$mbv/mockbukkit-v$MC_VERSION-$mbv.pom" 2>/dev/null \
          | tr -d '\n ' | sed -n 's:.*<artifactId>junit-jupiter-api</artifactId><version>\([^<]*\)</version>.*:\1:p')}"
    jv="${jv:-6.1.3}"
    say "minecraft: paper-api $papi, MockBukkit $mbv, JUnit $jv"
    cat > "$TMP/mc/pom.xml" <<EOF
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>forge</groupId><artifactId>mc-libs</artifactId><version>1</version>
  <repositories>
    <repository><id>papermc</id><url>https://repo.papermc.io/repository/maven-public/</url></repository>
  </repositories>
  <dependencies>
    <dependency><groupId>io.papermc.paper</groupId><artifactId>paper-api</artifactId><version>$papi</version></dependency>
    <dependency><groupId>org.mockbukkit.mockbukkit</groupId><artifactId>mockbukkit-v$MC_VERSION</artifactId><version>$mbv</version><scope>test</scope></dependency>
    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>$jv</version><scope>test</scope></dependency>
  </dependencies>
</project>
EOF
    # The test server's settings: offline, a small flat world, no spawn
    # protection, and nothing that phones home.
    cat > "$TMP/mc/server.properties" <<'EOF'
online-mode=false
level-name=world
level-type=minecraft\:flat
generate-structures=false
spawn-protection=0
view-distance=3
simulation-distance=3
max-players=4
enable-rcon=false
enable-query=false
server-ip=127.0.0.1
server-port=25565
motd=forge test server
EOF
    # Start the server once while building (Paper's launcher fetches and
    # patches Mojang's server jar on first start, and the world is
    # generated), then stop it from its console.
    cat > "$TMP/mc/mc-prime.sh" <<'EOF'
#!/bin/sh
set -eu
cd /opt/mc/server
printf 'eula=true\n' > eula.txt
mkdir -p plugins/bStats
printf 'enabled: false\n' > plugins/bStats/config.yml
rm -f /tmp/console && mkfifo /tmp/console
exec 3<>/tmp/console
java -Xms256M -Xmx1536M -jar paper.jar --nogui <&3 > /tmp/prime.log 2>&1 &
pid=$!
n=0
until grep -q 'Done (' /tmp/prime.log; do
    if ! kill -0 "$pid" 2>/dev/null; then tail -n 60 /tmp/prime.log; echo "the server exited during its first start"; exit 1; fi
    n=$((n + 1)); [ "$n" -lt 450 ] || { tail -n 60 /tmp/prime.log; echo "no 'Done' after 15 minutes"; exit 1; }
    sleep 2
done
echo stop >&3
wait "$pid" || true
grep -m1 'Starting minecraft server version' /tmp/prime.log || true
grep -m1 'Done (' /tmp/prime.log
rm -rf logs crash-reports
EOF
    printf '{"minecraft": "%s", "paper_build": %s, "paper_api": "%s", "mockbukkit": "%s", "junit": "%s", "api_version": "%s"}\n' \
        "$MC_VERSION" "$pbuild" "$papi" "$mbv" "$jv" "$MC_VERSION" > "$TMP/mc/versions.json"
    javaapi=""
    for c in "$HOME/forge/tools/java_api.py" "$HERE/tools/java_api.py"; do
        [ -z "$javaapi" ] && [ -f "$c" ] && javaapi="$c"
    done
    [ -n "$javaapi" ] || { warn "minecraft: tools/java_api.py is missing (it makes forge's Paper API index)"; FAILED+=("minecraft"); continue; }
    build minecraft "
FROM maven:3.9-eclipse-temurin-25 AS libs
COPY pom.xml /libs/pom.xml
RUN cd /libs && mvn -B -q dependency:copy-dependencies -DincludeScope=compile -DoutputDirectory=/opt/mc/lib/compile \\
    && mvn -B -q dependency:copy-dependencies -DincludeScope=test -DoutputDirectory=/opt/mc/lib/test \\
    && mvn -B -q dependency:copy -Dartifact=org.junit.platform:junit-platform-console-standalone:$jv -DoutputDirectory=/opt/mc/tools \\
    && ls /opt/mc/lib/compile | grep -q '^paper-api-' && ls /opt/mc/lib/test | grep -q '^mockbukkit-'

FROM eclipse-temurin:25-jdk AS server
COPY paper.jar server.properties /opt/mc/server/
COPY mc-prime.sh /usr/local/bin/mc-prime.sh
RUN chmod 755 /usr/local/bin/mc-prime.sh && /usr/local/bin/mc-prime.sh

FROM forge-sandbox-base:latest
USER root
COPY --from=eclipse-temurin:25-jdk /opt/java/openjdk /opt/jdk
COPY --from=libs /opt/mc/lib /opt/mc/lib
COPY --from=libs /opt/mc/tools /opt/mc/tools
COPY --from=server /opt/mc/server /opt/mc/server
COPY versions.json java_api.py /opt/mc/
RUN for t in java javac jar javap; do ln -sf /opt/jdk/bin/\$t /usr/local/bin/\$t; done \\
    && mv /opt/mc/tools/junit-platform-console-standalone-*.jar /opt/mc/tools/junit-console.jar \\
    && python3 /opt/mc/java_api.py --name 'Paper API' --version $papi -o /opt/mc/paper-api.json.gz \\
         --prefix org.bukkit --prefix io.papermc --prefix com.destroystokyo --prefix net.kyori.adventure \\
         /opt/mc/lib/compile/*.jar \\
    && python3 /opt/mc/java_api.py --name MockBukkit --version $mbv -o /opt/mc/mockbukkit-api.json.gz \\
         --prefix org.mockbukkit /opt/mc/lib/test/mockbukkit-*.jar \\
    && chmod -R a+rX /opt/mc /opt/jdk
ENV JAVA_HOME=/opt/jdk FORGE_MC=/opt/mc
USER 1000:1000" "$TMP/mc/pom.xml" "$TMP/mc/paper.jar" "$TMP/mc/server.properties" \
        "$TMP/mc/mc-prime.sh" "$TMP/mc/versions.json" "$javaapi"
    # forge reads the Paper API index on the host (compile-error hints).
    if [[ " ${BUILT[*]} " == *" minecraft "* ]]; then
        mkdir -p "$HOME/forge/minecraft"
        $DOCKER run --rm --network none forge-sandbox-minecraft:latest cat /opt/mc/paper-api.json.gz \
            > "$HOME/forge/minecraft/paper-$papi-api.json.gz" \
            && $DOCKER run --rm --network none forge-sandbox-minecraft:latest cat /opt/mc/mockbukkit-api.json.gz \
            > "$HOME/forge/minecraft/mockbukkit-$mbv-api.json.gz" \
            && ok "minecraft: API indexes -> ~/forge/minecraft/ (Paper $papi, MockBukkit $mbv)"
    fi ;;

node)
    # Node.js (the official node image's build, NODE_VERSION=26 - the LTS
    # line in Nov 2026) with TypeScript, vitest, tsx, jsdom and @types/node
    # in /opt/node-tools. A project's other npm packages are fetched per
    # run by a download-only container (npm --ignore-scripts: no package
    # code runs) and mounted read-only, like Rust's crates.
    build node "
FROM node:$NODE_VERSION-trixie-slim AS node
FROM forge-sandbox-base:latest
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \\
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \\
    && mkdir -p /opt/node-tools && cd /opt/node-tools \\
    && echo '{\"name\": \"forge-node-tools\", \"private\": true}' > package.json \\
    && HOME=/tmp npm install --ignore-scripts --no-audit --no-fund \\
        'typescript@$TS_VERSION' 'vitest@$VITEST_VERSION' tsx jsdom '@types/node@$NODE_VERSION' \\
    && rm -rf /tmp/.npm && chmod -R a+rX /opt/node-tools \\
    && node -e 'const fs=require(\"fs\"),v={node:process.versions.node};for(const p of [\"typescript\",\"vitest\",\"tsx\",\"jsdom\",\"@types/node\"])v[p]=JSON.parse(fs.readFileSync(\"/opt/node-tools/node_modules/\"+p+\"/package.json\")).version;fs.writeFileSync(\"/opt/node-tools/versions.json\",JSON.stringify(v))' \\
    && cat /opt/node-tools/versions.json
ENV PATH=/opt/node-tools/node_modules/.bin:\$PATH NPM_CONFIG_UPDATE_NOTIFIER=false
USER 1000:1000" ;;
go)
    # Go (the official golang image's /usr/local/go, GO_VERSION=1.27) with
    # gcc for cgo and the race detector. Modules are fetched per run by a
    # download-only container (go mod download runs no module code) into
    # the run's deps/, mounted read-only.
    build go "
FROM golang:$GO_VERSION-trixie AS go
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \\
    && rm -rf /var/lib/apt/lists/*
COPY --from=go /usr/local/go /usr/local/go
ENV PATH=/usr/local/go/bin:\$PATH GOTOOLCHAIN=local GOTELEMETRY=off
RUN go version
USER 1000:1000" ;;
homeassistant)
    # Home Assistant custom integrations and configuration: Home Assistant
    # core with pytest-homeassistant-custom-component (which pins the core
    # version it was cut from; PHACC_VERSION), on Python 3.14 as core
    # needs; ESPHome (ESPHOME_VERSION) in its own venv for validating device
    # YAML (`esphome config` - no firmware compile: that needs PlatformIO's
    # downloads); yamllint.
    build homeassistant "
FROM python:3.14-slim-trixie
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \\
        ca-certificates git curl build-essential libffi-dev libssl-dev libjpeg-dev zlib1g-dev \\
        libturbojpeg0 libpcap0.8 ffmpeg jq \\
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'pytest-homeassistant-custom-component==$PHACC_VERSION' yamllint \\
    && pip install --no-cache-dir \$(python3 -c 'from homeassistant.scripts import check_config as c; print(\" \".join(c.REQUIREMENTS))') \\
    && python3 -m venv /opt/esphome && /opt/esphome/bin/pip install --no-cache-dir 'esphome==$ESPHOME_VERSION' \\
    && ln -s /opt/esphome/bin/esphome /usr/local/bin/esphome \\
    && python3 -c 'import json, importlib.metadata as m; v = {p: m.version(p) for p in (\"homeassistant\", \"pytest-homeassistant-custom-component\", \"pytest\")}; v[\"python\"] = __import__(\"platform\").python_version(); v[\"esphome\"] = \"$ESPHOME_VERSION\"; open(\"/opt/ha-versions.json\", \"w\").write(json.dumps(v)); print(v)'
RUN useradd -u 1000 -m runner
USER 1000:1000
WORKDIR /work" ;;
c-cpp)
    # General C and C++ (not games - that is c-sdl): GCC 14 and Clang 19,
    # CMake, Ninja, Meson, the sanitizers, clang-tidy, clang-format,
    # cppcheck, valgrind, gcovr; test frameworks (GoogleTest/GoogleMock,
    # Catch2, doctest, cmocka, Check) and common libraries as Debian builds
    # them, so projects need nothing from the network.
    build c-cpp '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ clang clang-tidy clang-format clang-tools lld llvm libclang-rt-19-dev \
        cppcheck valgrind gdb cmake ninja-build meson pkg-config ccache lcov gcovr bear \
        libgtest-dev libgmock-dev catch2 doctest-dev libcmocka-dev check libbenchmark-dev \
        libfmt-dev nlohmann-json3-dev libspdlog-dev libcli11-dev libboost-dev \
        libboost-program-options-dev libboost-filesystem-dev libyaml-cpp-dev libeigen3-dev \
        libcurl4-openssl-dev libssl-dev libsqlite3-dev libpq-dev zlib1g-dev libzstd-dev \
        libpcre2-dev libtbb-dev libsdl2-dev \
    && rm -rf /var/lib/apt/lists/* \
    && gcc --version | head -1 && clang --version | head -1
USER 1000:1000' ;;
sql)
    # SQL: PostgreSQL 17 with pgTAP (pg_prove), MariaDB 11, SQLite and
    # DuckDB - each started per test as a throwaway server in /tmp, as
    # uid 1000 - and sqlfluff (lint), sqlglot (parse/transpile), psycopg,
    # PyMySQL.
    build sql '
FROM forge-sandbox-base:latest
USER root
RUN mkdir -p /etc/postgresql-common && echo "create_main_cluster = false" \
        > /etc/postgresql-common/createcluster.conf \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-17 postgresql-17-pgtap libtap-parser-sourcehandler-pgtap-perl \
        postgresql-client-17 mariadb-server mariadb-client sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir sqlfluff sqlglot "psycopg[binary]" pymysql duckdb
ENV PATH=/usr/lib/postgresql/17/bin:$PATH
USER 1000:1000' ;;
mcu)
    # Microcontrollers: GCC for Arm Cortex-M (newlib and picolibc), AVR
    # (avr-libc) and RISC-V (picolibc); the Raspberry Pi Pico SDK
    # (PICO_SDK_VERSION, with its submodules - TinyUSB, the CYW43 driver,
    # lwIP...) with picotool and pioasm built and installed, so a Pico build
    # fetches nothing; Unity (UNITY_VERSION) for unit tests; QEMU
    # (Cortex-M, AVR, RISC-V boards) and simavr to RUN firmware; the
    # MicroPython unix port and mpremote; and the flashing tools (avrdude,
    # esptool, dfu-util, openocd, stlink) for checking images offline.
    build mcu "
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \\
        build-essential cmake ninja-build pkg-config git gdb-multiarch cppcheck clang-format \\
        gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib \\
        picolibc-arm-none-eabi gcc-avr avr-libc binutils-avr simavr \\
        gcc-riscv64-unknown-elf picolibc-riscv64-unknown-elf \\
        qemu-system-arm qemu-system-misc qemu-system-riscv \\
        micropython avrdude dfu-util openocd esptool stlink-tools libusb-1.0-0-dev \\
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch '$PICO_SDK_VERSION' --recurse-submodules --shallow-submodules \\
        https://github.com/raspberrypi/pico-sdk /opt/pico-sdk \\
    && git clone --depth 1 --branch '$UNITY_VERSION' https://github.com/ThrowTheSwitch/Unity /opt/unity \\
    && git clone --depth 1 --branch '$PICOTOOL_VERSION' https://github.com/raspberrypi/picotool /tmp/picotool \\
    && PICO_SDK_PATH=/opt/pico-sdk cmake -S /tmp/picotool -B /tmp/picotool/build -G Ninja \\
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \\
    && cmake --build /tmp/picotool/build && cmake --install /tmp/picotool/build \\
    && cmake -S /opt/pico-sdk/tools/pioasm -B /tmp/pioasm -G Ninja -DCMAKE_BUILD_TYPE=Release \\
        -DCMAKE_INSTALL_PREFIX=/usr/local -DPIOASM_FLAT_INSTALL=1 -DPIOASM_VERSION_STRING='$PICO_SDK_VERSION' \\
    && cmake --build /tmp/pioasm && cmake --install /tmp/pioasm \\
    && rm -rf /tmp/picotool /tmp/pioasm /opt/pico-sdk/.git /opt/unity/.git \\
    && pip install --no-cache-dir mpremote pyserial intelhex \\
    && chmod -R a+rX /opt/pico-sdk /opt/unity \\
    && printf '{\"pico_sdk\": \"%s\", \"picotool\": \"%s\", \"unity\": \"%s\"}' '$PICO_SDK_VERSION' '$PICOTOOL_VERSION' '$UNITY_VERSION' > /opt/mcu-versions.json
COPY forge_hw.py machine.py /opt/micropython-fakes/
RUN chmod -R a+rX /opt/micropython-fakes
ENV PICO_SDK_PATH=/opt/pico-sdk UNITY_PATH=/opt/unity FORGE_MPY_FAKES=/opt/micropython-fakes
USER 1000:1000" "$HERE/mcu/micropython_fakes/forge_hw.py" "$HERE/mcu/micropython_fakes/machine.py" ;;
esp32)
    # Espressif ESP32 family: ESP-IDF (ESP_IDF_VERSION) from Espressif's own
    # image (~13 GB), which already has every chip's toolchain and
    # Espressif's QEMU for the ESP32 and ESP32-C3, so firmware runs in
    # tests. The image is Ubuntu (uid 1000 is its "ubuntu" user); forge's
    # helpers only need its python3. IDF Component Manager dependencies
    # (idf_component.yml) cannot be fetched at test time: use the
    # components IDF ships.
    build esp32 "
FROM espressif/idf:$ESP_IDF_VERSION
USER root
RUN printf '{\"esp_idf\": \"%s\"}' '$ESP_IDF_VERSION' > /opt/esp-versions.json \\
    && ls /opt/esp/tools | grep -q qemu-xtensa
ENV IDF_COMPONENT_MANAGER=0 IDF_TOOLS_EXPORT_CMD=/opt/esp/idf/export.sh
ENTRYPOINT []
USER 1000:1000
WORKDIR /work" ;;
devices)
    # Talking to devices over USB and serial, tested without them: adb and
    # fastboot (and adb-shell for Python), libmtp's mtp-tools, gphoto2/
    # libgphoto2 (PTP), libimobiledevice (iOS), filesystem images for USB
    # mass storage (mtools, dosfstools, exfatprogs, e2fsprogs, sfdisk,
    # parted, gdisk, squashfs, 7z), umockdev (replays USB devices recorded
    # with umockdev-record on real hardware), socat (virtual serial ports),
    # sg3-utils, usbutils, libusb, exiftool; pyusb, pyudev, pyserial.
    build devices '
FROM forge-sandbox-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        adb fastboot android-sdk-libsparse-utils mtp-tools libmtp-dev gphoto2 libgphoto2-dev \
        libimobiledevice-utils libimobiledevice-dev usbmuxd \
        mtools dosfstools exfatprogs e2fsprogs fdisk parted gdisk squashfs-tools p7zip-full \
        umockdev gir1.2-umockdev-1.0 libumockdev-dev sg3-utils usbutils libusb-1.0-0-dev \
        socat libimage-exiftool-perl sqlite3 jq build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyusb pyudev pyserial adb-shell \
    && adb version | head -1
USER 1000:1000' ;;
raspberrypi)
    # Raspberry Pi (Raspberry Pi OS is Debian, so trixie arm64/armhf):
    # cross compilers with arm64 libraries installed multiarch (libgpiod,
    # i2c) and qemu-user to run the binaries; gpiozero with its MockFactory
    # for GPIO code; smbus2, spidev, pyserial; dtc for device-tree overlays;
    # cloud-init (schema checks for user-data); mtools/sfdisk to edit a Pi
    # image'"'"'s boot partition without mounting it; systemd-analyze; shellcheck.
    build raspberrypi '
FROM forge-sandbox-base:latest
USER root
RUN dpkg --add-architecture arm64 && apt-get update && apt-get install -y --no-install-recommends \
        gcc-aarch64-linux-gnu g++-aarch64-linux-gnu gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf \
        libc6-dev-arm64-cross libc6-dev-armhf-cross libgpiod-dev:arm64 libi2c-dev:arm64 \
        qemu-user cmake ninja-build pkg-config build-essential device-tree-compiler \
        cloud-init mtools dosfstools fdisk e2fsprogs xz-utils systemd shellcheck gpiod \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir gpiozero smbus2 spidev pyserial pytest
USER 1000:1000' ;;
ux)
    # axe-core (MPL-2.0) from the npm registry, checked against npm's
    # published sha512 integrity before Docker sees it.
    cache="$HOME/forge/toolchain-cache"
    mkdir -p "$cache"
    axe="axe-core-$AXE_VERSION.tgz"
    axe_ok() { [ -f "$1" ] && [ "sha512-$(openssl dgst -sha512 -binary "$1" | base64 -w0)" = "$AXE_INTEGRITY" ]; }
    if ! axe_ok "$cache/$axe"; then
        say "axe-core: https://registry.npmjs.org/axe-core/-/$axe"
        curl -fsSL -o "$cache/$axe.part" "https://registry.npmjs.org/axe-core/-/$axe" \
            && axe_ok "$cache/$axe.part" && mv "$cache/$axe.part" "$cache/$axe" || {
            rm -f "$cache/$axe.part"
            warn "ux: could not download $axe, or its sha512 did not match AXE_INTEGRITY"
            FAILED+=("ux"); continue; }
    fi
    ok "ux: $axe (integrity ok)"
    build ux "
FROM forge-sandbox-base:latest
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install --no-cache-dir 'playwright==$PLAYWRIGHT_VERSION' \\
    && playwright install --with-deps chromium \\
    && apt-get install -y --no-install-recommends fonts-liberation fonts-noto-color-emoji \\
    && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /opt/ms-playwright
COPY $axe /tmp/axe.tgz
RUN mkdir -p /opt/axe && tar -xzf /tmp/axe.tgz -C /tmp package/axe.min.js package/LICENSE \\
    && mv /tmp/package/axe.min.js /tmp/package/LICENSE /opt/axe/ && rm -rf /tmp/axe.tgz /tmp/package \\
    && chmod a+r /opt/axe/*
ENV FORGE_AXE=/opt/axe/axe.min.js
USER 1000:1000" "$cache/$axe" ;;
esac
done

# What each image's extra tools can do, for forge's coder prompts
# (~/forge/toolchain-help/<name>.txt is added to that toolchain's notes).
mkdir -p "$HOME/forge/toolchain-help"
if [[ " ${BUILT[*]} " == *" re "* ]]; then
    $DOCKER run --rm --network none forge-sandbox-re:latest cat /opt/rea-help.txt \
        > "$HOME/forge/toolchain-help/re.txt" 2>/dev/null && ok "saved REA's command list for forge"
fi

say "Smoke test (no network, read-only, uid 1000 - as forge runs them)"
for t in "${WANT[@]}"; do
    [[ " ${BUILT[*]} " == *" $t "* ]] || continue
    case "$t" in
        godot) cmd='xvfb-run -a godot --headless --version' ;;
        c-sdl) cmd='cmake --version | head -1 && sdl2-config --version' ;;
        rust) cmd='cargo --version' ;;
        lua-love) cmd='luajit -v && lua5.4 -v && command -v love' ;;
        dotnet) cmd='DOTNET_CLI_HOME=/tmp/home dotnet --version' ;;
        java) cmd='java -version 2>&1 | head -1' ;;
        re) cmd='rizin -v | head -1 && test -x /opt/ghidra/support/analyzeHeadless && echo ghidra ok && rea --help >/dev/null 2>&1 && echo rea ok' ;;
        sbox) cmd='DOTNET_CLI_HOME=/tmp/home dotnet --version && test -d /opt/forge-nuget/mstest.testframework && echo mstest cached' ;;
        gmod) cmd='(glualint --version 2>/dev/null || echo glualint ok) && python3 --version' ;;
        fo4) cmd='mono --version | head -1 && wine --version && DOTNET_CLI_HOME=/tmp/home dotnet --version && test -f /opt/wineprefix/system.reg && test -d /opt/forge-nuget/mutagen.bethesda.fallout4 && echo mutagen cached' ;;
        blender) cmd='blender -b --factory-startup -noaudio --version | head -1' ;;
        shell) cmd='shellcheck --version | sed -n 2p && bats --version && systemd-analyze --version | head -1' ;;
        node) cmd='node --version && tsc --version && vitest --version && cat /opt/node-tools/versions.json' ;;
        go) cmd='GOCACHE=/tmp/gocache go version && GOCACHE=/tmp/gocache go env GOROOT' ;;
        homeassistant) cmd='cat /opt/ha-versions.json && esphome version' ;;
        c-cpp) cmd='gcc --version | head -1 && clang --version | head -1 && cmake --version | head -1' ;;
        sql) cmd='postgres --version && mariadbd --version && sqlite3 --version | cut -c1-20 && sqlfluff --version' ;;
        mcu) cmd='arm-none-eabi-gcc --version | head -1 && avr-gcc --version | head -1 && picotool version && micropython -c "import sys; print(sys.version)" && cat /opt/mcu-versions.json' ;;
        esp32) cmd='. /opt/esp/idf/export.sh >/dev/null 2>&1 && idf.py --version && ls /opt/esp/tools | grep -c qemu && cat /opt/esp-versions.json' ;;
        devices) cmd='adb version | head -1 && gphoto2 --version | sed -n 1p && mtp-detect --help 2>&1 | head -1; umockdev-run --version' ;;
        raspberrypi) cmd='aarch64-linux-gnu-gcc --version | head -1 && dtc --version && python3 -c "import gpiozero, smbus2, serial; print(gpiozero.__name__)"' ;;
        minecraft) cmd='java -version 2>&1 | head -1 && test -f /opt/mc/server/paper.jar && ls /opt/mc/lib/compile | grep -m1 paper-api && cat /opt/mc/versions.json' ;;
        ux) cmd='python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=[\"--disable-dev-shm-usage\"])
    pg = b.new_page(); pg.set_content(\"<html lang=en><title>t</title><main><h1>ok</h1></main></html>\")
    pg.add_script_tag(path=\"/opt/axe/axe.min.js\")
    r = pg.evaluate(\"() => axe.run().then(r => r.violations.length)\")
    print(\"chromium\", b.version, \"axe\", pg.evaluate(\"axe.version\"), \"violations\", r); b.close()
"' ;;
        *) cmd='true' ;;
    esac
    if out=$($DOCKER run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=256m \
              --user 1000:1000 -e HOME=/tmp/home "forge-sandbox-$t:latest" sh -c "$cmd" 2>&1); then
        ok "$t: $(echo "$out" | tr '\n' ' ' | cut -c1-100)"
    else
        warn "$t: smoke test failed: $out"
    fi
done

cat <<'EOF'

Done. forge picks the image from a spec's `Toolchain:` line. Check a game
toolchain end to end (its sandbox, mounts and limits, as a run uses them):

  forge selftest sbox      # or gmod, fo4, blender, minecraft, shell, node,
                           # go, homeassistant

Optional per-toolchain limits go in ~/forge/config.toml, e.g.:

  [sandbox.toolchains.rust]
  memory = "6g"
  timeout = 1200

Rust crates, npm packages (node) and Go modules are fetched for each run by
a separate download-only container (cargo vendor / npm --ignore-scripts /
go mod download - no package code runs there) and mounted read-only into
the test sandbox.
EOF

if [ ${#FAILED[@]} -gt 0 ]; then
    warn "Not built: ${FAILED[*]} - the lines above (and $LOGDIR/<name>.log) say why."
    warn "Retry just those:  ./install-forge-toolchains.sh ${FAILED[*]}"
    exit 1
fi
ok "Built: ${BUILT[*]}"
