#!/usr/bin/env bash
#
# fo4-probe.sh - what can forge build and check for Fallout 4 on Linux?
#
# Run on Gemini as your normal user (needs Docker):
#     bash fo4-probe.sh [path to the Fallout 4 install]
# Default: ~/.steam/debian-installation/steamapps/common/Fallout 4
#
# The install is mounted READ-ONLY; everything is written to ~/fo4-probe/.
#   1. Layout: masters, archives, the Creation Kit, the Papyrus compiler and
#      script sources.
#   2. Mutagen (the GPL-3.0 .NET plugin library, pinned to 0.54.4) in a .NET
#      10 container: read Fallout4.esm, create ForgeProbe.esp with a new misc
#      item, a new quest and an override of one Fallout4.esm misc item, write
#      it, read it back. Then the same again with no network (as forge's
#      sandbox runs). ForgeProbe.esp is left in ~/fo4-probe/out/ - opening
#      it in the Creation Kit is a good extra check.
#   3. Whether the Creation Kit's Papyrus compiler runs on Linux under Mono
#      (it is a .NET Framework program): compiles a tiny script against the
#      base script sources.
# Report: ~/fo4-probe/report.txt - send me that file.
#
set -uo pipefail
FO4="${1:-$HOME/.steam/debian-installation/steamapps/common/Fallout 4}"
FO4="$(cd "$FO4" 2>/dev/null && pwd)" || { echo "Fallout 4 install not found: ${1:-default path}"; exit 1; }
OUT="$HOME/fo4-probe"
SDK="${SDK_IMAGE:-mcr.microsoft.com/dotnet/sdk:10.0}"
MONO="${MONO_IMAGE:-mono:6.12}"
mkdir -p "$OUT/app" "$OUT/out" "$OUT/nuget" "$OUT/home" "$OUT/papyrus"
R="$OUT/report.txt"; : > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }

sec "host"
log "date: $(date -Is)  host: $(hostname)  docker: $(docker --version 2>&1)"
log "install: $FO4"

sec "install layout"
log "size: $(du -sh "$FO4" 2>/dev/null | cut -f1)"
log "top level: $(ls "$FO4" | tr '\n' '|')"
log "masters and plugins in Data/:"
ls -la "$FO4/Data" 2>/dev/null | grep -iE '\.(esm|esl|esp)$' | awk '{printf "  %-10s %s\n", $5, $NF}' | tee -a "$R" >/dev/null
log "BA2 archives: $(ls "$FO4/Data" 2>/dev/null | grep -ic '\.ba2$')"
log "Strings folder: $(ls "$FO4/Data/Strings" 2>/dev/null | head -5 | tr '\n' ' ')"
log "Creation Kit: $(ls "$FO4" | grep -i 'creationkit' | tr '\n' ' ')"
PC="$(find "$FO4" -maxdepth 3 -iname 'PapyrusCompiler.exe' 2>/dev/null | head -1)"
log "Papyrus compiler: ${PC:-NOT FOUND}"
[ -n "$PC" ] && log "  type: $(file -b "$PC" | cut -c1-80)"
SRC="$FO4/Data/Scripts/Source"
log "Scripts/Source: $(ls "$SRC" 2>/dev/null | tr '\n' ' ')"
log "  Base .psc files: $(ls "$SRC/Base" 2>/dev/null | grep -ic '\.psc$')"
log "  Base.zip: $(find "$FO4/Data/Scripts" -maxdepth 2 -iname 'Base.zip' 2>/dev/null | head -1)"
FLG="$(find "$SRC" -maxdepth 2 -iname 'Institute_Papyrus_Flags.flg' 2>/dev/null | head -1)"
log "  flags file: ${FLG:-NOT FOUND}"
log "compiled scripts (Data/Scripts/*.pex): $(ls "$FO4/Data/Scripts" 2>/dev/null | grep -ic '\.pex$')"

sec "Mutagen probe"
cat > "$OUT/app/ForgeProbe.csproj" <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
	<PropertyGroup>
		<OutputType>Exe</OutputType>
		<TargetFramework>net10.0</TargetFramework>
		<Nullable>disable</Nullable>
		<ImplicitUsings>disable</ImplicitUsings>
	</PropertyGroup>
	<ItemGroup>
		<PackageReference Include="Mutagen.Bethesda.Fallout4" Version="0.54.4" />
	</ItemGroup>
