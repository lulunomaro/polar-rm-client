"""第 11 轮审计补网：config_store / serial_ports 的"另一侧"决策点。

这一批断言全部来自**先探针、后断言**：每条断言的期望值都是用探针脚本在
**当前实现**上跑出来的真实值（见提交说明），不是照抄源码算出来的。

覆盖的决策点分两类：
  * 守卫条件**从未被做成真**（第 10 轮 `tick_polar_link` 的老毛病）：旧档案
    迁移、导入守卫、load 的形状/版本闸门、后台落盘的外层重试；
  * 守卫条件**从未被做成假**：CC 的 Auto 阈值、双 FTDI 并列、重扫时的
    "消失" 提示、空当前值。

跑法：``python tests/config_store_branch_audit_test.py``
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path('C:\\Users\\lulunomaro\\Desktop\\claude\\openzenuicodexver\\rm_custom_client')
sys.path.insert(0, str(ROOT))

from rm_client import config_store as cs  # noqa: E402
from rm_client import serial_ports as sp  # noqa: E402
from rm_client.config_store import (  # noqa: E402
    ClientConfigStore,
    apply_registry_config,
    export_client_config,
)
from rm_client.model import Module, ModuleRegistry, Setting, build_default_registry  # noqa: E402

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


def tiny_registry(*settings: Setting) -> ModuleRegistry:
    return ModuleRegistry([Module(name="M", category="Test", settings=list(settings))])


def attempt(label: str, fn):
    """跑一次调用并把异常变成一条 [FAIL]。

    守卫被摘掉时正确的反应本来就是"抛异常"；如果让异常直接冲出整份文件，
    后面的断言一条都跑不到，那就不是"断言变红"而是"进程死了"。这里把它
    记成一条和原断言同名的失败，既红得明确，也保住后面的断言。
    """
    try:
        return fn()
    except BaseException as exc:  # noqa: BLE001
        check(label, False, f"抛出 {type(exc).__name__}: {exc}")
        return None


def with_ports(sp_mod, entries):
    real = sp_mod.list_serial_ports
    sp_mod.list_serial_ports = lambda: [dict(e) for e in entries]
    return real


# ===========================================================================
# A. 后台落盘队列：外层重试 / 关闭 / 超时 / 失败留痕
# ===========================================================================

def test_outer_retry_survives_a_held_file() -> None:
    """config_store.py:226 —— 外层 _MAX_WRITE_ATTEMPTS 重排。

    探针事实：`_replace_with_retry` 一次会调 10 次 ``os.replace``
    （9 个退避档 + 末尾兜底那一次），全部失败才把异常抛给调用方；
    抛给调用方之后 `_run` 必须把它重新排到队尾再试一轮。杀软/索引器
    把档案按住 200 ms 就会走到这一层。
    """
    with tempfile.TemporaryDirectory() as tmp:
        writer = cs._BackgroundWriter()
        target = Path(tmp) / "held.json"
        target.write_text('{"version": 1}', encoding="utf-8")
        real_replace = cs.os.replace
        calls = {"n": 0}

        def sharing_violation(src, dst):
            calls["n"] += 1
            if calls["n"] <= 12:  # 前两轮全部撞上"文件正被别的句柄打开"
                exc = OSError(13, "The process cannot access the file")
                exc.winerror = 32  # ERROR_SHARING_VIOLATION
                raise exc
            return real_replace(src, dst)

        cs.os.replace = sharing_violation
        drained = False
        try:
            writer.submit(target, b'{"version": 1, "n": 2}')
            try:
                drained = writer.flush(timeout=20.0)
            except OSError:
                drained = False
        finally:
            cs.os.replace = real_replace
            writer.close(5.0)
        check(
            f"文件被占用 {calls['n']} 次后重排落盘成功（不是直接丢弃这次保存）",
            drained and target.read_bytes() == b'{"version": 1, "n": 2}',
            f"drained={drained} calls={calls['n']} file={target.read_bytes()[:30]!r}",
        )
        check(
            "被按住的那次保存最终算成一次成功落盘",
            writer.stats()["writes"] == 1,
            str(writer.stats()),
        )
        check("重排没有留下临时文件", not list(Path(tmp).glob("*.tmp")), str(list(Path(tmp).glob("*.tmp"))))


def test_final_write_failure_leaves_a_trace() -> None:
    """config_store.py:240 —— 彻底失败时必须往 stderr 留痕。

    探针事实：`_report` 把 ``[config_store] 后台落盘失败: ...`` 写进
    ``sys.stderr``。没有这行，"点保存没反应"在日志里是彻底无痕的。
    """
    with tempfile.TemporaryDirectory() as tmp:
        writer = cs._BackgroundWriter()
        original = cs._atomic_write_bytes
        cs._atomic_write_bytes = lambda path, payload: (_ for _ in ()).throw(OSError(28, "disk full"))
        buf = io.StringIO()
        try:
            writer.submit(Path(tmp) / "lost.json", b"{}")
            with contextlib.redirect_stderr(buf):
                drained = writer.flush(timeout=20.0, raise_errors=False)
        finally:
            cs._atomic_write_bytes = original
            writer.close(5.0)
        text = buf.getvalue()
        check("后台写彻底失败时 flush() 不抛给渲染线程", drained, f"drained={drained}")
        check(
            "后台写彻底失败会在 stderr 上留痕（否则保存丢失是无声的）",
            "[config_store]" in text and "后台落盘失败" in text,
            repr(text[:160]),
        )
        check("失败的写没有把半截文件留在盘上", not (Path(tmp) / "lost.json").exists())


def test_exit_flush_warns_when_still_unflushed() -> None:
    """config_store.py:252 —— 退出前还有排队内容时必须出声。"""
    real_flush = cs._BackgroundWriter.flush
    buf = io.StringIO()
    try:
        cs._BackgroundWriter.flush = lambda self, timeout=5.0, *, raise_errors=True: False
        with contextlib.redirect_stderr(buf):
            finished = cs.flush_pending_saves(0.1)
    finally:
        cs._BackgroundWriter.flush = real_flush
    text = buf.getvalue()
    check("退出前没刷完时 flush_pending_saves() 返回 False", finished is False, str(finished))
    check(
        "退出前没刷完会在 stderr 上说清楚（不是静默返回）",
        "退出前仍有配置未落盘" in text,
        repr(text[:160]),
    )


def test_flush_path_times_out_instead_of_claiming_drained() -> None:
    """config_store.py:171 —— flush_path 超时必须返回 False。"""
    with tempfile.TemporaryDirectory() as tmp:
        writer = cs._BackgroundWriter()
        target = Path(tmp) / "slow.json"
        original = cs._atomic_write_bytes

        def slow_write(path, payload):
            time.sleep(1.5)
            return original(path, payload)

        cs._atomic_write_bytes = slow_write
        try:
            writer.submit(target, b"{}")
            t0 = time.perf_counter()
            result = writer.flush_path(target, timeout=0.2)
            elapsed_ms = (time.perf_counter() - t0) * 1e3
            writer.flush(timeout=20.0)
        finally:
            cs._atomic_write_bytes = original
            writer.close(5.0)
        check(
            f"写盘被占住时 flush_path 超时返回 False 而不是谎报已排空（{elapsed_ms:.0f} ms）",
            result is False,
            f"result={result}",
        )
        check("flush_path 超时不会把调用线程永久挂住", elapsed_ms < 1400, f"{elapsed_ms:.0f} ms")


def test_closed_writer_refuses_new_saves() -> None:
    """config_store.py:132 —— close() 之后再 save() 必须立刻报错。

    探针事实：``_WRITER.close()`` 之后 ``submit()`` 抛
    ``RuntimeError('配置落盘器已关闭')``。守卫被摘掉的话，这次保存会
    静静排进一条已经没人消费的队列，档案永远停在旧内容。
    """
    with tempfile.TemporaryDirectory() as tmp:
        writer = cs._BackgroundWriter()
        target = Path(tmp) / "late.json"
        writer.submit(target, b'{"version": 1}')
        writer.flush(5.0)
        writer.close(5.0)
        raised = None
        try:
            writer.submit(target, b'{"version": 2}')
        except RuntimeError as exc:
            raised = exc
        check("关闭后的落盘器拒绝新的保存（而不是静静丢掉）", isinstance(raised, RuntimeError), f"raised={raised!r}")
        check("拒绝时盘上仍是上一版", target.read_bytes() == b'{"version": 1}', target.read_text())
        writer.close(5.0)  # 变异体下这里会重新起一个写盘线程；收尾前先让它退出
        time.sleep(0.3)


# ===========================================================================
# B. 档案列表 / 读档的闸门
# ===========================================================================

def test_profile_names_ignores_another_store() -> None:
    """config_store.py:290 —— 别的 ClientConfigStore 实例的排队档案不许混进来。

    探针事实：两个实例各指向一个临时目录，B 提交 ``otherstore`` 后，
    ``A.profile_names()`` 只有 ``['default']``。
    """
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        store_a = ClientConfigStore(Path(a))
        store_b = ClientConfigStore(Path(b))
        # 让写盘卡在"正在写"上：key 留在队列里（pending_keys 含 _active），
        # 这样 profile_names() 看到的是确定的状态，不是竞态。
        original = cs._atomic_write_bytes
        gate = threading.Event()

        def blocking(path, payload):
            gate.wait(5.0)
            return original(path, payload)

        cs._atomic_write_bytes = blocking
        try:
            store_b.save("otherstore", {"modules": {}})
            time.sleep(0.4)
            names_a = store_a.profile_names()
            names_b = store_b.profile_names()
        finally:
            gate.set()
            cs._atomic_write_bytes = original
        store_b.flush(20.0)
        check(
            "别的实例排队的档案不出现在本实例的档案列表里",
            "otherstore" not in names_a,
            f"A={names_a}",
        )
        check("本实例自己的排队档案仍然可见", "otherstore" in names_b, f"B={names_b}")
        check(
            "列表里的每个名字在本实例目录下都真的有落盘路径",
            all(store_a.profile_path(n).parent == Path(a) for n in names_a),
            str(names_a),
        )


def test_load_rejects_missing_broken_and_wrong_shaped_profiles() -> None:
    """config_store.py:306 / 310 / 312 —— load() 的三道闸门。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        check("档案不存在时 load() 回 None",
              attempt("档案不存在时 load() 回 None", lambda: store.load("absent")) is None)

        (Path(tmp) / "truncated.json").write_text('{"version": 1, "mod', encoding="utf-8")
        check("档案被截断（半截 JSON）时 load() 回 None 而不是抛",
              attempt("档案被截断（半截 JSON）时 load() 回 None 而不是抛",
                      lambda: store.load("truncated")) is None)

        (Path(tmp) / "listed.json").write_text("[1, 2, 3]", encoding="utf-8")
        check("档案顶层是数组时 load() 回 None",
              attempt("档案顶层是数组时 load() 回 None", lambda: store.load("listed")) is None)

        (Path(tmp) / "scalar.json").write_text('"just a string"', encoding="utf-8")
        check("档案顶层是标量时 load() 回 None",
              attempt("档案顶层是标量时 load() 回 None", lambda: store.load("scalar")) is None)

        # 读不出来（这里用一个"叫 *.json 的目录"模拟权限/占用类 OSError）
        blocked = Path(tmp) / "blocked.json"
        blocked.mkdir()
        value = attempt("档案读不出来（OSError）时 load() 回 None而不是把异常抛给 GUI 线程",
                        lambda: store.load("blocked"))
        check("档案读不出来（OSError）时 load() 回 None而不是把异常抛给 GUI 线程",
              value is None, f"value={value!r}")


