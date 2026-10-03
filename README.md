# POLAR-RM 自定义客户端

> 液态玻璃、Dynamic Island、页面过渡和高刷新动画的实现必须遵循
> [《POLAR-RM 液态玻璃与灵动动画规范》](README_液态玻璃与灵动动画规范.md)。
>
> 弹丸、补血、复活和空中支援的规则与正式请求边界见
> [《POLAR-RM 经济操作与裁判链路》](README_经济操作与裁判链路.md)。

这是一个使用 Python 编写的 RoboMaster 2026 自定义客户端。当前版本按本机 JAR 的字节码常量、原始资源和调用时序一比一移植 Sigma Jello 5.1.0 ClickGUI（不是风格模仿），并提供同一套 RM 模块注册表、共享设置系统、进入 HUD 后启用摄像头图传、POLAR 风格覆盖层、按键绑定捕获、本地配置文件，以及 MQTT/UDP/串口等通信接口预留。

## 60 Hz 逻辑拍：**没有可动手的性能问题**（第 76 轮实测，不是推断）

一个测量代理量了整条拍，另一个代理报了一条"每 120 拍吃掉整帧 9.0%"的尖峰。
**我自己逐层复量了一遍** —— 两边结论对不上，而**可信的那个结论是：这条路上没什么可省的。**

| 项 | 中位 µs | 占整帧 16667 µs |
|---|---|---|
| 全部可测的 60 Hz 逻辑拍（代理实测） | 39.7 | **0.238 %** |
| `polar.link` ACTIVE 45 段/拍（代理实测） | 79.8 | 0.479 % |
| `polar.link` IDLE（代理实测） | 29.1 | 0.175 % |
| `polar.stats()` 整体（**我实测**） | 3.80 | 0.0228 % |
| &nbsp;&nbsp;└ `asdict()` | 2.80 | 0.0168 % |
| &nbsp;&nbsp;└ `describe_ports_cached()` 热命中 | 0.20 | 0.0012 % |
| **冷路径 `describe_ports_cached()`**（**我实测**） | **127.1** | 0.763 % |
| 冷路径 `list_serial_ports()`（我实测） | 125.9 | 0.755 % |

**两件事要说清楚：**

**① 那条"9.0% 的尖峰"，我复现不出来。** 代理报 `describe_ports_cached()` = **1.50 ms**，
我强制冷路径量同一个函数 = **127 µs**，**差 12 倍**。最接近 1.50 的数是 import 模块的
1.331 ms，但那是一次性的。**我没有证据说代理错了**（端口数、负载、驱动状态都可能不同），
只记成**"复现不出"**，不用它去否定任何东西。

**② 即便按 1.50 ms 算，它也已经不重要了。** 第 74 轮把该路径的 TTL 从 2 秒提到 10 秒，
尖峰从**每 120 拍**降到**每 600 拍**。按 127 µs 摊薄是 **0.21 µs/拍**。

**所以：把 `stats()` 或 `collect_settings` 缓存 / 降频，能省下大约 0.02%–0.05% 的帧预算 ——
一个在均值和尾延迟里都看不见的数。** 代理建议的动手顺序，在这个量级上**不值得做**。

> **这条路的形状是尖峰，不是均值** —— 判断依据应该是**尖峰 ÷ 帧预算**，
> 而不是"占单核百分之几"。第 64 轮我盯的恰恰是后者，那是我判断错的地方。

### 那预算到底花在哪才算数

| 位置 | 为什么不能只看它 |
|---|---|
| **冻结区里的 9 个 UI 段** | 要 Qt + GPU + 摄像头才能测；**上面 0.238% 只是后端拍，不是整帧** |
| `control.tick` 的真串口写 | 代理测到的 0.3 µs 是心跳早退，**不是真写 DT7** |
| 极地链路收 / 发线程 | 真 `serial.read/write` + 线上段速率，要硬件 |
| MQTT 传输、活着的 ffmpeg | 要真 broker / 真进程 |

**这几项代理都明确标了"测不出来"，没有替它们编数字** —— 这比多报几条更值钱。

**结论：剩下值得花力气的地方不是继续微优化这条已经只占 0.24% 预算的拍，
而是 Rust 侧那 7 个"建好了、有向量、有单测，但还没接进程序"的 crate。**

## 运行

