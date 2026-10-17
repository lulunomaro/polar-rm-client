#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# prepare_hero_build.py 新增的两条能力要有离线证据：改鼠标灵敏度、以及"注释吞 define"能被体检抓住。
from __future__ import annotations

import importlib.util as ilu
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "rm_custom_client")) if not (ROOT / "rm_client").exists() else None
sys.path.insert(0, str(HERE))

_SPEC = ilu.spec_from_file_location("phb", HERE / "prepare_hero_build.py")
phb = ilu.module_from_spec(_SPEC)
_SPEC.loader.exec_module(phb)

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    print(f"{'[ OK ]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    FAILED += 0 if ok else 1


@contextlib.contextmanager
def expected_failure_output():
    """Label deliberately rejected fixture operations, not test assertions."""
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            yield
    finally:
        print(captured.getvalue().replace("[FAIL]", "[EXPECTED-FAIL]"), end="")


def main() -> int:
    print("\n--- prepare_hero_build 新增选项 ---")
    step = phb.Step()

    # 1) 注释剥离器：证明"躺在注释里的 #define"再也骗不过体检
    src = ("/* 说明\n#define __CONTROL_LINK_KM CL_KM_VT\n*/\n"
           "#ifndef X\n#define X 1\n#endif\n"
           "// #define Y 2\n#define Z 3\n")
    stripped = phb.strip_c_comments(src)
    check("块注释里的 #define 被剥掉", "CL_KM_VT" not in stripped, stripped.replace(chr(10), "|"))
    check("注释外的 #define 留着", "Z 3" in stripped and "Y 2" not in stripped)
    check("字符串里的 // 不会被当注释", "http://x" in phb.strip_c_comments('a="http://x";'))
    check("剥注释后还能看见 ifndef", "#ifndef X" in stripped)

    # 2) --mouse-sens 在临时副本上真的改得动，且幂等
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "application").mkdir()
        hero = repo / phb.HERO_PARAM_REL
        hero.write_text('#define MOUSE_SENSITIVITY (100000.0f)\n#define OTHER 1\n',
                        encoding="utf-8")
        phb.switch_mouse_sensitivity(repo, step, "0.5")
        t1 = hero.read_text(encoding="utf-8")
        check("英雄参数被改成可用量级", "#define MOUSE_SENSITIVITY (0.5f)" in t1, t1.splitlines()[0])
        check("旁边的宏不受影响", "#define OTHER 1" in t1)
        phb.switch_mouse_sensitivity(repo, step, "1.2")
        t2 = hero.read_text(encoding="utf-8")
        check("再改一次是替换不是叠加（只有一行定义）",
              t2.count("#define MOUSE_SENSITIVITY") == 1 and "1.2f" in t2, t2.splitlines()[0])
        phb.switch_mouse_sensitivity(repo, step, "0.5f")
        check("带 f 后缀的输入也不会变成 0.5ff",
              "#define MOUSE_SENSITIVITY (0.5f)" in hero.read_text(encoding="utf-8"))
        n_before = len(step.failures)
        with expected_failure_output():
            phb.switch_mouse_sensitivity(Path(td) / "missing", step, "0.5")
        check("找不到文件时记一条失败，不是静默返回",
              len(step.failures) == n_before + 1, str(step.failures[-1:]))
        hero.write_text("#define SOMETHING_ELSE 1\n", encoding="utf-8")
        n_before = len(step.failures)
        with expected_failure_output():
            phb.switch_mouse_sensitivity(repo, step, "0.5")
        check("文件在但宏写法变了也要报错（上游改了不能装看不见）",
              len(step.failures) == n_before + 1, str(step.failures[-1:]))
        shutil.rmtree(td, ignore_errors=True)
    # 2b) 默认值本身要有物理含义，不能是抄来的"看起来像"的数
    # 1px=1mrad 那一档是错的：云台每 1ms 积一次 mouse/S，一个值还会被 DR16 保持约
    # 14ms，所以 1000 实际约 33rad/s（一推就顶限位）。可用区间见 prepare 的体检。
    check("默认 MOUSE_SENSITIVITY 在可用量级（角速度=1000*(每帧像素)/S）",
          5000.0 <= float(phb.DEFAULT_MOUSE_SENS) <= 50000.0,
          str(phb.DEFAULT_MOUSE_SENS))
    check("默认值不是死配置 0.5（那是配套没有鼠标的遥控器的）",
          float(phb.DEFAULT_MOUSE_SENS) > 10.0, str(phb.DEFAULT_MOUSE_SENS))

    # 3) 最关键的一条：--km 与 --km-fire-keys 必须真的生效（历史上是静默空转）
    import re
    with tempfile.TemporaryDirectory() as td2:
        repo2 = Path(td2)
        (repo2 / "application").mkdir()
        param = repo2 / "application" / "robot_param.h"
        param.write_text(
            "#define __RC_TYPE RC_ET08A\n"
            "#ifndef __CONTROL_LINK_KM\n#define __CONTROL_LINK_KM CL_KM_RC\n#endif\n",
            encoding="utf-8")
        st = phb.Step()
        phb.switch_km_link(repo2, st, "vt", False)
        text = param.read_text(encoding="utf-8")
        stripped = phb.strip_c_comments(text)
        hit = re.search(r"^\s*#define\s+__CONTROL_LINK_KM\s+(CL_KM_\w+)", stripped, re.M)
        check("--km vt 把宏写在**注释外**（编译器真看得见，不是靠 ifndef 兜底）",
              hit is not None and hit.group(1) == "CL_KM_VT",
              str(hit.group(1)) if hit else "注释外没有这个 define")
        check("ifndef 兜底块还在（幂等重跑不会叠出两份）",
              text.count("#define __CONTROL_LINK_KM") == 2, str(text.count("#define __CONTROL_LINK_KM")))
        phb.switch_km_link(repo2, st, "rc", True)
        stripped2 = phb.strip_c_comments(param.read_text(encoding="utf-8"))
        check("--km rc 换档也真的生效（切回来同样要有效）",
              re.search(r"^\s*#define\s+__CONTROL_LINK_KM\s+CL_KM_RC\b", stripped2, re.M) is not None)
        check("--km-fire-keys 的 KM_KEY_ALLOWED_MASK 也在注释外（开火键位不再静默失效）",
              re.search(r"^\s*#define\s+KM_KEY_ALLOWED_MASK", stripped2, re.M) is not None)
        phb.switch_km_link(repo2, st, "vt", False)
        stripped3 = phb.strip_c_comments(param.read_text(encoding="utf-8"))
        check("不给 --km-fire-keys 时必须**没有**开火掩码（不偷偷扩权）",
              re.search(r"^\s*#define\s+KM_KEY_ALLOWED_MASK", stripped3, re.M) is None)
        check("重跑不会把块叠成两份",
              param.read_text(encoding="utf-8").count(phb.KM_BLOCK_HEAD) == 1)

    # 4) 脚本会写的文件必须登记在 OWNED：否则第二次跑 prepare 会被 check_clean 拒
    #    （referee.c 踩过一次，--mouse-sens 又踩一次，所以这条要变成断言）
    check("英雄参数文件在 OWNED 名单里（第二次 prepare 不会被拒）",
          phb.HERO_PARAM_REL in phb.OWNED)
    check("robot_param.h 也在 OWNED 里", phb.ROBOT_PARAM_REL in phb.OWNED)
    import subprocess
    real_git = phb.git

    def fake_git(owned_only):
        def _f(repo, *args):
            if args and args[0] == "status":
                out = (" M " + phb.HERO_PARAM_REL + "\n"
                       + (" M application/typedef/host_km_link.h\n" if owned_only else
                          " M application/somebody_else.c\n"))
                return subprocess.CompletedProcess(["git"], 0, out, "")
            return real_git(repo, *args)
        return _f

    try:
        with tempfile.TemporaryDirectory() as td3:
            phb.git = fake_git(True)
            st = phb.Step()
            check("只有本脚本碰过的改动时 check_clean 放行（幂等重跑）",
                  phb.check_clean(Path(td3), st) is True and not st.failures)
            st2 = phb.Step()
            phb.git = fake_git(False)
            with expected_failure_output():
                clean = phb.check_clean(Path(td3), st2)
            check("别人改的文件仍然要拒绝（不能为了幂等把闸门拆了）",
                  clean is False and bool(st2.failures))
    finally:
        phb.git = real_git

    # 5) 兵种可切换（目标写的是"英雄/步兵"，以前只有英雄一条路）
    with tempfile.TemporaryDirectory() as td5:
        repo5 = Path(td5)
        (repo5 / "application").mkdir()
        p5 = repo5 / "application" / "robot_param.h"
        p5.write_text('#include "robot_param_omni_infantry.h"\n#define __DEVELOP 1\n',
                      encoding="utf-8")
        st5 = phb.Step()
        phb.switch_robot(repo5, st5, "hero")
        txt = p5.read_text(encoding="utf-8")
        check("步兵 -> 英雄切得动", phb.HERO_INCLUDE in txt and phb.INFANTRY_INCLUDE not in txt.split("\n")[0], txt.splitlines()[0])
        check("切过去时把另一条留成注释（现场能一眼回切）",
              "// " + phb.INFANTRY_INCLUDE in txt)
        phb.switch_robot(repo5, st5, "hero")
        check("重复切同一兵种是幂等（不会叠出两行 include）",
              p5.read_text(encoding="utf-8").count("robot_param_mecannum_hero.h") == 1)
        phb.switch_robot(repo5, st5, "infantry")
        txt2 = p5.read_text(encoding="utf-8")
        check("英雄 -> 步兵也切得回来（认得出被注释掉的那行）",
              p5.read_text(encoding="utf-8").split("\n")[0].strip().startswith(phb.INFANTRY_INCLUDE),
              txt2.splitlines()[0])
        phb.switch_robot(repo5, st5, "hero")
        check("来回切三次仍然只有一行生效 include",
              sum(1 for l in p5.read_text(encoding="utf-8").splitlines()
                  if l.strip().startswith("#include")) == 1,
              p5.read_text(encoding="utf-8").splitlines()[0])
        # 来回切 6 次，文件里也只能有"一行生效 + 一行备选"（曾经能攒到 3 行）
        for want in ("hero", "infantry") * 3:
            phb.switch_robot(repo5, st5, want)
        incs = [l for l in p5.read_text(encoding="utf-8").splitlines()
                if "robot_param_" in l and l.strip().startswith(("#include", "// #include"))]
        check("来回切 6 次后 include 只有两条（1 生效 + 1 备选），不攒重复注释行",
              len(incs) == 2 and incs[0].startswith("#include"), str(incs))
        check("切换兵种不许动文件里别的内容", "__DEVELOP" in p5.read_text(encoding="utf-8"))
        other = repo5 / "application" / "nope.h"
        p5.write_text("#define NOTHING_HERE 1\n", encoding="utf-8")
        st6 = phb.Step()
        with expected_failure_output():
            phb.switch_robot(repo5, st6, "hero")
        check("找不到任何兵种 include 时报错而不是乱插", bool(st6.failures), str(st6.failures))
        check("步兵参数文件也在 OWNED 里", phb.INFANTRY_PARAM_REL in phb.OWNED)
        check("ROBOT_PROFILES 两个兵种的参数文件都在 OWNED 里",
              all(v["param"] in phb.OWNED for v in phb.ROBOT_PROFILES.values()))

    # 6) --mouse-sens 必须改到**所选兵种**那份文件，不能永远改英雄
    with tempfile.TemporaryDirectory() as td6:
        repo6 = Path(td6)
        (repo6 / "application").mkdir()
        (repo6 / phb.HERO_PARAM_REL).write_text(
            "#define MOUSE_SENSITIVITY (100000.0f)\n", encoding="utf-8")
        (repo6 / phb.INFANTRY_PARAM_REL).write_text(
            "#define MOUSE_SENSITIVITY (40000.0f)\n", encoding="utf-8")
        st7 = phb.Step()
        phb.switch_mouse_sensitivity(repo6, st7, "1000", phb.INFANTRY_PARAM_REL)
        check("步兵档改的是步兵参数文件",
              "MOUSE_SENSITIVITY (1000f)" in
              (repo6 / phb.INFANTRY_PARAM_REL).read_text(encoding="utf-8"))
        check("改步兵时不许顺手动英雄的文件",
              "100000.0f" in (repo6 / phb.HERO_PARAM_REL).read_text(encoding="utf-8"))
        check("ROBOT_PROFILES 两个兵种的参数文件都在 OWNED 里",
              all(v["param"] in phb.OWNED for v in phb.ROBOT_PROFILES.values()))

    print("\n" + ("有失败项" if FAILED else "prepare_hero_build 新选项全部通过"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
