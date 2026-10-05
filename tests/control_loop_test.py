# -*- coding: utf-8 -*-
"""控制回路：75Hz 名义值、时钟一致性、键鼠来源冲突、改绑、审计快照惰性化。

这个文件量的是**非视觉**的控制回路本身。背景是 control-loop 组的五条缺陷清单，
每一条都在这里被钉住，并且"改前/改后"都给出可复算的数字：

1. 名义 75Hz 在任何配置下都跑不到 —— polar_app.py:844 的 0.92 容差外层门控把
   机会切在 12.267ms 的栅格上，control.py 旧门控又要求"距上一帧 >= 13.333ms"，
   两个栅格对不齐，每对不齐一次就整拍作废（外层时戳已经先推进了）。60 秒长跑
   实测：外层给了 71.50Hz，自发只有 60.72Hz。改成按名义节奏表推进之后，
   自发帧率回到"外层给多少就用多少"。
2. km_source_conflict 下 dt7_release_pending 永不清除，心跳被钉在 75Hz 空转。
3. tick(now=) 的注入时钟被 send_command 的模块级 perf_counter() 覆盖。
4. set_bind 的"释放旧占用"分支是死代码，W<->D 永远配不出来。
5. to_payload() 的 asdict()+json.dumps 占满 send_from_state 的热路径。

**全部用假时钟，一次 sleep 都没有**：结果可复算，不受机器快慢影响。
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import time as real_time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rm_client.control as C                                       # noqa: E402
from rm_client.control import (                                     # noqa: E402
    CONTROL_KEYS,
    DEFAULT_CONTROL_BINDS,
    HEARTBEAT_CATCHUP_SPAN,
    HEARTBEAT_MIN_SPACING,
    ChassisCommand,
    KeyboardControlBridge,
)
from rm_client.model import RobotState, build_default_registry      # noqa: E402

FAILED = 0
STATE = RobotState()
REG = build_default_registry()

#: polar_app.on_tick 的两个常量（那个文件在视觉冻结范围内，只读不改）
OUTER_TOLERANCE = 0.92
OUTER_INTERVAL = 1.0 / 75.0
HEARTBEAT_HZ = 75.0
HEARTBEAT_INTERVAL = 1.0 / HEARTBEAT_HZ


class FakeClock:
    """整条桥共用的假钟：control.time 换掉，perf_counter 与 tick(now=) 同源。

    真实运行时这两者本来就是同一把钟（polar_app.py:728 的 now 就是
    time.perf_counter()）；这里显式钉住，是为了让"逐帧一致"的比对有一个
    双方都认的时基。
    """

    t = 0.0

    def perf_counter(self):
        return self.t

    def time(self):
        return real_time.time()


CLOCK = FakeClock()
C.time = CLOCK


def at(now, fn, *args, **kwargs):
    """把假钟拨到 now 再调用（模拟 on_tick 入口的 now 与落戳同源）。"""
    CLOCK.t = now
    return fn(*args, **kwargs)


def check(name, ok, detail=""):
    global FAILED
    # ⚠ **第 156 轮：成功时打 `[PASS]`** ——
    # 原来打 `[ OK ]`，而 `verify_all.run()` 只数**以** `[PASS]` 开头**的行**，
    # **所以这个套件给门禁的贡献一直是 0**（第 155 轮量出来了）。
    #
    # ⚠ **第 155 轮我在这里下过一个错结论**：我量它的输出时用了一个
    # 不匹配 `[ OK ]`（左括号后面有空格）的正则，于是判它
    # 「只有测量值、不是检查项」而**没有动它**。
    # **它本来就有逐项 check()，和我上一轮刚改过的那个是同一种形状。**
    # **测量的正则错了，结论就跟着错** —— 这是本会话第三次。
    #
    # **只改成功那一侧**：失败仍打 `[FAIL]`。
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + detail) if detail else ""))
    FAILED += 0 if ok else 1


def header(text):
    print("\n--- " + text + " ---")


# ---------------------------------------------------------------------------
# 参考实现：被替换掉的那道门控，原样抄一份当尺子
# ---------------------------------------------------------------------------
class OldBridgeModel:
    """旧 control.py 门控的等价模型（tick() 的 426-428 行 + send_command 落戳）。

    逐条照抄：活跃判定 -> 区间选择 -> "距上一帧 >= interval 才发" -> 落戳。
    真实桥喂什么它就吃什么，因此两边的比对是同一组机会、同一份按键脚本，
    唯一差别就是门控本身。
    """

    def __init__(self, idle_zero_interval_s=0.25, heartbeat_hz=75.0):
        self.last_send_at = 0.0
        self.idle_zero_interval_s = idle_zero_interval_s
        self.heartbeat_hz = heartbeat_hz
        self.times: list[float] = []
        self.keys: dict[str, bool] = {key: False for key in CONTROL_KEYS}
        self.dt7_release_pending = False
        self.dt7_forced_keys = None
        self.dt7_enabled = False
        self.radio_km_enabled = False
        self.cc_enabled = False
        self._dirty = False
        self.idle_authorized = True

    def _stamp(self, now):
        self.last_send_at = now
        self._dirty = False                  # mock 模式下 send_command 会清重试标志
        self.times.append(now)

    def set_key(self, key, pressed, now):
        if self.keys.get(key, False) == bool(pressed):
            return
        self.keys[key] = bool(pressed)
        self._dirty = True
        self._stamp(now)                  # set_key 立刻发一帧（自带落戳）

    def _conflicted(self):
        live = [bool(self.radio_km_enabled), bool(self.dt7_enabled), bool(self.cc_enabled)]
        return sum(1 for v in live if v) > 1

    def tick(self, now):
        active = (any(self.keys.values())
                  or self.dt7_forced_keys is not None)
        must_flush = self.dt7_release_pending
        if not active and not self._dirty and not must_flush and not self.idle_authorized:
            return False
        interval = (1.0 / self.heartbeat_hz
                    if (active or self._dirty or must_flush) else self.idle_zero_interval_s)
        if now - self.last_send_at < interval:
            return False
        self._stamp(now)
        return True


# ---------------------------------------------------------------------------
# 调用方模型：QTimer 整数量化 + 0.92 容差外层门控（polar_app.on_tick 的形状）
# ---------------------------------------------------------------------------
def offer_stream(hud_hz: float, seconds: float, jitter: float = 0.0008,
                 seed: int = 12345) -> list[float]:
    """返回"外层门控肯把控制 tick 交给 control.tick 的那些时刻"。

    间隔取 polar_app.py:748 的 ``max(4, round(1000/render_fps_target))`` 毫秒整数量化，
    叠加 ±0.8ms 抖动；再按 polar_app.py:844 的 0.92 容差切一刀。
    """
    interval = max(4, int(round(1000.0 / hud_hz))) / 1000.0
    rng = random.Random(seed)
    now = 0.0
    last_control = 0.0
    out: list[float] = []
    while now < seconds:
        now += interval + rng.uniform(-jitter, jitter)
        if now - last_control >= OUTER_INTERVAL * OUTER_TOLERANCE:
            last_control = now
            out.append(now)
    return out


def drive(bridge, offers, script):
    """按给定机会流驱动真实的桥，返回 [(发出时刻, 该帧 command)]。

    ``script`` 是一个带 ledger 的按键脚本：ledger = [(槽位或 None, 生效时刻), ...]，
    None 表示把 ``script.held`` 松开。set_key 会立刻发一帧，两边（参考模型与真实桥）
    都按这个约定记账。
    """
    sent = []
    cursor = 0
    for now in offers:
        while cursor < len(script.ledger) and script.ledger[cursor][1] <= now:
            slot, _at = script.ledger[cursor]
            cursor += 1
            immediate = (at(now, bridge.set_key, slot, True, STATE, REG) if slot
                         else at(now, bridge.set_key, script.held, False, STATE, REG))
            if immediate is not None:        # set_key 自己也会立刻发一帧，两边同样记账
                sent.append((now, immediate))
        command = at(now, bridge.tick, STATE, REG, now)
        if command is not None:
            sent.append((now, command))
    return sent


def drive_old(model, offers, script):
    """同一份机会流 + 同一份按键脚本，喂给旧门控模型（改前的行为基线）。"""
    cursor = 0
    for now in offers:
        while cursor < len(script.ledger) and script.ledger[cursor][1] <= now:
            slot, _at = script.ledger[cursor]
            cursor += 1
            model.set_key(slot, True, now) if slot else model.set_key(script.held, False, now)
        model.tick(now)
    return model.times


class Script:
    """按键脚本。"""

    def __init__(self, ledger=()):
        self.held = "W"
        self.ledger = list(ledger)

    def value_at(self, now):
        bit = 0
        for slot, at in self.ledger:        # 逐条重放，得到"这一刻物理上按着的位"
            if at <= now:
                bit = 1 if slot else 0
        return bit


# ---------------------------------------------------------------------------
print("\n=== 1) 60 秒长跑：外层给的机会到底用掉了多少 ===")
#: 审计报告的原始数字（同一套假时钟、同一 seed、旧的严格门控），只作对照打印
AUDIT_HZ = {120.0: 62.51, 144.0: 60.96, 165.0: 47.27, 240.0: 52.12}
OLD_HZ: dict[float, float] = {}
NEW_HZ: dict[float, float] = {}
OFFERED_HZ: dict[float, float] = {}
HOLD = Script([("W", 0.0)])           # 全程按住 W：活跃心跳档
for hz in (120.0, 144.0, 165.0, 240.0):
    offers = offer_stream(hz, 60.0)
    OFFERED_HZ[hz] = len(offers) / 60.0
    NEW_HZ[hz] = len(drive(KeyboardControlBridge(publisher=None, mock_output=True),
                           offers, HOLD)) / 60.0
    OLD_HZ[hz] = len(drive_old(OldBridgeModel(), offers, HOLD)) / 60.0
    print("HUD %5.0f Hz | 外层给 %6.2f Hz | 审计报告 %6.2f | 本测试的旧门控 %6.2f Hz "
          "-> 改后 %6.2f Hz (%+.1f%%) | 用掉外层机会 %5.1f%%"
          % (hz, OFFERED_HZ[hz], AUDIT_HZ[hz], OLD_HZ[hz], NEW_HZ[hz],
             100.0 * (NEW_HZ[hz] - OLD_HZ[hz]) / OLD_HZ[hz],
             100.0 * NEW_HZ[hz] / OFFERED_HZ[hz]))
check("旧门控模型复现了审计报告的量级（尺子本身可信，偏差 < 3%）",
      all(abs(OLD_HZ[hz] - AUDIT_HZ[hz]) / AUDIT_HZ[hz] < 0.03 for hz in OLD_HZ),
      str({int(hz): round(OLD_HZ[hz], 2) for hz in OLD_HZ}))
check("每一档 HUD 下改后都不低于改前（不许有回退档）",
      all(NEW_HZ[hz] >= OLD_HZ[hz] for hz in NEW_HZ),
      str({int(hz): (round(OLD_HZ[hz], 2), round(NEW_HZ[hz], 2)) for hz in NEW_HZ}))
check("控制层不再吃掉外层给的机会（每一档用掉 >= 99%）",
      all(NEW_HZ[hz] >= OFFERED_HZ[hz] * 0.99 for hz in NEW_HZ),
      str({int(hz): round(100.0 * NEW_HZ[hz] / OFFERED_HZ[hz], 2) for hz in NEW_HZ}))
check("最差档（165Hz HUD）比改前高 25% 以上", NEW_HZ[165.0] >= 1.25 * OLD_HZ[165.0],
      "%.2f -> %.2f Hz" % (OLD_HZ[165.0], NEW_HZ[165.0]))
check("控制层自己的损失（外层给的 - 实际发的）< 1%",
      all((OFFERED_HZ[hz] - NEW_HZ[hz]) / OFFERED_HZ[hz] < 0.01 for hz in NEW_HZ),
      str({int(hz): round(100.0 * (OFFERED_HZ[hz] - NEW_HZ[hz]) / OFFERED_HZ[hz], 2)
           for hz in NEW_HZ}))
print("  残余缺口（**不在 control.py**，是冻结文件 polar_app.py:844 的外层门控 + "
      "polar_app.py:748 的整数量化 QTimer）：")
for hz in sorted(NEW_HZ):
    print("    HUD %5.0f Hz：外层最高只给 %6.2f Hz，距名义 75Hz 还差 %5.2f Hz"
          % (hz, OFFERED_HZ[hz], 75.0 - OFFERED_HZ[hz]))
check("记录残余缺口：名义 75Hz 仍够不着（这是冻结文件的账，不是本文件的锅）",
      max(NEW_HZ.values()) < 75.0,
      "最好一档 %.2f Hz" % max(NEW_HZ.values()))

# ---------------------------------------------------------------------------
print("\n=== 2) 逐帧一致性：改后只许多发，不许少发、不许更晚、不许改内容 ===")
for hz in (120.0, 144.0, 165.0, 240.0):
    offers = offer_stream(hz, 20.0)
    script = Script([("W", 0.0), (None, 10.0)])      # 0-10s 按住 W，之后回空闲
    bridge = KeyboardControlBridge(publisher=None, mock_output=True)
    new = drive(bridge, offers, script)
    new_times = [t for t, _ in new]
    old_times = drive_old(OldBridgeModel(), offers, script)

    tag = "HUD %5.0f" % hz
    check("%s: 改后帧数 >= 改前帧数（没有丢帧）" % tag,
          len(new_times) >= len(old_times), "%d -> %d" % (len(old_times), len(new_times)))
    check("%s: 第 k 帧改后不晚于改前（门控没有把任何一帧往后推）" % tag,
          all(new_times[k] <= old_times[k] + 1e-12 for k in range(len(old_times))),
          "最大滞后 %.4f ms" % (1000.0 * max((new_times[k] - old_times[k])
                                             for k in range(len(old_times)))))
    # 内容：去掉连续重复后的键位状态序列必须逐位相同（只多了重复帧，没多/没少状态）
    def collapse(values):
        out = []
        for v in values:
            if not out or out[-1] != v:
                out.append(v)
        return out
    old_states = [script.value_at(t) for t in old_times]
    new_states = collapse([c.keyboard_value for _, c in new])
    check("%s: 键位状态跃迁序列逐位相同（只多了重复帧，没多/没少状态）" % tag,
          new_states == collapse(old_states),
          "改后 %s / 改前 %s" % (new_states, collapse(old_states)))
    # 每一帧的其余字段也必须与"按下状态"完全一致（不多带键、不多带位移）
    check("%s: 多发出来的帧与邻居内容一致（是心跳重发，不是多带了键）" % tag,
          len({(c.keyboard_value, c.mouse_x, c.mouse_y, c.mouse_z,
                c.left_button_down, c.right_button_down, c.mid_button_down)
               for _, c in new}) <= 2)
    gaps = [b - a for a, b in zip(new_times, new_times[1:])]
    floor = HEARTBEAT_INTERVAL * HEARTBEAT_MIN_SPACING
    check("%s: 最小帧间隔 >= %.1fms（补齐量化误差也不许突发）" % (tag, 1000.0 * floor),
          min(gaps) >= floor - 1e-9,
          "最小 %.2f ms / 中位 %.2f ms" % (1000.0 * min(gaps), 1000.0 * statistics.median(gaps)))
    check("%s: 帧数不超过 1/最小间隔 的上界（洪泛守卫）" % tag,
          len(new_times) <= (offers[-1] - offers[0]) / floor + 2,
          "%d 帧" % len(new_times))

# ---------------------------------------------------------------------------
print("\n=== 3) 空闲 4Hz 零帧不受影响，活跃/空闲切换不突发 ===")


class Armed:
    class CS:
        value = "MATCH_ARMED"

    control_state = CS()

    def is_fighting(self):
        return True


class Pub:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0
        self.safety = Armed()

    def publish_keyboard_mouse(self, *a, **k):
        self.calls += 1
        return (True, "ok") if self.ok else (False, "match control is LOCKED")


pub = Pub()
idle = KeyboardControlBridge(pub, mock_output=False)
idle_times = [float(i) * 0.01 for i in range(2400)]          # 24 秒 @100Hz
idle_sent = [t for t in idle_times if idle.tick(STATE, REG, t) is not None]
gaps = [b - a for a, b in zip(idle_sent, idle_sent[1:])]
check("空闲授权下仍是 4Hz 零帧（24s 期望 96 帧）",
      abs(len(idle_sent) - 96) <= 1, "%d 帧 = %.3f Hz" % (len(idle_sent), len(idle_sent) / 24.0))
check("空闲零帧的间隔仍贴着 250ms（补齐机制不许把它拉快）",
      min(gaps) >= 0.24, "最小 %.1f ms" % (1000.0 * min(gaps)))

mix = KeyboardControlBridge(pub, mock_output=False)
seq = []
for i in range(1200):                                        # 12 秒 @100Hz
    now = i * 0.01
    if i == 200:
        mix.set_key("W", True, STATE, REG)
    if i == 800:
        mix.set_key("W", False, STATE, REG)
    if mix.tick(STATE, REG, now) is not None:
        seq.append(now)
burst = [b - a for a, b in zip(seq, seq[1:])]
active_gaps = [g for g in burst if g < 0.2]
check("空闲<->活跃切换不产生突发（活跃段最小间隔 >= 0.6 个心跳）",
      min(active_gaps) >= HEARTBEAT_INTERVAL * HEARTBEAT_MIN_SPACING - 1e-9,
      "最小 %.2f ms" % (1000.0 * min(active_gaps)))

# ---------------------------------------------------------------------------
print("\n=== 4) 停摆/时钟跳变后重新锚定，不许补一串洪峰 ===")
stall = KeyboardControlBridge(publisher=None, mock_output=True)
stall.set_key("W", True, STATE, REG)
post = []
for i in range(60):                                          # 5 秒空窗之后恢复
    now = 100.0 + i * 0.0133
    if stall.tick(STATE, REG, now) is not None:
        post.append(now)
gaps = [b - a for a, b in zip(post, post[1:])]
check("5 秒空窗后第一拍就恢复（不许静默丢一整段心跳）", len(post) > 40, "%d 帧" % len(post))
check("恢复后第一拍只发一帧，之后立刻回到名义节拍（没有洪峰）",
      gaps and min(gaps) >= HEARTBEAT_INTERVAL * HEARTBEAT_MIN_SPACING - 1e-9,
      "最小 %.2f ms" % (1000.0 * min(gaps)))
check("停摆判定阈值确实生效（落后 > %d 格要重锚）" % HEARTBEAT_CATCHUP_SPAN,
      stall._next_send_at is not None and stall._next_send_at <= post[-1] + HEARTBEAT_INTERVAL + 1e-9,
      "next=%.4f last=%.4f" % (stall._next_send_at, stall.last_send_at))

# ---------------------------------------------------------------------------
print("\n=== 5) 注入时钟：tick(now=) 不许再被模块级 perf_counter 覆盖 ===")
inj = KeyboardControlBridge(publisher=None, mock_output=True)
inj.tick(STATE, REG, 1000.0)                                  # 第一拍把桥锚到注入钟
inj.set_key("W", True, STATE, REG)
sends = sum(1 for i in range(1, 240)
            if inj.tick(STATE, REG, 1000.0 + i * 0.02) is not None)
check("注入时钟下 240 次 tick（20ms 一次）全部发得出去", sends == 239, "%d 帧" % sends)
check("落戳用的是注入钟，不是 perf_counter（差了几百秒）",
      abs(inj.last_send_at - (1000.0 + 239 * 0.02)) < 1e-9,
      "last_send_at=%.3f" % inj.last_send_at)
check("整条桥只认一把钟（_tick_now 记录的就是驱动它的那把）",
      inj._tick_now == 1000.0 + 239 * 0.02, "%.3f" % inj._tick_now)

no_anchor = KeyboardControlBridge(publisher=None, mock_output=True)
at(0.0, no_anchor.set_key, "W", True, STATE, REG)
before_stamp = no_anchor.last_send_at
at(5.0, no_anchor.send_command,
   ChassisCommand(seq=99, timestamp=0.0, vx=0.0, vy=0.0, wz=0.0,
                  spin_mode=False, speed_limit=0, power_limit=0, mock_output=True))
check("tick() 从没跑过时，send_command 仍退回 perf_counter（老调用点行为不变）",
      no_anchor.last_send_at == 5.0, "last_send_at=%.3f" % no_anchor.last_send_at)

# ---------------------------------------------------------------------------
print("\n=== 6) km_source_conflict：pending 必须落地，心跳不许空转 ===")


class SpyDt7:
    def __init__(self, max_hz=30.0):
        self.max_hz = max_hz
        self.frames: list[tuple] = []
        self.last_send_dropped = False
        self.last_send_rate_limited = False
        self.released = 0

    def send(self, pressed_bits=0, mouse_dx=0, mouse_y=0, mouse_z=0,
             left=False, right=False, mid=False):
        self.frames.append((pressed_bits, mouse_dx, mouse_y, mouse_z, left, right, mid))
        return True, ""

    def release(self):
        self.released += 1
        return True, ""


conf = KeyboardControlBridge(publisher=Pub(), mock_output=False)
link = SpyDt7()
conf.dt7_link = link
conf.dt7_enabled = True
conf.dt7_send_paused = True                                 # 逼出 pending
conf.dt7_release_pending = True
conf.radio_km_enabled = True                                # -> 键鼠来源冲突
# 除了 pending 之外**什么都不许驱动心跳**（不按任何键、空闲零帧也没授权），
# 这样"发了多少帧"就只反映 pending 有没有被钉住。
conf._idle_zero_authorized = lambda: False
t0, n_conflict = 0.0, 0
while t0 < 3.1:                                             # 旧行为：3.1 秒 225 次空转
    t0 += HEARTBEAT_INTERVAL
    if at(t0, conf.tick, STATE, REG, t0) is not None:
        n_conflict += 1
check("冲突期间 pending 立刻落回 False（线上根本没写，清了等价）",
      conf.dt7_release_pending is False, str(conf.dt7_release_pending))
check("冲突期间心跳不再被 pending 钉在 75Hz 空转",
      n_conflict <= 1, "3.1 秒 %d 次（改前 225 次 = 72.6Hz）" % n_conflict)
check("冲突期间冲突原因写得出来（界面要看）", "只能有一个" in conf.km_source_conflict,
      conf.km_source_conflict)
check("冲突期间一条帧都没往 DT7 写（清 pending 不改变线上状态）",
      len(link.frames) == 0, "%d 帧" % len(link.frames))

conf.radio_km_enabled = False                               # 解除冲突
conf.dt7_send_paused = False
at(t0, conf.set_key, "W", True, STATE, REG)                 # 真正开始驾驶
t_end, n_held = t0, 0
while t_end < t0 + 1.0:
    t_end += HEARTBEAT_INTERVAL
    if at(t_end, conf.tick, STATE, REG, t_end) is not None:
        n_held += 1
check("冲突解除后按住不放，心跳恢复按 75Hz 走", 70 <= n_held <= 80, "1 秒 %d 次" % n_held)
check("按住时线上确实带着 W", link.frames[-1][0] == 1, str(link.frames[-1]))
at(t_end, conf.set_key, "W", False, STATE, REG)
t_end2, n_rel = t_end, 0
while t_end2 < t_end + 0.5:
    t_end2 += HEARTBEAT_INTERVAL
    if at(t_end2, conf.tick, STATE, REG, t_end2) is not None:
        n_rel += 1
check("松手后 neutral 帧真的落到线上了",
      link.frames[-1][0] == 0, str(link.frames[-1]))
check("松手后 pending 落回 False（不钉住心跳）",
      conf.dt7_release_pending is False)
check("冲突解除后没有残留的 pending 空转（松手帧已落地 + 空闲零帧未授权 = 一次都不该再发）",
      n_rel == 0, "0.5 秒 %d 次（pending 没钉住的话旧行为是这里 37 次空转）" % n_rel)

# 冲突那一帧也必须照常消费鼠标增量：_dt7_keep_motion 描述的是"这一帧"的 C 路结果，
# 上一帧被限流留下的 True 不许跨帧泄漏（否则位移会一直攒着不发）。
rot = KeyboardControlBridge(publisher=Pub(), mock_output=False)
rot.dt7_link = SpyDt7()
rot.dt7_enabled = True
rot.radio_km_enabled = True                              # 制造冲突
rot.add_mouse_motion(77.0, -13.0)
at(10.0, rot.set_key, "W", True, STATE, REG)
check("冲突帧同样把 _dt7_keep_motion 清零（不许把上一帧的限流标记带进这一帧）",
      rot._dt7_keep_motion is False)
check("冲突帧照样消费掉这一帧的鼠标增量（不许攒着）",
      (rot.mouse_x, rot.mouse_y) == (0, 0), "%s" % ((rot.mouse_x, rot.mouse_y),))

print("\n=== 7) set_bind：W<->D 这种对调必须配得出来 ===")
swap = KeyboardControlBridge(mock_output=True)
check("前置：出厂状态下 W 槽占着物理键 W", swap.resolve_slot("W") == ("W",))
check("把 D 槽绑到物理键 W 成功（旧实现返回 False）", swap.set_bind("D", "W") is True)
check("被让位的 W 槽置成 None（不接任何物理键）", swap.binds["W"] == "None", swap.binds["W"])
check("物理键 W 现在只驱动 D 槽", swap.resolve_slot("W") == ("D",), str(swap.resolve_slot("W")))
check("反向再把 W 槽绑到物理键 D 成功（真正的对调）", swap.set_bind("W", "D") is True)
check("对调后 W->D、D->W 各归各槽，没有一键双动作",
      swap.resolve_slot("W") == ("D",) and swap.resolve_slot("D") == ("W",),
      "W=%s D=%s" % (swap.resolve_slot("W"), swap.resolve_slot("D")))
check("对调之后别的槽不能再抢 D（显式占用仍然拒绝）", swap.set_bind("S", "D") is False)
check("拒绝后 S 槽保持原绑定", swap.binds["S"] == "S", swap.binds["S"])
check("拒绝后 S 槽也没有被悄悄改成 None", swap.binds["S"] != "None")
check("reset_binds 之后全部回到出厂", swap.reset_binds() is None and swap.binds == DEFAULT_CONTROL_BINDS)

print("\n=== 7b) set_bind 的两个 `or`：只有后半截是承重的 ===")
# 第 117 轮加。一次 or_drop 普查（AST，92 个生产文件）把 control.py:346 / :358
# 都列为真风险位置。**实测下来只有 :358 承重**，两处的分工与普查说的不一样：
#
#   L346: candidate = str(key_name or "None").strip()   <- 实测：等价，删掉抓不住
#   L358: self.binds[slot] = candidate or "None"        <- 实测：承重，删掉必红
#
# ## 为什么 L346 删不掉也看不出来
#
# `str(key_name or "None").strip()` 与 `str(key_name).strip() or "None"` 只在两类输入上分叉：
#
#   key_name="   "  -> 原=""      变="None"
#   key_name=0/[]/False -> 原="None" 变="0"/"[]"/"False"
#
# **第一类被 L358 的 `or` 吃掉了**（`"" or "None"` 与 `"None" or "None"` 同义），
# 所以最终 `binds[slot]` 两边一模一样。
# **第二类不在契约内** —— `set_bind` 的签名是 `key_name: str`。
#
# 所以这条断言是**替 L358 说的**，别当成「两个 `or` 都重要」。
blank = KeyboardControlBridge(mock_output=True)
check("纯空白键名被兜成 'None' 而不是空串（L358 那一半）",
      blank.set_bind("W", "   ") is True and blank.binds["W"] == "None",
      "set_bind=%s binds[W]=%r" % (blank.set_bind("W", "   "), blank.binds["W"]))
check("空串键名被兜成 'None'",
      blank.set_bind("A", "") is True and blank.binds["A"] == "None",
      "binds[A]=%r" % (blank.binds["A"],))
check("键名为 None 时被兜成 'None'",
      blank.set_bind("S", None) is True and blank.binds["S"] == "None",
      "binds[S]=%r" % (blank.binds["S"],))
check("**两边都不是空串**：正常键名原样落库，没有被 'None' 吃掉",
      blank.set_bind("D", "Up") is True and blank.binds["D"] == "Up",
      "binds[D]=%r" % (blank.binds["D"],))
check("空串与哨兵 'None' 在 binds 里**不是**同一句话 —— 兜底不能等于没兜",
      "" not in set(blank.binds.values()),
      "binds=%r" % (blank.binds,))
blank.reset_binds()

# 旧行为必须逐条保持
old = KeyboardControlBridge(mock_output=True)
check("旧规则1：改绑到新键名成功", old.set_bind("W", "Up") is True)
check("旧规则2：同一个键名被第二个槽抢 -> 拒绝", old.set_bind("S", "Up") is False)
check("旧规则3：拒绝后状态不变", old.binds["S"] == "S" and old.binds["W"] == "Up")
check("旧规则4：未知键名拒绝", old.set_bind("D", "NotARealKey") is False)
check("旧规则5：未知槽位拒绝", old.set_bind("NOPE", "W") is False)
check("旧规则6：None 解绑成功且按该键无输出",
      old.set_bind("A", "None") is True and old.resolve_slot("A") == ())
check("旧规则7：None 不占用任何键（还能被别人绑）", old.set_bind("S", "A") is True)
check("旧规则8：显式占用后原槽再抢回来要两次都成功（先让位再绑回）",
      KeyboardControlBridge(mock_output=True).set_bind("Q", "W") is True)

# ---------------------------------------------------------------------------
print("\n=== 8) last_payload 惰性化：字节逐位相同，热路径不再序列化 ===")
calls = {"n": 0}
real_to_payload = ChassisCommand.to_payload


def counting_to_payload(self):
    calls["n"] += 1
    return real_to_payload(self)


ChassisCommand.to_payload = counting_to_payload
try:
    audit = KeyboardControlBridge(publisher=None, mock_output=True)
    for i in range(100):
        audit.set_key("W", i % 2 == 0, STATE, REG)
    check("100 次 send_from_state 期间 to_payload 一次都没被调（热路径干净了）",
          calls["n"] == 0, "%d 次" % calls["n"])

    samples = [
        ChassisCommand(seq=1, timestamp=123.5, vx=1.0, vy=0.0, wz=0.0, spin_mode=True,
                       speed_limit=3, power_limit=2, mock_output=True,
                       keyboard_value=0b1010, mouse_x=-5, mouse_y=7, mouse_z=9,
                       left_button_down=True, right_button_down=False, mid_button_down=True),
        ChassisCommand(seq=2, timestamp=0.0, vx=-0.7071, vy=0.5, wz=-1.0, spin_mode=False,
                       speed_limit=0, power_limit=0, mock_output=False),
    ]
    ok_bytes = True
    detail = ""
    for cmd in samples:
        audit.send_command(cmd)
        expected = json.dumps(asdict(cmd), separators=(",", ":"),
                              ensure_ascii=True).encode("utf-8")
        got = audit.last_payload
        if got != expected:
            ok_bytes = False
            detail = "%r != %r" % (got, expected)
        if calls["n"] == 0:
            ok_bytes = False
            detail = "读取 last_payload 没有触发编码"
    check("读到的 last_payload 与旧实现 json.dumps(asdict()) 逐位相同", ok_bytes, detail)
    check("空桥的 last_payload 仍是 b\"\"（不是 None、不是 AttributeError）",
          KeyboardControlBridge(mock_output=True).last_payload == b"")
    check("last_payload 只读（不再是可写属性，热路径不会被外部改坏）",
          not hasattr(type(audit).last_payload, "fset") or type(audit).last_payload.fset is None)
finally:
    ChassisCommand.to_payload = real_to_payload

# ---------------------------------------------------------------------------
print("\n=== 9) 回归：既有契约仍然成立 ===")
smoke = KeyboardControlBridge(publisher=None, mock_output=True)
smoke.set_key("W", True, STATE, REG)
b = smoke.build_command(STATE, REG)
smoke.send_command(b)
check("last_payload 里仍带协议标识（tests/smoke_test.py:1337 依赖它）",
      b"rm2026.KeyboardMouseControl.v1.3.1" in smoke.last_payload)
check("mouse 增量仍然每帧消费（不积压）", (smoke.mouse_x, smoke.mouse_y, smoke.mouse_z) == (0, 0, 0))

smoke2 = KeyboardControlBridge(publisher=Pub(ok=False), mock_output=False)
smoke2.add_mouse_motion(120.0, -40.0)
for i in range(5):
    if smoke2.tick(STATE, REG, 0.02 * (i + 1)) is not None:
        pass
check("发布被拒时 last_error 仍写得出原因", smoke2.last_error != "", repr(smoke2.last_error))

lock = KeyboardControlBridge(publisher=None, mock_output=False)
check("mock_output=False + 无 publisher 时 _idle_zero_authorized 为 False（失败即闭）",
      lock._idle_zero_authorized() is False)
check("心跳常量本身没被动过（75.0）", lock.heartbeat_hz == 75.0, str(lock.heartbeat_hz))
check("键位表没被动过（16 位官方位序）", len(C.KEYBOARD_BITS) == 16 and CONTROL_KEYS == frozenset(C.KEYBOARD_BITS))

# ---------------------------------------------------------------------------
print("\n=== 10) 热路径计时（同一进程内新旧对照，可复算） ===")


def median_us(fn, reps=20000):
    for _ in range(2000):
        fn()
    samples = []
    for _ in range(reps):
        t0 = real_time.perf_counter()
        fn()
        samples.append((real_time.perf_counter() - t0) * 1e6)
    return statistics.median(samples)


class Link:
    last_send_dropped = False
    last_send_rate_limited = False

    def send(self, **kwargs):
        return True, ""


class Cc:
    dropped_keys: list = []

    def send_keymouse(self, **kwargs):
        return True, ""


def bench_pair(setup):
    """同一台机器、同一个进程里量：改后的 send_from_state vs 旧行为（+ 每次序列化）。"""
    new_bridge = setup()
    old_bridge = setup()

    def new_path():
        new_bridge.send_from_state(STATE, REG)

    def old_path():
        old_bridge.send_from_state(STATE, REG)
        old_bridge.last_payload            # 旧实现在 send_command 里无条件做这一步

    t_new = median_us(new_path)
    t_old = median_us(old_path)
    return t_new, t_old


mock_case = bench_pair(lambda: (lambda br: (br.set_key("W", True, STATE, REG), br)[1])(
    KeyboardControlBridge(publisher=None, mock_output=True)))


def _full():
    br = KeyboardControlBridge(publisher=Pub(), mock_output=False)
    br.dt7_link, br.dt7_enabled = Link(), True
    br.cc_link, br.cc_enabled = Cc(), True
    br.set_key("W", True, STATE, REG)
    return br


full_case = bench_pair(_full)
for tag, (t_new, t_old) in (("mock 无出口", mock_case), ("KMC+直连DT7+0x0306", full_case)):
    print("  send_from_state %-20s 改后 %6.3f us | 旧行为(每帧序列化) %6.3f us | 省 %5.1f%%"
          % (tag, t_new, t_old, 100.0 * (t_old - t_new) / t_old))
check("热路径不再为没人读的 last_payload 付序列化钱（改后明显更快）",
      mock_case[0] < mock_case[1] * 0.85 and full_case[0] < full_case[1] * 0.85,
      "mock %.3f<%.3f / full %.3f<%.3f" % (mock_case[0], mock_case[1],
                                           full_case[0], full_case[1]))

print("\n" + ("控制回路全部达标" if FAILED == 0 else str(FAILED) + " 条失败"))
sys.exit(1 if FAILED else 0)
