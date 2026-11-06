#!/usr/bin/env bash
#
# fo4-probe5.sh - Papyrus, round 5. Run on Gemini after fo4-probe4.sh:
#     bash fo4-probe5.sh [path to the Fallout 4 install]
#
# Round 4 confirmed the cause: the compiler (.NET) works under Mono and
# writes a correct .pas, then calls PapyrusAssembler.exe - a native 32-bit
# Windows program - which Mono cannot run. Under Wine it got as far as
# "'/tmp' is not owned by you": Wine will only create its settings folder
# (the "prefix") inside a folder the user owns. This round:
#   1. Creates the Wine prefix once, in ~/fo4-probe/wine (owned by you), with
#      no network. forge would keep one like this and reuse it.
#   2. Runs the real assembler on round 4's ForgeProbeBare.pas directly.
#   3. Compiles all three test scripts through the compiler, end to end.
#   4. Reads the .pex headers back (magic number and script name).
# Uses the forge-papyrus-probe image built by round 4.
# Report: ~/fo4-probe/report5.txt
#
set -uo pipefail
FO4="${1:-$HOME/.steam/debian-installation/steamapps/common/Fallout 4}"
FO4="$(cd "$FO4" 2>/dev/null && pwd)" || { echo "Fallout 4 install not found"; exit 1; }
OUT="$HOME/fo4-probe"
W="$OUT/papyrus"
SHIM="$W/pc-shim"
[ -x "$SHIM/PapyrusAssembler.exe" ] || { echo "Run fo4-probe4.sh first."; exit 1; }
docker image inspect forge-papyrus-probe >/dev/null 2>&1 || { echo "Image forge-papyrus-probe missing; run fo4-probe4.sh first."; exit 1; }
R="$OUT/report5.txt"; : > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }
indent() { sed "s/^/${1:-  }/" | tee -a "$R" >/dev/null; }
NOISE='^Copyright|runtime version supported|Using default runtime|^Papyrus Compiler Version|^Starting 1 compile|^Batch compile|^\s*$'
log "date: $(date -Is)  install: $FO4"
PCDIR="$(dirname "$(find "$FO4" -maxdepth 3 -iname 'PapyrusCompiler.exe' | head -1)")"
FLG="$(basename "$(find "$W/Base" -iname 'Institute_Papyrus_Flags.flg' | head -1)")"

# /wine is a folder you own: HOME and the Wine prefix both live there.
WINE_DIR="$OUT/wine"
mkdir -p "$WINE_DIR/home"
RUN=(docker run --rm --network none --user "$(id -u):$(id -g)"
     -e HOME=/wine/home -e WINEPREFIX=/wine/prefix -e WINEDEBUG=-all
     -e WINEDLLOVERRIDES="mscoree,mshtml=" -v "$WINE_DIR":/wine)

sec "1. create the Wine prefix (one time, no network)"
if [ -f "$WINE_DIR/prefix/system.reg" ]; then
    log "prefix already exists: $WINE_DIR/prefix"
else
    start=$(date +%s)
    timeout 300 "${RUN[@]}" forge-papyrus-probe wineboot -i 2>&1 | grep -v '^\s*$' | tail -8 | indent
    log "wineboot exit: ${PIPESTATUS[0]}  seconds: $(( $(date +%s) - start ))"
fi
log "prefix size: $(du -sh "$WINE_DIR/prefix" 2>/dev/null | cut -f1)"
log "drives: $(ls "$WINE_DIR/prefix/dosdevices" 2>/dev/null | tr '\n' ' ')"

