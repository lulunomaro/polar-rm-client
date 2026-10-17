#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DT7 直连出口（C 路）离线断言。

规矩：**能离线证明的才断言；证不了的表现成"拒发"，不许假装能发。**
  * 帧字节型：对齐大疆 12.x 选手端反汇编现场
    （``SerialPortThread.OnSendInputStatus``，GameAssembly ``0x18027B486/48D/491/4F5/522/5BA/5EB``）
    —— ``5A | length | version | crc8 | seq(u16) | cmd(u16) | 12B 载荷 | crc16 LE``，总长 22；
  * CRC 族：DT7 用 ``CRCCheck``(0x77/0x1862)，与图传/裁判那族 ``CRCCheck_StudentSerialPort``
    (0xFF/0xFFFF) **不是一回事**，两套都要能出、能认、且互不认；
  * 交叉验收：student 族拿队内固件那份官方 CRC 代码认一遍；
  * 车端事实：18 字节 DBUS 的 mouse/key 字段固件一直在解
    （``application/other/remote_control.c:611-634``）⇒ C 路不需要改固件；
  * **仍属候选**：``0x2000`` 该读成命令号还是模块号（字节值确凿，归属是推断）。
"""
from __future__ import annotations

import json
import re
import struct
import sys
import types
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from offline_policy import OfflineDt7Safety
from rm_client.backend import dt7_link as dl          # noqa: E402
from rm_client.control import (ChassisCommand, KeyboardControlBridge,
                           DT7_DEFAULT_KEY_MASK as C_DT7_DEFAULT_KEY_MASK,
                           DT7_SPIN_KEY_MASK as C_DT7_SPIN_KEY_MASK)  # noqa: E402
from rm_client.protocol import crc16, crc8            # noqa: E402

FAILED = 0
SKIPPED = []

# 厂商现场复原出来的金向量：keys=0x11, mouse=(-45,120,7), left=1
GOLDEN = bytes.fromhex("5A10013600000020D3FF78000700010011000000CB50")
GOLDEN_KW = dict(pressed_bits=0x11, mouse_dx=-45, mouse_y=120, mouse_z=7, left=True)


def check(name, ok, detail=""):
    global FAILED
    print(f"{'[ OK ]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED += 1


def prof_of(**over):
    prof = dict(dl.DEFAULT_PROFILE)
    prof["payload_fields"] = list(dl.DEFAULT_PROFILE["payload_fields"])
    prof["_profile_error"] = ""
    prof.update(over)
    return prof


# ================================================= 1) DT7 帧字节型（对厂商现场）
def test_dt7_frame() -> None:
    print("\n--- 1) DT7 口那一帧：逐字节对齐反汇编现场 ---")
    payload = struct.pack("<hhhBBHH", -45, 120, 7, 1, 0, 0x11, 0)
    f = dl.build_frame(0x2000, payload, seq=0)
    check("总长 22（4 头 + 4 前缀 + 12 载荷 + 2 尾）", len(f) == 22, str(len(f)))
    check("字节 0 = 0x5A（厂商 mov byte [hdr],0x5a）", f[0] == 0x5A, hex(f[0]))
    check("字节 1 = length 0x10 = 前缀4 + 载荷12（含 cmd 那 2 字节，不含尾）",
          f[1] == 16, str(f[1]))
    check("字节 2 = protocol_version 1", f[2] == 1, str(f[2]))
    check("字节 3 = crc8 只覆盖 out[0..2]，初值 0x77",
          f[3] == dl._crc8_init(f[:3], 0x77) and f[3] != dl._crc8_init(f[:4], 0x77), hex(f[3]))
    check("out[4..5] = sequence_number u16（厂商这条恒 0）",
          struct.unpack_from("<H", f, 4)[0] == 0, f[4:6].hex(" "))
    check("out[6..7] = cmd u16 线值 0x2000（小端落进线缆是 00 20）",
          struct.unpack_from("<H", f, 6)[0] == 0x2000 and f[6:8] == bytes([0x00, 0x20]),
          f[6:8].hex(" "))
    check("out[8..19] = 12 字节载荷原样在后面", f[8:20] == payload, f[8:20].hex(" "))
    check("尾 2 字节 = crc16 覆盖 out[0..19]，小端，初值 0x1862",
          struct.unpack_from("<H", f, 20)[0] == dl._crc16_init(f[:20], 0x1862), f[20:22].hex(" "))
    check("金向量：与厂商字节序列复原结果一致", f == GOLDEN, f.hex().upper())
    d = dl.decode_dt7_frame(f)
    check("能解回来且族判定正确",
          d and d["variant"] == "dji_rc_v1" and d["cmd_wire"] == 0x2000
          and d["payload"] == payload and d["seq"] == 0 and d["protocol_version"] == 1, str(d))
    for desc, mutate in (
        ("改载荷一字节", lambda b: b.__setitem__(12, b[12] ^ 0x01)),
        ("改长度域", lambda b: b.__setitem__(1, b[1] + 1)),
        ("改 crc8", lambda b: b.__setitem__(3, b[3] ^ 0xFF)),
    ):
        t = bytearray(f); mutate(t)
        check(f"{desc} 之后必须解不出东西", dl.decode_dt7_frame(bytes(t)) is None)
    check("空帧/短帧不炸", dl.decode_dt7_frame(b"") is None and dl.decode_dt7_frame(b"\x5a\x10") is None)


# ================================================= 2) 两套帧与两套 CRC 不许混
def test_frame_and_crc_families() -> None:
    print("\n--- 2) A5(图传/裁判) 与 5A(DT7) 两族并存、互不认 ---")
    pay = bytes([1, 2, 3, 4])
    a5 = dl.build_frame(0x1100, pay, seq=9, frame_variant="a5_tail",
                        datalen_includes_cmd=False, crc_variant="student")
    check("a5_tail：datalen 不含 cmd（厂商 payload.Length-2）",
          struct.unpack_from("<H", a5, 1)[0] == 4, str(struct.unpack_from("<H", a5, 1)[0]))
    check("a5_tail：5 + 2 + 载荷 + 2", len(a5) == 5 + 2 + 4 + 2, str(len(a5)))
    check("a5 用 student 族解得开", dl.decode_dt7_frame(a5, crc_variant="student",
                                                  datalen_includes_cmd=False) is not None)
    check("a5 用 rc 族解不开（两族初值不同，别互相冒充）",
          dl.decode_dt7_frame(a5, crc_variant="rc_check",
                              datalen_includes_cmd=False) is None)
    check("DT7 帧用 student 族解不开", dl.decode_dt7_frame(GOLDEN, crc_variant="student") is None)
    no_tail = dl.build_frame(0x1100, bytes([1, 2]), seq=1, frame_variant="a5_no_tail",
                             crc_variant="student")
    check("frame_variant=a5_no_tail 真的没有尾 2 字节", len(no_tail) == 5 + 2 + 2, str(len(no_tail)))
    incl = dl.build_frame(0x1100, bytes([1, 2]), seq=1, frame_variant="a5_tail",
                          datalen_includes_cmd=True, crc_variant="student")
    check("datalen_includes_cmd=True 时长度域多算 2",
          struct.unpack_from("<H", incl, 1)[0] == 4, str(struct.unpack_from("<H", incl, 1)[0]))
    check("crc_variant=video 用第三族初值 0x77/0x3692",
          dl.build_frame(1, b"", 0, crc_variant="video")[4]
          == dl._crc8_init(dl.build_frame(1, b"", 0, crc_variant="video")[:4], 0x77))
    for data in (b"", b"\xa5", bytes(range(20))):
        check(f"student 族与裁判系统那份实现同源 len={len(data)}",
              dl._crc8_init(data, 0xFF) == crc8(data)
              and dl._crc16_init(data, 0xFFFF) == crc16(data))
    try:
        dl.build_frame(1, b"", 0, frame_variant="nope")
        check("未知 frame_variant 必须抛", False)
    except dl.Dt7ConfigError:
        check("未知 frame_variant 必须抛", True)


# ================================================= 3) 载荷结构
def test_payload_layout() -> None:
    print("\n--- 3) 载荷 = Input_Device_Status_RC（12 字节、无中键）---")
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of())
    lk._gate = lambda: ""
    frame = lk.encode({"mouse_x": -45, "mouse_y": 120, "mouse_z": 7,
                       "left": True, "right": False, "keyboard_value": 0x11})
    payload = dl.decode_dt7_frame(frame)["payload"]
    check("载荷 12 字节", len(payload) == 12, str(len(payload)))
    x, y, z, lb, rb, key, resv = struct.unpack("<hhhBBHH", payload)
    # reserved 不再填 0：它在 DBUS 里落在 bytes16-17，固件当 ch[4] 解（减中值 1024），
    # 填 0 = 左上角滚轮满偏。所以载荷里这两位必须是通道中值 1024。
    check("字段顺序/位宽对得上（含 reserved=通道中值 1024）",
          (x, y, z, lb, rb, key, resv) == (-45, 120, 7, 1, 0, 0x11, 1024), payload.hex(" "))
    check("键位是 uint16（16 个键，不是老八键）", key == 0x11)
    check("鼠标是有符号增量（int16 负值正常落进载荷）", x == -45)
    check("默认 profile 里就没有 mid 字段", "mid" not in dl.DEFAULT_PROFILE["payload_fields"])


# ================================================= 4) 拒发闸门
def test_refusals() -> None:
    print("\n--- 4) 配置说不通就拒发（宁可不动，也不骗人）---")
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(cmd_wire=None))
    ok, why = lk.send(pressed_bits=1)
    check("命令号空 -> 拒发并写明", not ok and "未定案" in why, f"{ok} {why}")
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(cmd_wire=0x2000, cmd_confirmed=False))
    ok, why = lk.send(pressed_bits=1)
    check("有值但未确认 -> 依然拒发", not ok and "未确认" in why, f"{ok} {why}")
    check("拒发的帧不计入 sent", lk.sent == 0 and lk.refused >= 1, f"{lk.sent}/{lk.refused}")
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(cmd_wire=0x2000, cmd_confirmed=False,
                                                        allow_experimental_cmd=True))
    check("试号工具显式放行时才肯发", lk.send(pressed_bits=1)[0] and "实验" in lk.last_note,
          lk.last_note)
    bad = prof_of(payload_layout="<hhhBB",
                  payload_fields=["mouse_x", "mouse_y", "mouse_z", "left", "right",
                                 "keyboard_value"])
    bad["_profile_error"] = ""
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=bad)
    ok, why = lk.send(pressed_bits=1, mouse_dx=1)
    check("字段数与格式符对不上 -> 拒发", (not ok) and "不匹配" in why, f"{ok} {why}")
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(
        payload_fields=["mouse_x", "mouse_y", "mouse_z", "left", "right", "keyboard_value",
                        "nonsense"]))
    check("未知字段名也要被拒", "未知载荷字段" in str(lk._gate()), lk._gate())
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(crc_variant="magic"))
    check("未知 crc 族名要带错误出场（不是静默退回）",
          "crc_variant" in str(lk._gate()), lk._gate())
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of())
    ok, _ = lk.send(pressed_bits=1, mid=True)
    check("中键没有落点：照发其它字段但把话说清楚（不能因中键把 WASD 拒了）",
          ok and "中键" in lk.last_note, f"{ok} {lk.last_note}")


# ================================================= 5) 节奏 / seq / 句柄
def test_rate_seq_handle() -> None:
    print("\n--- 5) 限流不吞时隙、seq 语义、串口句柄长持 ---")
    clock = {"t": 1000.0}
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(max_hz=30.0),
                            time_fn=lambda: clock["t"])
    lk._gate = lambda: ""
    ok1, _ = lk.send(pressed_bits=1)
    ok2, why2 = lk.send(pressed_bits=1)
    check("同一时刻第二帧被限流丢掉", not ok2 and "上限" in why2 and lk.dropped_rate == 1,
          f"{ok2} {why2}")
    clock["t"] += 1.0 / 30.0
    ok3, _ = lk.send(pressed_bits=2)
    check("被限流那帧不消耗时隙：过了一个周期照发", ok3 and lk.sent == 2, f"{ok3} {lk.sent}")
    check("厂商这条 seq 恒 0（seq_increments=false）", lk.seq == 0, str(lk.seq))
    inc = prof_of(seq_increments=True)
    lk2 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=inc)
    lk2._gate = lambda: ""
    f0 = lk2.encode({"mouse_x": 0, "mouse_y": 0, "mouse_z": 0, "left": False,
                     "right": False, "keyboard_value": 0})
    f1 = lk2.encode({"mouse_x": 0, "mouse_y": 0, "mouse_z": 0, "left": False,
                     "right": False, "keyboard_value": 0})
    check("seq_increments=true 时确实在递增（现场若发现要递增可一键切）",
          struct.unpack_from("<H", f0, 4)[0] == 0 and struct.unpack_from("<H", f1, 4)[0] == 1,
          f"{f0[4:6].hex()} {f1[4:6].hex()}")
    made = []

    class FakeSerial:
        def __init__(self, port, baud, **kw):
            self.port, self.baud, self.written, self.closed = port, baud, [], False
            made.append(self)

        def write(self, data):
            self.written.append(bytes(data))
            return len(data)

        def close(self):
            self.closed = True

    saved = sys.modules.get("serial")
    sys.modules["serial"] = types.ModuleType("serial")
    sys.modules["serial"].Serial = FakeSerial
    try:
        live = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(max_hz=200.0), mock_output=False,
                                  time_fn=lambda: clock["t"])
        live._gate = lambda: ""
        for i in range(3):
            live._next_due = 0.0
            clock["t"] += 1.0
            live.send(pressed_bits=i)
        check("连续三帧只开一次串口（旧版每帧开关一次，会被 DTR/RTS 与端口独占拖死）",
              len(made) == 1 and len(made[0].written) == 3,
              f"opens={len(made)}")
        check("打开的是厂商 RC 口的 961200", bool(made) and made[0].baud == 961200,
              str(made[0].baud if made else None))
        check("写出去的三帧都是 22 字节", all(len(w) == 22 for w in made[0].written))
        live.close()
        check("close() 真的关掉句柄", bool(made) and made[0].closed is True)
    finally:
        if saved is not None:
            sys.modules["serial"] = saved
        else:
            sys.modules.pop("serial", None)


# ================================================= 6) profile 文件
def test_profile_file() -> None:
    print("\n--- 6) 仓库 profile 与容错 ---")
    base = dl.load_profile()
    check("仓库 profile 存在", dl.PROFILE_FILE.exists(), str(dl.PROFILE_FILE))
    check("仓库 profile 自检通过（无错误、载荷 12 字节）",
          base["_profile_error"] == "" and base["_payload_size"] == 12, base["_profile_error"])
    check("仓库 profile 就是厂商那族：5A/dji_rc_v1/rc_check/0x2000/961200",
          (base["frame_variant"], base["crc_variant"], base["cmd_wire"], base["baudrate"],
           base["cmd_confirmed"]) == ("dji_rc_v1", "rc_check", 0x2000, 961200, True), str(base))
    check("仓库 profile 的默认载荷布局 = Input_Device_Status_RC",
          base["payload_layout"] == "<hhhBBHH" and "mid" not in base["payload_fields"])
    tmp = ROOT / "configs" / "_t_dt7_profile.json"
    try:
        tmp.write_text(json.dumps({"cmd_wire": 70}), encoding="utf-8")
        one = dl.load_profile(str(tmp))
        check("改 profile 就能换号（不改代码）", one["cmd_wire"] == 70)
        tmp.write_text(json.dumps({"cmd_wire": 70, "cmd_confirmed": False}), encoding="utf-8")
        check("确认是独立的显式开关：换号后没确认就不许真发",
              dl.load_profile(str(tmp))["cmd_confirmed"] is False)
        check("没写的字段保留默认", one["payload_layout"] == base["payload_layout"]
              and one["baudrate"] == base["baudrate"])
        tmp.write_text("{坏 json", encoding="utf-8")
        broken = dl.load_profile(str(tmp))
        check("profile 坏了：退回[未定案]并把错误带出来",
              broken["cmd_wire"] is None and "profile" in broken["_profile_error"].lower(),
              broken["_profile_error"])
        tmp.write_text(json.dumps({"cmd_wire": 99999, "baudrate": 7, "max_hz": 0,
                                   "protocol_version": 999}), encoding="utf-8")
        weird = dl.load_profile(str(tmp))
        check("越界值被拒收并留下原因",
              weird["baudrate"] == 961200 and weird["max_hz"] == 30.0
              and weird["protocol_version"] == 1 and "超 16 位" in weird["_profile_error"],
              weird["_profile_error"])
        tmp.write_text(json.dumps({"payload_layout": "<hhhBBH",
                                   "payload_fields": ["mouse_x", "mouse_y", "mouse_z", "left",
                                                      "right", "keyboard_value", "reserved"]}),
                       encoding="utf-8")
        check("布局与字段不一致在 load 阶段就报出来",
              "不匹配" in dl.load_profile(str(tmp))["_profile_error"])
    finally:
        tmp.unlink(missing_ok=True)


# ================================================= 7) 闸门 / 互斥 / 松手
class FakeLink:
    def __init__(self, profile=None):
        self.calls = []
        self.released = 0
        self.profile = profile if profile is not None else dl.DEFAULT_PROFILE
        self.port = "Stub"
        self.mock_output = True
        self.last_error = ""
        self.sent = 0

    def send(self, **kw):
        self.calls.append(kw)
        self.sent += 1
        return True, "ok"

    def release(self, now=None):
        self.released += 1
        return True, "ok"

    def close(self):
        pass


def test_bridge_gates_mutex() -> None:
    print("\n--- 7) 桥侧闸门 / 键鼠来源互斥 / 每条降级路径都松手 ---")
    import tempfile
    import rm_client.backend.manager as mgr_mod
    from rm_client.backend.manager import BackendManager
    from rm_client.control import (ChassisCommand, KeyboardControlBridge,
                           DT7_DEFAULT_KEY_MASK as C_DT7_DEFAULT_KEY_MASK,
                           DT7_SPIN_KEY_MASK as C_DT7_SPIN_KEY_MASK)
    from rm_client.model import RobotState, build_default_registry

    def cmd(seq=1, keys=0x11, mid=True):
        return ChassisCommand(seq=seq, timestamp=0.0, vx=1.0, vy=0.0, wz=0.0,
                              spin_mode=False, speed_limit=0, power_limit=0,
                              mock_output=False, keyboard_value=keys, mouse_x=-45,
                              mouse_y=120, mouse_z=7, left_button_down=True,
                              right_button_down=False, mid_button_down=mid)

    br = KeyboardControlBridge(publisher=None, mock_output=True)
    fake = FakeLink()
    br.dt7_link = fake
    check("默认不直连 DT7", br.dt7_enabled is False)
    br.send_command(cmd())
    check("开关关着时一帧都不发", fake.calls == [], str(fake.calls))
    br.dt7_enabled = True
    br.send_command(cmd())
    got = fake.calls[-1]
    check("默认掩码下只把驾驶键送出去（0x11 -> 0x01，SHIFT 要显式放行）",
          got["pressed_bits"] == 0x01 and (got["mouse_dx"], got["mouse_y"]) == (-45, 120),
          str(got))
    check("被挡掉的键要报名字", got["pressed_bits"] == 0x01
          and br.dt7_dropped_keys == ["SHIFT"], str(br.dt7_dropped_keys))
    br.dt7_key_mask = C_DT7_DEFAULT_KEY_MASK | C_DT7_SPIN_KEY_MASK
    br.send_command(cmd())
    check("显式放行 SHIFT 之后 0x11 原样透传（掩码不是死路，是可开关）",
          fake.calls[-1]["pressed_bits"] == 0x11, str(fake.calls[-1]))
    br.dt7_key_mask = C_DT7_DEFAULT_KEY_MASK
    br.release_direct_dt7()
    check("主动松手会发全零帧（DT7 没有过期兜底）", fake.released == 1, str(fake.released))

    br2 = KeyboardControlBridge(publisher=None, mock_output=True)
    dt7f, ccf = FakeLink(), FakeLink()
    br2.dt7_link, br2.cc_link = dt7f, ccf
    br2.dt7_enabled = True
    br2.cc_enabled = True
    br2.cc_key_mask = 0x1F
    br2.send_command(cmd())
    check("同开两条键鼠出口 -> 判冲突并写明是谁",
          "只能有一个" in br2.km_source_conflict and "0x0306" in br2.km_source_conflict,
          br2.km_source_conflict)
    check("冲突时谁都不发（两个写者抢同一个 DT7 字段比停发危险）",
          dt7f.calls == [] and ccf.calls == [], f"{dt7f.calls} {ccf.calls}")
    br2.cc_enabled = False
    br2.send_command(cmd())
    check("只留一条时恢复正常发送",
          br2.km_source_conflict == "" and len(dt7f.calls) == 1, br2.km_source_conflict)

    orig = mgr_mod.dt7_link.load_profile
    mgr_mod.dt7_link.load_profile = lambda path=None: prof_of()
    try:
        manager = BackendManager(workspace=tempfile.mkdtemp(prefix="dt7_gate_"),
                                scenario_key="OfficialEvent", client_id="3")
        registry = build_default_registry()
        state = RobotState()
        manager.state = state
        manager._apply_config(manager.config)
        module = registry.get("RobotLink")
        for name in ("Direct DT7 Egress", "DT7 Port", "DT7 Real TX"):
            check(f"RobotLink 有设置项 {name}", module.get_setting(name) is not None)
        check("直连默认关", bool(module.get_setting("Direct DT7 Egress").value) is False)
        check("真发闸默认关（不勾就绝不碰串口）",
              bool(module.get_setting("DT7 Real TX").value) is False)
        br3 = KeyboardControlBridge(publisher=None, mock_output=True)
        on = module.get_setting("Direct DT7 Egress")
        drive = module.get_setting("Allow Drive")
        module.enabled = True
        on.value = True
        drive.value = False
        manager.tick_polar_link(state, registry, br3)
        check("没 Allow Drive -> 不生效并把原因写脸上",
              br3.dt7_enabled is False and "未生效" in br3.dt7_last_error, br3.dt7_last_error)
        drive.value = True
        manager.tick_polar_link(state, registry, br3)
        check("三闸齐开 -> 生效", br3.dt7_enabled is True)
        check("没勾真发时句柄仍是 mock", br3.dt7_link.mock_output is True)
        link = br3.dt7_link
        module.get_setting("DT7 Real TX").value = True
        manager.tick_polar_link(state, registry, br3)
        check("勾了真发才切成非 mock", br3.dt7_link.mock_output is False
              and br3.dt7_link is not link)
        check("换真发闸不该每 tick 重建句柄",
              (manager.tick_polar_link(state, registry, br3), br3.dt7_link is manager._dt7_link)[1])
        module.get_setting("DT7 Real TX").value = False      # 回到 mock，测试绝不碰硬件
        retiring = br3.dt7_link
        manager.tick_polar_link(state, registry, br3)
        # Real writers retire on their owning worker, never by blocking a UI
        # tick. Wait only in this offline test, then process the next tick.
        if getattr(retiring, "asynchronous", False):
            check("异步写线程有界退出", retiring.wait_closed(2.0))
            manager.tick_polar_link(state, registry, br3)
        live = br3.dt7_link
        live._next_due = 0.0
        n0 = live.sent
        br3.send_command(cmd(mid=False))
        check("闸门全开时 C 路真的产帧（22 字节、走 mock 出口）",
              live.sent == n0 + 1 and len(live.last_frame) == 22, f"{live.sent} {live.last_error}")
        n1 = live.sent
        live._next_due = 0.0                      # 别被 30Hz 限流误判成"没发"
        br3.dt7_allow_fire = True  # test unsupported mid rather than fire authorization
        br3.send_command(cmd(mid=True))
        check("按中键时只丢中键、驾驶键照发",
              live.sent == n1 + 1 and "中键" in live.last_note, live.last_note)
        live._next_due = 0.0
        live.release()
        z = live.sent
        manager.safety.estop_active = True
        manager._dt7_was_enabled = True
        br3.dt7_enabled = True
        manager.tick_polar_link(state, registry, br3)
        check("急停一票否决", br3.dt7_enabled is False)
        # 松手帧只看前 10 字节（鼠标/左右键/键位）；末尾 reserved 恒为中值 1024
        check("急停补松手帧", live.sent == z + 1 and set(live.last_payload[:10]) == {0},
              live.last_payload.hex(" "))
        manager.safety.estop_active = False
        manager.tick_polar_link(state, registry, br3)        # 恢复一次，把 was_enabled 记成 True
        br3.dt7_enabled = True
        manager._dt7_was_enabled = True
        drive.value = False
        manager.tick_polar_link(state, registry, br3)
        check("撤 Allow Drive 也要补松手帧（旧代码这里只停发）",
              br3.dt7_enabled is False and live.sent >= z + 2 and set(live.last_payload[:10]) == {0},
              str(live.sent))
        on.value = False
        drive.value = True
        manager.tick_polar_link(state, registry, br3)        # 重建 link，was=False
        br3.dt7_link = live
        br3.dt7_enabled = True
        manager._dt7_was_enabled = True
        on.value = False
        manager.tick_polar_link(state, registry, br3)
        check("直接关掉开关也要补松手帧",
              br3.dt7_enabled is False and live.sent >= z + 3, str(live.sent))
    finally:
        mgr_mod.dt7_link.load_profile = orig


# ================================================= 8) 固件 CRC 交叉验收
def test_crc_vs_firmware() -> None:
    print("\n--- 8) student 族与队内固件那份官方 CRC 交叉验收 ---")
    import tempfile
    try:
        import test_cc_link as tc
        fw = Path(os.environ.get("POLAR_FIRMWARE_ROOT") or tc.DEFAULT_FIRMWARE).resolve()
        if not (fw / "components/support/CRC8_CRC16.c").exists():
            SKIPPED.append("没有固件树：CRC 交叉验收未跑")
            print("[SKIP] 没有固件树")
            return
        with tempfile.TemporaryDirectory() as td:
            exe = tc.build_crc_verifier(fw, td)
            if exe is None:
                SKIPPED.append("CRC 验收器编译失败")
                print("[SKIP] CRC 验收器编译失败")
                return
            frame = dl.build_frame(0x2000, struct.pack("<hhhBBHH", -45, 120, 7, 1, 0, 0x11, 0),
                                   seq=0, frame_variant="a5_tail",
                                   datalen_includes_cmd=False, crc_variant="student")
            v = tc.verify_with_firmware(exe, bytes(frame))
            check("**固件那份官方 CRC 认 student 族帧：CRC8 通过**", v["crc8_ok"] == 1, str(v))
            check("**固件那份官方 CRC 认 student 族帧：CRC16 通过**", v["crc16_ok"] == 1, str(v))
            bad = bytearray(frame); bad[7] ^= 0x01
            check("改一字节必须被固件 CRC16 拒掉",
                  tc.verify_with_firmware(exe, bytes(bad))["crc16_ok"] == 0)
            v2 = tc.verify_with_firmware(exe, bytes(GOLDEN))
            check("DT7 那帧**故意**不被固件族认（两族初值不同，别去[修正]它）",
                  v2["crc8_ok"] == 0 or v2["crc16_ok"] == 0, str(v2))
    except Exception as exc:                       # noqa: BLE001
        SKIPPED.append(f"CRC 交叉验收异常：{type(exc).__name__}: {exc}")
        print(f"[SKIP] CRC 交叉验收未跑：{type(exc).__name__}: {exc}")


def test_probe_and_runtime_agree() -> None:
    print("\n--- 9) 现场探针工具与运行期实现必须同构（两边各写一份最容易跑偏）---")
    try:
        import dt7_vcp_probe as vp
    except Exception as exc:                       # noqa: BLE001
        SKIPPED.append(f"探针工具导不进来：{exc}")
        print(f"[SKIP] 探针未比对：{exc}")
        return
    pay = struct.pack("<hhhBBHH", -45, 120, 7, 1, 0, 0x11, 0)
    runtime = dl.build_frame(0x2000, pay, seq=0)
    probe = vp.vendor_frame(0x11, (-45, 120, 7), left=1)
    check("dt7_vcp_probe.vendor_frame == 运行期 build_frame", probe == runtime,
          probe.hex(" ").upper())
    check("探针帧能被运行期解码器认出来", dl.decode_dt7_frame(probe) is not None)
    check("金向量与探针产物也一致", probe == GOLDEN, runtime.hex(" ").upper())


def test_link_recovery() -> None:
    print("\n--- 14) DT7 口掉线恢复（车上拔一次线就会撞）---")
    import types

    made = []

    class DeadAfter:
        def __init__(self, port, baud, **kw):
            self.port, self.baud, self.writes, self.closed = port, baud, 0, False
            made.append(self)

        def write(self, data):
            self.writes += 1
            if self.writes >= 2:
                raise OSError("设备已移除")
            self.last = bytes(data)
            return len(data)

        def close(self):
            self.closed = True

    fake = types.ModuleType("serial")
    fake.Serial = DeadAfter
    saved = sys.modules.get("serial")
    sys.modules["serial"] = fake
    try:
        lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(), mock_output=False)
        lk._gate = lambda: ""
        ok1, _ = lk.send(pressed_bits=1)          # 第 1 次写：成功
        check("第一帧正常写出去、并记住真在用的口",
              ok1 and lk.resolved_port == "COM9", f"{ok1} {lk.resolved_port}")
        dead = made[0]
        lk._next_due = 0.0
        ok2, why2 = lk.send(pressed_bits=1)       # 第 2 次写：假驱动在这里炸
        check("写失败这一帧报失败并说清原因", not ok2 and "丢弃" in why2, why2[:70])
        check("死句柄必须被丢掉并关掉（留着就会永久失效）",
              lk._handle is None and dead.closed is True, f"{lk._handle} {dead.closed}")
        check("resolved_port 清空，水印不会显示一个已经不存在的口",
              lk.resolved_port == "", lk.resolved_port)
        lk._next_due = 0.0
        lk._open_backoff_until = 0.0  # isolate recovery after backoff
        ok4, why4 = lk.send(pressed_bits=1)
        check("下一帧自动重开并恢复发送",
              ok4 and len(made) == 2 and lk.resolved_port == "COM9"
              and made[1].writes == 1, f"{ok4} {why4} opens={len(made)}")

        boom = types.ModuleType("serial")

        class AlwaysFail:
            def __init__(self, *a, **k):
                raise OSError("拒绝访问")

        boom.Serial = AlwaysFail
        sys.modules["serial"] = boom
        bad = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM8", profile=prof_of(), mock_output=False,
                                 time_fn=lambda: 100.0)
        bad._gate = lambda: ""
        first_ok, first_why = bad.send(pressed_bits=1)
        bad._next_due = 0.0
        bad._open_backoff_until = 100.5
        second_ok, second_why = bad.send(pressed_bits=1)
        check("打开失败要退避：退避窗口内不再砸端口", not second_ok and "不重试" in second_why,
              second_why[:60])
        check("退避不改变'失败就是失败'的返回", first_ok is False and second_ok is False,
              f"{first_ok}/{second_ok}")
        bad.time_fn = lambda: 101.0
        bad._next_due = 0.0
        third_ok, third_why = bad.send(pressed_bits=1)
        check("退避窗口过后要重新尝试（不是一辈子放弃）",
              not third_ok and "拒绝访问" in third_why, third_why[:60])
    finally:
        if saved is not None:
            sys.modules["serial"] = saved
        else:
            sys.modules.pop("serial", None)
    prof_bad = prof_of()
    lk2 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_bad, mock_output=False)
    lk2.write_bytes = lambda b: None
    lk2.send(pressed_bits=1)
    check("注入 write_bytes 时 resolved_port 不该冒充真口",
          lk2.resolved_port == "", lk2.resolved_port)


def test_range_clamping() -> None:
    print("\n--- 10) 越界值：截进位宽并说明，而不是抛错把整帧废掉 ---")
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of())
    lk._gate = lambda: ""
    frame = lk.encode({"mouse_x": 999999, "mouse_y": -999999, "mouse_z": 0,
                       "left": False, "right": False, "keyboard_value": 0x1FFFF})
    dec = dl.decode_dt7_frame(frame)
    x, y, _z, _lb, _rb, key, _r = struct.unpack("<hhhBBHH", dec["payload"])
    check("鼠标越界被截到 int16 边界（不是抛错）",
          (x, y) == (32767, -32768), f"{x} {y}")
    check("键位超 16 位时按位截断", key == 0xFFFF, hex(key))
    check("被截过哪几个字段要记下来", "mouse_x" in lk.clipped_fields, str(lk.clipped_fields))
    ok, _ = lk.send(mouse_dx=99999, pressed_bits=0x11)
    check("越界那一帧照样发得出去（WASD 不能被鼠标值连坐）", ok, lk.last_error)
    check("发完之后 last_note 说清楚截了什么", "截断" in lk.last_note, lk.last_note)
    normal = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of())
    normal._gate = lambda: ""
    normal.send(mouse_dx=12, mouse_y=-7, pressed_bits=3)
    check("正常范围内的值不受影响、也不误报截断",
          normal.clipped_fields == [] and normal.last_note == "",
          f"{normal.clipped_fields} {normal.last_note!r}")
    check("clamp_field 对 bool 化的左右键不会越界",
          dl.clamp_field("left", "B", 1) == (1, False))


def test_mouse_limit() -> None:
    print("\n--- 12) 每帧鼠标增量上限（防一帧甩到云台限位）---")
    base = dl.load_profile()
    check("仓库 profile 带了 mouse_limit 且是有限值",
          isinstance(base.get("mouse_limit"), int) and 0 < base["mouse_limit"] <= 2000,
          str(base.get("mouse_limit")))
    lk = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of())
    lk._gate = lambda: ""
    ok, _ = lk.send(mouse_dx=9000, mouse_y=-9000, pressed_bits=0x11)
    dec = dl.decode_dt7_frame(lk.last_frame)
    x, y = struct.unpack_from("<hh", dec["payload"], 0)
    check("界面掉帧攒出来的巨大位移被截到 ±mouse_limit",
          ok and (x, y) == (200, -200), f"{x} {y}")
    check("截断要写在 note 里，不静默", "截断" in lk.last_note, lk.last_note)
    unlimited = prof_of(mouse_limit=0)
    lk2 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=unlimited)
    lk2._gate = lambda: ""
    lk2.send(mouse_dx=30000)
    check("mouse_limit=0 表示不额外限制（仍受 int16 截断保护）",
          struct.unpack_from("<h", dl.decode_dt7_frame(lk2.last_frame)["payload"], 0)[0] == 30000,
          lk2.last_note)
    lk3 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=prof_of(mouse_limit=32767))
    lk3._gate = lambda: ""
    lk3.send(mouse_dx=-40000)
    # 32767 本身就是上限，截到 -32767（不是 -32768：int16 的不对称不该被测试猜错）
    check("上限设成 int16 边界时按该边界截断",
          struct.unpack_from("<h", dl.decode_dt7_frame(lk3.last_frame)["payload"], 0)[0] == -32767,
          struct.unpack_from("<h", dl.decode_dt7_frame(lk3.last_frame)["payload"], 0))
    tmp = ROOT / "configs" / "_t_limit.json"
    try:
        tmp.write_text(json.dumps({"mouse_limit": "很多"}), encoding="utf-8")
        check("mouse_limit 写错类型要报错（不能静默当成不限制）",
              "mouse_limit" in dl.load_profile(str(tmp))["_profile_error"])
        tmp.write_text(json.dumps({"mouse_limit": -5}), encoding="utf-8")
        check("负上限也要报错", "mouse_limit" in dl.load_profile(str(tmp))["_profile_error"])
    finally:
        tmp.unlink(missing_ok=True)
    bad = prof_of(mouse_limit=0)
    bad["_profile_error"] = ""
    lk4 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=bad)
    lk4._gate = lambda: ""
    bad2 = prof_of()
    bad2["mouse_limit"] = "乱"
    lk5 = dl.Dt7KeyMouseLink(safety=OfflineDt7Safety(), port="COM9", profile=bad2)
    lk5._gate = lambda: ""
    lk5.send(mouse_dx=1234)
    check("运行期拿到垃圾 mouse_limit 时按不限制走，但值仍受 int16 保护",
          struct.unpack_from("<h", dl.decode_dt7_frame(lk5.last_frame)["payload"], 0)[0] == 1234)


def main() -> int:
    test_dt7_frame()
def _host_gimbal_request(manager) -> int:
    """读 polar transport 的云台直控请求位（它是方法，不是属性——写错就会静默拿到 0）。"""
    req = getattr(manager.polar, "gimbal_host_request", 0)
    if callable(req):
        req = req()
    try:
        return int(req or 0)
    except (TypeError, ValueError):
        return -1


def test_gimbal_request_note() -> None:
    print("\n--- 13) 云台直控请求会顶掉 C 路鼠标（固件仲裁要看得见）---")
    import tempfile
    import rm_client.backend.manager as mgr_mod
    from rm_client.backend.manager import BackendManager
    from rm_client.control import KeyboardControlBridge
    from rm_client.model import RobotState, build_default_registry
    orig = mgr_mod.dt7_link.load_profile
    mgr_mod.dt7_link.load_profile = lambda path=None: prof_of()
    try:
        manager = BackendManager(workspace=tempfile.mkdtemp(prefix="dt7_gm_"),
                                scenario_key="OfficialEvent", client_id="3")
        registry = build_default_registry()
        state = RobotState()
        manager.state = state
        manager._apply_config(manager.config)
        module = registry.get("RobotLink")
        module.enabled = True
        module.get_setting("Direct DT7 Egress").value = True
        module.get_setting("Allow Drive").value = True
        br = KeyboardControlBridge(publisher=None, mock_output=True)
        manager.tick_polar_link(state, registry, br)
        check("C 路生效且没有云台请求时，不该冒出误导提示",
              br.dt7_enabled is True and br.dt7_mouse_note == "", repr(br.dt7_mouse_note))
        # 请求位是每拍重算的（客户端想要云台 + 链路活跃 + 没急停），
        # 所以测试要把这三个条件真造出来，而不是手动塞一个 1 进去（那是自欺）
        module.get_setting("Client Gimbal Control").value = True
        # bridge.active 不是开关，是"链路新鲜 + 收到过段"算出来的（polar_bridge.py:151-152），
        # 所以要造的是这两个事实，而不是硬塞一个 True
        bridge = manager.polar_bridge
        bridge.last_segment_at = bridge.clock()
        bridge.segment_counts[0x1F] = 1      # 键是**整数段号**，写字符串会把 manager 噎住
        manager.tick_polar_link(state, registry, br)
        check("造的条件真的让请求位立起来了（否则这条测试什么都没测）",
              _host_gimbal_request(manager) == 1, str(_host_gimbal_request(manager)))
        manager.tick_polar_link(state, registry, br)
        check("上位机云台直控请求占着 -> 明确说鼠标这条路被顶掉",
              "AUTO_AIM" in br.dt7_mouse_note or "顶掉" in br.dt7_mouse_note,
              br.dt7_mouse_note)
        module.get_setting("Client Gimbal Control").value = False
        manager.tick_polar_link(state, registry, br)
        check("撤回请求后提示要跟着消失（不许留残影）",
              br.dt7_mouse_note == "", repr(br.dt7_mouse_note))
        drive = module.get_setting("Allow Drive")
        drive.value = False
        manager.tick_polar_link(state, registry, br)
        check("C 路本身没生效时不该给这条提示（闸门没过别吓操作手）",
              br.dt7_mouse_note == "", repr(br.dt7_mouse_note))
    finally:
        mgr_mod.dt7_link.load_profile = orig



VENDOR_DUMP = Path(r"D:\rmbuild\dump12\dump.cs")


def _vendor_struct_fields(text: str, name: str) -> list:
    """从 dump.cs 里取某个类型的字段顺序（声明顺序 + // 0x 偏移）。"""
    m = re.search("public (?:struct|class) " + name + "\\b.*?\\n\\}", text, re.S)
    if not m:
        return []
    fields = []
    for line in m.group(0).splitlines():
        fm = re.match(r"\s*public\s+\S+\s+(\w+)\s*;\s*//\s*0x([0-9a-fA-F]+)", line)
        if fm:
            fields.append((fm.group(1), int(fm.group(2), 16)))
    return fields


