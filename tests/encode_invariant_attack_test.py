"""攻击 AGENT.md 第 4 节的两条不变量：fire 恒 0、键位只放行 W/S/A/D。

# 为什么不变量 1、2 已经有测试了还要再来一遍

`polar_link_test.py::test_fire_bit_invariant`、`robot_roles_test.py`、
`polar_spec_test.py` 里都断言了 `RC_DRIVE_KEY_MASK == 0x000F`、
`RC_MAX_ALLOWED_KEY_MASK == 0x000F`、SHIFT 不在里面。**常量本身是有测试的。**

但 AGENT.md 不变量 2 写的不是"常量对不对"，而是：

> 注意"不允许"要做到**结构上不可达**，而不是仅靠调用方传 False ——
> 后者只要有一个遗漏的调用点就会漏。

"结构上不可达"是一条关于**编码路径**的断言，它得靠攻击编码器来验，
而不是靠看常量。常量对了但编码路径忘了用，是很常见的写法 ——
`RC_DRIVE_KEY_MASK` 摆在那儿，谁都能 import 进来，然后在某个角落
`key_mask=RC_DRIVE_KEY_MASK | SOMETHING` 就漏出去了。

本文件做三件事，前两件是攻，第三件是钉住"这个测试本身有效"：

1. **攻 fire**：试图通过参数、关键字、`**kwargs`、属性注入等手段把 fire 置 1；
2. **攻键位掩码**：给编码器一个"全 1"的 key_value 与一个"含禁位"的 key_mask，
   然后**把编码出来的帧解回去**，看禁位到底有没有上车；
3. **证明上面那些断言不是空转**（负证，见 `test_this_file_can_go_red`）。

# 为什么第 2 步一定要"解回去"而不是直接看返回值

编码器返回的是 bytes。如果你只断言 `effective_mask == 0x000F`，
那是在测一个中间变量，没测线上那个字节 —— 而现场出问题恰恰是
"帧里那个 bit 亮了"。所以这里走完整一趟：
编帧 → 剥帧头 → `struct.unpack` → 取 key_value 字段 → 比对。
只有这样，"禁位没上车"这句话才是关于**线上字节**的陈述。

跑法：``python tests/encode_invariant_attack_test.py``
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend import polar_link as pl  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


#: 上行 0x03 的 body 格式（polar_link.RC_CTRL_BODY_FORMAT）。
#: `key_value` 是最后一个字段 `H`，落在 body 末尾。
_KEY_VALUE_FORMAT = pl.RC_CTRL_BODY_FORMAT.split("H")[-1] or "H"
_KEY_VALUE_SIZE = struct.calcsize(_KEY_VALUE_FORMAT)


def _decode_key_value(frame: bytes) -> int:
    """把编出来的 0x03 帧解回 key_value 字段（末尾那个 ``H``）。

    刻意走"剥帧头 + unpack"而不是复用被测代码里的常量：
    这里重新按 `RC_CTRL_BODY_FORMAT` 自己算偏移，
    这样如果编码器把字段顺序改了，这里会跟着反映出来。
    """
    body = frame[pl.HEADER_SIZE + pl.TIMESTAMP_SIZE: len(frame) - pl.CRC16_SIZE]
    values = list(struct.unpack(pl.RC_CTRL_BODY_FORMAT, body))
    return int(values[-1])


def _frame_is_cmd_virtual_rc(frame: bytes) -> bool:
    return frame[2] == pl.CMD_VIRTUAL_RC


# ===========================================================================
# 1. 攻 fire：任何途径都不能把 fire 位置 1
# ===========================================================================

def test_fire_cannot_be_set_through_the_signature() -> None:
    """`encode_robot_cmd` 的签名**不暴露** fire —— 传了就必须是 TypeError。

    这是不变量 1 说的"结构上不可达"的字面含义：
    不是"函数内部会把它清零"，而是**根本没有这条路**。
    如果哪天签名被加了个 `fire: bool = False`，下面这条就会红。
    """
    import inspect

    encoder = pl.PolarFrameCodec()
    params = inspect.signature(encoder.encode_robot_cmd).parameters
    check("encode_robot_cmd 的签名里没有 fire / shoot 之类的参数",
          not any(k.lower() in ("fire", "shoot", "fric_fire") for k in params),
          f"实际参数：{list(params)}")
    check("encode_robot_cmd 全部是关键字参数（避免位置参数被误传）",
          all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()),
          f"实际：{[(k, str(v.kind)) for k, v in params.items()]}")

    # 真传一次，看它是不是真的拒绝
    raised = None
    try:
        encoder.encode_robot_cmd(raw_timestamp=1, gimbal_pitch_rad=0.0, gimbal_yaw_rad=0.0,
                                 fire=True)  # type: ignore[call-arg]
    except TypeError as exc:
        raised = exc
    check("传 fire=1 会当场 TypeError（不是被静默忽略）",
          raised is not None and "fire" in str(raised),
          f"{type(raised).__name__ if raised else '没抛'}: {raised}")


def test_fire_bit_is_zero_in_every_encoded_frame() -> None:
    """不管参数怎么给，编出来的帧里 fire 那一位永远是 0。

    `friction_on` / `tracking` 是**允许**置 1 的（它们不是开火权），
    所以这个用例顺带确认"两个确实能置 1 的字段不会被一起清掉" ——
    否则一个"把整个结构体清零"的错误实现也能让上一条通过。
    """
    encoder = pl.PolarFrameCodec()
    if encoder is None:
        # 有些版本把它做成模块级函数
        encode = getattr(pl.PolarFrameCodec(), "encode_robot_cmd", None)
        if encode is None:
            check("找得到 encode_robot_cmd 的入口", False, "既没有类也没有函数")
            return
        encode = encode
    else:
        encode = encoder.encode_robot_cmd

    for friction in (False, True):
        for tracking in (False, True):
            frame = encode(raw_timestamp=7, gimbal_pitch_rad=0.1, gimbal_yaw_rad=-0.2,
                           friction_on=friction, tracking=tracking)
            body = frame[pl.HEADER_SIZE + pl.TIMESTAMP_SIZE: len(frame) - pl.CRC16_SIZE]
            values = list(struct.unpack(pl._ROBOT_CMD_BODY.format, body))
            fire_index = 9
            check(f"friction_on={friction} tracking={tracking} 时 fire 恒为 0",
                  values[fire_index] == 0, f"实际 {values[fire_index]}")
            check(f"friction_on={friction} tracking={tracking} 时摩擦轮位如实编码",
                  bool(values[fire_index + 1]) == friction, f"实际 {values[fire_index + 1]}")
            check(f"friction_on={friction} tracking={tracking} 时 tracking 位如实编码",
                  bool(values[fire_index + 2]) == tracking, f"实际 {values[fire_index + 2]}")


# ===========================================================================
# 2. 攻键位掩码：禁位不许上线上的字节
# ===========================================================================

FORBIDDEN_KEYS = ("SHIFT", "Q", "E", "F", "R", "G", "Z", "X", "C", "V", "B", "CTRL")


def test_forbidden_key_bits_never_reach_the_wire() -> None:
    """给一个"什么位都置上"的键位表，编完再**解回来**，看禁位在不在。

    这是本文件的核心。断言的对象是**帧里那 16 位**，
    不是编码器内部某个中间变量。
    """
    encoder = pl.PolarFrameCodec()
    encode = encoder.encode_virtual_rc

    everything = 0xFFFF
    attacks = [
        ("全 1 键位 + 默认掩码", dict(key_value=everything)),
        ("全 1 键位 + 全 1 掩码", dict(key_value=everything, key_mask=everything)),
        ("全 1 键位 + 显式禁位掩码", dict(key_value=everything, key_mask=0xFFF0)),
        ("只按 SHIFT", dict(key_value=pl.RC_SPIN_KEY_MASK, key_mask=everything)),
        ("只按 Q", dict(key_value=1 << pl.RC_KEY_BITS["Q"], key_mask=everything)),
        ("只按 E（摩擦轮）", dict(key_value=1 << pl.RC_KEY_BITS["E"], key_mask=everything)),
        ("只按 F（sc_flag）", dict(key_value=1 << pl.RC_KEY_BITS["F"], key_mask=everything)),
        ("负数掩码", dict(key_value=everything, key_mask=-1)),
        ("超大键位值", dict(key_value=0x1FFFF, key_mask=0xFFFF)),
    ]
    for label, kwargs in attacks:
        frame = encode(raw_timestamp=1, **kwargs)
        check(f"{label}：编出来的确实是 0x03 帧", _frame_is_cmd_virtual_rc(frame),
              f"段号 {frame[2]:#04x}")
        got = _decode_key_value(frame)
        # 期望值按规则算：**调用方掩码 ∩ 驾驶四键**。
        # 第一版只算 `key_value & 驾驶四键`，于是
        #   ("全 1 键位 + 显式禁位掩码", key_value=0xFFFF, key_mask=0xFFF0)
        # 报红 —— 但线上 0x0000 恰恰是**做对了**的样子：调用方自己把掩码
        # 收窄到只含禁位，交集就是空。那条"掩码只允许收窄、不能放宽"的规则
        # 由编码器里的 `& RC_MAX_ALLOWED_KEY_MASK` 保证，与结果是不是空无关。
        #
        # 两次修正说明同一件事：**断言写错的样子和实现写错的样子，
        # 在只读结果上长得一模一样**。所以期望值必须是按规则算出来的，
        # 不能是一个"看起来合理的常量"。
        caller_mask = int(kwargs.get("key_mask", pl.RC_DRIVE_KEY_MASK))
        want = int(kwargs.get("key_value", 0)) & caller_mask & pl.RC_DRIVE_KEY_MASK & 0xFFFF
        check(f"{label}：线上键位值恰好是「键位值 & 调用方掩码 & 驾驶四键」",
              got == want,
              f"线上 0x{got:04X}，期望 0x{want:04X}"
              f"（key_value=0x{int(kwargs.get('key_value', 0)) & 0xFFFF:04X}，"
              f"key_mask=0x{caller_mask & 0xFFFF:04X}）")
        for key in FORBIDDEN_KEYS:
            bit = 1 << pl.RC_KEY_BITS[key]
            check(f"{label}：{key} 位没有上车",
                  not (got & bit), f"线上键位值 0x{got:04X} 含 {key} 位")


def test_rc_key_value_also_masks() -> None:
    """`rc_key_value` 是另一个算键位图的地方，它也得裁。

    只测编码器是不够的：调用方可能先用 `rc_key_value` 算出位图，
    再自己 `pack` 出去，那条路不经过编码器。
    """
    for allow_spin in (False, True):
        for key in FORBIDDEN_KEYS:
            state = {k: False for k in pl.RC_KEY_BITS}
            state[key] = True
            got = pl.rc_key_value(state, allow_spin)
            check(f"allow_spin={allow_spin} 只按 {key} 时输出为 0",
                  got == 0, f"得到 0x{got:04X}")


def test_drive_key_mask_argument_is_inert() -> None:
    """`drive_key_mask(allow_spin=True)` **必须**仍然返回 0x000F。

    不变量 8 说过这个参数"已无任何作用"。它的作用就是**失效**，
    所以要测的是"打开也没用"，而不是"打开会怎样"。
    """
    check("allow_spin=False 返回驾驶四键", pl.drive_key_mask(False) == 0x000F,
          hex(pl.drive_key_mask(False)))
    check("allow_spin=True 也返回驾驶四键（参数已失效）",
          pl.drive_key_mask(True) == 0x000F, hex(pl.drive_key_mask(True)))
    check("RC_MAX_ALLOWED_KEY_MASK 与驱动掩码一致（两层同宽）",
          pl.RC_MAX_ALLOWED_KEY_MASK == pl.RC_DRIVE_KEY_MASK,
          f"{hex(pl.RC_MAX_ALLOWED_KEY_MASK)} vs {hex(pl.RC_DRIVE_KEY_MASK)}")


# ===========================================================================
# 3. 证明上面那些断言不是空转
# ===========================================================================

def test_this_file_can_go_red() -> None:
    """负证：把这些断言喂一个"什么都不拦"的编码器，它们必须红。

    没有这一组，上面那些全绿只能说明"现在的实现是安全的"，
    说明不了"这些断言在守着"。一个恒绿的测试和没有测试是同一种东西。

    这里造一个假的编码器：原样返回 key_value，不做任何裁剪，
    然后逐条看断言判不判它红。
    """
    encoder = pl.PolarFrameCodec()

    class _PermissiveEncoder:
        """什么位都放行的假编码器 —— 用来证明断言会失败。

        它绕过 `PolarFrameCodec.encode_virtual_rc`，自己按同样的 body 格式
        pack 一个**不裁剪**的键位值，再照同样的帧格式包起来。
        如果连这样一条完全绕开被测代码的路都过不了那些断言，
        说明断言量的确实是"线上字节"，不是被测代码内部的某个变量。
        """

        def encode_virtual_rc(self, *, raw_timestamp, key_value=0, key_mask=None,
                              **_kwargs):
            body = struct.pack(pl.RC_CTRL_BODY_FORMAT, 0, 0, 0, 0, 0,
                               0, 0, 0, 0, 0, 0, 0, int(key_value) & 0xFFFF)
            head = bytes((pl.POLAR_SOF, len(body) + pl.TIMESTAMP_SIZE + pl.CRC16_SIZE,
                          pl.CMD_VIRTUAL_RC, 0))
            return (head + int(raw_timestamp).to_bytes(4, "little") + body
                    + b"\x00\x00")

    fake = _PermissiveEncoder()
    frame = fake.encode_virtual_rc(raw_timestamp=1, key_value=0xFFFF, key_mask=0xFFFF)
    got = _decode_key_value(frame)
    check("负证：不裁剪的假编码器确实会让禁位上车",
          got & ~pl.RC_DRIVE_KEY_MASK != 0, f"0x{got:04X} 居然没超")

    # 真正的编码器在同一输入下必须给出被裁过的结果
    good = _decode_key_value(
        encoder.encode_virtual_rc(raw_timestamp=1, key_value=0xFFFF, key_mask=0xFFFF))
    check("真编码器在同一输入下裁掉了禁位",
          good == 0xFFFF & pl.RC_DRIVE_KEY_MASK, f"0x{good:04X}")
    check("两者结果不同 → 裁剪这一步是真实存在的，不是测试在说空话",
          good != got, f"真 {good:#06x} 假 {got:#06x}")
    check("假编码器那一路会被上面的'禁位没上车'断言判红（逐位证明）",
          all(bool(got & (1 << pl.RC_KEY_BITS[k])) for k in ("SHIFT", "Q", "E", "F")),
          f"0x{got:04X}")


def main() -> int:
    tests = [
        test_fire_cannot_be_set_through_the_signature,
        test_fire_bit_is_zero_in_every_encoded_frame,
        test_forbidden_key_bits_never_reach_the_wire,
        test_rc_key_value_also_masks,
        test_drive_key_mask_argument_is_inert,
        test_this_file_can_go_red,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"编码不变量攻击测试：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"编码不变量攻击测试：全部通过（{PASSED} 项）")
    print("fire 恒 0、禁位不上线 —— 都是关于**线上字节**的陈述，不是关于常量的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
