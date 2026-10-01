"""combat_events 三条缺陷的回归钉。

为什么这个文件存在
------------------
``rm_client/combat_events.py`` 里有两处把**累计量/多来源**当成一次性事件，
一处是永远到不了的死代码。三个现场现象：

1. ``RobotInjuryStat`` 是裁判系统**累计 1 Hz** 报文（``backend/manager.py:1781``
   原文 "RobotInjuryStat is a cumulative 1 Hz report"），但 ``consume()`` 写的是
   ``server_kill_damage > 0`` —— 拿累计值当边沿。实测：累计值恒为 500 的 1 Hz
   报文，9 秒内重复产出 **5** 条 death 事件（每 2 秒重放一次，直到重连）。
2. ``_dedupe`` 的键是 ``(kind, victim_id, killer_id)``。血量归零来源拿不到
   killer_id（0），``Event`` topic 才有，于是**同一次击杀**在两个来源下是两个
   不同的三元组。实测：一次击杀产出 **2** 条 CombatEvent。
3. ``_finalize_tracks`` 结尾的 ``if not track.seen_this_frame:`` 块是结构性死
   代码（上面那个同条件块每条路径都以 ``continue`` 收尾），``sys.settrace``
   跨 960 组场景实测第 696–712、716–737 行**从未执行**。后果是
   ``README_战果判定与阵营.md:33`` 对外承诺的 ``MISS_TRACKED`` 状态和
   ``vision_confirmed_miss_after_lit`` 事件**永远不可能出现**。
本文件的取舍（对应"先判断哪个才是本意"）
----------------------------------------
第 3 条**不把它改可达，而是删掉**。理由：

* 生产调用方 ``ui/polar/mixins/video_backend_mixin.py:916`` 一定会传
  ``loss_evidence``，证据由 ``tongji_yolo.py:555`` 的 ``_LocalLossLightProbe``
  在目标消失时实采（``VisionLossEvidence`` 的 docstring 就写着
  "Local-only evidence captured after a tracked armor disappears"）。
* 上面那条 ``LOSS_*`` 路径才是本意：它带 ``probe_dark`` / ``moving_fast`` /
  ``near_edge`` / ``stable_frames`` 四道闸，产出
  ``vision_confirmed_dark_loss_probe``。旧块那四道闸一个都没有。
* 把旧块改可达 = 把"目标只是走出画面 / 被遮挡 / 没变暗"重新判成击杀确认，
  在一个专门防幻影击杀的系统里是**安全倒退**。它甚至不该是"待修的功能"。

所以这里同时做两件事：把 23760 组场景的状态/事件/逐条 track 计数器全部压成
一个 ``VISION_GOLDEN_DIGEST``（用 ``git show HEAD:`` 的原始源码现场算出的），
证明删除是**逐位等价**的，再用一条 AST 断言把死字符串钉死。

跑法：``python tests/combat_events_test.py``
"""

from __future__ import annotations

import hashlib
import itertools
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.combat_events import (  # noqa: E402
    CombatEvent,
    OfficialMatchFeed,
    VisionKillDetector,
)

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


# --- protobuf 编码小工具（只编本测试用得到的字段）-------------------------

def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def bytes_field(number: int, raw: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(raw)) + raw


def packed_field(number: int, values: list[int]) -> bytes:
    return bytes_field(number, b"".join(varint(value) for value in values))


