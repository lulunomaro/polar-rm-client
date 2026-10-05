# -*- coding: utf-8 -*-
"""rm_client/control.py —— 守卫条件"从来没被做成真/假"的那些决策点。

背景：第 10 轮对 60Hz 主泵做变异验证，97 个变异里 56 个改坏了全量一条都不红。
根因不是测试少，而是**所有测试的输入都落在同一侧**。同一个毛病在 control.py 上
也有一批，而且这一批更靠近安全侧。

这个文件的每一条断言都不是照抄源码写的：先用探针脚本
（``$TEMP/audit_ctrl11/facts.py``，在**未经改动的** control.py 上跑）
把事实读出来，再把那个事实写成断言。凡是"改坏了没人发现"的决策点，
都在这里被钉住，并且逐条做过变异验证（把那行改坏 -> 本文件变红）。

**全部用假时钟，一次 sleep 都没有**：数字可复算，不受机器快慢影响。
"""
from __future__ import annotations

import math
import sys
import time as real_time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rm_client.control as C                                       # noqa: E402
from rm_client.control import (                                     # noqa: E402
    DT7_DEFAULT_KEY_MASK,
    KEYBOARD_BITS,
    ChassisCommand,
    KeyboardControlBridge,
)
from rm_client.model import (                                      # noqa: E402
    ModuleRegistry,
    RobotState,
    build_default_registry,
)

FAILED = 0
STATE = RobotState()
REG = build_default_registry()
W = 1 << KEYBOARD_BITS["W"]
X = 1 << KEYBOARD_BITS["X"]
HEARTBEAT = 1.0 / 75.0


class FakeClock:
    """整条桥共用这一把钟。真实运行时 tick(now=) 与 time.perf_counter() 本来就是
    同一把；这里显式钉住，才能把"帧率"当成可复算的数字。"""

    t = 0.0

    def perf_counter(self):
        return self.t

    def time(self):
        return real_time.time()


CLOCK = FakeClock()
C.time = CLOCK


def check(name, ok, detail=""):
    global FAILED
    # ⚠ 第 155 轮：成功时打 `[PASS]`（失败仍打 `[FAIL]`）——
    # 原来只打 `[ OK ]`，而 `verify_all.run()` 只数 `[PASS]` 开头的行
    # —— **这个套件给门禁的贡献始终是 0**。
    # **只改成功那一边：失败那边一字不能动**。
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + detail) if detail else ""))
    FAILED += 0 if ok else 1


def header(text):
    print("\n--- " + text + " ---")


class Armed:
    class CS:
        value = "MATCH_ARMED"

    control_state = CS()

    def is_fighting(self):
        return True


class Pub:
    """KMC 主通道。calls 记它到底被调了几次 —— 出口异常不许影响主通道。"""

    safety = Armed()

    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    def publish_keyboard_mouse(self, *a, **k):
        self.calls += 1
        return (True, "ok") if self.ok else (False, "match control is LOCKED")


def frames_in_1s(bridge, start=0.05):
    """1 秒 @100Hz tick，返回真的发出去的帧数。"""
    n = 0
    for i in range(int(start * 100) + 1, 101):
        CLOCK.t = i * 0.01
        if bridge.tick(STATE, REG, CLOCK.t) is not None:
            n += 1
    return n


def fresh(publisher=None, mock_output=True):
    """一把干净的心跳表：set_key() 在第一次 tick 之前会拿模块级 perf_counter 落戳，
    不预置的话后面注入时钟的 tick 会被永久挡在门外（这本身是既有行为）。"""
    CLOCK.t = 0.0
    br = KeyboardControlBridge(publisher, mock_output=mock_output)
    br.last_send_at = 0.0
    br._next_send_at = None
    br._pacing_interval = 0.0
    br._tick_now = None
    return br