def test_load_version_gate() -> None:
    """config_store.py:318 —— 版本闸门的两侧。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        (Path(tmp) / "future.json").write_text('{"version": 2, "modules": {}}', encoding="utf-8")
        (Path(tmp) / "negative.json").write_text('{"version": -1, "modules": {}}', encoding="utf-8")
        (Path(tmp) / "legacy.json").write_text('{"modules": {}}', encoding="utf-8")
        (Path(tmp) / "floaty.json").write_text('{"version": 1.7, "modules": {}}', encoding="utf-8")
        (Path(tmp) / "boolish.json").write_text('{"version": true, "modules": {}}', encoding="utf-8")
        check("比本客户端更新的档案被拒（不回 Half-Apply 到不认识的字段）",
              store.load("future") is None, str(store.load("future")))
        check("负版本号的档案被拒", store.load("negative") is None)
        legacy = store.load("legacy")
        check("没有 version 字段的老档案仍然加载（老选手的档案不能丢）",
              legacy is not None and legacy.get("modules") == {}, str(legacy))
        check("小数版本号按 int() 截断后仍算本版本",
              store.load("floaty") is not None, str(store.load("floaty")))
        check("true 也会被 int() 读成版本 1",
              store.load("boolish") is not None, str(store.load("boolish")))


def test_import_file_guards() -> None:
    """config_store.py:353 —— 导入守卫的两侧。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        upper = Path(tmp) / "BACKUP.JSON"
        upper.write_bytes(b'{"version": 1, "note": "\xe4\xb8\xad"}')
        name = None
        raised = None
        try:
            name = store.import_file(upper)
        except Exception as exc:  # noqa: BLE001
            raised = exc
        check("大写 .JSON 的备份文件也能导入（Windows 上很常见）",
              raised is None and name == "BACKUP", f"raised={raised!r} name={name!r}")
        check("导入后字节逐位一致", (Path(tmp) / "BACKUP.json").read_bytes() == upper.read_bytes())

        for label, arg in (("已不存在的文件", Path(tmp) / "gone.json"),
                           ("非 .json 文件", Path(tmp) / "notes.txt")):
            if arg.suffix == ".txt":
                arg.write_text("x", encoding="utf-8")
            raised = None
            try:
                store.import_file(arg)
            except ValueError as exc:
                raised = exc
            except Exception as exc:  # noqa: BLE001
                raised = exc
            check(f"导入{label}时给的是人话 ValueError，而不是裸的 FileNotFoundError",
                  isinstance(raised, ValueError), f"raised={type(raised).__name__}: {raised}")
            check(f"导入{label}的错误说明里点明了原因", raised is not None and "json" in str(raised).lower(),
                  str(raised))


