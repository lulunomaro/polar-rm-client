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
  4) 每行都附 file:line，任何人可以点开核。

诚实边界：窗口式统计会有串味（相邻模块共用一段代码时字段会被双方都记到）。
所以表里给的是"证据计数"而不是"结论"，列名也刻意这么写。真要判某个模块，
点它第一条证据去看代码。
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
from tools.quality.runtime_sources import legacy_implementation_sources  # noqa: E402

PKG = ROOT / "rm_client"
SPEC = json.loads((PKG / "backend" / "polar_protocol.json").read_text(encoding="utf-8"))

# 注册表声明与翻译表中的名字不是消费者证据。用确切路径，避免误排除同名实现文件。
DECLARATION_PATHS = frozenset({"model.py", "module_catalog.py", "ui/jello_locale.py"})
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


def py_files(*, include_legacy: bool = False) -> list[Path]:
    legacy = legacy_implementation_sources(ROOT)
    return [p for p in sorted(PKG.rglob("*.py")) if p.relative_to(PKG).as_posix() not in DECLARATION_PATHS
            and (include_legacy or p not in legacy)]


def legacy_module_mentions(legacy: dict[Path, str]) -> dict[str, list[tuple[Path, int]]]:
    """Retain historical evidence separately; never count it as live coverage."""
    mentions = {module.name: [] for module in build_default_registry().modules}
    patterns = {name: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
                for name in mentions}
    for path in sorted(legacy):
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name, pattern in patterns.items():
                if pattern.search(text):
                    mentions[name].append((path, line))
    return {name: locations for name, locations in mentions.items() if locations}


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


def retained_manual_section(path: Path) -> str:
    """Preserve maintained prose exactly, including its line endings.

    Never synthesize old field claims when an output has no manual section.
    An unreadable existing file raises before any output is overwritten.
    """
    if path.exists():
        content = path.read_bytes().decode("utf-8")
        heading = re.search(r"^## 4\.", content, re.MULTILINE)
        if heading is not None:
            return content[heading.start():]
    return ("## 4. 人工维护的链路索引与验证范围\n\n"
            "本节尚未维护。自动名字搜索不能生成链路实现或现场资格结论；"
            "请先核对当前源码与独立验证证据，再补充人工索引。\n")


def main() -> int:
    registry = build_default_registry()
    modules = registry.modules
    files = py_files()
    legacy = legacy_implementation_sources(ROOT)
    legacy_mentions = legacy_module_mentions(legacy)
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
    a("> 生成命令：`python tools/module_backend_map.py`（更新自动段；第 4 节已有人工内容原样保留）")
    a("> ")
    a("> **怎么读**：一行 = 一个模块。`附近出现的段字段`=模块名附近 8 行内数到的段字段（**弱证据，只用来指路**，见第 4 节）")
    a("> `其它通路`=官方裁判 / MQTT / 本地视觉 / 串口这几条非段通路；`上行`=它会不会往车里发东西；`主要落点`=可直接点开核对的文件")
    a("> 空列仅表示该搜索窗口没有命中；不能据此证明没有数据消费者。历史 Tk 实现单列，不能算作当前 Qt 运行路径覆盖。")
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
    a(f"## 1. {len(modules)} 个模块逐个对照")
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
            loc = "、".join(f"`{p}`" for p, _ in where.most_common(2)) or "（当前搜索未找到名字证据，需核查通用或动态消费者）"
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
    a("## 2. 当前运行源码中未找到名字证据的模块")
    a("")
    a("下面这些模块在当前运行源码搜索范围内未找到模块名证据。通用处理、动态查找和跨函数数据流仍需人工核查，")
    a("不能据此宣称功能无效；历史 Tk 消费端见下一节，也不能代替当前 Qt 路径的行为测试。")
    a("")
    dry = [m.name for m, h, *_ in rows if not h]
    if dry:
        a("| 模块 | 分类 | 设置项数 | 建议 |")
        a("|---|---|---:|---|")
        for name in dry:
            mm = registry.get(name)
            a(f"| **{name}** | {mm.category} | {len(mm.settings)} | 核查当前 Qt 路径的真实消费者与行为测试 |")
    else:
        a("（本轮每个模块均有名字落点；不代表每个设置或数据通路已经接通。）")
    a("")
    a("> 为什么这里**不列**「有 UI 落点但没数到段字段」那一档：这个客户端的数据流是")
    a("> PolarStateBridge.apply(段) 写进共享的 RobotState，渲染层再读 state.xxx，")
    a("> 而「本模块开没开」是在别处判的。名字邻近度抓不到这种跨函数数据流，所以那种空列全凭运气，")
    a("> 不作判定依据（第一版把 48 个模块全误判成「不碰机器人数据」，就是这条坑）。")
    a("")
    a("## 2.1 保留的历史 Tk 实现（不计当前运行覆盖）")
    a("")
    a("这里只对已验证的兼容入口做窄范围分类，不声称完成全程序可达性分析。旧文件移动到新名字不算功能改善；")
    a("AST 结构度量仍保留这些函数和复杂度，设置孤儿审计的口径也不因此自动改变。")
    if legacy:
        for path, proof in sorted(legacy.items()):
            a(f"- `{path.relative_to(ROOT).as_posix()}`：{proof}")
        a("")
        a("| 模块 | 历史名字证据（不计当前覆盖） |")
        a("|---|---|")
        for name, locations in sorted(legacy_mentions.items()):
            evidence = "、".join(f"`{path.relative_to(ROOT).as_posix()}:{line}`" for path, line in locations[:3])
            a(f"| {name} | {evidence} |")
    else:
        a("（未获得可验证的历史入口排除证据；所有非清单源码继续纳入搜索。）")
    a("")
    a("## 3. 已知不准的地方（别当真到那份上）")
    a("")
    a("注册表声明与翻译表不作为消费者证据：" + "、".join(
        f"`rm_client/{path}`" for path in sorted(DECLARATION_PATHS)) + "。")
    a("声明移动到 module_catalog.py 不会增加实现覆盖；它仍计入 AST 源码度量。")
    if dropped:
        a("")
        a("先交代被整文件丢掉的「清单文件」（它们只列名字、不含实现）：")
        for d in dropped:
            a(f"- `{d}`")
    a("")
    a(f"1. 统计窗口只有上下各 {WINDOW} 行、段字段要命中 >= {MIN_SEG} 次才写表 —— 取向是「宁可少写也不要写错」，"
       "所以**空列不代表这个模块一定没读该段**，只代表附近没数到；相邻模块共用绘制代码时仍可能互相记到；")
    a("2. 通用字段名（如 `energy`、`heat`）在多个段里都有，出现次数会被各段都记一份；")
    a("3. `—` 不等于\"这个模块一定没用\"，只等于\"在它自己那几行代码附近没找到这类关键字\"；")
    a("4. 真要改某个模块，先看第 1 列模块名在 `polar_app.py` / `ui/` 里的实现，别看这张表。")
    # Human-reviewed evidence is not inferred or regenerated from name counts.
    manual = retained_manual_section(ROOT / "MODULE_BACKEND_MAP.md")
    text = "\n".join(out) + "\n\n" + manual
    (ROOT / "MODULE_BACKEND_MAP.md").write_bytes(text.encode("utf-8"))
    print(f"写出 {ROOT/'MODULE_BACKEND_MAP.md'}，{len(text)} 字符，模块 {len(modules)} 个")
    for m, h, seg, chan, up, where in rows[:6]:
        print(" ", m.name, "| seg", seg.most_common(3), "| chan", chan, "| up", up)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
