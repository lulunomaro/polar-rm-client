param(
    # Windows PowerShell 5.1 evaluates parameter default expressions before
    # $PSScriptRoot is populated. Resolve the default in the script body so
    # the checked-in release entry point works with powershell.exe -File.
    [string]$Output = "",
    [string]$GitRef = "HEAD",
    # 这三个**故意不再是 Mandatory**：PowerShell 在进脚本体之前就拦下缺参数的用户，
    # 那样下面那个构建环境探测（脚本体的第一件事）根本轮不到跑 —— 操作员得先把
    # 三样资产找齐，才会发现 venv 解释器其实是死的。校验挪到探测之后。
    [string]$FfmpegPath = "",
    [string]$TexasWallpaperPath = "",
    [string]$VendingWallpaperPath = "",
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

function Invoke-RequiredTest([string]$Python, [string]$Script, [string]$Label, [int]$TimeoutSeconds = 300, [string]$LogRoot = "") {
    Write-Host "Running release test: $Label"
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Python
    # Run the test under a tiny watchdog bootstrap. It prints the active test
    # function before invocation and dumps every Python thread if progress
    # stalls, turning intermittent native/Qt hangs into actionable logs.
    $bootstrap = @'
import faulthandler, os, runpy, sys, time
path = sys.argv[1]
faulthandler.enable(all_threads=True)
faulthandler.dump_traceback_later(120, repeat=True)
target = os.path.normcase(os.path.abspath(path))
started = {}
def profile(frame, event, _arg):
    if os.path.normcase(os.path.abspath(frame.f_code.co_filename)) != target:
        return profile
    name = frame.f_code.co_name
    if not name.startswith("test_"):
        return profile
    if event == "call":
        started[id(frame)] = time.monotonic()
        print(f"START {name}", flush=True)
    elif event == "return":
        elapsed = time.monotonic() - started.pop(id(frame), time.monotonic())
        print(f"PASS {name} {elapsed:.3f}s", flush=True)
    return profile
sys.setprofile(profile)
try:
    runpy.run_path(path, run_name="__main__")
finally:
    sys.setprofile(None)
faulthandler.cancel_dump_traceback_later()
'@
    $bootstrapPath = Join-Path $LogRoot "release_test_bootstrap.py"
    New-Item -ItemType Directory -Force $LogRoot | Out-Null
    [System.IO.File]::WriteAllText($bootstrapPath, $bootstrap, [System.Text.UTF8Encoding]::new($false))
    $startInfo.Arguments = '"' + $bootstrapPath.Replace('"', '\"') + '" "' + $Script.Replace('"', '\"') + '"'
    $startInfo.WorkingDirectory = Split-Path -Parent (Split-Path -Parent $Script)
    $startInfo.UseShellExecute = $false
    if (-not [string]::IsNullOrWhiteSpace($LogRoot)) {
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "$Label failed to start" }
    $stdoutTask = if ($startInfo.RedirectStandardOutput) { $process.StandardOutput.ReadToEndAsync() } else { $null }
    $stderrTask = if ($startInfo.RedirectStandardError) { $process.StandardError.ReadToEndAsync() } else { $null }
    $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
    if ($timedOut) {
        # Stop the complete child tree (FFmpeg/OpenVINO helpers included) so a
        # failed release gate can never leak into the next build attempt.
        try { $process.Kill($true) } catch { try { $process.Kill() } catch {} }
        $process.WaitForExit(10000) | Out-Null
    }
    if ($null -ne $stdoutTask) {
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        [System.IO.File]::WriteAllText((Join-Path $LogRoot "$Label.stdout.log"), $stdout, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText((Join-Path $LogRoot "$Label.stderr.log"), $stderr, [System.Text.UTF8Encoding]::new($false))
        if (-not [string]::IsNullOrWhiteSpace($stdout)) { Write-Host $stdout.TrimEnd() }
        if (-not [string]::IsNullOrWhiteSpace($stderr)) { Write-Warning $stderr.TrimEnd() }
    }
    if ($timedOut) { throw "$Label timed out after $TimeoutSeconds seconds; see $LogRoot" }
    if ($process.ExitCode -ne 0) { throw "$Label failed with exit code $($process.ExitCode)" }
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

# --- 构建环境探测：整个脚本的**第一件事** ---
#
# 原来这里只有一句 `Test-Path .venv\Scripts\python.exe`，而且位置在第一次真正调用
# 解释器**之后**，更在三个资源哈希校验与 `git archive` 源码暂存**之后**。
# 于是 venv 是坏的时候，操作员的实际体验是：
#
#   1. 把 FFmpeg 找出来，跑一遍哈希
#   2. 把两张壁纸找出来，各跑一遍哈希
#   3. 检查 GPL 许可证正文长度
#   4. 等 git archive 把整个仓库暂存完（这一步不便宜）
#   5. **然后**才被告知 venv 解释器是死的
#
# 前四步全是白做的。更糟的是第 1 步失败时人会以为"是我路径给错了"，
# 于是去翻目录、翻文档，而真正的原因根本不在那里。
#
# 现在这段提到脚本体的最前面：环境坏不坏，先说。
# 配套改动缺一不可 ——
#   * 位置提到任何 `Assert-Hash` 之前；
#   * 三个资源参数从 `Mandatory=$true` 改成空默认值，否则 PowerShell 在**进脚本体
#     之前**就把人拦下，探测根本轮不到跑；
#   * 参数校验挪到这里之后（下面的 $requiredAssets）。
#
# 关键点：Test-Path 只证明"文件在"，不证明"解释器活着"。一个 base 解释器被删/
# 被移走的 venv 完全符合前者，却会在第一次调用时炸出一句毫不相干的错误。
# 所以这里**真的把解释器跑起来**看它能不能执行。
$python = Join-Path $clientRoot ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $clientRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pyinstaller)) {
    throw "Build environment missing at $clientRoot\.venv " +
          "(python.exe or pyinstaller.exe not found). " +
          "Install requirements-build.txt before release; runtime never installs packages."
}
# 这里必须临时把 $ErrorActionPreference 降到 Continue：`2>&1` 会把解释器写到
# stderr 的 "No Python at ..." 变成 PowerShell 的错误记录，而在 Stop 模式下
# **那本身就是终止性错误** —— 脚本停在探测这一行就结束了，我们精心写的
# "删掉 .venv 重建、不要只是重跑"根本来不及显示。
# 实测过：不降优先级的话，操作员看到的是解释器自己那句 "No Python at"，
# 而不是可执行的下一步。降完再自己判退出码。
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$venvProbe = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>&1
$venvProbeExit = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
if ($venvProbeExit -ne 0) {
    $cfg = Join-Path $clientRoot ".venv\pyvenv.cfg"
    $base = ""
    if (Test-Path -LiteralPath $cfg) {
        $base = (Get-Content -LiteralPath $cfg -ErrorAction SilentlyContinue |
                 Where-Object { $_ -match '^\s*(home|executable)\s*=' } | Select-Object -First 1)
    }
    throw ("Build environment is broken: `.venv` exists but its interpreter cannot run " +
           "(exit $venvProbeExit).`n  probe output: $venvProbe`n  pyvenv.cfg: $base`n" +
           "  This happens when the venv's base interpreter was moved or deleted " +
           "(e.g. a wiped toolchain cache). Delete .venv and recreate it from " +
           "requirements-build.txt; do NOT just re-run the build.")
}
Write-Host ("  [build-env] .venv OK (python " + ($venvProbe | Select-Object -Last 1) + ")")
# 解释器活着**不等于**依赖装齐了。一个只有 PyInstaller、没有 PySide6 的 venv
# 完全能通过上面那两步，然后要等到 PyInstaller 分析到某个 import 时才炸 ——
# 那时候已经跑完了源码暂存与哈希校验，报错还指向一个包名，
# 而根因（构建环境没按 requirements.txt 装）离现场很远。
#
# 所以这里再验一次：把运行时那几个包挨个 import 一遍，缺哪个直接点名。
# 这一步很便宜（import 已经缓存过大部分），但把"环境没装全"从
# "跑到一半才炸"变成"第一条就说清楚缺哪个包"。
# 用临时 .py 文件，而不是 `python -c "<一长串>"`。
# 第三种写法了，三种都踩过：
#   1. `-c @"..."@`        —— here-string 不能当行内参数，报错还指着 $python；
#   2. `-c '...双引号...'`  —— PowerShell 拼原生命令行时会**吃掉**双引号，
#                             Python 收到的是 names=(PySide6,cv2,...)，SyntaxError；
#   3. PS 单引号里写 `''`   —— 能过，但嵌套引号让人读两遍才敢确认。
# 写文件只有一个转义要处理，而且这段会随发布脚本一起被 review，值得这点啰嗦。
$depCheckPy = Join-Path ([System.IO.Path]::GetTempPath()) ("polar-build-env-check-" + [guid]::NewGuid().ToString("N") + ".py")
@"
import importlib.util as u
names = ('PySide6', 'cv2', 'numpy', 'openvino', 'paho', 'serial')
missing = [n for n in names if u.find_spec(n) is None]
print('MISSING:' + ','.join(missing) if missing else 'ALL_PRESENT')
"@ | Set-Content -LiteralPath $depCheckPy -Encoding UTF8
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$depProbe = & $python $depCheckPy 2>&1
$depExit = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
Remove-Item -LiteralPath $depCheckPy -Force -ErrorAction SilentlyContinue
if ($depExit -ne 0) {
    throw ("Build environment is broken: `.venv` interpreter cannot run an import check`n" +
           "  probe output: $depProbe`n" +
           "  Recreate .venv from requirements.txt + requirements-build.txt.")
}
if ($depProbe -match '^MISSING:(.+)$') {
    $miss = $Matches[1] -split ',' | Where-Object { $_ }
    throw ("Build environment is incomplete: `.venv` is missing " + $miss.Count +
           " runtime package(s): " + ($miss -join ", ") + "`n" +
           "  A .venv with PyInstaller but no PySide6 passes the interpreter check,`n" +
           "  then fails deep inside PyInstaller with a bare ImportError.`n" +
           "  Install requirements.txt into .venv (runtime) plus requirements-build.txt`n" +
           "  (PyInstaller), then retry.")
}
Write-Host "  [build-env] runtime deps OK"

# 资源参数现在改成非 Mandatory，所以**这里**才校验给没给。
# 每个都报清楚是哪个参数、要给什么 —— 报错要指向下一步该做什么，
# 而不是"参数缺失"这种要人自己回去翻文档的措辞。
$requiredAssets = @(
    @("-FfmpegPath", $FfmpegPath, "FFmpeg 可执行文件（须通过 SHA-256 校验）"),
    @("-TexasWallpaperPath", $TexasWallpaperPath, "Texas 启动壁纸（须通过 SHA-256 校验）"),
    @("-VendingWallpaperPath", $VendingWallpaperPath, "补给站壁纸（须通过 SHA-256 校验）")
)
foreach ($asset in $requiredAssets) {
    if ([string]::IsNullOrWhiteSpace($asset[1])) {
        throw ("缺少必需参数 $($asset[0])：$($asset[2])`n" +
               "  用法：.\tools\build_release.ps1 -FfmpegPath <exe> " +
               "-TexasWallpaperPath <file> -VendingWallpaperPath <file>")
    }
}

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
            $_.Name -notin @(".git", ".venv", "release", "field_evidence", "logs", "user_data")
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
    $oldSkipExternalIntegration = $env:POLAR_SKIP_EXTERNAL_INTEGRATION
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
        $env:POLAR_SKIP_EXTERNAL_INTEGRATION = "1"
        $env:OPENCV_FOR_THREADS_NUM = "4"
        $env:OMP_NUM_THREADS = "4"
        $env:OMP_THREAD_LIMIT = "8"
        $env:OPENBLAS_NUM_THREADS = "1"
        # Set this before Python imports any Qt module. Individual tests use
        # setdefault(), which is too late when another imported module has
        # already selected the Windows platform plugin.
        $env:QT_QPA_PLATFORM = "offscreen"
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\backend_protocol_test.py") "backend_protocol" 240 $testLogRoot
        # Team StandardRobot++ 0x5A link: framing/CRC parity with the team's own
        # reference implementation, HUD overlay + arbitration, a virtual-robot
        # end-to-end loop, and a real-window startup path. These are the gates
        # that prove the client can talk to the actual robots, so a release must
        # never be cut without them.
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_spec_test.py") "polar_spec" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\robot_roles_test.py") "robot_roles" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_link_test.py") "polar_link" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_bridge_test.py") "polar_bridge" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_integration_test.py") "polar_integration" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_selftest_test.py") "polar_selftest" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_startup_test.py") "polar_startup" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\api_freeze_test.py") "api_freeze" 180 $testLogRoot
        # ---- 2026-09 补登记 ----
        # 这几个 suite 之前在 tools/verify_all.py 里注册了、这里没有 ——
        # 于是"发布前跑一遍"这道闸门**从来没跑过它们**。
        # 是 tests/docs_and_tests_test.py 报出来的（它专门比对两个注册表的一致性）：
        # 只在一个地方注册 = 另一半的失败没人看得见。两处必须同步改。
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\encode_invariant_attack_test.py") "encode_invariant_attack" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\spin_readback_test.py") "spin_readback" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\settings_access_test.py") "settings_access" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\test_suite_selfcheck.py") "suite_selfcheck" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\polar_link_snapshot_test.py") "polar_link_snapshot" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\overlay_write_guard_test.py") "overlay_write_guard" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\protocol_crc_test.py") "protocol_crc" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\zero_frame_and_diagnostics_test.py") "zero_frame_and_diagnostics" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\rust_port_coverage_test.py") "rust_port_coverage" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\tick_polar_link_branches_test.py") "tick_polar_link_branches" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\apply_one_dispatch_test.py") "apply_one_dispatch" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\udp_transport_test.py") "udp_transport" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\field_candidate_test.py") "field_candidate" 240 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\smoke_test.py") "ui_backend_regression" 300 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\gpu_compositor_test.py") "gpu_compositor" 180 $testLogRoot
        # QOpenGLWidget runtime smokes require a real desktop/WGL surface. A
        # caller's inherited offscreen setting would otherwise create zero
        # native draw calls and produce a misleading release failure.
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        # field_candidate's source preflight deliberately uses an offscreen Qt
        # platform. Repeat it on the desktop WGL path before freezing so the
        # release gate does not depend on the offscreen plugin supporting GL.
        & $python -c "from rm_client.preflight import _qt_opengl_context; ok, detail = _qt_opengl_context(); print(detail); raise SystemExit(0 if ok else 1)"
        if ($LASTEXITCODE -ne 0) { throw "desktop Qt/OpenGL preflight failed" }
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\gpu_renderer_runtime_smoke.py") "gpu_renderer_runtime" 180 $testLogRoot
        Invoke-RequiredTest $python (Join-Path $sourceRoot "tests\startup_gpu_runtime_smoke.py") "startup_gpu_runtime" 180 $testLogRoot
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
        $env:POLAR_FFMPEG = $oldFfmpeg
        $env:POLAR_USER_DATA_ROOT = $oldUserData
        $env:POLAR_DISABLE_LOCAL_CAPTURE = $oldDisableCapture
        $env:POLAR_DISABLE_MEDIA_PRELOAD = $oldDisableMediaPreload
        $env:POLAR_SKIP_EXTERNAL_INTEGRATION = $oldSkipExternalIntegration
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
    try {
        & $pyinstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $stageRoot "work") (Join-Path $sourceRoot "tools\POLAR-RM.spec")
    } finally { Pop-Location }
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
    Get-ChildItem -LiteralPath (Join-Path $sourceRoot "licenses") -Force |
        Copy-Item -Destination (Join-Path $portable "licenses") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $portable "licenses\THIRD_PARTY_NOTICES.md") -Force
    Copy-Item -LiteralPath (Join-Path $sourceRoot "FIELD_CANDIDATE.md") -Destination (Join-Path $portable "FIELD_CANDIDATE.md") -Force

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
    # 重量级包（cv2 / openvino）收没收、收了多少，由 POLAR-RM.spec 自己写在这里。
    # 以前这个决定不留痕：openvino 独占 211 MB（占压缩包 22.7%），而打包规则
    # 与 requirements-core.txt "openvino 不该进首发路径" 的说法互相矛盾，
    # 包体变大时构建日志里找不到任何对应证据。
    $bundledPackages = @{}
    $bundledReport = Join-Path $sourceRoot "build\bundled_packages.json"
    if (Test-Path -LiteralPath $bundledReport) {
        try {
            $bundledPackages = Get-Content -LiteralPath $bundledReport -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            $bundledPackages = @{ _unreadable = $_.Exception.Message }
        }
    } else {
        $bundledPackages = @{ _missing = "POLAR-RM.spec did not write build/bundled_packages.json" }
    }
    $criticalFiles = [ordered]@{
        "bin/ffmpeg.exe" = $expectedFfmpeg
        "defaults/default.json" = (Get-FileHash (Join-Path $portable "defaults\default.json") -Algorithm SHA256).Hash
        "resources/assets/wallpapers/builtin/texas.mp4" = $expectedTexas
        "resources/assets/wallpapers/builtin/vending-machine.mp4" = $expectedVending
        "licenses/FFMPEG-GPLv3.txt" = (Get-FileHash (Join-Path $portable "licenses\FFMPEG-GPLv3.txt") -Algorithm SHA256).Hash
        "licenses/FFMPEG-SOURCE.txt" = (Get-FileHash (Join-Path $portable "licenses\FFMPEG-SOURCE.txt") -Algorithm SHA256).Hash
        "licenses/FFMPEG-BUILD.txt" = (Get-FileHash (Join-Path $portable "licenses\FFMPEG-BUILD.txt") -Algorithm SHA256).Hash
        "licenses/THIRD_PARTY_NOTICES.md" = (Get-FileHash (Join-Path $portable "licenses\THIRD_PARTY_NOTICES.md") -Algorithm SHA256).Hash
        "FIELD_CANDIDATE.md" = (Get-FileHash (Join-Path $portable "FIELD_CANDIDATE.md") -Algorithm SHA256).Hash
        "START-POLAR-RM.bat" = (Get-FileHash (Join-Path $portable "START-POLAR-RM.bat") -Algorithm SHA256).Hash
        "POLAR-RM.exe" = (Get-FileHash (Join-Path $portable "POLAR-RM.exe") -Algorithm SHA256).Hash
    }
    $resourceTreeHash = Get-TreeHash $python $sourceRoot (Join-Path $portable "resources")
    $manifest = [ordered]@{
        schema = 1
        product = "POLAR-RM Field Candidate"
        verdict = "NOT READY FOR REAL MATCH"
        git_commit = $commit
        source_kind = $sourceKind
        built_at_utc = [DateTime]::UtcNow.ToString("o")
        protocol_version = "1.3.1"
        python = "$pythonVersion"
        dependencies = $dependencyLines
        bundled_packages = $bundledPackages
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
            backend_protocol = "PASS"
            field_candidate = "PASS"
            ui_backend_regression = "PASS"
            gpu_compositor = "PASS"
            gpu_renderer_runtime = "PASS"
            startup_gpu_runtime = "PASS"
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
    Write-Host "Self-contained field candidate built: $outputPath"
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Assert-UnderPath $stageRoot ([System.IO.Path]::GetTempPath())
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
