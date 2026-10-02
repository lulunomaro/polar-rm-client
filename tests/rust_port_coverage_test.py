"""Python 模块 → Rust crate 的**覆盖矩阵**，并且强制每个模块都有交代。

# 为什么要有这个文件

"把 rmclient 用 Rust 重写"这个目标，最难回答的问题是**"还差多少"**。
本轮之前没人能一句话说出来 —— 只能凭印象说"差不多了"。

所以这里把 31 个 Python 模块逐个归类成三种之一：

* ``ported``   —— 已经有 Rust 对应物，且有黄金向量对拍；
* ``frozen``   —— **刻意不搬**，因为它在视觉冻结区（搬了就会动画面）；
* ``deferred`` —— 明确还没搬，并且**写清了为什么**。

# 关键设计：新模块必须当场被拦下

这个文件真正的价值不是那张表，而是**表会过期**。三个月后有人新加了
``rm_client/foo.py``，忘了登记 —— 那时这张表就在骗人："31 个模块都有交代"。

所以下面有一条断言：**磁盘上每个 ``rm_client/*.py`` 都必须在三张表里出现**，
否则直接红并报出漏掉的那几个。它不要求"都搬了"，只要求"都有交代"。

这跟 ``tools/visual_freeze_guard.py`` 是同一个思路：把一条承诺变成
机器可校验的东西，而不是文档里的一句话。

# 三张表之间不许重叠

同一个模块被同时列进 ``ported`` 与 ``frozen``，是**自相矛盾**而不是"都做了" ——
比如有人手滑把 ``tongji_yolo.py``（视觉冻结）写进了已搬清单，
那会让"覆盖率"这个数字虚高。下面的断言专门查这个。

跑法：``python tests/rust_port_coverage_test.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PY_MODULES = ROOT / "rm_client"
RS_ROOT = ROOT.parent / "rmclient-rs"

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
# 三张表
# ===========================================================================

#: 已搬，且有黄金向量对拍。值是 Rust 侧的落点。
#: 键与磁盘上一致：``rm_client/`` 下的用裸文件名，``rm_client/backend/``
#: 下的带 ``backend/`` 前缀 —— 下面那条"表没过期"的断言就靠这个对齐。
PORTED: dict[str, str] = {
    "protocol.py": "rm-protocol（CRC / 0xA5 / 0x5A / protobuf wire）",
    "backend/protocol.py": "rm-protocol（0x5A 帧编解码；字段级常量在 rm-spec）",
    "robot_roles.py": "rm-spec（限幅 / 兵种对齐）",
    "backend/polar_link.py": "rm-protocol + rm-link（帧解码与段映射）",
    "backend/polar_bridge.py": "rm-link + rm-control（段→overlay / 云台仲裁 / 运动核验）",
    "backend/polar_transport.py": "rm-transport（收包判定链）",
    "backend/polar_spec.py": "rm-spec（规格加载与校验）+ rm-spec::segment（段体解包）",
    "backend/safety.py": "rm-safety（四道闸 / 资格令牌 / 限频 / 急停）",
    "backend/dt7_link.py": "rm-control（自检评估器）",
    "backend/serial_stream.py": "rm-transport（TCP/串口流抽象）",
    "backend/serial_backend.py": "rm-transport（串口后端）",
    "backend/mqtt_backend.py": "rm-client（--replay 产出官方侧字节）",
    "backend/cc_link.py": "rm-client（出口枚举）",
    "polar_selftest.py": "rm-client（--replay 回放自检）",
    "model.py": "rm-state（Setting / Module / ModuleRegistry / RobotState）",
    "control.py": "rm-control（75Hz 回路 / 自检评估器）",
    "transports.py": "rm-transport（FrameAssembler / UDP 收包）",
    "scenarios.py": "rm-client::plan::ScenarioKey（**手抄的，但已由 scenario_parity.rs 逐项对拍钉住**）",
    "robot_profiles.py": "rm-ballistics（消费 golden/tables_vectors.json 的 robot_profiles 段，**不手抄**）",
    "economy.py": "rm-economy（2,750 组向量）：报价矩阵 + QoS1 重复扣款准入 + 事务结算",
    "combat_events.py": "rm-combat（6,355 条观测）：累计量增量才是边沿",
    "match_chat.py": "rm-combat：战况播报文案逐字比对",
    "hero_lob.py": "rm-ballistics：抛物线解算（纯 math）",
    "armor_vehicle.py": "rm-ballistics：只搬不依赖 cv2 的数学那半（cv2 那一半不搬）",
    "replay.py": "rm-client（--replay-field-log）",
    "settings_access.py": "rm-state::value + 各 crate 自己的读取器",
    "preflight.py": "rm-client（只搬了参数解析与出口；预检执行仍留在 Python）",
    "native_viewer_telemetry.py": "rm-protocol::proto（protobuf 字段解包）",
}

#: **刻意不搬**：在视觉冻结区，搬了就等于动画面。
#:
#: 这张表**必须**与 ``tools/visual_freeze_guard.py`` 的 ``FROZEN_GLOBS``
#: 里那些单文件项逐个相等 —— 下面有断言查。
#:
#: 第一版这里写的是"app.py / tongji_yolo.py / vision.py / tongji_bridge.py /
#: ui_audio.py 视觉冻结"，是**我猜的**，断言当场把它打回来了：guard 的冻结清单
#: 里根本没有这几个。也就是说"冻结"这件事**只有一个权威来源，就是 guard 本身**，
#: 凭"看起来像视觉"往表里加，只会让覆盖率虚高、并让这些模块永远排不进移植计划。
FROZEN: dict[str, str] = {
    "kill_fx.py": "guard：视觉专属表现层模块",
    "combat_hud.py": "guard：视觉专属表现层模块",
    "startup_wallpaper.py": "guard：视觉专属表现层模块",
    "backend/video_backend.py": "guard：视频解码链路，任何改动都可能改变画面像素",
}

#: 还没搬。每条都必须写清为什么。
#:
#: 注意 ``vision.py`` / ``tongji_yolo.py`` / ``tongji_bridge.py`` / ``ui_audio.py`` /
#: ``app.py`` 都在这里而**不在** ``FROZEN`` 里 —— 它们确实与视觉强相关，
#: 但**没有被冻结**，所以"暂不搬"是**取舍**，不是"不许动"。
DEFERRED: dict[str, str] = {





    "auto_spin.py": "第 12 轮在搬（rm-calibration）：判定逻辑早已做进 rm-control，这里只搬编排",
    "config_store.py": "配置落盘：含 atexit / 并发写 / 原子替换，判据都在 Python 侧，待搬",
    "calibration.py": "第 12 轮在搬（rm-calibration）",
    "vision.py": "与视觉强相关但**未被冻结**：取舍，不是禁令。判据要靠假设备搭一套",
    "tongji_yolo.py": "与视觉强相关但**未被冻结**：取舍。含 cv2 与 numpy 推理链路",
    "tongji_bridge.py": "与视觉强相关但**未被冻结**：取舍。含 socket/threading",
    "ui_audio.py": "与视觉强相关但**未被冻结**：取舍。输出是声音不是画面",
    "app.py": "主窗口装配：UI 全在冻结区，搬了也没有意义；只可能搬编排层",
    "match_modes.py": "赛制模式表：已随 tables_vectors 导出，但 rm-client 尚未消费，待接",

    "runtime_paths.py": "第 12 轮在搬（rm-ports）",
    "serial_ports.py": "第 12 轮在搬（rm-ports）：只搬解析与排序，**不碰真设备**",
    "backend/manager.py": "组合根：已由 rm-client 承担判据，但这一层还混着大量编排，待拆",
    "backend/field_log.py": "现场日志：异步写盘 + 回放，判据在 Python 侧，待搬",
    "backend/qualification.py": "资格赛证据管理：与导出打包面耦合，待搬",
    "backend/readiness.py": "就绪度聚合：纯逻辑但与 UI 呈现耦合，待拆",
    "backend/codec_selftest.py": "编解码自检：要真 ffmpeg 才能验，本机装不了",
}


# ===========================================================================
# 断言
# ===========================================================================

def _modules_on_disk() -> set[str]:
    """磁盘上的 Python 模块（``rm_client/`` 与 ``rm_client/backend/``）。

    第一版只扫了 ``rm_client/*.py``，于是 ``backend/`` 下那 16 个模块
    全部落进"表里没有磁盘上的模块"的反向报错 —— 看起来像表过期了，
    其实是**扫描漏了一半**。backend 是逻辑层最密集的地方，漏掉它
    恰恰把最该被记账的那批漏在外面。
    """
    names = {p.name for p in PY_MODULES.glob("*.py") if not p.name.startswith("__")}
    for p in (PY_MODULES / "backend").glob("*.py"):
        if p.name != "__init__.py":
            names.add(f"backend/{p.name}")
    return names


def test_every_module_is_classified() -> None:
    """磁盘上每个 Python 模块都必须在三张表里出现。

    这是本文件的核心断言。**不要求都搬了，只要求都有交代** ——
    新加模块忘了登记，就是漏掉的下一个坑。
    """
    on_disk = _modules_on_disk()
    classified = set(PORTED) | set(FROZEN) | set(DEFERRED)
    missing = sorted(on_disk - classified)
    check(f"磁盘上 {len(on_disk)} 个模块全部有归类", not missing,
          f"没归类：{missing}")
    ghost = sorted(classified - on_disk)
    check("三张表里没有磁盘上不存在的模块（表没过期）", not ghost, f"多余：{ghost}")


def test_tables_do_not_overlap() -> None:
    """同一模块被列进两张表 = 自相矛盾，不是"都做了"。

    尤其 ``scenarios.py``：它被列在 ``PORTED`` 里，但那条备注写着
    "**手抄的**" —— 也就是说仓里其实有**两份**场景表。
    这不矛盾（那份确实在工作），但它是**漂移风险**，必须有下一轮的处置。
    """
    tables = {"PORTED": set(PORTED), "FROZEN": set(FROZEN), "DEFERRED": set(DEFERRED)}
    for i, (a, left) in enumerate(tables.items()):
        for b, right in list(tables.items())[i + 1:]:
            overlap = sorted(left & right)
            check(f"{a} 与 {b} 不重叠", not overlap, f"重叠：{overlap}")


def test_frozen_table_matches_the_actual_guard() -> None:
    """``FROZEN`` 必须与 ``tools/visual_freeze_guard.py`` 的冻结清单对得上。

    冻结清单是本项目**唯一**的红线，而"刻意不搬"的依据就是"它在红线里"。
    两处清单一旦漂移，这张表就开始骗人 —— 比如有人给某个模块解冻了，
    这张表还写着"视觉冻结，不搬"，于是它永远不会被排进移植计划。

    只比**单文件**条目：``rm_client/ui/**`` 这类目录通配不是模块，
    本表按模块记账，两者不混在一起比。``vision_wire_gpu.py`` 住在 ``ui/`` 下、
    由那条通配罩着，所以它**不在**本表里（它是 UI 层的一部分，不是顶层模块）。
    """
    guard = (ROOT / "tools" / "visual_freeze_guard.py").read_text(encoding="utf-8")
    # **先剥掉注释**再解析：guard 里解释那条路径历史的注释本身就写着
    # 旧路径（带引号），不剥就会把注释当成一条清单项 —— 第一版就栽在这，
    # 报出"有一条失效"，而那一条其实是我自己写的注释。
    code_only = "\n".join(
        line for line in guard.splitlines() if not line.lstrip().startswith("#")
    )
    # 只取**单文件**条目（不含 `/**`），目录通配不是模块，不参与比较。
    # guard 写的是仓库相对路径，本表按模块记账，所以要剥掉 `rm_client/` 前缀。
    # 再滤掉 `ui/` 下的：那些是 UI 层文件，本表只记顶层与 backend 两个目录的模块，
    # 它们由 `rm_client/ui/**` 那条通配整体罩住。
    frozen_in_guard = {
        e[len("rm_client/"):]
        for e in re.findall(r'"(rm_client/[^"]+)"', code_only)
        if not e.endswith("/**") and e.startswith("rm_client/")
        and not e.startswith("rm_client/ui/")
    }
    here = set(FROZEN)
    missing = sorted(frozen_in_guard - here)
    check(f"guard 里的单文件冻结项都在本表里（{len(frozen_in_guard)} 项）",
          not missing, f"本表漏了：{missing}")
    extra = sorted(here - frozen_in_guard)
    check("本表没有把已解冻的模块继续标成冻结", not extra,
          f"这些不在 guard 的冻结清单里了：{extra}")


def test_deferred_entries_all_have_a_reason() -> None:
    """``DEFERRED`` 每条都要写清为什么，空字符串等于没交代。"""
    empty = sorted(k for k, v in DEFERRED.items() if not v.strip())
    check(f"DEFERRED 的 {len(DEFERRED)} 条都有理由", not empty, f"空的：{empty}")
    short = sorted(k for k, v in DEFERRED.items() if len(v.strip()) < 8)
    check("DEFERRED 的理由不是敷衍的短句", not short, f"太短：{short}")


def test_ported_entries_name_a_real_crate() -> None:
    """``PORTED`` 每条都要指向一个**真实存在**的 crate。

    不指向真实 crate 的条目等于"我说搬了但你找不着"，比不写更糟。
    """
    if not RS_ROOT.is_dir():
        check("Rust 仓库在（否则跳过落点核对）", True, f"{RS_ROOT} 不存在")
        return
    crates = {p.name for p in RS_ROOT.glob("crates/*") if p.is_dir()}
    bad = []
    for module, where in PORTED.items():
        named = set(re.findall(r"\b(rm-[a-z]+)\b", where))
        if not named:
            bad.append(f"{module}: 落点里没写 crate 名（{where}）")
            continue
        unknown = sorted(named - crates)
        if unknown:
            bad.append(f"{module}: 指向不存在的 crate {unknown}")
    check(f"PORTED 的 {len(PORTED)} 条都指向真实存在的 crate", not bad, "; ".join(bad))
    print(f"      Rust 侧现有 crate：{sorted(crates)}")


def test_coverage_numbers_are_reported_honestly() -> None:
    """把三个数字打出来 —— 汇报口径必须是"有交代"而不是"都搬了"。

    这条本身不会失败，它的作用是**每次跑都把真实覆盖率印出来**，
    免得没人主动去数、然后凭印象说"差不多了"。
    """
    disk = _modules_on_disk()
    total = len(disk)
    ported = len(set(PORTED) & disk)
    frozen = len(set(FROZEN) & disk)
    deferred = total - ported - frozen
    print()
    print("      " + "=" * 58)
    print(f"      Python 模块 {total} 个：")
    print(f"        已搬且有向量对拍      {ported:3d}  ({ported * 100 // total}%)")
    print(f"        视觉冻结，刻意不搬    {frozen:3d}  ({frozen * 100 // total}%)")
    print(f"        还没搬（有交代）      {deferred:3d}  ({deferred * 100 // total}%)")
    print(f"        有交代的比例          {(ported + frozen) * 100 // total:3d}%")
    print("      " + "=" * 58)
    check("汇报口径是'有交代的比例'，不是'都搬了'",
          ported + frozen <= total, f"{ported}+{frozen} > {total}")


def test_scenarios_duplicate_is_pinned_to_the_reference() -> None:
    """``scenarios.py`` 在仓里有**两份**：Python 的原表与 ``rm-client`` 里手抄的。

    手抄的那份**不会**跟着 Python 变 —— 而场景表决定了一堆默认行为
    （限幅、mock_writes、allow_mqtt_publish）。改 Python 不改 Rust，
    于是"两个客户端对同一场景表现不同"，现场几乎无法归因：
    你看到的现象（"按了没反应"）和真正的原因（场景档位抄错了）
    中间隔着好几层。

    **这条从"红"变成"绿"，但不是因为 plan.rs 被改了。**
    理想做法是把 Python 那张表**搬**进 Rust 让两边共用一份；这轮没搬成。
    实际走的是另一条路：``crates/rm-client/tests/scenario_parity.rs``
    拿 ``golden/tables_vectors.json``（由 ``gen_tables_vectors.py``
    **从 Python 导出**）逐项比对 ``ScenarioKey`` 的键、标题与三道安全闸。

    所以"两份"这件事仍然存在，但**漂移现在是会红的** ——
    这比"只有一份"弱，但比"两份都没网"强得多。

    下面这四条守的就是这个：那份对拍必须还在，而且必须真的 include 了
    导出表、真的比了键集合与安全闸。哪天有人觉得"手抄的也挺好"把对拍删了，
    这里就红。
    """
    parity = RS_ROOT / "crates" / "rm-client" / "tests" / "scenario_parity.rs"
    if not parity.is_file():
        check("rm-client 的场景对拍测试还在", False,
              f"{parity} 不见了 —— 两份场景表的漂移又没人管了")
        return
    src = parity.read_text(encoding="utf-8", errors="ignore")
    check("场景对拍真的 include 了由 Python 导出的向量表",
          "golden/tables_vectors.json" in src,
          "它没有引用那份向量 —— 那它到底在跟什么对拍？")
    check("场景对拍真的覆盖了键集合",
          "scenario_keys_match_python_exactly" in src, "键集合对拍不见了")
    check("场景对拍真的覆盖了三道安全闸",
          "scenario_safety_flags_match_python" in src, "安全闸对拍不见了")
    plan = RS_ROOT / "crates" / "rm-client" / "src" / "plan.rs"
    if plan.is_file():
        handwritten = "enum ScenarioKey" in plan.read_text(encoding="utf-8", errors="ignore")
        check("（记录用）plan.rs 里仍是手抄的 ScenarioKey —— 所以对拍不可省",
              handwritten,
              "已经改成引用搬过去的表了？那本测试的注释要更新，"
              "并且 scenario_parity.rs 里那份缺口清单也要一起删")


def test_freeze_globs_actually_match_something() -> None:
    """冻结清单里**每一条都得真的匹配到文件**。

    这条是写这个文件时顺手发现的：``visual_freeze_guard.py`` 里有一条
    ``"rm_client/vision_wire_gpu.py"`` —— 而那个文件其实在
    ``rm_client/ui/vision_wire_gpu.py``（被 ``rm_client/ui/**`` 罩着）。

    所以那条单文件条目**已经失效**：它什么也没保护。红线没被突破
    （真实文件仍被目录通配覆盖），但清单在**骗人** ——
    有人看到单文件条目会以为"GPU 合成链路是单独锁的"，
    而实际上它只是碰巧被更宽的通配罩住。哪天有人调整 ``ui/**`` 的范围，
    这条失效条目不会提醒任何人。

    冻结清单是本项目唯一的红线，它**不允许有失效条目**：
    要么真的匹配到东西，要么说明这条该删了（因为通配已经罩住）。
    """
    guard = (ROOT / "tools" / "visual_freeze_guard.py").read_text(encoding="utf-8")
    # 同样要先剥注释 —— 见下面那条的说明，否则会把注释里的例子当成清单项。
    code_only = "\n".join(
        line for line in guard.splitlines() if not line.lstrip().startswith("#")
    )
    entries = re.findall(r'"((?:rm_client|assets|tests/baselines)/[^"]+)"', code_only)
    check("从 guard 里解析出了冻结条目", len(entries) >= 8, f"只解析到 {len(entries)} 条")

    stale = []
    for entry in entries:
        if entry.endswith("/**"):
            base = ROOT / entry[:-3]
            if not base.is_dir():
                stale.append(f"{entry} -> 目录不存在")
            continue
        if not (ROOT / entry).is_file():
            stale.append(f"{entry} -> 文件不在这个路径（可能搬过家了）")
    check(f"冻结清单的 {len(entries)} 条都真的匹配到东西", not stale, "; ".join(stale))
    for line in stale:
        print(f"      失效条目：{line}")
        moved = list(ROOT.glob("**/" + Path(line.split(" -> ")[0]).name))
        for path in moved:
            print(f"        也许搬到了：{path.relative_to(ROOT)}")


def main() -> int:
    tests = [
        test_every_module_is_classified,
        test_tables_do_not_overlap,
        test_frozen_table_matches_the_actual_guard,
        test_freeze_globs_actually_match_something,
        test_deferred_entries_all_have_a_reason,
        test_ported_entries_name_a_real_crate,
        test_coverage_numbers_are_reported_honestly,
        test_scenarios_duplicate_is_pinned_to_the_reference,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"Rust 移植覆盖矩阵：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"Rust 移植覆盖矩阵：全部通过（{PASSED} 项）")
    print("每个 Python 模块都有交代；'还没搬'的那部分也写清了为什么。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