class Clock:
    """可控的 time.perf_counter，让"1 Hz 累计报文"在毫秒内跑完 9 秒。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


def injury_payload(killer_id: int, server_kill_damage: int | None) -> bytes:
    body = b""
    if server_kill_damage is not None:
        body += field(9, server_kill_damage)
    body += field(10, killer_id)
    return body


# ======================================================================
# 1. RobotInjuryStat：累计量当边沿 → 改成增量判边沿
# ======================================================================

def test_injury_stat_cumulative_is_not_an_edge() -> None:
    """累计 1 Hz 报文在数值不变时不得重复产出 death。"""
    clock = Clock()
    feed = OfficialMatchFeed()
    emitted = []
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        for _ in range(10):  # 9 秒 @1Hz，累计值恒为 500
            events, _updates = feed.consume(
                "RobotInjuryStat", injury_payload(7, 500),
                alliance="Red", local_robot_id=3,
            )
            emitted.append(len(events))
            clock.tick(1.0)

    check(
        "injury/cumulative-constant-no-replay (10x1Hz@500 -> 0 events)",
        sum(emitted) == 0,
        f"emitted={emitted}",
    )


def test_injury_stat_first_sample_only_baselines() -> None:
    """连上时已经累计到 500 的首帧不得被判为一次新击杀。"""
    feed = OfficialMatchFeed()
    events, _updates = feed.consume(
        "RobotInjuryStat", injury_payload(7, 500),
        alliance="Red", local_robot_id=3,
    )
    check(
        "injury/first-sample-is-baseline-not-a-kill",
        events == [],
        f"events={events}",
    )


def test_injury_stat_increment_is_the_only_edge() -> None:
    """累计值真的增长时才发，且带当前 killer_id。"""
    clock = Clock()
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        first, _ = feed.consume("RobotInjuryStat", injury_payload(7, 0), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        again, _ = feed.consume("RobotInjuryStat", injury_payload(7, 0), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        grown, _ = feed.consume("RobotInjuryStat", injury_payload(9, 500), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        flat, _ = feed.consume("RobotInjuryStat", injury_payload(9, 500), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        grown2, _ = feed.consume("RobotInjuryStat", injury_payload(11, 1000), alliance="Red", local_robot_id=3)

    check("injury/zero-baseline-silent", first == [] and again == [], f"{first} {again}")
    check(
        "injury/increment-emits-one-death-with-killer",
        len(grown) == 1 and grown[0].kind == "death" and grown[0].source == "referee"
        and grown[0].killer_id == 9 and grown[0].detail == "server_kill",
        f"{grown}",
    )
    check("injury/flat-repeat-stays-silent", flat == [], f"{flat}")
    check(
        "injury/second-increment-emits-again",
        len(grown2) == 1 and grown2[0].killer_id == 11,
        f"{grown2}",
    )


def test_injury_stat_counter_reset_rebaselines_quietly() -> None:
    """复活/重连导致累计值回退：重建基线，不当成一次巨额新伤害。"""
    clock = Clock()
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        feed.consume("RobotInjuryStat", injury_payload(7, 1500), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        reset, _ = feed.consume("RobotInjuryStat", injury_payload(7, 0), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        after, _ = feed.consume("RobotInjuryStat", injury_payload(7, 0), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        real, _ = feed.consume("RobotInjuryStat", injury_payload(7, 400), alliance="Red", local_robot_id=3)

    check("injury/reset-is-quiet", reset == [] and after == [], f"{reset} {after}")
    check("injury/post-reset-growth-still-detected", len(real) == 1, f"{real}")


def test_injury_stat_missing_field9_never_touches_baseline() -> None:
    """截断/丢字段 9 的报文不得把基线打成 0（否则下一帧会伪造一次击杀）。"""
    clock = Clock()
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        feed.consume("RobotInjuryStat", injury_payload(7, 500), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        truncated, _ = feed.consume("RobotInjuryStat", injury_payload(7, None), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        flat, _ = feed.consume("RobotInjuryStat", injury_payload(7, 500), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        real, _ = feed.consume("RobotInjuryStat", injury_payload(7, 900), alliance="Red", local_robot_id=3)

    check("injury/truncated-payload-silent", truncated == [], f"{truncated}")
    check("injury/baseline-survives-truncated-payload", flat == [], f"{flat}")
    check("injury/real-growth-after-truncated-still-detected", len(real) == 1, f"{real}")


def test_injury_stat_never_poisons_kill_freshness() -> None:
    """去重窗口过期后也不回放：增量没变就没有事件，但新鲜度照常刷新。"""
    clock = Clock()
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        events, _ = feed.consume("RobotInjuryStat", injury_payload(7, 500), alliance="Red", local_robot_id=3)
        check("injury/freshness-still-set", feed.is_kill_data_fresh() is True)
        for _ in range(4):  # 每次都跨过 2.0 s 的去重窗口
            clock.tick(2.5)
            events, _ = feed.consume("RobotInjuryStat", injury_payload(7, 500), alliance="Red", local_robot_id=3)
            if events:
                break
    check("injury/no-replay-across-dedupe-window", events == [], f"replayed {events}")


# ======================================================================
# 2. 去重键合并不了双来源
# ======================================================================

def _kill_once(feed: OfficialMatchFeed, clock: Clock, *, victim_type: int = 7,
               event_topic_first: bool = False) -> list[CombatEvent]:
    """一次击杀的完整裁判报文序列，返回最终进入消费端的 CombatEvent。"""
    produced: list[CombatEvent] = []
    own = [500] * 5
    alive = own + [400, 300, 200, 100, 500]
    dead = own + [400, 300, 200, 100, 0]
    slot = 9 - 5  # 敌方第 5 位 -> ENEMY_ORDER[4] == victim_type
    assert OfficialMatchFeed.ENEMY_ORDER[slot] == victim_type
    victim_id = 100 + victim_type  # Red 本方时，敌方 id 用 100+type

    def health(payload: bytes) -> None:
        events, _ = feed.consume("GlobalUnitStatus", payload, alliance="Red", local_robot_id=3)
        produced.extend(events)

    def event_topic() -> None:
        events, _ = feed.consume(
            "Event", field(1, 1) + bytes_field(2, f"{victim_id},3".encode()),
            alliance="Red", local_robot_id=3,
        )
        produced.extend(events)

    health(packed_field(11, alive))
    if event_topic_first:
        event_topic()
        clock.tick(0.25)  # 250 ms 抖动，实测双来源窗口
        health(packed_field(11, dead))
    else:
        health(packed_field(11, dead))
        clock.tick(0.25)
        event_topic()
    return produced


def test_dual_source_same_kill_collapses_to_one() -> None:
    """GlobalUnitStatus 血量归零 + Event topic 250 ms 后到达 → 只出 1 条。

    哪一条活下来取决于谁先到：consume() 是**同步返回**的，调用方
    （manager / video_backend_mixin）拿到列表的当下就已经消费掉了，不可能
    事后把"瘦"事件（只有 victim）升级成"胖"事件（带 killer/kill_type/
    combo/score_delta）。所以这里取**先到先得**而不是回溯替换。
    现场 `Event` topic 是击杀瞬间推送的、`GlobalUnitStatus` 是 1 Hz，
    因此实际绝大多数情况是信息更全的 Event 那条活下来。
    """
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        produced = _kill_once(feed, clock)
    check(
        "dedupe/health-zero+event-topic -> 1 event",
        len(produced) == 1,
        f"got {[(e.kind, e.source, e.victim_id, e.killer_id, e.detail) for e in produced]}",
    )
    check(
        "dedupe/survivor-is-the-first-source",
        produced and produced[0].source == "referee_health" and produced[0].detail == "health_zero",
        f"{produced}",
    )


def test_dual_source_event_topic_wins_when_it_arrives_first() -> None:
    """Event topic 先到时，信息更全的那条必须活下来。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        produced = _kill_once(feed, clock, event_topic_first=True)
    check("dedupe/event-topic-first -> 1 event", len(produced) == 1,
          f"got {[(e.source, e.detail) for e in produced]}")
    check(
        "dedupe/survivor-carries-killer",
        produced and produced[0].source == "referee" and produced[0].killer_id == 3,
        f"{produced}",
    )


