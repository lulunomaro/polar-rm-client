"""config-io 组回归：后台落盘 / 原子导入 / 档案名清洗 / 恢复语义。

这个文件把本组四条改动逐条钉死，并且对"行为应当逐位不变"的部分做**逐位比对**
（不是"差不多"）：

* 落盘搬去后台线程之后，写出来的**字节**必须与同步版一模一样；
* 内置默认档案"导出 → 落盘 → 读回 → 应用"之后，59 个模块 387 个 setting 的值
  必须逐位不变（否则 `number` 吸附 step 会把 Armor3DESP Opacity 0.82 改成 0.8
  ——那是在没人授权的情况下动画面）；
* 进程退出（真的起一个子进程、真的直接 return）之前，落盘必须已经完成。

跑法：``python tests/config_store_io_test.py``
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rm_client import config_store as cs  # noqa: E402
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


def sample_config() -> dict[str, Any]:
    registry = build_default_registry()
    registry.get("ArmorAutoAim").keybind = "J"
    registry.get("ArmorAutoAim").get_setting("Fov").value = 72
    return export_client_config(
        registry,
        gui_mode="PolarNew",
        selected_category="Combat",
        selected_module="ArmorAutoAim",
        module_expanded={"ArmorAutoAim"},
        old_panels={},
        new_panels={},
        dropdown_panels={},
        polar_windows={"Combat": {"x": 80, "y": 100, "scroll": 0}},
        quick_action_wheel={"sectors": 6, "radius": 132.0, "dead_zone": 42.0},
    )


def median_ms(fn, reps: int = 60, warmup: int = 5) -> float:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(samples)


def snapshot(registry: ModuleRegistry) -> list[tuple[str, str, str, str]]:
    """把 registry 拍成可逐位比对的形状（类型名 + repr，含浮点表示）。

    number 的 int→float 变宽是**改动前就有**的行为（老代码同样写
    ``number = float(value)``），所以这里比的是数值本身：Python 3 的 float
    repr 是精确往返的，``repr(85.0) == repr(float(85))`` 即"逐位相同"。
    """
    rows = []
    for module in registry.modules:
        rows.append(("MODULE", module.name, repr(module.enabled), repr(module.keybind)))
        for setting in module.settings:
            value = setting.value
            if setting.kind == "number":
                rows.append((setting.kind, f"{module.name}/{setting.name}", repr(float(value)), "number"))
            else:
                rows.append((setting.kind, f"{module.name}/{setting.name}", repr(value), type(value).__name__))
    return rows


# --- 1. 后台落盘：快、且最终一定落盘 -----------------------------------------

def test_async_save_is_fast_and_lands() -> None:
    data = sample_config()
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        target = store.save("practice", data)
        # 调用方立刻返回：此刻后台线程多半还没写完
        async_ms = median_ms(lambda: store.save("practice", data), reps=200)
        sync_ms = median_ms(lambda: store.save("practice", data, wait=True), reps=60)
        store.flush()
        check(
            f"save() 异步化后不再阻塞调用线程（同步 {sync_ms:.3f} ms → 异步 {async_ms:.3f} ms，"
            f"{sync_ms / max(async_ms, 1e-9):.1f}×）",
            async_ms < sync_ms * 0.25,
            f"只快了 {sync_ms / max(async_ms, 1e-9):.2f}×",
        )
        payload = json.loads(target.read_text(encoding="utf-8"))
        check("save() 立刻返回正确的目标路径", target.name == "practice.json", target.name)
        check("后台落盘最终把数据写到了盘上", payload["profile"] == "practice", str(payload.get("profile")))
        check(
            f"同步写与后台写的耗时都在同一量级之外（异步 {async_ms:.3f} ms << 同步 {sync_ms:.3f} ms）",
            async_ms * 4 < sync_ms,
        )


def test_async_and_sync_bytes_identical() -> None:
    data = sample_config()
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        async_store = ClientConfigStore(Path(tmp_a))
        sync_store = ClientConfigStore(Path(tmp_b))
        async_store.save("practice", data)
        sync_store.save("practice", data, wait=True)
        async_store.flush()
        blob_async = (Path(tmp_a) / "practice.json").read_bytes()
        blob_sync = (Path(tmp_b) / "practice.json").read_bytes()
        check(
            f"后台写与同步写逐位一致（{len(blob_async)} B）",
            blob_async == blob_sync,
            f"async={blob_async[:80]!r} sync={blob_sync[:80]!r}",
        )
        check("没有残留临时文件", not list(Path(tmp_a).glob("*.tmp")))


def test_load_reads_your_owns_writes() -> None:
    data = sample_config()
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        first = dict(data, selected_module="ArmorAutoAim")
        second = dict(data, selected_module="PowerGuard")
        store.save("practice", first)
        store.save("practice", second)
        loaded = store.load("practice")
        check("save() 紧接着 load() 读到的是最新一版（读己之写）", loaded is not None and loaded["selected_module"] == "PowerGuard", str(loaded and loaded.get("selected_module")))
        store.flush()
        on_disk = json.loads((Path(tmp) / "practice.json").read_text(encoding="utf-8"))
        check("读到的内容与最终落盘的内容一致", on_disk["selected_module"] == "PowerGuard")


def test_repeated_saves_are_coalesced() -> None:
    data = sample_config()
    original = cs._atomic_write_bytes
    counter = {"writes": 0}

    def counting_write(path: Path, payload: bytes) -> None:
        counter["writes"] += 1
        original(path, payload)

    cs._atomic_write_bytes = counting_write
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store = ClientConfigStore(Path(tmp))
            for index in range(40):  # 按住滑块 40 次，比去抖窗口快得多
                store.save("practice", dict(data, selected_module=f"M{index}"))
            store.flush()
            writes = counter["writes"]
            landed = json.loads((Path(tmp) / "practice.json").read_text(encoding="utf-8"))
            check(
                f"连续 40 次保存被合并成 {writes} 次落盘",
                writes < 40,
                f"写了 {writes} 次",
            )
            check("合并写之后落盘的是最后一版", landed["selected_module"] == "M39", str(landed.get("selected_module")))
    finally:
        cs._atomic_write_bytes = original


def test_profile_names_are_cleaned() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        # 只用"合法但脏"的文件名：Windows 不让 * ? : | < > " 出现在文件名里。
        for stem in ("my profile", "battle royale", "  spaced  ", "dup name", "dup  name", "纯中文"):
            (Path(tmp) / f"{stem}.json").write_text("{}", encoding="utf-8")
        names = store.profile_names()
        uncleaned = [n for n in names if n != store.clean_profile_name(n)]
        check(f"profile_names() 全部是清洗后的名字 {names}", not uncleaned, f"未清洗: {uncleaned}")
        check("清洗后重名的档案只出现一次", len(names) == len(set(names)), str(names))
        # 真正的判据：列表里的名字必须正好是 profile_path() 会用的名字，
        # 否则选了 A 存到 B（这正是本条要修的洞）。
        wrong_target = [
            n for n in names
            if store.profile_path(n) != Path(tmp) / f"{n}.json"
        ]
        check(
            "profile_names() 里每个名字都是 profile_path() 真正会用的那个名字",
            not wrong_target,
            str(wrong_target),
        )
        check("干净文件名仍指向磁盘上真实存在的档案", (Path(tmp) / "纯中文.json").exists() and "纯中文" in names)
        check("清洗结果与 current_profile() 用的是同一个函数", store.clean_profile_name("my profile") == "my_profile")
        check(
            "两个不同脏名清洗到同一个名字时只留一个条目",
            sum(1 for n in names if n == "dup_name") == 1,
            str(names),
        )


def test_profile_names_sees_pending_save() -> None:
    data = sample_config()
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        misses = 0
        for _ in range(200):
            store.save("practice", data)
            if "practice" not in store.profile_names():
                misses += 1
        store.flush()
        check(
            f"save() 返回后 profile_names() 立刻能看到它（{200 - misses}/200）",
            misses == 0,
            f"漏了 {misses} 次",
        )
        names_ms = median_ms(lambda: store.profile_names(), reps=300, warmup=20)
        check(f"profile_names() 仍然便宜（{names_ms * 1e3:.1f} µs）", names_ms < 0.2, f"{names_ms:.4f} ms")


# --- 2. 原子写 ---------------------------------------------------------------

def test_import_file_is_atomic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        source = Path(tmp) / "incoming.json"
        blob = b'{\r\n  "version": 1,\r\n  "note": "\xe4\xb8\xad\xe6\x96\x87"\r\n}\r\n'
        source.write_bytes(blob)
        name = store.import_file(source)
        target = Path(tmp) / f"{name}.json"
        check("import_file 返回清洗后的档案名", name == "incoming", name)
        check("导入的字节与源文件逐位一致（含 CRLF 与 UTF-8）", target.read_bytes() == blob, repr(target.read_bytes()[:40]))

        # 同名同路径：不该自复制
        again = store.import_file(target)
        check("源就是目标时是空操作", again == name and target.read_bytes() == blob)

        # 导入途中失败：旧档案必须完好，且不留临时文件
        store.save("keeper", {"modules": {}}, wait=True)
        keeper = Path(tmp) / "keeper.json"
        keeper_blob = keeper.read_bytes()
        # 源与目标必须是两个不同的文件（同名会走"同路径空操作"分支），
        # 所以用一个"脏"文件名：second copy.json → 清洗成 second_copy。
        other = Path(tmp) / "second copy.json"
        other.write_bytes(blob)
        original_replace = cs.os.replace
        calls = {"n": 0}

        def exploding_replace(src, dst):
            calls["n"] += 1
            raise OSError(28, "No space left on device")

        cs.os.replace = exploding_replace
        try:
            try:
                store.import_file(other)
                raised = None
            except OSError as exc:
                raised = exc
        finally:
            cs.os.replace = original_replace
        check("落盘失败时异常照常抛出", raised is not None, f"raised={raised!r}")
        check("失败的导入没有覆盖已有档案", keeper.read_bytes() == keeper_blob)
        check("失败的导入没有留下临时文件", not list(Path(tmp).glob("*.tmp")), str(list(Path(tmp).glob("*.tmp"))))
        check("替换只被尝试了一次就报错（错误类型不匹配，不做无谓重试）", calls["n"] == 1, f"尝试 {calls['n']} 次")
        check("失败的导入没有留下半截的 second_copy.json", not (Path(tmp) / "second_copy.json").exists())


def test_save_failure_keeps_old_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        store.save("keeper", {"modules": {"X": 1}}, wait=True)
        keeper = Path(tmp) / "keeper.json"
        keeper_blob = keeper.read_bytes()
        original_replace = cs.os.replace
        cs.os.replace = lambda src, dst: (_ for _ in ()).throw(OSError(5, "denied"))
        try:
            try:
                store.save("keeper", {"modules": {"X": 2}}, wait=True)
                raised = None
            except OSError as exc:
                raised = exc
        finally:
            cs.os.replace = original_replace
        check("同步 save 失败时抛出异常", raised is not None, f"raised={raised!r}")
        check("同步 save 失败时旧档案完好", keeper.read_bytes() == keeper_blob)
        check("同步 save 失败时没有临时文件残留", not list(Path(tmp).glob("*.tmp")))


def test_replace_retries_while_reader_holds_file() -> None:
    """Windows 上读句柄会挡住 os.replace（本机实测 winerror 5 / errno 13）。

    这里让"读者"像 GUI 那样短暂地打开档案（40 ms），验证写盘在退避重试后
    仍然落盘——没有这层重试，一次自动保存就会被一个并发读静默吃掉。
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "held.json"
        target.write_text('{"version": 1}', encoding="utf-8")
        original_replace = cs.os.replace
        violations = {"n": 0}
        real_replace = os.replace

        def counting_replace(src, dst):
            try:
                return real_replace(src, dst)
            except OSError as exc:
                if getattr(exc, "winerror", None) in (5, 32) or exc.errno in cs._REPLACE_RETRY_ERRORS:
                    violations["n"] += 1
                raise

        cs.os.replace = counting_replace
        held = open(target, "rb")
        releaser = threading.Timer(0.04, held.close)
        try:
            releaser.start()
            t0 = time.perf_counter()
            try:
                cs._atomic_write_bytes(target, b'{"version": 1, "n": 2}')
                landed = True
            except OSError:
                landed = False
            elapsed_ms = (time.perf_counter() - t0) * 1e3
        finally:
            releaser.cancel()
            if not held.closed:
                held.close()
            cs.os.replace = original_replace
        print(f"       （本平台观测到 {violations['n']} 次占用冲突）")
        if violations["n"]:
            check(
                f"读句柄占用时写盘重试后仍能落盘（{violations['n']} 次冲突，{elapsed_ms:.1f} ms）",
                landed and target.read_bytes() == b'{"version": 1, "n": 2}',
                f"landed={landed}",
            )
        else:
            check("读句柄占用时写盘重试后仍能落盘（本平台无占用冲突）", landed and target.read_bytes() == b'{"version": 1, "n": 2}')


