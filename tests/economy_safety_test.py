"""经济链路的**准入 / 观察 / 拒绝原因 / mock 裁判源**四条不变量回归。

为什么这个文件存在
------------------
``rm_client/economy.py`` 是整条链路上唯一会真扣弹、真扣金的地方。改它之前
先把四条实测缺陷钉在这里，改完之后逐条对照前后数字：

1. **submit() 没有在途准入控制。** 可以在第一帧还没确认时再发一帧**完全相同**
   的 QoS1 CommonCommand。CommonCommand 按"被裁判系统接受的帧"计费，所以
   第二帧不是重试，是第二次扣款。这条不变量此前只由**冻结的** UI 层
   （``economy_mixin.py:370-373``）兜底，而 economy.py 自己却声称要保护它。
   实测（改前）：同一 key 连发 2 帧，payload 完全相同（``0801100a``）。

2. **update() 是单槽观察者。** ``update`` 只看 ``history[-1]``。被后一笔顶替的
   交易永远停在 ``pending``，它自己的 5 秒截止时间永远不会被检查，于是
   "未确认；不会自动重试" 这条提示永远不会出现——操作员看到的是一个永远
   转圈的条目。实测（改前）：60 秒 / 600 次 update 后仍是 ``pending``。

3. **_apply_mock 里 economy_managed=True 是一次性闩锁。**
   ``RobotState.tick`` 在该标志为真时整段跳过金币/弹量/总收入的仿真源，
   而这个标志从设成真之后再没人清过。实测（改前）：一笔 10 金币 / 10 发
   mock 购买之后，3600 次 tick（60 秒）里金币、弹量、总收入**一字不动**。

4. **submit() 把所有传输拒绝压成同一句话。** ``OfficialSafety.can_publish``
   已经算好了可执行的原因（"match control is LOCKED"、"CommonCommand
   exceeds 10 Hz"、"capability economy remains LOCKED MOCK"…），
   ``MqttBackend`` 把它抄进 ``last_error``，然后 ``submit`` 把它扔掉，统一
   写成 "MQTT 请求未发送"。实测（改前）：5 个不同原因 → 5 次同样的一句话。

跑法：``python tests/economy_safety_test.py``
"""

from __future__ import annotations

import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client import economy as economy_module  # noqa: E402
from rm_client.economy import (  # noqa: E402
    AMBIGUOUS_RESEND_REASON,
    CONFIRM_TIMEOUT_S,
    DUPLICATE_INFLIGHT_REASON,
    NO_SENDER_REASON,
    SEND_FAILED_FALLBACK,
    EconomyCommandBridge,
    EconomyOffer,
    available_actions,
    encode_air_support_command,
    encode_common_command,
    request_payload,
)
from rm_client.model import RobotState  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

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


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

def fresh_state(**overrides) -> RobotState:
    state = RobotState()
    state.match_mode = "RMUC7v7"
    state.robot_profile = "Infantry1"
    state.current_health = state.max_health
    state.remaining_economy = 500
    state.total_economy_obtained = 620
    state.remaining_ammo = 180
    state.ammo_capacity = 180
    state.economy_status_revision = 0
    state.economy_command_status = ""
    state.economy_mock_mode = True
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def offer_for(state: RobotState, key: str = "rmuc_infantry_17_10") -> EconomyOffer:
    return {offer.key: offer for offer in available_actions(state)}[key]


class RefusingWindow:
    """Stand-in for PolarRMWindow's bound ``send_economy_request``.

    Mirrors the real sender shape: a *bound method* that returns a bare bool
    and leaves the transport's own diagnosis in ``mqtt.last_error`` -- exactly
    what ``MqttBackend.publish_topic`` does on every failure path.
    """

    def __init__(self, reason: str = "", accepted: bool = False) -> None:
        self.mqtt = type("Mqtt", (), {})()
        self.reason = reason
        self.accepted = accepted
        self.calls = 0

    def send_economy_request(self, _request) -> bool:
        self.calls += 1
        self.mqtt.last_error = self.reason
        return self.accepted


# --------------------------------------------------------------------------
# 1. 在途准入控制
# --------------------------------------------------------------------------

def test_admission_refuses_identical_in_flight_frame() -> None:
    """第二帧完全相同的 QoS1 帧必须**根本不出网**。"""

    state = fresh_state()
    offer = offer_for(state)
    window = RefusingWindow(accepted=True)
    bridge = EconomyCommandBridge()
    bridge.sender = window.send_economy_request

    first = bridge.submit(state, offer, mock=False, now=1.0)
    second = bridge.submit(state, offer, mock=False, now=1.2)

    check("第一帧已发送", window.calls == 1, f"calls={window.calls}")
    check(
        "同一 key 在途时第二帧被拒",
        second.status == "rejected" and second.message == DUPLICATE_INFLIGHT_REASON,
        f"status={second.status} message={second.message!r}",
    )
    check(
        "被拒时传输层一次都没被调用（fail-closed，不是发了再撤）",
        window.calls == 1,
        f"calls={window.calls}",
    )
    check(
        "被拒的两帧 payload 完全相同（否则这条断言没意义）",
        first.request.payload == second.request.payload == b"\x08\x01\x10\x0a",
        f"{first.request.payload.hex()} vs {second.request.payload.hex()}",
    )
    check("两笔都进了 history，可审计", len(bridge.history) == 2, f"{len(bridge.history)}")
    check("被拒的那笔不在在途集合里", bridge.in_flight == 1, f"{bridge.in_flight}")
    check(
        "被拒的那笔不是 pending（UI 不会再被它挡住）",
        bridge.latest is second and second.status == "rejected",
    )