# ---------------------------------------------------------------------------
header("1) 斜向驾驶：W+D 必须归一化到单位圆（build_command 的 length > 1.0 分支）")
# 探针事实：W 单独 -> vx=1.0；W+D -> vx=vy=0.7071067811865475；四键同按 -> (0,0,0)。
# 探针事实：Q 单独 -> wz=-1.0，E 单独 -> wz=1.0（偏航**不**参与归一化）。
# 变异 M01（把 `if length > 1.0:` 改成 `if False:`）改坏后本节必须红。
br = fresh()
FACTS = {}


def vxyz(keys):
    for k in C.CONTROL_KEYS:
        br.key_state[k] = k in keys
    c = br.build_command(STATE, REG)
    return (c.vx, c.vy, c.wz)


FACTS["W"] = vxyz({"W"})
FACTS["W+D"] = vxyz({"W", "D"})
FACTS["W+D+A+S"] = vxyz({"W", "D", "A", "S"})
FACTS["Q"] = vxyz({"Q"})
FACTS["E"] = vxyz({"E"})
FACTS["W+D+Q+E"] = vxyz({"W", "D", "Q", "E"})

half = 1.0 / math.sqrt(2.0)
check("单轴不动归一化：W 单独 = vx 1.0 / vy 0.0", FACTS["W"] == (1.0, 0.0, 0.0), str(FACTS["W"]))
check("斜向 W+D 两轴各 1/sqrt(2)（不是各 1.0）",
      all(abs(v - half) < 1e-12 for v in FACTS["W+D"][:2]) and FACTS["W+D"][2] == 0.0,
      str(FACTS["W+D"]))
check("斜向之后合速度仍然是 1.0（不许超发 41%）",
      abs(math.hypot(*FACTS["W+D"][:2]) - 1.0) < 1e-9,
      "%.12f" % math.hypot(*FACTS["W+D"][:2]))
check("W+D+Q+E：底盘被归一化、偏航不受影响",
      all(abs(v - half) < 1e-12 for v in FACTS["W+D+Q+E"][:2])
      and FACTS["W+D+Q+E"][2] == 0.0, str(FACTS["W+D+Q+E"]))
check("对顶键互相抵消成 0（length=0 时不许除）", FACTS["W+D+A+S"] == (0.0, 0.0, 0.0),
      str(FACTS["W+D+A+S"]))
check("偏航 Q/E 各 ±1.0（当前设计不归一化，此处钉住现状）",
      FACTS["Q"] == (0.0, 0.0, -1.0) and FACTS["E"] == (0.0, 0.0, 1.0),
      "Q=%s E=%s" % (FACTS["Q"], FACTS["E"]))

# ---------------------------------------------------------------------------
header("2) 注册表里没有 ChassisAssist/SpinMode 时必须回落到 RobotState")
# 探针事实：空注册表 -> (spin_mode False, speed_limit 420, power_limit 80)；
# 默认注册表 -> (True, 420, 70)。80 != 70，所以回退分支是可观测的。
empty = ModuleRegistry([])
c_empty = br.build_command(STATE, empty)
c_full = br.build_command(STATE, REG)
check("空注册表：spin_mode 假、限速/限功率回落到 RobotState",
      (c_empty.spin_mode, c_empty.speed_limit, c_empty.power_limit) == (False, 420, 80),
      "got %s / RobotState=%s" % ((c_empty.spin_mode, c_empty.speed_limit, c_empty.power_limit),
                                  (STATE.speed_limit, STATE.power_limit)))
check("默认注册表：限速取 Spin Speed、限功率取 ChassisAssist 的 Power Limit",
      (c_full.spin_mode, c_full.speed_limit, c_full.power_limit) == (True, 420, 70),
      str((c_full.spin_mode, c_full.speed_limit, c_full.power_limit)))
spin = REG.get("SpinMode")
spin.set_enabled(False)
try:
    c_off = br.build_command(STATE, REG)
    check("SpinMode 关掉：spin_mode 假，但限速仍取设置值（不是回落到 RobotState）",
          (c_off.spin_mode, c_off.speed_limit) == (False, 420),
          str((c_off.spin_mode, c_off.speed_limit)))
