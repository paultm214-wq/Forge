#!/usr/bin/env bash
#
# sbox-probe3.sh - get s&box's engine past SDL_Init in a container.
#
# Run on Gemini after sbox-probe.sh and sbox-probe2.sh:
#     bash sbox-probe3.sh [path to the s&box install]
#
# Probe 2 got the engine loading (libengine2.so and its own libraries are
# found) and stopped at "SDL_Init failed: x11 not available": the .NET SDK
# container has no display and none of the X11, GLib or Qt-support libraries.
# Nothing needs installing on Gemini itself. This builds a local Docker image
# "forge-sbox-probe" = the .NET 10 SDK image + those libraries + Xvfb, then
# runs the engine tests three ways, all with no network:
#   a. SDL's offscreen video driver
#   b. SDL's dummy video driver
#   c. under Xvfb (a virtual X display)
# Results: ~/sbox-probe/report3.txt - send me that file.
#
set -uo pipefail
SBOX="${1:-$HOME/.steam/debian-installation/steamapps/common/sbox}"
SBOX="$(cd "$SBOX" 2>/dev/null && pwd)" || { echo "s&box install not found"; exit 1; }
OUT="$HOME/sbox-probe"
P="$OUT/project"
BASE="${SDK_IMAGE:-mcr.microsoft.com/dotnet/sdk:10.0}"
IMG=forge-sbox-probe
[ -f "$P/Engine/ProbeEngineTests.csproj" ] || { echo "Run sbox-probe.sh first."; exit 1; }
R="$OUT/report3.txt"; : > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }

log "date: $(date -Is)  install: $SBOX  base image: $BASE"

sec "build image $IMG"
out="$(docker build -t "$IMG" - 2>&1 <<EOF
FROM $BASE
RUN apt-get update \\
 && (apt-get install -y --no-install-recommends libglib2.0-0t64 libpng16-16t64 \\
     || apt-get install -y --no-install-recommends libglib2.0-0 libpng16-16) \\
 && apt-get install -y --no-install-recommends \\
      libpcre2-16-0 libdbus-1-3 libharfbuzz0b libfontconfig1 libfreetype6 \\
      libx11-6 libx11-xcb1 libxext6 libxcursor1 libxrandr2 libxi6 libxfixes3 libxss1 \\
      libxkbcommon0 libxkbcommon-x11-0 libwayland-client0 libdecor-0-0 \\
      libgl1 libegl1 libvulkan1 libasound2t64 libpulse0 \\
      xvfb xauth \\
 || apt-get install -y --no-install-recommends \\
      libpcre2-16-0 libdbus-1-3 libharfbuzz0b libfontconfig1 libfreetype6 \\
      libx11-6 libx11-xcb1 libxext6 libxcursor1 libxrandr2 libxi6 libxfixes3 libxss1 \\
      libxkbcommon0 libxkbcommon-x11-0 libgl1 libegl1 libvulkan1 xvfb xauth \\
 && rm -rf /var/lib/apt/lists/*
EOF
)"
rc=$?
printf '%s\n' "$out" | grep -E "^#[0-9]+ (ERROR|DONE)|E: |Unable to locate|naming to" | tail -15 | tee -a "$R" >/dev/null
log "build exit code: $rc"
[ $rc = 0 ] || { log "image build failed - see above"; echo "Report: $R"; exit 1; }

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
AFTER='
rc=$?
echo "--- engine logs:"; for f in /tmp/engine/logs/*; do echo "## $f"; tail -60 "$f"; done
exit $rc'

run() {  # label, extra docker env args, test command
    sec "$1"
    local out
    out="$(timeout 900 docker run --rm --network none --user "$(id -u):$(id -g)" \
        -v "$SBOX":/sbox:ro -v "$P":/work -v "$OUT/nuget":/nuget -v "$OUT/home":/home/probe \
        -e HOME=/home/probe -e DOTNET_CLI_HOME=/home/probe -e NUGET_PACKAGES=/nuget \
        -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
        -e FACEPUNCH_ENGINE=/tmp/engine -e LD_LIBRARY_PATH=/sbox/bin/linuxsteamrt64 $2 \
        -w /work/Engine "$IMG" bash -c "$SETUP
$3
$AFTER" 2>&1)"
    log "exit code: $?"
    printf '%s\n' "$out" | grep -vE "^\s*$" | tail -70 | tee -a "$R" >/dev/null
}

sec "libraries still missing in $IMG"
docker run --rm --network none -e LD_LIBRARY_PATH=/sbox/bin/linuxsteamrt64 -v "$SBOX":/sbox:ro "$IMG" bash -c '
cd /sbox/bin/linuxsteamrt64
for f in *.so*; do ldd "$f" 2>/dev/null | grep "not found" | sed "s/^/$f: /"; done | sort -u | head -40
echo "(end of list)"' 2>&1 | tee -a "$R" >/dev/null

TEST='dotnet test --no-restore --blame-hang-timeout 180s --logger "console;verbosity=normal"'
run "a. SDL offscreen driver" "-e SDL_VIDEO_DRIVER=offscreen -e SDL_VIDEODRIVER=offscreen -e SDL_AUDIO_DRIVER=dummy -e SDL_AUDIODRIVER=dummy" "$TEST"
run "b. SDL dummy driver" "-e SDL_VIDEO_DRIVER=dummy -e SDL_VIDEODRIVER=dummy -e SDL_AUDIO_DRIVER=dummy -e SDL_AUDIODRIVER=dummy" "$TEST"
run "c. Xvfb virtual display" "-e SDL_AUDIO_DRIVER=dummy -e SDL_AUDIODRIVER=dummy" "xvfb-run -a -s '-screen 0 1280x720x24' $TEST"

sec "summary"
grep -E "^=== |Passed |Failed |Total tests|Test Run|exit code|SDL_Init|Exception|not found" "$R" | grep -v "^  " | head -60 | sed 's/^/  /' | tee -a "$R" >/dev/null
echo
echo "Report: $R"