def test_admission_refusal_does_not_mutate_state() -> None:
    """准入拒绝只动交易记录，绝不碰 state —— 钱和弹量一个字节都不能变。"""

    state = fresh_state()
    offer = offer_for(state)
    window = RefusingWindow(accepted=True)
    bridge = EconomyCommandBridge()
    bridge.sender = window.send_economy_request

    before = (state.remaining_economy, state.remaining_ammo, state.economy_status_revision)
    bridge.submit(state, offer, mock=False, now=1.0)
    bridge.submit(state, offer, mock=False, now=1.2)
    bridge.submit(state, offer, mock=False, now=1.4)
    after = (state.remaining_economy, state.remaining_ammo, state.economy_status_revision)
    check("连拒两次后 state 未被改动", before == after, f"{before} -> {after}")


def test_admission_allows_a_different_frame_while_one_is_in_flight() -> None:
    """不同 payload = 不同裁判命令，不该被误伤。"""

    state = fresh_state()
    ten = offer_for(state, "rmuc_infantry_17_10")
    hundred = offer_for(state, "rmuc_infantry_17_100")
    window = RefusingWindow(accepted=True)
    bridge = EconomyCommandBridge()
    bridge.sender = window.send_economy_request

    first = bridge.submit(state, ten, mock=False, now=1.0)
    second = bridge.submit(state, hundred, mock=False, now=1.1)
    check("第一帧 pending", first.status == "pending", first.status)
    check("不同 payload 的第二帧被放行", second.status == "pending", second.status)
    check("两帧都出网了", window.calls == 2, f"calls={window.calls}")
    check("两笔都在途", bridge.in_flight == 2, f"{bridge.in_flight}")


def test_air_support_frames_are_admission_controlled_too() -> None:
    """AirSupportCommand 的 payload 与 quantity 无关，同一 command_id 就是同一帧。"""

    state = fresh_state(robot_profile="Aerial", air_support_active=False, air_support_can_free=False)
    paid = offer_for(state, "rmuc_air_support_paid")
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    first = bridge.submit(state, paid, mock=False, now=1.0)
    second = bridge.submit(state, paid, mock=False, now=1.05)
    check("AirSupport 载荷与 quantity 无关", first.request.payload == encode_air_support_command(2))
    check("同一 command_id 的第二帧被拒", second.status == "rejected", second.status)
    check("理由是重复扣款保护", second.message == DUPLICATE_INFLIGHT_REASON, second.message)


# --------------------------------------------------------------------------
# 1b. 超时后的模糊窗口（"没答复"不等于"被拒绝"）
# --------------------------------------------------------------------------

def test_timeout_blocks_identical_resend_until_the_settle_window() -> None:
    state = fresh_state()
    offer = offer_for(state)
    window = RefusingWindow(accepted=True)
    bridge = EconomyCommandBridge()
    bridge.sender = window.send_economy_request

    first = bridge.submit(state, offer, mock=False, now=1.0)
    check("超时后状态为 timed_out", bridge.update(state, now=6.01).status == "timed_out", first.status)
    check("超时文案保留「不会自动重试」", "不会自动重试" in first.message, first.message)

    retry = bridge.submit(state, offer, mock=False, now=6.2)
    check(
        "超时后立刻重发被拒（裁判系统可能仍在处理第一帧）",
        retry.status == "rejected" and retry.message == AMBIGUOUS_RESEND_REASON,
        f"status={retry.status} message={retry.message!r}",
    )
    check("被拒的重发没有出网", window.calls == 1, f"calls={window.calls}")

    later = bridge.submit(state, offer, mock=False, now=6.0 + CONFIRM_TIMEOUT_S + 0.5)
    check("模糊窗口过后允许重试", later.status == "pending", f"{later.status} {later.message}")
    check("重试确实出网了", window.calls == 2, f"calls={window.calls}")


def test_refusal_and_success_do_not_arm_the_ambiguous_window() -> None:
    """被拒 / 被确认的请求，钱的下落是明确的，不该再挡一次。"""

    state = fresh_state()
    offer = offer_for(state)

    refused = EconomyCommandBridge()
    refused.sender = RefusingWindow(reason="match control is LOCKED").send_economy_request
    refused.submit(state, offer, mock=False, now=1.0)
    again = refused.submit(state, offer, mock=False, now=1.1)
    check("传输层明确拒绝后允许立即重试", again.status == "rejected", again.status)
    check(
        "且重试拿到的是真实原因而不是模糊窗口",
        again.message == "match control is LOCKED",
        again.message,
    )

    confirmed = EconomyCommandBridge()
    confirmed.sender = RefusingWindow(accepted=True).send_economy_request
    confirmed.submit(state, offer, mock=False, now=1.0)
    state.remaining_ammo += 10
    state.economy_status_revision += 1
    confirmed.update(state, now=1.2)
    retry = confirmed.submit(state, offer, mock=False, now=1.3)
    check("被裁判确认后允许再次购买（是买第二次，不是重试）", retry.status == "pending", retry.status)


def test_settle_window_is_configurable() -> None:
    state = fresh_state()
    offer = offer_for(state)
    bridge = EconomyCommandBridge(ambiguous_settle_s=0.0)
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    bridge.submit(state, offer, mock=False, now=1.0)
    bridge.update(state, now=6.01)
    retry = bridge.submit(state, offer, mock=False, now=6.02)
    check("ambiguous_settle_s=0 可关闭模糊窗口", retry.status == "pending", f"{retry.status} {retry.message}")


