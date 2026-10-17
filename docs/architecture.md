# 当前架构与所有权

本文记录当前 Python 客户端的装配关系和修改边界，不宣称已经完成分层重写。
完整模块功能映射见 [MODULE_BACKEND_MAP.md](../MODULE_BACKEND_MAP.md)，
工程约定见 [CONTRIBUTING.md](../CONTRIBUTING.md)。蓝图中的旧行号和阶段计划
不是当前代码位置，应从下面的文件/符号导航。

[第一阶段报告](client_quality_20261016.md) 保留当时快照与验证结果；日志和 MQTT
生命周期的后续变化见 [第二阶段报告](client_lifecycle_20261016.md)。不能把后续实现
或测试结果倒填为第一阶段已经完成的证据。

## 入口与窗口

1. [main.py](../main.py) 是支持的运行入口。只读现场日志回放在 GUI 导入之前
   分流；队内链路自检、运行目录准备和 preflight 也在正常 GUI 启动前处理。
   preflight 不播种用户配置，正常启动才在目标不存在时复制默认配置。
2. `main.py` 导入 `rm_client.ui.polar_app.main`。这个兼容导出来自
   [polar/app_entry.py](../rm_client/ui/polar/app_entry.py)，负责 QApplication、
   OpenGL surface 配置、创建/显示 `PolarRMWindow` 及启动事件循环。
   `PolarRMWindow` 在函数内延迟导入，避免 re-export 的循环初始化。
3. [polar_app.py](../rm_client/ui/polar_app.py) 仍拥有窗口构造、主 tick、GPU 帧
   编排、CPU overlay 分发和关闭次序。`polar/renderers/` 与 `polar/mixins/`
   分出了绘制/交互职责，但多数方法仍通过同一个 `self` 访问窗口状态。
4. [app.py](../rm_client/app.py) 是惰性兼容门面，`app.main()` 与模块执行都委托 Qt。
   历史 Tk 实现位于 [legacy_tk_app.py](../rm_client/legacy_tk_app.py)，仅显式访问旧导出时加载。
   旧 Tk 类和旧 transport 示例不计作当前 Qt 运行路径。

整洁代码重构已把时钟和调度决策放入 [tick_controller.py](../rm_client/ui/polar/tick_controller.py)，
把面板动画决策放入 [presentation_controller.py](../rm_client/ui/polar/presentation_controller.py)，
把不可变叠层计划与最终合成顺序放入 [compositor_plan.py](../rm_client/ui/polar/compositor_plan.py)。
窗口仍承担 Qt 所有权和阶段适配，不应把这些提取描述成所有窗口状态已完全解耦。

`rm_client.ui.polar_app` 的导出与窗口成员有显式兼容契约，完整历史快照作为可选审计保留；重排类、Mixin MRO、
初始化顺序或导出时，要检查调用点并运行相关回归，不能仅改 import 就认为兼容。

## 数据、配置与 UI

- [model.py](../rm_client/model.py) 定义 `RobotState`、模块注册表和设置。
  `scenarios.py`、`match_modes.py`、`robot_profiles.py` 提供不同维度的默认值；
  配置覆盖与运行时同步仍由窗口协作对象完成，不是独立的纯模型层。
- `RobotState` 同时承载遥测、仿真值和前端状态。新增字段必须标明真实来源、单位、
  新鲜度、默认/失联行为；界面显示成功不能反过来当作设备确认。
- `BackendManager.apply_updates()` 是官方 MQTT 解码队列的消费入口，调用年度
  adapter 应用到状态后再通知只读 `referee_view_observer`。展示观察者不可抢队列、
  二次应用消息或决定控制授权。
- 当前逻辑 tick 先执行 `state.tick()`，再进行后端应用、业务/前端处理和模块 tick，
  最后处理队内链路及 overlay。队内 `polar_*` 状态不能提前写入再被仿真覆盖，
  也不能覆盖官方字段的所有权或改变其容器类型。
- [state_overlay.py](../rm_client/state_overlay.py) 对写入模型的队内遥测实施显式
  字段/类型契约，并复制可变容器。新增内建模型字段需要扩展契约；JSON 规格中的
  通用扩展仍可保留在 `polar_overlay` 中供消费者读取，不获得任意属性写权限。