def test_ensure_default_never_overwrites_an_existing_profile() -> None:
    """config_store.py:346 —— 守卫摘掉就等于每次启动覆盖选手的 default 档案。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        path = store.profile_path("default")
        attempt("首次启动写出 default.json",
                lambda: store.ensure_default({"modules": {"A": {"enabled": True}}}))
        first = attempt("读回首次写出的 default.json", lambda: path.read_bytes())
        check("首次启动写出 default.json", path.exists() and path.name == "default.json", str(path))
        store.save("default", {"modules": {"A": {"enabled": False}}, "marker": "mine"}, wait=True)
        mine = path.read_bytes()
        store.ensure_default({"modules": {"A": {"enabled": True}}})
        after = attempt("读回 ensure_default 之后的 default.json", lambda: path.read_bytes())
        check("default.json 已存在时 ensure_default 不再覆盖它",
              after == mine and first != mine,
              f"{after!r}")
        check("选手自己写进 default 的内容还在",
              attempt("读回 default.json 里的内容",
                      lambda: json.loads(path.read_text(encoding="utf-8")).get("marker")) == "mine")


def test_clean_profile_name_empty_falls_back_to_default() -> None:
    """config_store.py:367 —— 洗不出名字时必须退回 default。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        check("空档案名洗成 default", store.clean_profile_name("") == "default", repr(store.clean_profile_name("")))
        check("纯空白档案名洗成 default", store.clean_profile_name("   ") == "default", repr(store.clean_profile_name("   ")))
        check("空档案名落盘路径仍然是 default.json",
              store.profile_path("").name == "default.json", store.profile_path("").name)
        store.save("", {"a": 1}, wait=True)
        check("用空名字保存真的写进了 default.json（而不是一个点开头的隐形文件）",
              [p.name for p in Path(tmp).iterdir()] == ["default.json"],
              str([p.name for p in Path(tmp).iterdir()]))
        check("空名字保存完能在档案列表里看到", store.profile_names() == ["default"], str(store.profile_names()))


def test_async_save_env_zero_is_synchronous() -> None:
    """config_store.py:336 —— POLAR_CONFIG_ASYNC_SAVE=0 的排障/录制基准模式。

    探针事实：在子进程里设 ``POLAR_CONFIG_ASYNC_SAVE=0`` 再 import，
    ``save()`` 的写盘发生在**调用线程**上，返回即落盘。
    """
    with tempfile.TemporaryDirectory() as tmp:
        script = (
            "import os, sys, threading\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            f"os.environ['POLAR_USER_DATA_ROOT'] = {tmp!r}\n"
            "from pathlib import Path\n"
            "from rm_client import config_store as cs\n"
            "on_call_thread = []\n"
            "real = cs._atomic_write_bytes\n"
            "def spy(path, payload):\n"
            "    on_call_thread.append(threading.get_ident())\n"
            "    return real(path, payload)\n"
            "cs._atomic_write_bytes = spy\n"
            "store = cs.ClientConfigStore(Path(r'" + tmp + "'))\n"
            "store.save('sync-mode', {'modules': {}})\n"
            "me = threading.get_ident()\n"
            "print('ASYNC_FLAG', cs.ASYNC_SAVE)\n"
            "print('ON_CALLER', me in on_call_thread)\n"
            "print('LANDED', (Path(r'" + tmp + "') / 'sync-mode.json').exists())\n"
        )
        env = dict(os.environ)
        env["POLAR_CONFIG_ASYNC_SAVE"] = "0"
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=120, env=env)
        out = proc.stdout
        check("POLAR_CONFIG_ASYNC_SAVE=0 时 ASYNC_SAVE 读成 False", "ASYNC_FLAG False" in out, out[-200:])
        check("save() 返回即落盘：写盘发生在调用线程上（不是排给后台线程）",
              "ON_CALLER True" in out, out[-200:])
        check("同步模式下返回后盘上已经有文件", "LANDED True" in out, out[-200:])


# ===========================================================================
# C. 恢复语义：钳位、迁移、跳过
# ===========================================================================