# --------------------------------------------------------------------------
# 2. 多槽观察者
# --------------------------------------------------------------------------

def test_superseded_transaction_reaches_its_own_deadline() -> None:
    state = fresh_state()
    ten = offer_for(state, "rmuc_infantry_17_10")
    hundred = offer_for(state, "rmuc_infantry_17_100")
    window = RefusingWindow(accepted=True)
    bridge = EconomyCommandBridge()
    bridge.sender = window.send_economy_request

    old = bridge.submit(state, ten, mock=False, now=1.0)
    new = bridge.submit(state, hundred, mock=False, now=1.1)
    for step in range(1, 601):  # 60 s at 10 Hz
        bridge.update(state, now=1.1 + step * 0.1)

    check("被顶替的那笔不再停在 pending", old.status != "pending", old.status)
    check("被顶替的那笔按自己的截止时间超时", old.status == "timed_out", old.status)
    check("超时文案正确", old.message == "未确认；不会自动重试，避免 QoS1 重复扣款", old.message)
    check("完成时间晚于它自己的 deadline", old.completed_at >= old.deadline_at,
          f"{old.completed_at} < {old.deadline_at}")
    check("后一笔也各自到达终态", new.status == "timed_out", new.status)
    check("在途集合已清空", bridge.in_flight == 0, f"{bridge.in_flight}")


def test_superseded_transaction_can_still_be_confirmed() -> None:
    """顶替不等于作废：先发的那笔若真被裁判系统确认，仍应变成 succeeded。"""

    state = fresh_state()
    ten = offer_for(state, "rmuc_infantry_17_10")
    hundred = offer_for(state, "rmuc_infantry_17_100")
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    old = bridge.submit(state, ten, mock=False, now=1.0)
    bridge.submit(state, hundred, mock=False, now=1.1)
    state.remaining_ammo += 10
    state.economy_status_revision += 1
    bridge.update(state, now=1.2)
    check("被顶替的旧交易仍能被确认", old.status == "succeeded", f"{old.status} {old.message}")
    check("确认文案正确", old.message == "裁判系统已确认", old.message)


def test_one_observation_credits_at_most_one_transaction() -> None:
    """一次观测只能确认一笔；否则"队友也买了"会把我们第二笔也判成功。"""

    state = fresh_state()
    ten = offer_for(state, "rmuc_infantry_17_10")
    hundred = offer_for(state, "rmuc_infantry_17_100")
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    first = bridge.submit(state, ten, mock=False, now=1.0)
    second = bridge.submit(state, hundred, mock=False, now=1.1)
    state.remaining_ammo += 10  # 裁判系统只处理了第一笔
    state.economy_status_revision += 1
    bridge.update(state, now=1.2)
    check("第一笔被确认", first.status == "succeeded", first.status)
    check(
        "第二笔没有蹭到同一次观测的确认",
        second.status == "pending",
        f"{second.status} {second.message}",
    )
    check("第二笔的基线已重设到当前状态", second.baseline_ammo == 190, f"{second.baseline_ammo}")


def test_a_still_pending_transaction_is_never_finalised_early() -> None:
    """还没到终态的交易，**不许被提前当成"已结算"**。

    第 106 轮加。背景是一次变异审计把 `_settle` 里「仍然 pending」那一支的
    `return False` 改成 `return True`，**六个套件全部照样绿**。

    ## 这里要把两件事分开，因为它们**不是同一件**

    改错之后**确实**会出问题，但**不是**「没等到裁判确认就把钱扣了」：

    * `_settle` 里有**三道互相独立的闸**——
      `self._finish(transaction)`、`if succeeded:`、`self._rebase_rest(...)`。
      `succeeded` 只在裁判确认那一支才置 True，
      所以提前 `return True` **走不到 `_rebase_rest`**，**金币不会被错扣**。
    * 真正坏掉的是：提前返回会让执行**继续往下走**，
      于是给一笔**仍然 pending** 的交易**盖上 `completed_at` 并 `_finish` 掉**。
      交易被当成终态移出队列，**之后真的来确认时已经没有在途交易可确认了**。

    所以这里钉的是**「pending 不得被提前终结」**，不是「金币不会被错扣」
    —— 后者本来就由 `if succeeded:` 挡着，那道闸另有测试在钉。
    """
    state = fresh_state()
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    tx = bridge.submit(state, offer_for(state, "rmuc_infantry_17_10"), mock=False, now=1.0)

    # 未确认、也远未到 deadline：既没有裁判观测，也没有超时。
    result = bridge._settle(state, tx, now=1.05)

    check("_settle 对仍然 pending 的交易返回 False（= 本拍没有结算任何东西）",
          result is False, f"got {result!r}")
    check("它没有被标成终态", tx.status == "pending", tx.status)
    check("它没有被盖上完成时间", not tx.completed_at, repr(tx.completed_at))
    check("它还在队列里等确认", bridge._inflight.get(tx.sequence) is tx,
          f"inflight={sorted(bridge._inflight)}")


# --------------------------------------------------------------------------
# 2b. 逐位一致：只有一笔在途时，新旧判定表必须完全相同
# --------------------------------------------------------------------------

