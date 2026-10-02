"""`manager.py`（`tick_polar_link` 以外）与 `safety.py` 的**零覆盖决策点**补断言。

## 这份文件是什么

第 10 轮把 `tick_polar_link`（60 Hz 主泵）逐分支变异验证过了。第 12 轮把
`BackendManager` 的**其余部分**与 `safety.py` 做了同款审计：

* AST 枚举决策点（`if` / 三元 / `except` / `and`·`or` 的**每一个子句** / 推导式里的
  `if`，含嵌套），`manager.py` 去掉 `tick_polar_link` 后 256 个 + `safety.py` 28 个
  = **284 个**；
* 先用 **LINE 事件**粗筛（只做粗筛，分支两侧归属一律交给变异验证；
  不用 INSTRUCTION 级跟踪 —— 那会拖慢被测代码，把依赖墙钟的分支"测"出来）；
* 对粗筛出来的零覆盖点**逐条改坏**，跑**全量 60 套件**，逐套件比
  `(退出码, [PASS] 数, [FAIL] 数)`（只比退出码会漏掉"本来就红的套件又多红了几条"）。

本文件只收**行为覆盖**（变异后会红的那部分）。下面三类分开列，**不混进覆盖数**：

### 等价变异 —— 语义完全等价，任何行为断言都抓不住（5 条，明说抓不住）

| 位置 | 变异 | 为什么抓不住 |
|---|---|---|
| `manager.py:1094` | `manifest_path.is_file()` → `True` | 文件不存在时 `read_text` 抛 `FileNotFoundError`，正好落进 :1100 的 `except`，最终仍返回 `"UNKNOWN"` |
| `manager.py:1333` | `dt7_handle is not None` → `True` | 没有 `_dt7_link` 时 `None.close()` 抛 `AttributeError`，被 :1336 同一个 `except Exception` 吞掉，可观测行为不变 |
| `manager.py:2193` | `session is None` → `False` | `verified_core_ready()` 要么提前返回（:2189 已拦住）要么把 `session` 重新赋成非 None —— 这条 guard 的 True 侧**结构上不可达** |
| `safety.py:71` | `hook is None` → `False` | `hook=None` 时 `hook(str(reason))` 抛 `TypeError`，被 :75 吞掉；`control_state`/`last_reason` 一样 |
| `safety.py:157` | `name == "KeyboardMouseControl"` → `False` | 落到底部 :163 的 `return "core_kmc"`，返回值与 :158 完全相同 —— 这一行在当前实现里是冗余的 |

### 文本钉子 —— 0 条

本文件**没有**任何"匹配源码字符串"的断言。每条断言都跑真实对象、比对真实返回值。

### 仍然看不见的 —— 不假装覆盖

这轮扫完仍有 **109 个变异方向**在 `tests/**` 全部套件下**无人察觉**
（`manager.py` 61 个位置）。它们不在本文件里，因为本轮没有为它们写断言。
最密集的几处：`sync_frontend_state` 的血量/上限/队伍归属（25 个方向）、
`performance_values` 的设置项读取默认值（11）、`arm_match_control`（7）、
`_apply_custom_byte_block`（8）、`_cc_link_for`（8）、`stats`（5）。

## 断言是怎么来的

全部数值先用探针脚本在**正确实现**上跑出来再写成断言（照抄源码只会得到
一份恒绿的测试）。凡是探针读出来的事实与"看起来应该怎样"不一致的，
本文件断言的是**实测事实**，并在该条 `check()` 的 detail 里写明差在哪 ——
不假装现状是设计意图。

    python tests/manager_safety_branch_audit_test.py
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["POLAR_USER_DATA_ROOT"] = tempfile.mkdtemp(prefix="mgr_safety_audit_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client.backend.manager import BackendManager                # noqa: E402
from rm_client.backend.qualification import (                      # noqa: E402
    QualificationEvidenceStore,
    QualificationManager,
)
from rm_client.backend.safety import ControlState, OfficialSafety   # noqa: E402
from rm_client.model import RobotState, build_default_registry      # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILED.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


@contextlib.contextmanager
def manager_and_state(scenario: str = "LabDebug", *, with_state: bool = True):
    with tempfile.TemporaryDirectory() as workspace:
        state = RobotState() if with_state else None
        mgr = BackendManager(workspace=workspace, state=state,
                             client_id="3", scenario_key=scenario)
        try:
            yield mgr, state
        finally:
            try:
                mgr.shutdown()
            except Exception:                                       # noqa: BLE001
                pass


class _Raising:
    """A stub whose method raises, to drive a ``except`` branch honestly."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise self.exc


# ----------------------------------------------------------------------
# sync_mqtt_link —— 操作员在二级界面把 Port 填成非数字时的收尾
# ----------------------------------------------------------------------
def test_sync_mqtt_link_illegal_port() -> None:
    """line 251 ``except (TypeError, ValueError)`` / line 257 ``note`` 三元。

    现状实测：``int()`` 失败被 :251 接住并让 ``want_port = -1``，但 :263 又对
    **同一个**坏值做 ``int(port_setting.value or 0)``，于是 ValueError 从
    ``sync_mqtt_link`` 抛出去。
    现场症状（``polar_app.py:887`` 每拍都调它，且被 :920 ``_run_tick_stage``
    接住）：操作员在 MQTTLink 的 Port 里打错一个字之后，``mqtt.link`` 这一拍
    每 16 ms 失败一次，``backend_stats["ui_tick"]["last_error"]`` 永远钉在
    ``mqtt.link: ValueError: ...``，broker 地址**再也不会跟着设置项变**。

    这里断言的是**实测事实**（ValueError 而非 UnboundLocalError —— 删掉
    :251 的 except 会让 ``want_port`` 从未绑定，抛的是 UnboundLocalError），
    因此这一条能真正杀掉那个变异。修这个 bug 是另一件事，见交付报告。
    """
    with manager_and_state("LabDebug") as (mgr, _):
        registry = build_default_registry()
        module = registry.get("MQTTLink")
        good = (mgr.mqtt.host, mgr.mqtt.port)
        module.get_setting("Port").value = "not-a-number"
        raised: BaseException | None = None
        try:
            mgr.sync_mqtt_link(registry)
        except BaseException as exc:                                  # noqa: BLE001
            raised = exc
        check("MQTT Port 填非数字：抛的是 ValueError（不是删掉 except 后的 UnboundLocalError）",
              type(raised) is ValueError,
              f"{type(raised).__name__}: {raised}")
        check("MQTT Port 填非数字：有效地址没被动过",
              (mgr.mqtt.host, mgr.mqtt.port) == good,
              f"{mgr.mqtt.host}:{mgr.mqtt.port}")
        check("MQTT Port 填非数字：设置项没有被改写（异常发生在回写之前）",
              module.get_setting("Port").value == "not-a-number",
              repr(module.get_setting("Port").value))


def test_sync_mqtt_link_note_concatenation() -> None:
    """line 257 ``(note + "；" if note else "")`` —— 两处都非法时的合并写法。

    实测：Host 为空**且** Port 越界时，note 里两句都在，中间是全角分号。
    把 ``note + "；" if note else ""`` 改成只保留后半句，两句就只剩一句，
    操作员在界面上只会看到"端口非法"而不知道地址也被退回过。
    """
    with manager_and_state("LabDebug") as (mgr, _):
        registry = build_default_registry()
        module = registry.get("MQTTLink")
        module.get_setting("Host").value = "  "
        module.get_setting("Port").value = 1
        note = mgr.sync_mqtt_link(registry)
        check("地址与端口同时非法：note 里两句都在",
              "MQTT 地址为空或含空格" in note and "MQTT 端口非法" in note, note)
        check("地址与端口同时非法：两句用全角分号连接", "；" in note, note)
        check("两处非法后 broker 仍是原地址",
              (mgr.mqtt.host, mgr.mqtt.port) == ("127.0.0.1", 3333),
              f"{mgr.mqtt.host}:{mgr.mqtt.port}")
        check("两处非法后设置项被回写成真正在用的地址",
              str(module.get_setting("Host").value) == mgr.mqtt.host
              and int(module.get_setting("Port").value) == int(mgr.mqtt.port),
              f"{module.get_setting('Host').value}:{module.get_setting('Port').value}")


