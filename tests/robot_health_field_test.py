"""GlobalUnitStatus 的机器人血量回退取错了字段，会凭空造出淘汰事件。

问题
----
``OfficialMatchFeed.consume`` 处理 ``GlobalUnitStatus`` 时写的是::

    health_values = fields.get(11) or fields.get(6) or []

字段 11 是 ``robot_health``（对，backend/protocol.py:318）。**字段 6 不是** ——
它是 ``enemy_base_health``（敌方基地血量，protocol.py:313），一个标量。

于是当消息里没有字段 11 时（报文被截断、协议版本差异、解析丢字段），
代码会把**敌方基地血量当成机器人血量表**用：

* ``health = [enemy_base_health]`` → 长度 1
* ``own_count = min(5, len(health) // 2) = min(5, 0) = 0``
  → 第 0 个被当成**敌方**机器人
* 上一条消息的 ``_robot_health`` 还是 ``[基地血量]``，这一条变成 ``[0]``
  （基地被摧毁）→ ``before > 0 and current == 0`` 成立
  → 发出 ``victim_id = 100 + ENEMY_ORDER[0] = 101`` 的淘汰事件

也就是说：**敌方基地一被摧毁，客户端就报一条"敌方 101 号机器人被淘汰"**，
而场上根本没有任何机器人死。

修法：字段 11 是机器人血量的**唯一**来源，没有第二个字段能替代它。
拿不到就不给（fail-closed），不拿敌方基地血量顶替 ——
这与 AGENT.md 反复强调的"不要静默产生假数据"是同一条取向。

跑法：``python tests/robot_health_field_test.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.combat_events import OfficialMatchFeed  # noqa: E402
from rm_client.backend.protocol import encode_packed_uints, encode_uint  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def encode_fields(fields: dict[int, object]) -> bytes:
    """把 {字段号: 值} 拼成一条 GlobalUnitStatus 用的 protobuf 载荷。

    字段 11（robot_health）是 packed uints，字段 6（enemy_base_health）是标量 uint。
    刻意按真实线格式编码，而不是走仓库里的解码器再编码回来 ——
    否则测的是"解码器能不能认出自己造的假数据"。
    """
    out = b""
    for number, value in sorted(fields.items()):
        if number == 11:
            out += encode_packed_uints(11, list(value))  # type: ignore[arg-type]
        else:
            out += encode_uint(number, int(value))  # type: ignore[arg-type]
    return out


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"[PASS] {label}")
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"[FAIL] {label} {detail}")


def _consume(feed: OfficialMatchFeed, payload: bytes, alliance: str = "Red") -> tuple[list, dict]:
    # 官方 topic 的最后一段就是消息名（protocol.topic_message_name 与
    # combat_events._topic_name 都取最后一段比对），不是 cmd id。
    # 曾按 "/robot/status/0x0303" 写过，name 解析成 "0x0303"，整条用例
    # 什么都没走到却"看起来通过"——这类静默空转正是要避免的。
    return feed.consume(
        "/robot/status/GlobalUnitStatus",
        payload,
        alliance=alliance,
        local_robot_id=101,
    )


# --- 1. 正例：字段 11 在时，血量与事件都正常 --------------------------

def test_field11_is_robot_health() -> None:
    feed = OfficialMatchFeed()
    # 前置守卫：topic 真的解析成 GlobalUnitStatus，否则下面全是空转
    check(
        "前置：topic 解析成 GlobalUnitStatus",
        OfficialMatchFeed()._topic_name("/robot/status/GlobalUnitStatus") == "GlobalUnitStatus"
        if hasattr(OfficialMatchFeed, "_topic_name") else True,
        "topic 写法错了，整条用例会空转",
    )
    # 10 个机器人：5 我方 + 5 敌方；敌方 1 号(id=1) 血量归零
    health = [500] * 5 + [0, 500, 500, 500, 500]
    payload = encode_fields({11: health})
    events, updates = _consume(feed, payload)
    check("字段 11 被当作机器人血量写入", updates.get("official_robot_health") == health,
          str(updates.get("official_robot_health")))
    check("首条不产生淘汰事件（无前一帧可比）", not events, str([e.kind for e in events]))

    # 第二条：敌方 1 号仍是 0
    events2, _updates2 = _consume(feed, payload)
    check("血量不变时重复消息不产生新事件", not events2, str([e.kind for e in events2]))


# --- 2. 核心回归：缺字段 11 时不得拿敌方基地血量顶替 -----------------

def test_missing_field11_does_not_fabricate() -> None:
    feed = OfficialMatchFeed()
    # 只有字段 6（敌方基地血量），没有字段 11
    alive = encode_fields({6: 5000})
    dead = encode_fields({6: 0})

    events_a, updates_a = _consume(feed, alive)
    check(
        "缺字段 11 时不写 official_robot_health（没有数据就不要造）",
        "official_robot_health" not in updates_a or not updates_a.get("official_robot_health"),
        f"writes {updates_a.get('official_robot_health')!r}（enemy_base_health 被当成了机器人血量）",
    )
    check("缺字段 11 的首条不产生事件", not events_a, str([e.kind for e in events_a]))

    events_b, updates_b = _consume(feed, dead)
    check(
        "敌方基地血量归零时**不得**产生机器人淘汰事件",
        not events_b,
        f"产出了 {[(e.kind, e.victim_id) for e in events_b]} —— 这是拿基地血量当机器人血量造出来的假事件",
    )
    check(
        "基地归零后也不写 official_robot_health",
        "official_robot_health" not in updates_b or not updates_b.get("official_robot_health"),
        str(updates_b.get("official_robot_health")),
    )


# --- 3. 反例护栏：真的机器人血量归零**必须**仍然报淘汰 -----------------

def test_real_zero_health_still_reports() -> None:
    """把上面那条守卫钉死：不能为了不造假就把真事件也一起吞掉。"""

    feed = OfficialMatchFeed()
    alive = [500] * 5 + [500, 500, 500, 500, 500]
    zeroed = [500] * 5 + [0, 500, 500, 500, 500]

    _consume(feed, encode_fields({11: alive}))
    events, _updates = _consume(feed, encode_fields({11: zeroed}))

    check(
        "真·敌方机器人血量归零仍然报淘汰（守卫没把功能一起堵死）",
        any(e.kind == "elimination" for e in events),
        f"events={[(e.kind, e.victim_id) for e in events]}",
    )
    # 编码口径：红方视角下敌方第 1 类机器人的 victim_id 就是 100 + 1 = 101
    #（combat_events.py: `100 + type_id if alliance == "Red" else type_id`）。
    # 注意 101 这个值在"基地血量顶替"那条路上也会出现，所以**不能**用
    # "是不是 101"来区分真假事件 —— 要看它是不是在字段 11 缺失时冒出来的。
    victim_ids = {e.victim_id for e in events}
    check(
        "真事件的 victim_id 按红方口径编码为 101（敌方 1 号）",
        victim_ids == {101},
        str(sorted(victim_ids)),
    )


# --- 4. 字段 11 与 6 同时存在时，仍以 11 为准 -------------------------

def test_field11_wins_over_field6() -> None:
    feed = OfficialMatchFeed()
    health = [500] * 5 + [500, 500, 500, 500, 500]
    payload = encode_fields({11: health, 6: 0})  # 基地已摧毁，机器人满血
    _events, updates = _consume(feed, payload)
    check(
        "字段 11 与 6 同时存在时取 11（机器人血量），不被 6 覆盖",
        updates.get("official_robot_health") == health,
        f"got {updates.get('official_robot_health')!r}",
    )


# --- 5. 空表也不该造数据 ---------------------------------------------

def test_empty_health_no_fabrication() -> None:
    feed = OfficialMatchFeed()
    _events, updates = _consume(feed, encode_fields({11: []}))
    check(
        "字段 11 为空表时不写血量",
        not updates.get("official_robot_health"),
        str(updates.get("official_robot_health")),
    )


def main() -> int:
    tests = [
        test_field11_is_robot_health,
        test_missing_field11_does_not_fabricate,
        test_real_zero_health_still_reports,
        test_field11_wins_over_field6,
        test_empty_health_no_fabrication,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"机器人血量字段：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"机器人血量字段：全部通过（{PASSED} 项）")
    print("敌方基地血量不会再被当成机器人血量，也不会再凭空造出淘汰事件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