</Project>
EOF
cat > "$OUT/app/Program.cs" <<'EOF'
using System;
using System.IO;
using System.Linq;
using Mutagen.Bethesda;
using Mutagen.Bethesda.Fallout4;
using Mutagen.Bethesda.Plugins;
using Mutagen.Bethesda.Plugins.Records;

static class Probe
{
	static int Main(string[] args)
	{
		var data = args.Length > 0 ? args[0] : "/fo4/Data";
		var outDir = args.Length > 1 ? args[1] : "/work/out";
		Directory.CreateDirectory(outDir);
		var esmPath = Path.Combine(data, "Fallout4.esm");
		Console.WriteLine($"reading {esmPath} ({new FileInfo(esmPath).Length / 1048576} MB)");
		IMiscItemGetter sample = null;
		try
		{
			using var esm = Fallout4Mod.CreateFromBinaryOverlay(esmPath, Fallout4Release.Fallout4);
			Console.WriteLine($"masters of Fallout4.esm: [{string.Join(", ", esm.ModHeader.MasterReferences.Select(m => m.Master.FileName))}]");
			Console.WriteLine($"statics {esm.Statics.Count}, misc items {esm.MiscItems.Count}, quests {esm.Quests.Count}, worldspaces {esm.Worldspaces.Count}, npcs {esm.Npcs.Count}");
			var cw = esm.Worldspaces.FirstOrDefault(w => w.EditorID == "Commonwealth");
			Console.WriteLine($"Commonwealth worldspace: {(cw != null ? cw.FormKey.ToString() : "NOT FOUND")}");
			sample = esm.MiscItems.First(m => !string.IsNullOrEmpty(m.EditorID));
			Console.WriteLine($"sample misc item: {sample.EditorID} {sample.FormKey} value {sample.Value}");
			Console.WriteLine("READ_OK");
		}
		catch (Exception e) { Console.WriteLine($"READ_FAILED {e.GetType().Name}: {e.Message}\n{e.StackTrace}"); return 1; }

		var outPath = Path.Combine(outDir, "ForgeProbe.esp");
		try
		{
			var mod = new Fallout4Mod(ModKey.FromFileName("ForgeProbe.esp"), Fallout4Release.Fallout4);
			var misc = mod.MiscItems.AddNew();
			misc.EditorID = "ForgeProbeTag";
			misc.Name = "Forge probe tag";
			misc.Value = 7;
			var quest = mod.Quests.AddNew();
			quest.EditorID = "ForgeProbeQuest";
			quest.Name = "Forge probe";
			var over = mod.MiscItems.GetOrAddAsOverride(sample);
			over.Value = 99;
			((IModGetter)mod).WriteToBinary(outPath);
			Console.WriteLine($"wrote {outPath} ({new FileInfo(outPath).Length} bytes)");
			Console.WriteLine("WRITE_OK");
		}
		catch (Exception e) { Console.WriteLine($"WRITE_FAILED {e.GetType().Name}: {e.Message}\n{e.StackTrace}"); return 1; }

		try
		{
			using var back = Fallout4Mod.CreateFromBinaryOverlay(outPath, Fallout4Release.Fallout4);
			Console.WriteLine($"read back: misc items {back.MiscItems.Count}, quests {back.Quests.Count}, masters [{string.Join(", ", back.ModHeader.MasterReferences.Select(m => m.Master.FileName))}]");
			var tag = back.MiscItems.First(m => m.EditorID == "ForgeProbeTag");
			Console.WriteLine($"ForgeProbeTag: {tag.FormKey} value {tag.Value} name '{tag.Name?.String}'");
			var ov = back.MiscItems.First(m => m.FormKey == sample.FormKey);
			Console.WriteLine($"override of {sample.EditorID}: value {ov.Value} (expected 99)");
			Console.WriteLine(ov.Value == 99 && tag.Value == 7 ? "READBACK_OK" : "READBACK_MISMATCH");
		}
		catch (Exception e) { Console.WriteLine($"READBACK_FAILED {e.GetType().Name}: {e.Message}\n{e.StackTrace}"); return 1; }
		return 0;
	}
}
EOF
run_sdk() {  # network, command
    timeout 1200 docker run --rm --network "$1" --user "$(id -u):$(id -g)" \
        -v "$FO4":/fo4:ro -v "$OUT/app":/work/app -v "$OUT/out":/work/out \
        -v "$OUT/nuget":/nuget -v "$OUT/home":/home/probe \
        -e HOME=/home/probe -e DOTNET_CLI_HOME=/home/probe -e NUGET_PACKAGES=/nuget \
        -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
        -w /work/app "$SDK" bash -c "$2" 2>&1
}
docker pull -q "$SDK" >/dev/null 2>&1 || log "could not pull $SDK"
out="$(run_sdk bridge 'dotnet build -c Release -o /work/app/bin 2>&1 | grep -E "error|warn|Build succeeded|Elapsed" | head -30; dotnet /work/app/bin/ForgeProbe.dll /fo4/Data /work/out')"
log "exit code: $?"
printf '%s\n' "$out" | tail -40 | tee -a "$R" >/dev/null

