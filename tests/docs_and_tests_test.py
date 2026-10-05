"""文档与测试基础设施的自证测试（docs-and-tests 组）。

为什么要有这个文件
------------------
这一组改的三样东西全都是**"没人看得出它过期了"**的东西：

1. ``MODULE_BACKEND_MAP.md`` 是脚本生成的产物。生成过之后就静静躺在那儿，
   代码改了它不会自己变 —— 而过期的行号照样能点开，只是点到了别的函数。
2. ``BACKEND_MATCH_READINESS.md`` 里"选 TeamBench / 按 4"这种指引，
   在场景早被删除之后仍然能读、读起来还很有道理。
3. ``tests/smoke_test.py`` 里定义了却没接进 runner 的测试函数，
   在文件里和"真正在跑的测试"长得一模一样。

所以本文件不测业务，只测**上面三样东西是不是真的和代码一致**，并把每一处
结论钉成断言：

  A. MODULE_BACKEND_MAP.md 与生成器输出**逐字节相同**（= 产物没过期）
  B. 文档第 4 节里每个 `文件:行号` 都真的指向那一行源码（= 行号没漂）
     并且生成器源码里**不含任何写死的行号字面量**（= 下次不会再漂）
  C. BACKEND_MATCH_READINESS.md 不再教人按 4 选 TeamBench；
     它引用的每个 `tests/*.py` 都真的存在
  D. CHANGELOG.md 不停在最后一个"已记"提交太久
  E. tests/smoke_test.py 的每个 test_* 都被 runner 调用（本次补进 9 个）
  F. tests/ 下的测试**注册**情况：verify_all / build_release 的漏项清单化，
     并且"新增一个没注册的测试"当场变红

跑法：``python tests/docs_and_tests_test.py``
（不碰任何视觉文件；不写盘；纯离线检查，几秒内跑完。）
"""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAP_MD = ROOT / "MODULE_BACKEND_MAP.md"
READINESS_MD = ROOT / "BACKEND_MATCH_READINESS.md"
CHANGELOG_MD = ROOT / "CHANGELOG.md"
GENERATOR = ROOT / "tools" / "module_backend_map.py"
SMOKE = ROOT / "tests" / "smoke_test.py"

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


def note(label: str, detail: str = "") -> None:
    """**只报告，不计入通过数，也不算失败。**

    第 108 轮加。存在的理由就是下面那个曾经撒谎的地方：

    ```python
    if not other.exists():
        check("tools/MODULE_BACKEND_MAP.md 存在", True)   # \u7f3a\u5931\u65f6\u624d\u6253\u5b83
    ```

    这行**只在文件不存在时才执行**，却打印「存在」——
    **标签断言的正好是它所在分支条件的反面。**
    一个空转断言只是没用；**一个反向的断言是在骗人**：
    它会把「文档不见了」记成「文档存在，已通过」。

    打印出来不等于通过。所以这里用 `note`：**看得见，但不进通过数。**
    """
    print(f"[NOTE] {label} {detail}".rstrip())


def load_generator():
    spec = importlib.util.spec_from_file_location("module_backend_map_probe", GENERATOR)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# A. MODULE_BACKEND_MAP.md 没有过期
# ---------------------------------------------------------------------------

def test_map_is_not_stale() -> None:
    gen = load_generator()
    text = gen.render()
    on_disk = MAP_MD.read_text(encoding="utf-8")
    check(
        "MODULE_BACKEND_MAP.md 与生成器输出逐字节相同（产物没过期）",
        on_disk == text,
        f"盘上 {len(on_disk)} 字符 vs 生成 {len(text)} 字符；"
        f"修法：python tools/module_backend_map.py",
    )
    # 生成器本身可导入、可渲染，且产物里确实有那 59 个模块
    check("生成器 render() 可用且非空", len(text) > 10000, f"{len(text)} 字符")
    for probe in ("## 0.", "## 1. 59 个模块逐个对照", "## 2.", "## 3.", "## 4."):
        check(f"产物含章节 {probe!r}", probe in text)