def test_number_reclamped_after_snapping_to_step_grid() -> None:
    """config_store.py:469 / 471 —— 吸附到 step 网格之后必须再钳一次。

    探针事实：``min=0, max=10, step=6, 当前值 6``，档案里存 ``10`` 恢复到
    **10.0**（吸附会算出 12，越过 10，所以必须再钳一次）；
    ``min=7, max=100, step=6, 当前值 12``，档案里存 ``7`` 恢复到
    **7.0**（吸附会算出 6，跌破 7）。
    """
    high = tiny_registry(Setting(name="Hi", kind="number", value=6.0,
                                 min_value=0.0, max_value=10.0, step=6.0))
    hi = high.get("M").get_setting("Hi")
    apply_registry_config(high, {"modules": {"M": {"settings": {"Hi": 10}}}})
    check("吸附越过上界后被钳回上界（不会以 120% 上限运行）", hi.value == 10.0, str(hi.value))

    low = tiny_registry(Setting(name="Lo", kind="number", value=12.0,
                                min_value=7.0, max_value=100.0, step=6.0))
    lo = low.get("M").get_setting("Lo")
    apply_registry_config(low, {"modules": {"M": {"settings": {"Lo": 7}}}})
    check("吸附跌破下界后被钳回下界", lo.value == 7.0, str(lo.value))


def test_sound_theme_vape_is_migrated() -> None:
    """config_store.py:493 —— 旧包的 ``"Vape"`` 音效主题名必须迁移。"""
    registry = build_default_registry()
    theme = registry.get("AudioFeedback").get_setting("Sound Theme")
    theme.value = "LiquidBounce"  # 先拨到一个"别的合法值"，迁移才看得出来
    apply_registry_config(registry, {"modules": {"AudioFeedback": {"settings": {"Sound Theme": "Vape"}}}})
    check("旧档案的 Sound Theme=Vape 迁到 Polar UI（不留在 LiquidBounce）",
          theme.value == "Polar UI", str(theme.value))
    theme.value = "Polar Classic"
    apply_registry_config(registry, {"modules": {"AudioFeedback": {"settings": {"Sound Theme": "Polar Classic"}}}})
    check("已经是新包名的 Sound Theme 原样保留", theme.value == "Polar Classic", str(theme.value))


def test_render_backend_gpu_cached_is_migrated() -> None:
    """config_store.py:582 —— 旧渲染后端名必须迁到 Unified GPU。

    注意：这里只断言**设置项的字符串值**，不碰任何绘制代码。
    """
    registry = build_default_registry()
    backend = registry.get("ClickGui").get_setting("Render Backend")
    for legacy in ("GPU Cached", "Direct GPU"):
        backend.value = "Compatibility Painter"
        apply_registry_config(registry, {"modules": {"ClickGui": {"settings": {"Render Backend": legacy}}}})
        check(f"旧档案的 Render Backend={legacy} 迁到 Unified GPU",
              backend.value == "Unified GPU", str(backend.value))
    backend.value = "Compatibility Painter"
    apply_registry_config(registry, {"modules": {"ClickGui": {"settings": {"Render Backend": "Compatibility Painter"}}}})
    check("已经是新名的 Render Backend 原样保留", backend.value == "Compatibility Painter", str(backend.value))


def test_explicit_alliance_side_beats_legacy_matchmode() -> None:
    """config_store.py:501-506 —— 新档案的 Side 不得被老 MatchMode 覆盖。"""
    registry = build_default_registry()
    side = registry.get("AllianceProfile").get_setting("Side")
    apply_registry_config(registry, {"modules": {
        "AllianceProfile": {"settings": {"Side": "Red"}},
        "MatchMode": {"settings": {"Alliance": "Blue"}},
    }})
    check("档案里已有 AllianceProfile.Side 时，老 MatchMode.Alliance 不得覆盖它",
          side.value == "Red", str(side.value))

    fresh = build_default_registry()
    apply_registry_config(fresh, {"modules": {"MatchMode": {"settings": {"Alliance": "Blue"}}}})
    check("只有老字段时才做迁移（Side=Blue）",
          fresh.get("AllianceProfile").get_setting("Side").value == "Blue")

    quiet = build_default_registry()
    quiet.get("AllianceProfile").enabled = False
    apply_registry_config(quiet, {"modules": {"MatchMode": {"settings": {"Alliance": "Red"}}}})
    check("阵营迁移只改 Side，不会顺手把 AllianceProfile 打开",
          quiet.get("AllianceProfile").enabled is False, str(quiet.get("AllianceProfile").enabled))


def test_legacy_elimination_fx_profile_migrates() -> None:
    """config_store.py:510 / 516 / 518 / 525 —— 旧击杀特效档案的四处迁移。"""
    registry = build_default_registry()
    fx = registry.get("EliminationFX")

    apply_registry_config(registry, {"modules": {"EliminationFX": {"settings": {
        "Style": "Valorant", "Variant": "Glitchpop", "Volume": 70,
        "Anchor Policy": "Original Position",
    }}}})
    check("旧 Style=Valorant + Variant=Glitchpop 迁成 Preset=Valorant Glitchpop",
          fx.get_setting("Preset").value == "Valorant Glitchpop", str(fx.get_setting("Preset").value))
    check("旧 Volume 迁成 Sound Volume", fx.get_setting("Sound Volume").value == 70, str(fx.get_setting("Sound Volume").value))
    check("旧锚点 Original Position 迁成 Original Bottom",
          fx.get_setting("Anchor Policy").value == "Original Bottom", str(fx.get_setting("Anchor Policy").value))
    check("迁移出来的 Anchor Policy 是当前合法选项之一",
          fx.get_setting("Anchor Policy").value in fx.get_setting("Anchor Policy").options,
          str(fx.get_setting("Anchor Policy").options))

    apply_registry_config(registry, {"modules": {"EliminationFX": {"settings": {
        "Style": "Valorant", "Variant": "Default",
    }}}})
    check("Variant=Default 时落到 Valorant Prime（不是拼出不存在的 'Valorant Default'）",
          fx.get_setting("Preset").value == "Valorant Prime", str(fx.get_setting("Preset").value))

    apply_registry_config(registry, {"modules": {"EliminationFX": {"settings": {
        "Style": "CardBar",
    }}}})
    check("旧 Style=CardBar 迁成 Preset=Card",
          fx.get_setting("Preset").value == "Card", str(fx.get_setting("Preset").value))

    apply_registry_config(registry, {"modules": {"EliminationFX": {"settings": {
        "Preset": "Battlefield 5", "Sound Volume": 33, "Anchor Policy": "Manual",
    }}}})
    check("已经是新档案时三处都不被动（Preset/Volume/Anchor 原样）",
          (fx.get_setting("Preset").value, fx.get_setting("Sound Volume").value,
           fx.get_setting("Anchor Policy").value) == ("Battlefield 5", 33, "Manual"),
          str((fx.get_setting("Preset").value, fx.get_setting("Sound Volume").value,
               fx.get_setting("Anchor Policy").value)))
    check("老档案没有 Responsive Scale 时补上 Auto",
          fx.get_setting("Responsive Scale").value == "Auto", str(fx.get_setting("Responsive Scale").value))


