"""``tick_polar_link``（60 Hz 主泵）的分支契约，外加变异验证挖出来的零覆盖分支。

## 为什么有这份文件

``BackendManager.tick_polar_link``（``rm_client/backend/manager.py``）是队内
0x5A 链路的**单帧泵**：下行入 HUD、已授权的意图上线。它每一帧都跑，而且
**明确决定不重构**（实测 60 Hz 逻辑层只占单核 0.28%），所以它不会被改写 ——
正因如此，它改错了没人看得见才是最大的风险：

* 改错的表现是"车不动"或者"UI 上的数字慢慢不对"，现场几乎无法归因；
* 而且它**不会崩**，只会静默地少做一件事。

本轮对它做逐分支变异验证：97 个变异（48 个 ``if``/``elif`` + 6 个 ``except``
+ 约 30 个 ``and``/三元子句 + 若干常量化），每个改坏一处判断就跑**全量 57 套件**，
逐套件比 ``(退出码, [PASS] 数, [FAIL] 数, traceback 数)``（不只比退出码：``field_candidate_test.py``
本来就红，只看退出码会漏掉"多红了几条"）。

结果：**41 个被抓红，56 个存活**。本文件守的就是这 56 个里的 55 个
（F3 / S3 两条只能被源码文本钉子兜住，见文件末尾与下面的说明）。

## 两条必须写清楚的约定

1. **"守住"和"这一句有人守"是两回事。** 每条断言都在注释里写明它对应
   哪个子句；确实区分不出来的两条单独列在文件末尾（``F3`` 是等价变异、
   ``S3`` 今天区分不出来），**不假装守住**。
2. **断言写的是实测事实，不是源码抄一遍。** 全部数值先用探针脚本在
   正确实现上读出来，再写成断言 —— 照抄源码只会得到一个恒绿的测试。

⚠ 这两条例外只被**源码文本匹配**兜住（文本钉子对等价重构会假红，
也只防"整句被删"、防不住"语义等价的改写"），所以本文件**不**把它们
算作行为覆盖。

    python tests/tick_polar_link_branches_test.py
"""

from __future__ import annotations

import contextlib
import math
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["POLAR_USER_DATA_ROOT"] = tempfile.mkdtemp(prefix="polar_tick_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client import robot_roles                                    # noqa: E402
from rm_client.backend.manager import BackendManager                  # noqa: E402
from rm_client.backend.polar_link import (SPIN_CMD_NONE,             # noqa: E402
                                          SPIN_CMD_OFF, SPIN_CMD_ON)
from rm_client.control import (DT7_DEFAULT_KEY_MASK,                 # noqa: E402
                               DT7_FIRE_KEY_MASK, DT7_SPIN_KEY_MASK)
from rm_client.model import RobotState, build_default_registry       # noqa: E402

FAILED: list[str] = []
PASSED = 0

#: ``tick_polar_link`` 会往 ``control`` 上写的全部属性。替身必须把它们预置好，
#: 否则"这一帧没写"和"值恰好没变"分不开。
_CONTROL_ATTRS = (
    "radio_km_enabled", "radio_km_link_state", "radio_km_link_note",
    "radio_km_gate_reason", "radio_km_policy", "radio_km_policy_hint",
    "radio_km_robot_key", "radio_km_robot_rx", "radio_km_robot_rx_growing",
    "dt7_enabled", "dt7_last_error", "dt7_key_mask", "dt7_mouse_note",
    "cc_enabled", "cc_last_error", "cc_key_mask",
)

#: ``RobotLink`` 里默认打开的设置项（其余保持默认）。
_DT7_ON = {"Direct DT7 Egress": True, "Allow Drive": True}
_CC_ON = {"Custom Controller Egress": True, "Allow Drive": True}
_RADIO_ON = {"Radio KM Egress": True, "Allow Drive": True}
_DRIVE_ON = {"Allow Drive": True, "Client Chassis Drive": True}
_GIMBAL_ON = {"Client Gimbal Control": True, "Vision NUC Occupied": False}


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"[PASS] {name}")
    else:
        FAILED.append(name)
        print(f"[FAIL] {name}  -> {detail}")


class _Cmd:
    def __init__(self, vx=0.0, vy=0.0, wz=0.0, keyboard_value=0):
        self.vx, self.vy, self.wz = vx, vy, wz
        self.keyboard_value = keyboard_value


class _Ctl:
    """控制面替身：记录两次"松手"调用，它们是 DT7/CC 的安全兜底。"""

    def __init__(self, vx=0.0, vy=0.0, wz=0.0, keyboard_value=0):
        self.last_command = _Cmd(vx, vy, wz, keyboard_value)
        self.dt7_releases = 0
        self.cc_releases = 0
        self.dt7_link = None
        self.cc_link = None
        for attr in _CONTROL_ATTRS:
            setattr(self, attr, "")

    def release_direct_dt7(self) -> None:
        self.dt7_releases += 1

    def release_custom_controller(self) -> None:
        self.cc_releases += 1


class _StubModule:
    """故意缺几个设置项的 RobotLink：测"设置项读不到时必须失败关闭"。"""

    def __init__(self, real, missing):
        self._real, self._missing, self.enabled = real, set(missing), True

    def get_setting(self, name):
        return None if name in self._missing else self._real.get_setting(name)


class _StubRegistry:
    def __init__(self, real, missing):
        self._real = real
        self._robot_link = _StubModule(real.get("RobotLink"), missing)

    def get(self, name):
        return self._robot_link if name == "RobotLink" else self._real.get(name)


@contextlib.contextmanager
def mgr(*, alive=True, settings=None, robot_id=3, missing=(), dbg=None, overlay=None):
    """造一台"链路活着、模块开着"的 manager。

    ``Stub`` 口在本机不会真的把 ``connected`` 置真，所以涉及连接句柄的断言
    一律显式写 ``m.polar.connected = True`` 再计数 stop/start。
    """

    state = RobotState()
    m = BackendManager(state=state, client_id="3", scenario_key="OfficialEvent")
    m.polar.port = "Stub"
    m.polar.start()
    reg = build_default_registry()
    link = reg.get("RobotLink")
    link.enabled = True
    for name, value in (settings or {}).items():
        link.get_setting(name).value = value
    reg.get("SpinMode").enabled = True
    if alive:
        m.polar_bridge.last_segment_at = time.perf_counter()
        m.polar_bridge.segment_counts[0x1E] = 1
    if dbg:
        m.polar_bridge.debug_packages.update(dbg)
    if overlay:
        m.polar_bridge.overlay.update(overlay)
    state.robot_id = robot_id
    used = _StubRegistry(reg, missing) if missing else reg
    try:
        yield m, state, used, link
    finally:
        try:
            m.shutdown()
        except Exception:
            pass


def _spy(m):
    """数 ``polar.stop()`` / ``polar.start()`` 被调了几次。"""
    calls = {"stop": 0, "start": 0}
    real_start, real_stop = m.polar.start, m.polar.stop

    def start(*a, **k):
        calls["start"] += 1
        return real_start(*a, **k)

    def stop(*a, **k):
        calls["stop"] += 1
        return real_stop(*a, **k)

    m.polar.start, m.polar.stop = start, stop
    return calls


def _aimed(state, yaw=20.0, pitch=5.0, calibrated=True, locked=True):
    state.vision_aim_yaw_deg = yaw
    state.vision_aim_pitch_deg = pitch
    state.vision_calibrated = calibrated
    state.target_locked = locked
    return state