def test_dual_source_reverse_order_also_collapses() -> None:
    """Event topic 先到、血量归零后到 —— 同样只出 1 条。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    produced: list[CombatEvent] = []
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        own = [500] * 5
        events, _ = feed.consume("GlobalUnitStatus", packed_field(11, own + [400, 300, 200, 100, 500]),
                                 alliance="Red", local_robot_id=3)
        produced += events
        events, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"),
                                 alliance="Red", local_robot_id=3)
        produced += events
        clock.tick(0.25)
        events, _ = feed.consume("GlobalUnitStatus", packed_field(11, own + [400, 300, 200, 100, 0]),
                                 alliance="Red", local_robot_id=3)
        produced += events
    check("dedupe/event-first+health-zero -> 1 event", len(produced) == 1,
          f"got {[(e.source, e.detail) for e in produced]}")


def test_dual_source_alive_state_also_merges() -> None:
    """RobotStaticStatus(alive_state=2) 也要和 Event topic 合并。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        first, _ = feed.consume("RobotStaticStatus", field(4, 107) + field(3, 2),
                                 alliance="Red", local_robot_id=3)
        clock.tick(0.25)
        second, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"),
                                  alliance="Red", local_robot_id=3)
    check(
        "dedupe/alive_state+event-topic -> 1 event",
        len(first) == 1 and len(second) == 0,
        f"{[(e.source, e.detail) for e in first]} + {[(e.source, e.detail) for e in second]}",
    )


