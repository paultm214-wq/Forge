#!/usr/bin/env bash
#
# fo4-probe4.sh - Papyrus, round 4. Run on Gemini after fo4-probe3.sh:
#     bash fo4-probe4.sh [path to the Fallout 4 install]
#
# Round 3: every compile failed silently, even a script that needs no other
# files - so case-sensitive names were not the cause. Fallout 4's compiler
# works in two steps: PapyrusCompiler.exe (.NET, runs under Mono) turns a
# .psc into Papyrus assembly (.pas), then runs PapyrusAssembler.exe to make
# the .pex. If the assembler is a native Windows program, Mono cannot start
# it and the compile fails with no message. This round:
#   0. Shows what each file in the compiler folder is, and the compiler's
#      own option list.
#   1. Uses a copy of the compiler folder (the install is not changed) with a
#      stand-in "PapyrusAssembler.exe": a small script that records how it
#      was called and saves a copy of the .pas it was given.
#   2. Builds a local image "forge-papyrus-probe" (Ubuntu 24.04 + Mono +
#      Wine; about 1.5 GB, one-time).
#   3. Makes the stand-in run the REAL PapyrusAssembler.exe under Wine.
#      Command-line programs run under Wine without a display. No network
#      at compile time.
# Report: ~/fo4-probe/report4.txt
#
set -uo pipefail
FO4="${1:-$HOME/.steam/debian-installation/steamapps/common/Fallout 4}"
FO4="$(cd "$FO4" 2>/dev/null && pwd)" || { echo "Fallout 4 install not found"; exit 1; }
OUT="$HOME/fo4-probe"
W="$OUT/papyrus"
[ -f "$W/src/ForgeProbeBare.psc" ] || { echo "Run fo4-probe3.sh first."; exit 1; }
R="$OUT/report4.txt"; : > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }
indent() { sed "s/^/${1:-  }/" | tee -a "$R" >/dev/null; }
NOISE='^Copyright|runtime version supported|Using default runtime|^Papyrus Compiler Version|^Starting 1 compile|^Batch compile|^\s*$'
log "date: $(date -Is)  install: $FO4"
PC="$(find "$FO4" -maxdepth 3 -iname 'PapyrusCompiler.exe' | head -1)"
[ -n "$PC" ] || { log "PapyrusCompiler.exe not found"; exit 1; }
PCDIR="$(dirname "$PC")"
FLG="$(basename "$(find "$W/Base" -iname 'Institute_Papyrus_Flags.flg' | head -1)")"

