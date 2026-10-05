from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math
import time
from typing import Any

from .model import ModuleRegistry, RobotState


# 官方 V1.3.1 KeyboardMouseControl.keyboard_value 位序。
KEYBOARD_BITS = {
    "W": 0,
    "S": 1,
    "A": 2,
    "D": 3,
    "SHIFT": 4,
    "CTRL": 5,
    "Q": 6,
    "E": 7,
    "R": 8,
    "F": 9,
    "G": 10,
    "Z": 11,
    "X": 12,
    "C": 13,
    "V": 14,
    "B": 15,
}
CONTROL_KEYS = frozenset(KEYBOARD_BITS)

# 默认绑定：协议槽位 -> 物理键名。与历史行为一致（硬编码 W/A/S/D…），
# 但现在可以被用户改绑。这里存的是“这个协议槽由哪个物理键触发”，
# 而不是反过来——因为上位机下发的永远是协议槽位，固件按槽位语义解读。
DEFAULT_CONTROL_BINDS: dict[str, str] = {slot: slot for slot in KEYBOARD_BITS}

# 可供绑定的物理键名候选（与 `PolarRMWindow.key_name()` 的输出同一命名体系）。
BINDABLE_KEY_NAMES = (
    "W", "A", "S", "D", "Q", "E", "R", "F", "G", "Z", "X", "C", "V", "B",
    "Shift", "RightShift", "Ctrl", "Space", "Tab", "Enter", "Escape",
    "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
    "Up", "Down", "Left", "Right", "F1", "F2", "F3", "F4", "F5",
    "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "None",
)

# --- 心跳节奏表 --------------------------------------------------------------
# 调用方（polar_app.on_tick）的节拍是 `max(4, round(1000/hz))` 毫秒的**整数量化**，
# 再叠一层 0.92 容差的外层门控：它每拍都肯给我们，可它给的时刻落在 12.267ms 的
# 栅格上，与名义的 13.333ms 对不齐。旧门控写的是"距上一帧 >= 13.333ms 才发"，
# 于是每一次 12.3ms 的间隙都被判太密而整拍丢掉 —— 丢掉的还不是"这一拍"，
# 而是被外层门控连同它的时戳一起作废的那一拍（polar_app 先更新
# _last_control_tick_time 再回调这里）。量化误差因此**累加**而不是平均掉：
# 60 秒长跑实测 60.72Hz / 47.42Hz / 52.13Hz，而同一时刻外层门控明明给到了
# 71.50Hz / 62.70Hz / 68.97Hz。
#
# 现在改成**按名义节奏表**推进：deadline 每次只前进一个 interval，永远从上一格
# deadline 往前推，不从"这一帧实际发出去的时刻"重算。落后就被记成落后，
# 下一拍自动补回来，量化误差互相抵消而不是滚雪球。
#
# 两个闸不许省：
#: 落后多少个周期还判定为"节奏表已失效"（线程被挂起 / 注入时钟跳变）—— 那种
#: 情况要重新锚定，不许当成量化误差补一串洪峰出来。
HEARTBEAT_CATCHUP_SPAN = 10.0
#: 两帧之间的最小间隔 = interval × 本系数。只用来兜住"同一拍里连发"，
#: 不是速率上限：外层门控保证相邻两次机会本来就 >= 12.267ms（> 0.6×13.333ms），
#: 所以正常路径上它永远不生效；真生效时也只允许 100Hz 的一口气补发。
HEARTBEAT_MIN_SPACING = 0.6


