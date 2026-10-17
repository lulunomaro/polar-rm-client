# 二级界面设置项对齐清单

当前注册表有 **74 个模块、470 个设置项**（2026-10-16 统计校对）。
这份文档区分静态审计结果与历史接线记录；静态命中不能单独证明设置已经影响画面或输出。

审计工具：`tools/audit_settings.py`（本文档所有数字都由它产出，可复现）

```powershell
python tools/audit_settings.py              # 汇总 + 列出孤儿与动态项
python tools/audit_settings.py --all        # 列当前全部设置项
python tools/audit_settings.py --json out.json
```

---

## 当前结果

| 档位 | 数量 | 含义 |
|---|---:|---|
| **READ** | 355 | 静态工具检出字面命中且名字不冲突；仍需确认是否为功能消费者 |
| **AMBIGUOUS** | 63 | 多个模块共用设置名；其中 48 项有工具归属提示、15 项仍无归属提示，均不能仅凭总数算已对齐 |
| **DYNAMIC** | 31 | 25 个动态设置名读取候选，加 6 个精确注册且存在消费端的动态音效命令；仍需行为测试 |
| **ORPHAN** | **21** | 静态工具未发现字面或动态读取证据；按下表逐项保留待处理范围 |

2026-10-16 的命令边界抽取把六个 AudioFeedback 的 Import/Preview 名字改为有界循环生成，
因此从 READ 改列 DYNAMIC，孤儿上限仍为 21。这不是消除了六个功能缺口。
`tools/settings_action_evidence.py` 只有在精确解析器接受该命令、入口传给执行分派、
对应类别调用音效消费端时才记录动态证据；纯声明、未知槽和断开的分派均不算。
`tests/test_settings_action_evidence.py` 覆盖这些负例与真实审计输出，
`tests/test_configuration_commands.py` 则逐一执行六个生产命令分派及其参数断言。


历史 PR17 基线 `b26b7b7` 与命令抽取前工作树均为 READ 361 / AMBIGUOUS 63 /
DYNAMIC 25 / ORPHAN 21；命令抽取后只有上述六项转为 DYNAMIC。
21 项 ORPHAN 的 `(module, setting)` 集合没有变化，
**没有把旧文档校正的 22→21 记为本轮新增接线**。

旧清单多列的项是 `ClickGui / Save Layout`。两棵树中工具都因为
`rm_client/ui/cs2_settings_center.py` 的设置列表
`("ClickGui", ("Scale", "Language", "Save Layout"), False)` 将其归为 READ。
这只是设置列表中的名字，**不是已经验证的布局保存消费者**；该设置的功能兑现仍需
单独检查，不能把它从 ORPHAN 统计中移出解释为修好了功能。

### 接线进度（只许减不许增，基线已接入 `tools/verify_all.py`）

| 阶段 | 孤儿数 | 本批接上的 / 统计说明 |
|---|---:|---|
| 初始真实值（排除翻译表后） | 71 | — |
| 第 1–2 批：自瞄 + 遥测 | 68 | `Fov`、`Priority`、`Warn`、`Show Bar`、`Show Limit`（+修复 `Send Hz`） |
| 第 3 批：战况面板 | 56 | `Own Base`、`Enemy Base`、`Outposts`、`Shield`、`Victory Points`、`Control Zone`、`Team Status`、`Gold`、`Tech Level`、`Ammo Type`、`Show Cost`、`Show Speed` |
| 第 4 批：顶栏 | 53 | `Show Stage`、`Show Scores`、`Pulse Last 30s` |
| 第 5 批：覆盖层与准星 | 51 | `Robot ID`、`Heat Color` |
| 第 6 批：渲染后端 | 50 | `Renderer`（Armor3DESP 的 GPU Auto / CPU） |
| 第 7 批：超限脉冲与外发光 | 48 | `Pulse Overrun`、`Use Glow` |
| 审计修正：常量别名 | 47 | 见下方「更正」第 3 条（`ClickGui / Language` 其实一直被读） |
| 第 8 批：启动与状态 | 43 | `Second Step`、`Show Lock`、`Warn Low`、`Buffer` |
| 第 9 批：装甲盒与数据源 | 42 | `Show Depth`（并修正电容读 mock 字段的既有 bug） |
| 第 10 批：RM 指示灯 | 40 | `Show State`、`Anti Rotation`（并把 `Show Lock` 从灯也接上）|
| 第 11 批：雷达与锁定 | 38 | `High Light`、`Pulse Locked` |
| 第 12 批：增益灯 | **36** | `Small Buff`、`Large Buff`（映射由 `buff_indicator_state()` 自己作证）|
| 第 13 批：地图标记 | **34** | `Mark Type`、`Send All`（上一轮误判为“无协议编码”，见下方更正） |
| 第 14 批：状态卡片 | **31** | `Show Voltage`、`Glow Alert`、`Warn Below`（上一轮误判为无数据源）|
| 第 15 批：装甲盒 | **30** | `Armor Boxes`（与 `ESP Style` 的职责区分见下）|
| 第 16 批：击杀加分 | **29** | `Lock Target Bonus`（上一轮判成“需改绘制层”，实际只差一行供值）|
| 第 17 批：同名档里挖出的两个 | **29**（不变）| `Crosshair/Style`、`RobotOverlay/Distance`。它们本来就不在孤儿数里（被同名的另一个模块洗白，归在 AMBIGUOUS，不计入也不计减），所以接完后这个数既不上升也不下降 |
| 第 18 批：告警阈值 | **27** | `Heat Warn`、`Energy Warn`（`PowerGuard` 默认关着，接线零画面风险）|
| 第 19 批：增益时间轴 | **24** | `Buffs`、`Special Mechanism`、`Timeline`——整个模块**本来连面板都没有**，本轮新建了消费者（位置是量出来的）|
| 第 20 批：装甲盒配色 | **23** | `Color`——默认值改成等于接线前画面的 White；Team 只在认得出 red/blue 板色时染阵营色；Threat 无威胁度数据，按 `ArmorAutoAim/Priority` 的 Radar 先例**降级并记原因** |
| 第 21 批：雷达叠加 | **22** | `Map`——前两轮"等语义确认"里的同型题：默认值改成等于接线前画面的 `Both`（两张图都画），Mini/Full 以面板自己的展开插值 `detail` 为唯一判据分档，Hidden 只隐藏显示、不吞数据与 TTL |
| 第 22 次记录：统计校对（2026-10-16，非接线批次） | **21** | PR17 `b26b7b7` 与当前审计均为 21；移出旧清单的 `ClickGui/Save Layout` 只是被设置列表字面命中，不表示本轮新增或验证了功能消费者 |