- `KeyboardControlBridge` 的 75 Hz 控制检查与 60 Hz 逻辑检查在窗口计时器中分别
  调度；这不是硬实时线程保证，实际频率仍受事件循环和运行环境约束。

## 后端与控制出口

[BackendManager](../rm_client/backend/manager.py) 统一装配和协调以下对象。
窗口保留的 `mqtt`、`udp`、`serial` 属性指向 manager 的实例，不是第二套连接。

| 职责 | 当前实现 | 主要边界 |
| --- | --- | --- |
| 裁判 MQTT | `RmMqttBackend` | profile 校验/解码、有界接收队列、重连、发布安全门禁 |
| Shark UDP 图传 | `RmVideoBackend` | 分片重组、解码、最新帧与时效；不重复组包 |
| 原生 `0x0310` 图传 | manager + `ContinuousMjpegDecoder("h264")` | 独立解码器、序号/恢复门禁；等待可恢复码流 |
| 官方 `0xA5` 串口 | `RmSerialBackend` | 读线程、帧编解码、mock/真实写；拒绝 `0x0310/0x0311` |
| 队内 `0x5A` 链路 | `PolarLinkTransport` + `PolarStateBridge` | 独立端口/连接状态、段映射、驾驶授权和归零 |
| DT7 / 自定义键鼠 | `AsyncDt7KeyMouseLink` / `AsyncCustomControllerLink` | worker 拥有真实串口，输入意图与写完成分开 |
| 实体控制器角度 | `ControllerAngleRelay` | 独立 RX/TX、显式端口、Arm/超时；不取代键鼠链路 |

年度裁判差异通过 `backend/referee/registry.py` 中的 profile 与年度 adapter
处理。队内协议以 `backend/polar_protocol.json` 为规格；不要把段格式散到
manager、transport 或绘制代码中。需要计算/限幅的特殊映射仍可能需要专用 handler，
不能承诺任意协议变化都只改 JSON。

`transports.py` 中的 `OfficialMqttBridge`、`UDPVideoReceiver`、`SerialBridgeStub`
仍是 `Rm*Backend` 继承的基础/兼容实现。它们不是三个待实现的生产占位符；
`MQTTTransport` 也不是当前窗口装配的 MQTT 后端。

`OfficialSafety`、资格证据和控制状态共同限制真实输出。队内 `0x5A` 与官方
裁判/MQTT 通道并行，不能用场景名代替单个链路授权。云台出口归属由
`BackendManager.gimbal_egress_owner()` 统一裁决，不能让官方与队内出口各自决定
发送同一份目标。开火、键位、断流、物理遥控优先等约束见 [AGENT.md](../AGENT.md)。

## 线程、资源与关闭

- 摄像头采集、壁纸解码、本地视觉、网络接收和若干设备写入有各自 worker。
  UI 负责消费结果和 Qt/GL 操作；新增路径先确认所属线程，不把阻塞工作搬回帧回调。
- DT7/自定义键鼠 worker 使用有界意图邮箱和归零优先逻辑，处理慢打开、过期输入、
  generation 变化和完成通知。已排队、已写出、归零确认和失败是不同状态。
- POLAR 连接使用异步请求与单一连接所有权；旧连接/清理仍占有资源时不能启动替代者。
  manager 普通帧路径不会等 POLAR 驱动持有的 TX 锁。
- `RmMqttBackend` 的自动重连、地址和裁判 profile 变更使用 `stop(wait=False)`：
  先撤销当前客户端/接收 epoch 并锁控制，再让唯一清理 worker 等旧 Paho 网络循环
  退出。期间启动请求合并为最新配置，清理未完成或失败时不创建竞争客户端。
  这只描述 MQTT 后端的交互重启路径，不代表完整场景切换已经异步化。
- OpenGL 资源必须在正确上下文创建/释放。尺寸、DPR、存储替换或语言/设置变化后，
  缓存失效要覆盖实际依赖；复用缓存不等于可以复用过期 framebuffer 内容。
- `PolarRMWindow.closeEvent()` 先停计时器、锁控制/释放输入、急停并关闭后端，
  再保存配置、释放 GL 和其他 worker。单项清理异常不能阻断后续安全清理。

这些路径的详细约束与剩余同步关闭限制见
[UI-thread device I/O isolation](ui_device_io_hardening.md)。测试 fake 驱动的低延迟
不能推导出真实 Windows 驱动、GPU 或串口关闭的上界。