sec "0. compiler folder"
for f in "$PCDIR"/*; do
    if command -v file >/dev/null; then kind="$(file -b "$f" | cut -c1-90)"; else kind="$(head -c2 "$f" | od -An -c | tr -s ' ')"; fi
    log "  $(basename "$f"): $kind"
done
log "ScriptCompile.bat:"; tr -d '\r' < "$PCDIR/ScriptCompile.bat" 2>/dev/null | head -10 | indent
log "PapyrusCompiler.exe.config:"; tr -d '\r' < "$PCDIR/PapyrusCompiler.exe.config" 2>/dev/null | head -20 | indent
log "compiler options (its own help):"
timeout 120 docker run --rm --network none -v "$PCDIR":/pc:ro mono:6.12 mono /pc/PapyrusCompiler.exe -? 2>&1 \
    | grep -vE "$NOISE" | head -40 | indent

# Writable copy of the compiler folder; the compiler looks for the assembler
# next to itself.
SHIM="$W/pc-shim"
rm -rf "$SHIM"; mkdir -p "$SHIM"
for f in "$PCDIR"/*; do
    [ "$(basename "$f")" = "PapyrusAssembler.exe" ] || cp "$f" "$SHIM/"
done

# Shared top of both stand-ins: record the call, copy any .pas it can see.
SHIM_HEAD='#!/bin/bash
LOG=/work/asm-calls.log
{ echo "called in: $(pwd)"; for a in "$@"; do echo "  arg: $a"; done; } >> "$LOG"
mkdir -p /work/pas-copy
for a in "$@"; do
    for p in "$a" "$a.pas" "$(pwd)/$a" "$(pwd)/$a.pas"; do
        case "$p" in *.pas|*.PAS) [ -f "$p" ] && cp "$p" /work/pas-copy/ ;; esac
    done
done
cp ./*.pas /work/pas-copy/ 2>/dev/null
'

sec "1. stand-in assembler that only records its call (Mono 6.12)"
printf '%s\nexit 1\n' "$SHIM_HEAD" > "$SHIM/PapyrusAssembler.exe"
chmod +x "$SHIM/PapyrusAssembler.exe"
rm -rf "$W/pex" "$W/asm-calls.log" "$W/pas-copy"; mkdir -p "$W/pex"
out="$(timeout 300 docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$SHIM":/pc:ro -v "$W/Base":/base:ro -v "$W":/work -w /work/src mono:6.12 \
    mono /pc/PapyrusCompiler.exe ForgeProbeBare.psc "-f=$FLG" "-i=/work/src;/base" -o=/work/pex 2>&1)"
log "exit code: $?"
printf '%s\n' "$out" | grep -vE "$NOISE" | head -15 | indent
if [ -s "$W/asm-calls.log" ]; then log "assembler was called:"; indent < "$W/asm-calls.log"
else log "assembler was NOT called (the failure is in the compile step itself)"; fi
log "files in pex/: $(ls -A "$W/pex" | tr '\n' ' ')"
PAS="$(find "$W/pas-copy" "$W/pex" "$W/src" -maxdepth 1 -iname '*.pas' 2>/dev/null | head -1)"
if [ -n "$PAS" ]; then log ".pas produced ($PAS), first lines:"; head -15 "$PAS" | indent "    "
else log "no .pas found"; fi

sec "2. build image forge-papyrus-probe (Ubuntu 24.04 + Mono + Wine)"
out="$(docker build -t forge-papyrus-probe - 2>&1 <<'EOF'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates mono-runtime mono-devel \
      wine wine64 wine32:i386 \
 && rm -rf /var/lib/apt/lists/*
EOF
)"
rc=$?
printf '%s\n' "$out" | grep -E "E: |Unable to locate|ERROR|error:|naming to" | tail -8 | indent
log "build exit code: $rc"
docker run --rm forge-papyrus-probe sh -c 'mono --version | head -1; wine --version 2>&1 | head -1' 2>&1 | indent

sec "3. real assembler under Wine"
# Unix paths in the arguments become Z:\... paths so the Windows program
# finds them; Wine maps Z: to /.
printf '%s\n' "$SHIM_HEAD" > "$SHIM/PapyrusAssembler.exe"
cat >> "$SHIM/PapyrusAssembler.exe" <<'EOF'
export WINEPREFIX=/tmp/wineprefix WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml="
args=()
for a in "$@"; do
    case "$a" in /*) args+=("$(winepath -w "$a" 2>/dev/null || echo "$a")") ;; *) args+=("$a") ;; esac
done
echo "  wine args: ${args[*]}" >> "$LOG"
wine /pc-real/PapyrusAssembler.exe "${args[@]}" > >(tee -a "$LOG") 2>&1
rc=$?
echo "  wine exit: $rc" >> "$LOG"
exit $rc
EOF
chmod +x "$SHIM/PapyrusAssembler.exe"
for f in ForgeProbeBare.psc ForgeProbeScript.psc ForgeProbe/Quests/TrackerScript.psc; do
    rm -rf "$W/pex" "$W/asm-calls.log" "$W/pas-copy"; mkdir -p "$W/pex"
    out="$(timeout 600 docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$SHIM":/pc:ro -v "$PCDIR":/pc-real:ro -v "$W/Base":/base:ro -v "$W":/work -w /work/src \
        forge-papyrus-probe mono /pc/PapyrusCompiler.exe "$f" "-f=$FLG" \
        "-i=/work/src;/base" -o=/work/pex 2>&1)"
    log "--- $f (exit $?)"
    printf '%s\n' "$out" | grep -vE "$NOISE" | head -12 | indent
    log "  assembler log:"; head -25 "$W/asm-calls.log" 2>/dev/null | indent "    "
    log "  pex: $(cd "$W/pex" && find . -iname '*.pex' -printf '%p (%s bytes) ' 2>/dev/null)"
done

sec "summary"
grep -E "^--- |  pex: |assembler was" "$R" | indent
echo
echo "Report: $R"