finally:
    spin.set_enabled(True)

# ---------------------------------------------------------------------------
header("3) 只按鼠标 / 只剩滚存位移：心跳必须仍然是 75Hz，不许掉到空闲 4Hz")
# 探针事实：按住鼠标键 1 秒 = 75 帧；什么都不做 = 4 帧（250ms 零帧）。
# 探针事实：C 路被 max_hz 挡下时增量滚存，松键之后仍只剩位移的那 1 秒 = 74 帧。
# 变异 M04a（删掉三个鼠标键子句）/ M05（删 mouse_y）/ M06（删 mouse_z）后本节必须红。
ref = frames_in_1s(fresh(Pub(), mock_output=False))
check("基线：什么都不做 = 空闲 4Hz（24 秒口径的 1/6）", ref <= 6, "%d 帧 / 1 秒" % ref)
for btn, label in ((1, "左键"), (2, "中键"), (3, "右键")):
    b = fresh(Pub(), mock_output=False)
    b.set_mouse_button(btn, True, STATE, REG)
    got = frames_in_1s(b)
    check("只按住鼠标%s（没按任何键盘键）仍走 75Hz 心跳" % label, got >= 60,
          "%d 帧 / 1 秒（掉到 4Hz 的话是 %d 帧）" % (got, ref))


class LimitedLink:
    """C 路被 max_hz 挡下：这一帧的鼠标增量必须滚存到下一帧。"""

    def __init__(self):
        self.frames = []
        self.last_send_dropped = False
        self.last_send_rate_limited = False

    def send(self, **kw):
        self.frames.append(kw)
        self.last_send_rate_limited = True
        return False, "DT7 max_hz 挡下"


def motion_only_after_release(setup, label):
    b = fresh(Pub(), mock_output=False)
    b.dt7_link = LimitedLink()
    b.dt7_enabled = True
    b.dt7_key_mask = 0xFFFF
    b.set_key("W", True, STATE, REG)
    setup(b)
    for i in range(1, 4):
        CLOCK.t = i * 0.01
        b.tick(STATE, REG, CLOCK.t)
    b.set_key("W", False, STATE, REG)             # 手松开：键全空，只剩滚存位移
    CLOCK.t = 0.04
    b.send_command(b.build_command(STATE, REG))
    retained = (b.mouse_x, b.mouse_y, b.mouse_z)
    return retained, frames_in_1s(b)


for setup, label, axis in (
    (lambda b: b.add_mouse_motion(40.0, 0.0), "横向位移", 0),
    (lambda b: b.add_mouse_motion(0.0, 40.0), "纵向位移", 1),
    (lambda b: b.add_wheel(60.0), "滚轮增量", 2),
):
    retained, got = motion_only_after_release(setup, label)
    check("C 路限流把%s滚存下来之后，心跳仍是 75Hz" % label,
          retained[axis] != 0 and got >= 60,
          "滚存 %s -> %d 帧 / 1 秒" % (str(retained), got))

# ---------------------------------------------------------------------------
header("4) set_key / set_mouse_button 的入口守卫")
# 探针事实：set_key("NOPE", True) -> None，seq 不动，key_state 不长出该键；
# set_key("w", ...) 会先 upper() 所以认得；set_mouse_button(4/0) -> None；
# 同一个键重复按第二次 -> None。
# 变异 M09（删 `key not in CONTROL_KEYS`）/ M11（删 set_mouse_button 守卫）后本节必须红。
b = fresh()
check("未知协议槽位返回 None（不建帧）", b.set_key("NOPE", True, STATE, REG) is None)
check("未知协议槽位不产生 seq（一条帧都不许多发）", b.seq == 0, "seq=%d" % b.seq)
check("未知协议槽位不进 key_state（不许污染按键表）", "NOPE" not in b.key_state)
check("小写槽位名先归一化再认（W 槽）",
      (b.set_key("w", True, STATE, REG) or b.last_command).keyboard_value == W)
