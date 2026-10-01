"""机器人 USB 串口的发现与解析。

放在顶层模块而不是 `backend/` 下，是为了让 `model.py`（ClickGUI 设置项）和
`backend/polar_transport.py`（真正打开端口）都能引用它而不形成循环导入
（`backend/manager.py` 已经依赖 `..model`）。

实车上的关键约束：StandardRobot++ 的 USB CDC 会被 Windows 枚举成任意
COM 号（COM7、COM12 都可能），因此选项列表不能写死，必须现场探测；同时要
支持 "Auto"：车上常常只插了一个 USB 设备，自动挑唯一候选比让人去设备管理器
查口号可靠得多。
"""

from __future__ import annotations

import re

# StandardRobot++ / STM32 常见 USB 虚拟串口身份。命中即优先选中。
PREFERRED_USB_IDS: tuple[tuple[int, int], ...] = (
    (0x0483, 0x5720),  # STMicroelectronics Virtual COM Port
    (0x16C0, 0x05DC),  # 通用 Arduino/DFRobot 风格 VID:PID（部分 C 板 bootloader）
    (0x1EAF, 0x0003),  # Atmel/LPCUSBM 常见 CDC
)
PREFERRED_HINTS = ("stm32", "virtual com", "usb serial", "polar", "c-board", "c板")
# 明显不是机器人的内置设备
REJECT_HINTS = ("intel", "contee", "bluetooth", "raspblu", "irda", "standard uart")
# 官方驱动 INF：USB\\VID_FFF0&PID_0008 -> usbser.sys，描述 "DJI USB Virtual COM"。
# 一条事实两处用：A 路要避开它，DT7 出口只认它。
DJI_RC_USB_IDS: tuple[tuple[int, int], ...] = ((0xFFF0, 0x0008),)

AUTO_PORT = "Auto"
STUB_PORT = "Stub"
# Auto 最低入选分：必须是带 VID/PID 的 USB 设备
AUTO_MIN_SCORE = 30


def list_serial_ports() -> list[dict[str, object]]:
    """返回系统可用串口；没装 pyserial 或没插设备时返回空列表，绝不抛异常。"""

    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return []
    ports: list[dict[str, object]] = []
    try:
        items = list(list_ports.comports())
    except Exception:
        return []
    for item in items:
        ports.append(
            {
                "device": str(item.device),
                "name": str(item.device).split("\\")[-1],
                "description": str(getattr(item, "description", "") or ""),
                "vid": int(getattr(item, "vid", 0) or 0),
                "pid": int(getattr(item, "pid", 0) or 0),
                "serial_number": str(getattr(item, "serial_number", "") or ""),
            }
        )
    ports.sort(key=lambda entry: _com_number(str(entry["device"])))
    return ports


def _com_number(device: str) -> int:
    match = re.search(r"COM(\d+)$", device.upper())
    return int(match.group(1)) if match else 1 << 30


def score_port(entry: dict[str, object]) -> int:
    """分值越高越像机器人本体，供 Auto 挑选。

    **只有带 VID/PID 的 USB 设备才有资格被 Auto 选中。**主板自带的
    传统 COM1/COM2 在中文 Windows 上描述是“通信端口”，不包含任何可识别
    的英文关键字，如果允许它入选，Auto 会优先挑中这个假目标，车上表现为
    “连上了但永远收不到数据”。宁缺勿滥，找不到就返回 Stub 让操作员显式选。
    """

    vid = int(entry.get("vid", 0) or 0)
    pid = int(entry.get("pid", 0) or 0)
    description = str(entry.get("description", "")).lower()
    if any(hint in description for hint in REJECT_HINTS):
        return -100
    # 遥控器那条 DJI VCOM **绝不能**被 A 路的 Auto 选中：中文 Windows 上 C 板的描述是
    # 「USB 串行设备」（不含英文关键字，只有 30 分），而 DT7 有 60 分 —— 不判负就会
    # 把 0x5A 帧写进遥控器、并且让闭环自检读不到 km_key，整场调试被带偏。
    if (vid and pid and (vid, pid) in DJI_RC_USB_IDS) or "dji" in description:
        return -200
    if not (vid and pid):
        return -50  # 非 USB（主板传统串口、蓝牙 RFCOMM 等）：不参与 Auto
    if (vid, pid) in PREFERRED_USB_IDS:
        return 100
    if any(hint in description for hint in PREFERRED_HINTS):
        return 60
    return 30  # 其他带 VID/PID 的 USB 串口设备


def candidate_port_names() -> list[str]:
    return [str(entry["device"]) for entry in list_serial_ports()]


