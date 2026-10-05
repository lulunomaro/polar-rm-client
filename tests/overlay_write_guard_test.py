"""``apply_polar_overlay`` 的写入白名单：能挡住什么，为什么必须挡。

问题
----
``apply_polar_overlay`` 以前用 ``hasattr(self, name)`` 判存在再 ``setattr``。
``hasattr`` 对**方法名**和 **property 名**同样返回 True（property 还会顺带
执行一次 getter），于是 ``setattr`` 会把实例上的方法/属性永久遮蔽成 float。

已实测复现：overlay 里出现 ``'tick'`` 或 ``'push_event'`` 之后，
``state.tick`` / ``state.push_event`` 变成 float，再调用即
``TypeError: 'float' object is not callable``。60Hz 逻辑层下一拍整个崩掉，
而 ``_run_tick_stage`` 只会把它记成 'logic' 阶段的一次错误 —— RobotState
从此永久损坏，但日志里看不出所以然。

为什么这不是"理论上才会发生"：写入 overlay 的键**不是硬编码的**，它由
``polar_bridge`` 从 ``polar_protocol.json`` 的 ``state_fields`` 读出来。
也就是说电控往 JSON 里加一行 ``"state_fields"`` 就能引爆，而那正是本项目
设计的正常改法（AGENT.md 表二）。

顺带钉住第二条：规格里声明了但 RobotState 上没有落点的键，现在必须**可数**，
不能再静默丢 —— 本机当前规格就有 3 个这样的键（见下方用例）。

跑法：``python tests/overlay_write_guard_test.py``
"""

# 改动这个文件之前先读下面这段。
#
# **做负证（临时改坏源码再跑）之后，"还原了"不等于"跑的是还原后的代码"。**
# 本轮就踩了：把 `apply_polar_overlay` 的闸门判断挪到循环之后跑出 3 项红，
# 还原文件后 `git status` 显示**零差异**，可测试**仍然是红的**。
# 原因不是没还原，而是 Python 拿了变异版编译出来的 `__pycache__/*.pyc`。
#
# 同一类坑 Rust 那边也踩过：`Copy-Item` 会保留 LastWriteTime，
# cargo 按 mtime 判脏，于是"恢复后全绿"一度是假的。
#
# 所以规矩是：**凡是用备份/还原做负证，必须先 bust 缓存再复跑**，
# 而且要拿 `git status` 证明源码真的回去了 —— 光看"没抛异常"不算证据。
#   PowerShell: Get-ChildItem -Recurse -Directory -Filter __pycache__ |
#               ForEach-Object { Remove-Item "$($_.FullName)\*.pyc" -Force }
#   Rust:       touch 一下被改过的源文件，再 cargo test


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend import polar_spec  # noqa: E402
from rm_client.model import _OVERLAY_WRITABLE, RobotState  # noqa: E402

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


def armed_state(overlay: dict) -> RobotState:
    state = RobotState()
    state.polar_link_active = True
    state.polar_overlay = dict(overlay)
    return state


# --- 1. 白名单就是 RobotState 自己的字段全集 ----------------------------

def test_whitelist_matches_dataclass_fields() -> None:
    fields = set(RobotState.__dataclass_fields__)
    check(
        f"白名单覆盖 RobotState 全部 {len(fields)} 个字段",
        fields == set(_OVERLAY_WRITABLE),
        f"差集 {fields ^ set(_OVERLAY_WRITABLE)}",
    )
    # 白名单里若混进方法名/property 名，遮蔽问题就又回来了
    not_fields = {n for n in dir(RobotState) if not n.startswith("__")} - fields
    intruders = sorted(set(_OVERLAY_WRITABLE) & not_fields)
    check(
        f"白名单里没有任何方法/property（可被遮蔽的成员共 {len(not_fields)} 个）",
        not intruders,
        f"混入了 {intruders}",
    )


# --- 2. 核心：方法名注入不再遮蔽方法 -----------------------------------

