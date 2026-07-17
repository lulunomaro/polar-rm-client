# POLAR-RM 自定义客户端

这是一个使用 Python 编写的 RoboMaster 2026 自定义客户端。当前版本基于 PySide6/QPainter 渲染，提供 POLAR 风格 ClickGUI 结构：同一套 RM 模块注册表、四种 GUI 模式、共享设置系统、进入 HUD 后启用摄像头图传、POLAR 风格覆盖层、按键绑定捕获、本地配置文件，以及 MQTT/UDP/串口等通信接口预留。

## 运行

```powershell
cd rm_custom_client
.\.venv\Scripts\python.exe main.py
```

如果虚拟环境不存在，先执行 `pip install -r requirements.txt` 安装依赖。

启动后会先进入两级 POLAR 选择界面，然后再进入图传/HUD 主界面。启动选择页默认使用德克萨斯视频壁纸和鼠标视差；摄像头会延迟到进入主 HUD 后才打开，避免启动页直接显示真实摄像头画面。

## 启动壁纸

启动页底部点击 `Wallpaper` 可以直接打开液态玻璃壁纸库，不需要先进入 ClickGUI。客户端内置以下壁纸：

- `TexasMP4`：默认德克萨斯视频壁纸。
- `VendingMachine4K`：Vending Machine Girl 视频壁纸。
- `Default3000925581Pkg`：Wallpaper Engine 场景兼容模式。
- `ParticleGrid`：视频不可用时的交互粒子网格。

壁纸库支持导入 `mp4 / webm / mov / avi / mkv / gif / png / jpg`。导入文件会复制到 `assets/wallpapers/user/`，当前选择会随 config 自动保存；用户壁纸可以删除，内置壁纸不会被删除。

`StartupVisual -> Motion Quality` 控制启动页运动质量：

- `Smooth60`：默认模式，界面、鼠标视差和玻璃插值按 60 FPS 更新。
- `Balanced30`：降低玻璃缓存分辨率和动画负载。
- `Source`：跟随壁纸媒体的原始帧率。

`StartupVisual -> Glass Style` 可选 `ReGlass` 或 `Legacy`，默认 `ReGlass`。`Glass Strength` 控制暗色 tint、边缘折射和 rim light 的整体强度。ReGlass 样式使用稳定的双层背景模糊：内部使用轻模糊，边缘窄带使用更强模糊和局部折射；鼠标静止时面板不会持续改变亮度。

视频解码、颜色转换和双层模糊预处理在后台线程运行，界面只消费完整发布的最新帧，不会因解码落后而积累播放延迟。视觉实现参考本地 MIT 许可的 ReGlass 项目分层思路，不包含其 Minecraft 或 GLSL 源码。

第一级选择运行场景：

- `OfficialEvent`：官方赛事网络预设，真实机器人输出默认仍保持 mock。
- `CampusSimulation`：校内模拟或 SharkDataServer 本地仿真预设。
- `LabDebug`：实验室调试（Gestalt：OBS 图传 / 本机调试；见下方 Gestalt 文档）。

第二级选择比赛制式：

- `RMUC7v7`：`28m x 15m` 官方战场，默认显示基地、前哨站、经济、Buff、弹丸、热量、底盘功率和剩余比赛时间。
- `RMUL3v3`：`12m x 8m` 官方战场，默认显示控制区、胜利点、队友状态、经济、弹丸兑换、热量、底盘功率和剩余比赛时间。

协议与场景对齐说明见 `README_协议对齐与场景.md`。

### Gestalt System 联调（同目录上级 `RMaccseeplan`）

在 Steam 游戏 **Gestalt System** 上模拟 RM 操作手端时，请读：

| 文档 | 内容 |
|------|------|
| **[README_需求与改动说明.md](./README_需求与改动说明.md)** | 需求、全部改动、数据/控制流、快捷键原则、验收 |
| **[README_GESTALT_LAB.md](./README_GESTALT_LAB.md)** | 启动顺序、OBS、注入、故障速查 |
| [README_同济真实视觉联调.md](./README_同济真实视觉联调.md) | NUC 检测与 client 画框分工 |

要点：

- 图传：OBS 虚拟摄像头 → `CameraFeed`  
- 状态：Workshop Telemetry Relay → 本机桥 → client（真源）  
- 自瞄框：同济 NUC UDP（**默认完整 Python `sp_vision_py`**，一键台 `start_lab_launcher.bat`）；`ArmorAutoAim` 只控制是否画框/跟枪  
- 开车：`GestaltControl.Inject` / **F7**；**仅 WASD/QE** 注入游戏  
- **RightShift / R / V 等全部模块绑定键永远只作用 client**（注入时也如此）  
- 部署选车：游戏 UI，非 client  

## 快捷键

### 系统 / ClickGUI

- `Right Shift` 或 `F8`：打开/关闭 ClickGUI（Gestalt 注入开启时打开 GUI 会**暂停注入**）。
- `F7`：开关 Gestalt 键鼠注入（Lab；需已上车且能找到游戏窗口）。
- `F9`：切换 GUI 模式。
- 长按 `Tab`：放大 `TacticalMap`；松开 `Tab` 后回到缩略小地图。修改 `TacticalMap` 模块绑定键后，新的绑定键也会作为长按查看键。
- 启动页：`1/2/3` 选择运行场景，然后 `1/2` 选择 `RMUC7v7/RMUL3v3`；`Backspace` 返回场景选择。

### 模块绑定键（全部在 client 内生效）

- 各模块可在 ClickGUI 绑定任意键（默认示例：`V` ArmorAutoAim 画框、`R` RobotOverlay、`X` SpinMode 等，以配置/KEY 显示为准）。
- **这些键只切换 POLAR 模块，不会注入进 Gestalt。**
- 注入游戏的键只有 **W/A/S/D/Q/E**（且未被绑成模块键时）以及可选鼠标。

