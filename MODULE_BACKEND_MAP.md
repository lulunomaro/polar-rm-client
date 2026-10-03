# 模块 ↔ 后端实现 对照（通俗版，脚本生成）

> 生成命令：`python tools/module_backend_map.py`（本文件即其输出）　过期自检：`python tools/module_backend_map.py --check`（exit 1 = 该重跑了）
> 
> **改了 `rm_client/` 下任何一个文件都要重跑**：这张表是数出来的，代码一动证据就变，而过期的行号照样能点开、只是点到了别的函数——没人看得出它过期了，所以它由 `--check` / `tests/docs_and_tests_test.py` 把关。
> 
> **怎么读**：一行 = 一个模块。`附近出现的段字段`=模块名附近 8 行内数到的段字段（**弱证据，只用来指路**，见第 4 节）
> `其它通路`=官方裁判 / MQTT / 本地视觉 / 串口这几条非段通路；`上行`=它会不会往车里发东西；`主要落点`=可直接点开核对的文件
> 整行数据列全空 = 这个模块后端其实没人喂数据

## 0. 先弄清楚：客户端的数据一共从哪几条路进来

| 通路 | 是什么 | 谁能给 | 缺了会怎样 |
|---|---|---|---|
| **队内 0x5A 下行** | 咱们自己电控板通过 USB 线/图传吐出的 15 个自定义段 | 队内 StandardRobot++ 固件（`usb_task.c`） | 依赖它的模块只能靠官方数据或干脆空着 |
| **官方裁判协议** | 裁判系统发给机器人的标准报文（血量、比分、伤害、增益…） | 赛场裁判系统 + `backend/protocol.py` | 战况类模块全瞎 |
| **MQTT / 图传** | 官方云端那套（PRM1） | `backend/mqtt_backend.py` | 远程指挥类模块不可用 |
| **本地视觉** | 同济视觉 / NUC 那一路识别结果 | `backend/video_backend.py` | 视觉自瞄退化成只看血量 |
| **上行（客户端发出去）** | 0x01 控制指令、0x02 调参、0x03 虚拟遥控器键位、0x04 底盘自转与云台直控 | 只有插了 USB 线（或走通图传）才到得了车 | 只能看不能开 |

## 1. 59 个模块逐个对照

列说明：**证据计数**是在模块名出现位置上下各 8 行的代码里数出来的字段/关键字出现次数，
不是官方结论，但每条都附了出处，点进去就能核对。`—` 表示该模块在这条通路上没有证据。