def test_sync_mqtt_link_missing_pieces() -> None:
    """line 240/241/245 三处早退。"""
    with manager_and_state("LabDebug") as (mgr, _):
        check("registry 为 None：返回空串且不改地址",
              mgr.sync_mqtt_link(None) == ""
              and (mgr.mqtt.host, mgr.mqtt.port) == ("127.0.0.1", 3333))
        empty = type("R", (), {"get": lambda self, key: None})()
        check("MQTTLink 模块缺失：返回空串", mgr.sync_mqtt_link(empty) == "")
        registry = build_default_registry()
        module = registry.get("MQTTLink")
        saved = module.get_setting("Port")
        module.settings = [s for s in module.settings if s.name != "Port"]
        check("Port 设置项缺失：返回空串（不是崩）", mgr.sync_mqtt_link(registry) == "")
        module.settings = list(module.settings) + [saved]


# ----------------------------------------------------------------------
# _sync_local_projectile_allowance —— 未知兵种
# ----------------------------------------------------------------------
def test_projectile_allowance_unknown_role() -> None:
    """line 1002-1006 ``except ValueError`` —— 兵种不在 (1,2,3,4,7) 里。

    现场症状：Aerial/Dart/Radar（role 6/8/9）的机器人，HUD 弹量格子会一直
    显示上一个场景的 17 mm 余弹（LabDebug 仿真值），而不是"没有数据"。
    删掉 except 以后 ``index`` 未绑定 → UnboundLocalError，整条
    GlobalUnitStatus 报文被 :1412 的兜底吞掉，**这一帧所有兵种数据都不更新**。
    """
    with manager_and_state("OfficialEvent") as (mgr, state):
        state.official_robot_bullets = [100, 90, 80, 70, 60]
        observed = {}
        for role_id in (6, 8, 9, 0):
            state.robot_id = role_id
            state.projectile_allowance_17mm = 111
            state.projectile_allowance_42mm = 222
            mgr._sync_local_projectile_allowance(state)
            observed[role_id] = (state.projectile_allowance_17mm,
                                 state.projectile_allowance_42mm)
        check("未知兵种（6/8/9/0）：17mm 与 42mm 双双归零，不沿用旧值",
              all(v == (0, 0) for v in observed.values()), str(observed))
        state.robot_id = 1
        mgr._sync_local_projectile_allowance(state)
        check("未知兵种被清干净之后：英雄仍正确拿 42mm 槽",
              (state.projectile_allowance_17mm, state.projectile_allowance_42mm) == (0, 100),
              f"{state.projectile_allowance_17mm}/{state.projectile_allowance_42mm}")


def test_projectile_allowance_role_slots() -> None:
    """line 1007 越界 / 1012 ``role_id == 1`` 分流 / 1011 ``max(0, ...)``。"""
    with manager_and_state("OfficialEvent") as (mgr, state):
        state.official_robot_bullets = [100, 90, 80, 70, 60]
        state.robot_id = 3
        mgr._sync_local_projectile_allowance(state)
        check("步兵（role 3，取 index 2）拿 17 mm 槽",
              (state.projectile_allowance_17mm, state.projectile_allowance_42mm) == (80, 0),
              f"{state.projectile_allowance_17mm}/{state.projectile_allowance_42mm}")
        state.robot_id = 5
        mgr._sync_local_projectile_allowance(state)
        check("role 5 不在 (1,2,3,4,7)：归零", state.projectile_allowance_17mm == 0)
        state.official_robot_bullets = [10]
        state.robot_id = 3
        state.projectile_allowance_17mm = 55
        mgr._sync_local_projectile_allowance(state)
        check("官方数组比角色表短（index 越界）：归零而不是留旧值",
              (state.projectile_allowance_17mm, state.projectile_allowance_42mm) == (0, 0),
              f"{state.projectile_allowance_17mm}/{state.projectile_allowance_42mm}")
        state.official_robot_bullets = [-5, 0, 7, 8, 9]
        state.robot_id = 3
        mgr._sync_local_projectile_allowance(state)
        check("官方给了负余弹：按 max(0, ...) 夹到 0（不显示 -5）",
              (state.projectile_allowance_17mm, state.projectile_allowance_42mm) == (7, 0),
              f"{state.projectile_allowance_17mm}/{state.projectile_allowance_42mm}")
        state.official_robot_bullets = []
        state.robot_id = 1
        mgr._sync_local_projectile_allowance(state)
        check("官方数组为空：两个槽都归零", state.projectile_allowance_42mm == 0)
        state.robot_id = 101
        state.official_robot_bullets = [100, 90, 80, 70, 60]
        mgr._sync_local_projectile_allowance(state)
        check("红方 id 101 取模后按 role 1（英雄）处理，走 42 mm 槽",
              (state.projectile_allowance_17mm, state.projectile_allowance_42mm) == (0, 100),
              f"{state.projectile_allowance_17mm}/{state.projectile_allowance_42mm}")