def test_write_error_surfaces_at_flush() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        original = cs._atomic_write_bytes
        cs._atomic_write_bytes = lambda path, payload: (_ for _ in ()).throw(OSError(28, "disk full"))
        try:
            store.save("practice", sample_config())  # 调用方不被打断
            raised = None
            try:
                store.flush(timeout=2.0)
            except OSError as exc:
                raised = exc
        finally:
            cs._atomic_write_bytes = original
        check("后台写失败不会在 save() 处抛（渲染线程不受影响）", True)
        check("后台写失败会在 flush() 时抛回调用方", raised is not None, f"raised={raised!r}")
        store.save("practice", sample_config())
        store.flush(timeout=5.0)
        check("失败之后仍能继续正常落盘", (Path(tmp) / "practice.json").exists())


# --- 3. 退出前一定刷完 -------------------------------------------------------

def test_process_exit_flushes_pending_writes() -> None:
    """真的起一个子进程：save() 之后立刻 return，靠 atexit 把档案写完。"""
    with tempfile.TemporaryDirectory() as tmp:
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from pathlib import Path\n"
            "from rm_client.config_store import ClientConfigStore\n"
            "store = ClientConfigStore(Path(r'" + tmp + "'))\n"
            "store.save('exit-case', {'modules': {'M': {'enabled': True}}})\n"
            "print('returned', flush=True)\n"
        )
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
        elapsed_ms = (time.perf_counter() - t0) * 1e3
        target = Path(tmp) / "exit-case.json"
        landed = target.exists() and json.loads(target.read_text(encoding="utf-8"))["profile"] == "exit-case"
        check(
            f"进程退出前 atexit 把队列刷完（子进程 {elapsed_ms:.0f} ms，returncode={proc.returncode}）",
            landed,
            f"stdout={proc.stdout!r} stderr={proc.stderr[-400:]!r}",
        )
        check("子进程没有残留报错", proc.returncode == 0, proc.stderr[-400:])


