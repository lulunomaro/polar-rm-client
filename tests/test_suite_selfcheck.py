"""测试套件自检：不允许存在"名字承诺了、断言没做"的测试。

# 要防的是什么

这个仓库里现在有 **10 个测试函数，一个断言都没有**过 —— 不是"忘了写"，
而是它们的名字看起来覆盖了安全属性，**实际上一行都没验证**。
一个绿色的空测试比没有测试更糟：它让覆盖率看起来比实际好，
下一个人 review 时看到"这条有测试守着"就会放心改。

常见的踩法是"先写测试名占位，实现没做完就 commit 了"，
或者"改代码时把断言删了，忘了删测试名"。

# 为什么这件事需要一个工具而不是靠自觉

本仓库有**三套断言写法**：

* `check(条件, "说明")` —— 大部分 suite 用它
* 裸 `assert` —— `smoke_test.py` 用它
* `require(值, "名称")` —— `field_candidate_test.py` 用它，失败时抛 AssertionError

我这轮为此写了两版"静态找零断言测试"的脚本，**两版都是假警报**：
第一版只认 `check(`，把 42 个用裸 `assert` 的测试全报成零断言；
第二版加了 `assert` 却漏了 `require(`，又把 10 个 `field_candidate_test.py`
里的测试全报成零断言。

教训很直接：**审计工具本身必须先被验证**，否则你只是在批量制造
"看起来严谨"的错误结论。第三版就是把这三种写法都认全，并且**拿已知的
正例反例互相校准**。

跑法：``python tests/test_suite_selfcheck.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TESTS_DIR = Path(__file__).resolve().parent

#: 本仓库实际在用的断言写法。**新增一种就必须加进来**，
#: 否则本文件会开始误报，而误报的下场是"大家都不看了"。
ASSERT_FORMS: list[tuple[str, re.Pattern[str], str]] = [
    ("check(...)", re.compile(r"\bcheck\s*\("), "check()"),
    ("assert", re.compile(r"(?m)^\s*assert\b"), "assert"),
    ("require(...)", re.compile(r"\brequire\s*\("), "require()"),
    ("assert_raises/pytest.raises", re.compile(r"\b(raises|assert_raises|pytest\.raises)\s*\("),
     "raises()"),
    ("self.assert*", re.compile(r"\bself\.assert"), "unittest"),
]

#: **脚本式** suite 的验证动作：它们不逐个断言，而是收集 failures / problems
#: 再以非零码退出（`raise SystemExit(main())`）。
#: 这也是本仓库的第四种写法 —— 前三种只统计断言会把这 4 个文件误报成"空的"。
SCRIPT_FORMS: list[tuple[str, re.Pattern[str], str]] = [
    ("failures/problems 累加",
     re.compile(r"\b(failures|problems|errors)\s*(:\s*list\[str\])?\s*=\s*\[\]"), "failures[]"),
    ("raise SystemExit(main())", re.compile(r"raise SystemExit\(\s*main\(\s*\)"), "SystemExit"),
]

_FUNC_SPLIT = re.compile(r"(?m)^(?=def test_)")


def _assertion_forms(body: str) -> list[str]:
    return [label for _pat, rx, label in ASSERT_FORMS if rx.search(body)]


def _test_functions(path: Path) -> list[tuple[str, str]]:
    """返回 [(函数名, 函数体)]。"""
    src = path.read_text(encoding="utf-8", errors="ignore")
    out = []
    for part in _FUNC_SPLIT.split(src):
        m = re.match(r"def (test_\w+)", part)
        if m:
            out.append((m.group(1), part))
    return out


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


# ===========================================================================
# 第 1 组：先验证**本检测器自己**是对的 —— 否则后面全是噪音
# ===========================================================================

def test_detector_recognises_every_assertion_idiom() -> None:
    """用样本断言三种写法都能被认出来。

    这一组是整个文件的地基：我写的前两版检测器就是因为漏了一种写法，
    报了一堆假警报。**检测器必须先证明自己是对的。**
    """
    check("认得出 check(...)",
          "check()" in _assertion_forms("    check(a == b, 'a should equal b')"),
          str(_assertion_forms("    check(a == b, 'x')")))
    check("认得出裸 assert",
          "assert" in _assertion_forms("    assert state.revision == 1"),
          str(_assertion_forms("    assert state.revision == 1")))
    check("认得出 require(...)",
          "require()" in _assertion_forms("    require(ev.closed, 'gate closed')"),
          str(_assertion_forms("    require(ev.closed, 'x')")))
    check("认得出 raises()",
          "raises()" in _assertion_forms("    with self.assertRaises(ValueError):\n        f()")
          or "raises()" in _assertion_forms("    with raises(ValueError):\n        f()"),
          str(_assertion_forms("    with raises(ValueError):\n        f()")))


def test_detector_does_not_hallucinate() -> None:
    """空函数、只有注释、只有准备代码 —— 都**不该**被算成有断言。

    没有这一组，检测器就会变成"什么都报有断言"，那它永远不会报警，
    等于没有。
    """
    for sample in (
        "def test_x():\n    pass\n",
        "def test_x():\n    # TODO: 补断言\n    pass\n",
        "def test_x():\n    data = [1, 2, 3]\n    total = sum(data)\n    print(total)\n",
        "def test_x():\n    '''docstring only'''\n",
    ):
        check(f"不把无断言的样本误判成有断言：{sample.splitlines()[1][:34]!r}",
              not _assertion_forms(sample), str(_assertion_forms(sample)))


def test_detector_ignores_assertions_in_another_function() -> None:
    """文件里别处的 assert 不能算到这个函数头上。

    这是分块最容易出错的地方：如果不按函数切，一个函数后面随便写点什么，
    它就会被前一个函数的 assert"认证"成有断言。
    """
    src = (
        "def test_good():\n"
        "    assert True\n"
        "\n"
        "def test_empty():\n"
        "    value = 1\n"
        "    return value\n"
    )
    parts = _FUNC_SPLIT.split(src)
    empty = [p for p in parts if p.startswith("def test_empty")][0]
    check("不把别的函数的 assert 算进来", not _assertion_forms(empty),
          str(_assertion_forms(empty)))


# ===========================================================================
# 第 2 组：用验证过的检测器扫真实套件
# ===========================================================================

def test_no_assertion_free_test_functions() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in sorted(TESTS_DIR.glob("*.py")):
        for name, body in _test_functions(path):
            scanned += 1
            forms = _assertion_forms(body)
            if not forms:
                lines = len([ln for ln in body.splitlines() if ln.strip()])
                offenders.append(f"{path.name}::{name}（{lines} 行，一个断言都没有）")
    check(f"扫过 {scanned} 个测试函数，没有一个是零断言的",
          not offenders, "; ".join(offenders[:6]))


def test_every_suite_actually_verifies_something() -> None:
    """每个 suite 都要有**可验证的动作**：`test_*` 函数或模块级断言。

    反过来那种情况（新加了一个 suite 但忘了写任何检查）同样是绿的。

    这里不要求必须有 `test_` 函数 —— 本仓库有**两种**suite 写法：
    * 函数式：`def test_xxx(): ...`，跑测试的人按名字挑
    * 脚本式：模块级 `main()` + 直接调 `check()`（api_freeze_test、
      control_loop_test 等 6 个文件是这种）

    我最初写成"每个 suite 都得有 test_ 函数"，结果把那 6 个脚本式 suite
    全报成空 —— 又一次**测量工具比被测对象错**。
    所以判据改成"有没有可验证的断言动作"，而不是"用了哪种写法"。
    """
    inert = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8", errors="ignore")
        has_functions = bool(_FUNC_SPLIT.search(src))
        has_assertions = any(rx.search(src) for _p, rx, _l in ASSERT_FORMS)
        has_script_exit = any(rx.search(src) for _p, rx, _l in SCRIPT_FORMS)
        if not (has_functions or has_assertions or has_script_exit):
            inert.append(path.name)
    total = len(sorted(TESTS_DIR.glob("*.py")))
    check(f"{total} 个 suite 都有可验证的动作（断言函数 / 逐条断言 / 脚本式非零退出）",
          not inert, f"看不出它会失败的 suite：{inert}")


def test_assurance_suites_are_registered_somewhere() -> None:
    """关键 suite 必须被 `tools/verify_all.py` 覆盖。

    一份没人跑的测试等于没有。本仓库的闸门是 verify_all.py，
    所以"这个 suite 存在"不等于"它的失败会让人看见"。
    """
    verify = (TESTS_DIR.parent / "tools" / "verify_all.py").read_text(
        encoding="utf-8", errors="ignore")
    must_run = [
        "settings_access_test",     # 本轮新增的设置项读取等价性
        "spin_readback_test",       # 本轮新增的自转 0x1F 回读闭环
        "protocol_crc_test",
        "polar_link_failclosed_test",
        "overlay_write_guard_test",
        "tongji_postprocess_parity_test",
        "udp_transport_test",
        "economy_safety_test",
        "config_store_io_test",
        "vision_pipeline_hardening_test",
        "field_log_async_test",
        "control_loop_test",
        "polar_link_snapshot_test",
        "test_settings_are_read",
    ]
    missing = [name for name in must_run if name not in verify]
    check(f"关键 suite 都被 verify_all.py 覆盖（查 {len(must_run)} 个）",
          not missing, f"没被覆盖：{missing}")


def main() -> int:
    tests = [
        test_detector_recognises_every_assertion_idiom,
        test_detector_does_not_hallucinate,
        test_detector_ignores_assertions_in_another_function,
        test_no_assertion_free_test_functions,
        test_every_suite_actually_verifies_something,
        test_assurance_suites_are_registered_somewhere,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"测试套件自检：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"测试套件自检：全部通过（{PASSED} 项）")
    print("没有零断言的测试；关键 suite 都被闸门覆盖。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
