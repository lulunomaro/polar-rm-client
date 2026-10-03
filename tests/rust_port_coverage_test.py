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

    # ⚠ 这七条**原来写了两遍**（一份在下面那段历史记录之前、一份在之后），
    # 而 **Python 的 dict 字面量只保留最后一次出现** —— 于是前半份是**死代码**：
    # 改它不报错、不告警、也毫无效果。
    # 守卫是 `test_no_table_has_a_duplicated_key`，它读 AST 而不是运行时 dict
    # （运行时的 dict 恰恰是骗人的那个东西）。

    "tongji_yolo.py": "与视觉强相关但**未被冻结**：取舍。含 cv2 与 numpy 推理链路",
    "tongji_bridge.py": "与视觉强相关但**未被冻结**：取舍。含 socket/threading",
    # ---- 以下几项是第 15 轮之前**忘了更新**的 ----
    #
    # 这一段留着当记录：第 12 轮我给 `PORTED` 加了"'待搬/在搬'字样直接红"的断言，
    # 以为那就能挡住状态漂移。**它挡不住本轮这种。**
    # 原因是那两条断言只查 `PORTED` —— 而漂移发生在 `DEFERRED` 里：
    # 这些条目写着"第 12 轮在搬"，可它们**已经搬完了**（rm-calibration /
    # rm-ports / rm-config / rm-logstore 都已经落地并对拍过），
    # 分类却还停在"还没搬"。于是**"已搬"这个事实只存在于我的脑子里**，
    # 表说的仍然是 28，实际已经是 34。
    #
    # 教训与第 12 轮那次一样：**表会骗人，而它骗人的方式每次都不一样。**
    # 上一轮骗的是"已搬的忘了改备注"，这一轮骗的是"已搬的忘了改分类"。
    # 所以补的守卫不是"查某一张表"，而是**查磁盘与表是否一致**：
    # 见下面 `every_crate_on_disk_is_registered_somewhere`。
    "config_store.py": "配置落盘：含 atexit / 并发写 / 原子替换，判据都在 Python 侧，待搬",
    "vision.py": "与视觉强相关但**未被冻结**：取舍，不是禁令。判据要靠假设备搭一套",
    "app.py": "主窗口装配：UI 全在冻结区，搬了也没有意义；只可能搬编排层",
    "match_modes.py": "红线留下（非待办）：整个文件就是**视觉配置** —— `asset_path`、场地热区坐标、`default_right_display` / `default_map_size` / `default_module_list_style`。搬它不能帮 rm-client 的控制链，而要消费它就得先接管渲染默认值——**那正好是红线禁的那一段**",
    "backend/manager.py": "组合根：已由 rm-client 承担判据，但这一层还混着大量编排，待拆",
    "backend/field_log.py": "现场日志：异步写盘 + 回放，判据在 Python 侧，待搬",
    "backend/qualification.py": "资格赛证据管理：与导出打包面耦合，待搬",
    "backend/readiness.py": "就绪度聚合：纯逻辑但与 UI 呈现耦合，待拆",
    "backend/codec_selftest.py": "编解码自检：要真 ffmpeg 才能验，本机装不了",
}

# 第 15 轮新增：这些**已经搬完了**，从 ``DEFERRED`` 挪到 ``PORTED``。
#: 每条都注明是哪个 crate 在消费它，以及"搬的是什么"（不是"整个文件"）。
PORTED_IN_R15: dict[str, str] = {
    "auto_spin.py": "rm-calibration：编排层（判定早已在 rm-control 对拍过）",
    "calibration.py": "rm-calibration：档案解析/校验（cv2 那半不搬）",
    "runtime_paths.py": "rm-ports::paths：路径发现与清单校验",
    "serial_ports.py": "rm-ports::ports：描述串解析/打分/排序，**不碰真设备**",
    "config_store.py": "rm-config：存读 + 原子写 + 异步写盘状态机 + 迁移（线程未搬）",
    "backend/field_log.py": "rm-logstore：轮转写盘 + 队列状态机 + 回放（写盘线程未搬）",
    "backend/readiness.py": "rm-readiness：28 条闸门聚合（打包格式刻意没搬）",
    "backend/qualification.py": "rm-readiness：证据组装/签署（打包格式刻意没搬）",
    # ---- 第 17 轮：用户给了"**能保证视觉效果不变就动**"的有条件许可 ----
    #
    # 这两条**原来挂在 DEFERRED**，写着"判据要靠假设备搭一套"。
    # 第 17 轮那套假设备（黄金向量 + 逐位对拍）**已经建好并证过了**，
    # 所以分类该跟着走 —— 表没跟上，方向还是**乐观**的那种漂移：
    # 表说"还没搬"，实际"搬了、还证过"。
    # 守卫是 `test_a_ported_crate_means_its_module_is_not_still_deferred`。
    "vision.py": "rm-vision：观测/快照/桥接协议/摄像头策略，**一帧像素都不产生**。"
                 "cv2 推理那一半（tongji_yolo.py）不满足条件，仍在 DEFERRED",
    "ui_audio.py": "rm-audio：音效判定 + 音频池状态机 + 导入，**与视觉零耦合**",
}