def test_dedupe_still_separates_distinct_victims() -> None:
    """同时团灭两个不同敌人不能被合掉。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        own = [500] * 5
        events, _ = feed.consume("GlobalUnitStatus", packed_field(11, own + [400, 300, 200, 100, 500]),
                                  alliance="Red", local_robot_id=3)
        events2, _ = feed.consume("GlobalUnitStatus", packed_field(11, own + [400, 300, 200, 100, 0]),
                                   alliance="Red", local_robot_id=3)
        clock.tick(0.25)
        events3, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"),
                                  alliance="Red", local_robot_id=3)
        events4, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"102,5"),
                                  alliance="Red", local_robot_id=3)
    total = len(events) + len(events2) + len(events3) + len(events4)
    check("dedupe/distinct-victims-not-merged (want 2)", total == 2,
          f"got {total}: {events + events2 + events3 + events4}")


def test_dedupe_still_separates_two_known_distinct_killers() -> None:
    """killer_id 双方都知道且不同 → 视为两次不同的事件，不合并。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        a, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"), alliance="Red", local_robot_id=3)
        clock.tick(0.1)
        b, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,5"), alliance="Red", local_robot_id=3)
    check("dedupe/two-known-killers-stay-separate", len(a) == 1 and len(b) == 1, f"{a} {b}")