> 上表第 17 批的“29 不变”是历史口径看不见那两项。
> 下面「同名共用那 51 项」保留当时记录；当前同名档数量以本页结果表为准。

对应回归测试：`autoaim_settings_test.py`、`hud_telemetry_settings_test.py`、
`hud_status_settings_test.py`、`hud_overlay_settings_test.py`、`hud_batch8_settings_test.py`、
`hud_batch9_settings_test.py`（含电容单一来源与深度边）、
`hud_batch10_settings_test.py`（含缓冲上限的端到端解码断言）、
`hud_batch13_settings_test.py`（含“默认编出的字节与接线前逐字节相同”）、
`hud_batch14_settings_test.py`（三处消费点共用判据）、
`hud_batch16_killfx_test.py`（从 ClickGUI 开关一路验到提示音）、
`hud_batch20_robotoverlay_color_test.py`（装甲盒配色：默认档三个消费点逐值不变，主目标面用真实 aim 板色）、
`hud_batch21_radaroverlay_map_test.py`（雷达叠加：默认 Both 逐值不变 + 单消费者守卫，防未来绕过开关）。
全部已接入 `tools/verify_all.py`，孤儿基线由 `--max-orphans` 卡住。

### 剩下 21 个：当前 ORPHAN 清单与历史处置分类

下面完整列出当前静态 ORPHAN，按历史评审的处置类型整理。2026-10-16 校对的是
统计集合，不是对所有功能/协议结论重新验收；功能是否兑现仍需追踪消费者与行为测试。

| 情况 | 证据 | 项数 |
|---|---|---:|
| **刻意不做 / 需先审查默认行为** | `MQTTLink/Auto Connect`、`UDPVideo/Reassemble`、`EnergyAutoAim/Rune Type`、`EnergyAutoAim/Lead Time`、`FireControlStub/Safety Lock`、`FireControlStub/Max Rate`；连接默认值、已有整帧入口及无真实输出能力不能靠补一个读取点改变 | 6 |
| **得先定义新功能** | `EconomyPanel/Open Style`、`TongjiVisionBridge/Local Model`；历史评审分别缺第二种呈现和模型选择消费者 | 2 |
| **可能新增比赛行为，需明确授权与语义** | `ArmorLock/Lock Point`、`ChassisAssist/Buffer Protect`、`PowerGuard/Auto Limit`、`MapMarker/Repeat`、`TongjiVisionBridge/Desktop Safe` | 5 |
| **等一句语义确认**（怎么定都不唯一） | `LockState/Lock Source` 的 Radar 需“地图→像素”投影、Manual 无手选目标子系统；`FrictionWheel/Spin Preview` 含义不明（客户端从不驱动摩擦轮，`manager.py` 里 `friction_on=False` 硬编） | 2 |
| **数据字段存在但含义要按裁判版本对** | `EconomyPanel/Radar Level`：0x0202 第 4 字段已解码进 `state.encryption_level`，且**全仓无处显示**。旧规则表这一槽叫“雷达合法等级”，新版叫“加密等级”——同一个槽在不同协议修订里名字不同，我不猜 | 1 |
| **真无数据源** | `FrictionWheel/Target RPM`：摩擦轮在本仓只有一个布尔 `state.friction_wheel`，没有任何转速字段可读（云台电压**其实存在**且已接，见更正三） | 1 |
| **B 类控制选项（不能由“有发送函数”推导已接线）** | `AirSupport/Command`、`AirSupport/Require Confirm`、`SentinelCommand/Posture`、`SentinelCommand/Confirm Prompt`；文末保留历史处置建议 | 4 |

**孤儿按模块分布（14 个模块共 21 项；其余 60 个模块没有被此口径标为 ORPHAN，不等于全部功能已验证）**：
`AirSupport` 2、`EconomyPanel` 2、`EnergyAutoAim` 2、`FireControlStub` 2、
`FrictionWheel` 2、`SentinelCommand` 2、`TongjiVisionBridge` 2，
以及 `ArmorLock` / `ChassisAssist` / `LockState` / `MQTTLink` /
`MapMarker` / `PowerGuard`（仅剩 `Auto Limit`）/ `UDPVideo` 各 1。
`BuffTimeline` 的 3 项接线属于历史第 19 批，当前校对没有改动其生产代码。

#### 历史接线记录（第 1–21 批，以下数量保持当时口径）

当时记录孤儿从 71 降到 22（共 49 项），其中 **48 项是真的接上了消费者**，
另有 1 项（48→47）是审计工具自己修了个假孤儿（常量别名没被认出来）。
此外还有 **两项根本不在这个计数里**（被同名项洗白在 AMBIGUOUS 档）：
`Crosshair/Style`、`RobotOverlay/Distance`，第 17 批接上。
算上它们，当时共记录接上 **50 项**。
接上的 48 项分布在：`Capacitor`、`PowerLimit`、`SpeedLimit`、`GimbalPower`、`MatchTimer`、
`MatchMode`、`BaseOutpostStatus`、`VictoryPointStatus`、`ProjectileAllowance`、
`ProjectileShop`、`ThermalPowerPanel`、`BuffStatus`、`EliminationFX`（28 项里 0 孤儿）、
`PowerGuard`（3 项里已接 2，只剩 `Auto Limit`）、`BuffTimeline`（3 项全接）
等模块；`Capacitor`、`PowerLimit`、`MatchTimer`、`MatchMode`、`BuffStatus`、
`BuffTimeline`、`RobotOverlay`、`RadarOverlay` 已整模块清零，
`Armor3DESP`、`LockState`、`FrictionWheel`、
`EconomyPanel`、`MapMarker` 各剩最后一两项（都在上面的分类表里）。

**当前结论**：静态 ORPHAN 为 21，保留的历史接线数量不再拿来推导当前功能完成率。
新增功能、比赛行为与语义/数据口径仍需逐项确认；READ / AMBIGUOUS 的命中也不等于
端到端通过。以下历次更正、同名项和处置建议保留历史背景，当前统计以本页顶部为准。

### ❗ 更正四：第四次同一源的误判

上一轮我把 `EliminationFX / Lock Target Bonus` 归为“需要绘制层动手术”，
理由是“发光/闪烁透传牵动多个消费方”。**实际一行代码都没动过绘制层**：