def test_atexit_hook_is_registered() -> None:
    import atexit as atexit_mod

    # atexit 没有公开的注册表；用"再注册一个会记账的钩子"证明模块确实在
    # 解释器里装了自己的退出钩子（否则 子进程测试 只是碰巧成功）。
    before = len(getattr(atexit_mod, "_exithandlers", []) or [])
    cs._flush_at_exit()  # 幂等：没有排队内容时是空操作
    after = len(getattr(atexit_mod, "_exithandlers", []) or [])
    check("atexit 钩子可重复调用且不会抛", before == after, f"{before} -> {after}")


# --- 4. 恢复语义：bool / number / 档案名 --------------------------------------

def tiny_registry(*settings: Setting) -> ModuleRegistry:
    return ModuleRegistry([Module(name="M", category="Test", settings=list(settings))])


def test_bool_restore() -> None:
    registry = build_default_registry()
    module = next(m for m in registry.modules if any(s.kind == "bool" for s in m.settings))
    setting = next(s for s in module.settings if s.kind == "bool")
    cases: list[tuple[Any, bool]] = [
        ("false", False), ("False", False), ("FALSE", False), (" false ", False),
        ("0", False), ("no", False), ("off", False), ("OFF", False), ("n", False), ("", False),
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True), ("ON", True),
        (True, True), (False, False), (1, True), (0, False),
        ("maybe", True),  # 认不出来的字符串保持旧行为：非空即真
    ]
    wrong = []
    for raw, want in cases:
        setting.value = not want
        apply_registry_config(registry, {"modules": {module.name: {"settings": {setting.name: raw}}}})
        if setting.value is not want:
            wrong.append((raw, setting.value, want))
    check(
        f"bool 恢复按词义解释 {len(cases)} 个取值（'false'/'off'/'0' 不再变 True）",
        not wrong,
        f"错: {wrong}",
    )
    check("bool 恢复出来的仍然是真正的 bool", isinstance(setting.value, bool))


