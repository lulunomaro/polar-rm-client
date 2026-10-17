#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二级界面设置项审计：470 个可自定义项里，哪些**代码根本没人读**。

为什么需要它
------------
ClickGUI 里 74 个模块、470 个设置项，每一个都是界面上能拖/能勾的。
但"界面上能调"和"调了有作用"是两件事：

    界面上能调 → 代码里有人读 → 读到的值真的影响了行为

中间任何一环断了，用户看到的就是**一个动不了的滑块**：拖了、存了、
重启也还在，但机器人/画面毫无变化。这比功能缺失更糟——功能缺失
用户知道没有，而这个看起来像是有的。

判定方法（全部可复核）
----------------------
对每个设置项名，扫描整个 rm_client/ 找**读取点**：

    读取点  = 出现 "<设置名>" 且不在定义行里
    定义行  = bool_setting("X" / number_setting("X" / mode_setting("X" /
              action_setting("X" / action_group_setting("X"

然后分档：

    ORPHAN   0 个读取点 —— 界面能调，代码完全没人读，纯装饰
    DYNAMIC  没有字面读取点，但代码里有能拼出这个名字的 f-string /
             字符串拼接，或精确命令解析与消费端相连 —— 需人工复核
    READ     有字面读取点，列出在哪些文件

为什么必须有 DYNAMIC 这一档
--------------------------
第一版没有它，结果把 RobotLink 的 16 个 `Bind W` / `Bind S` / … 全报成
ORPHAN。实际上它们是被 `f"Bind {slot}"` 这样**动态拼名字**读的
（见 rm_client/control.py 的 resolve_slot）。按字面名搜永远搜不到。
一大块假警报混在真问题里，等于把整份报告作废——人会直接不信它。

局限（必须说清楚，否则结论会被过度解读）
------------------------------------------
1. **按名字全局匹配**：不同模块可能有同名设置项（例如 "Power Guard"
   同时出现在 SpinMode 和 AutoSpinOnHit）。这种情况下读取点无法归属到
   具体模块，工具会标 `名字被 N 个模块共用`，需要人工看上下文。
2. **"有人读"不等于"读对了"**：本工具只证明存在读取点。值是否真的
   影响了下发字节/画面，要看 tests/ 里的端到端断言。
   SpinMode 的 "Spin Speed" 就是反例——有人读，但固件的自转只是一个
   开关标志，转速由固件自己定，读了也兑现不了。
3. **读了但兑现不了**这一档工具判不出来，需要对照 GAPS.md 人工确认。

用法
----
    python tools/audit_settings.py                    # 汇总 + 只列 ORPHAN
    python tools/audit_settings.py --all              # 列全部 470 项
    python tools/audit_settings.py --module SpinMode  # 只看一个模块
    python tools/audit_settings.py --json out.json

默认退出码 0；指定 --max-orphans 时超过上限返回 1。动态证据不是行为资格。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

# 必须在导入 rm_client 之前设置：本脚本会构造真实 registry。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("POLAR_USER_DATA_ROOT", "").strip():
    os.environ["POLAR_USER_DATA_ROOT"] = tempfile.mkdtemp(prefix="polar_audit_set_")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.settings_action_evidence import (
    DISPATCH_SOURCE, dynamic_action_evidence, dynamic_action_routes,
)

SRC_ROOT = ROOT / "rm_client"

#: 设置项的定义辅助函数。出现在这些调用里的名字算"定义"，不算"读取"。
DEFINERS = (
    "bool_setting", "number_setting", "mode_setting",
    "action_setting", "action_group_setting",
)

_DEFINITION_RE = re.compile(
    r"\b(?:" + "|".join(DEFINERS) + r")\(\s*\n?\s*[\"']([^\"']+)[\"']"
)

#: 能动态拼出设置项名的写法：f"Bind {slot}"、"Bind " + slot、f"{prefix} Hz"。
#: 只取字面部分做前缀/后缀匹配，宁可多报 DYNAMIC 也不要漏报成 ORPHAN。
_FSTRING_RE = re.compile(r"f[\"']([^\"']*?)\{[^}]*\}([^\"']*?)[\"']")
_CONCAT_RE = re.compile(r"[\"']([A-Za-z][A-Za-z0-9 _%/-]{1,40})[\"']\s*\+\s*\w")
_CONCAT_REV_RE = re.compile(r"\w\s*\+\s*[\"']([A-Za-z][A-Za-z0-9 _%/-]{1,40})[\"']")

#: 动态模板只有在**同一行确实在读设置**时才算证据。
#: 为什么必须加这一条：不加的话 `Rune Type` 会被当成 DYNAMIC，
#: 因为 tactical_map.py:522 有一句 `self.toast(f"Rune {'sent' if ...}")`——
#: 那是一句**提示文字**，不是读设置。同理，model.py 里用
#: `mode_setting(f"Bind {slot}", ...)` 批量**生成**设置项，那是定义点。
#: 两边都不是读取，但字面前缀都对得上。
_READ_CALL_RE = re.compile(
    r"\bget_setting\s*\(|\bsettings\s*\[|\bsettings\.get\s*\(|\bsetting\s*\(\s*f[\"']"
)
#: 生成设置项的定义点，即使带了 get_setting 也不算读取证据。
_DEFINING_LINE_RE = re.compile(
    r"\b(?:" + "|".join(DEFINERS) + r")\s*\(\s*f[\"']"
)


def collect_dynamic_templates(sources: list[tuple[Path, list[str]]]) -> list[tuple[str, str, str, str]]:
    """找出所有可能拼出设置项名的模板。

    只收集**确实在读设置**的行，排除两类干扰：
      - 提示文字/日志里的 f-string（如 `toast(f"Rune {...}")`）
      - 批量生成设置项的定义行（如 `mode_setting(f"Bind {slot}", ...)`）

    返回 (前缀, 后缀, 文件, 行号)。前缀/后缀都可为空串。
    """
    out: list[tuple[str, str, str, str]] = []
    for path, lines in sources:
        rel = str(path.relative_to(ROOT)).replace(os.sep, "/")
        for i, line in enumerate(lines, 1):
            if _DEFINING_LINE_RE.search(line):
                continue          # 定义点，不是读取点
            if not _READ_CALL_RE.search(line):
                continue          # 这一行根本没在读设置
            for m in _FSTRING_RE.finditer(line):
                out.append((m.group(1), m.group(2), rel, str(i)))
            for m in _CONCAT_RE.finditer(line):
                out.append((m.group(1), "", rel, str(i)))
            for m in _CONCAT_REV_RE.finditer(line):
                out.append(("", m.group(1), rel, str(i)))
    return out


def match_dynamic(name: str, templates: list[tuple[str, str, str, str]]) -> list[str]:
    """该设置名能否由某个模板拼出？返回命中的位置描述。"""
    hits: list[str] = []
    for prefix, suffix, rel, line in templates:
        if not prefix and not suffix:
            continue
        # 前缀太短会到处命中（例如 f"{x} Hz" 的前缀是空），
        # 所以要求字面部分至少 3 字符，且确实是对应位置的前/后缀。
        if prefix and len(prefix) >= 3 and name.startswith(prefix) \
                and (not suffix or name.endswith(suffix)):
            hits.append(f"{rel}:{line}  f\"{prefix}{{...}}{suffix}\"")
        elif suffix and len(suffix) >= 3 and name.endswith(suffix) \
                and (not prefix or name.startswith(prefix)):
            hits.append(f"{rel}:{line}  f\"{prefix}{{...}}{suffix}\"")
    return hits[:4]


#: 这些文件里的字面出现**不是读取点**。
#:
#: 为什么必须排除：`jello_locale.py` 是中英翻译表，里面全是
#: `"Show Bar": "电容进度条"` 这样的条目。不排的话，审计会把
#: “这个设置项有中文名”当成“这个设置项有人读”，直接把孤儿洗白成
#: READ。实测踏过：`Capacitor / Show Bar` 接线前零读取点，
#: 却被报成 READ，就因为它在翻译表里。
I18N_FILE_HINTS = ("locale", "i18n", "translation", "strings")


def is_i18n_source(path: Path) -> bool:
    stem = path.name.lower()
    return any(hint in stem for hint in I18N_FILE_HINTS)


def collect_sources() -> list[tuple[Path, list[str]]]:
    """读入 rm_client/ 下所有 .py（排除翻译表），返回 (路径, 行列表)。"""
    out: list[tuple[Path, list[str]]] = []
    for p in sorted(SRC_ROOT.rglob("*.py")):
        if is_i18n_source(p):
            continue
        try:
            out.append((p, p.read_text(encoding="utf-8", errors="ignore").splitlines()))
        except OSError:
            continue
    return out


def build_const_aliases() -> dict[str, tuple[str, ...]]:
    """收集形如 `FOO_SETTING = "Language"` 的全大写模块级常量。

    为什么必须有：只匹配字面量会把"经由常量读取"的设置项误判成孤儿。
    实测踏过：`ClickGui / Language` 确实被读，但写法是
    `module.get_setting(LANGUAGE_SETTING)`（jello_locale.py 与
    input_dispatch_mixin.py 各一处），字面量只出现在常量的定义行。
    不补这一层，基线里会混入假孤儿，"只减不增"的闸门就是假的。

    只匹配双引号形式：仓库经 black 格式化，设置项字面量一律双引号，
    支持撇号版本没有实际收益，反而迫使源码写转义（上一版就是这么写坏的）。

    注意：这里**不排除**翻译表。定义常量在哪里都行，需要排除的只是
    "把翻译表里的字面量当成读取点"——两者不同。`LANGUAGE_SETTING`
    恰好就定义在 jello_locale.py 里，跟着 collect_sources() 过滤会采不到。
    """
    literal_re = re.compile(r'^([A-Z][A-Z0-9_]{2,})\s*=\s*"([^"]+)"\s*$')
    out: dict[str, set[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            m = literal_re.match(line.strip())
            if m:
                out.setdefault(m.group(2), set()).add(m.group(1))
    return {value: tuple(names) for value, names in out.items()}


def find_reads(
    name: str,
    sources: list[tuple[Path, list[str]]],
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, str]]:
    """找出该设置名的所有**非定义**出现位置（含经由全大写常量的引用）。"""
    quoted = re.compile(f'"{re.escape(name)}"')
    const_res = [
        re.compile(rf"(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])")
        for c in (aliases or {}).get(name, ())
        if c != name
    ]
    hits: list[dict[str, str]] = []
    for path, lines in sources:
        for i, line in enumerate(lines, 1):
            matched = bool(quoted.search(line))
            if not matched and const_res:
                stripped = line.strip()
                # import 行不算读取：`from x import LANGUAGE_SETTING` 只证明
                # 这个名字被引入了，不证明有人拿它去读设置。把它当证据会制造
                # 假 READ，而假 READ 比假孤儿更糟：它会把真问题藏起来。
                is_import = stripped.startswith(("import ", "from "))
                # 常量自己的定义行不算读取：那只是在说"这个常量叫什么名字"。
                is_const_def = bool(re.match(r"^[A-Z][A-Z0-9_]{2,}\s*=", stripped))
                if not is_import and not is_const_def:
                    matched = any(r.search(line) for r in const_res)
            if not matched:
                continue
            # 这一行是不是定义行？定义行的特征是同一行（或紧邻上一行）里
            # 出现了某个 *_setting( 调用。
            window = "\n".join(lines[max(0, i - 3):i])
            if _DEFINITION_RE.search(window) and _is_definition_of(window, name):
                continue
            hits.append({
                "file": str(path.relative_to(ROOT)).replace(os.sep, "/"),
                "line": str(i),
                "text": line.strip()[:130],
            })
    return hits


def _is_definition_of(window: str, name: str) -> bool:
    """判断这段窗口里的 *_setting(...) 定义的是不是就是这个 name。"""
    for m in _DEFINITION_RE.finditer(window):
        if m.group(1) == name:
            return True
    return False


#: `module = self.registry.get("EconomyPanel")` 这类绑定。变量名 + 模块名字面量
#: 一起拿到，才能把 `setting_bool(module, "Gold")` 归回 EconomyPanel。
_MODULE_BIND_RE = re.compile(
    r'\b([a-z_][a-z0-9_]*)\s*=\s*[\w.]*\.?get(?:_module)?\(\s*"([A-Za-z][A-Za-z0-9_]*)"'
)

#: (函数名, 形参位置) -> 调用点传进来的模块名集合。全仓扫描不便宜，必须缓存。
_CALLER_MODULE_CACHE: dict[tuple[str, int], set[str]] = {}


def _split_args(text: str) -> list[str]:
    """按**顶层逗号**切参数列表（括号/引号内的逗号不算）。"""
    out: list[str] = []
    depth = 0
    quote = ""
    buf = ""
    for ch in text:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _balanced_chunk(lines: list[str], start: int, cap: int = 12) -> str:
    """从 start 行拼到括号平衡，拿到完整调用表达式（调用经常跨行写）。"""
    text = ""
    depth = 0
    for j in range(start, min(len(lines), start + cap)):
        text += (" " if text else "") + lines[j].strip()
        depth += lines[j].count("(") - lines[j].count(")")
        if depth <= 0 and j > start:
            break
    return text


def _enclosing_defs(lines: list[str], idx: int, cap: int = 200) -> list[tuple[int, str, list[str]]]:
    """往上收集**候选**外层 `def`（最近的在前，包含局部函数与逐层外函数）。

    为什么是多候选而不是单一答案：靠缩进算“函数体到哪里结束”很脆（多行
    字符串/退进的续行都能把边界算错），算错就会把本来能归上的弄得归不上。
    所以先试最近那个，不行再试外层——这样只会多归不会漏归。局部函数
    （闭包）也是一行 `def`，`hero_lob.py` 的读取就写在 `def setting` 后面。
    """
    out: list[tuple[int, str, list[str]]] = []
    last_indent: int | None = None
    for j in range(idx, max(-1, idx - cap), -1):
        stripped = lines[j].strip()
        if not stripped.startswith("def "):
            continue
        indent = len(lines[j]) - len(lines[j].lstrip())
        # 只接受“逐层往外”的候选：同级 def 是**另一个函数**，它的形参
        # 与本行无关，拿它的调用点当证据就是误归。
        if last_indent is not None and indent >= last_indent:
            continue
        sig = stripped
        k = j
        while not sig.rstrip().endswith(":") and k + 1 < len(lines):
            k += 1
            sig += " " + lines[k].strip()
        m = re.match(r"def\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->.*)?\s*:$", sig)
        if not m:
            continue
        params = [p.split(":")[0].split("=")[0].strip() for p in _split_args(m.group(2))]
        params = [p for p in params if p and p != "*"]
        out.append((j, m.group(1), params))
        last_indent = indent
        if len(out) >= 5:
            break
    return out


def _caller_modules(fname: str, pos: int, index: dict[str, list[str]]) -> set[str]:
    """看 `fname()` 的所有调用点，第 pos 个实参（不含 self/cls）上传的是哪些模块。

    只认两种写法，宁缺勿滥：调用行里直接 `get("X")`，或调用所在函数体内
    有 `v = ...get("X")` 且传的就是 `v`。

    要缓存：一个 (函数名, 位置) 全仓扫一遍并不便宜，而不同设置项会反复
    碰到同一个函数（例如多处都经 `draw_target_overlay` 传 module）。
    """
    key = (fname, pos)
    cached = _CALLER_MODULE_CACHE.get(key)
    if cached is not None:
        return cached
    call_re = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(fname)}\s*\(")
    lit_re = re.compile(r'\.?get(?:_module)?\(\s*"([A-Za-z][A-Za-z0-9_]*)"')
    found: set[str] = set()
    for rel, lines in index.items():
        for i, line in enumerate(lines):
            m = call_re.search(line)
            if not m or line.strip().startswith("def "):
                continue
            chunk = _balanced_chunk(lines, i)
            named = chunk.find(fname + "(")
            open_at = chunk.find("(", named if named >= 0 else 0)
            inside = chunk[open_at + 1:]
            # 找顶层闭括号：不能用“删掉最后一个字符”凑，结尾带尾注/逗号就切错
            depth = 0
            end = len(inside)
            for ch_index, ch in enumerate(inside):
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    if depth == 0:
                        end = ch_index
                        break
                    depth -= 1
            args = _split_args(inside[:end]) if end else []
            if pos >= len(args):
                continue
            arg = args[pos].split("#")[0].strip()
            for lit in lit_re.findall(arg):
                found.add(lit)
            ident = re.fullmatch(r"[A-Za-z_]\w*", arg)
            if ident:
                enc = _enclosing_defs(lines, i - 1)
                for cand in enc[:1]:
                    body = "\n".join(lines[cand[0] + 1:i + 1])
                    for var, mod in _MODULE_BIND_RE.findall(body):
                        if var == arg:
                            found.add(mod)
    _CALLER_MODULE_CACHE[key] = found
    return found


def _used_params(text: str, params: list[str], fn_body: str) -> list[str]:
    """本行用到了哪些形参。**直接出现**与**经闭包捕获**的两路全部合并**返回。

    不能“命中一个就直接返”：`float(setting("Distance", getattr(state, ...)))`
    这一行既直接用了形参 `state`，又经闭包 `setting()` 用了 `module`。
    先命中 `state` 就返回，会去查调用点第 0 个实参（传的是 state对象，
    永远归不上），反而把本来能归属的填目弄丢了。
    """
    usable = [p for p in params if p not in ("self", "cls")]
    used = [p for p in usable if re.search(rf"(?<![A-Za-z0-9_]){re.escape(p)}\b", text)]
    for m in re.finditer(r"\b([a-z_]\w*)\s*\(", text):
        inner = re.search(
            rf"def\s+{re.escape(m.group(1))}\s*\([^)]*\)[^:]*:([\s\S]*?)(?=\n\s*def\s|\Z)",
            fn_body,
        )
        if not inner:
            continue
        for p in usable:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(p)}\b", inner.group(1)) and p not in used:
                used.append(p)
    return used


#: 同一函数里把某个模块的全部设置项做成字典的写法：
#: `settings = {s.name: s for s in module.settings}` 然后 `settings["Baud"]` 读。
#: 读取行里根本不出现 `module`，所以证据 2 看不到它。
_SETTINGS_MAP_RE = re.compile(
    r"\b([a-z_][\w]*)\s*=\s*\{[^}]*\bfor\s+\w+\s+in\s+([a-z_][\w]*)\.(?:settings|get_settings)"
)
#: `self.xxx_bool(...)` 这种自写谓词：真正绑模块的是谓词函数体内部。
_HELPER_CALL_RE = re.compile(r"(?:self\.)?\b([a-z_]\w*)\s*\(")


def _build_function_defs(index: dict[str, list[str]]) -> dict[str, tuple[int, str, list[str], str]]:
    """建立 函数名 -> (起始行, 文件, 形参, 函数体)。同名取第一个，只供谓词追踪用。"""
    out: dict[str, tuple[int, str, list[str], str]] = {}
    for rel, lines in index.items():
        for i, line in enumerate(lines):
            if not line.strip().startswith("def "):
                continue
            cands = _enclosing_defs(lines, i)
            if not cands or cands[0][0] != i:
                continue
            _, fname, params = cands[0]
            if fname in out:
                continue
            end = min(len(lines), _def_extent(lines, i))
            out[fname] = (i, rel, params, "\n".join(lines[i:end]))
    return out


def _def_extent(lines: list[str], def_i: int) -> int:
    """函数体结束行（不含）。用缩进算，只供取函数体文本用，算错不致命。"""
    indent = len(lines[def_i]) - len(lines[def_i].lstrip())
    last = def_i
    for j in range(def_i + 1, len(lines)):
        raw = lines[j]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if (len(raw) - len(raw.lstrip())) <= indent:
            break
        last = j
    return last + 1


_FUNCS_CACHE: dict[int, dict[str, tuple[int, str, list[str], str]]] = {}


def _funcs(index: dict[str, list[str]]) -> dict[str, tuple[int, str, list[str], str]]:
    """函数表（按本次扫描的 index 缓存，不反复重扫）。"""
    key = id(index)
    if key not in _FUNCS_CACHE:
        _FUNCS_CACHE.clear()
        _FUNCS_CACHE[key] = _build_function_defs(index)
    return _FUNCS_CACHE[key]


def _attribute_via_settings_map(mod_name: str, text: str, fn_body: str, bound: dict[str, str]) -> str:
    """证据 4：同一函数里把本模块的设置项做成了字典，读取行用的是那个字典。

    不补这一条，`manager.py` 的 `settings["Mock Writes"]` 这种写法永远
    归不上——读取行里只有字典名，没有模块变量名。而它恰恰是区分
    “接了”与“没接”的关键：RobotLink 的取用清单里没有 Port/Baud，
    归不上就是真没人读，不是工具没看见。

    认两种字典构造：`{s.name: s for s in module.settings}` 与
    `{name: module.get_setting(name) for name in (...)}`。
    """
    for dict_var, module_var in _SETTINGS_MAP_RE.findall(fn_body):
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(dict_var)}\b", text):
            continue
        if bound.get(module_var) == mod_name:
            return f"字典 {dict_var} 由本模块的 settings 构造"
    # 第二种形式：字典体逐行写，靠 `.get_setting(` 找模块变量。
    lines = fn_body.splitlines()
    i = 0
    while i < len(lines):
        head = re.match(r"\s*([a-z_][\w]*)\s*=\s*\{\s*$", lines[i])
        if not head:
            i += 1
            continue
        dict_var = head.group(1)
        j = i + 1
        block: list[str] = []
        while j < len(lines) and not re.match(r"\s*\}\S*\s*(?:#.*)?$", lines[j]):
            block.append(lines[j])
            j += 1
            if j - i > 60:
                break
        i = j + 1
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(dict_var)}\b", text):
            continue
        for module_var in re.findall(r"\b([a-z_][\w]*)\.get_setting\s*\(", "\n".join(block)):
            if bound.get(module_var) == mod_name:
                return f"字典 {dict_var} 由本模块的 get_setting 构造"
    return ""


def _attribute_via_helper(mod_name: str, text: str, index: dict[str, list[str]]) -> str:
    """证据 5：读取走的是自写谓词（如 `liquid_map_bool("Show Labels", True)`），
    谓词函数体里才绑的模块。这在本仓很常见（每个模块有自己的专属谓词）。"""
    for m in _HELPER_CALL_RE.finditer(text):
        entry = _funcs(index).get(m.group(1))
        if not entry:
            continue
        body = entry[3]
        for lit in re.findall(r'\.?get(?:_module)?\(\s*"([A-Za-z][A-Za-z0-9_]*)"', body):
            if lit == mod_name:
                return f"经谓词 {m.group(1)}() 内部绑定本模块"
    return ""


def attribute_read(
    mod_name: str,
    hit: dict[str, str],
    index: dict[str, list[str]],
    lookback: int = 140,
    call_site: bool = True,
) -> str:
    """把一条读取点归属到具体模块；归不上就返回 ""。

    为什么需要这一层：AMBIGUOUS 那 51 项只证明了“这个名字有人读”，
    没证明“本模块自己的那份有人读”。上一轮就是这么被 `EnergyAutoAim/Fov`
    骗过一次（ArmorAutoAim 的读取点把它洗成了 READ）。

    三条证据，从强到弱：
      1) 同一行既有 `"<模块名>"` 字面量又有设置名——显式配对，无歧义；
      2) 同一函数体内有 `x = registry.get("<模块名>")`，且本行用到了 `x`；
      3) 本行用的是**函数形参**（或捕获了形参的闭包），再去**调用点**看
         传进来的是哪个模块。没这一条时，`hero_lob.py` 的
         `setting("Heat Guard", 0.88)` 与 `Lock Style` 只能停在“无法归属”。

    只在**当前函数体内**回溯（越过最近的 `def` 就停），否则会拿着上一个
    函数的绑定误归——那比不归更坑。
    """
    text = hit.get("text", "")
    if re.search(rf'"{re.escape(mod_name)}"', text):
        return "同行带模块名字面量"
    try:
        line_no = int(hit.get("line") or 0)
    except ValueError:
        return ""
    lines = index.get(hit.get("file", "")) or []
    if line_no <= 0 or line_no > len(lines):
        return ""
    bottom = line_no - 1                      # 读取行的上一行（0-based）
    top = max(0, bottom - lookback)
    for j in range(bottom, top - 1, -1):
        if lines[j].lstrip().startswith("def "):
            top = j + 1
            break
    bound: dict[str, str] = {}
    for j in range(top, bottom + 1):
        for var, mod in _MODULE_BIND_RE.findall(lines[j]):
            bound[var] = mod
    for var, mod in bound.items():
        if mod != mod_name:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(var)}\b", text):
            return f"变量 {var} 绑定自本模块"
    fn_body = "\n".join(lines[top:bottom + 1])
    via_map = _attribute_via_settings_map(mod_name, text, fn_body, bound)
    if via_map:
        return via_map
    via_helper = _attribute_via_helper(mod_name, text, index)
    if via_helper:
        return via_helper
    if not call_site:
        return ""
    # 证据 3：本行读的是形参 -> 去调用点找传的是哪个模块。
    # 逐层候选函数都试（闭包/多行体边界靠缩进算不准）。
    for def_i, fname, params in _enclosing_defs(lines, bottom):
        base = params[1:] if params and params[0] in ("self", "cls") else params
        body = "\n".join(lines[def_i + 1:bottom + 1])
        for p in _used_params(text, params, body):
            if p not in base:
                continue
            if mod_name in _caller_modules(fname, base.index(p), index):
                return f"经 {fname}() 形参 {p} 传入（调用点绑定）"
    return ""


def attribution_report(
    rows: list[dict[str, object]],
    index: dict[str, list[str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """按已算好的 `attribution` 字段分组，返回 (已归上, 仍无法归属)。

    归属是在主循环里用**全量**读取点算的（行里只存前 6 条供打印，
    拿它算归属会偷漏证据）。`index` 参数仅为兼容旧调用保留。
    """
    own = [r for r in rows if r["shared_by"] > 1 and r.get("attribution")]
    unresolved = [
        r for r in rows
        if r["shared_by"] > 1 and r["tier"] in ("AMBIGUOUS", "READ") and not r.get("attribution")
    ]
    return own, unresolved


def kind_of(setting: object) -> str:
    kind = str(getattr(setting, "kind", "") or "")
    if kind:
        return kind
    # 兜底：从取值类型猜
    value = getattr(setting, "value", None)
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "other"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", default=None, help="只看这一个模块")
    ap.add_argument("--all", action="store_true", help="列出全部设置项，不只 ORPHAN")
    ap.add_argument("--json", type=Path, default=None, help="把结果写成 JSON")
    ap.add_argument("--max-orphans", type=int, default=None,
                    help="孤儿数超过这个值就返回 1（防倒退闸门，供 verify_all 调用）")
    ap.add_argument("--attribute", action="store_true",
                    help="把同名共用项按模块归属，列出仍无法归属的那些")
    args = ap.parse_args(argv)

    from rm_client.model import build_default_registry

    registry = build_default_registry()
    modules = registry.modules
    items = (list(modules.values()) if hasattr(modules, "values") else list(modules))

    sources = collect_sources()
    templates = collect_dynamic_templates(sources)
    aliases = build_const_aliases()
    action_routes = {}
    for path, lines in sources:
        if path == ROOT / DISPATCH_SOURCE:
            action_routes = dynamic_action_routes("\n".join(lines))
            break
    index = {
        str(path.relative_to(ROOT)).replace(os.sep, "/"): lines
        for path, lines in sources
    }

    # 先统计每个设置名被几个模块共用：共用的名字，读取点无法归属，
    # 必须显式标出来，否则会把"另一个模块在读"误当成"这个模块对齐了"。
    name_owners: Counter[str] = Counter()
    all_settings: list[tuple[str, str, object]] = []
    for mod in items:
        mod_name = str(getattr(mod, "name", "?"))
        settings = getattr(mod, "settings", None) or {}
        seq = settings.values() if hasattr(settings, "values") else list(settings)
        for st in seq:
            nm = str(getattr(st, "name", ""))
            if not nm:
                continue
            name_owners[nm] += 1
            all_settings.append((mod_name, nm, st))

    rows: list[dict[str, object]] = []
    for mod_name, nm, st in all_settings:
        if args.module and mod_name != args.module:
            continue
        reads = find_reads(nm, sources, aliases)
        shared = name_owners[nm]
        kind = kind_of(st)
        options = list(getattr(st, "options", []) or [])
        # 按钮类（action / action_group）派发的是**选项名**，不是设置名。
        # 按设置名搜永远搜不到，会把 MatchReadiness 的 22 个资格赛动作
        # 全误报成孤儿。改成查选项有没有被派发。
        if kind in ("action", "action_group") and options:
            dispatched = [o for o in options if find_reads(str(o), sources)]
            dyn = [evidence for option in options
                   for evidence in dynamic_action_evidence(str(option), action_routes)]
            if dispatched:
                tier = "READ"
                reads = [{"file": "-", "line": "-",
                          "text": f"{len(dispatched)}/{len(options)} 个选项有派发点"}]
            elif dyn:
                tier = "DYNAMIC"
                reads = []
            else:
                tier = "ORPHAN"
                reads = []
        else:
            dyn = [] if reads else match_dynamic(nm, templates)
            if kind == "action" and not reads:
                dyn.extend(dynamic_action_evidence(nm, action_routes))
            if reads:
                tier = "READ"
            elif dyn:
                tier = "DYNAMIC"
            else:
                tier = "ORPHAN"
            # 同名且“有读取点”：读取点可能属于另一个模块，不能算已对齐。
            # 这不是理论风险：接完 ArmorAutoAim/Fov 后，同样叫 Fov 的
            # EnergyAutoAim/Fov 立刻被扫成 READ——事实上它仍然没人读。
            # 所以这类单独成档，必须人工看，也不计进“已修好”的进度。
            if tier == "READ" and shared > 1:
                tier = "AMBIGUOUS"
        # 归属：同名的读取点到底属于哪个模块。必须在主循环里用**全量**
        # reads 算（行里只存前 6 条供打印）。
        attribution = ""
        if shared > 1 and tier in ("AMBIGUOUS", "READ"):
            for hit in reads:
                if hit.get("file") == "-":
                    attribution = "按钮类：按选项名派发"
                    break
                attribution = attribute_read(mod_name, hit, index)
                if attribution:
                    break
        rows.append({
            "module": mod_name,
            "setting": nm,
            "kind": kind,
            "value": getattr(st, "value", None),
            "options": options,
            "unit": str(getattr(st, "unit", "") or ""),
            "shared_by": shared,
            "tier": tier,
            "reads": reads[:6],
            "read_count": len(reads),
            "dynamic": dyn,
            "attribution": attribution,
        })

    orphans = [r for r in rows if r["tier"] == "ORPHAN"]
    dynamic = [r for r in rows if r["tier"] == "DYNAMIC"]
    ambiguous = [r for r in rows if r["shared_by"] > 1]
    unclear = [r for r in rows if r["tier"] == "AMBIGUOUS"]

    index_for_attr = index          # 主循环已建好，这里直接用
    attributed, unattributed = attribution_report(rows, index_for_attr)

    print(f"扫描源文件：{len(sources)}    设置项：{len(rows)}")
    print(f"  READ       有读取点且名字不冲突    ：{len(rows) - len(orphans) - len(dynamic) - len(unclear)}")
    print(f"  AMBIGUOUS  有读取点但同名多模块    ：{len(unclear)}  （不能算已对齐，需人工归属）")
    print(f"  DYNAMIC    动态拼名/命令消费候选   ：{len(dynamic)}")
    print(f"  ORPHAN     没有任何读取点（真装饰）：{len(orphans)}")
    print()

    show = rows if args.all else (orphans + dynamic + unclear)
    current = None
    for r in show:
        if r["module"] != current:
            current = r["module"]
            print(f"\n### {current}")
        flag = "  [名字共用x%d]" % r["shared_by"] if r["shared_by"] > 1 else ""
        extra = ""
        if r["kind"] == "number":
            extra = f"  当前={r['value']} {r['unit']}".rstrip()
        elif r["options"]:
            extra = f"  选项={r['options']} 当前={r['value']}"
        else:
            extra = f"  当前={r['value']}"
        print(f"  [{r['tier']:7}] {r['setting']}{extra}{flag}")
        for h in r["reads"][:3]:
            print(f"            {h['file']}:{h['line']}  {h['text'][:76]}")
        for h in r.get("dynamic", [])[:2]:
            print(f"            动态证据 {h[:160]}")

    print(
        f"\n同名共用项归属：共 {len(attributed) + len(unattributed)} 项，"
        f"已能归到本模块 {len(attributed)} 项，仍无法归属 {len(unattributed)} 项"
    )
    if unattributed:
        print("  以下项目前只能证明“这个名字有人读”，不能证明本模块读了自己那份：")
        for r in unattributed:
            print(f"    ?  {r['module']} / {r['setting']}"
                  f"（同名共 {r['shared_by']} 处，{r['read_count']} 个读取点无一可归）")
    if args.attribute:
        for r in attributed:
            print(f"  [已归属] {r['module']} / {r['setting']}  <- {r['attribution']}")

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\n[OK] 已写出 {args.json}")

    print("\n" + "=" * 78)
    print(
        "如何读这份输出（重要）：\n"
        "  ORPHAN 只证明\"没人按这个名字读它\"。有三种可能，必须人工区分：\n"
        "    a) 真的是装饰 —— 该删掉，或者该接上；\n"
        "    b) 通过别的方式读 —— 例如遍历所有设置项做持久化/序列化，\n"
        "       那不是功能读取，仍算装饰；\n"
        "    c) 名字被别的模块共用 —— 看 [名字共用xN] 标记。\n"
        "  反过来，READ 也**不等于对齐**：值可能被读了但兑现不了。\n"
        "  典型例子是 SpinMode 的 \"Spin Speed\"(420 dps) 与 \"Direction\"(CW/CCW)：\n"
        "  固件的自转只是一个开关标志，转速与方向由固件自己的 SPIN 模式决定，\n"
        "  这两个滑块读了也改不了机器人行为。这类要对照\n"
        "  tools/firmware/host_bridge/GAPS.md 人工确认。\n"
    )

    # 防倒退闸门：孤儿只许减不许增。
    # 新加一个设置项而忘了接线，或者把已有接线改断了，verify_all 就会红。
    if args.max_orphans is not None:
        if len(orphans) > args.max_orphans:
            print(
                f"[FAIL] 孤儿设置项 {len(orphans)} 个，超过基线 {args.max_orphans} 个。\n"
                "       新增了设置项但没接线，或把已有接线改断了。\n"
                "       要么接上，要么确认应该删除后把基线调低。\n"
                "       当前孤儿清单见 tools/SETTINGS_GAPS.md。"
            )
            return 1
        print(f"[OK] 孤儿设置项 {len(orphans)} 个，未超过基线 {args.max_orphans}")
        # 基线只卡 ORPHAN。AMBIGUOUS 不计入也不计减：同名设置项的读取点
        # 无法归属，把它当已修好会虚报进度（实测发生过）。
        if unclear:
            print(
                f"提醒：另有 {len(unclear)} 个设置项名字与其他模块冲突，读取点无法归属，\n"
                "      它们**不算已对齐**，需要人工确认各自模块真的读了自己的值：\n"
                + "\n".join(
                    f"        {r['module']} / {r['setting']}（同名共 {r['shared_by']} 处）"
                    for r in unclear
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
