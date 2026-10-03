"""POLAR-RM 后端包：对齐 SharkClient 的后端能力（协议/通信/安全）。

对外只暴露 BackendManager，三种运行模式（LabDebug/CampusSimulation/OfficialEvent）
由 BackendManager.apply_scenario() 统一编排。

第 106 轮：这些**再导出改成惰性**（PEP 562 `__getattr__`）。

原来的写法在模块级把八个后端全部导入，于是**导入任何一个子模块都要先拖进全部**：

    import rm_client.backend.polar_spec   ->  87.5 ms，拉进 28 个 rm_client 模块

`polar_spec` 是个 336 行的纯数据叶子模块，**它自己什么都不依赖**。

改成惰性之后：**只有在真的用到某个名字时才导入它**，
`from rm_client.backend import BackendManager` 这种写法**照旧能用**（`__getattr__` 接住），
`from rm_client.backend import field_log` 这种取子模块的**也照旧能用**（子模块本来就不走这条路径）。

本仓库 43 处 `from rm_client.backend import ...` 里，绝大多数要的其实是**子模块**，
它们本来就不需要这些再导出 —— 代价却照付。
"""

from typing import Any

# 名字 -> 所在子模块。惰性导入按这张表解析。
_LAZY: dict[str, str] = {
    "BackendManager": "manager",
    "OfficialSafety": "safety",
    "ControlState": "safety",
    "QualificationToken": "safety",
    "RmMqttBackend": "mqtt_backend",
    "RmVideoBackend": "video_backend",
    "RmSerialBackend": "serial_backend",
    "BackendReadinessReport": "readiness",
    "ReadinessGate": "readiness",
    "audit_backend": "readiness",
    "Capability": "qualification",
    "CapabilityAuthorization": "qualification",
    "QualificationEvidenceStore": "qualification",
    "QualificationManager": "qualification",
    "QualificationSession": "qualification",
    "TestResult": "qualification",
    "TestStatus": "qualification",
}

__all__ = [
    "BackendManager",
    "OfficialSafety",
    "ControlState",
    "QualificationToken",
    "RmMqttBackend",
    "RmVideoBackend",
    "RmSerialBackend",
    "BackendReadinessReport",
    "ReadinessGate",
    "audit_backend",
    "Capability",
    "CapabilityAuthorization",
    "QualificationEvidenceStore",
    "QualificationManager",
    "QualificationSession",
    "TestResult",
    "TestStatus",
    "protocol",
]


def __getattr__(name: str) -> Any:
    """PEP 562：只有在**真的取这个名字**时才导入它所在的子模块。"""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value          # 缓存：第二次取就不再走这里
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_LAZY))