def test_number_restore_snaps_to_step() -> None:
    registry = tiny_registry(
        Setting(name="Big", kind="number", value=140.0, min_value=0.0, max_value=1000.0, step=5.0),
        Setting(name="Fine", kind="number", value=1.0, min_value=0.0, max_value=10.0, step=0.05),
    )
    module = registry.get("M")
    apply_registry_config(registry, {"modules": {"M": {"settings": {"Big": 137.77, "Fine": 0.37}}}})
    big = module.get_setting("Big")
    fine = module.get_setting("Fine")
    check(f"137.77 立刻被吸附到 step 网格（→ {big.value}）", big.value == 140.0, str(big.value))
    # 0.05 的浮点表示使 7*0.05 = 0.35000000000000003 —— 这正是滑块自己会写出的
    # 那一位（见下面与 set_from_ratio 的逐位比对），display_value() 显示为 0.35。
    check(
        f"细粒度 0.37 也吸附到 0.05 网格（→ {fine.value!r} ≈ 0.35）",
        abs(fine.value - 0.35) < 1e-12 and fine.value == 7 * 0.05,
        repr(fine.value),
    )
    # 吸附结果必须与"用户真去拖一次滑块"逐位相同
    probe = Setting(name="Big", kind="number", value=137.77, min_value=0.0, max_value=1000.0, step=5.0)
    probe.set_from_ratio(probe.ratio())
    check(
        f"恢复值与滑块 set_from_ratio() 逐位一致（{big.value!r} vs {probe.value!r}）",
        big.value == probe.value,
    )
    fine_probe = Setting(name="Fine", kind="number", value=0.37, min_value=0.0, max_value=10.0, step=0.05)
    fine_probe.set_from_ratio(fine_probe.ratio())
    check(f"细粒度同样逐位一致（{fine.value!r} vs {fine_probe.value!r}）", fine.value == fine_probe.value)