def test_unknown_and_dirty_module_entries_are_skipped() -> None:
    """config_store.py:534 —— 档案里的陌生模块不许把整份配置炸掉。"""
    registry = build_default_registry()
    status = registry.get("TacticalMap").get_setting("Status Display")
    status.value = "LiquidAuto"
    raised = None
    try:
        apply_registry_config(registry, {"modules": {
            "A Module That No Longer Exists": {"enabled": True, "keybind": "X", "settings": {"Fov": 1}},
            "ArmorAutoAim": 7,  # 脏条目：不是 dict
            "TacticalMap": {"settings": {"Status Display": "Off"}},
        }})
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("档案里有已删除的模块 / 脏条目时不抛异常", raised is None, f"raised={type(raised).__name__}: {raised}")
    check("跳过坏条目之后，后面的正常模块照样被应用",
          status.value == "Off", str(status.value))

    registry.get("ArmorAutoAim").get_setting("Fov").value = 72
    apply_registry_config(registry, {"modules": {
        "ArmorAutoAim": {"settings": {"A Setting That No Longer Exists": 5, "Fov": 55}},
    }})
    check("档案里已删除的 setting 名被跳过，不影响同模块的其它 setting",
          registry.get("ArmorAutoAim").get_setting("Fov").value == 55,
          str(registry.get("ArmorAutoAim").get_setting("Fov").value))


def test_non_dict_blocks_are_ignored() -> None:
    """config_store.py:477 / 481 / 491 / 548 —— 形状不对的块不许炸。"""
    registry = build_default_registry()
    for label, data in (
        ("没有 modules 键", {}),
        ("modules 是列表", {"modules": [1, 2, 3]}),
        ("modules 是字符串", {"modules": "nope"}),
        ("ClickGui.settings 是字符串", {"modules": {"ClickGui": {"settings": "nope"}}}),
        ("ClickGui.settings 是列表", {"modules": {"ClickGui": {"settings": []}}}),
        ("ClickGui.settings 是数字", {"modules": {"ClickGui": {"settings": 7}}}),
        ("AudioFeedback.settings 是数字", {"modules": {"AudioFeedback": {"settings": 7}}}),
        ("模块 settings 是数字", {"modules": {"ArmorAutoAim": {"settings": 7}}}),
        ("模块 settings 是列表", {"modules": {"ArmorAutoAim": {"settings": []}}}),
    ):
        raised = None
        try:
            apply_registry_config(registry, data)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            raised = exc
        check(f"形状不对的配置（{label}）被安静忽略而不是抛异常",
              raised is None, f"raised={type(raised).__name__}: {raised}")


def test_action_group_session_state_is_never_restored() -> None:
    """config_store.py:592 —— 动作组的展开/选中属于活会话，不许从档案恢复。"""
    registry = build_default_registry()
    group = registry.get("ClickGui").get_setting("Old ClickGUIs")
    before = repr(group.value)
    group.value = {"expanded": ["armor"], "selected": "vape-unified"}  # 活会话状态
    apply_registry_config(registry, {"modules": {"ClickGui": {"settings": {
        "Old ClickGUIs": {"expanded": [], "selected": ""},
    }}}})
    check("档案里的 action_group 不会覆盖当前正在用的动作组状态",
          repr(group.value) == repr({"expanded": ["armor"], "selected": "vape-unified"}),
          f"before={before} after={group.value!r}")

    registry.get("MatchReadiness").get_setting("Qualification Actions").value = {"selected": "live"}
    apply_registry_config(registry, {"modules": {"MatchReadiness": {"settings": {
        "Qualification Actions": {"selected": "archived"},
    }}}})
    check("另一个 action_group 同样不被档案覆盖",
          registry.get("MatchReadiness").get_setting("Qualification Actions").value == {"selected": "live"},
          str(registry.get("MatchReadiness").get_setting("Qualification Actions").value))


def test_action_group_never_reaches_the_exported_profile() -> None:
    """config_store.py:404 —— 导出时就把 action_group 剔掉。"""
    registry = build_default_registry()
    data = export_client_config(registry, gui_mode="x", selected_category="a",
                                selected_module="b", module_expanded=set(),
                                old_panels={}, new_panels={}, dropdown_panels={})
    leaked = [
        f"{name}/{key}"
        for name, payload in data["modules"].items()
        for key in (payload.get("settings") or {})
        if key in {"Old ClickGUIs", "Qualification Actions"}
    ]
    check("导出的档案里没有任何 action_group 键", not leaked, str(leaked))
    check("导出的档案仍然带着版本号", data["version"] == cs.CONFIG_VERSION, str(data.get("version")))


def test_keybind_edge_cases() -> None:
    """config_store.py:543 / 545 —— HeroLob 与空 keybind。"""
    registry = build_default_registry()
    apply_registry_config(registry, {"modules": {"HeroLob": {"keybind": ""}}})
    check("HeroLob 档案里 keybind 是空串时也补上 'K'（否则专用面板打不开）",
          registry.get("HeroLob").keybind == "K", repr(registry.get("HeroLob").keybind))
    apply_registry_config(registry, {"modules": {"HeroLob": {"keybind": "None"}}})
    check("HeroLob 档案里 keybind 是 'None' 时也补上 'K'",
          registry.get("HeroLob").keybind == "K", repr(registry.get("HeroLob").keybind))
    apply_registry_config(registry, {"modules": {"HeroLob": {"keybind": "L"}}})
    check("HeroLob 有自己的绑定时不动它", registry.get("HeroLob").keybind == "L",
          repr(registry.get("HeroLob").keybind))
    apply_registry_config(registry, {"modules": {"ArmorAutoAim": {"keybind": ""}}})
    check("普通模块的空 keybind 归一成 'None'（不留下一个无主键）",
          registry.get("ArmorAutoAim").keybind == "None", repr(registry.get("ArmorAutoAim").keybind))
    apply_registry_config(registry, {"modules": {"ArmorAutoAim": {"keybind": "J"}}})
    check("普通模块的正常 keybind 原样保留", registry.get("ArmorAutoAim").keybind == "J",
          repr(registry.get("ArmorAutoAim").keybind))