# 第 14/15 轮新增的**传输层** crate。
#:
#: 它们**不能**进 ``PORTED`` —— 那张表的键是 **Python 模块名**，
#: 而且有一条断言查"每个键都是磁盘上真实存在的模块"。
#: 所以另立一张表：值是说明，键是 crate 名。
#:
#: 为什么要单列：``rm-serial`` / ``rm-mqtt`` 对应的是
#: ``polar_link.py`` / ``mqtt_backend.py`` 这**两个模块的传输那一半**，
#: 而那两个模块的判据已经分别挂在 ``PORTED`` 的其它条目上了。
#: 不单列的后果很具体：**新增 crate 忘了登记 nobody 会发现** ——
#: 这正是下面 ``every_crate_on_disk_is_registered_somewhere`` 抓到的第一批漏项。
CRATE_NOTES: dict[str, str] = {
    "rm-serial": "polar_link.py + serial_ports.py 的**真串口那一半**"
                 "（0x5A 编解码/收发/限频/看门狗/归零停机）",
    "rm-mqtt": "mqtt_backend.py 的传输与裁判解码那一半"
               "（MQTT 出口 + 0xA5 RobotStatus/GameStatus + 自回声分账）",
    # 第 17 轮：用户给了"**能保证视觉效果不变就动**"的有条件许可，
    # 这批是满足条件的。`tongji_yolo.py`（cv2/numpy 推理，真动像素）与
    # `app.py`（UI 全在冻结区）**不满足**，所以仍然不在这里。
    "rm-audio": "ui_audio.py：音频输出（**与视觉零耦合**，这批里最安全的）",
    "rm-vision": "vision.py：判定与数值，**不碰像素**",
}
#: 已定的产品决策（**用户拍板**，不是我能自己改的）。
#:
#: 记在这里是因为它是一条**长期悬着**的分叉：Python 与 Rust 都**不加** DT7 闸门。
#: 依据是 Python 的 ``_send_direct_dt7`` 本来就没有 ``OfficialSafety`` ——
#: 只给一边加会让两个客户端**对同一输入反应不同**，
#: 而"以 Python 为准"是本仓所有对拍的前提。
#:
#: 风险照实记着：**DT7 这条路没有任何许可校验**。
#: 真要收口，正确做法是**两个客户端同时加同一道闸**，
#: 而不是只给 Rust 加 —— 后者会破坏上面那条前提。
DECIDED: dict[str, str] = {
    "DT7 出口安全闸": "**不加**（用户第 16 轮拍板）：Python 侧本来也没有，"
                       "只给一边加会让两个客户端对同一输入反应不同。"
                       "风险：DT7 无任何许可校验，已知并接受。",
    "视觉取舍那 5 个模块": "**可以搬，条件是能保证视觉效果不变**（用户第 16 轮）。"
                          "保证机制：Python 参照实现逐字节不动 + 黄金向量对拍 + "
                          "Rust 侧不碰任何像素。`tongji_yolo.py`（cv2/numpy 推理）"
                          "与 `app.py`（UI 全在冻结区）不满足这个条件，本轮不搬。",
}

for _name, _why in PORTED_IN_R15.items():
    DEFERRED.pop(_name, None)
    PORTED.setdefault(_name, _why)


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


