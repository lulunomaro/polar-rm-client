# 模块 ↔ 后端实现 对照（通俗版，脚本生成）

> 生成命令：`python tools/module_backend_map.py`（本文件即其输出，改代码后重跑即可）
> 
> **怎么读**：一行 = 一个模块。`附近出现的段字段`=模块名附近 8 行内数到的段字段（**弱证据，只用来指路**，见第 4 节）
> `其它通路`=官方裁判 / MQTT / 本地视觉 / 串口这几条非段通路；`上行`=它会不会往车里发东西；`主要落点`=可直接点开核对的文件
> 空列仅表示该搜索窗口没有命中；不能据此证明没有数据消费者。历史 Tk 实现单列，不能算作当前 Qt 运行路径覆盖。

## 0. 先弄清楚：客户端的数据一共从哪几条路进来

| 通路 | 是什么 | 谁能给 | 缺了会怎样 |
|---|---|---|---|
| **队内 0x5A 下行** | 咱们自己电控板通过 USB 线/图传吐出的 15 个自定义段 | 队内 StandardRobot++ 固件（`usb_task.c`） | 依赖它的模块只能靠官方数据或干脆空着 |
| **官方裁判协议** | 裁判系统发给机器人的标准报文（血量、比分、伤害、增益…） | 赛场裁判系统 + `backend/protocol.py` | 战况类模块全瞎 |
| **MQTT / 图传** | 官方云端那套（PRM1） | `backend/mqtt_backend.py` | 远程指挥类模块不可用 |
| **本地视觉** | 同济视觉 / NUC 那一路识别结果 | `backend/video_backend.py` | 视觉自瞄退化成只看血量 |
| **上行（客户端发出去）** | 0x01 控制指令、0x02 调参、0x03 虚拟遥控器键位、0x04 底盘自转与云台直控 | 只有插了 USB 线（或走通图传）才到得了车 | 只能看不能开 |

## 1. 74 个模块逐个对照

列说明：**证据计数**是在模块名出现位置上下各 8 行的代码里数出来的字段/关键字出现次数，
不是官方结论，但每条都附了出处，点进去就能核对。`—` 表示该模块在这条通路上没有证据。


### 分类 Combat（7 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **ArmorAutoAim** | 开 | 7 | — | 本地视觉(同济)x5、MQTT/图传x1 | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/ui/polar_app.py` |
| **EnergyAutoAim** | 关 | 7 | — | 本地视觉(同济)x2、官方裁判协议x1 | — | `rm_client/backend/readiness.py`、`rm_client/ui/polar_app.py` |
| **ArmorLock** | 关 | 3 | — | — | — | `rm_client/ui/minecraft_hud.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **HeroLob** | 关 | 26 | — | 官方裁判协议x2 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **FireControlStub** | 关 | 3 | — | — | — | （当前搜索未找到名字证据，需核查通用或动态消费者） |
| **FrictionWheel** | 关 | 3 | — | 本地视觉(同济)x2 | — | `rm_client/ui/polar/renderers/module_list.py`、`rm_client/ui/minecraft_hud.py` |
| **LockState** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/ui/polar/renderers/module_list.py` |

### 分类 Movement（4 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **SpinMode** | 开 | 4 | 0x08 裁判警告 RefereeWarning x4 | 官方裁判协议x4、本地视觉(同济)x4、MQTT/图传x1 | — | `rm_client/auto_spin.py`、`rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **AutoSpinOnHit** | 关 | 8 | — | — | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **ChassisAssist** | 关 | 3 | — | 本地视觉(同济)x2 | — | `rm_client/control.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **PowerGuard** | 关 | 3 | — | 本地视觉(同济)x2 | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |

### 分类 Vision（8 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **CameraFeed** | 开 | 5 | — | 本地视觉(同济)x4 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/backend/manager.py` |
| **TongjiVisionBridge** | 开 | 9 | — | 本地视觉(同济)x5、MQTT/图传x2、官方裁判协议x1 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/polar_app.py` |
| **CameraCalibration** | 开 | 7 | — | — | — | `rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **Armor3DESP** | 开 | 8 | — | — | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/configuration/migrations.py` |
| **RobotOverlay** | 开 | 12 | — | 本地视觉(同济)x1 | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/ui/polar/mixins/video_backend_mixin.py` |
| **RadarOverlay** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **UWBStatus** | 关 | 1 | — | 本地视觉(同济)x1 | — | `rm_client/student_ui.py` |
| **ArmorStatus** | 关 | 1 | — | 本地视觉(同济)x1 | — | `rm_client/student_ui.py` |