### 分类 Combat（7 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **ArmorAutoAim** | 开 | 7 | — | 本地视觉(同济)x4、MQTT/图传x1 | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/ui/polar_app.py` |
| **EnergyAutoAim** | 关 | 7 | — | 本地视觉(同济)x4、官方裁判协议x1 | — | `rm_client/app.py`、`rm_client/backend/readiness.py` |
| **ArmorLock** | 关 | 3 | — | — | — | `rm_client/ui/minecraft_hud.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **HeroLob** | 关 | 26 | — | 官方裁判协议x2 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **FireControlStub** | 关 | 3 | — | — | — | （只在注册表里出现，**没有消费端**） |
| **FrictionWheel** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/module_list.py`、`rm_client/ui/minecraft_hud.py` |
| **LockState** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/ui/polar/renderers/module_list.py` |

### 分类 Movement（4 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **SpinMode** | 开 | 4 | — | 官方裁判协议x3、MQTT/图传x2、本地视觉(同济)x1 | — | `rm_client/auto_spin.py`、`rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **AutoSpinOnHit** | 关 | 8 | — | — | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **ChassisAssist** | 关 | 3 | — | — | — | `rm_client/control.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **PowerGuard** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |

### 分类 Vision（6 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **CameraFeed** | 开 | 5 | — | 本地视觉(同济)x6、MQTT/图传x1 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/app.py` |
| **TongjiVisionBridge** | 开 | 9 | — | 本地视觉(同济)x6、MQTT/图传x2、官方裁判协议x1 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/vision.py` |
| **CameraCalibration** | 开 | 7 | — | — | — | `rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **Armor3DESP** | 开 | 8 | — | — | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/config_store.py` |
| **RobotOverlay** | 开 | 12 | — | 本地视觉(同济)x2 | — | `rm_client/ui/polar/renderers/esp_wireframe.py`、`rm_client/app.py` |
| **RadarOverlay** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |

### 分类 Strategy（12 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **MapMarker** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/esp_wireframe.py` |
| **TacticalMap** | 开 | 10 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/minecraft_hud.py` |
| **SentinelCommand** | 关 | 2 | — | — | — | （只在注册表里出现，**没有消费端**） |
| **AirSupport** | 关 | 2 | — | — | — | （只在注册表里出现，**没有消费端**） |
| **MatchTimer** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar_app.py` |
| **BaseOutpostStatus** | 开 | 4 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **VictoryPointStatus** | 关 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **EconomyPanel** | 开 | 5 | — | MQTT/图传x4、官方裁判协议x1、本地视觉(同济)x1 | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/scenarios.py` |
| **ProjectileShop** | 开 | 3 | 0x08 裁判警告 RefereeWarning x12、0x0A 本体状态 RobotStatus（热量/电容/转速） x6 | MQTT/图传x4、官方裁判协议x1、本地视觉(同济)x1 | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/scenarios.py` |
| **MatchChat** | 开 | 4 | — | 官方裁判协议x4、本地视觉(同济)x4 | — | `rm_client/ui/polar_app.py`、`rm_client/ui/polar/mixins/video_backend_mixin.py` |
| **BuffStatus** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/module_list.py` |
| **ProjectileAllowance** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |

### 分类 System（15 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **Scenario** | 开 | 5 | — | MQTT/图传x6 | — | `rm_client/scenarios.py`、`rm_client/ui/polar/renderers/startup_scene.py` |
| **StartupVisual** | 开 | 10 | — | — | — | `rm_client/ui/polar/renderers/startup_scene.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **MatchMode** | 开 | 3 | — | MQTT/图传x2 | — | `rm_client/config_store.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **RobotProfile** | 开 | 6 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x5、0x08 裁判警告 RefereeWarning x4 | 官方裁判协议x1 | — | `rm_client/robot_profiles.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **AllianceProfile** | 开 | 2 | — | MQTT/图传x1 | — | `rm_client/config_store.py`、`rm_client/ui/polar/renderers/tactical_map.py` |
| **ConfigManager** | 开 | 6 | — | — | — | `rm_client/ui/polar/mixins/persistence_mixin.py`、`rm_client/ui/polar/mixins/input_dispatch_mixin.py` |
| **EmergencyStop** | 开 | 2 | — | — | — | （只在注册表里出现，**没有消费端**） |
| **MatchReadiness** | 开 | 1 | — | — | — | （只在注册表里出现，**没有消费端**） |
| **AudioFeedback** | 开 | 13 | — | — | — | `rm_client/ui/polar/mixins/config_actions_mixin.py`、`rm_client/ui/polar/mixins/persistence_mixin.py` |
| **QuickActionWheel** | 开 | 14 | — | — | — | `rm_client/ui/polar_app.py`、`rm_client/ui/polar/mixins/input_dispatch_mixin.py` |
| **MQTTLink** | 关 | 3 | — | MQTT/图传x5 | — | `rm_client/backend/manager.py`、`rm_client/scenarios.py` |
| **UDPVideo** | 关 | 3 | — | — | — | `rm_client/scenarios.py` |
| **SerialBridge** | 关 | 3 | — | 官方裁判协议x3、MQTT/图传x3、本地视觉(同济)x3 | — | `rm_client/scenarios.py` |
| **RobotLink** | 关 | 44 | — | MQTT/图传x7、本地视觉(同济)x2、串口链路x2、官方裁判协议x1 | — | `rm_client/backend/manager.py`、`rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **JumpPTEWheelLES** | 开 | 4 | — | — | — | （只在注册表里出现，**没有消费端**） |

### 分类 Render（15 个）

| 模块 | 开关 | 设置项 | 附近出现的段字段（弱证据） | 其它通路 | 上行 | 主要落点 |
|---|---|---:|---|---|---|---|
| **EliminationFX** | 开 | 28 | — | 本地视觉(同济)x4、官方裁判协议x1、MQTT/图传x1 | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/config_store.py` |
| **LiquidTacticalMap** | 开 | 5 | — | — | — | `rm_client/ui/polar_app.py` |
| **ThermalPowerPanel** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py` |
| **BuffTimeline** | 开 | 3 | 0x0A 本体状态 RobotStatus（热量/电容/转速） x3 | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar_app.py` |
| **HUD** | 开 | 23 | 0x0D 增益 Buff x6、0x0A 本体状态 RobotStatus（热量/电容/转速） x5 | 本地视觉(同济)x10、官方裁判协议x5 | — | `rm_client/ui/polar_app.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **UIScale** | 开 | 8 | — | — | — | `rm_client/ui/polar/mixins/persistence_mixin.py`、`rm_client/ui/polar_app.py` |
| **Crosshair** | 开 | 3 | — | — | — | `rm_client/ui/polar_app.py`、`rm_client/app.py` |
| **CombatTargetHUD** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/combat_target_hud.py` |
| **MinecraftCombatHUD** | 关 | 11 | — | — | — | `rm_client/ui/polar/mixins/video_backend_mixin.py`、`rm_client/ui/polar/mixins/config_actions_mixin.py` |
| **ChassisGimbalIndicator** | 开 | 5 | — | — | — | `rm_client/ui/polar_app.py` |
| **ClickGui** | 关 | 5 | — | 本地视觉(同济)x2、官方裁判协议x1 | — | `rm_client/ui/polar_app.py`、`rm_client/ui/jello_clickgui.py` |
| **GimbalPower** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/module_list.py` |
| **PowerLimit** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **SpeedLimit** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |
| **Capacitor** | 开 | 3 | — | — | — | `rm_client/ui/polar/renderers/tactical_map.py`、`rm_client/ui/polar/renderers/module_list.py` |

## 2. 真·没有后端的模块

下面这些模块的名字**只出现在注册表**（rm_client/model.py 的 build_default_registry()）里，
全仓没有任何实现读它 —— 界面上能勾、能存盘，但什么都不会发生。

| 模块 | 分类 | 设置项数 | 建议 |
|---|---|---:|---|
| **EmergencyStop** | System | 2 | 要么接上、要么从界面删掉；留着比删掉更坏：操作员会以为它在起作用 |
| **MatchReadiness** | System | 1 | 要么接上、要么从界面删掉；留着比删掉更坏：操作员会以为它在起作用 |
| **FireControlStub** | Combat | 3 | 要么接上、要么从界面删掉；留着比删掉更坏：操作员会以为它在起作用 |
| **SentinelCommand** | Strategy | 2 | 要么接上、要么从界面删掉；留着比删掉更坏：操作员会以为它在起作用 |
| **AirSupport** | Strategy | 2 | 要么接上、要么从界面删掉；留着比删掉更坏：操作员会以为它在起作用 |
| **JumpPTEWheelLES** | System | 4 | 要么接上、要么从界面删掉；留着比删掉更坏：操作员会以为它在起作用 |

> 为什么这里**不列**「有 UI 落点但没数到段字段」那一档：这个客户端的数据流是
> PolarStateBridge.apply(段) 写进共享的 RobotState，渲染层再读 state.xxx，
> 而「本模块开没开」是在别处判的。名字邻近度抓不到这种跨函数数据流，所以那种空列全凭运气，
> 不作判定依据（第一版把 48 个模块全误判成「不碰机器人数据」，就是这条坑）。

## 3. 已知不准的地方（别当真到那份上）

先交代被整文件丢掉的「清单文件」（它们只列名字、不含实现）：
- `rm_client/match_modes.py`：出现 26 个模块名，平均每 6 行就点一个（清单，不含实现）

1. 统计窗口只有上下各 8 行、段字段要命中 >= 2 次才写表 —— 取向是「宁可少写也不要写错」，所以**空列不代表这个模块一定没读该段**，只代表附近没数到；相邻模块共用绘制代码时仍可能互相记到；
2. 通用字段名（如 `energy`、`heat`）在多个段里都有，出现次数会被各段都记一份；
3. `—` 不等于"这个模块一定没用"，只等于"在它自己那几行代码附近没找到这类关键字"；
4. 真要改某个模块，先看第 1 列模块名在 `polar_app.py` / `ui/` 里的实现，别看这张表。

## 4. 重点模块：数据到底从哪个函数进来（人写的，行号生成时现查）

第 1 节是机器数出来的，只会指路；这一节是逐条核过代码写的。要改哪个模块，先看这里。

> 每一处行号都由 `locate()` 在**生成这一刻**现查 `rm_client/` 源码得到，
> 脚本里只写「去哪找」（文件 + 那一行原文），不写死行号。
> 真出现 `未找到` / `缺失` 就是符号被改名或文件被删了，**不要**沿用上一版的旧数字。

### 开车 / 控车这条线（队内 0x5A 上行）

| 模块 | 后端实现在哪 | 说人话 |
|---|---|---|
| **RobotLink** | 编码：`rm_client/backend/polar_link.py:617 encode_robot_cmd`(0x01)、`<br>`rm_client/backend/polar_link.py:656 encode_virtual_rc`(0x03 键位图)、`<br>`rm_client/backend/polar_link.py:698 encode_chassis_cmd`(0x04 自转+云台直控)；`<br>`成帧与写串口：`rm_client/backend/polar_transport.py:549 _write_after_intent_held`(分派)、`rm_client/backend/polar_transport.py:592 _frame_for`(0x01 成帧)、`rm_client/backend/polar_transport.py:583 _take_intent_locked`(取意图+死区)；`<br>自转开关联动：`rm_client/backend/manager.py:464 _spin_assert 落定（自转开关联动判定）` | 整条「客户端往车里发东西」的路都归它。松键时它会补发一次全零（`rm_client/backend/polar_transport.py:581 松键下降沿补发全零 0x03`），所以固件那边不会因为停发而冻在最后一帧 |
| **SpinMode** / **AutoSpinOnHit** | `rm_client/auto_spin.py:42 ProjectileHitSpinController`（受击后限时开自转，判定在 `rm_client/auto_spin.py:74 update（受击后限时开自转的判定）`）→ 上面那个 `encode_chassis_cmd()`（上行 0x04）；状态回读是固件下行 0x1F | 以前只能借 SHIFT 键，而固件对 SHIFT 是边沿翻转 —— **关不掉**；现在是显式 ON/OFF + 回读闭环 |
| **EmergencyStop** | 行为在 `backend/safety.py`(OfficialSafety) 与 `rm_client/backend/manager.py:2276 release_estop`；但注册表里那个 `EmergencyStop` 模块**没有任何代码读它** | 急停本身是真有的，只是「界面那个模块条目」和它之间没接线 —— 界面上那个开关是装饰 |

