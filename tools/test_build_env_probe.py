"""发布脚本的构建环境探测：必须真的把解释器跑起来，不能只查文件在不在。

问题
----
`tools/build_release.ps1` 原来在**第一次调用 venv 解释器之后**才检查
``.venv`` 存不存在，而且只用了 ``Test-Path``。两种坏法都拿不到准确提示：

1. ``.venv`` 整个不存在 → 脚本死在规格校验那步，报
   ``polar protocol spec is not usable in the release source tree``，
   而真正的原因是"没装构建环境"。
2. ``.venv`` 在、但**解释器是死的** → ``python.exe`` 与 ``pyinstaller.exe``
   都好好地躺在磁盘上，``Test-Path`` 照样通过；可是
   ``pyvenv.cfg`` 指向的 base 解释器已经被删掉，进程一跑就
   ``exit 103  No Python at '...'``。本机实测就是这个状态
   （``.venv`` 指向一个已被删除的 codex runtime）。

两种情况下，那句写得很准的 ``Build environment missing`` 都**永远不可达**。
"报错信息指向错误的根因"在发布流程里代价很高：人会去查协议规格、
去查 git archive，唯独不会想到去重建 venv。

修法
----
把探测挪到第一次使用之前，并且**真的执行一次解释器**（``-c "print(version)"``），
失败时把 probe 输出和 ``pyvenv.cfg`` 的 base 解释器路径一起打出来 ——
因为"base 被移走了"是需要人手工处理的情况，不重跑一遍是修不好的。

跑法：``python tools/test_build_env_probe.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "build_release.ps1"

FAILURES: list[str] = []
PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


def _read() -> str:
    if not SCRIPT.is_file():
        print(f"[SKIP] 找不到 {SCRIPT}")
        raise SystemExit(0)
    return SCRIPT.read_text(encoding="utf-8-sig")


def test_script_exists_and_parses_as_powershell() -> None:
    text = _read()
    check("build_release.ps1 存在且非空", len(text) > 1000, f"{len(text)} 字符")
    # 括号配平是最廉价的语法自检（真正解析要 PowerShell）
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            break
    check("花括号配平", depth == 0, f"最终深度 {depth}")


def test_env_probe_runs_before_any_other_work() -> None:
    """探测必须是**整个脚本的第一件事**，不只是"第一次用解释器之前"。

    上一轮的版本已经把探测提到第一次调用解释器之前了，但那还不够 —— 它当时
    排在三个 `Assert-Hash`（资源哈希校验）和 `git archive` 源码暂存**之后**。
    于是 venv 坏的时候操作员的实际顺序是：

        1. 找 FFmpeg，跑哈希        2. 找两张壁纸，各跑哈希
        3. 检查 GPL 正文长度         4. 等 git archive 暂存整个仓库
        5. **然后**才被告知 venv 解释器是死的

    前四步全是白做的。而且第 1 步失败时人会以为"是我路径给错了"，
    去翻目录、翻文档 —— 真正的原因根本不在那里。

    只比"探测 vs 第一次用解释器"抓不到这个：那个位置关系在上一轮就已经
    成立了。这条断言比的是**探测 vs 一切别的检查**。
    """
    text = _read()
    probe = text.find("$venvProbe = & $python")
    check("找得到探测那一行", probe > 0, f"index={probe}")

    # 判据是 `probe < at`（探测在前的**前面**）。第一版写成了 `at < probe`，
    # 于是"资源哈希校验在探测之前"反倒被判成通过 —— 断言的方向反了，
    # 而方向写反的断言比没有断言更糟：它看起来在守着，绿灯其实没意义。
    #
    # 标记必须挑**只出现在代码里**的那一处。第一版用 `git archive` 和
    # `$requiredAssets` 直接 find，结果两次都先命中了本次改动**写在注释里的**
    # 那些词（注释在 177/183/194 行，代码在 272/235 行），于是断言红，
    # 而实际顺序是对的。教训和前面几次一样：搜索标记时先确认它不会在注释里出现。
    for marker, label in (
        ("Assert-Hash $FfmpegPath", "资源哈希校验"),
        ('"git archive failed"', "git archive 源码暂存"),
    ):
        at = text.find(marker)
        check(f"探测排在{label}之前",
              0 <= at and probe < at, f"{label}@{at} 探测@{probe}")


def test_asset_params_are_not_mandatory() -> None:
    """三个资源参数**不许**是 ``Mandatory=$true``。

    这条是上一条断言的前提：PowerShell 在**进脚本体之前**就拦下缺参数的用户，
    所以只要这三个还是 Mandatory，探测代码根本轮不到执行 ——
    操作员会先看到"缺少必需参数"，然后去找资产，最后才发现 venv 是死的。

    代价是把 Mandatory 去掉之后，校验得挪到脚本体里（探测之后），
    报错措辞要写清楚是哪个参数、要给什么。
    """
    text = _read()
    param_block = text[text.find("param("):text.find("\n)", text.find("param("))]
    for name in ("FfmpegPath", "TexasWallpaperPath", "VendingWallpaperPath"):
        line = next((ln for ln in param_block.splitlines() if "$" + name in ln), "")
        check(f"{name} 不再是 Mandatory",
              "Mandatory" not in line and "$" + name in line,
              f"实际：{line.strip()}")

    # 标记要用 `$requiredAssets = @(`：直接 find `$requiredAssets` 会先命中
    # 这次改动写在注释里的那句 "下面的 $requiredAssets"，位置在探测之前。
    probe = text.find("$venvProbe = & $python")
    assets = text.find("$requiredAssets = @(")
    check("资源参数校验挪到了脚本体里（探测之后）",
          probe > 0 and assets > probe, f"探测@{probe} 资源校验@{assets}")


def test_probe_suppresses_terminating_error() -> None:
    """探测必须临时把 ``$ErrorActionPreference`` 降下来。

    不降的话有个反直觉的后果：``2>&1`` 把解释器写到 stderr 的
    ``No Python at ...`` 变成 PowerShell 的错误记录，而在 ``Stop`` 模式下
    **那本身就是终止性错误** —— 脚本停在探测这一行就结束了，
    我们写的那段"删掉 .venv 重建、不要只是重跑"根本来不及显示。

    实测过：不加这一段，操作员看到的是解释器自己那句 "No Python at"，
    恰恰不是可执行的下一步。所以这条不是洁癖，是那句话能不能被看见的问题。
    """
    text = _read()
    probe = text.find("$previousErrorAction = $ErrorActionPreference")
    restore = text.find("$ErrorActionPreference = $previousErrorAction")
    run = text.find("$venvProbe = & $python")
    check("探测前降低了 ErrorActionPreference", 0 < probe < run,
          f"降低@{probe} 实跑@{run}")
    check("探测后恢复了 ErrorActionPreference", run < restore,
          f"实跑@{run} 恢复@{restore}")
    check("判定用的是退出码，不是 $LASTEXITCODE 被后续覆盖",
          "if ($venvProbeExit -ne 0)" in text,
          "没看到 if ($venvProbeExit -ne 0)")


def test_env_probe_runs_before_first_interpreter_use() -> None:
    """核心断言：探测必须在第一次**真正使用**解释器之前。

    注意"探测自己也是一次 `& $python -c`"，所以不能拿探测行本身去比 ——
    第一版就这么写，结果是拿探测跟自己比，永远不相等（第一版红的就是这个）。
    这里从探测之后开始找调用。
    """
    text = _read()

    probe = text.find("sys.version_info")
    check("能找到'真的把解释器跑起来'的探测", probe > 0, f"probe index={probe}")
    if probe <= 0:
        return

    call_re = r"&(?:\s+\$python|\s*\(Join-Path[^\n]*python\.exe\))\s+-c"
    calls = [m.start() for m in re.finditer(call_re, text)]
    check("脚本里确实有调用解释器的地方", bool(calls), f"找到 {len(calls)} 处")
    if not calls:
        return

    # 探测之后的第一次调用才是"真正使用"
    after = [c for c in calls if c > probe]
    check("探测之后还有真正的解释器调用", bool(after), "探测后面没有用到解释器，这断言就没意义")
    if after:
        check(
            "探测在第一次真正使用解释器之前",
            probe < min(after),
            f"探测在 {probe}，探测后第一次调用在 {min(after)}",
        )


def test_probe_actually_executes_the_interpreter() -> None:
    """不能只查文件在不在，必须真的执行一次。"""
    text = _read()
    start = text.find("sys.version_info")
    # 窗口要够宽：提示语在探测之后好几百字符处，第一版只取 +400 就把
    # "Delete .venv ..." 那句切掉了，于是断言红在自己写窄了。
    probe_region = text[max(0, start - 1400): start + 1400]
    check("探测里检查了 python.exe / pyinstaller.exe 是否存在",
          "Test-Path" in probe_region and "python.exe" in probe_region,
          "应当先查文件存在，再查能不能跑")
    check("探测里检查了 $LASTEXITCODE（解释器是否真的执行成功）",
          "LASTEXITCODE" in probe_region,
          "没有它就退化成只查文件在不在，正是原来的 bug")
    check("探测失败时把 pyvenv.cfg 的 base 解释器路径打出来",
          "pyvenv.cfg" in probe_region,
          "base 被移走是需要人手工修的，必须说清楚是哪一个")
    check("探测失败时给出了可执行的修复建议（删掉 .venv 重建）",
          "Delete .venv" in probe_region,
          "只说'坏了'没用，要说怎么修")


def test_old_duplicate_check_removed() -> None:
    """原来那份重复的、位置错误的检查应当已经删掉。

    只数 `throw "Build environment missing`，不数注释里引用的那一句 ——
    注释提到它是解释"为什么原来不可达"的一部分，数进去会误报。
    """
    text = _read()
    throws = list(re.finditer(r'throw\s+"Build environment missing', text))
    check("只应有一处 throw 'Build environment missing'", len(throws) == 1, f"找到 {len(throws)} 处")
    check("那条准确提示已带上更具体的目录信息",
          "Build environment missing at" in text,
          "应当指出是哪个 .venv")


def test_probe_would_catch_a_dead_interpreter() -> None:
    """拿本机真实的坏 .venv 验证探测逻辑本身有效。"""
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        check("本机没有 .venv，跳过实机验证", True, "没有坏 venv 可测")
        return

    # 完全复刻 build_release.ps1 里的探测两行
    import subprocess

    proc = subprocess.run(
        [str(python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True, text=True, errors="replace",
    )
    check(
        f"探测能区分'文件在'与'解释器活着'（本机 exit={proc.returncode}）",
        (proc.returncode == 0) != python.is_file() or proc.returncode != 0,
        f"文件在={python.is_file()} exit={proc.returncode}",
    )
    if proc.returncode != 0:
        check(
            "坏解释器的输出里带有 base 路径线索（No Python at '...'）",
            "No Python at" in (proc.stdout + proc.stderr),
            f"实际输出 {(proc.stdout + proc.stderr)[:120]!r}",
        )
        check(
            "这正是旧 Test-Path 检查抓不到的那种情况",
            python.is_file(),
            "如果文件都不在，那旧检查本来就有效；文件在但跑不起来才是 bug",
        )
    else:
        check("本机 .venv 其实是好的（探测通过）", proc.returncode == 0)


def main() -> int:
    tests = [
        test_script_exists_and_parses_as_powershell,
        test_env_probe_runs_before_any_other_work,
        test_asset_params_are_not_mandatory,
        test_probe_suppresses_terminating_error,
        test_env_probe_runs_before_first_interpreter_use,
        test_probe_actually_executes_the_interpreter,
        test_old_duplicate_check_removed,
        test_probe_would_catch_a_dead_interpreter,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"构建环境探测：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"构建环境探测：全部通过（{PASSED} 项）")
    print("坏的 .venv 会在第一次用之前被抓到，并说清楚是哪个 base 解释器没了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