- 消费端整套早就写好了：`kill_fx.py` 里有 `LockedTargetTracker`、
  `LOCKED_TARGET_TAG`、专用提示音 `addscore_df.ogg`，`trigger()` 明确读
  `element_toggles.get("locked_target_bonus", True)`；
- 真正缺的只是 `video_backend_mixin.elimination_element_toggles()` 构造那个
  字典时**没供这个键**，于是它永远取默认的 True。

后果说人话：这个开关**关不掉任何东西**——“锁定目标”标签照弹、
提示音照播。而且 `Bonus Tags`（整行）与 `Lock Target Bonus`（行内子项）
是两个独立开关，之前只有一个真的能用。

四次同一错法（Small/Large Buff、地图标记、云台电压、本项）的共同根因：
**先假设“没找到 = 不存在”，而不是先列出真名清单再判定**。本轮开始
每一项都先拿实际字段名/函数名/字典键名反查一遍，再下结论。

### 历史记录：同名共用那 51 项，42 项已能归属、9 项仍待确认

以前只能说“这 51 项同名，得人工看”。本轮给 `audit_settings.py` 加了
**按模块归属**（`--attribute`）：

```powershell
python tools/audit_settings.py --attribute      # 列出仍无法归属的那些
```

五条证据，从强到弱：

1. 同一行既有 `"<模块名>"` 字面量又有设置名（`_bool_on("EconomyPanel", "Tech Level")`）；
2. 同函数体内 `x = registry.get("<模块名>")` 且本行用到了 `x`；
3. 同函数内把本模块设置项做成了字典（`settings["Mock Writes"]` 这种写法，
   读取行里只有字典名）；
4. 读取走自写谓词（`liquid_map_bool("Show Labels", True)`），谓词体内绑的模块；
5. 本行读的是**形参**（或捕获形参的闭包），再去**调用点**看传进来的是哪个模块。

**不跨函数边界乱归**：同级 `def` 是另一个函数，拿它的形参绑定当证据就是误归。
取最近 def 还不够——局部函数（闭包）也是一行 `def`，`hero_lob.py` 的读取
就写在 `def setting` 后面（是在调它）。先试最近的不行再逐层往外试，
这样只会多归不会漏归。

结果：**51 项里 42 项已能确认“本模块自己读了自己那份”**，9 项仍归不上。
已归属的那些里就包括本会话接好的 `ArmorAutoAim/Fov`、`EconomyPanel/Gold`、
`Capacitor/Show Bar`，以及以前根本看不见的 `Lock Style`（经
`draw_target_overlay()` 形参传入）、`HeroLob/Heat Guard`（经
`preview_from_state()` 的闭包 `setting()` 读）。

#### 仍归不上的 9 项：逐个查过，其中两个本轮已接

这 9 项都是**同一个设置名被好几个模块共用**，而且能找到的读取点全部属于
别的模块。逐个核实结果：

| 项 | 查证结果 | 处理 |
|---|---|---|
| `Crosshair / Style`（RM Dot/Circle/Cross） | 真孤儿：`draw_crosshair()` 只读了 `Size`，选哪个样式画出来都一样 | ✅ **第 17 批已接**，19 项断言；默认档逐图元不变 |
| `RobotOverlay / Distance` | 真孤儿：标签里那个“12.3m”写死的，开关管不到它 | ✅ **第 17 批已接**；开着时与接线前逐字节相同 |
| `ArmorAutoAim / Smooth`（0.28） | 真孤儿：全仓“Smooth”只有一个读取点，而那里 `module` 是 ChassisGimbalIndicator | ❌ 需你们定它该管什么（云台输出低通？**那是控制行为**，且已有 `RobotLink/Gimbal Slew` 在管；帧插值？那个 `amount` 是时间量，语义完全不同） |
| `EnergyAutoAim / Fov`、`/ Predict` | 真没人读（被 ArmorAutoAim 同名读取洗白） | ❌ 该模块无视觉管线也无发送出口，接了就是发假角度（已定） |
| `ArmorLock / Predict`、`/ Heat Guard` | 真没人读（读取点分属 EnergyAutoAim / HeroLob） | ❌ ArmorLock 整模块自述“预留”，接它 = 新增锁定策略，需你们授权 |
| `HeroLob / Mode` | 真没人读：“Radar Assisted”全仓只出现在定义行 | ❌ 接它需新增“雷达辅助吊射”逻辑，需你们授权 |
| `FireControlStub / Mode` | 真没人读 | ❌ C 类，按设计就是空壳 |
| `RobotLink / Port`、`/ Baud` | **真没人读——与 `Send Hz` 同一个形状**：端口/波特率只从场景配置取（`manager.py:177-178`），而模块自己的取用清单里没有它们 | ❌ 没顺手接：接上会让默认值为 `Auto` 的设置在官方场景下去自动找串口，**选错口有真实后果**。需你们定“场景配置与模块设置谁赢” |

> **一个必须说清的口径问题**：这 11 个“被同名洗白的真孤儿”**不在 29 里**——
> 审计把它们归在 AMBIGUOUS，不计入也不计减基线。所以“孤儿 29”这个数
> **低估了问题**，而“原先估计 A 类可接 28 项”也不是个完整名单。
> 本轮把归属能力补上，就是为了让 A 类的实际范围能被数清而不是靠估。

> 这一档的另面也要说：审计能归上 ≠ 值兑现得了。`SpinMode/Spin Speed` 就是
> 有人读但固件兑现不了的反例（见下文）。

### 新建面板时必须量的东西（第 19 批踩到的两个坑）

1. **位置不能猜**。仓库里有 `tactical_map_rect()` / `hud_density()` 这类纯几何
   函数，先问出地图面板在 1600x900 / 1920x1080 / 1280x720 下占哪块（实测
   左列 x 12..228、y 48..~190，底部到屏幕底还有 535px 以上），再定落点。
   而且**同一条带已经有别人要用**（模块列表在右侧放不下时会落在地图下方），
   所以两处共用 `buff_timeline_band()` 一个真相源，模块列表落点改为
   `band.bottom() + 14`——重叠在结构上不可能，而不是靠“应该差不多”保证。
2. **真窗口渲染必须子进程隔离**。在同一进程里先建 QImage/QPainter、再
   构造真 `PolarRMWindow`（QOpenGLWidget）会把整个测试进程带崩（退出码
   0xC0000409，**输出全丢**——看上去反而像“全部通过”）。所以像素断言改成
   子进程跑、父进程看退出码；并且所有 `check()` 都带 `flush=True`，崩在下一步时
   已经说过的话不会一起丢。另一个坑：往 1x1 的 QImage 上画在光栅路径里是
   病态目标（圆角底会算出零宽度渐变），同样崩进程，画布要用正常尺寸。