# ----------------------------------------------------------------------
# _recover_video_decoders —— native 0x0310 解码器"活着但不产帧"
# ----------------------------------------------------------------------
def test_recover_video_decoders_crashed_epoch() -> None:
    """lines 1062-1084：解码器崩了之后按 epoch 恢复 + 指数退避。

    现场症状：ffmpeg 进程还活着但已经不出帧时，画面**永久停在最后一帧**，
    HUD 上的 ``decoder_restarts`` 计数不动，现场根本看不出"链路已死"。
    删掉 ``crashed`` 的四个子句里任何一条，这一整套恢复都不触发 —— 没有任何
    套件会发现（改坏前 0 覆盖）。

    ⚠ 本条只用一个假解码器对象驱动纯状态机，**不碰 ffmpeg、不解一帧像素**，
    因此与视觉红线无关（`video_backend.py` 与 `rm_client/ui/**` 均未改动）。
    """
    class FakeDecoder:
        available = False
        restart_calls = 0
        restart_result = True

        def health(self):
            return {"first_frame_timeout": False, "stale": False}

        def restart(self, clear_latest=True):
            FakeDecoder.restart_calls += 1
            return FakeDecoder.restart_result

        def frame_size(self):
            return (0, 0)

        def latest_timestamp(self):
            return 0.0

        def stop(self):
            return None

        def start(self):
            return False

        def write(self, payload):
            return False

        def available_depth(self):
            return 0

    with manager_and_state("LabDebug") as (mgr, state):
        mgr.native_decoder = FakeDecoder()
        mgr.set_active_video_source("Native 0x0310")
        mgr._active_video_source = "native 0x0310"

        # crashed 四条件全真 -> 必须重启
        FakeDecoder.restart_calls = 0
        FakeDecoder.restart_result = True
        mgr._native_0310_decoder_started_once = True
        mgr._native_0310_bytes_since_reset = 5000
        mgr._native_failure_epoch_handled = False
        mgr._native_next_restart_at = 0.0
        mgr._native_restart_backoff_s = 0.5
        mgr._recover_video_decoders()
        check("解码器崩了（started_once + !available + 有流量 + 本轮未处理）：真的重启了一次",
              FakeDecoder.restart_calls == 1, str(FakeDecoder.restart_calls))
        check("重启成功：restarts 计数 +1 且 epoch 标记为已处理",
              mgr._native_0310_decoder_restarts == 1
              and mgr._native_failure_epoch_handled is True,
              f"restarts={mgr._native_0310_decoder_restarts}")
        check("重启成功：退避被复位成 0.5s 且下次重启时间清零",
              mgr._native_restart_backoff_s == 0.5
              and mgr._native_next_restart_at == 0.0,
              f"{mgr._native_restart_backoff_s}/{mgr._native_next_restart_at}")
        check("重启成功：重新等 SPS（blocked_until_sps 置位）",
              mgr._native_0310_blocked_until_sps is True)

        # 重启失败 -> 退避翻倍，不加 restarts
        FakeDecoder.restart_calls = 0
        FakeDecoder.restart_result = False
        mgr._native_0310_decoder_started_once = True
        mgr._native_0310_bytes_since_reset = 5000
        mgr._native_failure_epoch_handled = False
        mgr._native_next_restart_at = 0.0
        mgr._native_restart_backoff_s = 0.5
        before = time.perf_counter()
        mgr._recover_video_decoders()
        check("重启失败：退避 0.5 -> 1.0（翻倍）",
              mgr._native_restart_backoff_s == 1.0, str(mgr._native_restart_backoff_s))
        check("重启失败：下次重启时间被推到未来",
              mgr._native_next_restart_at > before, str(mgr._native_next_restart_at))
        check("重启失败：restarts 计数不虚增、epoch 不标记为已处理",
              mgr._native_0310_decoder_restarts == 1
              and mgr._native_failure_epoch_handled is False,
              f"restarts={mgr._native_0310_decoder_restarts}")

        # 退避窗口内不再敲 ffmpeg
        FakeDecoder.restart_calls = 0
        FakeDecoder.restart_result = True
        mgr._native_next_restart_at = time.perf_counter() + 30.0
        mgr._native_failure_epoch_handled = False
        mgr._recover_video_decoders()
        check("退避窗口内：一次都不再调用 restart（不会把 ffmpeg 打死）",
              FakeDecoder.restart_calls == 0, str(FakeDecoder.restart_calls))

        # crashed 的四个子条件逐个拿掉都不该重启
        for label, attr, value in (
            ("从未启动过", "_native_0310_decoder_started_once", False),
            ("本 reset 后一个字节都没收到", "_native_0310_bytes_since_reset", 0),
            ("本轮已经处理过这次失败", "_native_failure_epoch_handled", True),
        ):
            FakeDecoder.restart_calls = 0
            FakeDecoder.restart_result = True
            mgr._native_next_restart_at = 0.0
            mgr._native_0310_decoder_started_once = True
            mgr._native_0310_bytes_since_reset = 99
            mgr._native_failure_epoch_handled = False
            setattr(mgr, attr, value)
            mgr._recover_video_decoders()
            check(f"不满足 crashed 条件（{label}）：不重启",
                  FakeDecoder.restart_calls == 0, str(FakeDecoder.restart_calls))
            setattr(mgr, attr, True if attr != "_native_failure_epoch_handled" else False)

        # deploy_mode == 1 本身就足以选中 native
        mgr.set_active_video_source("UDP HEVC")
        mgr._active_video_source = "udp hevc"
        state.deploy_mode = 1
        FakeDecoder.restart_calls = 0
        FakeDecoder.restart_result = True
        mgr._native_next_restart_at = 0.0
        mgr._native_0310_decoder_started_once = True
        mgr._native_0310_bytes_since_reset = 99
        mgr._native_failure_epoch_handled = False
        mgr._recover_video_decoders()
        check("DeployModeStatusSync=1 独自就能选中 native 并触发恢复",
              FakeDecoder.restart_calls == 1, str(FakeDecoder.restart_calls))


# ----------------------------------------------------------------------
# _apply_custom_byte_block —— 0x0310 原生流写入重试
# ----------------------------------------------------------------------
def test_custom_byte_block_write_retry_loop() -> None:
    """line 1796-1805 ``while not wrote`` —— 写不进去时的那 10 ms 重试。

    现场症状：解码器输入队列满（合成突发 / 直播抖动）时，第一帧 ``write``
    返回 False。如果这个重试循环被删掉，那一帧直接被判成"传输丢包"，
    `_native_0310_blocked_until_sps` 置位、Annex-B 闸门 reset，
    **画面要一直等到下一个 SPS 才会恢复** —— 中间几秒黑屏，而 HUD 上看不出
    任何异常（``payload_written`` 只是 backend_stats 里的一个布尔）。

    ⚠ 用假解码器对象驱动，不启动 ffmpeg、不解一帧像素，与视觉红线无关。
    """
    class SlowDecoder:
        """First write is rejected (queue full), the retry inside the 10 ms
        window succeeds.  That is the only way to enter :1803."""

        def __init__(self) -> None:
            self.available = False
            self.started = 0
            self.writes: list[int] = []
            self.input_chunks_dropped = 0
            self.input_chunks_written = 0
            self.input_bytes_written = 0
            self.frames_decoded = 0
            self.last_error = ""
            self.continuous_decoded_seconds = 0.0

        def health(self):
            return {"first_frame_timeout": False, "stale": False}

        def start(self):
            self.started += 1
            self.available = True
            return True

        def write(self, payload):
            self.writes.append(len(payload))
            if len(self.writes) == 1:
                return False           # queue full this once
            return True

        def frame_size(self):
            return (0, 0)

        def latest_timestamp(self):
            return 0.0

        def input_queue_depth(self):
            return 0

        def stop(self):
            self.available = False

        def stop(self):
            self.available = False

    class PassThroughGate:
        """Stand-in for AnnexBRecoveryGate: the SPS/PPS gate has its own
        coverage; this test is about what happens after bytes are allowed out."""

        blocked = False

        def __init__(self) -> None:
            self.fed: list[int] = []

        def feed(self, payload):
            self.fed.append(len(payload))
            return bytes(payload)

        def reset(self):
            self.blocked = False

    with manager_and_state("LabDebug") as (mgr, state):
        decoder = SlowDecoder()
        mgr.native_decoder = decoder
        mgr._native_recovery = PassThroughGate()
        mgr._active_video_source = "native 0x0310"
        # A real 300-byte 0x0310 packet (see tests/smoke_test.py for the layout).
        payload = bytes((index * 13) & 0xFF for index in range(297))
        packet = bytes((0b11, 23, 297 & 0xFF)) + payload
        mgr._apply_one(state, "CustomByteBlock", {"data": packet})
        info = state.backend_stats.get("native_video_0310", {})
        check("解码器第一次写不进去时，重试循环把它写进去了（payload_written 为真）",
              info.get("payload_written") is True, str(info.get("payload_written")))
        check("重试至少发生了一次（decoder 收到 >=2 次 write）",
              len(decoder.writes) >= 2, str(decoder.writes))
        check("写成功之后不算丢包：blocked_until_sps 不因这次失败而置位",
              info.get("blocked_until_sps") is False, str(info.get("blocked_until_sps")))
        check("decoder_restarts 仍是 0（不是把正常重试当成崩溃）",
              mgr._native_0310_decoder_restarts == 0,
              str(mgr._native_0310_decoder_restarts))

        # Retry that never succeeds inside the window -> treated as a transport
        # gap, and the epoch is left unhandled so _recover_video_decoders can act.
        class DeadDecoder(SlowDecoder):
            def write(self, payload):
                self.writes.append(len(payload))
                return False

        dead = DeadDecoder()
        mgr.native_decoder = dead
        mgr._native_failure_epoch_handled = True
        mgr._apply_one(state, "CustomByteBlock", {"data": packet})
        info = state.backend_stats.get("native_video_0310", {})
        check("10 ms 内一直写不进去：按传输丢包处理，blocked_until_sps 置位",
              info.get("blocked_until_sps") is True, str(info.get("blocked_until_sps")))
        check("10 ms 内一直写不进去：epoch 标记被清掉，让恢复逻辑接手",
              mgr._native_failure_epoch_handled is False,
              str(mgr._native_failure_epoch_handled))
        check("10 ms 内一直写不进去：解码器确实被反复调用过（不是一次就放弃）",
              len(dead.writes) >= 2, str(len(dead.writes)))