def test_method_names_cannot_be_shadowed() -> None:
    for name in ("tick", "push_event", "apply_polar_overlay", "buff_indicator_state"):
        state = armed_state({name: 12345.0})
        state.apply_polar_overlay()
        current = getattr(state, name, None)
        check(
            f"注入 '{name}' 不会把它打成 float",
            callable(current) and not isinstance(current, float),
            f"现在它是 {type(current).__name__}",
        )
    # 真正的后果：方法还能被正常调用
    state = armed_state({"tick": 12345.0})
    state.apply_polar_overlay()
    try:
        state.tick(1 / 60.0)
        ok, detail = True, ""
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check("注入 'tick' 之后 state.tick(1/60) 仍可正常调用", ok, detail)

    # 反面对照：真字段**必须**能被写成 float（否则这条守卫把功能一起堵死了）
    state = armed_state({"polar_ally_hp": 1.5})
    state.apply_polar_overlay()
    check(
        "对照：真字段仍然可写成 float",
        isinstance(state.polar_ally_hp, float) and state.polar_ally_hp == 1.5,
        f"got {state.polar_ally_hp!r} ({type(state.polar_ally_hp).__name__})",
    )
# --- 3. property 名注入不再遮蔽 property --------------------------------

def test_property_names_cannot_be_shadowed() -> None:
    props = [
        name for name in dir(RobotState)
        if isinstance(getattr(RobotState, name, None), property)
    ]
    check(f"RobotState 上有 {len(props)} 个 property 供测试", bool(props), str(props))
    for name in props[:6]:
        state = armed_state({name: 999.0})
        try:
            state.apply_polar_overlay()
            value = getattr(state, name)
            ok = not isinstance(value, float) or value != 999.0
            check(f"注入 '{name}' 不会遮蔽 property", ok, f"现在返回 {value!r}")
        except Exception as exc:  # noqa: BLE001
            check(f"注入 '{name}' 不会遮蔽 property", False, f"{type(exc).__name__}: {exc}")


# --- 4. 正常字段照常写入（不能把功能一起堵死） -------------------------

def test_normal_fields_still_write() -> None:
    # 注意：这里必须挑**确实存在**的字段。曾经随手写 polar_spin_active，
    # 结果 AttributeError —— 而它不存在本身就是下面 test_spec_state_fields
    # 要报的那 3 个孤儿落点之一（polar_protocol.json 声明了，RobotState 没有）。
    state = armed_state({
        "polar_ally_hp": 4321,
        "polar_drive_armed": True,
        "polar_gimbal_client": True,
    })
    state.apply_polar_overlay()
    check("polar_ally_hp 正常写入", state.polar_ally_hp == 4321, str(state.polar_ally_hp))
    check("polar_drive_armed 正常写入", state.polar_drive_armed is True, str(state.polar_drive_armed))
    check("polar_gimbal_client 正常写入", state.polar_gimbal_client is True, str(state.polar_gimbal_client))
    check("没有被记成丢弃", state.polar_dropped_overlay_keys == 0, str(state.polar_dropped_overlay_keys))


# --- 5. 丢弃必须可数、可定位 -------------------------------------------

def test_dropped_keys_are_counted() -> None:
    state = armed_state({
        "polar_ally_hp": 100,
        "nonexistent_field": 1,
        "another_missing": 2,
    })
    state.apply_polar_overlay()
    check(
        f"丢弃的键被计数（实测 {state.polar_dropped_overlay_keys}）",
        state.polar_dropped_overlay_keys == 2,
        str(state.polar_dropped_overlay_keys),
    )
    check(
        "首个丢弃键被记下来可定位",
        state.polar_first_dropped_overlay_key in {"nonexistent_field", "another_missing"},
        state.polar_first_dropped_overlay_key,
    )
    check("正常字段仍然写入", state.polar_ally_hp == 100, str(state.polar_ally_hp))


# --- 6. 本机当前规格：state_fields 的落点必须都在白名单里 -------------
#
# 这条现在**是红的**（本机 polar_protocol.json 有 3 个 state_fields 指向
# RobotState 上不存在的字段）。这不是测试写错了，而是它把一个此前完全
# 静默的数据丢失摆到了台面上；修规格是维护者的决定，不在"零像素优化"
# 这一轮的范围里。所以这里断言"白名单外有多少个"并把清单打出来，
# 让人看得见，而不是让它继续静默。