### ❗ 更正五：“工具看不见”不等于“不存在”

前四次错在“用自己猜的名字去搜”，这一次错在方向上但同源：
我拿审计的 ORPHAN 名单当“A 类全部”，而那 51 个同名项只被标为
“待人工确认”就再没动过。结果里面藏着 **11 个真孤儿**（本模块那份
根本没人读，只是同名的另一个模块被读了），其中两个是完全可接的普通
显示开关（准星样式、透视距离），本轮才挖出来接上。

教训：一个工具说“这个档位无法判定”时，它不是“已确认无问题”，而是
**未被检查**。未检查的部分不能被当作完成的一部分。

### ❗ 更正二：我又误判了一次"不存在"

上一轮我在这份文档里写"地图标记连协议编码都没有"，并把那 3 项归为无法接。
**这是错的**，而且错法和 `Small/Large Buff` 那次同源：

- 我当时搜的是 `map_command`、`0x0106` —— 这两个名字在本仓库**根本不存在**，
  真实报文叫 `MapClickCmd`。
- 实际上编码器 `protocol.encode_map_click_cmd`、发布函数
  `backend.publish_map_click_cmd`、点击入口
  `tactical_map.publish_map_click_from_screen` 全都在，而且一直在发。
- 所以 `Mark Type`（对应字段 6）与 `Send All`（字段 1）**本来就可接**，
  本轮已接，16 项断言。

判定"某样东西不存在"时，用自己猜的词去搜，得到的"不存在"是假的。
正确做法是先从协议清单/函数清单里找真名。

**接的时候有一条硬约束**：发送处原先硬写 `is_send_all=1`（全队广播），
而 `Send All` 设置项原默认是 False。照默认值直接接会把广播变成单发——
那是比赛可见的行为改变。所以把该项默认改为 True（等于今天的实际行为），
并有一条断言钉住"默认配置下编出的字节与接线前逐字节相同"。

`Repeat` 没有对应的编码器字段，且重复广播要过裁判侧 10Hz 限流，
补它等于新增外发流量，本批不做。

### 查证后**故意没接**的两项（接了会上比赛事故）

**`MQTTLink / Auto Connect`**

现行为是三种模式下**无条件** `mqtt.start()`（`manager.py:226`，原始注释写着
“三种模式都启动 MQTT（可收裁判数据；发布按场景门控）”）。而这个开关**默认是 False**。

接上它等于：新默认配置下客户端不再连裁判系统 → **血量/增益/能量全部看不到**。
而且已经持久化了 False 的用户升级后会直接回归。这就是“点了大疆官方后什
么都看不到”同类的事故。要接得先做两件事，**这两件都需要你们拍板**：

1. 把默认值改成 True（行为与今天一致）；
2. 加一条就绪告警：“Auto Connect 已关，收不到裁判数据”，避免静默失能。

**`UDPVideo / Reassemble`**

全仓没有重组器实现，而且 `polar_app.py:421` 的注释写明
“Backend UDP has already reassembled a complete frame”——后端交付的就是整帧。

所以这不是“忘了接”，是**没有可接的东西**。要么写一个客户端重组器
（没需求），要么删掉这个开关。建议后者，但删你们的设置项需要你们同意。

### ❗ 更正：本档最初的数字是错的

本文档第一版报的是“36 个孤儿”，**实际是 71 个**（本轮修完 3 项后为 68）。

原因：审计工具当时没排除 `jello_locale.py`（中英翻译表），而里面全是
`"Show Bar": "电容进度条"` 这样的条目。按名字全局匹配会把
**“这个设置项有中文名”当成“这个设置项有人读”**，直接把孤儿洗白成 READ。

这一类假清除影响 35 项，几乎把真实问题数量低估一半。

**第 3 种假孤儿（后来又发现的）**：只匹配字面量会漏掉"经由全大写常量读取"的设置项。
`ClickGui / Language` 一直真的被读，写法是 `get_setting(LANGUAGE_SETTING)`，
字面量只出现在常量定义行里。修法是收集 `FOO = "literal"` 形式的常量并把它当别名；
同时必须**排除 import 行**（`from x import LANGUAGE_SETTING` 只证明名字被引入，
不证明有人读它）—— 否则会从"假孤儿"翻成"假 READ"，而假 READ 更糟：它把真问题藏起来。

三个教训：

1. 审计工具里，“名字出现”必须区分**出现在哪类文件**：翻译表、告警文案、
   注释、定义行，统统不是读取点。
2. 上面的 AMBIGUOUS 档位是同一个错的第二种形态（同名串模块）。
   两个形态的共同根因都是一个：**按名字匹配，而不是按归属匹配**。

68 个孤儿就是"拖了滑块什么都没发生"的那一批。下面按**该怎么处理**分三类。

### 为什么要单独设一个 AMBIGUOUS 档（不是凑数字）

接完 `ArmorAutoAim / Fov` 之后，审计一度报"孤儿从 36 降到 33"，看起来修了 3 个。
**实际只修了 2 个**：第 3 个是 `EnergyAutoAim / Fov`，它和刚接好的那个**同名**，
按名字全局匹配的扫描把给 `ArmorAutoAim` 写的读取点算到了它头上——
而它事实上仍然没人读。

这个假进度是在我自己身上实时发生的，不是理论风险。所以这类单独成档，
**不计入也不计减基线**，必须人工确认各自模块真的读了自己的值。

> 另一面也要说清：`ArmorAutoAim / Fov` （已接好的那个）字面上**仍落在 AMBIGUOUS**，
> 因为只看行内文字无法把读取点归到具体模块。所以**光看档位不能证明这类修复**，
> 能证明的只有两件事：`tests/autoaim_settings_test.py` 的 31 项断言，以及
> 本轮新加的 `--attribute` 归属（它把 `ArmorAutoAim/Fov` 归到了本模块）。
> 不要把审计的档位当成唯一验收依据。

51 个 AMBIGUOUS 按名字分组（同一行的几个模块共用一个设置名）：