# ----------------------------------------------------------------------
# _build_commit / _request_input_clear / shutdown
# ----------------------------------------------------------------------
def test_build_commit_unreadable_manifest() -> None:
    """lines 1094-1100 / 1104-1115 的两个 except。

    现场症状：发布包里的 ``BUILD_MANIFEST.json`` 损坏时，commit 退化成
    ``UNKNOWN``；所有现场资格证据（``field_evidence``）都记成 ``UNKNOWN``，
    事后**无法证明这份证据是这台机器、这次构建产出的** —— 资格签字等于作废。
    """
    import types

    with manager_and_state("LabDebug") as (mgr, _):
        import tempfile as _tf
        broken = _tf.mkdtemp(prefix="mgr_safety_manifest_")
        manifest = Path(broken) / "BUILD_MANIFEST.json"
        manifest.write_text("{ this is not json", encoding="utf-8")
        original = mgr.paths
        mgr.paths = types.SimpleNamespace(manifest_path=manifest, app_root=Path(broken))
        try:
            value = mgr._build_commit()
        finally:
            mgr.paths = original
        check("manifest 存在但内容损坏：JSON 解析错被接住，commit 退化成 UNKNOWN",
              value == "UNKNOWN", value)


def test_build_commit_git_unavailable() -> None:
    """line 1114 第二个 except：git 不可用时也不能抛；顺带钉住 1112 的成功路径。

    现场症状：发布包里的 commit 决定了现场资格证据能不能证明"这份证据来自
    这次构建"。git 成功时必须真的采用它的输出 —— 把 ``returncode == 0``
    的判断写反，commit 会永远退化成 ``UNKNOWN``，所有证据失去绑定。
    """
    import subprocess as sp

    with manager_and_state("LabDebug") as (mgr, _):
        import types
        original = mgr.paths
        mgr.paths = types.SimpleNamespace(
            manifest_path=Path(r"Z:\audit\no\such\BUILD_MANIFEST.json"),
            app_root=Path(r"Z:\audit\no\such"),
        )
        real_run = sp.run

        def boom(*args, **kwargs):
            raise OSError("git is not installed")

        sp.run = boom
        try:
            value = mgr._build_commit()
        finally:
            sp.run = real_run
            mgr.paths = original
        check("git 子进程不可用：commit 退化成 UNKNOWN（不是 OSError 冒到 __init__）",
              value == "UNKNOWN", value)

        # 成功路径：有 git 且 checkout 合法时必须采用 git 的输出
        try:
            proc = sp.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=10.0)
            head = proc.stdout.strip() if proc.returncode == 0 else ""
        except Exception:                                           # noqa: BLE001
            head = ""
        if head:
            mgr.paths = types.SimpleNamespace(
                manifest_path=Path(r"Z:\audit\no\such\BUILD_MANIFEST.json"),
                app_root=Path(ROOT),
            )
            try:
                value = mgr._build_commit()
            finally:
                mgr.paths = original
            check("git 可用时 commit 就是 HEAD 的 40 位哈希（不是 UNKNOWN）",
                  value == head, f"{value} vs {head}")
        else:
            print("[SKIP] 本机拿不到 git HEAD，跳过 commit 成功路径断言")


def test_request_input_clear_hook_raises() -> None:
    """line 1135-1145：清零回调抛异常时记 ``input_clear_error`` 并**跳过**正常事件。

    现场症状：GUI 崩了/被关掉时这条 ``return`` 让 ``input_clear`` 这条正常
    现场记录不再落盘 —— 事后从 field log 里看不出"这一帧请求过清零"。
    删掉 except 会让异常冒到 :1315/:2124 的 safety 迁移里，
    ``control_state`` 改不到 LOCKED/ESTOP —— 那一帧之后客户端仍可能发上行。
    """
    with manager_and_state("LabDebug") as (mgr, _):
        events: list[tuple[str, dict]] = []
        real_emit = mgr.field_log.emit
        mgr.field_log.emit = lambda event, **payload: events.append((event, payload))
        try:
            mgr.input_clear_hook = _Raising(RuntimeError("ui gone"))
            before = mgr._input_clear_generation
            mgr._request_input_clear("probe")
        finally:
            mgr.field_log.emit = real_emit
        names = [name for name, _ in events]
        check("清零回调抛异常：只落 input_clear_error，不落 input_clear",
              names == ["input_clear_error"], str(names))
        check("清零回调抛异常：代数仍然 +1（后代仍有唯一序号）",
              mgr._input_clear_generation == before + 1,
              f"{before} -> {mgr._input_clear_generation}")
        payload = events[0][1] if events else {}
        check("input_clear_error 里带异常类型与原因",
              payload.get("error") == "RuntimeError: ui gone"
              and payload.get("reason") == "probe",
              str(payload))


def test_request_input_clear_without_hook() -> None:
    """line 1135 ``if hook is not None`` 的另一侧 + callback_installed 字段。"""
    with manager_and_state("LabDebug") as (mgr, _):
        events: list[tuple[str, dict]] = []
        real_emit = mgr.field_log.emit
        mgr.field_log.emit = lambda event, **payload: events.append((event, payload))
        try:
            mgr.input_clear_hook = None
            mgr._request_input_clear("probe")
        finally:
            mgr.field_log.emit = real_emit
        names = [name for name, _ in events]
        check("没装清零回调：照常落 input_clear（headless 也要有记录）",
              names == ["input_clear"], str(names))
        check("没装清零回调：callback_installed 记 False",
              events and events[0][1].get("callback_installed") is False,
              str(events[0][1] if events else None))


def test_shutdown_dt7_close_raises() -> None:
    """line 1332-1337：DT7 独占口 close 失败不许打断 shutdown 的其余步骤。

    现场症状：关程序时 DT7 句柄被占用 → close 抛错 → ``backend_shutdown``
    不落盘、``native_decoder`` 已停但 field log 里看不到"后端已关"，
    事后无法用日志确认"这一轮确实收过尾帧"。
    """

    class Handle:
        closed = False

        def close(self):
            Handle.closed = True
            raise RuntimeError("port busy")

    with manager_and_state("LabDebug") as (mgr, _):
        mgr._dt7_link = Handle()
        events: list[str] = []
        real_emit = mgr.field_log.emit
        mgr.field_log.emit = lambda event, **payload: events.append(event)
        try:
            mgr.shutdown()
        finally:
            mgr.field_log.emit = real_emit
        check("DT7 close 抛异常：close 确实被调用过", Handle.closed is True)
        check("DT7 close 抛异常：shutdown 仍然走完并落 backend_shutdown",
              "backend_shutdown" in events, str(events))

    # 没有 DT7 链路时（纯裁判系统部署）整条关机链也必须跑完：
    # 把 :1333 的 `dt7_handle is not None` 写反会在这里变成 None.close()。
    with manager_and_state("LabDebug") as (fresh, _):
        fresh.shutdown()
        check("没有 _dt7_link 时 shutdown 不抛（纯裁判部署也是常规路径）", True)


