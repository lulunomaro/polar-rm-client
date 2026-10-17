# POLAR-RM 自定义客户端

实体工程控制器角度新增独立 `ControllerAngles` 接收与转发入口（默认关闭及 mock）。
支持的六轴协议、端口设置、独立超时与仍需现场验证的边界见
[实体控制器角度链路](docs/controller_angle_link.md)。现有 0x0306 键鼠出口保留。

新增学生端展示入口：`~` 受伤害计分板、`E` MC 模块背包/技能工作台、
Jello 结算/飞镖/性能面板、原经济区补弹详情、MC 判罚/连杀播报与状态效果。
详细入口和「草稿不下发 / 聊天仍本地」边界见 [学生端前端说明](STUDENT_FRONTEND.md)。

> 液态玻璃、Dynamic Island、页面过渡和高刷新动画的实现必须遵循
> [《POLAR-RM 液态玻璃与灵动动画规范》](README_液态玻璃与灵动动画规范.md)。
>
> 弹丸、补血、复活和空中支援的规则与正式请求边界见
> [《POLAR-RM 经济操作与裁判链路》](README_经济操作与裁判链路.md)。

这是一个使用 Python / PySide6 编写的 RoboMaster 2026 自定义客户端。当前版本按本机 JAR 的字节码常量、原始资源和调用时序移植 Sigma Jello 5.1.0 ClickGUI，并提供 RM 模块注册表、共享设置系统、进入 HUD 后启用摄像头图传、POLAR 风格覆盖层、按键绑定捕获、本地配置，以及由 `BackendManager` 编排的 MQTT/UDP/串口后端。通信代码已实现不等于完成现场资格验证，当前仍为 **NOT READY FOR REAL MATCH**。

## 运行

```powershell
# 在仓库根目录执行
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-core.txt
.\.venv\Scripts\python.exe main.py
```

`requirements-core.txt` 包含 GUI、图像和通信的核心 Python 依赖；需要同济 Local
推理时，再安装 `requirements.txt`（另含 OpenVINO）。`requirements-build.txt`
用于发布/编译验证，不是现场运行依赖。这些清单都不是模型、素材或 FFmpeg 的下载器。

开发前先读 [贡献与验证约定](CONTRIBUTING.md) 和 [当前架构与所有权](docs/architecture.md)。
正式入口是 `main.py`：它先处理运行目录、preflight、回放和队内链路自检，再进入
`rm_client.ui.polar_app.main`，实际 Qt 装配位于 `rm_client/ui/polar/app_entry.py`。
`rm_client/app.py` 是惰性兼容入口，直接运行也进入 Qt；历史 Tk 实现保留在
`rm_client/legacy_tk_app.py`，只有显式访问旧导出时才加载，不属于当前主窗口。