def port_choices() -> list[str]:
    """ClickGUI 用的选项：固定值 + 当前探测到的口（去重、保持顺序）。"""

    choices = [STUB_PORT, AUTO_PORT, "COM1", "COM3", "COM5"]
    for device in candidate_port_names():
        if device not in choices:
            choices.append(device)
    return choices


def resolve_port(port: str) -> str:
    """把 "Auto" 解析成具体设备名；显式端口原样返回。"""

    value = str(port or STUB_PORT).strip()
    if value and value.lower() != AUTO_PORT.lower():
        return value
    entries = list_serial_ports()
    if not entries:
        return STUB_PORT
    ranked = sorted(entries, key=lambda entry: score_port(entry), reverse=True)
    best = score_port(ranked[0])
    if best < AUTO_MIN_SCORE:
        return STUB_PORT  # 没有像样的 USB 候选：宁可不连，也不乱开到传统串口
    if len(ranked) > 1 and score_port(ranked[1]) == best:
        # 两个同等可疑的口：不猜，交给操作员显式选择
        return STUB_PORT
    return str(ranked[0]["device"])


def describe_ports(limit: int = 6) -> list[str]:
    """给 HUD/预检用的人类可读清单。"""

    lines: list[str] = []
    for entry in list_serial_ports()[:limit]:
        vid = int(entry.get("vid", 0) or 0)
        pid = int(entry.get("pid", 0) or 0)
        tag = f"USB {vid:04X}:{pid:04X}" if vid and pid else "非 USB"
        lines.append(f"{entry['device']} [{tag}] {entry['description']}"[:120])
    return lines


#: ``describe_ports_cached`` 的缓存：(到期时刻, 结果)。模块级单槽。
_PORT_DESCRIBE_CACHE: dict[str, object] = {"at": 0.0, "limit": None, "value": []}


def describe_ports_cached(limit: int = 6, ttl_s: float = 2.0) -> list[str]:
    """带 TTL 的 :func:`describe_ports`，供**每帧都会调到**的调用方使用。

    为什么需要它：``describe_ports`` 会走一遍 ``list_serial_ports()``（枚举
    全部端口 + 读 USB 描述串），本机实测 **2.63 ms/次**。而
    ``PolarLinkTransport.stats()`` 在 60Hz 逻辑拍上被调用 —— 直接接上去
    就是 158 ms/秒、约 15% 的单核，而它填的只是一个 HUD tooltip 里
    "已探测到：COMx" 那一行。

    TTL 为什么是 2 秒：拔插一个串口后最多 2 秒出现在列表里，对操作员是
    察觉不到的；而 2 秒 TTL 把成本摊到 1.3 ms/秒（0.13% 单核）。

    **不要用这个去驱动行为**（比如"Auto 该连哪个口"）—— 那条链路必须永远
    读真值，见 :func:`resolve_port`。缓存只配给"给人看的清单"。
    """
    import time

    now = time.monotonic()
    cache = _PORT_DESCRIBE_CACHE
    if cache["limit"] == limit and now - float(cache["at"]) < ttl_s:  # type: ignore[arg-type]
        return list(cache["value"])  # type: ignore[arg-type]
    value = describe_ports(limit)
    cache["at"] = now
    cache["limit"] = limit
    cache["value"] = value
    return list(value)


# ---------------------------------------------------------------------------
# DT7 遥控器那条 DJI USB Virtual COM
# ---------------------------------------------------------------------------
# 出处：大疆官方驱动 INF 把 `USB\VID_FFF0&PID_0008`（接口 MI_00）绑到微软
# `usbser.sys`，描述串是 "DJI USB Virtual COM"（逐字见 tools/dt7_link/dt7_vcp_probe.py 顶部，
# 载荷是从 DJI_WIN_Driver_Installer.exe 里解出来的 INF 正文）。
DJI_RC_HINTS = ("dji",)


def is_robot_port(entry: dict) -> bool:
    """这一看就是机器人本体的 CDC（StandardRobot++ / STM32），不是遥控器。"""
    vid = int(entry.get("vid", 0) or 0)
    pid = int(entry.get("pid", 0) or 0)
    return bool(vid and pid) and (vid, pid) in PREFERRED_USB_IDS


def score_rc_port(entry: dict) -> int:
    """给"像不像 DT7 遥控器口"打分。认错的代价比认不出大，所以机器人口直接判负。"""
    vid = int(entry.get("vid", 0) or 0)
    pid = int(entry.get("pid", 0) or 0)
    description = str(entry.get("description", "")).lower()
    if is_robot_port(entry):
        return -200                     # 这是车：把遥控器帧写进去会得到一个假结论
    if any(hint in description for hint in REJECT_HINTS):
        return -100
    if vid and pid and (vid, pid) in DJI_RC_USB_IDS:
        return 100
    if any(hint in description for hint in DJI_RC_HINTS):
        return 70                       # 描述里有 DJI 但 VID 不同（驱动版本差异）
    return -1                           # 其它 USB 串口一律不猜：遥控器口必须可辨认