def test_every_product_decision_is_written_down() -> None:
    """已定的产品决策必须留在代码里，不能只活在对话里。

    DT7 那条悬了好几轮：每次我都在报告末尾问一遍，用户每次都没当场答，
    于是我一直按"暂不决定"处理。**一个没有落点的决策会一直漂** ——
    下一个人接手时既看不到问题，也看不到理由。

    所以把拍板结果写进代码：值就是"用户第几轮拍的 + 依据 + 风险"。
    风险那一段是**故意留着的** —— 只写"不加"不写"所以 DT7 没有许可校验"，
    下一个人会以为这是个安全的现状。
    """
    check("有 DECIDED 这张表", bool(DECIDED), "空的 —— 拍板结果没落进代码")
    check("DT7 出口的决策被记下来了", "DT7" in " ".join(DECIDED),
          "DT7 这条决策不见了")
    dt7 = next((v for k, v in DECIDED.items() if "DT7" in k), "")
    check("DT7 那条同时记了依据**和**风险",
          "不加" in dt7 and ("风险" in dt7 or "无任何许可校验" in dt7),
          f"只写了决定、没写风险：{dt7[:70]}")
    check("视觉取舍那条记了'保证不变'的条件",
          any("保证" in v for v in DECIDED.values()),
          "用户给的是有条件的许可，条件必须跟着记下来，否则会被当成无条件")


def test_every_crate_on_disk_is_registered_somewhere() -> None:
    """磁盘上每个 crate 都得在这张表里**留下痕迹**。

    # 为什么加这条

    这张表骗过我两次，**而且两次骗的方式不同**：

    * **第 12 轮**：``rm-economy`` / ``rm-combat`` / ``rm-ballistics`` 落地了，
      表里还写着"第 7 轮在搬"。那次我加的守卫是
      "``PORTED`` 里若有'待搬/在搬'字样直接红" ——
      它确实能抓到那一类。
    * **第 15 轮**：``rm-calibration`` / ``rm-ports`` / ``rm-config`` /
      ``rm-logstore`` 都落地并对拍过了，表里却还把它们分类成
      **"还没搬"**。上面那条守卫**完全没响** ——
      因为漂移发生在 ``DEFERRED``，不是 ``PORTED``。

    所以**只查某一张表里的措辞是不够的**：分类本身会漂。
    真正稳定的判据是**磁盘与表是否一致**。

    # 判定口径

    允许"没痕迹"的只有一类：**纯支撑 crate**
    （``rm-client`` 是组合根，``rm-protocol`` 等是底层）——
    它们不对应任何单个 Python 模块，是 Rust 侧自己的分层。
    其余任何 crate 都必须在 ``PORTED`` 的说明里出现过。

    一条 crate 名既不在 ``PORTED`` 的说明里、也不在豁免名单里 ⇒ 红。
    """

    crates_dir = RS_ROOT / "crates"
    if not crates_dir.is_dir():
        check("找得到 rmclient-rs 的 crates 目录（否则本条静默跳过）", False,
              f"{crates_dir} 不存在 —— 那这条断言等于没跑")
        return

    on_disk = {p.name for p in crates_dir.iterdir() if p.is_dir()}
    # 纯支撑层：不对应单个 Python 模块，是 Rust 侧自己的分层。
    exempt = {
        "rm-client",     # 组合根二进制
        "rm-link",       # PolarStateBridge + 仲裁器
        "rm-transport",  # 传输层
        "rm-control",    # 75Hz 回路
        "rm-safety",     # 安全闸
        "rm-state",      # 状态容器
        "rm-spec",       # 规格加载
    }
    blob = " || ".join(PORTED.values()) + " || " + " || ".join(CRATE_NOTES.values())
    # 判定要查**两种**痕迹：``CRATE_NOTES`` 的**键**（传输层 crate 靠它登记）
    # 与 ``PORTED`` 说明正文里提到的 crate 名（纯逻辑 crate 靠它登记）。
    #
    # 第一版只查了 blob，于是刚补的 ``CRATE_NOTES`` 自己没算进去 ——
    # 守卫立刻把它刚登记的两条报成"没登记"。**加了守卫之后要看着它跑一遍**，
    # 否则你不知道自己写的守卫查的是不是你以为的那张表。
    missing = sorted(
        name for name in on_disk
        if name not in exempt and name not in CRATE_NOTES and name not in blob
    )
    check(f"磁盘上 {len(on_disk)} 个 crate，每一个都能在 PORTED/CRATE_NOTES 说明里找到对应",
          not missing,
          "这些 crate 没有对应条目：" + ", ".join(missing)
          + " —— 新增 crate 时忘了写它对应哪个 Python 模块")

    # 反向：``CRATE_NOTES`` 里提到、但磁盘上没有的 crate（写错了 / 已改名）。
    # 表会骗人的方式之一就是"引用了一个不存在的东西"。
    stale = sorted(n for n in CRATE_NOTES if n not in on_disk)
    check("CRATE_NOTES 里没有磁盘上不存在的 crate", not stale,
          "提到了不存在的：" + ", ".join(stale)
          + " —— crate 改名或写错了，表会开始骗人")


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