def _legacy_update_decision(state, transaction, now: float) -> tuple[str, str | None]:
    """改前 ``update`` 的判定表，原样搬运，用来逐位比对。

    ``pending`` 时改前是**原样返回** ``transaction``、不碰 message，所以
    这里的 pending 分支返回 ``None`` 表示"message 不参与比对"。
    """

    if transaction.request.mock and now >= transaction.mock_complete_at:
        return "succeeded", "MOCK 操作成功"
    explicit = str(getattr(state, "economy_command_status", "")).lower()
    revision_changed = int(getattr(state, "economy_status_revision", 0)) > transaction.baseline_revision
    if revision_changed and explicit in {"succeeded", "accepted", "success"}:
        return "succeeded", "裁判系统已确认"
    if revision_changed and explicit in {"rejected", "denied", "failed"}:
        return "rejected", "裁判系统拒绝请求"
    if not transaction.request.mock and EconomyCommandBridge._official_result_observed(state, transaction):
        return "succeeded", "裁判系统已确认"
    if now >= transaction.deadline_at:
        return "timed_out", "未确认；不会自动重试，避免 QoS1 重复扣款"
    return "pending", None


def test_single_request_decision_table_is_bit_identical() -> None:
    """单笔在途（也就是 99% 的实机路径）下，状态/文案/完成时间逐位不变。"""

    statuses = ["", "succeeded", "accepted", "success", "rejected", "denied", "failed", "REJECTED"]
    deltas = [0, 1, 2]
    ammo_deltas = [0, 10, -5]
    now_offsets = [0.0, 0.5, 2.0, 4.999, 5.0, 5.001, 20.0]
    mismatches: list[str] = []
    compared = 0
    for mock in (False, True):
        for status in statuses:
            for delta in deltas:
                for ammo_delta in ammo_deltas:
                    for offset in now_offsets:
                        # --- 旧实现：单槽、就地判定 ---
                        state_a = fresh_state()
                        offer = offer_for(state_a)
                        bridge_a = EconomyCommandBridge()
                        bridge_a.sender = RefusingWindow(accepted=True).send_economy_request
                        base = 10.0
                        legacy_tx = bridge_a.submit(state_a, offer, mock=mock, now=base)
                        state_a.economy_status_revision = 5
                        state_a.economy_command_status = status
                        if delta:
                            state_a.economy_status_revision += delta
                        if ammo_delta:
                            state_a.remaining_ammo += ammo_delta
                        expected = _legacy_update_decision(state_a, legacy_tx, base + offset)

                        # --- 新实现：同一份输入 ---
                        state_b = fresh_state()
                        offer_b = offer_for(state_b)
                        bridge_b = EconomyCommandBridge()
                        bridge_b.sender = RefusingWindow(accepted=True).send_economy_request
                        tx_b = bridge_b.submit(state_b, offer_b, mock=mock, now=base)
                        state_b.economy_status_revision = 5
                        state_b.economy_command_status = status
                        if delta:
                            state_b.economy_status_revision += delta
                        if ammo_delta:
                            state_b.remaining_ammo += ammo_delta
                        returned = bridge_b.update(state_b, now=base + offset)
                        actual = (tx_b.status, tx_b.message)
                        compared += 1
                        mismatch = tx_b.status != expected[0] or returned is not tx_b
                        if not mismatch and expected[1] is not None:
                            mismatch = tx_b.message != expected[1]
                        if mismatch:
                            mismatches.append(
                                f"mock={mock} status={status!r} rev+={delta} ammo{delta:+d} "
                                f"t+{offset}: {actual!r} != {expected!r} (returned={returned is tx_b})"
                            )
    check(
        f"单笔在途判定表逐位一致（{compared} 组输入）",
        not mismatches,
        f"{len(mismatches)} 组不一致，例：{mismatches[:3]}",
    )


def test_update_returns_latest_when_nothing_changed() -> None:
    state = fresh_state()
    offer = offer_for(state)
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    check("空 history 时 update 返回 None", bridge.update(state, now=1.0) is None)
    tx = bridge.submit(state, offer, mock=False, now=1.0)
    check("没有状态变化时返回 latest（与改前一致）", bridge.update(state, now=1.1) is tx)
    state.remaining_ammo += 10
    state.economy_status_revision += 1
    check("发生状态变化时返回被结算的那笔", bridge.update(state, now=1.2) is tx)
    settled = tx
    check("已是终态时仍返回 latest", bridge.update(state, now=1.3) is settled)
    check("history 只保留最近 32 笔", len(bridge.history) == 1, f"{len(bridge.history)}")


# --------------------------------------------------------------------------
# 3. 传输拒绝原因不再被压平
# --------------------------------------------------------------------------

def test_refusal_reason_is_preserved() -> None:
    """safety.py 算好的原因必须一路活到交易文案里。"""

    safety_reasons = [
        "match control is LOCKED",
        "capability economy remains LOCKED MOCK",
        "CommonCommand exceeds 10 Hz",
        "not in an authoritative fighting stage",
        "current scenario is mock-only and does not allow real MQTT publishing",
        "qualification token invalid or expired",
        "E-stop active; only the dedicated zero KMC bypass is allowed",
    ]
    lost: list[str] = []
    for reason in safety_reasons:
        state = fresh_state()
        bridge = EconomyCommandBridge()
        bridge.sender = RefusingWindow(reason=reason).send_economy_request
        tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
        if tx.message != reason:
            lost.append(f"{reason!r} -> {tx.message!r}")
    check(f"{len(safety_reasons)} 条 safety.py 真实原因原样透传", not lost, f"丢失：{lost}")


