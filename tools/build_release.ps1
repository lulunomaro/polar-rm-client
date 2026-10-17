param(
    # Windows PowerShell 5.1 evaluates parameter default expressions before
    # $PSScriptRoot is populated. Resolve the default in the script body so
    # the checked-in release entry point works with powershell.exe -File.
    [string]$Output = "",
    [string]$GitRef = "HEAD",
    [string]$Version = "0.1.0",
    # Explicit interpreter path; defaults to the historical build environment.
    # Pass .venv-polar\Scripts\python.exe to reuse the source launcher venv.
    [string]$PythonPath = "",
    [Parameter(Mandatory=$true)][string]$FfmpegPath,
    [Parameter(Mandatory=$true)][string]$TexasWallpaperPath,
    [Parameter(Mandatory=$true)][string]$VendingWallpaperPath,
    # Explicit local input, never scoop up the rest of a developer's user_data.
    # Public redistribution of this third-party pack requires separate rights.
    [string]$Cs2UiPackPath = "",
    [switch]$UseWorkingTree
)

$ErrorActionPreference = "Stop"
$clientRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Output = if ([string]::IsNullOrWhiteSpace($Output)) {
    Join-Path $clientRoot "release\POLAR-RM-Field-Candidate.zip"
} else {
    $Output
}
$outputPath = [System.IO.Path]::GetFullPath($Output)
$stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("polar-rm-build-" + [guid]::NewGuid().ToString("N"))
$sourceRoot = Join-Path $stageRoot "source"
$distRoot = Join-Path $stageRoot "dist"
$portable = Join-Path $distRoot "POLAR-RM"
$expectedFfmpeg = "79BDD4C65ADC74D4CA937B6A9D939349806D6C66C8A42C848B0E866BCBDF4EB7"
$expectedTexas = "42B5DD4223DD280A925580709E5C62A9CA835721274FD21934DF38FEBFA03B5E"
$expectedVending = "E005A9A4BB84EB88280978F86135EB6121F826F6FA2680E19F05A15850539985"
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "Version must be a numeric semantic version, for example 0.1.0"
}
$versionParts = @($Version.Split('.') | ForEach-Object { [int]$_ })
if (@($versionParts | Where-Object { $_ -gt 65535 }).Count -ne 0) { throw "Windows version components must fit uint16" }

function Assert-Hash([string]$Path, [string]$Expected, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label missing: $Path" }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actual -ne $Expected) { throw "$Label hash mismatch: $actual" }
}

function Assert-UnderPath([string]$Path, [string]$Root) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (-not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path outside build root: $full"
    }
}

function Invoke-PortablePreflight(
    [string]$Executable,
    [string]$ReportPath,
    [string]$LogRoot,
    [int]$TimeoutSeconds = 120
) {
    # POLAR-RM.exe uses the Windows GUI subsystem. PowerShell can wait for it,
    # but $LASTEXITCODE is not guaranteed to be populated for such an image.
    # Use Process.ExitCode and the emitted JSON report as two independent gates.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.Arguments = '--preflight --official --codec-selftest --preflight-output "' + $ReportPath.Replace('"', '\"') + '"'
    $startInfo.WorkingDirectory = Split-Path -Parent $Executable
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    # A developer's portable-root override wins even over a frozen EXE's
    # location. Do not let this candidate validate a different installation's
    # manifest, resources or FFmpeg. Change only the child environment so the
    # caller retains its override even if process startup or preflight fails.
    $startInfo.EnvironmentVariables.Remove("POLAR_APP_ROOT")
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "portable preflight failed to start" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
    if ($timedOut) {
        try { $process.Kill($true) } catch { try { $process.Kill() } catch {} }
        $process.WaitForExit(10000) | Out-Null
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    New-Item -ItemType Directory -Force $LogRoot | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $LogRoot "portable_preflight.stdout.log"), $stdout, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText((Join-Path $LogRoot "portable_preflight.stderr.log"), $stderr, [System.Text.UTF8Encoding]::new($false))
    if ($timedOut) { throw "portable preflight timed out after $TimeoutSeconds seconds" }
    if ($process.ExitCode -ne 0) { throw "portable preflight failed with exit code $($process.ExitCode)" }
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw "portable preflight report missing"
    }
    try {
        $report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch { throw "portable preflight report is invalid JSON: $($_.Exception.Message)" }
    if ($report.passed -ne $true) {
        $failed = @($report.checks | Where-Object { $_.passed -ne $true } | ForEach-Object { $_.key })
        throw "portable preflight report failed: $($failed -join ', ')"
    }
    return $report
}