### 看数据这条线

| 模块 | 后端实现在哪 | 说人话 |
|---|---|---|
| **HUD** / **CombatTargetHUD** / **RobotOverlay** / **RadarOverlay** / **TacticalMap** | 渲染在 `ui/polar_app.py` 与 `ui/polar/renderers/*`；数据一律读共享的 `RobotState`，由 `backend/polar_bridge.py` 的 `apply(segment)` 按段号写进来 | 队内 15 个下行段和官方裁判报文**汇到同一个 RobotState**，所以这些面板不区分数据是谁给的 —— 这也是「没插 USB 线时界面照样动」的原因 |
| **BuffTimeline** | `ui/polar/renderers/tactical_map.py`（地图下方那条带）；数据是下行 0x0D Buff | 这个模块以前一个消费者都没有，现在才真有一排标签；带上的剩余秒数就是「时间轴」开关管的东西 |
| **CameraFeed** / **TongjiVisionBridge** / **Armor3DESP** | `backend/video_backend.py` + `ui/polar/mixins/video_backend_mixin.py`；3D 线框在 `renderers/esp_wireframe.py` | 走本地视觉这一路，跟队内 0x5A 无关，不插 USB 线也能用 |
| **MQTTLink** / **UDPVideo** | `backend/mqtt_backend.py` / `backend/video_backend.py` | 界面上有「自动连接 / 重组」这类开关，但**刻意没接**（接上会让官方场景自己去抢端口），理由写在 `tools/SETTINGS_GAPS.md` |
| **SerialBridge** | `backend/serial_backend.py`（官方裁判串口那一路） | 与队内 0x5A 是两条互相独立的串口链路，互不代管 |
| **HeroLob** | 26 个设置项（数出来的，不是手写的），主要被 `ui/polar/mixins/` 引用 | 英雄投石那套界面参数；**注意**它和固件里的「英雄兵种」是两回事，固件侧不接这些参数 |