check("同一槽位重复按同一个方向是空操作（不许刷帧）",
      b.set_key("W", True, STATE, REG) is None and b.seq == 1, "seq=%d" % b.seq)

b = fresh()
check("非法鼠标键号（4）返回 None", b.set_mouse_button(4, True, STATE, REG) is None)
check("非法鼠标键号（0）返回 None", b.set_mouse_button(0, True, STATE, REG) is None)
check("非法键号不改变任何按钮状态、不发帧",
      (b.left_button_down, b.mid_button_down, b.right_button_down) == (False, False, False)
      and b.seq == 0, "seq=%d" % b.seq)
check("合法键号 1 = 左键", b.set_mouse_button(1, True, STATE, REG) is not None)
check("左键重复按下是空操作", b.set_mouse_button(1, True, STATE, REG) is None)
check("2 = 中键 / 3 = 右键（键号不许串台）",
      b.set_mouse_button(2, True, STATE, REG) is not None
      and b.set_mouse_button(3, True, STATE, REG) is not None
      and (b.left_button_down, b.mid_button_down, b.right_button_down) == (True, True, True))
check("resolve_slot('') / 空白 / None 都返回空元组",
      b.resolve_slot("") == () and b.resolve_slot("   ") == () and b.resolve_slot(None) == ())
check("C 路默认掩码只放行驾驶四键（不许把开火键带出去）",
      DT7_DEFAULT_KEY_MASK == sum(1 << KEYBOARD_BITS[k] for k in "WSAD"),
      hex(DT7_DEFAULT_KEY_MASK))

# ---------------------------------------------------------------------------
header("5) 回读样本里的 -1 / None = 读不到，必须被滤掉而不是当成结论")
# 探针事实：
#   selftest([-1,-1,-1]) -> NO-READBACK；selftest([None,None]) -> NO-READBACK
#   selftest([x,-1,None]) -> PASS；selftest([-1,0,0]) -> NO-CHANGE
#   hold([-1]*8) -> NO-READBACK；hold([None]*8) -> NO-READBACK；hold([0]*8) -> EXPIRES
# 滤掉之后 -1 会让判据翻脸：selftest 报 PARTIAL（冤枉载荷错位），
# hold 报 STUCK（冤枉 DT7 粘键 —— 现场会让驾驶员去换遥控器）。
# 变异 M07（`>= 0` 改 `>= -99`）/ M08 同款后本节必须红。
v, d = C.evaluate_dt7_selftest([-1, -1, -1], X, 0)
check("全程 -1 回读 -> NO-READBACK（不是 PARTIAL）", v == "NO-READBACK", v)
check("NO-READBACK 的原因要指向 C 板 USB 0x5A（现场照着做）", "0x5A" in d, d)
check("全程 None 回读 -> NO-READBACK",
      C.evaluate_dt7_selftest([None, None], X, 0)[0] == "NO-READBACK")
check("-1/None 与真值混在一起时，真值仍然判得出来",
      C.evaluate_dt7_selftest([X, -1, None], X, 0)[0] == "PASS")
check("只有 -1 和基线时判 NO-CHANGE（DT7 没理这一帧）",
      C.evaluate_dt7_selftest([-1, 0, 0], X, 0)[0] == "NO-CHANGE")
check("全程 -1 的粘键判据 -> NO-READBACK（不许判成 STUCK）",
      C.evaluate_dt7_hold([-1] * 8, X)[0] == "NO-READBACK")
check("全程 None 的粘键判据 -> NO-READBACK",
      C.evaluate_dt7_hold([None] * 8, X)[0] == "NO-READBACK")
check("NO-READBACK 明确写着「不许当成安全」（不许被读成 EXPIRES）",
      "不许当成安全" in C.evaluate_dt7_hold([-1] * 8, X)[1])
check("全 0 的稳态判 EXPIRES（安全侧）",
      C.evaluate_dt7_hold([0] * 8, X)[0] == "EXPIRES")