# ----------------------------------------------------------------------
# 上行发布闸门
# ----------------------------------------------------------------------
def test_qualification_custom_control_intent_gate() -> None:
    """line 2043 ``intent not in {"prm1_vision", "prm1_spin"}``。

    现场症状：这一行是"普通发布器不许偷带资格令牌"的唯一实现。改成放行后，
    任意 UI 开关都能把资格令牌塞进一条**不是** PRM1 的 CustomControl 帧发上线，
    绕过"资格专用出口只服务两条已评审 schema"的约束。
    """
    with manager_and_state("LabDebug") as (mgr, _):
        for intent in ("prm1_vision", "prm1_spin"):
            ok, reason = mgr.publish_qualification_custom_control(
                b"\x00" * 4, intent=intent, qualification_token="tok")
            check(f"资格出口放行已评审 intent={intent}", ok is True, f"{ok}/{reason}")
        for intent in ("core_kmc", "economy", "hero", "", "PRM1_VISION"):
            ok, reason = mgr.publish_qualification_custom_control(
                b"\x00" * 4, intent=intent, qualification_token="tok")
            check(f"资格出口拒绝 intent={intent or '(空串)'}",
                  ok is False
                  and reason == "unsupported qualification CustomControl intent",
                  f"{ok}/{reason}")


# ----------------------------------------------------------------------
# record_firmware_zero_observation
# ----------------------------------------------------------------------
def test_record_firmware_zero_observation() -> None:
    """lines 2139 / 2143 / 2146。

    现场症状：这是"物理观察员确认车上已经归零"的时间戳，是急停证据链最后一环。
    * 删掉 :2139 的 raise → 没触发过急停也能"记录"一个 0 ms 的归零，
      现场日志里凭空多出一条合规证据；
    * :2143 的 ``observed_at is None`` 决定用不用墙钟 —— 写死成用传入值时，
      真实观察（不传 observed_at）会拿 ``None`` 去算，直接 TypeError；
    * :2146 的 ``state is not None`` 决定要不要写进 ``backend_stats``，
      headless（state=None）时删掉会 AttributeError。
    """
    with manager_and_state("LabDebug") as (mgr, state):
        try:
            mgr.record_firmware_zero_observation()
            raised = None
        except ValueError as exc:
            raised = exc
        check("没触发急停就记录固件归零：抛 ValueError",
              raised is not None
              and str(raised) == "trigger E-stop before recording firmware zero",
              repr(raised))

        mgr._estop_triggered_at = time.perf_counter() - 0.5
        elapsed = mgr.record_firmware_zero_observation()
        check("不传 observed_at：用墙钟量到 ~500ms（不是 0、也不是 None 崩掉）",
              400.0 < elapsed < 1500.0, f"{elapsed:.1f}ms")
        check("默认量到的值同步写进 backend_stats",
              abs(state.backend_stats["firmware_zero_observed_ms"] - elapsed) < 1e-6)

        exact = mgr.record_firmware_zero_observation(
            observed_at=mgr._estop_triggered_at + 1.25)
        check("显式 observed_at：精确算成 1250.0ms（证明用的是传入值不是墙钟）",
              abs(exact - 1250.0) < 1e-6, f"{exact}")
        check("显式 observed_at 的值同样落到 backend_stats",
              abs(state.backend_stats["firmware_zero_observed_ms"] - 1250.0) < 1e-6)

        back = mgr.record_firmware_zero_observation(
            observed_at=mgr._estop_triggered_at - 5.0)
        check("观察时间早于急停时刻：按 max(0, ...) 夹成 0.0ms（不出现负延迟）",
              back == 0.0, f"{back}")

    with manager_and_state("LabDebug", with_state=False) as (headless, _):
        headless._estop_triggered_at = time.perf_counter()
        value = headless.record_firmware_zero_observation()
        check("没有 RobotState 时照样返回一个非负毫秒数（不 AttributeError）",
              isinstance(value, float) and value >= 0.0, f"{value}")


# ----------------------------------------------------------------------
# arm_match_control —— 整条 fail-closed 授权梯子
# ----------------------------------------------------------------------
def _qualified_manager(workspace: str, evidence_root: str) -> BackendManager:
    mgr = BackendManager(workspace=workspace, state=RobotState(), client_id="3",
                         scenario_key="OfficialEvent")
    store = QualificationEvidenceStore(Path(evidence_root),
                                       build_commit=mgr._build_commit())
    manager = QualificationManager(store)
    mgr.qualification = manager
    manager.new_session(robot_id="3", firmware_hash="A" * 64, match_mode="Standard",
                        video_source="udp", operator="alpha")
    for key in ("preflight", "codec_field_test", "control_heartbeat_test",
                "estop_robot_test", "firmware_loss_zero_test", "loss_recovery_test",
                "hero_deploy_status_test", "latency_soak_test"):
        manager._record_result(key, True, notes="probe",
                               source="qualification-runner")
    return mgr


def test_arm_match_control_gate_ladder() -> None:
    """lines 2182 / 2185 / 2189 / 2193 —— 前四道 fail-closed 闸。

    现场症状：这四道是"mock 证据不可签 / 必须真连着 / 必须真合格 / 会话还在"。
    任何一道被删掉，后果都是**在没有真实资格的情况下把控制解锁**：
    * 删 2182 → LabDebug mock 会话也能 arm，模拟出来的"全绿"被当成现场证据；
    * 删 2185 → 没连裁判系统也能 arm，车收不到任何急停/归属；
    * 删 2189 → 没有任何一条技术测试记录也能 arm。
    """
    with manager_and_state("LabDebug") as (mgr, _):
        check("LabDebug 场景：mock_output=True 且 allow_mqtt_publish=False",
              mgr.mqtt.mock_output is True
              and mgr.safety.allow_mqtt_publish is False,
              f"{mgr.mqtt.mock_output}/{mgr.safety.allow_mqtt_publish}")
        ok, reason = mgr.arm_match_control(firmware_hash="0" * 64)
        check("LabDebug（mock）场景拒绝 arm，且理由点名 mock 证据不可签",
              ok is False
              and reason == "arm requires a real MQTT output scenario (mock evidence is not signable)",
              f"{ok}/{reason}")
        check("拒绝 arm 之后状态仍是 LOCKED",
              mgr.safety.control_state is ControlState.LOCKED,
              mgr.safety.control_state.value)

    with manager_and_state("OfficialEvent") as (mgr, _):
        check("OfficialEvent 场景：mock_output=False 且 allow_mqtt_publish=True",
              mgr.mqtt.mock_output is False
              and mgr.safety.allow_mqtt_publish is True)
        check("未连接时 mqtt.connected 为 False", mgr.mqtt.connected is False)
        ok, reason = mgr.arm_match_control(firmware_hash="0" * 64)
        check("没连裁判系统时拒绝 arm，理由是 live MQTT",
              ok is False
              and reason == "arm requires a live MQTT referee connection",
              f"{ok}/{reason}")
        check("没连就 arm 被拒之后状态仍是 LOCKED",
              mgr.safety.control_state is ControlState.LOCKED,
              mgr.safety.control_state.value)
        mgr.mqtt.connected = True
        ok, reason = mgr.arm_match_control(firmware_hash="0" * 64)
        check("连着但没有任何资格会话时拒绝 arm",
              ok is False and reason == "no qualification session", f"{ok}/{reason}")
        check("无会话 arm 被拒之后状态仍是 LOCKED",
              mgr.safety.control_state is ControlState.LOCKED,
              mgr.safety.control_state.value)