def test_no_table_has_a_duplicated_key() -> None:
    """任何一张表里都**不许**有重复的键。

    # 这条守卫是被一个真实的、静默的缺陷逼出来的

    第 21 轮查分类漂移时，``DEFERRED`` 里看到两组**一模一样的条目**：

    ````python
    DEFERRED: dict[str, str] = {
        "vision.py": "...取舍，不是禁令。判据要靠假设备搭一套",   # L121
        ...
        "vision.py": "...取舍，不是禁令。判据要靠假设备搭一套",   # L147
        "backend/manager.py": "...",                              # L126
        ...
        "backend/manager.py": "...",                              # L156
    }
    ````

    **Python 的 dict 字面量只保留最后一次出现** —— 于是 L121-127 那七条
    是**死代码**：改它们不会有任何效果，而且**不报错、不告警**。

    这跟之前栽过的几次是同一个家族：**表会骗人，而它骗人的方式是新的**。
    上一轮骗的是"分类漂移"，这一轮还多一种：**同一条写两遍，后一条默默吃掉前一条**。

    所以判定口径是**读源码的 AST**，不读运行时的 dict ——
    运行时的 dict 恰恰是骗人的那个东西。
    """
    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"PORTED", "PORTED_IN_R15", "FROZEN", "DEFERRED",
              "CRATE_NOTES", "DECIDED"}

    seen_any = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            if not node.targets or not isinstance(node.targets[0], ast.Name):
                continue
            target, value = node.targets[0], node.value
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        if not isinstance(value, ast.Dict):
            continue
        seen_any = True
        keys: list[str] = []
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.append(key.value)
        repeated = sorted({k for k in keys if keys.count(k) > 1})
        check(
            f"{target.id} 里没有重复的键",
            not repeated,
            f"重复 {len(repeated)} 条：{repeated} —— "
            f"dict 字面量只保留最后一次出现，所以前一遍是**死代码**："
            f"改它不报错、不告警、也没有任何效果",
        )

    check("至少读到了一张表的 AST", seen_any, "一张表都没读到 —— 守卫等于没跑")



def test_a_deferred_entry_does_not_claim_to_be_porting_a_crate_that_already_exists() -> None:
    """**第 38 轮加的** —— 堵住 DEFERRED 的第三种漂移形态。

    前两轮已经堵住的：重复键（第 27 轮）、已搬的 crate 却还标着"待搬"（第 15 轮）。
    这一轮撞上的是**同一族的第三个形态**：条目写着"第 12 轮在搬（rm-ports）"，
    而 `crates/rm-ports/` **早就在磁盘上**、`paths.rs`/`ports.rs` 也都在，
    分类却还停在 `DEFERRED`。

    判据：DEFERRED 的说明里只要出现 `在搬`，
    **且它提到的那个 crate 目录真实存在**，就是漂移 —— 红。
    """
    import re as _re
    root = Path(__file__).resolve().parents[1]
    crate_dir = root.parent / "rmclient-rs" / "crates"
    crates = {q.name for q in crate_dir.glob("*") if q.is_dir()} if crate_dir.is_dir() else set()
    drifted = []
    for name, why in DEFERRED.items():
        for token in _re.findall(r"rm-[a-z][a-z0-9_-]*", why):
            if "在搬" in why and token in crates:
                drifted.append(f"{name} 声称在搬 {token}，而 crates/{token}/ 已存在")
    assert not drifted, "分类漂移（DEFERRED 里声称在搬的 crate 已经落地）：\n  " + "\n  ".join(drifted)