击杀特效（GD656 图/音）和同济 Local 检测权重（`yolov5.xml/.bin`）**已随本仓库资源目录提供**，不是 pip 包，无需再从 GitHub 用 pip 安装。完整同济 C++ 自瞄工程见 [TongjiSuperPower/sp_vision_25](https://github.com/TongjiSuperPower/sp_vision_25)，仅在真机/UDP 外挂时另 clone 编译。详情见 `requirements.txt` 注释与 `THIRD_PARTY_NOTICES.md`。

启动后会依次选择运行场景、比赛制式和机器人配置，然后进入图传/HUD 主界面。启动选择页默认使用德克萨斯视频壁纸和鼠标视差；摄像头会延迟到进入主 HUD 后才打开，避免启动页直接显示真实摄像头画面。

## 启动壁纸

启动页底部点击 `Wallpaper` 可以直接打开液态玻璃壁纸库，不需要先进入 ClickGUI。客户端内置以下壁纸：

- `TexasMP4`：默认德克萨斯视频壁纸。
- `VendingMachine4K`：Vending Machine Girl 视频壁纸。
- `Default3000925581Pkg`：Wallpaper Engine 场景兼容模式。
- `ParticleGrid`：视频不可用时的交互粒子网格。

壁纸库支持导入 `mp4 / webm / mov / avi / mkv / gif / png / jpg`。导入文件会复制到运行时用户数据目录的 `wallpapers/`（默认 `user_data/wallpapers/`），当前选择会随 config 自动保存；用户壁纸可以删除，内置壁纸不会被删除。

`StartupVisual -> Motion Quality` 控制启动页运动质量：

- `Smooth60`：默认模式，界面、鼠标视差和玻璃插值按 60 FPS 更新。
- `Balanced30`：降低玻璃缓存分辨率和动画负载。
- `Source`：跟随壁纸媒体的原始帧率。

`StartupVisual -> Glass Style` 默认使用 `LiquidGlass`。镜片先组合壁纸/视频与控件轨道，再在圆角内侧执行折射和边缘色散；文字与图标保持在镜片上层。视频解码在后台线程运行，界面只消费完整发布的最新帧，不会因解码落后而积累播放延迟。

第一级选择运行场景：

- `OfficialEvent`：官方赛事网络预设；裁判系统 MQTT 上行按年度协议限频和比赛阶段门禁运行，键鼠控制还需资格证据和本次进程人工 Arm。队内链路有独立模块开关与安全门禁；不能把场景选择当作真实输出授权。
- `CampusSimulation`：校内模拟或 SharkDataServer 本地仿真预设。
- `LabDebug`：实验室本地调试，使用摄像头或合成图传。

第二级选择比赛制式：

- `RMUC7v7`：`28m x 15m` 官方战场，默认显示基地、前哨站、经济、Buff、弹丸、热量、底盘功率和剩余比赛时间。
- `RMUL3v3`：`12m x 8m` 官方战场，默认显示控制区、胜利点、队友状态、经济、弹丸兑换、热量、底盘功率和剩余比赛时间。

协议与场景对齐说明见 `README_协议对齐与场景.md`。

## 快捷键

- 主 HUD 按 `Esc` 打开 Jello 二级菜单：继续、返回启动页、退出客户端（二次确认）；再次 `Esc` 关闭。已有输入框/弹窗优先处理 Esc。菜单不暂停遥测或安全监控，菜单键鼠不会下发为控制输入。见 [Esc 菜单说明](docs/escape_menu.md)。

- `Right Shift` 或 `F8`：打开/关闭配置界面（ClickGUI）。Jello 界面左键点击面板外空白处也可关闭；面板标题、留白、控件和拖动不会误关。
- `F9`：已停用，不再轮换 ClickGUI。
- 长按 `Tab`：放大 `TacticalMap`；松开 `Tab` 后回到缩略小地图。修改 `TacticalMap` 模块绑定键后，新的绑定键也会作为长按查看键。
- 启动页：`1/2/3` 选择运行场景，然后 `1/2` 选择 `RMUC7v7/RMUL3v3`；`Backspace` 返回场景选择。
- `B`：打开/关闭独立经济工作区；`ProjectileShop` 绑定键直接进入弹丸页。
- `K`：打开/关闭 Hero 专用部署/吊射控制台；进入或退出部署模式需要在确认窗口内再次确认。
- 经济工作区：滚轮/方向键浏览，`Enter` 确认，远程弹丸、补血、复活和支援需长按 600ms，`Esc` 取消或关闭。
- Jello 主界面中左键点击模块切换开关，右键打开居中设置面板。
- `Esc` 先关闭设置面板；临时旧界面中第一次返回 Jello，再次关闭 ClickGUI。
- 六套旧界面只在 `ClickGui -> Old ClickGUIs` 展开后出现，并且不会写入配置。

## ClickGUI

- `JelloSigma` 是唯一默认主界面：固定六分类 4+2 卡片布局、Sigma 原始字体/图片/颜色、450 ms 入场、125 ms 退场、20 px 模糊和原始模块开关音效。
- `PolarOld`、`PolarPanel`、`PolarNew`、`PolarDropdown`、`PolarUnified`、`PolarMulti` 保留为临时兼容界面，只能从 `Old ClickGUIs` 进入。
- 旧配置中的 Polar/Vape 模式会迁移到 `JelloSigma`；临时进入旧界面不会改变下次打开结果。
- `ClickGui -> Scale` 可在 `0.50x–2.00x` 调节；窗口布局会按可用宽高封顶。Jello 字形按实际 DPR 高分辨率栅格化并保持 Helvetica Neue Light 原始字重，图片/字体页预加载，绝对时钟帧调度避免 2K 入场动画因定时器量化而顿挫。

## Jello HUD 与 Minecraft 战斗预设

- `HUD -> ModuleList Style -> JelloActiveMods` 使用 Sigma 5.1.0 `ActiveMods` 的原始 Helvetica Neue Light 字体、右上角坐标、150 ms 显隐、宽度排序、缩放枢轴和 `hud_shadow.png`。
- `Armor3DESP -> Style -> JelloShadow` 替换旧的荧光白机械样式：使用 `#fefefe`、2 px 轮廓和 Sigma 原始阴影图。由于视觉桥只提供投影装甲四边形而不是 Minecraft 实体网格，该模式严格以可用装甲轮廓作为 silhouette，不伪造实体深度。
- `HUD -> Combat Preset -> Minecraft 1.21.11` 启用从用户本地 1.21.11 JAR 提取的原版 hotbar、心/护甲/饱食度、BossBar、状态图标和位图字体。
- Minecraft 护甲值表示剩余热量/冷却裕量，饱食度表示实时底盘功率/功率上限；右侧 scoreboard 显示基地/敌方基地护盾、前哨站、最多四名我方与四名敌方的 ID/血量/状态、目标、弹量和 Hero 吊射状态。BossBar、状态图标和 scoreboard 在 1000×620 到 2560×1440 的 Auto GUI Scale 下分区避让。
- Jello 模块行可拖到九格功能栏；`1–9` 执行对应功能。栏位保存在配置中，拖拽命中区域与当前 Minecraft 整数 GUI Scale 一致。

## Hero 吊射与后端就绪性

- `HeroLob` 提供高/低弧预览、炮口随 pitch 位移的迭代补偿、yaw/pitch 校准、遥测时效、热量、横滚和重定位门禁。
- `K` 或 `HeroLob -> Open Deploy Interface` 从顶部 Apple 风格灵动岛展开 Hero 吊射控制面；根面板、轨迹/遥测卡片和按钮直接复用现有 AndroidLiquidGlass GPU 镜片、文字保护、按压反馈与 Control Center 弹簧，不再使用独立科幻风全屏设计。部署进入/退出采用二次确认；mock 按官方 2 秒切换过程模拟，`OfficialEvent` 等待 `DeployModeStatusSync`，部署期间图传被切断时自定义 UI 和地图继续显示。
- 它是 fail-closed 的决策支持功能，**不会发送自动发射或云台命令**；定位重置或遥测异常会自动撤销预备状态。
- `AutoSpinOnHit` 只依据 `RobotInjuryStat` 的新增 17/42 mm 弹丸伤害触发，首个累计样本不触发；提供阈值、持续时间、冷却、比赛/裁判链路/功率门禁和人工 SpinMode 所有权保护。
- `rm-native-viewer` 的 `PDL1` 遥测和 `0x0310` 固定分块已接入常驻 H.264 解码器；reset、缺包或解码器异常后会停止喂流并等待下一个 Annex-B SPS，再从 SPS 精确恢复，序号回绕/重复/迟到包均有统计，历史码流不会无限驻留内存。UDP H.265 解码异常在接收线程外恢复，并与原生 H.264 使用同一单调解码时间线后再发布到 HUD。
- 当前实战审查结论为 **NOT READY FOR REAL MATCH**。阻塞项和真车验证清单见 `BACKEND_MATCH_READINESS.md`。
- 想知道「每个模块的后端到底是谁」：见 `MODULE_BACKEND_MAP.md`（由 
  `python tools/module_backend_map.py` 从代码里生成，重点模块另有人工核对过的行号）。

## HUD 模式

`HUD` 模块的 `Right Display` 设置可以选择以下右侧显示模式。`TacticalMap` 当前作为独立左上 HUD 绘制在 `POLAR-RM` 水印下方，因此右侧可以保持 `ModuleListHud`，不会互相遮挡。

- `ModuleListHud`
- `TacticalMapHud`
- `KeyBindsHud`
- `RobotListHud`
- `TargetHud`
- `StatusCardsHud`
- `RMIndicatorsHud`

右侧条目按右对齐绘制，并带有 POLAR 风格滑入、淡入和宽高动画。

## 官方地图小地图

`TacticalMap` 使用从本地官方规则 PDF 提取的地图位图资源：

- `assets/maps/rmuc_7v7_official.png`：RMUC 7v7 官方场地模块图。
- `assets/maps/rmul_3v3_official.png`：RMUL 3v3 官方场地图/尺寸图。

地图底图只做等比缩放和暗化处理，方便叠加信息；不会修改官方地图结构。机器人点位、雷达脉冲、地图标点和区域高亮都通过米制坐标转换后绘制：

- 7v7 坐标尺寸：`28m x 15m`。
- 3v3 坐标尺寸：`12m x 8m`。
- `MatchMode -> Alliance = Blue` 且 `TacticalMap -> Mirror Blue = ON` 时只镜像显示，不改变原始坐标数据。

`ModuleListHud` 还提供 `HUD -> ModuleList Style`：

- `PolarLegacy`
- `RainbowRight`
- `GlowRows`
- `Segmented`
- `DynamicIsland`
- `KeyBindsRows`
- `StatusBars`
- `RobotCards`

## 配置文件

源码与便携版都把默认文件视为只读模板：源码模板是
`configs/default.json`，便携包模板是 `defaults/default.json`。实际用户配置
统一保存在 `user_data/configs/`；首启仅在目标不存在时原子复制模板。配置会
保存模块开关、按键绑定、设置值、GUI 模式、HUD 模式、比赛场景、7v7/3v3
模式、小地图设置、UI 缩放和面板展开/位置。

`ConfigManager` 模块提供这些入口：

- `Profile`：选择当前配置文件。
- `Auto Save`：默认开启，修改模块或设置后自动保存。
- `Load On Start`：默认开启，启动时加载当前配置。
- `Save Current`：立即保存当前界面配置。
- `Reload Profile`：重新读取当前配置。
- `Import Config`：打开文件选择器，把外部 `.json` 配置复制进运行时 `user_data/configs/` 并加入 Profile 列表。

配置加载顺序是：默认模块注册表 -> 当前场景/比赛模式默认值 -> 用户配置覆盖 -> 运行时连接参数同步。兼容的未知字段可以忽略；已知字段类型错误或非有限 JSON 数值会在修改前拒绝整份配置，保留原设置。运行时切换异步完成，只有确认成功后才提交；失败会恢复设置并保持控制锁定。

## UI 缩放

不同尺寸显示器可以通过 `UIScale` 模块调整：

- `Auto Scale`：根据当前窗口尺寸自动缩放。
- `HUD Scale`：控制水印和 HUD 覆盖层大小。
- `GUI Scale`：控制 ClickGUI 大小。
- `Animation Speed`：控制主要 UI 动画速度。

大屏幕上如果 UI 显得过小，优先打开 `UIScale` 模块并调高 `HUD Scale` 或 `GUI Scale`。

## RM 模块

- `ArmorAutoAim`：打车/装甲板自瞄 UI，包含白色轮廓锁定框和预留云台输出接口。
- `EnergyAutoAim`：7v7 能量机关自瞄 UI，包含符进度显示和预留能量机关预测/输出接口。
- `SpinMode`：小陀螺底盘状态、参数和预留底盘输出接口。
- `AutoSpinOnHit`：检测裁判弹丸伤害增量后，在门禁允许时临时请求小陀螺；默认关闭，不能用普通血量下降代替弹丸命中证据。
- `RobotOverlay` / `RadarOverlay`：装甲板框、机器人 ID、距离、血量和雷达高亮覆盖层。
- `RobotTargetHUD`：目标血量、热量、距离和锁定状态卡片。
- `ModuleList`、`KeyBinds`、`Watermark`、`DynamicIsland`：可配置 RM HUD 覆盖层。
- RMUI 语义模块：`GimbalPower`、`PowerLimit`、`SpeedLimit`、`Capacitor`、`FrictionWheel`、`LockState`、`BuffStatus`、`ProjectileAllowance`、`JumpPTEWheelLES`。
- 比赛 HUD 语义模块：`MatchMode`、`TacticalMap`、`MatchTimer`、`BaseOutpostStatus`、`VictoryPointStatus`、`EconomyPanel`、`ProjectileShop`、`ThermalPowerPanel`、`BuffTimeline`。`EconomyPanel` 提供独立工作区，并按比赛制式、机器人、实时金币、弹量、脱战和复活状态过滤操作；复活结算后关闭会清理经济界面的瞬态玻璃/遮罩缓存，不会带走 HUD，随后可再次打开。

真实 KeyboardMouseControl 已接到官方 MQTT 上行，但每次启动默认 `LOCKED`。
只有现场资格测试器生成的当前构建/机器人/固件证据、24 小时有效期内的动态
测试、双人签字和本次进程人工 Arm 全部通过后，才进入 `MATCH_ARMED`。
PRM1 自瞄/小陀螺仍是独立 `LOCKED MOCK` 能力；客户端不提供自动发射输出。

## 接入点

- 统一编排：`rm_client/backend/manager.py::BackendManager` 管理场景切换、后端生命周期、状态应用、安全门禁及输出归属；窗口的 `mqtt`、`udp`、`serial` 是这些后端的兼容引用。
- 摄像头：`CameraCaptureWorker` 在后台采集，窗口消费帧；启动页不提前打开摄像头。合成画面不能作为真图传通过的证据。
- MQTT：`RmMqttBackend` 按裁判 profile 订阅、解码并缓冲消息，`BackendManager.apply_updates()` 消费队列并更新 `RobotState`；发布经过 `OfficialSafety` 和资格门禁。
- 图传：`RmVideoBackend` 处理 Shark UDP 重组与解码；原生 `0x0310` 由 manager 管理独立 H.264 解码器。不要把已组好的图像再送回分片重组入口。
- 官方串口：`RmSerialBackend` 处理 `0xA5` 帧，默认 `Stub` / mock；它明确拒绝把 `0x0310/0x0311` 写进该串口。
- 队内机器人链路：`PolarLinkTransport` / `PolarStateBridge` 处理独立的 `0x5A` 通道，协议规格位于 `rm_client/backend/polar_protocol.json`。它与官方裁判通道并行，规则见 [AGENT.md](AGENT.md)。
- 其他控制链路：DT7、`0x0306` 自定义键鼠与 `ControllerAngleRelay` 各有自己的配置、端口所有权和门禁，不能复用一个“已连接”标志表示全部就绪。

`rm_client/transports.py` 中的 `OfficialMqttBridge`、`UDPVideoReceiver`、
`SerialBridgeStub` 仍是后端复用的基础/兼容接口；旧 `MQTTTransport` 不是当前
窗口的生产 MQTT 实例。具体所有权和仍存在的耦合见 [架构说明](docs/architecture.md)。

## 验证入口

以下 `python` 应使用刚安装依赖的同一解释器，并从仓库根目录运行：

```sh
python tools/verify_all.py --list --profile all
python tools/verify_all.py --profile offline --json tmp_quality/verification.json --log-dir tmp_quality/logs
python tools/verify_all.py --test tests/backend_protocol_test.py
```

测试注册表由 `tools/test_registry.py` 维护；发布验证也消费同一份清单。
离线检查、桌面 OpenGL、视觉检查和固件检查的条件不同，具体运行方法见
[贡献与验证约定](CONTRIBUTING.md#验证与证据)。`SKIP` / `INCOMPLETE` 不是通过，
离线通过也不证明真机、Windows GPU 或正式赛事链路已验证。

## 模块切换与击杀音效

- `AudioFeedback` 默认开启。ClickGUI 点击或绑定键切换模块时，开启播放较亮的提示音，关闭播放较沉的提示音；配置加载、场景初始化和内部状态同步不会播放。
- `AudioFeedback -> UI Volume` 控制界面提示音音量，三个 `Preview` 操作可分别试听开启、关闭和无效操作音效。
- `EliminationFX -> Sound Enabled / Sound Volume` 独立控制 GD656 击杀音效。音频会预加载，短音效后端无法解码 OGG 时自动回退到 Qt 媒体播放器。
- LiquidBounce 点击音效来源及许可证位于 `assets/ui_sounds/`；GD656 击杀音效保持原始文件名和预设映射。
- `AudioFeedback -> Sound Theme` 可选择 `Polar Classic`：开启模块播放 `module_on.wav`，关闭播放 `module_off.wav`；也可切换其他交互音色或导入自定义音效。

## 启动页渲染

- 视频壁纸在可用的 OpenGL 上下文中作为纹理合成，鼠标视差和相邻源帧交叉淡化不再依赖 QPainter 全屏缩放。
- 视频解码和玻璃快照仍在后台线程处理；OpenGL、视频或着色器不可用时自动回退到原有 QPainter 路径。


## 自包含现场候选包

### 随源素材

`assets/` 已包含客户端使用的 Jello/Minecraft 字体、图片、音效、GD656 特效、
经用户确认再分发授权的 CS2 素材包，以及 Texas/Vending Machine 两段内置视频壁纸。
正常 Git 克隆即可取得，无需额外复制开发机 `user_data`；CS2 本机覆盖包仍可选。
版权与来源见 `THIRD_PARTY_NOTICES.md` 及各素材清单。个人配置、标定、日志、
现场资格证据、自定义导入素材与 Release 压缩包不混入源代码提交。

发布脚本以 PyInstaller 6.21 one-folder 方式构建，无需现场 Python、venv、
pip 或网络。运行时只认可便携包 `bin/ffmpeg.exe`，OfficialEvent 还会校验
FFmpeg 与 `BUILD_MANIFEST.json` 的哈希。

构建机需要 Windows、可用桌面/OpenGL 和完整构建依赖。发布脚本不安装依赖；
先在选定环境手动准备 `requirements.txt` 与 `requirements-build.txt`。
默认使用仓库 `.venv\Scripts\python.exe`；启动器创建的 `.venv-polar` 或其他
环境须显式传 `-PythonPath`（相对路径按调用时的工作目录解析）。测试、规格校验与
PyInstaller 都使用这个解释器，不依赖 PATH 上另一个 `pyinstaller.exe`。
Linux 验证锁文件不能替代 Windows 打包验证。

```powershell
# 也可使用已准备的 .venv-polar；以下创建独立发布环境
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\tools\build_release.ps1 `
  -PythonPath '.\.venv\Scripts\python.exe' `
  -GitRef <explicit-commit> `
  -FfmpegPath '<local-ffmpeg.exe>' `
  -TexasWallpaperPath '<local-texas.mp4>' `
  -VendingWallpaperPath '<local-vending-machine.mp4>'
```

详见 `FIELD_CANDIDATE.md`。构建完成仅表示“现场候选版”；缺少真车、正式
裁判系统、正式图传和双人签字证据时，结论必须保持
`NOT READY FOR REAL MATCH`。