def test_arm_match_control_identity_and_success() -> None:
    """lines 2198 / 2203 / 2204 / 2206 / 2212 —— 身份比对与 arm 成功路径。

    现场症状：身份比对是"这份现场证据只对得上这一台机器人、这一版固件、
    这套图传"的最后一道锁。删掉 :2206 的分支，证据与运行时不匹配也会 arm 成功。
    """
    with tempfile.TemporaryDirectory() as workspace, \
            tempfile.TemporaryDirectory() as evidence:
        mgr = _qualified_manager(workspace, evidence)
        try:
            check("只有技术记录、没有人在场签字：core_ready 仍为 False",
                  mgr.qualification.core_ready()[0] is False,
                  str(mgr.qualification.core_ready()))
            signed = None
            try:
                mgr.qualification.sign_off("alpha")
            except ValueError as exc:
                signed = exc
            check("同一个人不能既操作又签安全负责人（拒绝自签自核）",
                  isinstance(signed, ValueError)
                  and str(signed) == "safety lead must be a second named person",
                  repr(signed))
            mgr.qualification.sign_off("beta")
            check("签完双人在场：core_ready 为 True",
                  mgr.qualification.core_ready()[0] is True,
                  str(mgr.qualification.core_ready()))
            check("全部技术测试通过后拿到的能力集",
                  sorted(mgr.qualification.capabilities()) == ["core_kmc", "hero"],
                  str(sorted(mgr.qualification.capabilities())))
            mgr.mqtt.connected = True
            mgr.mqtt.mock_output = False
            mgr.safety.allow_mqtt_publish = True
            ok, reason = mgr.arm_match_control(
                firmware_hash="A" * 64, robot_id="3", match_mode="Standard",
                video_source="udp")
            check("身份全对时 arm 成功并进入 MATCH_ARMED",
                  ok is True and reason == "MATCH_ARMED"
                  and mgr.safety.control_state is ControlState.MATCH_ARMED,
                  f"{ok}/{reason}/{mgr.safety.control_state.value}")

            ok, reason = mgr.arm_match_control(
                firmware_hash="B" * 64, robot_id="3", match_mode="Standard",
                video_source="udp")
            check("固件哈希对不上：拒绝 arm 并说明是身份不符",
                  ok is False
                  and reason == "runtime robot/firmware/match/video identity differs from evidence",
                  f"{ok}/{reason}")

            ok, reason = mgr.arm_match_control(
                firmware_hash="A" * 64, robot_id="4", match_mode="Standard",
                video_source="udp")
            check("机器人 ID 对不上：拒绝 arm", ok is False, f"{ok}/{reason}")

            ok, reason = mgr.arm_match_control(
                firmware_hash="A" * 64, robot_id="3", match_mode="Campus",
                video_source="udp")
            check("比赛模式对不上：拒绝 arm", ok is False, f"{ok}/{reason}")

            ok, reason = mgr.arm_match_control(
                firmware_hash="A" * 64, robot_id="3", match_mode="Standard",
                video_source="native_0310")
            check("图传来源对不上：拒绝 arm", ok is False, f"{ok}/{reason}")

            ok, reason = mgr.arm_match_control(
                firmware_hash="nothex", robot_id="3", match_mode="Standard",
                video_source="udp")
            check("固件哈希形状非法：返回 False 而不是抛异常（异常会打断 arm 流程）",
                  ok is False
                  and reason == "firmware hash must be a 64-character SHA-256",
                  f"{ok}/{reason}")

            # 参数全缺省：robot_id / match_mode 从 state 取，video_source 从
            # 当前激活源规范化后取。此处激活源为空 -> 规范化得到 "" -> 拒绝。
            mgr.lock_control("probe")
            mgr.state.robot_id = 3
            mgr.state.match_mode = "Standard"
            mgr._active_video_source = ""
            ok, reason = mgr.arm_match_control(firmware_hash="A" * 64)
            check("三个身份参数全缺省时用 state/当前源的回退值（图传为空故拒绝）",
                  ok is False
                  and reason == "runtime robot/firmware/match/video identity differs from evidence",
                  f"{ok}/{reason}")

            mgr._active_video_source = "udp hevc"
            ok, reason = mgr.arm_match_control(firmware_hash="A" * 64)
            check("三个身份参数全缺省且当前源是 UDP 时，回退链能对上并 arm 成功",
                  ok is True and reason == "MATCH_ARMED", f"{ok}/{reason}")
        finally:
            try:
                mgr.shutdown()
            except Exception:                                       # noqa: BLE001
                pass


def test_canonical_readiness_video_source() -> None:
    """lines 2219 / 2221 —— 资格证据里的图传标识必须规范化。

    现场症状：这两行把界面上的 ``UDP HEVC`` 之类写法收敛成证据里那两种
    canonical 值。删掉 :2219（native 分支）后，native 现场会话永远对不上
    证据，arm 被无限拒绝；删掉 :2221 同理。
    """
    with manager_and_state("LabDebug") as (mgr, _):
        for text in ("Native 0x0310", "native", "0x0310"):
            mgr._active_video_source = text
            check(f"图传来源 {text!r} 规范化成 native_0310",
                  mgr._canonical_readiness_video_source() == "native_0310",
                  mgr._canonical_readiness_video_source())
        for text in ("UDP HEVC", "shark udp", "udp", "hevc", "h265"):
            mgr._active_video_source = text
            check(f"图传来源 {text!r} 规范化成 udp",
                  mgr._canonical_readiness_video_source() == "udp",
                  mgr._canonical_readiness_video_source())
        for text in ("webcam", "", "some-future-source"):
            mgr._active_video_source = text
            check(f"未声明的图传来源 {text!r} 规范化成空串（而不是猜一个）",
                  mgr._canonical_readiness_video_source() == "",
                  mgr._canonical_readiness_video_source())


# ----------------------------------------------------------------------
# performance_values
# ----------------------------------------------------------------------
def test_performance_values_role_matrix() -> None:
    """lines 2279-2296：英雄近战/远程、哨兵半自动/自动四条从未走到的取值。

    现场症状：官方性能体系取值是**发给裁判系统**的性能选择命令。
    英雄选近战却发成远程(4/4)，或哨兵选半自动却发成自动(0)，
    车上会按错的冷却/血量参数跑整场，而且**不会报错** —— 裁判只按收到的值执行。
    """
    registry = build_default_registry()
    profile = registry.get("RobotProfile")

    def setv(name: str, value: str) -> None:
        profile.get_setting(name).value = value

    zero = {"shooter": 0, "chassis": 0, "sentry_control": 0}
    setv("Role", "Hero")
    setv("Hero Type", "Melee")
    check("英雄近战：shooter/chassis 都取 HERO_MELEE(3)，哨兵 0",
          BackendManager.performance_values(registry)
          == {"shooter": 3, "chassis": 3, "sentry_control": 0},
          str(BackendManager.performance_values(registry)))
    setv("Hero Type", "Range")
    check("英雄远程：shooter/chassis 都取 HERO_RANGE(4)，哨兵 0",
          BackendManager.performance_values(registry)
          == {"shooter": 4, "chassis": 4, "sentry_control": 0},
          str(BackendManager.performance_values(registry)))
    setv("Hero Type", "")
    check("英雄类型没设：按非近战处理（HERO_RANGE），不是崩溃也不是 0",
          BackendManager.performance_values(registry)
          == {"shooter": 4, "chassis": 4, "sentry_control": 0},
          str(BackendManager.performance_values(registry)))

    setv("Role", "Sentinel")
    setv("Sentinel Control", "SemiAuto")
    check("哨兵半自动：只有 sentry_control=SENTRY_SEMI_AUTO(1)，其余 0",
          BackendManager.performance_values(registry)
          == {"shooter": 0, "chassis": 0, "sentry_control": 1},
          str(BackendManager.performance_values(registry)))
    setv("Sentinel Control", "Automatic")
    check("哨兵自动：sentry_control=SENTRY_AUTOMATIC(0)，与全 0 可区分吗（值相同）",
          BackendManager.performance_values(registry)
          == {"shooter": 0, "chassis": 0, "sentry_control": 0},
          str(BackendManager.performance_values(registry)))
    setv("Sentinel Control", "")
    check("哨兵控制项没设：按自动(0)处理", BackendManager.performance_values(registry) == zero)

    setv("Role", "Engineer")
    check("工程师：三条全 0（该角色不参与性能体系）",
          BackendManager.performance_values(registry) == zero)
    setv("Role", "")
    check("角色没设：三条全 0，不猜", BackendManager.performance_values(registry) == zero)
    check("注册表里没有 RobotProfile：三条全 0（不是 KeyError）",
          BackendManager.performance_values({}) == zero)
    raised = None
    try:
        BackendManager.performance_values(None)
    except AttributeError as exc:
        raised = exc
    check("注册表整个是 None：抛 AttributeError（调用方必须自己保证非空）",
          isinstance(raised, AttributeError), repr(raised))