```powershell
cd rm_custom_client
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

`requirements.txt` 只装 **Python 包**（PySide6、OpenCV、OpenVINO、numpy、MQTT/串口等）。

击杀特效（GD656 图/音）和同济 Local 检测权重（`yolov5.xml/.bin`）**已随本仓库资源目录提供**，不是 pip 包，无需再从 GitHub 用 pip 安装。完整同济 C++ 自瞄工程见 [TongjiSuperPower/sp_vision_25](https://github.com/TongjiSuperPower/sp_vision_25)，仅在真机/UDP 外挂时另 clone 编译。详情见 `requirements.txt` 注释与 `THIRD_PARTY_NOTICES.md`。

启动后会依次选择运行场景、比赛制式和机器人配置，然后进入图传/HUD 主界面。启动选择页默认使用德克萨斯视频壁纸和鼠标视差；摄像头会延迟到进入主 HUD 后才打开，避免启动页直接显示真实摄像头画面。

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

`StartupVisual -> Glass Style` 默认使用 `LiquidGlass`。镜片先组合壁纸/视频与控件轨道，再在圆角内侧执行折射和边缘色散；文字与图标保持在镜片上层。视频解码在后台线程运行，界面只消费完整发布的最新帧，不会因解码落后而积累播放延迟。

第一级选择运行场景：

- `OfficialEvent`：官方赛事网络预设；裁判系统 MQTT 上行按比赛阶段/10 Hz 门禁开放，真实串口、底盘和发射输出仍默认 mock/stub。
- `CampusSimulation`：校内模拟或 SharkDataServer 本地仿真预设。
- `LabDebug`：实验室本地调试，使用摄像头或合成图传。

第二级选择比赛制式：

- `RMUC7v7`：`28m x 15m` 官方战场，默认显示基地、前哨站、经济、Buff、弹丸、热量、底盘功率和剩余比赛时间。
- `RMUL3v3`：`12m x 8m` 官方战场，默认显示控制区、胜利点、队友状态、经济、弹丸兑换、热量、底盘功率和剩余比赛时间。

协议与场景对齐说明见 `README_协议对齐与场景.md`。

## 快捷键

- `Right Shift` 或 `F8`：打开/关闭 ClickGUI。
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

- 摄像头：OpenCV 摄像头作为当前机器人图传；摄像头不可用时绘制合成场地图传。
- UDP：`UDPVideoReceiver` 按 SharkDataSever 的 8 字节包头重组图传分片。
- MQTT：`MQTTTransport` 可在代码中启用，用于订阅 SharkDataSever 主题。
- 官方自定义客户端桥：`OfficialMqttBridge` 保留 `KeyboardMouseControl` 和 `CustomControl` 发布边界，真实发布默认 mock。
- 串口：`SerialBridgeStub` 保留未来 RS232/机器人桥接边界。
- 串口帧编解码：`RefereeFrameCodec` 实现 RoboMaster 风格 `0xA5` 帧头、CRC8 头校验、`cmd_id`、载荷和 CRC16 尾校验。

## 冒烟测试

```powershell
.\.venv\Scripts\python.exe tests\smoke_test.py
.\.venv\Scripts\python.exe tests\backend_protocol_test.py
.\.venv\Scripts\python.exe tests\field_candidate_test.py
.\.venv\Scripts\python.exe tests\gpu_compositor_test.py
.\.venv\Scripts\python.exe tests\gpu_renderer_runtime_smoke.py
.\.venv\Scripts\python.exe tests\startup_gpu_runtime_smoke.py
.\.venv\Scripts\python.exe tests\transition_visual_check.py
```

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

发布脚本以 PyInstaller 6.21 one-folder 方式构建，无需现场 Python、venv、
pip 或网络。运行时只认可便携包 `bin/ffmpeg.exe`，OfficialEvent 还会校验
FFmpeg 与 `BUILD_MANIFEST.json` 的哈希。

```powershell
.\tools\build_release.ps1 `
  -GitRef <explicit-commit> `
  -FfmpegPath '<local-ffmpeg.exe>' `
  -TexasWallpaperPath '<local-texas.mp4>' `
  -VendingWallpaperPath '<local-vending-machine.mp4>'
```

详见 `FIELD_CANDIDATE.md`。构建完成仅表示“现场候选版”；缺少真车、正式
裁判系统、正式图传和双人签字证据时，结论必须保持
`NOT READY FOR REAL MATCH`。
