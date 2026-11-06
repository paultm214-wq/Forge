#!/usr/bin/env bash
#
# sbox-probe2.sh - can s&box's engine start for unit tests on Linux?
#
# Run on Gemini after sbox-probe.sh (it reuses ~/sbox-probe/project and the
# packages that probe restored):
#     bash sbox-probe2.sh [path to the s&box install]
#
# The first probe showed TestAppSystem stops at a Windows-only check: it
# wants FACEPUNCH_ENGINE/bin/win64/engine2.dll. Everything after that check
# is cross-platform in s&box's source (native libraries load from
# bin/linuxsteamrt64 on Linux). So, inside the container only, this builds an
# engine folder of symlinks to the install plus an EMPTY placeholder file at
# bin/win64/engine2.dll, and points FACEPUNCH_ENGINE at it. The install
# itself stays read-only and unchanged. Writable folders the engine may use
# (logs, config, .source2, download) are fresh copies in the container.
#
# It also lists native libraries the Linux engine needs that the .NET SDK
# container lacks (ldd "not found"), and tries the engine tests with and
# without LD_LIBRARY_PATH. Results append to ~/sbox-probe/report2.txt.
#
set -uo pipefail
SBOX="${1:-$HOME/.steam/debian-installation/steamapps/common/sbox}"
SBOX="$(cd "$SBOX" 2>/dev/null && pwd)" || { echo "s&box install not found"; exit 1; }
OUT="$HOME/sbox-probe"
P="$OUT/project"
IMG="${SDK_IMAGE:-mcr.microsoft.com/dotnet/sdk:10.0}"
[ -f "$P/Engine/ProbeEngineTests.csproj" ] || { echo "Run sbox-probe.sh first."; exit 1; }
R="$OUT/report2.txt"
: > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }

# Runs inside the container: build the engine folder, then the given command.
SETUP='
set -u
E=/tmp/engine
mkdir -p "$E/bin/win64"
for x in /sbox/* /sbox/.[!.]*; do
    [ -e "$x" ] || continue
    n=$(basename "$x")
    case "$n" in
        bin) ;;
        logs|.source2|download) mkdir -p "$E/$n" ;;
        config) cp -r "$x" "$E/$n" ;;
        *) ln -s "$x" "$E/$n" ;;
    esac
done
for x in /sbox/bin/*; do ln -s "$x" "$E/bin/$(basename "$x")"; done
: > "$E/bin/win64/engine2.dll"     # placeholder for the Windows-only check
'

run() {  # label, network, extra env (or ""), command
    local label="$1" net="$2" envs="$3" cmd="$4"
    sec "$label"
    local out rc
    out="$(timeout 900 docker run --rm --network "$net" --user "$(id -u):$(id -g)" \
        -v "$SBOX":/sbox:ro -v "$P":/work -v "$OUT/nuget":/nuget -v "$OUT/home":/home/probe \
        -e HOME=/home/probe -e DOTNET_CLI_HOME=/home/probe -e NUGET_PACKAGES=/nuget \
        -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 $envs \
        -w /work/Engine "$IMG" bash -c "$SETUP
$cmd" 2>&1)"
    rc=$?
    printf '%s\n' "$out" | tail -70 | tee -a "$R" >/dev/null
    log "exit code: $rc"
}

log "date: $(date -Is)  install: $SBOX  image: $IMG"

run "native libraries the container lacks (ldd, bin/linuxsteamrt64)" none "" '
cd /sbox/bin/linuxsteamrt64
for f in *.so*; do LD_LIBRARY_PATH=/sbox/bin/linuxsteamrt64 ldd "$f" 2>/dev/null | grep "not found" | sed "s/^/$f: /"; done | sort -u | head -60
echo "libengine2.so needs:"; LD_LIBRARY_PATH=/sbox/bin/linuxsteamrt64 ldd libengine2.so | sed "s/^/  /" | head -60'

run "engine tests, placeholder engine folder" none "-e FACEPUNCH_ENGINE=/tmp/engine" \
    'dotnet test --no-restore --blame-hang-timeout 180s --logger "console;verbosity=normal"'

run "engine tests, placeholder folder + LD_LIBRARY_PATH" none \
    "-e FACEPUNCH_ENGINE=/tmp/engine -e LD_LIBRARY_PATH=/sbox/bin/linuxsteamrt64" \
    'dotnet test --no-restore --blame-hang-timeout 180s --logger "console;verbosity=normal"; echo "--- engine logs:"; ls -la /tmp/engine/logs; tail -40 /tmp/engine/logs/* 2>/dev/null'

sec "summary"
grep -E "not found|Passed |Failed |Total tests|exit code|Exception|DllNotFound|Unable to load|cannot open shared" "$R" | sort -u | head -50 | sed 's/^/  /' | tee -a "$R" >/dev/null
echo
echo "Report: $R"