| 共用名 | 涉及模块 | 备注 |
|---|---|---|
| `Mode` | Scenario / MatchMode / HeroLob / FireControlStub | 4 处，语义完全不同 |
| `Port` | MQTTLink / UDPVideo / SerialBridge / RobotLink | 4 处，传输层各自读写 |
| `Mock Output` | ArmorAutoAim / EnergyAutoAim / HeroLob / SpinMode | 4 处 |
| `Mock Writes` | Scenario / SerialBridge / RobotLink | 3 处 |
| `Show Labels` | Armor3DESP / LiquidTacticalMap / QuickActionWheel | 3 处 |
| `Style` | Armor3DESP / Crosshair / ChassisGimbalIndicator | 3 处 |
| `Fov` | ArmorAutoAim / EnergyAutoAim | 只修了前者；后者无视觉管线，刻意未接 |
| `Smooth` | ArmorAutoAim / ChassisGimbalIndicator | |
| `Lock Style` | ArmorAutoAim / EnergyAutoAim | |
| `Predict` | EnergyAutoAim / ArmorLock | |
| `Heat Guard` | ArmorLock / HeroLob | |
| `Distance` | HeroLob / RobotOverlay | |
| `Power Guard` | SpinMode / AutoSpinOnHit | |
| `Profile` | ConfigManager / CameraCalibration | |
| `Scale` | EliminationFX / ClickGui | |
| `GUI Scale` | UIScale / MinecraftCombatHUD | |
| `Size` | Crosshair / ChassisGimbalIndicator | |
| `Opacity` | Armor3DESP / TacticalMap | |
| `Host` | MQTTLink / UDPVideo | |
| `Baud` | SerialBridge / RobotLink | |
| `Limit` | PowerLimit / SpeedLimit | |

要彻底消除这一档，得把读取改成按模块定位（例如读之前先断言
`module.name`），而不是靠名字全局匹配。那是工具能力问题，不是代码缺陷。

---

## 已经修好的一个（作为范例）

### `RobotLink / Send Hz`（发送频率）

**问题**：设置项定义在 `model.py:1574`，全仓**没人读它**。真实发送频率来自场景配置
`config.connection.polar_send_hz`，只在 `BackendManager.__init__` 读一次。
所以你在界面把 50 拖到 80，存盘、重启都还在，**频率一点没变**。

**还有第二层**：即使接上也没用。发送线程里 `interval = 1.0 / self.send_hz`
写在 `while` **之前**，运行中改值不生效，必须重启链路。

**修法**（两处）：
- `polar_transport.py`：新增 `set_send_hz()`（带 5..100 限幅），并把周期计算移进循环内
- `manager.py`：`tick_polar_link` 每帧读模块设置并应用，与 `Mock Writes` / `Gimbal Slew` /
  `Drive Channel` 同一套写法

**回归断言**：`tests/polar_bridge_test.py::test_send_hz_setting_actually_drives_the_link`
（7 项：拖到 80 生效、超上限限幅、低下限限幅、垃圾值不崩、变更返回值语义）

> 限幅不是可选的：频率过高会淹没下行段解析，过低会让固件的 150ms 新鲜度门控
> 误判成掉线、把控制权交还遥控器。

---

## A 类：客户端侧能接上（不需要电控）

这些的消费者**应该**在客户端自己这边（渲染器、视觉管线、告警逻辑），
只是没接线。按"影响面 × 接线成本"排了优先级。

### A1 影响瞄准 —— 本批已完成 2 项，剩余 2 项刻意不做

| 模块 | 设置项 | 状态 |
|---|---|---|
| `ArmorAutoAim` | `Fov` | ✅ **已接**：按针孔模型 `fx × tan(fov/2)` 换算成像素半径，与原有像素圆取交集 |
| `ArmorAutoAim` | `Priority` | ✅ **已接**：Armor/Distance/Health 三种排名真实生效；数据不足时降级并记原因 |
| `EnergyAutoAim` | `Fov` | ❌ **刻意不接**：该模块无真实视觉管线，接了会发假角度 |
| `EnergyAutoAim` | `Lead Time` | ❌ **刻意不接**：同上 |

**`Fov` 接法上的两个安全约束**：
- 拿不到相机内参（未标定）时 **不加这道门**，而不是猜一个换算系数。行为与接线前完全一致。
- 标定分辨率与当前帧不符时同样不加门（内参只对标定时那个分辨率成立）。

**`Priority` 接法上的两个安全约束**：
- `Radar`（雷达优先）**故意不实现**：雷达上报的是地图坐标，与装甲板之间需要
  “地图位置→图像像素”投影才能对应。拿位置猜匹配会让客户端**瞄错车**，
  所以直接降级并明说原因。要做得单独立项。
- `Health` 需要裁判系统血量。台架上只连机器人 USB 时根本没这个数据，
  此时**降级为按置信度**，并把降级原因记在 `autoaim_priority_reason` 里，
  不谎称“按血量选了”。

**回归断言**：`tests/autoaim_settings_test.py`（31 项），包括：换算式正确、
收窄角度后门外的靶真选不到、未标定不加门、垃圾值不崩、Distance 与 Armor
选出不同的靶、无血量时降级且原因非空、已阵亡靶不因“低血量”被优先选中、
缺距离时当“最远”而不是贴脸。

> 距离取不到时必须返回 `inf` 而不是 0：0 会被当成“贴脸”，把缺数据的靶
> 抢到最前面，那比不优先更坑。

### A2 影响画面渲染（已接 16 项 / 共 17 项）

| 模块 | 设置项 | 状态 |
|---|---|---|
| `RobotOverlay` | `Armor Boxes` | ✅ 已接（第 15 批）：关掉后装甲线框本体不画；与 `ESP Style` 不重叠——Style 选“长什么样”，本项选“画不画” |
| `RobotOverlay` | `Color` | ✅ 已接（第 20 批）：默认值改为 White（=接线前画面，三个消费点逐值不变）；Team 只在认得出板色时染阵营色；Threat 无数据源→降级 Team 并在诊断记原因 |
| `RadarOverlay` | `High Light` | ✅ 已接（第 11 批） |
| `RadarOverlay` | `Map` | ✅ 已接（第 21 批）：默认值改为 Both（=接线前两张图都画，逐值不变）；Mini/Full 按面板展开态分档，Hidden 只关显示不动数据。若你们对 Mini/Full 另有定义，改 `radar_overlay_map_visible` 一处即可 |
| `Armor3DESP` | `Renderer` | ✅ 已接（第 6 批） |
| `BuffTimeline` | `Buffs`、`Special Mechanism`、`Timeline` | ✅ 已接（第 19 批）：这个模块**本来一个消费者都没有**（增益与特殊机制的时间早就解码好了，没人读）。新面板贴在战术地图下方，**位置是量出来的不是猜的**；无真数据时不出现也不占位，所以默认画面与接线前一致 |
| `ThermalPowerPanel` | `Heat`、`Buffer` | ✅ 已接（`Buffer` 第 8 批） |
| `EconomyPanel` | `Gold` | ✅ 已接（第 3 批） |
| `EconomyPanel` | `Radar Level` | ❌ 数据口径待确认：0x0202 第 4 字段已解码进 `state.encryption_level` 且全仓无处显示；旧规则表这一槽叫“雷达合法等级”、新版叫“加密等级”，我不猜 |
| `EconomyPanel` | `Open Style` | ❌ 得先做第二套呈现：`"Full Sheet"` 全仓只出现在设置定义那一行 |
| `EliminationFX` | `Lock Target Bonus` | ✅ 已接（第 16 批）：消费端整套早就在，只差给字典供一个键 |
| `Crosshair` | `Style`（RM Dot/Circle/Cross）| ✅ 已接（第 17 批）：原先被同名的 `Armor3DESP/Style` 洗白，实际选什么都画同一张准星；默认档逐图元不变 |
| `RobotOverlay` | `Distance` | ✅ 已接（第 17 批）：标签里的距离以前写死，开关管不到；但 `Polar Outline` 样式的标签本来就没距离，要不要也加需要你们定 |