### 和这次 DT7 适配直接相关的那一条

上行 0x03 的键位图到了车上是**或**进 `rc_ctrl.key.v` 的 —— 固件那边叫 `HOST_RC_HOST_KEYS` 这本账（`tools/firmware/host_bridge/RC_LINKS.md` §3）。而比赛链路本身是「电脑 USB 插 DT7 → 无线 → 车上 DR16 → UART3 的 18 字节帧」，那个帧自带驾驶员的键位与鼠标。两者是叠加、不是互相接管：驾驶员松手或客户端掉线，各自那份账都会归零。

### 还有一条我们客户端暂时走不了、但你们现场用过的路

以前打比赛是**大疆官方客户端 USB 连 DT7** 开车 —— 这说明 DT7 的 USB 口就是 PC 键鼠的注入通道，
而固件侧（SBUS 18 字节 -> key.v / mouse -> 底盘与云台）本来就是通的、不用改一行。
本客户端缺的只有「PC <-> DT7」那一段 DJI 私有协议：全仓搜过 dt7/dr16/hid/joystick，没有任何实现。
侦察工具已写好：`python tools/dt7_hid_probe.py --watch 20`（零依赖，先定它是 HID 设备还是 CDC 虚拟串口），
`--capture`（需 `pip install hidapi`）能把报文直接读出来。细节见 `tools/firmware/host_bridge/RC_LINKS.md` 第 7 节。

