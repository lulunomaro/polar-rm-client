#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把子代理复查 + 自己复核抓出来的坑钉成断言。

1) 载荷最后那个 reserved u16 不是"惰性填充"：它在 DBUS 里落在 bytes 16-17，
   固件按第 5 通道解（remote_control.c:711，再减中值 1024）。填 0 = 滚轮满偏。
2) 心跳 75Hz、DT7 出口 max_hz=30Hz：被限流挡下的帧若把鼠标增量无条件清零，
   操作手的意图有约 2/3 凭空消失（表现=推了不动）。
3) MOUSE_SENSITIVITY 的出厂值/上一版默认值都不在可用量级（见 prepare_hero_build.py 注释）。
"""
from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from offline_policy import OfflineDt7Safety
from rm_client.backend import dt7_link as dl                        # noqa: E402
from rm_client.control import ChassisCommand, KeyboardControlBridge  # noqa: E402

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    print(("[ OK ] " if ok else "[FAIL] ") + name + (("  " + detail) if detail else ""))
    FAILED += 0 if ok else 1


def cmd(**kw):
    base = dict(seq=1, timestamp=0.0, vx=0.0, vy=0.0, wz=0.0, spin_mode=False,
                speed_limit=0, power_limit=0, mock_output=0)
    base.update(kw)
    return ChassisCommand(**base)


class FakeLink:
    """只暴露 control.py 会碰的那几样：send() 与 last_send_rate_limited。"""

    def __init__(self, *, ok, rate_limited):
        self.ok = ok
        self._rate = rate_limited
        self.last_send_rate_limited = False
        self.calls = 0

    def send(self, **kw):
        self.calls += 1
        self.last_send_rate_limited = self._rate
        return self.ok, "" if self.ok else "假链路拒绝"


def new_ctl(link):
    ctl = KeyboardControlBridge(publisher=None, mock_output=False)
    ctl.dt7_enabled = True
    ctl.dt7_link = link
    ctl.dt7_send_paused = False
    ctl.dt7_forced_keys = None
    ctl.dt7_key_mask = 0xFFFF
    return ctl


print("--- 1) reserved 落点 ---")
link = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="DT7", profile=dl.load_profile(), mock_output=True)
frame = link.encode({"mouse_x": 0, "mouse_y": 0, "mouse_z": 0, "left": False,
                     "right": False, "keyboard_value": 0})
dec = dl.decode_dt7_frame(frame)
check("自己编的帧能被自己的解码器认出来", dec is not None, str(len(frame)) + " 字节")
reserved = struct.unpack_from("<H", dec["payload"], 10)[0]
check("reserved 默认=通道中值 1024（写 0 会让车上 ch[4] 满偏）",
      reserved == 1024, "reserved=" + str(reserved))
p0 = {**dl.load_profile(), "reserved_value": 0}
again = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="DT7", profile=p0, mock_output=True).encode(
    {"mouse_x": 0, "mouse_y": 0, "mouse_z": 0, "left": False, "right": False,
     "keyboard_value": 0})
check("profile 里能显式改回 0（说明这是配置不是玄学）",
      struct.unpack_from("<H", dl.decode_dt7_frame(again)["payload"], 10)[0] == 0)

print("\n--- 2) 限流不许吃掉鼠标增量 ---")
kept = new_ctl(FakeLink(ok=False, rate_limited=True))
kept.add_mouse_motion(30, -20)
kept._send_direct_dt7(cmd(keyboard_value=0, mouse_x=30, mouse_y=-20, mouse_z=0))
check("被 max_hz 挡下：标记要立起来", kept._dt7_keep_motion is True)
kept._consume_relative_motion()
check("被 max_hz 挡下：增量滚存到下一帧（不清零）",
      kept.mouse_x == 30 and kept.mouse_y == 20, str(kept.mouse_x) + "," + str(kept.mouse_y))

gate = new_ctl(FakeLink(ok=False, rate_limited=False))
gate.add_mouse_motion(30, -20)
gate._send_direct_dt7(cmd(keyboard_value=0, mouse_x=30, mouse_y=-20, mouse_z=0))
check("闸门拒绝：不立标记（否则解锁瞬间甩陈旧位移）", gate._dt7_keep_motion is False)
gate._consume_relative_motion()
check("闸门拒绝：照旧清零", gate.mouse_x == 0 and gate.mouse_y == 0)
check("顺带钉住 Qt y 取反：向上推 dy<0 => mouse_y 为正（云台抬）",
      kept.mouse_y > 0, "mouse_y=" + str(kept.mouse_y))

sent = new_ctl(FakeLink(ok=True, rate_limited=False))
sent.add_mouse_motion(30, -20)
sent._send_direct_dt7(cmd(keyboard_value=0, mouse_x=30, mouse_y=-20, mouse_z=0))
sent._consume_relative_motion()
check("真的发出去了：清零，且计数+1",
      sent.mouse_x == 0 and sent.dt7_sent == 1, "sent=" + str(sent.dt7_sent))

print("\n--- 3) MOUSE_SENSITIVITY 量级 ---")
spec = importlib.util.spec_from_file_location(
    "prepare_hero_build", str(ROOT / "tools" / "firmware" / "prepare_hero_build.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sens = float(mod.DEFAULT_MOUSE_SENS)
check("默认值在可用量级（5000~50000）", 5000.0 <= sens <= 50000.0,
      "DEFAULT_MOUSE_SENS=" + mod.DEFAULT_MOUSE_SENS)
# 云台角速度 = 1000 * (每帧像素) / S；每帧像素 = 手速 / 出口帧率(30Hz)
rate = 1000.0 * (1000.0 / 30.0) / sens
check("1000px/s 手速对应的角速度在 0.5~5 rad/s（能瞄又不甩飞）",
      0.5 <= rate <= 5.0, "{:.2f} rad/s".format(rate))
check("出厂 100000 慢到像没接上（<0.5rad/s），所以不能当默认",
      1000.0 * (1000.0 / 30.0) / 100000.0 < 0.5)
check("上一版误用的 1000 会甩飞（>20rad/s），默认值不许退回它",
      1000.0 * (1000.0 / 30.0) / 1000.0 > 20.0)

print("\n--- 4) 发布被拒时不许重发陈旧位移 ---")
# 现场最常见状态：没有裁判系统 -> 官方 MQTT 一直返回 (False, "match control is LOCKED")。
# 这一栏是相对量，只要有一次没被消费，之后每一帧都会把同一份位移重发给 DT7，
# 而固件按 mouse/S 每 1ms 积分 —— 车上的表现是"手早就停了，云台还在自己转"。
import time  # noqa: E402
from rm_client.model import RobotState, build_default_registry  # noqa: E402

bus = []
prof2 = dict(dl.load_profile())
prof2["max_hz"] = 10000.0                     # 关掉限流，只看消费行为
link2 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="DT7", profile=prof2, mock_output=False,
                           write_bytes=lambda b: bus.append(bytes(b)))


class Refuse:
    def publish_keyboard_mouse(self, *a, **k):
        return False, "match control is LOCKED"


ctl2 = KeyboardControlBridge(Refuse(), mock_output=False)
ctl2.dt7_link = link2
ctl2.dt7_enabled = True
ctl2.dt7_key_mask = 0xFFFF
ctl2.dt7_send_paused = False
ctl2.add_mouse_motion(120.0, -40.0)
rstate, rreg = RobotState(), build_default_registry()
for _ in range(5):
    time.sleep(0.02)                          # 手已经停下：不再产生新的位移
    ctl2.tick(rstate, rreg, time.perf_counter())
sent = [struct.unpack_from("<h", f, 8)[0] for f in bus]
check("第一帧要带走这 120 像素", bool(sent) and sent[0] == 120, str(sent))
check("手停了之后不许再发非零 mouse_x（云台不能自转）",
      all(v == 0 for v in sent[1:]), str(sent))

print("\n--- 5) 真出口自己被限流时，也要能报出\"限流\"这个原因 ---")
# 第 2 节用的是假出口；这里换成真的 Dt7KeyMouseLink + 真 max_hz 闸，
# 否则"标记立起来了"只证明了我自己写的假对象。
bus2 = []
prof5 = dict(dl.load_profile())
prof5["max_hz"] = 30.0
clock = [1000.0]
link5 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="DT7", profile=prof5, mock_output=False,
                           write_bytes=bus2.append, time_fn=lambda: clock[0])
link5._next_due = 0.0
ok_a, _ = link5.send(pressed_bits=0, mouse_dx=10)
ok_b, why_b = link5.send(pressed_bits=0, mouse_dx=10)     # 同一个时隙里的第二帧
check("第一帧写成、第二帧被 30Hz 闸挡下", ok_a and not ok_b, why_b)
check("被挡下那次要明确是限流（不是闸门、不是写失败）",
      link5.last_send_rate_limited is True, str(link5.last_send_rate_limited))
clock[0] += 1.0                                           # 时隙到了
ok_c, why_c = link5.send(pressed_bits=0, mouse_dx=10)
check("过了一个周期就恢复发送，且限流标记要落回去",
      ok_c and link5.last_send_rate_limited is False, why_c)
link5._gate = lambda: "没勾 Allow Drive"        # 与 test_dt7_link.py 同一套打法
ok_d, why_d = link5.send(pressed_bits=0, mouse_dx=10)
check("闸门拒绝不许被当成限流（否则增量会被错误滚存）",
      (not ok_d) and link5.last_send_rate_limited is False, why_d)

print("\n--- 6) 锁定比赛控制必须给 DT7 补一帧松手，否则车带着 W 自己跑 ---")
# _clear_control_inputs_for_lock 由 safety.lock_control / 授权换 epoch / 启动即锁定
# 触发。它以前只清本地 + clear_pending()，而 clear_pending 恰好把 reset_inputs()
# 立起来的 _dirty 取消掉 —— 心跳从此一帧都不发。DT7 没有过期兜底，会把收到的
# 最后一帧（W 按着）重复到永远。实测就是这个形状：帧数 1 -> 1。
from rm_client.model import RobotState as _RS, build_default_registry as _bdr   # noqa: E402
from rm_client.ui.polar.mixins.input_dispatch_mixin import InputDispatchMixin    # noqa: E402
from rm_client.ui.polar.pointer_interaction import PointerInteraction           # noqa: E402

bus3 = []
prof6 = dict(dl.load_profile())
prof6["max_hz"] = 10000.0                       # 别用限流掩盖"根本没发"
link6 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="DT7", profile=prof6, mock_output=False,
                           write_bytes=bus3.append)
ctl6 = KeyboardControlBridge(publisher=None, mock_output=False)
ctl6.dt7_enabled = True
ctl6.dt7_link = link6
ctl6.dt7_key_mask = 0xFFFF
st6, reg6 = _RS(), _bdr()
ctl6.set_key("W", True, st6, reg6)
n_w = len(bus3)
check("按 W 确实发过一帧（前置条件）", n_w == 1, str(n_w))
keys_w = struct.unpack_from("<H", dl.decode_dt7_frame(bus3[-1])["payload"], 8)[0]
check("那一帧里 W 位是立着的", keys_w == 1, hex(keys_w))


class _FakeUI:
    pass


ui6 = _FakeUI()
ui6.control = ctl6
ui6.pointer_interaction = PointerInteraction(drag={"type": "slider"}, binding_module="RobotLink")
ui6._economy_qualification_token = object()
ui6._control_mouse_point = object()
ui6._combat_input_was_allowed = True
InputDispatchMixin._clear_control_inputs_for_lock(ui6, "match control armed")
check("锁定清零时补发了（帧数 +1）", len(bus3) == n_w + 1, f"{n_w} -> {len(bus3)}")
keys_after = struct.unpack_from("<H", dl.decode_dt7_frame(bus3[-1])["payload"], 8)[0]
check("补的那一帧键位是 0（车上那本键位账真的被清）", keys_after == 0, hex(keys_after))
check("本地键位也清了", ctl6.key_state["W"] is False)
check("旧输入代际的拖动和绑定也取消",
      ui6.pointer_interaction.drag is None and ui6.pointer_interaction.binding_module is None)

print("\n" + ("六个复查坑全部钉住" if FAILED == 0 else str(FAILED) + " 条失败"))
sys.exit(1 if FAILED else 0)