check("前 3/12 停着、后 9/12 掉回 -> EXPIRES（判据看的是稳态不是历史）",
      C.evaluate_dt7_hold([X] * 3 + [0] * 9, X)[0] == "EXPIRES")
check("最后四分之一仍停着 -> STUCK（真粘键必须报出来）",
      C.evaluate_dt7_hold([0] * 7 + [X], X)[0] == "STUCK")

# ---------------------------------------------------------------------------
header("6) 三条出口的失败必须被记下来（异常不许吞、拒绝不许瞒）")
# 探针事实（全部来自 facts.py 的 F1/F2/F4/F5/F6/F7 段）：
#   dt7_link.send 抛异常 + 中立帧 -> dt7_last_error 是原话、dt7_release_pending=True、
#                                主通道 last_error 仍是 ""；
#   dt7_link.send 抛异常 + 还按着 W -> dt7_release_pending=False；
#   send 返回 (False, why) 且 last_send_dropped=True 的中立帧 -> pending=True；
#   release() 返回 (False, why) -> dt7_last_error=why；
#   cc send_keymouse 返回 (False, why) -> cc_last_error=why、cc_sent 不涨、dropped 记下；
#   publish_km_control 抛异常 -> radio_km_last_error=原话、主通道照常发、sent 不涨。
# 变异 M03/M12/M13/M14/M19/M22/M23 后本节必须红。
class SpyLink:
    def __init__(self, send=None, release=None, dropped=False):
        self.sent, self.released = [], 0
        self.last_send_dropped = dropped
        self.last_send_rate_limited = False
        self._send, self._release = send, release

    def send(self, **kw):
        if self._send is not None:
            return self._send(kw)
        self.sent.append(kw)
        return True, ""

    def release(self, now=None):
        self.released += 1
        if self._release is not None:
            return self._release()
        return True, ""


def boom(exc):
    def _f(*_a, **_k):
        raise exc
    return _f


def dt7_bridge(**kw):
    b = fresh(**kw)
    b.dt7_enabled = True
    b.dt7_key_mask = 0xFFFF
    return b


def cmd(seq, **kw):
    return ChassisCommand(seq=seq, timestamp=0.0, vx=0.0, vy=0.0, wz=0.0, spin_mode=False,
                          speed_limit=0, power_limit=0, mock_output=kw.pop("mock_output", True),
                          **kw)


b = dt7_bridge()
b.dt7_link = SpyLink(send=boom(OSError("DT7 口被占用")))
b.send_command(cmd(1))
check("C 路写口抛异常：原因原样写进 dt7_last_error",
      "DT7 口被占用" in b.dt7_last_error, b.dt7_last_error)
check("C 路写口抛异常 + 中立帧 -> pending 立起来（下一拍必须补松手）",
      b.dt7_release_pending is True)
check("C 路异常不许污染 KMC 主通道的 last_error", b.last_error == "", repr(b.last_error))

b = dt7_bridge()
b.dt7_link = SpyLink(send=boom(OSError("DT7 口被占用")))
b.send_command(cmd(2, keyboard_value=W))
check("C 路异常 + 还按着键 -> 不立 pending（那一帧不是松手）",
      b.dt7_release_pending is False)
check("C 路异常 + 还按着键：原因照样写出来（现场要看）",
      "DT7 口被占用" in b.dt7_last_error)

b = dt7_bridge()
b.dt7_link = SpyLink(send=lambda kw: (False, "max_hz 挡下"))
b.send_command(cmd(3))
check("C 路被闸门拒绝（配置问题）不许立 pending（否则心跳白转）",
      b.dt7_release_pending is False)
check("C 路被拒绝：原因写进 dt7_last_error", b.dt7_last_error == "max_hz 挡下",
      b.dt7_last_error)
b = dt7_bridge()
b.dt7_link = SpyLink(send=lambda kw: (False, "max_hz 挡下"), dropped=True)
b.send_command(cmd(4))
check("C 路被 max_hz 挡下的中立帧必须立 pending（DT7 没有过期清零）",
      b.dt7_release_pending is True)