def resolve_rc_port(port: str) -> str:
    """把 "Auto" 解析成 DT7 那个口；显式端口原样返回；认不出来返回 Stub。"""
    value = str(port or STUB_PORT).strip()
    if value and value.lower() != AUTO_PORT.lower():
        return value
    ranked = sorted(list_serial_ports(), key=score_rc_port, reverse=True)
    if not ranked or score_rc_port(ranked[0]) < 70:
        return STUB_PORT
    if len(ranked) > 1 and score_rc_port(ranked[1]) == score_rc_port(ranked[0]):
        return STUB_PORT                # 两个一样的 DJI 口（双遥控器）：不猜，让人显式选
    return str(ranked[0]["device"])


def rc_port_choices() -> list[str]:
    """DT7 专用下拉：Auto + 明确认得出的 DJI 口（不把机器人的口混进来）。"""
    choices = [AUTO_PORT]
    for entry in list_serial_ports():
        if score_rc_port(entry) >= 70:
            device = str(entry["device"])
            if device not in choices:
                choices.append(device)
    return choices


# 自定义控制器（0x0306）必须走 FTDI：官方选手端 DeviceInfo.dll 的 GetCCSSerialPort
# 就是按 FTDI 挑口的（取证见 tools/dt7_link/README.md 第 16 节）
FTDI_USB_IDS: tuple[tuple[int, int], ...] = ((0x0403, 0x6001), (0x0403, 0x6015))


def score_cc_port(entry: dict) -> int:
    vid = int(entry.get("vid", 0) or 0)
    pid = int(entry.get("pid", 0) or 0)
    if vid and pid and (vid, pid) in FTDI_USB_IDS:
        return 100
    if is_robot_port(entry):
        return -200
    return -1


def resolve_cc_port(port: str) -> str:
    """CC Port 的 Auto：只认 FTDI；多个并列或一个都没有都返回 Stub（宁缺勿滥）。"""
    value = str(port or STUB_PORT).strip()
    if value and value.lower() != AUTO_PORT.lower():
        return value
    ranked = sorted(list_serial_ports(), key=score_cc_port, reverse=True)
    if not ranked or score_cc_port(ranked[0]) < 100:
        return STUB_PORT
    if len(ranked) > 1 and score_cc_port(ranked[1]) == score_cc_port(ranked[0]):
        return STUB_PORT
    return str(ranked[0]["device"])


def port_identity(device: str) -> dict:
    """某个口现在挂着谁（用于"你选的这个其实是车的口"这种拦截）。"""
    for entry in list_serial_ports():
        if str(entry["device"]).upper() == str(device).upper():
            return entry
    return {}

def refresh_port_options(registry) -> list[str]:
    """重扫串口，把三个口下拉的 options 换成当前真值；返回人话形式的差异。

    为什么要专门做这件事：串口清单是 `build_default_registry()` 那一次算出来的
    （`model.py:1618` 的 Port、`:1653` 的 DT7 Port、`:1660` 的 CC Port），
    之后再没人刷新。而现场十有八九是**先开客户端、后插 DT7** —— 那时下拉里
    只有 Auto，Auto 又只认 DJI 那对 VID/PID，认不出就宁可拒发（这是故意的，
    不许乱开别人的口），于是操作员只剩"重启程序"一条路。比赛场上这一下就是几十秒。

    规矩：
      * 当前值**永远保住**。用户手填的 COM9 就算现在不在了也要留在列表里，
        否则一次重扫会把已配置的口吞掉，变成"越刷新越坏"。
      * 只换 options，不碰 value：刷新不该改变行为。
    """

    diffs: list[str] = []
    module = registry.get("RobotLink") if hasattr(registry, "get") else None
    if module is None:
        return diffs
    wanted = {"Port": port_choices(), "DT7 Port": rc_port_choices(),
              "CC Port": port_choices()}
    for name, opts in wanted.items():
        setting = module.get_setting(name)
        if setting is None:
            continue
        choices = [str(x) for x in opts]
        current = str(getattr(setting, "value", "") or "")
        if current and current not in choices:
            choices.append(current)
        before = [str(x) for x in (setting.options or [])]
        if before == choices:
            continue
        setting.options = choices
        gone = [x for x in before if x not in choices and x != "Auto"]
        added = [x for x in choices if x not in before]
        piece = name + ": " + (", ".join(added) if added else "无新增")
        if gone:
            piece += "（消失 " + ", ".join(gone) + "）"
        diffs.append(piece)
    return diffs