def test_bool_restore_word_table_is_complete() -> None:
    """config_store.py:414-415 —— 词义表里剩下的词也得按词义解释。

    ``bool("disable")`` 是 True，所以"关"的词一旦漏出表就会被读成"开"。
    """
    registry = build_default_registry()
    module = next(m for m in registry.modules if any(s.kind == "bool" for s in m.settings))
    setting = next(s for s in module.settings if s.kind == "bool")
    off_words = ["f", "disable", "disabled", "FALSE", " Off "]
    on_words = ["t", "enable", "enabled", "TrUe"]
    wrong = []
    for raw in off_words:
        setting.value = True
        apply_registry_config(registry, {"modules": {module.name: {"settings": {setting.name: raw}}}})
        if setting.value is not False:
            wrong.append((raw, setting.value))
    for raw in on_words:
        setting.value = False
        apply_registry_config(registry, {"modules": {module.name: {"settings": {setting.name: raw}}}})
        if setting.value is not True:
            wrong.append((raw, setting.value))
    check(f"{len(off_words) + len(on_words)} 个额外词义开关按词解释（不会静默翻转）",
          not wrong, f"错: {wrong}")


# ===========================================================================
# D. serial_ports
# ===========================================================================

ROBOT = {"device": "COM3", "description": "STMicroelectronics Virtual COM Port",
         "vid": 0x0483, "pid": 0x5720}
DT7 = {"device": "COM9", "description": "DJI USB Virtual COM", "vid": 0xFFF0, "pid": 0x0008}
FTDI = {"device": "COM7", "description": "USB Serial Port (COM7)", "vid": 0x0403, "pid": 0x6001}
OTHER = {"device": "COM5", "description": "USB Serial", "vid": 0x1A86, "pid": 0x7523}
BARE = {"device": "COM6", "description": "CH340", "vid": 0x1A86, "pid": 0x7523}
# 换了驱动版本：描述里仍然是 DJI，但 VID/PID 不是 FFF0:0008
DT7_ALT_VID = dict(DT7, device="COM10", vid=0x1234, pid=0x5678)


def test_a_lane_never_picks_the_remote_even_by_description() -> None:
    """serial_ports.py:85 —— ``"dji" in description`` 也必须判负。"""
    check("描述是 DJI 但 VID 不认的口，A 路打负分（-200）",
          sp.score_port(DT7_ALT_VID) == -200, str(sp.score_port(DT7_ALT_VID)))
    real = with_ports(sp, [DT7_ALT_VID])
    try:
        only_remote = sp.resolve_port("Auto")
    finally:
        sp.list_serial_ports = real
    check("只插着驱动版本不同的遥控器时，A 路 Auto 宁可不连（不回 Stub 之外的口）",
          only_remote == sp.STUB_PORT, only_remote)

    real = with_ports(sp, [DT7_ALT_VID, BARE])
    try:
        picked = sp.resolve_port("Auto")
    finally:
        sp.list_serial_ports = real
    check("遥控器与一个普通 USB 口同时插着时，A 路选普通口而不是遥控器",
          picked == "COM6", picked)


def test_dji_port_with_unknown_vid_is_still_usable_by_dt7() -> None:
    """serial_ports.py:200 —— 驱动版本不同的 DJI 口 DT7 侧必须认。"""
    check("描述含 dji 但 VID 不匹配时，RC 侧给 70 分（正好是 Auto 的受理线）",
          sp.score_rc_port(DT7_ALT_VID) == 70, str(sp.score_rc_port(DT7_ALT_VID)))
    real = with_ports(sp, [DT7_ALT_VID, ROBOT])
    try:
        resolved = sp.resolve_rc_port("Auto")
        choices = sp.rc_port_choices()
    finally:
        sp.list_serial_ports = real
    check("DT7 的 Auto 仍然能挑到驱动版本不同的 DJI 口", resolved == "COM10", resolved)
    check("它也出现在 DT7 的下拉候选里", choices == ["Auto", "COM10"], str(choices))
    check("车的口仍然不混进 DT7 候选", "COM3" not in choices, str(choices))


def test_cc_auto_refuses_everything_but_ftdi() -> None:
    """serial_ports.py:250 / 252 —— CC 的 Auto 只认 FTDI。"""
    real = with_ports(sp, [ROBOT, DT7, FTDI])
    try:
        check("CC 的 Auto 挑 FTDI", sp.resolve_cc_port("Auto") == "COM7", sp.resolve_cc_port("Auto"))
    finally:
        sp.list_serial_ports = real

    real = with_ports(sp, [ROBOT])
    try:
        only_robot = sp.resolve_cc_port("Auto")
    finally:
        sp.list_serial_ports = real
    check("没有 FTDI 时 CC 的 Auto 回 Stub（绝不退到机器人的 CDC 上）",
          only_robot == sp.STUB_PORT, only_robot)

    real = with_ports(sp, [ROBOT, DT7])
    try:
        no_ftdi = sp.resolve_cc_port("Auto")
    finally:
        sp.list_serial_ports = real
    check("插着车和遥控器、没有 FTDI 时 CC 的 Auto 仍然回 Stub",
          no_ftdi == sp.STUB_PORT, no_ftdi)

    real = with_ports(sp, [FTDI, dict(FTDI, device="COM14")])
    try:
        tie = sp.resolve_cc_port("Auto")
    finally:
        sp.list_serial_ports = real
    check("两个 FTDI 并列时不猜（回 Stub 让人显式选）", tie == sp.STUB_PORT, tie)


def test_a_lane_prefers_hinted_usb_over_bare_usb() -> None:
    """serial_ports.py:91 —— 描述关键字档必须真的比"其它 USB"高。"""
    check("带 USB 关键字的口是 60 分", sp.score_port(FTDI) == 60, str(sp.score_port(FTDI)))
    check("不带关键字的 USB 口是 30 分", sp.score_port(BARE) == 30, str(sp.score_port(BARE)))
    real = with_ports(sp, [FTDI, BARE])
    try:
        picked = sp.resolve_port("Auto")
    finally:
        sp.list_serial_ports = real
    check("A 路 Auto 在两个 USB 口之间选描述认得出的那个", picked == "COM7", picked)