b = dt7_bridge()
b.dt7_link = SpyLink(release=lambda: (False, "闸门未过"))
b.release_direct_dt7()
check("release() 被拒：原因必须上脸（界面靠它说清为什么没松手）",
      b.dt7_last_error == "闸门未过", b.dt7_last_error)
b = dt7_bridge()
b.dt7_link = SpyLink(release=boom(OSError("release 炸了")))
b.release_direct_dt7()
check("release() 抛异常：异常不许吞掉", "release 炸了" in b.dt7_last_error, b.dt7_last_error)


class RejectCc:
    dropped_keys = ["Q"]

    def send_keymouse(self, **kw):
        return False, "命令号未定案"

    def release_all(self, now=None):
        return False, "口被占用"


b = fresh()
b.cc_link, b.cc_enabled = RejectCc(), True
b.send_command(cmd(5, keyboard_value=W))
check("0x0306 被拒：原因写进 cc_last_error", b.cc_last_error == "命令号未定案", b.cc_last_error)
check("0x0306 被拒：cc_sent 不许涨", b.cc_sent == 0, str(b.cc_sent))
check("0x0306 被掩码挡掉的键要记下来（不许静默丢键）",
      b.cc_dropped_keys == ["Q"], str(b.cc_dropped_keys))
b.release_custom_controller()
check("0x0306 release_all 被拒：原因上脸", b.cc_last_error == "口被占用", b.cc_last_error)


class RaiseCc(RejectCc):
    def release_all(self, now=None):
        raise OSError("cc 炸了")


b = fresh()
b.cc_link, b.cc_enabled = RaiseCc(), True
b.release_custom_controller()
check("0x0306 release_all 抛异常：异常不许吞掉", "cc 炸了" in b.cc_last_error, b.cc_last_error)


class Raiser:
    safety = Armed()

    def publish_km_control(self, **kw):
        raise RuntimeError("broker 没连上")

    def publish_keyboard_mouse(self, *a, **k):
        return True, "ok"


pub = Raiser()
b = fresh(pub, mock_output=False)
b.radio_km_enabled = True
b.send_command(cmd(6, mock_output=False, keyboard_value=W))
check("0x0311 发布器抛异常：原因原样写进 radio_km_last_error",
      b.radio_km_last_error == "broker 没连上", b.radio_km_last_error)
check("0x0311 异常时 sent 不许涨", b.radio_km_sent == 0, str(b.radio_km_sent))
check("0x0311 异常不许污染 KMC 主通道的 last_error", b.last_error == "", repr(b.last_error))
check("0x0311 异常时 KMC 主通道照常发这一帧（出口异常不得影响主通道）",
      b.last_command.seq == 6, "seq=%d" % b.last_command.seq)


class Reject(Raiser):
    def publish_km_control(self, **kw):
        return False, "0x5A 不在线"


b = fresh(Reject(), mock_output=False)
b.radio_km_enabled = True
b.send_command(cmd(7, mock_output=False))
check("0x0311 被拒：原因写进 radio_km_last_error",
      b.radio_km_last_error == "0x5A 不在线", b.radio_km_last_error)
check("0x0311 被拒：sent 不涨", b.radio_km_sent == 0, str(b.radio_km_sent))
b = fresh(Reject(), mock_output=False)
b.radio_km_enabled = False
b.send_command(cmd(8, mock_output=False))
check("0x0311 开关关着：一帧都不许发、也不许写错误原因",
      b.radio_km_sent == 0 and b.radio_km_last_error == "", repr(b.radio_km_last_error))


class Bare:
    """连 publish_keyboard_mouse 都没有的发布器。"""


b = fresh(Bare(), mock_output=False)
b.add_mouse_motion(30.0, -12.0)
b.send_command(cmd(9, mock_output=False))
check("发布器缺 publish_keyboard_mouse：给出可读的错误，不许抛 AttributeError",
      b.last_error == "未连接 KeyboardMouseControl MQTT 发布器", repr(b.last_error))