### A3 影响告警/提示与面板开关（第 2 批：已接 3 项）

已接上（`tests/hud_telemetry_settings_test.py`，26 项断言）：

| 模块 | 设置项 | 接法 |
|---|---|---|
| `Capacitor` | `Warn` | 电容低于阈值时强制告警色。注意方向：**低为险**，不能直接复用 `hud_state_color(ratio)` |
| `Capacitor` | `Show Bar` | 关掉后 CAP 行真的从两个构造器里都消失 |
| `PowerLimit` | `Show Limit` | 关掉后 POWER 行真的从两个构造器里都消失 |

当初列为“仍未接”的已全部接完：

| 模块 | 设置项 | 接法 |
|---|---|---|
| `PowerLimit` | `Pulse Overrun` | ✅ 第 7 批。关键坑：判“超限”必须用**钳位前的原始 ratio**，钳到 0..1 后永远判不出 >1 |
| `PowerGuard / Heat Warn`、`Energy Warn` | ✅ 已接（第 18 批）。热量用**绝对阈值顶到告警档**且只加严不放松；电容那两个来源（`Capacitor/Warn` 与 `Energy Warn`）同时存在时**取更严的一个**。该模块默认是关的，所以接线本身零画面变化，但今天“打开 PowerGuard 什么都不会发生”这件事不再成立 |
| `Capacitor` | `Use Glow` | ✅ 第 7 批：默认值下发光量与接线前逐值一致 |
| `Crosshair` | `Heat Color` | ✅ 第 5 批 |
| `MatchTimer` | `Pulse Last 30s` | ✅ 第 4 批（`MATCH_PULSE_WINDOW_S = 30.0`） |
| `BaseOutpostStatus` | `Own Base`/`Outposts`/`Shield` | ✅ 第 3 批：面板开关统一走 `hud_row_visible()`，三个构造器共用 |

为什么必须同时改多个构造器：`map_telemetry_status_rows`（小地图）、
`draw_telemetry_strip`（遥测条）与 `draw_status_cards_hud`（状态卡片）各建一份。
只改一处会变成“同一个滑块在一处有效、一处无效”，那比统一不接更坑。
这个不一致我自己犯过一次（`Show Bar`/`Show Limit`/`Show Speed` 漏了第三个消费点），
第 14 批补回并用断言钉住。

### A4 影响链路/视觉后端（4 项，已接 1 项）

| 模块 | 设置项 | 状态 |
|---|---|---|
| `MatchMode` | `Second Step` | ✅ 已接（第 8 批）：启动页二级选择页开关 |
| `UDPVideo` | `Reassemble` | ❌ 无可接对象：后端已交付整帧（`polar_app.py:421` 注释写明），要么写客户端重组器（没需求），要么删开关 |
| `TongjiVisionBridge` | `Local Model` | ❌ 占位：只有一个选项。建议删开关或改只读显示 |
| `TongjiVisionBridge` | `Desktop Safe` | ❌ 与 `ArmorAutoAim/Mock Output` 语义重叠：接上等于新开一道控制输出门，得先定“谁说了算” |

---

## B 类：走裁判系统，队内链路本来就没有（**不是缺陷**）

> ⚠ 本节已被下面的「B/C 类处置建议」修正：那里的“只有官方出口、这是正确的”
> 不准确——实际是空中支援的官方出口**存在但从没人调用**；地图标记的报文
> **其实存在**（叫 `MapClickCmd`）且已接 2/3，见「更正二」。请以处置建议那一节为准。

| 模块 | 设置项 |
|---|---|
| `MapMarker` | `Mark Type`、`Send All`、`Repeat` |
| `SentinelCommand` | `Posture`、`Confirm Prompt` |
| `AirSupport` | `Command`（Free/Paid/Abort） |

这些是**裁判系统**的功能（地图指令、飞镖、空中支援），走官方链路，
不该从机器人 USB 下发。界面上对应模块只有官方出口，这是正确的。

**但它们仍然是孤儿**——意思是连官方出口也没在读这些设置项。
所以真正的问题是：**官方链路的发送代码没有引用这些设置**，
要么该接上，要么这些设置项该删掉。这一条需要你们确认这些功能比赛时到底用不用。

---

## C 类：模块本身就是空壳（**设计如此**）

| 模块 | 设置项 |
|---|---|
| `FireControlStub` | `Safety Lock`、`Max Rate` |

模块描述原文：**"不输出真实发射命令，仅保留接口占位。"**
所以它的设置项没人读是**故意的**——开火权始终在驾驶员手上，
客户端的 `fire` 位在编码器层就硬编码为 0，签名里根本没有这个参数。

**建议**：把这两个设置项从界面上去掉，或者在描述里明说"占位，不影响任何行为"。
留着一个能拖但没用的"最高射速 8 Hz"，会让人误以为客户端在控射速。

---

## 一个"有人读、但兑现不了"的例子（工具查不出来）

`READ` 档**不等于对齐**。最典型的是：

| 模块 | 设置项 | 界面承诺 | 固件实际 |
|---|---|---|---|
| `SpinMode` | `Spin Speed` = 420 dps | 可调自转转速 | 固件的自转只是**一个开关标志**（`spin_flag`），转速由固件自己的 SPIN 模式决定，**不接受外部设定** |
| `SpinMode` | `Direction` = Auto/CW/CCW | 可调自转方向 | 同上，方向也是固件定的 |