### ClickGUI 操作

- 左键点击模块：根据当前 GUI 模式选择模块或切换模块开关。
- 右键点击模块：在内嵌式 GUI 中展开/折叠设置。
- 模块右侧 `KEY`/`K` 按钮或 `Keybind` 设置行：打开 POLAR 风格按键绑定覆盖层。
- 按键绑定覆盖层中按任意键即可绑定；按 `ESC` 清空绑定。
- `PolarOld` 模式中，左键拖动分类标题可移动面板，右键点击分类标题可展开/折叠。

Gestalt Lab 下请保持 **Keep Client Focus=ON、Focus Game=OFF**，否则快捷键会进游戏侧导致「client 全部键无效」。

## 已实现 GUI 模式

- `PolarOld`：横向可拖拽分类小面板，右键展开，模块内嵌设置，模块高度带动画。
- `PolarPanel`：居中 `600x400` 主面板，80 alpha 遮罩，`0.98 -> 1.0` 打开缩放，左侧模块列表，右侧设置面板，分类图标栏，搜索栏，Profile/Scale 文本，Toast 和 KeybindOverlay。
- `PolarNew`：顶部分类列，`120px` 分类面板，深色圆角面板，滚动、tooltip 和可展开设置。
- `PolarDropdown`：下拉式分类面板，启用行带强调渐变，包含滑条、开关、模式控件和按键绑定入口。

四种模式都操作同一套 RM 模块与设置，不会拆成互相独立的界面。

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

客户端配置保存在 `configs/` 目录，默认配置为 `configs/default.json`。配置会保存模块开关、按键绑定、设置值、GUI 模式、HUD 模式、比赛场景、7v7/3v3 模式、小地图设置、UI 缩放和面板展开/位置。

`ConfigManager` 模块提供这些入口：

- `Profile`：选择当前配置文件。
- `Auto Save`：默认开启，修改模块或设置后自动保存。
- `Load On Start`：默认开启，启动时加载当前配置。
- `Save Current`：立即保存当前界面配置。
- `Reload Profile`：重新读取当前配置。
- `Import Config`：打开文件选择器，把外部 `.json` 配置复制进 `configs/` 并加入 Profile 列表。

配置加载顺序是：默认模块注册表 -> 当前场景/比赛模式默认值 -> 用户配置覆盖 -> 运行时连接参数同步。坏字段会被跳过，不会让客户端无法启动。

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
- `RobotOverlay` / `RadarOverlay`：装甲板框、机器人 ID、距离、血量和雷达高亮覆盖层。
- `RobotTargetHUD`：目标血量、热量、距离和锁定状态卡片。
- `ModuleList`、`KeyBinds`、`Watermark`、`DynamicIsland`：可配置 RM HUD 覆盖层。
- RMUI 语义模块：`GimbalPower`、`PowerLimit`、`SpeedLimit`、`Capacitor`、`FrictionWheel`、`LockState`、`BuffStatus`、`ProjectileAllowance`、`JumpPTEWheelLES`。
- 比赛 HUD 语义模块：`MatchMode`、`TacticalMap`、`MatchTimer`、`BaseOutpostStatus`、`VictoryPointStatus`、`EconomyPanel`、`ProjectileShop`、`ThermalPowerPanel`、`BuffTimeline`。

当前没有实现真实发射和真实底盘控制。所有控制输出默认都是 mock/stub。

## 接入点

- 摄像头：OpenCV 摄像头作为当前机器人图传；摄像头不可用时绘制合成场地图传。
- UDP：`UDPVideoReceiver` 按 SharkDataSever 的 8 字节包头重组图传分片。
- MQTT：`MQTTTransport` 可在代码中启用，用于订阅 SharkDataSever 主题。
- 官方自定义客户端桥：`OfficialMqttBridge` 保留 `KeyboardMouseControl` 和 `CustomControl` 发布边界，真实发布默认 mock。
- 串口：`SerialBridgeStub` 保留未来 RS232/机器人桥接边界。
- 串口帧编解码：`RefereeFrameCodec` 实现 RoboMaster 风格 `0xA5` 帧头、CRC8 头校验、`cmd_id`、载荷和 CRC16 尾校验。

## 冒烟测试

```powershell
.\.venv\Scripts\python.exe tests\smoke_test.py
```

## 模块切换与击杀音效

- `AudioFeedback` 默认开启。ClickGUI 点击或绑定键切换模块时，开启播放较亮的提示音，关闭播放较沉的提示音；配置加载、场景初始化和内部状态同步不会播放。
- `AudioFeedback -> UI Volume` 控制界面提示音音量，三个 `Preview` 操作可分别试听开启、关闭和无效操作音效。
- `EliminationFX -> Sound Enabled / Sound Volume` 独立控制 GD656 击杀音效。音频会预加载，短音效后端无法解码 OGG 时自动回退到 Qt 媒体播放器。
- LiquidBounce 点击音效来源及许可证位于 `assets/ui_sounds/`；GD656 击杀音效保持原始文件名和预设映射。
- `AudioFeedback -> Sound Theme` 默认是 `Zen`：开启模块播放 OpenZen 的 `truth.wav`，关闭播放 `lie.wav`；也可切回 `LiquidBounce` click 主题。

## 启动页渲染

- 视频壁纸在可用的 OpenGL 上下文中作为纹理合成，鼠标视差和相邻源帧交叉淡化不再依赖 QPainter 全屏缩放。
- 视频解码和玻璃快照仍在后台线程处理；OpenGL、视频或着色器不可用时自动回退到原有 QPainter 路径。