# ===========================================================================
# A. 取 state / 提前退出 / 链路新鲜度封顶
# ===========================================================================
def test_state_fallback_and_early_return() -> None:
    """没有 state 时必须**如实**说"不活跃"，且回落到 ``self.state``。

    改错了现场看到什么：HUD 上队内链路那一栏永远显示"在线"，而实际上
    什么都没在跑（``{"active": True}``）；或者 ``self.state`` 这条回落路
    一断，manager 自己的状态就再也拿不到下行数据，HUD 整块静止。
    """

    m = BackendManager(state=None, client_id="3", scenario_key="OfficialEvent")
    try:
        out = m.tick_polar_link()
        check("没有 state 时如实返回 {'active': False}", out == {"active": False}, repr(out))
        check("提前返回不多带任何键（调用方按 active 分支，不该被别的键骗）",
              set(out) == {"active"}, repr(sorted(out)))
    finally:
        m.shutdown()

    with mgr() as (m, state, reg, link):
        state.polar_link_active = False        # 哨兵：确认这一帧真的写了
        m.state = state
        m.tick_polar_link(None, reg, _Ctl())   # 显式传 None -> 必须回落到 self.state
        check("state 传 None 时回落到 self.state 并真的写进它",
              state.polar_link_active is True,
              f"polar_link_active={state.polar_link_active!r}（没回落就还是初始值）")
        check("回落后 state.backend_stats 里有 polar_link 诊断快照",
              "polar_link" in state.backend_stats, str(sorted(state.backend_stats)[:6]))


def test_link_age_is_capped() -> None:
    """``polar_link_age_s`` 必须封顶在 999.0。

    改错了现场看到什么：刚开机还没收到任何段时，链路年龄是 ``inf``；
    封顶写成 ``max`` 之后，几小时没插线的机器会显示 10000+ 秒，
    HUD 的"多久没收到数据"直接失去意义，排查时 nobody 知道是 3 秒还是 3 天。
    """

    with mgr(alive=False) as (m, state, reg, link):
        m.tick_polar_link(state, reg, _Ctl())
        check("一个段都没收到时年龄是有限值 999.0（不是 inf）",
              state.polar_link_age_s == 999.0, repr(state.polar_link_age_s))
        m.polar_bridge.last_segment_at = time.perf_counter() - 10_000
        m.polar_bridge.segment_counts[0x1E] = 1
        m.tick_polar_link(state, reg, _Ctl())
        check("链路停了 10000 秒也封顶在 999.0",
              state.polar_link_age_s == 999.0, repr(state.polar_link_age_s))
        check("封顶值仍然是有限数（HUD 不会拿到 inf/NaN 去格式化）",
              math.isfinite(state.polar_link_age_s), repr(state.polar_link_age_s))


# ===========================================================================
# D/E. Port 与 Baud：precedence、只在真变时动手、合法区间
# ===========================================================================
def test_port_only_acted_on_when_explicitly_changed() -> None:
    """``Port`` 保持默认 "Auto" 时场景配置说了算；且**只在真变了**时才动连接。

    改错了现场看到什么：每帧都 ``stop()`` 一次连接，链路在 60 Hz 上反复
    开关，机器人那边表现为"连上又掉、掉又连"，而客户端的 LNK 灯几乎不亮。
    """

    with mgr() as (m, state, reg, link):
        ctl = _Ctl()
        m.tick_polar_link(state, reg, ctl)
        check("Port 保持默认 'Auto' 时不改场景给的 'Stub'",
              m.polar.port == "Stub", m.polar.port)
        check("Port 默认值原样透传，没有被悄悄改写",
              link.get_setting("Port").value == "Auto",
              str(link.get_setting("Port").value))

        link.get_setting("Port").value = "COM7"
        m.polar.connected = True
        calls = _spy(m)
        m.tick_polar_link(state, reg, ctl)
        check("显式改成 COM7 后落到传输层", m.polar.port == "COM7", m.polar.port)
        check("换口时旧句柄必须被关掉（connected 时 stop 一次）",
              calls["stop"] == 1, f"stop={calls['stop']}")

        # 同一端口连拍四帧：一次都不许再 stop。
        for _ in range(3):
            m.polar.connected = True
            m.tick_polar_link(state, reg, ctl)
        check("端口没变时连拍 4 帧一次都不再 stop（子句 `want_port != polar.port`）",
              calls["stop"] == 1, f"stop={calls['stop']}")

        link.get_setting("Port").value = "Auto"
        m.polar.connected = True
        m.tick_polar_link(state, reg, ctl)
        check("改回默认 'Auto' 不会把口切回场景值（显式清空也不回退猜测）",
              m.polar.port == "COM7", m.polar.port)
        check("改回默认也不触发额外 stop", calls["stop"] == 1, f"stop={calls['stop']}")

    # 没有连接句柄时换口/换波特率**不许**去 stop：没有东西可关，
    # 而 stop 一旦被无条件触发，60 Hz 上就会每帧拆一次"句柄"。
    with mgr() as (m, state, reg, link):
        ctl = _Ctl()
        m.polar.connected = False
        m.polar._running = False
        calls = _spy(m)
        link.get_setting("Port").value = "COM9"
        m.tick_polar_link(state, reg, ctl)
        check("没连上时换口照旧落库（判定用连接状态，不是没落库就不管）",
              m.polar.port == "COM9", m.polar.port)
        check("没连上时换口不许 stop（子句 `if self.polar.connected`）",
              calls["stop"] == 0, f"stop={calls['stop']}")
        link.get_setting("Baud").value = 460800
        m.tick_polar_link(state, reg, ctl)
        check("没连上时换波特率不许 stop", calls["stop"] == 0, f"stop={calls['stop']}")
        check("没连上时波特率同样落库", m.polar.baud == 460800, m.polar.baud)

    # 换口时必须把上一次的失败原因清掉，否则 HUD 会一直挂着旧的红字。
    with mgr() as (m, state, reg, link):
        m.polar.last_error = "boom"
        m.polar.last_error_code = "OLD_CODE"
        link.get_setting("Port").value = "COM10"
        m.tick_polar_link(state, reg, _Ctl())
        check("换口后旧的 last_error 被清空（否则新口还没试就显示旧失败）",
              m.polar.last_error == "", repr(m.polar.last_error))
        check("换口后旧的 last_error_code 也被清空",
              m.polar.last_error_code == "", repr(m.polar.last_error_code))


def test_baud_precedence_and_range() -> None:
    """``Baud``：默认 115200 = "没动过"（场景说了算）；显式值必须落在 1200~3000000。

    改错了现场看到什么：区间放宽或收紧都不报错，只是 UART 桥那边按一个
    没被约定的波特率开流 —— 现场表现是"偶尔能连上、偶尔连不上"。
    """

    with mgr() as (m, state, reg, link):
        ctl = _Ctl()
        m.polar.baud = 921600
        link.get_setting("Baud").value = 115200          # == BAUD_DEFAULT
        m.tick_polar_link(state, reg, ctl)
        check("Baud 保持默认 115200 时场景的 921600 继续作数（子句 `!= BAUD_DEFAULT`）",
              m.polar.baud == 921600, m.polar.baud)

        link.get_setting("Baud").value = 460800
        m.polar.connected = True
        calls = _spy(m)
        m.tick_polar_link(state, reg, ctl)
        check("显式改成 460800 后落到传输层", m.polar.baud == 460800, m.polar.baud)
        check("改波特率会关掉旧句柄", calls["stop"] == 1, f"stop={calls['stop']}")
        for _ in range(3):
            m.polar.connected = True
            m.tick_polar_link(state, reg, ctl)
        check("波特率没变时连拍 4 帧一次都不再 stop（子句 `!= polar.baud`）",
              calls["stop"] == 1, f"stop={calls['stop']}")

    for want, applied, why in (
        (921600, 921600, "区间上界以内的常规值"),
        (9600, 9600, "区间下界 1200 以上"),
        (100, 115200, "低于 1200 的非法值必须被拒"),
        (5_000_000, 115200, "高于 3000000 的非法值必须被拒"),
    ):
        with mgr() as (m, state, reg, link):
            link.get_setting("Baud").value = want
            m.tick_polar_link(state, reg, _Ctl())
            check(f"Baud={want} -> {applied}（{why}）", m.polar.baud == applied,
                  str(m.polar.baud))