def test_vendor_type_layout_anchor() -> None:
    print("\n--- 11) 厂商类型定义 == 我们的字节（外部锚点，不是自洽断言）---")
    vendor_dump = Path(os.environ.get("POLAR_VENDOR_DUMP") or VENDOR_DUMP).expanduser()
    if not vendor_dump.is_file():
        SKIPPED.append("没有 dump.cs：厂商类型锚点未跑")
        print(f"[SKIP] 找不到 {vendor_dump}")
        return
    text = vendor_dump.read_text(encoding="utf-8", errors="replace")
    prof = dl.load_profile()
    m = re.search(r"class Proto_Prefix[^}]*?CONTROL_PC_ROBOT\s*=\s*(\d+)", text, re.S)
    check("厂商常量 Proto_Prefix.CONTROL_PC_ROBOT 找得到", m is not None)
    if m:
        check("profile 的 cmd_wire == CONTROL_PC_ROBOT（0x2000 语义由厂商命名结案）",
              int(m.group(1)) == int(prof["cmd_wire"]), f"{m.group(1)} vs {prof['cmd_wire']}")
    hdr = _vendor_struct_fields(text, "CommonHeader_V1")
    check("CommonHeader_V1 字段顺序 == 我们的 4 字节头",
          [n for n, _ in hdr] == ["hdr_flag", "length", "protocol_version", "crc8"]
          and [o for _, o in hdr] == [0, 1, 2, 3], str(hdr))
    pre = _vendor_struct_fields(text, "Data_Body_Prefix")
    check("Data_Body_Prefix = sequence_number 在前、cmd_id 在后（写反就全废）",
          [n for n, _ in pre] == ["sequence_number", "cmd_id"] and [o for _, o in pre] == [0, 2],
          str(pre))
    body = _vendor_struct_fields(text, "Input_Device_Status_RC")
    names = [n for n, _ in body]
    check("厂商 RC 载荷字段顺序 == profile 的 payload_fields（逐字段）",
          names == ["mouse_x", "mouse_y", "mouse_z", "left", "right", "keyboard_value",
                    "reserved"]
          or names == ["mouse_x", "mouse_y", "mouse_z", "left_button_down",
                       "right_button_down", "keyboard_value", "reserved"], str(names))
    check("厂商 RC 载荷偏移 == 我们 struct 打包的偏移，且总长 12",
          [o for _, o in body] == [0, 2, 4, 6, 7, 8, 10]
          and dl.validate_layout(prof["payload_layout"], prof["payload_fields"]) == 12,
          str([o for _, o in body]))
    check("厂商 RC 载荷里没有中键字段（所以 mid 必须没有落点）",
          not any("mid" in n.lower() for n in names), str(names))


def main() -> int:
    test_dt7_frame()
    test_frame_and_crc_families()
    test_payload_layout()
    test_refusals()
    test_rate_seq_handle()
    test_profile_file()
    test_bridge_gates_mutex()
    test_crc_vs_firmware()
    test_probe_and_runtime_agree()
    test_range_clamping()
    test_mouse_limit()
    test_link_recovery()
    test_gimbal_request_note()
    test_vendor_type_layout_anchor()
    print("\n" + ("有失败项" if FAILED else "DT7 直连出口（帧型/CRC/载荷/闸门/互斥/松手）全部通过"))
    if SKIPPED:
        print("跳过项（别当已验）：" + "；".join(SKIPPED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