# ----------------------------------------------------------------------
# safety.py
# ----------------------------------------------------------------------
def test_safety_input_clear_hook_raises() -> None:
    """safety.py:73-77 ``except Exception: pass``。

    现场症状：这是**明确写死的**"安全状态迁移不许依赖 UI/诊断"。
    去掉它，一个坏掉的 UI 钩子就能让 ``lock_control`` / ``trigger_estop``
    在设置完 ``control_state`` 之后抛出，控制权实际解锁却向上抛异常 ——
    正好是这段注释想防的那种失败模式。
    """
    safety = OfficialSafety("LabDebug")
    safety.input_clear_hook = _Raising(RuntimeError("ui gone"))
    safety.lock_control("probe")
    check("清零钩子抛异常：control_state 仍然是 LOCKED（迁移不依赖 UI）",
          safety.control_state is ControlState.LOCKED,
          safety.control_state.value)
    check("清零钩子抛异常：last_reason 照样写下来了",
          safety.last_reason == "probe", safety.last_reason)

    reached: list[str] = []
    safety2 = OfficialSafety("LabDebug")
    safety2.input_clear_hook = reached.append
    safety2.lock_control("second")
    check("钩子正常时被调用，拿到的是迁移后的理由",
          reached == ["second"], str(reached))

    safety3 = OfficialSafety("LabDebug")
    safety3.input_clear_hook = _Raising(RuntimeError("boom"))
    safety3.trigger_estop()
    check("急停迁移同样不受钩子异常影响（ESTOP 生效）",
          safety3.control_state is ControlState.ESTOP
          and safety3.estop_active is True,
          safety3.control_state.value)


def test_safety_estop_elapsed_and_heartbeat_default_clock() -> None:
    """safety.py:237 / 245 的 ``now is None`` —— 省略参数时用 `perf_counter`。

    现场症状：``estop_elapsed()`` 是急停后"车真的停了吗"的判定输入；
    ``heartbeat_deadline()`` 是看门狗超时判定。写死成"总是用传入值"时，
    生产路径（不传 now）会拿 ``None`` 去减，直接 TypeError。
    """
    safety = OfficialSafety("LabDebug")
    safety.estop_active = True
    safety.estop_at = 100.0
    check("estop_elapsed(now=103.5) 精确算成 3.5",
          abs(safety.estop_elapsed(now=103.5) - 3.5) < 1e-9,
          str(safety.estop_elapsed(now=103.5)))
    check("estop_elapsed() 省略 now：走 perf_counter 而不 TypeError",
          isinstance(safety.estop_elapsed(), float), str(safety.estop_elapsed()))
    check("heartbeat_deadline(0.0)：省略 now 时 perf_counter - 0 一定超时",
          safety.heartbeat_deadline(0.0) is True)
    check("heartbeat_deadline(now 刚写过)：未超时",
          safety.heartbeat_deadline(last_write_at=100.0, now=100.2) is False)
    check("heartbeat_deadline(now=10.4, 默认 0.5s)：0.4s 未超时",
          safety.heartbeat_deadline(last_write_at=10.0, now=10.4) is False)
    check("heartbeat_deadline 恰好等于阈值不算超时（严格大于）",
          safety.heartbeat_deadline(last_write_at=10.0, now=10.5) is False)
    check("heartbeat_deadline 超过阈值：超时",
          safety.heartbeat_deadline(last_write_at=10.0, now=10.51) is True)
    check("heartbeat_deadline 自定义 timeout_s=1.0 时 2.0s 才算超时",
          safety.heartbeat_deadline(last_write_at=10.0, now=12.0, timeout_s=1.0) is True
          and safety.heartbeat_deadline(last_write_at=10.0, now=11.0,
                                        timeout_s=1.0) is False)


def test_safety_update_stage_edges() -> None:
    """safety.py:104 / 107 两条从未走到的分支。

    现场症状：
    * :104 的 ``len(stage) <= 2`` —— 裁判下发 ``"100"`` 这种三位数时，
      当前实现**忽略**它（保持上一阶段），而不是把它当成阶段 1 走进 MATCH。
      删掉长度判断后 ``current_stage`` 会变成 ``"100"``，
      ``is_fighting()`` 变 False，场景切不到正式比赛。
    * :107 的 ``stage == "MATCH" and self._stage_seen`` —— 只有先见过数字阶段，
      字面量 "MATCH" 才会被折成 4；启动前直接收到 "MATCH" 不算数（fail-closed）。
    """
    safety = OfficialSafety("LabDebug")
    safety.update_stage(" 4 ")
    check("带空白的 ' 4 ' 被规范化成 '4' 并标记 _stage_seen",
          (safety.current_stage, safety._stage_seen, safety.is_fighting())
          == ("4", True, True),
          f"{safety.current_stage}/{safety._stage_seen}/{safety.is_fighting()}")
    safety.update_stage("04")
    check("'04' 规范成 '4'（str(int(...))）", safety.current_stage == "4",
          safety.current_stage)
    safety.update_stage("123")
    check("三位数 '123' 被忽略，保持 '4' 而不是变成 '123'",
          safety.current_stage == "4", safety.current_stage)
    check("三位数被忽略后仍在交战阶段", safety.is_fighting() is True)

    fresh = OfficialSafety("LabDebug")
    fresh.update_stage("MATCH")
    check("还没见过数字阶段就收到 'MATCH'：不采信，保持 '0' 且非交战",
          (fresh.current_stage, fresh._stage_seen, fresh.is_fighting())
          == ("0", False, False),
          f"{fresh.current_stage}/{fresh._stage_seen}/{fresh.is_fighting()}")

    seen = OfficialSafety("LabDebug")
    seen.update_stage("2")
    check("先见过阶段 2（自检）时 current_stage 是 '2'",
          seen.current_stage == "2", seen.current_stage)
    seen.update_stage("MATCH")
    check("非 4 的阶段收到 'MATCH' 也会被折成 '4'（否则进了比赛仍在非交战阶段）",
          (seen.current_stage, seen.is_fighting()) == ("4", True),
          f"{seen.current_stage}/{seen.is_fighting()}")

    stayed = OfficialSafety("LabDebug")
    stayed.update_stage("4")
    stayed.update_stage("MATCH")
    check("本来就是 '4' 时再收到 'MATCH'：保持 '4' 并处于交战",
          (stayed.current_stage, stayed.is_fighting()) == ("4", True),
          f"{stayed.current_stage}/{stayed.is_fighting()}")

    stage5 = OfficialSafety("LabDebug")
    stage5.update_stage("5")
    check("阶段 5（SETTLEMENT）不算交战（is_fighting 为假）",
          stage5.is_fighting() is False, stage5.current_stage)

    stage100 = OfficialSafety("LabDebug")
    stage100.update_stage("100")
    check("'100' 被忽略：既不改状态也不标记 _stage_seen",
          (stage100.current_stage, stage100._stage_seen) == ("0", False),
          f"{stage100.current_stage}/{stage100._stage_seen}")