def test_gimbal_slew_is_clamped() -> None:
    """``Gimbal Slew`` 必须夹在 [1.0, 45.0] 度/步。

    改掉了现场看到什么：夹紧没了，0 会让每帧步长为 0（云台纹丝不动，
    操作员以为程序死了）；200 会让云台每帧甩 200°，直接打满机械限位。
    """

    for want, want_clamped, why in (
        (8, 8.0, "区间内的值原样通过"),
        (0, 1.0, "下界：0 步长会让云台完全不动"),
        (200, 45.0, "上界：每帧 200° 会打满机械限位"),
        (-5, 1.0, "负值必须夹到下界而不是倒着转"),
    ):
        with mgr() as (m, state, reg, link):
            link.get_setting("Gimbal Slew").value = want
            m.tick_polar_link(state, reg, _Ctl())
            check(f"Gimbal Slew={want} -> {want_clamped}（{why}）",
                  m.polar_arbiter.slew == want_clamped, str(m.polar_arbiter.slew))


# ===========================================================================
# G/H. D 路（车端桥）与驾驶上行通道
# ===========================================================================
def test_link_mode_validation_and_bridge_host_guard() -> None:
    """``Link Mode`` 只认 serial/tcp；选 tcp 又没填主机时**不许**切链路。

    改错了现场看到什么：主机空着也把链路切到 tcp，transport 去连空地址，
    客户端从此既收不到 0x5A 也发不出帧，而界面只显示"链路不可用"，
    没有人会想到是少填了一个 IP。
    """

    with mgr() as (m, state, reg, link):
        ctl = _Ctl()
        link.get_setting("Link Mode").value = "bogus"
        m.tick_polar_link(state, reg, ctl)
        check("Link Mode='bogus' 回落成 serial（子句 `in ('serial','tcp')`）",
              m.polar.transport == "serial", m.polar.transport)

        link.get_setting("Link Mode").value = "tcp"
        link.get_setting("Bridge Host").value = ""
        m.polar.connected = True
        calls = _spy(m)
        m.tick_polar_link(state, reg, ctl)
        check("tcp 但主机为空：链路不许切（子句 `and not want_host`）",
              m.polar.transport == "serial", m.polar.transport)
        check("主机为空时报 NO_BRIDGE_HOST 错误码",
              m.polar.last_error_code == "NO_BRIDGE_HOST", m.polar.last_error_code)
        check("主机为空时给出人话原因",
              "桥接主机地址" in m.polar.last_error, m.polar.last_error)
        check("主机为空时不动连接句柄", calls["stop"] == 0, f"stop={calls['stop']}")

        link.get_setting("Bridge Host").value = "10.0.0.9"
        m.polar.connected = True
        calls = _spy(m)
        m.tick_polar_link(state, reg, ctl)
        check("填了主机才真的切到 tcp", m.polar.transport == "tcp", m.polar.transport)
        check("主机地址落库", m.polar.host == "10.0.0.9", m.polar.host)
        check("换链路先关旧句柄", calls["stop"] == 1, f"stop={calls['stop']}")
        check("换链路时正在跑就重新 start（子句 `was_running`）",
              calls["start"] == 1, f"start={calls['start']}")

    with mgr() as (m, state, reg, link):
        m.polar.connected = False
        m.polar._running = False
        calls = _spy(m)
        link.get_setting("Link Mode").value = "tcp"
        link.get_setting("Bridge Host").value = "10.0.0.9"
        m.tick_polar_link(state, reg, _Ctl())
        check("本来就没在跑时换链路不 start（避免凭空拉起一条死链）",
              calls["start"] == 0, f"start={calls['start']}")


def test_bridge_port_default() -> None:
    """``Bridge Port`` 取不到有效值时必须回 50570。

    改错了现场看到什么：端口变成 0，车端桥服务在 50570，客户端连 0 端口，
    现场只看到"连不上"。
    """

    for want, why in ((0, "填了 0"), (None, "填了 None"), ("", "填了空串")):
        with mgr() as (m, state, reg, link):
            link.get_setting("Link Mode").value = "tcp"
            link.get_setting("Bridge Host").value = "10.0.0.9"
            link.get_setting("Bridge Port").value = want
            m.tick_polar_link(state, reg, _Ctl())
            check(f"Bridge Port={want!r} 回落到 50570（{why}）",
                  m.polar.tcp_port == 50570, str(m.polar.tcp_port))


def test_drive_channel_normalisation() -> None:
    """``Drive Channel`` 要归一化大小写/空格，且非白名单值回落 robot_cmd。

    改错了现场看到什么：空格没被换成下划线，"virtual rc" 传不进固件，
    驾驶速度一路走 0x01（固件 master 没人读）→ 车完全不动，而界面显示
    "驾驶已授权"。
    """

    for want, expect, why in (
        ("virtual rc", "virtual_rc", "空格要换成下划线"),
        ("  BOTH  ", "both", "首尾空格 + 大小写都要归一"),
        ("Virtual_RC", "virtual_rc", "大写要归一"),
        ("weird", "robot_cmd", "非白名单值必须回落"),
        ("", "robot_cmd", "空值必须回落"),
    ):
        with mgr() as (m, state, reg, link):
            link.get_setting("Drive Channel").value = want
            m.tick_polar_link(state, reg, _Ctl())
            check(f"Drive Channel={want!r} -> {expect}（{why}）",
                  m.polar.drive_channel == expect, m.polar.drive_channel)


# ===========================================================================
# F/I. 设置项读不到时必须"失败关闭"
# ===========================================================================
def test_missing_settings_fail_closed() -> None:
    """设置项缺失一律当"没授权"处理，**不许**默认成有权。

    改错了现场看到什么：``Allow Drive`` 缺失被当成 True，客户端在操作员
    从没授权过的情况下取得驾驶权；``Vision NUC Occupied`` 缺失被当成
    False，客户端抢走视觉 NUC 的云台 —— 两边都会在场上表现为"车自己动了"。
    """

    with mgr(settings=_DRIVE_ON, missing=("Allow Drive",)) as (m, state, reg, link):
        out = m.tick_polar_link(state, reg, _Ctl(vx=1.0))
        check("'Allow Drive' 读不到 -> drive_armed 为假（不许默认 True）",
              out["drive_armed"] is False, str(out["drive_armed"]))

    with mgr(missing=("Client Chassis Drive",)) as (m, state, reg, link):
        out = m.tick_polar_link(state, reg, _Ctl(vx=1.0))
        check("'Client Chassis Drive' 读不到 -> drive_armed 为假",
              out["drive_armed"] is False, str(out["drive_armed"]))

    with mgr(settings={**_DRIVE_ON, **_GIMBAL_ON},
             missing=("Client Gimbal Control",)) as (m, state, reg, link):
        out = m.tick_polar_link(state, reg, _Ctl())
        check("'Client Gimbal Control' 读不到 -> 仲裁停在 PASSIVE（不许默认 True）",
              m.polar_arbiter.state == "PASSIVE", m.polar_arbiter.state)
        check("'Client Gimbal Control' 读不到 -> 不下发任何云台角度",
              out["gimbal_target_deg"] is None, repr(out["gimbal_target_deg"]))
        check("'Client Gimbal Control' 读不到 -> state 上的授权标记为假",
              state.polar_gimbal_client is False, repr(state.polar_gimbal_client))

    with mgr(settings={**_DRIVE_ON, **_GIMBAL_ON},
             missing=("Vision NUC Occupied",)) as (m, state, reg, link):
        out = m.tick_polar_link(state, reg, _Ctl())
        check("'Vision NUC Occupied' 读不到 -> 按占用处理（不许默认 False）",
              m.polar_arbiter.state == "PASSIVE", m.polar_arbiter.state)
        check("'Vision NUC Occupied' 读不到 -> 理由是「让位」给视觉 NUC",
              "让位" in state.polar_authority_reason, state.polar_authority_reason)
        check("'Vision NUC Occupied' 读不到 -> 不下发任何云台角度",
              out["gimbal_target_deg"] is None, repr(out["gimbal_target_deg"]))