function Get-TreeHash([string]$Python, [string]$Source, [string]$Root) {
    $script = "from pathlib import Path; from rm_client.runtime_paths import RuntimePaths; print(RuntimePaths.hash_tree(Path(__import__('sys').argv[1])))"
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $Source
        $value = (& $Python -c $script $Root).Trim()
    }
    finally { $env:PYTHONPATH = $oldPythonPath }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "resource tree hash failed"
    }
    return $value
}

# Validate the selected environment before creating staging files or running
# protocol checks. Never guess another interpreter or install packages here.
$python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $clientRoot ".venv\Scripts\python.exe"
} else {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PythonPath)
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Build Python missing: $python. Prepare a Windows environment with requirements.txt and requirements-build.txt, then pass -PythonPath <python.exe>."
}
& $python -c "import sys, importlib.util; sys.exit('Python 3.10+ required' if sys.version_info < (3, 10) else ('Install requirements-build.txt in this interpreter' if importlib.util.find_spec('PyInstaller') is None else 0))"
if ($LASTEXITCODE -ne 0) {
    throw "Build Python or PyInstaller is unusable: $python. Prepare this interpreter with requirements.txt and requirements-build.txt; no packages were installed by the builder."
}

$buildSucceeded = $false
try {
    Assert-Hash $FfmpegPath $expectedFfmpeg "FFmpeg"
    Assert-Hash $TexasWallpaperPath $expectedTexas "Texas wallpaper"
    Assert-Hash $VendingWallpaperPath $expectedVending "Vending wallpaper"
    if ((Get-Item -LiteralPath (Join-Path $clientRoot "licenses\FFMPEG-GPLv3.txt")).Length -lt 30000) {
        throw "Complete GPLv3 license text is missing"
    }

    New-Item -ItemType Directory -Force $sourceRoot, $distRoot | Out-Null
    if ($UseWorkingTree) {
        # Developer smoke only. Field releases must omit this switch and use
        # an immutable explicit Git commit.
        Get-ChildItem -LiteralPath $clientRoot -Force | Where-Object {
            $_.Name -notin @(".git", ".venv", ".venv-polar", "release", "field_evidence", "logs", "user_data") -and
                $_.Name -notlike ".venv-polar.broken-*"
        } | Copy-Item -Destination $sourceRoot -Recurse -Force
        $commit = (& git -c core.excludesFile=NUL -C $clientRoot rev-parse HEAD).Trim()
        $sourceKind = "working-tree-smoke"
    } else {
        $archive = Join-Path $stageRoot "source.zip"
        # Ignore a broken/unreadable user-level excludes file: release inputs
        # come from the Git object database, and only the process exit code is
        # authoritative. This also prevents PS 5.1 from promoting a harmless
        # Git stderr warning to a terminating NativeCommandError.
        & git -c core.excludesFile=NUL -C $clientRoot archive --format=zip "--output=$archive" $GitRef
        if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
        Expand-Archive -LiteralPath $archive -DestinationPath $sourceRoot -Force
        $commit = (& git -c core.excludesFile=NUL -C $clientRoot rev-parse "$GitRef^{commit}").Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($commit)) { throw "GitRef must resolve to a commit" }
        $sourceKind = "immutable-git-commit"
    }
    Set-Content -LiteralPath (Join-Path $sourceRoot "BUILD_COMMIT.txt") -Value $commit -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $sourceRoot "BUILD_VERSION.txt") -Value $Version -Encoding ASCII
    $windowsVersion = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($versionParts[0]), $($versionParts[1]), $($versionParts[2]), 0),
    prodvers=($($versionParts[0]), $($versionParts[1]), $($versionParts[2]), 0), mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'POLAR-RM Field Candidate'),
    StringStruct('FileVersion', '$Version'), StringStruct('ProductVersion', '$Version'),
    StringStruct('ProductName', 'POLAR-RM'), StringStruct('OriginalFilename', 'POLAR-RM.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"@
    Set-Content -LiteralPath (Join-Path $sourceRoot "BUILD_VERSION_INFO.txt") -Value $windowsVersion -Encoding UTF8

    # Legacy user imports are private runtime data, even in a working-tree build.
    # Remove them only from this disposable staging tree, never from the checkout.
    $legacyStagedSounds = Join-Path $sourceRoot "assets\ui_sounds\custom"
    if (Test-Path -LiteralPath $legacyStagedSounds) {
        Assert-UnderPath $legacyStagedSounds $stageRoot
        Remove-Item -LiteralPath $legacyStagedSounds -Recurse -Force
    }

    # 队内 0x5A 链路的协议规格是数据文件（电控改它即可适配新固件）。
    # 代码在规格缺失/损坏时会回退到内置表继续跑，所以“能跑”不代表“规格已 shipped”。
    # 发布一个缺规格的包，会让电控改的 JSON 静默失效 —— 必须在这里拦住。
    $polarSpecJson = Join-Path $sourceRoot "rm_client\backend\polar_protocol.json"
    $polarSpecPy  = Join-Path $sourceRoot "rm_client\backend\polar_spec.py"
    $polarRolesPy = Join-Path $sourceRoot "rm_client\robot_roles.py"
    foreach ($pair in @(@($polarSpecJson, "polar protocol spec"), @($polarSpecPy, "polar spec loader"), @($polarRolesPy, "robot role table"))) {
        if (-not (Test-Path -LiteralPath $pair[0] -PathType Leaf)) {
            throw "$($pair[1]) missing from the release source tree: $($pair[0]). " +
                  "Untracked files are not archived by git archive - commit them first."
        }
    }

    # 不只要文件在，还要真能加载且不回退
    & $python -c @"
import sys
sys.path.insert(0, r'$sourceRoot')
from rm_client.backend import polar_spec
spec = polar_spec.load_spec(r'$polarSpecJson')
if not spec.ok:
    raise SystemExit('polar protocol spec failed validation: ' + spec.error_text())
if not spec.segment_formats:
    raise SystemExit('polar protocol spec loaded but produced no segment table')
if not spec.roles:
    raise SystemExit('polar protocol spec loaded but produced no role table')
print('polar spec OK: %d segments, %d roles, %d warnings'
      % (len(spec.segment_formats), len(spec.roles), len(spec.warnings)))
"@
    if ($LASTEXITCODE -ne 0) { throw "polar protocol spec is not usable in the release source tree" }

    # The two licensed wallpaper inputs intentionally stay outside Git. Inject
    # the hash-locked copies into the disposable source tree before tests and
    # freezing so both exercise exactly the resources shipped in the bundle.
    $sourceWallpaperRoot = Join-Path $sourceRoot "assets\wallpapers\builtin"
    New-Item -ItemType Directory -Force $sourceWallpaperRoot | Out-Null
    Copy-Item -LiteralPath $TexasWallpaperPath -Destination (Join-Path $sourceWallpaperRoot "texas.mp4") -Force
    Copy-Item -LiteralPath $VendingWallpaperPath -Destination (Join-Path $sourceWallpaperRoot "vending-machine.mp4") -Force

    $testLogRoot = Join-Path $stageRoot "test-logs"

    # Run against the exact source tree that will be frozen. Runtime state is
    # redirected to the temporary build root and never touches developer data.
    $oldPythonPath = $env:PYTHONPATH
    $oldFfmpeg = $env:POLAR_FFMPEG
    $oldUserData = $env:POLAR_USER_DATA_ROOT
    $oldQtPlatform = $env:QT_QPA_PLATFORM
    $oldDisableCapture = $env:POLAR_DISABLE_LOCAL_CAPTURE
    $oldDisableMediaPreload = $env:POLAR_DISABLE_MEDIA_PRELOAD
    $oldOpenCvThreads = $env:OPENCV_FOR_THREADS_NUM
    $oldOmpThreads = $env:OMP_NUM_THREADS
    $oldOmpLimit = $env:OMP_THREAD_LIMIT
    $oldOpenBlasThreads = $env:OPENBLAS_NUM_THREADS
    try {
        $env:PYTHONPATH = $sourceRoot
        $env:POLAR_FFMPEG = $FfmpegPath
        $env:POLAR_USER_DATA_ROOT = Join-Path $stageRoot "test-user-data"
        $env:POLAR_DISABLE_LOCAL_CAPTURE = "1"
        $env:POLAR_DISABLE_MEDIA_PRELOAD = "1"
        $env:OPENCV_FOR_THREADS_NUM = "4"
        $env:OMP_NUM_THREADS = "4"
        $env:OMP_THREAD_LIMIT = "8"
        $env:OPENBLAS_NUM_THREADS = "1"
        # Set this before Python imports any Qt module. Individual tests use
        # setdefault(), which is too late when another imported module has
        # already selected the Windows platform plugin.
        $env:QT_QPA_PLATFORM = "offscreen"
        # The Python registry owns the complete suite list, prerequisite
        # categories and PASS/SKIP/INCOMPLETE/FAIL interpretation. Exit zero
        # from an individual script is insufficient evidence for a release.
        # QOpenGLWidget runtime smokes require a real desktop/WGL surface. A
        # caller's inherited offscreen setting would otherwise create zero
        # native draw calls and produce a misleading release failure.
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        # field_candidate's source preflight deliberately uses an offscreen Qt
        # platform. Repeat it on the desktop WGL path before freezing so the
        # release gate does not depend on the offscreen plugin supporting GL.
        & $python -c "from rm_client.preflight import _qt_opengl_context; ok, detail = _qt_opengl_context(); print(detail); raise SystemExit(0 if ok else 1)"
        if ($LASTEXITCODE -ne 0) { throw "desktop Qt/OpenGL preflight failed" }
        & $python (Join-Path $sourceRoot "tools\verify_all.py") --profile release --desktop `
            --timeout 300 --log-dir $testLogRoot --json (Join-Path $testLogRoot "release-tests.json")
        if ($LASTEXITCODE -ne 0) {
            throw "Release registry contains failed, skipped, or incomplete checks; see $testLogRoot"
        }
        $testReportPath = Join-Path $testLogRoot "release-tests.json"
        if (-not (Test-Path -LiteralPath $testReportPath -PathType Leaf)) {
            throw "Release registry did not produce its evidence report"
        }
        $testReport = Get-Content -LiteralPath $testReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($testReport.passed -ne $true -or $testReport.results.Count -eq 0 -or
            @($testReport.results | Where-Object { $_.status -ne "PASS" }).Count -ne 0) {
            throw "Release registry report is incomplete or not fully passing"
        }
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
        $env:POLAR_FFMPEG = $oldFfmpeg
        $env:POLAR_USER_DATA_ROOT = $oldUserData
        $env:POLAR_DISABLE_LOCAL_CAPTURE = $oldDisableCapture
        $env:POLAR_DISABLE_MEDIA_PRELOAD = $oldDisableMediaPreload
        $env:OPENCV_FOR_THREADS_NUM = $oldOpenCvThreads
        $env:OMP_NUM_THREADS = $oldOmpThreads
        $env:OMP_THREAD_LIMIT = $oldOmpLimit
        $env:OPENBLAS_NUM_THREADS = $oldOpenBlasThreads
        if ($null -eq $oldQtPlatform) {
            Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        } else {
            $env:QT_QPA_PLATFORM = $oldQtPlatform
        }
    }
    Push-Location $sourceRoot
    $oldFreezeCi = $env:CI
    try {
        # PyInstaller hooks can import optional SDKs before application startup.
        # Scope the observed OpenVINO telemetry opt-out to this invocation;
        # restore the caller's process environment even when freezing fails.
        $env:CI = "true"
        & $python -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $stageRoot "work") (Join-Path $sourceRoot "tools\POLAR-RM.spec")
    } finally {
        if ($null -eq $oldFreezeCi) {
            Remove-Item Env:CI -ErrorAction SilentlyContinue
        } else {
            $env:CI = $oldFreezeCi
        }
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $portable "POLAR-RM.exe"))) {
        throw "PyInstaller one-folder build failed"
    }

    New-Item -ItemType Directory -Force `
        (Join-Path $portable "bin"), `
        (Join-Path $portable "resources"), `
        (Join-Path $portable "defaults"), `
        (Join-Path $portable "user_data\configs"), `
        (Join-Path $portable "user_data\calibrations"), `
        (Join-Path $portable "user_data\logs"), `
        (Join-Path $portable "field_evidence\sessions"), `
        (Join-Path $portable "licenses") | Out-Null
    Copy-Item -LiteralPath $FfmpegPath -Destination (Join-Path $portable "bin\ffmpeg.exe") -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "assets") -Destination (Join-Path $portable "resources\assets") -Recurse -Force
    if (Test-Path -LiteralPath (Join-Path $sourceRoot "3000925581")) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot "3000925581") -Destination (Join-Path $portable "resources\3000925581") -Recurse -Force
    }
    $visionSource = Get-ChildItem -LiteralPath $sourceRoot -Directory | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "sp_vision_25\assets\yolov5.xml")
    } | Select-Object -First 1
    if ($null -ne $visionSource) {
        Copy-Item -LiteralPath $visionSource.FullName -Destination (Join-Path $portable "resources\$($visionSource.Name)") -Recurse -Force
    }
    $wallpaperRoot = Join-Path $portable "resources\assets\wallpapers\builtin"
    New-Item -ItemType Directory -Force $wallpaperRoot | Out-Null
    Copy-Item -LiteralPath $TexasWallpaperPath -Destination (Join-Path $wallpaperRoot "texas.mp4") -Force
    Copy-Item -LiteralPath $VendingWallpaperPath -Destination (Join-Path $wallpaperRoot "vending-machine.mp4") -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "configs\default.json") -Destination (Join-Path $portable "defaults\default.json") -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "configs\dt7_profile.json") -Destination (Join-Path $portable "defaults\dt7_profile.json") -Force
    Get-ChildItem -LiteralPath (Join-Path $sourceRoot "licenses") -Force |
        Copy-Item -Destination (Join-Path $portable "licenses") -Recurse -Force
    & $python (Join-Path $sourceRoot "tools\collect_release_licenses.py") --out (Join-Path $portable "licenses\python")
    if ($LASTEXITCODE -ne 0) { throw "Dependency license collection failed" }
    Copy-Item -LiteralPath (Join-Path $sourceRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $portable "licenses\THIRD_PARTY_NOTICES.md") -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "FIELD_CANDIDATE.md") -Destination (Join-Path $portable "FIELD_CANDIDATE.md") -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "REFEREE_INTEGRATION.md") -Destination (Join-Path $portable "REFEREE_INTEGRATION.md") -Force
    $cs2PackMetadata = $null
    if (-not [string]::IsNullOrWhiteSpace($Cs2UiPackPath)) {
        # Validate exactly the same allowlist, bounds and per-file hashes as the
        # runtime loader. Copy declared assets only (not obsolete exports, Steam
        # files, configuration, calibration, credentials or user media).
        & $python -c @"
import sys
sys.path.insert(0, r'$sourceRoot')
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from rm_client.ui.cs2_assets import Cs2Assets
app = QGuiApplication([])
pack = Cs2Assets(Path(sys.argv[1]))
if not pack.available or pack.error:
    raise SystemExit('invalid CS2 local pack: ' + pack.error)
print('Verified CS2 asset pack:', len(pack.paths), 'assets')
"@ $Cs2UiPackPath
        if ($LASTEXITCODE -ne 0) { throw "CS2 pack validation failed" }
        $packManifestPath = Join-Path $Cs2UiPackPath "manifest.json"
        $packManifest = Get-Content -LiteralPath $packManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $packDestination = Join-Path $portable "user_data\cs2_ui_pack"
        New-Item -ItemType Directory -Force $packDestination | Out-Null
        Copy-Item -LiteralPath $packManifestPath -Destination (Join-Path $packDestination "manifest.json")
        foreach ($asset in $packManifest.assets.PSObject.Properties) {
            Copy-Item -LiteralPath (Join-Path $Cs2UiPackPath $asset.Value.file) -Destination (Join-Path $packDestination $asset.Value.file)
        }
        $cs2PackMetadata = [ordered]@{
            scope = "third-party-local-pack"
            asset_count = @($packManifest.assets.PSObject.Properties).Count
            tree_sha256 = Get-TreeHash $python $sourceRoot $packDestination
        }
    }

    $ffVersion = (& $FfmpegPath -hide_banner -version) -join "`n"
    Set-Content -LiteralPath (Join-Path $portable "licenses\FFMPEG-BUILD.txt") -Value $ffVersion -Encoding UTF8
    $launcher = @"
@echo off
setlocal
cd /d "%~dp0"
if /I "%~1"=="--replay-field-log" goto launch
"%~dp0POLAR-RM.exe" --preflight --official --codec-selftest --preflight-output "%~dp0user_data\logs\startup-preflight.json"
if errorlevel 1 (
  echo Startup preflight failed. See user_data\logs\startup-preflight.json.
  pause
  exit /b 2
)
:launch
"%~dp0POLAR-RM.exe" %*
exit /b %errorlevel%
"@
    Set-Content -LiteralPath (Join-Path $portable "START-POLAR-RM.bat") -Value $launcher -Encoding ASCII

    $pythonVersion = (& $python --version) 2>&1
    $dependencyLines = (& $python -m pip freeze) | Sort-Object
    # Read the reviewed contracts from the exact archived source, not the
    # developer checkout. The manifest must not silently pin a different year.
    $refereeJson = & $python -c @"
import json, sys
sys.path.insert(0, r'$sourceRoot')
from rm_client.backend.referee.registry import get_profile, supported_profiles
profiles = [get_profile(key) for key in supported_profiles()]
print(json.dumps({'default_profile': get_profile().profile_id, 'protocol_version': get_profile().protocol_version,
    'profiles': [{'id': p.profile_id, 'version': p.protocol_version, 'read_only': p.read_only,
        'contract_pdf_sha256': p.codec.SOURCE_SHA256} for p in profiles]}))
"@
    if ($LASTEXITCODE -ne 0) { throw "release referee profile metadata failed" }
    $refereeMetadata = $refereeJson | ConvertFrom-Json
    $criticalFiles = [ordered]@{
        "bin/ffmpeg.exe" = $expectedFfmpeg
        "_internal/rm_client/backend/polar_protocol.json" = (Get-FileHash (Join-Path $portable "_internal\rm_client\backend\polar_protocol.json") -Algorithm SHA256).Hash
        "_internal/configs/cc_key_usage.json" = (Get-FileHash (Join-Path $portable "_internal\configs\cc_key_usage.json") -Algorithm SHA256).Hash
        "defaults/default.json" = (Get-FileHash (Join-Path $portable "defaults\default.json") -Algorithm SHA256).Hash
        "defaults/dt7_profile.json" = (Get-FileHash (Join-Path $portable "defaults\dt7_profile.json") -Algorithm SHA256).Hash
        "resources/assets/wallpapers/builtin/texas.mp4" = $expectedTexas
        "resources/assets/wallpapers/builtin/vending-machine.mp4" = $expectedVending
        "licenses/FFMPEG-GPLv3.txt" = (Get-FileHash (Join-Path $portable "licenses\FFMPEG-GPLv3.txt") -Algorithm SHA256).Hash
        "licenses/FFMPEG-SOURCE.txt" = (Get-FileHash (Join-Path $portable "licenses\FFMPEG-SOURCE.txt") -Algorithm SHA256).Hash
        "licenses/FFMPEG-BUILD.txt" = (Get-FileHash (Join-Path $portable "licenses\FFMPEG-BUILD.txt") -Algorithm SHA256).Hash
        "licenses/THIRD_PARTY_NOTICES.md" = (Get-FileHash (Join-Path $portable "licenses\THIRD_PARTY_NOTICES.md") -Algorithm SHA256).Hash
        "FIELD_CANDIDATE.md" = (Get-FileHash (Join-Path $portable "FIELD_CANDIDATE.md") -Algorithm SHA256).Hash
        "REFEREE_INTEGRATION.md" = (Get-FileHash (Join-Path $portable "REFEREE_INTEGRATION.md") -Algorithm SHA256).Hash
        "START-POLAR-RM.bat" = (Get-FileHash (Join-Path $portable "START-POLAR-RM.bat") -Algorithm SHA256).Hash
        "POLAR-RM.exe" = (Get-FileHash (Join-Path $portable "POLAR-RM.exe") -Algorithm SHA256).Hash
    }
    $resourceTreeHash = Get-TreeHash $python $sourceRoot (Join-Path $portable "resources")
    if ($null -ne $cs2PackMetadata) {
        $criticalFiles["user_data/cs2_ui_pack/manifest.json"] = (Get-FileHash -LiteralPath (Join-Path $packDestination "manifest.json") -Algorithm SHA256).Hash
        foreach ($asset in $packManifest.assets.PSObject.Properties) {
            $criticalFiles["user_data/cs2_ui_pack/$($asset.Value.file)"] = $asset.Value.sha256
        }
    }
    $manifest = [ordered]@{
        schema = 1
        product = "POLAR-RM Field Candidate"
        version = $Version
        verdict = "NOT READY FOR REAL MATCH"
        git_commit = $commit
        source_kind = $sourceKind
        built_at_utc = [DateTime]::UtcNow.ToString("o")
        protocol_version = $refereeMetadata.protocol_version
        referee = $refereeMetadata
        python = "$pythonVersion"
        dependencies = $dependencyLines
        optional_cs2_pack = $cs2PackMetadata
        critical_files = $criticalFiles
        ffmpeg = [ordered]@{
            version = ($ffVersion -split "`n")[0]
            sha256 = $expectedFfmpeg
            source_commit = "c898ddb8fe"
            license = "GPLv3"
        }
        resources = [ordered]@{
            tree_sha256 = $resourceTreeHash
            texas_mp4_sha256 = $expectedTexas
            vending_machine_mp4_sha256 = $expectedVending
            default_json_sha256 = (Get-FileHash (Join-Path $portable "defaults\default.json") -Algorithm SHA256).Hash
        }
        tests = [ordered]@{
            registry_profile = $testReport.profile
            registry_counts = $testReport.counts
            registry_results = $testReport.results
            not_selected = $testReport.not_selected
            evidence_scope = $testReport.evidence_scope
            portable_preflight = "PENDING"
            clean_windows = "FIELD VALIDATION REQUIRED"
        }
    }
    $manifestPath = Join-Path $portable "BUILD_MANIFEST.json"
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($manifestPath, $manifestJson + "`n", [System.Text.UTF8Encoding]::new($false))
    $manifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    Set-Content -LiteralPath (Join-Path $portable "BUILD_MANIFEST.sha256") -Value "$manifestSha  BUILD_MANIFEST.json" -Encoding ASCII

    # Portable preflight with PATH stripped proves resolution from bin/ only.
    $oldPath = $env:PATH
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        $portablePreflightReport = Join-Path $testLogRoot "portable_preflight.json"
        $null = Invoke-PortablePreflight `
            (Join-Path $portable "POLAR-RM.exe") `
            $portablePreflightReport `
            $testLogRoot
    } finally { $env:PATH = $oldPath }

    # The codec self-test owns short-lived ffmpeg children. Windows may retain
    # an executable image section briefly after process exit. Wait for the
    # bundled binary to become share-readable before archiving instead of
    # racing kernel cleanup and intermittently failing Compress-Archive.
    $bundledFfmpeg = Join-Path $portable "bin\ffmpeg.exe"
    $ffmpegUnlocked = $false
    $unlockDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $unlockDeadline -and -not $ffmpegUnlocked) {
        try {
            $probe = [System.IO.File]::Open(
                $bundledFfmpeg,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::None
            )
            $probe.Dispose()
            $ffmpegUnlocked = $true
        }
        catch [System.IO.IOException] { Start-Sleep -Milliseconds 100 }
    }
    if (-not $ffmpegUnlocked) { throw "bundled FFmpeg remained locked after portable preflight" }

    # A release archive must contain writable directory scaffolding, never
    # state created by the build machine or by the preflight itself.  Keep this
    # as a hard postcondition so future startup changes cannot silently ship a
    # seeded config, logs, calibration, or qualification evidence.
    $runtimeStateFiles = @(
        Get-ChildItem -LiteralPath (Join-Path $portable "user_data") -Recurse -File -Force
        Get-ChildItem -LiteralPath (Join-Path $portable "field_evidence") -Recurse -File -Force
    )
    if ($null -ne $cs2PackMetadata) {
        $allowedPackNames = @("manifest.json") + @($packManifest.assets.PSObject.Properties | ForEach-Object { $_.Value.file })
        $runtimeStateFiles = @($runtimeStateFiles | Where-Object {
            $_.DirectoryName -ne $packDestination -or $_.Name -notin $allowedPackNames
        })
        if ((Get-TreeHash $python $sourceRoot $packDestination) -ne $cs2PackMetadata.tree_sha256) {
            throw "CS2 pack changed after verification"
        }
    }
    if ($runtimeStateFiles.Count -ne 0) {
        $relativeRuntimeFiles = $runtimeStateFiles | ForEach-Object {
            $_.FullName.Substring($portable.Length).TrimStart("\")
        }
        throw "portable runtime state is not empty: $($relativeRuntimeFiles -join ', ')"
    }

    $manifest.tests.portable_preflight = "PASS"
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($manifestPath, $manifestJson + "`n", [System.Text.UTF8Encoding]::new($false))
    $manifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    Set-Content -LiteralPath (Join-Path $portable "BUILD_MANIFEST.sha256") -Value "$manifestSha  BUILD_MANIFEST.json" -Encoding ASCII

    $destination = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Force $destination | Out-Null
    if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
    # Windows PowerShell's Compress-Archive intermittently reports copied EXE
    # files as in use even after an exclusive-open probe succeeds. The BCL ZIP
    # implementation uses ordinary read sharing and is deterministic here.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $portable,
        $outputPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true
    )
    $buildSucceeded = $true
    Write-Host "Self-contained field candidate built: $outputPath"
}
finally {
    if (-not $buildSucceeded) {
        Write-Warning "Build failed. Source and test logs retained for diagnosis: $stageRoot"
    }
    if ($buildSucceeded -and (Test-Path -LiteralPath $stageRoot)) {
        Assert-UnderPath $stageRoot ([System.IO.Path]::GetTempPath())
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