check("发布器不可用时 _dirty 保持（下一拍继续重试，不许悄悄放弃）", b._dirty is True)
check("发布器不可用时鼠标增量照样消费（不许每拍重发同一份陈旧位移）",
      (b.mouse_x, b.mouse_y, b.mouse_z) == (0, 0, 0))

# ---------------------------------------------------------------------------
header("7) neutral_now：还带着位移或鼠标键的帧不算松手（pending 不许立）")
# 探针事实（facts.py F3 段，dt7_link 返回 (False, 挡) 且 last_send_dropped=True）：
#   纯中立 -> pending True；带 mouse_x / mouse_y / mouse_z / 左键 / 右键 / 中键
#   -> pending 全是 False。
# 这是本节存在的理由：DT7 那侧没有过期自动清零，但反过来，**还带着位移的帧
# 也不是松手** —— 把它当松手补发出去等于在松手瞬间多甩一段陈旧位移。
# 变异 M15a/M15b/M15c 后本节必须红。
for label, kw in (("纯中立", {}),
                  ("鼠标横向位移", {"mouse_x": 5}),
                  ("鼠标纵向位移", {"mouse_y": 5}),
                  ("滚轮增量", {"mouse_z": 5}),
                  ("左键还按着", {"left_button_down": True}),
                  ("右键还按着", {"right_button_down": True}),
                  ("中键还按着", {"mid_button_down": True})):
    link = SpyLink(send=lambda k: (False, "挡"), dropped=True)
    b = dt7_bridge()
    b.dt7_link = link
    b.send_command(cmd(10, **kw))
    want = (kw == {})
    check("neutral_now：%s 的帧 %s立 pending" % (label, "要" if want else "不"),
          b.dt7_release_pending is want, str(b.dt7_release_pending))

# ---------------------------------------------------------------------------
header("8) 出口开着但链路对象不在 / 暂停到期自愈")
# 探针事实（facts.py G1~G4 段）：
#   dt7_paused_until 已过期 -> 下一帧自愈成 False 并且真的写出去了；
#   dt7_paused_until 仍是 inf -> 保持暂停、一帧不写、pending 清掉；
#   dt7_enabled=True 但 dt7_link=None -> 无错、无 pending、主通道无错；
#   cc_enabled=True 但 cc_link=None -> 同样安静。
# 变异 M02（把暂停到期判成恒真）/ M24 / M25 后本节必须红。
link = SpyLink()
b = dt7_bridge()
b.dt7_link = link
b.dt7_send_paused = True
b.dt7_paused_until = 0.0                                   # 自检中途出事，已经到期
b.send_command(cmd(11, keyboard_value=W))
check("暂停到期后自检自己恢复（不许让 C 路从此一帧都不发）",
      b.dt7_send_paused is False)
check("自愈之后这一帧真的写出去了（不是只翻了个布尔）",
      [f["pressed_bits"] for f in link.sent] == [W], str(link.sent))

link = SpyLink()
b = dt7_bridge()
b.dt7_link = link
b.dt7_send_paused = True                                   # dt7_paused_until 仍是 inf
b.dt7_release_pending = True
b.send_command(cmd(12, keyboard_value=W))
check("暂停未到期：保持暂停、一帧都不写",
      b.dt7_send_paused is True and len(link.sent) == 0, str(link.sent))
check("暂停期间 pending 要清掉（心跳不许被它钉在 75Hz 空转）",
      b.dt7_release_pending is False)
b = fresh()
b.dt7_enabled = True                                      # dt7_link 保持 None
b.dt7_release_pending = True
b.send_command(cmd(13, keyboard_value=W))
check("C 路开关开着但链路对象没注入：安静跳过，不许报 AttributeError",
      b.dt7_last_error == "" and b.last_error == "",
      "%r / %r" % (b.dt7_last_error, b.last_error))
