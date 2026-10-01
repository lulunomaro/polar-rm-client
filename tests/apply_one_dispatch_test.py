"""`_apply_one` 分发表的契约，以及变异验证挖出来的 **15 个零覆盖分支**。

## 为什么有这份文件

`_apply_one` 以前是 376 行的 if/elif 链，25 个模块名分支。改成
`_APPLY_ONE_DISPATCH` 分发表之后，逐个分支做变异验证（把新实现里那一段
改坏一行，再跑全量套件）发现：

* 9 个分支改坏了会被抓红；
* **15 个分支改坏了，全量套件一条都不红**（见下面的分支清单）。

15/25 = 六成分支零覆盖，是本轮最有价值的产出：这些分支落错字段时**不会
报错**，只会让 HUD 上某个数字悄悄不对。所以这里给每个零覆盖分支补断言。

补断言时踩到一个必须说清楚的东西：`data.get(k, default)` 的 default 有
**两种约定**，仓库里两种都在用，测试必须分别按真实行为断言：

* `data.get(k, state.k)` —— 保持现值（GameStatus / GlobalLogisticsStatus /
  RobotDynamicStatus / RobotRespawnStatus ...）。把 default 写成 0 是**变异**。
* `data.get(k, 0)` / `data.get(k, "")` —— 按缺省复位（AirSupportStatusSync /
  Event ...）。把它改成"保持现值"同样是变异，但方向相反。

所以每条断言都写成"喂真实报文字段要对"+"喂空报文行为要对"，两边都钉死。

同时把分发表本身的契约钉住：模块名齐全、处理函数都返回 bool、表项顺序
不承载语义、未知模块名不改状态、未选中的原生流提前退出（不走尾声）。

    python tests/apply_one_dispatch_test.py
"""

from __future__ import annotations