def test_spec_state_fields_landing_sites() -> None:
    spec = polar_spec.get_spec()
    if not spec.ok:
        check("规格可加载（前置条件）", False, f"spec.ok=False {spec.errors[:2]}")
        return

    landing: dict[str, list[str]] = {}
    for segment_id, mapping in sorted(getattr(spec, "segment_state_fields", {}).items()):
        for decoded_name, state_name in sorted(mapping.items()):
            landing.setdefault(state_name, []).append(f"0x{segment_id:02X}:{decoded_name}")

    orphans = {name: src for name, src in landing.items() if name not in _OVERLAY_WRITABLE}
    if orphans:
        print(f"      当前规格有 {len(orphans)} 个 state_fields 落点不在 RobotState 上：")
        for name, src in sorted(orphans.items()):
            print(f"        - {name}  ← {', '.join(src)}")
        check(
            "规格里没有落点不存在的 state_fields（否则这些数据被静默丢弃）",
            False,
            f"{len(orphans)} 个: {sorted(orphans)}",
        )
    else:
        check("规格里所有 state_fields 落点都存在于 RobotState", True)

    # ⚠⚠ **第 174 轮：这里原来是 `check(..., True)`** ——
    # **而标签写着「这些必须真的写进去」**。
    #
    # **子代理实测：把 `spec.segment_state_fields` 整个抹掉以后，**
    # **打出来是「已接通 0 个」而仍然全绿。**
    #
    # 它做两件事：
    hit = [name for name in landing if name in _OVERLAY_WRITABLE]
    #
    # ① **落点不能为空** —— **为空时下面那条会成为空转**，
    # **而上面那条「落点都存在于 RobotState」也变成空转**。
    check(
        f"规格里确实有 state_fields 落点（当前 {len(landing)} 个）",
        bool(landing),
        "落点为空 → 下面两条都变成空转，"
        f"而不是「都正确」",
    )
    # ② **每一个落点都要落在可写字段上** —— 这才是标签上那句话。
    unwritable = sorted(name for name in landing if name not in _OVERLAY_WRITABLE)
    check(
        f"已接通 {len(hit)}/{len(landing)} 个 state_fields 落点"
        f"（这些必须真的写进去）",
        not unwritable,
        f"落在不可写字段上的：{unwritable}"
        f"（落点：{ {k: v for k, v in landing.items() if k in unwritable} }）",
    )


# --- 7. 链路未激活时照旧不写（既有语义不许被改） -----------------------

def test_inactive_link_does_not_write() -> None:
    """AGENT.md 表二第 3 条：**链路未激活时一个字段都不回写**。

    原来的版本只有一条断言、只测了 `polar_ally_hp` 一个键。表二说的是
    "overlay 只在 polar_link_active 为真时回写" —— 那是**对全部字段**的断言，
    只测一个键等于没测：白名单里另外三十几个键要是哪天被挪到了 gate 之前
    （比如为了"先写再判断"），原来那条照样绿。

    所以这里改成两遍全量：
    * 未激活：**全部** polar_* 键一个都不能动；
    * 已激活：全部 polar_* 键都要真的落下去（否则"一个都没写"也能满足上面那条）。
    """
    from rm_client.model import RobotState as _RS

    polar_names = sorted(
        name for name in _RS.__dataclass_fields__
        if name.startswith("polar_") and name not in ("polar_overlay",)
    )
    check("确实抓到了 polar_* 字段族（少于 10 个说明取值方式变了）",
          len(polar_names) >= 10, f"只有 {len(polar_names)} 个：{polar_names[:5]}")

    # --- 未激活：一个都不许动 ---
    state = _RS()
    state.polar_link_active = False
    sentinel = 424242
    overlay = {name: sentinel for name in polar_names if name != "polar_link_active"}
    state.polar_overlay = dict(overlay)
    before = {name: getattr(state, name, "<missing>") for name in overlay}
    state.apply_polar_overlay()
    leaked = [n for n in overlay if getattr(state, n, "<missing>") != before[n]
              and n != "polar_dropped_overlay_keys"
              and not n.startswith("polar_first_dropped")]
    check(f"未激活时 {len(overlay)} 个键一个都没回写", not leaked, f"漏写了：{leaked[:6]}")

    # --- 已激活：全部都要落下去（反向对照） ---
    state2 = _RS()
    state2.polar_link_active = True
    state2.polar_overlay = {name: sentinel for name in polar_names
                            if name != "polar_link_active"}
    before2 = {n: getattr(state2, n, "<missing>") for n in state2.polar_overlay}
    state2.apply_polar_overlay()
    # 只要求"至少有一批确实变了"，因为不同字段类型不同，int 塞进 float 字段等
    # 情况会让某些键的值恰好不变或报错。全量逐键比在这里没有意义。
    changed = [n for n in state2.polar_overlay if getattr(state2, n, "<missing>") != before2[n]]
    check(f"已激活时确实回写了字段（{len(changed)}/{len(state2.polar_overlay)} 个键值变了）",
          len(changed) >= 10, f"只有 {len(changed)} 个变了，回写可能没生效")