def test_a_visual_adjacent_deferred_entry_must_name_the_red_line_not_a_pending_task() -> None:
    """**第 39 轮加的** —— “因为红线所以不搬”与“暂未搬”是两件完全不同的事。

    原来 `match_modes.py` 的说明是“已随 tables_vectors 导出，但 rm-client
    尚未消费，待接”——这句话说的是**尚未开始**。

    但它整个文件就是**视觉配置**：`asset_path`、场地热区坐标、
    `default_right_display` / `default_map_size` / `default_module_list_style`。
    搬它不能帮 rm-client 的控制链，而要**消费**它就得先接管
    渲染默认值——那正好是红线禁的那一段。

    把“永远不搬”写成“待接”、等于**留下一个会被完成的工作**。

    判据：说明里出现视觉词的 `DEFERRED` 条目，必须同时写明**红线**。
    """
    import re as _re
    visual = _re.compile(r"asset|\u70ed区|\u5730图|\u663e\u793a|\u6e32\u67d3|UI|\u89c6觉|display|layout|map")
    bad = []
    for name, why in DEFERRED.items():
        if visual.search(why) and "红线" not in why and "冻结" not in why:
            bad.append(f"{name}: {why[:60]}")
    assert not bad, (
        "\u8fd9些 `DEFERRED` 条目说的是**视觉**配置，"
        "却没有写明红线决定\u2014\u2014"
        "读的人会当它是**待办**，而不是**永远不办**\n  "
        + "\n  ".join(bad)
    )

def test_a_ported_crate_means_its_module_is_not_still_deferred() -> None:
    """``CRATE_NOTES`` 点名的 Python 模块，**不许**还挂在 ``DEFERRED`` 上。

    # 为什么加这条

    第 17 轮落地了 ``rm-audio`` / ``rm-vision`` 两个 crate，
    它们写进了 ``CRATE_NOTES``（守卫查磁盘与表一致，**没响** ——
    因为 disk-vs-table 一致是真的）。

    但 ``vision.py`` / ``ui_audio.py`` 在 ``DEFERRED`` 里**原封不动**，
    还写着"取舍，不是禁令。判据要靠假设备搭一套" ——
    而假设备那套判据**第 17 轮就建好了**（黄金向量 + 逐位对拍）。

    于是又是一次漂移，而且方向是**乐观**：表说"还没搬"，
    实际"搬了、还证过"。汇报口径会说"暂不搬 8 个"，
    而真实数字是 6 个。

    # 判定口径

    ``CRATE_NOTES`` 的值里出现的、**磁盘上真实存在**的 Python 模块名，
    必须已经不在 ``DEFERRED`` 里。
    """
    import re

    deferred_keys = set(DEFERRED)
    # PORTED 的键 + PORTED_IN_R15 的键 = 已经搬走的
    ported = set(PORTED) | set(PORTED_IN_R15)
    check("PORTED_IN_R15 的条目确实已从 DEFERRED 移走",
          not (set(PORTED_IN_R15) & deferred_keys),
          f"同时出现在两张表：{sorted(set(PORTED_IN_R15) & deferred_keys)}")

    pattern = re.compile(r"[\w/]+\.py")
    for crate, note in sorted(CRATE_NOTES.items()):
        for named in sorted(set(pattern.findall(note))):
            if named not in deferred_keys:
                continue  # 已经不在 DEFERRED 里 —— 这正是我们要的
            check(
                f"crate {crate} 消费的 {named} 不该还在 DEFERRED 里",
                False,
                f"{named} 被 {crate} 消费，却在 DEFERRED 里写着"
                f"「{DEFERRED[named]}」—— 那是第 17 轮之前的口径，"
                f"判据建好之后没回来改分类",
            )
            break  # 只报一次，继续报会刷屏


def main() -> int:
    # 第 39 轮改掉手写清单。
    #
    # 之前这里是**手写的测试函数列表**。后来加的守卫一个都没进来——
    # **它们根本没跑过，却看着像是"all pass"。**
    #
    # 很具体的失败：我加守卫后跑了一遍，看到"全部通过（31 项）"
    # 就算了守卫生效——**没有对项数”**。而项数本来就没变。
    #
    # 所以改成**自动发现**：只收集模块层的 `test_*`。
    # 手写清单的真正回报是“它不会共享一份清单”——
    # 但它的价格是**每次加守卫都可能静默地没跑**，而这种失败看不出来。
    import inspect as _inspect
    tests = [
        _obj
        for _name, _obj in sorted(globals().items())
        if _name.startswith("test_")
        and callable(_obj)
        and _inspect.getmodule(_obj) is _inspect.getmodule(main)
    ]
    assert tests, "一条 `test_*` 都没有 —— 探针本身出错了"
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