def test_refusal_reason_forms() -> None:
    state = fresh_state()

    # 1) 显式 (ok, reason) —— 传输层知道自己为什么失败时应当用这种
    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: (False, "CommonCommand exceeds 10 Hz")
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("(ok, reason) 形式被采纳", tx.message == "CommonCommand exceeds 10 Hz", tx.message)

    # 2) 纯字符串
    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: "未连接 MQTT 客户端"
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("字符串形式被采纳", tx.message == "未连接 MQTT 客户端", tx.message)

    # 3) 带 .ok/.reason 的对象
    class Outcome:
        ok = False
        reason = "capability economy remains LOCKED MOCK"

    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: Outcome()
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check(".ok/.reason 对象形式被采纳", tx.message == "capability economy remains LOCKED MOCK", tx.message)

    # 4) 没有发送通道
    bridge = EconomyCommandBridge()
    bridge.sender = None
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("没有通道时给出具体原因", tx.message == NO_SENDER_REASON, tx.message)

    # 5) 发送器抛异常：必须算拒绝，不能留成 pending 干等 5 秒
    def exploding(_request):
        raise RuntimeError("broker socket closed")

    bridge = EconomyCommandBridge()
    bridge.sender = exploding
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check(
        "发送器抛异常被记为拒绝并带原因",
        tx.status == "rejected" and "broker socket closed" in tx.message,
        f"{tx.status} {tx.message!r}",
    )
    check("异常后不在途集合里", bridge.in_flight == 0, f"{bridge.in_flight}")

    # 6) 什么都没暴露 —— 退回历史文案，不崩
    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: False
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("无法取得原因时退回旧文案", tx.message == SEND_FAILED_FALLBACK, tx.message)
    check("旧文案与改前逐字相同", SEND_FAILED_FALLBACK == "MQTT 请求未发送", SEND_FAILED_FALLBACK)

    # 7) numpy 风格的真值 / 整数：不是 bool 子类，但仍是"发出去了"
    import numpy as np

    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: np.bool_(True)
    check("numpy bool 真值被认作已发送", bridge.submit(state, offer_for(state), mock=False, now=1.0).status == "pending")
    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: 1
    check("整数真值被认作已发送", bridge.submit(state, offer_for(state), mock=False, now=1.0).status == "pending")
    bridge = EconomyCommandBridge()
    bridge.sender = lambda _request: np.bool_(False)
    check("numpy bool 假值被认作拒绝", bridge.submit(state, offer_for(state), mock=False, now=1.0).status == "rejected")


def test_success_outcome_never_reports_a_reason() -> None:
    state = fresh_state()
    window = RefusingWindow(reason="上一条无关的陈旧错误", accepted=True)
    bridge = EconomyCommandBridge()
    bridge.sender = window.send_economy_request
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("发送成功时状态是 pending", tx.status == "pending", tx.status)
    check("发送成功时不带任何原因", tx.message == "已发送，等待裁判系统确认", tx.message)


def test_refusal_observer_is_notified() -> None:
    state = fresh_state()
    seen: list[tuple[str, str]] = []
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(reason="match control is LOCKED").send_economy_request
    bridge.on_refusal = lambda request, reason: seen.append((request.action, reason))
    bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("on_refusal 收到真实原因", seen == [("rmuc_infantry_17_10", "match control is LOCKED")], f"{seen}")

    def boom(_request, _reason):
        raise RuntimeError("observer exploded")

    bridge.on_refusal = boom
    tx = bridge.submit(state, offer_for(state), mock=False, now=1.0)
    check("观察者抛异常不会影响交易结果", tx.status == "rejected", tx.status)


# --------------------------------------------------------------------------
# 4. mock 裁判源不再被一次性闩锁掐死
# --------------------------------------------------------------------------

def test_mock_generators_still_match_robot_state_tick_source() -> None:
    """桥接里抄了 RobotState.tick 的三条公式；源文件一改，这里必须立刻红。"""

    source = (REPO_ROOT / "rm_client" / "model.py").read_text(encoding="utf-8")
    body = re.sub(r"\s+", " ", source)
    expected = [
        "self.remaining_ammo = max(0, int(ammo_capacity - (self.stage_elapsed_sec * 0.18) % max(1, min(80, ammo_capacity))))",
        "self.remaining_economy = max(0, int(260 + 80 * math.sin(elapsed * 0.16)))",
        "self.total_economy_obtained = 620 + int(self.stage_elapsed_sec * 1.7)",
    ]
    missing = [line for line in expected if line not in body]
    check("三条 mock 裁判源公式仍与 model.py 逐字一致", not missing, f"已漂移：{missing}")

    # 生成器本身也要和公式等价
    gold_ok = all(
        EconomyCommandBridge._mock_gold_generator(t)
        == max(0, int(260 + 80 * math.sin(t * 0.16)))
        for t in (0.0, 1.7, 123.456, 98765.4)
    )
    check("金币生成器与公式逐位一致", gold_ok)
    ammo_ok = all(
        EconomyCommandBridge._mock_ammo_generator(stage, cap)
        == max(0, int(cap - (stage * 0.18) % max(1, min(80, cap))))
        for stage in (0, 1, 7, 60, 444, 4210)
        for cap in (0, 1, 12, 180, 190)
    )
    check("弹量生成器与公式逐位一致", ammo_ok)
    income_ok = all(
        EconomyCommandBridge._mock_income_generator(stage) == 620 + int(stage * 1.7)
        for stage in (0, 1, 60, 4210)
    )
    check("总收入生成器与公式逐位一致", income_ok)