# ===========================================================================
# K. 云台下发闸门：六个条件缺一不可
# ===========================================================================
def test_gimbal_plan_gate() -> None:
    """``plan_gimbal`` 的六道闸：非急停 / 拿到权 / 链路活 / 标定过 / 上锁了 / 兵种允许。

    改错了现场看到什么：任何一道闸被拆掉，最直观的后果是**没标定也发云台** ——
    开机瞬间自瞄还没初始化，客户端就把 0° 甩出去，炮口对着谁就是谁。
    """

    base = {**_DRIVE_ON, **_GIMBAL_ON}
    cases = (
        ("全都满足（步兵 3，标定+上锁）", dict(robot_id=3), (8.0, 5.0)),
        ("没标定 vision_calibrated=False", dict(robot_id=3, cal=False), None),
        ("没上锁 target_locked=False", dict(robot_id=3, lock=False), None),
        ("哨兵 7 没有可控云台", dict(robot_id=7), None),
    )
    for label, kw, expect in cases:
        kw = dict(kw)
        cal, lock = kw.pop("cal", True), kw.pop("lock", True)
        with mgr(settings=base, **kw) as (m, state, reg, link):
            _aimed(state, calibrated=cal, locked=lock)
            out = m.tick_polar_link(state, reg, _Ctl())
            check(f"云台闸门：{label} -> gimbal_target_deg={expect!r}",
                  out["gimbal_target_deg"] == expect, repr(out["gimbal_target_deg"]))

    with mgr(settings=base, alive=False) as (m, state, reg, link):
        _aimed(state)
        out = m.tick_polar_link(state, reg, _Ctl())
        check("云台闸门：链路不在线 -> 不下发", out["gimbal_target_deg"] is None,
              repr(out["gimbal_target_deg"]))

    with mgr(settings=base) as (m, state, reg, link):
        _aimed(state)
        m.trigger_estop()
        out = m.tick_polar_link(state, reg, _Ctl())
        check("云台闸门：急停中 -> 不下发", out["gimbal_target_deg"] is None,
              repr(out["gimbal_target_deg"]))

    with mgr(settings={**_DRIVE_ON, "Vision NUC Occupied": False}) as (m, state, reg, link):
        _aimed(state)
        out = m.tick_polar_link(state, reg, _Ctl())
        check("云台闸门：没开 Client Gimbal Control -> 不下发",
              out["gimbal_target_deg"] is None, repr(out["gimbal_target_deg"]))

    with mgr(settings=base) as (m, state, reg, link):
        _aimed(state, yaw=20.0, pitch=5.0)
        m.tick_polar_link(state, reg, _Ctl())
        intent = m.polar._intent
        check("下发时 yaw 落在 plan[0]（不是 plan[1]）",
              math.degrees(intent.gimbal_yaw_rad) == 8.0,
              f"yaw={math.degrees(intent.gimbal_yaw_rad)}")
        check("下发时 pitch 落在 plan[1]（不是 plan[0]）",
              math.degrees(intent.gimbal_pitch_rad) == 5.0,
              f"pitch={math.degrees(intent.gimbal_pitch_rad)}")


# ===========================================================================
# L8/L9. 限幅要乘操作员的 Speed/Yaw Scale
# ===========================================================================
def test_drive_scale_percent_reaches_the_wire() -> None:
    """``Speed Scale %`` / ``Yaw Scale %`` 必须真的乘进限幅。

    改错了现场看到什么：操作员把限速拉到 10%（怕撞人），线上仍然是满速
    1.5 m/s；反过来当成 100% 也会让场上新人当场撞上场边的人。
    """

    for scale, expect_vx, expect_wz in ((100, 1.5, 2.0), (50, 0.75, 1.0),
                                         (10, 0.15, 0.2)):
        with mgr(settings={**_DRIVE_ON, "Speed Scale %": scale,
                           "Yaw Scale %": scale}, robot_id=3) as (m, state, reg, link):
            out = m.tick_polar_link(state, reg, _Ctl(vx=5.0, wz=5.0))
            intent = m.polar._intent
            check(f"限速 {scale}%：满推杆上线 vx={expect_vx} m/s",
                  out["drive_armed"] is True
                  and abs(intent.vx - expect_vx) < 1e-9,
                  f"drive_armed={out['drive_armed']} vx={intent.vx}")
            check(f"限速 {scale}%：满推杆上线 wz={expect_wz} rad/s",
                  abs(intent.wz - expect_wz) < 1e-9, f"wz={intent.wz}")


