#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# broker 地址（MQTTLink Host/Port）必须真的决定连到哪儿，界面不许显示没在用的地址。
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent          # 本文件在 <仓库>/tools/ 下
sys.path.insert(0, str(ROOT))

from rm_client.backend.manager import BackendManager        # noqa: E402
from rm_client.model import RobotState, build_default_registry  # noqa: E402
from rm_client.scenarios import SCENARIOS                   # noqa: E402

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    print(f"{'[ OK ]' if ok else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED += 1


class OfflineClient:
    """Paho boundary only; production start/stop/state logic is not replaced."""

    def __init__(self, *args, **kwargs):
        self.endpoint = None
        self.started = False
        self.disconnects = self.stops = 0
        self.closing, self.release = threading.Event(), threading.Event()
        self.release.set()

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect_async(self, host, port, **kwargs):
        self.endpoint = (host, port)

    def loop_start(self):
        self.started = True
        return 0

    def disconnect(self):
        self.disconnects += 1

    def loop_stop(self):
        self.stops += 1
        self.closing.set()
        if not self.release.wait(3):
            raise TimeoutError("test did not release the old network owner")
        self.started = False


def until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(.002)
    return True


def main() -> int:
    import tempfile
    print("\n--- broker 地址接线（离线 Paho 边界）---")
    clients = []
    def make_client(*args, **kwargs):
        client = OfflineClient(*args, **kwargs)
        clients.append(client)
        return client
    paho, mqtt, client_module = (ModuleType(name) for name in ("paho", "paho.mqtt", "paho.mqtt.client"))
    paho.mqtt, mqtt.client = mqtt, client_module
    client_module.Client = make_client
    client_module.CallbackAPIVersion = SimpleNamespace(VERSION2=2)
    client_module.MQTTv311 = 4
    modules = {"paho": paho, "paho.mqtt": mqtt, "paho.mqtt.client": client_module}
    with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, modules):
        manager = BackendManager(workspace=td, scenario_key="LabDebug", client_id="3")
        try:
            manager.state = RobotState()
            manager._apply_config(manager.config)
            registry = build_default_registry()
            module = registry.get("MQTTLink")
            check("MQTTLink 有 Host/Port 两项", module.get_setting("Host") is not None
                  and module.get_setting("Port") is not None)
            check("实验室场景初值 127.0.0.1:3333",
                  (manager.mqtt.host, manager.mqtt.port) == ("127.0.0.1", 3333),
                  f"{manager.mqtt.host}:{manager.mqtt.port}")
            # 1) 改设置项 -> 后端真的换地址
            module.get_setting("Host").value = "192.168.12.1"
            module.get_setting("Port").value = 3334
            manager.sync_mqtt_link(registry)
            check("改 Host/Port 设置项就换 broker（以前改了没人读）",
                  (manager.mqtt.host, manager.mqtt.port) == ("192.168.12.1", 3334))
            manager.mqtt.start()
            old = manager.mqtt.client
            check("真实生命周期把配置端点交给网络边界",
                  old is not None and old.started and old.endpoint == ("192.168.12.1", 3334))
            # 2) 延迟旧网络线程退出，验证 UI 返回、单一所有者及最新配置合并。
            # 不替换 start/stop：这类调用次数断言无法证明真正重连到了新地址。
            old.release.clear()
            module.get_setting("Host").value = "10.0.0.9"
            before = time.perf_counter()
            manager.sync_mqtt_link(registry)
            elapsed = time.perf_counter() - before
            check("已连接时换地址不会等待旧网络线程", elapsed < .05, f"{elapsed:.6f}s")
            check("旧连接未退出前不创建替代连接",
                  old.closing.wait(1) and manager.mqtt.client is None and len(clients) == 1)
            module.get_setting("Host").value = "10.0.0.10"
            module.get_setting("Port").value = 3335
            manager.sync_mqtt_link(registry)
            for _ in range(100):
                manager.sync_mqtt_link(registry)
            check("关闭期间重复设置只保留一个旧所有者和最新端点",
                  old.stops == 1 and len(clients) == 1 and manager.mqtt.client is None)
            old.release.set()
            settled = until(lambda: manager.mqtt.client is not None and manager.mqtt.client.started)
            current = manager.mqtt.client
            check("旧连接退出后真实重连到最后一次配置",
                  settled and current is not old and current.started
                  and current.endpoint == ("10.0.0.10", 3335)
                  and old.disconnects == old.stops == 1 and not old.started)
            # 3) 幂等：同一套设置不得每拍都重连
            count = len(clients)
            for _ in range(100):
                manager.sync_mqtt_link(registry)
            check("重复 tick 不会反复重连",
                  manager.mqtt.client is current and len(clients) == count and current.stops == 0)
            # 4) 非法输入 -> 沿用有效值，并回写界面。
            good = (manager.mqtt.host, manager.mqtt.port)
            module.get_setting("Host").value = "   "
            module.get_setting("Port").value = 1
            note = manager.sync_mqtt_link(registry)
            check("空地址/非法端口不改用、并写明原因",
                  (manager.mqtt.host, manager.mqtt.port) == good and "非法" in note, note)
            check("设置项被回写成真正在用的地址（界面显示的不许是假的）",
                  str(module.get_setting("Host").value) == good[0]
                  and int(module.get_setting("Port").value) == good[1]
                  and manager.mqtt.client is current and current.endpoint == good)
            # 5) 真实场景桥接：界面、连接的实际端点、输出门控必须一致。
            from rm_client.scenarios import ScenarioManager
            official = SCENARIOS["OfficialEvent"]
            sm = ScenarioManager()
            sm.set_scenario("OfficialEvent")
            current.release.clear()
            sm.apply_to_registry(registry)
            ctl = SimpleNamespace(mock_output=True)
            sm.apply_to_runtime(manager.mqtt, manager.udp, ctl)
            manager.sync_mqtt_link(registry)
            check("切到正式赛场景 -> broker 变 192.168.12.1",
                  manager.mqtt.host == official.connection.mqtt_host == "192.168.12.1")
            check("切场景仍等待旧所有者退出才替换连接",
                  current.closing.wait(1) and manager.mqtt.client is None and len(clients) == count)
            current.release.set()
            settled = until(lambda: manager.mqtt.client is not None and manager.mqtt.client.started)
            official_client = manager.mqtt.client
            check("已连接时切场景会真正重连到正式赛端点",
                  settled and official_client is not current and official_client.started
                  and official_client.endpoint == (official.connection.mqtt_host, official.connection.mqtt_port)
                  and current.disconnects == current.stops == 1 and not current.started)
            check("切场景按 allow_mqtt_publish 决定键鼠是否真发（不许写永真断言）",
                  ctl.mock_output is not bool(official.safety.allow_mqtt_publish)
                  and bool(official.safety.allow_mqtt_publish) is True,
                  f"mock={ctl.mock_output} allow_publish={official.safety.allow_mqtt_publish}")
            check("切场景后界面显示值 = 真正连接的地址",
                  (str(module.get_setting("Host").value), int(module.get_setting("Port").value))
                  == official_client.endpoint)
        finally:
            for client in clients:
                client.release.set()
            manager.shutdown()
    print("\n" + ("有失败项" if FAILED else "broker 地址接线全部通过（仅离线验证）"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