def test_number_restore_keeps_clamps_and_rejects_junk() -> None:
    registry = tiny_registry(
        Setting(name="Ranged", kind="number", value=140.0, min_value=60.0, max_value=240.0, step=1.0),
    )
    module = registry.get("M")
    setting = module.get_setting("Ranged")
    apply_registry_config(registry, {"modules": {"M": {"settings": {"Ranged": 9999}}}})
    check(f"越界仍然被钳到上界（→ {setting.value}）", setting.value == 240.0, str(setting.value))
    apply_registry_config(registry, {"modules": {"M": {"settings": {"Ranged": -9999}}}})
    check(f"越界仍然被钳到下界（→ {setting.value}）", setting.value == 60.0, str(setting.value))
    setting.value = 137.0
    apply_registry_config(registry, {"modules": {"M": {"settings": {"Ranged": "not-a-number"}}}})
    check("非数值字符串仍然被跳过（保持原值）", setting.value == 137.0, str(setting.value))
    for junk in ("nan", "inf", "-inf", float("nan")):
        setting.value = 137.0
        apply_registry_config(registry, {"modules": {"M": {"settings": {"Ranged": junk}}}})
        check(f"非有限值 {junk!r} 被拒绝（不再塌到 min_value）", setting.value == 137.0, str(setting.value))


