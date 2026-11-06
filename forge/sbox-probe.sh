#!/usr/bin/env bash
#
# sbox-probe.sh - can forge build and test s&box code on Gemini?
#
# Run on Gemini as your normal user (needs Docker, which forge already uses):
#     bash sbox-probe.sh [path to the s&box install]
# Default path: ~/.steam/debian-installation/steamapps/common/sbox
#
# It changes nothing in the s&box install (mounted read-only) and writes only
# to ~/sbox-probe/. At the end it prints the path of report.txt - send me that
# file.
#
# What it checks:
#   1. The install's layout: managed DLLs (bin/managed), native libraries,
#      whether bin/win64/engine2.dll exists (s&box's TestAppSystem requires it).
#   2. A tiny game project + unit-test project written the way s&box's own
#      solution generator writes them (net10.0, MSTest, references to
#      bin/managed, the Sandbox.Generator analyzer), built and tested inside
#      the .NET 10 SDK container:
#        a. pure-logic tests (no engine)  - expected to work
#        b. tests that start the engine with TestAppSystem (headless) - the
#           open question on Linux
#   3. The same tests again with --network none and the packages restored in
#      step 2 - the way forge's sandbox runs them.
#
set -uo pipefail

SBOX="${1:-$HOME/.steam/debian-installation/steamapps/common/sbox}"
SBOX="$(cd "$SBOX" 2>/dev/null && pwd)" || { echo "s&box install not found: ${1:-default path}"; exit 1; }
OUT="$HOME/sbox-probe"
IMG="${SDK_IMAGE:-mcr.microsoft.com/dotnet/sdk:10.0}"
rm -rf "$OUT/project" "$OUT/report.txt"
mkdir -p "$OUT/project" "$OUT/nuget" "$OUT/home"
R="$OUT/report.txt"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }

sec "host"
log "date: $(date -Is)"
log "host: $(hostname)  kernel: $(uname -r)"
log "install: $SBOX"
log "docker: $(docker --version 2>&1)"
log "host dotnet SDKs: $(dotnet --list-sdks 2>/dev/null | tr '\n' ';' || true)"
log "GPU devices visible to containers: $(ls /dev/dri 2>/dev/null | tr '\n' ' ')"

sec "install layout"
log "size: $(du -sh "$SBOX" 2>/dev/null | cut -f1)"
log "top level:"; ls -la "$SBOX" 2>&1 | sed 's/^/  /' | tee -a "$R" >/dev/null
log "bin/:"; ls -la "$SBOX/bin" 2>&1 | sed 's/^/  /' | tee -a "$R" >/dev/null
for f in sbox sbox-dev sbox-server sbox.exe sbox-dev.exe sbox-server.exe; do
    [ -e "$SBOX/$f" ] && log "  launcher present: $f ($(file -b "$SBOX/$f" | cut -c1-60))"
done
log "bin/win64/engine2.dll (TestAppSystem checks for it): $([ -f "$SBOX/bin/win64/engine2.dll" ] && echo PRESENT || echo missing)"
log "engine2 native libraries anywhere:"
find "$SBOX" -maxdepth 4 \( -iname 'engine2.dll' -o -iname 'libengine2.so' -o -iname 'engine2.so' \) 2>/dev/null | sed "s#^$SBOX/#  #" | tee -a "$R" >/dev/null
log "folders holding native .so files (count):"
find "$SBOX" -maxdepth 4 -name '*.so*' -printf '%h\n' 2>/dev/null | sort | uniq -c | sed "s#$SBOX/##" | head -20 | tee -a "$R" >/dev/null
M="$SBOX/bin/managed"
if [ -d "$M" ]; then
    log "bin/managed: $(ls "$M" | wc -l) files, $(ls "$M"/*.dll 2>/dev/null | wc -l) DLLs"
    log "Sandbox.* assemblies:"; ls "$M" | grep -i '^Sandbox\..*\.dll$' | sed 's/^/  /' | tee -a "$R" >/dev/null
    for f in Sandbox.AppSystem.dll Sandbox.Generator.dll Sandbox.CodeUpgrader.dll Sandbox.Engine.dll Sandbox.System.dll; do
        log "  $f: $([ -f "$M/$f" ] && echo present || echo MISSING)"
    done
    log "runtimeconfig frameworks:"
    grep -h '"version"\|"name"' "$M"/*.runtimeconfig.json "$SBOX"/*.runtimeconfig.json 2>/dev/null | sort -u | head -8 | sed 's/^/  /' | tee -a "$R" >/dev/null
else
    log "bin/managed: NOT FOUND - managed DLLs are elsewhere:"
    find "$SBOX" -maxdepth 4 -iname 'Sandbox.Engine.dll' 2>/dev/null | sed 's/^/  /' | tee -a "$R" >/dev/null
fi
log "templates/: $(ls "$SBOX/templates" 2>/dev/null | tr '\n' ' ')"

[ -d "$M" ] || { log ""; log "Stopping: no bin/managed folder, so the build step below would not match this install."; echo; echo "Report: $R"; exit 0; }

sec "probe project"
P="$OUT/project"
mkdir -p "$P/Code" "$P/Logic" "$P/Engine"
cat > "$P/Code/ProbeGame.cs" <<'EOF'
using Sandbox;

/// <summary>Pure logic: what forge tests without the engine.</summary>
public static class ProbeMath
{
	public static int Score( int hits, int combo ) => hits * 10 + combo * combo;
}

/// <summary>A component: compiled with s&box's source generator.</summary>
public sealed class ProbeComponent : Component
{
	[Property] public int Health { get; set; } = 100;
	public void Hurt( int amount ) => Health = System.Math.Max( 0, Health - amount );
}
EOF
refs=""
for f in "$M"/Sandbox.*.dll; do
    n="$(basename "$f")"
    case "$n" in Sandbox.Generator.dll|Sandbox.CodeUpgrader.dll) continue ;; esac
    refs+="		<Reference Include=\"/sbox/bin/managed/$n\"/>"$'\n'
done
for extra in Facepunch.ActionGraphs.dll SkiaSharp.dll Microsoft.CodeAnalysis.dll Microsoft.CodeAnalysis.CSharp.dll; do
    [ -f "$M/$extra" ] && refs+="		<Reference Include=\"/sbox/bin/managed/$extra\"/>"$'\n'
done
csproj() {  # name, extra compile items
cat <<EOF
<Project Sdk="Microsoft.NET.Sdk.Razor">
	<PropertyGroup>
		<TargetFramework>net10.0</TargetFramework>
		<AssemblyName>$1</AssemblyName>
		<LangVersion>14</LangVersion>
		<Nullable>disable</Nullable>
		<RootNamespace>Sandbox</RootNamespace>
		<IsTestProject>true</IsTestProject>
		<AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
		<OutputPath>bin/</OutputPath>
		<NoWarn>1701;1702;1591;CS1591</NoWarn>
	</PropertyGroup>
	<ItemGroup>
		<PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.12.0" />
		<PackageReference Include="MSTest.TestAdapter" Version="3.6.4" />
		<PackageReference Include="MSTest.TestFramework" Version="3.6.4" />
	</ItemGroup>
	<ItemGroup>
		<Analyzer Include="/sbox/bin/managed/Sandbox.CodeUpgrader.dll"/>
		<Analyzer Include="/sbox/bin/managed/Sandbox.Generator.dll"/>
$refs	</ItemGroup>
	<ItemGroup>
		<Compile Include="../Code/**/*.cs" />
	</ItemGroup>