def test_resolve_port_with_no_ports_at_all() -> None:
    """serial_ports.py:117 —— 一个口都枚举不到时回 Stub。"""
    real = with_ports(sp, [])
    try:
        a = attempt("枚举不到任何口时 A 路回 Stub", lambda: sp.resolve_port("Auto"))
        b = attempt("枚举不到任何口时 DT7 侧回 Stub", lambda: sp.resolve_rc_port("Auto"))
        c = attempt("枚举不到任何口时 CC 侧回 Stub", lambda: sp.resolve_cc_port("Auto"))
    finally:
        sp.list_serial_ports = real
    check("枚举不到任何口时三条链路都回 Stub", (a, b, c) == ("Stub", "Stub", "Stub"), f"{a} {b} {c}")


def test_port_choices_have_no_duplicates() -> None:
    """serial_ports.py:105 —— 固定候选与探测结果去重。"""
    real = with_ports(sp, [ROBOT, DT7, FTDI, OTHER])
    try:
        choices = sp.port_choices()
    finally:
        sp.list_serial_ports = real
    check("候选列表里没有重复项", len(choices) == len(set(choices)), str(choices))
    check("候选列表以 Stub/Auto 开头", choices[:2] == [sp.STUB_PORT, sp.AUTO_PORT], str(choices[:2]))
    check("探测到的每一个口都在候选里（人工兜底可用）",
          {"COM3", "COM9", "COM7", "COM5"} <= set(choices), str(choices))


def test_describe_ports_tags_and_truncates() -> None:
    """serial_ports.py:136 —— 非 USB 标记与 120 字截断。"""
    real = with_ports(sp, [{"device": "COM2", "description": "通信端口 (COM2)", "vid": 0, "pid": 0}])
    try:
        legacy = sp.describe_ports()
    finally:
        sp.list_serial_ports = real
    check("没有 VID/PID 的主板串口标成 [非 USB]",
          legacy == ["COM2 [非 USB] 通信端口 (COM2)"], str(legacy))

    real = with_ports(sp, [dict(BARE, vid=0x1A86, pid=0)])
    try:
        vid_only = sp.describe_ports()
    finally:
        sp.list_serial_ports = real
    check("只带 VID、不带 PID 的口也标成 [非 USB]（不打印 1A86:0000 冒充 USB）",
          vid_only == ["COM6 [非 USB] CH340"], str(vid_only))

    real = with_ports(sp, [dict(BARE, description="X" * 300)])
    try:
        long_line = sp.describe_ports()
        limited = sp.describe_ports(limit=0)
    finally:
        sp.list_serial_ports = real
    check("超长描述被截到 120 字（HUD tooltip 不会撑爆）",
          len(long_line[0]) == 120, str(len(long_line[0])))
    check("limit=0 时给空清单", limited == [], str(limited))


class _Setting:
    def __init__(self, value: str, options: list[str]) -> None:
        self.value = value
        self.options = list(options)


class _Module:
    def __init__(self, settings: dict) -> None:
        self._s = settings

    def get_setting(self, name: str):
        return self._s.get(name)


class _Registry:
    def __init__(self, module) -> None:
        self._m = module

    def get(self, _name: str):
        return self._m


def test_refresh_port_options_reports_disappearing_ports() -> None:
    """serial_ports.py:291 / 297 / 299 / 300 —— 拔线后的重扫提示。"""
    base = ["Auto", "COM1", "COM3", "COM5", "COM7", "COM9"]
    module = _Module({
        "Port": _Setting("COM99", base),
        "DT7 Port": _Setting("Auto", base),
        "CC Port": _Setting("Auto", base),
    })
    real = with_ports(sp, [ROBOT])
    try:
        diffs = sp.refresh_port_options(_Registry(module))
    finally:
        sp.list_serial_ports = real
    port_line = next((d for d in diffs if d.startswith("Port: ")), "")
    dt7_line = next((d for d in diffs if d.startswith("DT7 Port")), "")
    check("拔掉的口被如实报成消失", "（消失 COM7, COM9）" in port_line, port_line)
    check("'Auto' 不会被当成消失的端口报给人看", "消失 Auto" not in " ".join(diffs), str(diffs))
    check("候选整体收缩时明写'无新增'", "无新增" in dt7_line, dt7_line)
    check("当前值 COM99 即使不在也必须留在候选里（越刷新越坏就出在这里）",
          "COM99" in module.get_setting("Port").options, str(module.get_setting("Port").options))
    check("重扫不改当前值", module.get_setting("Port").value == "COM99",
          str(module.get_setting("Port").value))
    check("再扫一次是幂等的（没有变化就不报）",
          sp.refresh_port_options(_Registry(module)) == [], "第二次非空")


def test_refresh_port_options_empty_current_is_not_offered() -> None:
    """serial_ports.py:291 —— 当前值是空串时不能把空串塞进候选。"""
    module = _Module({"Port": _Setting("", ["Auto"]),
                      "DT7 Port": _Setting("Auto", ["Auto"]),
                      "CC Port": _Setting("Auto", ["Auto"])})
    real = with_ports(sp, [ROBOT])
    try:
        sp.refresh_port_options(_Registry(module))
    finally:
        sp.list_serial_ports = real
    check("空当前值不会被写成候选里的一项", "" not in module.get_setting("Port").options,
          str(module.get_setting("Port").options))


def test_refresh_port_options_survives_missing_pieces() -> None:
    """serial_ports.py:280 / 281 / 287 —— registry 缺东西时不炸。"""
    check("registry 连 get 都没有时安静返回空差异",
          attempt("registry 连 get 都没有时安静返回空差异",
                  lambda: sp.refresh_port_options(object())) == [])
    check("registry 里没有 RobotLink 时安静返回空差异",
          attempt("registry 里没有 RobotLink 时安静返回空差异",
                  lambda: sp.refresh_port_options(_Registry(None))) == [])
    partial = _Module({"Port": _Setting("Auto", ["Auto"])})
    real = with_ports(sp, [ROBOT])
    try:
        diffs = attempt("模块缺 setting 时只更新存在的那一个",
                        lambda: sp.refresh_port_options(_Registry(partial)))
    finally:
        sp.list_serial_ports = real
    check("模块缺 DT7 Port / CC Port 两个 setting 时只更新存在的那一个",
          diffs is not None and len(diffs) == 1 and diffs[0].startswith("Port: "), str(diffs))
    check("缺失的 setting 没有被凭空创建", partial.get_setting("DT7 Port") is None)