sec "2. assembler alone on round 4's ForgeProbeBare.pas"
PAS="$W/pas-copy/ForgeProbeBare.pas"
if [ -f "$PAS" ]; then
    rm -rf "$W/asm-direct"; mkdir -p "$W/asm-direct"; cp "$PAS" "$W/asm-direct/"
    timeout 300 "${RUN[@]}" -v "$PCDIR":/pc-real:ro -v "$W/asm-direct":/asm -w /asm \
        forge-papyrus-probe wine /pc-real/PapyrusAssembler.exe ForgeProbeBare 2>&1 | head -20 | indent
    log "exit: ${PIPESTATUS[0]}"
    log "files: $(cd "$W/asm-direct" && find . -type f -printf '%f (%s bytes) ')"
else
    log "no .pas from round 4; skipped"
fi

sec "3. full compile: Mono compiler + assembler under Wine"
# Same stand-in as round 4, with the prefix settings taken from the
# environment and a listing of the folder it was called in.
cat > "$SHIM/PapyrusAssembler.exe" <<'EOF'
#!/bin/bash
LOG=/work/asm-calls.log
{ echo "called in: $(pwd)"; for a in "$@"; do echo "  arg: $a"; done
  echo "  folder has: $(ls -A | tr '\n' ' ')"; } >> "$LOG"
wine /pc-real/PapyrusAssembler.exe "$@" > >(tee -a "$LOG") 2>&1
rc=$?
echo "  wine exit: $rc" >> "$LOG"
exit $rc
EOF
chmod +x "$SHIM/PapyrusAssembler.exe"
for f in ForgeProbeBare.psc ForgeProbeScript.psc ForgeProbe/Quests/TrackerScript.psc; do
    rm -rf "$W/pex" "$W/asm-calls.log"; mkdir -p "$W/pex"
    start=$(date +%s)
    out="$(timeout 600 "${RUN[@]}" -v "$SHIM":/pc:ro -v "$PCDIR":/pc-real:ro -v "$W/Base":/base:ro \
        -v "$W":/work -w /work/src forge-papyrus-probe \
        mono /pc/PapyrusCompiler.exe "$f" "-f=$FLG" "-i=/work/src;/base" -o=/work/pex 2>&1)"
    log "--- $f (exit $?, $(( $(date +%s) - start )) s)"
    printf '%s\n' "$out" | grep -vE "$NOISE" | head -12 | indent
    log "  assembler log:"; head -25 "$W/asm-calls.log" 2>/dev/null | indent "    "
    log "  pex: $(cd "$W/pex" && find . -iname '*.pex' -printf '%p (%s bytes) ' 2>/dev/null)"
    mkdir -p "$W/pex-ok"; (cd "$W/pex" && find . -iname '*.pex' -exec cp --parents {} "$W/pex-ok/" \;)
done

sec "4. .pex headers"
# A Fallout 4 .pex starts with the magic FA 57 C0 DE (stored little-endian:
# DE C0 57 FA), then major/minor version, game id, compile time, and the
# source, user and computer names as length-prefixed strings.
python3 - "$W/pex-ok" <<'EOF' 2>&1 | indent
import os, struct, sys
root = sys.argv[1]
found = False
for dp, _, fs in os.walk(root):
    for f in fs:
        if not f.lower().endswith(".pex"):
            continue
        found = True
        b = open(os.path.join(dp, f), "rb").read()
        magic = struct.unpack_from("<I", b, 0)[0]
        major, minor = b[4], b[5]
        game = struct.unpack_from("<H", b, 6)[0]
        pos = 16
        strs = []
        for _ in range(3):
            n = struct.unpack_from("<H", b, pos)[0]; pos += 2
            strs.append(b[pos:pos + n].decode("latin-1")); pos += n
        print(f"{os.path.relpath(os.path.join(dp, f), root)}: {len(b)} bytes, magic {magic:#010x}"
              f" ({'ok' if magic == 0xFA57C0DE else 'UNEXPECTED'}), version {major}.{minor},"
              f" game {game}, source {strs[0]!r}")
if not found:
    print("no .pex files")
EOF

sec "summary"
grep -E "^--- |  pex: |wineboot exit|magic" "$R" | indent
echo
echo "Report: $R"