sec "Mutagen probe again with --network none"
rm -f "$OUT/out/ForgeProbe.esp"
out="$(run_sdk none 'dotnet /work/app/bin/ForgeProbe.dll /fo4/Data /work/out')"
log "exit code: $?"
printf '%s\n' "$out" | tail -20 | tee -a "$R" >/dev/null
log "ForgeProbe.esp: $(ls -la "$OUT/out/ForgeProbe.esp" 2>/dev/null | awk '{print $5" bytes"}')"

sec "Papyrus compiler under Mono"
if [ -z "$PC" ]; then
    log "skipped: no PapyrusCompiler.exe found"
else
    BASE="$SRC/Base"
    if [ ! -d "$BASE" ] || [ "$(ls "$BASE" | grep -ic '\.psc$')" = 0 ]; then
        BZ="$(find "$FO4/Data/Scripts" -maxdepth 2 -iname 'Base.zip' 2>/dev/null | head -1)"
        if [ -n "$BZ" ]; then
            log "extracting Base.zip to ~/fo4-probe/papyrus/Base (the install is not changed)"
            mkdir -p "$OUT/papyrus/Base" && python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$BZ" "$OUT/papyrus/Base"
            BASE="$OUT/papyrus/Base"
            [ -d "$BASE/Base" ] && BASE="$BASE/Base"
        fi
    fi
    log "base sources: $BASE ($(find "$BASE" -iname '*.psc' 2>/dev/null | wc -l) .psc)"
    [ -z "$FLG" ] && FLG="$(find "$BASE" -iname 'Institute_Papyrus_Flags.flg' | head -1)"
    mkdir -p "$OUT/papyrus/src" "$OUT/papyrus/pex"
    cat > "$OUT/papyrus/src/ForgeProbeScript.psc" <<'EOF'
ScriptName ForgeProbeScript extends Quest

Int Property Counter = 0 Auto

Event OnInit()
    Counter += 1
    Debug.Trace("forge probe: OnInit " + Counter)
EndEvent
EOF
    docker pull -q "$MONO" >/dev/null 2>&1 || log "could not pull $MONO"
    out="$(timeout 600 docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$FO4":/fo4:ro -v "$BASE":/base:ro -v "$OUT/papyrus":/work \
        -v "$(dirname "$FLG")":/flags:ro -w /work "$MONO" \
        mono "/fo4/${PC#"$FO4"/}" /work/src/ForgeProbeScript.psc \
        "-f=$(basename "$FLG")" "-i=/work/src;/base;/flags" -o=/work/pex 2>&1)"
    log "exit code: $?"
    printf '%s\n' "$out" | tail -30 | tee -a "$R" >/dev/null
    log "ForgeProbeScript.pex: $(ls -la "$OUT/papyrus/pex"/*.pex 2>/dev/null | awk '{print $5" bytes "$NF}')"
fi

echo
echo "Report: $R"
