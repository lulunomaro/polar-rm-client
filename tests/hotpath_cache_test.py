"""热路径缓存的**失效正确性**回归。

为什么这个文件存在
------------------
本轮给两条 60Hz 热路径加了缓存：

* ``robot_roles._load_roles_from_spec`` —— 以规格对象身份为键
* ``dt7_link.load_profile``            —— 以 (路径, mtime_ns, size) 为键

缓存换来的是速度，代价是"可能读到旧值"。对这两条来说，读到旧值都不是
小毛病：

* 兵种限幅表读旧 = 电控改了 ``polar_protocol.json`` 里的限速，客户端还在按
  旧限速放行 —— 这是安全问题（AGENT.md 第 4 节不变量那一类）。
* profile 读旧 = 帧变体 / 命令号 / CRC 变体是旧的，发出去的帧固件不认，
  现场现象是"发了但没反应"，最难查的一类。

所以这里逐条把失效路径钉死：**改了文件必须立刻生效，不能等 TTL、不看时钟**。

跑法：``python tests/hotpath_cache_test.py``
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client import robot_roles  # noqa: E402
from rm_client.backend import dt7_link, polar_spec  # noqa: E402

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


# --- 1. profile 缓存：改文件必须立刻被看见 -------------------------------

def test_profile_cache_invalidates_on_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "dt7_profile.json"
        original = {
            "cmd_wire": 0x1500,
            "frame_variant": "dji_rc_v1",
            "crc_variant": "x25",
            "protocol_version": 3,
        }
        target.write_text(json.dumps(original), encoding="utf-8")

        first = dt7_link.load_profile(str(target))
        check("首次读拿到文件里的值", first["cmd_wire"] == 0x1500, f"got {first['cmd_wire']}")

        # 第二次应当命中缓存 —— 这正是我们要的快路径
        second = dt7_link.load_profile(str(target))
        check("第二次读值一致（走缓存）", second["cmd_wire"] == 0x1500)

        # 改文件：mtime 与内容都会变，必须立刻看到新值
        changed = dict(original)
        changed["cmd_wire"] = 0x1600
        target.write_text(json.dumps(changed), encoding="utf-8")
        # Windows 的 mtime 粒度可能粗到 1 秒内不可见；显式把 mtime 推后，
        # 保证这条断言测的是"内容变了要重读"，而不是撞上文件系统时间精度。
        future = time.time() + 2
        os.utime(target, (future, future))

        third = dt7_link.load_profile(str(target))
        check(
            "改文件后立刻读到新 cmd_wire（未等 TTL）",
            third["cmd_wire"] == 0x1600,
            f"got {third['cmd_wire']}，仍是旧值说明缓存没失效",
        )


# --- 2. profile 缓存：调用方改返回值不能污染缓存 ------------------------

def test_profile_cache_returns_fresh_dict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "dt7_profile.json"
        target.write_text(json.dumps({"cmd_wire": 0x1500, "frame_variant": "dji_rc_v1"}), encoding="utf-8")

        a = dt7_link.load_profile(str(target))
        a["cmd_wire"] = 0xDEAD          # 调用方就地改
        a["payload_fields"].append("XX")  # 连内层列表一起改
        a["_profile_error"] = "污染"

        b = dt7_link.load_profile(str(target))
        check("返回值改 cmd_wire 不污染缓存", b["cmd_wire"] == 0x1500, f"got {b['cmd_wire']}")
        check(
            "返回值改 payload_fields 不污染缓存",
            "XX" not in b["payload_fields"],
            f"缓存被污染：{b['payload_fields']}",
        )
        check("返回值改 _profile_error 不污染缓存", b["_profile_error"] == "", b["_profile_error"])


# --- 3. profile 缓存：坏文件也走同一条失效路径 --------------------------

def test_profile_cache_surfaces_errors_each_time() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "dt7_profile.json"
        target.write_text(json.dumps({"cmd_wire": 0x1500}), encoding="utf-8")
        good = dt7_link.load_profile(str(target))
        check("好文件无错误", good["_profile_error"] == "", good["_profile_error"])

        target.write_text("{ 这不是 json", encoding="utf-8")
        future = time.time() + 2
        os.utime(target, (future, future))
        bad = dt7_link.load_profile(str(target))
        check(
            "改成坏文件后立刻报出错误（不被缓存吞掉）",
            bad["_profile_error"] != "" and bad["cmd_wire"] is None,
            f"error={bad['_profile_error']!r} cmd_wire={bad['cmd_wire']!r}",
        )


# --- 4. profile 缓存：文件被删掉不返回陈旧值 ----------------------------

def test_profile_cache_handles_deletion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "dt7_profile.json"
        target.write_text(json.dumps({"cmd_wire": 0x1500}), encoding="utf-8")
        check("存在时读到值", dt7_link.load_profile(str(target))["cmd_wire"] == 0x1500)

        target.unlink()
        gone = dt7_link.load_profile(str(target))
        check(
            "文件被删后不再返回陈旧 cmd_wire",
            gone["cmd_wire"] != 0x1500,
            f"仍返回 {gone['cmd_wire']}",
        )


# --- 5. 兵种表缓存：换规格必须立刻换表 ---------------------------------

def test_roles_cache_invalidates_on_new_spec() -> None:
    """把 robot_roles 指到一份临时规格上改限速，验证缓存不会给出旧限速。"""

    with tempfile.TemporaryDirectory() as tmp:
        spec_path = Path(tmp) / "polar_protocol.json"

        def write_spec(max_linear: float) -> None:
            # 这份规格必须能通过 polar_spec 的校验，否则 limits_for 会按设计
            # 回退到内置表，测出来的就不是缓存行为了（第一版就踩了这个坑：
            # 少写 uplink.virtual_rc.drive_key_mask，整份被拒，拿到 2.0 是内置值）。
            spec = {
                "spec_version": 1,
                "downlink_segments": [
                    {
                        "id": "0x01",
                        "name": "Minimal",
                        "struct": "<H",
                        "inputs": ["heartbeat"],
                        "outputs": [{"name": "heartbeat", "from": "heartbeat"}],
                    }
                ],
                "uplink": {
                    "robot_cmd": {
                        "id": "0x01",
                        "struct": "<B",
                        "inputs": ["x"],
                        "never_set": ["fire"],
                    },
                    "virtual_rc": {
                        "id": "0x03",
                        "struct": "<B",
                        "inputs": ["keys"],
                        "drive_key_mask": 0x000F,  # 只放行 W/S/A/D
                        "key_bits": {"W": 0, "S": 1, "A": 2, "D": 3, "Q": 4, "E": 5},
                    },
                },
                "roles": [
                    {
                        "robot_id": 1,
                        "label": "Hero",
                        "chassis": "omni",
                        "hud_code": "HERO",
                        "max_linear_mps": max_linear,
                        "max_yaw_rps": 2.0,
                        "drivable": True,
                        "gimbal_controllable": True,
                        "known": True,
                        "notes": "",
                    }
                ],
                "fallback_role": {
                    "robot_id": 0,
                    "label": "Unknown",
                    "chassis": "omni",
                    "max_linear_mps": 1.0,
                    "max_yaw_rps": 1.0,
                    "drivable": False,
                    "gimbal_controllable": False,
                    "known": False,
                    "notes": "",
                },
            }
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

        write_spec(1.25)
        spec_a = polar_spec.load_spec(spec_path)
        robot_roles._ROLES_SPEC_CACHE["spec"] = None
        robot_roles._ROLES_SPEC_CACHE["table"] = None
        first = robot_roles._load_roles_from_spec()
        # 直接把 limits_for 指向这份临时规格
        original_get_spec = polar_spec.get_spec
        polar_spec.get_spec = lambda path=None: spec_a  # type: ignore[assignment]
        try:
            got_a = robot_roles.limits_for(1)
        finally:
            polar_spec.get_spec = original_get_spec  # type: ignore[assignment]
        check("第一份规格读出英雄限速 1.25", abs(got_a.max_linear_mps - 1.25) < 1e-9, f"got {got_a.max_linear_mps}")

        # 换成限速不同的第二份规格：必须是新对象，缓存必须失效
        write_spec(3.75)
        spec_b = polar_spec.load_spec(spec_path)
        check("两次 load_spec 产出不同对象（否则缓存无法失效）", spec_a is not spec_b)

        polar_spec.get_spec = lambda path=None: spec_b  # type: ignore[assignment]
        try:
            got_b = robot_roles.limits_for(1)
        finally:
            polar_spec.get_spec = original_get_spec  # type: ignore[assignment]
        check(
            "换规格后立刻读到新限速 3.75（未返回旧表）",
            abs(got_b.max_linear_mps - 3.75) < 1e-9,
            f"got {got_b.max_linear_mps}，返回了旧表说明缓存键用错了",
        )
        _ = first


# --- 6. 缓存不改变限幅语义（回归：builtin 兜底仍然生效） ----------------

def test_roles_cache_preserves_fallback_semantics() -> None:
    original_get_spec = polar_spec.get_spec
    # 规格坏掉时必须回退内置表，而不是缓存住上一份好表
    polar_spec.get_spec = lambda path=None: polar_spec.PolarSpec(  # type: ignore[assignment]
        source="broken", ok=False
    )
    try:
        got = robot_roles.limits_for(9999)
    finally:
        polar_spec.get_spec = original_get_spec  # type: ignore[assignment]
    check(
        "规格不可用时仍回退内置保守表",
        got.known is False or got.max_linear_mps <= 1.5,
        f"known={got.known} max_linear={got.max_linear_mps}",
    )


# --- 7. 性能护栏：缓存必须真的更快（防有人改回去还全绿） ----------------

def test_caches_are_faster() -> None:
    import statistics

    N = 8000

    def median_us(fn) -> float:
        for _ in range(400):
            fn()
        samples = []
        for _ in range(N):
            t0 = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - t0) * 1e6)
        return statistics.median(samples)

    cached_profile = median_us(lambda: dt7_link.load_profile())
    uncached_profile = median_us(lambda: dt7_link._load_profile_uncached())
    check(
        f"load_profile 缓存比直读快（{uncached_profile:.2f}µs → {cached_profile:.2f}µs）",
        cached_profile < uncached_profile * 0.75,
        f"只快了 {uncached_profile / max(cached_profile, 1e-9):.2f}×",
    )

    cached_roles = median_us(lambda: robot_roles.limits_for(1))
    # 绕过缓存重建一次表来量"未缓存"的代价
    def uncached_roles() -> None:
        robot_roles._ROLES_SPEC_CACHE["spec"] = None
        robot_roles._load_roles_from_spec()
        robot_roles.limits_for(1)
    rebuild = median_us(uncached_roles)
    check(
        f"limits_for 缓存后明显更快（重建表 {rebuild:.2f}µs → 命中 {cached_roles:.2f}µs）",
        cached_roles < rebuild * 0.75,
        f"只快了 {rebuild / max(cached_roles, 1e-9):.2f}×",
    )


def main() -> int:
    tests = [
        test_profile_cache_invalidates_on_edit,
        test_profile_cache_returns_fresh_dict,
        test_profile_cache_surfaces_errors_each_time,
        test_profile_cache_handles_deletion,
        test_roles_cache_invalidates_on_new_spec,
        test_roles_cache_preserves_fallback_semantics,
        test_caches_are_faster,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"热路径缓存失效性：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"热路径缓存失效性：全部通过（{PASSED} 项）")
    print("缓存只在内容未变时生效；改文件/换规格/文件出错都立刻可见。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