## 路径与运行数据

[RuntimePaths](../rm_client/runtime_paths.py) 是源码/便携版的路径入口：

| 内容 | 源码模式默认值 | 便携包默认值 |
| --- | --- | --- |
| 只读资源根 | 仓库根 | `resources/` |
| 默认配置模板 | `configs/` | `defaults/` |
| 实际配置/标定/日志 | `user_data/configs/`、`user_data/calibrations/`、`user_data/logs/` | 同左 |
| manager 资格证据 | `user_data/field_evidence/` | `field_evidence/` |
| FFmpeg | `bin/ffmpeg.exe` | `bin/ffmpeg.exe`，发布清单校验 |

源码模式可用 `POLAR_USER_DATA_ROOT` 隔离运行数据；`POLAR_APP_ROOT` 是另一种
应用根/便携布局覆盖，不能拿它当同义的临时用户目录。默认配置只在目标不存在时
复制，不应在正常保存、测试或 preflight 时改写模板。

独立 CLI 有显式输出参数，不一定使用上表的 manager 目录。例如
`--polar-selftest` 默认写当前工作目录的 `field_evidence/`，回放默认生成
`POLAR-field-replay-summary.json`。测试/工具应传临时输出路径，不能把这些生成物
当成源文件。日志可能包含裁判载荷和设备诊断，提交前必须做范围与隐私检查。

### 现场日志的接收、写入与关闭

[RotatingJsonlLogger](../rm_client/backend/field_log.py) 由单一、惰性启动的 writer
拥有目录创建、轮转、open/write 等文件系统操作；空闲后可退出，再次有记录时重新启动。
生产者 `emit()` 只构造受大小/深度/节点数量限制的记录快照并尝试入队，返回值表示
**是否接收记录**，不能解释为写入成功。序列化、短状态锁和首次线程启动仍发生在调用路径，
因此也不是硬实时延迟保证。

- 默认上限为 256 条 / 4 MiB，包含正在写入的记录；队列满时拒绝新记录，不等待磁盘。
  非法/超限记录整条拒绝，视频载荷事件仍排除，裁判字节仍限制为 65536 字节。
- `snapshot()` 返回独立的只读统计，区分 accepted、written、pending、各种丢弃、
  写错误及等待超时。后续写入成功不会抹去累计丢失/错误，日志缺口必须对验收可见。
- `flush(timeout=...)` 等待调用前已接收记录的完成屏障；`close(timeout=..., drain=True)`
  先停止接收再等待清理。`drain=False` 会计数取消的排队记录，不能取消已进入 OS 的写入。
  返回 false 可能表示超时，也可能表示此前累计丢失/错误；需结合统计判断。
- 这些显式等待不用于普通帧/回调。manager 场景重启保留 logger，不 flush/close；
  最终关闭才给 logger 1 秒排空预算，失败时报告诊断。超时的 writer 仍保留所有权，
  不另起竞争 writer。写成功只表示 OS 接受，不包含 fsync 或断电持久性保证。

## 已知边界与后续拆分原则

- `PolarRMWindow`、`BackendManager`、`RobotState` 仍是大对象。Mixin 抽取减少了
  单文件体积，但没有自动消除共享可变状态、构造顺序或模块间耦合。
- manager 的机器人身份变更、场景切换与最终关闭仍有同步停止/归零/等待；`RmSerialBackend` 还保留同步
  open/write/close API，MQTT 默认 `stop(wait=True)` 仍等待 Paho 清理。
  现场日志文件操作已移到 writer，但显式 flush/close、配置/证据文件与其他同步
  生命周期操作不因此自动异步化。不要宣传为“全程无阻塞”或整个退出流程已有固定上界。
- 兼容类、fallback、mock 与真实路径同时存在。删除或合并前必须查清生产调用、
  现场工具、测试和发布入口；测试仅走 mock 不证明真实分支等价。
- 新拆分应同时收窄依赖和所有权，优先提取纯转换、明确的状态机或有生命周期的
  服务。每一步保留可运行入口与回归证据，不把批量搬文件等同于架构修复完成。
- 当前的离线回归、桌面渲染、发布预检和真车资格是分开的验收层次。
  缺少现场证据时仍保持 [BACKEND_MATCH_READINESS.md](../BACKEND_MATCH_READINESS.md)
  的 **NOT READY FOR REAL MATCH** 结论。