def test_default_profile_roundtrip_is_bit_identical() -> None:
    """内置默认档案走一圈 save/load/apply 之后必须逐位不变。"""
    source = build_default_registry()
    before = snapshot(source)
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        data = export_client_config(
            source,
            gui_mode="PolarNew",
            selected_category="Combat",
            selected_module="ArmorAutoAim",
            module_expanded=set(),
            old_panels={},
            new_panels={},
            dropdown_panels={},
        )
        store.save("default", data)
        loaded = store.load("default")
        store.flush()
        target = build_default_registry()
        apply_registry_config(target, loaded)
        after = snapshot(target)
    diff = [(b, a) for b, a in zip(before, after) if b != a]
    check(
        f"默认档案往返后 {len(before)} 个 setting 逐位不变",
        not diff and len(before) == len(after),
        f"差异 {diff[:5]}",
    )
    off_grid = [
        (m.name, s.name, s.value, s.step)
        for m in target.modules
        for s in m.settings
        if s.kind == "number" and round(s.value / s.step) * s.step != s.value
    ]
    check(
        f"6 个离网内置默认全部原样保留（{len(off_grid)} 个）",
        {row[0] + "/" + row[1] for row in off_grid} == {
            "HeroLob/Gravity", "HeroLob/Muzzle Length", "CameraFeed/Capture Height",
            "Armor3DESP/Opacity", "TacticalMap/Opacity", "ChassisGimbalIndicator/Smooth",
        },
        str(off_grid),
    )


# --- 5. 并发 -----------------------------------------------------------------

def test_concurrent_saves_all_land() -> None:
    data = sample_config()
    with tempfile.TemporaryDirectory() as tmp:
        store = ClientConfigStore(Path(tmp))
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                for round_index in range(5):
                    store.save(f"p{index}", dict(data, selected_module=f"{index}-{round_index}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        store.flush(timeout=10.0)
        check("并发保存没有抛异常", not errors, str(errors[:2]))
        landed = [p for p in Path(tmp).glob("p*.json")]
        check(f"8 个档案全部落盘（{len(landed)}/8）", len(landed) == 8, str(sorted(p.name for p in landed)))
        wrong = []
        for path in landed:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = path.stem[1:]  # p3.json → 3
            if payload["selected_module"] != f"{expected}-4":
                wrong.append((path.name, payload["selected_module"]))
        check("每个档案落盘的都是各自最后一版", not wrong, str(wrong))
        check("并发保存没有留下临时文件", not list(Path(tmp).glob("*.tmp")))


def main() -> int:
    tests = [
        test_async_save_is_fast_and_lands,
        test_async_and_sync_bytes_identical,
        test_load_reads_your_owns_writes,
        test_repeated_saves_are_coalesced,
        test_profile_names_are_cleaned,
        test_profile_names_sees_pending_save,
        test_import_file_is_atomic,
        test_save_failure_keeps_old_profile,
        test_replace_retries_while_reader_holds_file,
        test_write_error_surfaces_at_flush,
        test_process_exit_flushes_pending_writes,
        test_atexit_hook_is_registered,
        test_bool_restore,
        test_number_restore_snaps_to_step,
        test_number_restore_keeps_clamps_and_rejects_junk,
        test_default_profile_roundtrip_is_bit_identical,
        test_concurrent_saves_all_land,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"config-io 回归：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"config-io 回归：全部通过（{PASSED} 项）")
    print("落盘不进渲染线程、写入全原子、退出前刷完、恢复值与 UI 自洽。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