def test_dedupe_replays_after_window_still_blocked() -> None:
    """完全相同的三元组在 2 s 窗口内仍然互斥（原有语义未变）。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        a, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"), alliance="Red", local_robot_id=3)
        clock.tick(1.0)
        b, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"), alliance="Red", local_robot_id=3)
    check("dedupe/exact-triple-still-blocked", len(a) == 1 and len(b) == 0, f"{a} {b}")


def test_dedupe_merge_window_expires() -> None:
    """跨过 2 s 窗口后，同一受害者的一次新击杀必须能再次上报。"""
    clock = Clock(2000.0)
    feed = OfficialMatchFeed()
    with mock.patch("rm_client.combat_events.time.perf_counter", clock):
        a, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,3"), alliance="Red", local_robot_id=3)
        clock.tick(2.5)
        b, _ = feed.consume("Event", field(1, 1) + bytes_field(2, b"107,4"), alliance="Red", local_robot_id=3)
    check("dedupe/merge-window-expires", len(a) == 1 and len(b) == 1, f"{a} {b}")


# ======================================================================
# 3. _finalize_tracks 死代码：逐位等价性 + 死字符串钉死
# ======================================================================

class _Armor:
    """最小 ArmorObservation 替身，只带 VisionKillDetector 真正读到的字段。"""

    def __init__(self, *, color: str = "blue", light_strength: float = 0.8,
                 light_state: str = "lit", confidence: float = 0.92,
                 target_id: int = 7, armor_id: int = 103, shift: float = 0.0) -> None:
        self.target_id = target_id
        self.armor_id = armor_id
        self.label = "three"
        self.color = color
        self.confidence = confidence
        self.light_strength = light_strength
        self.light_state = light_state
        self.points = (
            (100.0 + shift, 100.0), (180.0 + shift, 100.0),
            (180.0 + shift, 140.0), (100.0 + shift, 140.0),
        )
        self.predicted = False


class _Loss:
    def __init__(self, *, probe_dark: bool = True, moving_fast: bool = False,
                 near_edge: bool = False, target_id: int = 7) -> None:
        self.target_id = target_id
        self.moving_fast = moving_fast
        self.near_edge = near_edge
        self.probe_dark = probe_dark


LIT = _Armor()
DIM = _Armor(light_strength=0.1)
OFF = _Armor(color="extinguish", light_state="extinguished", light_strength=0.1)
FAINT = _Armor(confidence=0.4)
# 亮过之后横移：dim 进度够但 stable_frames 被打回 1，用来打到 Balanced 的
# MISS_UNSTABLE 闸（否则这一分支在矩阵里永远到不了，摘要就证明不了什么）。
DIM_MOVED = _Armor(light_strength=0.1, shift=40.0)

_SENS = ("Lab Responsive", "Balanced", "Match Reliable")
_PRE_OBS = (
    (),
    (DIM,),
    (DIM, DIM),
    (DIM, DIM, DIM),
    (DIM, DIM_MOVED),
    (OFF,),
    (OFF, OFF),
    (OFF, OFF, OFF),
    (LIT,),
    (LIT, DIM),
    (FAINT,),
)
_DARK = (True, False)
_FAST = (True, False)
_EDGE = (True, False)
_FSIZE = (None, (640, 360), (1920, 1080))
_LUM = (None, 0.4, 0.95)

# 23760 组 = 3 灵敏度 x 11 预热序列 x 5 空帧数 x 2^3 证据位 x 3 画面尺寸
#         x 3 亮度 x 2（重复视觉帧 token）
SCENARIO_SPACE = len(_SENS) * len(_PRE_OBS) * 5 * len(_DARK) * len(_FAST) * len(_EDGE) * len(_FSIZE) * len(_LUM) * 2


def _run_scenario(sens: str, pre: list, empties: int, dark: bool, fast: bool,
                  edge: bool, fsize, lum, lum2: float, repeat_token: bool) -> str:
    detector = VisionKillDetector()
    now = 1.0
    lines: list[str] = []
    frames = (LIT,) + tuple(pre)
    for index, obs in enumerate(frames):
        token = 1 if (repeat_token and index > 0) else index + 1
        events = detector.process(
            [obs], alliance="Red", frame_luminance=lum, frame_token=token,
            frame_size=fsize, sensitivity=sens, now=now + 0.1 * index,
        )
        lines.append(f"{index}|{detector.last_status}|{_fmt(events)}")
    for index in range(empties):
        token = 100 if (repeat_token and index > 0) else 100 + index
        events = detector.process(
            [], alliance="Red", frame_luminance=lum2, frame_token=token,
            frame_size=fsize, sensitivity=sens,
            loss_evidence=_Loss(probe_dark=dark, moving_fast=fast, near_edge=edge),
            now=now + 0.1 * (len(frames) + index),
        )
        lines.append(f"e{index}|{detector.last_status}|{_fmt(events)}")
    for key, track in sorted(detector._tracks.items()):
        lines.append(
            f"track {key} lit={track.lit_at!r} tri={track.triggered!r} "
            f"ef={track.extinguish_frames} es={track.extinguish_start!r} "
            f"df={track.dim_frames} ds={track.dim_start!r} "
            f"mf={track.miss_frames} ms={track.miss_start!r} "
            f"stable={track.stable_frames} edge={track.last_in_safe_area!r} "
            f"base={track.lit_strength_baseline!r} seen={track.seen_this_frame!r} "
            f"center={track.last_center!r}"
        )
    lines.append(f"suppressed={sorted(detector._eliminated)}")
    return "\n".join(lines)


def _fmt(events: list[CombatEvent]) -> str:
    return "|".join(
        f"{e.kind},{e.source},{e.confirmed},{e.victim_id},{e.killer_id},{e.detail},{e.kill_type}"
        for e in events
    )


def vision_golden_digest() -> tuple[str, int]:
    """把 VisionKillDetector 在 23760 组场景下的全部可观测输出压成一个 sha256。

    这是"删掉死代码是逐位等价的"的机器证明：每一组记录**每一帧**的
    last_status、当帧事件、逐条 track 的全部计数器和 last_center，外加最终
    的 _eliminated 集合。改动前后这个摘要必须一字不差。

    这个常量是用 ``git show HEAD:rm_client/combat_events.py`` 的**原始源码**
    现场算出来的，不是"先跑一遍记下来"。
    """
    digest = hashlib.sha256()
    count = 0
    for sens, pre, empties, dark, fast, edge, fsize, lum in itertools.product(
        _SENS, _PRE_OBS, range(5), _DARK, _FAST, _EDGE, _FSIZE, _LUM
    ):
        for repeat_token in (False, True):
            count += 1
            digest.update(
                _run_scenario(sens, pre, empties, dark, fast, edge, fsize, lum, 0.1, repeat_token).encode("utf-8")
            )
            digest.update(b"\x1e")
    return digest.hexdigest(), count


VISION_GOLDEN_DIGEST = "4cd02435559d94b2070e20c5df2f38e78f5a5f69fc35051e7cbada4192b5420b"


def test_vision_detector_deadcode_removal_is_bit_identical() -> None:
    """删除 _finalize_tracks 死分支后，960 组场景输出必须逐位不变。"""
    digest, count = vision_golden_digest()
    check("vision/scenario-count", count == SCENARIO_SPACE, f"count={count}")
    check(
        "vision/golden-digest-unchanged",
        digest == VISION_GOLDEN_DIGEST,
        f"expected {VISION_GOLDEN_DIGEST} got {digest}",
    )


DEAD_SYMBOLS = ("MISS_TRACKED", "MISS_EDGE_GUARD", "vision_confirmed_miss_after_lit")


def test_dead_status_and_detail_are_gone() -> None:
    """死字符串不得再回到**可执行代码**里（注释里可以解释它们为什么被删）。

    做法是解析 AST 收集全部字符串常量，而不是全文 grep —— 否则源码里那句
    "解释为什么删掉它"的注释反而会把测试自己钉死。任何人把这些名字重新
    接回 ``_set_status()`` / ``_emit()`` 都会在这里红。
    """
    import ast

    path = Path(__file__).resolve().parent.parent / "rm_client" / "combat_events.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for dead in DEAD_SYMBOLS:
        check(f"dead-code/{dead}-not-in-code", dead not in literals, "reachable string literal again")

    # 再钉一层：优先级表里也不该有这三个状态的条目。
    detector_cls = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "VisionKillDetector"
    )
    priority = next(
        node for node in ast.walk(detector_cls)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "priority" for t in node.targets)
    )
    keys = {k.value for k in priority.value.keys}
    check("dead-code/priority-table-cleaned", not (keys & set(DEAD_SYMBOLS)), f"{sorted(keys)}")
    for live in ("LIT_TRACKED", "LOSS_PROBE_DARK", "DIM_TRACKED", "VISION_CONFIRMED", "MISS_UNSTABLE"):
        check(f"dead-code/priority-table-keeps-{live}", live in keys, f"{sorted(keys)}")


def test_live_loss_probe_path_still_works() -> None:
    """活着的 LOSS_PROBE 路径必须仍然能确认击杀（删除死代码没有打断它）。"""
    detector = VisionKillDetector()
    detector.process([LIT], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    detector.process([DIM], alliance="Red", frame_luminance=0.4, frame_token=2, now=1.1)
    detector.process([DIM], alliance="Red", frame_luminance=0.4, frame_token=3, now=1.2)
    evidence = _Loss(probe_dark=True)
    for token, now in ((4, 1.3), (5, 1.4), (6, 1.5)):
        out = detector.process([], alliance="Red", frame_luminance=0.4, frame_token=token,
                               frame_size=(640, 360), loss_evidence=evidence, now=now)
        check(f"vision/loss-probe-silent-{token}", out == [], f"{out}")
    events = detector.process([], alliance="Red", frame_luminance=0.4, frame_token=7,
                              frame_size=(640, 360), loss_evidence=evidence, now=1.65)
    check(
        "vision/loss-probe-confirms",
        len(events) == 1 and events[0].detail == "vision_confirmed_dark_loss_probe",
        f"{events}",
    )


def test_live_lamp_off_path_still_works() -> None:
    """经典灭灯路径不受影响。"""
    detector = VisionKillDetector()
    detector.process([LIT], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    for token, now in ((2, 1.1), (3, 1.2)):
        out = detector.process([OFF], alliance="Red", frame_luminance=0.4, frame_token=token, now=now)
        check(f"vision/lamp-off-silent-{token}", out == [], f"{out}")
    events = detector.process([OFF], alliance="Red", frame_luminance=0.4, frame_token=4, now=1.4)
    check("vision/lamp-off-confirms",
          len(events) == 1 and events[0].detail == "vision_confirmed_lamp_off", f"{events}")


def test_live_dim_lamp_path_still_works() -> None:
    """相对过暗路径不受影响。"""
    detector = VisionKillDetector()
    detector.process([LIT], alliance="Red", frame_luminance=0.4, frame_token=1, now=1.0)
    for token, now in ((2, 1.10), (3, 1.18)):
        out = detector.process([DIM], alliance="Red", frame_luminance=0.4, frame_token=token, now=now)
        check(f"vision/dim-silent-{token}", out == [], f"{out}")
    events = detector.process([DIM], alliance="Red", frame_luminance=0.4, frame_token=4, now=1.35)
    check("vision/dim-confirms",
          len(events) == 1 and events[0].detail == "vision_confirmed_dim_lamp", f"{events}")


def test_reachable_statuses_are_exactly_the_documented_set() -> None:
    """删除死代码后，last_status 可能取到的值里不再有 MISS_* 系列。

    README_战果判定与阵营.md:33 举例的 MISS_TRACKED 属于已被 LOSS_* 取代的旧
    命名，文档需要同步（不在本组可改文件内，见报告 blockers）。
    """
    seen: set[str] = set()
    for sens, pre, empties, dark, fast, edge, fsize, lum in itertools.product(
        _SENS, _PRE_OBS, range(5), _DARK, _FAST, _EDGE, _FSIZE, _LUM
    ):
        detector = VisionKillDetector()
        now = 1.0
        for index, obs in enumerate((LIT,) + pre):
            detector.process([obs], alliance="Red", frame_luminance=lum, frame_token=index + 1,
                             frame_size=fsize, sensitivity=sens, now=now + 0.1 * index)
            seen.add(detector.last_status)
        for index in range(empties):
            detector.process([], alliance="Red", frame_luminance=0.1, frame_token=100 + index,
                             frame_size=fsize, sensitivity=sens,
                             loss_evidence=_Loss(probe_dark=dark, moving_fast=fast, near_edge=edge),
                             now=now + 0.1 * (len(pre) + 1 + index))
            seen.add(detector.last_status)
    check("vision/MISS_TRACKED-unreachable", "MISS_TRACKED" not in seen, f"{sorted(seen)}")
    for live in ("LIT_TRACKED", "LOSS_PROBE_DARK", "DIM_TRACKED", "VISION_CONFIRMED", "LOSS_EDGE_GUARD"):
        check(f"vision/{live}-still-reachable", live in seen, f"{sorted(seen)}")


# ======================================================================
# 汇总
# ======================================================================

TESTS = [
    test_injury_stat_cumulative_is_not_an_edge,
    test_injury_stat_first_sample_only_baselines,
    test_injury_stat_increment_is_the_only_edge,
    test_injury_stat_counter_reset_rebaselines_quietly,
    test_injury_stat_missing_field9_never_touches_baseline,
    test_injury_stat_never_poisons_kill_freshness,
    test_dual_source_same_kill_collapses_to_one,
    test_dual_source_event_topic_wins_when_it_arrives_first,
    test_dual_source_reverse_order_also_collapses,
    test_dual_source_alive_state_also_merges,
    test_dedupe_still_separates_distinct_victims,
    test_dedupe_still_separates_two_known_distinct_killers,
    test_dedupe_replays_after_window_still_blocked,
    test_dedupe_merge_window_expires,
    test_vision_detector_deadcode_removal_is_bit_identical,
    test_dead_status_and_detail_are_gone,
    test_live_loss_probe_path_still_works,
    test_live_lamp_off_path_still_works,
    test_live_dim_lamp_path_still_works,
    test_reachable_statuses_are_exactly_the_documented_set,
]


def main() -> int:
    for test in TESTS:
        try:
            test()
        except Exception as error:  # noqa: BLE001
            FAILURES.append(f"{test.__name__} raised {error!r}")
            print(f"[FAIL] {test.__name__} raised {error!r}")
    print(f"\n{PASSED} passed, {len(FAILURES)} failed  (scenarios: {SCENARIO_SPACE * 2})")
    for failure in FAILURES:
        print(f"  - {failure}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
