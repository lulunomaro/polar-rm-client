"""`polar_spec.load_spec` 的坏规格路径：必须**记错**而不是**抛异常**。

跑法：``python tests/polar_spec_bad_input_test.py``

背景
----
``polar_spec`` 是**数据入口**。电控改固件协议只改 JSON，但 JSON 会写坏；
写坏了会怎样，完全取决于下面这几个 ``except`` / 类型守卫：

* ``_as_int``             ``except ValueError``            第 82 行
* ``_validate_segment``   ``except struct.error``（calcsize） 第 112 行
* ``_validate_segment``   ``except struct.error``（unpack）   第 121 行
* ``_validate_segment``   ``if isinstance(raw_map, dict)`     第 162 行
* ``_validate_uplink``    ``if isinstance(bits, dict)``       第 199 行
* ``_validate_roles``     ``except (TypeError, ValueError)``   第 253 行
* ``load_spec``           ``except OSError``                  第 290 行

变异验证（71 个套件逐分支扫，详见本轮审计报告）证明：**这七条里，
除第 112/121 行的 ``except`` 之外，其余全部没有任何一条断言钉着。**

现场表现是同一个：**规格坏了，要么整份拒收回退内置表
（HUD 上所有字段都是 0、遥测全灭，但没有人报错），要么异常从
``get_spec()`` 冲出去把队内链路启动搞砖。**

本文件把每条守卫的两侧都钉住：坏输入必须被**记错并拒收**，
好输入必须**照常通过** —— 只钉一侧的话，"改成更严格"不会被发现。
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rm_client.backend import polar_spec as ps  # noqa: E402

FAILURES: list[str] = []
PASSED = 0
TMP = Path(tempfile.mkdtemp(prefix="spec_bad_"))

BUNDLED = ROOT / "rm_client" / "backend" / "polar_protocol.json"
BASE = json.loads(BUNDLED.read_text(encoding="utf-8"))

_DEL = object()


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


def variant(*, segments=None, roles=None, fallback_role="KEEP", uplink="KEEP", drop_top=()):
    d = json.loads(json.dumps(BASE))
    if segments is not None:
        d["downlink_segments"] = segments
    if roles is not None:
        d["roles"] = roles
    if fallback_role == "DROP":
        d.pop("fallback_role", None)
    elif fallback_role != "KEEP":
        d["fallback_role"] = fallback_role
    if uplink != "KEEP":
        d["uplink"] = uplink
    for key in drop_top:
        d.pop(key, None)
    return d


def seg(**kw):
    """拿内置规格里 0x02 IMU 段当模板改几个键。"""
    d = json.loads(json.dumps(BASE["downlink_segments"][1]))
    d.update(kw)
    return d


def with_seg(**kw):
    """把 0x02 IMU 段换成改过的版本，其余段原样保留。

    注意：这里从 ``[2:]`` 开始 —— 内置列表里 0x02 自己就在下标 1，
    若从 ``[1:]`` 开始会把它自己再追加一次，坏段就被原版覆盖回去了。
    """
    return [seg(**kw)] + BASE["downlink_segments"][2:]


def load(obj_or_text, name="spec.json"):
    p = TMP / name
    if isinstance(obj_or_text, str):
        p.write_text(obj_or_text, encoding="utf-8")
    else:
        p.write_text(json.dumps(obj_or_text, ensure_ascii=False), encoding="utf-8")
    return ps.load_spec(p)


def load_safe(obj_or_text, name="spec.json"):
    """load_spec 的契约就是"不抛"，所以这里把抛异常也算成一条失败。"""
    if obj_or_text is _NO_ARG:
        try:
            return ps.load_spec(), ""
        except Exception as exc:  # noqa: BLE001
            return ps.PolarSpec(), repr(exc)
    if isinstance(obj_or_text, Path):
        try:
            return ps.load_spec(obj_or_text), ""
        except Exception as exc:  # noqa: BLE001
            return ps.PolarSpec(), repr(exc)
    try:
        return load(obj_or_text, name), ""
    except Exception as exc:  # noqa: BLE001
        return ps.PolarSpec(), repr(exc)


def errs(spec):
    return [f"{e.where}|{e.message}" for e in spec.errors]


class _NoArg:
    """load_spec 的默认参数这条路（``path`` 省略）只能这样走得到。"""

    def __fspath__(self):
        raise AssertionError("_NO_ARG 只是占位，不该被当成路径")


_NO_ARG = object()


# ======================================================================
# 1. `_as_int`：解析不了就退到 default（既不抛，也不默默给 0）
# ======================================================================


def test_as_int_falls_back_to_default() -> None:
    print("\n[1] _as_int 遇到解析不了的输入：退到 default")
    check("坏字符串退到调用方给的 default", ps._as_int("garbage", -7) == -7,
          repr(ps._as_int("garbage", -7)))
    check("坏字符串在 default=0 时退到 0", ps._as_int("garbage") == 0,
          repr(ps._as_int("garbage")))
    check("空串退到 default", ps._as_int("", -7) == -7, repr(ps._as_int("", -7)))
    check("None 退到 default", ps._as_int(None, -7) == -7, repr(ps._as_int(None, -7)))
    check("浮点**不**被截断成 int（退到 default）", ps._as_int(3.9, -7) == -7,
          repr(ps._as_int(3.9, -7)))
    check("列表退到 default", ps._as_int([1, 2], -7) == -7, repr(ps._as_int([1, 2], -7)))
    check("dict 退到 default", ps._as_int({"a": 1}, -7) == -7, repr(ps._as_int({"a": 1}, -7)))
    # 文档字符串承诺的两种写法都要能用（"既可以写 10 也可以写 0x0A"）
    check("十六进制串 '0x0A' == 10", ps._as_int("0x0A") == 10, repr(ps._as_int("0x0A")))
    check("**十进制**串 '10' == 10（不是 16）", ps._as_int("10") == 10, repr(ps._as_int("10")))
    check("十进制串 '255' == 255（不是 0x255=597）", ps._as_int("255") == 255,
          repr(ps._as_int("255")))
    check("八进制/二进制串 '0b101' == 5", ps._as_int("0b101") == 5, repr(ps._as_int("0b101")))
    check("大小写与空白不敏感", ps._as_int("  0x0a  ") == 10, repr(ps._as_int("  0x0a  ")))
    check("bool 当作 int 处理", ps._as_int(True, -7) == 1, repr(ps._as_int(True, -7)))


def test_bad_mask_string_is_rejected() -> None:
    """掩码写成非数字串 -> 整份拒收，而不是"掩码变 0 于是全放行/全不放行"。"""
    print("\n[2] drive_key_mask 写坏：必须拒收")
    bad = variant(uplink={**BASE["uplink"],
                          "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                         "drive_key_mask": "zzz"}})
    s, raised = load_safe(bad, "mask_bad.json")
    check("load_spec 没抛", raised == "", raised)
    check("掩码非法 -> ok=False", s.ok is False, f"ok={s.ok} {errs(s)}")
    check("错误里点名了 drive_key_mask",
          any("drive_key_mask" in e for e in errs(s)), errs(s))
    check("drive_key_mask 没有被当成 0 收下（ok=False 时派生表不可用）",
          s.ok is False and "掩码非法" in s.error_text(), s.error_text()[:120])


# ======================================================================
# 3/4. struct 格式串：calcsize 抛 与 unpack 抛 是两回事
# ======================================================================


def test_bad_struct_format_is_reported_not_raised() -> None:
    print("\n[3] struct 格式串非法（calcsize 抛）：记错 + 拒收该段 + load_spec 不抛")
    s, raised = load_safe(variant(segments=with_seg(struct="QZ", inputs=["roll", "pitch", "yaw",
                                                                       "vx", "vy", "vz"])),
                          "badfmt.json")
    check("load_spec 没抛", raised == "", raised)
    check("整份 ok=False", s.ok is False, f"ok={s.ok}")
    check("错误里点名 struct 格式串非法",
          any("struct 格式串非法" in e for e in errs(s)), errs(s))
    check("坏段没有进 segment_formats（否则会用错格式解包）",
          0x02 not in s.segment_formats, sorted(s.segment_formats))
    check("坏段也没有进 segment_names", 0x02 not in s.segment_names,
          sorted(s.segment_names))
    check("其余段照常登记（半套生效被禁止）", 0x03 in s.segment_formats,
          sorted(s.segment_formats))


def test_missing_struct_mentions_the_right_reason() -> None:
    print("\n[4] struct 键整个缺失：理由必须是「缺少格式串」而不是别的")
    s, raised = load_safe(variant(segments=with_seg(struct="")), "missing_struct.json")
    check("load_spec 没抛", raised == "", raised)
    check("ok=False", s.ok is False, f"ok={s.ok}")
    check("理由是「缺少 struct 格式串」",
          any("缺少 struct 格式串" in e for e in errs(s)), errs(s))


def test_unpack_handler_is_unreachable() -> None:
    """第 119-122 行那个 ``except struct.error`` **够不着**。

    缓冲长度是用 ``struct.calcsize(fmt)`` 算出来的，喂进去正好是
    ``Struct(fmt).unpack`` 需要的字节数 —— 所以 unpack 不可能抛
    ``struct.error``。本轮把常见格式串与 3 字符以内的组合全试了一遍，
    **零个**格式能走到那个 handler。

    所以这条不是"没写测试"，是"写不出来"。本文件把它记成
    "够不着但朝 fail-open 方向坏"：一旦哪天有人把缓冲改成定长，
    它就会从够不着变成够得着，所以这里钉住今天的事实。
    """
    print("\n[5] 第 121 行那个 except struct.error 够不着（不是「漏测」）")
    s, raised = load_safe(variant(segments=with_seg(struct="s", inputs=["only"])),
                          "struct_s.json")
    check("load_spec 没抛", raised == "", raised)
    check("'s' 段被收进 segment_formats（calcsize('s')=1，解 1 个零字节正好够）",
          s.segment_formats.get(0x02) == "s" and struct.calcsize("s") == 1,
          (dict(s.segment_formats), struct.calcsize("s")))
    check("整份仍然 ok", s.ok is True, f"ok={s.ok} {errs(s)[:3]}")

    # 事实核对：对任意被 struct 接受的格式，calcsize 长度的零缓冲都能解开
    unreachable = []
    for fmt in ("b", "B", "h", "H", "i", "I", "l", "L", "q", "Q", "n", "N",
                "f", "d", "?", "c", "s", "p", "0s", "2s", "5h",
                "<ffffff", "<HB", "<BHQ", "<IB", "<fff", "<4B", "<10f", "<ff", "8H"):
        try:
            size = struct.calcsize(fmt)
            struct.Struct(fmt).unpack(b"\x00" * size)
        except struct.error as exc:
            unreachable.append((fmt, str(exc)))
    check("没有任何一种常见格式能让 unpack 抛 struct.error（所以第 121 行够不着）",
          unreachable == [], unreachable)


# ======================================================================
# 6. outputs 校验循环：非数组 / 非对象 / 缺 name / 引用不存在的输入 / width 越界
# ======================================================================


def test_outputs_validation() -> None:
    print("\n[6] outputs 的五条校验规则（for 循环整条一次都没被走到过）")
    cases = [
        ("outputs_not_list", with_seg(outputs={"a": 1}), "outputs 不是数组"),
        ("out_not_dict", with_seg(outputs=["x"]), "输出声明非法"),
        ("out_no_name", with_seg(outputs=[{"from": "roll"}]), "输出声明非法"),
        ("out_bad_src", with_seg(outputs=[{"name": "x", "from": "nope"}]), "引用了不存在的输入"),
        ("width_0", with_seg(outputs=[{"name": "roll", "width": 0}]), "width=0 越界"),
        ("width_33", with_seg(outputs=[{"name": "roll", "width": 33}]), "width=33 越界"),
    ]
    for name, segments, needle in cases:
        s, raised = load_safe(variant(segments=segments), f"{name}.json")
        check(f"{name}: load_spec 没抛", raised == "", raised)
        check(f"{name}: 记了错（含「{needle}」）",
              any(needle in e for e in errs(s)), errs(s))
        check(f"{name}: 整份 ok=False", s.ok is False, f"ok={s.ok}")
        check(f"{name}: 坏段没进 segment_outputs", 0x02 not in s.segment_outputs,
              sorted(s.segment_outputs))

    check("load_spec() 不传参时用 SPEC_PATH（这条回退分支只在这条路上走到）",
          load_safe(_NO_ARG)[0].ok is True, "ok=False")
    check("load_spec() 与 load_spec(SPEC_PATH) 结果一致",
          load_safe(_NO_ARG)[0].segment_formats
          == load_safe(ps.SPEC_PATH)[0].segment_formats, "不一致")
    check("load_spec() 的 source 就是 SPEC_PATH",
          load_safe(_NO_ARG)[0].source == str(ps.SPEC_PATH), load_safe(_NO_ARG)[0].source)

    print("      ---- 顶层必需键 / 段表 ----")
    s, raised = load_safe(variant(segments=with_seg(
        outputs=[{"name": "roll", "from": "roll", "width": 1},
                 {"name": "pitch", "from": "pitch", "width": 32}])), "outputs_ok.json")
    check("合法 outputs：load_spec 没抛", raised == "", raised)
    check("合法 outputs：ok=True", s.ok is True, f"ok={s.ok} {errs(s)}")
    check("合法 outputs：进了 segment_outputs",
          len(s.segment_outputs.get(0x02, [])) == 2, s.segment_outputs.get(0x02))
    s, raised = load_safe(variant(segments=with_seg(outputs=None)), "outputs_none.json")
    check("outputs 缺失（None）按空数组处理且通过",
          s.ok is True and s.segment_outputs.get(0x02) == [], f"ok={s.ok} {errs(s)}")


def test_state_fields_validation() -> None:
    print("\n[7] state_fields：必须是对象、键值非空、且引用已声明的输出")
    s, raised = load_safe(variant(segments=with_seg(
        outputs=[{"name": "roll"}], state_fields={"nope": "X"})), "sf_unknown.json")
    check("引用未声明输出 -> 记错", raised == "" and any("state_fields 引用了未声明" in e
                                                  for e in errs(s)), f"{raised} {errs(s)}")
    s, raised = load_safe(variant(segments=with_seg(
        outputs=[{"name": "roll"}], state_fields={"roll": "X"})), "sf_ok.json")
    check("合法 state_fields -> ok=True 且登记",
          s.ok is True and s.segment_state_fields.get(0x02) == {"roll": "X"},
          f"ok={s.ok} {s.segment_state_fields}")
    s, raised = load_safe(variant(segments=with_seg(
        outputs=[{"name": "roll"}], state_fields={"roll": ""})), "sf_empty.json")
    check("值为空的条目被过滤掉（且不留空登记）",
          s.ok is True and 0x02 not in s.segment_state_fields,
          f"ok={s.ok} {s.segment_state_fields}")
    s, raised = load_safe(variant(segments=with_seg(
        outputs=[{"name": "roll"}], state_fields={"": "X"})), "sf_emptykey.json")
    check("键为空的条目被过滤掉（且不留空登记）",
          s.ok is True and 0x02 not in s.segment_state_fields,
          f"ok={s.ok} {s.segment_state_fields}")
    s, raised = load_safe(variant(segments=with_seg(state_fields=["x"])), "sf_notdict.json")
    check("state_fields 不是对象 -> 当作没写，不炸（今天的行为）",
          raised == "" and s.ok is True, f"{raised} ok={s.ok}")
    check("state_fields 不是对象时也不登记", 0x02 not in s.segment_state_fields,
          sorted(s.segment_state_fields))
    s, raised = load_safe(variant(segments=with_seg(state_fields={})), "sf_emptydict.json")
    check("state_fields 为空 dict -> 不登记空条目（下游按 key 存在与否区分）",
          s.ok is True and 0x02 not in s.segment_state_fields, sorted(s.segment_state_fields))


def test_key_bits_not_a_dict_is_ignored() -> None:
    """`"key_bits": ["a"]` 今天被 isinstance 挡下；挡不住就是 AttributeError。"""
    print("\n[8] uplink.key_bits 不是对象：忽略而不是崩")
    bad = variant(uplink={**BASE["uplink"],
                          "virtual_rc": {**BASE["uplink"]["virtual_rc"], "key_bits": ["a"]}})
    s, raised = load_safe(bad, "key_bits_bad.json")
    check("load_spec 没抛", raised == "", raised)
    check("key_bits 保持空（掩码仍照常解析）", s.key_bits == {}, dict(s.key_bits))
    check("整份仍 ok（掩码 0x000F 合法）", s.ok is True, f"ok={s.ok} {errs(s)}")
    check("drive_key_mask 照常拿到", s.drive_key_mask == 0x000F, hex(s.drive_key_mask))


def test_nameless_segment_gets_a_generated_name() -> None:
    """`or f"segment_{index}"` 那条回退：段没写 name 时按序号补名。"""
    print("\n[9] 段缺 name：按 segment_<序号> 补名")
    s, raised = load_safe(variant(segments=[{"id": "0x02", "struct": "<ffffff",
                                            "inputs": ["a", "b", "c", "d", "e", "f"]}]
                                      + BASE["downlink_segments"][2:]), "no_name.json")
    check("load_spec 没抛", raised == "", raised)
    check("整份 ok=True", s.ok is True, f"ok={s.ok} {errs(s)}")
    check("补出来的名字是 segment_0（不是字面量 'None'）",
          s.segment_names.get(0x02) == "segment_0", s.segment_names.get(0x02))
    check("空字符串 name 也走补名这条路",
          load_safe(variant(segments=with_seg(name="")))[0].segment_names.get(0x02)
          == "segment_0",
          load_safe(variant(segments=with_seg(name="")))[0].segment_names.get(0x02))


# ======================================================================
# 10. roles / fallback_role / uplink 顶层
# ======================================================================


def test_roles_validation() -> None:
    print("\n[10] roles：对象、编号、限速")
    for label, mutate, needle in [
        ("空数组", lambda d: [], "缺失或为空"),
        ("不是数组", lambda d: {}, "缺失或为空"),
        ("元素不是对象", lambda d: ["x"] + d[1:], "不是对象"),
        ("robot_id=0", lambda d: [dict(d[0], robot_id=0)] + d[1:], "robot_id 非法"),
        ("robot_id 是坏串", lambda d: [dict(d[0], robot_id="zz")] + d[1:], "robot_id 非法"),
        ("限速不是数值", lambda d: [dict(d[0], max_linear_mps="fast")] + d[1:], "限速不是数值"),
        ("限速是 None", lambda d: [dict(d[0], max_yaw_rps=None)] + d[1:], "限速不是数值"),
        ("限速是列表", lambda d: [dict(d[0], max_linear_mps=[1])] + d[1:], "限速不是数值"),
        ("限速为负", lambda d: [dict(d[0], max_linear_mps=-1.0)] + d[1:], "限速为负"),
    ]:
        s, raised = load_safe(variant(roles=mutate(BASE["roles"])), "role.json")
        check(f"{label}: load_spec 没抛", raised == "", raised)
        check(f"{label}: 记了错（含「{needle}」）",
              any(needle in e for e in errs(s)), errs(s))
        check(f"{label}: ok=False", s.ok is False, f"ok={s.ok}")
        check(f"{label}: 该兵种没进 roles（否则限幅查不到会退回内置）",
              1 not in s.roles, sorted(s.roles))

    s, raised = load_safe(variant(roles=[dict(BASE["roles"][0], max_linear_mps=3.0,
                                              max_yaw_rps=5.0)] + BASE["roles"][1:]), "role_high.json")
    check("限速高于常见值 -> 只告警，仍 ok=True", s.ok is True, f"ok={s.ok} {errs(s)}")
    check("告警点名了 roles[0] 与两个数值",
          any("roles[0]" in w.where and "3.0" in w.message for w in s.warnings),
          [f"{w.where}|{w.message}" for w in s.warnings])
    check("超限只告警时兵种仍登记", 1 in s.roles, sorted(s.roles))

    s, raised = load_safe(variant(fallback_role="DROP"), "no_fallback.json")
    check("缺 fallback_role -> ok=False", s.ok is False, f"ok={s.ok}")
    check("原因点名 fallback_role", "fallback_role" in s.error_text(), s.error_text())
    s, raised = load_safe(variant(fallback_role="nope"), "bad_fallback.json")
    check("fallback_role 不是对象 -> ok=False", s.ok is False, f"ok={s.ok}")


def test_uplink_and_top_level() -> None:
    print("\n[11] uplink / 顶层必需键 / downlink_segments")
    s, raised = load_safe(variant(uplink="nope"), "uplink_bad.json")
    check("uplink 不是对象 -> 记错", raised == "" and any("uplink" in e for e in errs(s)),
          f"{raised} {errs(s)}")
    s, raised = load_safe(variant(drop_top=("roles",)), "no_roles.json")
    check("缺 roles -> 报出缺哪些键", raised == "" and "缺少必需键" in s.error_text(),
          f"{raised} {s.error_text()}")
    check("缺 roles -> ok=False", s.ok is False, f"ok={s.ok}")
    s, _ = load_safe(variant(segments=[]), "empty_segments.json")
    check("downlink_segments 为空 -> ok=False", s.ok is False, f"ok={s.ok}")
    check("downlink_segments 为空 -> 原因点名", "downlink_segments" in s.error_text(),
          s.error_text())
    s, _ = load_safe(variant(segments={}), "segments_dict.json")
    check("downlink_segments 不是数组 -> ok=False", s.ok is False, f"ok={s.ok}")
    s, raised = load_safe(variant(segments=["not-a-dict"] + BASE["downlink_segments"][1:]),
                          "seg_notdict.json")
    check("段不是对象 -> ok=False", s.ok is False, f"ok={s.ok}")
    check("段不是对象 -> 理由点名", "不是对象" in s.error_text(), s.error_text())


def test_fire_and_mask_warnings_are_visible() -> None:
    print("\n[12] 两类告警必须留在台面上（操作员就是靠这两句话发现规格被改坏）")
    s, raised = load_safe(variant(uplink={**BASE["uplink"],
                                          "robot_cmd": {"never_set": ["w"]}}), "no_fire.json")
    check("没把 fire 列进 never_set -> 仍 ok=True", s.ok is True, f"ok={s.ok} {errs(s)}")
    check("但留下了一条提到 fire 的 warning",
          any("fire" in w.message for w in s.warnings), [w.message for w in s.warnings])
    check("warning_text() 里看得到", "fire" in s.warning_text(), s.warning_text())
    check("warning_text() 空规格时是空串", ps.PolarSpec().warning_text() == "", "非空")
    check("error_text() 空规格时是空串", ps.PolarSpec().error_text() == "", "非空")

    d = {**BASE["uplink"], "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                         "drive_key_mask": "0x001F"}}
    s, raised = load_safe(variant(uplink=d), "mask_1f.json")
    check("掩码 0x001F 本身合法 -> ok=True", s.ok is True, f"ok={s.ok} {errs(s)}")
    warn = " ".join(w.message for w in s.warnings)
    check("告警点名了具体的键位（不是空列表）",
          "SHIFT" in warn, [w.message for w in s.warnings])
    check("掩码值原样保留", s.drive_key_mask == 0x001F, hex(s.drive_key_mask))
    d = {**BASE["uplink"], "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                         "drive_key_mask": "0x0030"}}
    s, raised = load_safe(variant(uplink={**BASE["uplink"],
                                          "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                                         "drive_key_mask": "0x0030"}}),
                          "mask_30.json")
    offending = s.warning_text()
    # key_bits: W0 S1 A2 D3 SHIFT4 CTRL5 ... 0x0030 = bit4|bit5 = SHIFT+CTRL
    check("0x0030 命中的**只有** SHIFT 与 CTRL 两个键位",
          "['CTRL', 'SHIFT']" in offending, offending)
    check("没把无关的键位算进来（W/S/A/D 不在里面）",
          "'W'" not in offending and "'Q'" not in offending, offending)

    upper = variant(uplink={**BASE["uplink"],
                            "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                           "drive_key_mask": 0xFFFF}})
    check("上界 0xFFFF 合法", load_safe(upper, "mask_max.json")[0].ok is True, "ok=False")
    over = variant(uplink={**BASE["uplink"],
                           "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                          "drive_key_mask": 0x10000}})
    check("超过 0xFFFF 则拒收", load_safe(over, "mask_over.json")[0].ok is False, "ok=True")
    neg = variant(uplink={**BASE["uplink"],
                          "virtual_rc": {**BASE["uplink"]["virtual_rc"],
                                         "drive_key_mask": -1}})
    check("负掩码则拒收", load_safe(neg, "mask_neg.json")[0].ok is False, "ok=True")


def test_file_level_failures() -> None:
    print("\n[13] 文件层面：不存在 / 语法错 / 顶层不是对象 / 读不出来")
    s, raised = load_safe(TMP / "definitely_missing.json")
    check("文件不存在 -> 不抛且 ok=False", raised == "" and s.ok is False, raised)
    check("文件不存在 -> 理由是「文件不存在」", "文件不存在" in s.error_text(), s.error_text())
    s, raised = load_safe("{not json", "broken.json")
    check("JSON 语法错 -> 记「JSON 语法错误」",
          raised == "" and "JSON 语法错误" in s.error_text(), f"{raised} {s.error_text()}")
    s, raised = load_safe([1, 2, 3], "toplevel_list.json")
    check("顶层是数组 -> 记「顶层不是对象」",
          raised == "" and "顶层不是对象" in s.error_text(), f"{raised} {s.error_text()}")
    s, raised = load_safe("", "empty.json")
    check("空文件 -> ok=False（不是 ok=True 的空规格）",
          raised == "" and s.ok is False, f"{raised} ok={s.ok}")
    s, raised = load_safe("null", "null.json")
    check("顶层是 null -> ok=False", s.ok is False, f"ok={s.ok}")

    d = TMP / "a_directory"
    d.mkdir(exist_ok=True)
    s, raised = load_safe(d, "dir")
    check("路径存在但是目录（read_text 抛 OSError）-> 不抛", raised == "", raised)
    check("目录 -> 记「读取失败」", "读取失败" in s.error_text(), s.error_text())
    check("目录 -> ok=False", s.ok is False, f"ok={s.ok}")


def test_segment_id_bounds() -> None:
    print("\n[14] 段号边界")
    for label, seg_id, expect_ok in [
        ("0x00", "0x00", True), ("0xFF", "0xFF", True),
        ("0x100", "0x100", False), ("-1", "-1", False),
        ("坏串", "zzz", False), ("缺 id", None, False),
    ]:
        entry = {"name": "X", "struct": "<H", "inputs": ["v"]}
        if seg_id is not None:
            entry["id"] = seg_id
        s, raised = load_safe(variant(segments=[entry] + BASE["downlink_segments"][1:]),
                              f"id_{label}.json")
        check(f"段号 {label} -> ok={expect_ok}", s.ok is expect_ok, f"ok={s.ok} {errs(s)}")


def test_get_spec_cache_and_bypass() -> None:
    """`get_spec(path)` 是文档里写明的"测试注入替代规格"入口 —— 从来没被用过。"""
    print("\n[15] get_spec 的缓存与绕过")
    a = ps.get_spec()
    b = ps.get_spec()
    check("无参两次拿到同一个对象（缓存生效）", a is b, "不是同一个对象")
    c = ps.get_spec(BUNDLED)
    check("传 path 时绕过缓存（是另一个对象）", c is not a, "仍然返回了缓存对象")
    check("但内容一致", c.ok == a.ok and c.segment_formats == a.segment_formats, "内容不一致")
    check("缓存键就是 SPEC_PATH", ps._cache_key == str(ps.SPEC_PATH), ps._cache_key)
    d = ps.get_spec(TMP / "definitely_missing.json")
    check("传一个坏 path 也不炸，返回 ok=False", d.ok is False, f"ok={d.ok}")
    check("坏 path 的结果没有被写进缓存", ps.get_spec() is a, "缓存被污染了")


def test_bundled_spec_is_clean() -> None:
    """前提：本文件里所有"拒收"断言都建立在"内置规格本身干净"之上。"""
    print("\n[16] 内置 polar_protocol.json 必须 ok=True 且零 error")
    s = ps.get_spec()
    check("内置规格 ok=True", s.ok is True, f"ok={s.ok} {s.error_text()[:200]}")
    check("内置规格零 error", s.errors == [],
          [f"{e.where}|{e.message}" for e in s.errors][:5])
    check("内置规格零 warning", s.warnings == [],
          [f"{w.where}|{w.message}" for w in s.warnings][:5])
    check("15 个段：14 个有格式串 + 1 个 handled_special",
          len(s.segment_formats) == 14 and len(s.segment_names) == 15,
          (len(s.segment_formats), len(s.segment_names)))
    check("0x01 是 handled_special 的 Debug 段（只登记名字）",
          s.segment_names.get(1) == "Debug" and 1 not in s.segment_formats,
          (s.segment_names.get(1), s.segment_formats.get(1)))
    check("掩码是 0x000F（只放行 W/S/A/D）", s.drive_key_mask == 0x000F, hex(s.drive_key_mask))
    check("fire 在 never_set 里", "fire" in s.never_set_uplink_fields, s.never_set_uplink_fields)
    check("8 个已知兵种", sorted(s.roles) == [1, 2, 3, 4, 6, 7, 8, 9], sorted(s.roles))
    check("fallback_role 是 0 号未知编号", s.fallback_role.get("robot_id") == 0,
          s.fallback_role.get("robot_id"))
    check("key_bits 归一化成大写", "W" in s.key_bits, sorted(s.key_bits)[:5])
    check("spec_version 读成整数", s.spec_version == 1, repr(s.spec_version))


def main() -> int:
    test_as_int_falls_back_to_default()
    test_bad_mask_string_is_rejected()
    test_bad_struct_format_is_reported_not_raised()
    test_missing_struct_mentions_the_right_reason()
    test_unpack_handler_is_unreachable()
    test_outputs_validation()
    test_state_fields_validation()
    test_key_bits_not_a_dict_is_ignored()
    test_nameless_segment_gets_a_generated_name()
    test_roles_validation()
    test_uplink_and_top_level()
    test_fire_and_mask_warnings_are_visible()
    test_file_level_failures()
    test_segment_id_bounds()
    test_get_spec_cache_and_bypass()
    test_bundled_spec_is_clean()
    print()
    if FAILURES:
        print(f"polar_spec 坏规格路径：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"polar_spec 坏规格路径：全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())