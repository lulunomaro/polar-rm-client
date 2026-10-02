"""MQTT 端口写坏时，`sync_mqtt_link` **不该**抛异常。

# 这条钉的是一个已修的缺陷

`sync_mqtt_link` 里本来长这样（修之前）：

    try:
        want_port = int(port_setting.value)          # 250
    except (TypeError, ValueError):
        want_port = -1                               # 252
    if not 1000 <= want_port <= 9999:
        note = ... + "MQTT 端口非法，沿用 " + str(self.mqtt.port)
        want_port = int(self.mqtt.port)              # 258
    changed = self.mqtt.set_address(want_host, want_port)   # 259
    # 设置项永远等于真正在用的地址：UI 不骗人
    if str(host_setting.value) != self.mqtt.host:
        host_setting.value = str(self.mqtt.host)     # 261-262
    if int(port_setting.value or 0) != int(self.mqtt.port):  # 263  ← 又 int() 同一个坏值
        port_setting.value = int(self.mqtt.port)
    self.mqtt.last_error = note                      # 265

第 250 行把 `ValueError` 接住、退回上一个可用端口，第 258 行还写好了
一句现场提示。**然后第 263 行对同一个坏值又 `int()` 了一次**，异常从
`sync_mqtt_link` 冲出去。

而这个函数是 UI **每拍（60 Hz）** 调的（`rm_client/ui/polar_app.py:887`，
经 `_run_tick_stage` 接住）。所以操作员在 MQTTLink 的 Port 里打错一个字之后：

* 每 16 ms 记一次 `ValueError`，`backend_stats["ui_tick"]["last_error"]` 被永久钉住；
* **`self.mqtt.last_error` 永远设不上** —— 第 265 行在异常之后，第 258 行准备好的
  那句"端口非法，沿用 N"再也不会出现，操作员**看不到任何提示**。

# 注意影响范围：broker 地址本身**不受影响**

`set_address` 在第 259 行、**异常之前**就已经跑完了。所以：

* broker 地址**照旧**跟着设置项走；
* 坏掉的是**回写设置项**（第 263-264 行）与**留一句提示**（第 265 行）。

（审计的 agent 在报告里写的是"broker 地址再也不跟着设置项变"，
 实测不成立 —— 我把这一点在本文件里钉住，免得以后按那个描述去"修"一个不存在的症状。）

# 修法

第 263 行改成与第 261 行同款的 `str(...)` 比较：

    if str(port_setting.value) != str(int(self.mqtt.port)):
        port_setting.value = int(self.mqtt.port)

`int(self.mqtt.port)` 一定是刚由 `set_address` 写进去的合法整数，
所以这一步不可能抛；语义与原来完全一致（"设置项不等于真正在用的就改写成
真正在用的"），只是不再重复解析那个可能解析不了的原始值。

# 为什么要单独一个文件

因为它防的是**一类改写**：将来有人把第 261 行那种"回写设置项"的写法
换回 `int(raw)`，或者把第 250 行的 try/except 删掉，
这里都要红。而那种退化**不会让任何现有测试红** ——
`sync_mqtt_link` 的调用方把异常接住了，所以从外面看"只是多了一条错误日志"。

跑法：``python tests/mqtt_link_bad_port_test.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rm_client.backend.manager import BackendManager  # noqa: E402
from rm_client.model import build_default_registry  # noqa: E402

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


def _make() -> tuple[BackendManager, object, dict[str, object]]:
    """建一个 manager + 注册表，并把 MQTTLink 的设置项抓出来便于改。"""
    manager = BackendManager(workspace=tempfile.mkdtemp(prefix="mqttlink_"),
                             scenario_key="OfficialEvent", client_id="3")
    manager._apply_config(manager.config)
    registry = build_default_registry()
    found: dict[str, object] = {}
    for module in registry.modules:
        if module.name == "MQTTLink":
            for setting in module.settings:
                found[setting.name] = setting
    return manager, registry, found


def test_a_mistyped_port_does_not_raise() -> None:
    """**核心断言**：Port 写坏时 `sync_mqtt_link` 必须正常返回。

    修之前这里每次都抛 `ValueError`。
    """
    manager, registry, settings = _make()
    settings["Host"].value = "10.0.0.9"
    settings["Port"].value = 1883
    check("先设成一个合法值能跑通", manager.sync_mqtt_link(registry) == "",
          repr(manager.sync_mqtt_link(registry)))
    check("broker 地址真的换了", (manager.mqtt.host, manager.mqtt.port) == ("10.0.0.9", 1883),
          f"{manager.mqtt.host}:{manager.mqtt.port}")

    settings["Port"].value = "oops"
    try:
        note = manager.sync_mqtt_link(registry)
        raised = ""
    except Exception as exc:  # noqa: BLE001
        note, raised = None, f"{type(exc).__name__}: {exc}"
    check("Port 写成非数字时**不抛异常**", not raised,
          f"仍然抛 {raised} —— 这是被修掉的那个缺陷")
    check("提示里说清了非法并沿用旧端口", note is not None and "端口非法" in note,
          f"note={note!r}")
    check("提示里带上了沿用的端口号", note is not None and "1883" in note,
          f"note={note!r}")


def test_the_last_error_channel_is_not_poisoned() -> None:
    """`mqtt.last_error` 必须真的拿到那句提示。

    这是"静默失败"与"有提示"的分界：修之前第 265 行在异常之后，
    所以这句提示**永远设不上**，操作员看不到任何东西。
    """
    manager, registry, settings = _make()
    settings["Port"].value = 1883
    manager.sync_mqtt_link(registry)
    manager.mqtt.last_error = ""

    settings["Port"].value = "not-a-number"
    manager.sync_mqtt_link(registry)
    check("mqtt.last_error 收到了提示（而不是恒为空）",
          "端口非法" in manager.mqtt.last_error,
          f"last_error={manager.mqtt.last_error!r}")


def test_the_broker_address_still_follows_the_setting() -> None:
    """修好之后，**broker 地址仍然跟着设置项走**。

    这一条是为了钉住一个被误传的症状：审计报告里写的是
    "broker 地址再也不跟着设置项变"。实测不成立 ——
    `set_address` 在第 259 行、异常之前就跑完了。

    所以坏掉的只是"回写设置项"与"留一句提示"。
    把这条钉住，是为了防止以后有人照着那个描述去"修"一个不存在的症状，
    反而把本来正常的行为改坏。
    """
    manager, registry, settings = _make()
    settings["Port"].value = "oops"
    manager.sync_mqtt_link(registry)
    check("端口非法时 broker 仍保持在最后那个合法端口",
          manager.mqtt.port == 3333,
          f"port={manager.mqtt.port}，期望 3333（构造时的默认值）")

    settings["Port"].value = 1884
    manager.sync_mqtt_link(registry)
    check("改回合法值后 broker 跟着走", manager.mqtt.port == 1884,
          f"port={manager.mqtt.port}，期望 1884")


def test_the_setting_is_written_back_so_the_ui_does_not_lie() -> None:
    """设置项必须被回写成真正在用的端口 —— 这是那行代码的本意。

    注释写的是"设置项永远等于真正在用的地址：UI 不骗人"。
    修之前那行用 `int(raw)` 比较，坏端口时比较本身就抛，
    所以**回写根本没发生**。
    """
    manager, registry, settings = _make()
    settings["Port"].value = 1885
    manager.sync_mqtt_link(registry)
    check("合法端口原样留在设置项里", str(settings["Port"].value) == "1885",
          f"{settings['Port'].value!r}")

    settings["Port"].value = "garbage"
    manager.sync_mqtt_link(registry)
    check("非法端口被回写成真正在用的那个（UI 不骗人）",
          str(settings["Port"].value) == "1885",
          f"{settings['Port'].value!r}，期望被回写成 '1885'")


def test_both_synonym_forms_are_handled() -> None:
    """空串、`None`、浮点形态也要走同一条路。

    `int(port_setting.value or 0)` 对空串会得到 `0`、对 `None` 也得到 `0`，
    都落在"非法"那一侧；修完之后这些形态都必须**同样不抛**。
    """
    manager, registry, settings = _make()
    settings["Port"].value = 1886
    manager.sync_mqtt_link(registry)

    for label, value in (("空串", ""), ("None", None), ("浮点", 1886.7)):
        settings["Port"].value = value
        try:
            manager.sync_mqtt_link(registry)
            outcome = "不抛"
        except Exception as exc:  # noqa: BLE001
            outcome = f"抛 {type(exc).__name__}"
        check(f"Port = {label} 时{outcome}且回写成 1886",
              outcome == "不抛" and str(settings["Port"].value) == "1886",
              f"{outcome}，设置项={settings['Port'].value!r}")


def main() -> int:
    tests = [
        test_a_mistyped_port_does_not_raise,
        test_the_last_error_channel_is_not_poisoned,
        test_the_broker_address_still_follows_the_setting,
        test_the_setting_is_written_back_so_the_ui_does_not_lie,
        test_both_synonym_forms_are_handled,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(f"{test.__name__} 正常跑完", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"MQTT 端口坏值：{len(FAILURES)} 项未通过 / 共 {PASSED + len(FAILURES)} 项")
        for line in FAILURES:
            print(f"  - {line}")
        return 1
    print(f"MQTT 端口坏值：全部通过（{PASSED} 项）")
    print("端口写坏时：提示给出来、设置项被回写、broker 地址照旧跟着走。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())