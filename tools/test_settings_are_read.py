#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RobotLink 的每个设置项都必须"真的有人读"——这条是被两次同类事故逼出来的：
   `DT7 Real TX` 与 `DT7 Allow Fire Keys` 都因为没进 manager 那份 settings 白名单，
   界面上能勾、后端一行都不动。审计工具当时判它"已接线"，因为名字在源码里出现过。
   所以这里查的不是"出现过"，而是"被 settings.get(...) 或白名单读到"。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    print(f"{'[ OK ]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    FAILED += 0 if ok else 1


def robotlink_settings(model_src: str) -> list:
    """只取 RobotLink 那个 Module( 块里的设置项名。

    先按 8 空格缩进的 `Module(` 切块，再挑名字是 RobotLink 的那一块。
    上一版用"从名字位置找到下一个 Module("当边界，结果整个 registry 被当成一块，
    报出 103 项没人读——那种红是审计自己坏了，不是被测代码坏了。
    """
    lines = model_src.split("\n")
    starts = [n for n, l in enumerate(lines) if re.match(r"^\s{8}Module\($", l)]
    for n in starts:
        named = lines[n + 1] if n + 1 < len(lines) else ""
        if '"RobotLink"' not in named:
            continue
        nxt = next((m for m in starts if m > n), len(lines))
        block = "\n".join(lines[n:nxt])
        return re.findall(
            r"(?:bool_setting|number_setting|mode_setting|action_setting)\(\s*" + chr(34) +
            r"([^" + chr(34) + r"]+)" + chr(34), block)
    raise AssertionError("声明式目录里找不到 RobotLink 模块块")


def _module_settings(model_src: str) -> dict:
    """每个模块声明了哪些设置项（按 8 空格缩进的 Module( 切块）。"""
    lines = model_src.split("\n")
    starts = [n for n, l in enumerate(lines) if re.match(r"^\s{8}Module\($", l)]
    out: dict = {}
    Q = chr(34)
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        named = lines[a + 1] if a + 1 < len(lines) else ""
        m = re.search(Q + r"([^" + Q + r"]+)" + Q, named)
        if not m:
            continue
        block = "\n".join(lines[a:b])
        out[m.group(1)] = re.findall(
            r"(?:bool_setting|number_setting|mode_setting|action_setting)\(\s*" + Q +
            r"([^" + Q + r"]+)" + Q, block)
    return out


# 这些是"按设计只在界面/其它层用"的模块项；每条都要能解释，新增必须写理由。
KNOWN_UI_ONLY = {}


def functions_mentioning(src: str, module_name: str) -> list:
    """把源码按 `def ...` 切块，返回提到了某个模块名的那些函数体。

    为什么要按函数分块：设置项名是**按模块**才有意义的。MQTTLink 也有 `Port`，
    全局搜字符串会让 RobotLink 的 `Port` 蹭到别人的读取记录上（假绿）。
    """
    parts = re.split(r"(?m)^(?=def |    def |class )", src)
    return [p for p in parts if module_name in p]


def read_names_in(scopes: list) -> set:
    Q = chr(34)
    names = set()
    for src in scopes:
        names |= set(re.findall(r"settings\.get\(\s*" + Q + r"([^" + Q + r"]+)" + Q, src))
        names |= set(re.findall(r"get_setting\(\s*" + Q + r"([^" + Q + r"]+)" + Q, src))
        names |= set(re.findall(r"\.name ==\s*" + Q + r"([^" + Q + r"]+)" + Q, src))
        names |= set(re.findall(r"action_name ==\s*" + Q + r"([^" + Q + r"]+)" + Q, src))
        k = src.find("for name in (")
        if k >= 0:
            end = src.find(")", k)
            names |= set(re.findall(Q + r"([^" + Q + r"]+)" + Q, src[k:end]))
    return names


def module_helper_reads(src: str, module_name: str) -> set[str]:
    """Recognize real setting-helper calls tied to a registry module.

    Mouse input uses get_setting_number/setting_bool rather than settings.get.
    Associate their first argument with registry.get("RobotLink") in the same
    function; merely mentioning the module or setting in a comment, UI label,
    or another module's helper call is not wiring evidence. This deliberately
    handles unambiguous local aliases, not arbitrary interprocedural data flow.
    """
    tree = ast.parse(src.lstrip("\ufeff"))

    def module_lookup(node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "registry"
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            return node.args[0].value
        return None

    def local_nodes(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            yield child
            yield from local_nodes(child)

    names = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(local_nodes(function))
        aliases = {}
        for node in nodes:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets, value = [node.target], node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases.setdefault(target.id, set()).add(module_lookup(value))
        for node in nodes:
            if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute)
                    or node.func.attr not in {"get_setting_number", "setting_bool", "setting_mode"}
                    or len(node.args) < 2):
                continue
            module, setting = node.args[:2]
            matches = (aliases.get(module.id) == {module_name} if isinstance(module, ast.Name)
                       else module_lookup(module) == module_name)
            if matches and isinstance(setting, ast.Constant) and isinstance(setting.value, str):
                names.add(setting.value)
    return names


# 刻意只在界面/其它模块用的项（每条都要写理由，不然就是下一个漏网点）
UI_ONLY = {
    # 每条都要写理由；空着最严格，但也最容易让人直接删掉这条测试
    "Run DT7 Link Selftest": "按钮：由 ui 的 action_name 分派，见 config_actions_mixin",
    "Run Wireless KM Selftest": "按钮：同上",
    # 按钮类设置项天生不在 manager 的白名单里读，它们由 ui 按 action_name 分派；
    # 登记进白名单必须同时指出分派点与实现点，否则白名单就是下一个漏网点。
    "Rescan Serial Ports": "按钮：config_actions_mixin.handle_config_action 分派到 "
                           "rescan_serial_ports，实现是 serial_ports.refresh_port_options，"
                           "行为由 tools/test_port_picking.py 第 5 节测",
}


def main() -> int:
    catalog = (ROOT / "rm_client/module_catalog.py").read_text(encoding="utf-8")
    declared = robotlink_settings(catalog)
    files = [p for p in (ROOT / "rm_client").rglob("*.py")
             if p.name not in {"model.py", "module_catalog.py"}]
    scopes = []
    helper_names = set()
    for p in files:
        src = p.read_text(encoding="utf-8", errors="ignore")
        scopes += functions_mentioning(src, "RobotLink")
        helper_names |= module_helper_reads(src, "RobotLink")
    names = read_names_in(scopes) | helper_names
    check("抓到 RobotLink 的设置项（数量不对说明解析坏了）", 5 < len(declared) < 40,
          str(len(declared)))
    check("确实找到了读到 RobotLink 设置的代码块（一块都没有说明解析坏了）",
          len(scopes) >= 2, f"{len(scopes)} 块")
    missing = [n for n in declared if n not in names and n not in UI_ONLY]
    check("每个 RobotLink 设置项都在提到 RobotLink 的代码里被取用（或登记为只在界面用）",
          not missing, f"没人读：{missing}")
    stale = [n for n in UI_ONLY if n not in declared]
    check("UI_ONLY 白名单里不留过期项（过期就会盖住新的漏网点）", not stale, str(stale))
    # 光查字符串只能证明"被取用"，不能证明"取到之后真的落到传输层"——
    # Port/Baud 这两个曾经连字符串都蹭在 MQTTLink 上，所以这里加行为断言。
    import tempfile
    from rm_client.backend.manager import BackendManager
    from rm_client.model import RobotState, build_default_registry
    manager = BackendManager(workspace=tempfile.mkdtemp(prefix="set_read_"),
                             scenario_key="OfficialEvent", client_id="3")
    manager.polar.request_start = lambda: None  # configuration-only test, never open hardware
    registry = build_default_registry()
    state = RobotState()
    manager.state = state
    manager._apply_config(manager.config)
    module = registry.get("RobotLink")
    manager.tick_polar_link(state, registry, None)
    base_port, base_baud = str(manager.polar.port), int(manager.polar.baud)
    module.get_setting("Port").value = "COM99"
    module.get_setting("Baud").value = 57600
    manager.tick_polar_link(state, registry, None)
    check("界面改 Port 真的落到传输层", manager.polar.port == "COM99",
          f"{base_port} -> {manager.polar.port}")
    check("界面改 Baud 真的落到传输层", int(manager.polar.baud) == 57600,
          f"{base_baud} -> {manager.polar.baud}")
    module.get_setting("Baud").value = "乱码"
    manager.tick_polar_link(state, registry, None)
    check("非法波特率不许把传输层改坏", int(manager.polar.baud) == 57600,
          str(manager.polar.baud))
    module.get_setting("Port").value = ""
    manager.tick_polar_link(state, registry, None)
    check("清空 Port 不许悄悄改成一个猜的口", manager.polar.port == "COM99",
          str(manager.polar.port))
    # 谁赢：默认值 = 场景说话；操作员显式改过 = 模块赢
    module.get_setting("Port").value = "Auto"
    manager.polar.port = "COM5"
    manager.tick_polar_link(state, registry, None)
    check("模块保持默认 Auto 时不许抢走场景配的 COM5（否则官方场景会突然自动找串口）",
          manager.polar.port == "COM5", str(manager.polar.port))
    module.get_setting("Port").value = "COM7"
    manager.tick_polar_link(state, registry, None)
    check("操作员显式选了 COM7 就必须是 COM7（选口不再无效）",
          manager.polar.port == "COM7", str(manager.polar.port))
    module.get_setting("Baud").value = 115200
    manager.polar.baud = 230400
    manager.tick_polar_link(state, registry, None)
    check("Baud 同理：默认值不覆盖场景/桥接配置", int(manager.polar.baud) == 230400,
          str(manager.polar.baud))

    # ---- Tier B（保守、零误报）：设置名除了目录声明外，整个包一次都没出现 ----
    # 上一版把 Tier A 的判据（"提到模块名的函数里取用"）套到所有模块，结果把
    # 通过参数取用的 Limit / Fade 之类全报成没人读 —— 那种噪声会让下一个人直接删掉这条测试。
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in files]
    blob = "\n".join(texts)
    all_declared = _module_settings(catalog)
    dead = sorted({(m, n) for m, names in all_declared.items() for n in names
                   if n and n not in blob})
    print(f"     （Tier B 仅供参考：整包零引用的设置 {len(dead)} 项 —— "
          f"判据太粗，不当红色闸门：{str(dead)[:150]}）")
    # Tier A 只对"走 manager 白名单那套写法"的模块严格（目前就是 RobotLink）：
    # 别的模块的取法太多样，用一条判据硬套只会产噪声。
    print(f"     （Tier B 扫了 {len(all_declared)} 个模块、"
          f"{sum(len(v) for v in all_declared.values())} 个设置项）")

    print("\n" + ("有失败项" if FAILED else "设置项接线审计通过"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