class _Info:
    def __init__(self, device, description=None, vid=None, pid=None, serial_number=None):
        self.device = device
        if description is not None:
            self.description = description
        if vid is not None:
            self.vid = vid
        if pid is not None:
            self.pid = pid
        if serial_number is not None:
            self.serial_number = serial_number


def _with_fake_pyserial(items, fn):
    """把一个假的 ``serial.tools.list_ports`` 塞进 sys.modules，跑 fn，撤掉。"""
    fake_lp = types.ModuleType("serial.tools.list_ports")
    fake_lp.comports = lambda: items() if callable(items) else list(items)
    tools = types.ModuleType("serial.tools")
    tools.list_ports = fake_lp
    serial = types.ModuleType("serial")
    serial.tools = tools
    saved = {k: sys.modules.get(k) for k in ("serial", "serial.tools", "serial.tools.list_ports")}
    sys.modules["serial"] = serial
    sys.modules["serial.tools"] = tools
    sys.modules["serial.tools.list_ports"] = fake_lp
    try:
        return fn()
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def test_list_serial_ports_parses_and_sorts() -> None:
    """serial_ports.py:45-59 / 65 —— 真枚举路径（本机没装 pyserial，注入一个假的）。"""
    items = [
        _Info("COM10", "STMicroelectronics Virtual COM Port", 0x0483, 0x5720, "SN10"),
        _Info("COM3", "STMicroelectronics Virtual COM Port", 0x0483, 0x5720),
        _Info("COM1", description="通信端口 (COM1)"),
        _Info("\\\\.\\physical#1", "weird"),
        _Info("/dev/ttyUSB0", "Linux CDC"),
    ]

    def run():
        return sp.list_serial_ports()

    ports = _with_fake_pyserial(items, lambda: attempt("伪造 pyserial 时枚举出全部口", run))
    devices = [p["device"] for p in (ports or [])]
    check("端口按 COM 序号排（COM1 < COM3 < COM10），非 COM 名字排最后",
          devices == ["COM1", "COM3", "COM10", "\\\\.\\physical#1", "/dev/ttyUSB0"], str(devices))
    check("每个条目都带齐下游代码要用的 6 个键",
          bool(ports) and all(set(p) == {"device", "name", "description", "vid", "pid", "serial_number"} for p in ports),
          str([sorted(p) for p in (ports or [])][:1]))
    check("name 取设备名最后一段（去掉 \\\\?.\\ 前缀）",
          bool(ports) and len(ports) > 3 and ports[3]["name"] == "physical#1",
          str(ports[3]["name"]) if ports and len(ports) > 3 else "no entry")
    usb = ports[2] if ports and len(ports) > 2 else {}
    check("VID/PID 读成整数", (usb.get("vid"), usb.get("pid")) == (0x0483, 0x5720), f"{usb.get('vid')} {usb.get('pid')}")
    check("序列号原样带过来", usb.get("serial_number") == "SN10", repr(usb.get("serial_number")))
    bare = ports[0] if ports else {}
    check("pyserial 报不出 VID/PID 时补 0（下游按 0 当非 USB 处理）",
          (bare.get("vid"), bare.get("pid")) == (0, 0), f"{bare.get('vid')} {bare.get('pid')}")
    check("pyserial 报不出描述/序列号时补空串而不是 None",
          bare.get("description") == "通信端口 (COM1)" and bare.get("serial_number") == "",
          f"{bare.get('description')!r} {bare.get('serial_number')!r}")
    check("非 COM 设备名不会被 _com_number 解析成 0（否则排到最前面）",
          sp._com_number("/dev/ttyUSB0") == 1 << 30, str(sp._com_number("/dev/ttyUSB0")))


def test_list_serial_ports_never_raises() -> None:
    """serial_ports.py:46 / 47 —— 枚举失败时降级成空列表。"""

    def boom():
        raise OSError("usb subsystem on fire")

    def run():
        return sp.list_serial_ports()

    check("comports() 抛异常时返回空列表而不是把异常抛给调用方",
          _with_fake_pyserial(boom, lambda: attempt("枚举失败时降级成空列表", run)) == [])


def main() -> int:
    tests = [
        test_outer_retry_survives_a_held_file,
        test_final_write_failure_leaves_a_trace,
        test_exit_flush_warns_when_still_unflushed,
        test_flush_path_times_out_instead_of_claiming_drained,
        test_closed_writer_refuses_new_saves,
        test_profile_names_ignores_another_store,
        test_load_rejects_missing_broken_and_wrong_shaped_profiles,
        test_load_version_gate,
        test_import_file_guards,
        test_ensure_default_never_overwrites_an_existing_profile,
        test_clean_profile_name_empty_falls_back_to_default,
        test_async_save_env_zero_is_synchronous,
        test_number_reclamped_after_snapping_to_step_grid,
        test_sound_theme_vape_is_migrated,
        test_render_backend_gpu_cached_is_migrated,
        test_explicit_alliance_side_beats_legacy_matchmode,
        test_legacy_elimination_fx_profile_migrates,
        test_unknown_and_dirty_module_entries_are_skipped,
        test_non_dict_blocks_are_ignored,
        test_action_group_session_state_is_never_restored,
        test_action_group_never_reaches_the_exported_profile,
        test_keybind_edge_cases,
        test_bool_restore_word_table_is_complete,
        test_a_lane_never_picks_the_remote_even_by_description,
        test_dji_port_with_unknown_vid_is_still_usable_by_dt7,
        test_cc_auto_refuses_everything_but_ftdi,
        test_a_lane_prefers_hinted_usb_over_bare_usb,
        test_resolve_port_with_no_ports_at_all,
        test_port_choices_have_no_duplicates,
        test_describe_ports_tags_and_truncates,
        test_refresh_port_options_reports_disappearing_ports,
        test_refresh_port_options_empty_current_is_not_offered,
        test_refresh_port_options_survives_missing_pieces,
        test_list_serial_ports_parses_and_sorts,
        test_list_serial_ports_never_raises,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"config-store / serial-ports 分支补网：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"config-store / serial-ports 分支补网：全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