def test_generator_check_mode_agrees() -> None:
    """`--check` 的判定必须和"文件内容"一致：绿=一致，红=不一致且不改文件。

    这里用真实子进程跑一次（而不是调 main()），因为 --check 的价值就在于
    它是一条**能进 CI 的命令**：exit code 必须可信。
    """
    proc = subprocess.run(
        [sys.executable, "-u", str(GENERATOR), "--check"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check("--check 在产物新鲜时 exit 0", proc.returncode == 0,
          f"exit={proc.returncode} out={(proc.stdout or '').strip()[:200]}")
    before = MAP_MD.read_bytes()
    check("--check 不写盘", MAP_MD.read_bytes() == before, "check 模式改动了 MODULE_BACKEND_MAP.md")

    # 在临时目录里伪造"过期产物"，证明 --check 会红、新鲜会绿，且不碰真文件
    stale = subprocess.run(
        [sys.executable, "-u", "-c", _STALE_PROBE],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check("--check 判定：过期=1 / 新鲜=0", stale.returncode == 0,
          f"probe exit={stale.returncode} out={(stale.stdout or '').strip()[-160:]}")


_STALE_PROBE = """
# 在**临时目录**里伪造"过期产物"，验证 main() 的判定与 exit code。
# 不用真文件：改仓库里的 MODULE_BACKEND_MAP.md 去试判据，等于拿真产物冒险
# （进程被 kill 就会留下一份被截断的文档）。
import importlib.util, sys, tempfile
from pathlib import Path
ROOT = Path.cwd()
spec = importlib.util.spec_from_file_location('m', ROOT / 'tools' / 'module_backend_map.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
tmp = Path(tempfile.mkdtemp(prefix='map_probe_'))
m.ROOT = tmp                      # main() 的 target 由 ROOT 推导
m.render = lambda: 'GENERATED\\n'  # 生成器"认为"的产物
target = tmp / 'MODULE_BACKEND_MAP.md'
sys.argv = ['x', '--check']
target.write_text('STALE\\n', encoding='utf-8')
rc_stale = m.main()
target.write_text('GENERATED\\n', encoding='utf-8')
rc_fresh = m.main()
print('RC stale=%d fresh=%d' % (rc_stale, rc_fresh))
raise SystemExit(0 if (rc_stale, rc_fresh) == (1, 0) else 2)
"""


# ---------------------------------------------------------------------------
# B. 文档里的每个 file:line 都真的指向那一行（行号漂移守卫）
# ---------------------------------------------------------------------------

def test_section4_line_numbers_point_at_real_code() -> None:
    """逐个核对第 4 节里 `文件:行号` 的**那一行**确实是预期的符号。

    这条是 A 的补充：A 只保证"文件 == 生成器输出"，而生成器输出里的行号
    是它自己算的；如果 locate() 的匹配规则写错（当年就写错过：整行相等匹配
    命中不了签名换行写的长定义），两边会**一致地错**。这里从文档文本里
    重新解析出行号，独立核对一次。
    """
    gen = load_generator()
    md = MAP_MD.read_text(encoding="utf-8")
    checked = 0
    for key, (rel, needle, label) in gen.ANCHORS.items():
        anchor = gen.locate(key)
        if "未找到" in anchor or "缺失" in anchor:
            check(f"锚点 {key} 能定位", False, anchor)
            continue
        lineno = int(anchor.split(":")[1].split()[0])
        src = (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        actual = src[lineno - 1].strip()
        want = needle.strip()
        ok = actual.startswith(want)
        checked += 1
        check(f"锚点 {key} 行号指向真代码（{rel}:{lineno}）", ok,
              f"期望以 {want!r} 开头，实际 {actual[:70]!r}")
        # 文档里出现的那一行必须自带这个行号（防止有人手改 .md 对齐旧数字）
        check(f"文档里 {rel}:{lineno} 与锚点一致", f"{rel}:{lineno}" in md,
              f"文档里找不到 {rel}:{lineno}")
    check("11 个锚点全部核对", checked == len(gen.ANCHORS),
          f"{checked}/{len(gen.ANCHORS)}")


def test_generator_has_no_hardcoded_line_numbers() -> None:
    """生成器源码里不得再出现 `polar_link.py:597` 这类写死的行号字面量。

    这是"下次不会再漂"的保证：行号只能由 locate() 在生成时现查。
    判据只看**代码**：文档字符串与 `#` 注释里提到"当年写死的是哪几个"
    是解释性文字，允许存在（否则这条断言会把解释本身判成罪证）。
    """
    src = GENERATOR.read_text(encoding="utf-8")
    code_lines = []
    in_doc = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(('"""', "'''")):
            # 简易三引号配对：同一行开闭则不进文档态
            if not (stripped.endswith(('"""', "'''")) and len(stripped) > 5):
                in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    literal = re.findall(r"[A-Za-z0-9_/]+\.py:\d+", code)
    check("生成器代码里没有写死的 .py:行号 字面量（注释/文档字符串除外）", not literal, str(literal[:5]))
    # locate() 必须真的按"前缀"匹配：签名换行写的长定义行也要能命中
    gen = load_generator()
    for key in ("tx_pump", "tx_frame", "tx_intent"):
        anchor = gen.locate(key)
        check(f"locate({key}) 前缀匹配命中", "未找到" not in anchor and "缺失" not in anchor, anchor)


def test_herolob_setting_count_is_derived() -> None:
    """第 4 节里 HeroLob 的设置项数必须是数出来的，不能再是手写的 26。"""
    from rm_client.model import build_default_registry

    mod = build_default_registry().get("HeroLob")
    real = len(mod.settings) if mod is not None else -1
    md = MAP_MD.read_text(encoding="utf-8")
    m = re.search(r"\| \*\*HeroLob\*\* \| (\d+) 个设置项", md)
    check("文档里写了 HeroLob 的设置项数", m is not None)
    if m is not None:
        check("HeroLob 设置项数与注册表一致", int(m.group(1)) == real,
              f"文档 {m.group(1)} vs 注册表 {real}")


# ---------------------------------------------------------------------------
# C. BACKEND_MATCH_READINESS.md 不再教人按 4 选 TeamBench
# ---------------------------------------------------------------------------

def _instructional_lines(text: str) -> list[str]:
    """只留下"读者会照着做"的正文行。

    要排掉三类**故意保留**的历史痕迹，否则判据会把自己的解释判成罪证：
      1. `## 2026-10-01 对账记录` 整节（它逐条引用了被删掉的原文）；
      2. 划掉的旧行（`~~…~~`）与标了"作废/已作废"的行；
      3. `> ⚠️` 提醒块（点名那个 git 历史里从未存在的测试文件）。
    """
    out: list[str] = []
    in_record = False
    in_warn_quote = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_record = "对账记录" in line
        if in_record:
            continue
        stripped = line.lstrip()
        if stripped.startswith(">"):
            # ⚠ 提醒块整块都排除（续行里也会点名那个不存在的文件）
            if "⚠" in line:
                in_warn_quote = True
                continue
            if in_warn_quote:
                continue
        else:
            in_warn_quote = False
        if "~~" in line or "作废" in line:
            continue
        out.append(line)
    return out


def test_no_retired_scenario_instructions() -> None:
    text = READINESS_MD.read_text(encoding="utf-8")
    lines = _instructional_lines(text)
    forbidden = [
        (r"启动页第\s*4\s*项", "仍在教人选启动页第 4 项"),
        (r"按\s*`?4`?\s*选", "仍在教人按 4 选"),
        (r"快捷键\s*`?4`?", "仍写着快捷键 4"),
    ]
    for pat, why in forbidden:
        hits = [l for l in lines if re.search(pat, l)]
        check(f"BACKEND_MATCH_READINESS.md 没有「{why}」", not hits,
              " | ".join(h.strip()[:90] for h in hits[:2]))
    # 场景真值：SCENARIOS 里必须没有 TeamBench
    from rm_client.scenarios import SCENARIOS

    check("SCENARIOS 里没有 TeamBench", "TeamBench" not in SCENARIOS, str(list(SCENARIOS)))
    check("SCENARIOS 正好三个", list(SCENARIOS) == ["OfficialEvent", "CampusSimulation", "LabDebug"],
          str(list(SCENARIOS)))
    # 文档必须自己交代这次对账，否则后来人不知道那些划掉的话为什么划掉
    check("文档含 2026-10-01 对账记录", "2026-10-01 对账记录" in text)


def test_referenced_test_files_exist() -> None:
    """文档里引用的每个 `tests/xxx.py` 都必须真的存在。

    实测踩过：`tests/team_bench_entry_check.py` 被引用了三处，而它在 git 历史里
    **从未存在** —— 照着找的人只会浪费一轮。（那条引用现在只出现在 ⚠️ 提醒里，
    属于"故意点名的历史错误"，所以不参与本判据。）
    """
    text = READINESS_MD.read_text(encoding="utf-8")
    lines = _instructional_lines(text)
    body = "\n".join(lines)
    refs = sorted(set(re.findall(r"tests/[A-Za-z0-9_]+\.py", body)))
    check("文档正文引用了测试文件", bool(refs), "一个都没引用，正则可能失效")
    missing = [r for r in refs if not (ROOT / r).exists()]
    check(f"文档正文引用的 {len(refs)} 个测试文件全部存在", not missing, str(missing))
    # 引用到函数名时，函数也要真的在那个文件里
    for path, func in re.findall(r"(tests/[A-Za-z0-9_]+\.py)::([A-Za-z0-9_]+)", body):
        if not (ROOT / path).exists():
            check(f"{path}::{func} 文件存在", False, "文件不存在")
            continue
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        check(f"{path}::{func} 函数存在", func in names)


# ---------------------------------------------------------------------------
# D. CHANGELOG.md 不停在很久以前
# ---------------------------------------------------------------------------

def test_changelog_is_not_far_behind() -> None:
    text = CHANGELOG_MD.read_text(encoding="utf-8")
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%ad", "--date=short"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    newest = (proc.stdout or "").strip()
    if not newest:
        check("能读到最新提交日期", False, (proc.stderr or "")[:120])
        return
    newest_d = date.fromisoformat(newest)
    dates = [date.fromisoformat(d) for d in re.findall(r"(20\d\d-\d\d-\d\d)", text)]
    check("CHANGELOG 里有日期", bool(dates))
    if not dates:
        return
    freshest = max(dates)
    gap = (newest_d - freshest).days
    check(f"CHANGELOG 记到了最新提交附近（差 {gap} 天）", gap <= 30,
          f"最新提交 {newest_d}，CHANGELOG 最新记录 {freshest}；"
          f"超过 30 天视为又停更了，补完把日期写进去")
    if gap > 3:
        print(f"[WARN] CHANGELOG 比最新提交落后 {gap} 天（{freshest} -> {newest_d}），"
              f"新提交没有进 changelog")


# ---------------------------------------------------------------------------
# E. tests/smoke_test.py：定义了就得被 runner 调用
# ---------------------------------------------------------------------------

#: 本轮补接的 9 个（8 个 VisionKillDetector 击杀判定分支 + 1 个 GL pixel-store 回归）。
#: 单独 pin 住它们的名字：上面那条"一个都不能漏"是集合级断言，
#: 这里再加一层**名单级**断言，防止有人把某个具体测试从 runner 里删掉、
#: 同时在别的文件里补一个同名测试来"骗过"集合比较。
RECONNECTED_SMOKE_TESTS = (
    "test_vision_kill_same_empty_snapshot_never_times_out",
    "test_vision_kill_exposure_hold_blocks_missing_path",
    "test_vision_kill_reliable_mode_rejects_missing_path",
    "test_vision_kill_dim_uses_target_baseline",
    "test_vision_kill_no_lit_history_no_miss",
    "test_vision_kill_exposure_pauses_not_clears",
    "test_vision_kill_dim_lamp",
    "test_local_target_identity_survives_extinguish",
    "test_dynamic_texture_upload_restores_qt_text_pixel_store_state",
)


def _smoke_wiring() -> tuple[set[str], set[str]]:
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    called: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                and t.left.id == "__name__"
                and any(isinstance(op, ast.Eq) for op in t.ops)):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id.startswith("test_")):
                    called.add(sub.func.id)
    return defined, called


def test_smoke_runner_calls_every_test() -> None:
    defined, called = _smoke_wiring()
    missing = sorted(defined - called)
    check(f"smoke_test.py 的 {len(defined)} 个测试函数全部被 runner 调用", not missing,
          f"{len(defined)} 定义 / {len(called)} 调用；漏跑：{missing}")
    for name in RECONNECTED_SMOKE_TESTS:
        check(f"补接的测试仍在 runner 里：{name}", name in called and name in defined)


def test_smoke_reconnected_tests_still_pass() -> None:
    """真的把这 9 个跑一遍——接进 runner 不等于它们还绿。

    import 只花 0.2s，9 个函数合计 ~5ms，所以直接真跑最省事。
    """
    import os

    keys = ("QT_QPA_PLATFORM", "POLAR_DISABLE_LOCAL_CAPTURE", "POLAR_DISABLE_MEDIA_PRELOAD")
    backup = {k: os.environ.get(k) for k in keys}
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("POLAR_DISABLE_LOCAL_CAPTURE", "1")
    os.environ.setdefault("POLAR_DISABLE_MEDIA_PRELOAD", "1")
    try:
        spec = importlib.util.spec_from_file_location("smoke_probe", SMOKE)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        for name in RECONNECTED_SMOKE_TESTS:
            try:
                getattr(mod, name)()
                check(f"补接的测试实跑通过：{name}", True)
            except Exception as exc:  # noqa: BLE001 - 测试要看到任何异常
                check(f"补接的测试实跑通过：{name}", False, f"{type(exc).__name__}: {exc}")
    finally:
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# F. 测试注册漏项：清单化 + 防新增漏项
# ---------------------------------------------------------------------------

def _verify_all_suites() -> set[str]:
    spec = importlib.util.spec_from_file_location("verify_all_probe", ROOT / "tools" / "verify_all.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return {f"{n}.py" for n in mod.PYTHON_SUITES}


def _build_release_suites() -> set[str]:
    ps = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"tests[\\/]+([A-Za-z0-9_]+\.py)", ps))


#: 已知的注册漏项（**清单，不是许可**）：verify_all / build_release 都是
#: 显式清单制，不会自动发现测试，所以新加一个测试忘了注册 = 永不执行。
#: 这两个文件不属于 docs 组（改它们要动别的负责人的文件），因此这里把漏项
#: 钉成常量：新增任何一个未注册的测试文件 → 当场红；补了注册 → 清单会变短，
#: 同样要改这里。两者都可以用 ``python tests/docs_and_tests_test.py --update``
#: 一键重写（改了会打印改了什么）。
KNOWN_UNREGISTERED = {
    "verify_all": (
        "field_candidate_test.py", "gpu_renderer_runtime_smoke.py", "startup_gpu_runtime_smoke.py",
        "transition_visual_check.py",
    ),
    "build_release": (
        "autoaim_settings_test.py", "combat_events_test.py", "config_store_io_test.py",
        "control_idle_zero_direction_test.py", "control_loop_test.py", "economy_safety_test.py", "field_log_async_test.py",
        "gimbal_host_request_test.py", "hotpath_cache_test.py", "hud_batch10_settings_test.py",
        "hud_batch13_settings_test.py", "hud_batch14_settings_test.py", "hud_batch16_killfx_test.py",
        "hud_batch17_crosshair_test.py", "hud_batch18_powerguard_test.py", "hud_batch19_bufftimeline_test.py",
        "hud_batch20_robotoverlay_color_test.py", "hud_batch21_radaroverlay_map_test.py", "hud_batch8_settings_test.py",
        "hud_batch9_settings_test.py", "hud_overlay_settings_test.py", "hud_status_settings_test.py",
        "hud_telemetry_settings_test.py", "polar_link_failclosed_test.py", "robot_health_field_test.py",
        "dead_callers_test.py", "settings_access_call_sites_test.py", "settings_doc_consistency_test.py", "tongji_postprocess_parity_test.py", "transition_visual_check.py",
        "vision_pipeline_hardening_test.py",
    ),
}


def _registration_gaps() -> dict[str, list[str]]:
    all_tests = {p.name for p in (ROOT / "tests").glob("*.py")}
    return {
        "verify_all": sorted(all_tests - _verify_all_suites()),
        "build_release": sorted(all_tests - _build_release_suites()),
    }


def _rewrite_pins(gaps: dict[str, list[str]]) -> None:
    """把实测漏项写回 KNOWN_UNREGISTERED（仅 --update 时调用）。"""
    src = Path(__file__).read_text(encoding="utf-8")
    head = src.index("KNOWN_UNREGISTERED = {")
    tail = src.index("\n}\n", head) + len("\n}\n")
    block = "KNOWN_UNREGISTERED = {\n"
    for key in ("verify_all", "build_release"):
        block += f'    "{key}": (\n'
        names = gaps[key]
        line: list[str] = []
        for name in names:
            line.append(f'"{name}"')
            if len(line) == 3:
                block += "        " + ", ".join(line) + ",\n"
                line = []
        if line:
            block += "        " + ", ".join(line) + ",\n"
        block += "    ),\n"
    block += "}\n"
    Path(__file__).write_text(src[:head] + block + src[tail:], encoding="utf-8")
    print(f"[UPDATE] KNOWN_UNREGISTERED 已重写："
          + "，".join(f"{k} {len(v)} 项" for k, v in gaps.items()))


def test_test_registration_gaps_are_pinned() -> None:
    all_tests = {p.name for p in (ROOT / "tests").glob("*.py")}
    va = _verify_all_suites()
    br = _build_release_suites()
    gaps = {"verify_all": sorted(all_tests - va), "build_release": sorted(all_tests - br)}
    print(f"[INFO] tests/*.py={len(all_tests)}  verify_all 注册 {len(va)}"
          f"（漏 {len(gaps['verify_all'])}）  build_release 注册 {len(br)}"
          f"（漏 {len(gaps['build_release'])}）")
    if "--update" in sys.argv:
        _rewrite_pins(gaps)
    for key in ("verify_all", "build_release"):
        pinned = sorted(KNOWN_UNREGISTERED[key])
        actual = gaps[key]
        added = sorted(set(actual) - set(pinned))
        fixed = sorted(set(pinned) - set(actual))
        # ⚠ **第 153 轮更正：这里原来是一句 `check(..., actual == pinned)`。**
        #
        # **那是一个反向断言** —— 和第 141 轮修掉的那句
        # 「`MODULE_BACKEND_MAP.md` 的行号债务清单非空」**是同一个错**：
        # **有人把那四个套件注册进去（把债还了），这个套件反而变红。**
        #
        # **门禁该奖励还债，不该惩罚。** 所以拆成两件事：
        #
        # * `added`（**新**冒出来的漏项）—— **那才是真问题，判红**；
        # * `fixed`（**已登记**、所以从债务清单里该消失的）—— **那是好消息，只记一笔**。
        #
        # ⚠ **为什么不顺手把 `fixed` 从 `KNOWN_UNREGISTERED` 里删掉**：
        #   那是 `--update` 的活（`_rewrite_pins` 会重写整个块）。
        #   **判红之外再自动改源文件 —— 那会让「谁改了清单」看不出来。**
        check(f"{key} 没有**新**冒出来的注册漏项（当前债务 {len(actual)} 项）", not added,
              f"新增未注册={added}；要么去 verify_all/build_release 注册、"
              f"要么跑 `python tests/docs_and_tests_test.py --update` 把它们记进债务清单")
        if fixed:
            note(f"{key} 有 {len(fixed)} 项**已经还清**（从债务清单里该删掉）：{fixed}")
    # 注册表里写着的套件必须真的存在（否则是 SKIP 而非通过）
    ghost = sorted(n for n in va if not (ROOT / "tests" / n).exists())
    check("verify_all 注册的套件都真实存在", not ghost, str(ghost))


def test_other_map_doc_stale_lines_are_reported() -> None:
    """`tools/MODULE_BACKEND_MAP.md`（另一份手写对照表）的行号债务：**只报告**。

    那份文件不属于本组，不能改；把它写成一个会红的断言只会天天红。
    所以这里把"哪几行指到了什么"如实打出来，交由它的负责人处理。
    """
    other = ROOT / "tools" / "MODULE_BACKEND_MAP.md"
    if not other.exists():
        # 第 108 轮：这里原来写的是 `check("...存在", True)`，
        # **而这一行只在文件缺失时才执行** —— 标签说的正好是分支条件的反面。
        # 改成 note：**照样看得见，但不再冒充通过。**
        note("tools/MODULE_BACKEND_MAP.md 不存在，本项无可对拍", "（该文件不属于本组，不在这里断言它）")
        return
    text = other.read_text(encoding="utf-8")
    refs = re.findall(r"([A-Za-z0-9_./\\]+\.py):(\d+)", text)
    suspects: list[str] = []
    for name, num in refs:
        lineno = int(num)
        cands = list((ROOT / "rm_client").rglob(name)) or list((ROOT / "tools").rglob(name))
        if not cands:
            suspects.append(f"{name}:{num}（文件找不到）")
            continue
        src = cands[0].read_text(encoding="utf-8", errors="replace").splitlines()
        got = src[lineno - 1].strip() if lineno <= len(src) else "<超出文件末尾>"
        suspects.append(f"{name}:{num} -> {got[:48]}")
    print(f"[INFO] tools/MODULE_BACKEND_MAP.md 共 {len(refs)} 处 .py:行号（债务清单，只报告不改）：")
    for s in suspects:
        print(f"       {s}")
    # ⚠ 第 141 轮：原来是 `check(..., bool(suspects))`，
        #   **债务还清的时候它红。那就是“今天没人拿到 RED “”。
        #   门禁应该**奖励付债，不是惩罚他**。改成 `note`（它不计入通过数）。
        note("tools/MODULE_BACKEND_MAP.md 仍有 %d 处行号债务" % len(suspects))


def main() -> int:
    print("[docs] A. MODULE_BACKEND_MAP.md 产物新鲜度")
    test_map_is_not_stale()
    test_generator_check_mode_agrees()
    print("\n[docs] B. 行号锚点没有漂")
    test_section4_line_numbers_point_at_real_code()
    test_generator_has_no_hardcoded_line_numbers()
    test_herolob_setting_count_is_derived()
    print("\n[docs] C. BACKEND_MATCH_READINESS.md 与场景真值一致")
    test_no_retired_scenario_instructions()
    test_referenced_test_files_exist()
    print("\n[docs] D. CHANGELOG 不停更")
    test_changelog_is_not_far_behind()
    print("\n[docs] E. smoke_test.py 每个测试都被 runner 调到")
    test_smoke_runner_calls_every_test()
    test_smoke_reconnected_tests_still_pass()
    print("\n[docs] F. 测试注册漏项清单化")
    test_test_registration_gaps_are_pinned()
    test_other_map_doc_stale_lines_are_reported()

    print(f"\n{'=' * 60}")
    if FAILURES:
        print(f"[FAIL] {len(FAILURES)} / {PASSED + len(FAILURES)} 项未通过：")
        for line in FAILURES:
            print(f"  - {line[:200]}")
        return 1
    print(f"[OK] {PASSED} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())