# ===========================================================================
# M. 无线键鼠出口（0x0311）诊断状态机
# ===========================================================================
def test_radio_km_link_state_machine() -> None:
    """0x0311 的五种链路状态必须互斥且都能到达。

    这是操作员判断"无线键鼠到底通没通"的**唯一**依据。状态判错时现场
    现象是：明明一个帧都没发出去，界面却说"车上已收到 N 帧"；或者明明
    在涨，却报"链路中途断了"——两种都会让人白跑一趟现场。
    """

    with mgr(settings={"Radio KM Egress": False}, dbg={"km_rx": 7.0}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("开关没开 -> 状态 off", ctl.radio_km_link_state == "off",
              ctl.radio_km_link_state)
        check("开关没开 -> 闸门原因为空（没要求过就不该弹提示）",
              ctl.radio_km_gate_reason == "", ctl.radio_km_gate_reason)

    with mgr(settings={"Radio KM Egress": True}, dbg={"km_rx": 7.0}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("开关开了但没授 Allow Drive -> 状态 blocked",
              ctl.radio_km_link_state == "blocked", ctl.radio_km_link_state)
        check("blocked 时闸门原因说清是 Allow Drive",
              "Allow Drive" in ctl.radio_km_gate_reason, ctl.radio_km_gate_reason)

    with mgr(settings=_RADIO_ON, dbg={}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("没插 USB 读不到计数 -> no-readback（不许当成「一个都没收到」）",
              ctl.radio_km_link_state == "no-readback", ctl.radio_km_link_state)
        check("读不到计数时上报值是 -1（未知），不是 0（一帧都没收到）",
              ctl.radio_km_robot_rx == -1, repr(ctl.radio_km_robot_rx))
        check("读不到键位时上报值是 -1",
              ctl.radio_km_robot_key == -1, repr(ctl.radio_km_robot_key))

    with mgr(settings=_RADIO_ON, dbg={"km_rx": "abc"}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("计数是垃圾值时按「读不到」处理，不是当成 0",
              ctl.radio_km_link_state == "no-readback", ctl.radio_km_link_state)

    with mgr(settings=_RADIO_ON, dbg={"km_rx": 0.0}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("计数确实是 0 -> silent（车上一个帧都没收到）",
              ctl.radio_km_link_state == "silent", ctl.radio_km_link_state)
        check("silent 的提示要说清 921600 那一路本仓库没有",
              "921600" in ctl.radio_km_link_note, ctl.radio_km_link_note[:60])

    with mgr(settings=_RADIO_ON, dbg={"km_rx": 5.0}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        m.tick_polar_link(s, r, ctl)
        check("计数停着不动 -> stalled（不是 silent，也不是 live）",
              ctl.radio_km_link_state == "stalled", ctl.radio_km_link_state)
        s0 = ctl.radio_km_link_state
        m.polar_bridge.debug_packages["km_rx"] = 8.0
        m.tick_polar_link(s, r, ctl)
        check("计数从 5 涨到 8 -> live", ctl.radio_km_link_state == "live",
              ctl.radio_km_link_state)
        check("涨了之后 growing 标记为真", ctl.radio_km_robot_rx_growing is True,
              repr(ctl.radio_km_robot_rx_growing))
        m.polar_bridge.debug_packages["km_rx"] = 2.0
        m.tick_polar_link(s, r, ctl)
        check("计数倒着走不算增长（`rx_now > prev_rx`）",
              ctl.radio_km_robot_rx_growing is False,
              repr(ctl.radio_km_robot_rx_growing))
        check("计数倒着走时状态退回 stalled，不是 live",
              ctl.radio_km_link_state == "stalled", ctl.radio_km_link_state)
        del m.polar_bridge.debug_packages["km_rx"]
        m.tick_polar_link(s, r, ctl)
        check("回读整个消失 -> 回到 no-readback（不是把上一帧的数当现在）",
              ctl.radio_km_link_state == "no-readback", ctl.radio_km_link_state)
        check("回读消失时上报值回到 -1", ctl.radio_km_robot_rx == -1,
              repr(ctl.radio_km_robot_rx))
        check(f"状态机各态互斥（起点 {s0!r}，其余各态都走到过）",
              {s0, ctl.radio_km_link_state} >= {"stalled", "no-readback"})


def test_radio_km_live_note_separates_frames_from_policy() -> None:
    """"帧到了"和"这一档会用它的键位"必须分开说。

    改错了现场看到什么：默认档 CL_KM_RC 下计数照涨但键位永不参与，
    只报"已收到 N 帧"就是在骗人 —— 操作员会以为可以放手了，结果车没反应。
    """

    for pol, expect_uses in ((1, True), (3, False)):
        with mgr(settings=_RADIO_ON,
                 dbg={"km_rx": 4.0, "km_pol": float(pol)}) as (m, s, r, lk):
            ctl = _Ctl()
            m.tick_polar_link(s, r, ctl)
            m.polar_bridge.debug_packages["km_rx"] = 9.0
            m.tick_polar_link(s, r, ctl)
            name = {1: "rc", 3: "rc_vt"}[pol]
            check(f"km_pol={pol} -> 档位名翻译成 {name}",
                  ctl.radio_km_policy == name, ctl.radio_km_policy)
            if expect_uses:
                check(f"km_pol={pol} 档位不参与 -> live 提示必须说「不参与」",
                      "不参与" in ctl.radio_km_link_note, ctl.radio_km_link_note)
            else:
                check(f"km_pol={pol} 档位参与 -> live 提示不得说「不参与」",
                      "不参与" not in ctl.radio_km_link_note, ctl.radio_km_link_note)


def test_radio_km_gate_reason_three_causes() -> None:
    """闸门原因必须区分三种成因：急停 / 模块停用 / 没授权。

    改错了现场看到什么：三条原因互相串了，操作员按提示去勾了一个不相关
    的开关（比如模块已经停用却让他去开 Allow Drive），白跑一次。
    """

    with mgr(settings=_RADIO_ON, dbg={"km_rx": 3.0}) as (m, s, r, lk):
        ctl = _Ctl()
        m.trigger_estop()
        m.tick_polar_link(s, r, ctl)
        check("急停 -> 出口被关", ctl.radio_km_enabled is False,
              repr(ctl.radio_km_enabled))
        check("急停 -> 闸门原因指向急停",
              ctl.radio_km_gate_reason == "无线键鼠出口未生效：急停激活",
              ctl.radio_km_gate_reason)

    with mgr(settings={"Radio KM Egress": True}) as (m, s, r, lk):
        ctl = _Ctl()
        lk.enabled = False
        m.tick_polar_link(s, r, ctl)
        check("模块停用 -> 闸门原因指向模块",
              ctl.radio_km_gate_reason == "无线键鼠出口未生效：模块 RobotLink 未启用",
              ctl.radio_km_gate_reason)

    with mgr(settings=_RADIO_ON, dbg={"km_rx": 3.0}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("三条闸全过 -> 闸门原因被清空（不弹没用的提示）",
              ctl.radio_km_gate_reason == "", ctl.radio_km_gate_reason)


# ===========================================================================
# N/O. 直连 DT7 与自定义控制器出口
# ===========================================================================
def test_dt7_key_mask_follows_explicit_switches() -> None:
    """DT7 键位掩码按显式开关拼；开火键默认**不给**。

    改错了现场看到什么：SHIFT 被无条件放行，操作员以为没授权就不会有人
    切底盘模式；Q/E 被无条件放行，则是客户端在操作员明确不许的情况下
    替人开了摩擦轮 —— 那是能打伤人的那条。
    """

    for extra, expect, why in (
        ({}, DT7_DEFAULT_KEY_MASK, "两个开关都关：只留驾驶四键"),
        ({"Allow Spin Key": True}, DT7_DEFAULT_KEY_MASK | DT7_SPIN_KEY_MASK,
         "显式开 SHIFT 才给"),
        ({"DT7 Allow Fire Keys": True}, DT7_DEFAULT_KEY_MASK | DT7_FIRE_KEY_MASK,
         "显式开火键才给 Q/E"),
        ({"Allow Spin Key": True, "DT7 Allow Fire Keys": True},
         DT7_DEFAULT_KEY_MASK | DT7_SPIN_KEY_MASK | DT7_FIRE_KEY_MASK, "两个都开"),
    ):
        with mgr(settings={**_DT7_ON, **extra}) as (m, s, r, lk):
            ctl = _Ctl()
            m.tick_polar_link(s, r, ctl)
            check(f"DT7 键位掩码 {why} -> {bin(expect)}",
                  ctl.dt7_key_mask == expect, bin(ctl.dt7_key_mask))
    check("默认掩码确实不含开火位（Q/E）", not (DT7_DEFAULT_KEY_MASK & DT7_FIRE_KEY_MASK),
          bin(DT7_DEFAULT_KEY_MASK))
    check("默认掩码确实不含 SHIFT", not (DT7_DEFAULT_KEY_MASK & DT7_SPIN_KEY_MASK),
          bin(DT7_DEFAULT_KEY_MASK))


def test_dt7_real_tx_rebuilds_the_link() -> None:
    """勾/取消 ``DT7 Real TX`` 必须重建发送器并翻转 ``mock_output``。

    改错了现场看到什么：勾了"真发"仍然只在内存里造帧，操作员看到出口
    "已启用"却什么都没上线；而真发后不重建，则会拿着 mock 句柄去写真串口。
    """

    with mgr(settings={**_DT7_ON, "DT7 Real TX": False}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        first = ctl.dt7_link
        check("未勾真发 -> 发送器是 mock", first.mock_output is True,
              str(first.mock_output))
        lk.get_setting("DT7 Real TX").value = True
        m.tick_polar_link(s, r, ctl)
        check("勾上真发 -> 发送器被重建（子句 `link.mock_output == real_tx`）",
              ctl.dt7_link is not first, "还是同一个对象")
        check("勾上真发 -> mock_output 翻成 False",
              ctl.dt7_link.mock_output is False, str(ctl.dt7_link.mock_output))
        lk.get_setting("DT7 Real TX").value = False
        m.tick_polar_link(s, r, ctl)
        check("取消真发 -> 再次重建并翻回 mock",
              ctl.dt7_link.mock_output is True, str(ctl.dt7_link.mock_output))


def test_dt7_release_frames_on_every_deactivation_path() -> None:
    """DT7 侧没有"过期自动清零"，所以**每一条**降级路都必须补一帧松手。

    改错了现场看到什么：DT7 那侧的键会一直粘在车上，客户端进程一没，
    驾驶员立刻把 s[0] 拨下档锁底盘 —— 这是三处降级里最危险的一处。
    """

    with mgr(settings=_DT7_ON) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("闸全开时 DT7 已启用", ctl.dt7_enabled is True, repr(ctl.dt7_enabled))
        check("闸全开时不发松手帧（无谓的松手会被固件当故障）",
              ctl.dt7_releases == 0, str(ctl.dt7_releases))
        check("闸全开时没有报错文案", ctl.dt7_last_error == "", ctl.dt7_last_error)

        m.trigger_estop()
        m.tick_polar_link(s, r, ctl)
        check("急停 -> DT7 被关", ctl.dt7_enabled is False, repr(ctl.dt7_enabled))
        check("急停 -> 补一帧松手（子句 `_dt7_was_enabled and not dt7_live`）",
              ctl.dt7_releases == 1, str(ctl.dt7_releases))
        check("未生效时必须说清原因",
              "未生效" in ctl.dt7_last_error, ctl.dt7_last_error)

        m.release_estop()
        m.tick_polar_link(s, r, ctl)
        check("解除急停 -> 重新启用", ctl.dt7_enabled is True, repr(ctl.dt7_enabled))
        check("重新启用不再补松手帧", ctl.dt7_releases == 1, str(ctl.dt7_releases))

        lk.get_setting("Direct DT7 Egress").value = False
        m.tick_polar_link(s, r, ctl)
        check("关掉出口开关 -> 也必须补一帧松手（否则键留在车上）",
              ctl.dt7_releases == 2, str(ctl.dt7_releases))
        check("关掉出口开关 -> dt7_enabled 落回 False",
              ctl.dt7_enabled is False, repr(ctl.dt7_enabled))

    with mgr(settings={"Direct DT7 Egress": True, "Allow Drive": False}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("没授权时 DT7 不启用", ctl.dt7_enabled is False, repr(ctl.dt7_enabled))
        check("没授权时说清未生效", "未生效" in ctl.dt7_last_error, ctl.dt7_last_error)
        check("从没启用过时不该发松手帧（没有键粘在车上）",
              ctl.dt7_releases == 0, str(ctl.dt7_releases))


def test_custom_controller_mask_and_release() -> None:
    """自定义控制器出口（0x0306）的键位掩码与急停松手。

    改错了现场看到什么：急停只把"出口开关"关掉却不给选手端发全零帧，
    选手端那侧仍带着最后一帧的按键，官方选手端不会自己归零。
    """

    with mgr(settings=_CC_ON) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("CC 闸全开时默认只透传 W/S/A/D",
              ctl.cc_key_mask == 0x0F, bin(ctl.cc_key_mask))
        check("CC 闸全开时已启用", ctl.cc_enabled is True, repr(ctl.cc_enabled))
        lk.get_setting("Allow Spin Key").value = True
        m.tick_polar_link(s, r, ctl)
        check("显式开 SHIFT 才给 CC 加 0x10",
              ctl.cc_key_mask == 0x1F, bin(ctl.cc_key_mask))
        lk.get_setting("Allow Spin Key").value = False
        m.tick_polar_link(s, r, ctl)
        check("收回 SHIFT 授权后掩码跟着收回",
              ctl.cc_key_mask == 0x0F, bin(ctl.cc_key_mask))

    with mgr(settings=_CC_ON) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        m.trigger_estop()
        m.tick_polar_link(s, r, ctl)
        check("急停时给选手端发一帧全零（子句 `_cc_was_enabled`）",
              ctl.cc_releases == 1, str(ctl.cc_releases))
        check("急停时 CC 被关", ctl.cc_enabled is False, repr(ctl.cc_enabled))
        check("急停原因写在 release 之后（否则会被 release 抹掉）",
              ctl.cc_last_error == "自定义控制器出口未生效：急停激活",
              ctl.cc_last_error)

    with mgr(settings=_CC_ON) as (m, s, r, lk):
        ctl = _Ctl()
        m.trigger_estop()          # 先急停，CC 从未启用过
        m.tick_polar_link(s, r, ctl)
        check("从没启用过时急停不发 release（没有键粘在车上）",
              ctl.cc_releases == 0, str(ctl.cc_releases))
        check("没启用时 cc_last_error 说清是急停",
              ctl.cc_last_error == "自定义控制器出口未生效：急停激活",
              ctl.cc_last_error)

    with mgr(settings=_CC_ON) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        lk.get_setting("Allow Drive").value = False
        m.tick_polar_link(s, r, ctl)
        check("撤 Allow Drive -> CC 被关", ctl.cc_enabled is False,
              repr(ctl.cc_enabled))
        check("撤 Allow Drive -> 原因指向 Allow Drive",
              ctl.cc_last_error == "自定义控制器出口未生效：未授予 Allow Drive",
              ctl.cc_last_error)
        lk.enabled = False
        m.tick_polar_link(s, r, ctl)
        check("模块停用 -> 原因指向模块（与急停/未授权区分开）",
              ctl.cc_last_error == "自定义控制器出口未生效：模块 RobotLink 未启用",
              ctl.cc_last_error)

    with mgr(settings=_CC_ON) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        lk.get_setting("Custom Controller Egress").value = False
        m.tick_polar_link(s, r, ctl)
        check("关掉 CC 出口开关 -> cc_enabled 落回 False",
              ctl.cc_enabled is False, repr(ctl.cc_enabled))


# ===========================================================================
# P. 自转指令（0x04）与 0x1F 回读
# ===========================================================================
def test_spin_legacy_readback_key_is_consulted() -> None:
    """下行 0x1F 的旧键名 ``spin_active`` 也必须被读。

    改错了现场只能把 0x04 关到 1 秒然后放手，客户端从此**永久无法关闭**
    驾驶员用遥控器开起来的自转 —— 而且界面上看不出为什么。
    """

    with mgr(settings={"Allow Spin Key": False}, overlay={"spin_active": 1}) as (m, s, r, lk):
        m._spin_ever_on = True                      # 操作员之前要求过开
        m._spin_off_since = time.perf_counter() - 99  # 远超 SPIN_OFF_ASSERT_S
        m.tick_polar_link(s, r, _Ctl())
        check("旧键名 spin_active=1 -> 继续压着发 OFF（子句 `spin_reported is None`）",
              m._spin_assert == SPIN_CMD_OFF, str(m._spin_assert))
        check("读到回读就重置 OFF 计时（`_spin_off_since = 0.0`）",
              m._spin_off_since == 0.0, str(m._spin_off_since))
        check("读到「还在转」时不交还控制权", m._spin_ever_on is True,
              repr(m._spin_ever_on))

    with mgr(settings={"Allow Spin Key": False},
             overlay={"polar_spin_active": 1, "spin_active": 0}) as (m, s, r, lk):
        m._spin_ever_on = True
        m._spin_off_since = time.perf_counter() - 99
        m.tick_polar_link(s, r, _Ctl())
        check("新键名优先于旧键名（polar=1, legacy=0）",
              m._spin_assert == SPIN_CMD_OFF, str(m._spin_assert))

    with mgr(settings={"Allow Spin Key": False}, overlay={"spin_active": 0}) as (m, s, r, lk):
        m._spin_ever_on = True
        m._spin_off_since = time.perf_counter() - 99
        m.tick_polar_link(s, r, _Ctl())
        check("回读确认已停 -> 交还控制权（发 NONE）",
              m._spin_assert == SPIN_CMD_NONE, str(m._spin_assert))
        check("交还后不再压着自转", m._spin_ever_on is False, repr(m._spin_ever_on))


def test_spin_note_is_edge_triggered_and_logged() -> None:
    """自转没发出去的解释只在**第一次出现/内容变了**时记一笔。

    改错了现场看到什么：50 Hz 重算，每次都记一行，一秒刷 50 行，
    field_log 几秒就被冲掉，真正的故障现场反而找不到了。
    """

    with mgr(alive=False, settings={"Allow Spin Key": True}) as (m, s, r, lk):
        emitted = []
        m.field_log.emit = lambda *a, **k: emitted.append(a)
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("链路断 + 想要自转 -> 给出人话原因", bool(s.polar_spin_note), repr(s.polar_spin_note))
        check("原因里说清 0x04 走不出去", "0x04" in s.polar_spin_note, s.polar_spin_note[:50])
        check("第一次出现就记进 field_log", len(emitted) == 1, str(len(emitted)))
        check("事件名是 spin_command_blocked",
              emitted and emitted[0][0] == "spin_command_blocked", str(emitted[:1]))
        m.tick_polar_link(s, r, ctl)
        m.tick_polar_link(s, r, ctl)
        check("原因没变就不重复记（边沿触发，不是每帧）",
              len(emitted) == 1, f"3 帧记了 {len(emitted)} 次")

    with mgr(alive=True, settings={"Allow Spin Key": True}) as (m, s, r, lk):
        m.tick_polar_link(s, r, _Ctl())
        check("链路通着时不弹提示（0x04 真发出去了，没资格弹）",
              s.polar_spin_note == "", repr(s.polar_spin_note))

    with mgr(alive=False, settings={"Allow Spin Key": False}) as (m, s, r, lk):
        emitted = []
        m.field_log.emit = lambda *a, **k: emitted.append(a)
        m.tick_polar_link(s, r, _Ctl())
        check("没要求过自转就不弹提示", s.polar_spin_note == "", repr(s.polar_spin_note))
        check("没要求过自转就不记 field_log", not emitted, str(len(emitted)))


# ===========================================================================
# Q. DT7 鼠标与云台直控请求的固件仲裁提示
# ===========================================================================
def test_dt7_mouse_note_only_when_both_true() -> None:
    """这条提示必须**同时**满足两个条件才出现，且用本拍算出的请求位。

    改错了现场看到什么：用 `or` 的话，DT7 没开但云台直控开着时也会弹提示，
    操作员去查一条根本没在跑的出口；反过来，提示残留一拍就会让操作员
    以为鼠标还在被固件抢走。
    """

    with mgr(settings={**_DT7_ON, **_GIMBAL_ON}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("DT7 开着 + 云台直控请求 -> 给出仲裁提示",
              "AUTO_AIM" in ctl.dt7_mouse_note, repr(ctl.dt7_mouse_note))

    with mgr(settings={**_DT7_ON, "Client Gimbal Control": False}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("DT7 开着但无云台直控请求 -> 不提示（子句是 and 不是 or）",
              ctl.dt7_mouse_note == "", repr(ctl.dt7_mouse_note))

    with mgr(settings={"Direct DT7 Egress": False, **_GIMBAL_ON}) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("有云台直控请求但 DT7 没开 -> 不提示",
              ctl.dt7_mouse_note == "", repr(ctl.dt7_mouse_note))

    with mgr(settings={**_DT7_ON, **_GIMBAL_ON}, alive=False) as (m, s, r, lk):
        ctl = _Ctl()
        m.tick_polar_link(s, r, ctl)
        check("链路断（无云台直控请求）-> 不提示，无残影",
              ctl.dt7_mouse_note == "", repr(ctl.dt7_mouse_note))


# ===========================================================================
# R/Q2. 每帧提交的意图本体
# ===========================================================================
def test_submitted_intent_fields() -> None:
    """``PolarDriveIntent`` 里那几个**恒定/条件**字段不能被改坏。

    改错了现场看到什么：``allow_spin_key`` 一旦变成 True，0x03 又开始注入
    SHIFT 边沿，而固件对 SHIFT 是边沿翻转 —— 客户端只能开、关不掉，
    再开一次反而关掉；``friction_on`` 变 True 则是在没人开火时替人开了
    摩擦轮。两者都是"车自己动了"。
    """

    with mgr(settings={**_DRIVE_ON, **_GIMBAL_ON}) as (m, s, r, lk):
        _aimed(s)
        m.tick_polar_link(s, r, _Ctl())
        intent = m.polar._intent
        check("有云台 plan 时 tracking 为真", intent.tracking is True,
              repr(intent.tracking))
        check("friction_on 恒为 False（客户端没有摩擦轮权限）",
              intent.friction_on is False, repr(intent.friction_on))
        check("allow_spin_key 恒为 False（自转已改走 0x04，不许再注入 SHIFT）",
              intent.allow_spin_key is False, repr(intent.allow_spin_key))

    with mgr(settings={"Client Gimbal Control": False}) as (m, s, r, lk):
        _aimed(s)
        m.tick_polar_link(s, r, _Ctl())
        intent = m.polar._intent
        check("没有云台 plan 时 tracking 为假", intent.tracking is False,
              repr(intent.tracking))
        check("没有云台 plan 时角度归零（发零帧而不是甩炮）",
              intent.gimbal_yaw_rad == 0.0 and intent.gimbal_pitch_rad == 0.0,
              f"yaw={intent.gimbal_yaw_rad} pitch={intent.gimbal_pitch_rad}")


def test_key_value_zero_unless_drive_armed() -> None:
    """``key_value`` 只在驾驶已授权时透传。

    改错了现场看到什么：驾驶没授权（甚至刚急停完）却把 W/A/S/D 位图发上
    去，固件会按斜坡驱动底盘 —— 没有人推杆，车自己在走。
    """

    with mgr(settings=_DRIVE_ON) as (m, s, r, lk):
        m.tick_polar_link(s, r, _Ctl(keyboard_value=0b1011))
        check("驾驶已授权时键位照传",
              m.polar._intent.key_value == 0b1011, bin(m.polar._intent.key_value))

    with mgr(settings={**_DRIVE_ON, "Client Chassis Drive": False}) as (m, s, r, lk):
        m.tick_polar_link(s, r, _Ctl(keyboard_value=0b1011))
        check("底盘出口没开时键位必须清零（子句 `if drive_armed else 0`）",
              m.polar._intent.key_value == 0, bin(m.polar._intent.key_value))

    with mgr(settings={"Allow Drive": False, "Client Chassis Drive": True}) as (m, s, r, lk):
        m.tick_polar_link(s, r, _Ctl(keyboard_value=0b1011))
        check("没授 Allow Drive 时键位必须清零",
              m.polar._intent.key_value == 0, bin(m.polar._intent.key_value))


# ===========================================================================
# S. 返回给 HUD 的诊断字段
# ===========================================================================
def test_returned_authority_and_role_fields() -> None:
    """返回值里的急停标记与兵种限速必须逐位忠实。

    改错了现场看到什么：急停时 ``authority`` 报的是仲裁器状态（PASSIVE），
    操作员看到"云台被动、无人接管"就以为没锁零；而 ``role_max_*_mps``
    四舍五入成整数，1.5 变 2.0，兵种限速在诊断里直接超发 33%。
    """

    with mgr(settings=_DRIVE_ON, robot_id=3) as (m, s, r, lk):
        out = m.tick_polar_link(s, r, _Ctl())
        check("无急停时 authority 取仲裁器状态",
              out["authority"] == m.polar_arbiter.state, out["authority"])
        check("无急停时 polar_authority_state 同步",
              s.polar_authority_state == m.polar_arbiter.state,
              s.polar_authority_state)
        check("无急停时理由取仲裁器理由",
              s.polar_authority_reason == "客户端云台控制未开启",
              s.polar_authority_reason)
        check("步兵线速度原样带出 1.5（不是 round(...,0) 的 2.0）",
              out["role_max_linear_mps"] == 1.5, repr(out["role_max_linear_mps"]))
        check("步兵角速度原样带出 2.0",
              out["role_max_yaw_rps"] == 2.0, repr(out["role_max_yaw_rps"]))
        check("返回里标明按的是哪台车（上报优先）",
              out["role_id"] == 3 and out["role_label"] == "步兵1",
              f"{out['role_id']}/{out['role_label']}")

        m.trigger_estop()
        out2 = m.tick_polar_link(s, r, _Ctl())
        check("急停时 authority 必须是 ESTOP（子句 `if not estopped else 'ESTOP'`）",
              out2["authority"] == "ESTOP", out2["authority"])
        check("急停时 estopped 标记为真", out2["estopped"] is True,
              repr(out2["estopped"]))
        check("急停时 polar_authority_state 落在 state 上给 HUD",
              s.polar_authority_state == "ESTOP", s.polar_authority_state)
        check("急停时 polar_authority_reason 给出人话原因",
              s.polar_authority_reason == "急停激活，队内链路控制已锁零",
              s.polar_authority_reason)
        check("急停不提前 return：诊断字段仍然被刷新",
              "polar_link" in s.backend_stats, str(sorted(s.backend_stats)[:6]))

    with mgr(robot_id=7) as (m, s, r, lk):
        out = m.tick_polar_link(s, r, _Ctl())
        check("哨兵的云台禁控原因随返回值带出（不是空串）",
              bool(out["role_gimbal_block"]), repr(out["role_gimbal_block"]))
        check("哨兵线速度为 0（固件参数就是 0，不是默认 1.5）",
              out["role_max_linear_mps"] == 0.0, repr(out["role_max_linear_mps"]))


def test_returned_limits_match_the_role_table_exactly() -> None:
    """返回值里的限速必须与 ``robot_roles`` 表**逐位相同**（不加不减不舍入）。

    ⚠ 诚实说明：``max_linear_mps`` 里有 1.5 这种非整数值，所以这条
    今天能区分 ``round(x, 3)`` 与 ``round(x, 0)``；而 ``max_yaw_rps``
    在**当前**表里只有 2.0 与 0.0，全是整数 —— 所以针对 yaw 的那半条
    变异（把 3 位改成 0 位）今天在数学上就区分不出来。这里仍然钉住
    精确相等，让它在表里出现非整数角速度的那天立刻变红。
    """

    for rid, role in sorted(robot_roles.ROLE_LIMITS_BY_ID.items()):
        check(f"兵种 {rid}（{role.label}）线速度逐位带出 {role.max_linear_mps}",
              round(role.max_linear_mps, 3) == role.max_linear_mps,
              repr(role.max_linear_mps))
    # 这条是给上面那批断言的"防退化"检查：表里必须真的存在一个
    # round(x,3) 与 round(x,0) 不同的值，否则它们就退化成恒绿的恒真式。
    distinguishing_lin = [r for r in robot_roles.ROLE_LIMITS_BY_ID.values()
                          if round(r.max_linear_mps, 3) != round(r.max_linear_mps, 0)]
    check("表里存在 round(...,3) 与 round(...,0) 不同的线速度（否则上面会退化成恒绿）",
          bool(distinguishing_lin),
          str([r.max_linear_mps for r in distinguishing_lin]))

    for rid, role in sorted(robot_roles.ROLE_LIMITS_BY_ID.items()):
        check(f"兵种 {rid}（{role.label}）角速度逐位带出 {role.max_yaw_rps}",
              round(role.max_yaw_rps, 3) == role.max_yaw_rps,
              repr(role.max_yaw_rps))
    distinguishing_yaw = [r for r in robot_roles.ROLE_LIMITS_BY_ID.values()
                          if round(r.max_yaw_rps, 3) != round(r.max_yaw_rps, 0)]
    print(f"[INFO] 表里 round(...,3) 与 round(...,0) 不同的角速度兵种："
          f"{len(distinguishing_yaw)} 个"
          f"（0 个 => yaw 那半条变异今天区分不出来，本文件不假装守住）")


# ===========================================================================
# 说明：两条区分不出来的变异，单独写出来不假装守住
# ===========================================================================
def test_known_undistinguishable_mutants() -> None:
    """两条"没有行为断言能抓住"的变异，只用**源码文本钉子**兜住，如实说明。

    这两条在全量套件里曾经零覆盖，性质却和别的零覆盖分支不同，必须讲清楚，
    否则下一个人会把它们算进"有网"：

    * ``F3``（Gimbal Slew 的 ``!=`` 短路被去掉）—— **等价变异**。
      原写法与去掉 ``!=`` 之后在数值上完全等价：值在区间内时赋值幂等，
      超界时两边都赋同一个夹紧值。它**根本没有可观测差异**，
      所以任何行为断言都抓不住它，只能把源码里那半句钉住。
    * ``S3``（``role_max_yaw_rps`` 的 ``round(..., 3)`` 改成 ``round(..., 0)``）
      —— **今天不可区分**：``robot_roles`` 表里所有兵种的角速度都是
      2.0 或 0.0，数学上 ``round(2.0, 3) == round(2.0, 0)``。

    ⚠ 这两条钉子是**文本匹配**：任何等价重构都会让它们假红，而它们也只防
    "整句被删"、防不住"语义等价的改写"。所以它们是文档级别的护栏，
    本文件**不**把它们算作行为覆盖。
    """

    src = open(os.path.join(ROOT, "rm_client", "backend", "manager.py"),
               encoding="utf-8").read()
    check("Gimbal Slew 的短路判断还在源码里（等价变异只能这样钉）",
          "and self.polar_arbiter.slew != float(slew_setting.value)" in src,
          "短路判断不见了")
    check("role_max_yaw_rps 的 3 位精度还在源码里",
          '"role_max_yaw_rps": round(role.max_yaw_rps, 3),' in src,
          "精度从 3 位变成了别的")


def main() -> int:
    tests = [
        test_state_fallback_and_early_return,
        test_link_age_is_capped,
        test_port_only_acted_on_when_explicitly_changed,
        test_baud_precedence_and_range,
        test_gimbal_slew_is_clamped,
        test_link_mode_validation_and_bridge_host_guard,
        test_bridge_port_default,
        test_drive_channel_normalisation,
        test_missing_settings_fail_closed,
        test_gimbal_plan_gate,
        test_drive_scale_percent_reaches_the_wire,
        test_radio_km_link_state_machine,
        test_radio_km_live_note_separates_frames_from_policy,
        test_radio_km_gate_reason_three_causes,
        test_dt7_key_mask_follows_explicit_switches,
        test_dt7_real_tx_rebuilds_the_link,
        test_dt7_release_frames_on_every_deactivation_path,
        test_custom_controller_mask_and_release,
        test_spin_legacy_readback_key_is_consulted,
        test_spin_note_is_edge_triggered_and_logged,
        test_dt7_mouse_note_only_when_both_true,
        test_submitted_intent_fields,
        test_key_value_zero_unless_drive_armed,
        test_returned_authority_and_role_fields,
        test_returned_limits_match_the_role_table_exactly,
        test_known_undistinguishable_mutants,
    ]
    for fn in tests:
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\n{'FAILED' if FAILED else 'PASSED'}: {PASSED} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  [FAIL] {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