### 分类 Strategy（21 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **MapMarker** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/esp_wireframe.py` |
| **TacticalMap** | 开 | 10 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/minecraft_hud.py` |
| **SentinelCommand** | 关 | 2 | — | — | — | （当前搜索未找到名字证据，需核查通用或动态消费者） |
| **AirSupport** | 关 | 2 | — | — | — | （当前搜索未找到名字证据，需核查通用或动态消费者） |
| **MatchTimer** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar_app.py` |
| **BaseOutpostStatus** | 开 | 4 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **VictoryPointStatus** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **EconomyPanel** | 开 | 7 | — | MQTT/图传x4、官方裁判协议x1、本地视觉(同济)x1 | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/scenarios.py` |
| **ProjectileShop** | 开 | 3 | 0x08 裁判警告 RefereeWarning x12、0x0A 本体状态 RobotStatus（热量/电容/转速） x6 | MQTT/图传x4、官方裁判协议x1、本地视觉(同济)x1 | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/scenarios.py` |
| **MatchChat** | 开 | 4 | — | 官方裁判协议x4、本地视觉(同济)x4 | — | `rm_client/ui/polar_app.py`、`rm_client/ui/polar/mixins/video_backend_mixin.py` |
| **BuffStatus** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/module_list.py` |
| **ProjectileAllowance** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **InjuryStatistics** | 开 | 1 | — | — | — | `rm_client/student_ui.py`、`rm_client/ui/polar/mixins/student_frontend_mixin.py` |
| **MatchSettlement** | 开 | 2 | — | — | — | `rm_client/student_ui.py`、`rm_client/ui/polar/mixins/student_frontend_mixin.py` |
| **DartPanel** | 开 | 1 | — | — | — | `rm_client/student_ui.py`、`rm_client/ui/polar/mixins/student_frontend_mixin.py` |
| **SkillConfiguration** | 开 | 5 | — | 本地视觉(同济)x2 | — | `rm_client/student_ui.py`、`rm_client/ui/student_panels.py` |
| **ReloadingPanel** | 开 | 3 | — | 本地视觉(同济)x1 | — | `rm_client/student_ui.py`、`rm_client/ui/polar/mixins/student_frontend_mixin.py` |
| **RuneRingStatus** | 开 | 1 | — | MQTT/图传x4、本地视觉(同济)x1 | — | `rm_client/student_ui.py`、`rm_client/ui/polar/renderers/dynamic_island.py` |
| **ResourceIsland** | 关 | 1 | — | — | — | `rm_client/student_ui.py` |
| **GainZone** | 关 | 1 | — | — | — | `rm_client/student_ui.py` |
| **WirelessCharging** | 关 | 1 | — | — | — | `rm_client/student_ui.py` |

### 分类 System（16 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **Scenario** | 开 | 5 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x3 | MQTT/图传x2 | — | `rm_client/scenarios.py`、`rm_client/ui/polar/renderers/startup_scene.py` |
| **StartupVisual** | 开 | 10 | — | — | — | `rm_client/ui/polar/renderers/startup_scene.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **MatchMode** | 开 | 3 | 0x08 裁判警告 RefereeWarning x3 | MQTT/图传x2 | — | `rm_client/ui/polar/mixins/persistence_mixin.py`、`rm_client/configuration/effects.py` |
| **RobotProfile** | 开 | 6 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x9、0x08 裁判警告 RefereeWarning x7 | 官方裁判协议x6、MQTT/图传x1 | — | `rm_client/robot_profiles.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **AllianceProfile** | 开 | 2 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x2 | MQTT/图传x2 | — | `rm_client/configuration/migrations.py`、`rm_client/ui/polar/renderers/tactical_map.py` |
| **ConfigManager** | 开 | 6 | — | — | — | `rm_client/ui/polar/mixins/persistence_mixin.py`、`rm_client/configuration/effects.py` |
| **EmergencyStop** | 开 | 2 | — | — | — | （当前搜索未找到名字证据，需核查通用或动态消费者） |
| **MatchReadiness** | 开 | 1 | — | 本地视觉(同济)x1 | — | `rm_client/configuration/preferences.py`、`rm_client/ui/cs2_settings_center.py` |
| **AudioFeedback** | 开 | 13 | — | — | — | `rm_client/ui/polar/mixins/config_actions_mixin.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **QuickActionWheel** | 开 | 14 | 0x08 裁判警告 RefereeWarning x3 | — | — | `rm_client/configuration/wheel.py`、`rm_client/ui/polar_app.py` |
| **MQTTLink** | 关 | 4 | — | 官方裁判协议x5、MQTT/图传x1 | — | `rm_client/backend/manager.py`、`rm_client/scenarios.py` |
| **UDPVideo** | 关 | 3 | — | — | — | `rm_client/backend/manager.py`、`rm_client/scenarios.py` |
| **SerialBridge** | 关 | 3 | — | 官方裁判协议x3、MQTT/图传x3、本地视觉(同济)x3 | — | `rm_client/scenarios.py`、`rm_client/backend/manager.py` |
| **ControllerAngles** | 关 | 8 | — | MQTT/图传x1 | — | `rm_client/backend/manager.py`、`rm_client/configuration/effects.py` |
| **RobotLink** | 关 | 47 | — | MQTT/图传x8、本地视觉(同济)x5、官方裁判协议x3 | — | `rm_client/ui/polar/mixins/config_actions_mixin.py`、`rm_client/backend/manager.py` |
| **JumpPTEWheelLES** | 开 | 4 | — | — | — | （当前搜索未找到名字证据，需核查通用或动态消费者） |

