"""MQTT 后端（对齐 SharkClient Rust mqtt_client 的能力，纯 Python 实现）。

- 真实连接/自动重连（paho reconnect_delay_set）
- 收发统计、最后消息时间、重连次数
- 订阅全量 topic（#），在回调线程解码后放入队列，UI 主线程安全消费
- 发布走 OfficialMqttBridge 边界：mock 记录 或 真实发布 + 10Hz 限频 + 安全门控
"""

from __future__ import annotations

import atexit
import threading
import time
import weakref
from collections import deque
from typing import Any, Callable

from ..transports import MqttPublishRecord, OfficialMqttBridge
from .protocol import (
    UPLINK_MESSAGES,
    decode_message,
    encode_custom_control,
    encode_keyboard_mouse_control,
    topic_message_name,
)
from .safety import OfficialSafety


class _HookDispatch:
    """进程内唯一的 event hook 分发线程。

    为什么要有它
    ------------
    ``_emit_event`` 挂在 60/75Hz 的控制拍上：``publish_result`` 在
    OfficialEvent 的 75Hz KMC 上一秒 75 次。生产环境的 hook 是
    ``manager._log_backend_event`` → ``field_log.emit``，也就是**同步落盘**。
    实测 hook 本身 140µs/次，于是 75Hz 就是 10.6 ms/s 的阻塞磁盘 I/O 正好
    打在 Qt 主线程上；referee_rx 洪泛时更糟。

    这里把"排队"和"调用 hook"拆开：调用方只付一次 ``deque.append``
    （实测 1.1µs → 1.3µs），真正的 hook 调用（落盘）由这条后台线程做。
    顺序严格保持（FIFO、单线程投递）。

    不丢事件
    --------
    每个 backend 一个**有界**队列（``queue_max`` 条，默认 8192 ≈ 75Hz 下
    109 秒的积压）。队列满时事件被拒收，
    并**显式**记账：``dropped_events`` 加一、``last_error`` 当场写明、下一次
    成功投递之后补发一条 ``mqtt_event_dropped`` 记录，让证据链自己标出那段
    空洞。绝不静默丢弃，也绝不因为队列满而反过来阻塞 75Hz 控制拍 —— 宁可
    明确报错，也不能把一次日志拥塞变成一次控制拍卡顿。
    """

    def __init__(self, queue_max: int = 8192) -> None:
        self.queue_max = max(64, int(queue_max))
        self._cv = threading.Condition(threading.Lock())
        # 纯 list + weakref：WeakKeyDictionary 的 GC 回调可能在遍历中途改字典。
        self._sinks: list[weakref.ref] = []
        self._inflight = 0
        self._thread: threading.Thread | None = None
        self._closed = False
        self.delivered = 0
        self.dropped = 0

    # ------------------------------------------------------------------
    def register(self, sink: Any) -> deque:
        ref = weakref.ref(sink)
        with self._cv:
            if ref not in self._sinks:
                self._sinks.append(ref)
            self._start_locked()
        return sink._event_queue

    def _start_locked(self) -> None:
        # 第 63 轮：原来是 `self._thread is not None or self._closed`。
        #
        # 那测的是**身份**，不是**存活**。线程一旦因为任何原因结束了，
        # `self._thread` 就是一个**死的但非 None** 的 `Thread` 对象，
        # 而下一次 `register()` 看见它非 None 就**直接返回** ——
        # **这个派发线程永远不会重启**，而 `submit()` 仍然返回 True（队列填到 8192）。
        #
        # 同仓库的 `config_store._ensure_writer` 就是对的：`if ... .is_alive(): return`。
        # 两处同为的事，**这一处写错了**。
        if self._thread is not None and self._thread.is_alive():
            return
        if self._closed:
            return
        thread = threading.Thread(
            target=self._run, name="mqtt-event-hook", daemon=True
        )
        self._thread = thread
        thread.start()

    def submit(self, sink: Any, queue: deque, item: tuple[str, dict[str, Any]]) -> bool:
        """排队成功返回 True；队列满/已关闭返回 False（由调用方显式记账）。"""
        with self._cv:
            if self._closed:
                return False
            if len(queue) >= self.queue_max:
                self.dropped += 1
                return False
            if not queue:
                # 只在"空 -> 非空"沿上唤醒：持续负载下 notify 频率降到 0，
                # 否则每次 notify 都要把 GIL 让给后台线程，反而更贵。
                self._cv.notify()
            queue.append(item)
            return True

    def flush(self, sink: Any, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cv:
            while (sink._event_queue or self._inflight) and time.monotonic() < deadline:
                self._cv.wait(0.02)
            return not (sink._event_queue or self._inflight)

    # ------------------------------------------------------------------
    def _live_sinks_locked(self) -> list[Any]:
        """列出还活着的 sink。**调用方必须已持有 ``self._cv``**。

        ``self._cv`` 用的是非重入Lock，_run() 是在持锁状态下调进来的，所以
        这里绝不能自己再加锁。
        """
        live = []
        dead = False
        for ref in self._sinks:
            sink = ref()
            if sink is None:
                dead = True
            else:
                live.append(sink)
        if dead:
            self._sinks = [weakref.ref(sink) for sink in live]
        return live

    def all_sinks(self) -> list[Any]:
        with self._cv:
            return self._live_sinks_locked()

    def _run(self) -> None:
        while True:
            with self._cv:
                sinks = self._live_sinks_locked()
                while not any(sink._event_queue for sink in sinks) and not self._closed:
                    self._cv.wait(0.2)
                    sinks = self._live_sinks_locked()
                batch: list[tuple[Any, tuple[str, dict[str, Any]]]] = []
                for sink in sinks:
                    queue = sink._event_queue
                    while queue and len(batch) < 512:
                        batch.append((sink, queue.popleft()))
                if not batch and self._closed:
                    return
                self._inflight += len(batch)
            ok = 0
            for sink, item in batch:
                if sink._deliver_event(*item):
                    ok += 1
            with self._cv:
                self._inflight -= len(batch)
                self.delivered += ok
                self._cv.notify_all()

    def shutdown(self) -> None:  # pragma: no cover - 解释器退出路径
        with self._cv:
            self._closed = True
            self._cv.notify_all()


_HOOK_DISPATCH = _HookDispatch()


def _flush_hook_dispatch() -> None:  # pragma: no cover - 解释器退出路径
    """退出前把每个 backend 的队列排空。

    ``stop()`` 已经会 flush，但进程被 Ctrl+C / 强杀时不一定走到那里。
    证据链宁可多等零点几秒，也不能在最后一刻丢掉 mqtt_disconnect 这类
    最关键的收尾记录。
    """
    for sink in _HOOK_DISPATCH.all_sinks():
        try:
            sink.flush_events(timeout=0.5)
        except Exception:
            pass


atexit.register(_flush_hook_dispatch)


class RmMqttBackend(OfficialMqttBridge):
    """SharkClient 风格 MQTT 后端，兼容本地 OfficialMqttBridge 全部接口。"""

    # 第 63 轮新增：网络线程正常关闭失败的次数与最后一次原因。
    # 不再完全静默：它一旦失败，旧 Client 就会永远占着那个 client_id。
    loop_stop_errors = 0
    last_loop_stop_error = None

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3333,
        client_id: str = "3",
        mock_output: bool = True,
        safety: OfficialSafety | None = None,
    ) -> None:
        super().__init__(host=host, port=port, client_id=client_id)
        self.mock_output = bool(mock_output)
        self.safety = safety or OfficialSafety()
        self.messages_received = 0
        self.messages_sent = 0
        self.bytes_received = 0
        #: 入站消息里"裁判真发的"与"我们自己发出去又被 # 订阅送回来的"分开记。
        #: 现场排查时这两个数必须能分开看：混在一起时 referee_rx 会被自己的
        #: 75Hz 键盘帧淹没，真出问题时反而看不出"裁判一条都没发"。
        self.referee_messages = 0
        self.referee_bytes = 0
        self.self_echo_messages = 0
        self.self_echo_bytes = 0
        self.reconnect_count = 0
        self.last_message_at = 0.0
        self.connected_at = 0.0
        self._decoded: deque[tuple[str, str, dict[str, Any], float]] = deque(maxlen=512)
        self._decoded_lock = threading.Lock()
        self._started = False
        self._on_connect_hook: Callable[[], None] | None = None
        self.event_hook: Callable[[str, dict[str, Any]], None] | None = None
        self.disconnected_at = time.perf_counter()
        #: 没进 event hook（= 没进 field_log）的后端事件数。正常情况恒为 0。
        self.dropped_events = 0
        self._reported_drops = 0
        self._event_queue: deque[tuple[str, dict[str, Any]]] = deque()
        _HOOK_DISPATCH.register(self)

    # ------------------------------------------------------------------
    # event hook 分发（有序、有界、显式记账）
    # ------------------------------------------------------------------
    def _emit_event(self, event: str, **payload: Any) -> None:
        hook = self.event_hook
        if hook is None:
            return
        item = (str(event), payload)
        if _HOOK_DISPATCH.submit(self, self._event_queue, item):
            return
        # 队列满 / 分发线程已关。显式记账，绝不静默丢，也绝不阻塞控制拍。
        if self._dispatch_alive():
            self.dropped_events += 1
            self.last_error = (
                f"MQTT 事件队列已满（{_HOOK_DISPATCH.queue_max} 条），"
                f"本次 {event} 未进日志；累计 {self.dropped_events} 条"
            )
            return
        # 分发线程不在了（进程收尾）：退回同步投递，一步都不丢。
        self._deliver_event(*item)

    def _dispatch_alive(self) -> bool:
        thread = _HOOK_DISPATCH._thread
        return thread is not None and thread.is_alive()

    def _deliver_event(self, event: str, payload: dict[str, Any]) -> bool:
        hook = self.event_hook
        ok = True
        if hook is not None:
            try:
                hook(event, **payload)
            except Exception:
                ok = False
        dropped = self.dropped_events - self._reported_drops
        if dropped > 0:
            self._reported_drops = self.dropped_events
            if hook is not None:
                try:
                    hook(
                        "mqtt_event_dropped",
                        dropped=dropped,
                        reason="hook_queue_overflow",
                        total_dropped=self.dropped_events,
                    )
                except Exception:
                    pass
        return ok

    def flush_events(self, timeout: float = 2.0) -> bool:
        """等后台分发线程把队列排空（关停、测试、诊断用）。"""
        return _HOOK_DISPATCH.flush(self, timeout)

    def events_pending(self) -> int:
        return len(self._event_queue)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        try:
            import paho.mqtt.client as mqtt  # type: ignore
            self._mqtt = mqtt
            try:
                self.client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=self.client_id or None,
                    clean_session=True,
                )
            except Exception:
                self.client = mqtt.Client(client_id=self.client_id or None)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
            try:
                self.client.will_set(
                    "ClientStatus", b'{"online":false}', qos=1, retain=True
                )
            except Exception:
                pass
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
            self._started = True
        except Exception as exc:  # pragma: no cover - depends on paho availability
            self.last_error = str(exc)
            self.connected = False
            self._started = False

    # 第 63 轮新增：网络线程正常关闭失败的次数。
    # 不再完全静默：它一旦失败，旧 Client 就会永远占着那个 client_id。
    def stop(self, *, publish_status: bool = True) -> None:
        self._started = False
        client = self.client
        self.client = None
        if client is not None:
            if publish_status:
                try:
                    info = client.publish(
                        "ClientStatus", b'{"online":false}', qos=1, retain=True
                    )
                    # Give QoS1 a short bounded chance to leave the process
                    # before disconnect.  Fake/legacy clients need no special
                    # handling, and shutdown must never block indefinitely.
                    waiter = getattr(info, "wait_for_publish", None)
                    if callable(waiter):
                        waiter(timeout=0.5)
                except Exception:
                    pass
            try:
                client.disconnect()
            except Exception:
                pass
            # 第 63 轮：原来这里是 `client.loop_stop(force=True)`。
            #
            # **本仓库锁的 paho-mqtt 是 2.1.0，它的签名是 `loop_stop(self)`** ——
            # `force` 那个参数在 1.x 就有，2.x 去掉了。实测：
            #
            #     inspect.signature(paho.mqtt.client.Client.loop_stop) -> (self) -> MQTTErrorCode
            #
            # 所以每一次 `stop()` 都在这里抛 `TypeError`，然后被同下面的
            # `except Exception: pass` 吞掉。
            #
            # # 代价：网络线程**没被终止、也没被 join**
            #
            # `loop_stop` 是唯一会设 `_thread_terminate = True` 并做 `thread.join()` 的方法。
            # 不调它：
            #   * 旧 `Client` 一直被它自己的线程拿着，socket 不关，直到
            #     paho 自己意识到 `disconnect()` 才收尾（等待是 1 秒一切）；
            #   * 而 `start()` 紧接着就用**同一个 `client_id`** 开了一个新会话 ——
            #     MQTT 要求 id 唯一，broker 会把一个踢掉。踢的是**新的**，
            #     所以链路会抖到旧常驻完为止。
            #
            # 这正是第 34 轮“两个连接共一个 client_id”那个类似问题。
            try:
                client.loop_stop()
            except Exception as exc:
                # 不再完全静默（第 53—57 轮的纮端）：记一笔，让现场看得见。
                self.loop_stop_errors += 1
                self.last_loop_stop_error = f"{type(exc).__name__}: {exc}"
                self.connected = False
        self.connected = False
        self.disconnected_at = time.perf_counter()
        # 换 broker / 重连后**必须**丢掉上一个 epoch 攒下的解码结果。
        #
        # 不丢的话：切了地址之后 drain_decoded() 还能把上一个 broker 的
        # Event 之类 drain 出去（实测切 broker 后旧 broker 的 5 条 Event
        # 仍被 drain 出去）。现场表现是"换了地址却还在按上一场的状态动"，
        # 而且没有任何日志能解释 —— 因为那些数据看起来完全合法。
        with self._decoded_lock:
            self._decoded.clear()
        with self._received_lock:
            self._received_messages.clear()
        self._emit_event("mqtt_disconnect", reason="stop")
        # 关停必须把队列排空，否则最后几条（尤其是 mqtt_disconnect）就停在
        # 内存里没了。正常情况下这一步是微秒级；超时也只是一次有界等待。
        self.flush_events(timeout=0.5)

    def set_address(self, host: str, port: int) -> bool:
        """改 broker 地址。已经连着就断开重连，返回是否真的换了。

        以前 host/port 只在场景切换时被**赋值**（``manager._apply_config``），
        赋值不会重连：进程已经连着 127.0.0.1 时把字段改成 192.168.12.1，
        实际还在往老地址发 —— 检录当天这就会把"键鼠出口开着但赛事引擎收不到"
        演成一条查不出原因的故障。
        """
        host = str(host).strip()
        port = int(port)
        if (host, port) == (str(self.host), int(self.port)):
            return False
        self.host = host
        self.port = port
        if self._started:
            self.stop(publish_status=False)
            self.start()
        return True

    # ------------------------------------------------------------------
    # paho 回调
    # ------------------------------------------------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None) -> None:
        ok = int(reason_code) == 0 if not isinstance(reason_code, str) else reason_code == "Success"
        self.connected = ok
        if ok:
            self.connected_at = time.time()
            self.disconnected_at = 0.0
            self._emit_event("mqtt_connect", host=self.host, port=self.port)
            try:
                client.publish("ClientStatus", b'{"online":true}', qos=1, retain=True)
            except Exception:
                pass
            client.subscribe("#", qos=1)
            if self._on_connect_hook:
                try:
                    self._on_connect_hook()
                except Exception:
                    pass

    def _on_disconnect(self, _client, _userdata, _flags=None, _reason_code=None, _properties=None) -> None:
        if self.connected:
            self.reconnect_count += 1
        self.connected = False
        self.disconnected_at = time.perf_counter()
        self._emit_event("mqtt_disconnect", reason=str(_reason_code))

    def _on_message(self, client, userdata, msg) -> None:
        # messages_received 由基类 MQTTTransport._on_message 记一次。
        # 这里原来又 self.messages_received += 1，于是**每条消息被数两遍**
        # （实测 720 条真实消息 → 计数 1440），HUD 上的"收到消息数"直接翻倍。
        super()._on_message(client, userdata, msg)
        payload = bytes(msg.payload)
        self.bytes_received += len(payload)
        self.last_message_at = time.time()
        topic = str(msg.topic)
        name = topic_message_name(topic)

        # --- 自己发出去的又自己收回来，不要当成裁判流量记进日志 -------------
        #
        # 本类在 _on_connect 里 subscribe("#")，所以客户端 publish 的每一个字节
        # 都会被 broker 原样送回。KeyboardMouseControl 在 OfficialEvent 下是
        # 75Hz，实测每条均值 220.6 字节 —— 也就是 16.1 KiB/s ≈ **58 MiB/h**。
        #
        # 危害不只是"日志变大"：
        #   1. RotatingJsonlLogger 总容量只有 20MiB×5=100MiB，约**每 20 分钟**
        #      就把轮转日志冲刷一遍，真正要查的裁判失败记录被挤掉；
        #   2. 字段名叫 referee_rx，排查裁判链路的人会据此认定"裁判在发键盘帧"，
        #      而现场一条真实的裁判消息都没有（实测 85/85 条全是自己的回环）；
        #   3. field_log.emit 是**同步落盘**（实测 194 µs/次），每条都打就在
        #      paho 网络线程上堆了 75Hz 的磁盘 I/O。
        #
        # 判据用 UPLINK_MESSAGES：裁判系统不会给我们发上行消息，所以凡是
        # 名字落在上行集合里的入站消息，100% 是自己的回环。
        # （实测 UPLINK_MESSAGES 与 safety.CONTROL_TOPICS 集合相等，
        #   所以这一个判据覆盖全部 11 条控制类 topic。）
        if name in UPLINK_MESSAGES:
            self.self_echo_messages += 1
            self.self_echo_bytes += len(payload)
            return

        self.referee_messages += 1
        self.referee_bytes += len(payload)
        self._emit_event("referee_rx", topic=topic, payload=payload)

        if name in {"GameStatus"}:
            # 比赛阶段同步给安全策略
            data = decode_message(name, payload)
            if data:
                self.safety.update_stage(data.get("current_stage", "0"))
                with self._decoded_lock:
                    self._decoded.append((topic, name, data, time.perf_counter()))
                return
        data = decode_message(name, payload)
        if not data:
            return
        with self._decoded_lock:
            self._decoded.append((topic, name, data, time.perf_counter()))

    # ------------------------------------------------------------------
    # 解码结果消费（UI 主线程）
    # ------------------------------------------------------------------
    def drain_decoded(self) -> list[tuple[str, str, dict[str, Any], float]]:
        with self._decoded_lock:
            messages = list(self._decoded)
            self._decoded.clear()
        return messages

    # ------------------------------------------------------------------
    # 发布（带安全门控 + 10Hz 限频）
    # ------------------------------------------------------------------
    def publish_topic(self, topic: str, payload: bytes, *, qos: int = 1,
                      now: float | None = None, intent: str = "control",
                      qualification_token: str | None = None) -> tuple[bool, str]:
        # 急停在任何模式（含 mock）都立即生效
        if self.safety.estop_active:
            self.last_error = "急停激活，禁止发布"
            return False, self.last_error
        allowed, reason = self.safety.can_publish(
            topic,
            now=now,
            intent=intent,
            qualification_token=qualification_token,
        )
        if not allowed and not self.mock_output:
            self.last_error = reason
            self._emit_event("publish_result", topic=topic, ok=False, reason=reason, intent=intent)
            return False, reason
        record = self._publish_record(topic, payload, qos)
        if self.mock_output:
            self._emit_event("publish_result", topic=topic, ok=True, reason="mock", intent=intent)
            return True, "mock"
        if self.client is None or not self.connected:
            # 这条错误在现场几乎总是同一个原因：3333 的监听者是**选手端那一侧**
            # （12.x 裁判端/服务器包里没有任何 MQTT 栈，见
            # tools/dt7_link/README.md 第 16.2 节），所以先确认选手端在跑、host 填对。
            self.last_error = ("未连接 MQTT 客户端：3333 由官方选手端那侧监听，"
                              "先确认选手端已启动、且 host 是选手端那台机器"
                              "（同机用 127.0.0.1，跨机用它的 192.168.12.x）")
            self._emit_event("publish_result", topic=topic, ok=False, reason=self.last_error, intent=intent)
            return False, self.last_error
        try:
            info = self.client.publish(topic, payload, qos=qos)
            rc = getattr(info, "rc", 0)
            mqtt = getattr(self, "_mqtt", None)
            success = rc == 0 or (mqtt is not None and rc == mqtt.MQTT_ERR_SUCCESS)
            if not success:
                self.last_error = f"MQTT 发布未进入网络（rc={rc}）"
                self._emit_event("publish_result", topic=topic, ok=False, reason=self.last_error, intent=intent)
                return False, self.last_error
            self.messages_sent += 1
            self.safety.mark_published(topic, now=now)
            self._emit_event("publish_result", topic=topic, ok=True, reason="ok", intent=intent)
            return True, "ok"
        except Exception as exc:  # pragma: no cover
            self.last_error = str(exc)
            self._emit_event("publish_result", topic=topic, ok=False, reason=self.last_error, intent=intent)
            return False, str(exc)

    def _publish_record(self, topic: str, payload: bytes, qos: int) -> MqttPublishRecord:
        record = MqttPublishRecord(
            topic=topic, payload=payload, qos=qos,
            timestamp=time.time(), mock_output=self.mock_output,
        )
        self.publish_records.append(record)
        return record

    def publish_named(self, name: str, payload: bytes, *, qos: int = 1,
                      now: float | None = None, intent: str = "control",
                      qualification_token: str | None = None) -> tuple[bool, str]:
        return self.publish_topic(
            name,
            payload,
            qos=qos,
            now=now,
            intent=intent,
            qualification_token=qualification_token,
        )

    # 兼容经济链路（透传 now，保持旧语义）
    def publish_common_command(
        self,
        payload: bytes,
        *,
        now: float | None = None,
        qualification_token: str | None = None,
    ) -> bool:
        ok, _ = self.publish_topic(
            "CommonCommand",
            payload,
            qos=1,
            now=now,
            intent="economy",
            qualification_token=qualification_token,
        )
        return ok

    def publish_air_support_command(
        self,
        payload: bytes,
        *,
        now: float | None = None,
        qualification_token: str | None = None,
    ) -> bool:
        ok, _ = self.publish_topic(
            "AirSupportCommand",
            payload,
            qos=1,
            now=now,
            intent="economy",
            qualification_token=qualification_token,
        )
        return ok

    # 堵住继承入口的安全后门：所有发布统一走 publish_topic（门控 + 10Hz + 急停）
    def publish_binary(self, topic: str, payload: bytes, qos: int = 1) -> bool:
        ok, _ = self.publish_topic(topic, payload, qos=qos)
        return ok

    def publish_match_command(self, topic: str, payload: bytes, *, qos: int = 1,
                              now: float | None = None) -> bool:
        ok, _ = self.publish_topic(topic, payload, qos=qos, now=now)
        return ok

    def publish_keyboard_mouse(self, payload: bytes) -> bool:
        ok, _ = self.publish_topic("KeyboardMouseControl", payload, qos=1)
        return ok

    def publish_estop_zero(self) -> tuple[bool, str]:
        """仅发布固定全零 KMC，绕过普通限频以避免按键释放被 75Hz 门控吞掉。"""
        payload = encode_keyboard_mouse_control()
        if self.mock_output:
            self._publish_record("KeyboardMouseControl", payload, 1)
            self._emit_event("publish_result", topic="KeyboardMouseControl", ok=True, reason="estop mock", intent="estop_zero")
            return True, "mock"
        if self.client is None or not self.connected:
            self.last_error = "未连接 MQTT 客户端，急停零帧未上网"
            self._emit_event("publish_result", topic="KeyboardMouseControl", ok=False, reason=self.last_error, intent="estop_zero")
            return False, self.last_error
        try:
            info = self.client.publish("KeyboardMouseControl", payload, qos=1)
            rc = getattr(info, "rc", 0)
            mqtt = getattr(self, "_mqtt", None)
            success = rc == 0 or (mqtt is not None and rc == mqtt.MQTT_ERR_SUCCESS)
            if not success:
                self.last_error = f"急停零帧未进入网络（rc={rc}）"
                self._emit_event("publish_result", topic="KeyboardMouseControl", ok=False, reason=self.last_error, intent="estop_zero")
                return False, self.last_error
            self._publish_record("KeyboardMouseControl", payload, 1)
            self.messages_sent += 1
            self.safety.mark_published("KeyboardMouseControl")
            self._emit_event("publish_result", topic="KeyboardMouseControl", ok=True, reason="ok", intent="estop_zero")
            return True, "ok"
        except Exception as exc:
            self.last_error = str(exc)
            self._emit_event("publish_result", topic="KeyboardMouseControl", ok=False, reason=self.last_error, intent="estop_zero")
            return False, self.last_error

    def publish_custom_control(self, data: bytes) -> bool:
        if len(data) > 30:
            self.last_error = "CustomControl 载荷最多 30 字节"
            return False
        ok, _ = self.publish_topic("CustomControl", encode_custom_control(data), qos=1)
        return ok

    def stats(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "mock_output": self.mock_output,
            "messages_received": self.messages_received,
            "messages_sent": self.messages_sent,
            "bytes_received": self.bytes_received,
            "reconnect_count": self.reconnect_count,
            "last_message_at": self.last_message_at,
            "connected_at": self.connected_at,
            "disconnected_at": self.disconnected_at,
            "last_error": self.last_error,
            # 诊断证据链的自检字段：非 0 就说明有事件没进 field_log。
            "dropped_events": self.dropped_events,
            "events_pending": len(self._event_queue),
            "event_queue_max": _HOOK_DISPATCH.queue_max,
        }