这两项**有人读**（走官方 MQTT 的 PRM1 kind=2 出口），所以工具判 READ。
但机器人固件兑现不了。上行 0x04 的 `param0` / `param1` 两个字节我**已经预留**
给方向和速度档，见 `tools/firmware/host_bridge/host_chassis_bridge.h`——
要不要真做，需要电控确认固件侧能不能接受外部转速。

**这类只能靠人工对照 `tools/firmware/host_bridge/GAPS.md` 确认，工具判不出来。**

---

## 防倒退

孤儿数量被 `tools/verify_all.py` 盯着，**只许减不许增**：
新增被工具识别为孤儿的设置项会触发门限。当前基线 **21**（2026-10-16 与 PR17
基线及当前工作树校对一致；不是本轮新接一项）。

```powershell
python tools/audit_settings.py --max-orphans 21
```

当前 AMBIGUOUS 的 63 项**不计入也不计减**基线；历史的 51 项记录不代表当前总数。

本文档里那个孤儿数一共出现在 6 个地方（结果表、进度表末行、“剩下 N 个”
标题、防倒退正文、示例命令，加上 `verify_all.py` 里的闸门值），
手工改一次就对不上一次。`tests/settings_doc_consistency_test.py` 专门钉这件事：
它**跑一遍真审计**拿实测值，再按位置把这几处拉出来比，并要求文档写实测值
（基线允许暂时高于实测，文档写高了就是虚报）。

> **环境前提：`ziglang` 必须装**。`verify_all` 里三项 C 检查（固件补丁编译、
> 行为断言、自转桥接）靠 `python -m ziglang cc` 跑。本机一开始没装，
> 结果是“代码全绿但 NO-GO —— 3/23 未通过”，看上去像代码坏了。
> 装一下就行：`pip install ziglang`。注意缺依赖时这三项是**硬失败**而不是
> 跳过（`prepare_hero_build.py` 对同一依赖的处理是警告+跳过），两边不一致。
> 本轮已装并验证：23/23 全绿，GO。

---

## 这份清单的局限（不要过度解读）

1. **按名字全局匹配**：不同模块可能有同名设置项（例如 `Fov` 同时在
   `ArmorAutoAim` 和 `EnergyAutoAim`）。这种情况下读取点无法归属到具体模块，
   工具会标 `[名字共用x2]`，需要人工看上下文。
2. **ORPHAN 只证明"没人按这个名字读它"**，有三种可能：
   真的是装饰 / 通过遍历所有设置项做持久化（那不算功能读取）/ 名字被别的模块共用。
3. **READ 不证明读对了**：值可能被读了但兑现不了（见上一节 SpinMode）。
4. 判定一条通路是否真的通，唯一可靠的办法是看 `tests/` 里的**端到端断言**：
   从模块状态一直追到上线字节或画面像素。小陀螺和 Send Hz 就是这么修的。

---

## B / C 类处置建议

本节的每一条都查过源码，不是推测。目的：说清该接、该删、还是该你们定。

### 事实核对表

| 模块 | 官方出口存在吗 | 有人调用吗 | 协议编码存在吗 |
|---|---|---|---|
| `MapMarker`（地图标记） | ✅ | ✅ 小地图点击即发；`Mark Type`/`Send All` 已接（第 13 批） | ✅ `protocol.encode_map_click_cmd`（我先前说“没写”是错的，见更正二） |
| `SentinelCommand`（哨兵） | ✅ `publish_sentry_command()` | ✅ 但只被**小地图点击**调用（`tactical_map.py:591`），不读模块设置 | ✅ |
| `AirSupport`（空中支援） | ✅ `publish_air_support()` | ❌ **零调用者** —— 整个功能不可达 | ✅ |
| `FireControlStub` | — | — | — （设计上就是空壳） |

### 建议

**1）`AirSupport`（2 项：`Command`、`Require Confirm`）——优先级最高**

发送函数写好了、协议也对，但**全仓没有任何地方调用它**。
也就是说：这个功能现在**百分百不能用**，而且界面上看着像能用。

这比其他孤儿严重：不是“某个滑块没接”，是**整个按钮按下去什么都不会发生**。

建议：**要么接完（工作量很小，就差一个入口），要么先标“未启用”**。
但空中支援会花掉队伍资源、影响比赛，**发送入口该放哪（小地图？快捷轮？热键？）
必须你们定**，我不替你们决定，也不擅自加一个会自动花钱的按钮。

**2）`MapMarker`（3 项：`Mark Type`、`Send All`、`Repeat`）——建议删除或降期望**

雷达地图指令（`0x0106`）在客户端**连协议编码都没写**。这不是“忘了读设置”，
是**从零开始做一个官方协议报文**。

而且地图标记是**发给裁判系统、由裁判机广播给全队雷达**的，属于“会影响队友/对手
看到什么”的动作。建议：要么你们确认要做并当做一个独立功能排期，要么
先把这 3 个设置项从界面上去掉，不要让指挥手以为标了就发得出去。

**3）`SentinelCommand`（2 项：`Posture`、`Confirm Prompt`）——接一半不如接完**

哨兵指令能发（小地图上点就能发），但 `Posture`（攻击/防御/机动）与
`Confirm Prompt` 根本没参与 —— 发的 `command_id` 来自点击，不来自设置。

这两项**可接且成本低**（把点击时的 command_id 或确认弹窗接到设置上），
但哨兵是自动驾驶的，改它的姿态是**比赛行为**，所以我要你们确认：
期望是“`Posture` 设定默认姿态，小地图点击覆盖它”，还是“点击必须过 `Posture` 映射”？
两种语义不一样，我不猜。

**4）`FireControlStub`（2 项：`Safety Lock`、`Max Rate`）——建议删掉设置项**

模块描述原文：“不输出真实发射命令，仅保留接口占位。”
而且 `encode_robot_cmd()` 的签名里**根本没有 fire 参数**，编码器层硬编 0，
`polar_link_test` 有不变量断看着。所以“没人读”在这里是**正确的事**。

但界面上摆一个能拖的“最高射速 8 Hz”，会让人以为客户端在控射速。
建议：保留模块（占位有用），**删掉这两个设置项**，或在描述里明写
“占位，不影响任何行为”。不建议把 `Safety Lock` 真接上去 —— 那等于
等于开一个“默认安全”但其实什么都不拦的开关。

### 汇总：我不擅自动的，等你一句话