import contextlib
import os
import random
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["POLAR_USER_DATA_ROOT"] = tempfile.mkdtemp(prefix="polar_apply_one_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rm_client.backend import manager as mgr_mod                     # noqa: E402
from rm_client.backend.manager import BackendManager                 # noqa: E402
from rm_client.model import RobotState                               # noqa: E402

FAILED: list[str] = []

#: 分发表必须收录的模块名。顺序取自重构前那条 if/elif 链的**源码顺序**，
#: 用来证明重构没有悄悄改掉任何一条分支的归属。
EXPECTED_MODULES = (
    "GameStatus", "GlobalUnitStatus", "GlobalLogisticsStatus", "RobotStaticStatus",
    "RobotDynamicStatus", "RobotModuleStatus", "RobotPosition", "Buff", "PenaltyInfo",
    "RobotPathPlanInfo", "MapClickInfo", "RadarInfoToClient",
    "TechCoreMotionStateSync", "RobotPerformanceSelectionSync", "DeployModeStatusSync",
    "RuneStatusSync", "SentryStatusSync", "DartSelectTargetStatusSync",
    "SentryCtrlResult", "AirSupportStatusSync", "GlobalSpecialMechanism", "Event",
    "CustomByteBlock", "RobotInjuryStat", "RobotRespawnStatus",
)


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILED.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


@contextlib.contextmanager
def manager_and_state(**kwargs):
    state = RobotState()
    mgr = BackendManager(state=state, client_id="3",
                         scenario_key=kwargs.pop("scenario", "OfficialEvent"), **kwargs)
    try:
        yield mgr, state
    finally:
        mgr.shutdown()


def dirty(state: RobotState) -> None:
    """把 state 弄成"每个字段都非默认"，这样"缺省值写成 0"立刻看得见。"""
    state.current_round = 11
    state.total_rounds = 22
    state.red_score = 33
    state.blue_score = 44
    state.stage_countdown_sec = 77
    state.stage_elapsed_sec = 88
    state.remaining_economy = 555
    state.total_economy_obtained = 666
    state.tech_level = 7
    state.encryption_level = 8
    state.current_health = 333
    state.current_heat = 99.5
    state.current_chassis_energy = 42
    state.current_buffer_energy = 43
    state.total_projectiles_fired = 1234
    state.remaining_ammo = 44
    state.out_of_combat = False
    state.can_remote_heal = False
    state.can_remote_ammo = False
    state.respawn_pending = False
    state.respawn_can_free = True
    state.respawn_gold_cost = 250
    state.respawn_can_pay = False
    state.air_support_free_time_sec = 60
    state.air_support_cost_coins = 300
    state.economy_status_revision = 5
    state.economy_command_status = "pending-buy"


# ===========================================================================
# A. 分发表自身的契约
# ===========================================================================
def test_dispatch_table_shape() -> None:
    table = mgr_mod._APPLY_ONE_DISPATCH
    check("分发表收录全部 25 个模块名",
          set(table) == set(EXPECTED_MODULES),
          f"缺={sorted(set(EXPECTED_MODULES) - set(table))} "
          f"多={sorted(set(table) - set(EXPECTED_MODULES))}")
    check("表项顺序与重构前的 if/elif 链一致（没有分支被挪走或漏掉）",
          tuple(table) == EXPECTED_MODULES)
    check("每个表项都指向一个真实存在的处理函数",
          all(callable(f) for f in table.values()))


def test_every_handler_returns_bool() -> None:
    """`_apply_one` 用返回值决定走不走尾声，所以每个处理函数都必须返回 bool。

    处理函数写成裸 `return`（返回 None）会让"提前退出"静默失效 —— 尾声
    照跑，而现象是经济确认被莫名触发。这条把"每个出口都返回值"钉成断言。
    """
    bad: list[str] = []
    for module, func in mgr_mod._APPLY_ONE_DISPATCH.items():
        with manager_and_state() as (mgr, state):
            results = set()
            for payload in ({}, {"__present__": [4], "robot_id": 3}):
                try:
                    results.add(func(mgr, state, dict(payload)))
                except Exception:                       # noqa: BLE001
                    results.add("raised")
            if not all(isinstance(r, bool) for r in results):
                bad.append(f"{module}->{func.__name__}:{results}")
    check("每个处理函数都返回 bool（含所有出口）", not bad, str(bad))


def test_unknown_module_is_a_noop() -> None:
    with manager_and_state() as (mgr, state):
        dirty(state)
        before = (state.economy_status_revision, state.backend_stats.copy())
        mgr._apply_one(state, "SomeModuleFromTheFuture", {"current_round": 999})
        check("未知模块名不改任何状态字段",
              state.current_round == 11 and state.backend_stats == before[1])
        check("未知模块名不走经济确认尾声（签名没变）",
              state.economy_status_revision == before[0])


def test_order_is_not_semantics() -> None:
    """**可执行地**证明表项顺序不承载语义，而不是"应该没关系"。

    做法：把同一批消息在原顺序表和随机置乱顺序表下各跑一遍，逐字段比对
    落地的 RobotState。25 次置乱全部一致，才算证明完。
    """
    messages = [
        ("GameStatus", {"current_round": 2, "current_stage": 4}),
        ("GlobalUnitStatus", {"base_health": 4000, "robot_health": [500, 400]}),
        ("GlobalLogisticsStatus", {"remaining_economy": 200}),
        ("RobotStaticStatus", {"__present__": [4, 9], "robot_id": 3, "max_health": 600}),
        ("RobotDynamicStatus", {"current_health": 300, "remaining_ammo": 40}),
        ("RobotModuleStatus", {"module_status_1": 1, "module_status_2": 0}),
        ("RobotPosition", {"robot_id": 3, "x": 1.0, "y": 2.0}),
        ("Buff", {"robot_id": 3, "buff_type": 1, "buff_delta": 10}),
        ("PenaltyInfo", {"robot_id": 5, "penalty_type": 2}),
        ("RobotPathPlanInfo", {"waypoint_count": 2}),
        ("MapClickInfo", {"ascii": 66, "map_x": 1.0, "map_y": 2.0, "type": 1}),
        ("RadarInfoToClient", {"robots": [{"robot_id": 5}]}),
        ("TechCoreMotionStateSync", {"core_status": 1}),
        ("RobotPerformanceSelectionSync", {"shooter": 500, "chassis": 400}),
        ("RuneStatusSync", {"rune_status": 2, "average_rings": 2.0}),
        ("SentryStatusSync", {"sentry_health": 300}),
        ("DartSelectTargetStatusSync", {"dart_id": 4}),
        ("SentryCtrlResult", {"result_code": 1}),
        ("AirSupportStatusSync", {"airsupport_status": 1, "left_time": 30,
                                  "cost_coins": 200}),
        ("GlobalSpecialMechanism", {"mechanism_id": [1], "mechanism_time_sec": [20]}),
        ("Event", {"event_id": 3, "param": "x"}),
        ("RobotInjuryStat", {"killer_id": 1, "small_projectile_damage": 10,
                             "large_projectile_damage": 5}),
        ("RobotRespawnStatus", {"is_pending_respawn": 1, "total_respawn_progress": 2,
                                "current_respawn_progress": 1}),
    ]

    def run() -> dict:
        with manager_and_state() as (mgr, state):
            for name, data in messages:
                mgr._apply_one(state, name, dict(data))
            return {
                "stage": state.current_stage, "round": state.current_round,
                "economy": state.remaining_economy, "health": state.current_health,
                "ammo": state.remaining_ammo, "modules": dict(state.robot_module_status),
                "positions": list(state.robot_positions), "buffs": list(state.buffs),
                "penalties": list(state.penalties), "markers": len(state.map_markers),
                "radar": list(state.radar_detections), "tech": dict(state.tech_core),
                "perf": dict(state.performance_sync), "rune": state.energy_rune_phase,
                "sentry": dict(state.sentry_status), "dart": dict(state.dart_status),
                "ctrl": dict(state.sentry_ctrl_result),
                "air": state.air_support_active, "mech": list(state.special_mechanisms),
                "event": dict(state.backend_stats.get("last_event", {})),
                "hit_rev": state.projectile_hit_revision,
                "respawn": state.respawn_pending,
                "econ_rev": state.economy_status_revision,
            }

    reference = run()
    saved = dict(mgr_mod._APPLY_ONE_DISPATCH)
    mismatches: list[int] = []
    try:
        for seed in range(25):
            order = list(saved)
            random.Random(seed).shuffle(order)
            mgr_mod._APPLY_ONE_DISPATCH.clear()
            mgr_mod._APPLY_ONE_DISPATCH.update({k: saved[k] for k in order})
            if run() != reference:
                mismatches.append(seed)
    finally:
        mgr_mod._APPLY_ONE_DISPATCH.clear()
        mgr_mod._APPLY_ONE_DISPATCH.update(saved)
    check("分发表置乱 25 次，落地状态逐字段不变（表项顺序不承载语义）",
          not mismatches, f"不一致的置乱种子={mismatches}")


# ===========================================================================
# B. 提前退出的契约（CustomByteBlock / 未选中的原生流）
# ===========================================================================
def _native_packet(sequence: int, reset: bool, payload: bytes) -> bytes:
    header = (0x01 if reset else 0x00) | (0x02 if len(payload) > 255 else 0x00)
    block = bytes((header, sequence & 0xFF, len(payload) & 0xFF)) + payload
    return block + bytes(300 - len(block))


def test_unselected_native_stream_returns_early() -> None:
    """未选中的原生流必须**提前退出**，连经济确认尾声都不走。

    重构前那是 `_apply_one` 里一句裸 `return`；拆成分发处理函数之后，
    同样的 `return` 只会退出处理函数，尾声会被补跑 —— 这条把契约钉住。
    """
    with manager_and_state() as (mgr, state):
        mgr._active_video_source = "udp hevc"          # 明确"没选原生"
        state.deploy_mode = 0
        payload = {"data": _native_packet(7, True, b"junk" * 8)}
        check("未选中的原生流：处理函数返回 False（= 不走尾声）",
              mgr._apply_custom_byte_block(state, payload) is False)
        state.backend_stats.pop("native_video_0310", None)
        mgr._apply_one(state, "CustomByteBlock", dict(payload))
        check("未选中的原生流：0x0310 统计不落地（提前退出的可观察后果）",
              "native_video_0310" not in state.backend_stats)
        check("未选中的原生流：恢复门被复位、且要求重新等 SPS",
              mgr._native_0310_last_sequence is None
              and mgr._native_0310_blocked_until_sps is True)


# ===========================================================================
# C. 15 个零覆盖分支的补测
# ===========================================================================
def test_game_status() -> None:
    with manager_and_state() as (mgr, state):
        dirty(state)
        mgr._apply_one(state, "GameStatus", {
            "current_round": 5, "total_rounds": 7, "red_score": 250,
            "blue_score": 200, "current_stage": 4,
            "stage_countdown_sec": 42, "stage_elapsed_sec": 8,
        })
        check("GameStatus: 轮次/比分/阶段真的落进状态",
              state.current_round == 5 and state.total_rounds == 7
              and state.red_score == 250 and state.blue_score == 200
              and state.current_stage == "MATCH"
              and state.stage_countdown_sec == 42 and state.stage_elapsed_sec == 8,
              f"{state.current_stage}/{state.stage_countdown_sec}")
        mgr._apply_one(state, "GameStatus", {})
        check("GameStatus: 空报文保持现值（缺省值不得写成 0）",
              state.current_round == 5 and state.total_rounds == 7
              and state.red_score == 250 and state.blue_score == 200
              and state.stage_countdown_sec == 42 and state.stage_elapsed_sec == 8,
              f"round={state.current_round} cd={state.stage_countdown_sec}")
        mgr._apply_one(state, "GameStatus", {"stage_countdown_sec": -5})
        check("GameStatus: 负的倒计时被夹到 0", state.stage_countdown_sec == 0)


def test_global_logistics_status() -> None:
    with manager_and_state() as (mgr, state):
        dirty(state)
        state.economy_status_revision = 0
        mgr._apply_one(state, "GlobalLogisticsStatus", {
            "remaining_economy": 321, "total_economy_obtained": 999,
            "tech_level": 4, "encryption_level": 5,
        })
        check("GlobalLogisticsStatus: 经济/科技/加密等级落进状态",
              state.remaining_economy == 321 and state.total_economy_obtained == 999
              and state.tech_level == 4 and state.encryption_level == 5,
              f"econ={state.remaining_economy}")
        check("GlobalLogisticsStatus: 经济真变了要触发经济确认尾声",
              state.economy_status_revision == 1 and state.economy_command_status == "",
              f"rev={state.economy_status_revision}")
        mgr._apply_one(state, "GlobalLogisticsStatus", {})
        check("GlobalLogisticsStatus: 空报文保持现值（缺省值不得写成 0）",
              state.remaining_economy == 321 and state.total_economy_obtained == 999
              and state.tech_level == 4 and state.encryption_level == 5,
              f"econ={state.remaining_economy} tech={state.tech_level}")


def test_robot_dynamic_status() -> None:
    with manager_and_state() as (mgr, state):
        dirty(state)
        state.economy_status_revision = 0
        state.economy_command_status = "pending-buy"
        mgr._apply_one(state, "RobotDynamicStatus", {
            "current_health": 210, "current_heat": 12.5,
            "current_chassis_energy": 33, "current_buffer_energy": 44,
            "total_projectiles_fired": 4321, "remaining_ammo": 56,
            "is_out_of_combat": 1, "can_remote_heal": 1, "can_remote_ammo": 1,
        })
        check("RobotDynamicStatus: 全部动态字段落进状态",
              state.current_health == 210 and state.current_heat == 12.5
              and state.current_chassis_energy == 33
              and state.current_buffer_energy == 44
              and state.total_projectiles_fired == 4321 and state.remaining_ammo == 56
              and state.out_of_combat is True and state.can_remote_heal is True
              and state.can_remote_ammo is True,
              f"hp={state.current_health} ammo={state.remaining_ammo}")
        check("RobotDynamicStatus: 弹药/血量变了要触发经济确认尾声",
              state.economy_status_revision == 1 and state.economy_command_status == "",
              f"rev={state.economy_status_revision}")
        check("RobotDynamicStatus: 本机动态报文按 robot_id 归档",
              3 in state.robot_dynamics and state.robot_dynamics[3]["current_health"] == 210)
        mgr._apply_one(state, "RobotDynamicStatus", {})
        check("RobotDynamicStatus: 空报文保持现值（缺省值不得写成 0）",
              state.current_health == 210 and state.current_heat == 12.5
              and state.current_chassis_energy == 33
              and state.current_buffer_energy == 44
              and state.total_projectiles_fired == 4321 and state.remaining_ammo == 56,
              f"hp={state.current_health} ammo={state.remaining_ammo}")


def test_robot_module_status() -> None:
    with manager_and_state() as (mgr, state):
        state.robot_id = 7
        mgr._apply_one(state, "RobotModuleStatus",
                       {"module_status_1": 1, "module_status_2": 0,
                        "module_status_3": 2, "module_status_4": 1})
        check("RobotModuleStatus: 四个模块状态全部落进状态",
              state.robot_module_status == {"module_status_1": 1, "module_status_2": 0,
                                             "module_status_3": 2, "module_status_4": 1},
              str(state.robot_module_status))
        check("RobotModuleStatus: 同一份按 robot_id 归档（供战况面板读）",
              7 in state.robot_modules
              and state.robot_modules[7]["module_status_1"] == 1)


def test_robot_path_plan_info() -> None:
    with manager_and_state() as (mgr, state):
        state.path_plan = {"old": 1}
        mgr._apply_one(state, "RobotPathPlanInfo",
                       {"waypoint_count": 5, "path_id": 42})
        check("RobotPathPlanInfo: 整份路径规划替换进状态",
              state.path_plan == {"waypoint_count": 5, "path_id": 42},
              str(state.path_plan))


def test_map_click_info() -> None:
    with manager_and_state() as (mgr, state):
        mgr._apply_one(state, "MapClickInfo",
                       {"ascii": 65, "map_x": 3.0, "map_y": 4.0, "type": 1})
        check("MapClickInfo: 攻击标记（type=1 -> kind=attack）真的落地",
              len(state.map_markers) == 1
              and state.map_markers[0]["kind"] == "attack"
              and state.map_markers[0]["x"] == 3.0
              and state.map_markers[0]["y"] == 4.0
              and state.map_markers[0]["label"] == "A",
              str(state.map_markers[-1]))
        mgr._apply_one(state, "MapClickInfo", {"ascii": 0, "type": 0})
        check("MapClickInfo: 非可打印 ascii 落成 MARK，且 type=0 -> marker",
              state.map_markers[-1]["label"] == "MARK"
              and state.map_markers[-1]["kind"] == "marker",
              str(state.map_markers[-1]))
        for _ in range(30):
            mgr._apply_one(state, "MapClickInfo", {"ascii": 66, "type": 0})
        # 实现是 `[*state.map_markers[-15:], marker]`：先截到 15 再追加一个，
        # 所以上限是 **16** 不是 15。这里按真实行为钉住，免得后人"顺手改成 15"
        # 时以为是等价简化（那会让 HUD 上多丢一个标记）。
        check("MapClickInfo: 标记保留最近 15 个 + 新来的一个（上限 16）",
              len(state.map_markers) == 16, str(len(state.map_markers)))
        check("MapClickInfo: 被挤出去的是最老的那几个，最新的仍在",
              state.map_markers[-1]["label"] == "B" and state.map_markers[0]["label"] == "B",
              f"first={state.map_markers[0].get('label')} last={state.map_markers[-1].get('label')}")


def test_radar_info_to_client() -> None:
    with manager_and_state() as (mgr, state):
        mgr._apply_one(state, "RadarInfoToClient", {
            "robots": [{"robot_id": 5, "x": 1.0, "y": 2.0},
                       {"robot_id": 6, "x": 3.0, "y": 4.0}]})
        check("RadarInfoToClient: 雷达检测点真的落进状态",
              [r["robot_id"] for r in state.radar_detections] == [5, 6],
              str(state.radar_detections))
        check("RadarInfoToClient: 同时刷新时间戳（供陈旧判定用）",
              state.radar_detections_at > 0.0, str(state.radar_detections_at))


def test_tech_core_motion_state_sync() -> None:
    with manager_and_state() as (mgr, state):
        state.tech_core = {"stale": True}
        mgr._apply_one(state, "TechCoreMotionStateSync",
                       {"core_status": 2, "core_health": 900})
        check("TechCoreMotionStateSync: 科技核心状态整份替换进状态",
              state.tech_core == {"core_status": 2, "core_health": 900},
              str(state.tech_core))


def test_sentry_status_sync() -> None:
    with manager_and_state() as (mgr, state):
        state.sentry_status = {"stale": True}
        mgr._apply_one(state, "SentryStatusSync",
                       {"sentry_health": 450, "remaining_ammo": 120})
        check("SentryStatusSync: 哨兵状态整份替换进状态",
              state.sentry_status == {"sentry_health": 450, "remaining_ammo": 120},
              str(state.sentry_status))


def test_dart_select_target_status_sync() -> None:
    with manager_and_state() as (mgr, state):
        state.dart_status = {"stale": True}
        mgr._apply_one(state, "DartSelectTargetStatusSync",
                       {"dart_id": 8, "dart_cooldown": 2})
        check("DartSelectTargetStatusSync: 飞镖状态整份替换进状态",
              state.dart_status == {"dart_id": 8, "dart_cooldown": 2},
              str(state.dart_status))


def test_sentry_ctrl_result() -> None:
    with manager_and_state() as (mgr, state):
        state.sentry_ctrl_result = {"stale": True}
        mgr._apply_one(state, "SentryCtrlResult", {"result_code": 3})
        check("SentryCtrlResult: 哨兵控制结果落进状态",
              state.sentry_ctrl_result == {"result_code": 3},
              str(state.sentry_ctrl_result))


def test_air_support_status_sync() -> None:
    with manager_and_state() as (mgr, state):
        dirty(state)
        state.air_support_active = False
        state.economy_status_revision = 0
        state.economy_command_status = "pending-buy"
        mgr._apply_one(state, "AirSupportStatusSync", {
            "airsupport_status": 1, "left_time": 25, "cost_coins": 150})
        check("AirSupportStatusSync: 激活态/剩余时间/金币落进状态",
              state.air_support_active is True
              and state.air_support_free_time_sec == 25
              and state.air_support_cost_coins == 150,
              f"active={state.air_support_active} t={state.air_support_free_time_sec}")
        check("AirSupportStatusSync: 激活态变了要触发经济确认尾声",
              state.economy_status_revision == 1 and state.economy_command_status == "",
              f"rev={state.economy_status_revision}")
        mgr._apply_one(state, "AirSupportStatusSync", {})
        # 这一支的缺省值是 0/False（**不是**"保持现值"），空报文会把上一份
        # 支援状态清掉。与 GameStatus 那种 data.get(k, state.x) 的写法不同，
        # 所以这里必须按真实行为断言 —— 写成"保持现值"会把正确实现判成错。
        check("AirSupportStatusSync: 空报文按缺省 0/False 复位（该支就是重置语义）",
              state.air_support_free_time_sec == 0
              and state.air_support_cost_coins == 0
              and state.air_support_active is False,
              f"t={state.air_support_free_time_sec} coins={state.air_support_cost_coins}")


def test_global_special_mechanism() -> None:
    with manager_and_state() as (mgr, state):
        mgr._apply_one(state, "GlobalSpecialMechanism", {
            "mechanism_id": [1, 4], "mechanism_time_sec": [30, 45]})
        check("GlobalSpecialMechanism: 机制 id 与剩余时间成对落进状态",
              state.special_mechanisms == [{"mechanism_id": 1, "time_sec": 30},
                                           {"mechanism_id": 4, "time_sec": 45}],
              str(state.special_mechanisms))
        mgr._apply_one(state, "GlobalSpecialMechanism",
                       {"mechanism_id": [1], "mechanism_time_sec": []})
        check("GlobalSpecialMechanism: 两个数组长度不等时按 zip 截断，不得抛异常",
              state.special_mechanisms == [], str(state.special_mechanisms))


def test_event() -> None:
    with manager_and_state() as (mgr, state):
        mgr._apply_one(state, "Event", {"event_id": 7, "param": "boom"})
        check("Event: 事件 id 与参数落进 backend_stats",
              state.backend_stats["last_event"] == {"event_id": 7, "param": "boom"},
              str(state.backend_stats["last_event"]))
        mgr._apply_one(state, "Event", {})
        # 同样：Event 的缺省值是 0 / ""（重置语义），不是"保持现值"。
        check("Event: 空报文按缺省 0/\"\" 复位（该支就是重置语义）",
              state.backend_stats["last_event"] == {"event_id": 0, "param": ""},
              str(state.backend_stats["last_event"]))


def test_robot_respawn_status() -> None:
    with manager_and_state() as (mgr, state):
        dirty(state)
        state.economy_status_revision = 0
        state.economy_command_status = "pending-buy"
        mgr._apply_one(state, "RobotRespawnStatus", {
            "is_pending_respawn": 1, "total_respawn_progress": 4,
            "current_respawn_progress": 2, "can_free_respawn": 0,
            "gold_cost_for_respawn": 400, "can_pay_for_respawn": 1})
        check("RobotRespawnStatus: 待重生/进度/金币落进状态",
              state.respawn_pending is True
              and abs(state.respawn_progress - 0.5) < 1e-9
              and state.respawn_can_free is False
              and state.respawn_gold_cost == 400
              and state.respawn_can_pay is True,
              f"pending={state.respawn_pending} p={state.respawn_progress}")
        check("RobotRespawnStatus: 待重生状态变了要触发经济确认尾声",
              state.economy_status_revision == 1 and state.economy_command_status == "",
              f"rev={state.economy_status_revision}")
        mgr._apply_one(state, "RobotRespawnStatus",
                       {"total_respawn_progress": 0, "current_respawn_progress": 3})
        check("RobotRespawnStatus: total=0 不得除零，进度夹到 1.0",
              state.respawn_progress == 1.0, str(state.respawn_progress))
        mgr._apply_one(state, "RobotRespawnStatus", {})
        check("RobotRespawnStatus: 空报文保持现值（缺省值不得写成常量）",
              state.respawn_pending is True
              and state.respawn_gold_cost == 400
              and state.respawn_can_pay is True,
              f"pending={state.respawn_pending} gold={state.respawn_gold_cost}")


def test_telemetry_block_lands_every_state_field() -> None:
    """原生遥测（CustomByteBlock 走 parse_vehicle_telemetry 那条路）也要全覆盖。

    `smoke_test` 已经钉住了 `native_gimbal_online` / `frame_seq` / `status_text`
    三项，但这个分支一次写进 state 的字段有二十来个。变异验证把
    `native_bullet_speed` 改成 0.0 时**全量套件一条都不红** —— 解析器本身
    被测到了，但从报文到 state 的那一步没被测到。缺的那一环就在这里补上。
    """
    import struct

    status = b"AUTO READY\x00"
    fixed_size = struct.calcsize("<4sHHIQIHHHHIBBH9f")
    packet = struct.pack(
        "<4sHHIQIHHHHIBBH9f",
        b"PDL1", 1, fixed_size + len(status), 0b11, 1_725_000_000_000, 77,
        1280, 720, 5994, 125, 3200, 1, 0, 12, 15.5, 0.4, -3.25, -0.2, 16.8,
        0.0, 0.0, 0.0, 1.0,
    ) + status

    with manager_and_state() as (mgr, state):
        mgr._apply_one(state, "CustomByteBlock", {"data": packet})
        check("遥测帧：帧序号真的落进 state（不只是解析器返回对）",
              state.native_telemetry_frame_seq == 77,
              str(state.native_telemetry_frame_seq))
        check("遥测帧：弹速落进 state（变异验证挖出来的漏网字段）",
              abs(state.native_bullet_speed - 16.8) < 1e-4,
              str(state.native_bullet_speed))
        check("遥测帧：弹数落进 state", state.native_bullet_count == 12,
              str(state.native_bullet_count))
        check("遥测帧：云台模式落进 state", state.native_gimbal_mode == "AUTO_AIM",
              state.native_gimbal_mode)
        check("遥测帧：角速度与姿态落进 state",
              abs(state.native_yaw_deg - 15.5) < 1e-4
              and abs(state.native_pitch_deg - (-3.25)) < 1e-4,
              f"yaw={state.native_yaw_deg} pitch={state.native_pitch_deg}")
        check("遥测帧：状态文本落进 state", state.native_status_text == "AUTO READY",
              state.native_status_text)
        check("遥测帧：时间戳刷新", state.native_telemetry_at > 0.0
              and state.native_telemetry_unix_ms == 1_725_000_000_000)
        check("遥测帧：汇总进 backend_stats",
              state.backend_stats["native_telemetry"]["frame_seq"] == 77
              and state.backend_stats["native_telemetry"]["status"] == "AUTO READY",
              str(state.backend_stats["native_telemetry"]))
        check("遥测帧：块长度记进 backend_stats",
              state.backend_stats["last_custom_block"]["len"] == len(packet))


def main() -> int:
    tests = [
        test_dispatch_table_shape,
        test_every_handler_returns_bool,
        test_unknown_module_is_a_noop,
        test_order_is_not_semantics,
        test_unselected_native_stream_returns_early,
        test_telemetry_block_lands_every_state_field,
        test_game_status,
        test_global_logistics_status,
        test_robot_dynamic_status,
        test_robot_module_status,
        test_robot_path_plan_info,
        test_map_click_info,
        test_radar_info_to_client,
        test_tech_core_motion_state_sync,
        test_sentry_status_sync,
        test_dart_select_target_status_sync,
        test_sentry_ctrl_result,
        test_air_support_status_sync,
        test_global_special_mechanism,
        test_event,
        test_robot_respawn_status,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"FAILED {len(FAILED)}")
        for name in FAILED:
            print(f"  - {name[:150]}")
        return 1
    print("ALL APPLY-ONE DISPATCH TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())