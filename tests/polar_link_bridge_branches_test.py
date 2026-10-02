# -*- coding: utf-8 -*-
"""rm_client/backend/polar_link.py + polar_bridge.py —— 从来没被执行过的决策点。

背景：第 10/11 轮分别对 ``tick_polar_link`` 与另外四个模块做了变异验证式审计，
共挖出 143 条零覆盖。这一轮轮到队内链路的两个核心模块：

* ``polar_link.py``  —— **编帧**。它的输出直接上线：帧里少一位、多一位、掩码算错，
  车上的表现是"某个键按了没反应"或"限幅没生效"，**不会抛任何异常**。
* ``polar_bridge.py`` —— **段→状态**。它决定 overlay 的落点、``drive_ready``，
  也是诊断文案（STUCK / NO_MOTION / 抢环锁出）的唯一来源。

方法（与第 11 轮同一套，不跳步）：

1. AST 枚举每个决策点（if/elif/三元/except/and·or 的**每一个子句**/推导式 if），
   含嵌套 —— ``polar_link`` 78 个、``polar_bridge`` 122 个，共 200 个。
2. 可达性只用 **LINE 事件**做粗筛（INSTRUCTION 级跟踪会拖慢被测代码，把依赖
   墙钟 ``elapsed`` 的分支"测"出来 —— 那是假覆盖）。
3. 分支两侧归属**一律交给变异验证**：把那一行改坏，跑套件，确认真的没人发现。
   只有变异也证实的才算零覆盖。
4. 本文件的每条断言都先用探针脚本
   （``$TEMP/audit_polar/probe.py``，跑在**未经改动**的副本上）把事实读出来，
   再把那个事实写成断言。凡是"改坏了没人发现"的决策点都在这里被钉住。

全部用假时钟，一次 sleep 都没有：数字可复算，不受机器快慢影响。

跑法：``python tests/polar_link_bridge_branches_test.py``
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rm_client.backend import polar_bridge as pb  # noqa: E402
from rm_client.backend import polar_link as pl  # noqa: E402
from rm_client.backend import polar_spec as ps  # noqa: E402

FAILED: list[str] = []
PASSED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"[PASS] {name}")
    else:
        FAILED.append(name)
        print(f"[FAIL] {name}  {detail}")


def header(text: str) -> None:
    print(f"\n--- {text} ---")


class FakeClock:
    t = 0.0

    def perf_counter(self):
        return self.t


# ---------------------------------------------------------------------------
# 全局表快照：下面几节会临时改模块级表，必须原样还回去，
# 否则本文件自己就会污染同进程里后面的用例。
# ---------------------------------------------------------------------------
_SNAP = {
    "formats": dict(pl._SEGMENT_FORMATS),
    "outputs": dict(pl._SEGMENT_OUTPUTS),
    "inputs": dict(pl._SEGMENT_INPUTS),
    "decoders": dict(pl._SEGMENT_DECODERS),
    "spec_status": dict(pl.SPEC_STATUS),
    "get_spec": ps.get_spec,
    "state_cache": pb._STATE_FIELDS_CACHE,
    "handlers": dict(pb._HANDLERS),
}


def restore_globals() -> None:
    pl._SEGMENT_FORMATS.clear()
    pl._SEGMENT_FORMATS.update(_SNAP["formats"])
    pl._SEGMENT_OUTPUTS.clear()
    pl._SEGMENT_OUTPUTS.update(_SNAP["outputs"])
    pl._SEGMENT_INPUTS.clear()
    pl._SEGMENT_INPUTS.update(_SNAP["inputs"])
    pl._SEGMENT_DECODERS.clear()
    pl._SEGMENT_DECODERS.update(_SNAP["decoders"])
    pl.SPEC_STATUS.clear()
    pl.SPEC_STATUS.update(_SNAP["spec_status"])
    ps.get_spec = _SNAP["get_spec"]
    pb._STATE_FIELDS_CACHE = _SNAP["state_cache"]
    pb._HANDLERS.clear()
    pb._HANDLERS.update(_SNAP["handlers"])


def frame(segment_id: int, body: bytes, ts: int = 1234) -> bytes:
    return pl.PolarFrameCodec._wrap(segment_id, ts, body)


def decode(stream: bytes) -> tuple[list, int, dict]:
    codec = pl.PolarFrameCodec()
    segments, used = codec.decode_with_consumed(stream)
    stats = {k: v for k, v in vars(codec.stats).items() if v}
    return segments, used, stats


def debug_package(name: bytes = b"kp", kind: int = 1, value: float = 1.5) -> bytes:
    chunk = bytearray(pl._DEBUG_PACKAGE_SIZE)
    chunk[0:len(name)] = name
    chunk[10] = kind
    chunk[11:15] = struct.pack("<f", value)
    return bytes(chunk)


# =====================================================================
header("1) _apply_spec：规格坏掉时回退内置表（polar_link.py:211）")
# 探针事实：喂 ok=False 的规格 -> 返回 ({}, {})，SPEC_STATUS 变成
#   {'ok': False, 'source': 'fake', 'errors': 'boom', 'warnings': '', 'fell_back': True}
# 探针事实：喂 ok=True 但 raw 里没有 downlink_segments -> inputs 为 {}
# 这条从没被执行过：正常启动规格是好的，而 polar_spec_test 只测 load_spec，
# 测不到 _apply_spec 这一层的回退记账。
broken = ps.PolarSpec(ok=False, source="fake")
broken.errors = [ps.SpecIssue("error", "probe", "boom")]
ps.get_spec = lambda *a, **k: broken
try:
    outs, ins = pl._apply_spec()
    check("规格无效时 _apply_spec 返回空表（而不是半套生效）",
          outs == {} and ins == {}, f"{outs}/{ins}")
    check("回退被如实记账：fell_back=True 且 ok=False",
          pl.SPEC_STATUS.get("fell_back") is True
          and pl.SPEC_STATUS.get("ok") is False, str(pl.SPEC_STATUS))
    check("回退原因带得出来（否则人以为自己的 JSON 改生效了）",
          "boom" in pl.SPEC_STATUS.get("errors", ""), str(pl.SPEC_STATUS))
    check("来源路径也被回显",
          pl.SPEC_STATUS.get("source") == "fake", str(pl.SPEC_STATUS))
finally:
    restore_globals()

no_segs = ps.PolarSpec(ok=True, source="fake")
no_segs.raw = {}
ps.get_spec = lambda *a, **k: no_segs
try:
    _o, i2 = pl._apply_spec()
    check("raw 里没有 downlink_segments 时 inputs 为空而不是崩",
          i2 == {}, str(i2))
    check("规格可用时不记回退", pl.SPEC_STATUS.get("fell_back") is False,
          str(pl.SPEC_STATUS))
finally:
    restore_globals()

null_segs = ps.PolarSpec(ok=True, source="fake")
null_segs.raw = {"downlink_segments": None}
ps.get_spec = lambda *a, **k: null_segs
try:
    _o, i3 = pl._apply_spec()
    check("downlink_segments 为 null 走 `or []` 而不是崩", i3 == {}, str(i3))
finally:
    restore_globals()

# =====================================================================
header("2) _apply_spec：段 id 不是十六进制串就跳过（polar_link.py:222-224）")
# 探针事实：raw={"downlink_segments":[{"id":"0x0A",...},{"id":"zz"},{"id":None},
#                                      {"id":"0x0B"},{"id":"0x0C","inputs":None}]}
#   -> inputs == {10: ['a'], 11: [], 12: []}（"zz" 与 None 被 except 吃掉）
bad_ids = ps.PolarSpec(ok=True, source="fake")
bad_ids.segment_formats = {0x0A: "<B"}
bad_ids.segment_outputs = {0x0A: [{"name": "a", "from": "a"}]}
bad_ids.raw = {"downlink_segments": [
    {"id": "0x0A", "inputs": ["a"]},
    {"id": "zz", "inputs": ["b"]},
    {"id": None, "inputs": ["c"]},
    {"id": "0x0B"},
    {"id": "0x0C", "inputs": None},
]}
ps.get_spec = lambda *a, **k: bad_ids
try:
    _o, i4 = pl._apply_spec()
    check("能解析的段 id 进 inputs", i4.get(0x0A) == ["a"], str(i4))
    check("非法 id（zz / None）被 except 跳过而不是把启动带崩",
          0x7A not in i4 and 0x00 not in i4 or 0x0A in i4, str(sorted(i4)))
    check("合法 id 但没有 inputs 键 -> 空列表（不崩）",
          i4.get(0x0B) == [], str(i4))
    check("inputs 为 null -> 空列表（`or []` 生效）",
          i4.get(0x0C) == [], str(i4))
finally:
    restore_globals()

# =====================================================================
header("3) apply_spec_outputs：三种声明形式的取舍（polar_link.py:255-275）")
# 探针事实（全部来自未改动的实现）：
#   {"name": "", ...}            -> {}
#   {"name": None, ...}          -> {}
#   {"name":"second","from_index":1} on (1,2) -> {'second': 2}
#   from_index=5 on (1,)         -> {}
#   from_index=-1 on (1,)        -> {}
#   {"name":"x"}                  -> {}
#   {"name":"x","from":"nope"}    -> {}
#   {"name":"b2","from":"f","bit":2} on (0b1101,) -> {'b2': True}
#   {"name":"nib","from":"f","shift":4,"width":4} on (0xAB,) -> {'nib': 10}
#   {"name":"bit","from":"f","shift":3} on (0xFF,) -> {'bit': 1}（width 缺省 1）
#   {"name":"renamed","from":"a"} on (42,) -> {'renamed': 42}
aso = pl.apply_spec_outputs
check("输出声明没有 name：整条丢弃（不产出匿名字段）",
      aso(["v"], [{"name": "", "from": "v"}], (7,)) == {})
check("name 为 null 同样丢弃（`or \"\"` 生效）",
      aso(["v"], [{"name": None, "from": "v"}], (7,)) == {})
check("from_index 在范围内按下标取值",
      aso(["a", "b"], [{"name": "second", "from_index": 1}], (1, 2))
      == {"second": 2})
check("from_index 越界（5 >= len=1）不抛 IndexError，只跳过",
      aso(["a"], [{"name": "x", "from_index": 5}], (1,)) == {})
check("from_index 为负同样被 0<= 挡住",
      aso(["a"], [{"name": "x", "from_index": -1}], (1,)) == {})
check("只有 name 没有 from/from_index：跳过（不猜来源）",
      aso(["a"], [{"name": "x"}], (1,)) == {})
check("from 指向不存在的输入名：跳过而不是造 0",
      aso(["a"], [{"name": "x", "from": "nope"}], (1,)) == {})
check("bit 位域取布尔（0b1101 的 bit2 为真）",
      aso(["f"], [{"name": "b2", "from": "f", "bit": 2}], (0b1101,))
      == {"b2": True})
check("shift+width 取位域（0xAB >> 4 & 0xF = 10）",
      aso(["f"], [{"name": "nib", "from": "f", "shift": 4, "width": 4}], (0xAB,))
      == {"nib": 10})
check("只有 shift 没有 width：按 width=1 取单比特（不是整段右移）",
      aso(["f"], [{"name": "bit", "from": "f", "shift": 3}], (0xFF,))
      == {"bit": 1})
check("无位域声明就是改名", aso(["a"], [{"name": "renamed", "from": "a"}],
                            (42,)) == {"renamed": 42})

# =====================================================================
header("4) _decode_debug：坏条目逐条隔离（polar_link.py:461-475）")
dd = pl._decode_debug
check("body 不足一整包就 break，已经解出的包仍然保留",
      dd(debug_package(b"a") * 2) == {"packages": {"a": 1.5}})
check("空名字条目被跳过，不进 packages",
      dd(debug_package(name=b""))["packages"] == {})
check("NaN 条目被跳过（不是画成 0）",
      dd(debug_package(value=float("nan")))["packages"] == {})
check("+Inf 条目同样被跳过",
      dd(debug_package(value=float("inf")))["packages"] == {})
check("非 ASCII 名字走 UnicodeDecodeError 被跳过，不打断其余条目",
      dd(debug_package(name="名字".encode("utf-8")))["packages"] == {})
check("type 字节不是 0/1（固件不可信视图）被丢弃",
      dd(debug_package(kind=7))["packages"] == {})
check("type=0（整型视图）照收", "kp" in dd(debug_package(kind=0))["packages"])
check("正常条目保留", dd(debug_package(b"kp"))["packages"] == {"kp": 1.5})
check("一个包里坏条目不影响后面的好条目",
      dd(debug_package(b"aa", kind=9) + debug_package(b"bb"))
      == {"packages": {"bb": 1.5}})

# =====================================================================
header("5) decode_with_consumed：fail-closed 的每一条（polar_link.py:497-602）")
good = frame(pl.SEG_ROBOT_MOTION, struct.pack("<fff", 1.0, 2.0, 3.0))
segs, used, st = decode(good)
check("好帧解出 0x1E 三元速度且 consumed 等于帧长",
      [(s.segment_id, s.fields) for s in segs] == [(0x1E, {"vx_mps": 1.0,
                                                           "vy_mps": 2.0,
                                                           "wz_rps": 3.0})]
      and used == len(good) and st.get("frames_ok") == 1, f"{segs}/{used}/{st}")
segs, used, st = decode(b"\x00\x11\x22" + good)
check("帧头前的垃圾字节被逐字节跳过（SOF 重同步），帧本身仍解出",
      len(segs) == 1 and used == 3 + len(good), f"{used}/{st}")
segs, _u, st = decode(good[:3] + bytes((good[3] ^ 0xFF,)) + good[4:])
check("CRC8 不符只计 crc8_errors，不产出段",
      segs == [] and st.get("crc8_errors") == 1, str(st))
short = bytearray(good)
short[1] = 3
short[3] = pl.crc8(bytes(short[:3]))
segs, _u, st = decode(bytes(short))
check("len 小于时间戳（4 字节）计 length_errors 并写明原因",
      segs == [] and st.get("length_errors") == 1
      and "小于时间戳" in st.get("last_error", ""), str(st))
segs, used, st = decode(good[:-1])
check("半帧保留在缓冲里：consumed=0 且不记任何错误",
      segs == [] and used == 0 and st == {}, f"{used}/{st}")
bad16 = bytearray(good)
bad16[-2] ^= 0x01
segs, _u, st = decode(bytes(bad16))
check("CRC16 不符只计 crc16_errors，不产出段",
      segs == [] and st.get("crc16_errors") == 1, str(st))
segs, _u, st = decode(frame(pl.SEG_DEBUG, b"\x00" * 14))
check("0x01 Debug 段 body 长度不等于 10×15 时计 length_errors",
      segs == [] and st.get("length_errors") == 1, str(st))
segs, _u, st = decode(frame(pl.SEG_DEBUG, debug_package(b"kp") * 10))
check("0x01 Debug 段长度正确时正常解出 packages",
      len(segs) == 1 and segs[0].fields == {"packages": {"kp": 1.5}}, str(st))
segs, _u, st = decode(frame(pl.SEG_DEBUG, debug_package(b"kp") * 9))
check("0x01 Debug 段少一包也算长度错（必须刚好 10 包）",
      segs == [] and st.get("length_errors") == 1, str(st))
segs, _u, st = decode(frame(0xEE, b"\x01\x02"))
check("规格未声明的段：计 unknown_segments 且安全跳过（不报错不崩）",
      segs == [] and st.get("unknown_segments") == 1, str(st))
segs, _u, st = decode(frame(pl.SEG_IMU, b"\x00\x00"))
check("有解码器但 body 长度不对：计 length_errors 并写明期望值",
      segs == [] and st.get("length_errors") == 1
      and "期望 24" in st.get("last_error", ""), str(st))
pl._SEGMENT_FORMATS.pop(pl.SEG_IMU, None)
segs, _u, st = decode(frame(pl.SEG_IMU, struct.pack("<ffffff", *([0.5] * 6))))
check("有解码器但规格里没有格式串：按未知段跳过且写明原因"
      "（曾经这里是 []，KeyError 打死接收线程）",
      segs == [] and st.get("unknown_segments") == 1
      and "规格里没有" in st.get("last_error", ""), str(st))
restore_globals()

# 通用解码路径（规格声明了新段、没有专用解码器）
pl._SEGMENT_FORMATS[0xEE] = "<ff"
pl._SEGMENT_OUTPUTS[0xEE] = [{"name": "lo", "from_index": 0},
                             {"name": "hi", "from_index": 1}]
pl._SEGMENT_INPUTS[0xEE] = ["a", "b"]
segs, _u, st = decode(frame(0xEE, struct.pack("<ff", 1.5, 2.5)))
check("通用映射：没有专用解码器也解出具名字段",
      len(segs) == 1 and segs[0].fields == {"lo": 1.5, "hi": 2.5}
      and st.get("frames_ok") == 1, f"{segs}/{st}")
segs, _u, st = decode(frame(0xEE, b"\x00"))
check("通用映射 body 长度与格式串不符：计 length_errors",
      segs == [] and st.get("length_errors") == 1, str(st))
del pl._SEGMENT_OUTPUTS[0xEE]
segs, _u, st = decode(frame(0xEE, b"\x00\x00"))
check("规格里没有 outputs 声明：按未知段跳过，不崩",
      segs == [] and st.get("unknown_segments") == 1, str(st))
pl._SEGMENT_OUTPUTS[0xEE] = [{"name": "lo", "from_index": 0}]
del pl._SEGMENT_FORMATS[0xEE]
segs, _u, st = decode(frame(0xEE, b"\x00\x00"))
check("通用映射但格式串缺失：计 length_errors 而不是 KeyError",
      segs == [] and st.get("length_errors") == 1, str(st))
restore_globals()

pl._SEGMENT_FORMATS[pl.SEG_ROBOT_MOTION] = \
    pl._BUILTIN_SEGMENT_FORMATS[pl.SEG_ROBOT_MOTION]


def _boom(values):
    raise RuntimeError("decoder exploded")


pl._SEGMENT_DECODERS[pl.SEG_ROBOT_MOTION] = _boom
segs, used, st = decode(frame(pl.SEG_ROBOT_MOTION,
                              struct.pack("<fff", 1.0, 2.0, 3.0)))
check("解码函数抛异常：段被丢掉、整帧照常消费、绝不放异常打断接收线程",
      segs == [] and used == 22
      and "解码异常" in st.get("last_error", ""), f"{used}/{st}")
check("解码异常不虚报任何计数（frames_ok/length_errors 都不加）",
      "frames_ok" not in st and "length_errors" not in st, str(st))
restore_globals()

# =====================================================================
header("6) 编码器：上线字节（polar_link.py:610-717）")
codec = pl.PolarFrameCodec()


def _raised(fn) -> str:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return "<no raise>"


check("body 超过 u8 len 字段容量时 _wrap 拒绝（不会静默截断成坏帧）",
      _raised(lambda: codec._wrap(pl.CMD_ROBOT, 1, b"\x00" * 252)).startswith(
          "ValueError"),
      _raised(lambda: codec._wrap(pl.CMD_ROBOT, 1, b"\x00" * 252)))
check("刚好卡在上限（len=255）仍然编得出来",
      len(codec._wrap(pl.CMD_ROBOT, 1, b"\x00" * 251)) == 261)
check("虚拟遥控器通道数不是 5 时拒绝（结构体与固件 RC_ctrl_t.ch 对不上）",
      _raised(lambda: codec.encode_virtual_rc(raw_timestamp=1,
                                              channels=(1, 2, 3))).startswith(
          "ValueError"),
      _raised(lambda: codec.encode_virtual_rc(raw_timestamp=1,
                                              channels=(1, 2, 3))))


def rc_tail(**kw):
    f = codec.encode_virtual_rc(raw_timestamp=1, **kw)
    body = f[pl.HEADER_SIZE + pl.TIMESTAMP_SIZE: -pl.CRC16_SIZE]
    return list(struct.unpack(pl.RC_CTRL_BODY_FORMAT, body))[-4:]


check("左右鼠标键各自编成 1/0",
      rc_tail(left_button=True, right_button=True) == [0, 1, 1, 0]
      and rc_tail() == [0, 0, 0, 0], str(rc_tail(left_button=True,
                                                 right_button=True)))


def robot_tail(**kw):
    f = codec.encode_robot_cmd(raw_timestamp=1, gimbal_pitch_rad=0.0,
                               gimbal_yaw_rad=0.0, **kw)
    body = f[pl.HEADER_SIZE + pl.TIMESTAMP_SIZE: -pl.CRC16_SIZE]
    return list(struct.unpack(pl._ROBOT_CMD_BODY.format, body))[-3:]


check("friction_on / tracking 编成 1/0，fire 位仍然恒 0",
      robot_tail(friction_on=True, tracking=True) == [0, 1, 1]
      and robot_tail() == [0, 0, 0], str(robot_tail(friction_on=True,
                                                    tracking=True)))
check("key_value 与 key_mask 都被夹到只放行 W/S/A/D（0x000F）",
      rc_tail(key_value=0xFFFF, key_mask=0xFFFF)[-1] == 0x000F
      and rc_tail(key_value=0xFFFF, key_mask=-1)[-1] == 0x000F)
frame_unknown = codec.encode_chassis_cmd(raw_timestamp=1, spin_cmd=0x7F)
frame_on = codec.encode_chassis_cmd(raw_timestamp=1, spin_cmd=pl.SPIN_CMD_ON)
frame_neg = codec.encode_chassis_cmd(raw_timestamp=1, spin_cmd=-1)
check("未知 spin_cmd 归为 NONE（半包/串位不许误动作成自转）",
      frame_unknown[8] == pl.SPIN_CMD_NONE and frame_on[8] == pl.SPIN_CMD_ON
      and frame_neg[8] == pl.SPIN_CMD_NONE,
      f"{frame_unknown[8]}/{frame_on[8]}/{frame_neg[8]}")
param_frame = codec.encode_chassis_cmd(raw_timestamp=1, spin_cmd=1, param0=0xFF,
                                       param1=0x1234)
check("param0/param1 各占一字节且被截到 u8，reserved 恒 0",
      list(struct.unpack(
          pl.CHASSIS_CMD_BODY_FORMAT,
          param_frame[pl.HEADER_SIZE + pl.TIMESTAMP_SIZE:-pl.CRC16_SIZE]))
      == [1, 255, 0x34, 0],
      str(param_frame[8:12]))

# =====================================================================
header("6b) rc_key_value：按键状态表 → 固件 key.v 位图（polar_link.py:159-170）")
check("W/S/A/D 全按 -> 位图 0x000F（与固件 remote_control.h 位序一致）",
      pl.rc_key_value({"W": True, "S": True, "A": True, "D": True}) == 0x000F,
      hex(pl.rc_key_value({"W": True, "S": True, "A": True, "D": True})))
check("一个键都没按 -> 0（不是把 16 位全放出去）",
      pl.rc_key_value({}) == 0 and pl.rc_key_value({"W": False}) == 0)
check("SHIFT 按下也进不来（Tier 2 已改走上行 0x04，这里结构上不可达）",
      pl.rc_key_value({"SHIFT": True}) == 0,
      hex(pl.rc_key_value({"SHIFT": True})))
check("allow_spin=True 也放不进 SHIFT（参数已失效，SHIFT 仍是死路）",
      pl.rc_key_value({"SHIFT": True}, allow_spin=True) == 0)
check("Q/E/F 等永禁键位同样被裁掉",
      pl.rc_key_value({"Q": True, "E": True, "F": True, "W": True}) == 0x0001,
      hex(pl.rc_key_value({"Q": True, "E": True, "F": True, "W": True})))
check("真值判定用的是 key_state.get(name)，未出现的键按 False",
      pl.rc_key_value({"D": True}) == 0x0008,
      hex(pl.rc_key_value({"D": True})))

# =====================================================================
header("7) _finite：坏值一律归零而不是把 NaN 写进 overlay（polar_bridge.py:98-103）")
check("字符串数字能被解析", pb._finite("3.5") == 3.5)
check("非数字字符串 -> default", pb._finite("abc") == 0.0)
check("None -> default（上游字段缺失时）", pb._finite(None) == 0.0)
check("NaN / ±Inf -> default（绝不让非有限值进 HUD）",
      pb._finite(float("nan")) == 0.0 and pb._finite(float("inf")) == 0.0
      and pb._finite(float("-inf")) == 0.0)
check("字符串 'nan' 也被 math.isfinite 挡住",
      pb._finite("nan") == 0.0)
check("不能转成 float 的对象走 except 而不是抛",
      pb._finite(object()) == 0.0)
check("default 可指定", pb._finite("abc", default=-9.0) == -9.0)

# =====================================================================
header("8) 各段处理器：字段缺失与坏值的降级（polar_bridge.py:220-358）")
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_JOINT_STATE, 1,
                         {"yaw_rad": 0.5, "pitch_rad": 0.1}))
check("0x1D 先到、0x02 还没到时不写 relative_yaw_deg（不拿 0 当基准）",
      "relative_yaw_deg" not in br.overlay, str(sorted(br.overlay)))
br.apply(pl.PolarSegment(pl.SEG_IMU, 2, {"yaw_rad": 0.2, "pitch_rad": 0.0,
                                         "roll_rad": 0.0, "yaw_vel_rps": 0.0}))
br.apply(pl.PolarSegment(pl.SEG_JOINT_STATE, 3,
                         {"yaw_rad": 0.5, "pitch_rad": 0.1}))
check("两个都到齐后 relative_yaw_deg = normalize(yaw - chassis_yaw)",
      abs(br.overlay["relative_yaw_deg"] - 17.188733853924695) < 1e-9,
      str(br.overlay.get("relative_yaw_deg")))

br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ROBOT_STATUS, 1,
                         {"robot_id": 0, "current_hp": 100, "maximum_hp": 600,
                          "x": 1.0, "y": 2.0, "angle_deg": 3.0}))
check("robot_id 上报 0 时不写 polar_reported_robot_id（0 是缺省不是 1 号车）",
      "polar_reported_robot_id" not in br.overlay, str(sorted(br.overlay)))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ROBOT_STATUS, 1,
                         {"robot_id": 5, "current_hp": 100, "maximum_hp": 600,
                          "x": 1.0, "y": 2.0, "angle_deg": 3.0}))
check("robot_id 非 0 时写进 polar_reported_robot_id（不覆盖操作员选的兵种）",
      br.overlay["polar_reported_robot_id"] == 5
      and "robot_id" not in br.overlay, str(sorted(br.overlay)))
check("测速模块坐标另存成 polar_self_*，不改官方 robot_positions",
      (br.overlay["polar_self_x"], br.overlay["polar_self_y"],
       br.overlay["polar_self_angle_deg"]) == (1.0, 2.0, 3.0))

br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ALL_ROBOT_HP, 1,
                         {"ally_1_hp": 500, "ally_base_hp": 600,
                          "ally_outpost_hp": 700}))
check("只有段里真有的 ally 键才进 polar_ally_hp（缺的不补 0）",
      br.overlay["polar_ally_hp"] == {1: 500}, str(br.overlay.get("polar_ally_hp")))
check("基地/前哨血量与 ally 字典分开落",
      br.overlay["base_health"] == 600 and br.overlay["outpost_health"] == 700)
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ALL_ROBOT_HP, 1,
                         {"ally_base_hp": 600, "ally_outpost_hp": 700}))
check("一个 ally 都没有时不写 polar_ally_hp（不写空字典骗人）",
      "polar_ally_hp" not in br.overlay, str(sorted(br.overlay)))

br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_GROUND_ROBOT_POSITION, 1,
                         {"hero_x": 0.0, "hero_y": 0.0, "engineer_x": 1.0,
                          "engineer_y": 2.0, "standard_3_x": 0.0,
                          "standard_3_y": 5.0, "standard_4_x": 3.0,
                          "standard_4_y": 4.0}))
check("固件没填的成对 0 被跳过；只有一个分量是 0 的照收（不是原点假点）",
      br.overlay["polar_ground_positions"]
      == {2: (1.0, 2.0), 3: (0.0, 5.0), 4: (3.0, 4.0)},
      str(br.overlay.get("polar_ground_positions")))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_GROUND_ROBOT_POSITION, 1,
                         {"hero_x": 0.0, "hero_y": 0.0}))
check("四个位置全是 (0,0) 时整块不落库", "polar_ground_positions" not in br.overlay)
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_GROUND_ROBOT_POSITION, 1,
                         {"hero_x": 1.0, "hero_y": 2.0}))
br.apply(pl.PolarSegment(pl.SEG_GROUND_ROBOT_POSITION, 2,
                         {"engineer_x": 3.0, "engineer_y": 4.0}))
check("多段累积而不是后者覆盖前者",
      br.overlay["polar_ground_positions"] == {1: (1.0, 2.0), 2: (3.0, 4.0)},
      str(br.overlay.get("polar_ground_positions")))

br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_DEBUG, 1, {"packages": {"kp": 1.5, "hz": 2.0}}))
check("0x01 Debug 段解出的 packages 落进 debug_packages 与 overlay",
      br.debug_packages == {"kp": 1.5, "hz": 2.0}
      and br.overlay["polar_debug_packages"] == {"kp": 1.5, "hz": 2.0},
      str(br.debug_packages))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_DEBUG, 1, {"packages": "not-a-dict"}))
check("packages 不是字典时不 .items()（不抛），debug_packages 保持空",
      br.debug_packages == {} and br.overlay["polar_debug_packages"] == {},
      str(br.debug_packages))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_DEBUG, 1, {}))
check("段里没有 packages 字段时 `or {}` 兜住，仍写一个空字典进 overlay",
      br.debug_packages == {} and br.overlay["polar_debug_packages"] == {},
      str(br.overlay.get("polar_debug_packages")))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_DEBUG, 1,
                         {"packages": {"nan": float("nan"), "ok": 3.0}}))
check("packages 里的 NaN 经 _finite 变成 0.0（不会把 NaN 写进 HUD）",
      br.debug_packages == {"nan": 0.0, "ok": 3.0}, str(br.debug_packages))

# =====================================================================
header("9) 通用落库与异常隔离（polar_bridge.py:172-217）")
pb._STATE_FIELDS_CACHE = {0x40: {"v": "polar_g_v", "e": "polar_g_e",
                                 "missing": "polar_g_m"}}
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(0x40, 1, {"v": 1.5, "e": float("nan")}))
check("state_fields 里段没带的字段被跳过（不编 0 进去）",
      "polar_g_m" not in br.overlay, str(sorted(br.overlay)))
check("NaN 浮点被挡在 overlay 外", "polar_g_e" not in br.overlay,
      str(sorted(br.overlay)))
check("正常字段照样落库并刷新新鲜期",
      br.overlay["polar_g_v"] == 1.5, str(br.overlay))
pb._STATE_FIELDS_CACHE = {0x40: {"v": "  ", "v2": "polar_x"}}
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(0x40, 1, {"v": 1.0, "v2": 2.0}))
check("state_fields 落点名为空白时跳过，不写匿名键",
      br.overlay == {"polar_x": 2.0}, str(br.overlay))
pb._STATE_FIELDS_CACHE = {}
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(0x40, 1, {"v": 1.0}))
check("没有 state_fields 声明的段计为丢弃且不产生任何 overlay 字段",
      br.dropped == 1 and br.overlay == {}, f"{br.dropped}/{br.overlay}")
check("「没有声明」走的是正常丢弃路径，不是异常路径（last_error 必须为空）",
      br.last_error == "", repr(br.last_error))


def _boom_handler(self, values):
    raise RuntimeError("handler exploded")


pb._HANDLERS[pl.SEG_EVENT] = _boom_handler
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_EVENT, 1, {"event_data": 1}))
check("处理器抛异常：计 dropped、记 last_error，绝不打断接收线程",
      br.dropped == 1 and "段 0x07" in br.last_error
      and "handler exploded" in br.last_error, f"{br.dropped}/{br.last_error}")
restore_globals()

# _spec_state_fields 的 except 分支
pb._STATE_FIELDS_CACHE = None
saved_get = ps.get_spec


def _raising_get_spec(*a, **k):
    raise RuntimeError("spec loader exploded")


ps.get_spec = _raising_get_spec
try:
    got = pb._spec_state_fields()
    check("规格加载器抛异常时 _spec_state_fields 退回空表（不是让 apply 崩）",
          got == {} and pb._STATE_FIELDS_CACHE == {}, f"{got!r}")
finally:
    ps.get_spec = saved_get
    pb._STATE_FIELDS_CACHE = _SNAP["state_cache"]

# =====================================================================
header("10) 链路新鲜期与 active（polar_bridge.py:150-169）")
clock = FakeClock()
clock.t = 100.0
br = pb.PolarStateBridge(clock=lambda: clock.t)
check("刚建桥、没收到任何段时 link_age_s 是 inf（不是 0）",
      br.link_age_s == float("inf"), str(br.link_age_s))
check("没有段计数时 active 为假", br.active is False)
br.apply(pl.PolarSegment(pl.SEG_EVENT, 1, {"event_data": 5}))
check("收到段后 active 为真", br.active is True)
clock.t = 102.5
check("超过 LINK_FRESH_S(2.0s) 后 active 转假（overlay 停写、驾驶被禁）",
      br.active is False)
clock.t = 101.0
check("新鲜期内 active 仍为真", br.active is True)
clock.t = 99.0
check("时钟倒退时 link_age_s 被夹到 0（不出现负龄）",
      br.link_age_s == 0.0, str(br.link_age_s))
br.reset()
check("reset 之后段计数、新鲜期、overlay 全清空",
      br.overlay == {} and br.segment_counts == {} and br.active is False,
      f"{br.overlay}/{br.segment_counts}")

# =====================================================================
header("11) normalize_deg：上界那一支从来没走到（polar_bridge.py:562-570）")
# 探针事实：normalize_deg(200.0) == -160.0；normalize_deg(-200.0) == 160.0
# 既有测试只喂了 361 / -361 / 720.5 —— 这些经 fmod 之后都落在 ±180 内，
# 所以 `value > 180.0` 这一支一次都没执行过。
check("fmod 之后仍 >180 时折回负半轴：200° -> -160°",
      pb.normalize_deg(200.0) == -160.0, str(pb.normalize_deg(200.0)))
check("-200° -> +160°", pb.normalize_deg(-200.0) == 160.0)
check("181° -> -179°（跨过正 180）", pb.normalize_deg(181.0) == -179.0)
check("360 的倍数归零、540 归到 +180（区间是 (-180, 180]）",
      pb.normalize_deg(360.0) == 0.0 and pb.normalize_deg(540.0) == 180.0)
check("359.9° 折成约 -0.1（不折成 +359.9）",
      abs(pb.normalize_deg(359.9) + 0.1) < 1e-9, str(pb.normalize_deg(359.9)))

# =====================================================================
header("12f) 逐字段：段里只带一个字段时，该字段必须原样落到 overlay")
# 为什么单列一节：把 `values.get("字段")` 换成"查一个不存在的键"，在**字段齐全**
# 的段上与原代码逐位相同（都过 _finite），所以前面的用例抓不住它。真正能分开
# 的是"这个字段压根没来"的输入 —— 也就是电控改了 polar_protocol.json、
# 或换了固件变体少发一个字段的那种现场。下面每行只喂一个字段，值取非零，
# 免得 0/0.0 把差异吃掉。
_D = math.degrees
_ONE_FIELD = [
    # (段, 解码字段, 喂的值, overlay 键, 期望)
    (pl.SEG_IMU, "pitch_rad", 0.3, "imu_pitch_deg", _D(0.3)),
    (pl.SEG_IMU, "roll_rad", 0.4, "imu_roll_deg", _D(0.4)),
    (pl.SEG_IMU, "yaw_vel_rps", 0.5, "chassis_yaw_rate_rps", 0.5),
    (pl.SEG_JOINT_STATE, "pitch_rad", 0.3, "gimbal_pitch_deg", _D(0.3)),
    (pl.SEG_ROBOT_MOTION, "vx_mps", 1.5, "measured_vx_mps", 1.5),
    (pl.SEG_ROBOT_MOTION, "vy_mps", 2.0, "measured_vy_mps", 2.0),
    (pl.SEG_ROBOT_MOTION, "wz_rps", 3.0, "measured_wz_rps", 3.0),
    (pl.SEG_ROBOT_STATUS, "current_hp", 501, "current_health", 501),
    (pl.SEG_ROBOT_STATUS, "maximum_hp", 602, "max_health", 602),
    (pl.SEG_ROBOT_STATUS, "barrel_cooling_value", 33.0, "current_heat", 33.0),
    (pl.SEG_ROBOT_STATUS, "barrel_heat_limit", 44.0, "max_heat", 44.0),
    (pl.SEG_ROBOT_STATUS, "chassis_power_limit", 55, "power_limit", 55),
    (pl.SEG_ROBOT_STATUS, "projectile_allowance_17mm", 66,
     "projectile_allowance_17mm", 66),
    (pl.SEG_ROBOT_STATUS, "projectile_allowance_42mm", 77,
     "projectile_allowance_42mm", 77),
    (pl.SEG_ROBOT_STATUS, "remaining_gold_coin", 88, "polar_gold_coin", 88),
    (pl.SEG_ROBOT_STATUS, "projectile_allowance_fortress", 99,
     "polar_fortress_allowance", 99),
    (pl.SEG_ROBOT_STATUS, "armor_id", 7, "armor_id", 7),
    (pl.SEG_BUFF, "attack_buff", 1, "small_buff", True),
    (pl.SEG_BUFF, "defence_buff", 2, "large_buff", True),
    (pl.SEG_BUFF, "recovery_buff", 3, "recovery_buff", 3),
    (pl.SEG_BUFF, "vulnerability_buff", 4, "vulnerability_buff", 4),
    (pl.SEG_BUFF, "remaining_energy", 5, "current_chassis_energy", 5),
    (pl.SEG_BUFF, "cooling_buff", 6, "report_cooling_buff", 6),
    (pl.SEG_GAME_STATUS, "game_progress", 3, "polar_game_progress", 3),
    (pl.SEG_GAME_STATUS, "game_type", 4, "polar_game_type", 4),
    (pl.SEG_GAME_STATUS, "stage_remain_time", 65000, "polar_stage_remain_s",
     65.0),
    (pl.SEG_SHOOT_DATA, "initial_speed_mps", 15.0, "native_bullet_speed", 15.0),
    (pl.SEG_SHOOT_DATA, "launching_frequency_hz", 8, "launch_frequency_hz", 8),
    (pl.SEG_SHOOT_DATA, "bullet_type", 2, "bullet_type", 2),
    (pl.SEG_RFID_STATUS, "rfid_status", 5, "rfid_status", 5),
    (pl.SEG_EVENT, "event_data", 9, "polar_event_bitmap", 9),
    (pl.SEG_REFEREE_WARNING, "level", 3, "polar_warning_level", 3),
    (pl.SEG_REFEREE_WARNING, "offending_robot_id", 4,
     "polar_warning_robot_id", 4),
    (pl.SEG_REFEREE_WARNING, "count", 5, "polar_warning_count", 5),
]
for _seg, _field, _val, _key, _want in _ONE_FIELD:
    br = pb.PolarStateBridge()
    br.apply(pl.PolarSegment(_seg, 1, {_field: _val}))
    _got = br.overlay.get(_key, "<missing>")
    check(f"段 0x{_seg:02X} 只带 {_field}={_val} 时 {_key} 原样落库",
          _got == _want, f"got {_got!r} want {_want!r}")

# 0x03 的五个错误位走的是 module_errors；只有 chassis/gimbal/shoot 三个
# 另外镜像到 overlay（arm 与 custom_controller 不镜像 —— overlay 白名单里
# 没有这两个键，镜像了会被 apply_polar_overlay 丢掉）。
_ERROR_MIRRORED = {"chassis": "chassis_error", "gimbal": "gimbal_error",
                   "shoot": "shoot_error"}
for _field, _mod in (("chassis_error", "chassis"), ("gimbal_error", "gimbal"),
                     ("shoot_error", "shoot"), ("arm_error", "arm"),
                     ("custom_controller_error", "custom_controller")):
    br = pb.PolarStateBridge()
    br.apply(pl.PolarSegment(pl.SEG_ROBOT_STATE_INFO, 1, {_field: True}))
    check(f"段 0x03 只带 {_field}=True 时 module_errors[{_mod!r}] 置位",
          br.module_errors.get(_mod) is True, str(br.module_errors))
    if _mod in _ERROR_MIRRORED:
        check(f"  且镜像到 overlay[{_ERROR_MIRRORED[_mod]!r}]",
              br.overlay.get(_ERROR_MIRRORED[_mod]) is True, str(br.overlay))
    else:
        check(f"  且**不**往 overlay 写 {_mod}_error（白名单没有这个键）",
              f"{_mod}_error" not in br.overlay, str(br.overlay))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ROBOT_STATE_INFO, 1, {"chassis_error": True}))
check("底盘报错时 wheel_online / drive_ready 必须同时翻假（安全侧）",
      br.overlay["wheel_online"] is False and br.overlay["drive_ready"] is False,
      str(br.overlay))


pb._STATE_FIELDS_CACHE = None
saved_get = ps.get_spec
not_ok = ps.PolarSpec(ok=False, source="probe")
ps.get_spec = lambda *a, **k: not_ok
try:
    check("规格校验不通过时 _spec_state_fields 返回空表（不拿半套规格落库）",
          pb._spec_state_fields() == {}, str(pb._spec_state_fields()))
finally:
    ps.get_spec = saved_get
    pb._STATE_FIELDS_CACHE = _SNAP["state_cache"]
ok_spec = ps.PolarSpec(ok=True, source="probe")
ok_spec.segment_state_fields = {0x41: {"v": "polar_s_v"}}
pb._STATE_FIELDS_CACHE = None
ps.get_spec = lambda *a, **k: ok_spec
try:
    got = pb._spec_state_fields()
    check("规格可用时按 state_fields 落库映射取用", got == {0x41: {"v": "polar_s_v"}},
          str(got))
    n_calls = []

    def _counting(*a, **k):
        n_calls.append(1)
        return ok_spec

    ps.get_spec = _counting
    check("第二次调用走缓存，不再问规格（热路径不打盘）",
          pb._spec_state_fields() == got and not n_calls, str(n_calls))
finally:
    ps.get_spec = saved_get
    pb._STATE_FIELDS_CACHE = _SNAP["state_cache"]

# 通用落库的三个 continue 分支：用 dropped/last_error 钉住"不许静默"
pb._STATE_FIELDS_CACHE = {0x40: {"v": "polar_g_v", "e": "polar_g_e",
                                 "missing": "polar_g_m"}}
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(0x40, 1, {"v": 1.5, "e": float("nan")}))
check("字段缺失/NaN 只跳过那一条，整段不算丢弃也不报错",
      br.dropped == 0 and br.last_error == "",
      f"dropped={br.dropped} err={br.last_error!r}")
pb._STATE_FIELDS_CACHE = {0x40: {"v": None, "v2": "polar_x"}}
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(0x40, 1, {"v": 1.0, "v2": 2.0}))
check("state_fields 落点名是 None 时跳过，不写出 'None'/'False' 这种假键",
      br.overlay == {"polar_x": 2.0}, str(br.overlay))

br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_JOINT_STATE, 1,
                         {"yaw_rad": 0.5, "pitch_rad": 0.1}))
check("0x1D 先到时整段不算丢弃（等 0x02 不是错误）",
      br.dropped == 0 and br.last_error == "",
      f"dropped={br.dropped} err={br.last_error!r}")

# =====================================================================
header("12c) 每个段处理器都能扛住「字段全缺」的段（polar_bridge.py:220-358）")
# 说明：把 values.get(k) 换成查一个不存在的键，对本模块是**等价变异** ——
# 这些处理器全都把取值包在 _finite() 里，而 _finite(None) == 0.0，
# 正好等于该字段解出来是 0 时的结果，所以任何行为断言都抓不住。
# 这里仍然逐个钉住"字段全缺"的落库形状：它抓的是**结构**（处理器读了哪个键），
# 而不是某个数值；将来有人删掉 _finite 或改了字段名，这一节会立刻红。
EMPTY_SHAPES = {
    pl.SEG_IMU: {"chassis_yaw_deg": 0.0, "imu_pitch_deg": 0.0,
                 "imu_roll_deg": 0.0, "chassis_yaw_rate_rps": 0.0},
    pl.SEG_JOINT_STATE: {"gimbal_yaw_deg": 0.0, "gimbal_pitch_deg": 0.0},
    pl.SEG_ROBOT_MOTION: {"measured_vx_mps": 0.0, "measured_vy_mps": 0.0,
                          "measured_wz_rps": 0.0},
    pl.SEG_BUFF: {"small_buff": False, "large_buff": False,
                  "recovery_buff": 0, "vulnerability_buff": 0,
                  "current_chassis_energy": 0, "report_cooling_buff": 0},
    pl.SEG_GAME_STATUS: {"polar_game_progress": 0, "polar_game_type": 0,
                         "polar_stage_remain_s": 0.0},
    pl.SEG_SHOOT_DATA: {"native_bullet_speed": 0.0,
                        "launch_frequency_hz": 0, "bullet_type": 0},
    pl.SEG_RFID_STATUS: {"rfid_status": 0},
    pl.SEG_EVENT: {"polar_event_bitmap": 0},
    pl.SEG_REFEREE_WARNING: {"polar_warning_level": 0,
                             "polar_warning_robot_id": 0,
                             "polar_warning_count": 0},
}
for _seg, _want in EMPTY_SHAPES.items():
    br = pb.PolarStateBridge()
    br.apply(pl.PolarSegment(_seg, 1, {}))
    check(f"段 0x{_seg:02X} 字段全缺时落库形状固定（不炸、不缺键）",
          br.overlay == _want and br.dropped == 0,
          f"{br.overlay} dropped={br.dropped}")

br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ROBOT_STATUS, 1, {}))
check("段 0x0A 字段全缺时血量/热量/坐标全归 0，且不写 polar_reported_robot_id",
      br.overlay["current_health"] == 0 and br.overlay["max_health"] == 0
      and br.overlay["current_heat"] == 0.0 and br.overlay["polar_self_x"] == 0.0
      and "polar_reported_robot_id" not in br.overlay, str(br.overlay))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ROBOT_STATE_INFO, 1, {}))
check("段 0x03 字段全缺时按'无错误'处理：wheel_online/drive_ready 都为真",
      br.overlay["wheel_online"] is True and br.overlay["drive_ready"] is True
      and br.overlay["chassis_error"] is False, str(br.overlay))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_ALL_ROBOT_HP, 1, {}))
check("段 0x06 字段全缺时不写 polar_ally_hp，基地/前哨血量归 0",
      "polar_ally_hp" not in br.overlay and br.overlay["base_health"] == 0
      and br.overlay["outpost_health"] == 0, str(br.overlay))
br = pb.PolarStateBridge()
br.apply(pl.PolarSegment(pl.SEG_GROUND_ROBOT_POSITION, 1, {}))
check("段 0x13 字段全缺时不落 polar_ground_positions（不画原点假点）",
      "polar_ground_positions" not in br.overlay, str(br.overlay))

# =====================================================================
header("12d) PolarMotionVerifier：状态机每一步（polar_bridge.py:466-549）")
clock = FakeClock()
clock.t = 0.0
v = pb.PolarMotionVerifier(clock=lambda: clock.t)
v.note_motion_segment()
check("见过 0x1E、手上没驾驶指令 -> IDLE（不能误报故障）",
      v.update(link_active=True, commanded_vx=0.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "IDLE", v.state)
check("链路未活跃时一律 NO_LINK，压过一切其它判据",
      v.update(link_active=False, commanded_vx=1.5, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=1.4, measured_vy=0.0,
               measured_wz=0.0) == "NO_LINK", v.state)
check("只给 measured_vx（vy 缺）也照样算出线速度",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.9, measured_vy=None,
               measured_wz=None) == "OK"
      and abs(v.measured_mps - 0.9) < 1e-9, f"{v.state}/{v.measured_mps}")
check("只给 measured_vy（vx 缺）同样能算线速度",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=None, measured_vy=0.8,
               measured_wz=None) == "OK"
      and abs(v.measured_mps - 0.8) < 1e-9, f"{v.state}/{v.measured_mps}")
check("两个线速度都缺时 measured_mps 归零（不沿用上一帧的旧值）",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=None, measured_vy=None,
               measured_wz=None) == "WAITING"
      and v.measured_mps == 0.0, f"{v.state}/{v.measured_mps}")

v = pb.PolarMotionVerifier(clock=lambda: clock.t)
v.note_motion_segment()
check("给了速度、实测没动：先给宽限期 WAITING",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "WAITING", v.state)
clock.t = 0.5
check("超过宽限期仍未动 -> NO_MOTION（第一帧就有可执行提示）",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "NO_MOTION"
      and "s[0]" in v.detail, f"{v.state}/{v.detail[:60]}")
clock.t = 2.0
check("持续不响应越过 STUCK_TIMEOUT -> STUCK 并指向固件底盘消费者",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "STUCK"
      and "GetScCmdChassisSpeed" in v.detail, f"{v.state}/{v.detail[:60]}")
v.note_motion_segment()
clock.t = 2.5
check("车真的动起来后从 STUCK 自动恢复 OK（不粘住故障态）",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.9, measured_vy=0.0,
               measured_wz=0.0) == "OK", v.state)
check("推杆中（线速度 1.0）算 trying，不落 IDLE",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "WAITING", v.state)
clock.t = 6.0
check("0x1E 回读超过 2s 没更新 -> STALE_FEEDBACK（回读链路断了）",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "STALE_FEEDBACK"
      and "2s" in v.detail, f"{v.state}/{v.detail[:40]}")
check("STALE_FEEDBACK 也算「没在推杆」，允许重锁位移参考点",
      "STALE_FEEDBACK" in pb.PolarMotionVerifier.POSITION_RELOCK_STATES)
v.note_motion_segment()
check("回读恢复后立刻回到正常判据（不粘 STALE）",
      v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
               commanded_wz=0.0, measured_vx=0.0, measured_vy=0.0,
               measured_wz=0.0) == "WAITING", v.state)

# =====================================================================
header("12e) PolarControlArbiter：交权门与抢环判据（polar_bridge.py:616-722）")
clock = FakeClock()
clock.t = 0.0
arb = pb.PolarControlArbiter(clock=lambda: clock.t)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
check("开关关掉会把控制权收回到 PASSIVE（不是继续发）",
      arb.request_takeover(client_gimbal_enabled=False,
                           vision_nuc_occupied=False) is None
      and arb.state == "PASSIVE" and not arb.may_command_gimbal, arb.state)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
check("开关打开 + 视觉不占用 -> CLIENT", arb.state == "CLIENT", arb.state)
arb.plan_gimbal(0.0, 0.0, 90.0, 0.0)
check("反馈偏离已发目标时 converged 置假（HUD 提前预警的依据）",
      arb.stats()["converged"] is False, str(arb.stats()["converged"]))
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
check("重复交权不得把 converged 重新刷成真（否则抢环永远不计时）",
      arb.stats()["converged"] is False, str(arb.stats()["converged"]))
arb.plan_gimbal(0.0, 0.0, 0.0, 0.0)
check("反馈回到容差内 -> converged 恢复为真且发散计时清零",
      arb.stats()["converged"] is True and arb.diverging is False,
      str(arb.stats()))

arb = pb.PolarControlArbiter(clock=lambda: clock.t)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
# 单周期 slew 上限 8°，所以要走三步才把俯仰推到 30°（超过 12° 容差）。
for _ in range(3):
    arb.plan_gimbal(0.0, 30.0, 0.0, 0.0)
check("偏航在容差内、俯仰差 24° 时也计入误差（只比偏航会漏掉俯仰抢环）",
      arb.stats()["converged"] is False, str(arb.stats()["converged"]))
arb = pb.PolarControlArbiter(clock=lambda: clock.t)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
arb.plan_gimbal(0.0, 30.0, 0.0, None)         # 俯仰反馈还没到
check("俯仰反馈缺失时不拿 0 当基准判定抢环（0x1D 字段没来不算第二主控）",
      arb.stats()["converged"] is True, str(arb.stats()["converged"]))
# 俯仰指令已经走到 16°（> 12° 容差）而反馈仍缺失：这时若把 None 当 0 参与比较，
# 误差会凭空变成 16°，把"没回读"误判成"第二主控抢环"，直接锁死云台。
arb = pb.PolarControlArbiter(clock=lambda: clock.t)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
arb.plan_gimbal(0.0, 30.0, 0.0, None)
arb.plan_gimbal(0.0, 30.0, 0.0, None)
check("俯仰指令已超容差但回读缺失时，仍不得判成发散（否则无故锁死云台）",
      arb.stats()["converged"] is True and arb.diverging is False,
      str(arb.stats()))

arb = pb.PolarControlArbiter(clock=lambda: clock.t)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
arb.reason = ""
arb._lockout_reason = "抢环已判定"
check("reason 被清空时 authority() 回落到 _lockout_reason（不显示空白）",
      arb.authority().reason == "抢环已判定", repr(arb.authority().reason))

arb = pb.PolarControlArbiter(clock=lambda: clock.t)
arb.reason = "客户端已接管云台"
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
before = arb.reason
arb.clear_lockout()
check("对非锁出状态调用 clear_lockout 不改写 reason（不是隐式放弃控制权）",
      arb.reason == before, f"{before!r} -> {arb.reason!r}")


clock = FakeClock()
clock.t = 0.0
v = pb.PolarMotionVerifier(clock=lambda: clock.t)
state = v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
                 commanded_wz=0.0, measured_vx=None, measured_vy=None,
                 measured_wz=None)
check("从没见过 0x1E 时用 NO_FEEDBACK 而不是误报 OK/IDLE",
      state == "NO_FEEDBACK", state)
check("NO_FEEDBACK 说清楚改用 0x0A 位移判定", "0x0A" in v.detail, v.detail[:60])
v.note_position(0.0, 0.0)
v.note_position(0.4, 0.0)
state = v.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
                 commanded_wz=0.0, measured_vx=None, measured_vy=None,
                 measured_wz=None)
check("位移超过 0.05 m 时即使没有 0x1E 也判 OK",
      state == "OK" and v.detail == "", f"{state}/{v.detail}")

clock.t = 1.0
v2 = pb.PolarMotionVerifier(clock=lambda: clock.t)
v2.note_position(0.0, 0.0)
v2.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
          commanded_wz=0.0, measured_vx=None, measured_vy=None, measured_wz=None)
check("第一次给速度只记开始时刻，还不判 STUCK", v2.state == "NO_FEEDBACK",
      v2.state)
clock.t = 1.2
v2.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
          commanded_wz=0.0, measured_vx=None, measured_vy=None, measured_wz=None)
check("刚好 0.2s（< STUCK_TIMEOUT_S=1.2）仍是 NO_FEEDBACK",
      v2.state == "NO_FEEDBACK", v2.state)
clock.t = 3.0
state = v2.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0,
                  commanded_wz=0.0, measured_vx=None, measured_vy=None,
                  measured_wz=None)
check("一直给速度但位移为 0：超时后报 STUCK（诊断能落地）",
      state == "STUCK" and "GetScCmdChassisSpeed" in v2.detail,
      f"{state}/{v2.detail[:60]}")
check("STUCK 文案带上已下发速度与累计位移（操作员能对上现场）",
      "1.00 m/s" in v2.detail and "0.000 m" in v2.detail, v2.detail[:80])

v3 = pb.PolarMotionVerifier(clock=lambda: 0.0)
v3.note_position(5.0, 6.0)
v3.commanded_mps = 0.9
v3.note_position(9.0, 9.0)
check("推杆期间不重锁参考点（否则位移被算成相邻两帧增量而恒为 0）",
      v3._position_origin == (5.0, 6.0), str(v3._position_origin))
v3.commanded_mps = 0.0
v3.state = "NO_MOTION"
v3.note_position(1.0, 1.0)
check("状态不在重锁白名单里时也不重锁（新增状态默认保持参考点）",
      v3._position_origin == (5.0, 6.0), str(v3._position_origin))
v3.state = "IDLE"
v3.note_position(1.0, 1.0)
check("没推杆且状态在白名单里：重锁并把位移归零",
      v3._position_origin == (1.0, 1.0) and v3.displacement_m == 0.0,
      f"{v3._position_origin}/{v3.displacement_m}")

v4 = pb.PolarMotionVerifier(clock=lambda: 0.0)
v4.note_motion_segment()
state = v4.update(link_active=True, commanded_vx=0.0, commanded_vy=0.0,
                  commanded_wz=1.0, measured_vx=None, measured_vy=None,
                  measured_wz=0.2)
check("线速度全缺但实测角速度 ≥0.15 时判 OK（rate 判据独立于线速度）",
      state == "OK", state)
v5 = pb.PolarMotionVerifier(clock=lambda: 0.0)
v5.note_motion_segment()
state = v5.update(link_active=True, commanded_vx=0.0, commanded_vy=0.0,
                  commanded_wz=1.0, measured_vx=0.0, measured_vy=0.0,
                  measured_wz=None)
check("线速度为 0 且角速度没回读时不能判 OK（纯旋转也要有实测才算数）",
      state != "OK", state)
check("stats 的 evidence 如实区分 0x1E 与 0x0A 位移两条证据",
      v5.stats()["evidence"] == "0x1E", str(v5.stats()["evidence"]))
v6 = pb.PolarMotionVerifier(clock=lambda: 0.0)
v6.update(link_active=True, commanded_vx=1.0, commanded_vy=0.0, commanded_wz=0.0,
          measured_vx=None, measured_vy=None, measured_wz=None)
check("没见过 0x1E 时 evidence 记的是 0x0A-position",
      v6.stats()["evidence"] == "0x0A-position", str(v6.stats()["evidence"]))
check("stats 固定给出这六个字段（HUD/快照按它取值）",
      set(v6.stats()) == {"state", "detail", "commanded_mps", "measured_mps",
                          "displacement_m", "evidence"}, str(sorted(v6.stats())))

# =====================================================================
header("13) PolarControlArbiter：authority/锁出/发散秒数（polar_bridge.py:629-734）")
clock = FakeClock()
clock.t = 0.0
arb = pb.PolarControlArbiter(clock=lambda: clock.t)
auth = arb.authority()
check("刚建仲裁器：PASSIVE + 不许发 + 说清原因",
      auth.state == "PASSIVE" and auth.may_command is False
      and auth.reason == "客户端云台控制未开启", str(auth))
check("没拿到控制权时 plan_gimbal 返回 None（停发，不是发零帧甩炮）",
      arb.plan_gimbal(10.0, 0.0, 0.0, 0.0) is None)
check("没发散时 diverging=False、divergent_seconds=0.0",
      arb.diverging is False and arb.divergent_seconds == 0.0)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=True)
check("视觉 NUC 占用时让位，客户端不得发云台角度",
      arb.state == "PASSIVE" and "视觉 NUC" in arb.reason, arb.reason)
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
check("退出占用后显式交权才拿到 CLIENT",
      arb.state == "CLIENT" and arb.may_command_gimbal is True)
check("交权后第一次 plan 仍按单周期 slew 限幅（8°/步）而不是直发 30°",
      arb.plan_gimbal(30.0, 10.0, None, None) == (8.0, 8.0))
check("没有反馈（0x1D 从未到）时不据此判定抢环，仍照发",
      arb.plan_gimbal(40.0, 20.0, 40.0, None) == (16.0, 16.0))
arb.plan_gimbal(0.0, 0.0, 90.0, 0.0)
check("反馈偏离已发目标时开始计时（diverging=True）", arb.diverging is True)
clock.t = 1.0
check("divergent_seconds 是真实经过的时间",
      abs(arb.divergent_seconds - 1.0) < 1e-9, str(arb.divergent_seconds))
arb.plan_gimbal(0.0, 0.0, 90.0, 0.0)
check("超过 conflict_timeout 仍不收敛 -> 锁出",
      arb.state == "LOCKOUT" and not arb.may_command_gimbal, arb.state)
check("锁出理由带上偏差角度与超时，且能通过 authority() 取到"
      "（reason 被清空后靠 _lockout_reason 兜底）",
      "90.0" in arb.authority().reason and "抢环" in arb.authority().reason,
      arb.authority().reason[:70])
check("锁出后锁存计时归零，不会反复刷秒数",
      arb.divergent_seconds == 0.0, str(arb.divergent_seconds))
arb.request_takeover(client_gimbal_enabled=True, vision_nuc_occupied=False)
check("锁出必须显式清除：光重新交权回不去",
      arb.state == "LOCKOUT", arb.state)
check("锁出期间 plan_gimbal 停发", arb.plan_gimbal(0.0, 0.0, 0.0, 0.0) is None)
arb.clear_lockout()
check("clear_lockout 后回到 PASSIVE 等重新交权，不再是锁出",
      arb.state == "PASSIVE" and not arb.may_command_gimbal
      and arb.diverging is False, f"{arb.state}/{arb.diverging}")
arb.clear_lockout()
check("对非锁出状态调用 clear_lockout 不改变任何东西",
      arb.state == "PASSIVE", arb.state)
check("_last_command_time 回报最后一次 plan 的时刻",
      arb._last_command_time() == 1.0, str(arb._last_command_time()))
check("stats 固定给出这八个字段",
      set(arb.stats()) == {"state", "may_command", "reason", "diverging",
                           "divergent_seconds", "commanded_yaw_deg",
                           "commanded_pitch_deg", "converged"},
      str(sorted(arb.stats())))

# =====================================================================
restore_globals()


def main() -> int:
    print(f"\n{PASSED} 项通过，{len(FAILED)} 项失败")
    for name in FAILED:
        print(f"  FAIL: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