@dataclass
class ChassisCommand:
    """可审计的本地键鼠快照；实际 wire 使用 KeyboardMouseControl protobuf。"""

    seq: int
    timestamp: float
    vx: float
    vy: float
    wz: float
    spin_mode: bool
    speed_limit: int
    power_limit: int
    mock_output: bool
    keyboard_value: int = 0
    mouse_x: int = 0
    mouse_y: int = 0
    mouse_z: int = 0
    left_button_down: bool = False
    right_button_down: bool = False
    mid_button_down: bool = False
    source: str = "keyboard_mouse"
    protocol: str = "rm2026.KeyboardMouseControl.v1.3.1"

    def to_payload(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def evaluate_radio_km_selftest(
    rx0: int, rx1: int, sent0: int, sent1: int
) -> tuple[bool, str]:
    """判"客户端 -> 图传链路 -> C 板"这一程通没通（纯函数，好测）。

    判据**只看车上回读的到达计数**：客户端自己发了多少只能自证，
    说明不了链路，所以"发了但车上没涨"必须是 FAIL。
    rx < 0 表示回读读不到（没插 0x5A），那是"无法判定"，不能算通过。
    """
    rx0 = int(rx0); rx1 = int(rx1)
    if rx0 < 0 or rx1 < 0:
        return False, "读不到车上计数：先插 C 板 USB(0x5A) 才能做这个自检"
    if rx1 > rx0:
        return True, f"车上计数 {rx0}->{rx1} 帧，客户端同期发出 {max(0, sent1 - sent0)} 帧"
    if int(sent1) <= int(sent0):
        return False, "客户端一帧都没发出去（看 radio_km_last_error / 闸门原因）"
    return False, ("发了但车上收到 0 增量：查图传发送端那一路串口是否接到 C 板"
                   "（官方：图传链路 921600），见 FIELD_WIRELESS_RUNBOOK.md 第 3 步")


def evaluate_dt7_selftest(samples, forced_bits: int, baseline: int,
                          frames_sent=None):
    """判"客户端 -> DT7 -> 空口 -> C 板"这一程通没通（纯函数，好测）。

    证据只认**车上合并后的最终键位** `km_key`（固件 remote_control.c:629-630 那本账）；
    客户端自己发了多少帧说明不了链路。与终端版 `dt7_wireless_selftest` 同一套判据，
    两边由同一条测试盯着，不许跑偏。
    """
    vals = [int(s) for s in samples if s is not None and int(s) >= 0]
    forced = int(forced_bits)
    if frames_sent is not None and int(frames_sent) <= 0:
        # 优先于其它结论：自己一帧都没发的时候，说"DT7 没理这一帧"是假结论
        return ("NOT-SENT", "客户端自己一帧都没写 DT7：查出口闸门 / DT7 Real TX / "
                           "dt7_last_error，别去怀疑遥控器")
    if not vals:
        return ("NO-READBACK",
                "一个 km_key 样本都没读到（要插着 C 板 USB 0x5A 才有回读），判定不成立")
    if any((s & forced) == forced and (int(baseline) & forced) != forced for s in vals):
        return ("PASS", f"{len(vals)} 个回读样本里出现目标键位 0b{forced:016b}")
    if any(s != int(baseline) for s in vals):
        return ("PARTIAL", "键位动了但不是我们发的那些位：帧被吃了、载荷错位（字段顺序/偏移）")
    return ("NO-CHANGE", "DT7 完全没理这一帧：换帧型再试 tools/dt7_link/dt7_vcp_probe.py")


def evaluate_dt7_hold(tail_samples, forced_bits: int):
    """停发之后键位还在不在 = DT7 会不会粘键。

    看**最后四分之一**的稳态，不看"有没有样本仍停在按住位"——超时清零的实现前一段
    必然还停着，那样会把安全侧误判成危险侧（本队踩过一次，判据就是那么写崩的）。
    """
    tail = [int(s) for s in tail_samples if s is not None and int(s) >= 0]
    if not tail:
        return ("NO-READBACK", "停发后没有回读样本，粘键判定不成立（不许当成安全）")
    quarter = max(1, len(tail) // 4)
    still = [s for s in tail[-quarter:] if (s & int(forced_bits)) == int(forced_bits)]
    if still:
        return ("STUCK", "停发后仍停在按住位 = DT7 粘键：客户端进程一没，"
                         "驾驶员立刻把 s[0] 拨下档锁底盘")
    return ("EXPIRES", "停发后键位自己掉回基线 = DT7 会过期清零（安全侧）")


def describe_km_policy(pol) -> tuple:
    """把车上报回来的键鼠档位编号翻成人话，并回答"图传键鼠在这一档会不会参与"。

    编号来自固件 `host_km_link.h` 的 HOST_KM_POL_*（0 none / 1 rc / 2 vt / 3 rc_vt / 4 vt_rc）。
    为什么非要知道它：`km_rx` 在 referee **收到帧**时就涨，档位是之后才裁决用不用；
    默认档 CL_KM_RC 下 0x0311 整个不参与，但计数照涨 —— 只报"已收到 N 帧"
    会被读成"能控车了"。这条把两件事分开。
    """
    try:
        value = int(pol) if pol is not None else -1
    except (TypeError, ValueError):
        value = -1
    table = {0: ("none", False, "两路键鼠都被屏蔽（连驾驶员键位也屏蔽）"),
             1: ("rc", False, "图传键鼠不参与：要用 0x0311 就跑 --km rc_vt / vt / vt_rc"),
             2: ("vt", True, "只认图传：驾驶员键位门闸被关"),
             3: ("rc_vt", True, "互补、遥控器优先（图传可用）"),
             4: ("vt_rc", True, "互补、图传优先")}
    if value not in table:
        return ("?", None, "车上没报档位（固件没打 POLAR-KM-LINK，或 debug 槽被占）")
    name, participates, hint = table[value]
    return (name, participates, hint)


def build_dt7_selftest_report(*, keys, forced_bits: int, baseline, verdict: str, detail: str,
                              hold_verdict: str = "", hold_detail: str = "",
                              samples: int = 0, tail_samples: int = 0,
                              link_profile=None) -> dict:
    """把一次按钮式自检压成可复核的证据（跑完不只剩一句话）。"""
    prof = dict(link_profile or {})
    return {
        "tool": "dt7_link_selftest(ui)",
        "link": "C (PC -> DT7 VCOM -> DR16 -> UART3 -> rc_ctrl.key.v)",
        "keys": list(keys),
        "sent_bits": int(forced_bits),
        "baseline_km_key": baseline,
        "samples": int(samples),
        "tail_samples": int(tail_samples),
        "verdict": verdict,
        "detail": detail,
        "hold_verdict": hold_verdict,
        "hold_detail": hold_detail,
        "passed": verdict == "PASS",
        "safe_when_dying": hold_verdict == "EXPIRES",
        "profile": {k: prof.get(k) for k in
                    ("frame_variant", "crc_variant", "cmd_wire", "payload_layout",
                     "baudrate", "mouse_limit")},
    }


# C 路（DT7）默认允许的键位：只有驾驶四键。与 0x5A 侧 VRC_DRIVE_KEY_MASK、
# 0x0311 侧 KM_KEY_ALLOWED_MASK 同口径 —— 三条出口里任何一条都不许"顺手"
# 把开火前置执行器（Q/E）带出去。
DT7_DEFAULT_KEY_MASK = ((1 << KEYBOARD_BITS["W"]) | (1 << KEYBOARD_BITS["S"])
                        | (1 << KEYBOARD_BITS["A"]) | (1 << KEYBOARD_BITS["D"]))
DT7_SPIN_KEY_MASK = 1 << KEYBOARD_BITS["SHIFT"]        # 防御性底盘模式，显式开关放行
DT7_FIRE_KEY_MASK = ((1 << KEYBOARD_BITS["Q"]) | (1 << KEYBOARD_BITS["E"]))

class KeyboardControlBridge:
    """采集键鼠并经 BackendManager 的官方 MQTT 上行发送。

    两条上行出口：
      * 官方 KMC（MQTT KeyboardMouseControl）——本队固件没有它的接收方，
        留着只是为兼容官方裁判链路；
      * 无线键鼠（PRM1 kind=3 走 CustomControl/0x0311）——见 _send_radio_km()，
        由 RobotLink."Radio KM Egress" 开关驱动，默认关。
    0x0311 属于图传链路；这里绝不直接写 115200 串口。BackendManager
    会编码两路载荷，MQTT/图传模块再转发给机器人。
    """

    def __init__(self, publisher: Any | None = None, mock_output: bool = True) -> None:
        self.publisher = publisher
        self.mock_output = bool(mock_output)
        #: 无线键鼠出口（CustomControl -> 图传链路 -> 机器人 0x0311，PRM1 kind=3）。
        #: 默认关：开着要同时满足"车上固件带 POLAR-KM-LINK"和
        #: "__CONTROL_LINK_KM 不是 CL_KM_RC"，两端没对齐时白占图传带宽。
        self.radio_km_enabled = False
        self.radio_km_sent = 0
        #: 发送层结果：真的尝试发了、但被 MQTT/门控拒了的原因。
        self.radio_km_last_error = ""
        #: 闸门层原因：三道闸（急停 / 模块启用 / Allow Drive）没过，
        #: 根本没走到发送。与 last_error 分开存，否则"没让我发"和
        #: "发了但被拒"会互相覆盖，现场看不出到底该去点哪个开关。
        self.radio_km_gate_reason = ""
        #: C 路这一帧被 max_hz 挡下时置 True：_consume_relative_motion
        #: 据此保留鼠标增量，别把操作手 2/3 的意图清零丢掉。
        self._dt7_keep_motion = False
        #: 松手中立帧"还没落到线上"。DT7/DR16 没有过期自动清零，这一位为 True
        #: 期间车上的键位仍是我们上一帧的；tick 会按心跳节奏一直补发直到落地。
        #: 它是"限流挡下松手帧"和"写口失败"两种情况的兜底，界面也读它。
        self.dt7_release_pending = False
        # ---- 车上回读的实况（来自 0x5A Debug 段的 km_rx / km_key，-1 = 读不到）----
        #: 我们只能自证"发了多少帧"，那说明不了链路。真正的证据是**C 板自己数到的
        #: 0x0311 帧数**（固件 POLAR-KM-LINK 的 HostKmReportUplink 报回来）。
        self.radio_km_robot_rx = -1
        self.radio_km_policy = "?"      # 车上报回来的键鼠档位名
        self.radio_km_policy_hint = ""
        self.radio_km_robot_key = -1
        self.radio_km_robot_rx_growing = False
        #: 一句话实况，直接给界面显示：off / blocked / no-readback / live /
        #: silent（车上一个包都没收到）/ stalled（收到过但不长了）
        self.radio_km_link_state = "off"
        self.radio_km_link_note = "未开启"
        # ---- 自定义控制器出口（0x0306 -> 官方选手端 -> DT7 空口）----
        #: 这条**完全不依赖车上的图传 921600 接线**：键鼠是塞进选手端，
        #: 由选手端经 DT7 的键鼠字段发出来的（《2026 通信协议》表1-1 / 1.5 / 表1-43）。
        #: 对象由 manager 注入；没注入时这里为 None，开关打开也不发。
        self.cc_link = None
        self.cc_enabled = False
        self.cc_sent = 0
        self.cc_dropped_keys: list[str] = []
        self.cc_last_error = ""
        #: 允许经 0x0306 注入的协议槽位图。默认只有驾驶四键；SHIFT/Q/E 的授权
        #: 与 0x0311 那条走同一套决定，由 manager 下发，这里不自己扩权。
        self.cc_key_mask = 0x0F
        # ---- 直连 DT7 出口（自己发键鼠给遥控器，不依赖官方选手端在跑）----
        #: 对象由 manager 注入；命令号仍是**候选值**（configs/dt7_profile.json），
        #: 所以没注入、没显式打开就一帧都不发。
        self.dt7_link = None
        self.dt7_enabled = False
        self.dt7_sent = 0
        self.dt7_mouse_note = ""
        self.km_source_conflict = ""
        self.dt7_last_error = ""
        # ---- C 路自检的两个把手（界面那个按钮用；平时都是"不干预"）----
        #: 不为 None 时，DT7 这一帧起一律发这个键位图（覆盖键盘当前值）。
        self.dt7_forced_keys = None
        #: True 时**一帧都不写 DT7**，也不补松手 —— 用来模拟"进程被杀"，
        #: 从而量出 DT7 会不会把最后一帧的键位一直重复（粘键）。
        self.dt7_send_paused = False
        #: 暂停的到期时刻（假时钟友好：用 perf_counter）。默认无穷大 = "布尔位说了算"，
        #: 老的调用点/测试只设布尔位时行为不变；界面自检会写一个有限值，于是
        #: "自检中途出事、finish 没跑成"这种最阴的失效模式会自己恢复，而不是
        #: 让 C 路从此一帧都不发。
        self.dt7_paused_until = float("inf")
        #: C 路允许到达固件的键位掩码。DT7 那侧的键位被固件当作驾驶员按键记账
        #: （remote_control.c:629-630），完全不过 KM_KEY_ALLOWED_MASK，
        #: 所以客户端这道掩码是唯一的闸，不能不设防。
        self.dt7_key_mask = DT7_DEFAULT_KEY_MASK
        #: 被掩码挡掉的键名（要显示出来，不许静默丢键）
        self.dt7_dropped_keys: list[str] = []
        self.key_state: dict[str, bool] = {key: False for key in CONTROL_KEYS}
        # 协议槽 -> 已绑物理键名；可由前端改绑，默认与旧行为一致。
        self.binds: dict[str, str] = dict(DEFAULT_CONTROL_BINDS)
        self.mouse_x = 0
        self.mouse_y = 0
        self.mouse_z = 0
        self.left_button_down = False
        self.right_button_down = False
        self.mid_button_down = False
        self.seq = 0
        self.last_command: ChassisCommand | None = None
        #: 最近一帧的审计快照，**按需**编码（见 last_payload 属性）。
        #: 不再每帧无条件 asdict()+json.dumps()：实测那一步 5.5us/帧
        #: （占 send_from_state 的 60%），而全仓库只有测试断言读它。
        self._last_payload_command: ChassisCommand | None = None
        self.last_error = ""
        self.last_send_at = 0.0
        #: 心跳节奏表（见模块顶部注释）：下一帧的**名义**到期时刻，以及它按
        #: 哪个 interval 推的。两者都不是发送闸门，闸门只有 _send_due() 一处。
        self._next_send_at: float | None = None
        self._pacing_interval = 0.0
        #: 最近一次 tick() 用的钟。整条桥只认这一把：set_key()/release_all() 这些
        #: 不经 tick 的发送也用它落戳，于是"门控比较的时刻"和"落戳的时刻"
        #: 永远来自同一个时源。注入时钟的测试与真实运行都不再出现两把钟打架。
        self._tick_now: float | None = None
        self.heartbeat_hz = 75.0
        # Active input remains 75 Hz. While an operator-authorized match is
        # idle, send a bounded 4 Hz neutral frame for the robot watchdog.
        self.idle_zero_interval_s = 0.25
        self._dirty = False

    def set_bind(self, slot: str, key_name: str) -> bool:
        """改绑一个协议槽。失败时返回 False 且不改变现有状态。

        不允许两个槽位绑到同一个物理键（会造成一键双动作）。但"占用"只算
        **别人显式挑过的那一次**：一个槽还停在出厂的"槽名=键名"同义绑定上时，
        它并没有占住那个物理键 —— 否则 W<->D 这种"把两个键对调"永远配不出来
        （旧实现里"释放旧占用"那一段被上面的拒绝分支完全挡住，是死代码：
        三次 set_bind 全部返回 False）。这种同义让位会把对方置成 "None"，
        该槽从此不接任何物理键，**别的槽仍然一个都动不了**。
        绑成 "None" 表示该槽不接物理键。
        """

        slot = str(slot).upper()
        if slot not in CONTROL_KEYS:
            return False
        candidate = str(key_name or "None").strip()
        if candidate and candidate not in BINDABLE_KEY_NAMES:
            return False
        if candidate != "None":
            lowered = candidate.lower()
            for other, bound in self.binds.items():
                if other == slot or str(bound).lower() != lowered:
                    continue
                if str(bound) != DEFAULT_CONTROL_BINDS.get(other):
                    return False  # 已被别的槽显式占用
                # 还停在出厂同义绑定：让出来，避免两个槽同时响应同一个物理键
                self.binds[other] = "None"
        self.binds[slot] = candidate or "None"
        return True

    def reset_binds(self) -> None:
        self.binds = dict(DEFAULT_CONTROL_BINDS)

    def resolve_slot(self, key_name: str, is_right_shift: bool = False) -> str | None:
        """把按下的物理键解析为它当前驱动的协议槽（可能有多个）。

        返回所有命中的槽位，调用方逐个 set_key。一个物理键可以合法地驱动
        多个协议槽（例如 Shift 与 RightShift 共享同一个物理键），但同一个槽
        只能有一个绑定。
        """

        name = str(key_name or "").strip()
        if not name:
            return ()
        lowered = name.lower()
        hits = tuple(
            slot
            for slot, bound in self.binds.items()
            if slot in CONTROL_KEYS and str(bound).lower() == lowered
        )
        return hits

    def set_key(self, key: str, pressed: bool, state: RobotState, registry: ModuleRegistry) -> ChassisCommand | None:
        key = key.upper()
        if key not in CONTROL_KEYS:
            return None
        if self.key_state.get(key, False) == bool(pressed):
            return None
        self.key_state[key] = bool(pressed)
        self._dirty = True
        return self.send_from_state(state, registry)

    def set_mouse_button(
        self,
        button: int,
        pressed: bool,
        state: RobotState,
        registry: ModuleRegistry,
    ) -> ChassisCommand | None:
        attr = {1: "left_button_down", 2: "mid_button_down", 3: "right_button_down"}.get(int(button))
        if attr is None or bool(getattr(self, attr)) == bool(pressed):
            return None
        setattr(self, attr, bool(pressed))
        self._dirty = True
        return self.send_from_state(state, registry)

    def add_mouse_motion(self, dx: float, dy: float) -> None:
        self.mouse_x = self._clamp_i32(self.mouse_x + int(round(dx)))
        # Qt y 向下为正；官方协议 y 负值表示向下，故取反。
        self.mouse_y = self._clamp_i32(self.mouse_y - int(round(dy)))
        self._dirty = True

    def add_wheel(self, delta: float) -> None:
        self.mouse_z = self._clamp_i32(self.mouse_z + int(round(delta)))
        self._dirty = True

    def reset_inputs(self) -> None:
        for key in self.key_state:
            self.key_state[key] = False
        self.mouse_x = self.mouse_y = self.mouse_z = 0
        self.left_button_down = self.right_button_down = self.mid_button_down = False
        self._dirty = True

    def release_all(self, state: RobotState, registry: ModuleRegistry) -> ChassisCommand:
        """Clear local input and immediately publish one neutral KMC snapshot."""

        self.reset_inputs()
        command = self.send_from_state(state, registry)
        # 上面那一帧走的是普通出口：它可能被闸门拒了、也可能被 max_hz 挡了。
        # 而关窗口 / 失去焦点这两条路调的就是本函数，只清本地等于"手松了、
        # 线上还按着"——DT7 与选手端那两侧都没有过期自动清零。所以这里再各补
        # 一帧强制中立（release_* 自己会抢时隙，多一帧零键无害）。
        self.release_direct_dt7()
        self.release_custom_controller()
        return command

    def clear_pending(self) -> None:
        """急停已走专用零帧后清除普通发送重试。"""
        self._dirty = False

    def send_from_state(self, state: RobotState, registry: ModuleRegistry,
                        now: float | None = None) -> ChassisCommand:
        command = self.build_command(state, registry)
        self.send_command(command, now=now)
        return command

    def tick(self, state: RobotState, registry: ModuleRegistry, now: float | None = None) -> ChassisCommand | None:
        now = time.perf_counter() if now is None else float(now)
        self._tick_now = now
        active = (
            any(self.key_state.values())
            or self.left_button_down
            or self.right_button_down
            or self.mid_button_down
            or self.mouse_x != 0
            or self.mouse_y != 0
            or self.mouse_z != 0
            # 自检按住探针键位时也算活跃：空闲档只有 4Hz，而未授权时压根不发，
            # 那时按钮测出来的"DT7 没理这一帧"是假结论——其实客户端一帧都没出去
            or self.dt7_forced_keys is not None
        )
        # 松手帧没落地时，即使本地看着"全空闲"也必须继续按心跳发：这是
        # "驾驶员手都抬了车还在往前顶"的唯一兜底，不能受 MQTT/空闲零帧授权影响。
        must_flush = bool(getattr(self, "dt7_release_pending", False))
        if not active and not self._dirty and not must_flush and not self._idle_zero_authorized():
            return None
        interval = (1.0 / self.heartbeat_hz
                    if (active or self._dirty or must_flush) else self.idle_zero_interval_s)
        if not self._send_due(now, interval):
            return None
        # now 往下一路传：门控、落戳、节奏表必须用**同一把钟**。以前
        # send_command 自己取模块级 time.perf_counter()，注入时钟的 tick() 在
        # 另一把钟上被比较，差值恒为负 —— 整条桥会永久停发（240 次 tick 只发 0 次）。
        return self.send_from_state(state, registry, now=now)

    def _send_due(self, now: float, interval: float) -> bool:
        """心跳的唯一发送闸门：按**名义节奏表**判到期，不按"距上一帧多久"。

        两者在理想时刻等价，在整数量化的调用栅格上不等价：旧判据每遇到一次
        12.3ms 的间隙就整拍作废，而外层门控的时戳已经先被推进了 —— 那一拍
        永久损失，量化误差越跑越远。改判"这一拍对应的名义 deadline 到了没有"，
        迟到就记成迟到，下一拍自动补齐。
        """
        if interval != self._pacing_interval or self._next_send_at is None:
            # 节奏在 4Hz 空闲零帧与 75Hz 活跃心跳之间切换（或者第一次跑）：
            # 上一套 deadline 不再成立，从上次发送重新锚定。
            self._pacing_interval = interval
            self._next_send_at = self.last_send_at + interval
        if now < self._next_send_at:
            return False
        return (now - self.last_send_at) >= interval * HEARTBEAT_MIN_SPACING

    def _mark_sent(self, now: float) -> None:
        """记一次真实发送：落时间戳，并让节奏表前进**一格**。

        前进的是 deadline，不是"实际发送时刻 + interval" —— 这正是量化误差
        能互相抵消的那一处。落后超过 HEARTBEAT_CATCHUP_SPAN 格（线程挂起、
        注入时钟跳变）就当节奏表作废、重新锚定，不许补一串洪峰。
        """
        self.last_send_at = now
        base = self._next_send_at
        interval = self._pacing_interval or (1.0 / self.heartbeat_hz)
        if base is None or now - base > interval * HEARTBEAT_CATCHUP_SPAN:
            base = now
        self._next_send_at = base + interval

    def _idle_zero_authorized(self) -> bool:
        """Avoid denied heartbeat/log spam while the fail-closed gate is locked."""

        if self.mock_output:
            return True
        safety = getattr(self.publisher, "safety", None)
        control_state = getattr(safety, "control_state", None)
        value = getattr(control_state, "value", control_state)
        if str(value) != "MATCH_ARMED":
            return False
        is_fighting = getattr(safety, "is_fighting", None)
        # 第 135 轮：**读不到 `is_fighting` 就算「不在交战」**（不放行）。
        #
        # 原来写的是 `not callable(is_fighting) or bool(is_fighting())` ——
        # `safety` 上没有这个方法时 `not callable(None)` 是 `True`，
        # 于是**读不到反而放行**。
        #
        # ⚠ 而**上面两行**的阶段检查对同一类「读不到」是 fail-closed 的
        #   （`str(value) != "MATCH_ARMED"` 直接 `return False`）。
        #   **同一个判定函数里，两种读不到给出相反的答案** ——
        #   那正是「不确定性该往哪边倒」没有定下来的症状。
        #
        # 实际影响有界：这一帧是**全零**指令，而且真发布仍由
        # `safety.can_publish()` 把关。所以那不是「车动了」，
        # 而是「闸门锁着时还在尝试发」—— **但方向仍然是反的**。
        return callable(is_fighting) and bool(is_fighting())

    def build_command(self, state: RobotState, registry: ModuleRegistry) -> ChassisCommand:
        forward = float(self.key_state["W"]) - float(self.key_state["S"])
        strafe = float(self.key_state["D"]) - float(self.key_state["A"])
        yaw = float(self.key_state["E"]) - float(self.key_state["Q"])
        length = math.hypot(forward, strafe)
        if length > 1.0:
            forward /= length
            strafe /= length
        keyboard_value = sum(1 << bit for key, bit in KEYBOARD_BITS.items() if self.key_state[key])
        chassis = registry.get("ChassisAssist")
        spin = registry.get("SpinMode")
        power_setting = chassis.get_setting("Power Limit") if chassis else None
        speed_setting = spin.get_setting("Spin Speed") if spin else None
        self.seq += 1
        return ChassisCommand(
            seq=self.seq,
            timestamp=time.time(),
            vx=forward,
            vy=strafe,
            wz=yaw,
            spin_mode=bool(spin and spin.enabled),
            speed_limit=int(speed_setting.value) if speed_setting else state.speed_limit,
            power_limit=int(power_setting.value) if power_setting else state.power_limit,
            mock_output=self.mock_output,
            keyboard_value=keyboard_value,
            mouse_x=self.mouse_x,
            mouse_y=self.mouse_y,
            mouse_z=self.mouse_z,
            left_button_down=self.left_button_down,
            right_button_down=self.right_button_down,
            mid_button_down=self.mid_button_down,
        )

    def _send_radio_km(self, command: ChassisCommand) -> None:
        """无线键鼠出口（PRM1 kind=3 -> CustomControl -> 图传 -> 机器人 0x0311）。

        它与官方 KMC 出口**刻意互不隶属**，理由有两条，都是以前踩过的坑：

        1. 不看 ``self.mock_output``。真决定"上不上网络"的是 BackendManager 里
           ``MqttBackend.publish_topic()`` 的 mock 分支和 ``safety.can_publish()``
           门控；这里再挡一层的结果是"开关明明打开了却一帧都看不到"，
           现场没法区分"没发"和"发了但被 mock 吞了"。
        2. 不看 KMC 成不成功。两条出口在车上是两套不同消费者
           （KMC 那条在本队固件里根本没有接收方），一条失败不构成
           另一条不发 reasons，也不许把自己的错误写进 ``last_error`` 冒充主通道。

        所以本函数只可能改这三个量：radio_km_sent / radio_km_last_error，
        绝不改 last_error、last_payload 与重试标志。
        """
        if not self.radio_km_enabled:
            return
        publish = getattr(self.publisher, "publish_km_control", None)
        if not callable(publish):
            self.radio_km_last_error = "无线键鼠出口不可用：发布器没有 publish_km_control"
            return
        try:
            ok, reason = publish(
                keyboard_value=command.keyboard_value,
                mouse_x=command.mouse_x,
                mouse_y=command.mouse_y,
                mouse_z=command.mouse_z,
                left=command.left_button_down,
                right=command.right_button_down,
                mid=command.mid_button_down,
            )
        except Exception as exc:  # 出口异常不得影响主通道
            self.radio_km_last_error = str(exc)
            return
        self.radio_km_last_error = "" if ok else str(reason)
        if ok:
            self.radio_km_sent += 1

    def _send_custom_controller(self, command: ChassisCommand) -> None:
        """自定义控制器出口：把键鼠写成 0x0306 帧发给官方选手端。

        只转发 `cc_key_mask` 里的槽位（默认 W/S/A/D）。被掩码挡掉、或被
        2 键无冲挤掉的键都记进 `cc_dropped_keys`，**不静默丢**——
        "驾驶员按了没反应"是最难查的一类问题。
        本函数绝不写 last_error：这条出口失败不代表官方通道失败。
        """
        if not self.cc_enabled or self.cc_link is None:
            return
        names = [slot for slot, bit in KEYBOARD_BITS.items()
                 if (command.keyboard_value & (1 << bit))
                 and (self.cc_key_mask & (1 << bit))]
        try:
            ok, why = self.cc_link.send_keymouse(
                pressed=names,
                dx=command.mouse_x, dy=command.mouse_y,
                left=command.left_button_down, right=command.right_button_down,
            )
        except Exception as exc:      # 出口异常不得影响主通道
            self.cc_last_error = str(exc)
            return
        self.cc_last_error = "" if ok else str(why)
        if ok:
            self.cc_sent += 1
        self.cc_dropped_keys = list(getattr(self.cc_link, "dropped_keys", []) or [])


    def _send_direct_dt7(self, command: ChassisCommand) -> None:
        """直连 DT7：把键鼠按厂商那 22 字节（0x5A 头）写进 DJI VCOM。

        鼠标用**增量**（不是绝对像素）：DT7 的 mouse_x/y 本身就是"移动速度"语义
        （接收机 DBUS 第 6-11 字节 + 厂商 Input_Device_Status_RC 都是 int16 增量），
        与 0x0306 那条走选手端绝对坐标的语义不同 —— 两条路的鼠标字段不许互相借用。
        键位直接复用 command.keyboard_value：位序已被厂商枚举核实。
        """
        if not self.dt7_enabled or self.dt7_link is None:
            # 出口本来就没开：线上不可能有我们按住的键（关出口那一下 manager 已经
            # 补过松手帧）。这里必须把 pending 清掉，否则心跳会被它钉在 75Hz 空转。
            self.dt7_release_pending = False
            return
        if self.dt7_send_paused:
            if time.perf_counter() < self.dt7_paused_until:
                # 粘键测试的第二阶段：故意什么都不写。这里**不能**补松手帧，否则量的
                # 就不是"进程没了以后 DT7 怎么办"，而是"我们主动松手以后怎么办"。
                self.dt7_release_pending = False
                return
            # 到期自愈：暂停是自检借用的把手，不是永久开关。
            self.dt7_send_paused = False
        if self.dt7_forced_keys is not None:
            command = replace(command, keyboard_value=int(self.dt7_forced_keys))
        else:
            # 上游键位表可能带着 SHIFT/Q/E 过来，出口这里再收一次
            masked = int(command.keyboard_value) & int(self.dt7_key_mask) & 0xFFFF
            self.dt7_dropped_keys = sorted(
                name for name, bit in KEYBOARD_BITS.items()
                if (int(command.keyboard_value) >> bit) & 1 and not ((masked >> bit) & 1))
            if masked != int(command.keyboard_value):
                command = replace(command, keyboard_value=masked)
        neutral_now = (int(command.keyboard_value) == 0
                       and int(command.mouse_x) == 0 and int(command.mouse_y) == 0
                       and int(command.mouse_z) == 0
                       and not command.left_button_down and not command.right_button_down
                       and not command.mid_button_down)
        try:
            ok, why = self.dt7_link.send(
                pressed_bits=command.keyboard_value,
                mouse_dx=command.mouse_x, mouse_y=command.mouse_y, mouse_z=command.mouse_z,
                left=command.left_button_down, right=command.right_button_down,
                mid=command.mid_button_down,
            )
        except Exception as exc:
            self.dt7_last_error = str(exc)
            # 异常时无法判断这一帧到底进没进口，按最坏情况处理：中立帧要补发。
            self.dt7_release_pending = bool(neutral_now)
            return
        self.dt7_last_error = "" if ok else str(why)
        if ok:
            self.dt7_sent += 1
            self.dt7_release_pending = False
        else:
            # "内容合法但没落地"（被 max_hz 挡 / 写口失败）的中立帧要一直补发；
            # 闸门拒绝（profile 不对、命令号没定案）不补，那属于配置问题，
            # 补发只会在日志里刷同一句原因。
            self.dt7_release_pending = bool(
                neutral_now and getattr(self.dt7_link, "last_send_dropped", False))
            if getattr(self.dt7_link, "last_send_rate_limited", False):
                # 只有"被 max_hz 挡了"这一种失败要留增量；闸门拒绝/写串口失败
                # 一律照旧清零，否则解除锁定的一瞬间会甩出一大段陈旧位移。
                self._dt7_keep_motion = True

    def release_direct_dt7(self) -> None:
        """松手一帧（DT7 那侧没有"过期自动清零"的兜底，必须我们主动发）。"""
        if self.dt7_link is None:
            return
        try:
            ok, why = self.dt7_link.release()
        except Exception as exc:
            self.dt7_last_error = str(exc)
            return
        if not ok:
            self.dt7_last_error = str(why)

    def release_custom_controller(self) -> None:
        """急停/失去焦点时把选手端那侧的键鼠也归零（0x0306 全零键值）。"""
        if self.cc_link is None:
            return
        try:
            ok, why = self.cc_link.release_all()
        except Exception as exc:
            self.cc_last_error = str(exc)
            return
        self.cc_last_error = "" if ok else str(why)
    @property
    def last_payload(self) -> bytes:
        """最近一帧的 JSON 审计快照（按需编码，读到的字节与旧实现逐位相同）。

        原来是 send_command 里一句无条件的 ``command.to_payload()``：
        asdict() + json.dumps() 实测 5.5-7.4us/帧，cProfile 里 _asdict_inner
        一帧被调 19 次、占总时间 46%，而全仓库只有两处测试断言读这个属性。
        热路径上没有任何消费者，所以改成惰性求值。
        """
        command = self._last_payload_command
        return b"" if command is None else command.to_payload()

    def send_command(self, command: ChassisCommand, now: float | None = None) -> None:
        self.last_command = command
        self._last_payload_command = command
        self.last_error = ""
        if now is None:
            # 落戳用 tick() 的那把钟；tick() 从没跑过才退回模块级 perf_counter。
            # 以前这里无条件 time.perf_counter()，于是注入时钟的 tick() 拿一把
            # 钟去比一把差了几分钟的钟，差值恒为负 —— 整条桥永久停发。
            now = time.perf_counter() if self._tick_now is None else self._tick_now
        self._mark_sent(float(now))
        # 无线键鼠出口放在最前面，且不受下面的 mock 短路影响：
        # 它是独立的一条上行路（0x0311），开关就是它的开关。
        # 键鼠来源互斥：《2026 通信协议》2.2.1 明确"同时仅能通过一个来源生成发送给
        # 机器人的键鼠指令"。更硬的理由在车上：直连 DT7 与 0x0306 经选手端，最终灌进的是
        # **同一个** DT7 键鼠字段（固件 application/other/remote_control.c:623-630 的
        # HOST_RC_DRIVER_KEYS），两条同开 = 两个写者抢一条口，键位互相覆盖、帧还会撕裂。
        km_live = [name for name, on in (
            ("0x0311 无线键鼠", bool(getattr(self, "radio_km_enabled", False))),
            ("直连 DT7", bool(self.dt7_enabled)),
            ("0x0306 自定义控制器", bool(getattr(self, "cc_enabled", False))),
        ) if on]
        self.km_source_conflict = ("键鼠来源只能有一个，当前同时开着："
                                   + "、".join(km_live)) if len(km_live) > 1 else ""
        # C 路被限流挡下时，鼠标增量要滚存到下一帧（见 _consume_relative_motion）。
        # 这一位每帧无条件清零：它描述的是"**这一帧**的 C 路结果"，不是上一帧的。
        self._dt7_keep_motion = False
        if self.km_source_conflict:
            # 冲突时下面三条出口一个都不走，等于"全部出口都没开"。于是
            # dt7_release_pending 永远等不到落地的那帧，心跳被它钉死在
            # 75Hz 空转（实测 3.1 秒 225 次 vs 解除后 1 次）。这里必须按
            # "出口没开"同义清掉：冲突期间线上一个字节都不写，清不清 pending
            # 对车完全等价，而不清就把整条回路烧在这儿空转。
            self.dt7_release_pending = False
        else:
            self._send_radio_km(command)
            # 自定义控制器出口同理：它是"经官方选手端进 DT7"的独立一条路，
            # 既不受 KMC 成败影响，也不受 0x5A / 图传链路状态影响。
            self._send_custom_controller(command)
            # 直连 DT7：键位图可以复用 command.keyboard_value（位序已被厂商 KeyBoard 枚举
            # 核实，见 README 19.1）；但鼠标这里是**增量**，与 0x0306 那条的绝对像素
            # 语义不同，两条路的鼠标字段不许互相借用。
            self._send_direct_dt7(command)
        if self.mock_output:
            self._dirty = False
            self._consume_relative_motion()
            return
        if self.publisher is None or not hasattr(self.publisher, "publish_keyboard_mouse"):
            self.last_error = "未连接 KeyboardMouseControl MQTT 发布器"
            self._consume_relative_motion()
            return
        try:
            ok, reason = self.publisher.publish_keyboard_mouse(
                command.mouse_x,
                command.mouse_y,
                command.mouse_z,
                left=command.left_button_down,
                right=command.right_button_down,
                keyboard_value=command.keyboard_value,
                mid=command.mid_button_down,
            )
            if not ok:
                self.last_error = str(reason)
            else:
                self._dirty = False
        except Exception as exc:
            self.last_error = str(exc)
        # 发没发出去都要消费这一栏。它是**相对量**：不清零就每个 tick 把同一份
        # 陈旧位移重发一遍，而现场 MQTT 十有八九一直是 LOCKED（没裁判/没 arm），
        # C 路于是拿到一个恒定的 mouse_x —— 云台按 mouse/S 每 1ms 积分，
        # 表现就是"手早就停了，云台还在自己转"。实测见 test_dt7_review_fixes.py。
        # 唯一例外是 C 路被 max_hz 挡下，那由 _dt7_keep_motion 在内部跳过。
        self._consume_relative_motion()

    def _consume_relative_motion(self) -> None:
        # 心跳 75Hz、DT7 出口 max_hz=30Hz：不滚存的话每 tick 攒下的位移有约 2/3
        # 被无条件清零，云台只能实现操作手意图的三分之一 —— 表现是"推了不动"。
        if getattr(self, "_dt7_keep_motion", False):
            return
        self.mouse_x = self.mouse_y = self.mouse_z = 0

    @staticmethod
    def _clamp_i32(value: int) -> int:
        return max(-(1 << 31), min((1 << 31) - 1, int(value)))