def test_safety_capability_for_table() -> None:
    """safety.py:155-163 四条：intent 优先，其次 topic 名，最后兜底 core_kmc。

    现场症状：这张表决定"已授权能力 → 哪条 topic"。把 :155 的 intent 短路删掉，
    PRM1 自瞄/自转会退回 core_kmc，于是**只要 core_kmc 被授权，自瞄就跟着解锁** ——
    没评审过的 schema 也能上线。兜底 ``core_kmc`` 是保守方向，不是漏洞。
    """
    table = {
        ("KeyboardMouseControl", "control"): "core_kmc",
        ("KeyboardMouseControl", "core_kmc"): "core_kmc",
        ("CustomControl", "prm1_vision"): "prm1_vision",
        ("CustomControl", "prm1_spin"): "prm1_spin",
        ("CustomControl", "economy"): "economy",
        ("CustomControl", "hero"): "hero",
        ("HeroDeployModeEventCommand", "control"): "hero",
        ("CommonCommand", "control"): "economy",
        ("MapClickCmd", "control"): "core_kmc",
        ("RuneActivateCommand", "control"): "core_kmc",
        ("DartCommand", "control"): "core_kmc",
        ("SentryCtrlCommand", "control"): "core_kmc",
        ("AirSupportCommand", "control"): "core_kmc",
        ("AssemblyCommand", "control"): "core_kmc",
        ("RobotPerformanceSelectionCommand", "control"): "core_kmc",
    }
    for (name, intent), expected in table.items():
        got = OfficialSafety._capability_for(name, intent)
        check(f"_capability_for({name}, {intent}) == {expected}", got == expected, got)
    check("未知 intent（prm1_bench）不在白名单里时退回 core_kmc",
          OfficialSafety._capability_for("CustomControl", "prm1_bench") == "core_kmc",
          OfficialSafety._capability_for("CustomControl", "prm1_bench"))


def test_safety_qualification_ttl_clamp() -> None:
    """safety.py:128 ``now is None`` + :132 ``max(1.0, min(300.0, ttl))``。

    现场症状：ttl 是"资格窗口"，运营方可以传。写死成不夹取时，
    ``ttl_s=0`` 会给出一个**立即过期**的令牌（现场按按钮没反应且看不出原因），
    ``ttl_s=10_000`` 会给出一个 2.7 小时的长期令牌 —— 等于把短时凭证变成长时凭证。
    """
    safety = OfficialSafety("LabDebug")
    cases = [(0.0, 1.0), (-5.0, 1.0), (0.5, 1.0), (30.0, 30.0),
             (300.0, 300.0), (10_000.0, 300.0)]
    for ttl, expected in cases:
        token = safety.begin_qualification(["core_kmc"], ttl_s=ttl, now=1000.0)
        check(f"ttl_s={ttl} 被夹到 {expected}s",
              abs((token.expires_at - 1000.0) - expected) < 1e-9,
              f"{token.expires_at - 1000.0}")
    token = safety.begin_qualification(["core_kmc"], now=1000.0)
    check("省略 ttl_s 时默认 30s", abs((token.expires_at - 1000.0) - 30.0) < 1e-9,
          str(token.expires_at - 1000.0))
    check("显式 now 时 last_transition_at 也用同一个时钟",
          safety.last_transition_at == 1000.0, str(safety.last_transition_at))
    safety2 = OfficialSafety("LabDebug")
    safety2.begin_qualification(["core_kmc"])
    check("省略 now 时 last_transition_at 取 perf_counter（是个真实时刻）",
          safety2.last_transition_at > 0.0, str(safety2.last_transition_at))


def test_safety_stats_token_projection() -> None:
    """safety.py:248/256：有令牌报过期时刻，没令牌报 0.0。

    现场症状：HUD 上"资格窗口剩余"读的就是 ``qualification_expires_at``。
    没有令牌时报 0.0 而不是 None/负数，UI 才有稳定的"未激活"显示。
    """
    safety = OfficialSafety("LabDebug")
    check("没有令牌：qualification_expires_at 为 0.0",
          safety.stats()["qualification_expires_at"] == 0.0,
          str(safety.stats()["qualification_expires_at"]))
    token = safety.begin_qualification(["core_kmc"], ttl_s=42.0, now=777.0)
    check("有令牌：报的就是令牌自己的 expires_at",
          safety.stats()["qualification_expires_at"] == token.expires_at,
          str(safety.stats()["qualification_expires_at"]))
    stats = safety.stats()
    check("stats 里 authorized_capabilities 是排序后的列表",
          stats["authorized_capabilities"] == []
          and isinstance(stats["authorized_capabilities"], list),
          str(stats["authorized_capabilities"]))


def test_arm_match_control_without_core_kmc() -> None:
    """line 2210-2213 ``except ValueError`` —— 能力集里没有 core_kmc。

    正常路径走不到这里（``core_ready`` 为真时 ``core_kmc`` 必被授权），
    所以这一层是**防御性兜底**。正因为走不到，它此前完全没有断言。

    现场症状：一旦这层被删掉，``safety.arm_match`` 抛出的 ValueError 会从
    ``arm_match_control`` 冒出去 —— UI 那层的按钮回调没有 try，异常直接变成
    "按了没反应"，而 ``control_state`` 停在 LOCKED，**操作员看不出是哪一步
    把他挡住的**。这里直接把返回值钉住：必须是 ``(False, 原因)`` 而不是抛。
    """
    with tempfile.TemporaryDirectory() as workspace, \
            tempfile.TemporaryDirectory() as evidence:
        mgr = _qualified_manager(workspace, evidence)
        try:
            mgr.qualification.sign_off("beta")
            mgr.mqtt.connected = True
            mgr.mqtt.mock_output = False
            mgr.safety.allow_mqtt_publish = True
            # 强制"证据里没有 core_kmc"这一状态，走那条从未被触达的 except
            mgr.qualification.capabilities = lambda: frozenset({"economy", "hero"})
            raised: BaseException | None = None
            try:
                result = mgr.arm_match_control(firmware_hash="A" * 64, robot_id="3",
                                               match_mode="Standard",
                                               video_source="udp")
            except BaseException as exc:                              # noqa: BLE001
                raised, result = exc, None
            check("能力集缺 core_kmc：返回 (False, 原因) 而不是把异常抛给 UI",
                  raised is None and result == (False, "core_kmc qualification is required"),
                  f"raised={raised!r} result={result!r}")
            check("能力集缺 core_kmc 之后控制权仍是 LOCKED",
                  mgr.safety.control_state is ControlState.LOCKED,
                  mgr.safety.control_state.value)
        finally:
            try:
                mgr.shutdown()
            except Exception:                                       # noqa: BLE001
                pass


def main() -> int:
    tests = [
        test_sync_mqtt_link_illegal_port,
        test_sync_mqtt_link_note_concatenation,
        test_sync_mqtt_link_missing_pieces,
        test_projectile_allowance_unknown_role,
        test_projectile_allowance_role_slots,
        test_recover_video_decoders_crashed_epoch,
        test_build_commit_unreadable_manifest,
        test_build_commit_git_unavailable,
        test_request_input_clear_hook_raises,
        test_request_input_clear_without_hook,
        test_shutdown_dt7_close_raises,
        test_custom_byte_block_write_retry_loop,
        test_qualification_custom_control_intent_gate,
        test_record_firmware_zero_observation,
        test_arm_match_control_gate_ladder,
        test_arm_match_control_identity_and_success,
        test_arm_match_control_without_core_kmc,
        test_canonical_readiness_video_source,
        test_performance_values_role_matrix,
        test_safety_input_clear_hook_raises,
        test_safety_estop_elapsed_and_heartbeat_default_clock,
        test_safety_update_stage_edges,
        test_safety_capability_for_table,
        test_safety_qualification_ttl_clamp,
        test_safety_stats_token_projection,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"FAILED {len(FAILED)}")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("ALL MANAGER/SAFETY BRANCH-AUDIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