def test_mock_feed_keeps_moving_after_a_purchase() -> None:
    """改前：60 秒 3600 次 tick，金币/弹量/总收入一字不动。"""

    state = fresh_state()
    bridge = EconomyCommandBridge()
    bridge.submit(state, offer_for(state), mock=True, now=1.0)
    settled = bridge.update(state, now=1.5)
    check("mock 交易成功", settled.status == "succeeded", settled.status)
    gold_at_settle, ammo_at_settle = state.remaining_economy, state.remaining_ammo
    check("结算当帧即扣款", gold_at_settle == 490, f"{gold_at_settle}")
    check("结算当帧即到弹", ammo_at_settle == 190, f"{ammo_at_settle}")

    seen_gold, seen_ammo, seen_income = {gold_at_settle}, {ammo_at_settle}, {state.total_economy_obtained}
    clock = 1.0
    for index in range(3600):  # 60 s at 60 Hz
        clock += 1.0 / 60.0
        state.stage_elapsed_sec = int(clock)  # 真机 60 秒后 tick 会推进到这个值
        state.tick(1.0 / 60.0)
        state.stage_elapsed_sec = int(clock)
        bridge.update(state, now=clock)
        seen_gold.add(state.remaining_economy)
        seen_ammo.add(state.remaining_ammo)
        seen_income.add(state.total_economy_obtained)

    check(f"金币在 60 秒里取到过 {len(seen_gold)} 个不同值（改前恒为 1）", len(seen_gold) > 1, f"{seen_gold}")
    check(f"弹量在 60 秒里取到过 {len(seen_ammo)} 个不同值（改前恒为 1）", len(seen_ammo) > 1, f"{seen_ammo}")
    check(f"总收入在 60 秒里取到过 {len(seen_income)} 个不同值（改前恒为 1）", len(seen_income) > 1, f"{seen_income}")
    check("弹量始终不超过弹匣容量", state.remaining_ammo <= state.ammo_capacity,
          f"{state.remaining_ammo} > {state.ammo_capacity}")
    check("金币从未为负", state.remaining_economy >= 0, f"{state.remaining_economy}")


def test_mock_feed_reproduces_the_purchase_value_bit_exactly() -> None:
    """买完那一帧必须和改前一模一样：490 金币 / 190 发，不能跳变。"""

    state = fresh_state()
    bridge = EconomyCommandBridge()
    bridge.submit(state, offer_for(state), mock=True, now=10.0)
    check("未结算时 state 不被改动", (state.remaining_economy, state.remaining_ammo) == (500, 180),
          f"{(state.remaining_economy, state.remaining_ammo)}")
    check("结算前仍 pending", bridge.update(state, now=10.20).status == "pending")
    bridge.update(state, now=10.40)
    check("结算当帧金币逐位等于 490", state.remaining_economy == 490, f"{state.remaining_economy}")
    check("结算当帧弹量逐位等于 190", state.remaining_ammo == 190, f"{state.remaining_ammo}")
    check("结算当帧弹匣容量逐位等于 190（与改前一致）", state.ammo_capacity == 190, f"{state.ammo_capacity}")


def test_mock_feed_is_a_pure_deduction_never_a_credit() -> None:
    """钱只能变少。桥接写出的金币必须恒等于「同一时刻若没买过该是几何 − 本次花费」。"""

    state = fresh_state()
    bridge = EconomyCommandBridge()
    offer = offer_for(state)
    cost = offer.cost
    pre_purchase_gold = state.remaining_economy
    anchor_clock = 1.5
    bridge.submit(state, offer, mock=True, now=1.0)
    bridge.update(state, now=anchor_clock)
    check("mock 花费就是报价", cost == 10, f"{cost}")
    check(
        "购买只把水位压低到「买前值 − 花费」",
        state.remaining_economy == pre_purchase_gold - cost,
        f"{state.remaining_economy} != {pre_purchase_gold - cost}",
    )

    def baseline(clock: float) -> int:
        """同一条裁判源曲线在「没买过」时该有的值（锚点与真实实现逐字相同）。"""

        return pre_purchase_gold + (
            EconomyCommandBridge._mock_gold_generator(clock)
            - EconomyCommandBridge._mock_gold_generator(anchor_clock)
        )

    clock = anchor_clock
    mismatches = 0
    for _ in range(600):  # 10 s at 60 Hz
        clock += 1.0 / 60.0
        state.tick(1.0 / 60.0)
        state.stage_elapsed_sec = int(clock)
        bridge.update(state, now=clock)
        if state.remaining_economy != baseline(clock) - cost:
            mismatches += 1
    check("600 帧里金币恒等于「无购买基线 − 花费」（绝不凭空多钱）", mismatches == 0, f"{mismatches} 帧不符")
    check(
        "末帧金币仍等于无购买基线减花费",
        state.remaining_economy == baseline(clock) - cost,
        f"{state.remaining_economy} != {baseline(clock) - cost}",
    )
    check("末帧金币为非负", state.remaining_economy >= 0, f"{state.remaining_economy}")


def test_mock_feed_hands_the_fields_back_when_the_authoritative_feed_returns() -> None:
    """mock 闩锁被持有时若切到真实裁判源，必须立刻交还，绝不把 mock 偏移压到真实值上。"""

    state = fresh_state()
    bridge = EconomyCommandBridge()
    bridge.submit(state, offer_for(state), mock=True, now=1.0)
    bridge.update(state, now=1.5)
    check("持有期间标志为真", state.economy_managed is True)

    state.official_state_active = True
    bridge.update(state, now=1.6)
    check("切到官方源后闩锁被清", state.economy_managed is False, f"{state.economy_managed}")

    real_gold, real_ammo = 777, 42
    state.remaining_economy, state.remaining_ammo = real_gold, real_ammo
    for clock in (1.2, 1.3, 5.0, 90.0):
        state.tick(1.0 / 60.0)
        state.stage_elapsed_sec = int(clock)
        bridge.update(state, now=clock)
    check("真实值不再被桥接改写", state.remaining_economy == real_gold, f"{state.remaining_economy}")
    check("真实弹量不再被桥接改写", state.remaining_ammo == real_ammo, f"{state.remaining_ammo}")