| 项 | 要你定什么 |
|---|---|
| `AirSupport` 2 项 | 发送入口放哪？现在零调用，等于不能用 |
| `SentinelCommand` 2 项 | `Posture` 是“默认值”还是“必经映射”；`Confirm Prompt` 要不要接管点击确认 |
| `MapMarker` 的 `Repeat` | 重复广播要新增外发流量（10Hz 限流），且无对应报文字段：做还是删 |
| `MQTTLink / Auto Connect` | 可否接受改默认值为 True + 加就绪告警 |
| `EconomyPanel / Open Style` | 要不要真做第二套呈现（Full Sheet）|
| `EconomyPanel / Radar Level` | 你们的裁判机跑的哪个协议修订？0x0202 第 4 字段到底该叫雷达等级还是加密等级 |
| `LockState / Lock Source`、`FrictionWheel / Spin Preview` | 语义确认（每个都是一句话的事，但怎么定都不唯一，我不猜） |
| `ArmorAutoAim / Smooth` | 它到底该管云台输出低通、画面插值，还是什么都不管（删开关）——见上面“同名洗白的真孤儿” |
| `RobotLink / Port`、`/ Baud` | **场景配置与模块设置谁赢**？现在是场景说了算，界面上选串口/波特率无效（与 `Send Hz` 同形状）。接上会让官方场景去自动找串口，选错口有真实后果，我不自己定 |
| `ArmorLock / Predict`、`/ Heat Guard`、`HeroLob / Mode` | 接它们都要新增锁定/吊射策略（模块自己写的是“预留”），需你们授权还是删开关 |
| `PowerGuard / Auto Limit` | 要不要客户端真介入限功率（它默认关着，接上就是新增主动控制）|
| `ChassisAssist / Buffer Protect`、`ArmorLock / Lock Point` | 要不要客户端真介入控车/限功率（这批必须你们授权，我不会因为“能写”就写）|
| `ClickGui / Save Layout`、`TongjiVisionBridge / Local Model`、`UDPVideo / Reassemble` | 全仓无对应逻辑：建议删开关 |
| `FireControlStub` 2 项 | 删开关还是只改描述（“没人读”在这里是设计正确，但滑块不能骗人） |

这一轮不再自己往下推了：上面每一项都能写代码，但写下去要么新增比赛行为、
要么替你们定一个语义。这两种都不是“修修补补”，而是改东西。
不改动比赛行为、只差给字典供值的那一类，已经全部做完，并由 23 项检查锁定。


## 本轮（C 路定案那一批）新查出来的一类假接线：**"有人写"不等于"有人读"**

`tools/audit_settings.py` 判"这个设置项接没接"的办法是看设置名在代码里被不被引用。
`MQTTLink / Host`、`MQTTLink / Port` 就这样被判成已接线 —— 因为
`rm_client/scenarios.py:134-135` 把场景值抄到界面上（**写**），于是审计看见名字出现在文件里。
但**没有任何地方反向读它**：`BackendManager` 只在 `_apply_config` 里按场景赋值
（老代码 `manager.py:169`），`ScenarioManager.apply_to_runtime`（老 `scenarios.py:155-156`）也是纯赋值。

后果是现场最难查的那种：

1. 操作员在二级界面把 broker 从 `127.0.0.1` 改成 `192.168.12.1`，**屏幕上留下了新地址**，
   后端一个字节都没改 —— 键鼠还在往本机发。
2. 更狠的一条：MQTT 客户端**已经连着**的时候赋值 `host` 字段**不会重连**。
   所以"从实验室切到正式赛"这个动作，地址改了、连接还挂在老的 broker 上。

本轮的修法（都带断言，落在 `tools/test_mqtt_link_settings.py`，已进 `verify_all`）：

- `RmMqttBackend.set_address(host, port)`：换地址 + 已连接则 `stop(publish_status=False)` 后 `start()`。
- `ScenarioManager.apply_to_runtime` 与 `BackendManager._apply_config` 都改走 `set_address`
  （老替身没有该方法时退回赋值，向后兼容）。
- `BackendManager.sync_mqtt_link(registry)` 挂进主循环 tick（`polar_app.py` 的 `mqtt.link` 一环）：
  界面设置项 → 后端真地址；非法输入（空主机名/含空格/端口越界）沿用当前有效值，
  并且**把真正在用的地址回写进设置项** —— 界面显示的东西必须是真的。

给下一个人的提醒：**审计工具看不见不等于不存在，反之也一样**。
"设置项出现在源码里"只证明有人**写**它；要证明它有用，得有一条断言是
"改了设置 → 后端行为变了"。本轮两个真 bug（`DT7 Real TX` 没进 `settings` 白名单、
`MQTTLink/Host` 只有人写）都是靠这种断言才抓住的，而它们此前都"看起来接好了"。


## 本轮决定并接线：`RobotLink.Port` / `RobotLink.Baud`（原来写着"我不自己定"）

之前这条挂在"需你们定：场景配置与模块设置谁赢"上，界面上选串口/改波特率**完全无效**，
而出错提示还写着"在 RobotLink.Port 里显式选口"——那是指了一条不起作用的路。

现在按这条规则接上，并且写死成断言：

| 情况 | 谁赢 | 为什么 |
|---|---|---|
| 模块设置保持默认（`Auto` / `115200`） | **场景配置** | 否则官方场景下会突然自动去找串口，选错口的后果比"没生效"严重 |
| 操作员显式改过（选了 COM7 / 57600） | **模块设置** | "我在界面上选了 COM7 就得是 COM7" |
| 把 Port 清空 | 保持上一个确定值 | 不许悄悄猜一个口 |
| 波特率非法（乱码、越界） | 保持原值 | 非法输入不许把传输层改坏 |

显式改过 = 值不等于默认哨兵（`PORT_DEFAULT = "Auto"`、`BAUD_DEFAULT = 115200`），
两个哨兵写在 manager 顶部，改默认值时会被断言提醒。
C 板那条是 USB CDC，固件本身不看波特率；但换成 UART 桥一类接法时它就是有效参数，
所以这个框接上不是多余的。

顺带：这次是**新写的模块作用域审计**抓出来的（`tools/test_settings_are_read.py`）。
原来的审计只查"名字在源码里出现过"，于是 MQTTLink 也有的 `Port` 替 RobotLink 的 `Port`
打了掩护，一直判"已接线"。新审计按函数分块，只有在**提到该模块的代码块里**取用才算数。
同一条审计还抓出 `DT7 Allow Fire Keys` 没进白名单（界面上能勾但后端不读）——
就是我把这类问题当成一等公民后，它立刻兑现了一次。