check("C 路出口没开时 pending 按『出口没开』同义清掉",
      b.dt7_release_pending is False)
b = fresh()
b.cc_enabled = True                                       # cc_link 保持 None
b.send_command(cmd(14, keyboard_value=W))
check("0x0306 开关开着但链路对象没注入：安静跳过，不许报 AttributeError",
      b.cc_last_error == "" and b.last_error == "",
      "%r / %r" % (b.cc_last_error, b.last_error))
check("0x0306 开着但没链路时不算键鼠来源冲突（线上一个字节都没写）",
      b.km_source_conflict == "", repr(b.km_source_conflict))

# ---------------------------------------------------------------------------
header("9) tick() 不传 now= 的公开默认，以及 _idle_zero_authorized 的几种 safety 形态")
# 探针事实（facts.py H1/H2 段）：tick(STATE, REG) 不传 now 也能发；
# _tick_now == last_send_at（落戳与门控同一把钟）；
# ⚠ 第 135 轮：这里原来钉的是 `True`，那是**记了实测到的现象**，不是有意之为。
#   原因：`control.py` 里上两行的阶段检查对同一类「读不到」是 fail-closed，
#   而这句是 fail-open —— **同一个函数里两种相反的答案**。
# safety 没有 is_fighting -> **False**（第 135 轮从 True 改过来）；
# is_fighting() False -> False；control_state 是裸字符串 -> True；
# LOCKED -> False；control_state None -> False。
# 变异 M18（删掉 hasattr 那一半）/ M20（删掉 now is None 的回退）/ M21 后本节必须红。
br = fresh()
CLOCK.t = 5.0                                   # 让"没落过戳"这件事看得见
out = br.tick(STATE, REG)
check("tick() 不传 now= 也能发一帧（公开 API 的默认值不许炸）", out is not None,
      "now 走的是模块级 perf_counter" if out is None else "")
check("不传 now= 时落戳用的是模块级 perf_counter，且门控/落戳同一把钟",
      br._tick_now is not None and br._tick_now == br.last_send_at == 5.0,
      "_tick_now=%r last_send_at=%r" % (br._tick_now, br.last_send_at))


class SafetyNoFight:
    class CS:
        value = "MATCH_ARMED"
    control_state = CS()


class SafetyFightFalse:
    class CS:
        value = "MATCH_ARMED"
    control_state = CS()

    def is_fighting(self):
        return False


class SafetyStrState:
    control_state = "MATCH_ARMED"


class SafetyLocked:
    class CS:
        value = "LOCKED"
    control_state = CS()

    def is_fighting(self):
        return True


class SafetyNone:
    control_state = None


for name, obj, want in (("safety 没有 is_fighting", SafetyNoFight(), False),
                        ("is_fighting() 为假（不在打仗阶段）", SafetyFightFalse(), False),
                        # ⚠ **第 135 轮从 True 改成 False。**
                        # 裸字符串的 `control_state` 能过第一道阶段检查（值就是 "MATCH_ARMED"），
                        # 但那个替身**同样没有 `is_fighting`** ——
                        # **所以它和上一行是同一个 fail-open 的两个受害者。**
                        ("control_state 是裸字符串（且没有 is_fighting）", SafetyStrState(), False),
                        ("control_state=LOCKED", SafetyLocked(), False),
                        ("control_state=None", SafetyNone(), False)):
    probe = KeyboardControlBridge(type("P", (), {"safety": obj})(), mock_output=False)
    check("_idle_zero_authorized：%s -> %s" % (name, want), probe._idle_zero_authorized() is want)
check("_idle_zero_authorized：mock 模式恒真（离线仿真不受闸门限制）",
      KeyboardControlBridge(mock_output=True)._idle_zero_authorized() is True)

print("\n" + ("control.py 未被走到的分支：全部达标" if FAILED == 0
              else str(FAILED) + " 条失败"))
sys.exit(1 if FAILED else 0)
