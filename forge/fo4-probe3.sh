#!/usr/bin/env bash
#
# fo4-probe3.sh - why does the Papyrus compiler fail silently under Mono?
# Run on Gemini after fo4-probe2.sh (it reuses ~/fo4-probe/papyrus):
#     bash fo4-probe3.sh [path to the Fallout 4 install]
#
# Round 2: the compiler found Base's 7,854 sources and the flags file, then
# failed both scripts with no message. Likely cause: it was written for
# Windows (case-insensitive file names, backslash paths). This compiles the
# same scripts, plus a bare one, four ways (all with no network):
#   a. Mono 6.12, full output, Mono debug output on
#   b. Mono 6.12 with an all-lowercase mirror of the sources (symlinks)
#   c. Mono 5.20 with MONO_IOMAP=all (case/backslash mapping, gone in Mono 6)
#   d. Mono 5.20 with MONO_IOMAP=all and the lowercase mirror
# Report: ~/fo4-probe/report3.txt
#
set -uo pipefail
FO4="${1:-$HOME/.steam/debian-installation/steamapps/common/Fallout 4}"
FO4="$(cd "$FO4" 2>/dev/null && pwd)" || { echo "Fallout 4 install not found"; exit 1; }
OUT="$HOME/fo4-probe"
W="$OUT/papyrus"
[ -d "$W/Base" ] || { echo "Run fo4-probe2.sh first."; exit 1; }
R="$OUT/report3.txt"; : > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }
log "date: $(date -Is)  install: $FO4"
PC="$(find "$FO4" -maxdepth 3 -iname 'PapyrusCompiler.exe' | head -1)"
PCDIR="$(dirname "$PC")"
log "compiler folder: $(ls "$PCDIR" | tr '\n' ' ')"
FLG="$(find "$W/Base" -iname 'Institute_Papyrus_Flags.flg' | head -1)"
log "flags file: $FLG"
log "case collisions in Base (names differing only by case): $(cd "$W/Base" && find . -type f | tr 'A-Z' 'a-z' | sort | uniq -d | wc -l)"
log "sample names: $(cd "$W/Base" && ls | head -8 | tr '\n' ' ')"

sec "scripts"
rm -rf "$W/src" "$W/pex"; mkdir -p "$W/src/ForgeProbe/Quests"
cat > "$W/src/ForgeProbeBare.psc" <<'EOF'
ScriptName ForgeProbeBare

Int Function Twice(Int x) Global
    Return x * 2
EndFunction
EOF
cat > "$W/src/ForgeProbeScript.psc" <<'EOF'
ScriptName ForgeProbeScript extends Quest

Int Property Counter = 0 Auto

Event OnInit()
    Counter += 1
    Debug.Trace("forge probe: OnInit " + Counter)
EndEvent
EOF
cat > "$W/src/ForgeProbe/Quests/TrackerScript.psc" <<'EOF'
ScriptName ForgeProbe:Quests:TrackerScript extends Quest

GlobalVariable Property ForgeProbeCount Auto

Function Bump(Int amount)
    ForgeProbeCount.SetValue(ForgeProbeCount.GetValue() + amount)
EndFunction
EOF
log "ForgeProbeBare.psc (no extends), ForgeProbeScript.psc (extends Quest), ForgeProbe/Quests/TrackerScript.psc (namespace)"

sec "lowercase mirrors (symlinks, nothing copied)"
python3 - "$W/Base" "$W/base-lower" /base "$W/src" "$W/src-lower" /work/src <<'EOF'
import os, sys, shutil
def mirror(src, dst, target_root):
    shutil.rmtree(dst, ignore_errors=True)
    n = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        low = "" if rel == "." else rel.lower()
        os.makedirs(os.path.join(dst, low), exist_ok=True)
        for f in files:
            link = os.path.join(dst, low, f.lower())
            if not os.path.lexists(link):
                os.symlink(os.path.join(target_root, "" if rel == "." else rel, f), link)
                n += 1
    return n
args = sys.argv[1:]
print("base-lower:", mirror(args[0], args[1], args[2]), "links;", "src-lower:", mirror(args[3], args[4], args[5]), "links")
EOF
for img in mono:6.12 mono:5.20; do docker pull -q "$img" >/dev/null 2>&1 || log "could not pull $img"; done

compile() {  # label, image, extra env, import list, file
    local label="$1" img="$2" envs="$3" imports="$4" f="$5"
    rm -rf "$W/pex"; mkdir -p "$W/pex"
    local out
    out="$(timeout 600 docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp $envs \
        -v "$PCDIR":/pc:ro -v "$W/Base":/base:ro -v "$W":/work -w /work/src "$img" \
        mono "/pc/$(basename "$PC")" "$f" "-f=$(basename "$FLG")" "-i=$imports" -o=/work/pex 2>&1)"
    local rc=$?
    log "--- [$label] $f  (exit $rc)"
    printf '%s\n' "$out" | grep -vE "^Copyright|^Papyrus Compiler Version|^Starting 1 compile|^Batch compile|^\s*$" | head -25 | tee -a "$R" >/dev/null
    log "    pex: $(cd "$W/pex" && find . -iname '*.pex' -printf '%p (%s bytes) ' 2>/dev/null)"
}
FILES="ForgeProbeBare.psc ForgeProbeScript.psc ForgeProbe/Quests/TrackerScript.psc"
for f in $FILES; do compile "a. mono 6.12 debug" mono:6.12 "-e MONO_ENV_OPTIONS=--debug" "/work/src;/base" "$f"; done
for f in $FILES; do compile "b. mono 6.12 lowercase" mono:6.12 "-e MONO_ENV_OPTIONS=--debug" "/work/src;/work/src-lower;/base;/work/base-lower" "$f"; done
for f in $FILES; do compile "c. mono 5.20 IOMAP" mono:5.20 "-e MONO_IOMAP=all -e MONO_ENV_OPTIONS=--debug" "/work/src;/base" "$f"; done
for f in $FILES; do compile "d. mono 5.20 IOMAP + lowercase" mono:5.20 "-e MONO_IOMAP=all -e MONO_ENV_OPTIONS=--debug" "/work/src;/work/src-lower;/base;/work/base-lower" "$f"; done

sec "summary"
grep -E "^--- |pex: " "$R" | sed 's/^/  /' | tee -a "$R" >/dev/null
echo
echo "Report: $R"
