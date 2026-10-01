# -*- coding: utf-8 -*-
"""生成 `MODULE_BACKEND_MAP.md`：59 个模块 <-> 后端实现 的通俗对照表。

为什么要脚本生成而不是手写
--------------------------
手写的模块表一定会过期，而且写的人容易把"我觉得它读这个"当成事实。
本脚本只做一件事：**从代码里取证据**——
  1) 模块清单来自 `build_default_registry()`（真实注册表，不是文档）；
  2) 每个模块名的出现位置来自全仓 grep（排除中英翻译表）；
  3) "读哪些数据段"由该模块代码附近出现的**段字段名**统计得出，
     段字段名来自 `polar_protocol.json`（协议唯一真源）与 `polar_bridge.py`；
  4) 每行都附 file:line，任何人可以点开核。第 4 节那些"带行号"的锚点
     也是**现查**的（`locate()`），不是写死的字符串。

诚实边界：窗口式统计会有串味（相邻模块共用一段代码时字段会被双方都记到）。
所以表里给的是"证据计数"而不是"结论"，列名也刻意这么写。真要判某个模块，
点它第一条证据去看代码。

两种用法
--------
    python tools/module_backend_map.py            # 重写 MODULE_BACKEND_MAP.md
    python tools/module_backend_map.py --check    # 只比对，不写；过期时 exit 1

为什么要有 --check：这份产物一旦过期，读者根本看不出它过期了（旧行号照样
能点开、点开是另一段代码），所以"过期"必须变成一条能红的机器判据，
而不是靠人记得重跑。tests/docs_and_tests_test.py 就是调它的。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rm_client.model import CATEGORIES, build_default_registry  # noqa: E402

PKG = ROOT / "rm_client"
SPEC = json.loads((PKG / "backend" / "polar_protocol.json").read_text(encoding="utf-8"))

# 不参与"实现落点"统计的文件：中英翻译表里每个模块名都出现一次，纯噪音
SKIP_FILES = {"jello_locale.py", "model.py"}
WINDOW = 8   # 证据行上下各取这么多行做字段扫描。
# 为什么这么小：窗口一大就会把隔壁模块的字段算进来（实测 26 行时
# System 类模块普遍被记成在读 0x0A/0x08，一眼就是假信号）。
MIN_SEG = 2  # 段字段命中次数低于这个数就不写进表（宁可少写，不要写错）

SEG_CN = {
    "0x01": "调试量 Debug",
    "0x02": "IMU 姿态",
    "0x03": "机器人状态 RobotStateInfo",
    "0x04": "比赛状态 GameStatus",
    "0x06": "全体血量 AllRobotHp",
    "0x07": "事件 Event（击杀/掉血/受伤）",
    "0x08": "裁判警告 RefereeWarning",
    "0x0A": "本体状态 RobotStatus（热量/电容/转速）",
    "0x0D": "增益 Buff",
    "0x0F": "射击数据 ShootData",
    "0x11": "RFID 定位（哨兵桩）",
    "0x13": "全场坐标 GroundRobotPosition",
    "0x1D": "关节状态 JointState",
    "0x1E": "底盘速度回读 RobotMotion",
    "0x1F": "底盘状态回读 ChassisState",
}

UPLINK_CN = {
    "robot_cmd": "0x01 官方机器人控制指令",
    "pid_debug": "0x02 PID 调参",
    "virtual_rc": "0x03 虚拟遥控器键位图（驾驶键位）",
    "chassis_cmd": "0x04 底盘自转 / 云台直控",
}

# 其它数据通路（不是 0x5A 段，而是各自的后端模块）
OTHER_CHANNELS = {
    "官方裁判协议": [r"\breferee\b", r"protocol\.py", r"RefereeProtocol", r"\bSPL\b"],
    "MQTT/图传": [r"\bmqtt\b", r"mqtt_backend", r"PRM1"],
    "本地视觉(同济)": [r"video_backend", r"\bvision\b", r"nuc", r"装甲板识别"],
    "串口链路": [r"serial_backend", r"polar_transport", r"QSerialPort", r"pyserial"],
    # 这一档以前是 `config_store` / `self.value` 之类，实测几乎每个模块都能命中，
    # 属于"永远非空所以毫无信息量"，直接去掉：没有数据通路就是"纯本地/UI"。
}


def segment_fields() -> dict[str, list[str]]:
    """段号 -> 该段独有的字段名（来自协议 JSON，不是手写清单）。"""
    out: dict[str, list[str]] = defaultdict(list)
    for seg in SPEC["downlink_segments"]:
        sid = seg.get("id") or seg.get("segment")
        sid = str(sid)
        fields: list[str] = []
        for block in ("state_fields", "output", "outputs", "fields"):
            value = seg.get(block)
            if isinstance(value, dict):
                fields += list(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        fields.append(str(item.get("name") or item.get("field") or ""))
        uniq = []
        for f in fields:
            f = str(f).strip()
            if len(f) >= 3 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f) and f not in uniq:
                uniq.append(f)
        out[sid] = uniq
    return dict(out)


def py_files() -> list[Path]:
    return [p for p in sorted(PKG.rglob("*.py")) if p.name not in SKIP_FILES]


#: "清单文件"判据：**同时**满足"出现的模块名够多"和"密度够高"。
#: 光看数量会误杀 —— polar_app.py 里出现 24 个模块名，可它 5000 多行、
#: 平均每 200+ 行才提一次名字，那里就是实现本体；而 renderers/module_list.py
#: 是"把 59 个名字挨个列一遍"，密度高得多。所以第二条（密度）才是关键。
LIST_FILE_THRESHOLD = 20
LIST_FILE_DENSITY = 0.02  # 模块名种类数 / 文件行数；约"每 50 行就点一个名字"


def build_index(files: list[Path]) -> dict[str, list[tuple[Path, int]]]:
    """模块名 -> 出现位置（file, line）。按整词匹配，避免 EnergyAutoAim 命中 AutoAim。

    会**整文件丢掉清单文件**：像 renderers/module_list.py 那种把 59 个名字挨个
    列一遍的文件，命中数极高却没有一条实现证据。不丢的话"主要落点"这一列会被
    module_list.py / match_modes.py 占满，真正的实现文件反而被挤掉（第一次生成
    实测就是这样，整张表都在指向清单文件）。
    """
    names = [m.name for m in build_default_registry().modules]
    pats = {n: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])") for n in names}
    hits: dict[str, list[tuple[Path, int]]] = {n: [] for n in names}
    lines_cache: dict[Path, list[str]] = {}
    dropped: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        found = {n for n, pat in pats.items() if pat.search(text)}
        density = len(found) / max(1, len(lines))
        if len(found) >= LIST_FILE_THRESHOLD and density >= LIST_FILE_DENSITY:
            dropped.append(
                f"`{path.relative_to(ROOT).as_posix()}`：出现 {len(found)} 个模块名，"
                f"平均每 {max(1, int(len(lines) / len(found)))} 行就点一个（清单，不含实现）")
            continue
        lines_cache[path] = lines
        for lineno, line in enumerate(lines, 1):
            for name, pat in pats.items():
                if pat.search(line):
                    hits[name].append((path, lineno))
    build_index._cache = lines_cache  # type: ignore[attr-defined]
    build_index._dropped = dropped  # type: ignore[attr-defined]
    return hits


def window_text(hits: list[tuple[Path, int]]) -> str:
    cache = build_index._cache  # type: ignore[attr-defined]
    chunks: list[str] = []
    for path, lineno in hits:
        lines = cache[path]
        chunks.append("\n".join(lines[max(0, lineno - 1 - WINDOW):lineno + WINDOW]))
    return "\n".join(chunks)


def _cat_counts(modules) -> "Counter[str]":
    """分类分布，用来核对表格有没有把 59 个模块都写进去。"""
    from collections import Counter as _C

    return _C(m.category for m in modules)


# ---------------------------------------------------------------- 行号锚点
#: 下面这张表是第 4 节里那些"给人点开核对"的锚点。
#: **只写"去哪找"，不写"在第几行"**：行号在生成时现查（`locate`），
#: 查不到就把「未找到」原样写进文档，绝不沿用上一版的旧数字。
#: 实测代价：写死版有 9 个行号（polar_link 3 个、polar_transport 3 个、
#: manager 2 个、auto_spin 1 个），其中 3 个已经漂到无关代码
#: （`manager.py:222` 落在 MQTT 端口回退提示上、`polar_transport.py:487`
#: 落在 `gimbal_yaw_rad=...` 上），而**重跑脚本修不好**它们——那是字符串字面量。
#: 三元组 = (仓库相对路径, 在源码里现找的那一行原文, 文档里显示的符号名)。
ANCHORS = {
    "robot_cmd": ("rm_client/backend/polar_link.py", "def encode_robot_cmd(", "encode_robot_cmd"),
    "virtual_rc": ("rm_client/backend/polar_link.py", "def encode_virtual_rc(", "encode_virtual_rc"),
    "chassis_cmd": ("rm_client/backend/polar_link.py", "def encode_chassis_cmd(", "encode_chassis_cmd"),
    "tx_pump": ("rm_client/backend/polar_transport.py", "def _write_after_intent_held(",
                "_write_after_intent_held"),
    "tx_frame": ("rm_client/backend/polar_transport.py", "def _frame_for(", "_frame_for"),
    "tx_intent": ("rm_client/backend/polar_transport.py", "def _take_intent_locked(",
                  "_take_intent_locked"),
    "tx_release_zero": ("rm_client/backend/polar_transport.py",
                        "self._write(self._zero_virtual_rc_frame())",
                        "松键下降沿补发全零 0x03"),
    "spin_switch": ("rm_client/backend/manager.py", "self._spin_assert = cmd",
                    "_spin_assert 落定（自转开关联动判定）"),
    "estop": ("rm_client/backend/manager.py", "def release_estop(", "release_estop"),
    "spin_controller": ("rm_client/auto_spin.py", "class ProjectileHitSpinController:",
                        "ProjectileHitSpinController"),
    "spin_update": ("rm_client/auto_spin.py", "def update(", "update（受击后限时开自转的判定）"),
}

_SOURCE_CACHE: dict[str, list[str]] = {}


def locate(key: str) -> str:
    """把锚点名解析成 `文件:行号 符号名`，供第 4 节拼表格用。

    匹配规则：`needle` 按**去掉缩进后的前缀**匹配整行（所以
    `def _frame_for(` 能命中签名换行写的长定义行）。

    返回值的三种形态：
      * `rm_client/backend/polar_link.py:617 encode_robot_cmd` —— 找到了
      * `rm_client/backend/polar_link.py:未找到(def encode_robot_cmd()`
        —— 文件在但符号没了（改名/删除），把这条明写进文档，逼人去改
      * `rm_client/xxx.py:缺失` —— 文件本身不在了

    为什么"查不到"要显式写出来：错的行号比没有行号更坑——它能点开，
    读者会以为核对过了。
    """
    rel, needle, label = ANCHORS[key]
    path = ROOT / rel
    if not path.exists():
        return f"{rel}:缺失（{label}）"
    lines = _SOURCE_CACHE.setdefault(
        rel, path.read_text(encoding="utf-8", errors="replace").splitlines())
    probe = needle.strip()
    for i, line in enumerate(lines, 1):
        if line.strip().startswith(probe):
            return f"{rel}:{i} {label}"
    return f"{rel}:未找到({needle.strip()} → {label})"


def anchors() -> dict[str, str]:
    return {key: locate(key) for key in ANCHORS}


def render() -> str:
    """产出第 1–4 节的 Markdown 文本（纯函数：不碰磁盘、不读时间戳）。

    抽出来是为了让 `tests/docs_and_tests_test.py` 能在不写文件的前提下
    拿产物做逐字节比对 —— 写文件再读回来会让"生成时那一刻的树"和
    "比对时那一刻的树"之间产生竞态。
    """
    registry = build_default_registry()
    modules = registry.modules
    files = py_files()
    hits = build_index(files)
    dropped = getattr(build_index, '_dropped', [])
    fields = segment_fields()

    # 字段名 -> 段号（同名字段归给所有出现的段，统计时如实标注"共享"）
    owner: dict[str, list[str]] = defaultdict(list)
    for sid, names in fields.items():
        for f in names:
            owner[f].append(sid)

    rows = []
    for m in modules:
        h = hits.get(m.name, [])
        text = window_text(h) if h else ""
        seg_counts: Counter[str] = Counter()
        for f, sids in owner.items():
            n = len(re.findall(rf"(?<![A-Za-z0-9_]){re.escape(f)}(?![A-Za-z0-9_])", text))
            if n:
                if n < MIN_SEG:
                    continue
                for sid in sids:
                    seg_counts[sid] += n
        chan = {}
        for label, pats in OTHER_CHANNELS.items():
            n = sum(len(re.findall(p, text, re.I)) for p in pats)
            if n:
                chan[label] = n
        up = {k: len(re.findall(rf"(?i){re.escape(k)}", text)) for k in UPLINK_CN}
        up = {k: v for k, v in up.items() if v}
        where = Counter(p.relative_to(ROOT).as_posix() for p, _ in h)
        rows.append((m, h, seg_counts, chan, up, where))

    # ---------------- 输出 Markdown ----------------
    out: list[str] = []
    a = out.append
    a("# 模块 ↔ 后端实现 对照（通俗版，脚本生成）")
    a("")
    a("> 生成命令：`python tools/module_backend_map.py`（本文件即其输出）"
      "　过期自检：`python tools/module_backend_map.py --check`（exit 1 = 该重跑了）")
    a("> ")
    a("> **改了 `rm_client/` 下任何一个文件都要重跑**：这张表是数出来的，"
      "代码一动证据就变，而过期的行号照样能点开、只是点到了别的函数——"
      "没人看得出它过期了，所以它由 `--check` / `tests/docs_and_tests_test.py` 把关。")
    a("> ")
    a("> **怎么读**：一行 = 一个模块。`附近出现的段字段`=模块名附近 8 行内数到的段字段（**弱证据，只用来指路**，见第 4 节）")
    a("> `其它通路`=官方裁判 / MQTT / 本地视觉 / 串口这几条非段通路；`上行`=它会不会往车里发东西；`主要落点`=可直接点开核对的文件")
    a("> 整行数据列全空 = 这个模块后端其实没人喂数据")
    a("")
    a("## 0. 先弄清楚：客户端的数据一共从哪几条路进来")
    a("")
    a("| 通路 | 是什么 | 谁能给 | 缺了会怎样 |")
    a("|---|---|---|---|")
    a("| **队内 0x5A 下行** | 咱们自己电控板通过 USB 线/图传吐出的 15 个自定义段 | 队内 StandardRobot++ 固件（`usb_task.c`） | 依赖它的模块只能靠官方数据或干脆空着 |")
    a("| **官方裁判协议** | 裁判系统发给机器人的标准报文（血量、比分、伤害、增益…） | 赛场裁判系统 + `backend/protocol.py` | 战况类模块全瞎 |")
    a("| **MQTT / 图传** | 官方云端那套（PRM1） | `backend/mqtt_backend.py` | 远程指挥类模块不可用 |")
    a("| **本地视觉** | 同济视觉 / NUC 那一路识别结果 | `backend/video_backend.py` | 视觉自瞄退化成只看血量 |")
    a("| **上行（客户端发出去）** | 0x01 控制指令、0x02 调参、0x03 虚拟遥控器键位、0x04 底盘自转与云台直控 | 只有插了 USB 线（或走通图传）才到得了车 | 只能看不能开 |")
    a("")
    a("## 1. 59 个模块逐个对照")
    a("")
    a("列说明：**证据计数**是在模块名出现位置上下各 {} 行的代码里数出来的字段/关键字出现次数，".format(WINDOW))
    a("不是官方结论，但每条都附了出处，点进去就能核对。`—` 表示该模块在这条通路上没有证据。")
    a("")

    for cat in CATEGORIES:
        cat_rows = [r for r in rows if r[0].category == cat]
        if not cat_rows:
            continue
        a("")
        a(f"### 分类 {cat}（{len(cat_rows)} 个）")
        a("")
        a("| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |")
        a("|---|---|---:|---|---|---|---|")
        for m, h, seg_counts, chan, up, where in cat_rows:
            seg_txt = "、".join(
                f"{sid} {SEG_CN.get(sid, '?')} x{n}" for sid, n in seg_counts.most_common(4)
            ) or "—"
            chan_txt = "、".join(
                f"{k}x{v}" for k, v in sorted(chan.items(), key=lambda kv: -kv[1])[:4]
            ) or "—"
            up_txt = "、".join(
                f"{UPLINK_CN[k].split(' ')[0]}x{v}" for k, v in up.items()
            ) or "—"
            loc = "、".join(f"`{p}`" for p, _ in where.most_common(2)) or "（只在注册表里出现，**没有消费端**）"
            a(f"| **{m.name}** | {'开' if m.enabled else '关'} | {len(m.settings)} "
              f"| {seg_txt} | {chan_txt} | {up_txt} | {loc} |")
        # 出现注册表里但分类不在 CATEGORIES 的，兜底单独列出，避免漏模块
    extra = [r for r in rows if r[0].category not in CATEGORIES]
    if extra:
        a("")
        a(f"### 分类未登记（{len(extra)} 个，界面里不会出现，需修注册表）")
        for m, *_ in extra:
            a(f"- {m.name}")
    a("")
    a("## 2. 真·没有后端的模块")
    a("")
    a("下面这些模块的名字**只出现在注册表**（rm_client/model.py 的 build_default_registry()）里，")
    a("全仓没有任何实现读它 —— 界面上能勾、能存盘，但什么都不会发生。")
    a("")
    dry = [m.name for m, h, *_ in rows if not h]
    if dry:
        a("| 模块 | 分类 | 设置项数 | 建议 |")
        a("|---|---|---:|---|")
        for name in dry:
            mm = registry.get(name)
            a(f"| **{name}** | {mm.category} | {len(mm.settings)} | 要么接上、要么从界面删掉；"
              f"留着比删掉更坏：操作员会以为它在起作用 |")
    else:
        a("（本轮没有查到零消费端的模块。）")
    a("")
    a("> 为什么这里**不列**「有 UI 落点但没数到段字段」那一档：这个客户端的数据流是")
    a("> PolarStateBridge.apply(段) 写进共享的 RobotState，渲染层再读 state.xxx，")
    a("> 而「本模块开没开」是在别处判的。名字邻近度抓不到这种跨函数数据流，所以那种空列全凭运气，")
    a("> 不作判定依据（第一版把 48 个模块全误判成「不碰机器人数据」，就是这条坑）。")
    a("")
    a("## 3. 已知不准的地方（别当真到那份上）")
    if dropped:
        a("")
        a("先交代被整文件丢掉的「清单文件」（它们只列名字、不含实现）：")
        for d in dropped:
            a(f"- {d}")
    a("")
    a(f"1. 统计窗口只有上下各 {WINDOW} 行、段字段要命中 >= {MIN_SEG} 次才写表 —— 取向是「宁可少写也不要写错」，"
       "所以**空列不代表这个模块一定没读该段**，只代表附近没数到；相邻模块共用绘制代码时仍可能互相记到；")
    a("2. 通用字段名（如 `energy`、`heat`）在多个段里都有，出现次数会被各段都记一份；")
    a("3. `—` 不等于\"这个模块一定没用\"，只等于\"在它自己那几行代码附近没找到这类关键字\"；")
    a("4. 真要改某个模块，先看第 1 列模块名在 `polar_app.py` / `ui/` 里的实现，别看这张表。")
    a("")
    # ---------------- 5. 人写的重点模块对照 ----------------
    # 机器只能数名字，说不出"这个模块的数据其实从哪个函数进来"。
    # 这一节是逐条核过代码写的。表格单元格里要换行只能用 <br>，
    # 直接放换行符会把整行 Markdown 表格打断。
    # **行号不写死**：下面每一个 `A[...]` 都在生成时现查（`anchors()`），
    # 写死的那一版已经漂了 3 处而重跑脚本修不掉——因为它是字符串字面量。
    A = anchors()
    hero_lob = registry.get("HeroLob")
    hero_lob_settings = len(hero_lob.settings) if hero_lob is not None else "?"
    a("## 4. 重点模块：数据到底从哪个函数进来（人写的，行号生成时现查）")
    a("")
    a("第 1 节是机器数出来的，只会指路；这一节是逐条核过代码写的。要改哪个模块，先看这里。")
    a("")
    a("> 每一处行号都由 `locate()` 在**生成这一刻**现查 `rm_client/` 源码得到，")
    a("> 脚本里只写「去哪找」（文件 + 那一行原文），不写死行号。")
    a("> 真出现 `未找到` / `缺失` 就是符号被改名或文件被删了，**不要**沿用上一版的旧数字。")
    a("")
    a("### 开车 / 控车这条线（队内 0x5A 上行）")
    a("")
    a("| 模块 | 后端实现在哪 | 说人话 |")
    a("|---|---|---|")
    a(f"| **RobotLink** | 编码：`{A['robot_cmd']}`(0x01)、`<br>`{A['virtual_rc']}`(0x03 键位图)、"
      f"`<br>`{A['chassis_cmd']}`(0x04 自转+云台直控)；`<br>`"
      f"成帧与写串口：`{A['tx_pump']}`(分派)、`{A['tx_frame']}`(0x01 成帧)、`{A['tx_intent']}`(取意图+死区)；"
      f"`<br>自转开关联动：`{A['spin_switch']}` | 整条「客户端往车里发东西」的路都归它。"
      f"松键时它会补发一次全零（`{A['tx_release_zero']}`），所以固件那边不会因为停发而冻在最后一帧 |")
    a(f"| **SpinMode** / **AutoSpinOnHit** | `{A['spin_controller']}`（受击后限时开自转，判定在 `{A['spin_update']}`）"
      f"→ 上面那个 `encode_chassis_cmd()`（上行 0x04）；状态回读是固件下行 0x1F | "
      "以前只能借 SHIFT 键，而固件对 SHIFT 是边沿翻转 —— **关不掉**；现在是显式 ON/OFF + 回读闭环 |")
    a(f"| **EmergencyStop** | 行为在 `backend/safety.py`(OfficialSafety) 与 `{A['estop']}`；"
      "但注册表里那个 `EmergencyStop` 模块**没有任何代码读它** | "
      "急停本身是真有的，只是「界面那个模块条目」和它之间没接线 —— 界面上那个开关是装饰 |")
    a("")
    a("### 看数据这条线")
    a("")
    a("| 模块 | 后端实现在哪 | 说人话 |")
    a("|---|---|---|")
    a("| **HUD** / **CombatTargetHUD** / **RobotOverlay** / **RadarOverlay** / **TacticalMap** | 渲染在 `ui/polar_app.py` 与 `ui/polar/renderers/*`；数据一律读共享的 `RobotState`，由 `backend/polar_bridge.py` 的 `apply(segment)` 按段号写进来 | 队内 15 个下行段和官方裁判报文**汇到同一个 RobotState**，所以这些面板不区分数据是谁给的 —— 这也是「没插 USB 线时界面照样动」的原因 |")
    a("| **BuffTimeline** | `ui/polar/renderers/tactical_map.py`（地图下方那条带）；数据是下行 0x0D Buff | 这个模块以前一个消费者都没有，现在才真有一排标签；带上的剩余秒数就是「时间轴」开关管的东西 |")
    a("| **CameraFeed** / **TongjiVisionBridge** / **Armor3DESP** | `backend/video_backend.py` + `ui/polar/mixins/video_backend_mixin.py`；3D 线框在 `renderers/esp_wireframe.py` | 走本地视觉这一路，跟队内 0x5A 无关，不插 USB 线也能用 |")
    a("| **MQTTLink** / **UDPVideo** | `backend/mqtt_backend.py` / `backend/video_backend.py` | 界面上有「自动连接 / 重组」这类开关，但**刻意没接**（接上会让官方场景自己去抢端口），理由写在 `tools/SETTINGS_GAPS.md` |")
    a("| **SerialBridge** | `backend/serial_backend.py`（官方裁判串口那一路） | 与队内 0x5A 是两条互相独立的串口链路，互不代管 |")
    a(f"| **HeroLob** | {hero_lob_settings} 个设置项（数出来的，不是手写的），主要被 `ui/polar/mixins/` 引用 | 英雄投石那套界面参数；**注意**它和固件里的「英雄兵种」是两回事，固件侧不接这些参数 |")
    a("")
    a("### 和这次 DT7 适配直接相关的那一条")
    a("")
    a("上行 0x03 的键位图到了车上是**或**进 `rc_ctrl.key.v` 的 —— 固件那边叫 `HOST_RC_HOST_KEYS` 这本账（`tools/firmware/host_bridge/RC_LINKS.md` §3）。而比赛链路本身是「电脑 USB 插 DT7 → 无线 → 车上 DR16 → UART3 的 18 字节帧」，那个帧自带驾驶员的键位与鼠标。两者是叠加、不是互相接管：驾驶员松手或客户端掉线，各自那份账都会归零。")
    a("")
    a("### 还有一条我们客户端暂时走不了、但你们现场用过的路")
    a("")
    a("以前打比赛是**大疆官方客户端 USB 连 DT7** 开车 —— 这说明 DT7 的 USB 口就是 PC 键鼠的注入通道，")
    a("而固件侧（SBUS 18 字节 -> key.v / mouse -> 底盘与云台）本来就是通的、不用改一行。")
    a("本客户端缺的只有「PC <-> DT7」那一段 DJI 私有协议：全仓搜过 dt7/dr16/hid/joystick，没有任何实现。")
    a("侦察工具已写好：`python tools/dt7_hid_probe.py --watch 20`（零依赖，先定它是 HID 设备还是 CDC 虚拟串口），")
    a("`--capture`（需 `pip install hidapi`）能把报文直接读出来。细节见 `tools/firmware/host_bridge/RC_LINKS.md` 第 7 节。")
    a("")
    text = "\n".join(out) + "\n"
    return text


def main() -> int:
    """写文件模式；`--check` 只比对不写（过期时 exit 1）。"""
    text = render()
    target = ROOT / "MODULE_BACKEND_MAP.md"
    check_only = "--check" in sys.argv[1:]
    on_disk = target.read_text(encoding="utf-8") if target.exists() else ""
    if check_only:
        if on_disk == text:
            print(f"[OK] {target.name} 与生成器输出一致（{len(text)} 字符）")
            return 0
        print(f"[STALE] {target.name} 与生成器输出不一致："
              f"盘上 {len(on_disk)} 字符 / 生成 {len(text)} 字符。"
              f"改过 rm_client/ 就重跑 python tools/module_backend_map.py")
        diff = [i for i, (x, y) in enumerate(zip(on_disk.splitlines(), text.splitlines()), 1)
                if x != y]
        print(f"        首个不同的行：{diff[:8] if diff else '（行数不同）'}")
        return 1
    target.write_text(text, encoding="utf-8")
    print(f"写出 {target}，{len(text)} 字符，模块 {len(build_default_registry().modules)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