def test_mock_feed_is_released_by_economy_mock_mode_off() -> None:
    state = fresh_state()
    bridge = EconomyCommandBridge()
    bridge.submit(state, offer_for(state), mock=True, now=1.0)
    bridge.update(state, now=1.5)
    check("持有期间标志为真", state.economy_managed is True)
    state.economy_mock_mode = False
    bridge.update(state, now=1.6)
    check("economy_mock_mode=False 即交还", state.economy_managed is False, f"{state.economy_managed}")


def test_bridge_does_not_touch_a_state_it_never_bought_on() -> None:
    """没有 mock 购买过的 state，桥接一个字节都不能写（老路径逐位不变）。"""

    state = fresh_state()
    bridge = EconomyCommandBridge()
    bridge.sender = RefusingWindow(accepted=True).send_economy_request
    bridge.submit(state, offer_for(state), mock=False, now=1.0)
    for clock in (1.1, 1.2, 3.0, 9.9):
        bridge.update(state, now=clock)
    check("economy_managed 仍为假", state.economy_managed is False, f"{state.economy_managed}")
    check("金币未被桥接改动", state.remaining_economy == 500, f"{state.remaining_economy}")
    check("弹量未被桥接改动", state.remaining_ammo == 180, f"{state.remaining_ammo}")
    check("总收入未被桥接改动", state.total_economy_obtained == 620, f"{state.total_economy_obtained}")


def test_second_mock_purchase_accumulates() -> None:
    state = fresh_state()
    bridge = EconomyCommandBridge()
    offer = offer_for(state)
    bridge.submit(state, offer, mock=True, now=1.0)
    bridge.update(state, now=1.5)
    check("第一次：490 / 190", (state.remaining_economy, state.remaining_ammo) == (490, 190),
          f"{(state.remaining_economy, state.remaining_ammo)}")
    bridge.submit(state, offer, mock=True, now=2.0)
    bridge.update(state, now=2.5)
    check("第二次再扣 10 金", state.remaining_economy == 480, f"{state.remaining_economy}")
    check("第二次再加 10 发", state.remaining_ammo == 200, f"{state.remaining_ammo}")
    check("弹匣容量跟着抬到 200（保证 X<=Y 的显示不被撑破）", state.ammo_capacity == 200, f"{state.ammo_capacity}")
    clock = 2.5
    for _ in range(120):
        clock += 1.0 / 60.0
        state.tick(1.0 / 60.0)
        state.stage_elapsed_sec = int(clock)
        bridge.update(state, now=clock)
        if state.remaining_ammo > state.ammo_capacity:
            check("长时间运行后弹量仍不超过容量", False, f"{state.remaining_ammo} > {state.ammo_capacity}")
            return
    check("长时间运行后弹量始终不超过容量", True)


# --------------------------------------------------------------------------
# 5. 未改动路径的逐位一致
# --------------------------------------------------------------------------

def test_unchanged_paths_are_bit_identical() -> None:
    state = fresh_state()
    bridge = EconomyCommandBridge()
    tx = bridge.submit(state, offer_for(state), mock=True, now=1.0)
    check("varint 编码未变", encode_common_command(1, 10) == b"\x08\x01\x10\x0a")
    check("大 varint 未变", encode_common_command(5, 150) == b"\x08\x05\x10\x96\x01")
    check("空中支援编码未变", encode_air_support_command(2) == b"\x08\x02")
    check("提交时不变更 state", (state.remaining_economy, state.remaining_ammo) == (500, 180))

    payload = request_payload(tx.request)
    check("request_payload 协议标识未变", payload["protocol"] == "rm.economy.request.v2", payload["protocol"])
    check(
        "request_payload 字段集合未变（identity 是 property，不进 asdict）",
        set(payload) == {
            "protocol", "sequence", "timestamp", "match_mode", "robot_profile", "action",
            "quantity", "cost", "command_type", "command_param", "topic", "payload", "mock",
        },
        f"{sorted(payload)}",
    )
    check("request_payload 载荷十六进制未变", payload["payload"] == "0801100a", payload["payload"])

    check("mock 提交时 mock 为真", tx.request.mock is True)
    check("mock 命令号未变", tx.request.command_type == 1, f"{tx.request.command_type}")
    check("mock 命令参数未变", tx.request.command_param == 10, f"{tx.request.command_param}")
    check("mock 等待文案未变", tx.message == "MOCK 正在处理", tx.message)
    check("mock 确认延迟仍是 0.36 秒", abs(tx.mock_complete_at - tx.submitted_at - 0.36) < 1e-12)
    check("mock 确认超时仍是 2 秒", abs(tx.deadline_at - tx.submitted_at - 2.0) < 1e-12)

    official = EconomyCommandBridge()
    official.sender = RefusingWindow(accepted=True).send_economy_request
    live = official.submit(state, offer_for(state), mock=False, now=2.0)
    check("正式提交待确认文案未变", live.message == "已发送，等待裁判系统确认", live.message)
    check("正式确认超时仍是 5 秒", abs(live.deadline_at - live.submitted_at - 5.0) < 1e-12)
    check("正式提交没有 mock 完成时间", live.mock_complete_at == 0.0)

    disabled = {o.key: o for o in available_actions(fresh_state(robot_profile="Sentinel", match_mode="RMUL3v3"))}
    blocked = disabled["rmul_sentinel_ammo_status"]
    try:
        official.submit(fresh_state(), blocked, mock=True, now=3.0)
    except ValueError as exc:
        check("不可用 offer 仍抛 ValueError", str(exc) == "联盟赛哨兵不开放弹丸购买", str(exc))
    else:
        check("不可用 offer 仍抛 ValueError", False, "没有抛异常")