def test_overlay_cannot_bootstrap_its_own_gate() -> None:
    """overlay **不能**把自己所在的闸门打开。

    `apply_polar_overlay` 的判断在循环**之前**（model.py:693），而
    `polar_link_active` 本身又是一个合法的 dataclass 字段、因而落在白名单里。
    这两条凑在一起就产生一个必须钉死的问题：

        overlay = {"polar_link_active": True, "polar_ally_hp": 999}
        state.polar_link_active = False
        state.apply_polar_overlay()

    如果判断被挪到循环之后（比如为了"先写再判断"），上面这段就会**打开闸门**
    并把 999 写下去 —— 而 overlay 的键**不是硬编码的**，它由 polar_bridge
    从 `polar_protocol.json` 的 `state_fields` 读出来。也就是说：
    电控往 JSON 里加一行 `"state_fields": {"polar_link_active": "..."}`
    就能让客户端自己解除 fail-closed 门控。

    这是"不可达"必须做到结构上的又一例，跟不变量 2 是同一个道理。
    """
    state = RobotState()
    state.polar_link_active = False
    state.polar_ally_hp = 0
    state.polar_overlay = {"polar_link_active": True, "polar_ally_hp": 999}
    state.apply_polar_overlay()
    check("overlay 没能把自己写进 polar_link_active（闸门仍关着）",
          state.polar_link_active is False, f"polar_link_active={state.polar_link_active}")
    check("闸门关着时连 payload 字段也没跟着落下去",
          state.polar_ally_hp == 0, f"polar_ally_hp={state.polar_ally_hp}")

    # 反向对照：闸门本来就是开的，那 overlay 里的 polar_link_active=True 无害，
    # 但 payload 字段要真的落下去 —— 证明上面那条不是"什么都不写"。
    state2 = RobotState()
    state2.polar_link_active = True
    state2.polar_ally_hp = 0
    state2.polar_overlay = {"polar_link_active": True, "polar_ally_hp": 999}
    state2.apply_polar_overlay()
    check("闸门本来就开时，payload 字段确实回写（反向对照）",
          state2.polar_ally_hp == 999, f"polar_ally_hp={state2.polar_ally_hp}")


def test_empty_overlay_is_a_no_op() -> None:
    """空 overlay 直接返回，连 `official_state_at` 都不该被动。

    model.py:691 有 `if not self.polar_overlay: return`。这条测它**真的**早退：
    如果空 dict 也往下走，`official_state_at` 会被写成当前时间，
    于是"队内链路的值是什么时候到的"这个诊断字段在**根本没链路**时就有了值 ——
    现场看到它有值就会以为链路来过。
    """
    state = RobotState()
    state.polar_link_active = True
    state.polar_overlay = {}
    state.official_state_at = None
    state.apply_polar_overlay()
    check("空 overlay 不会给 official_state_at 打时间戳",
          state.official_state_at is None, str(state.official_state_at))


def main() -> int:
    tests = [
        test_whitelist_matches_dataclass_fields,
        test_method_names_cannot_be_shadowed,
        test_property_names_cannot_be_shadowed,
        test_normal_fields_still_write,
        test_dropped_keys_are_counted,
        test_spec_state_fields_landing_sites,
        test_inactive_link_does_not_write,
        test_overlay_cannot_bootstrap_its_own_gate,
        test_empty_overlay_is_a_no_op,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"overlay 写入白名单：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"overlay 写入白名单：全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