### 分类 Render（18 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **EliminationFX** | 开 | 28 | — | 本地视觉(同济)x4、官方裁判协议x1、MQTT/图传x1 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/configuration/migrations.py` |
| **LiquidTacticalMap** | 开 | 5 | — | — | — | `rm_client/ui/polar_app.py` |
| **TopMatchBar** | 开 | 10 | — | MQTT/图传x2 | — | `rm_client/ui/top_match_bar_controller.py`、`rm_client/ui/cs2_settings_center.py` |
| **ThermalPowerPanel** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **BuffTimeline** | 开 | 3 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x3 | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar_app.py` |
| **HUD** | 开 | 23 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x10、0x0D 增益 Buff x6 | 官方裁判协议x5、本地视觉(同济)x5、MQTT/图传x1 | — | `rm_client/ui/polar_app.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **UIScale** | 开 | 8 | — | — | — | `rm_client/ui/polar/mixins/persistence_mixin.py`、`rm_client/ui/polar_app.py` |
| **Crosshair** | 开 | 41 | — | — | — | `rm_client/cs2_crosshair.py`、`rm_client/ui/polar_app.py` |
| **CombatTargetHUD** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/combat_target_hud.py`、`rm_client/ui/cs2_settings_center.py` |
| **MinecraftCombatHUD** | 关 | 11 | — | — | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/student_panels.py` |
| **ChassisGimbalIndicator** | 开 | 5 | — | — | — | `rm_client/ui/polar_app.py` |
| **ClickGui** | 关 | 6 | — | 本地视觉(同济)x2、官方裁判协议x1 | — | `rm_client/ui/polar_app.py`、`rm_client/ui/jello_clickgui.py` |
| **GimbalPower** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/module_list.py` |
| **PowerLimit** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **SpeedLimit** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **Capacitor** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **MinecraftInventory** | 开 | 1 | — | 本地视觉(同济)x1 | — | `rm_client/student_ui.py`、`rm_client/ui/polar/mixins/student_frontend_mixin.py` |
| **MinecraftBuffs** | 开 | 1 | — | 本地视觉(同济)x1 | — | `rm_client/ui/student_panels.py`、`rm_client/student_ui.py` |

## 2. 当前运行源码中未找到名字证据的模块

下面这些模块在当前运行源码搜索范围内未找到模块名证据。通用处理、动态查找和跨函数数据流仍需人工核查，
不能据此宣称功能无效；历史 Tk 消费端见下一节，也不能代替当前 Qt 路径的行为测试。

| 模块 | 分类 | 设置项数 | 建议 |
|---|---|---:|---|
| **EmergencyStop** | System | 2 | 核查当前 Qt 路径的真实消费者与行为测试 |
| **FireControlStub** | Combat | 3 | 核查当前 Qt 路径的真实消费者与行为测试 |
| **SentinelCommand** | Strategy | 2 | 核查当前 Qt 路径的真实消费者与行为测试 |
| **AirSupport** | Strategy | 2 | 核查当前 Qt 路径的真实消费者与行为测试 |
| **JumpPTEWheelLES** | System | 4 | 核查当前 Qt 路径的真实消费者与行为测试 |

> 为什么这里**不列**「有 UI 落点但没数到段字段」那一档：这个客户端的数据流是
> PolarStateBridge.apply(段) 写进共享的 RobotState，渲染层再读 state.xxx，
> 而「本模块开没开」是在别处判的。名字邻近度抓不到这种跨函数数据流，所以那种空列全凭运气，
> 不作判定依据（第一版把 48 个模块全误判成「不碰机器人数据」，就是这条坑）。

## 2.1 保留的历史 Tk 实现（不计当前运行覆盖）

这里只对已验证的兼容入口做窄范围分类，不声称完成全程序可达性分析。旧文件移动到新名字不算功能改善；
AST 结构度量仍保留这些函数和复杂度，设置孤儿审计的口径也不因此自动改变。
- `rm_client/legacy_tk_app.py`：rm_client.app.main forwards directly to rm_client.ui.polar_app.main; the facade references historical symbols only inside its explicit compatibility __getattr__

| 模块 | 历史名字证据（不计当前覆盖） |
|---|---|
| ArmorAutoAim | `rm_client/legacy_tk_app.py:70`、`rm_client/legacy_tk_app.py:77`、`rm_client/legacy_tk_app.py:196` |
| CameraFeed | `rm_client/legacy_tk_app.py:24`、`rm_client/legacy_tk_app.py:62`、`rm_client/legacy_tk_app.py:163` |
| ClickGui | `rm_client/legacy_tk_app.py:611`、`rm_client/legacy_tk_app.py:617`、`rm_client/legacy_tk_app.py:633` |
| Crosshair | `rm_client/legacy_tk_app.py:200`、`rm_client/legacy_tk_app.py:224` |
| EnergyAutoAim | `rm_client/legacy_tk_app.py:77`、`rm_client/legacy_tk_app.py:146`、`rm_client/legacy_tk_app.py:196` |
| HUD | `rm_client/legacy_tk_app.py:191`、`rm_client/legacy_tk_app.py:202`、`rm_client/legacy_tk_app.py:249` |
| RobotOverlay | `rm_client/legacy_tk_app.py:77`、`rm_client/legacy_tk_app.py:196` |

## 3. 已知不准的地方（别当真到那份上）

注册表声明与翻译表不作为消费者证据：`rm_client/model.py`、`rm_client/module_catalog.py`、`rm_client/ui/jello_locale.py`。
声明移动到 module_catalog.py 不会增加实现覆盖；它仍计入 AST 源码度量。

先交代被整文件丢掉的「清单文件」（它们只列名字、不含实现）：
- ``rm_client/match_modes.py`：出现 26 个模块名，平均每 6 行就点一个（清单，不含实现）`
- ``rm_client/ui/minecraft_mod_items.py`：出现 70 个模块名，平均每 3 行就点一个（清单，不含实现）`

1. 统计窗口只有上下各 8 行、段字段要命中 >= 2 次才写表 —— 取向是「宁可少写也不要写错」，所以**空列不代表这个模块一定没读该段**，只代表附近没数到；相邻模块共用绘制代码时仍可能互相记到；
2. 通用字段名（如 `energy`、`heat`）在多个段里都有，出现次数会被各段都记一份；
3. `—` 不等于"这个模块一定没用"，只等于"在它自己那几行代码附近没找到这类关键字"；
4. 真要改某个模块，先看第 1 列模块名在 `polar_app.py` / `ui/` 里的实现，别看这张表。
## 4. 人工维护的链路索引与验证范围

本节于 2026-10-16 更新源码索引。自动生成的名字命中不证明功能接线完成；下面列出可检查的代码入口，不代表实机链路已经验收。使用函数名定位，避免重构后旧行号误导。

| 范围 | 当前源码入口 | 验证限制 |
|---|---|---|
| 队内 0x5A 命令编码/传输 | `rm_client/backend/polar_link.py`、`polar_transport.py`；`manager.py` 的 RobotLink 分派及提取的策略模块 | 离线编码/顺序对照不能替代固件接收与车辆响应验证 |
| DT7 键鼠出口 | `rm_client/backend/dt7_link.py:Dt7KeyMouseLink`（配置、编码、发送、释放）、`dt7_worker.py`（异步发送与关闭所有权） | 已存在实现；必须分别满足配置、真实发送开关、控制授权与现场资格条件 |
| 控制授权与急停 | `rm_client/backend/safety.py:OfficialSafety`、manager 的授权/撤权路径 | 应验证实际调用链，不能由注册表条目或模块名称判断急停接线 |
| 裁判/队内状态输入 | `rm_client/backend/serial_backend.py`、`polar_bridge.py`、`referee/rm2026/state_adapter.py` | 状态可来自不同输入；UI 动起来不能证明某条实体链路已接通 |
| MQTT | `rm_client/backend/mqtt_backend.py` | 回调线程、撤权通知和关闭所有权仍需专门回归；本轮无真实网络/设备资格结论 |
| 视频 | `rm_client/backend/video_backend.py`、`rm_client/ui/polar/mixins/video_backend_mixin.py` | 离线解码生命周期与原生 GPU/图传实测是不同验证范围 |
| HUD / 地图 / ESP | `rm_client/ui/polar_app.py`、`rm_client/ui/polar/renderers/` | 渲染入口和状态消费者索引不等于所有数据源或控件均完成端到端验收 |

DT7 的入口和检查工具见 `tools/dt7_link/README.md`。保留 `tools/dt7_hid_probe.py` 等侦察工具不意味着当前客户端没有 DT7 实现，也不证明任意型号/固件的 USB 协议兼容。

历史手工段中“全仓没有 DT7/HID 实现”“停发不会留下车辆旧状态”等断言不再作为当前结论。设备超时、独立输入源及无线/固件行为必须通过选定版本和实际链路验证；本轮未提供这样的硬件验收。