def test_offer_matrix_is_untouched() -> None:
    """offer 构造完全没动过，这里做一次整体快照比对。"""

    state = fresh_state()
    offers = available_actions(state)
    check(
        "步兵 RMUC7v7 的 offer 矩阵逐位未变",
        [(o.key, o.cost, o.command_type, o.command_param, o.topic, o.enabled, o.confirmation)
         for o in offers]
        == [
            ("rmuc_infantry_17_10", 10, 1, 10, "CommonCommand", True, "double"),
            ("rmuc_infantry_17_100", 100, 1, 100, "CommonCommand", True, "double"),
            ("rmuc_infantry_remote_17_100", 150, 5, 100, "CommonCommand", True, "hold"),
            ("rmuc_remote_heal", 50, 6, 0, "CommonCommand", True, "hold"),
            ("rmuc_confirm_respawn", 0, 3, 0, "CommonCommand", False, "hold"),
            ("rmuc_gold_respawn", 40, 4, 0, "CommonCommand", False, "hold"),
        ],
        f"{[(o.key, o.cost, o.enabled) for o in offers]}",
    )


def test_hot_path_cost_of_update() -> None:
    """update() 每帧都跑：加了多槽遍历后不能把热路径拖垮。"""

    import statistics

    def sample(fn, count: int = 4000) -> float:
        for _ in range(400):
            fn()
        values = []
        for _ in range(count):
            start = time.perf_counter()
            fn()
            values.append((time.perf_counter() - start) * 1e6)
        return statistics.median(values)

    idle_state = fresh_state()
    idle_bridge = EconomyCommandBridge()
    idle = sample(lambda: idle_bridge.update(idle_state, now=1.1))

    # 一笔永远 pending 的正式请求：每帧都要走完整条判定链。
    busy_state = fresh_state()
    busy_bridge = EconomyCommandBridge()
    busy_bridge.sender = RefusingWindow(accepted=True).send_economy_request
    busy_bridge.submit(busy_state, offer_for(busy_state), mock=False, now=1.0)
    busy = sample(lambda: busy_bridge.update(busy_state, now=1.1))

    # 一笔已结算 + 桥接持有 mock 源：每帧还要驱动三条生成器。
    feed_state = fresh_state()
    feed_bridge = EconomyCommandBridge()
    feed_bridge.submit(feed_state, offer_for(feed_state), mock=True, now=1.0)
    feed_bridge.update(feed_state, now=1.5)
    feeding = sample(lambda: feed_bridge.update(feed_state, now=1.6))

    check(f"无事可做时 update 中位耗时 {idle:.2f}µs < 20µs", idle < 20.0, f"{idle:.2f}µs")
    check(f"一笔在途时 update 中位耗时 {busy:.2f}µs < 30µs", busy < 30.0, f"{busy:.2f}µs")
    check(f"驱动 mock 裁判源时 update 中位耗时 {feeding:.2f}µs < 30µs", feeding < 30.0, f"{feeding:.2f}µs")


def main() -> int:
    tests = [
        test_admission_refuses_identical_in_flight_frame,
        test_admission_refusal_does_not_mutate_state,
        test_admission_allows_a_different_frame_while_one_is_in_flight,
        test_air_support_frames_are_admission_controlled_too,
        test_timeout_blocks_identical_resend_until_the_settle_window,
        test_refusal_and_success_do_not_arm_the_ambiguous_window,
        test_settle_window_is_configurable,
        test_superseded_transaction_reaches_its_own_deadline,
        test_superseded_transaction_can_still_be_confirmed,
        test_one_observation_credits_at_most_one_transaction,
        test_a_still_pending_transaction_is_never_finalised_early,
        test_single_request_decision_table_is_bit_identical,
        test_update_returns_latest_when_nothing_changed,
        test_refusal_reason_is_preserved,
        test_refusal_reason_forms,
        test_success_outcome_never_reports_a_reason,
        test_refusal_observer_is_notified,
        test_mock_generators_still_match_robot_state_tick_source,
        test_mock_feed_keeps_moving_after_a_purchase,
        test_mock_feed_reproduces_the_purchase_value_bit_exactly,
        test_mock_feed_is_a_pure_deduction_never_a_credit,
        test_mock_feed_hands_the_fields_back_when_the_authoritative_feed_returns,
        test_mock_feed_is_released_by_economy_mock_mode_off,
        test_bridge_does_not_touch_a_state_it_never_bought_on,
        test_second_mock_purchase_accumulates,
        test_unchanged_paths_are_bit_identical,
        test_offer_matrix_is_untouched,
        test_hot_path_cost_of_update,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 68)
    if FAILURES:
        print(f"经济链路准入/安全：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"经济链路准入/安全：全部通过（{PASSED} 项）")
    print("同一帧只出一次；超时是「未知」不是「拒绝」；拒绝原因不压平；mock 裁判源不再被闩锁掐死。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