</Project>
EOF
}
csproj ProbeLogicTests > "$P/Logic/ProbeLogicTests.csproj"
csproj ProbeEngineTests > "$P/Engine/ProbeEngineTests.csproj"
cat > "$P/Logic/LogicTests.cs" <<'EOF'
using Microsoft.VisualStudio.TestTools.UnitTesting;

[TestClass]
public class LogicTests
{
	[TestMethod]
	public void Score() => Assert.AreEqual( 39, ProbeMath.Score( 3, 3 ) );

	[TestMethod]
	public void ComponentTypeLoads() => Assert.IsNotNull( typeof( ProbeComponent ).GetProperty( "Health" ) );
}
EOF
cat > "$P/Engine/EngineTests.cs" <<'EOF'
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Sandbox;

[TestClass]
public class TestInit
{
	public static TestAppSystem AppSystem;

	[AssemblyInitialize]
	public static void AssemblyInitialize( TestContext context )
	{
		AppSystem = new TestAppSystem();
		AppSystem.Init();
	}

	[AssemblyCleanup]
	public static void AssemblyCleanup() => AppSystem?.Shutdown();
}

[TestClass]
public class EngineTests
{
	[TestMethod]
	public void SceneWithComponent()
	{
		var scene = new Scene();
		using var _ = scene.Push();
		var go = new GameObject();
		var c = go.Components.Create<ProbeComponent>();
		c.Hurt( 30 );
		Assert.AreEqual( 70, c.Health );
	}

	[TestMethod]
	public void CameraComponent()
	{
		var scene = new Scene();
		using var _ = scene.Push();
		var go = new GameObject();
		go.Components.Create<CameraComponent>();
		Assert.IsNotNull( scene.Camera );
	}
}
EOF
log "written to $P (Code/, Logic/, Engine/)"

run_in_sdk() {  # network, workdir, command...
    local net="$1" dir="$2"; shift 2
    timeout 900 docker run --rm --network "$net" --user "$(id -u):$(id -g)" \
        -v "$SBOX":/sbox:ro -v "$P":/work -v "$OUT/nuget":/nuget -v "$OUT/home":/home/probe \
        -e HOME=/home/probe -e DOTNET_CLI_HOME=/home/probe -e NUGET_PACKAGES=/nuget \
        -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 -e FACEPUNCH_ENGINE=/sbox \
        -w "/work/$dir" "$IMG" "$@" 2>&1
}

sec "SDK image"
docker pull -q "$IMG" >/dev/null 2>&1 || log "could not pull $IMG"
log "$(run_in_sdk bridge Logic dotnet --version)"

for proj in Logic Engine; do
    sec "$proj tests (with network: restore + build + test)"
    out="$(run_in_sdk bridge "$proj" dotnet test --logger "console;verbosity=normal")"
    rc=$?
    printf '%s\n' "$out" | tail -60 | tee -a "$R" >/dev/null
    log "exit code: $rc"
done

for proj in Logic Engine; do
    sec "$proj tests again with --network none (as forge's sandbox runs them)"
    out="$(run_in_sdk none "$proj" dotnet test --no-restore --logger "console;verbosity=normal")"
    rc=$?
    printf '%s\n' "$out" | tail -40 | tee -a "$R" >/dev/null
    log "exit code: $rc"
done

sec "summary"
grep -E "^(Passed!|Failed!)|Total tests|exit code|engine2.dll|TestAppSystem|FACEPUNCH_ENGINE|DllNotFound|BadImageFormat|Exception" "$R" | sort -u | head -40 | sed 's/^/  /' | tee -a "$R" >/dev/null
echo
echo "Report: $R"
