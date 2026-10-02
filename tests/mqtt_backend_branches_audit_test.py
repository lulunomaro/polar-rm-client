"""`mqtt_backend` 的连接 / 收发 / 关停路径：那些**一条套件都没走到过**的分支。

跑法：``python tests/mqtt_backend_branches_audit_test.py``

为什么单独写这个文件
--------------------
`mqtt_backend.py` 是全仓库**唯一**能把指令真正送上 MQTT 的地方，
但逐行覆盖探针跑完全部 71 个套件之后，下面这些行**一次都没有执行过**：

    start()      276-305   建 paho 客户端 / will / connect_async / 退避参数
    _on_connect  372-388   连上那一刻：订阅 #、发 ClientStatus、跑 connect hook
    _on_disconnect 390-395 断线：reconnect_count 记账
    _on_message  397-447   **收包路径**：自回声与裁判流量分账、GameStatus 同步

也就是说：**"回声被当成裁判流量"这条已知会造成 58MiB/h 日志污染的判据，
在写本文件之前没有任何一条断言钉着它。**

本文件全部走 mock：不连 broker，不开 socket。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rm_client.backend.mqtt_backend import RmMqttBackend, _HOOK_DISPATCH  # noqa: E402
from rm_client.backend import mqtt_backend as mb  # noqa: E402
from rm_client.backend.protocol import (  # noqa: E402
    UPLINK_MESSAGES,
    encode_keyboard_mouse_control,
    encode_uint,
)
from rm_client.backend.safety import ControlState, OfficialSafety  # noqa: E402

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


# ======================================================================
# 测试替身：假 paho / 假 client / 假 message
# ======================================================================


class FakeInfo:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.waited: list = []


_LAST_WAIT: list = []


class WaiterInfo(FakeInfo):
    """带 wait_for_publish 的 paho 返回值 —— QoS1 离进程前要给一个短机会。"""

    def wait_for_publish(self, timeout=None):
        _LAST_WAIT.append(timeout)
        return True


class FakeClient:
    """只实现 mqtt_backend 真正用到的那几个方法。"""

    def __init__(self, rc: int = 0, publish_exc: Exception | None = None,
                 wait_exc: bool = False, info_cls=None) -> None:
        self.rc = rc
        self._publish_exc = publish_exc
        self._wait_exc = wait_exc
        self._info_cls = info_cls or FakeInfo
        self.published: list[tuple] = []
        self.subscribed: list[tuple] = []
        self.disconnect_calls = 0
        self.loop_stop_calls = 0
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def publish(self, topic, payload=None, qos=0, retain=False):
        if self._publish_exc is not None:
            raise self._publish_exc
        self.published.append((topic, payload, qos, retain))
        return self._info_cls(self.rc)

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def disconnect(self):
        self.disconnect_calls += 1
        if self._wait_exc:
            raise RuntimeError("disconnect boom")

    def loop_stop(self, force=False):
        self.loop_stop_calls += 1
        if self._wait_exc:
            raise RuntimeError("loop_stop boom")


def install_fake_paho(*, rc=0, publish_exc=None, version2=True, will_exc=None,
                      ctor_exc=None, wait_exc=False):
    """把假的 paho.mqtt.client 装进 sys.modules，返回 (记录字典, 还原函数)。"""
    saved = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.client")}
    made: dict = {}

    class CallbackAPIVersion:
        VERSION2 = "VERSION2"

    def _build(version2_ok: bool):
        class Client:
            def __init__(self, *a, **kw):
                made["args"] = a
                made["kwargs"] = kw
                if ctor_exc is not None:
                    raise ctor_exc
                self.rc = rc
                self.published = []
                self.subscribed = []
                self.disconnect_calls = 0
                self.loop_stop_calls = 0
                self.on_connect = self.on_disconnect = self.on_message = None

            def reconnect_delay_set(self, min_delay=None, max_delay=None):
                made["reconnect_delay"] = (min_delay, max_delay)

            def will_set(self, *a, **kw):
                made["will"] = (a, kw)
                if will_exc is not None:
                    raise will_exc

            def connect_async(self, host, port, keepalive=None):
                made["connect_async"] = (host, port, keepalive)

            def loop_start(self):
                made["loop_start"] = made.get("loop_start", 0) + 1

            def disconnect(self):
                self.disconnect_calls += 1
                if wait_exc:
                    raise RuntimeError("disconnect boom")

            def loop_stop(self, force=False):
                self.loop_stop_calls += 1
                if wait_exc:
                    raise RuntimeError("loop_stop boom")

            def publish(self, topic, payload=None, qos=0, retain=False):
                if publish_exc is not None:
                    raise publish_exc
                self.published.append((topic, payload, qos, retain))
                return FakeInfo(self.rc)

            def subscribe(self, topic, qos=0):
                self.subscribed.append((topic, qos))

        return Client if version2_ok else None

    mod = types.ModuleType("paho.mqtt.client")
    mod.CallbackAPIVersion = CallbackAPIVersion
    mod.MQTT_ERR_SUCCESS = 0
    good = _build(True)

    def Client(*a, **kw):                       # noqa: N802 - 冒充 paho 的名字
        # 老版 paho 的 Client() 不接受 CallbackAPIVersion 位置参数
        if a and not version2:
            raise TypeError("CallbackAPIVersion is not a valid API version")
        return good(*a, **kw)

    mod.Client = Client
    sys.modules["paho"] = types.ModuleType("paho")
    sys.modules["paho.mqtt"] = types.ModuleType("paho.mqtt")
    sys.modules["paho.mqtt.client"] = mod

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    return made, restore


def make(events=None, **kw) -> RmMqttBackend:
    b = RmMqttBackend(**kw)
    if events is not None:
        b.event_hook = lambda name, **pl: events.append((name, pl))
    return b


def drain(b: RmMqttBackend) -> list:
    b.flush_events(timeout=3.0)
    return []


def feed(b: RmMqttBackend, topic: str, payload: bytes) -> None:
    b._on_message(FakeClient(), None, types.SimpleNamespace(topic=topic, payload=payload))
    b.flush_events(timeout=3.0)


def real_backend(connected: bool = True, rc: int = 0, publish_exc=None):
    """一个"真的想上网"的后端：mock_output=False + 安全门已开 + 有 client。"""
    b = RmMqttBackend(mock_output=False)
    safety = OfficialSafety()
    safety.configure(mock_writes=False, allow_mqtt_publish=True, enforce_official_limits=False)
    safety.control_state = ControlState.MATCH_ARMED
    safety._authorized_capabilities = frozenset({"core_kmc", "economy"})
    b.safety = safety
    b.client = FakeClient(rc=rc, publish_exc=publish_exc)
    b.connected = connected
    return b


def armed_safety() -> OfficialSafety:
    """一份"门全开"的 safety —— 用来看注入点本身有没有被绕过。"""
    s = OfficialSafety()
    s.configure(mock_writes=False, allow_mqtt_publish=True, enforce_official_limits=False)
    s.control_state = ControlState.MATCH_ARMED
    s._authorized_capabilities = frozenset({"core_kmc", "economy"})
    return s


# ======================================================================
# 1. start()
# ======================================================================


def test_start_wires_the_client_exactly_once() -> None:
    print("\n[1] start(): 建客户端、装 will、连、起循环")
    made, restore = install_fake_paho()
    try:
        b = make(host="10.0.0.9", port=1883, client_id="3")
        b.start()
        check("start 之后 _started=True", b._started is True, f"{b._started}")
        check("start 不抛", b.last_error == "", b.last_error)
        check("用了 CallbackAPIVersion.VERSION2",
              made["args"][0] is not None, made["args"])
        check("client_id 透传", made["kwargs"].get("client_id") == "3", made["kwargs"])
        check("clean_session=True", made["kwargs"].get("clean_session") is True, made["kwargs"])
        check("reconnect_delay_set(1, 30)（退避上限）",
              made.get("reconnect_delay") == (1, 30), made.get("reconnect_delay"))
        check("will 是 ClientStatus / offline / qos1 / retain",
              made.get("will", (None, None))[0][0] == "ClientStatus"
              and made["will"][0][1] == b'{"online":false}'
              and made["will"][1] == {"qos": 1, "retain": True}, made.get("will"))
        check("connect_async 用的是当前 host/port/keepalive30",
              made.get("connect_async") == ("10.0.0.9", 1883, 30), made.get("connect_async"))
        check("loop_start 调了一次", made.get("loop_start") == 1, made.get("loop_start"))
        check("三个回调都挂上了",
              b.client.on_connect == b._on_connect
              and b.client.on_disconnect == b._on_disconnect
              and b.client.on_message == b._on_message, "callbacks")
    finally:
        restore()


def test_start_is_idempotent() -> None:
    """第二次 start() 必须直接返回：否则场景切换会**再开一条网络线程**。"""
    print("\n[2] start() 幂等：已 started 就直接返回")
    made, restore = install_fake_paho()
    try:
        b = make()
        b.start()
        first = b.client
        b.start()
        check("第二次 start() 没有换 client", b.client is first, "client 被换掉了")
        check("loop_start 仍然只调了一次", made.get("loop_start") == 1, made.get("loop_start"))
        check("第二次之后仍 connected 状态不变", b.connected is False, f"{b.connected}")
    finally:
        restore()


def test_start_empty_client_id_becomes_none() -> None:
    print("\n[3] client_id 为空串 -> 传给 paho 的是 None（让 broker 分配）")
    made, restore = install_fake_paho()
    try:
        b = make(client_id="")
        b.start()
        check("空 client_id -> None", made["kwargs"].get("client_id") is None, made["kwargs"])
    finally:
        restore()


def test_start_survives_legacy_paho_and_broken_will() -> None:
    print("\n[4] 老版 paho / will_set 抛异常：都要能起来")
    made, restore = install_fake_paho(version2=False)
    try:
        b = make()
        b.start()
        check("VERSION2 不可用时退回老式构造", b._started is True, f"{b._started} {b.last_error}")
        check("老式构造也带上 client_id", made["kwargs"].get("client_id") == "3", made["kwargs"])
        check("老式构造不传 clean_session",
              "clean_session" not in made["kwargs"], made["kwargs"])
    finally:
        restore()

    made, restore = install_fake_paho(version2=False)
    try:
        b = make(client_id="")
        b.start()
        check("老式 paho + 空 client_id -> 同样退成 None",
              made["kwargs"].get("client_id") is None, made["kwargs"])
    finally:
        restore()

    made, restore = install_fake_paho(will_exc=RuntimeError("will boom"))
    try:
        b = make()
        b.start()
        check("will_set 抛异常不影响启动", b._started is True, f"{b._started} {b.last_error}")
        check("will 抛异常后仍然连上了",
              made.get("connect_async") is not None, made.get("connect_async"))
    finally:
        restore()


def test_start_without_paho_records_the_error() -> None:
    print("\n[5] paho 根本装不上：记 last_error，不抛，且不置 _started")
    saved = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.client")}
    sys.modules["paho"] = None            # 让 import 直接 ImportError
    sys.modules.pop("paho.mqtt", None)
    sys.modules.pop("paho.mqtt.client", None)
    try:
        b = make()
        b.start()
        check("start 没抛", True, "")
        check("_started 仍是 False（不会假装连上）", b._started is False, f"{b._started}")
        check("connected 仍是 False", b.connected is False, f"{b.connected}")
        check("last_error 记下了原因", b.last_error != "", repr(b.last_error))
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ======================================================================
# 2. _on_connect
# ======================================================================


def test_on_connect_success_wires_everything() -> None:
    print("\n[6] _on_connect(rc=0)：订阅 #、发 ClientStatus online、跑 connect hook")
    events: list = []
    b = make(events, host="10.0.0.9", port=1883)
    hooked: list = []
    b._on_connect_hook = lambda: hooked.append(1)
    c = FakeClient()
    b._on_connect(c, None, {}, 0)
    b.flush_events(timeout=3.0)
    check("connected=True", b.connected is True, f"{b.connected}")
    check("connected_at 有值", b.connected_at > 0.0, f"{b.connected_at}")
    check("disconnected_at 清零", b.disconnected_at == 0.0, f"{b.disconnected_at}")
    check("订阅了 # / qos1", c.subscribed == [("#", 1)], c.subscribed)
    check("发了 ClientStatus online（retain）",
          c.published == [("ClientStatus", b'{"online":true}', 1, True)], c.published)
    check("connect hook 跑了一次", hooked == [1], hooked)
    check("落了 mqtt_connect 事件", ("mqtt_connect", {"host": "10.0.0.9", "port": 1883}) in events,
          events)


def test_on_connect_accepts_the_legacy_string_reason_code() -> None:
    print("\n[7] _on_connect('Success')：paho v1 传字符串也算连上")
    b = make([])
    c = FakeClient()
    b._on_connect(c, None, {}, "Success")
    b.flush_events(timeout=3.0)
    check("'Success' -> connected=True", b.connected is True, f"{b.connected}")
    check("'Success' 也订阅了", c.subscribed == [("#", 1)], c.subscribed)

    b2 = make([])
    c2 = FakeClient()
    b2._on_connect(c2, None, {}, "NoConnection")
    b2.flush_events(timeout=3.0)
    check("非 'Success' 字符串 -> connected=False", b2.connected is False, f"{b2.connected}")
    check("没连上就不订阅", c2.subscribed == [], c2.subscribed)


def test_on_connect_failure_does_nothing() -> None:
    print("\n[8] _on_connect(rc=非0)：不订阅、不发状态、不跑 hook、不落 connect 事件")
    events: list = []
    b = make(events)
    hooked: list = []
    b._on_connect_hook = lambda: hooked.append(1)
    c = FakeClient()
    b._on_connect(c, None, {}, 5)
    b.flush_events(timeout=3.0)
    check("connected=False", b.connected is False, f"{b.connected}")
    check("没有 publish", c.published == [], c.published)
    check("没有 subscribe", c.subscribed == [], c.subscribed)
    check("connect hook 没跑", hooked == [], hooked)
    check("没有 mqtt_connect 事件", [n for n, _ in events] == [], events)
    check("connected_at 没被刷新", b.connected_at == 0.0, f"{b.connected_at}")


def test_on_connect_tolerates_publish_and_hook_failures() -> None:
    print("\n[9] _on_connect 里 publish / connect hook 抛异常：连上这件事照样成立")
    b = make([])
    b._on_connect(FakeClient(publish_exc=RuntimeError("publish boom")), None, {}, 0)
    b.flush_events(timeout=3.0)
    check("publish 抛异常不影响 connected", b.connected is True, f"{b.connected}")

    events: list = []
    b2 = make(events)
    b2._on_connect_hook = lambda: (_ for _ in ()).throw(RuntimeError("hook boom"))
    b2._on_connect(FakeClient(), None, {}, 0)
    b2.flush_events(timeout=3.0)
    check("hook 抛异常不影响 connected", b2.connected is True, f"{b2.connected}")
    check("hook 抛异常仍落了 mqtt_connect",
          ("mqtt_connect", {"host": "127.0.0.1", "port": 3333}) in events, events)


def test_on_connect_without_hook() -> None:
    print("\n[10] 没设 _on_connect_hook 也不许炸")
    b = make([])
    c = FakeClient()
    b._on_connect(c, None, {}, 0)
    b.flush_events(timeout=3.0)
    check("connected=True", b.connected is True, f"{b.connected}")
    check("照常订阅", c.subscribed == [("#", 1)], c.subscribed)


# ======================================================================
# 3. _on_disconnect —— reconnect_count 只在"真的连上过"时加
# ======================================================================


def test_reconnect_count_only_counts_real_disconnects() -> None:
    print("\n[11] reconnect_count：只有连上过之后的断开才计数")
    events: list = []
    b = make(events)
    b.connected = False
    b._on_disconnect(None, None, None, 7)
    b.flush_events(timeout=3.0)
    check("从没连上过就断开 -> reconnect_count 仍是 0", b.reconnect_count == 0,
          f"{b.reconnect_count}")
    check("connected 变 False", b.connected is False, f"{b.connected}")
    check("disconnected_at 有值", b.disconnected_at > 0.0, f"{b.disconnected_at}")
    check("落了 mqtt_disconnect 且带 reason",
          ("mqtt_disconnect", {"reason": "7"}) in events, events)

    b.connected = True
    b._on_disconnect(None, None, None, 0)
    b.flush_events(timeout=3.0)
    check("连着的时候断开 -> reconnect_count 加一", b.reconnect_count == 1,
          f"{b.reconnect_count}")
    b.connected = True
    b._on_disconnect(None, None, None, 0)
    b.flush_events(timeout=3.0)
    check("再断一次 -> 2", b.reconnect_count == 2, f"{b.reconnect_count}")
    b.connected = False
    b._on_disconnect(None, None, None, 0)
    b.flush_events(timeout=3.0)
    check("本来就没连着的那次断开不再加（还是 2）", b.reconnect_count == 2,
          f"{b.reconnect_count}")
    b.connected = True
    b._on_disconnect(None, None, None, 0)
    b.flush_events(timeout=3.0)
    check("连着的时候再断 -> 3", b.reconnect_count == 3, f"{b.reconnect_count}")


# ======================================================================
# 4. _on_message —— 自回声 vs 裁判流量分账
# ======================================================================


def test_uplink_echo_is_not_counted_as_referee() -> None:
    """**这一条是本文件的核心**：75Hz 的 KMC 会被 broker 原样送回来。

    记成 referee_rx 的现场表现：RotatingJsonlLogger 100MiB 总量约每 20 分钟
    轮转一次，裁判失败记录被自己的回环挤掉；排查的人看到 `referee_rx`
    满屏，会认定"裁判在发键盘帧"，而现场裁判一条都没发。
    """
    print("\n[12] 自回声：上行 topic 只进 self_echo_*，不进 referee_*")
    events: list = []
    b = make(events)
    payload = encode_keyboard_mouse_control(keyboard_value=0x41)
    feed(b, "KeyboardMouseControl", payload)
    check("self_echo_messages 加一", b.self_echo_messages == 1, f"{b.self_echo_messages}")
    check("self_echo_bytes 记了字节数", b.self_echo_bytes == len(payload),
          f"{b.self_echo_bytes}")
    check("referee_messages **没有**加", b.referee_messages == 0, f"{b.referee_messages}")
    check("referee_bytes **没有**加", b.referee_bytes == 0, f"{b.referee_bytes}")
    check("没有落 referee_rx 事件", [n for n, _ in events] == [], events)
    check("回声不进解码队列", b.drain_decoded() == [], b._decoded)
    check("messages_received 只加一次（基类记一次）", b.messages_received == 1,
          f"{b.messages_received}")
    check("bytes_received 加了整条", b.bytes_received == len(payload), f"{b.bytes_received}")
    check("last_message_at 有值", b.last_message_at > 0.0, f"{b.last_message_at}")


def test_every_uplink_topic_is_classified_as_echo() -> None:
    print("\n[13] UPLINK_MESSAGES 里每一条都算回环（不止 KMC）")
    b = make([])
    for name in sorted(UPLINK_MESSAGES):
        feed(b, name, b"\x01\x02")
    check("每条都进 self_echo", b.self_echo_messages == len(UPLINK_MESSAGES),
          f"{b.self_echo_messages} vs {len(UPLINK_MESSAGES)}")
    check("referee 一条都没有", b.referee_messages == 0, f"{b.referee_messages}")
    check("11 条控制类 topic 全部覆盖", len(UPLINK_MESSAGES) == 11, len(UPLINK_MESSAGES))


def test_downlink_is_counted_as_referee_and_decoded() -> None:
    print("\n[14] 下行 topic：进 referee_* + 落 referee_rx + 进解码队列")
    events: list = []
    b = make(events)
    payload = encode_uint(5, 4)                 # GameStatus.current_stage = 4
    feed(b, "GameStatus", payload)
    check("referee_messages 加一", b.referee_messages == 1, f"{b.referee_messages}")
    check("referee_bytes 记了字节数", b.referee_bytes == len(payload), f"{b.referee_bytes}")
    check("self_echo 没动", b.self_echo_messages == 0, f"{b.self_echo_messages}")
    check("落了 referee_rx 且带 topic/payload",
          ("referee_rx", {"topic": "GameStatus", "payload": payload}) in events, events)
    decoded = b.drain_decoded()
    check("进了解码队列且名字是 GameStatus",
          [m[1] for m in decoded] == ["GameStatus"], decoded)
    check("safety 收到了比赛阶段", b.safety.current_stage == "4", b.safety.current_stage)
    check("因此判定为交战阶段", b.safety.is_fighting() is True, b.safety.stats())


def test_game_status_undecodable_is_dropped_cleanly() -> None:
    print("\n[15] GameStatus 载荷解不开：计入裁判流量，但不进解码、不改阶段")
    events: list = []
    b = make(events)
    feed(b, "GameStatus", b"")
    check("仍算裁判消息", b.referee_messages == 1, f"{b.referee_messages}")
    check("解码队列为空", b.drain_decoded() == [], b._decoded)
    check("比赛阶段没被动过", b.safety.current_stage == "0", b.safety.current_stage)
    check("仍然落了 referee_rx（证据链要有）",
          any(n == "referee_rx" for n, _ in events), events)


def test_undecodable_and_unknown_topics() -> None:
    print("\n[16] 非 GameStatus 的下行解不开 / 未知 topic")
    events: list = []
    b = make(events)
    feed(b, "Event", b"")
    check("Event 空载荷：算裁判、不进解码", b.referee_messages == 1 and b.drain_decoded() == [],
          f"{b.referee_messages} {list(b._decoded)}")

    events2: list = []
    b2 = make(events2)
    feed(b2, "some/unknown/topic", b"\x01\x02")
    check("未知 topic：算裁判流量（证据链留痕）", b2.referee_messages == 1,
          f"{b2.referee_messages}")
    check("未知 topic：不解码", b2.drain_decoded() == [], list(b2._decoded))
    check("未知 topic：字节数照记", b2.bytes_received == 2, f"{b2.bytes_received}")
    check("未知 topic：落了 referee_rx",
          ("referee_rx", {"topic": "some/unknown/topic", "payload": b"\x01\x02"}) in events2,
          events2)


def test_generic_decode_path_for_non_game_status() -> None:
    """第 443-447 行那条通用解码路径：能解开就必须进解码队列。

    第 435 行的 GameStatus 特判是"提前 return"，所以**非 GameStatus
    的下行消息只能靠这一段**落到解码队列上；一旦它被废掉，
    RobotDynamicStatus / Buff / Event 全部收不到，HUD 的对应字段就一直是 0。
    """
    print("\n[17] 非 GameStatus 的下行消息走通用解码路径")
    events: list = []
    b = make(events)
    payload = encode_uint(1, 42)                 # Event.type = 42
    feed(b, "Event", payload)
    decoded = b.drain_decoded()
    check("能解开就必须进解码队列", [m[1] for m in decoded] == ["Event"], decoded)
    check("解码值带得回来", decoded and decoded[0][2].get("event_id") == 42,
          decoded[0][2] if decoded else None)
    check("topic 也带得回来", decoded and decoded[0][0] == "Event",
          decoded[0][0] if decoded else None)
    check("仍算裁判流量", b.referee_messages == 1, f"{b.referee_messages}")
    check("仍落了 referee_rx", any(n == "referee_rx" for n, _ in events), events)
    check("**只有** GameStatus 能推动 safety 的阶段判定（_stage_seen 仍为 False）",
          b.safety._stage_seen is False and b.safety.current_stage == "0",
          (b.safety._stage_seen, b.safety.current_stage))

    b2 = make([])
    feed(b2, "Buff", encode_uint(1, 7))
    check("第二条也照常解码", [m[1] for m in b2.drain_decoded()] == ["Buff"], "没解码")
    check("第二条也没碰阶段", b2.safety._stage_seen is False, b2.safety._stage_seen)


def test_received_messages_buffer_is_filled_and_drainable() -> None:
    print("\n[17] 基类的 _received_messages 缓存（UI 主线程消费的那一份）")
    b = make([])
    feed(b, "GameStatus", encode_uint(5, 4))
    buffered = b.drain_received_messages()
    check("收包进缓存", len(buffered) == 1, buffered)
    check("缓存里带 topic", buffered[0][0] == "GameStatus", buffered)
    check("drain 之后清空", b.drain_received_messages() == [], "还有残留")


# ======================================================================
# 5. _deliver_event / 事件队列溢出记账
# ======================================================================


def test_hook_exception_does_not_kill_the_dispatcher() -> None:
    """hook 是 field_log.emit（同步落盘）。它抛异常时**分发线程不能死**。

    线程一死，从这往后所有事件（referee_rx / publish_result / mqtt_disconnect）
    都只进内存不出日志 —— 现场表现是"日志在比赛中途静静停住，没有任何报错"。
    """
    print("\n[18] hook 抛异常：_deliver_event 报 False，但不把它带崩")
    seen: list = []

    def hook(name, **pl):
        seen.append(name)
        if name == "boom":
            raise RuntimeError("disk boom")

    b = RmMqttBackend()
    b.event_hook = hook
    check("hook 抛异常 -> False（这一条没送到）", b._deliver_event("boom", {}) is False, "True")
    check("hook 正常 -> True", b._deliver_event("fine", {}) is True, "False")
    check("两次都调到了 hook", seen == ["boom", "fine"], seen)

    b2 = RmMqttBackend()
    b2.event_hook = None
    check("没有 hook 时也返回 True（不报错）", b2._deliver_event("x", {}) is True, "False")
    b2.dropped_events = 4
    b2._reported_drops = 0
    try:
        b2._deliver_event("x", {})
        raised = ""
    except Exception as exc:  # noqa: BLE001
        raised = repr(exc)
    check("没有 hook 且有溢出时也不崩（补发那段必须先问 hook 在不在）",
          raised == "", raised)
    check("没有 hook 时 _reported_drops 仍然推进", b2._reported_drops == 4,
          f"{b2._reported_drops}")


def test_dropped_events_are_reported_exactly_once() -> None:
    print("\n[19] 溢出记账：补发一条 mqtt_event_dropped，且只补一次")
    seen: list = []
    b = RmMqttBackend()
    b.event_hook = lambda name, **pl: seen.append((name, pl))
    b.dropped_events = 3
    b._reported_drops = 0
    b._deliver_event("publish_result", {"ok": True})
    check("补发 mqtt_event_dropped",
          ("mqtt_event_dropped",
           {"dropped": 3, "reason": "hook_queue_overflow", "total_dropped": 3}) in seen, seen)
    n = len(seen)
    b._deliver_event("publish_result", {"ok": True})
    b._deliver_event("publish_result", {"ok": True})
    check("同一次溢出不会反复补发",
          len([s for s in seen if s[0] == "mqtt_event_dropped"]) == 1, seen[n:])

    seen.clear()
    b.dropped_events = 5
    b._reported_drops = 0
    b._deliver_event("x", {})
    check("新一轮溢出会再补一条",
          any(s[0] == "mqtt_event_dropped" and s[1]["dropped"] == 5 for s in seen), seen)


def test_drop_report_survives_a_raising_hook() -> None:
    print("\n[20] 补发那条自己也抛异常：不能把 _deliver_event 带崩")

    def hook(name, **pl):
        raise RuntimeError("disk boom")

    b = RmMqttBackend()
    b.event_hook = hook
    b.dropped_events = 2
    b._reported_drops = 0
    check("补发失败也正常返回 False", b._deliver_event("x", {}) is False, "True")
    check("记账已经推进（下一次不会重发）", b._reported_drops == 2, f"{b._reported_drops}")


def test_emit_event_without_hook_is_a_noop() -> None:
    print("\n[21] 没有 event_hook：_emit_event 直接返回，不排队")
    b = RmMqttBackend()
    b.event_hook = None
    b._emit_event("referee_rx", topic="x")
    check("队列保持空", b.events_pending() == 0, b.events_pending())


def test_flush_hook_dispatch_survives_a_raising_sink() -> None:
    """退出前的收尾：一条 sink 抛异常不能带倒后面的 sink。"""
    print("\n[22] atexit 收尾 _flush_hook_dispatch 容忍抛异常的 sink")

    class Boom:
        _event_queue = None

        def flush_events(self, timeout=0.5):
            raise RuntimeError("flush boom")

    class Good:
        _event_queue = None

        def __init__(self):
            self.flushed = 0

        def flush_events(self, timeout=0.5):
            self.flushed += 1

    # 注意：必须持有引用 —— dispatcher 存的是 weakref，不持有就当场被剪掉了
    boom = Boom()
    good = Good()
    _HOOK_DISPATCH.register(boom)
    _HOOK_DISPATCH.register(good)
    live = _HOOK_DISPATCH.all_sinks()
    check("两个 sink 都在列表里（引用被持有）",
          boom in live and good in live, [type(x).__name__ for x in live])
    try:
        mb._flush_hook_dispatch()
        raised = ""
    except Exception as exc:  # noqa: BLE001
        raised = repr(exc)
    check("收尾整体不抛", raised == "", raised)
    check("抛异常的 sink 后面那个照样被 flush", good.flushed >= 1, f"{good.flushed}")


# ======================================================================
# 6. 发布：rc 判定 / 异常 / 未连接
# ======================================================================


def test_publish_reports_paho_return_code() -> None:
    print("\n[23] publish_topic：paho 返回非 0 rc 时必须当成没发出去")
    events: list = []
    b = real_backend(rc=4)
    b.event_hook = lambda n, **pl: events.append((n, pl))
    ok, reason = b.publish_topic("KeyboardMouseControl", b"\x01\x02")
    b.flush_events(timeout=3.0)
    check("rc=4 -> False", ok is False, f"ok={ok}")
    check("rc 出现在 reason 里", "rc=4" in reason, reason)
    check("last_error 写了 rc", "rc=4" in b.last_error, b.last_error)
    check("messages_sent **没有**加（这一帧没上网）", b.messages_sent == 0,
          f"{b.messages_sent}")
    check("落了 publish_result ok=False",
          ("publish_result", {"topic": "KeyboardMouseControl", "ok": False,
                              "reason": b.last_error, "intent": "control"}) in events, events)


def test_publish_exception_is_contained() -> None:
    """网络栈抛异常必须被接住 —— 否则它会冲进 16ms 的控制拍。"""
    print("\n[24] client.publish 抛异常：转成 (False, 原因) 而不是往上抛")
    events: list = []
    b = real_backend(publish_exc=RuntimeError("net down"))
    b.event_hook = lambda n, **pl: events.append((n, pl))
    try:
        ok, reason = b.publish_topic("KeyboardMouseControl", b"\x01\x02")
        raised = ""
    except Exception as exc:  # noqa: BLE001
        ok, reason, raised = None, None, repr(exc)
    b.flush_events(timeout=3.0)
    check("没有往上抛", raised == "", raised)
    check("返回 False", ok is False, f"{ok}")
    check("reason 是异常文本", "net down" in reason, reason)
    check("last_error 记下了", "net down" in b.last_error, b.last_error)
    check("messages_sent 没加", b.messages_sent == 0, f"{b.messages_sent}")
    check("落了 publish_result ok=False",
          any(n == "publish_result" and pl.get("ok") is False for n, pl in events), events)


def test_publish_when_not_connected() -> None:
    print("\n[25] 没连上：明确告诉操作员 3333 是选手端在监听")
    events: list = []
    b = real_backend(connected=False)
    b.event_hook = lambda n, **pl: events.append((n, pl))
    ok, reason = b.publish_topic("KeyboardMouseControl", b"\x01")
    b.flush_events(timeout=3.0)
    check("返回 False", ok is False, f"{ok}")
    check("reason 点名选手端", "选手端" in reason, reason)
    check("reason 点名端口 3333", "3333" in reason, reason)
    check("messages_sent 没加", b.messages_sent == 0, f"{b.messages_sent}")
    check("落了 publish_result ok=False",
          any(n == "publish_result" and pl.get("ok") is False for n, pl in events), events)

    b2 = real_backend(connected=False)
    b2.client = None
    check("client 为 None 也一样返回 False", b2.publish_topic("KeyboardMouseControl", b"\x01")[0]
          is False, "True")


def test_publish_success_marks_safety() -> None:
    print("\n[26] 发布成功：计数 + 给 safety 记时间戳")
    events: list = []
    b = real_backend(rc=0)
    b.event_hook = lambda n, **pl: events.append((n, pl))
    ok, reason = b.publish_topic("KeyboardMouseControl", b"\x01\x02", now=123.5)
    b.flush_events(timeout=3.0)
    check("返回 (True, 'ok')", (ok, reason) == (True, "ok"), f"{(ok, reason)}")
    check("messages_sent 加一", b.messages_sent == 1, f"{b.messages_sent}")
    check("safety 记下了这次发布",
          b.safety._publish_at.get("KeyboardMouseControl") == 123.5,
          b.safety._publish_at)
    check("publish_records 里有一条", len(b.publish_records) == 1, list(b.publish_records))
    check("记录标着非 mock", b.publish_records[0].mock_output is False,
          b.publish_records[0])
    check("落了 publish_result ok=True",
          ("publish_result", {"topic": "KeyboardMouseControl", "ok": True, "reason": "ok",
                              "intent": "control"}) in events, events)


# ======================================================================
# 7. publish_estop_zero：绕过限频的那条路
# ======================================================================


def test_estop_zero_paths() -> None:
    print("\n[27] publish_estop_zero：mock / 成功 / rc 异常 / 未连接 / 网络异常")
    events: list = []
    b = RmMqttBackend()                    # mock_output=True
    b.event_hook = lambda n, **pl: events.append((n, pl))
    ok, reason = b.publish_estop_zero()
    b.flush_events(timeout=3.0)
    check("mock 下返回 (True, 'mock')", (ok, reason) == (True, "mock"), f"{(ok, reason)}")
    check("mock 记录进 publish_records", len(b.publish_records) == 1, list(b.publish_records))
    zero_frame = b.publish_records[0].payload
    check("记录的是 KeyboardMouseControl 上的全零 KMC（14 字节）",
          b.publish_records[0].topic == "KeyboardMouseControl"
          and zero_frame == encode_keyboard_mouse_control(), zero_frame)
    check("每个字段值都是 0（奇数位是 protobuf 的 varint 值）",
          all(byte == 0 for byte in zero_frame[1::2]), zero_frame.hex())
    check("记录标着是 mock", b.publish_records[0].mock_output is True,
          b.publish_records[0].mock_output)
    check("intent 标成 estop_zero",
          any(n == "publish_result" and pl.get("intent") == "estop_zero"
              for n, pl in events), events)

    events2: list = []
    b2 = real_backend(rc=3)
    b2.event_hook = lambda n, **pl: events2.append((n, pl))
    ok, reason = b2.publish_estop_zero()
    b2.flush_events(timeout=3.0)
    check("rc=3 -> False", ok is False, f"ok={ok}")
    check("reason 点名急停零帧", "急停零帧" in reason, reason)
    check("rc=3 不进 publish_records", len(b2.publish_records) == 0, list(b2.publish_records))

    b3 = real_backend(publish_exc=RuntimeError("net down"))
    b3.event_hook = lambda n, **pl: []
    ok, reason = b3.publish_estop_zero()
    check("网络异常 -> False", ok is False, f"ok={ok}")
    check("网络异常也不进 publish_records", len(b3.publish_records) == 0,
          list(b3.publish_records))

    b4 = real_backend(connected=False)
    b4.event_hook = lambda n, **pl: []
    ok, reason = b4.publish_estop_zero()
    check("未连接 -> False", ok is False, f"ok={ok}")
    check("未连接时 reason 点名急停", "急停" in reason, reason)

    b5 = real_backend(rc=0)
    b5.event_hook = lambda n, **pl: []
    ok, _ = b5.publish_estop_zero()
    check("成功路径返回 True", ok is True, f"{ok}")
    check("成功路径 messages_sent 加一", b5.messages_sent == 1, f"{b5.messages_sent}")
    check("成功路径记进 publish_records", len(b5.publish_records) == 1,
          list(b5.publish_records))


# ======================================================================
# 8. publish_custom_control 的 30 字节上限
# ======================================================================


def test_custom_control_length_bound() -> None:
    """`encode_custom_control` 对 >30 字节是**抛异常**的。

    所以 `publish_custom_control` 里那道 `len(data) > 30` 闸门不是"省流量"，
    它是唯一挡住这个异常的��。闸门一松，31 字节的载荷就会在控制拍里抛
    ValueError —— 和第 12 轮 manager.py:263 那个每 16 ms 抛一次是同一类事故。
    """
    print("\n[28] publish_custom_control 的 30 字节上限")
    b = RmMqttBackend()
    b.event_hook = lambda n, **pl: []
    check("正好 30 字节放行", b.publish_custom_control(b"a" * 30) is True, "False")
    check("31 字节被拦下", b.publish_custom_control(b"a" * 31) is False, "True")
    check("last_error 点名 30 字节", "30" in b.last_error, b.last_error)
    topics = [r.topic for r in b.publish_records]
    check("只有那一条真的进了 publish_records",
          topics == ["CustomControl"], topics)
    try:
        from rm_client.backend.protocol import encode_custom_control
        encode_custom_control(b"a" * 31)
        raised = ""
    except ValueError as exc:
        raised = str(exc)
    check("对照事实：encode_custom_control 对 31 字节确实会抛", raised != "", raised)


# ======================================================================
# 9. stop() —— 换地址必须丢掉上一个 epoch 的解码结果
# ======================================================================


def test_stop_clears_the_previous_epoch() -> None:
    print("\n[29] stop()：必须丢掉上一个 broker 的解码结果与收包缓存")
    made, restore = install_fake_paho()
    try:
        b = make(host="1.2.3.4", port=1883)
        b.start()
        client = b.client
        events: list = []
        b.event_hook = lambda n, **pl: events.append((n, pl))
        feed(b, "GameStatus", encode_uint(5, 4))
        check("先收一条", len(b._decoded) == 1, len(b._decoded))
        check("先收一条也进了基类缓存", len(b._received_messages) == 1,
              len(b._received_messages))
        b.stop()
        b.flush_events(timeout=3.0)
        check("_decoded 清空", len(b._decoded) == 0, list(b._decoded))
        check("_received_messages 清空", len(b._received_messages) == 0,
              list(b._received_messages))
        check("client 置 None", b.client is None, b.client)
        check("connected 置 False", b.connected is False, b.connected)
        check("_started 置 False", b._started is False, f"{b._started}")
        check("真断了", client.disconnect_calls == 1, client.disconnect_calls)
        check("循环线程停了", client.loop_stop_calls == 1, client.loop_stop_calls)
        check("发了 ClientStatus offline",
              ("ClientStatus", b'{"online":false}', 1, True) in client.published,
              client.published)
        check("落了 mqtt_disconnect", ("mqtt_disconnect", {"reason": "stop"}) in events,
              events)
    finally:
        restore()


def test_stop_survives_a_broken_client() -> None:
    """disconnect / loop_stop 抛异常时，stop 仍要把队列和缓存收拾干净。"""
    print("\n[30] stop() 容忍 client 抛异常")
    b = RmMqttBackend()
    client = FakeClient(wait_exc=True)
    b.client = client
    b._started = True
    b._decoded.append(("t", "n", {}, 0.0))
    b._received_messages.append(("t", b"p", 0.0))
    events: list = []
    b.event_hook = lambda n, **pl: events.append((n, pl))
    try:
        b.stop()
        b.flush_events(timeout=3.0)
        raised = ""
    except Exception as exc:  # noqa: BLE001
        raised = repr(exc)
    check("stop 没抛", raised == "", raised)
    check("解码缓存照样清了", len(b._decoded) == 0, list(b._decoded))
    check("收包缓存照样清了", len(b._received_messages) == 0, list(b._received_messages))
    check("connected 置 False", b.connected is False, b.connected)
    check("仍然落了 mqtt_disconnect", ("mqtt_disconnect", {"reason": "stop"}) in events,
          events)


def test_stop_survives_a_publish_that_raises() -> None:
    """`stop()` 里那条 `{"online":false}` **发不出去**时，仍要收拾干净。

    # 为什么单列一条

    第 17 轮复核第 14 轮的变异结果时发现：`stop()` 里有**三个**字节完全一样的
    `except Exception:`，而第 14 轮那个变异注入器用
    `str.replace(raw, new, 1)` 做**文本首次出现**替换、没有按行号定位 ——
    于是这一行**从来没被真正改过**，它"已覆盖"是假的。

    复核 agent 给出���现场原因很具体：`FakeClient(wait_exc=True)` 只让
    `disconnect` / `loop_stop` 抛，**`publish` 不抛** ——
    所以全仓库没有任何一处让 `stop()` 里的 `client.publish` 抛异常，
    那一行的 handler 不可达。

    这条把它变成可达：让 `publish` 抛，断言**后面三步照走**。
    """
    print("\n[31] stop() 容忍 publish 抛异常（online:false 发不出去）")
    b = RmMqttBackend()
    client = FakeClient(publish_exc=RuntimeError("publish boom"), wait_exc=True)
    b.client = client
    b._started = True
    b._decoded.append(("t", "n", {}, 0.0))
    b._received_messages.append(("t", b"p", 0.0))
    events: list = []
    b.event_hook = lambda n, **pl: events.append((n, pl))
    try:
        b.stop()
        b.flush_events(timeout=3.0)
        raised = ""
    except Exception as exc:  # noqa: BLE001
        raised = repr(exc)
    check("publish 抛异常时 stop 也没抛", raised == "", raised)
    check("离线那条根本没发出去（所以下面那句 connected 是干净的 False）",
          client.published == [], client.published)
    check("解码缓存照样清了", len(b._decoded) == 0, list(b._decoded))
    check("收包缓存照样清了", len(b._received_messages) == 0, list(b._received_messages))
    check("connected 置 False", b.connected is False, b.connected)
    check("仍然落了 mqtt_disconnect", ("mqtt_disconnect", {"reason": "stop"}) in events,
          events)
    check("disconnect / loop_stop 仍然被调过（publish 抛异常不许把后面两步吃掉）",
          client.disconnect_calls == 1 and client.loop_stop_calls == 1,
          f"disconnect={client.disconnect_calls} loop_stop={client.loop_stop_calls}")


def test_stop_waits_for_qos1_when_available() -> None:
    """`if callable(waiter)`：只有返回对象真的有 wait_for_publish 才等它。

    现场：QoS1 的 ClientStatus offline 若不等一下就断，可能永远出不去，
    别的客户端仍以为我们在线；反过来无界等待会把关停卡死 —— 所以是
    固定 0.5 秒。
    """
    print("\n[31] stop() 只在 wait_for_publish 真的存在时等它（有界 0.5s）")
    b = RmMqttBackend()
    client = FakeClient(info_cls=WaiterInfo)
    b.client = client
    b._started = True
    b.event_hook = lambda n, **pl: None
    b.stop()
    b.flush_events(timeout=3.0)
    check("确实调用了 wait_for_publish(0.5)",
          bool(_LAST_WAIT) and _LAST_WAIT[0] == 0.5, _LAST_WAIT)

    b2 = RmMqttBackend()
    client2 = FakeClient()                       # 返回对象没有 wait_for_publish
    b2.client = client2
    b2._started = True
    b2.event_hook = lambda n, **pl: None
    try:
        b2.stop()
        b2.flush_events(timeout=3.0)
        raised = ""
    except Exception as exc:  # noqa: BLE001
        raised = repr(exc)
    check("没有 wait_for_publish 时不炸", raised == "", raised)
    check("offline 帧照样发了",
          client2.published == [("ClientStatus", b'{"online":false}', 1, True)],
          client2.published)


def test_stop_without_client() -> None:
    print("\n[32] 从没 start 过就 stop()：不炸")
    b = RmMqttBackend()
    events: list = []
    b.event_hook = lambda n, **pl: events.append((n, pl))
    try:
        b.stop()
        b.flush_events(timeout=3.0)
        raised = ""
    except Exception as exc:  # noqa: BLE001
        raised = repr(exc)
    check("stop 没抛", raised == "", raised)
    check("仍然落了 mqtt_disconnect（证据链要有收尾）",
          ("mqtt_disconnect", {"reason": "stop"}) in events, events)


def test_stop_without_publishing_status() -> None:
    print("\n[32] stop(publish_status=False)：换地址时不发 offline")
    made, restore = install_fake_paho()
    try:
        b = make()
        b.start()
        client = b.client
        b.stop(publish_status=False)
        b.flush_events(timeout=3.0)
        check("没有发 ClientStatus",
              not any(t == "ClientStatus" for t, *_ in client.published), client.published)
        check("还是断了", client.disconnect_calls == 1, client.disconnect_calls)
    finally:
        restore()


def test_set_address_reconnects_when_started() -> None:
    print("\n[33] set_address：已连着时改地址要真的断线重连")
    made, restore = install_fake_paho()
    try:
        b = make(host="1.1.1.1", port=1883)
        b.start()
        first_client = b.client
        check("同地址 -> False（不重连）", b.set_address("1.1.1.1", 1883) is False, "True")
        check("空白 host 视为同一个", b.set_address("  1.1.1.1 ", "1883") is False, "True")
        check("地址真的换了", b.set_address("2.2.2.2", 1884) is True, "False")
        check("字段已更新", (b.host, b.port) == ("2.2.2.2", 1884), (b.host, b.port))
        check("旧 client 被停了", first_client.disconnect_calls == 1,
              first_client.disconnect_calls)
        check("新 client 已建", b.client is not first_client, "还是旧 client")
        check("连的是新地址", b._started is True, f"{b._started}")
    finally:
        restore()
        b.stop()


def test_set_address_when_not_started_keeps_disconnected() -> None:
    print("\n[34] set_address：没 start 过就只改字段，不去建连接")
    made, restore = install_fake_paho()
    try:
        b = make(host="1.1.1.1", port=1883)
        check("改地址返回 True", b.set_address("3.3.3.3", 1885) is True, "False")
        check("字段更新", (b.host, b.port) == ("3.3.3.3", 1885), (b.host, b.port))
        check("没有建 client", b.client is None, b.client)
        check("没有启动循环", "loop_start" not in made, made)
        check("_started 仍是 False", b._started is False, f"{b._started}")
    finally:
        restore()


# ======================================================================
# 10. 入口收敛：所有 publish_* 归口
# ======================================================================


def test_all_publish_entrypoints_go_through_publish_topic() -> None:
    print("\n[35] 各 publish_* 入口都归口到 publish_topic（限频 + 急停 + 门控）")
    b = RmMqttBackend()
    b.event_hook = lambda n, **pl: []
    check("publish_binary", b.publish_binary("CommonCommand", b"\x01") is True, "False")
    check("publish_match_command", b.publish_match_command("CommonCommand", b"\x01") is True,
          "False")
    check("publish_keyboard_mouse", b.publish_keyboard_mouse(b"\x01") is True, "False")
    check("publish_common_command", b.publish_common_command(b"\x01") is True, "False")
    check("publish_air_support_command", b.publish_air_support_command(b"\x01") is True,
          "False")
    check("publish_named", (named := b.publish_named("CommonCommand", b"\x01")) == (True, "mock"),
          named)
    topics = [r.topic for r in b.publish_records]
    check("六条入口都留下了记录",
          topics == ["CommonCommand", "CommonCommand", "KeyboardMouseControl",
                     "CommonCommand", "AirSupportCommand", "CommonCommand"], topics)

    # 急停时这些入口必须全部闭嘴（mock 也一样）
    b2 = RmMqttBackend()
    b2.event_hook = lambda n, **pl: []
    b2.safety.trigger_estop()
    check("急停下 publish_binary 被拒", b2.publish_binary("CommonCommand", b"\x01") is False,
          "True")
    check("急停下 publish_keyboard_mouse 被拒",
          b2.publish_keyboard_mouse(b"\x01") is False, "True")
    check("急停下 publish_common_command 被拒",
          b2.publish_common_command(b"\x01") is False, "True")
    check("急停下 publish_air_support_command 被拒",
          b2.publish_air_support_command(b"\x01") is False, "True")
    check("急停下 last_error 写明", "急停" in b2.last_error, b2.last_error)
    check("急停下 publish_records 仍然空（真的一帧都没排）",
          list(b2.publish_records) == [], list(b2.publish_records))
    check("**但** publish_estop_zero 仍然放行（专用的全零帧）",
          b2.publish_estop_zero()[0] is True, b2.publish_estop_zero())


def test_stats_exposes_the_diagnostics_fields() -> None:
    print("\n[36] stats()：诊断字段必须都在（dropped_events 是证据链的自检）")
    b = RmMqttBackend(host="5.5.5.5", port=1884, client_id="9")
    s = b.stats()
    for key in ("connected", "host", "port", "client_id", "mock_output",
                "messages_received", "messages_sent", "bytes_received",
                "reconnect_count", "last_message_at", "connected_at",
                "disconnected_at", "last_error", "dropped_events",
                "events_pending", "event_queue_max"):
        check(f"stats 里有 {key}", key in s, sorted(s))
    check("host/port 正确", (s["host"], s["port"]) == ("5.5.5.5", 1884), (s["host"], s["port"]))
    check("dropped_events 初值 0", s["dropped_events"] == 0, s["dropped_events"])
    check("event_queue_max 是有界队列上限", s["event_queue_max"] >= 64, s["event_queue_max"])


def test_injected_safety_is_the_one_that_gets_used() -> None:
    """`safety or OfficialSafety()`：传进来的 safety 必须**就是**生效的那一个。

    现场表现：manager 与 UI 共用同一个 OfficialSafety（场景切换会
    `from_scenario` 重新上锁）。注入点一旦被忽略，UI 上的急停按钮按下去
    对 MQTT 出口不起作用 —— 车还在动。
    """
    print("\n[37] 注入的 safety 就是生效的那一个")
    s = armed_safety()
    b = RmMqttBackend(safety=s)
    check("构造后 safety 是传进来的那个", b.safety is s, "被换掉了")
    s.trigger_estop()
    check("外部触发的急停在后端立刻可见", b.safety.estop_active is True, "没看到")
    check("因此发布被挡住", b.publish_topic("KeyboardMouseControl", b"\x01")[0] is False,
          "True")

    s2 = armed_safety()
    b2 = RmMqttBackend(mock_output=False, safety=s2)
    b2.client = FakeClient(rc=0)
    b2.connected = True
    s2.lock_control("operator lock")
    check("外部重新上锁后发布被挡（不是走内置默认 safety）",
          b2.publish_topic("KeyboardMouseControl", b"\x01")[0] is False, "True")

    b3 = RmMqttBackend()
    check("不传 safety 时用的是新的一份 OfficialSafety",
          isinstance(b3.safety, OfficialSafety), type(b3.safety))


def test_dead_sinks_are_pruned() -> None:
    """`_live_sinks_locked` 的 dead 分支：被 GC 掉的 backend 不留在列表里。

    现场表现：一整场比赛里反复切换场景会建出很多 backend；不剪枝的话
    `_sinks` 单调增长，事件分发线程每 0.2 s 就要遍历一串死对象。
    """
    print("\n[38] 死掉的 sink 要被剪掉")
    import gc

    before = len(_HOOK_DISPATCH.all_sinks())
    doomed = RmMqttBackend()
    keep = RmMqttBackend()
    doomed_tag = "doomed-sink"
    doomed.__dict__["_audit_tag"] = doomed_tag
    check("注册后多了一个 sink", len(_HOOK_DISPATCH.all_sinks()) == before + 2,
          len(_HOOK_DISPATCH.all_sinks()))
    del doomed
    gc.collect()
    live = _HOOK_DISPATCH.all_sinks()
    tagged = [getattr(x, "_audit_tag", None) for x in live]
    check("死 sink 不再出现", doomed_tag not in tagged and keep in live, tagged)
    check("列表长度回到 1", len(live) == before + 1, (before, len(live)))
    with _HOOK_DISPATCH._cv:
        stored = _HOOK_DISPATCH._sinks
    check("内部 _sinks 列表本身也被剪短了（不是只过滤返回值）",
          all(r() is not None for r in stored)
          and len(stored) == len(_HOOK_DISPATCH.all_sinks()),
          (len(stored), len(_HOOK_DISPATCH.all_sinks())))


def test_registering_the_same_sink_twice_dedupes() -> None:
    """`if ref not in self._sinks`：重复注册必须去重。

    不去重的话每条事件会被投递两次 —— field_log 里 referee_rx / publish_result
    双份，写盘量翻倍，而"消息数"看着还挺正常，不容易一眼看出来。
    """
    print("\n[39] 同一个 backend 重复注册只登记一次")
    n0 = len(_HOOK_DISPATCH.all_sinks())
    b = RmMqttBackend()          # 构造函数已经注册过一次
    check("构造时已登记", len(_HOOK_DISPATCH.all_sinks()) == n0 + 1,
          (n0, len(_HOOK_DISPATCH.all_sinks())))
    n1 = len(_HOOK_DISPATCH.all_sinks())
    _HOOK_DISPATCH.register(b)
    _HOOK_DISPATCH.register(b)
    _HOOK_DISPATCH.register(b)
    live = _HOOK_DISPATCH.all_sinks()
    check("再注册三次，长度不变", len(live) == n1, (n1, len(live)))
    check("返回值就是自己的队列", _HOOK_DISPATCH.register(b) is b._event_queue,
          "不是同一条队列")
    seen: list = []
    b.event_hook = lambda name, **pl: seen.append(name)
    b._emit_event("referee_rx", topic="x")
    b.flush_events(timeout=3.0)
    check("事件只投递一次（不是三份）",
          [n for n in seen if n == "referee_rx"] == ["referee_rx"], seen)


def test_delivered_counter_follows_successful_drops() -> None:
    """`if sink._deliver_event(*item)`：投递成功的条数要累进 delivered。"""
    print("\n[40] delivered 计数只涨在投递成功的那些事件上")
    seen: list = []
    b = RmMqttBackend()
    b.event_hook = lambda name, **pl: seen.append(name)
    before = _HOOK_DISPATCH.delivered
    for i in range(5):
        b._emit_event("publish_result", i=i)
    b.flush_events(timeout=3.0)
    check("五条都投递到了", len([n for n in seen if n == "publish_result"]) == 5, seen)
    check("delivered 涨了 5", _HOOK_DISPATCH.delivered == before + 5,
          (before, _HOOK_DISPATCH.delivered))

    def bad(name, **pl):
        raise RuntimeError("disk boom")

    b2 = RmMqttBackend()
    b2.event_hook = bad
    before2 = _HOOK_DISPATCH.delivered
    b2._emit_event("referee_rx", topic="x")
    b2.flush_events(timeout=3.0)
    check("投递失败的那条不计入 delivered",
          _HOOK_DISPATCH.delivered == before2, (before2, _HOOK_DISPATCH.delivered))
    check("但分发线程还活着（后面的事件照样送）",
          b._dispatch_alive() is True, "分发线程死了")


def test_emit_event_sync_fallback_when_dispatcher_is_gone() -> None:
    """分发线程不在了（进程收尾）时退回**同步投递，一步都不丢**。

    这条最容易被改成"顺手记一笔丢弃"——那样关停那一刻的
    ``mqtt_disconnect`` 就只留在内存里，现场证据链永远缺最后一环。
    """
    print("\n[41] 分发线程已停：事件退回同步投递，而不是被记成丢弃")
    import threading as _th

    events: list = []
    b = make(events)
    saved_thread = _HOOK_DISPATCH._thread
    saved_closed = _HOOK_DISPATCH._closed
    _HOOK_DISPATCH._thread = _th.Thread(target=lambda: None, name="audit-dead")  # 从未 start
    _HOOK_DISPATCH._closed = True
    try:
        check("分发线程确实不在了", b._dispatch_alive() is False, "还在跑")
        b._emit_event("mqtt_disconnect", reason="stop")
        check("一步都不丢：退回同步投递",
              [n for n, _ in events] == ["mqtt_disconnect"], events)
        check("并且**不**记成丢弃", b.dropped_events == 0, b.dropped_events)
        check("last_error 里也没有队列溢出的说法",
              "队列已满" not in b.last_error, b.last_error)
    finally:
        _HOOK_DISPATCH._closed = saved_closed
        _HOOK_DISPATCH._thread = saved_thread
        if not (saved_thread is not None and saved_thread.is_alive()):
            _HOOK_DISPATCH._thread = None
            with _HOOK_DISPATCH._cv:
                _HOOK_DISPATCH._start_locked()
    check("分发线程恢复运行（没有把全局分发器留在关停态）",
          b._dispatch_alive() is True, "没恢复")


def main() -> int:
    test_start_wires_the_client_exactly_once()
    test_start_is_idempotent()
    test_start_empty_client_id_becomes_none()
    test_start_survives_legacy_paho_and_broken_will()
    test_start_without_paho_records_the_error()
    test_on_connect_success_wires_everything()
    test_on_connect_accepts_the_legacy_string_reason_code()
    test_on_connect_failure_does_nothing()
    test_on_connect_tolerates_publish_and_hook_failures()
    test_on_connect_without_hook()
    test_reconnect_count_only_counts_real_disconnects()
    test_uplink_echo_is_not_counted_as_referee()
    test_every_uplink_topic_is_classified_as_echo()
    test_downlink_is_counted_as_referee_and_decoded()
    test_game_status_undecodable_is_dropped_cleanly()
    test_undecodable_and_unknown_topics()
    test_generic_decode_path_for_non_game_status()
    test_received_messages_buffer_is_filled_and_drainable()
    test_hook_exception_does_not_kill_the_dispatcher()
    test_dropped_events_are_reported_exactly_once()
    test_drop_report_survives_a_raising_hook()
    test_emit_event_without_hook_is_a_noop()
    test_flush_hook_dispatch_survives_a_raising_sink()
    test_publish_reports_paho_return_code()
    test_publish_exception_is_contained()
    test_publish_when_not_connected()
    test_publish_success_marks_safety()
    test_estop_zero_paths()
    test_custom_control_length_bound()
    test_stop_clears_the_previous_epoch()
    test_stop_survives_a_broken_client()
    test_stop_survives_a_publish_that_raises()
    test_stop_waits_for_qos1_when_available()
    test_stop_without_client()
    test_stop_without_publishing_status()
    test_set_address_reconnects_when_started()
    test_set_address_when_not_started_keeps_disconnected()
    test_all_publish_entrypoints_go_through_publish_topic()
    test_stats_exposes_the_diagnostics_fields()
    test_injected_safety_is_the_one_that_gets_used()
    test_dead_sinks_are_pruned()
    test_registering_the_same_sink_twice_dedupes()
    test_delivered_counter_follows_successful_drops()
    test_emit_event_sync_fallback_when_dispatcher_is_gone()
    print()
    if FAILURES:
        print(f"mqtt_backend 分支：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"mqtt_backend 分支：全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())